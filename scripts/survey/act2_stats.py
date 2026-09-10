import json, statistics

rows = []
with open("/tmp/claude-1000/-var-home-painter/f53546bc-cae3-4e7c-bf6f-22cb8cc383fa/scratchpad/survey/act2_rows.jsonl") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rows.append(json.loads(line))

print(f"n={len(rows)}")
deaths = [r for r in rows if r["died"]]
survivors = [r for r in rows if not r["died"]]
heart_kills = [r for r in rows if r["final_outcome"] == "Heart kill"]
rest = [r for r in rows if r["final_outcome"] != "Heart kill"]

NUMERIC = ["deck_size", "upgrades", "attacks", "strikes", "defends", "block_cards",
           "aoe", "heavy10", "hp_frac", "potions", "turns", "dpt"]

def mean(vals):
    vals = [v for v in vals if v is not None]
    return statistics.fmean(vals) if vals else None

def report(group_a, name_a, group_b, name_b):
    print(f"\n{name_a} (n={len(group_a)}) vs {name_b} (n={len(group_b)}):")
    for k in NUMERIC:
        ma, mb = mean([r[k] for r in group_a]), mean([r[k] for r in group_b])
        print(f"  {k:<12} {name_a}={ma:.2f}  {name_b}={mb:.2f}" if ma is not None and mb is not None else f"  {k:<12} n/a")
    ca = sum(1 for r in group_a if r["has_core"]); cb = sum(1 for r in group_b if r["has_core"])
    fa = sum(1 for r in group_a if r["has_finisher"]); fb = sum(1 for r in group_b if r["has_finisher"])
    print(f"  has_core     {name_a}={ca}/{len(group_a)}  {name_b}={cb}/{len(group_b)}")
    print(f"  has_finisher {name_a}={fa}/{len(group_a)}  {name_b}={fb}/{len(group_b)}")

report(deaths, "DIED@33", survivors, "SURVIVED@33")
report(heart_kills, "HEART_KILL", rest, "REST")

print("\n--- per-run table (died@33) ---")
for r in deaths:
    print(json.dumps(r, sort_keys=True))

# boss breakdown among deaths
from collections import Counter
print("\nboss breakdown among deaths:", Counter(r["boss"] for r in deaths))
print("boss breakdown all:", Counter(r["boss"] for r in rows))
