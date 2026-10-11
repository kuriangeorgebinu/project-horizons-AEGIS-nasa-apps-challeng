"""
AEGIS LLM layer: DeepSeek (OpenAI-compatible chat completions API).
NASA Space Apps Challenge 2026

Design rules
- Detection stays rule-based (thresholds + cascade rule). The model only EXPLAINS findings
  and grounds them in the knowledge-base evidence the agent passes in.
- Calls run on a small background thread pool so alerts appear immediately; the AI text is
  written onto the alert / recommendation a few seconds later.
- Fails safe: no key, no network, timeout or API error -> returns None and AEGIS keeps its
  rule-based text. Nothing here can block or crash an analysis run.
- Privacy: prompts carry crew ID + role, vitals and findings only - never names.

Environment (.env)
  DEEPSEEK_API_KEY   required to enable
  DEEPSEEK_MODEL     default "deepseek-flash" (also: "deepseek-v4-pro")
  DEEPSEEK_BASE_URL  default "https://api.deepseek.com"
  DEEPSEEK_TIMEOUT   seconds, default 25
  AEGIS_LLM          "off" disables the layer without removing the key
"""

import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Callable, Optional

import httpx

AI_MARKER = "🧠 DeepSeek"          # prefix the dashboard looks for

_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="deepseek")
_lock = threading.Lock()
_stats = {"calls": 0, "ok": 0, "errors": 0, "last_error": None,
          "last_latency_s": None, "last_call_at": None, "last_model": None}

BASE_SYSTEM = (
    "You are {persona}, part of AEGIS, the health team looking after astronauts on a long deep-space "
    "mission where help from Earth is slow. You talk with mission control and the crew like a calm, "
    "friendly crewmate who happens to be an expert.\n"
    "How to talk: everyday words and short sentences, the way a person speaks out loud. No jargon, codes or "
    "acronyms unless they are explained in a few plain words (say 'an inflammation signal in the blood' rather "
    "than 'IL-6 elevation'; 'the recovery plan' rather than 'Protocol RECOVER-3'). Round numbers sensibly. "
    "No markdown, no bullet points, no lists, at most {words} words.\n"
    "Facts: use ONLY the measurements, findings and evidence given to you; never invent readings, drugs, doses "
    "or studies. This is a heads-up about risk, not a diagnosis. Refer to a crew member by role (for example "
    "'the Commander'), never by ID.\n"
    "{mode_rules}"
)

MODE_RULES = {
    # Alerts, recommendations, forecasts: plain and calm, no jokes
    "report": "Tone: clear, calm and reassuring. No jokes here. Say what is happening, why it matters and the one "
              "or two things to do, in that order. You are talking to mission control ABOUT the crew member, so use "
              "the third person by role ('the Commander's sleep is a bit low'), never 'you' or 'your'.",
    # Voice Terminal: a real conversation
    "chat": "Tone: warm, natural and conversational, like talking to a colleague. You can chat about anything, not "
            "only health. If someone asks for a joke, tell a short, kind one (space and science humour works well); a "
            "light touch of humour is fine in relaxed moments too, but never when someone is in danger or an urgent "
            "alert is open. If a health question comes in, answer it simply first.",
}

PERSONAS = {
    "PULSE": "PULSE, who keeps an eye on heart rate and oxygen levels (upbeat, coach-like)",
    "SOLARIS": "SOLARIS, who watches space radiation and solar storms (calm, with a dry sense of humour)",
    "GENE": "GENE, who tracks inflammation signals in the blood (curious and gentle)",
    "MIRROR": "MIRROR, who looks after sleep and wellbeing (warm and supportive)",
    "OSTEOWATCH": "OsteoWatch, who looks after bones and muscles",
    "AEGIS": "AEGIS, the team lead who pulls everyone's findings together and spots when several problems build "
             "up at once (steady, reassuring)",
}


def _key() -> str:
    return os.getenv("DEEPSEEK_API_KEY", "").strip()


def model() -> str:
    return os.getenv("DEEPSEEK_MODEL", "deepseek-flash").strip() or "deepseek-flash"


def enabled() -> bool:
    return bool(_key()) and os.getenv("AEGIS_LLM", "on").strip().lower() != "off"


def status() -> dict:
    with _lock:
        return {"enabled": enabled(), "provider": "deepseek", "model": model(), **_stats}


def _record(ok: bool, latency: float, error: Optional[str] = None):
    with _lock:
        _stats["calls"] += 1
        _stats["ok" if ok else "errors"] += 1
        _stats["last_latency_s"] = round(latency, 2)
        _stats["last_call_at"] = datetime.utcnow().isoformat()
        _stats["last_model"] = model()
        if error:
            _stats["last_error"] = error[:300]


def chat(prompt: str, persona: str = "AEGIS", words: int = 70,
         max_tokens: int = 1500, temperature: Optional[float] = None, mode: str = "report") -> Optional[str]:
    """Blocking DeepSeek call. Returns the answer text, or None on any failure.

    DeepSeek's current models reason before answering and the reasoning counts against
    max_tokens, so a small budget can come back with an empty answer. We allow a generous
    budget and retry once with double the budget if the answer is empty.
    """
    if not enabled():
        return None
    system = BASE_SYSTEM.format(persona=PERSONAS.get(persona.upper(), PERSONAS["AEGIS"]), words=words,
                                mode_rules=MODE_RULES.get(mode, MODE_RULES["report"]))
    if temperature is None:
        temperature = 0.8 if mode == "chat" else 0.2   # lively conversation, steady health reports
    base = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    t0 = time.time()
    try:
        for budget in (max_tokens, max_tokens * 2):
            r = httpx.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {_key()}"},
                timeout=float(os.getenv("DEEPSEEK_TIMEOUT", "25")),
                json={"model": model(), "max_tokens": budget, "temperature": temperature,
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": prompt}]},
            )
            if r.status_code != 200:
                _record(False, time.time() - t0, f"HTTP {r.status_code}: {r.text[:200]}")
                return None
            text = ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            text = " ".join(text.replace("**", "").split())  # single paragraph, no markdown
            if text:
                _record(True, time.time() - t0)
                return text
        _record(False, time.time() - t0, "empty completion after retry (reasoning used the token budget)")
        return None
    except Exception as e:                                   # network, timeout, bad JSON...
        _record(False, time.time() - t0, f"{type(e).__name__}: {e}")
        return None


def chat_tools(prompt: str, tools: list, persona: str = "AEGIS", words: int = 60,
               max_tokens: int = 1500, mode: str = "chat") -> Optional[dict]:
    """
    DeepSeek call with function calling (OpenAI-compatible 'tools'). Returns
    {"text": str, "tool_calls": [{"name": str, "arguments": dict}]} or None on failure.
    The model decides whether to call a tool; the caller executes it.
    """
    if not enabled():
        return None
    import json as _json
    system = BASE_SYSTEM.format(persona=PERSONAS.get(persona.upper(), PERSONAS["AEGIS"]), words=words,
                                mode_rules=MODE_RULES.get(mode, MODE_RULES["report"]))
    base = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    t0 = time.time()
    try:
        r = httpx.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {_key()}"},
            timeout=float(os.getenv("DEEPSEEK_TIMEOUT", "25")),
            json={"model": model(), "max_tokens": max_tokens, "temperature": 0.3 if mode == "report" else 0.7,
                  "tools": tools, "tool_choice": "auto",
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]},
        )
        if r.status_code != 200:
            _record(False, time.time() - t0, f"HTTP {r.status_code}: {r.text[:200]}")
            return None
        msg = ((r.json().get("choices") or [{}])[0].get("message") or {})
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                args = _json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            calls.append({"name": fn.get("name"), "arguments": args})
        text = " ".join((msg.get("content") or "").replace("**", "").split())
        _record(True, time.time() - t0)
        return {"text": text, "tool_calls": calls}
    except Exception as e:
        _record(False, time.time() - t0, f"{type(e).__name__}: {e}")
        return None


def submit(fn: Callable, *args, **kwargs) -> bool:
    """Run fn in the background if the LLM layer is enabled. Never raises."""
    if not enabled():
        return False

    def _safe():
        try:
            fn(*args, **kwargs)
        except Exception as e:
            _record(False, 0.0, f"enrichment failed: {type(e).__name__}: {e}")

    _executor.submit(_safe)
    return True
