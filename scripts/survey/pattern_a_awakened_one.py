"""Pattern A: Awakened One fights (Act 3 boss, floor 50).

For every run that reaches the Awakened One: entry HP, player HP when phase 1
"died" (monster half_dead flips True, i.e. rebirth triggered), turns per
phase, outcome, and the mcts search files for the last 2 turns of phase 1.
"""
from __future__ import annotations

import json
from pathlib import Path

import common


def analyze(run_dir: Path) -> dict | None:
    entries = common.load_entries(run_dir)
    records = [
        c for c in common.combat_states(entries)
        if c["floor"] == 50 and any(m.get("name") == "Awakened One" for m in c["monsters"])
    ]
    if not records:
        return None

    outcome = common.run_outcome(run_dir)
    entry_hp = records[0]["player"].get("current_hp")
    entry_maxhp = records[0]["player"].get("max_hp")

    def awakened(rec):
        for m in rec["monsters"]:
            if m.get("name") == "Awakened One":
                return m
        return {}

    phase1_death_idx = None  # index into records
    for i, rec in enumerate(records):
        if awakened(rec).get("half_dead"):
            phase1_death_idx = i
            break

    last_turn = records[-1]["turn"]
    if phase1_death_idx is not None:
        phase1_death_turn = records[phase1_death_idx]["turn"]
        hp_at_phase1_death = records[phase1_death_idx]["player"].get("current_hp")
        phase1_turns = phase1_death_turn
        phase2_turns = last_turn - phase1_death_turn
        phase1_killed = True
    else:
        phase1_death_turn = None
        hp_at_phase1_death = None
        phase1_turns = last_turn
        phase2_turns = 0
        phase1_killed = False

    # mcts files for the last 2 distinct turns of phase 1 (turns <= phase1_death_turn,
    # or the whole fight if phase 1 was never killed).
    boundary_turn = phase1_death_turn if phase1_killed else last_turn
    phase1_records = [r for r in records if r["turn"] is not None and r["turn"] <= boundary_turn]
    turns_present = sorted({r["turn"] for r in phase1_records if r["turn"] is not None})
    last_two_turns = set(turns_present[-2:]) if turns_present else set()
    mcts_files = []
    for r in phase1_records:
        if r["turn"] in last_two_turns and r["search_id"]:
            p = common.mcts_path(run_dir, r["search_id"])
            mcts_files.append({
                "turn": r["turn"],
                "player_hp": r["player"].get("current_hp"),
                "monster_hp": awakened(r).get("current_hp"),
                "action": (r["action"] or {}).get("command"),
                "path": str(p),
            })

    return {
        "seed": run_dir.name,
        "entry_hp": entry_hp,
        "entry_max_hp": entry_maxhp,
        "phase1_killed": phase1_killed,
        "hp_at_phase1_death": hp_at_phase1_death,
        "phase1_death_turn": phase1_death_turn,
        "phase1_turns": phase1_turns,
        "phase2_turns": phase2_turns,
        "last_turn": last_turn,
        "final_player_hp": records[-1]["player"].get("current_hp"),
        "outcome": outcome["outcome"],
        "last_two_phase1_turns_mcts": mcts_files,
    }


def main():
    results = []
    for d in common.valid_run_dirs():
        r = analyze(d)
        if r:
            results.append(r)
    print(json.dumps(results, indent=2))
    print(f"\n# {len(results)} runs reached Awakened One (floor 50)", flush=True)
    killed_p1 = [r for r in results if r["phase1_killed"]]
    print(f"# {len(killed_p1)} killed phase 1 (rebirth triggered)")
    for r in results:
        print(f"{r['seed']:<15} entryHP={r['entry_hp']}/{r['entry_max_hp']} "
              f"p1_killed={r['phase1_killed']} hp@p1death={r['hp_at_phase1_death']} "
              f"p1_turns={r['phase1_turns']} p2_turns={r['phase2_turns']} "
              f"final_hp={r['final_player_hp']} outcome={r['outcome']}")


if __name__ == "__main__":
    main()
