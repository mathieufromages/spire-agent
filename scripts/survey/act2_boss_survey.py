"""Act 2 boss (floor 33) entry survey: deck metrics at entry vs outcome.

Read-only. For every valid run that reaches a combat state with
before.run.floor==33 and room_type in {MonsterRoomBoss}, record deck/HP/
potions at entry and the outcome of that specific fight (won if the run's
history shows the boss fight ending with monsters dead and floor advancing
past 33 with a later non-boss-33 state; died if the run terminates during
that fight with player HP<=0 or run history ends there and summarize_runs
says "died to X (floor 33)").
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

import common

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from spire_agent.tools.heuristics.cards import (
    HEART_SCALING, HEART_FINISHERS, base_name, normalize,
)

ACT2_BOSSES = {"the champ", "bronze automaton", "the collector", "the guardian"}

# Base damage numbers (from cards.csv Desc, unupgraded), used for the >=10 dmg
# attack classification. Filled by hand for cards actually seen in Act 1/2
# reward pools + starter attacks, cross-checked against
# src/spire_agent/tools/data/cards.csv.
HEAVY_ATTACKS_10PLUS = {
    "bludgeon": 32, "heavy blade": 14, "carnage": 20, "reaper": 4,  # reaper is per-hit low but ignored (not "heavy")
    "whirlwind": 5, "immolate": 21, "uppercut": 13, "pommel strike": 9,
    "twin strike": 5, "clothesline": 12, "iron wave": 5, "cleave": 8,
    "sword boomerang": 3, "thunderclap": 4, "wild strike": 12,
    "perfected strike": 6, "anger": 6, "body slam": 0, "headbutt": 9,
    "searing blow": 12, "hemokinesis": 15, "rampage": 8, "sever soul": 16,
    "dropkick": 5, "feed": 10, "fiend fire": 7, "rupture": 0,
    "clash": 14, "reckless charge": 7, "infernal blade": 0,
}
FINISHER_NAMES = {
    "reaper", "fiend fire", "whirlwind", "immolate", "heavy blade",
    "bludgeon", "carnage", "uppercut", "pommel strike",
}
AOE_NAMES = {"whirlwind", "cleave", "immolate", "thunderclap", "carnage", "sunder", "reaper"}
BLOCK_CARD_NAMES = {
    "defend", "shrug it off", "iron wave", "true grit", "second wind",
    "impervious", "flame barrier", "ghostly armor", "power through",
    "entrench", "sentinel", "armaments", "leap", "steam barrier",
    "consecrate", "battle trance",
}


def card_base_damage(name: str) -> int | None:
    """Look up unupgraded base damage from cards.csv Desc via 'Deal N damage'."""
    row = _CARD_DB.get(name)
    if not row:
        return None
    import re
    desc = row.get("Desc", "")
    m = re.search(r"Deal (\d+) damage", desc)
    if m:
        return int(m.group(1))
    return None


def _load_card_db():
    import csv
    path = Path("/var/home/painter/spire-agent/src/spire_agent/tools/data/cards.csv")
    db = {}
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            db[normalize(row["Name"])] = row
    return db


_CARD_DB = _load_card_db()


def deck_metrics(deck_counts: dict, deck_upgrades: dict) -> dict:
    size = sum(deck_counts.values())
    upgrades = sum(deck_upgrades.values())
    attacks = strikes = defends = block_cards = aoe = heavy10 = 0
    finisher_present = set()
    core_present = set()
    for name, count in deck_counts.items():
        key = normalize(name)
        row = _CARD_DB.get(key)
        ctype = normalize(row.get("Type")) if row else ""
        if key == "strike":
            strikes += count
        if key == "defend":
            defends += count
        if key in BLOCK_CARD_NAMES:
            block_cards += count
        if ctype == "attack":
            attacks += count
        dmg = card_base_damage(key)
        if dmg is not None and dmg >= 10:
            heavy10 += count
        if key in AOE_NAMES:
            aoe += count
        if key in FINISHER_NAMES:
            finisher_present.add(key)
        if key in HEART_SCALING:
            core_present.add(key)
    return dict(
        deck_size=size, upgrades=upgrades, attacks=attacks, strikes=strikes,
        defends=defends, block_cards=block_cards, aoe=aoe, heavy10=heavy10,
        has_core=bool(core_present), cores=sorted(core_present),
        has_finisher=bool(finisher_present), finishers=sorted(finisher_present),
    )


def deck_counts_from_list(deck: list) -> tuple[dict, dict]:
    counts: dict[str, int] = {}
    upgrades: dict[str, int] = {}
    for c in deck or []:
        if isinstance(c, dict):
            name = base_name(c.get("name", ""))
            cnt = int(c.get("count", 1))
            up = int(c.get("upgrades", 0)) if int(c.get("upgrades", 0)) > 0 else (1 if str(c.get("name", "")).rstrip().endswith("+") else 0)
        else:
            name, cnt, up = base_name(str(c)), 1, 0
        key = normalize(name)
        counts[key] = counts.get(key, 0) + cnt
        if up:
            upgrades[key] = upgrades.get(key, 0) + min(cnt, up if up > 1 else cnt)
    return counts, upgrades


def find_act2_boss_entry(entries: list[dict]) -> dict | None:
    """First combat 'before' state with run.floor==33 and boss room."""
    for e in entries:
        before = e.get("before") or {}
        run = before.get("run") or {}
        combat = before.get("combat")
        if combat and int(run.get("floor", 0) or 0) == 33 and run.get("room_type") == "MonsterRoomBoss":
            return e
    return None


def fight_turns_and_dpt(entries: list[dict], start_idx: int) -> tuple[int, float | None, str | None]:
    """From the floor-33 boss entry index onward, track turns and player damage dealt
    per turn (sum of monster hp lost while floor stays 33 and room is boss)."""
    turns = set()
    boss_name = None
    dmg_events = []
    prev_monster_hp = {}
    last_turn = None
    for e in entries[start_idx:]:
        before = e.get("before") or {}
        run = before.get("run") or {}
        combat = before.get("combat")
        if int(run.get("floor", 0) or 0) != 33 or run.get("room_type") != "MonsterRoomBoss":
            if int(run.get("floor", 0) or 0) != 33:
                break
            continue
        if combat is None:
            continue
        monsters = combat.get("monsters") or []
        if monsters and boss_name is None:
            boss_name = "/".join(dict.fromkeys(m.get("name") for m in monsters if m.get("name")))
        t = combat.get("turn")
        if t is not None:
            turns.add(t)
            last_turn = t
        for m in monsters:
            mid = m.get("uuid") or m.get("name")
            hp = m.get("current_hp")
            if mid in prev_monster_hp and isinstance(hp, (int, float)) and isinstance(prev_monster_hp[mid], (int, float)):
                delta = prev_monster_hp[mid] - hp
                if delta > 0:
                    dmg_events.append((t, delta))
            prev_monster_hp[mid] = hp
    total_dmg = sum(d for _, d in dmg_events)
    n_turns = len(turns) if turns else (last_turn or 0)
    dpt = (total_dmg / n_turns) if n_turns else None
    return n_turns, dpt, boss_name


def analyze(run_dir: Path) -> dict | None:
    entries = common.load_entries(run_dir)
    boss_entry = find_act2_boss_entry(entries)
    if boss_entry is None:
        return None
    start_idx = entries.index(boss_entry)
    before = boss_entry.get("before") or {}
    run = before.get("run") or {}
    deck = run.get("deck") or []
    counts, upgrades = deck_counts_from_list(deck)
    metrics = deck_metrics(counts, upgrades)
    hp = run.get("current_hp")
    max_hp = run.get("max_hp")
    potions_raw = run.get("potions") or []
    potions = [p for p in potions_raw if p]
    relics = [r.get("name") if isinstance(r, dict) else r for r in run.get("relics") or []]
    turns, dpt, boss_name = fight_turns_and_dpt(entries, start_idx)
    outcome = common.run_outcome(run_dir)
    died_here = outcome["outcome"].startswith("died to") and "(floor 33)" in outcome["outcome"]
    won_here = not died_here  # survived past floor 33 (won that fight) whatever happens later
    return dict(
        seed=run_dir.name,
        boss=boss_name or (outcome.get("killer") if died_here else None),
        died=died_here,
        won=won_here,
        final_outcome=outcome["outcome"],
        hp=hp, max_hp=max_hp, hp_frac=(hp / max_hp) if hp is not None and max_hp else None,
        potions=len(potions),
        relics=relics,
        turns=turns, dpt=dpt,
        **metrics,
    )


def main():
    rows = []
    for d in common.valid_run_dirs():
        r = analyze(d)
        if r is not None:
            rows.append(r)
    for r in rows:
        print(json.dumps(r))
    print(f"\n# {len(rows)} runs entered the Act 2 boss (floor 33); "
          f"{sum(1 for r in rows if r['died'])} died there")


if __name__ == "__main__":
    main()
