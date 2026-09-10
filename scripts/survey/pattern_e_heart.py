"""Pattern E: Heart fights (Corrupt Heart, floor 55).

Per turn: player HP, block, Heart HP, Heart intent, chosen command. Turn of
death/kill. Whether the fatal hit landed with unused potions in the belt.
Heart damage taken per turn, kills vs losses.
"""
from __future__ import annotations

import json
from pathlib import Path

import common


def analyze(run_dir: Path) -> dict | None:
    entries = common.load_entries(run_dir)
    records = [
        c for c in common.combat_states(entries)
        if c["floor"] == 55 and any(m.get("name") == "Corrupt Heart" for m in c["monsters"])
    ]
    if not records:
        return None
    outcome = common.run_outcome(run_dir)

    def heart(rec):
        for m in rec["monsters"]:
            if m.get("name") == "Corrupt Heart":
                return m
        return {}

    # One row per turn: first record seen for that turn (start-of-turn state,
    # before any cards played) - gives per-turn HP/block/intent progression.
    per_turn = {}
    for r in records:
        t = r["turn"]
        if t not in per_turn:
            per_turn[t] = r

    turns = []
    prev_hp = None
    for t in sorted(per_turn):
        r = per_turn[t]
        h = heart(r)
        hp = r["player"].get("current_hp")
        dmg_taken = (prev_hp - hp) if (prev_hp is not None and hp is not None) else None
        turns.append({
            "turn": t,
            "player_hp": hp,
            "player_block": r["player"].get("block"),
            "heart_hp": h.get("current_hp"),
            "heart_intent": h.get("intent"),
            "heart_move_id": h.get("move_id"),
            "dmg_taken_since_prev_turn_start": dmg_taken,
            "command": (r["action"] or {}).get("command"),
        })
        prev_hp = hp

    last = records[-1]
    last_hp = last["player"].get("current_hp")
    last_heart_hp = heart(last).get("current_hp")
    died = outcome["outcome"].startswith("died to the Heart")
    kill = outcome["outcome"] == "Heart kill"
    potions_at_end = last["potions"] or []
    unused_potions_at_death = sum(1 for p in potions_at_end if p) if died else None

    return {
        "seed": run_dir.name,
        "outcome": outcome["outcome"],
        "kill": kill,
        "died_to_heart": died,
        "last_turn": last["turn"],
        "final_player_hp": last_hp,
        "final_heart_hp": last_heart_hp,
        "unused_potions_at_death": unused_potions_at_death,
        "turns": turns,
    }


def main():
    results = []
    for d in common.valid_run_dirs():
        r = analyze(d)
        if r:
            results.append(r)

    print(f"# {len(results)} Heart fights\n")
    for r in results:
        print(f"--- {r['seed']} outcome={r['outcome']} last_turn={r['last_turn']} "
              f"final_hp={r['final_player_hp']} final_heart_hp={r['final_heart_hp']} "
              f"unused_potions_at_death={r['unused_potions_at_death']} ---")
        for t in r["turns"]:
            print(f"  turn {t['turn']:>2}: playerHP={t['player_hp']:>4} block={t['player_block']:>3} "
                  f"heartHP={t['heart_hp']:>5} intent={t['heart_intent']:<10} "
                  f"dmg_since_prev_turn={t['dmg_taken_since_prev_turn_start']} cmd={t['command']}")

    kills = [r for r in results if r["kill"]]
    losses = [r for r in results if r["died_to_heart"]]
    print(f"\n=== Summary: {len(kills)} kills, {len(losses)} losses to the Heart "
          f"({len(results) - len(kills) - len(losses)} other/incomplete) ===")

    def dmg_per_turn_stats(rows):
        vals = []
        for r in rows:
            for t in r["turns"]:
                if t["dmg_taken_since_prev_turn_start"] is not None:
                    vals.append(t["dmg_taken_since_prev_turn_start"])
        if not vals:
            return "n=0"
        return f"n={len(vals)} mean={sum(vals)/len(vals):.2f} max={max(vals)} min={min(vals)}"

    print("dmg/turn-boundary (kills):", dmg_per_turn_stats(kills))
    print("dmg/turn-boundary (losses):", dmg_per_turn_stats(losses))
    print("last_turn (kills):", [r["last_turn"] for r in kills])
    print("last_turn (losses):", [r["last_turn"] for r in losses])
    print("unused_potions_at_death (losses):", [r["unused_potions_at_death"] for r in losses])
    n_with_unused = sum(1 for r in losses if (r["unused_potions_at_death"] or 0) > 0)
    print(f"losses with >=1 unused potion at death: {n_with_unused}/{len(losses)}")


if __name__ == "__main__":
    main()
