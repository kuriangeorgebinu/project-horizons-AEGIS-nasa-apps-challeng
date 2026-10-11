"""
AEGIS phone alarm: places a real phone call that reads the alert out loud (Twilio Programmable Voice).

Configure in .env (never commit it):
    TWILIO_ACCOUNT_SID=AC...
    TWILIO_AUTH_TOKEN=...
    TWILIO_FROM_NUMBER=+1...        your Twilio number
    AEGIS_ONCALL_NUMBER=+1...       the phone to ring (on a trial account it must be a verified number)
Optional:
    AEGIS_CALLS=off                 turn calling off without removing the keys
    AEGIS_CALL_COOLDOWN_MIN=10      no repeat call for the same alert within this many minutes
    AEGIS_CALL_MAX_PER_HOUR=4       hard cap across all alerts

Without the keys nothing is dialled: the call is logged as "would have called" so the demo still shows it.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from typing import Optional
from xml.sax.saxutils import escape

import httpx

_lock = threading.Lock()
_last_by_key: dict[str, float] = {}
_recent: list[float] = []
HISTORY: list[dict] = []          # newest first, shown on the dashboard


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def configured() -> bool:
    return all(_env(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "AEGIS_ONCALL_NUMBER"))


def enabled() -> bool:
    return _env("AEGIS_CALLS", "on").lower() != "off"


def _masked(num: str) -> str:
    return ("•••" + num[-4:]) if len(num) >= 4 else "•••"


def status() -> dict:
    return {"enabled": enabled(), "configured": configured(),
            "to": _masked(_env("AEGIS_ONCALL_NUMBER")) if configured() else None,
            "cooldown_min": float(_env("AEGIS_CALL_COOLDOWN_MIN", "10")),
            "max_per_hour": int(_env("AEGIS_CALL_MAX_PER_HOUR", "4")),
            "history": HISTORY[:10]}


def _twiml(message: str) -> str:
    """What the phone says: the alert twice, with a pause, in a clear voice."""
    m = escape(message)
    return ("<Response>"
            "<Say voice=\"Polly.Joanna\">This is AEGIS, the crew health system, with an urgent alert.</Say>"
            "<Pause length=\"1\"/>"
            f"<Say voice=\"Polly.Joanna\">{m}</Say>"
            "<Pause length=\"2\"/>"
            f"<Say voice=\"Polly.Joanna\">Repeating. {m}</Say>"
            "<Pause length=\"1\"/>"
            "<Say voice=\"Polly.Joanna\">Please check the AEGIS dashboard. Goodbye.</Say>"
            "</Response>")


def call(message: str, key: str, reason: str = "alert", source: str = "rules") -> dict:
    """
    Ring the on-call phone and read `message`. `key` groups repeats of the same alert (cooldown).
    Returns a record {status: called | logged | skipped | failed, ...}; never raises.
    """
    message = " ".join(str(message).split())[:600]
    now = time.time()
    cooldown = float(_env("AEGIS_CALL_COOLDOWN_MIN", "10")) * 60
    cap = int(_env("AEGIS_CALL_MAX_PER_HOUR", "4"))
    rec = {"at": datetime.utcnow().isoformat(), "key": key, "reason": reason, "source": source,
           "message": message, "status": None, "detail": None}
    with _lock:
        if not enabled():
            rec.update(status="skipped", detail="calls turned off (AEGIS_CALLS=off)")
        elif now - _last_by_key.get(key, 0) < cooldown:
            rec.update(status="skipped", detail="already called for this alert recently")
        else:
            _recent[:] = [t for t in _recent if now - t < 3600]
            if len(_recent) >= cap:
                rec.update(status="skipped", detail=f"hourly limit of {cap} calls reached")
            else:
                _last_by_key[key] = now
                _recent.append(now)
    if rec["status"] is None:
        if not configured():
            rec.update(status="logged", detail="Twilio not configured: would have called the on-call phone")
        else:
            sid, token = _env("TWILIO_ACCOUNT_SID"), _env("TWILIO_AUTH_TOKEN")
            try:
                r = httpx.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json",
                               auth=(sid, token), timeout=15,
                               data={"To": _env("AEGIS_ONCALL_NUMBER"), "From": _env("TWILIO_FROM_NUMBER"),
                                     "Twiml": _twiml(message)})
                if r.status_code in (200, 201):
                    rec.update(status="called", detail=f"calling {_masked(_env('AEGIS_ONCALL_NUMBER'))}",
                               call_sid=r.json().get("sid"))
                else:
                    rec.update(status="failed", detail=f"Twilio HTTP {r.status_code}: {r.text[:160]}")
            except Exception as e:
                rec.update(status="failed", detail=f"{type(e).__name__}: {e}"[:200])
    with _lock:
        HISTORY.insert(0, rec)
        del HISTORY[30:]
    return rec


def call_async(message: str, key: str, reason: str = "alert", source: str = "rules") -> None:
    threading.Thread(target=call, args=(message, key, reason, source), daemon=True, name="aegis-call").start()


# ── Tool definition for DeepSeek (OpenAI-compatible function calling) ──────────────────────────────
CALL_TOOL = {
    "type": "function",
    "function": {
        "name": "call_flight_surgeon",
        "description": ("Phone the on-call flight surgeon and read them a short spoken message. Use ONLY when a crew "
                        "member is in danger now (an open critical alert or cascade), or when mission control "
                        "explicitly asks you to call or page the doctor. Never for routine questions."),
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string",
                            "description": "What to say on the call, under 60 words, plain spoken English: who, what "
                                           "is wrong (with the reading), and what has been started."},
                "urgency": {"type": "string", "enum": ["urgent", "critical"]},
            },
            "required": ["message", "urgency"],
        },
    },
}
