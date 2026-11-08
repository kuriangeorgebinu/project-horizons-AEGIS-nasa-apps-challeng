"""
AEGIS Prometheus metrics, served at GET /metrics (scraped by the Prometheus on the server; not public).

What it answers on the Grafana dashboard:
  - Is the backend up and fast?           HTTP requests, errors and latency per route, process memory/CPU
  - Is the model (DeepSeek) working?      calls by outcome (ok / timeout / busy / HTTP error / empty), latency,
                                          and how many replies fell back to the rules
  - Are the agents running?               analysis passes, duration, time since the last pass
  - What is the mission state?            open alerts, pending recommendations, crew status, demo scenario
  - Did the phone alarm work?             calls by status (called / logged / skipped / failed)

If prometheus_client isn't installed every function here is a no-op, so the backend never breaks over metrics.
"""
from __future__ import annotations

import time

try:
    from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
    ENABLED = True
except Exception:  # pragma: no cover - optional dependency
    ENABLED = False
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4"

if ENABLED:
    HTTP_REQUESTS = Counter("aegis_http_requests_total", "HTTP requests handled by the backend",
                            ["method", "route", "status"])
    HTTP_LATENCY = Histogram("aegis_http_request_duration_seconds", "Backend request latency", ["route"],
                             buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 40))
    LLM_CALLS = Counter("aegis_llm_calls_total",
                        "DeepSeek calls by outcome (ok, timeout, busy, http_error, empty, error)", ["outcome"])
    LLM_LATENCY = Histogram("aegis_llm_latency_seconds", "DeepSeek call latency (successful and failed)",
                            buckets=(0.5, 1, 2, 3, 5, 8, 12, 15, 20, 25, 30, 45, 60))
    LLM_ENABLED = Gauge("aegis_llm_enabled", "1 if DeepSeek is configured and switched on")
    LLM_LAST_SUCCESS = Gauge("aegis_llm_last_success_timestamp_seconds", "Unix time of the last successful DeepSeek call")
    ANSWERS = Counter("aegis_answers_total", "Replies shown to the crew, by surface (ask, echo) and source (deepseek, rules)",
                      ["surface", "source"])
    ANALYSIS_RUNS = Counter("aegis_analysis_runs_total", "Agent analysis passes over the whole crew", ["result"])
    ANALYSIS_DURATION = Histogram("aegis_analysis_duration_seconds", "Duration of one analysis pass",
                                  buckets=(0.5, 1, 2, 5, 10, 20, 40, 80, 160))
    ANALYSIS_LAST = Gauge("aegis_analysis_last_run_timestamp_seconds", "Unix time of the last analysis pass")
    PHONE_ALARM = Counter("aegis_phone_alarm_total", "Phone alarm attempts by status", ["status"])
    ALERTS_OPEN = Gauge("aegis_alerts_open", "Unacknowledged alerts", ["severity"])
    RECS_PENDING = Gauge("aegis_recommendations_pending", "Pending recommendations", ["agent"])
    CREW_STATUS = Gauge("aegis_crew_members", "Crew members by health status", ["status"])
    SIM_RUNNING = Gauge("aegis_simulator_running", "1 if the vitals simulator is running")
    SIM_SCENARIO = Gauge("aegis_simulator_scenario_active", "1 while a demo scenario (or the story demo) is running")


def _outcome(ok: bool, error: str | None) -> str:
    if ok:
        return "ok"
    e = (error or "").lower()
    if "timeout" in e:
        return "timeout"
    if "503" in e or "busy" in e or "429" in e or "rate" in e:
        return "busy"
    if e.startswith("http"):
        return "http_error"
    if "empty" in e:
        return "empty"
    return "error"


def llm_call(ok: bool, latency: float, error: str | None = None) -> None:
    if not ENABLED:
        return
    LLM_CALLS.labels(_outcome(ok, error)).inc()
    LLM_LATENCY.observe(max(0.0, latency))
    if ok:
        LLM_LAST_SUCCESS.set(time.time())


def answer(surface: str, source: str | None) -> None:
    if ENABLED:
        ANSWERS.labels(surface, "deepseek" if source == "deepseek" else "rules").inc()


def analysis(duration: float, ok: bool) -> None:
    if ENABLED:
        ANALYSIS_RUNS.labels("ok" if ok else "error").inc()
        ANALYSIS_DURATION.observe(duration)
        ANALYSIS_LAST.set(time.time())


def phone_alarm(status: str) -> None:
    if ENABLED:
        PHONE_ALARM.labels(status or "unknown").inc()


def http(method: str, route: str, status: int, duration: float) -> None:
    if ENABLED:
        HTTP_REQUESTS.labels(method, route, str(status)).inc()
        HTTP_LATENCY.labels(route).observe(duration)


def _refresh_state() -> None:
    """Mission-state gauges, read when Prometheus scrapes (cheap SQLite reads)."""
    try:
        import aegis_llm
        LLM_ENABLED.set(1 if aegis_llm.enabled() else 0)
    except Exception:
        pass
    try:
        import database as db
        sev = db.count_open_alerts()               # whole table, every severity (incl. info)
        for k in ("critical", "high", "medium", "low", "info"):
            ALERTS_OPEN.labels(k).set(sev.get(k, 0))
        from models import RecommendationStatus
        per: dict = {}
        for r in db.get_recommendations(status=RecommendationStatus.PENDING, limit=500):
            k = getattr(r.agent, "value", str(r.agent))
            per[k] = per.get(k, 0) + 1
        for k in ("AEGIS", "SOLARIS", "GENE", "PULSE", "MIRROR"):
            RECS_PENDING.labels(k).set(per.get(k, 0))
        st: dict = {}
        for m in db.get_all_crew():
            k = getattr(m.status, "value", str(m.status))
            st[k] = st.get(k, 0) + 1
        for k in ("nominal", "caution", "warning", "critical"):
            CREW_STATUS.labels(k).set(st.get(k, 0))
    except Exception:
        pass
    try:
        from simulator import get_simulator
        sim = get_simulator()
        SIM_RUNNING.set(1 if getattr(sim, "running", False) else 0)
        SIM_SCENARIO.set(1 if getattr(sim.config, "scenario", None) else 0)
    except Exception:
        pass


def render() -> tuple[bytes, str]:
    if not ENABLED:
        return b"# prometheus_client not installed\n", CONTENT_TYPE_LATEST
    _refresh_state()
    return generate_latest(), CONTENT_TYPE_LATEST
