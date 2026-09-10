"""Pattern B: elite/boss fights where player HP <= 15% of max at any turn.

Also: Bronze Automaton Hyperbeam turns (move_id==2, move_base_damage==45) -
player HP/block right before the hit, what the search chose, and the mcts file.
"""
from __future__ import annotations

import json
from pathlib import Path

import common


def analyze_low_hp(run_dir: Path) -> list[dict]:
    entries = common.load_entries(run_dir)
    out = []
    outcome = common.run_outcome(run_dir)
    seen_fights = {}  # (floor,) -> min_hp_frac record, one per elite/boss encounter
    for c in common.combat_states(entries):
        if c["room_type"] not in common.ELITE_BOSS_ROOMS:
            continue
        player = c["player"]
        hp = player.get("current_hp")
        max_hp = player.get("max_hp") or c.get("max_hp")
        if hp is None or not max_hp:
            continue
        frac = hp / max_hp
        key = c["floor"]
        names = common.monster_names(c)
        rec = seen_fights.get(key)
        if rec is None or frac < rec["frac"]:
            seen_fights[key] = {
                "floor": key,
                "monsters": names,
                "frac": frac,
                "min_hp": hp,
                "max_hp": max_hp,
                "turn": c["turn"],
            }
    for floor, rec in seen_fights.items():
        if rec["frac"] <= 0.15:
            out.append({
                "seed": run_dir.name,
                "floor": floor,
                "monsters": rec["monsters"],
                "min_hp": rec["min_hp"],
                "max_hp": rec["max_hp"],
                "min_hp_frac": round(rec["frac"], 3),
                "turn": rec["turn"],
                "outcome": outcome["outcome"],
            })
    return out


def analyze_bronze_hyperbeam(run_dir: Path) -> list[dict]:
    entries = common.load_entries(run_dir)
    out = []
    for c in common.combat_states(entries):
        bronze = None
        for m in c["monsters"]:
            if m.get("name") == "Bronze Automaton" and m.get("move_id") == 2 and m.get("move_base_damage") == 45:
                bronze = m
                break
        if bronze is None:
            continue
        player = c["player"]
        search_path = str(common.mcts_path(run_dir, c["search_id"])) if c["search_id"] else None
        chosen_command = None
        if search_path and Path(search_path).is_file():
            try:
                rec = json.loads(Path(search_path).read_text())
                chosen_command = (rec.get("raw_result") or {}).get("rootCommand")
            except (OSError, json.JSONDecodeError):
                pass
        out.append({
            "seed": run_dir.name,
            "floor": c["floor"],
            "turn": c["turn"],
            "player_hp": player.get("current_hp"),
            "player_block": player.get("block"),
            "player_max_hp": player.get("max_hp"),
            "move_adjusted_damage": bronze.get("move_adjusted_damage"),
            "action_taken": (c["action"] or {}).get("command"),
            "root_command": chosen_command,
            "mcts_file": search_path,
        })
    return out


def main():
    low_hp_all = []
    hyperbeam_all = []
    for d in common.valid_run_dirs():
        low_hp_all.extend(analyze_low_hp(d))
        hyperbeam_all.extend(analyze_bronze_hyperbeam(d))

    print("=== Low HP (<=15% max) elite/boss fights ===")
    for r in low_hp_all:
        print(f"{r['seed']:<15} floor={r['floor']:<3} monsters={'/'.join(r['monsters']):<30} "
              f"minHP={r['min_hp']}/{r['max_hp']} ({r['min_hp_frac']*100:.1f}%) turn={r['turn']} outcome={r['outcome']}")
    print(f"\n# {len(low_hp_all)} low-HP elite/boss encounters across {len({r['seed'] for r in low_hp_all})} seeds")

    print("\n=== Bronze Automaton Hyperbeam turns (distinct turns only, first occurrence per turn) ===")
    dedup = {}
    for r in hyperbeam_all:
        key = (r["seed"], r["floor"], r["turn"])
        dedup.setdefault(key, r)
    for r in dedup.values():
        print(json.dumps(r))
    print(f"\n# {len(dedup)} distinct Hyperbeam-active turns across "
          f"{len({r['seed'] for r in dedup.values()})} seeds")


if __name__ == "__main__":
    main()
