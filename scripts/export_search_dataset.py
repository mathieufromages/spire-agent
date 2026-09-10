#!/usr/bin/env python3
"""Export a parquet imitation-learning dataset from recorded MCTS searches.

Walks runs/<seed>/mcts/*.json, applies the audit pipeline (dedup by request
hash, special-root exclusion, no-signal exclusion using the C++ dump's own
legal mask -- not the recorded stop-reason label), feeds the surviving
positions through `battle-sim batch_features` (which reads each position's
own settings.allowed_potion_slots directly from the file, so the potion mask
is honoured automatically, per-position, with zero grouping needed), and
writes train/val parquet files plus a JSON sidecar.

Run with the training env's interpreter (it has pyarrow/numpy; the bot's
system python3 does not):

    cd /var/home/painter/sts1-train && \
        HSA_OVERRIDE_GFX_VERSION=11.0.0 HIP_VISIBLE_DEVICES=0 \
        uv run python /var/home/painter/spire-agent/scripts/export_search_dataset.py \
        --out /var/home/painter/spire-agent/data/search_dataset

Safety: this script only reads runs/ and the features binary; it never
writes under runs/, build/, or build-final/, and it runs the feature dump as
one short-lived single process (a few tens of seconds for the full corpus).
"""
import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from collections import Counter, defaultdict

DEFAULT_RUNS_DIR = "/var/home/painter/spire-agent/runs"
DEFAULT_FEATURES_BINARY = (
    "/var/home/painter/spire-agent/3rd/sts_lightspeed/build-features/battle-sim"
)
DEFAULT_OUT = "/var/home/painter/spire-agent/data/search_dataset"
DEFAULT_VAL_SEEDS = [
    "1FKKUB75I30V3", "1GUI2VUCXEKDD", "3MIZS1173ZPJF", "5FE0DVVDEE5E7",
    "3MD41H9XYUF2U", "SX0QARW8FXG", "3HFKH7FBF8673", "1F1TK117VYE9E",
    "39TXJLRYPLZBP", "3C8DSZRZ85XTB", "54N8YZ4V5F2PD",
]
NEVER_VAL_SEEDS = {"2ACEGSXXAMYQJ", "45XFWP97LG7R9"}
ROLE_PRIORITY = {"potion_final": 0, "authorized_potion": 1, "baseline": 2, "potion_probe": 3}
ACTION_SLOT_COUNT = 91
FEATURE_LENGTH = 1701


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR)
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--features-binary", default=DEFAULT_FEATURES_BINARY)
    p.add_argument("--val-seeds", default=",".join(DEFAULT_VAL_SEEDS),
                    help="comma-separated seed list")
    p.add_argument("--temperature", type=float, default=0.1,
                    help="softmax temperature over recorded rootActions[].value")
    p.add_argument("--limit", type=int, default=None,
                    help="cap the number of post-dedup positions processed (smoke runs)")
    p.add_argument("--scratch", default="/tmp/export_search_dataset_batch.jsonl",
                    help="temp file for the batch_features stdout")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Step 1: walk runs/, extract lightweight per-position records
# ---------------------------------------------------------------------------

def walk_records(runs_dir):
    records = []
    errors = []
    run_dirs = sorted(
        d for d in os.listdir(runs_dir)
        if os.path.isdir(os.path.join(runs_dir, d)) and not d.endswith(".pre-fix-backup")
    )
    for seed in run_dirs:
        mcts_dir = os.path.join(runs_dir, seed, "mcts")
        if not os.path.isdir(mcts_dir):
            continue
        for fn in sorted(os.listdir(mcts_dir)):
            if not fn.endswith(".json"):
                continue
            fp = os.path.join(mcts_dir, fn)
            try:
                with open(fp, "r") as f:
                    d = json.load(f)
            except Exception as e:
                errors.append((seed, fn, "load_error", str(e)))
                continue
            try:
                req = d.get("request", {}) or {}
                gs = req.get("game_state", {}) or {}
                settings = d.get("settings", {}) or {}
                rr = d.get("raw_result", {}) or {}
                root_actions = rr.get("rootActions", []) or []
                ra_slim = [
                    {"action": a.get("action"), "value": a.get("value")}
                    for a in root_actions
                ]
                req_hash = hashlib.md5(
                    json.dumps(req, sort_keys=True, default=str).encode("utf-8")
                ).hexdigest()
                cs = gs.get("combat_state")
                rec = {
                    "seed": seed,
                    "path": fp,
                    "search_id": d.get("search_id") or os.path.splitext(fn)[0],
                    "act": gs.get("act"),
                    "room_type": gs.get("room_type"),
                    "screen_type": gs.get("screen_type"),
                    "floor": gs.get("floor"),
                    "turn": cs.get("turn") if isinstance(cs, dict) else None,
                    "search_role": settings.get("search_role"),
                    "rootCommand": rr.get("rootCommand"),
                    "followUp": rr.get("followUp"),
                    "searchStopReason": rr.get("searchStopReason"),
                    "n_root_actions": len(root_actions),
                    "root_actions": ra_slim,
                    "req_hash": req_hash,
                }
                records.append(rec)
            except Exception as e:
                errors.append((seed, fn, "parse_error", str(e)))
    return records, errors, run_dirs


# ---------------------------------------------------------------------------
# Step 2: outcomes from run_history.jsonl (direct join by search_id, plus a
# same-(seed,floor) fallback built only from directly-joined positions)
# ---------------------------------------------------------------------------

def build_fight_outcomes(runs_dir, seed):
    path = os.path.join(runs_dir, seed, "run_history.jsonl")
    outcome = {}
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except Exception:
        return outcome
    cur_fight_sids = []
    for l in lines:
        try:
            d = json.loads(l)
        except Exception:
            continue
        if d.get("type") != "action":
            continue
        before, after = d.get("before", {}), d.get("after", {})
        b_combat, a_combat = before.get("combat"), after.get("combat")
        action = d.get("action", {})
        dec = action.get("decision", {})
        sid = dec.get("metrics", {}).get("search_id")
        if dec.get("source") == "combat.mcts" and sid:
            cur_fight_sids.append(sid)
        if b_combat is None and a_combat is not None:
            cur_fight_sids = []
        if b_combat is not None and a_combat is None:
            term = after.get("terminal", False)
            victory = ((after.get("screen") or {}).get("details") or {}).get("victory")
            run_after = after.get("run", {})
            end_hp = run_after.get("current_hp")
            max_hp = run_after.get("max_hp")
            win = not (term and victory is False)
            for s in cur_fight_sids:
                outcome[s] = (win, end_hp, max_hp)
            cur_fight_sids = []
    return outcome


def build_outcomes(runs_dir, run_dirs):
    all_outcomes = {}
    for seed in run_dirs:
        for sid, v in build_fight_outcomes(runs_dir, seed).items():
            all_outcomes[(seed, sid)] = v
    return all_outcomes


def build_floor_fallback(records, outcomes):
    floor_outcome = {}
    for r in records:
        key = (r["seed"], r["search_id"])
        if key in outcomes:
            floor_outcome.setdefault((r["seed"], r["floor"]), outcomes[key])
    return floor_outcome


def resolve_value_target(rec, outcomes, floor_outcome):
    key = (rec["seed"], rec["search_id"])
    if key in outcomes:
        win, end_hp, max_hp = outcomes[key]
    else:
        fkey = (rec["seed"], rec["floor"])
        if fkey not in floor_outcome:
            return None, False
        win, end_hp, max_hp = floor_outcome[fkey]
    if not win:
        return 0.0, True
    if not max_hp:
        return None, False
    return max(0.0, min(1.0, end_hp / max_hp)), True


# ---------------------------------------------------------------------------
# Step 3: dedup by (seed, req_hash); prefer the search actually applied
# (direct run_history join), else role priority potion_final > authorized_potion
# > baseline > potion_probe.
# ---------------------------------------------------------------------------

def dedup(records, outcomes):
    groups = defaultdict(list)
    for i, r in enumerate(records):
        groups[(r["seed"], r["req_hash"])].append(i)

    def has_outcome(i):
        r = records[i]
        return (r["seed"], r["search_id"]) in outcomes

    reps = []
    for idxs in groups.values():
        if len(idxs) == 1:
            reps.append(idxs[0])
            continue
        applied = [i for i in idxs if has_outcome(i)]
        pool = applied if applied else idxs
        pool_sorted = sorted(pool, key=lambda i: ROLE_PRIORITY.get(records[i]["search_role"], 9))
        reps.append(pool_sorted[0])
    return reps, len(groups)


def is_special_root(rec):
    return rec["screen_type"] != "NONE" or rec["followUp"] is not None


# ---------------------------------------------------------------------------
# Step 4: run the C++ feature dump in ONE batch call over the representative
# positions' own file paths -- batch_features re-reads settings from each
# path itself, so the recorded allowed_potion_slots mask is honoured
# per-position automatically. No grouping needed; verified empirically
# (see report).
# ---------------------------------------------------------------------------

def run_batch_features(features_binary, paths, scratch_listfile, scratch_out):
    with open(scratch_listfile, "w") as f:
        for p in paths:
            f.write(p + "\n")
    t0 = time.time()
    with open(scratch_out, "w") as out:
        subprocess.run(
            [features_binary, "batch_features", scratch_listfile],
            stdout=out, stderr=subprocess.PIPE, check=True,
        )
    elapsed = time.time() - t0
    dump_by_path = {}
    with open(scratch_out) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            dump_by_path[d.get("path")] = d
    return dump_by_path, elapsed


def softmax(values, temperature):
    vmax = max(values)
    exps = [math.exp((v - vmax) / temperature) for v in values]
    s = sum(exps)
    return [e / s for e in exps]


# ---------------------------------------------------------------------------
# Step 5: build final rows
# ---------------------------------------------------------------------------

def build_rows(records, rep_idxs, dump_by_path, outcomes, floor_outcome, temperature):
    rows = []
    funnel = Counter()
    mismatches = []  # rootCommand not exactly-one-legal in the dump
    immediate_lethal_like = 0  # single recorded action, >1 legal per dump -> kept hard-only
    no_signal_dropped = 0      # single recorded action, <=1 legal per dump -> dropped
    soft_target_unusable = 0
    dump_error_or_skip = Counter()

    for i in rep_idxs:
        rec = records[i]
        funnel["post_dedup_pre_special_root"] += 1
        if is_special_root(rec):
            funnel["excluded_special_root"] += 1
            continue
        funnel["post_special_root_exclusion"] += 1

        dump = dump_by_path.get(rec["path"])
        if dump is None:
            funnel["excluded_dump_missing"] += 1
            continue
        if not dump.get("ok", False):
            dump_error_or_skip[dump.get("error", "unknown_error")] += 1
            funnel["excluded_dump_error"] += 1
            continue
        if dump.get("skipped", False):
            dump_error_or_skip["skipped:" + str(dump.get("skipReason"))] += 1
            funnel["excluded_dump_skipped"] += 1
            continue

        actions = dump.get("actions", [])
        if len(actions) != ACTION_SLOT_COUNT or len(dump.get("features", [])) != FEATURE_LENGTH:
            funnel["excluded_dump_shape_mismatch"] += 1
            continue

        # Multiple action-table slots can render the same command string --
        # e.g. a non-targeted card gets an identical "play N" text at every
        # target bucket, with only the no-target bucket actually legal -- so
        # matching must be restricted to LEGAL slots to be unique. (Verified:
        # /var/home/painter/spire-agent/runs/13A1LBRVTAEYT/mcts/000001.json,
        # command "play 2" appears at indices 6-11, only index 11 legal.)
        legal_mask = [bool(a["legal"]) for a in actions]
        n_legal = sum(legal_mask)
        legal_command_to_idx = defaultdict(list)
        for a in actions:
            if a["legal"]:
                legal_command_to_idx[a["command"]].append(a["index"])

        root_command = rec["rootCommand"]
        matches = legal_command_to_idx.get(root_command, [])
        if len(matches) != 1:
            mismatches.append((rec["seed"], rec["search_id"], root_command, matches))
            funnel["excluded_rootcommand_not_unique_legal"] += 1
            continue
        policy_target_index = matches[0]

        n_root_actions = rec["n_root_actions"]
        stop_reason = rec["searchStopReason"]
        soft_target = [0.0] * ACTION_SLOT_COUNT
        has_soft_target = False
        is_early_stop_hard_only = False

        if n_root_actions <= 1:
            # Recorded search reported only the chosen action. Trust the
            # C++ dump's own legal mask (not the stop-reason label) to
            # decide whether more actions were actually legal:
            #   n_legal > 1  -> early-stop shortcut (e.g. immediate_lethal);
            #                    keep as a hard-target-only row.
            #   n_legal <= 1 -> genuinely forced move, no policy signal at all.
            if n_legal > 1:
                is_early_stop_hard_only = True
                immediate_lethal_like += 1
            else:
                no_signal_dropped += 1
                funnel["excluded_no_signal_single_legal"] += 1
                continue
        else:
            if n_legal <= 1:
                # Recorded multiple root actions but the dump now shows only
                # one legal slot (shouldn't happen given the audit's 63/63
                # mask-honoured check, but guard anyway).
                funnel["excluded_dump_legal_count_shrank"] += 1
                continue
            values, support_idx = [], []
            for a in rec["root_actions"]:
                v = a["value"]
                idxs = legal_command_to_idx.get(a["action"], [])
                if v is None or (isinstance(v, float) and math.isnan(v)) or len(idxs) != 1:
                    continue
                values.append(v)
                support_idx.append(idxs[0])
            if len(values) >= 2 and len(set(values)) > 1:
                probs = softmax(values, temperature)
                for idx, p in zip(support_idx, probs):
                    soft_target[idx] = p
                has_soft_target = True
            else:
                soft_target_unusable += 1

        bootstrap_value = None
        for a in rec["root_actions"]:
            if a["action"] == root_command:
                bootstrap_value = a["value"]
                break
        if bootstrap_value is None:
            funnel["excluded_no_bootstrap_value"] += 1
            continue

        gt_value, has_gt_value = resolve_value_target(rec, outcomes, floor_outcome)
        if gt_value is None:
            gt_value = 0.0

        rows.append({
            "features": dump["features"],
            "legal_mask": legal_mask,
            "policy_target_index": policy_target_index,
            "soft_target": soft_target,
            "has_soft_target": has_soft_target,
            "is_early_stop_hard_only": is_early_stop_hard_only,
            "bootstrap_value": float(bootstrap_value),
            "gt_value": float(gt_value),
            "has_gt_value": bool(has_gt_value),
            "stop_reason": stop_reason if stop_reason is not None else "",
            "seed": rec["seed"],
            "floor": rec["floor"] if rec["floor"] is not None else -1,
            "act": rec["act"] if rec["act"] is not None else -1,
            "room_type": rec["room_type"] if rec["room_type"] is not None else "",
            "turn": rec["turn"] if rec["turn"] is not None else -1,
            "search_id": rec["search_id"],
            "search_role": rec["search_role"] if rec["search_role"] is not None else "",
        })
        funnel["kept"] += 1

    diagnostics = {
        "mismatches": mismatches,
        "immediate_lethal_like_kept": immediate_lethal_like,
        "no_signal_single_legal_dropped": no_signal_dropped,
        "soft_target_unusable_degenerate": soft_target_unusable,
        "dump_error_or_skip_reasons": dict(dump_error_or_skip),
    }
    return rows, funnel, diagnostics


# ---------------------------------------------------------------------------
# Step 6: split, write parquet + sidecar
# ---------------------------------------------------------------------------

def write_split(rows, out_dir, split_name):
    import pyarrow as pa
    import pyarrow.parquet as pq

    n = len(rows)
    feat_flat = [x for r in rows for x in r["features"]]
    legal_flat = [x for r in rows for x in r["legal_mask"]]
    soft_flat = [x for r in rows for x in r["soft_target"]]

    features_arr = pa.FixedSizeListArray.from_arrays(
        pa.array(feat_flat, type=pa.float32()), FEATURE_LENGTH
    )
    legal_arr = pa.FixedSizeListArray.from_arrays(
        pa.array(legal_flat, type=pa.bool_()), ACTION_SLOT_COUNT
    )
    soft_arr = pa.FixedSizeListArray.from_arrays(
        pa.array(soft_flat, type=pa.float32()), ACTION_SLOT_COUNT
    )

    table = pa.table({
        "features": features_arr,
        "legal_mask": legal_arr,
        "policy_target_index": pa.array([r["policy_target_index"] for r in rows], type=pa.int32()),
        "soft_target": soft_arr,
        "has_soft_target": pa.array([r["has_soft_target"] for r in rows], type=pa.bool_()),
        "is_early_stop_hard_only": pa.array([r["is_early_stop_hard_only"] for r in rows], type=pa.bool_()),
        "bootstrap_value": pa.array([r["bootstrap_value"] for r in rows], type=pa.float32()),
        "gt_value": pa.array([r["gt_value"] for r in rows], type=pa.float32()),
        "has_gt_value": pa.array([r["has_gt_value"] for r in rows], type=pa.bool_()),
        "stop_reason": pa.array([r["stop_reason"] for r in rows], type=pa.string()),
        "seed": pa.array([r["seed"] for r in rows], type=pa.string()),
        "floor": pa.array([r["floor"] for r in rows], type=pa.int32()),
        "act": pa.array([r["act"] for r in rows], type=pa.int32()),
        "room_type": pa.array([r["room_type"] for r in rows], type=pa.string()),
        "turn": pa.array([r["turn"] for r in rows], type=pa.int32()),
        "search_id": pa.array([r["search_id"] for r in rows], type=pa.string()),
        "search_role": pa.array([r["search_role"] for r in rows], type=pa.string()),
    })
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{split_name}.parquet")
    pq.write_table(table, path)
    return path, n


def main():
    args = parse_args()
    val_seeds = [s.strip() for s in args.val_seeds.split(",") if s.strip()]
    bad = [s for s in val_seeds if s in NEVER_VAL_SEEDS]
    if bad:
        print(f"WARNING: dropping never-val seeds from val list: {bad}", file=sys.stderr)
        val_seeds = [s for s in val_seeds if s not in NEVER_VAL_SEEDS]
    val_seed_set = set(val_seeds)

    t0 = time.time()
    print("Walking runs dir...", file=sys.stderr)
    records, walk_errors, run_dirs = walk_records(args.runs_dir)
    print(f"  {len(records)} positions across {len(run_dirs)} run dirs, "
          f"{len(walk_errors)} parse errors, {time.time()-t0:.1f}s", file=sys.stderr)

    t1 = time.time()
    print("Building outcomes from run_history.jsonl...", file=sys.stderr)
    outcomes = build_outcomes(args.runs_dir, run_dirs)
    floor_outcome = build_floor_fallback(records, outcomes)
    print(f"  {len(outcomes)} directly-joinable fight outcomes, "
          f"{len(floor_outcome)} (seed,floor) fallback keys, {time.time()-t1:.1f}s", file=sys.stderr)

    t2 = time.time()
    rep_idxs, n_groups = dedup(records, outcomes)
    print(f"Dedup: {len(records)} raw -> {len(rep_idxs)} distinct-request representatives "
          f"({n_groups} groups), {time.time()-t2:.1f}s", file=sys.stderr)

    if args.limit is not None:
        rep_idxs = rep_idxs[:args.limit]
        print(f"--limit applied: {len(rep_idxs)} representatives", file=sys.stderr)

    n_special = sum(1 for i in rep_idxs if is_special_root(records[i]))
    print(f"Special roots (screen_type!=NONE or followUp set) among representatives: {n_special}",
          file=sys.stderr)

    dump_paths = [records[i]["path"] for i in rep_idxs if not is_special_root(records[i])]
    print(f"Running batch_features over {len(dump_paths)} paths (single process)...", file=sys.stderr)
    dump_by_path, dump_elapsed = run_batch_features(
        args.features_binary, dump_paths, args.scratch + ".list", args.scratch
    )
    rate = len(dump_paths) / dump_elapsed if dump_elapsed > 0 else float("inf")
    print(f"  batch_features: {len(dump_by_path)} results in {dump_elapsed:.1f}s "
          f"({rate:.0f} positions/s)", file=sys.stderr)

    rows, funnel, diagnostics = build_rows(
        records, rep_idxs, dump_by_path, outcomes, floor_outcome, args.temperature
    )
    print("Funnel:", dict(funnel), file=sys.stderr)
    print(f"immediate-lethal-like hard-only rows kept: {diagnostics['immediate_lethal_like_kept']}",
          file=sys.stderr)
    print(f"true no-signal (single legal action) rows dropped: {diagnostics['no_signal_single_legal_dropped']}",
          file=sys.stderr)
    print(f"soft-target degenerate/unusable among multi-action rows: {diagnostics['soft_target_unusable_degenerate']}",
          file=sys.stderr)
    if diagnostics["mismatches"]:
        print(f"!!! rootCommand mismatches: {len(diagnostics['mismatches'])} (showing up to 10):",
              file=sys.stderr)
        for m in diagnostics["mismatches"][:10]:
            print("   ", m, file=sys.stderr)

    # --- verification: rootCommand legal in 100% of kept rows ---
    illegal = [r for r in rows if not r["legal_mask"][r["policy_target_index"]]]
    if illegal:
        print(f"FATAL: {len(illegal)} kept rows have an illegal policy_target_index!", file=sys.stderr)
        sys.exit(1)
    bad_soft = [r for r in rows if r["has_soft_target"] and not math.isclose(sum(r["soft_target"]), 1.0, abs_tol=1e-4)]
    if bad_soft:
        print(f"FATAL: {len(bad_soft)} rows with has_soft_target=True but soft_target doesn't sum to 1!",
              file=sys.stderr)
        sys.exit(1)
    bad_value = [r for r in rows if not (0.0 <= r["gt_value"] <= 1.0) or not (-1.5 <= r["bootstrap_value"] <= 1.5)]
    if bad_value:
        print(f"FATAL: {len(bad_value)} rows with out-of-range value targets!", file=sys.stderr)
        sys.exit(1)
    print(f"Verification OK: rootCommand legal in 100% of {len(rows)} kept rows, "
          f"soft targets sum to 1 where flagged usable, value targets in range.", file=sys.stderr)

    train_rows = [r for r in rows if r["seed"] not in val_seed_set]
    val_rows = [r for r in rows if r["seed"] in val_seed_set]
    print(f"Split: train={len(train_rows)} val={len(val_rows)} "
          f"({100*len(val_rows)/max(1,len(rows)):.1f}% val)", file=sys.stderr)

    os.makedirs(args.out, exist_ok=True)
    train_path, n_train = write_split(train_rows, args.out, "train")
    val_path, n_val = write_split(val_rows, args.out, "val")
    print(f"Wrote {train_path} ({n_train} rows), {val_path} ({n_val} rows)", file=sys.stderr)

    def act_room_counts(rs):
        return {
            "by_act": dict(Counter(r["act"] for r in rs)),
            "by_room_type": dict(Counter(r["room_type"] for r in rs)),
            "by_stop_reason": dict(Counter(r["stop_reason"] for r in rs)),
        }

    sidecar = {
        "export_params": {
            "runs_dir": args.runs_dir,
            "features_binary": args.features_binary,
            "temperature": args.temperature,
            "limit": args.limit,
            "val_seeds": sorted(val_seed_set),
            "never_val_seeds": sorted(NEVER_VAL_SEEDS),
        },
        "cpp_schema": json.loads(
            subprocess.run([args.features_binary, "schema"], capture_output=True, check=True, text=True).stdout
        ),
        "funnel": dict(funnel),
        "diagnostics": {
            "n_rootcommand_mismatches": len(diagnostics["mismatches"]),
            "immediate_lethal_like_kept": diagnostics["immediate_lethal_like_kept"],
            "no_signal_single_legal_dropped": diagnostics["no_signal_single_legal_dropped"],
            "soft_target_unusable_degenerate": diagnostics["soft_target_unusable_degenerate"],
            "dump_error_or_skip_reasons": diagnostics["dump_error_or_skip_reasons"],
        },
        "walk_errors": len(walk_errors),
        "raw_positions": len(records),
        "distinct_request_groups": n_groups,
        "counts": {
            "train": n_train,
            "val": n_val,
            "total": n_train + n_val,
            "train_mix": act_room_counts(train_rows),
            "val_mix": act_room_counts(val_rows),
        },
        "timing_seconds": {
            "batch_features_wall_time": dump_elapsed,
            "batch_features_positions_per_second": rate,
            "total_wall_time": time.time() - t0,
        },
    }
    sidecar_path = os.path.join(args.out, "dataset_manifest.json")
    with open(sidecar_path, "w") as f:
        json.dump(sidecar, f, indent=2)
    print(f"Wrote {sidecar_path}", file=sys.stderr)
    print(f"TOTAL wall time: {time.time()-t0:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
