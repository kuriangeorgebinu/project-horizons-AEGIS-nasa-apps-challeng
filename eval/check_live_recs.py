"""Audit the recommendations currently shown on the dashboard against live data (read-only)."""
import json, re, sys, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import aegis_verify as V

B = "http://127.0.0.1:8000"
get = lambda p: json.loads(urllib.request.urlopen(B + p, timeout=20).read())
recs = get("/api/recommendations/pending")
crew = {c["id"]: c for c in get("/api/crew")}
pred = {c["crew_member_id"]: c for c in get("/api/predict")["crew"]}
out = []
for r in recs:
    cid = r["crew_member_id"]
    c, p = crew.get(cid, {}), pred.get(cid, {})
    v = c.get("vitals") or {}
    rat = r.get("rationale") or ""
    m = re.match(r"^🧠 DeepSeek\s*\(([^)]+)\):\s*([\s\S]*?)(?:\n\nVerification:|\n\nRule basis:|$)", rat)
    ai = m.group(2).strip() if m else None
    vm = re.search(r"Verification: (\{[^\n]*\})", rat)
    ver = json.loads(vm.group(1)) if vm else None
    rb = rat.split("Rule basis:", 1)[1].strip() if "Rule basis:" in rat else None
    ev = []
    for e in r.get("evidence") or []:
        try:
            ev.append(json.loads(e))
        except Exception:
            ev.append({"text": e})
    live = (f"HR {v.get('heart_rate')} SpO2 {v.get('spo2')} sleep {v.get('sleep_quality')} "
            f"rad24 {v.get('radiation_dose_24h')} cum {c.get('cumulative_radiation')} day {c.get('mission_day')} "
            f"IL6 {(p.get('metrics') or {}).get('il6', {}).get('value')}")
    # Re-check the numbers in the AI text against TODAY's live readings (they may have changed since it was written)
    now_nums = V.check_numbers(ai or "", live + " " + (rb or "") + " " + r.get("description", "")) if ai else None
    out.append({"id": r["id"], "crew": cid, "role": c.get("role"), "agent": r["agent"], "pillar": r["pillar"],
                "severity": r["severity"], "title": r["title"], "created": r.get("created_at"),
                "description": r.get("description"), "rule_basis": rb, "ai": ai, "verification": ver,
                "actions": r.get("actions"), "sources": [(e.get("source"), e.get("cited")) for e in ev],
                "live_now": live, "ai_numbers_vs_live_now": now_nums,
                "live_out_of_range": p.get("out_now"), "live_risk": p.get("risk")})
(ROOT / "eval" / "live_recs.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
print(len(out), "pending recommendations")
