#!/usr/bin/env python3
"""Build a list of fight-start positions for offline playout A/B testing.

Scans /var/home/painter/spire-agent/runs/<seed>/ for recorded MCTS searches
(runs/<seed>/mcts/NNNNNN.json) and, for every fight that took place in an
elite or boss room (or any room, with --all-rooms), finds the FIRST baseline
search of that fight: the lowest-numbered search id for that seed+floor with
settings.search_role == "baseline" and request.game_state.combat_state.turn
== 1. That search's recorded `request` is exactly the fight's turn-1
fight-start battle-sim input, so it doubles as a ready-to-replay playout seed.

Room/category classification uses the recorded search's own
request.game_state.room_type and request.game_state.act directly (verified
against actual recorded data): MonsterRoomBoss at act 4 is always the
Corrupt Heart fight ("heart"); MonsterRoomElite at act 4 is "act4_elite";
MonsterRoomBoss at act 1/2/3 is "act1_boss"/"act2_boss"/"act3_boss";
MonsterRoomElite at act 1/2/3 is the generic "elite" (not split by act,
matching the requested category set); MonsterRoom (regular fights, any act)
is "hallway" and is only considered when --all-rooms is given or "hallway"
is explicitly requested via --categories.

Runs are skipped entirely if their name ends in ".pre-fix-backup" or their
run_history.jsonl has fewer than 60 lines.

Output is a JSON list of position records:
    {
        "seed": str, "floor": int, "act": int, "room_type": str,
        "monsters": [str, ...],
        "mcts_file": "<abs path to the NNNNNN.json search file>",
        "category": one of heart|act4_elite|act3_boss|act2_boss|act1_boss
                    |elite|hallway,
        "player_hp": int, "max_hp": int
    }

Usage:
    cd /var/home/painter/spire-agent && uv run python \\
        scripts/build_playout_positions.py \\
        [--categories heart,act3_boss,act2_boss,act1_boss,elite,act4_elite,hallway] \\
        [--limit-per-category N] \\
        [--all-rooms] \\
        [--runs-dir PATH] \\
        [--out PATH]

Examples:
    uv run python scripts/build_playout_positions.py
    uv run python scripts/build_playout_positions.py \\
        --categories heart,act3_boss,act2_boss --limit-per-category 5 \\
        --out /tmp/positions.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DEFAULT_RUNS_DIR = Path("/var/home/painter/spire-agent/runs")
DEFAULT_OUT = Path(
    "/tmp/claude-1000/-var-home-painter/f53546bc-cae3-4e7c-bf6f-22cb8cc383fa"
    "/scratchpad/ab/positions.json"
)
MIN_RUN_HISTORY_LINES = 60

ALL_CATEGORIES = [
    "heart",
    "act4_elite",
    "act3_boss",
    "act2_boss",
    "act1_boss",
    "elite",
    "hallway",
]


def classify(room_type: str, act: int) -> str | None:
    if room_type == "MonsterRoomBoss":
        if act == 4:
            return "heart"
        if act in (1, 2, 3):
            return f"act{act}_boss"
        return None
    if room_type == "MonsterRoomElite":
        if act == 4:
            return "act4_elite"
        if act in (1, 2, 3):
            return "elite"
        return None
    if room_type == "MonsterRoom":
        return "hallway"
    return None


def run_history_line_count(run_dir: Path) -> int:
    path = run_dir / "run_history.jsonl"
    if not path.exists():
        return 0
    count = 0
    with path.open("r", encoding="utf-8") as f:
        for _ in f:
            count += 1
    return count


def scan_run(run_dir: Path, want_hallway: bool) -> dict[tuple[int, int], dict[str, Any]]:
    """Return {(floor, search_id): position_record} for the FIRST baseline
    turn-1 search of every qualifying fight in this run, keyed so the caller
    can reduce to one record per floor by minimum search_id."""
    best_by_floor: dict[int, tuple[int, dict[str, Any]]] = {}
    mcts_dir = run_dir / "mcts"
    if not mcts_dir.is_dir():
        return {}
    seed = run_dir.name
    for path in sorted(mcts_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        settings = data.get("settings") or {}
        if settings.get("search_role") != "baseline":
            continue
        request = data.get("request") or {}
        game_state = request.get("game_state") or {}
        combat_state = game_state.get("combat_state") or {}
        if combat_state.get("turn") != 1:
            continue
        room_type = game_state.get("room_type")
        act = game_state.get("act")
        if room_type == "MonsterRoom" and not want_hallway:
            continue
        if room_type not in ("MonsterRoomBoss", "MonsterRoomElite", "MonsterRoom"):
            continue
        category = classify(room_type, act)
        if category is None:
            continue
        floor = game_state.get("floor")
        try:
            search_id = int(data.get("search_id") if data.get("search_id") is not None else path.stem)
        except (TypeError, ValueError):
            search_id = int(path.stem) if path.stem.isdigit() else -1
        player = combat_state.get("player") or {}
        monsters = [m.get("name") for m in combat_state.get("monsters") or []]
        record = {
            "seed": seed,
            "floor": floor,
            "act": act,
            "room_type": room_type,
            "monsters": monsters,
            "mcts_file": str(path.resolve()),
            "category": category,
            "player_hp": player.get("current_hp", game_state.get("current_hp")),
            "max_hp": player.get("max_hp", game_state.get("max_hp")),
        }
        prior = best_by_floor.get(floor)
        if prior is None or search_id < prior[0]:
            best_by_floor[floor] = (search_id, record)
    return {(floor, sid): rec for floor, (sid, rec) in best_by_floor.items()}


def build_positions(
    runs_dir: Path,
    categories: set[str] | None,
    all_rooms: bool,
) -> list[dict[str, Any]]:
    want_hallway = all_rooms or (categories is not None and "hallway" in categories)
    positions: list[dict[str, Any]] = []
    for run_dir in sorted(runs_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        if run_dir.name.endswith(".pre-fix-backup"):
            continue
        if run_history_line_count(run_dir) < MIN_RUN_HISTORY_LINES:
            continue
        found = scan_run(run_dir, want_hallway)
        for (_floor, _sid), record in sorted(found.items()):
            if categories is not None and record["category"] not in categories:
                continue
            positions.append(record)
    positions.sort(key=lambda r: (r["category"], r["seed"], r["floor"]))
    return positions


def apply_limit_per_category(
    positions: list[dict[str, Any]], limit: int | None
) -> list[dict[str, Any]]:
    if limit is None:
        return positions
    counts: dict[str, int] = {}
    kept = []
    for record in positions:
        cat = record["category"]
        counts.setdefault(cat, 0)
        if counts[cat] >= limit:
            continue
        counts[cat] += 1
        kept.append(record)
    return kept


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--categories",
        default=None,
        help="Comma-separated category allowlist (default: all except hallway; "
        f"choices: {','.join(ALL_CATEGORIES)}).",
    )
    parser.add_argument(
        "--limit-per-category", type=int, default=None, help="Max positions kept per category."
    )
    parser.add_argument(
        "--all-rooms",
        action="store_true",
        help="Also include regular (non-elite/boss) fights, classified as 'hallway'.",
    )
    parser.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR), help="Runs directory to scan.")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output JSON path.")
    args = parser.parse_args()

    categories = None
    if args.categories:
        categories = {c.strip() for c in args.categories.split(",") if c.strip()}
        unknown = categories - set(ALL_CATEGORIES)
        if unknown:
            raise SystemExit(f"unknown categories: {sorted(unknown)}")

    runs_dir = Path(args.runs_dir)
    positions = build_positions(runs_dir, categories, args.all_rooms)
    positions = apply_limit_per_category(positions, args.limit_per_category)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(positions, indent=2), encoding="utf-8")

    counts: dict[str, int] = {}
    for record in positions:
        counts[record["category"]] = counts.get(record["category"], 0) + 1

    print(f"Wrote {len(positions)} positions to {out_path}")
    print(f"{'category':<12}{'count':>8}")
    for cat in ALL_CATEGORIES:
        if cat in counts:
            print(f"{cat:<12}{counts[cat]:>8}")


if __name__ == "__main__":
    main()
