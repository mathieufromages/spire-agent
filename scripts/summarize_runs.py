#!/usr/bin/env python3
"""Summarize recorded bot runs (runs/<seed>/) in one table.

    python scripts/summarize_runs.py                 # every run under runs/
    python scripts/summarize_runs.py runs/ZAVAZLEZQBM runs/3M270UNELZE2
    python scripts/summarize_runs.py --since 2026-09-08 --json

Per run: outcome (Heart kill / died to <enemy> on floor N / incomplete),
HP entering the Heart, deck size and upgrades, whether the deck carried a
scaling core and a finisher (the pattern behind every Heart kill so far),
MCTS search count and mean search time.  Runs that never reached a terminal
screen (crashes, killed loops) are listed as "incomplete" and excluded from
the tally, so they do not dilute the Heart-kill rate.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
try:  # the card sets live with the heuristics; fall back to a copy if absent
    from spire_agent.tools.heuristics.cards import HEART_FINISHERS, HEART_SCALING
except Exception:  # pragma: no cover - defensive
    HEART_SCALING = frozenset(
        {"limit break", "demon form", "corruption", "feel no pain",
         "dark embrace", "barricade", "apotheosis"}
    )
    HEART_FINISHERS = frozenset(
        {"reaper", "fiend fire", "immolate", "whirlwind", "bludgeon",
         "offering", "shockwave", "disarm"}
    )

HEART_FLOOR = 55


def _load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    entries = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


def _state(entry: dict) -> dict | None:
    return entry.get("after") or entry.get("state") or entry.get("before")


def _card_key(card: dict | str) -> str:
    name = card.get("name", "") if isinstance(card, dict) else str(card)
    return name.replace("+", "").strip().casefold()


def _deck_summary(run: dict) -> dict:
    deck = run.get("deck") or []
    size = sum(int(c.get("count", 1)) if isinstance(c, dict) else 1 for c in deck)
    upgrades = sum(
        int(c.get("count", 1)) for c in deck
        if isinstance(c, dict) and int(c.get("upgrades", 0)) > 0
    )
    keys = {_card_key(c) for c in deck}
    cores = sorted(k for k in keys if k in HEART_SCALING)
    finishers = sorted(k for k in keys if k in HEART_FINISHERS)
    return {
        "deck_size": size,
        "upgrades": upgrades,
        "cores": cores,
        "finishers": finishers,
    }


def _mcts_stats(run_dir: Path) -> dict:
    files = sorted((run_dir / "mcts").glob("*.json"))
    elapsed: list[float] = []
    capped = 0
    visits: list[float] = []
    first = last = None
    for path in files:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        ms = record.get("elapsed_ms")
        if isinstance(ms, (int, float)):
            elapsed.append(float(ms))
        metrics = ((record.get("result") or {}).get("metrics")) or {}
        if metrics.get("stop_reason") == "time_budget":
            capped += 1
        raw = record.get("raw_result") or {}
        roots = raw.get("rootActions") if isinstance(raw, dict) else None
        if isinstance(roots, list) and roots:
            total = sum(float(r.get("visits", 0) or 0) for r in roots if isinstance(r, dict))
            if total > 0 and isinstance(ms, (int, float)) and ms > 0:
                visits.append(total / (ms / 1000.0))
        stamp = record.get("recorded_at")
        if isinstance(stamp, str):
            first = first or stamp
            last = stamp
    return {
        "searches": len(files),
        "mean_search_s": (statistics.fmean(elapsed) / 1000.0) if elapsed else None,
        "search_time_s": sum(elapsed) / 1000.0 if elapsed else 0.0,
        "capped_fraction": (capped / len(elapsed)) if elapsed else None,
        "sims_per_s": statistics.median(visits) if visits else None,
        "first_search": first,
        "last_search": last,
    }


def summarize_run(run_dir: Path) -> dict:
    entries = _load_jsonl(run_dir / "run_history.jsonl")
    config = {}
    config_path = run_dir / "run_config.json"
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8")).get("settings", {})
        except (OSError, json.JSONDecodeError):
            config = {}
    summary: dict = {
        "seed": run_dir.name,
        "character": config.get("character"),
        "ascension": config.get("ascension"),
        "actions": max((e.get("entry_index", 0) for e in entries), default=0),
        "started": datetime.fromtimestamp(
            (run_dir / "run_history.jsonl").stat().st_mtime, tz=timezone.utc
        ).isoformat(timespec="minutes") if (run_dir / "run_history.jsonl").is_file() else None,
    }
    if not entries:
        summary.update(outcome="incomplete", floor=0, complete=False)
        return summary
    final = _state(entries[-1]) or {}
    run = final.get("run") or {}
    floor = int(run.get("floor", 0) or 0)
    room = run.get("room_type", "")
    hp = run.get("current_hp")
    max_hp = run.get("max_hp")
    terminal = bool(final.get("terminal")) or room == "TrueVictoryRoom" or (
        (final.get("screen") or {}).get("type") == "GAME_OVER"
    )
    summary.update(
        floor=floor, act=run.get("act"), hp=hp, max_hp=max_hp, gold=run.get("gold"),
        relics=[r.get("name") if isinstance(r, dict) else r for r in run.get("relics") or []],
        **_deck_summary(run),
    )
    # The enemy that ended the run: monsters of the last combat state seen.
    killer = None
    for entry in reversed(entries):
        before = entry.get("before") or {}
        combat = before.get("combat")
        if combat and combat.get("monsters"):
            names = [m.get("name") for m in combat["monsters"] if not m.get("is_gone")]
            killer = "/".join(dict.fromkeys(n for n in names if n)) or None
            break
    # HP when the Heart fight started (first combat state on floor 55).
    heart_entry_hp = None
    for entry in entries:
        before = entry.get("before") or {}
        run_before = before.get("run") or {}
        if int(run_before.get("floor", 0) or 0) == HEART_FLOOR and before.get("combat"):
            heart_entry_hp = (before["combat"].get("player") or {}).get("current_hp")
            break
    summary["heart_entry_hp"] = heart_entry_hp
    if room == "TrueVictoryRoom" or floor > HEART_FLOOR:
        outcome = "Heart kill"
    elif terminal or (hp is not None and int(hp) <= 0):
        where = "the Heart" if floor == HEART_FLOOR else (killer or room or "?")
        outcome = f"died to {where} (floor {floor})"
    else:
        outcome = "incomplete"
    summary["outcome"] = outcome
    summary["complete"] = outcome != "incomplete"
    summary["killer"] = killer
    summary.update(_mcts_stats(run_dir))
    sources = Counter(
        (e.get("action") or {}).get("decision", {}).get("source")
        for e in entries if e.get("action")
    )
    summary["decision_sources"] = dict(sources.most_common())
    return summary


def _fmt(value, digits=1) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="*", type=Path, help="run directories (default: all under runs/)")
    parser.add_argument("--since", help="only runs whose history was last written on/after this date (YYYY-MM-DD)")
    parser.add_argument("--json", action="store_true", help="emit one JSON object per run instead of a table")
    parser.add_argument("--all", action="store_true", help="include incomplete runs in the table")
    args = parser.parse_args(argv)

    dirs = args.runs or sorted(
        (p for p in (ROOT / "runs").iterdir() if p.is_dir()),
        key=lambda p: (p / "run_history.jsonl").stat().st_mtime if (p / "run_history.jsonl").is_file() else 0,
    )
    since = None
    if args.since:
        since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
    rows = []
    for run_dir in dirs:
        summary = summarize_run(run_dir)
        if since and summary.get("started") and datetime.fromisoformat(summary["started"]) < since:
            continue
        rows.append(summary)

    if args.json:
        for row in rows:
            print(json.dumps(row, sort_keys=True))
        return 0

    shown = [r for r in rows if r["complete"] or args.all]
    header = (
        f"{'seed':<15} {'char':<9} {'A':>2} {'outcome':<34} {'HP':>7} {'@Heart':>6} "
        f"{'deck':>4} {'upg':>3} {'core':<26} {'finisher':<22} {'srch':>4} {'s/srch':>6} {'cap%':>4} {'sims/s':>7}"
    )
    print(header)
    print("-" * len(header))
    for r in shown:
        hp = f"{_fmt(r.get('hp'))}/{_fmt(r.get('max_hp'))}" if r.get("max_hp") else "-"
        cap = r.get("capped_fraction")
        print(
            f"{r['seed']:<15} {str(r.get('character') or '?')[:9]:<9} {_fmt(r.get('ascension')):>2} "
            f"{r['outcome'][:34]:<34} {hp:>7} {_fmt(r.get('heart_entry_hp')):>6} "
            f"{_fmt(r.get('deck_size')):>4} {_fmt(r.get('upgrades')):>3} "
            f"{','.join(r.get('cores') or [])[:26]:<26} {','.join(r.get('finishers') or [])[:22]:<22} "
            f"{_fmt(r.get('searches')):>4} {_fmt(r.get('mean_search_s')):>6} "
            f"{_fmt(round(cap * 100) if cap is not None else None):>4} "
            f"{_fmt(round(r['sims_per_s']) if r.get('sims_per_s') else None):>7}"
        )
    complete = [r for r in rows if r["complete"]]
    kills = [r for r in complete if r["outcome"] == "Heart kill"]
    heart_losses = [r for r in complete if r["outcome"].startswith("died to the Heart")]
    incomplete = [r for r in rows if not r["complete"]]
    print()
    print(
        f"{len(complete)} completed runs: {len(kills)} Heart kills "
        f"({(100 * len(kills) / len(complete)) if complete else 0:.0f}%), "
        f"{len(heart_losses)} Heart losses, {len(complete) - len(kills) - len(heart_losses)} earlier deaths; "
        f"{len(incomplete)} incomplete (excluded)"
    )
    if incomplete and not args.all:
        print("incomplete: " + ", ".join(f"{r['seed']} (floor {r.get('floor', 0)}, {r['actions']} actions)" for r in incomplete))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
