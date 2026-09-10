#!/usr/bin/env python3
"""Replay recorded battle-sim MCTS searches against a battle-sim binary.

Takes one or more recorded mcts/NNNNNN.json files (or a run directory plus
one or more --search-id values) and re-runs each search with the recorded
settings against a battle-sim binary, using the exact argv construction
CombatMCTS.choose() uses in
src/spire_agent/tools/mcts/tool.py (around line 225-260):

    battle-sim <input.json> <simulations_per_thread> <threads> <max_time_ms> 0 \\
        [potion_slots=A,B,...] \\
        adaptive_max_time_ms=<N> adaptive_max_simulations=<N> \\
        [recovery_horizon_turns=<N>] [recovery_threat=<P>,<O>]

`<input.json>` is the recorded search's `request` object written verbatim
(that is exactly what CombatMCTS.choose()/encode_state() write to the temp
input file battle-sim reads).

Examples:

    python scripts/replay_search.py runs/4XYEN4045SYSS/mcts/000300.json
    python scripts/replay_search.py --run-dir runs/4XYEN4045SYSS \\
        --search-id 000300 --search-id 000301 --threads 8 --repeat 2
    python scripts/replay_search.py runs/SEED/mcts/000300.json \\
        --binary 3rd/sts_lightspeed/build-next/battle-sim --json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BINARY = ROOT / "3rd" / "sts_lightspeed" / "build" / "battle-sim"


def _load_search(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _resolve_search_paths(args: argparse.Namespace) -> list[Path]:
    paths = [Path(p) for p in args.paths]
    if args.run_dir:
        run_dir = Path(args.run_dir)
        mcts_dir = run_dir / "mcts"
        if not args.search_id:
            raise SystemExit("--run-dir requires at least one --search-id")
        for search_id in args.search_id:
            # search ids are recorded zero-padded (e.g. "000300"); accept
            # either the padded or bare form.
            candidate = mcts_dir / f"{search_id}.json"
            if not candidate.exists():
                candidate = mcts_dir / f"{int(search_id):06d}.json"
            paths.append(candidate)
    elif args.search_id:
        raise SystemExit("--search-id requires --run-dir")
    if not paths:
        raise SystemExit("no search files given (positional paths, or --run-dir/--search-id)")
    return paths


def _format_number(value: float) -> str:
    """Render a float for a battle-sim CLI token without a spurious ".0"."""

    return f"{value:g}"


def _build_argv(
    binary: Path,
    input_file: Path,
    settings: dict[str, Any],
    *,
    threads_override: int | None,
    simulations_override: int | None,
    max_time_ms_override: int | None,
    recovery_horizon_turns_override: int | None = None,
    recovery_threat_override: tuple[float, float] | None = None,
) -> list[str]:
    """Mirror CombatMCTS.choose()'s argv construction (tool.py:225-260)."""
    simulations = simulations_override if simulations_override is not None else settings["simulations_per_thread"]
    threads = threads_override if threads_override is not None else settings["threads"]
    max_time = max_time_ms_override if max_time_ms_override is not None else settings["max_time_ms"]
    argv = [
        str(binary),
        str(input_file),
        str(simulations),
        str(threads),
        str(max_time),
        "0",
    ]
    potion_slots = settings.get("allowed_potion_slots") or []
    if potion_slots:
        argv.append("potion_slots=" + ",".join(str(s) for s in potion_slots))
    argv.append(f"adaptive_max_time_ms={settings['adaptive_max_time_ms']}")
    argv.append(f"adaptive_max_simulations={settings['adaptive_max_simulations']}")
    recovery_horizon_turns = (
        recovery_horizon_turns_override
        if recovery_horizon_turns_override is not None
        else settings.get("recovery_horizon_turns")
    )
    if recovery_horizon_turns is not None:
        argv.append(f"recovery_horizon_turns={recovery_horizon_turns}")
    recovery_threat = (
        recovery_threat_override
        if recovery_threat_override is not None
        else settings.get("recovery_threat")
    )
    if recovery_threat is not None:
        argv.append(
            "recovery_threat=" + ",".join(_format_number(v) for v in recovery_threat)
        )
    return argv


def _run_once(
    binary: Path,
    request: dict[str, Any],
    settings: dict[str, Any],
    *,
    threads_override: int | None,
    simulations_override: int | None,
    max_time_ms_override: int | None,
    recovery_horizon_turns_override: int | None,
    recovery_threat_override: tuple[float, float] | None,
    timeout_s: float,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="replay-search-") as directory:
        input_file = Path(directory) / "input.json"
        input_file.write_text(
            json.dumps(request, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        argv = _build_argv(
            binary,
            input_file,
            settings,
            threads_override=threads_override,
            simulations_override=simulations_override,
            max_time_ms_override=max_time_ms_override,
            recovery_horizon_turns_override=recovery_horizon_turns_override,
            recovery_threat_override=recovery_threat_override,
        )
        started = time.perf_counter()
        proc = subprocess.run(
            argv,
            cwd=directory,
            text=True,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
        elapsed_s = time.perf_counter() - started
    if proc.returncode != 0:
        return {
            "argv": argv,
            "elapsed_s": elapsed_s,
            "returncode": proc.returncode,
            "stderr": proc.stderr[-4000:],
            "raw_result": None,
        }
    try:
        raw_result = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return {
            "argv": argv,
            "elapsed_s": elapsed_s,
            "returncode": proc.returncode,
            "stderr": proc.stderr[-4000:],
            "stdout_parse_error": str(exc),
            "raw_result": None,
        }
    return {
        "argv": argv,
        "elapsed_s": elapsed_s,
        "returncode": proc.returncode,
        "stderr": proc.stderr[-2000:] if proc.stderr else "",
        "raw_result": raw_result,
    }


_CANDIDATE_FIELDS = (
    "value",
    "winSampleRate",
    "meanBestWinEndHp",
    "cutoffSamples",
)


def _summarize(raw_result: dict[str, Any] | None) -> dict[str, Any]:
    if raw_result is None:
        return {
            "rootCommand": None,
            "searchStopReason": None,
            "selectedCandidateIndex": None,
            "chosen": None,
        }
    idx = raw_result.get("selectedCandidateIndex")
    root_actions = raw_result.get("rootActions") or []
    chosen = None
    if isinstance(idx, int) and 0 <= idx < len(root_actions):
        candidate = root_actions[idx]
        chosen = {field: candidate.get(field) for field in _CANDIDATE_FIELDS}
        chosen["action"] = candidate.get("action")
    return {
        "rootCommand": raw_result.get("rootCommand"),
        "searchStopReason": raw_result.get("searchStopReason"),
        "selectedCandidateIndex": idx,
        "chosen": chosen,
    }


def _fmt_chosen(chosen: dict[str, Any] | None) -> str:
    if not chosen:
        return "-"
    return (
        f"value={chosen.get('value'):.6f} "
        f"winSampleRate={chosen.get('winSampleRate'):.4f} "
        f"meanBestWinEndHp={chosen.get('meanBestWinEndHp')} "
        f"cutoffSamples={chosen.get('cutoffSamples')}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", help="recorded mcts/NNNNNN.json files")
    parser.add_argument("--run-dir", help="run directory (runs/<seed>); use with --search-id")
    parser.add_argument("--search-id", action="append", default=[], help="search id under --run-dir/mcts/ (repeatable)")
    parser.add_argument("--binary", default=str(DEFAULT_BINARY), help=f"battle-sim binary (default: {DEFAULT_BINARY})")
    parser.add_argument("--threads", type=int, default=None, help="override thread count (default: recorded settings.threads)")
    parser.add_argument("--simulations", type=int, default=None, help="override simulations_per_thread")
    parser.add_argument("--max-time-ms", type=int, default=None, help="override max_time_ms")
    parser.add_argument(
        "--recovery-horizon-turns",
        type=int,
        default=None,
        help="override recovery_horizon_turns (1-4); omit to use the recorded settings",
    )
    parser.add_argument(
        "--recovery-threat",
        default=None,
        help="override recovery_threat as 'P,O' (two floats); omit to use the recorded settings",
    )
    parser.add_argument("--repeat", type=int, default=1, help="replay each search this many times (default 1)")
    parser.add_argument("--timeout-s", type=float, default=120.0, help="subprocess timeout in seconds per replay")
    parser.add_argument("--json", action="store_true", help="dump full results as JSON instead of the text table")
    args = parser.parse_args(argv)

    recovery_threat_override: tuple[float, float] | None = None
    if args.recovery_threat is not None:
        parts = args.recovery_threat.split(",")
        if len(parts) != 2:
            raise SystemExit("--recovery-threat must be 'P,O' (two comma-separated floats)")
        try:
            recovery_threat_override = (float(parts[0]), float(parts[1]))
        except ValueError:
            raise SystemExit("--recovery-threat must be 'P,O' (two comma-separated floats)")

    binary = Path(args.binary).resolve()
    if not binary.exists():
        raise SystemExit(f"binary not found: {binary}")

    search_paths = _resolve_search_paths(args)

    all_results: list[dict[str, Any]] = []
    for path in search_paths:
        if not path.exists():
            print(f"# {path}: NOT FOUND", file=sys.stderr)
            continue
        data = _load_search(path)
        request = data["request"]
        settings = data["settings"]
        raw_result = data.get("raw_result")
        recorded_summary = _summarize(raw_result)
        recorded_elapsed_ms = data.get("elapsed_ms")

        entry: dict[str, Any] = {
            "path": str(path),
            "search_id": data.get("search_id"),
            "recorded": {
                "settings": settings,
                "elapsed_ms": recorded_elapsed_ms,
                **recorded_summary,
            },
            "replays": [],
        }

        if not args.json:
            print(f"=== {path} (search_id={data.get('search_id')}) ===")
            print(
                f"  recorded : rootCommand={recorded_summary['rootCommand']!r} "
                f"stopReason={recorded_summary['searchStopReason']!r} "
                f"idx={recorded_summary['selectedCandidateIndex']} "
                f"elapsed={recorded_elapsed_ms:.0f}ms"
            )
            print(f"             {_fmt_chosen(recorded_summary['chosen'])}")

        for rep in range(args.repeat):
            result = _run_once(
                binary,
                request,
                settings,
                threads_override=args.threads,
                simulations_override=args.simulations,
                max_time_ms_override=args.max_time_ms,
                recovery_horizon_turns_override=args.recovery_horizon_turns,
                recovery_threat_override=recovery_threat_override,
                timeout_s=args.timeout_s,
            )
            summary = _summarize(result["raw_result"])
            replay_entry = {
                "rep": rep,
                "elapsed_s": result["elapsed_s"],
                "returncode": result["returncode"],
                "argv": result["argv"],
                **summary,
            }
            if result["raw_result"] is None:
                replay_entry["stderr"] = result.get("stderr")
                replay_entry["stdout_parse_error"] = result.get("stdout_parse_error")
            entry["replays"].append(replay_entry)

            if not args.json:
                if result["raw_result"] is None:
                    print(
                        f"  replay {rep} (binary={binary.name}): FAILED "
                        f"rc={result['returncode']} elapsed={result['elapsed_s']:.2f}s"
                    )
                    if result.get("stderr"):
                        print(f"    stderr: {result['stderr'].strip()[-500:]}")
                    continue
                agrees_root = summary["rootCommand"] == recorded_summary["rootCommand"]
                agrees_stop = summary["searchStopReason"] == recorded_summary["searchStopReason"]
                flags = []
                if not agrees_root:
                    flags.append("ROOT-COMMAND-DIFFERS")
                if not agrees_stop:
                    flags.append("STOP-REASON-DIFFERS")
                flag_str = f"  [{' '.join(flags)}]" if flags else "  [matches recording]"
                print(
                    f"  replay {rep} (binary={binary.name}, threads="
                    f"{args.threads or settings['threads']}): "
                    f"rootCommand={summary['rootCommand']!r} "
                    f"stopReason={summary['searchStopReason']!r} "
                    f"idx={summary['selectedCandidateIndex']} "
                    f"elapsed={result['elapsed_s']:.2f}s{flag_str}"
                )
                print(f"             {_fmt_chosen(summary['chosen'])}")

        all_results.append(entry)
        if not args.json:
            print()

    if args.json:
        print(json.dumps(all_results, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
