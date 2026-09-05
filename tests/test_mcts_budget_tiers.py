"""Hallway fights may use a shorter MCTS wall-clock cap; nothing else changes."""

from __future__ import annotations

import tempfile
import unittest

from spire_agent.contracts import AgentKind, GameState, ScreenState
from spire_agent.tools.mcts import CombatMCTS


def combat_state(**facts) -> GameState:
    base = {"act": 1, "room_type": "MonsterRoom", "current_hp": 70, "max_hp": 80}
    base.update(facts)
    return GameState(
        AgentKind.COMBAT,
        "seed:a1:f3:combat",
        ScreenState("NONE", commands=("play", "end")),
        facts=base,
        combat={"monsters": [{"id": "JawWorm"}], "hand": []},
    )


class HallwayBudgetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.search = CombatMCTS(
            "battle-sim",
            self.directory.name,
            simulations=100_000,
            max_time_ms=10_000,
            hallway_max_time_ms=5_000,
        )

    def tearDown(self):
        self.directory.cleanup()

    def test_act_one_hallway_at_healthy_hp_uses_the_short_cap(self):
        self.assertEqual(self.search._limits(combat_state()), (100_000, 5_000))
        self.assertEqual(self.search._limits(combat_state(act=2)), (100_000, 5_000))

    def test_elites_bosses_act_three_and_low_hp_keep_the_full_budget(self):
        for facts in (
            {"room_type": "MonsterRoomElite"},
            {"room_type": "MonsterRoomBoss"},
            {"act": 3},
            {"current_hp": 39},
            {"room_type": "EventRoom"},
        ):
            with self.subTest(facts=facts):
                self.assertEqual(self.search._limits(combat_state(**facts)), (100_000, 10_000))

    def test_tier_is_off_by_default_and_never_raises_the_cap(self):
        default = CombatMCTS("battle-sim", self.directory.name)
        self.assertEqual(default._limits(combat_state()), (100_000, 10_000))
        wide = CombatMCTS("battle-sim", self.directory.name, max_time_ms=3_000, hallway_max_time_ms=5_000)
        self.assertEqual(wide._limits(combat_state()), (100_000, 3_000))

    def test_heart_fights_keep_the_adaptive_budget(self):
        state = GameState(
            AgentKind.COMBAT,
            "seed:a4:f55:combat",
            ScreenState("NONE", commands=("play", "end")),
            facts={"act": 4, "room_type": "MonsterRoomBoss", "current_hp": 80, "max_hp": 80},
            combat={"monsters": [{"id": "CorruptHeart"}], "hand": []},
        )
        self.assertEqual(self.search._limits(state), (500_000, 30_000))


if __name__ == "__main__":
    unittest.main()
