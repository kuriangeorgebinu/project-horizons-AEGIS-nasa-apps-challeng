import json, sys
from pathlib import Path
R = json.loads((Path(__file__).parent / "results.json").read_text())
for t in R.get("B_traces", []):
    print(f"\n=== B{t['scenario']} {t['agent']} {t.get('inputs')} :: {t.get('title')}")
    print("MENU:", t.get("menu"))
    for i, p in enumerate(t.get("passages", []), 1):
        print(f"  P{i}: {p[:160]}")
    for k, h in enumerate(t.get("history", []), 1):
        d = h.get("data") or {}
        print(f" -- attempt {k}: ok={h['result']['ok']}")
        print("   TEXT:", d.get("assessment"))
        print("   ACTS:", d.get("actions"))
        print("   PROBLEMS:", h["result"]["problems"])
        c = h["result"].get("checks")
        if c:
            print("   CITE:", [(x["ref"], x["sim"]) for x in c["citations"]["detail"]])
            print("   ACTSIM:", [(x["sim"], (x["menu_item"] or "")[:40]) for x in c["actions"]["detail"]])
print("\nC:", R.get("C_checker_recall"))
print("B:", json.dumps(R.get("B_summary"), indent=1))
for q in R.get("D_qa", []):
    print("\nQ:", q["agent"], q["question"], "\nA:", q["answer"], q["first_attempt_unsupported"])
