"""Step 1 live check of the scenarios: cascade_demo (amber, no shelter), spe_event (shelter), buildup timing."""
import sys, time
sys.path.insert(0, __file__.rsplit("\\", 1)[0])
from verify_step1 import get, post, crew_line, alerts  # noqa: E402  (runs the nominal checks first)


def scenario(name, wait):
    post("/api/demo/reset")
    time.sleep(6)
    post(f"/api/simulation/scenario/{name}")
    time.sleep(wait)
    print(f"\n{name.upper()} after {wait}s:", *crew_line(), sep="\n  ")
    print("  alerts:", alerts())


scenario("cascade_demo", 40)
scenario("spe_event", 30)

# Build-up: when does each domain cross, and when does AEGIS first forecast the cascade?
post("/api/demo/reset"); time.sleep(6)
post("/api/simulation/scenario/buildup_demo")
t0, first_fc, crossed = time.time(), None, {}
while time.time() - t0 < 300:
    c = next(x for x in get("/api/predict")["crew"] if x["crew_member_id"] == "crew-001")
    el = round((time.time() - t0) / 60, 2)
    if c["risk"] == "cascade" and first_fc is None:
        first_fc = (el, c["cascade_eta_min"])
    for a in c["out_now"]:
        crossed.setdefault(a, el)
    if len(crossed) >= 3:
        break
    time.sleep(5)
print("\nBUILDUP: first cascade forecast at (min, eta):", first_fc, "| domains crossed (min):", crossed)
if first_fc and len(crossed) >= 3:
    third = sorted(crossed.values())[2]
    print(f"  lead time = {third - first_fc[0]:.1f} min before the 3rd domain crossed")
print("  alerts:", alerts())
post("/api/demo/reset")
