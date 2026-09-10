import json, sys
from pathlib import Path

sys.path.insert(0, "/tmp/claude-1000/-var-home-painter/f53546bc-cae3-4e7c-bf6f-22cb8cc383fa/scratchpad/survey")
sys.path.insert(0, "/var/home/painter/spire-agent/src")
import common
from act2_boss_survey import card_base_damage, _CARD_DB
from spire_agent.tools.heuristics.cards import HEART_SCALING, normalize

DYING_SEEDS = [
    "1RVKP2WCX5TCP", "1XSK3H2EN0TF1", "489ZFTZYT9F1U", "4BGWNFFSQK201",
    "502RQ0J3NS71T", "562BGVECI77BH", "YGUXMTABVTK8",
]

REPO = Path("/var/home/painter/spire-agent")

def load_jsonl(p):
    out = []
    if not p.is_file():
        return out
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out

print("=== card-reward offers floor 17-32: heavy(>=12dmg)/core offered but NOT taken ===")
for seed in DYING_SEEDS:
    d = REPO / "runs" / seed
    choices = load_jsonl(d / "card_choices.jsonl")
    for c in choices:
        dec = c.get("decision") or {}
        floor = dec.get("floor")
        if floor is None or not (17 <= floor <= 32):
            continue
        offered = dec.get("offered") or []
        picked = dec.get("picked")
        flagged = []
        for name in offered:
            key = normalize(name)
            dmg = card_base_damage(key)
            is_heavy = dmg is not None and dmg >= 12
            is_core = key in HEART_SCALING
            if (is_heavy or is_core) and normalize(picked or "") != key:
                flagged.append((name, "heavy" if is_heavy else "", "core" if is_core else "", dmg))
        if flagged:
            print(f"{seed} floor {floor}: offered={offered} picked={picked!r} skipped={dec.get('skipped')} "
                  f"source={dec.get('source')} reason={dec.get('reason')} flagged_not_taken={flagged}")

print("\n=== shop purchases floor 17-32 (dying runs) ===")
for seed in DYING_SEEDS:
    d = REPO / "runs" / seed
    entries = common.load_entries(d)
    for e in entries:
        before = e.get("before") or {}
        run = before.get("run") or {}
        floor = run.get("floor")
        if floor is None or not (17 <= floor <= 32):
            continue
        action = e.get("action") or {}
        dec = action.get("decision") or {}
        reason = str(dec.get("reason") or "")
        if dec.get("source") == "build.shop_heuristic" and reason.startswith("buy"):
            print(f"{seed} floor {floor}: {reason} (label={action.get('label')})")
