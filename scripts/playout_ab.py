#!/usr/bin/env python3
"""Offline playout A/B runner for battle-sim tuning.

For every (position, arm, seed) triple, runs battle-sim in `playout` mode
(it repeatedly calls decideRootAction and executes each root decision until
the fight resolves or a decision cap is hit — see runPlayout()/main() in
3rd/sts_lightspeed/apps/battle-sim.cpp) starting from a recorded fight-start
state, and aggregates the outcomes per arm, plus a paired comparison of every
non-base arm against the first arm listed.

Positions come from build_playout_positions.py's output (a JSON list of
{"seed","floor","act","room_type","monsters","mcts_file","category",
"player_hp","max_hp"} records) — the recorded search's `request` at
`mcts_file` is written verbatim to a temp input file and passed as
battle-sim's argv[1], exactly like scripts/replay_search.py does.

Each playout's argv is:
    battle-sim <input.json> <simulations> <threads_per_playout> <max_time_ms> 0 \\
        playout playout_max_decisions=<N> rng_offset=<S> \\
        [potion_slots=<A,B,...>] <arm tokens...>

`stdout` (the final-outcome JSON runPlayout() emits) plus a `_meta` block
(position, arm, seed offset, argv, wall time) is written to
    <out>/<arm-label>/<seed>_<floor>_s<S>.json
and `stderr` (one JSON line per decision) to the matching `.log` file next to
it. A `manifest.json` at the top of `<out>` records the resolved arm order
(the FIRST arm listed is the baseline every other arm is compared against)
and run settings, so `--report-only` can rebuild the tables without being
handed `--arms`/`--positions` again.

Concurrency: a ThreadPoolExecutor of --parallel workers runs playouts, each
itself using --threads-per-playout battle-sim threads, so total CPU use is
parallel * threads-per-playout — keep that at or under 8.

Usage:
    cd /var/home/painter/spire-agent && uv run python scripts/playout_ab.py \\
        --positions PATH --arms 'name:opt1 opt2' [--arms 'name2:opt3' ...] \\
        [--binary PATH] [--simulations N] [--threads-per-playout N] \\
        [--parallel N] [--max-time-ms N] [--seeds N] \\
        [--potion-slots 0,1,2] [--max-decisions N] [--out DIR] [--resume] \\
        [--timeout-s N] [--report-only] [--json-summary PATH]

Each --arms value is "label:tok1 tok2 ..." — the label names the arm's
output subdirectory, and the space-separated tokens after the colon are
extra battle-sim mode tokens appended to every playout's argv for that arm
(a token may itself contain commas, e.g. a hypothetical
'threat:recovery_threat=0.5,1.5' — pass --arms once per arm, e.g.:
    --arms 'base:' --arms 'threat:recovery_threat=0.5,1.5' --arms 'h3:recovery_horizon_turns=3'
Note: recovery_horizon_turns=N and recovery_threat=P,O are real battle-sim
mode tokens (as of 3rd/sts_lightspeed submodule HEAD cba6644), alongside
root_potions, potion_slots=, adaptive_max_time_ms=, adaptive_max_simulations=,
playout, playout_max_decisions=, rng_offset=. An arm using a token
battle-sim doesn't recognize will fail every playout with exit code 1
("unknown battle-sim mode: ..."), which this script records as ERROR per
playout rather than crashing the run.

Example 3-arm A/B (8 threads: --parallel 2 * --threads-per-playout 4):
    uv run python scripts/playout_ab.py \\
        --positions /tmp/.../ab/positions.json \\
        --arms 'base:' --arms 'root_potions:root_potions' \\
        --simulations 10000 --threads-per-playout 4 --parallel 2 \\
        --max-time-ms 3000 --seeds 2 --potion-slots 0,1,2 \\
        --out /tmp/.../ab/$(date +%Y%m%d_%H%M%S)

Timestamped --out: the default is computed from Python's clock at argparse
time; when this script is driven from an orchestrating Workflow script,
compute the timestamp with a shell command (e.g. `$(date +%Y%m%d_%H%M%S)`)
and pass it explicitly via --out instead of relying on the script's default,
so a retried workflow step reuses (and can --resume) the same directory
rather than minting a new one each time.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BINARY = ROOT / "3rd" / "sts_lightspeed" / "build-next" / "battle-sim"
DEFAULT_OUT_BASE = Path(
    "/tmp/claude-1000/-var-home-painter/f53546bc-cae3-4e7c-bf6f-22cb8cc383fa"
    "/scratchpad/ab"
)
RESULT_FILE_RE = re.compile(r"^(?P<seed>.+)_(?P<floor>\d+)_s(?P<seed_offset>\d+)\.json$")


# --------------------------------------------------------------------------
# Arm / job setup
# --------------------------------------------------------------------------


def parse_arm(spec: str) -> dict[str, Any]:
    if ":" not in spec:
        raise SystemExit(f"--arms value {spec!r} must be 'label:tok1 tok2 ...' (colon required)")
    label, rest = spec.split(":", 1)
    label = label.strip()
    if not label:
        raise SystemExit(f"--arms value {spec!r} has an empty label")
    tokens = rest.split()
    return {"label": label, "tokens": tokens}


def build_argv(
    binary: Path,
    input_file: Path,
    simulations: int,
    threads: int,
    max_time_ms: int,
    max_decisions: int,
    seed_offset: int,
    potion_slots: str,
    arm_tokens: list[str],
) -> list[str]:
    argv = [
        str(binary),
        str(input_file),
        str(simulations),
        str(threads),
        str(max_time_ms),
        "0",
        "playout",
        f"playout_max_decisions={max_decisions}",
        f"rng_offset={seed_offset}",
    ]
    if potion_slots:
        argv.append(f"potion_slots={potion_slots}")
    argv.extend(arm_tokens)
    return argv


def load_positions(path: Path) -> list[dict[str, Any]]:
    positions = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(positions, list):
        raise SystemExit(f"{path}: expected a JSON list of positions")
    return positions


_REQUEST_CACHE: dict[str, Any] = {}
_REQUEST_CACHE_LOCK = Lock()


def load_request(mcts_file: str) -> Any:
    with _REQUEST_CACHE_LOCK:
        cached = _REQUEST_CACHE.get(mcts_file)
    if cached is not None:
        return cached
    data = json.loads(Path(mcts_file).read_text(encoding="utf-8"))
    request = data["request"]
    with _REQUEST_CACHE_LOCK:
        _REQUEST_CACHE[mcts_file] = request
    return request


# --------------------------------------------------------------------------
# Running one playout
# --------------------------------------------------------------------------


def run_one_playout(
    binary: Path,
    position: dict[str, Any],
    arm: dict[str, Any],
    seed_offset: int,
    simulations: int,
    threads: int,
    max_time_ms: int,
    max_decisions: int,
    potion_slots: str,
    timeout_s: float,
    result_path: Path,
    log_path: Path,
) -> dict[str, Any]:
    request = load_request(position["mcts_file"])
    with tempfile.TemporaryDirectory(prefix="playout-ab-") as directory:
        input_file = Path(directory) / "input.json"
        input_file.write_text(
            json.dumps(request, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        argv = build_argv(
            binary,
            input_file,
            simulations,
            threads,
            max_time_ms,
            max_decisions,
            seed_offset,
            potion_slots,
            arm["tokens"],
        )
        started = time.perf_counter()
        error: str | None = None
        stdout = ""
        stderr = ""
        returncode: int | None = None
        try:
            proc = subprocess.run(
                argv,
                cwd=directory,
                text=True,
                capture_output=True,
                timeout=timeout_s,
                check=False,
            )
            stdout, stderr, returncode = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as exc:
            error = f"timeout after {timeout_s}s"
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
        wall_time_s = time.perf_counter() - started

    parsed: dict[str, Any] | None = None
    if error is None:
        if returncode != 0:
            error = f"exit code {returncode}"
            # battle-sim's runPlayout() still emits a final JSON (outcome
            # "ERROR" plus whatever endHp/turns/decisions it reached) before
            # exiting non-zero — recover those diagnostic fields when present.
            try:
                parsed = json.loads(stdout)
            except json.JSONDecodeError:
                parsed = None
        else:
            try:
                parsed = json.loads(stdout)
            except json.JSONDecodeError as exc:
                error = f"stdout parse error: {exc}"

    result: dict[str, Any] = dict(parsed) if parsed is not None else {}
    if error is not None:
        result.setdefault("mode", "playout")
        result["outcome"] = "ERROR"
        result["error"] = error
    result["_meta"] = {
        "position": position,
        "arm": arm["label"],
        "arm_tokens": arm["tokens"],
        "seed_offset": seed_offset,
        "argv": argv,
        "wall_time_s": wall_time_s,
        "returncode": returncode,
    }

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(stderr, encoding="utf-8")
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------


def hp_fraction(result: dict[str, Any]) -> float | None:
    max_hp = result.get("maxHp") or result.get("_meta", {}).get("position", {}).get("max_hp")
    end_hp = result.get("endHp")
    if max_hp in (None, 0) or end_hp is None:
        return None
    return end_hp / max_hp


def is_error(result: dict[str, Any]) -> bool:
    return result.get("outcome") == "ERROR"


def is_win(result: dict[str, Any]) -> bool:
    return result.get("outcome") == "PLAYER_VICTORY"


def is_inconclusive(result: dict[str, Any]) -> bool:
    """A playout that neither errored nor reached a definite win/loss.

    battle-sim's playout mode prints outcome "UNDECIDED" with stoppedOnCap
    true when playout_max_decisions is hit before the fight resolves; treat
    any outcome outside PLAYER_VICTORY/PLAYER_LOSS, or one flagged
    stoppedOnCap, the same way.
    """
    if is_error(result):
        return False
    if result.get("stoppedOnCap") is True:
        return True
    return result.get("outcome") not in ("PLAYER_VICTORY", "PLAYER_LOSS")


def is_excluded(result: dict[str, Any]) -> bool:
    return is_error(result) or is_inconclusive(result)


def aggregate_arm(results: list[dict[str, Any]]) -> dict[str, Any]:
    playouts = len(results)
    errors = [r for r in results if is_error(r)]
    inconclusive = [r for r in results if is_inconclusive(r)]
    ok = [r for r in results if not is_excluded(r)]
    wins = [r for r in ok if is_win(r)]
    win_hps = [r["endHp"] for r in wins if r.get("endHp") is not None]
    hp_fracs = [f for f in (hp_fraction(r) for r in ok) if f is not None]
    turns = [r["turns"] for r in ok if r.get("turns") is not None]
    decisions = [r["decisions"] for r in ok if r.get("decisions") is not None]
    wall_times = [r.get("_meta", {}).get("wall_time_s", 0.0) for r in results]
    return {
        "playouts": playouts,
        "wins": len(wins),
        "win_rate": (len(wins) / len(ok)) if ok else None,
        "mean_end_hp_on_win": (sum(win_hps) / len(win_hps)) if win_hps else None,
        "mean_end_hp_fraction": (sum(hp_fracs) / len(hp_fracs)) if hp_fracs else None,
        "mean_turns": (sum(turns) / len(turns)) if turns else None,
        "mean_decisions": (sum(decisions) / len(decisions)) if decisions else None,
        "errors": len(errors),
        "inconclusive": len(inconclusive),
        "wall_time_s": sum(wall_times),
    }


def sign_test_p(n_pos: int, n_neg: int) -> float | None:
    """Two-sided exact sign test p-value, computed by hand (no scipy)."""
    n = n_pos + n_neg
    if n == 0:
        return None
    k = min(n_pos, n_neg)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2**n)
    return min(1.0, 2 * tail)


def position_key(result: dict[str, Any]) -> tuple[Any, Any, Any]:
    meta = result.get("_meta", {})
    pos = meta.get("position", {})
    return (pos.get("seed"), pos.get("floor"), meta.get("seed_offset"))


def paired_comparison(base_results: list[dict[str, Any]], other_results: list[dict[str, Any]]) -> dict[str, Any]:
    base_by_key = {position_key(r): r for r in base_results if not is_excluded(r)}
    other_by_key = {position_key(r): r for r in other_results if not is_excluded(r)}
    common_keys = sorted(set(base_by_key) & set(other_by_key))

    # Pairs dropped because either side's result was ERROR/inconclusive (or
    # simply missing on one side) — every attempted position on either arm,
    # minus the ones that made it into common_keys.
    attempted_keys = {position_key(r) for r in base_results} | {position_key(r) for r in other_results}
    dropped = len(attempted_keys) - len(common_keys)

    wins_gained = 0
    wins_lost = 0
    deltas = []
    n_pos = n_neg = 0
    for key in common_keys:
        b, o = base_by_key[key], other_by_key[key]
        b_win, o_win = is_win(b), is_win(o)
        if o_win and not b_win:
            wins_gained += 1
        elif b_win and not o_win:
            wins_lost += 1
        bf, of = hp_fraction(b), hp_fraction(o)
        if bf is not None and of is not None:
            delta = of - bf
            deltas.append(delta)
            if delta > 0:
                n_pos += 1
            elif delta < 0:
                n_neg += 1
    return {
        "n_paired": len(common_keys),
        "dropped": dropped,
        "wins_gained": wins_gained,
        "wins_lost": wins_lost,
        "mean_hp_fraction_delta": (sum(deltas) / len(deltas)) if deltas else None,
        "sign_test_p": sign_test_p(n_pos, n_neg),
        "n_pos": n_pos,
        "n_neg": n_neg,
    }


def fmt_pct(x: float | None) -> str:
    return f"{x * 100:.1f}%" if x is not None else "n/a"


def fmt_num(x: float | None, digits: int = 1) -> str:
    return f"{x:.{digits}f}" if x is not None else "n/a"


def print_aggregate_table(arms_results: dict[str, list[dict[str, Any]]]) -> None:
    header = (
        f"{'arm':<16}{'playouts':>9}{'wins':>6}{'win%':>8}{'meanHPwin':>11}"
        f"{'meanHP%':>9}{'meanTurns':>10}{'meanDec':>9}{'errors':>7}{'inconcl':>8}{'wallS':>9}"
    )
    print(header)
    print("-" * len(header))
    for label, results in arms_results.items():
        agg = aggregate_arm(results)
        print(
            f"{label:<16}{agg['playouts']:>9}{agg['wins']:>6}{fmt_pct(agg['win_rate']):>8}"
            f"{fmt_num(agg['mean_end_hp_on_win']):>11}{fmt_pct(agg['mean_end_hp_fraction']):>9}"
            f"{fmt_num(agg['mean_turns']):>10}{fmt_num(agg['mean_decisions']):>9}"
            f"{agg['errors']:>7}{agg['inconclusive']:>8}{fmt_num(agg['wall_time_s'],1):>9}"
        )


def print_paired_table(arms_results: dict[str, list[dict[str, Any]]], base_label: str) -> None:
    labels = list(arms_results.keys())
    others = [l for l in labels if l != base_label]
    if not others:
        return
    print(f"\nPaired vs base arm '{base_label}':")
    header = (
        f"{'arm':<16}{'n':>5}{'dropped':>8}{'winsGained':>11}{'winsLost':>9}"
        f"{'meanHP%delta':>13}{'signTestP':>10}"
    )
    print(header)
    print("-" * len(header))
    for label in others:
        cmp = paired_comparison(arms_results[base_label], arms_results[label])
        delta = cmp["mean_hp_fraction_delta"]
        delta_str = f"{delta * 100:+.1f}%" if delta is not None else "n/a"
        p_str = f"{cmp['sign_test_p']:.4f}" if cmp["sign_test_p"] is not None else "n/a"
        print(
            f"{label:<16}{cmp['n_paired']:>5}{cmp['dropped']:>8}{cmp['wins_gained']:>11}{cmp['wins_lost']:>9}"
            f"{delta_str:>13}{p_str:>10}"
        )


def print_category_breakdown(arms_results: dict[str, list[dict[str, Any]]]) -> None:
    categories: set[str] = set()
    for results in arms_results.values():
        for r in results:
            cat = r.get("_meta", {}).get("position", {}).get("category")
            if cat:
                categories.add(cat)
    if not categories:
        return
    print("\nPer-category breakdown:")
    header = f"{'arm':<16}{'category':<12}{'playouts':>9}{'win%':>8}{'meanHP%':>9}{'inconcl':>8}"
    print(header)
    print("-" * len(header))
    for label, results in arms_results.items():
        for cat in sorted(categories):
            cat_results = [
                r for r in results if r.get("_meta", {}).get("position", {}).get("category") == cat
            ]
            if not cat_results:
                continue
            agg = aggregate_arm(cat_results)
            print(
                f"{label:<16}{cat:<12}{agg['playouts']:>9}{fmt_pct(agg['win_rate']):>8}"
                f"{fmt_pct(agg['mean_end_hp_fraction']):>9}{agg['inconclusive']:>8}"
            )


# --------------------------------------------------------------------------
# JSON summary (--json-summary)
# --------------------------------------------------------------------------


def build_json_summary(arms_results: dict[str, list[dict[str, Any]]], base_label: str) -> dict[str, Any]:
    """Build the same per-arm, per-category and paired data as the printed
    tables, as plain JSON-serializable structures."""
    arm_labels = list(arms_results.keys())

    per_arm = {label: aggregate_arm(results) for label, results in arms_results.items()}

    categories: set[str] = set()
    for results in arms_results.values():
        for r in results:
            cat = r.get("_meta", {}).get("position", {}).get("category")
            if cat:
                categories.add(cat)
    per_category: dict[str, dict[str, Any]] = {}
    for label, results in arms_results.items():
        for cat in sorted(categories):
            cat_results = [
                r for r in results if r.get("_meta", {}).get("position", {}).get("category") == cat
            ]
            if not cat_results:
                continue
            per_category.setdefault(label, {})[cat] = aggregate_arm(cat_results)

    paired: dict[str, Any] = {}
    if base_label in arms_results:
        for label in arm_labels:
            if label == base_label:
                continue
            paired[label] = paired_comparison(arms_results[base_label], arms_results[label])

    return {
        "arm_labels": arm_labels,
        "base_label": base_label,
        "per_arm": per_arm,
        "per_category": per_category,
        "paired_vs_base": paired,
    }


def write_json_summary(path: Path, arms_results: dict[str, list[dict[str, Any]]], base_label: str) -> None:
    summary = build_json_summary(arms_results, base_label)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nWrote JSON summary to {path}")


# --------------------------------------------------------------------------
# Loading existing results (for --resume / --report-only)
# --------------------------------------------------------------------------


def load_arm_results(out_dir: Path, arm_label: str) -> list[dict[str, Any]]:
    arm_dir = out_dir / arm_label
    results = []
    if not arm_dir.is_dir():
        return results
    for path in sorted(arm_dir.glob("*.json")):
        if not RESULT_FILE_RE.match(path.name):
            continue
        try:
            results.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return results


def result_path_for(out_dir: Path, arm_label: str, position: dict[str, Any], seed_offset: int) -> Path:
    return out_dir / arm_label / f"{position['seed']}_{position['floor']}_s{seed_offset}.json"


def log_path_for(out_dir: Path, arm_label: str, position: dict[str, Any], seed_offset: int) -> Path:
    return out_dir / arm_label / f"{position['seed']}_{position['floor']}_s{seed_offset}.log"


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--positions",
        default=None,
        help="Path to build_playout_positions.py output JSON. Required unless --report-only.",
    )
    parser.add_argument("--binary", default=str(DEFAULT_BINARY), help="battle-sim binary to run.")
    parser.add_argument(
        "--arms",
        action="append",
        default=None,
        help="'label:tok1 tok2 ...' — repeat for multiple arms. First arm listed is the baseline.",
    )
    parser.add_argument("--simulations", type=int, default=10000, help="simulations_per_thread.")
    parser.add_argument("--threads-per-playout", type=int, default=4, help="battle-sim thread_count per playout.")
    parser.add_argument("--parallel", type=int, default=2, help="Concurrent playouts (total CPU = parallel * threads-per-playout).")
    parser.add_argument("--max-time-ms", type=int, default=3000, help="Per-decision max_time_ms.")
    parser.add_argument("--seeds", type=int, default=1, help="Number of rng_offset values to run, 1..N.")
    parser.add_argument("--potion-slots", default="0,1,2", help="Comma list for potion_slots=..., empty to omit.")
    parser.add_argument("--max-decisions", type=int, default=400, help="playout_max_decisions=N.")
    parser.add_argument("--out", default=None, help="Output directory (default: scratchpad/ab/<timestamp>).")
    parser.add_argument("--resume", action="store_true", help="Skip playouts whose result file already exists.")
    parser.add_argument("--timeout-s", type=float, default=1800.0, help="Per-playout wall timeout (default 30 min).")
    parser.add_argument("--report-only", action="store_true", help="Skip running; just print tables for --out.")
    parser.add_argument(
        "--json-summary",
        default=None,
        help="Path to also write the per-arm, per-category and paired tables as JSON.",
    )
    args = parser.parse_args()

    out_dir = Path(args.out) if args.out else DEFAULT_OUT_BASE / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"

    if args.report_only:
        if args.arms:
            arm_labels = [parse_arm(a)["label"] for a in args.arms]
        elif manifest_path.exists():
            arm_labels = json.loads(manifest_path.read_text(encoding="utf-8"))["arm_labels"]
        else:
            arm_labels = sorted(p.name for p in out_dir.iterdir() if p.is_dir())
        if not arm_labels:
            raise SystemExit(f"--report-only: no arms found in {out_dir} (pass --arms or check the path)")
        arms_results = {label: load_arm_results(out_dir, label) for label in arm_labels}
        total = sum(len(v) for v in arms_results.values())
        print(f"--report-only: loaded {total} results from {out_dir}")
        print_aggregate_table(arms_results)
        print_paired_table(arms_results, arm_labels[0])
        print_category_breakdown(arms_results)
        if args.json_summary:
            write_json_summary(Path(args.json_summary), arms_results, arm_labels[0])
        return

    if not args.arms:
        raise SystemExit("--arms is required (repeat for multiple arms; first arm is the baseline)")
    if not args.positions:
        raise SystemExit("--positions is required (unless --report-only)")
    arms = [parse_arm(a) for a in args.arms]
    arm_labels = [a["label"] for a in arms]
    if len(set(arm_labels)) != len(arm_labels):
        raise SystemExit(f"--arms labels must be unique, got {arm_labels}")

    binary = Path(args.binary)
    if not binary.exists():
        raise SystemExit(f"binary not found: {binary}")
    positions = load_positions(Path(args.positions))
    if not positions:
        raise SystemExit(f"no positions in {args.positions}")
    seed_offsets = list(range(1, args.seeds + 1))
    potion_slots = args.potion_slots.strip()

    manifest_path.write_text(
        json.dumps(
            {
                "arm_labels": arm_labels,
                "arms": arms,
                "positions_file": str(Path(args.positions).resolve()),
                "n_positions": len(positions),
                "simulations": args.simulations,
                "threads_per_playout": args.threads_per_playout,
                "parallel": args.parallel,
                "max_time_ms": args.max_time_ms,
                "seeds": args.seeds,
                "potion_slots": potion_slots,
                "max_decisions": args.max_decisions,
                "binary": str(binary.resolve()),
                "created_at": datetime.now().isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    jobs = []
    for position in positions:
        for arm in arms:
            for seed_offset in seed_offsets:
                result_path = result_path_for(out_dir, arm["label"], position, seed_offset)
                log_path = log_path_for(out_dir, arm["label"], position, seed_offset)
                if args.resume and result_path.exists():
                    continue
                jobs.append((position, arm, seed_offset, result_path, log_path))

    total_jobs = len(jobs)
    print(
        f"Running {total_jobs} playouts "
        f"({len(positions)} positions x {len(arms)} arms x {len(seed_offsets)} seeds) "
        f"with --parallel {args.parallel} x --threads-per-playout {args.threads_per_playout} "
        f"= {args.parallel * args.threads_per_playout} threads, into {out_dir}"
    )

    arms_results: dict[str, list[dict[str, Any]]] = {label: [] for label in arm_labels}
    if args.resume:
        for label in arm_labels:
            arms_results[label] = load_arm_results(out_dir, label)
        skipped = sum(len(v) for v in arms_results.values())
        if skipped:
            print(f"--resume: {skipped} existing results loaded and will be kept")

    done = 0
    print_every = max(1, total_jobs // 20) if total_jobs > 30 else 1
    lock = Lock()

    def submit(position, arm, seed_offset, result_path, log_path):
        return run_one_playout(
            binary,
            position,
            arm,
            seed_offset,
            args.simulations,
            args.threads_per_playout,
            args.max_time_ms,
            args.max_decisions,
            potion_slots,
            args.timeout_s,
            result_path,
            log_path,
        )

    start_time = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
        futures = {
            pool.submit(submit, position, arm, seed_offset, result_path, log_path): arm["label"]
            for (position, arm, seed_offset, result_path, log_path) in jobs
        }
        for future in as_completed(futures):
            label = futures[future]
            result = future.result()
            with lock:
                arms_results[label].append(result)
                done += 1
                if done % print_every == 0 or done == total_jobs:
                    running = ", ".join(
                        f"{l}={fmt_pct(aggregate_arm(r)['win_rate'])}"
                        for l, r in arms_results.items()
                        if r
                    )
                    print(f"[{done}/{total_jobs}] running win rates: {running}")

    wall_s = time.perf_counter() - start_time
    print(f"\nAll playouts finished in {wall_s:.1f}s\n")
    print_aggregate_table(arms_results)
    print_paired_table(arms_results, arm_labels[0])
    print_category_breakdown(arms_results)
    if args.json_summary:
        write_json_summary(Path(args.json_summary), arms_results, arm_labels[0])


if __name__ == "__main__":
    main()
