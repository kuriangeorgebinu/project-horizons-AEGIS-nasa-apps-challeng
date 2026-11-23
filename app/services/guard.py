"""
Public-deployment guard.

When AEGIS_PUBLIC=1 (set by the AWS deploy), the backend is reachable by anyone, so:
  * admin / debug endpoints that change data directly or spend money are switched off, unless the request
    carries the X-AEGIS-Admin header matching AEGIS_ADMIN_TOKEN (set only if the owner adds that secret);
  * the endpoints the dashboard needs (demo controls, agent chat, ECHO, recommendation actions...) stay on,
    but are rate-limited per visitor and overall, so one visitor can't flood the shared demo or the DeepSeek quota;
  * /docs, /redoc and /openapi.json are not served (see main.py).
Locally (AEGIS_PUBLIC unset) nothing is restricted.
"""
from __future__ import annotations

import hmac
import os
import re
import threading
import time
from collections import defaultdict, deque


def public() -> bool:
    return (os.getenv("AEGIS_PUBLIC") or "").strip().lower() in ("1", "true", "on", "yes")


def _admin_ok(headers) -> bool:
    token = os.getenv("AEGIS_ADMIN_TOKEN") or ""
    given = headers.get("x-aegis-admin") or ""
    return bool(token) and hmac.compare_digest(token, given)


# (method, path regex) -> switched off on the public deployment
ADMIN_ONLY = [
    ("PUT", r"^/api/crew/[^/]+/vitals$"),                 # overwrite vitals directly
    ("POST", r"^/api/simulation/(start|stop|config|inject/[^/]+|clear-anomaly)$"),
    ("POST", r"^/api/notify/test$"),                      # rings the real phone
    ("POST", r"^/api/llm/(test|backfill)$"),              # spends DeepSeek quota, unbounded
    ("POST", r"^/api/analyze(/[^/]+)?$"),                 # manual full analysis (DeepSeek calls)
    ("POST", r"^/api/query$"),                            # free-text RAG + LLM
]

# (method, path regex, bucket, per-visitor per minute, everyone per minute)
LIMITS = [
    ("POST", r"^/api/demo/", "demo", 6, 20),
    ("POST", r"^/api/simulation/scenario/", "demo", 6, 20),
    ("POST", r"^/api/ask$", "llm", 15, 60),
    ("POST", r"^/api/echo/[^/]+/(chat|checkin)$", "llm", 15, 60),
    ("POST", r"^/api/whatif$", "whatif", 20, 80),
    ("POST", r"^/api/echo/[^/]+/(share|dismiss)$", "echo-act", 6, 30),
    ("POST", r"^/api/recommendations/[^/]+/action$", "act", 30, 120),
    ("POST", r"^/api/alerts/[^/]+/acknowledge$", "act", 30, 120),
    ("POST", r"^/api/crew/[^/]+/prevent$", "act", 10, 40),
    ("GET", r"^/api/tts$", "tts", 30, 120),
]
_ADMIN = [(m, re.compile(p)) for m, p in ADMIN_ONLY]
_LIMITS = [(m, re.compile(p), b, pv, pa) for m, p, b, pv, pa in LIMITS]
_hits: dict = defaultdict(deque)
_lock = threading.Lock()


def _allow(key: str, limit: int, now: float) -> bool:
    q = _hits[key]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


def check(method: str, path: str, client_ip: str, headers) -> tuple[int, str] | None:
    """None if the request may proceed, else (status code, reason)."""
    if not public() or _admin_ok(headers):
        return None
    for m, rx in _ADMIN:
        if method == m and rx.match(path):
            return 403, "This endpoint is switched off on the public demo."
    for m, rx, bucket, per_visitor, per_all in _LIMITS:
        if method == m and rx.match(path):
            now = time.time()
            with _lock:
                if not _allow(f"{bucket}:{client_ip}", per_visitor, now):
                    return 429, "Too many requests from you for this action. Please wait a minute."
                if not _allow(f"{bucket}:*", per_all, now):
                    return 429, "The public demo is busy right now. Please try again in a minute."
            return None
    return None


def client_ip(request) -> str:
    # Behind Caddy on the same host: trust X-Forwarded-For only from the local proxy
    peer = request.client.host if request.client else "?"
    if peer in ("127.0.0.1", "::1"):
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return peer
