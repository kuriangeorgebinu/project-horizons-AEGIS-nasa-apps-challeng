"""Extract IL-6, CRP and blood-count context from the Inspiration4 OSDR files (data/real/)."""
import csv, json, re, statistics
from pathlib import Path

REAL = Path(__file__).resolve().parent.parent / "data" / "real"

def rows(name):
    with open(REAL / name, newline="", encoding="utf-8-sig", errors="replace") as f:
        rs = list(csv.DictReader(f))
    for r in rs:                                   # normalise header names (BOM / stray spaces)
        for k in list(r):
            if k and k.strip().lstrip("﻿") != k:
                r[k.strip().lstrip("﻿")] = r.pop(k)
    return rs

def tp(sample):
    m = re.match(r"(C\d+)_.*?_(L-\d+|R\+\d+)", sample)
    return (m.group(1), m.group(2)) if m else (sample, "?")

def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None

out = {"il6": [], "crp": [], "cbc": []}
imm = rows("LSDS-8_Multiplex_serum_immune_EvePanel_TRANSFORMED.csv")
il6_cols = [c for c in imm[0] if c.lower().startswith("il_6_concentration") or c.lower().startswith("il6_concentration")]
for r in imm:
    crew, t = tp(r["Sample Name"])
    out["il6"].append({"crew": crew, "tp": t, "pg_ml": num(r[il6_cols[0]]) if il6_cols else None})
cv = rows("LSDS-8_Multiplex_serum_cardiovascular_EvePanel_TRANSFORMED.csv")
for r in cv:
    crew, t = tp(r["Sample Name"])
    v = num(r.get("crp_concentration_picogram_per_milliliter"))
    out["crp"].append({"crew": crew, "tp": t, "mg_l": round(v / 1e6, 3) if v is not None else None})
cbc = rows("LSDS-7_Complete_Blood_Count_CBC_TRANSFORMED.csv")
wbc = [c for c in cbc[0] if c.startswith("white_blood_cell_count_value") or c.startswith("wbc_value")]
lym = [c for c in cbc[0] if c.startswith("absolute_lymphocytes_value")]
for r in cbc:
    crew, t = tp(r["Sample Name"])
    out["cbc"].append({"crew": crew, "tp": t, "wbc": num(r[wbc[0]]) if wbc else None,
                       "lymph": num(r[lym[0]]) if lym else None})
out["il6_column"] = il6_cols
out["immune_columns_sample"] = [c for c in imm[0] if "concentration" in c][:80]

def summary(key, field):
    pre = [x[field] for x in out[key] if x[field] is not None and x["tp"].startswith("L-")]
    r1 = [x[field] for x in out[key] if x[field] is not None and x["tp"] == "R+1"]
    later = [x[field] for x in out[key] if x[field] is not None and x["tp"].startswith("R+") and x["tp"] != "R+1"]
    f = lambda a: {"n": len(a), "min": min(a), "median": statistics.median(a), "max": max(a)} if a else None
    return {"preflight": f(pre), "return_day_1": f(r1), "recovery": f(later)}

out["summary"] = {"il6_pg_ml": summary("il6", "pg_ml"), "crp_mg_l": summary("crp", "mg_l")}
(Path(__file__).resolve().parent / "i4_values.json").write_text(json.dumps(out, indent=1))
print(json.dumps({"il6_column": out["il6_column"], "summary": out["summary"],
                  "il6": out["il6"], "crp": out["crp"]}, indent=1))
