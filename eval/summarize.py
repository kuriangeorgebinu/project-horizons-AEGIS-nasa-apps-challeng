import json, pathlib
R = json.loads((pathlib.Path(__file__).resolve().parent / "results.json").read_text(encoding="utf-8"))
print("generated", R["generated"], R["model"])
print("A1", json.dumps(R["A1_gene_inspiration4"]["rules"]), R["A1_gene_inspiration4"].get("before_rule_any_flag_all_samples"))
a2 = R["A2_solaris_environments"]
print("A2 now", sum(r["agrees"] for r in a2), "/", len(a2), " before", sum(r["before_agrees"] for r in a2))
print("A3", sum(1 for r in R["A3_pulse_boundaries"] if r["agrees"]), "/", len(R["A3_pulse_boundaries"]))
print("B_summary", json.dumps(R["B_summary"], indent=1))
for i, t in enumerate(R["B_traces"], 1):
    h = t["history"]
    print(f" B{i} {t['title'][:45]:45} tries={len(h)} first={h[0]['result']['ok']} final={h[-1]['result']['ok']} "
          f"p1={[p[:70] for p in h[0]['result']['problems']]}" + (f" p2={[p[:70] for p in h[-1]['result']['problems']]}" if len(h) > 1 else ""))
print("C", json.dumps(R["C_checker_recall"], indent=0)[:1500])
for q in R["D_qa"] if isinstance(R["D_qa"], list) else R["D_qa"].get("items", []):
    print(" D", {k: (str(v)[:150]) for k, v in q.items() if k in ("agent", "question", "answer", "numbers_ok", "first_ok", "source", "unsupported", "first_unsupported")})
if isinstance(R["D_qa"], dict):
    print("D summary", {k: v for k, v in R["D_qa"].items() if k != "items"})
