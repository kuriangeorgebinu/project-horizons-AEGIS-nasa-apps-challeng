"""
Mission profile and shared physiology models: the single source for radiation and IL-6 numbers used by
the agents, the PREDICT trends, the simulator and the dashboard.

Radiation (SOLARIS) - why these rules:
  Normal galactic-cosmic-ray background depends on where the crew is, so the daily dose is judged against
  the mission's own background instead of one fixed number (a fixed 0.5 / 1.0 mSv rule would order a Mars
  crew into the storm shelter every day; see artifacts/AEGIS-Accuracy-Report.html).
    - warning  : 24 h dose >= 2 x background (an enhancement above normal GCR variation)
    - shelter  : 24 h dose >= 5 x background and >= background + 5 mSv (the step change of a solar
                 particle event). These two factors are AEGIS engineering choices, not NASA limits:
                 NASA's operational storm-shelter calls are made on proton-flux alerts.
    - NASA limits used: 600 mSv career effective dose and the 30-day short-term limit of 250 mGy-Eq to the
      blood-forming organs (both NASA-STD-3001). 250 is NOT a per-storm or per-event dose.
IL-6 (GENE) - modelled, not measured:
  No blood sensor feeds AEGIS, so IL-6 is MODELLED from stress and radiation relative to background. The
  pre-flight baseline per crew member is seeded to sit inside the Inspiration4 crew's real pre-flight range
  (2.5-5.9 pg/mL, NASA OSDR OSD-575). GENE flags a rise against that personal baseline, the rule that caught
  3 of 4 real post-flight rises in the Inspiration4 data.
"""
import math
import os

CAREER_LIMIT_MSV = 600.0          # NASA-STD-3001 career effective dose limit
SPE_EVENT_LIMIT_MSV = 250.0       # NASA-STD-3001 30-day limit: 250 mGy-Eq to the blood-forming organs (not per event)
WARN_FACTOR = 2.0
SHELTER_FACTOR = 5.0
SHELTER_MIN_EXCESS_MSV = 5.0

PROFILES = {
    "mars_transit": {"label": "Mars transit (deep-space cruise)", "background": 1.84, "phase_days": 180,
                     "source": "Curiosity RAD, cruise to Mars (Zeitlin et al. 2013): 1.84 mSv/day"},
    "mars_surface": {"label": "Mars surface", "background": 0.64, "phase_days": 500,
                     "source": "Curiosity RAD, Gale crater (Hassler et al. 2014): 0.64 mSv/day"},
    "lunar_surface": {"label": "Lunar surface", "background": 1.37, "phase_days": 30,
                      "source": "Chang'e-4 LND (Zhang et al. 2020): 1.37 mSv/day"},
    "iss": {"label": "ISS (low Earth orbit)", "background": 0.5, "phase_days": 180,
            "source": "SIDC: ISS crews receive about 80-160 mSv per 6 months (0.44-0.88 mSv/day)"},
}
DEFAULT_PROFILE = "mars_transit"


def profile_key() -> str:
    k = os.getenv("AEGIS_MISSION", DEFAULT_PROFILE).strip().lower()
    return k if k in PROFILES else DEFAULT_PROFILE


def profile(key: str = None) -> dict:
    return PROFILES[key or profile_key()]


def background(key: str = None) -> float:
    return profile(key)["background"]


def radiation_thresholds(bg: float = None) -> dict:
    bg = background() if bg is None else bg
    return {"background": bg, "warning": round(WARN_FACTOR * bg, 2),
            "shelter": round(max(SHELTER_FACTOR * bg, bg + SHELTER_MIN_EXCESS_MSV), 2)}


def mission_info() -> dict:
    k = profile_key()
    p = PROFILES[k]
    return {"profile": k, **p, **radiation_thresholds(p["background"]),
            "career_limit_msv": CAREER_LIMIT_MSV, "spe_event_limit_msv": SPE_EVENT_LIMIT_MSV}


# ── IL-6 (modelled) ────────────────────────────────────────────────────────────────────────────────────────
IL6_PERSONAL_RATIO = 1.5          # >= 1.5 x own pre-flight baseline ...
IL6_PERSONAL_MIN_RISE = 1.5       # ... and at least +1.5 pg/mL above it
IL6_ABS_CAUTION, IL6_ABS_WARNING = 8.0, 12.0   # absolute backstop (pg/mL)

# Seeded pre-flight baselines (pg/mL), inside the Inspiration4 pre-flight range 2.5-5.9 pg/mL
PREFLIGHT_IL6 = {"crew-001": 3.85, "crew-002": 4.13, "crew-003": 4.48, "crew-004": 3.65}
DEFAULT_PREFLIGHT_IL6 = 4.0


I4_OBSERVED_MAX_IL6 = 12.07       # highest IL-6 in the Inspiration4 data the GENE validation rests on (pg/mL)
REST_STRESS = 40.0                # stress index at or below which the model adds nothing (resting + light work)


def il6_model(stress_index: float, dose_24h: float, bg: float = None, baseline: float = None) -> float:
    """MODELLED IL-6 (pg/mL), centred on the person's own pre-flight baseline: at rest (stress <= 40, dose at the
    mission background) it equals that baseline. Stress above rest and radiation above background add to it, with a
    soft ceiling just under the Inspiration4 observed maximum (12.07 pg/mL), so the model never claims values outside
    the real data the GENE rule was validated on."""
    bg = background() if bg is None else bg
    base = DEFAULT_PREFLIGHT_IL6 if baseline is None else baseline
    stress = REST_STRESS if stress_index is None else stress_index
    dose = bg if dose_24h is None else dose_24h
    excess = max(0.0, stress - REST_STRESS) / 10.0 + 0.9 * max(0.0, dose / bg - 1.0)
    room = max(0.1, I4_OBSERVED_MAX_IL6 - 0.1 - base)
    return base + room * (1.0 - math.exp(-excess / room))


# Modest smoothing of the MODELLED IL-6 (no real lag model): a blood marker does not jump from 3.9 to 10 pg/mL in one
# 10-second sample, so the value shown and checked moves at most IL6_MAX_RATE pg/mL per second toward the model's
# target. The demo also compresses time (a storm that would take hours plays in ~2.5 minutes); this only stops the
# one-tick jump, it does not claim real IL-6 kinetics. Both disclosed on the dashboard and in the accuracy report.
IL6_MAX_RATE = 0.12               # pg/mL per second of demo time
_IL6_LIVE: dict = {}              # crew_id -> (value, time.monotonic())
IL6_SMOOTHING = False             # switched on by the live server (main.py); evals and tests see the raw model


def il6_step(prev: float, target: float, dt_s: float) -> float:
    cap = IL6_MAX_RATE * max(0.0, dt_s)
    return prev + max(-cap, min(cap, target - prev))


def il6_smoothed(crew_id: str, target: float) -> float:
    """Live smoothed IL-6 for one crew member (restarts at the target after a pause of more than 60 s)."""
    if not IL6_SMOOTHING:
        return target
    import time as _t
    now = _t.monotonic()
    prev = _IL6_LIVE.get(crew_id)
    val = target if prev is None or now - prev[1] > 60 else il6_step(prev[0], target, now - prev[1])
    _IL6_LIVE[crew_id] = (val, now)
    return val


def il6_smooth_series(points: list) -> list:
    """Same cap over a history series [(seconds, raw_target), ...] so forecasts and the live value agree."""
    if not IL6_SMOOTHING:
        return [raw for _, raw in points]
    out, prev, pt = [], None, None
    for t, raw in points:
        prev = raw if prev is None or t - pt > 60 else il6_step(prev, raw, t - pt)
        pt = t
        out.append(prev)
    return out


def il6_reset_smoothing():
    _IL6_LIVE.clear()


def il6_baseline(crew_id: str) -> float:
    return PREFLIGHT_IL6.get(crew_id, DEFAULT_PREFLIGHT_IL6)


def il6_personal_limit(baseline: float) -> float:
    """The IL-6 value at which GENE's personal-baseline rule fires."""
    return round(max(IL6_PERSONAL_RATIO * baseline, baseline + IL6_PERSONAL_MIN_RISE), 2)
