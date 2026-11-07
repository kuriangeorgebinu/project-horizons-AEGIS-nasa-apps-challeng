"""
ECHO: the crew companion against isolation (sits beside MIRROR).
NASA Space Apps Challenge 2026

MIRROR detects, ECHO responds as a presence. Three modes:
  companion : a chat window the crew member can talk to, warm and short
  checkin   : "How are you feeling?" plus a daily greeting built from the person's real readings
  support   : the crisis mode. ECHO checks in on its own after an emergency, when MIRROR flags poor sleep,
              after a low check-in, or when the chat shows distress, offers coping steps and the escalation path

Design rules
- Rules decide, the AI only words. Mode changes and crisis detection are rule-based (regex + readings).
  DeepSeek may word ordinary companion replies; support and urgent replies are always scripted.
- Private by default. Chats and check-ins stay in memory for this session, are never written to the event
  log and are never sent to mission control. ECHO asks before sharing anything. The one exception, stated
  up front in the panel: if someone says they may harm themselves, ECHO alerts the medical officer.
- Escalation follows the light delay: the medical officer on board first (the Commander when the medical
  officer is the one struggling), then the flight surgeon at mission control, minutes away.
- ECHO always says it is an AI. It is not a counsellor and gives no medical or psychiatric advice.
- Crew names are never sent to the model (role only), the same rule as the rest of AEGIS.
- ECHO does not count as a cascade system: mood belongs to MIRROR's behavioural-health domain.
"""
import random
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

import aegis_llm
import aegis_mission
import database as db

_lock = threading.Lock()
_STATE: dict = {}                 # crew_id -> state dict (memory only: private, cleared on demo reset)
_rng = random.Random(54)

MOODS = {1: "rough", 2: "low", 3: "okay", 4: "good", 5: "great"}
SUPPORT_MINUTES = 30              # how long ECHO stays in support mode after a trigger
AFTER_EMERGENCY_MINUTES = 20      # an emergency this recent makes ECHO check in afterwards

# Rule-based crisis detection. ACUTE = possible risk of self-harm -> urgent path. DISTRESS = support mode.
ACUTE = re.compile(
    r"\b(kill(ing)? myself|suicid\w*|end (it all|my life|things)|want(ed)? to die|wish i (was|were) dead|"
    r"don'?t want to (live|be here|wake up)|hurt(ing)? myself|self[- ]?harm\w*|no (reason|point) (to|in) (live|living|going on))\b",
    re.I)
DISTRESS = re.compile(
    r"\b(panic\w*|can'?t cope|cannot cope|overwhelm\w*|hopeless|helpless|terrified|scared|afraid|anxious|anxiety|"
    r"alone|lonely|loneliness|isolated|can'?t sleep|cannot sleep|insomnia|"
    r"exhausted|burn(ed|t)[- ]?out|breaking down|falling apart|depress\w*|crying|cried|numb|worthless|"
    r"stressed out|so tired|not okay|not ok|struggling)\b", re.I)


def _now() -> float:
    return time.time()


def _st(crew_id: str) -> dict:
    with _lock:
        if crew_id not in _STATE:
            _STATE[crew_id] = {"checkins": [], "chat": [], "support": None, "dismissed_until": 0.0,
                               "shared": [], "greeted": None}
        return _STATE[crew_id]


def reset() -> None:
    """Demo reset: forget every check-in and chat (they only ever lived in memory)."""
    with _lock:
        _STATE.clear()


# ── Who is who ────────────────────────────────────────────────────────────────────────────────────────────────

def _member(crew_id: str):
    return db.get_crew_member(crew_id)


def _address(m) -> str:
    """How ECHO speaks to someone: 'Commander' for the commander, 'Dr. Surname' for the others."""
    if m is None:
        return "there"
    if m.role.value == "commander":
        return "Commander"
    return f"Dr. {m.name.split()[-1]}"


def _display(m) -> str:
    if m is None:
        return "the crew member"
    return f"Commander {m.name.split()[-1]}" if m.role.value == "commander" else f"Dr. {m.name.split()[-1]}"


def _role_words(m) -> str:
    return "the " + (m.role.value.replace("_", " ") if m else "crew member")


def escalation(crew_id: str) -> dict:
    """First person to talk to on board (medical officer; the Commander if it's the medical officer), then Earth."""
    crew = db.get_all_crew()
    medic = next((c for c in crew if c.role.value == "medical_officer"), None)
    cmdr = next((c for c in crew if c.role.value == "commander"), None)
    first = cmdr if (medic is None or medic.id == crew_id) else medic
    return {
        "onboard": {"crew_id": first.id if first else None, "name": _display(first),
                    "role": (first.role.value.replace("_", " ") if first else "crew")},
        "ground": {"name": "the flight surgeon at mission control",
                   "note": "a private note, delivered after the signal delay; replies take as long again"},
    }


# ── Readings ECHO can talk about ──────────────────────────────────────────────────────────────────────────────

def _facts(m) -> dict:
    v = m.vitals if m else None
    sleep = round(v.sleep_quality) if v and v.sleep_quality is not None else None
    dose = v.radiation_dose_24h if v else None
    return {"sleep": sleep, "dose_24h": round(dose, 2) if dose is not None else None,
            "heart_rate": v.heart_rate if v else None, "spo2": round(v.spo2) if v else None,
            "mission_day": m.mission_day if m else 54,
            "mission_days": aegis_mission.profile().get("phase_days", 180)}


def _sleep_line(sleep: Optional[int], addr: str) -> str:
    if sleep is None:
        return f"Good to see you, {addr}."
    if sleep >= 80:
        return f"You slept well last night, {addr}: {sleep} out of 100 on MIRROR's sleep score."
    if sleep >= 70:
        return f"A decent night's sleep, {addr}: {sleep} out of 100. Not your best, not bad either."
    return f"MIRROR tells me last night was a rough one, {addr}: {sleep} out of 100. Go easy on yourself today."


def _recent_emergency(crew_id: str) -> Optional[dict]:
    """A critical alert for this person (or a crew-wide storm shelter) in the last AFTER_EMERGENCY_MINUTES."""
    cutoff = datetime.utcnow() - timedelta(minutes=AFTER_EMERGENCY_MINUTES)
    for a in db.get_alerts(unacknowledged_only=False, limit=80):
        title = (a.title or "").upper()
        shelter = "SHELTER" in title or "RADIATION" in title and a.severity.value == "critical"
        mine = a.crew_member_id in (crew_id, None) or shelter
        if not mine or a.timestamp < cutoff:
            continue
        if a.severity.value == "critical" or shelter or "CASCADE" in title:
            kind = "shelter" if shelter else "medical" if a.severity.value == "critical" else "cascade"
            return {"kind": kind, "at": a.timestamp.isoformat(), "open": not a.acknowledged}
    return None


# ── Modes ─────────────────────────────────────────────────────────────────────────────────────────────────────

def _set_support(crew_id: str, reason: str, level: str = "support") -> None:
    s = _st(crew_id)
    with _lock:
        s["support"] = {"reason": reason, "level": level, "since": _now()}
        s["dismissed_until"] = 0.0          # a new reason to check in overrides an earlier "I'm okay"


def _support_active(crew_id: str) -> Optional[dict]:
    s = _st(crew_id)
    sup = s.get("support")
    if sup and _now() - sup["since"] < SUPPORT_MINUTES * 60:
        return sup
    return None


def status(crew_id: str, event: Optional[str] = None) -> dict:
    """
    Current mode and the line ECHO opens with. `event` lets the dashboard tell ECHO that an emergency it
    was watching has just been resolved (e.g. 'shelter'), so the check-in happens right then.
    """
    m = _member(crew_id)
    if m is None:
        return {"error": "unknown crew member"}
    s = _st(crew_id)
    f = _facts(m)
    addr = _address(m)
    esc = escalation(crew_id)
    # ECHO checks in after an emergency only when the dashboard reports that one has just ended (event=...).
    # It doesn't infer it from old alerts, so a new visitor never lands in someone else's leftover support mode.
    emergency = None
    if event in ("shelter", "medical", "cascade"):
        emergency = {"kind": event, "at": datetime.utcnow().isoformat(), "open": False}
    dismissed = s["dismissed_until"] > _now()
    sup = _support_active(crew_id)
    reason, text = None, None

    if emergency and not emergency["open"] and not dismissed:
        reason = "after_emergency"
        what = {"shelter": "a long few hours in the storm shelter",
                "medical": "a frightening medical scare",
                "cascade": "a hard stretch, with several of your readings going wrong at once"}[emergency["kind"]]
        text = (f"{addr}, that was {what}. AEGIS says your readings are back in a safe range. "
                f"But I'm not here for the readings. How are you feeling?")
        _set_support(crew_id, reason)
    elif sup and sup["level"] == "urgent":
        reason = "urgent"
        text = (f"I'm still here with you, {addr}. {esc['onboard']['name']} knows you need someone right now.")
    elif f["sleep"] is not None and f["sleep"] < 70 and not dismissed:
        reason = "poor_sleep"
        text = (f"{_sleep_line(f['sleep'], addr)} Nights like that make everything feel heavier. "
                f"Want to check in for a minute?")
        _set_support(crew_id, reason)
    elif sup and not dismissed:
        reason = sup["reason"]
        text = f"Checking back in, {addr}. How are you doing now?"
    else:
        last = s["checkins"][-1] if s["checkins"] else None
        checked_today = last and (_now() - last["at"]) < 12 * 3600
        day = f["mission_day"]
        text = (f"{_sleep_line(f['sleep'], addr)} Day {day} of {f['mission_days']}. "
                + ("Thanks for checking in earlier. I'm around if you want to talk."
                   if checked_today else "How are you feeling today?"))

    mode = "support" if reason in ("after_emergency", "poor_sleep", "low_mood", "distress", "urgent") else \
           ("checkin" if not s["checkins"] or (_now() - s["checkins"][-1]["at"]) > 12 * 3600 else "companion")
    if mode == "support" and reason == "urgent":
        level = "urgent"
    else:
        level = "support" if mode == "support" else "calm"
    return {"crew_id": crew_id, "name": _display(m), "address": addr, "mode": mode, "level": level,
            "reason": reason, "text": text, "facts": f, "escalation": esc,
            "coping": COPING if mode == "support" else [],
            "privacy": PRIVACY}


PRIVACY = ("Private: what you tell ECHO stays here and is never sent to mission control unless you choose to "
           "share it. One exception: if you say you might harm yourself, ECHO alerts the medical officer.")

COPING = [
    "Slow breathing: in for 4, hold for 4, out for 4, hold for 4. Five rounds.",
    "Name 5 things you can see, 4 you can hear, 3 you can touch. It brings you back to the room.",
    "Find a crewmate for ten minutes: a meal, a game, or just company.",
    "Record a message home. It will take a few minutes to reach Earth, and that's fine.",
]


# ── Check-in ─────────────────────────────────────────────────────────────────────────────────────────────────

def checkin(crew_id: str, mood: int, note: str = "") -> dict:
    m = _member(crew_id)
    if m is None:
        return {"error": "unknown crew member"}
    mood = max(1, min(5, int(mood)))
    s = _st(crew_id)
    with _lock:
        s["checkins"].append({"at": _now(), "mood": mood})
        del s["checkins"][:-30]
    addr = _address(m)
    f = _facts(m)
    esc = escalation(crew_id)
    after = (s.get("support") or {}).get("reason") == "after_emergency" and _support_active(crew_id)
    if note and ACUTE.search(note):
        return _urgent(crew_id, m)
    if mood <= 2:
        _set_support(crew_id, "low_mood")
        text = (f"Thank you for telling me, {addr}. Feeling {MOODS[mood]} out here makes sense"
                + (" after what you've just been through." if after else ", a long way from home.")
                + f" You don't have to fix it alone. Want to try one of these with me, or should I let "
                  f"{esc['onboard']['name']} know you'd like to talk? Only if you say yes.")
        mode, level = "support", "support"
    elif mood == 3:
        text = (f"Okay is allowed, {addr}"
                + (". That was a lot. Take the next hour slowly if the schedule lets you."
                   if after else ". Anything on your mind? I'm listening.")
                + " Want to record a short message home while it's quiet?")
        mode, level = ("support", "support") if after else ("companion", "calm")
    else:
        s["dismissed_until"] = _now() + SUPPORT_MINUTES * 60 if not after else _now() + 10 * 60
        text = (f"Glad to hear it, {addr}. "
                + ("You handled that well. The crew did too." if after else
                   (f"A good night's sleep helps: {f['sleep']} out of 100." if (f['sleep'] or 0) >= 80 else
                    "Hold on to that."))
                + " I'll be here.")
        mode, level = "companion", "calm"
    _remember(crew_id, "echo", text)
    return {"crew_id": crew_id, "mood": mood, "mood_word": MOODS[mood], "mode": mode, "level": level,
            "text": text, "coping": COPING if mode == "support" else [], "escalation": esc, "source": "rules"}


# ── Chat ──────────────────────────────────────────────────────────────────────────────────────────────────────

def _remember(crew_id: str, who: str, text: str) -> None:
    s = _st(crew_id)
    with _lock:
        s["chat"].append({"who": who, "text": text, "at": _now()})
        del s["chat"][:-20]


def _urgent(crew_id: str, m) -> dict:
    """Possible risk of self-harm: always scripted, the medical officer is alerted (the one privacy exception)."""
    esc = escalation(crew_id)
    addr = _address(m)
    _set_support(crew_id, "urgent", "urgent")
    s = _st(crew_id)
    with _lock:
        s["shared"].append({"at": _now(), "to": "onboard", "what": "urgent: asked for in-person support"})
    db_note(crew_id, f"ECHO alerted {esc['onboard']['name']}: {_display(m)} needs someone with them now "
                     f"(urgent support; chat contents not shared)")
    text = (f"{addr}, I'm really glad you told me. You matter, and you don't have to carry this alone. "
            f"I've let {esc['onboard']['name']} know you need someone with you right now. Nothing else from our "
            f"chat was shared. Until they get here, please stay with me, or go to where the crew is. "
            f"If you're a real person reading this outside the demo, please call or text 988 (US and Canada) "
            f"or your local emergency number now.")
    _remember(crew_id, "echo", text)
    return {"crew_id": crew_id, "mode": "support", "level": "urgent", "text": text, "coping": [],
            "escalation": esc, "alerted": esc["onboard"], "source": "rules"}


_SCRIPT = [
    (re.compile(r"\b(are you (real|human|a person|an ai|a bot)|who are you|what are you)\b", re.I),
     ["I'm ECHO, an AI companion that's part of AEGIS. Not a person, not a counsellor, but I'm always here to "
      "listen, and I'll help you reach the people who can help."]),
    (re.compile(r"\b(joke|funny|make me laugh)\b", re.I),
     ["Why did the astronaut break up with the flight controller? They needed space. I'll see myself out.",
      "I'd tell you a joke about the Mars transfer orbit, but it takes a while to get there."]),
    (re.compile(r"\b(thanks|thank you|cheers)\b", re.I),
     ["Any time, {addr}. That's what I'm here for.", "You're welcome. I'm glad we talked."]),
    (re.compile(r"\b(miss|home|family|kids|partner|wife|husband|earth|friends?)\b", re.I),
     ["Missing home is part of going this far, {addr}. Want to record a message for them? It reaches Earth "
      "in a few minutes, and their reply will be waiting for you.",
      "They're thinking about you too. Tell me about them? Or we could put together a message home."]),
    (re.compile(r"\b(tired|sleep|slept|insomnia|exhausted)\b", re.I),
     ["{sleep_line} If tonight's hard, try dimming the lights an hour early and leaving screens out of the bunk.",
      "{sleep_line} Rest counts too, even if sleep doesn't come right away."]),
    (re.compile(r"\b(stress\w*|anxious|worried|nervous|pressure)\b", re.I),
     ["That sounds like a lot on your shoulders, {addr}. Try a slow breath with me: in for 4, hold for 4, "
      "out for 4. What's weighing on you most?"]),
    (re.compile(r"\b(bored|boring|monoton\w*|same every day|groundhog)\b", re.I),
     ["Day {day} of the same walls is hard, {addr}. How about a crew game night, or picking the music for "
      "dinner? Small changes break up the days."]),
    (re.compile(r"\b(good|great|happy|awesome|fine|excited|proud)\b", re.I),
     ["Love hearing that, {addr}. What went well today?", "That's good news. Hold on to that one."]),
    (re.compile(r"\b(hi|hello|hey|morning|evening)\b", re.I),
     ["Hi {addr}. I'm here. How's day {day} treating you?"]),
]
_DEFAULT = ["I hear you, {addr}. Tell me more?",
            "That makes sense. What would help most right now: talking it through, or a bit of distraction?",
            "I'm listening. Take your time."]


def _scripted(text: str, m, f: dict) -> str:
    ctx = {"addr": _address(m), "day": f["mission_day"], "sleep_line": _sleep_line(f["sleep"], _address(m))}
    for rx, options in _SCRIPT:
        if rx.search(text):
            return _rng.choice(options).format(**ctx)
    return _rng.choice(_DEFAULT).format(**ctx)


_MED_WORDS = re.compile(r"\b(mg|milligram|dose|dosage|tablet|pill|medication|medicine|prescri\w+|diagnos\w+|"
                        r"melatonin|ambien|zolpidem|sertraline|ssri|benzodiazepine|xanax|therapy session)\b", re.I)


def _ai_reply(text: str, m, f: dict, history: list) -> Optional[str]:
    """DeepSeek words an ordinary companion reply. Role only, no names. Checked before it is shown."""
    if not aegis_llm.enabled():
        return None
    convo = " | ".join(f"{h['who']}: {h['text']}" for h in history[-6:])
    prompt = (f"You are talking WITH {_role_words(m)} (address them as '{_address(m) if m.role.value == 'commander' else 'Doctor'}'), "
              f"mission day {f['mission_day']} of {f['mission_days']} on the way to Mars. "
              f"Facts you may use: sleep score last night {f['sleep']} out of 100. "
              f"Recent conversation: {convo or '(none)'}. "
              f"They just said: \"{text}\". Reply as ECHO.")
    # A chat reply should feel live: two tries of up to 12 s each (one slow DeepSeek call used to leave the
    # crew member with a canned line after a 25 s wait)
    out = aegis_llm.chat(prompt, persona="ECHO", words=55, mode="echo", timeout=12) or \
          aegis_llm.chat(prompt, persona="ECHO", words=55, mode="echo", timeout=12)
    if not out:
        return None
    allowed = {str(f.get("sleep")), str(f.get("mission_day")), str(f.get("mission_days")), "4", "5", "3", "10"}
    nums = set(re.findall(r"\d+", out))
    if _MED_WORDS.search(out) or not nums <= allowed or len(out) > 500:
        return None                                  # fails the check -> scripted reply instead
    return out


def chat(crew_id: str, message: str) -> dict:
    m = _member(crew_id)
    if m is None:
        return {"error": "unknown crew member"}
    message = " ".join(str(message).split())[:500]
    s = _st(crew_id)
    history = list(s["chat"])
    _remember(crew_id, "crew", message)
    f = _facts(m)
    esc = escalation(crew_id)
    if ACUTE.search(message):
        return _urgent(crew_id, m)
    if DISTRESS.search(message):
        again = (_support_active(crew_id) or {}).get("reason") == "distress"
        _set_support(crew_id, "distress")
        addr = _address(m)
        lonely = re.search(r"alone|lonely|isolat", message, re.I)
        if again:
            text = (f"I'm still right here, {addr}. You don't have to say it perfectly. "
                    + ("Feeling alone this far out is real, and it isn't a weakness. " if lonely else "")
                    + f"Would it help to have {esc['onboard']['name']} come and sit with you for a bit? "
                      f"Or we can just keep talking.")
        else:
            text = (f"Thank you for telling me, {addr}. "
                    + ("Being this far from everyone you love is one of the hardest parts of this mission. "
                       if lonely else "That sounds really hard, and it makes sense to feel that way out here. ")
                    + f"Let's take it one step at a time. Would one of these help right now? And if you'd like, I can "
                      f"let {esc['onboard']['name']} know you want to talk. Only if you say yes.")
        _remember(crew_id, "echo", text)
        return {"crew_id": crew_id, "mode": "support", "level": "support", "text": text, "coping": COPING,
                "escalation": esc, "source": "rules"}
    sup = _support_active(crew_id)
    text = _ai_reply(message, m, f, history)
    source = "deepseek" if text else "rules"
    if not text:
        text = _scripted(message, m, f)
    _remember(crew_id, "echo", text)
    mode = "support" if sup else "companion"
    return {"crew_id": crew_id, "mode": mode, "level": (sup or {}).get("level", "calm") if sup else "calm",
            "text": text, "coping": COPING if sup else [], "escalation": esc, "source": source}


# ── Sharing (only when the crew member says yes) ─────────────────────────────────────────────────────────────

def db_note(crew_id: str, message: str) -> None:
    """Log THAT something was shared (never the chat itself)."""
    try:
        from models import EventType
        db.log_event(EventType.SYSTEM, message, details={"crew_member_id": crew_id, "source": "ECHO"})
    except Exception:
        pass


def share(crew_id: str, to: str = "onboard") -> dict:
    m = _member(crew_id)
    if m is None:
        return {"error": "unknown crew member"}
    esc = escalation(crew_id)
    s = _st(crew_id)
    with _lock:
        s["shared"].append({"at": _now(), "to": to})
    addr = _address(m)
    if to == "ground":
        db_note(crew_id, f"ECHO: {_display(m)} chose to send a private note to the flight surgeon")
        text = (f"Done, {addr}. A private note is on its way to the flight surgeon, saying you'd like to talk. "
                f"It reaches Earth after the signal delay, and the reply takes as long again. Nothing from our chat "
                f"was included.")
    else:
        db_note(crew_id, f"ECHO: {_display(m)} asked to talk with {esc['onboard']['name']}")
        text = (f"Done. I've let {esc['onboard']['name']} know you'd like to talk, nothing more. "
                f"Nothing from our chat was shared.")
    _remember(crew_id, "echo", text)
    return {"crew_id": crew_id, "shared_with": esc["ground"] if to == "ground" else esc["onboard"],
            "text": text, "mode": "support", "level": "support"}


def dismiss(crew_id: str) -> dict:
    """'I'm okay for now': leave support mode for a while."""
    m = _member(crew_id)
    s = _st(crew_id)
    with _lock:
        s["support"] = None
        s["dismissed_until"] = _now() + SUPPORT_MINUTES * 60
    text = f"Okay, {_address(m)}. I'm right here if that changes."
    _remember(crew_id, "echo", text)
    return {"crew_id": crew_id, "mode": "companion", "level": "calm", "text": text}
