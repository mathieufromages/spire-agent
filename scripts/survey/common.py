"""Shared loaders for the spire-agent run-data survey.

Reads /var/home/painter/spire-agent/runs/<seed>/{run_history.jsonl,mcts/*.json,
potion_decisions.jsonl}. Read-only. Never writes under runs/.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path("/var/home/painter/spire-agent")
RUNS_DIR = REPO / "runs"

sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "src"))
import summarize_runs  # noqa: E402  (scripts/summarize_runs.py)

MIN_ENTRIES_NOT_CRASH = 60


def valid_run_dirs() -> list[Path]:
    """All runs/<seed> dirs, excluding .pre-fix-backup and crash-sized runs."""
    out = []
    for d in sorted(RUNS_DIR.iterdir()):
        if not d.is_dir():
            continue
        if ".pre-fix-backup" in d.name:
            continue
        hist = d / "run_history.jsonl"
        if not hist.is_file():
            continue
        n = sum(1 for _ in hist.open(encoding="utf-8"))
        if n < MIN_ENTRIES_NOT_CRASH:
            continue
        out.append(d)
    return out


def load_entries(run_dir: Path) -> list[dict]:
    entries = []
    with (run_dir / "run_history.jsonl").open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def load_potion_decisions(run_dir: Path) -> list[dict]:
    path = run_dir / "potion_decisions.jsonl"
    if not path.is_file():
        return []
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def mcts_path(run_dir: Path, search_id: str) -> Path:
    return run_dir / "mcts" / f"{search_id}.json"


def run_outcome(run_dir: Path) -> dict:
    """Reuse scripts/summarize_runs.py's outcome/floor/killer logic."""
    return summarize_runs.summarize_run(run_dir)


def combat_states(entries: list[dict]):
    """Yield every 'before' combat state in order, decorated with context.

    Each yielded dict: idx, floor, act, room_type, turn, player, monsters,
    action (the action dict taken from this state, or None), search_id
    (metrics.search_id of the combat.mcts decision, or None), potions (run.potions).
    Also yields one synthetic trailing record from the *last* entry's 'after'
    state (idx=-1, action=None) so the final combat frame (e.g. HP=0 on death,
    or monster HP=0 on kill) is visible even though no action was taken from it.
    """
    for i, e in enumerate(entries):
        before = e.get("before") or {}
        combat = before.get("combat")
        if not combat:
            continue
        run = before.get("run") or {}
        action = e.get("action")
        search_id = None
        if action:
            metrics = ((action.get("decision") or {}).get("metrics")) or {}
            if (action.get("decision") or {}).get("source") == "combat.mcts":
                search_id = metrics.get("search_id")
        yield {
            "idx": i,
            "floor": int(run.get("floor", 0) or 0),
            "act": run.get("act"),
            "room_type": run.get("room_type"),
            "max_hp": run.get("max_hp"),
            "potions": run.get("potions"),
            "turn": combat.get("turn"),
            "player": combat.get("player") or {},
            "monsters": combat.get("monsters") or [],
            "hand": combat.get("hand") or [],
            "discard_pile": combat.get("discard_pile") or [],
            "exhaust_pile": combat.get("exhaust_pile") or [],
            "action": action,
            "search_id": search_id,
        }
    if entries:
        last = entries[-1]
        after = last.get("after") or {}
        combat = after.get("combat")
        if combat:
            run = after.get("run") or {}
            yield {
                "idx": -1,
                "floor": int(run.get("floor", 0) or 0),
                "act": run.get("act"),
                "room_type": run.get("room_type"),
                "max_hp": run.get("max_hp"),
                "potions": run.get("potions"),
                "turn": combat.get("turn"),
                "player": combat.get("player") or {},
                "monsters": combat.get("monsters") or [],
                "hand": combat.get("hand") or [],
                "discard_pile": combat.get("discard_pile") or [],
                "exhaust_pile": combat.get("exhaust_pile") or [],
                "action": None,
                "search_id": None,
            }


def monster_names(state: dict) -> list[str]:
    return [m.get("name") for m in state["monsters"] if not m.get("is_gone")]


ELITE_BOSS_ROOMS = {"MonsterRoomElite", "MonsterRoomBoss"}
