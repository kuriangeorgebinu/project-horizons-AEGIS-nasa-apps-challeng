"""Step 1 live check: mission profile, nominal radiation never shelters, cascade amber, SPE shelters, What-If."""
import json, time, urllib.request

B = "http://127.0.0.1:8000"


def get(path):
    return json.load(urllib.request.urlopen(B + path, timeout=60))


def post(path, body=None):
    req = urllib.request.Request(B + path, data=json.dumps(body or {}).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=120))


def crew_line():
    out = []
    for c in get("/api/crew"):
        v = c.get("vitals") or {}
        out.append(f"{c['id']} {c['status']:8} rad24={v.get('radiation_dose_24h')} cum={c.get('cumulative_radiation'):.1f}")
    return out


def alerts():
    a = get("/api/alerts?unacknowledged_only=true&limit=50")
    a = a if isinstance(a, list) else a.get("alerts", a)
    return [f"{x.get('severity')} {x.get('title')}" for x in a]


print("MISSION", get("/api/mission"))
post("/api/demo/reset")
time.sleep(20)
print("NOMINAL:", *crew_line(), sep="\n  ")
print("  alerts:", alerts())
for cid in ("crew-001", "crew-003"):
    bm = get(f"/api/crew/{cid}/biomarkers")
    print("  biomarkers", cid, {k: bm[k] for k in ("il6", "baseline", "personal_limit", "status")})
fc = get("/api/predict")
for c in fc["crew"]:
    print("  predict", c["crew_member_id"], c["risk"], {m: (t["value"], t["threshold"], t.get("baseline"))
                                                        for m, t in c["metrics"].items() if m in ("il6", "radiation_dose_24h")})

print("\nWHAT-IF")
base = {"heart_rate": 70, "spo2": 98, "il6": 4.0, "sleep_quality": 80, "crew_id": "crew-001"}
for dose in (1.84, 2.5, 4.0, 9.5, 25):
    r = post("/api/whatif", {**base, "radiation_dose_24h": dose})
    print(f"  dose {dose:5}: verdict={r['verdict']} protocol={r.get('protocol')} solaris={r['agents'].get('SOLARIS', {}).get('level')}")
for il6 in (4.0, 5.5, 6.0, 13):
    r = post("/api/whatif", {**base, "il6": il6, "radiation_dose_24h": 1.84})
    print(f"  il6 {il6:5}: verdict={r['verdict']} gene={r['agents'].get('GENE', {}).get('level')} {r['agents'].get('GENE', {}).get('findings')}")
