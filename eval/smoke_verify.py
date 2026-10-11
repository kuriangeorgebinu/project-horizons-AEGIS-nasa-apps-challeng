import sys, json
sys.path.insert(0, r"C:\Users\kgeor\Desktop\project-horizons-AEGIS-nasa-apps-challeng")
import aegis_verify as V

menu = ["Move to designated storm shelter area NOW", "Don personal radiation monitoring device",
        "Await all-clear from Mission Control", "Log exposure event in radiation tracking system"]
passages = ["Solar particle events can deliver high doses within hours; crews shelter in the most shielded "
            "module surrounded by water and stowage to reduce dose.",
            "Sleep loss impairs cognitive performance; the HERA analog showed reduced reaction times."]
ctx = "Flight Engineer: heart rate 88 bpm, SpO2 97%, radiation 1.24 mSv in last 24h. Threshold 1.0 mSv."
good = "The engineer took 1.24 mSv in a day, above the 1.0 limit, so shielding matters now [1]."
bad = "The engineer took 3.8 mSv, and potassium iodide 130 mg is advised; sleep studies agree [1]. Shielding cuts dose [2]."
for t, acts in [(good, ["Head to the storm shelter right now", "Wear your radiation badge"]),
                (bad, ["Take a nap for 2 hours", "Head to the storm shelter right now"])]:
    print(json.dumps(V.summary(V.verify(t, acts, menu, passages, ctx)), indent=1))
pairs = [("Head to the storm shelter right now", menu), ("Take it easy for the next couple of hours", menu)]
for a, m in pairs:
    print(a, [round(x, 2) for x in V.similarity(a, m)])
