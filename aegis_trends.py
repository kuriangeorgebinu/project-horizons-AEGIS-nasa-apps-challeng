"""
AEGIS PREDICT layer: trends and time-to-threshold forecasts for the five dashboard metrics.

Works on the vitals history the simulator (or a real telemetry feed) already writes every tick.
For each metric it fits a least-squares line over the recent window and reports:
  - direction: rising / falling / steady (steady = change within the metric's noise tolerance)
  - eta_min: minutes until the metric crosses its agent's amber threshold, when it is heading there
    with a consistent trend (R^2 >= MIN_R2) and the crossing is within HORIZON_MIN
AEGIS then combines the domains per crew member: if the domains already out of range plus the domains
forecast to cross soon reach 3, that is a PREDICTED cascade, with an ETA (when the 3rd domain crosses).
"""
import json
from datetime import datetime
from typing import Optional

import aegis_mission
import database as db

WINDOW_POINTS = 24      # ~2 min at the 5 s simulator tick: the window the trend is fitted on
SERIES_POINTS = 120     # ~10 min of history for the click-to-expand chart
MIN_POINTS = 8
MAX_GAP_S = 30          # a longer gap between readings = feed restart: don't fit a trend across it
HORIZON_MIN = 20.0      # forecast only crossings expected within this many minutes
MIN_R2 = 0.5            # trend must be consistent, not noise
CASCADE_DOMAINS = 3     # same rule as the AEGIS cascade detector

# Same amber cut-offs the agents use (agents.py): PULSE HR > 100 / SpO2 < 95, MIRROR sleep < 70.
# GENE IL-6 and SOLARIS radiation thresholds are per crew member / per mission (aegis_mission):
#   IL-6      -> the crew member's personal-baseline limit (filled in by _thresholds)
#   radiation -> 2 x the mission's background dose (warning level)
# The numbers below are fallbacks only. "tol" = change over the window treated as noise.
METRICS = {
    "heart_rate":         {"agent": "PULSE",   "label": "Heart rate", "unit": "bpm",   "dir": "up",   "threshold": 100.0, "tol": 6.0},
    "spo2":               {"agent": "PULSE",   "label": "SpO2",       "unit": "%",     "dir": "down", "threshold": 95.0,  "tol": 0.8},
    "il6":                {"agent": "GENE",    "label": "IL-6 (modelled)", "unit": "pg/mL", "dir": "up", "threshold": 8.0, "tol": 0.4},
    "sleep_quality":      {"agent": "MIRROR",  "label": "Sleep",      "unit": "/100",  "dir": "down", "threshold": 70.0,  "tol": 5.0},
    "radiation_dose_24h": {"agent": "SOLARIS", "label": "Radiation",  "unit": "mSv",   "dir": "up",   "threshold": 3.68,  "tol": 0.3},
}

# DeepSeek PREDICT notes per crew member (written by the coordinator in the background)
NOTES: dict = {}


def _il6(v: dict) -> float:
    """IL-6 as GENE models it (aegis_mission.il6_model): modelled from stress and dose vs background."""
    return aegis_mission.il6_model(v.get("stress_index"), v.get("radiation_dose_24h"))


def _thresholds(crew_id: str) -> dict:
    """Per-crew / per-mission amber thresholds that override the METRICS fallbacks."""
    return {"il6": aegis_mission.il6_personal_limit(aegis_mission.il6_baseline(crew_id)),
            "radiation_dose_24h": aegis_mission.radiation_thresholds()["warning"]}


def _parse_ts(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")


def _recent(crew_id: str, n: int) -> list[tuple[datetime, dict]]:
    with db.get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT vitals_json, timestamp FROM vitals_history WHERE crew_member_id = ? "
                    "ORDER BY id DESC LIMIT ?", (crew_id, n))
        rows = cur.fetchall()
    out = []
    for r in reversed(rows):
        v = json.loads(r["vitals_json"])
        v["il6"] = _il6(v)
        out.append((_parse_ts(r["timestamp"]), v))
    # Keep only the latest contiguous run: a gap means the feed restarted (new baselines), and fitting a
    # trend across it would forecast a jump that never happened.
    for i in range(len(out) - 1, 0, -1):
        if (out[i][0] - out[i - 1][0]).total_seconds() > MAX_GAP_S:
            return out[i:]
    return out


def _fit(ts: list[float], ys: list[float]):
    """Least squares y = a + b*t. Returns (slope, intercept, r2)."""
    n = len(ys)
    mx, my = sum(ts) / n, sum(ys) / n
    sxx = sum((t - mx) ** 2 for t in ts)
    if sxx == 0:
        return 0.0, my, 0.0
    sxy = sum((t - mx) * (y - my) for t, y in zip(ts, ys))
    syy = sum((y - my) ** 2 for y in ys)
    slope = sxy / sxx
    r2 = (sxy * sxy) / (sxx * syy) if syy > 0 else 0.0
    return slope, my - slope * mx, r2


def _trend(metric: str, pts: list[tuple[datetime, dict]], threshold: Optional[float] = None) -> Optional[dict]:
    meta = dict(METRICS[metric])
    if threshold is not None:
        meta["threshold"] = threshold
    pts = [(t, v[metric]) for t, v in pts if v.get(metric) is not None]
    if not pts:
        return None
    t_end = pts[-1][0]
    cur = float(pts[-1][1])
    up = meta["dir"] == "up"
    out_of_range = cur > meta["threshold"] if up else cur < meta["threshold"]
    res = {"metric": metric, "agent": meta["agent"], "label": meta["label"], "unit": meta["unit"],
           "value": round(cur, 2), "threshold": meta["threshold"], "risk_direction": meta["dir"],
           "out_of_range": out_of_range, "direction": "steady", "slope_per_min": None,
           "fitted_now": None, "r2": None, "eta_min": None}
    if len(pts) < MIN_POINTS:
        return res
    ts = [(t - t_end).total_seconds() / 60 for t, _ in pts]   # minutes, last point = 0
    ys = [float(y) for _, y in pts]
    span = ts[-1] - ts[0]
    if span <= 0:
        return res
    slope, now, r2 = _fit(ts, ys)
    change = slope * span
    direction = "rising" if change > meta["tol"] else "falling" if change < -meta["tol"] else "steady"
    res.update(direction=direction, slope_per_min=round(slope, 4), fitted_now=round(now, 3), r2=round(r2, 2))
    toward = (slope > 0) if up else (slope < 0)
    # The 2-min fit lags a sudden reversal (e.g. right after a protocol is confirmed): if the last ~30 s
    # clearly move AWAY from the threshold, don't forecast a crossing.
    reversing = False
    if len(ys) >= 6:
        recent = (sum(ys[-3:]) - sum(ys[-6:-3])) / 3
        reversing = (recent < -meta["tol"] / 2) if up else (recent > meta["tol"] / 2)
    res["reversing"] = reversing
    if direction != "steady" and toward and not reversing and not out_of_range and r2 >= MIN_R2:
        eta = (meta["threshold"] - now) / slope
        eta = max(eta, 0.25)       # fitted line already past it but the latest reading isn't: imminent
        if eta <= HORIZON_MIN:
            res["eta_min"] = round(eta, 1)
    return res


def crew_forecast(crew_id: str, series: bool = False) -> dict:
    """Trends + forecasts for one crew member, and AEGIS's predicted-cascade verdict."""
    pts = _recent(crew_id, SERIES_POINTS if series else WINDOW_POINTS)
    window = pts[-WINDOW_POINTS:]
    metrics = {}
    thr = _thresholds(crew_id)
    base = {"il6": aegis_mission.il6_baseline(crew_id), "radiation_dose_24h": aegis_mission.background()}
    for m in METRICS:
        t = _trend(m, window, thr.get(m))
        if t is None:
            continue
        if m in base:
            t["baseline"] = base[m]          # personal / mission normal value (Cascade Map ring = 0 here)
        if series:
            t_end = pts[-1][0]
            t["series"] = [[round((ts - t_end).total_seconds() / 60, 2), round(float(v[m]), 2)]
                           for ts, v in pts if v.get(m) is not None]
        metrics[m] = t

    # One entry per agent (domain): out of range now, or the soonest forecast crossing among its metrics
    out_now, upcoming = set(), {}
    for t in metrics.values():
        if t["out_of_range"]:
            out_now.add(t["agent"])
    for t in metrics.values():
        a = t["agent"]
        if a in out_now or t["eta_min"] is None:
            continue
        if a not in upcoming or t["eta_min"] < upcoming[a]["eta_min"]:
            upcoming[a] = {"agent": a, "metric": t["metric"], "label": t["label"], "unit": t["unit"],
                           "threshold": t["threshold"], "value": t["value"], "eta_min": t["eta_min"]}
    upcoming_list = sorted(upcoming.values(), key=lambda u: u["eta_min"])
    need = CASCADE_DOMAINS - len(out_now)
    cascade_eta = None
    if need <= 0:
        risk = "converged"            # the cascade detector itself handles this
    elif need <= len(upcoming_list):
        risk = "cascade"
        cascade_eta = upcoming_list[need - 1]["eta_min"]
    else:
        risk = "watch" if upcoming_list else "none"
    return {"crew_member_id": crew_id, "risk": risk, "cascade_eta_min": cascade_eta,
            "out_now": sorted(out_now), "upcoming": upcoming_list, "metrics": metrics,
            "window_points": len(window), "horizon_min": HORIZON_MIN,
            "note": NOTES.get(crew_id)}


def as_findings(fc: dict) -> list[dict]:
    """Agent PREDICT findings (type 'forecast': shown to operators, never counted as a cascade domain)."""
    out = []
    for t in fc["metrics"].values():
        if t["eta_min"] is not None:
            out.append({"type": "forecast", "metric": t["metric"], "value": t["value"], "agent": t["agent"],
                        "eta_min": t["eta_min"], "pillar": "PREDICT",
                        "message": f"{t['label']} {t['direction']} ({t['value']} {t['unit']}): forecast to cross "
                                   f"{t['threshold']} {t['unit']} in ~{t['eta_min']:.0f} min"})
    return out


def predict_all() -> dict:
    """Forecast for every crew member, plus the most urgent predicted cascade ('top')."""
    crew = []
    for m in db.get_all_crew():
        fc = crew_forecast(m.id)
        fc["role"] = getattr(m.role, "value", m.role)
        crew.append(fc)
    risky = [c for c in crew if c["risk"] == "cascade"]
    top = min(risky, key=lambda c: c["cascade_eta_min"]) if risky else None
    return {"generated_at": datetime.utcnow().isoformat(), "horizon_min": HORIZON_MIN, "crew": crew, "top": top}
