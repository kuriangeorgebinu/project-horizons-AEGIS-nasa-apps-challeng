"""Step 0 check: after a reset, the same scenario produces the same readings (twice), and the reset is clean."""
import json
import time
import urllib.request

B = "http://127.0.0.1:8000"


def call(path, method="GET"):
    with urllib.request.urlopen(urllib.request.Request(B + path, method=method), timeout=20) as r:
        return json.loads(r.read())


for _ in range(60):                                   # wait for the backend
    try:
        call("/health"); break
    except Exception:
        time.sleep(2)


def run():
    out = call("/api/demo/reset", "POST")
    call("/api/simulation/scenario/buildup_demo?crew_id=crew-001", "POST")
    time.sleep(32)                                    # ~6 simulator ticks
    hist = sorted(call("/api/crew/crew-001/vitals/history?hours=1")["history"], key=lambda h: h["timestamp"])
    rows = [(h["vitals"]["heart_rate"], h["vitals"]["sleep_quality"], h["vitals"]["spo2"]) for h in hist][:5]
    return out, rows


r1, a = run()
r2, b = run()
alerts = call("/api/alerts?unacknowledged_only=true&limit=50")
recs = call("/api/recommendations/pending")
crew = {c["id"]: (c["name"], round(c["cumulative_radiation"], 1)) for c in call("/api/crew")}
call("/api/simulation/scenario/nominal", "POST")
print("reset 1 removed:", r1["removed"])
print("run 1 first readings (HR, sleep, SpO2):", a)
print("run 2 first readings (HR, sleep, SpO2):", b)
print("REPEATABLE:", a == b)
print("open alerts right after run 2:", len(alerts), "| pending recommendations:", len(recs))
print("crew after reset:", crew)
