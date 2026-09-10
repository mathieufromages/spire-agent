from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from spire_agent.contracts import AgentKind, GameState, ScreenState
from spire_agent.extensions import RunDirectory
from spire_agent.tools.mcts import MCTSResult, PotionGate
from spire_agent.tools.mcts.potion_gate import DANGER, EMERGENCY, assess_risk, potion_slots


def combat_state(*, heart=False, potion_count=5, current_hp=50):
    potions = [
        {
            "id": f"TestPotion{index}",
            "name": f"Test Potion {index}",
            "can_use": True,
            "requires_target": False,
        }
        for index in range(potion_count)
    ]
    return GameState(
        AgentKind.COMBAT,
        "seed:a4:f56:boss:combat" if heart else "seed:a1:f7:elite:combat",
        ScreenState("NONE", commands=("play", "end", "potion")),
        facts={
            "act": 4 if heart else 1,
            "act_boss": "Corrupt Heart" if heart else "Slime Boss",
            "room_type": "MonsterRoomBoss" if heart else "MonsterRoomElite",
            "current_hp": current_hp,
            "max_hp": 100,
            "potions": potions,
        },
        combat={
            "player": {"current_hp": current_hp, "max_hp": 100},
            "hand": ({"id": "Strike_R", "name": "Strike"},),
            "monsters": ({"id": "TestMonster", "current_hp": 100},),
        },
    )


def boss_state(*, potion_count=3, current_hp=50):
    base = combat_state(potion_count=potion_count, current_hp=current_hp)
    facts = {**base.facts, "act": 3, "act_boss": "Time Eater", "room_type": "MonsterRoomBoss"}
    return GameState(AgentKind.COMBAT, "seed:a3:f50:boss:combat", base.screen, facts=facts, combat=base.combat)


def hallway_state(*, act=2, room_type="MonsterRoom", potion_count=5, current_hp=50):
    """A combat state in an arbitrary act/room_type, defaulting to the Act 2
    hallway (floors 17-32, room_type "MonsterRoom") leading to the floor-33
    boss."""
    base = combat_state(potion_count=potion_count, current_hp=current_hp)
    facts = {**base.facts, "act": act, "act_boss": None, "room_type": room_type}
    return GameState(
        base.owner_hint,
        f"seed:a{act}:f20:{room_type.casefold()}:combat",
        base.screen,
        facts=facts,
        combat=base.combat,
    )


def entropic_brew_state(*, potions, current_hp=50):
    """A combat state whose potion belt is exactly `potions` (raw facts entries)."""
    base = combat_state(potion_count=1, current_hp=current_hp)
    return GameState(
        base.owner_hint,
        base.scope_id,
        base.screen,
        facts={**base.facts, "potions": tuple(potions)},
        combat=base.combat,
    )


def result(end_hp, *, credible=True, search_id="test"):
    return MCTSResult(
        "end",
        None,
        {
            "search_id": search_id,
            "credible_win_evidence": credible,
            "risk": {
                "winSamples": 100 if credible else 0,
                "lossSamples": 20,
                "winSampleRate": 0.8 if credible else 0.0,
                "meanBestWinEndHp": end_hp,
                "expectedEndHpOnWin": end_hp,
                "visits": 120,
            },
        },
    )


class FakeSearch:
    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = []

    def __call__(self, state, **kwargs):
        slots = tuple(kwargs.get("potion_slots") or ())
        self.calls.append((slots, kwargs.get("probe"), kwargs.get("search_role")))
        value = self.outcomes.get(slots, self.outcomes.get("default", 5))
        return result(value, search_id=f"search-{len(self.calls)}")


class PotionGateTests(unittest.TestCase):
    def test_smoke_bomb_escapes_a_non_boss_without_a_credible_win(self):
        state = combat_state(potion_count=1)
        state = GameState(
            state.owner_hint,
            state.scope_id,
            state.screen,
            facts={
                **state.facts,
                "potions": ({"id": "SmokeBomb", "name": "Smoke Bomb", "can_use": True},),
            },
            combat=state.combat,
        )
        with tempfile.TemporaryDirectory() as directory:
            runs = RunDirectory(Path(directory) / "runs")
            runs.bind("ABC123")
            selected = PotionGate(runs).select(
                state, result(1, credible=False), FakeSearch({})
            )

        self.assertEqual(selected.command, "potion use 0")
        self.assertEqual(selected.metrics["potion_gate"], "SMOKE_BOMB_ESCAPE")

    def test_pair_is_probed_when_one_potion_only_reduces_emergency_to_danger(self):
        state = combat_state(potion_count=2)
        search = FakeSearch({(0,): 30, (1,): 25, (0, 1): 45, "default": 10})
        with tempfile.TemporaryDirectory() as directory:
            runs = RunDirectory(Path(directory) / "runs")
            runs.bind("ABC123")
            selected = PotionGate(runs).select(
                state, result(5, credible=False), search
            )

        self.assertEqual(search.calls[-1][0], (0, 1))
        self.assertEqual(selected.metrics["search_id"], "search-4")

    def test_inventory_supports_all_five_slots(self):
        self.assertEqual(potion_slots(combat_state()), (0, 1, 2, 3, 4))

    def test_risk_uses_expected_hp_instead_of_optimistic_best_hp(self):
        state = combat_state(potion_count=1)
        result = MCTSResult(
            "end",
            None,
            {
                "credible_win_evidence": True,
                "risk": {
                    "winSamples": 100,
                    "meanBestWinEndHp": 45,
                    "expectedEndHpOnWin": 5,
                },
            },
        )

        risk = assess_risk(state, result)

        self.assertEqual(risk["level"], "EMERGENCY")
        self.assertEqual(risk["expected_end_hp"], 5)

    def test_danger_checks_five_singles_and_releases_only_one(self):
        state = combat_state()
        baseline = result(30, search_id="baseline")
        search = FakeSearch(
            {
                (0,): 40,
                (1,): 32,
                (2,): 31,
                (3,): 30,
                (4,): 29,
                "default": 40,
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            runs = RunDirectory(Path(directory) / "runs")
            runs.bind("ABC123")
            selected = PotionGate(runs).select(state, baseline, search)
            trace = json.loads(
                (runs.path / "potion_decisions.jsonl").read_text().splitlines()[0]
            )

        self.assertEqual(selected.metrics["search_id"], "search-6")
        self.assertEqual(search.calls[-1], ((0,), None, "potion_final"))
        self.assertEqual(
            [call[0] for call in search.calls[:-1]],
            [(0,), (1,), (2,), (3,), (4,)],
        )
        self.assertEqual(trace["selected_slots"], [0])

    def test_emergency_uses_pair_only_when_no_single_is_enough(self):
        state = combat_state()
        baseline = result(5, credible=False, search_id="baseline")
        outcomes = {
            (0,): 12,
            (1,): 14,
            (2,): 6,
            (3,): 6,
            (4,): 6,
            (0, 1): 35,
            "default": 10,
        }
        search = FakeSearch(outcomes)
        with tempfile.TemporaryDirectory() as directory:
            runs = RunDirectory(Path(directory) / "runs")
            runs.bind("ABC123")
            selected = PotionGate(runs).select(state, baseline, search)
            trace = json.loads(
                (runs.path / "potion_decisions.jsonl").read_text().splitlines()[0]
            )

        pair_probes = [call for call in search.calls if call[1] is True and len(call[0]) == 2]
        self.assertEqual(len(pair_probes), 10)
        self.assertEqual(search.calls[-1], ((0, 1), None, "potion_final"))
        self.assertEqual(selected.metrics["search_id"], "search-16")
        self.assertEqual(trace["selected_slots"], [0, 1])
        self.assertEqual(trace["reason"], "PAIR_REQUIRED_FOR_EMERGENCY")

    def test_heart_releases_all_five_without_counterfactuals(self):
        state = combat_state(heart=True)
        baseline = result(5, credible=False)
        search = FakeSearch({"default": 40})
        with tempfile.TemporaryDirectory() as directory:
            runs = RunDirectory(Path(directory) / "runs")
            runs.bind("ABC123")
            PotionGate(runs).select(state, baseline, search)

        self.assertEqual(
            search.calls,
            [((0, 1, 2, 3, 4), None, "potion_final")],
        )

    def test_boss_without_a_credible_win_releases_everything(self):
        state = boss_state()
        baseline = result(5, credible=False)
        search = FakeSearch({"default": 5})
        with tempfile.TemporaryDirectory() as directory:
            runs = RunDirectory(Path(directory) / "runs")
            runs.bind("ABC123")
            PotionGate(runs).select(state, baseline, search)
        self.assertEqual(search.calls, [((0, 1, 2), None, "potion_final")])

        # A boss emergency that still has winning lines keeps the probe logic.
        credible = boss_state()
        search = FakeSearch({"default": 40})
        with tempfile.TemporaryDirectory() as directory:
            runs = RunDirectory(Path(directory) / "runs")
            runs.bind("ABC123")
            PotionGate(runs).select(credible, result(5, credible=True), search)
        self.assertTrue(search.calls[0][1], "expected a probe first")

    def test_emergency_is_rechecked_after_projected_hp_drops_materially(self):
        state = combat_state(potion_count=1)
        calls = []

        def search(state, **kwargs):
            calls.append((
                tuple(kwargs.get("potion_slots") or ()),
                kwargs.get("probe"),
                kwargs.get("search_role"),
            ))
            return result(11, credible=False, search_id=f"search-{len(calls)}")
        gate = PotionGate(object())

        gate.select(state, result(10, credible=False), search)
        gate.select(state, result(8, credible=False), search)
        selected = gate.select(state, result(4, credible=False), search)

        self.assertEqual(selected.metrics["search_id"], "search-3")
        self.assertEqual(
            calls,
            [
                ((0,), True, "potion_probe"),
                ((0,), True, "potion_probe"),
                ((0,), None, "potion_final"),
            ],
        )

    def test_entropic_brew_excluded_from_full_belt(self):
        # Full belt (no empty slot): Entropic Brew in slot 0 is a game no-op
        # (it only fills empty slots) that the simulator over-values and that
        # settle_game_state() can't confirm, so it must never be exposed.
        state = entropic_brew_state(
            potions=(
                {"id": "EntropicBrew", "name": "Entropic Brew", "can_use": True},
                {"id": "Block Potion", "name": "Block Potion", "can_use": True},
                {"id": "SpeedPotion", "name": "Speed Potion", "can_use": True},
            )
        )

        self.assertEqual(potion_slots(state), (1, 2))

        # current_hp=50/max_hp=100 with an end_hp of 28 lands in DANGER
        # (loss_of_max_hp == 0.22, below the 0.35/0.50 EMERGENCY thresholds).
        baseline = result(28, search_id="baseline")
        search = FakeSearch({"default": 28})
        with tempfile.TemporaryDirectory() as directory:
            runs = RunDirectory(Path(directory) / "runs")
            runs.bind("ABC123")
            selected = PotionGate(runs).select(state, baseline, search)

        self.assertTrue(search.calls, "expected the remaining slots to still be probed")
        self.assertTrue(
            all(0 not in call[0] for call in search.calls),
            f"slot 0 (Entropic Brew) must never be probed or released: {search.calls}",
        )
        self.assertEqual(selected.metrics.get("search_id", "baseline"), "baseline")

    def test_entropic_brew_eligible_with_an_empty_slot(self):
        state = entropic_brew_state(
            potions=(
                {"id": "EntropicBrew", "name": "Entropic Brew", "can_use": True},
                None,
                None,
            )
        )

        self.assertEqual(potion_slots(state), (0,))

    def test_active_slots_excludes_entropic_brew_even_if_previously_authorized(self):
        state = entropic_brew_state(
            potions=(
                {"id": "EntropicBrew", "name": "Entropic Brew", "can_use": True},
                {"id": "Block Potion", "name": "Block Potion", "can_use": True},
                {"id": "SpeedPotion", "name": "Speed Potion", "can_use": True},
            )
        )
        gate = PotionGate(object())
        # Pin the gate's scope to this state so _enter() treats slot 0 as
        # already authorized from an earlier (pre-fix) decision, rather than
        # resetting on scope entry.
        gate._scope = state.scope_id
        gate._authorized = {0, 1}

        active = gate.active_slots(state)

        self.assertNotIn(0, active)
        self.assertEqual(active, (1,))

    def test_danger_is_rechecked_after_projected_hp_drops_materially(self):
        state = combat_state(potion_count=1, current_hp=80)
        calls = []

        def search(state, **kwargs):
            calls.append(tuple(kwargs.get("potion_slots") or ()))
            return result(59, search_id=f"search-{len(calls)}")

        gate = PotionGate(object())
        gate.select(state, result(60), search)
        gate.select(state, result(58), search)
        selected = gate.select(state, result(54), search)

        self.assertEqual(calls, [(0,), (0,), (0,)])
        self.assertEqual(selected.metrics["search_id"], "search-3")

    def test_act2_hallway_holds_danger_release_with_healthy_hp_buffer(self):
        # current_hp=60, end_hp=40 -> loss_of_max_hp=0.20 (DANGER, below the
        # 0.35 EMERGENCY cutoff), expected_end_hp_of_max=0.40 (>= the 0.35
        # hold threshold).
        state = hallway_state(current_hp=60)
        baseline = result(40, search_id="baseline")
        risk = assess_risk(state, baseline)
        self.assertEqual(risk["level"], DANGER)
        self.assertEqual(risk["expected_end_hp_of_max"], 0.4)

        search = FakeSearch({"default": 40})
        with tempfile.TemporaryDirectory() as directory:
            runs = RunDirectory(Path(directory) / "runs")
            runs.bind("ABC123")
            selected = PotionGate(runs).select(state, baseline, search)
            trace = json.loads(
                (runs.path / "potion_decisions.jsonl").read_text().splitlines()[0]
            )

        self.assertEqual(search.calls, [])
        self.assertIs(selected, baseline)
        self.assertEqual(trace["reason"], "ACT2_HALLWAY_HOLD_FOR_BOSS")
        self.assertEqual(trace["selected_slots"], [])

    def test_act2_hallway_still_probes_danger_below_the_buffer_threshold(self):
        # current_hp=55, end_hp=30 -> loss_of_max_hp=0.25 (DANGER),
        # expected_end_hp_of_max=0.30 (below the 0.35 hold threshold), so the
        # hold does not apply and normal DANGER probing proceeds.
        state = hallway_state(current_hp=55)
        baseline = result(30, search_id="baseline")
        risk = assess_risk(state, baseline)
        self.assertEqual(risk["level"], DANGER)
        self.assertEqual(risk["expected_end_hp_of_max"], 0.3)

        search = FakeSearch({"default": 30})
        with tempfile.TemporaryDirectory() as directory:
            runs = RunDirectory(Path(directory) / "runs")
            runs.bind("ABC123")
            selected = PotionGate(runs).select(state, baseline, search)
            trace = json.loads(
                (runs.path / "potion_decisions.jsonl").read_text().splitlines()[0]
            )

        self.assertTrue(search.calls, "expected normal DANGER probing")
        self.assertNotEqual(trace["reason"], "ACT2_HALLWAY_HOLD_FOR_BOSS")

    def test_act2_hallway_hold_does_not_apply_at_emergency(self):
        # current_hp=100, end_hp=40 -> loss_of_max_hp=0.60 (EMERGENCY), with
        # expected_end_hp_of_max=0.40 still above the hold threshold: the
        # hold is scoped to DANGER only, so EMERGENCY probing/pair logic is
        # unaffected.
        state = hallway_state(current_hp=100)
        baseline = result(40, search_id="baseline")
        risk = assess_risk(state, baseline)
        self.assertEqual(risk["level"], EMERGENCY)
        self.assertEqual(risk["expected_end_hp_of_max"], 0.4)

        search = FakeSearch({"default": 40})
        with tempfile.TemporaryDirectory() as directory:
            runs = RunDirectory(Path(directory) / "runs")
            runs.bind("ABC123")
            selected = PotionGate(runs).select(state, baseline, search)
            trace = json.loads(
                (runs.path / "potion_decisions.jsonl").read_text().splitlines()[0]
            )

        self.assertTrue(search.calls, "expected normal EMERGENCY probing")
        self.assertNotEqual(trace["reason"], "ACT2_HALLWAY_HOLD_FOR_BOSS")

    def test_act2_hallway_hold_is_scoped_to_act2_monsterroom_only(self):
        # Same DANGER baseline (current_hp=60, end_hp=40 -> loss_of_max_hp
        # 0.20, expected_end_hp_of_max 0.40) as the holding case above, but
        # outside Act 2 hallway fights: normal DANGER probing proceeds.
        for act, room_type in (
            (1, "MonsterRoom"),
            (2, "MonsterRoomElite"),
            (3, "MonsterRoom"),
        ):
            with self.subTest(act=act, room_type=room_type):
                state = hallway_state(act=act, room_type=room_type, current_hp=60)
                baseline = result(40, search_id="baseline")
                risk = assess_risk(state, baseline)
                self.assertEqual(risk["level"], DANGER)

                search = FakeSearch({"default": 40})
                with tempfile.TemporaryDirectory() as directory:
                    runs = RunDirectory(Path(directory) / "runs")
                    runs.bind("ABC123")
                    selected = PotionGate(runs).select(state, baseline, search)
                    trace = json.loads(
                        (runs.path / "potion_decisions.jsonl").read_text().splitlines()[0]
                    )

                self.assertTrue(search.calls, "expected normal DANGER probing")
                self.assertNotEqual(trace["reason"], "ACT2_HALLWAY_HOLD_FOR_BOSS")


if __name__ == "__main__":
    unittest.main()
