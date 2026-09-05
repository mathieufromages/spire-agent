"""Rule-based map and build agents must decide every scene without an LLM."""

from __future__ import annotations

import unittest

from spire_agent.contracts import (
    AgentKind,
    ContextEntry,
    ContinuationOperation,
    DecisionRequest,
    DecisionScope,
    GameState,
    ScreenState,
)
from spire_agent.run_agent import runtime_registry
from spire_agent.subagents.map import create_map_agent
from spire_agent.tools.build_flow import build_choice_policy
from spire_agent.tools.heuristics import HeuristicBuildStage, create_heuristic_build_agent
from spire_agent.tools.heuristics.events import EventContext, neow_option_score, rank_choices
from spire_agent.tools.map import HeuristicMapTool
from spire_agent.tools.mcts import DefaultCombatTool, MCTSResult
from spire_agent.tools.run_keys import RUN_ROUTE_KEY
from spire_agent.tools.winning_path import WinningPathCardPicker


class NeverCalledLLM:
    def complete(self, request):  # pragma: no cover - the test fails if reached
        raise AssertionError(f"LLM called for {request.purpose}")


def map_state(*, hp=70, max_hp=80, act=1, nodes=None, choices=("x=0", "x=2")) -> GameState:
    if nodes is None:
        nodes = [
            {"x": 0, "y": 0, "symbol": "M", "children": [{"x": 0, "y": 1}]},
            {"x": 0, "y": 1, "symbol": "E", "children": [{"x": 3, "y": 16}]},
            {"x": 2, "y": 0, "symbol": "M", "children": [{"x": 2, "y": 1}]},
            {"x": 2, "y": 1, "symbol": "R", "children": [{"x": 3, "y": 16}]},
        ]
    return GameState(
        owner_hint=AgentKind.MAP,
        scope_id="seed:a1:f0:map:map",
        screen=ScreenState(type="MAP", commands=("choose", "state"), choices=choices),
        facts={
            "class": "IRONCLAD",
            "ascension_level": 0,
            "act": act,
            "floor": 0,
            "current_hp": hp,
            "max_hp": max_hp,
            "gold": 99,
            "act_boss": "The Guardian",
            "deck": [{"name": "Strike"}, {"name": "Strike"}, {"name": "Bash"}],
            "relics": [{"name": "Burning Blood"}],
            "potions": [{"name": "Potion Slot"}],
            "map": nodes,
        },
    )


def build_state(screen, *, commands=("choose",), choices=(), details=None, facts=None) -> GameState:
    return GameState(
        owner_hint=AgentKind.BUILD,
        scope_id="seed:a1:f2:event:build",
        screen=ScreenState(type=screen, commands=commands, choices=choices, details=details or {}),
        facts={
            "class": "IRONCLAD",
            "act": 1,
            "floor": 2,
            "ascension_level": 0,
            "current_hp": 60,
            "max_hp": 80,
            "gold": 99,
            "deck": [
                {"name": "Strike", "count": 5, "upgrades": 0},
                {"name": "Defend", "count": 4, "upgrades": 0},
                {"name": "Bash", "count": 1, "upgrades": 0},
                {"name": "Shrug It Off", "count": 1, "upgrades": 0},
            ],
            "relics": [{"name": "Burning Blood"}],
            "potions": [{"name": "Potion Slot"}],
            **(facts or {}),
        },
    )


def request(state: GameState, *, shared=None) -> DecisionRequest:
    return DecisionRequest(
        state=state,
        scope=DecisionScope(state.owner_hint, state.scope_id),
        continuation=None,
        shared=shared or {},
        previous=ContextEntry(0, None, state, True),
    )


def build_agent():
    return create_heuristic_build_agent(WinningPathCardPicker(), choice_policy=build_choice_policy)


class HeuristicMapTests(unittest.TestCase):
    def test_route_is_legal_and_recorded_without_an_llm(self):
        decision = create_map_agent(HeuristicMapTool()).decide(request(map_state()))

        self.assertIn(decision.command, {"choose 0", "choose 1"})
        self.assertEqual(decision.source, "map.heuristic")
        route = decision.payload[RUN_ROUTE_KEY]
        self.assertEqual(route["planned_path"][-1], "BOSS")
        self.assertEqual(route["planned_rooms"][-1], "Boss")

    def test_healthy_run_takes_the_elite_and_low_hp_takes_the_rest(self):
        healthy = create_map_agent(HeuristicMapTool()).decide(request(map_state(hp=80)))
        wounded = create_map_agent(HeuristicMapTool()).decide(request(map_state(hp=22)))

        self.assertEqual(healthy.command, "choose 0")
        self.assertEqual(healthy.payload["room"], "M")
        self.assertIn("Elite", healthy.payload[RUN_ROUTE_KEY]["planned_rooms"])
        self.assertEqual(wounded.command, "choose 1")
        self.assertIn("Rest", wounded.payload[RUN_ROUTE_KEY]["planned_rooms"])

    def test_single_boss_entrance_is_forced(self):
        state = GameState(
            owner_hint=AgentKind.MAP,
            scope_id="seed:a1:f16:map:map",
            screen=ScreenState(type="MAP", commands=("choose",), choices=("boss",)),
            facts={"act": 1, "map": []},
        )
        decision = create_map_agent(HeuristicMapTool()).decide(request(state))
        self.assertEqual(decision.command, "choose 0")


class HeuristicRestTests(unittest.TestCase):
    def test_low_hp_rests(self):
        state = build_state("REST", choices=("rest", "smith"), facts={"current_hp": 30})
        decision = build_agent().decide(request(state))

        self.assertEqual(decision.command, "choose 0")
        self.assertEqual(decision.source, "build.rest_heuristic")

    def test_healthy_run_smiths_the_best_unupgraded_card(self):
        state = build_state("REST", choices=("rest", "smith"), facts={"current_hp": 75})
        decision = build_agent().decide(request(state))

        self.assertEqual(decision.command, "choose 1")
        self.assertEqual(decision.payload["targets"], ("Shrug It Off",))
        self.assertIs(decision.continuation.operation, ContinuationOperation.SET)
        self.assertEqual(decision.continuation.value.data["targets"], ("Shrug It Off",))

    def test_elite_ahead_raises_the_rest_threshold(self):
        state = build_state("REST", choices=("rest", "smith"), facts={"current_hp": 54})
        shared = {RUN_ROUTE_KEY: {"planned_rooms": ["Rest", "Elite", "Rest", "Boss"]}}
        decision = build_agent().decide(request(state, shared=shared))
        self.assertEqual(decision.command, "choose 0")

        calm = {RUN_ROUTE_KEY: {"planned_rooms": ["Rest", "Event", "Rest", "Boss"]}}
        decision = build_agent().decide(request(state, shared=calm))
        self.assertEqual(decision.command, "choose 1")

    def test_recall_stays_deferred_before_act_three(self):
        state = build_state("REST", choices=("rest", "smith", "recall"), facts={"current_hp": 79})
        decision = build_agent().decide(request(state))
        self.assertEqual(decision.command, "choose 1")


class HeuristicShopTests(unittest.TestCase):
    def shop(self, gold, *, choices, cards=(), relics=(), purge_cost=75):
        return build_state(
            "SHOP_SCREEN",
            commands=("choose", "leave"),
            choices=choices,
            details={
                "cards": tuple(cards),
                "relics": tuple(relics),
                "potions": (),
                "purge_cost": purge_cost,
                "purge_available": "purge" in choices,
            },
            facts={"gold": gold},
        )

    def test_affordable_removal_targets_a_strike(self):
        state = self.shop(
            120,
            choices=("purge", "clash", "lantern"),
            cards=({"name": "Clash", "price": 45},),
            relics=({"name": "Lantern", "price": 147},),
        )
        decision = build_agent().decide(request(state))

        self.assertEqual(decision.command, "choose 0")
        self.assertEqual(decision.payload["targets"], ("Strike",))
        self.assertEqual(decision.source, "build.shop_heuristic")

    def test_unaffordable_shop_is_left_and_continues_past_the_room(self):
        state = self.shop(
            10,
            choices=("purge", "clash"),
            cards=({"name": "Clash", "price": 45},),
        )
        decision = build_agent().decide(request(state))

        self.assertEqual(decision.command, "leave")
        self.assertIs(decision.continuation.operation, ContinuationOperation.SET)
        self.assertEqual(decision.continuation.value.data["flow"], "shop_exit")

    def test_relic_is_bought_when_removal_is_gone(self):
        state = self.shop(
            200,
            choices=("lantern", "dolly's mirror"),
            relics=({"name": "Lantern", "price": 147}, {"name": "Dolly's Mirror", "price": 150}),
        )
        decision = build_agent().decide(request(state))
        self.assertEqual(decision.command, "choose 0")
        self.assertIn("Lantern", decision.reason)


class HeuristicBossRelicTests(unittest.TestCase):
    def test_coffee_dripper_respects_the_healing_policy_and_runic_dome_is_avoided(self):
        choices = ("runic dome", "coffee dripper", "snecko eye")
        no_healing = build_state(
            "BOSS_REWARD", commands=("choose", "skip"), choices=choices, facts={"relics": []}
        )
        decision = build_agent().decide(request(no_healing))
        self.assertEqual(decision.command, "choose 2")

        with_healing = build_state("BOSS_REWARD", commands=("choose", "skip"), choices=choices)
        decision = build_agent().decide(request(with_healing))
        self.assertEqual(decision.command, "choose 1")

    def test_astrolabe_supplies_three_transform_targets(self):
        state = build_state(
            "BOSS_REWARD", commands=("choose", "skip"), choices=("astrolabe", "runic dome")
        )
        decision = build_agent().decide(request(state))
        self.assertEqual(decision.command, "choose 0")
        self.assertEqual(len(decision.payload["targets"]), 3)
        self.assertTrue(set(decision.payload["targets"]) <= {"Strike", "Defend"})


class HeuristicEventTests(unittest.TestCase):
    def event(self, event_id, choices, *, facts=None, texts=None):
        options = [
            {"choice_index": i, "disabled": False, "label": label, "text": (texts or {}).get(i, f"[{label}]")}
            for i, label in enumerate(choices)
        ]
        return build_state(
            "EVENT",
            choices=tuple(label.casefold() for label in choices),
            details={"event_id": event_id, "event_name": event_id, "options": options},
            facts=facts,
        )

    def test_unknown_event_leaves(self):
        state = self.event("Totally New Event", ("Gamble", "Leave"))
        decision = build_agent().decide(request(state))
        self.assertEqual(decision.command, "choose 1")
        self.assertIn("default", decision.reason)

    def test_big_fish_heals_when_hurt_and_grows_when_healthy(self):
        hurt = self.event("Big Fish", ("Banana", "Donut", "Box"), facts={"current_hp": 30})
        healthy = self.event("Big Fish", ("Banana", "Donut", "Box"), facts={"current_hp": 78})
        self.assertEqual(build_agent().decide(request(hurt)).command, "choose 0")
        self.assertEqual(build_agent().decide(request(healthy)).command, "choose 1")

    def test_neow_prefers_a_relic_and_never_swaps_the_starter(self):
        choices = (
            "Lose your starting Relic Obtain a random boss Relic",
            "Obtain a random common Relic",
            "Enemies in your next three combats have 1 HP",
        )
        decision = build_agent().decide(request(self.event("Neow Event", choices)))
        self.assertEqual(decision.command, "choose 1")

    def test_neow_scores_costs_against_gains(self):
        ctx = EventContext((), (), 80, 80, 99, 1, 0, 0, frozenset(), 10, 9, 0, 3)
        self.assertGreater(
            neow_option_score("obtain 100 gold", ctx),
            neow_option_score("lose all gold choose a rare colorless card to obtain", ctx),
        )
        self.assertLess(neow_option_score("lose your starting relic obtain a random boss relic", ctx), 0)

    def test_removal_event_plans_the_worst_card(self):
        state = self.event("Purifier", ("Pray", "Leave"), texts={0: "[Pray] Remove a card from your deck."})
        decision = build_agent().decide(request(state))
        self.assertEqual(decision.command, "choose 0")
        self.assertEqual(decision.payload["targets"], ("Strike",))

    def test_falling_loses_the_least_valuable_named_card(self):
        state = self.event(
            "Falling",
            ("Land", "Channel", "Strike"),
            texts={0: "[Land] Lose Shrug It Off", 1: "[Channel] Lose Demon Form", 2: "[Strike] Lose Strike"},
        )
        decision = build_agent().decide(request(state))
        self.assertEqual(decision.command, "choose 2")

    def test_rank_choices_orders_by_marker_then_fills_the_rest(self):
        ctx = EventContext(("accept", "refuse"), ("accept", "refuse"), 80, 80, 0, 2, 20, 0, frozenset(), 10, 9, 0, 3)
        ordered, rule = rank_choices("ghosts", ctx, [0, 1])
        self.assertEqual((ordered, rule), ([1, 0], "ghosts"))


class HeuristicSelectorTests(unittest.TestCase):
    def test_unplanned_upgrade_grid_picks_the_best_card(self):
        state = build_state(
            "GRID",
            choices=("strike", "defend", "shrug it off"),
            details={"for_upgrade": True, "num_cards": 1, "selected_cards": []},
        )
        decision = build_agent().decide(request(state))
        self.assertEqual(decision.command, "choose 2")

    def test_unplanned_removal_grid_picks_the_worst_card(self):
        state = build_state(
            "GRID",
            choices=("shrug it off", "strike", "defend"),
            details={"for_purge": True, "num_cards": 1, "selected_cards": []},
        )
        decision = build_agent().decide(request(state))
        self.assertEqual(decision.command, "choose 1")


class FakePicker(WinningPathCardPicker):
    def __init__(self, result):
        super().__init__("IRONCLAD")
        self.result = result

    def review(self, request):
        return dict(self.result)


class HeuristicCardRewardTests(unittest.TestCase):
    def test_direct_picker_command_is_used(self):
        picker = FakePicker(
            {
                "owner": "CardRewardPolicy", "mode": "DIRECT", "policy": "TEMPLATE_PROGRESS",
                "command": "choose 1", "reason": "TEMPLATE_PROGRESS", "allowed_choice_ids": [1],
                "allow_skip": True, "bowl_choice_id": None, "candidates": [], "winning_path": {},
            }
        )
        state = build_state("CARD_REWARD", commands=("choose", "skip"), choices=("anger", "shrug it off"))
        decision = create_heuristic_build_agent(picker).decide(request(state))
        self.assertEqual((decision.command, decision.source), ("choose 1", "card_reward.policy"))

    def test_advice_required_picks_the_best_scored_shortlist_card(self):
        picker = FakePicker(
            {
                "owner": "CardRewardPolicy", "mode": "ADVICE_REQUIRED", "policy": "EXPERT_EXPERIENCE_CONFLICT",
                "command": None, "reason": "conflict", "allowed_choice_ids": [0, 1], "allow_skip": True,
                "bowl_choice_id": None,
                "candidates": [
                    {"choice_id": 0, "name": "Anger", "expert": {"level": "DIRECT", "score": 1.0}},
                    {"choice_id": 1, "name": "Shrug It Off", "expert": {"level": "DIRECT", "score": 6.0}},
                ],
                "winning_path": {},
            }
        )
        state = build_state("CARD_REWARD", commands=("choose", "skip"), choices=("anger", "shrug it off"))
        decision = create_heuristic_build_agent(picker).decide(request(state))
        self.assertEqual((decision.command, decision.source), ("choose 1", "card_reward.policy_heuristic"))
        self.assertEqual(decision.payload["card_reward_policy_result"]["card"], "Shrug It Off")


class DeckGuardTests(unittest.TestCase):
    def _direct(self, choice, choices, *, deck, act=1, bowl=None):
        picker = FakePicker(
            {
                "owner": "CardRewardPolicy", "mode": "DIRECT", "policy": "TEMPLATE_PROGRESS",
                "command": f"choose {choice}", "reason": "TEMPLATE_PROGRESS",
                "allowed_choice_ids": [choice], "allow_skip": False, "bowl_choice_id": bowl,
                "candidates": [], "winning_path": {},
            }
        )
        state = build_state(
            "CARD_REWARD", commands=("choose", "skip"), choices=choices,
            details={"cards": [{"name": c.title()} for c in choices]},
            facts={"deck": deck, "act": act},
        )
        return create_heuristic_build_agent(picker).decide(request(state))

    def test_second_copy_of_a_single_copy_power_is_skipped(self):
        deck = [{"name": "Strike", "count": 5}, {"name": "Dark Embrace", "count": 1, "upgrades": 1}]
        decision = self._direct(0, ("dark embrace", "anger"), deck=deck)
        self.assertEqual((decision.command, decision.source), ("skip", "card_reward.deck_guard"))
        self.assertIn("power limit 1", decision.reason)

    def test_stacking_power_is_still_taken(self):
        deck = [{"name": "Strike", "count": 5}, {"name": "Demon Form", "count": 2}]
        decision = self._direct(0, ("demon form", "anger"), deck=deck, act=3)
        self.assertEqual(decision.command, "choose 0")

    def test_late_filler_is_skipped_but_scaling_is_taken(self):
        deck = [{"name": "Strike", "count": 5}, {"name": "Defend", "count": 4}, {"name": "Bash", "count": 1}] + [
            {"name": name, "count": 1}
            for name in (
                "Shrug It Off", "Whirlwind", "Headbutt", "Uppercut", "Body Slam", "Feed", "Disarm",
                "Impervious", "Offering", "Metallicize", "Flame Barrier", "True Grit", "Rampage",
                "Blind", "Madness", "Corruption",
            )
        ]
        self.assertEqual(self._direct(0, ("anger", "inflame"), deck=deck, act=3).command, "skip")
        self.assertEqual(self._direct(1, ("anger", "inflame"), deck=deck, act=3).command, "choose 1")

    def test_singing_bowl_replaces_a_vetoed_pick(self):
        deck = [{"name": "Strike", "count": 5}, {"name": "Corruption", "count": 1}]
        decision = self._direct(0, ("corruption", "anger", "bowl"), deck=deck, bowl=2)
        self.assertEqual(decision.command, "choose 2")

    def test_boss_relics_avoid_fusion_hammer_and_cursed_key(self):
        state = build_state(
            "BOSS_REWARD", commands=("choose", "skip"),
            choices=("Fusion Hammer", "Cursed Key", "Black Blood"),
        )
        decision = build_agent().decide(request(state))
        self.assertEqual(decision.command, "choose 2")
        state = build_state(
            "BOSS_REWARD", commands=("choose", "skip"), choices=("Fusion Hammer", "Cursed Key", "Sozu")
        )
        self.assertEqual(build_agent().decide(request(state)).command, "choose 1")


class CoreOverrideTests(unittest.TestCase):
    def _decide(self, command, choices, *, deck, act=2, policy="EXPERT_EXPERIENCE"):
        picker = FakePicker(
            {
                "owner": "CardRewardPolicy", "mode": "DIRECT", "policy": policy,
                "command": command, "reason": policy, "allowed_choice_ids": [], "allow_skip": True,
                "bowl_choice_id": None, "candidates": [], "winning_path": {},
            }
        )
        state = build_state(
            "CARD_REWARD", commands=("choose", "skip"), choices=choices,
            details={"cards": [{"name": c.title()} for c in choices]},
            facts={"deck": deck, "act": act},
        )
        return create_heuristic_build_agent(picker).decide(request(state))

    DECK = [{"name": "Strike", "count": 4}, {"name": "Defend", "count": 4}, {"name": "Inflame", "count": 2}]

    def test_missing_scaling_core_beats_a_skip(self):
        decision = self._decide("skip", ("thunderclap", "demon form", "evolve"), deck=self.DECK, act=3)
        self.assertEqual((decision.command, decision.source), ("choose 1", "card_reward.core_override"))

    def test_missing_scaling_core_beats_a_filler_pick(self):
        decision = self._decide("choose 0", ("body slam", "reckless charge", "feel no pain"), deck=self.DECK, act=3)
        self.assertEqual(decision.command, "choose 2")

    def test_core_versus_core_is_left_to_the_picker(self):
        decision = self._decide("choose 0", ("corruption", "demon form"), deck=self.DECK, act=2)
        self.assertEqual((decision.command, decision.source), ("choose 0", "card_reward.policy"))

    def test_owned_core_is_not_taken_again(self):
        deck = self.DECK + [{"name": "Corruption", "count": 1}]
        decision = self._decide("skip", ("corruption", "anger"), deck=deck, act=2)
        self.assertEqual(decision.command, "skip")

    def test_finisher_is_taken_from_act_two_when_none_owned(self):
        self.assertEqual(self._decide("skip", ("immolate", "anger"), deck=self.DECK, act=2).command, "choose 0")
        self.assertEqual(self._decide("skip", ("immolate", "anger"), deck=self.DECK, act=1).command, "skip")

    def test_blocking_survival_pick_is_respected(self):
        decision = self._decide("choose 0", ("shrug it off", "demon form"), deck=self.DECK, policy="BLOCKING_SURVIVAL")
        self.assertEqual(decision.command, "choose 0")

    def test_neow_does_not_sell_max_hp_for_gold(self):
        from spire_agent.tools.heuristics.events import EventContext, neow_option_score
        ctx = EventContext(labels=(), texts=(), hp=80, max_hp=80, gold=99, act=1, floor=0, ascension=0,
                           relics=frozenset(), deck_size=10, basics=9, curses=0, empty_potion_slots=3)
        self.assertLess(neow_option_score("lose 8 max hp gain 250 gold", ctx), neow_option_score("max hp +8", ctx))


class ShopPotionTests(unittest.TestCase):
    def _shop(self, *, potions, act=4, gold=300):
        state = build_state(
            "SHOP_SCREEN", commands=("choose", "leave"),
            choices=("purge", "fear potion", "nunchaku"),
            details={
                "cards": [], "purge_cost": 500, "purge_available": True,
                "relics": [{"name": "Nunchaku", "price": 400}],
                "potions": [{"name": "Fear Potion", "price": 51}],
            },
            facts={"act": act, "gold": gold, "potions": potions},
        )
        return build_agent().decide(request(state))

    def test_empty_slot_buys_a_potion_late(self):
        decision = self._shop(potions=[None, None, None])
        self.assertEqual((decision.command, decision.reason), ("choose 1", "buy potion Fear Potion"))

    def test_full_slots_leave(self):
        decision = self._shop(potions=[{"name": "Block Potion"}, {"name": "Fire Potion"}])
        self.assertEqual(decision.command, "leave")

    def test_boss_relics_prefer_pandoras_box_to_philosophers_stone(self):
        state = build_state(
            "BOSS_REWARD", commands=("choose", "skip"),
            choices=("philosopher's stone", "tiny house", "pandora's box"),
        )
        self.assertEqual(build_agent().decide(request(state)).command, "choose 2")


class FakeCombatSearch:
    def choose(self, state):
        return MCTSResult(command="end", follow_up=None, metrics={"search_id": "test"})


class HeuristicRegistryTests(unittest.TestCase):
    def test_registry_composes_heuristic_agents_without_an_llm_client(self):
        registry = runtime_registry(
            None,
            HeuristicMapTool(),
            DefaultCombatTool(FakeCombatSearch()),
            map_implementation="heuristic",
            build_implementation="heuristic",
        )
        state = build_state(
            "EVENT",
            choices=("gamble", "leave"),
            details={"event_id": "Mystery", "options": []},
        )
        decision = registry.get(AgentKind.BUILD).decide(request(state))
        self.assertEqual(decision.command, "choose 1")

        map_decision = registry.get(AgentKind.MAP).decide(request(map_state()))
        self.assertTrue(map_decision.command.startswith("choose "))


if __name__ == "__main__":
    unittest.main()
