"""Pattern D: potion timing in Act 3 (floors 34-50) and downstream potion
counts at the Act 3 boss (floor 50), Act 4 elite (floor 54) and the Heart
(floor 55).

potion_decisions.jsonl: scope_id is "<seed>:a<act>:f<floor>:<room>:combat".
A row is a real *release* iff selected_slots is non-empty (COMBAT_BUDGET_EXHAUSTED
and NO_RELEASE/NO_MATERIAL_SINGLE_GAIN rows carry empty selected_slots - those
are "considered but did not release").
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import common

SCOPE_RE = re.compile(r":a(\d+):f(\d+):([a-z]+):combat")


def parse_scope(scope_id: str) -> tuple[int | None, int | None, str | None]:
    m = SCOPE_RE.search(scope_id or "")
    if not m:
        return None, None, None
    return int(m.group(1)), int(m.group(2)), m.group(3)


def potion_count_at_floor(entries: list[dict], floor: int) -> int | None:
    for c in common.combat_states(entries):
        if c["floor"] == floor:
            potions = c["potions"] or []
            return sum(1 for p in potions if p)
    return None


def analyze(run_dir: Path) -> dict | None:
    entries = common.load_entries(run_dir)
    # does this run reach Act 3 (floor >= 34)?
    reached_act3 = any(c["floor"] >= 34 for c in common.combat_states(entries))
    if not reached_act3:
        return None
    outcome = common.run_outcome(run_dir)
    decisions = common.load_potion_decisions(run_dir)

    act3_hallway_releases = []
    for d in decisions:
        act, floor, room = parse_scope(d.get("scope_id", ""))
        if floor is None or not (34 <= floor <= 50) or room != "monsterroom":
            continue
        slots = d.get("selected_slots") or []
        if not slots:
            continue
        act3_hallway_releases.append({
            "floor": floor,
            "reason": d.get("reason"),
            "level": (d.get("baseline") or {}).get("level"),
            "slots": slots,
        })

    return {
        "seed": run_dir.name,
        "outcome": outcome["outcome"],
        "act3_hallway_releases": act3_hallway_releases,
        "potions_at_act3_boss": potion_count_at_floor(entries, 50),
        "potions_at_act4_elite": potion_count_at_floor(entries, 54),
        "potions_at_heart": potion_count_at_floor(entries, 55),
    }


def main():
    results = []
    for d in common.valid_run_dirs():
        r = analyze(d)
        if r:
            results.append(r)

    print(f"# {len(results)} runs reached Act 3 (floor >= 34)\n")
    for r in results:
        rel = r["act3_hallway_releases"]
        rel_str = "; ".join(f"f{x['floor']}:{x['level']}/{x['reason']}" for x in rel) or "none"
        print(f"{r['seed']:<15} outcome={r['outcome']:<32} "
              f"act3_releases={len(rel):<2} [{rel_str}] "
              f"pot@boss50={r['potions_at_act3_boss']} pot@elite54={r['potions_at_act4_elite']} pot@heart55={r['potions_at_heart']}")

    kills = [r for r in results if r["outcome"] == "Heart kill"]
    losses = [r for r in results if r["outcome"] != "Heart kill"]

    def stats(rows, field):
        vals = [r[field] for r in rows if r[field] is not None]
        if not vals:
            return "n=0"
        return f"n={len(vals)} mean={sum(vals)/len(vals):.2f} min={min(vals)} max={max(vals)} vals={vals}"

    print(f"\n=== Heart kills (n={len(kills)}) ===")
    print("act3 hallway releases per run:", [len(r["act3_hallway_releases"]) for r in kills])
    print("potions@boss50:", stats(kills, "potions_at_act3_boss"))
    print("potions@elite54:", stats(kills, "potions_at_act4_elite"))
    print("potions@heart55:", stats(kills, "potions_at_heart"))

    print(f"\n=== Non-kills / losses (n={len(losses)}) ===")
    print("act3 hallway releases per run:", [len(r["act3_hallway_releases"]) for r in losses])
    print("potions@boss50:", stats(losses, "potions_at_act3_boss"))
    print("potions@elite54:", stats(losses, "potions_at_act4_elite"))
    print("potions@heart55:", stats(losses, "potions_at_heart"))


if __name__ == "__main__":
    main()
