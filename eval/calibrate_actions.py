"""Action-similarity calibration on the DeepSeek actions from results.json.
Positives: every AI action vs its own menu (all were human-reviewed as faithful rewrites).
Negatives: the same actions vs the menu of a scenario from a DIFFERENT agent (an off-menu action)."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import aegis_verify as V

R = json.loads((ROOT / "eval" / "results.json").read_text())
T = [t for t in R["B_traces"] if t.get("history")]
pos, neg = [], []
for t in T:
    for h in t["history"]:
        for a in (h.get("data") or {}).get("actions", []):
            core = V._ADDRESSEE.sub("", a)
            pos.append(max(V.similarity(core, t["menu"])))
            for u in T:
                if u["agent"] != t["agent"]:
                    neg.append(max(V.similarity(core, u["menu"])))
pos.sort(); neg.sort()
print("positives n=%d min=%.2f p05=%.2f median=%.2f" % (len(pos), pos[0], pos[len(pos)//20], pos[len(pos)//2]))
print("negatives n=%d median=%.2f p95=%.2f max=%.2f" % (len(neg), neg[len(neg)//2], neg[int(len(neg)*.95)], neg[-1]))
for th in (0.30, 0.35, 0.40, 0.45, 0.50):
    fa = sum(p < th for p in pos) / len(pos); miss = sum(n >= th for n in neg) / len(neg)
    print(f"threshold {th:.2f}: false alarms on genuine {fa:.1%}, off-menu missed {miss:.1%}")
json.dump({"pos": pos, "neg": neg}, open(ROOT / "eval" / "action_calibration.json", "w"))
