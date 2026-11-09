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
  4. MEDICINE   no medication, supplement or dose may appear unless it is in the menu or evidence
                (named drugs, drug-name endings like -pam/-olol/-mycin, and any dose such as "500 mg/day").
  5. PROSE      advice written in the assessment itself ("start X", "give Y") must also map onto the menu.

Numbers are bound to what they measure: "SpO2 72%" is only supported by an oxygen reading of 72 %, not by a
heart rate of 72 bpm. Spelled-out numbers ("seventy-two") are checked too.

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
    "ketamine", "insulin", "fentanyl", "oxycodone", "hydrocodone", "codeine", "tramadol", "naloxone", "narcan",
    "methadone", "heroin", "cocaine", "amphetamine", "methamphetamine", "adderall", "ritalin", "methylphenidate",
    "cannabis", "marijuana", "thc", "cbd", "kratom", "psilocybin", "lsd", "mdma", "ecstasy", "ghb", "xanax",
    "alprazolam", "clonazepam", "valium", "ambien", "trazodone", "quetiapine", "olanzapine", "haloperidol",
    "lithium", "sertraline", "fluoxetine", "citalopram", "escitalopram", "paroxetine", "venlafaxine", "bupropion",
    "mirtazapine", "amitriptyline", "ssri", "antidepressant", "antipsychotic", "sedative", "sleeping pill",
    "painkiller", "stimulant", "barbiturate", "warfarin", "heparin", "clopidogrel", "digoxin", "amiodarone",
    "lidocaine", "nitrous", "propofol", "midazolam", "glucagon", "metformin", "statin", "atorvastatin",
    "lisinopril", "losartan", "amlodipine", "insulin glargine", "iv fluids", "saline drip", "electrolyte tablet",
]
# Drug-name endings (generic names): -pam, -olol, -pril, -sartan, -statin, -mycin, -cillin, -oxacin, -azole...
DRUG_SUFFIX_RE = re.compile(r"\b[a-z]{3,}(?:azepam|zolam|olol|pril|sartan|statin|mycin|cillin|oxacin|cycline|"
                            r"conazole|prazole|tidine|dipine|triptan|barbital|codone|morphone|fentanil|caine|"
                            r"setron|lukast|mab|tinib|vir)\b", re.I)
_DOSE_UNITS = (r"(?:mg|mcg|µg|μg|ug|iu|ml|g|grams?|milligrams?|micrograms?|millilit(?:er|re)s?|units?|"
               r"tablets?|pills?|capsules?|drops?|puffs?|sprays?|injections?|shots?|doses?)")
DOSE_RE = re.compile(rf"\b\d+(?:\.\d+)?\s*{_DOSE_UNITS}\b(?:\s*(?:/|per|a|an|each|every)\s*"
                     r"(?:day|daily|night|hour|hr|kg|dose|time|meal))?", re.I)
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


def similarity_many(texts: list[str], b: str) -> list[float]:
    """Cosine similarity of each string in `texts` against `b`."""
    if not texts:
        return []
    from sentence_transformers import util
    m = _model()
    ea = m.encode(texts, convert_to_tensor=True, normalize_embeddings=True)
    eb = m.encode([b], convert_to_tensor=True, normalize_embeddings=True)
    return [float(x) for x in util.cos_sim(ea, eb)[:, 0]]


# Identifiers that contain digits but are not quantities: IL-6, SpO2, MEDIC-1, H2O, NASA/TM-2016...
IDENT_RE = re.compile(r"\b[A-Za-z]+[-/]?\d+[A-Za-z0-9]*\b")
# A quantity: 1,200 / 93.5 / 500 (units may follow directly: 500mL, 12.5pg)
QTY_RE = re.compile(r"(?<![\w.,\[])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![\d\]])")
HEDGE_RE = re.compile(r"(?:about|around|roughly|approximately|approx\.?|nearly|almost|close to|~)\s*$", re.I)


_ONES = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
         "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_NUMWORD_RE = re.compile(r"\b(?:(?:" + "|".join(list(_ONES) + list(_TENS)) + r")(?:[\s-]+(?:and[\s-]+)?"
                         r"(?:" + "|".join(list(_ONES) + list(_TENS) + ["hundred", "thousand"]) + r"))*"
                         r"|hundred|thousand)\b", re.I)


def _words_value(phrase: str) -> Optional[int]:
    total, cur = 0, 0
    for w in re.split(r"[\s-]+", phrase.lower()):
        if w == "and" or not w:
            continue
        if w in _ONES:
            cur += _ONES[w]
        elif w in _TENS:
            cur += _TENS[w]
        elif w == "hundred":
            cur = (cur or 1) * 100
        elif w == "thousand":
            total += (cur or 1) * 1000
            cur = 0
        else:
            return None
    return total + cur


def spell_to_digits(text: str) -> str:
    """'seventy-two percent' -> '72 percent' so spelled-out numbers can't slip past the number check."""
    def sub(m):
        v = _words_value(m.group(0))
        return str(v) if v is not None else m.group(0)
    return _NUMWORD_RE.sub(sub, text)


def _clean(text: str) -> str:
    return IDENT_RE.sub(" ", CITE_RE.sub(" ", spell_to_digits(text)))


def _numbers(text: str) -> list[float]:
    """Every quantity in a source text (pool the claims are checked against)."""
    return [float(m.group(1).replace(",", "")) for m in QTY_RE.finditer(_clean(text))]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text) if s.strip()]


_TIME_AFTER = re.compile(r"^\s*(?:-|\s)?(sec|secs|seconds?|min|mins|minutes?|hr|hrs|hours?|days?|weeks?|x|times)\b", re.I)
# Advice durations are not data claims ("rest for 2 hours"), but only at sensible sizes: "999 hours" is a claim
_TIME_MAX = {"sec": 300, "secs": 300, "second": 300, "seconds": 300, "min": 120, "mins": 120, "minute": 120,
             "minutes": 120, "hr": 72, "hrs": 72, "hour": 72, "hours": 72, "day": 14, "days": 14, "week": 8,
             "weeks": 8, "x": 20, "times": 20}

# What a number measures, from the unit after it or the metric named just before it
_TAG_UNIT = [("hr", r"bpm|beats"), ("spo2", r"%|percent"), ("rad", r"msv|millisieverts?"),
             ("il6", r"pg/ml|picograms?"), ("bp", r"mmhg"), ("temp", r"°c|degrees?")]
_TAG_WORD = [("hr", r"heart[\s-]*rate|pulse|\bhr\b"), ("spo2", r"spo2|oxygen|saturation|\bo2\b"),
             ("sleep", r"sleep"), ("il6", r"il-?6|interleukin|inflammation"), ("rad", r"radiation|dose|msv"),
             ("bp", r"blood pressure|systolic|diastolic"), ("temp", r"temperature"), ("stress", r"stress")]


def _tag(text: str, start: int, end: int) -> Optional[str]:
    after = text[end:end + 14].lower()
    for tag, rx in _TAG_UNIT:
        if re.match(rf"^\s*(?:{rx})", after):
            return "sleep" if tag == "spo2" and re.search(r"sleep", text[max(0, start - 30):start], re.I) else tag
    before = text[max(0, start - 30):start].lower()
    best, pos = None, -1
    for tag, rx in _TAG_WORD:
        for m in re.finditer(rx, before):
            if m.start() > pos:
                best, pos = tag, m.start()
    return best


def _claimed_numbers(text: str) -> list[tuple[str, float, bool, Optional[str]]]:
    """(token, value, hedged, tag). Advice durations of a sensible size and small counts are not data claims."""
    clean = _clean(text)
    out = []
    for m in QTY_RE.finditer(clean):
        val = float(m.group(1).replace(",", ""))
        t = _TIME_AFTER.match(clean[m.end():m.end() + 12])
        if t and val <= _TIME_MAX.get(t.group(1).lower(), 0):
            continue
        tag = _tag(clean, m.start(), m.end())
        # 1, 2, 3 as plain counts ("two steps", "3 systems") are not readings; with a unit or metric they are
        if val in (1, 2, 3) and tag is None and not re.match(r"^\s*(%|percent|msv|bpm|pg|mmhg|°)", clean[m.end():m.end() + 8], re.I):
            continue
        hedged = bool(HEDGE_RE.search(clean[max(0, m.start() - 16):m.start()]))
        out.append((m.group(1), val, hedged, tag))
    return out


def _tagged_pool(text: str) -> list[tuple[float, Optional[str]]]:
    clean = _clean(text)
    return [(float(m.group(1).replace(",", "")), _tag(clean, m.start(), m.end())) for m in QTY_RE.finditer(clean)]


def _matches(tok: str, val: float, pool: list[float], hedged: bool = False) -> bool:
    """A claimed number matches a source number if it is that number rounded as written (or within 1 %).
    'about / around N' may be a rounder approximation: within 5 %."""
    decimals = len(tok.split(".")[1]) if "." in tok else 0
    half_ulp = 0.5 * 10 ** (-decimals) + 1e-9
    rel = 0.05 if hedged else 0.01
    return any(abs(val - p) <= max(half_ulp, rel * abs(p)) for p in pool)


def check_numbers(text: str, sources: str) -> dict:
    """Every number claimed must appear in the sources, measuring the same thing when that's known
    (a tagged claim like "SpO2 72 %" only matches source numbers tagged oxygen, or untagged ones)."""
    tagged = _tagged_pool(sources)
    claimed = _claimed_numbers(text)
    bad = []
    for tok, v, h, tag in claimed:
        pool = [p for p, t in tagged if tag is None or t is None or t == tag]
        if not _matches(tok, v, pool, h):
            bad.append(tok + (f" ({tag})" if tag else ""))
    return {"claimed": len(claimed), "unsupported": bad, "ok": not bad}


_CLAUSE_SPLIT = re.compile(r"\s*(?:;|:|,\s*(?:and|but|while|whereas|so)\s+|\s+(?:and|but|while|whereas)\s+)\s*", re.I)
MIN_PASSAGE_BODY = 80        # a passage that is only a title can't support a claim


def _passage_body(p: str) -> str:
    return p.split(":", 1)[1].strip() if ":" in p[:200] else p.strip()


def check_citations(text: str, passages: list[str], weak: Optional[list[bool]] = None) -> dict:
    """Each sentence citing [n] must be similar enough to passage n, and so must each substantial clause of it
    (an invented clause can't ride along with a supported one). Title-only passages support nothing."""
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
            passage = passages[n - 1]
            if len(_passage_body(passage)) < MIN_PASSAGE_BODY:
                bad.append(f"[{n}] is only a title, it can't support: '{claim[:60]}'")
                results.append({"ref": n, "sim": None, "ok": False, "title_only": True})
                continue
            sim = similarity(claim, [passage])[0]
            clauses = [c for c in _CLAUSE_SPLIT.split(claim) if len(c.split()) >= 5]
            csims = similarity_many(clauses, passage) if len(clauses) > 1 else []
            weakest = min(csims) if csims else sim
            ok = sim >= CITE_MIN_SIM and weakest >= CITE_MIN_SIM * 0.8
            results.append({"ref": n, "sim": round(sim, 3), "weakest_clause": round(weakest, 3), "ok": ok,
                            "weak_source": bool(weak and weak[n - 1])})
            if not ok:
                bad.append(f"[{n}] does not support: '{claim[:80]}' (sim {sim:.2f}, weakest clause {weakest:.2f})")
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


def _norm_dose(s: str) -> str:
    return re.sub(r"\s+", "", s.lower())


def check_medicine(text: str, allowed_text: str) -> dict:
    """No medicine, drug or dose unless the approved menu / evidence contains exactly that."""
    text = spell_to_digits(text)
    low, allowed = text.lower(), spell_to_digits(allowed_text).lower()
    allowed_words = set(re.findall(r"[a-z][a-z-]+", allowed))
    bad = [d for d in DRUGS if re.search(rf"\b{re.escape(d)}(?:s|es)?\b", low)
           and not re.search(rf"\b{re.escape(d)}", allowed)]
    bad += [m.group(0) for m in DRUG_SUFFIX_RE.finditer(text) if m.group(0).lower() not in allowed_words
            and m.group(0).lower() not in _NOT_DRUGS]
    allowed_doses = {_norm_dose(m.group(0)) for m in DOSE_RE.finditer(allowed)}
    bad += [m.group(0) for m in DOSE_RE.finditer(text) if _norm_dose(m.group(0)) not in allowed_doses]
    return {"unsupported": list(dict.fromkeys(bad)), "ok": not bad}


# ordinary words that happen to share a drug ending
_NOT_DRUGS = {"because", "environment", "dilemma", "review", "preview", "interview",
              "domain", "remain", "germab", "tab", "slab"}


# Units a spoken answer may attach to a number: those of the dashboard metrics (mSv, pg/mL, bpm, %, /100,
# mmHg, °C) plus time and counts. Any other measurement unit (doses, Gy/Sv/rem, lab units) is rejected.
ALLOWED_UNITS = {"msv", "millisievert", "millisieverts", "pg/ml", "bpm", "%", "mmhg"}
UNIT_RE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?\s*(%|[a-zA-Zµμ][a-zA-Z/µμ]*)\b", re.I)
_UNIT_LIKE = {"mg", "mcg", "µg", "μg", "ug", "g", "kg", "ml", "l", "cc", "iu", "units", "unit", "tablets", "tablet",
              "pills", "pill", "capsules", "doses", "gy", "mgy", "cgy", "sv", "usv", "µsv", "μsv", "rem", "mrem",
              "rad", "rads", "mmol", "mmol/l", "mg/dl", "mg/l", "ng/ml", "µg/ml", "nmol/l", "u/l", "meq", "mbq",
              "bq", "ppm", "kpa", "mph", "km", "m/s", "w", "kcal", "cal", "psi"}


# Spelled-out or SI-style measurement units (anything that measures, not counts). A unit-like word after a
# number must be on the spec whitelist; ordinary words ("3 crew members", "2 steps") are not units.
_UNIT_WORD_RE = re.compile(r"^(?:(?:milli|micro|nano|kilo|centi)?(?:grams?|lit(?:er|re)s?|sieverts?|grays?|rems?|rads?|"
                           r"moles?|becquerels?|curies?|joules?|watts?|volts?|amps?|hertz|calories?|meters?|metres?)|"
                           r"pounds?|ounces?|lbs?|oz|doses?|shots?|units?|tablets?|pills?|capsules?|drops?|puffs?)$", re.I)
_SI_RE = re.compile(r"^(?:[kmµμunpc]?(?:g|l|sv|gy|mol|bq|j|w|v|a|hz|cal|iu|rad|rem|m)(?:/[a-z]+)?)$", re.I)
SPEC_UNITS = ALLOWED_UNITS | {"percent", "millisievert", "millisieverts", "mmhg", "°c", "/100",
                              "sec", "secs", "second", "seconds", "min", "mins", "minute", "minutes", "hr", "hrs",
                              "hour", "hours", "day", "days", "week", "weeks", "times", "x"}


def check_units(text: str) -> dict:
    """Numbers in a spoken answer may only carry units from the spec whitelist (mSv, pg/mL, bpm, %, mmHg,
    time, counts). Any other measurement unit, abbreviated or spelled out ("5 grays", "9 sieverts", "20 mg"), fails."""
    bad = []
    for m in UNIT_RE.finditer(_clean(text)):
        u = m.group(1).lower()
        if u in SPEC_UNITS:
            continue
        if u in _UNIT_LIKE or _UNIT_WORD_RE.match(u) or _SI_RE.match(u):
            bad.append(m.group(0))
    return {"unsupported": bad, "ok": not bad}


# Advice in the assessment prose: an instruction must map onto an approved menu step (or be monitoring only)
_IMPERATIVE_RE = re.compile(r"^(?:please\s+)?(?:start|begin|take|give|administer|increase|decrease|reduce|stop|"
                            r"move|shelter|evacuate|apply|use|inject|drink|eat|sleep|rest|exercise|avoid|switch|"
                            r"put|place|send|call|schedule|perform|run|do|try|add|cut|limit|boost|keep)\b", re.I)
_ADVICE_RE = re.compile(r"\b(?:should|must|needs? to|recommend(?:ed|s)?|advise[sd]?|consider|have (?:him|her|them|the \w+) )\b", re.I)
_MONITOR_RE = re.compile(r"\b(?:watch|monitor|keep an eye|track|check|re-?check|look out|confirm|flight surgeon|"
                         r"mission control will|decide)\b", re.I)
PROSE_MIN_SIM = 0.35


def check_prose_actions(text: str, menu: list[str]) -> dict:
    """Sentences that give an instruction must be a rewording of an approved step (monitoring advice is fine)."""
    bad, detail = [], []
    for sent in _sentences(CITE_RE.sub("", text)):
        core = sent.strip().rstrip(".!")
        if not (_IMPERATIVE_RE.match(core) or _ADVICE_RE.search(core)):
            continue
        if _MONITOR_RE.search(core) and not _IMPERATIVE_RE.match(core):
            continue
        sim = max(similarity(core, menu)) if menu else 0.0
        ok = sim >= PROSE_MIN_SIM or bool(_MONITOR_RE.search(core) and len(core.split()) <= 14)
        detail.append({"sentence": core[:120], "sim": round(sim, 3), "ok": ok})
        if not ok:
            bad.append(f"advice not in the approved steps: '{core[:80]}' (best {sim:.2f})")
    return {"instructions": len(detail), "unsupported": bad, "detail": detail, "ok": not bad}


# NASA's 250 figure is a 30-day limit (250 mGy-Eq to the blood-forming organs, NASA-STD-3001), not a per-storm or
# per-event dose. Any wording that ties 250 to a single event/storm/SPE is rejected, even though the number appears
# in the sources (so the numbers check alone can't catch it).
LIMIT_FRAMING_RE = re.compile(
    r"\b250\s*(?:m?sv|mgy(?:-eq)?|millisieverts?|milligrays?)?\s*(?:\(?\s*)?"
    r"(?:(?:per|each|every|a|for\s+(?:a|one|each))\s+(?:solar\s+)?(?:particle\s+)?(?:event|storm|spe|flare)s?"
    r"|(?:solar\s+)?(?:particle\s+)?(?:event|storm|spe|flare|per-event|per-storm)\s+(?:dose\s+)?(?:limit|cap|ceiling|threshold))"
    r"|\b(?:event|storm|spe|per-event|per-storm)\s+(?:dose\s+)?(?:limit|cap|ceiling)\s+(?:of\s+)?250\b",
    re.I)


def check_limit_framing(text: str) -> dict:
    bad = [m.group(0) for m in LIMIT_FRAMING_RE.finditer(text or "")]
    return {"unsupported": [f"misframed NASA limit: '{b}' (250 is a 30-day limit, mGy-Eq to the blood-forming organs)"
                            for b in bad], "ok": not bad}


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
        "prose_actions": check_prose_actions(text, menu),
        "limits": check_limit_framing(text + " " + act_text),
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
            "medicine_ok": c["medicine"]["ok"], "prose_ok": c.get("prose_actions", {}).get("ok", True),
            "problems": result["problems"][:4]}



def verify_note(text: str, context: str, menu: Optional[list[str]] = None) -> dict:
    """Free-text notes (alert assessment, cascade explanation, PREDICT note): numbers bound to the readings,
    no medicine or dose beyond the menu, only spec units, and any instruction must be an approved step."""
    menu = menu or []
    checks = {
        "numbers": check_numbers(text, "\n".join([context, *menu])),
        "medicine": check_medicine(text, "\n".join(menu)),
        "units": check_units(text),
        "prose_actions": check_prose_actions(text, menu),
        "limits": check_limit_framing(text),
    }
    problems = [p for c in checks.values() for p in c["unsupported"]]
    return {"ok": not problems, "problems": problems, "checks": checks}
