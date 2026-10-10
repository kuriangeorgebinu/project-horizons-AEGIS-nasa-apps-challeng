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
    "You are {persona}, part of AEGIS, an autonomous health-monitoring system for astronauts on a "
    "long-duration deep-space mission where Earth support is delayed. You write for the crew medical "
    "officer and commander.\n"
    "Rules: use ONLY the measurements, findings and evidence given to you; never invent values, drugs, "
    "doses or studies. This is a risk assessment, not a diagnosis. Refer to the crew member in the third "
    "person by role only (for example 'the Commander'), never by ID and never as 'you'. Plain clinical "
    "English, no markdown, no bullet points, at most {words} words."
)

PERSONAS = {
    "PULSE": "PULSE, the cardiovascular and physiology specialist agent",
    "SOLARIS": "SOLARIS, the space-radiation specialist agent",
    "GENE": "GENE, the immune and inflammatory biomarker (IL-6 / CRP) specialist agent",
    "MIRROR": "MIRROR, the behavioral-health specialist agent (stress, sleep, isolation)",
    "OSTEOWATCH": "OsteoWatch, the bone and muscle specialist agent",
    "AEGIS": "the AEGIS orchestrator, which detects cascades where several health domains deteriorate together",
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
         max_tokens: int = 1500, temperature: float = 0.3) -> Optional[str]:
    """Blocking DeepSeek call. Returns the answer text, or None on any failure.

    DeepSeek's current models reason before answering and the reasoning counts against
    max_tokens, so a small budget can come back with an empty answer. We allow a generous
    budget and retry once with double the budget if the answer is empty.
    """
    if not enabled():
        return None
    system = BASE_SYSTEM.format(persona=PERSONAS.get(persona.upper(), PERSONAS["AEGIS"]), words=words)
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
