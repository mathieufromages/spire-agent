"""Rule-based BuildAgent stage: every non-combat decision without an LLM.

The stage keeps the deterministic pieces of the default BuildAgent
(continuations, fast paths, the Winning Path card picker, key/rest/event/boss
relic constraints) and replaces every point where the default stage would
escalate to a language model with a fixed rule:

* card rewards  - the picker's direct command, else its best shortlisted card
* shops         - removal when affordable, else the best approved card or relic
* rest sites    - heal below an HP threshold, else smith the best upgrade target
* events        - a per-event preference table, ``leave``/safest otherwise
* boss relics   - a fixed preference list filtered by the deterministic policy
* selectors     - worst cards for remove/transform, best card for upgrade/pick
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import re
from typing import Any

from spire_agent.contracts import AgentKind, Decision, DecisionRequest, GameState
from spire_agent.subagents.agents import BuildAgent
from spire_agent.subagents.build import CardPicker
from spire_agent.tools.build_flow import (
    BuildError,
    build_choice_policy,
    continue_build,
    fast_decision,
    policy_decision,
    selection_kinds,
)
from spire_agent.tools.run_keys import RUN_ROUTE_KEY
from spire_agent.tools.winning_path.card_policy import CardRewardError

from . import cards as card_values
from .events import EventContext, event_key, normalize as normalize_event, rank_choices
from .relics import boss_relic_value, shop_relic_value


_NO_HEAL_RELICS = frozenset({"coffee dripper", "mark of the bloom"})
_REST_THRESHOLD = 0.60
_REST_THRESHOLD_ELITE = 0.70
_REST_THRESHOLD_BOSS = 0.78


class HeuristicBuildStage:
    """Owner-bound BUILD stage that never calls ``complete()``."""

    def __init__(
        self,
        card_picker: CardPicker,
        choice_policy: Callable[[DecisionRequest], Mapping[str, Any] | None] = build_choice_policy,
    ) -> None:
        self._picker = card_picker
        self._choice_policy = choice_policy

    def try_decide(self, request: DecisionRequest) -> Decision | None:
        continued = continue_build(request)
        if continued is not None:
            return continued
        if request.scope.owner is not AgentKind.BUILD:
            return None
        decision = fast_decision(request)
        if decision is not None:
            return decision
        screen = request.state.screen.type
        handler = {
            "CARD_REWARD": self._card_reward,
            "SHOP_SCREEN": self._shop,
            "REST": self._rest,
            "EVENT": self._event,
            "BOSS_REWARD": self._boss_reward,
            "GRID": self._selector,
            "HAND_SELECT": self._selector,
        }.get(screen)
        if handler is not None:
            return handler(request)
        return self._generic(request)

    # -- card rewards -----------------------------------------------------

    def _card_reward(self, request: DecisionRequest) -> Decision:
        result = self._picker.review(request)
        direct = result.get("command")
        if isinstance(direct, str) and direct:
            return policy_decision(
                request,
                direct,
                "card_reward.policy",
                str(result.get("reason") or "card picker direct decision"),
                payload=self._picker.decision_payload(result, command=direct),
            )
        state = request.state
        commands = set(state.screen.commands)
        allowed = [int(value) for value in result.get("allowed_choice_ids") or ()]
        candidates = {
            int(row["choice_id"]): row
            for row in result.get("candidates") or ()
            if isinstance(row, Mapping) and "choice_id" in row
        }
        allowed = [cid for cid in allowed if cid < len(state.screen.choices)]
        ranked = sorted(allowed, key=lambda cid: (-_candidate_score(candidates.get(cid, {})), cid))
        allow_skip = bool(result.get("allow_skip")) and "skip" in commands
        bowl = result.get("bowl_choice_id")
        if ranked and (not allow_skip or _candidate_score(candidates.get(ranked[0], {})) > 0):
            command = f"choose {ranked[0]}"
            reason = f"best shortlisted card {candidates.get(ranked[0], {}).get('name', ranked[0])}"
        elif allow_skip:
            command, reason = "skip", "no shortlisted card scores positively"
        elif isinstance(bowl, int) and 0 <= bowl < len(state.screen.choices):
            command, reason = f"choose {bowl}", "Singing Bowl instead of an unwanted card"
        elif ranked:
            command, reason = f"choose {ranked[0]}", "forced pick from the shortlist"
        elif "skip" in commands:
            command, reason = "skip", "no legal shortlisted card"
        else:
            command, reason = "choose 0", "forced pick without a shortlist"
        proposal = {
            "action": command.split()[0],
            "choice_id": int(command.split()[1]) if command.startswith("choose ") else None,
            "targets": [],
            "reason": reason,
        }
        approval: Mapping[str, Any] | None
        try:
            approval = self._picker.approve(result, proposal)
        except CardRewardError:
            approval = None
        return policy_decision(
            request,
            command,
            "card_reward.policy_heuristic",
            reason,
            payload={
                **self._picker.decision_payload(result, command=command, approval=approval),
                "heuristic_proposal": proposal,
            },
        )

    # -- shops ------------------------------------------------------------

    def _shop(self, request: DecisionRequest) -> Decision:
        state = request.state
        previous = request.previous
        if (
            not previous.confirmed
            and previous.state.screen.type == "SHOP_SCREEN"
            and "leave" in state.screen.commands
        ):
            return policy_decision(
                request, "leave", "build.shop_heuristic", "previous purchase was rejected; leave"
            )
        details = state.screen.details
        gold = _int(state.facts.get("gold"))
        deck = state.facts.get("deck")
        policy = self._picker.review_shop(request)
        options: list[tuple[float, float, str, str, tuple[str, ...], dict[str, Any]]] = []

        purge_id = policy.get("purge_choice_id")
        purge_cost = float(policy.get("purge_cost") or 0)
        if (
            isinstance(purge_id, int)
            and details.get("purge_available", True)
            and purge_cost > 0
            and gold >= purge_cost
        ):
            targets = card_values.removal_targets(deck, 1)
            if targets:
                value = card_values.removal_value(targets[0])
                if value >= 20:
                    score = 3.0 if value >= 90 else 2.4 if value >= 45 else 1.7
                    options.append(
                        (score, purge_cost, f"choose {purge_id}", f"remove {targets[0]}", (targets[0],), {})
                    )

        allowed = {int(value) for value in policy.get("allowed_card_choice_ids") or ()}
        result = policy.get("policy_result") if isinstance(policy.get("policy_result"), Mapping) else {}
        direct = str(result.get("command") or "")
        candidates = {
            int(row["choice_id"]): row
            for row in result.get("candidates") or ()
            if isinstance(row, Mapping) and "choice_id" in row
        }
        for local, row in enumerate(policy.get("card_choices") or ()):
            if not isinstance(row, Mapping):
                continue
            cid, price = int(row["choice_id"]), float(row.get("price") or 0)
            if cid not in allowed or price > gold:
                continue
            score = 1.2 + 0.12 * _candidate_score(candidates.get(local, {}))
            if direct == f"choose {local}":
                score += 1.0
            options.append(
                (
                    score,
                    price,
                    f"choose {cid}",
                    f"buy {row.get('name')}",
                    (),
                    {"shop_card_policy": dict(policy), "shop_card_local_id": local},
                )
            )

        labels = [normalize_event(_label(choice)) for choice in state.screen.choices]
        for relic in _sequence(details.get("relics")):
            if not isinstance(relic, Mapping):
                continue
            name = str(relic.get("name") or relic.get("id") or "")
            price = float(relic.get("price") or 0)
            cid = next((i for i, label in enumerate(labels) if label == normalize_event(name)), None)
            if cid is None or price <= 0 or price > gold:
                continue
            value = shop_relic_value(name, price)
            if value is None:
                continue
            options.append((value, price, f"choose {cid}", f"buy relic {name}", (), {}))

        if not options:
            return policy_decision(
                request, "leave", "build.shop_heuristic", "nothing affordable is worth buying"
            )
        score, price, command, reason, targets, extra = max(
            options, key=lambda item: (item[0], -item[1])
        )
        if score < 1.0:
            return policy_decision(
                request, "leave", "build.shop_heuristic", "remaining offers score too low"
            )
        payload: dict[str, Any] = {"shop_heuristic": {"score": score, "price": price}, **extra}
        if payload.pop("shop_card_local_id", None) is not None:
            payload.update(
                self._picker.shop_decision_payload(
                    policy,
                    {
                        "data": {"action": "choose", "choice_id": int(command.split()[1]), "targets": [], "reason": reason},
                        "approved": True,
                        "veto_reason": None,
                    },
                )
            )
        return policy_decision(
            request, command, "build.shop_heuristic", reason, targets=targets, payload=payload
        )

    # -- rest sites -------------------------------------------------------

    def _rest(self, request: DecisionRequest) -> Decision:
        state = request.state
        legal = self._legal_ids(request)
        labels = [normalize_event(_label(choice)) for choice in state.screen.choices]

        def find(name: str) -> int | None:
            return next((i for i in legal if labels[i] == name), None)

        current, maximum = _int(state.facts.get("current_hp")), max(1, _int(state.facts.get("max_hp")))
        ratio = current / maximum
        relics = {normalize_event(name) for name in _names(state.facts.get("relics"))}
        heals = not (relics & _NO_HEAL_RELICS)
        threshold = _rest_threshold(request.shared)
        deck = state.facts.get("deck")
        rest, smith = find("rest"), find("smith")
        if rest is not None and heals and ratio < threshold:
            return policy_decision(
                request,
                f"choose {rest}",
                "build.rest_heuristic",
                f"rest at {current}/{maximum} HP (threshold {threshold:.0%})",
            )
        if smith is not None:
            targets = card_values.upgrade_targets(deck, 1)
            if targets:
                return policy_decision(
                    request,
                    f"choose {smith}",
                    "build.rest_heuristic",
                    f"smith {targets[0]}",
                    targets=(targets[0],),
                )
        for name in ("lift", "dig"):
            index = find(name)
            if index is not None:
                return policy_decision(request, f"choose {index}", "build.rest_heuristic", name)
        toke = find("toke")
        if toke is not None:
            targets = card_values.removal_targets(deck, 1)
            if targets and card_values.removal_value(targets[0]) >= 20:
                return policy_decision(
                    request, f"choose {toke}", "build.rest_heuristic", f"toke {targets[0]}", targets=(targets[0],)
                )
        if rest is not None and heals and current < maximum:
            return policy_decision(
                request, f"choose {rest}", "build.rest_heuristic", "nothing to upgrade; rest"
            )
        recall = find("recall")
        if recall is not None:
            return policy_decision(request, f"choose {recall}", "build.rest_heuristic", "recall for the Ruby Key")
        if rest is not None:
            return policy_decision(request, f"choose {rest}", "build.rest_heuristic", "rest as the last option")
        return self._first_legal(request, legal, "build.rest_heuristic")

    # -- events -----------------------------------------------------------

    def _event(self, request: DecisionRequest) -> Decision:
        state = request.state
        legal = self._legal_ids(request)
        details = state.screen.details
        options = {
            _int(option.get("choice_index", index)): option
            for index, option in enumerate(_sequence(details.get("options")))
            if isinstance(option, Mapping)
        }
        labels = tuple(normalize_event(_label(choice)) for choice in state.screen.choices)
        texts = tuple(
            normalize_event(
                f"{_label(choice)} {options.get(i, {}).get('label', '')} {options.get(i, {}).get('text', '')}"
            )
            for i, choice in enumerate(state.screen.choices)
        )
        legal = [i for i in legal if not options.get(i, {}).get("disabled")] or legal
        ctx = _event_context(state, labels, texts)
        key = event_key(details)
        ordered, rule = rank_choices(key, ctx, legal)
        if not ordered:
            raise BuildError("event screen has no legal choice")
        for cid in ordered:
            kinds = selection_kinds(state, cid)
            targets: tuple[str, ...] = ()
            if kinds:
                targets = tuple(self._targets_for(state, kinds, _target_count(texts[cid])))
                if not targets:
                    continue
            return policy_decision(
                request,
                f"choose {cid}",
                "build.event_heuristic",
                f"{rule}: {_label(state.screen.choices[cid])}",
                targets=targets,
                payload={"event_rule": rule, "choice_id": cid, "event_key": key or ""},
            )
        cid = ordered[0]
        return policy_decision(
            request,
            f"choose {cid}",
            "build.event_heuristic",
            f"{rule}: {_label(state.screen.choices[cid])} (selector decided later)",
            payload={"event_rule": rule, "choice_id": cid, "event_key": key or ""},
        )

    # -- boss relics ------------------------------------------------------

    def _boss_reward(self, request: DecisionRequest) -> Decision:
        state = request.state
        legal = self._legal_ids(request)
        labels = [_label(choice) for choice in state.screen.choices]
        ranked = sorted(legal, key=lambda i: (-boss_relic_value(labels[i]), i))
        for cid in ranked:
            if boss_relic_value(labels[cid]) <= 0:
                continue
            kinds = selection_kinds(state, cid)
            targets: tuple[str, ...] = ()
            if kinds:
                name = normalize_event(labels[cid])
                count = 3 if "astrolabe" in name else 2 if "empty cage" in name else 1
                targets = tuple(self._targets_for(state, kinds, count))
                if len(targets) < count:
                    continue
            return policy_decision(
                request,
                f"choose {cid}",
                "build.boss_relic_heuristic",
                f"take {labels[cid]}",
                targets=targets,
                payload={"relic": labels[cid]},
            )
        if "skip" in state.screen.commands:
            return policy_decision(request, "skip", "build.boss_relic_heuristic", "no acceptable boss relic")
        return self._first_legal(request, legal, "build.boss_relic_heuristic")

    # -- unplanned card selectors ----------------------------------------

    def _selector(self, request: DecisionRequest) -> Decision:
        state = request.state
        details = state.screen.details
        choices = [_label(choice) for choice in state.screen.choices]
        if not choices:
            if "confirm" in state.screen.commands:
                return policy_decision(request, "confirm", "build.selector_heuristic", "nothing left to select")
            raise BuildError(f"{state.screen.type} selector exposes no choices")
        selected = [
            str(card.get("name") or card.get("id") or "")
            for card in _sequence(details.get("selected_cards"))
            if isinstance(card, Mapping)
        ]
        wanted = max(1, _int(details.get("num_cards") or details.get("max_cards") or 1))
        if len(selected) >= wanted and "confirm" in state.screen.commands:
            return policy_decision(request, "confirm", "build.selector_heuristic", "selection complete")
        operation = str(details.get("grid_operation") or "").upper()
        action = str(state.screen.current_action or "").casefold()
        if details.get("for_upgrade") or operation == "UPGRADE":
            mode = "upgrade"
        elif (
            details.get("for_purge")
            or details.get("for_transform")
            or operation in {"REMOVE", "TRANSFORM"}
            or "discard" in action
            or "exhaust" in action
        ):
            mode = "remove"
        else:
            mode = "pick"
        remaining = list(selected)
        scored = []
        for index, label in enumerate(choices):
            if label in remaining:
                remaining.remove(label)
                continue
            if mode == "upgrade":
                if label.rstrip().endswith("+") or re.search(r"\+\d+$", label.rstrip()):
                    continue
                value = card_values.upgrade_value(label)
            elif mode == "remove":
                value = card_values.removal_value(label)
            else:
                value = card_values.pick_value(label)
            scored.append((value, -index, index))
        if not scored:
            if "confirm" in state.screen.commands:
                return policy_decision(request, "confirm", "build.selector_heuristic", "no further legal target")
            scored = [(0.0, -i, i) for i in range(len(choices))]
        _value, _neg, index = max(scored)
        return policy_decision(
            request,
            f"choose {index}",
            "build.selector_heuristic",
            f"{mode} {choices[index]}",
            payload={"selector_mode": mode, "card": choices[index]},
        )

    # -- helpers ----------------------------------------------------------

    def _generic(self, request: DecisionRequest) -> Decision:
        state = request.state
        for action in ("proceed", "leave", "skip", "confirm"):
            if action in state.screen.commands:
                return policy_decision(request, action, "build.generic_heuristic", f"{action} through {state.screen.type}")
        if "choose" in state.screen.commands and state.screen.choices:
            return policy_decision(request, "choose 0", "build.generic_heuristic", f"first choice on {state.screen.type}")
        raise BuildError(f"heuristic Build agent cannot act on screen {state.screen.type}")

    def _legal_ids(self, request: DecisionRequest) -> list[int]:
        count = len(request.state.screen.choices)
        rule = self._choice_policy(request)
        if isinstance(rule, Mapping):
            legal = [int(value) for value in rule.get("legal_choice_ids") or () if 0 <= int(value) < count]
            if legal:
                return legal
        return list(range(count))

    def _first_legal(self, request: DecisionRequest, legal: Sequence[int], source: str) -> Decision:
        if not legal:
            raise BuildError(f"no legal choice on {request.state.screen.type}")
        return policy_decision(request, f"choose {legal[0]}", source, "first legal choice")

    @staticmethod
    def _targets_for(state: GameState, kinds: frozenset[str], count: int) -> list[str]:
        deck = state.facts.get("deck")
        if "upgrade" in kinds and "remove" not in kinds:
            return card_values.upgrade_targets(deck, count)
        if "duplicate" in kinds and not (kinds & {"remove", "transform"}):
            best = card_values.upgrade_targets(deck, 1)
            if best:
                return best
            rows = card_values.deck_rows(deck)
            return [rows[0]["name"]] if rows else []
        targets = card_values.removal_targets(deck, count)
        if "transform" in kinds and "remove" not in kinds:
            return targets[:count]
        return [name for name in targets if card_values.removal_value(name) > -5][:count]


def create_heuristic_build_agent(
    card_picker: CardPicker,
    *,
    choice_policy: Callable[[DecisionRequest], Mapping[str, Any] | None] = build_choice_policy,
) -> BuildAgent:
    return BuildAgent(tool_stages=(HeuristicBuildStage(card_picker, choice_policy),))


def _candidate_score(row: Mapping[str, Any]) -> float:
    if not row or row.get("rejected"):
        return -100.0
    score = 0.0
    expert = row.get("expert") if isinstance(row.get("expert"), Mapping) else {}
    level = str(expert.get("level") or "")
    score += {"DIRECT": 2.0, "POSITIVE": 1.0, "NEGATIVE": -2.0}.get(level, 0.0)
    try:
        score += 0.2 * float(expert.get("score") or 0.0)
    except (TypeError, ValueError):
        pass
    template = row.get("template") if isinstance(row.get("template"), Mapping) else {}
    if str(template.get("level") or "NONE") != "NONE":
        score += 3.0
    transition = row.get("transition") if isinstance(row.get("transition"), Mapping) else {}
    transition_level = str(transition.get("level") or "NONE")
    if transition_level == "BLOCKING_NEED":
        score += 5.0
    elif transition_level != "NONE":
        score += 1.5
    if row.get("hard_constraints"):
        score -= 3.0
    return score


def _rest_threshold(shared: Mapping[str, object]) -> float:
    route = shared.get(RUN_ROUTE_KEY)
    rooms: list[str] = []
    if isinstance(route, Mapping):
        planned = route.get("planned_rooms")
        if isinstance(planned, Sequence) and not isinstance(planned, (str, bytes)):
            rooms = [str(room) for room in planned]
        else:
            rooms = [
                str(row.get("room") or "")
                for row in _sequence(route.get("forced_segment"))
                if isinstance(row, Mapping)
            ]
    upcoming = rooms[1:] if rooms else []
    for room in upcoming:
        name = normalize_event(room)
        if name in {"rest", "r"}:
            break
        if name in {"boss"}:
            return _REST_THRESHOLD_BOSS
        if name in {"elite", "burning elite", "e", "e*"}:
            return _REST_THRESHOLD_ELITE
    return _REST_THRESHOLD


def _event_context(state: GameState, labels: tuple[str, ...], texts: tuple[str, ...]) -> EventContext:
    facts = state.facts
    rows = card_values.deck_rows(facts.get("deck"))
    basics = sum(1 for row in rows if card_values.normalize(row["name"]) in {"strike", "defend"})
    curses = sum(
        1
        for row in rows
        if card_values.is_curse(row["name"], row["type"])
        and card_values.normalize(row["name"]) not in card_values.UNREMOVABLE
    )
    potions = _sequence(facts.get("potions"))
    empty = 0
    for slot in potions:
        name = slot.get("name") or slot.get("id") if isinstance(slot, Mapping) else slot
        if name in (None, "") or normalize_event(name) in {"potion slot", "empty", "empty slot"}:
            empty += 1
    return EventContext(
        labels=labels,
        texts=texts,
        hp=_int(facts.get("current_hp")),
        max_hp=max(1, _int(facts.get("max_hp"))),
        gold=_int(facts.get("gold")),
        act=_int(facts.get("act")),
        floor=_int(facts.get("floor")),
        ascension=_int(facts.get("ascension_level")),
        relics=frozenset(normalize_event(name) for name in _names(facts.get("relics"))),
        deck_size=len(rows),
        basics=basics,
        curses=curses,
        empty_potion_slots=empty,
    )


def _target_count(text: str) -> int:
    match = re.search(r"(\d+)\s+cards?", text)
    return max(1, int(match.group(1))) if match else 1


def _label(value: object) -> str:
    if isinstance(value, Mapping):
        return str(value.get("name") or value.get("value") or value.get("text") or "")
    return str(value)


def _names(value: object) -> list[str]:
    result = []
    for item in _sequence(value):
        name = item.get("name") or item.get("id") if isinstance(item, Mapping) else item
        if name not in (None, ""):
            result.append(str(name))
    return result


def _sequence(value: object) -> tuple[Any, ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(value)
    return ()


def _int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


__all__ = ["HeuristicBuildStage", "create_heuristic_build_agent"]
