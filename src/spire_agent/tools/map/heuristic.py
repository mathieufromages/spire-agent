"""Rule-based map routing that never calls a language model.

The tool enumerates every complete route from each legal exit to the act boss,
simulates a crude HP budget along the route, and scores the rooms it passes.
The intent is a legal and sane picker: rest when low, take Elites when healthy,
value one funded shop per act, and never commit to a route the HP budget cannot
survive.  It reuses the deterministic route gate (``_policy_options``) so the
Act 3 key constraints and encounter-readiness filters still apply.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from spire_agent.contracts import Decision, DecisionRequest, GameState
from spire_agent.subagents.map import MapDecisionError, MapTool
from spire_agent.tools.map.tool import (
    _NODE_ID,
    _decision,
    _is_boss,
    _map_nodes,
    _names,
    _policy_options,
    _route_payload,
    forced_map_choice,
    render_map,
)
from spire_agent.tools.run_keys import key_view


_MAX_PATHS = 40_000
_NO_HEAL_RELICS = frozenset({"coffee dripper", "mark of the bloom"})


class HeuristicMapTool(MapTool):
    """Score complete routes with a fixed HP-budget heuristic."""

    def __init__(self, encounter_readiness: object | None = None) -> None:
        self._encounter_readiness = encounter_readiness

    def try_decide(self, request: DecisionRequest) -> Decision | None:
        state = request.state
        if state.screen.type != "MAP":
            return None
        if "choose" not in state.screen.commands:
            raise MapDecisionError("MAP screen does not expose the choose command")
        forced = forced_map_choice(state)
        if forced is not None:
            return _decision(state, forced, "map.single_choice", "only legal boss entrance")
        _graph, options = render_map(state)
        options, gate = _policy_options(request, options, self._encounter_readiness)
        if len(options) == 1 and options[0].get("planned_path"):
            return _decision(
                state,
                options[0],
                str(gate.get("source") or "map.single_choice"),
                str(gate.get("reason") or "only legal exit"),
            )
        try:
            need_emerald = not key_view(request.shared, state)["emerald"]
        except (KeyError, TypeError, ValueError):
            need_emerald = False
        option, score, summary = choose_route(state, options, need_emerald=need_emerald)
        reason = f"{summary} (score {score:.1f})"
        if gate.get("reason"):
            reason = f"{gate['reason']}; {reason}"
        return _decision(state, option, "map.heuristic", reason)


def choose_route(
    state: GameState, options: Sequence[Mapping[str, object]], *, need_emerald: bool = False
) -> tuple[dict[str, object], float, str]:
    """Return the best option extended with its planned route.

    ``need_emerald``: the Emerald Key is still missing.  In Act 3 the plan
    then has to pass through the Burning Elite (the route gate only keeps it
    *reachable*, so the planner used to dodge it until it was the only exit
    left: run 24I4U0C8BFNH8 was funnelled into M, M, M, E* at 37% HP with no
    potion and died on the first hallway).  Earlier acts get a bonus for a
    healthy Burning Elite so the key is usually banked before Act 3.
    """

    nodes = _map_nodes(state)
    context = _context(state)
    context["need_emerald"] = 1.0 if need_emerald else 0.0
    candidates: list[tuple[Mapping[str, object], list[tuple[tuple[int, int], ...]]]] = []
    for option in options:
        root = _coordinate(str(option.get("node") or ""))
        if root is None or root not in nodes:
            continue
        candidates.append((option, _enumerate_paths(root, nodes)))
    if need_emerald and int(context["act"]) >= 3:
        through = [
            (option, [path for path in paths if any(nodes[c][0] == "E*" for c in path)])
            for option, paths in candidates
        ]
        through = [(option, paths) for option, paths in through if paths]
        if through:
            candidates = through
    best: tuple[float, int, dict[str, object], str] | None = None
    for option, paths in candidates:
        if not paths:
            # No explicit edge to the boss (e.g. partial map data): score the
            # forced segment we do know about instead of failing.
            rooms = [
                str(row.get("room") or "?")
                for row in option.get("forced_segment", ())
                if isinstance(row, Mapping)
            ]
            score = _score_rooms(rooms, context)
            candidate = dict(option)
            summary = _summary(rooms)
        else:
            scored = max(
                ((_score_rooms([nodes[c][0] for c in path], context), path) for path in paths),
                key=lambda item: (item[0], -len(item[1])),
            )
            score, path = scored
            labels = tuple(f"L{y:02d}C{x}" for y, x in path) + ("BOSS",)
            candidate = {**option, **_route_payload(labels, nodes)}
            summary = _summary([nodes[c][0] for c in path])
        key = (score, -int(option["choice_id"]))
        if best is None or key > (best[0], -best[1]):
            best = (score, int(option["choice_id"]), candidate, summary)
    if best is None:
        raise MapDecisionError("no legal map option could be scored")
    return best[2], best[0], best[3]


def _enumerate_paths(
    root: tuple[int, int],
    nodes: Mapping[tuple[int, int], tuple[str, tuple[tuple[int, int], ...]]],
) -> list[tuple[tuple[int, int], ...]]:
    paths: list[tuple[tuple[int, int], ...]] = []
    stack: list[tuple[tuple[int, int], tuple[tuple[int, int], ...]]] = [(root, (root,))]
    while stack and len(paths) < _MAX_PATHS:
        current, path = stack.pop()
        _symbol, children = nodes[current]
        live = [child for child in children if child in nodes and not _is_boss(child, nodes)]
        boss = [child for child in children if child not in nodes or _is_boss(child, nodes)]
        if boss:
            paths.append(path)
        for child in live:
            if child not in path:
                stack.append((child, path + (child,)))
    return paths


def _context(state: GameState) -> dict[str, float]:
    facts = state.facts
    relics = {name.casefold() for name in _names(facts.get("relics"))}
    maximum = max(1, int(facts.get("max_hp") or 1))
    return {
        "act": int(facts.get("act") or 1),
        "floor": int(facts.get("floor") or 0),
        "hp": float(facts.get("current_hp") or 0),
        "max_hp": float(maximum),
        "gold": float(facts.get("gold") or 0),
        "ascension": float(facts.get("ascension_level") or 0),
        "rest_heals": 0.0 if relics & _NO_HEAL_RELICS else 1.0,
        "regal_pillow": 1.0 if "regal pillow" in relics else 0.0,
    }


def _score_rooms(rooms: Sequence[str], context: Mapping[str, float]) -> float:
    """Score one complete route with a simulated HP budget."""

    act = int(context["act"])
    max_hp = context["max_hp"]
    hp = context["hp"]
    gold = context["gold"]
    danger = 1.0 + context["ascension"] / 40.0
    late = act >= 2
    score = 0.0
    elites = shops = rests = 0
    for index, room in enumerate(rooms):
        ratio = hp / max_hp
        if room == "M":
            cost = max_hp * (0.10 if late else 0.07) * danger
            score += 0.45 if late else 0.65
            hp -= cost
        elif room == "?":
            cost = max_hp * 0.05 * danger
            score += 0.75
            hp -= cost
        elif room in {"E", "E*"}:
            elites += 1
            cost = max_hp * (0.30 if late else 0.24) * danger
            if room == "E*":
                cost *= 1.15
            value = (3.2, 2.2, 0.8, 0.0)[min(elites - 1, 3)]
            if room == "E*" and context.get("need_emerald"):
                # The Emerald Key: worth a healthy fight in Acts 1-2 (5 of 6
                # recorded early takers reached the Heart), mandatory in Act 3.
                value += 2.0 if act < 3 else 3.0
            if ratio < 0.55:
                value -= 3.0
            elif ratio < 0.70:
                value -= 1.2
            if act == 1 and index < 3 and context["floor"] < 4:
                value -= 1.0
            score += value
            hp -= cost
        elif room == "R":
            rests += 1
            heal = 0.0
            if context["rest_heals"]:
                heal = max_hp * 0.30 + (15.0 if context["regal_pillow"] else 0.0)
            missing = max(0.0, max_hp - max(hp, 0.0))
            recovered = min(heal, missing)
            score += 1.0 + 4.0 * recovered / max_hp
            hp = min(max_hp, hp + heal)
        elif room == "$":
            shops += 1
            expected_gold = gold + 12.0 * index
            # Gold is only worth what a shop turns it into: a removal, a core
            # card, a relic, potions.  Run 11 (2026-09-05) reached floor 48
            # with 1038 gold after an Act 2 route with no shop at all.
            spendable = min(max(expected_gold, 0.0), 600.0)
            if shops == 1:
                score += (0.6 if expected_gold < 150 else 1.6) + max(0.0, spendable - 150.0) / 150.0
            else:
                score += 0.25 + max(0.0, spendable - 300.0) / 300.0
            gold = max(0.0, expected_gold - min(expected_gold, 250.0)) - 12.0 * index
        elif room == "T":
            score += 1.2
        else:
            score += 0.3
        if hp <= 0:
            score -= 12.0
            hp = 1.0
        elif hp / max_hp < 0.25:
            score -= 3.0
    final_ratio = hp / max_hp
    if final_ratio < 0.65:
        score -= (0.65 - final_ratio) * 10.0
    return round(score, 3)


def _summary(rooms: Sequence[str]) -> str:
    counts = {symbol: rooms.count(symbol) for symbol in ("M", "?", "E", "E*", "R", "$", "T")}
    parts = [f"{count}{symbol}" for symbol, count in counts.items() if count]
    return "route " + " ".join(parts) if parts else "route"


def _coordinate(label: str) -> tuple[int, int] | None:
    match = _NODE_ID.fullmatch(label)
    if match is None:
        return None
    return int(match.group("layer")), int(match.group("column"))


__all__ = ["HeuristicMapTool", "choose_route"]
