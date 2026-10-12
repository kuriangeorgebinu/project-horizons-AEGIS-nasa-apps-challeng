"""
AEGIS grounding checker: catches hallucinations in DeepSeek output before it reaches the dashboard.

It never asks another LLM whether the text "looks right". Every check is mechanical, against what the
model was actually given:

  1. NUMBERS    every number in the text must appear in the crew readings, the thresholds or the
                evidence passages (within a small tolerance). An invented "SpO2 of 88%" fails.
  2. CITATIONS  every sentence that cites [n] must be semantically supported by passage n
                (MiniLM cosine similarity, the same model the RAG index uses). A citation pointing
                at an unrelated paper fails. Citations to a passage that does not exist fail.
  3. ACTIONS    every action must map onto an item of the rule-approved action menu (similarity).
                An action the rules never approved fails.
  4. MEDICINE   no medication, supplement or dose may appear unless it is in the menu or evidence.

verify(...) returns {"ok": bool, "score": 0..1, "problems": [...], "checks": {...}}.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Optional

CITE_MIN_SIM = 0.30      # sentence <-> cited passage (MiniLM cosine); unrelated pairs sit ~0.0-0.2
ACTION_MIN_SIM = 0.40    # rewritten action <-> the menu item it claims to rewrite (calibrated: eval/calibrate_actions.py)
NUM_REL_TOL = 0.05       # 5 % relative tolerance for numbers
NUM_ABS_TOL = 0.15

# Medications / supplements an LLM tends to volunteer. Flagged only if absent from menu + evidence.
DRUGS = [
    "ibuprofen", "acetaminophen", "paracetamol", "aspirin", "naproxen", "potassium iodide", "iodide",
    "epinephrine", "adrenaline", "atropine", "ondansetron", "promethazine", "scopolamine", "meclizine",
    "melatonin", "zolpidem", "zaleplon", "modafinil", "dexamethasone", "prednisone", "hydrocortisone",
    "steroid", "antibiotic", "amoxicillin", "ciprofloxacin", "filgrastim", "g-csf", "amifostine",
    "beta blocker", "metoprolol", "propranolol", "diuretic", "furosemide", "nitroglycerin", "oxygen mask",
    "antihistamine", "diphenhydramine", "benzodiazepine", "lorazepam", "diazepam", "opioid", "morphine",
    "vitamin d", "vitamin c", "vitamin e", "omega-3", "fish oil", "antioxidant supplement", "caffeine pill",
]
DOSE_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|µg|ug|iu|ml|mL|g)\b(?!/)", re.I)
NUM_RE = re.compile(r"(?<![\w\[])[-+]?\d+(?:[.,]\d+)?(?![\w\]])")
CITE_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer("all-MiniLM-L6-v2")


def similarity(a: str, b_list: list[str]) -> list[float]:
    """Cosine similarity of `a` against each string in `b_list`."""
    if not b_list:
        return []
    from sentence_transformers import util
    m = _model()
    ea = m.encode([a], convert_to_tensor=True, normalize_embeddings=True)
    eb = m.encode(b_list, convert_to_tensor=True, normalize_embeddings=True)
    return [float(x) for x in util.cos_sim(ea, eb)[0]]


# Identifiers that contain digits but are not quantities: IL-6, SpO2, MEDIC-1, H2O, NASA/TM-2016...
IDENT_RE = re.compile(r"\b[A-Za-z]+[-/]?\d+[A-Za-z0-9]*\b")
# A quantity: 1,200 / 93.5 / 500 (units may follow directly: 500mL, 12.5pg)
QTY_RE = re.compile(r"(?<![\w.,\[])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![\d\]])")
HEDGE_RE = re.compile(r"(?:about|around|roughly|approximately|approx\.?|nearly|almost|close to|~)\s*$", re.I)


def _clean(text: str) -> str:
    return IDENT_RE.sub(" ", CITE_RE.sub(" ", text))


def _numbers(text: str) -> list[float]:
    """Every quantity in a source text (pool the claims are checked against)."""
    return [float(m.group(1).replace(",", "")) for m in QTY_RE.finditer(_clean(text))]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text) if s.strip()]


_TIME_AFTER = re.compile(r"^\s*(?:-|\s)?(?:min|minute|minutes|hr|hrs|hour|hours|day|days|week|weeks|sec|seconds|x|times)\b", re.I)


def _claimed_numbers(text: str) -> list[tuple[str, float, bool]]:
    """(token, value, hedged). Durations in advice ('rest for 2 hours') are not data claims."""
    clean = _clean(text)
    out = []
    for m in QTY_RE.finditer(clean):
        if _TIME_AFTER.match(clean[m.end():m.end() + 10]):
            continue
        hedged = bool(HEDGE_RE.search(clean[max(0, m.start() - 16):m.start()]))
        out.append((m.group(1), float(m.group(1).replace(",", "")), hedged))
    return out


def _matches(tok: str, val: float, pool: list[float], hedged: bool = False) -> bool:
    """A claimed number matches a source number if it is that number rounded as written (or within 1 %).
    'about / around N' may be a rounder approximation: within 5 %."""
    decimals = len(tok.split(".")[1]) if "." in tok else 0
    half_ulp = 0.5 * 10 ** (-decimals) + 1e-9
    rel = 0.05 if hedged else 0.01
    return any(abs(val - p) <= max(half_ulp, rel * abs(p)) for p in pool)


def check_numbers(text: str, sources: str) -> dict:
    pool = _numbers(sources)
    claimed = _claimed_numbers(text)
    bad = [tok for tok, v, h in claimed if not (abs(v) <= 3 and v == int(v)) and not _matches(tok, v, pool, h)]
    return {"claimed": len(claimed), "unsupported": bad, "ok": not bad}


def check_citations(text: str, passages: list[str], weak: Optional[list[bool]] = None) -> dict:
    """Each sentence citing [n] must be similar enough to passage n."""
    results, bad = [], []
    for sent in _sentences(text):
        refs = []
        for grp in CITE_RE.findall(sent):
            refs += [int(x) for x in re.split(r"\s*,\s*", grp)]
        if not refs:
            continue
        claim = CITE_RE.sub("", sent).strip()
        for n in refs:
            if not 1 <= n <= len(passages):
                bad.append(f"[{n}] does not exist")
                results.append({"ref": n, "sim": None, "ok": False})
                continue
            sim = similarity(claim, [passages[n - 1]])[0]
            ok = sim >= CITE_MIN_SIM
            results.append({"ref": n, "sim": round(sim, 3), "ok": ok,
                            "weak_source": bool(weak and weak[n - 1])})
            if not ok:
                bad.append(f"[{n}] does not support: '{claim[:80]}' (sim {sim:.2f})")
    return {"citations": len(results), "unsupported": bad, "detail": results, "ok": not bad}


_ADDRESSEE = re.compile(r"^(?:please\s+)?(?:have|ask|get|let|help|make sure|ensure)\s+(?:the\s+)?"
                        r"(?:commander|pilot|flight engineer|engineer|mission specialist|medical officer|"
                        r"science officer|payload specialist|crew member|crew|him|her|them)\s+(?:to\s+)?", re.I)


def _action_text(a) -> tuple[str, Optional[int]]:
    """Actions are {'step': n, 'text': '...'} (n = menu item the model says it rewrote) or plain strings."""
    if isinstance(a, dict):
        try:
            step = int(a.get("step"))
        except (TypeError, ValueError):
            step = None
        return str(a.get("text", "")).strip(), step
    return str(a).strip(), None


def check_actions(actions: list, menu: list[str], verbatim: bool = False) -> dict:
    """Each action must be a rewrite of an approved menu item. With a declared step, the text is compared to
    THAT item (a model can't claim step 2 and write something else); otherwise to the best-matching item.
    verbatim=True (production): the dashboard shows the APPROVED step text, never the model's rewording, so an
    action only needs a valid step number; the similarity is still recorded for the audit trail."""
    bad, detail = [], []
    for a in actions:
        text, step = _action_text(a)
        core = _ADDRESSEE.sub("", text)
        sims = similarity(core, menu) if menu else []
        if step is not None and 1 <= step <= len(menu):
            sim, mapped = sims[step - 1], menu[step - 1]
        elif step is not None:
            sim, mapped = 0.0, None
        else:
            sim = max(sims) if sims else 0.0
            mapped = menu[sims.index(sim)] if sims else None
        ok = (mapped is not None and step is not None) if verbatim else sim >= ACTION_MIN_SIM
        detail.append({"action": text, "step": step, "menu_item": mapped, "sim": round(sim, 3), "ok": ok})
        if not ok:
            bad.append((f"no valid approved step number for: '{text[:80]}'" if verbatim else
                        f"not a rewrite of an approved step: '{text[:80]}'")
                       + (f" (claimed step {step}, sim {sim:.2f})" if step is not None else f" (best {sim:.2f})"))
    return {"actions": len(actions), "unsupported": bad, "detail": detail, "ok": not bad}


def check_medicine(text: str, allowed_text: str) -> dict:
    low, allowed = text.lower(), allowed_text.lower()
    allowed_nospace = re.sub(r"\s+", "", allowed)
    bad = [d for d in DRUGS if re.search(rf"\b{re.escape(d)}(?:s|es)?\b", low) and d not in allowed]
    bad += [m.group(0) for m in DOSE_RE.finditer(text)
            if re.sub(r"\s+", "", m.group(0).lower()) not in allowed_nospace]
    return {"unsupported": bad, "ok": not bad}


# Units a spoken answer may attach to a number: those of the dashboard metrics (mSv, pg/mL, bpm, %, /100,
# mmHg, °C) plus time and counts. Any other measurement unit (doses, Gy/Sv/rem, lab units) is rejected.
ALLOWED_UNITS = {"msv", "millisievert", "millisieverts", "pg/ml", "bpm", "%", "mmhg"}
UNIT_RE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?\s*(%|[a-zA-Zµμ][a-zA-Z/µμ]*)\b", re.I)
_UNIT_LIKE = {"mg", "mcg", "µg", "μg", "ug", "g", "kg", "ml", "l", "cc", "iu", "units", "unit", "tablets", "tablet",
              "pills", "pill", "capsules", "doses", "gy", "mgy", "cgy", "sv", "usv", "µsv", "μsv", "rem", "mrem",
              "rad", "rads", "mmol", "mmol/l", "mg/dl", "mg/l", "ng/ml", "µg/ml", "nmol/l", "u/l", "meq", "mbq",
              "bq", "ppm", "kpa", "mph", "km", "m/s", "w", "kcal", "cal", "psi"}


def check_units(text: str) -> dict:
    """Numbers in a spoken answer may only carry whitelisted units (no invented doses, Gy/Sv, lab units...)."""
    bad = [m.group(0) for m in UNIT_RE.finditer(_clean(text))
           if m.group(1).lower() in _UNIT_LIKE and m.group(1).lower() not in ALLOWED_UNITS]
    return {"unsupported": bad, "ok": not bad}


def verify(text: str, actions: list[str], menu: list[str], passages: list[str], context: str,
           weak: Optional[list[bool]] = None, verbatim_actions: bool = False) -> dict:
    """Run all four checks. `context` = everything numeric the model was given (readings, thresholds)."""
    sources = "\n".join([context, *menu, *passages])
    act_text = " ".join(_action_text(a)[0] for a in actions)
    checks = {
        "numbers": check_numbers(text + " " + act_text, sources),
        "citations": check_citations(text, passages, weak),
        "actions": check_actions(actions, menu, verbatim_actions),
        "medicine": check_medicine(text + " " + act_text, "\n".join([*menu, *passages])),
    }
    problems = [p for c in checks.values() for p in c["unsupported"]]
    passed = sum(1 for c in checks.values() if c["ok"])
    return {"ok": not problems, "score": round(passed / len(checks), 2), "problems": problems, "checks": checks}


def summary(result: dict) -> dict:
    """Compact form stored with a recommendation."""
    c = result["checks"]
    return {"ok": result["ok"], "numbers": f"{c['numbers']['claimed'] - len(c['numbers']['unsupported'])}/{c['numbers']['claimed']}",
            "citations": f"{c['citations']['citations'] - len(c['citations']['unsupported'])}/{c['citations']['citations']}",
            "actions": f"{c['actions']['actions'] - len(c['actions']['unsupported'])}/{c['actions']['actions']}",
            "medicine_ok": c["medicine"]["ok"], "problems": result["problems"][:4]}
