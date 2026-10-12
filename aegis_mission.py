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
    - NASA limits used: 600 mSv career effective dose (NASA-STD-3001) and 250 mSv per solar particle event
      (NASA OCHMO technical brief).
IL-6 (GENE) - modelled, not measured:
  No blood sensor feeds AEGIS, so IL-6 is MODELLED from stress and radiation relative to background. The
  pre-flight baseline per crew member is seeded to sit inside the Inspiration4 crew's real pre-flight range
  (2.5-5.9 pg/mL, NASA OSDR OSD-575). GENE flags a rise against that personal baseline, the rule that caught
  3 of 4 real post-flight rises in the Inspiration4 data.
"""
import os

CAREER_LIMIT_MSV = 600.0          # NASA-STD-3001 career effective dose limit
SPE_EVENT_LIMIT_MSV = 250.0       # NASA OCHMO: 250 mSv per solar particle event
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


def il6_model(stress_index: float, dose_24h: float, bg: float = None) -> float:
    """MODELLED IL-6 (pg/mL): rises with stress and with radiation above the mission's normal background."""
    bg = background() if bg is None else bg
    stress = 50.0 if stress_index is None else stress_index
    dose = bg if dose_24h is None else dose_24h
    return 1.5 + stress / 20.0 + 0.8 * (dose / bg)


def il6_baseline(crew_id: str) -> float:
    return PREFLIGHT_IL6.get(crew_id, DEFAULT_PREFLIGHT_IL6)


def il6_personal_limit(baseline: float) -> float:
    """The IL-6 value at which GENE's personal-baseline rule fires."""
    return round(max(IL6_PERSONAL_RATIO * baseline, baseline + IL6_PERSONAL_MIN_RISE), 2)
