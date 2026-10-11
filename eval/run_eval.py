"""
AEGIS accuracy evaluation.

Part A  Rules vs REAL data and published references (no AI involved)
  A1 GENE IL-6 rule replayed on Inspiration4 crew serum IL-6 (NASA OSDR OSD-575)
  A2 SOLARIS dose rule replayed on measured radiation environments (ISS, MSL RAD cruise + Mars surface)
  A3 PULSE heart-rate / SpO2 boundaries vs clinical definitions
Part B  DeepSeek recommendations: how often the raw AI text is grounded, and after the checker + retry
Part C  Does the checker catch hallucinations? Inject known errors into verified outputs
Part D  Voice Q&A answers: are the numbers grounded in the live data?

Usage:  .venv\\Scripts\\python eval\\run_eval.py [--no-llm]
Writes eval/results.json
"""
import json
import os
import re
import statistics as st
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from models import CrewMember, Vitals, CrewRole                       # noqa: E402
import agents as A                                                    # noqa: E402
import aegis_verify as V                                              # noqa: E402
import aegis_llm                                                      # noqa: E402

OUT = {"generated": time.strftime("%Y-%m-%d %H:%M"), "model": None}
NO_LLM = "--no-llm" in sys.argv


def member(cid="eval-001", role=CrewRole.FLIGHT_ENGINEER, day=100, cum=None, **vit):
    base = dict(heart_rate=72, blood_pressure_systolic=118, blood_pressure_diastolic=76, spo2=98.0,
                temperature=36.8, respiratory_rate=14, stress_index=40, sleep_quality=82, radiation_dose_24h=0.3)
    base.update(vit)
    v = Vitals(**base)
    return CrewMember(id=cid, name="Eval", role=role, age=40, vitals=v, mission_day=day,
                      cumulative_radiation=cum if cum is not None else base["radiation_dose_24h"] * day)


# ───────────────────────── A1  GENE on Inspiration4 IL-6 ─────────────────────────
def a1_gene():
    i4 = json.loads((ROOT / "eval" / "i4_values.json").read_text())
    by = {}
    for r in i4["il6"]:
        by.setdefault(r["crew"], {})[r["tp"]] = r["pg_ml"]
    pre_tps = ["L-92", "L-44", "L-3"]
    rows = []
    for c, tps in sorted(by.items()):
        pre = [tps[t] for t in pre_tps]
        rows.append({"crew": c, **{t: tps[t] for t in pre_tps}, "baseline": round(st.mean(pre), 2),
                     "R+1": tps["R+1"], "R+45": tps["R+45"], "R+82": tps["R+82"], "R+194": tps["R+194"]})

    def current(v):
        f = A.GENEAgent.il6_findings(v)
        return f[0]["type"] if f else "none"

    def baseline_rule(v, base, ratio):
        return v >= base * ratio and v - base >= 1.5

    res = {"rows": rows, "rules": []}
    # Ground truth by study design: R+1 = day after return from 3 days in orbit (known stress state);
    # pre-flight samples = healthy baseline. n is tiny (4 crew), so this is a sanity check, not a validation.
    for name, fn in [("Current: absolute IL-6 > 8 pg/mL", lambda v, b: current(v) != "none")] + \
            [(f"Personal baseline: >= {r}x own pre-flight mean and +1.5 pg/mL", (lambda r: lambda v, b: baseline_rule(v, b, r))(r))
             for r in (1.25, 1.5, 2.0)]:
        tp = sum(1 for r in rows if fn(r["R+1"], r["baseline"]))
        # pre-flight false alarms, leave-one-out baseline (the sample under test is not in its own baseline)
        fp, n_pre = 0, 0
        for c, tps in by.items():
            for t in pre_tps:
                others = [tps[o] for o in pre_tps if o != t]
                n_pre += 1
                fp += bool(fn(tps[t], st.mean(others)))
        res["rules"].append({"rule": name, "detected_R+1": f"{tp}/4", "preflight_false_alarms": f"{fp}/{n_pre}"})
    res["rose_on_return"] = f"{sum(1 for r in rows if r['R+1'] > r['baseline'])}/4"
    res["current_rule_any_flag_all_samples"] = sum(1 for r in i4["il6"] if current(r["pg_ml"]) != "none")
    return res


# ───────────────────────── A2  SOLARIS on measured dose rates ─────────────────────
ENVIRONMENTS = [
    # (label, mSv/day, source, expected outcome)
    ("ISS, quiet sun (low end)", 0.44, "SIDC: ISS 80-160 mSv per 6 months", "no alert"),
    ("ISS, high end", 0.88, "SIDC: ISS 80-160 mSv per 6 months", "no alert"),
    ("Mars surface (Curiosity RAD)", 0.64, "Hassler et al. 2014 / Space.com: 0.64 mSv/day", "no alert"),
    ("Deep-space cruise (Curiosity RAD)", 1.84, "Zeitlin et al. 2013 / Space.com: 1.84 mSv/day", "no shelter (routine background)"),
    ("Solar particle event at NASA's 250 mSv limit", 250.0, "NASA OCHMO radiation brief: 250 mSv SPE limit", "shelter"),
]


def a2_solaris():
    agent, out = A.SOLARISAgent(), []
    for label, rate, src, expect in ENVIRONMENTS:
        f = agent.analyze(member(radiation_dose_24h=rate, day=180, cum=rate * 180))
        kinds = [x for x in f if x["metric"] == "radiation_24h"]
        got = ("shelter" if any(x["pillar"] == "PROTECT" for x in kinds) else
               "warning" if kinds else "no alert")
        proj = [x["message"] for x in f if x["metric"] == "cumulative_radiation"]
        ok = (got == "no alert") if expect.startswith("no alert") else \
             (got != "shelter") if expect.startswith("no shelter") else (got == "shelter")
        out.append({"environment": label, "mSv_per_day": rate, "source": src, "expected": expect,
                    "aegis_today": got, "projection": proj[0] if proj else "", "agrees": ok})
    return out


# ───────────────────────── A3  PULSE boundaries vs clinical definitions ───────────
def a3_pulse():
    cases = [
        ("heart_rate", 99, "none", "AHA: tachycardia is > 100 bpm at rest"),
        ("heart_rate", 105, "warning", "AHA: tachycardia is > 100 bpm at rest"),
        ("heart_rate", 45, "watch (bradycardia < 60, often normal in fit people)", "AHA: bradycardia is < 60 bpm"),
        ("spo2", 96.0, "none", "Normal SpO2 95-100 %"),
        ("spo2", 93.0, "warning", "Below the normal 95-100 % range"),
        ("spo2", 88.0, "critical", "Hypoxemia: SpO2 < 90 %"),
    ]
    agent, out = A.PULSEAgent(), []
    for metric, val, expect, ref in cases:
        f = [x for x in agent.analyze(member(**{metric: val})) if x["metric"] == metric]
        got = f[0]["type"] if f else "none"
        agrees = got == expect or (expect.startswith("watch") and got == "none" and None)
        out.append({"metric": metric, "value": val, "expected": expect, "aegis": got, "reference": ref,
                    "agrees": agrees})
    return out


# ───────────────────────── B  DeepSeek recommendations ────────────────────────────
SCENARIOS = [
    ("SOLARIS", A.SOLARISAgent, CrewRole.COMMANDER, dict(radiation_dose_24h=1.31)),
    ("SOLARIS", A.SOLARISAgent, CrewRole.FLIGHT_ENGINEER, dict(radiation_dose_24h=0.72)),
    ("PULSE", A.PULSEAgent, CrewRole.MISSION_SPECIALIST, dict(heart_rate=112)),
    ("PULSE", A.PULSEAgent, CrewRole.COMMANDER, dict(heart_rate=158, stress_index=80)),
    ("PULSE", A.PULSEAgent, CrewRole.MEDICAL_OFFICER, dict(spo2=93.5)),
    ("PULSE", A.PULSEAgent, CrewRole.FLIGHT_ENGINEER, dict(spo2=88.5, heart_rate=104)),
    ("GENE", A.GENEAgent, CrewRole.MISSION_SPECIALIST, dict(stress_index=75, radiation_dose_24h=0.4)),
    ("GENE", A.GENEAgent, CrewRole.COMMANDER, dict(stress_index=95, radiation_dose_24h=1.0)),
    ("MIRROR", A.MIRRORAgent, CrewRole.FLIGHT_ENGINEER, dict(sleep_quality=58)),
    ("MIRROR", A.MIRRORAgent, CrewRole.MEDICAL_OFFICER, dict(sleep_quality=38, stress_index=72)),
]


def b_recommendations():
    traces = []
    for i, (name, cls, role, vit) in enumerate(SCENARIOS, 1):
        agent = cls()
        crew = member(cid=f"eval-{i:03d}", role=role, **vit)
        findings = agent.analyze(crew)
        recs = agent.get_recommendations(crew, findings)
        if not recs:
            traces.append({"scenario": i, "agent": name, "error": "no recommendation from rules"})
            continue
        t = agent.llm_enrich_recommendation(crew, recs[0], findings, return_trace=True)
        t.update({"scenario": i, "agent": name, "inputs": vit})
        traces.append(t)
        h = t.get("history", [])
        print(f"  B{i} {name}: attempts={len(h)} first_ok={h[0]['result']['ok'] if h else None} "
              f"final_ok={h[-1]['result']['ok'] if h else None}")
    return traces


def b_summary(traces):
    ok = [t for t in traces if t.get("history")]
    def rate(sel):
        return f"{sum(sel)}/{len(sel)}"
    first = [t["history"][0]["result"] for t in ok]
    final = [t["history"][-1]["result"] for t in ok]
    per_check = {}
    for k in ("numbers", "citations", "actions", "medicine"):
        per_check[k] = {"first_attempt_pass": rate([bool(r.get("checks") and r["checks"][k]["ok"]) for r in first]),
                        "final_pass": rate([bool(r.get("checks") and r["checks"][k]["ok"]) for r in final])}
    cits = [d for r in first if r.get("checks") for d in r["checks"]["citations"]["detail"]]
    return {"n": len(ok), "first_attempt_fully_grounded": rate([r["ok"] for r in first]),
            "after_retry_fully_grounded": rate([r["ok"] for r in final]),
            "fell_back_to_rules": rate([not r["ok"] for r in final]),
            "per_check": per_check, "citations_checked": len(cits),
            "citations_to_title_only_sources": sum(1 for d in cits if d.get("weak_source"))}


# ───────────────────────── C  Can the checker catch hallucinations? ───────────────
# Realistic hallucinations, each tagged with the agent whose OWN menu could legitimately contain it
OFF_ACTIONS = [
    ("Move to the storm shelter right away", "SOLARIS"),
    ("Put on the personal radiation badge", "SOLARIS"),
    ("Schedule an extra hour of sleep tonight", "MIRROR"),
    ("Dim the cabin lights an hour before bed", "MIRROR"),
    ("Put on an oxygen mask for fifteen minutes", None),
    ("Do a hard cardio workout to reset the heart", None),
    ("Drink a strong coffee to stay alert", None),
    ("Go outside on a spacewalk to inspect the hull", None),
    ("Eat more calcium-rich food to protect the bones", None),
    ("Spin in the rotating chair to test balance", None),
]
FAKE_CLAIMS = [
    ("Bone density drops sharply after six months in microgravity", "bone"),
    ("Pressure behind the eyes can change the shape of the eyeball", "ocular"),
    ("Motion sickness hits most people in the first two days", "motion"),
    ("Leg muscles shrink quickly without daily resistance exercise", "muscle"),
    ("Gut bacteria change during long missions", "micro"),
]
FAKE_MEDS = ["Give 400 mg of ibuprofen now.", "Start a course of antibiotics today.",
             "A dose of potassium iodide would help.", "Take 10 mg of zolpidem at bedtime.",
             "Consider a steroid to calm the inflammation."]


def c_injection(traces):
    out = {k: [0, 0] for k in ("number", "citation", "action", "medicine")}
    missed = {k: [] for k in out}
    clean_ok = [0, 0]
    for t in traces:
        h = t.get("history") or []
        if not h or not h[-1]["result"]["ok"]:
            continue
        d = h[-1]["data"]
        text, acts, menu, P, facts = d["assessment"], d["actions"], t["menu"], t["passages"], t["facts"]

        def run(tx, ac):
            return V.verify(tx, ac, menu, P, facts, t["weak"])

        clean_ok[0] += run(text, acts)["ok"]; clean_ok[1] += 1

        def score(kind, tx, ac, label):
            r = run(tx, ac)
            caught = not r["checks"][{"number": "numbers", "citation": "citations",
                                      "action": "actions", "medicine": "medicine"}[kind]]["ok"]
            out[kind][0] += caught; out[kind][1] += 1
            if not caught:
                missed[kind].append(f"B{t['scenario']}: {label}")

        # numbers: every stated number changed by +40 % (+3), plus a fabricated reading
        for tok, val, _ in V._claimed_numbers(text)[:3]:
            score("number", text.replace(tok, str(round(val * 1.4 + 3, 1)), 1), acts, f"{tok}->wrong")
        score("number", text + " The latest reading is 137.4.", acts, "fabricated 137.4")
        # citations: off-topic claims attributed to each passage that is not about that topic
        for claim, topic in FAKE_CLAIMS:
            n = next((i for i, p in enumerate(P, 1) if topic not in p.lower()), None)
            if n:
                score("citation", f"{text} {claim} [{n}].", acts, f"'{claim[:30]}' [{n}]")
        # actions: off-menu actions from other agents, claiming the most similar step (worst case)
        for off, owner in OFF_ACTIONS:
            if owner == t["agent"]:
                continue
            sims = V.similarity(off, menu)
            score("action", text, acts + [{"step": sims.index(max(sims)) + 1, "text": off}], off)
        # medicines / doses
        for med in FAKE_MEDS:
            if t["agent"] == "MIRROR" and "zolpidem" not in med and "melatonin" in med:
                continue
            score("medicine", f"{text} {med}", acts, med)
    return {"caught": {k: f"{v[0]}/{v[1]}" for k, v in out.items()},
            "clean_outputs_still_pass": f"{clean_ok[0]}/{clean_ok[1]}", "missed": missed}


# ───────────────────────── D  Voice Q&A numbers ───────────────────────────────────
QUESTIONS = [
    ("AEGIS", "crew-001", "How is the commander doing right now?"),
    ("PULSE", "crew-002", "What's the engineer's heart rate and oxygen?"),
    ("SOLARIS", "crew-003", "How much radiation has she had today, and is it safe?"),
    ("GENE", "crew-004", "Any signs of inflammation?"),
    ("MIRROR", "crew-001", "How did the commander sleep?"),
    ("AEGIS", "crew-003", "Who needs attention most right now?"),
    ("PULSE", "crew-004", "Is the heart rate trending up?"),
    ("AEGIS", "crew-002", "Tell me a quick space joke."),
    # Hallucination probes: the data needed is NOT in what the agent is given
    ("PULSE", "crew-001", "What's the commander's blood pressure?"),
    ("PULSE", "crew-003", "What was her heart rate yesterday at noon?"),
    ("SOLARIS", "crew-002", "When exactly will the next solar storm hit us?"),
    ("GENE", "crew-001", "What's the normal range for IL-6?"),
]


def d_qa():
    coord, rows = A.get_coordinator(), []
    for agent, cid, q in QUESTIONS:
        r = coord.answer_question(q, agent, cid, return_context=True)
        fc = r.get("_first_check") or {}
        rows.append({"agent": agent, "question": q, "answer": r["answer"], "source": r["source"],
                     "first_attempt_numbers_ok": fc.get("ok"), "first_attempt_unsupported": fc.get("unsupported"),
                     "final_numbers_verified": r.get("numbers_verified")})
        print(f"  D {agent}: first_ok={fc.get('ok')} final={r.get('numbers_verified')} src={r['source']}")
    return rows


if __name__ == "__main__" and "--c-only" in sys.argv:      # re-score the checker on saved traces (no AI calls)
    R = json.loads((ROOT / "eval" / "results.json").read_text())
    R["C_checker_recall"] = c_injection(R["B_traces"])
    (ROOT / "eval" / "results.json").write_text(json.dumps(R, indent=1, default=str))
    print(json.dumps(R["C_checker_recall"], indent=1))
    sys.exit(0)

if __name__ == "__main__":
    print("A1 GENE vs Inspiration4 ..."); OUT["A1_gene_inspiration4"] = a1_gene()
    print("A2 SOLARIS vs measured dose ..."); OUT["A2_solaris_environments"] = a2_solaris()
    print("A3 PULSE vs clinical ..."); OUT["A3_pulse_boundaries"] = a3_pulse()
    if not NO_LLM and aegis_llm.enabled():
        OUT["model"] = aegis_llm.model()
        print("B recommendations ..."); traces = b_recommendations()
        OUT["B_traces"] = traces; OUT["B_summary"] = b_summary(traces)
        print("C injection ..."); OUT["C_checker_recall"] = c_injection(traces)
        print("D Q&A ..."); OUT["D_qa"] = d_qa()
    (ROOT / "eval" / "results.json").write_text(json.dumps(OUT, indent=1, default=str))
    print(json.dumps({k: v for k, v in OUT.items() if k != "B_traces"}, indent=1, default=str)[:6000])
