"""OSSEO's cohort reference and synthetic DXA examples for the AEGIS demo.

The cited total-body values are published historical cohort means; regional values
are invented UI fixtures. None are measurements for the selected astronaut or a
validated in-flight bone-health signal.
"""
from copy import deepcopy


NASA_TOTAL_BODY_REFERENCE = {
    "source_title": "Benefits for bone from resistance exercise and nutrition in long-duration spaceflight: Evidence from biochemistry and densitometry",
    "source_url": "https://onlinelibrary.wiley.com/doi/full/10.1002/jbmr.1647",
    "source_label": "Smith et al., Journal of Bone and Mineral Research, 2012",
    "study_population": "13 ISS crew across Expeditions 14–20 (2006–2009)",
    "study_context": "Small historical countermeasure cohorts; not NASA-wide normative averages.",
    "measurement": "Total-body DXA areal bone mineral density (aBMD)",
    "unit": "g/cm²",
    "scan_timing": "Preflight: 31–453 days before flight (mean 129 ± 123); postflight: 5–45 days after flight (mean 12 ± 11).",
    "groups": [
        {"name": "iRED", "n": 8, "preflight_mean": 1.213, "preflight_sd": 0.098, "postflight_mean": 1.175, "postflight_sd": 0.076},
        {"name": "ARED", "n": 5, "preflight_mean": 1.147, "preflight_sd": 0.135, "postflight_mean": 1.148, "postflight_sd": 0.137},
    ],
}

DEMO_BMD_COMPARISONS = {
    # Illustrative synthetic site values solely to populate the comparison UI.
    "lumbar_spine": {"preflight": 1.034, "postflight": 1.020},
    "total_hip": {"preflight": 0.940, "postflight": 0.925},
    "femoral_neck": {"preflight": 0.836, "postflight": 0.824},
    "pelvis": {"preflight": 1.095, "postflight": 1.075},
    "both_legs": {"preflight": 1.359, "postflight": 1.334},
    "calcaneus": {"preflight": 0.548, "postflight": 0.536},
    "midshaft_radius": {"preflight": 0.748, "postflight": 0.744},
    "ultradistal_radius": {"preflight": 0.438, "postflight": 0.431},
}


def demo_bmd_payload() -> dict:
    """Return documented cohort reference data plus explicitly synthetic site fixtures."""
    return {
        "data_status": "COHORT REFERENCE + SYNTHETIC SITE EXAMPLES",
        "source_note": (
            "The total-body values are historical group means from the cited study, not "
            "measurements for the selected crew member. Regional site examples below are synthetic."
        ),
        "nasa_total_body_reference": deepcopy(NASA_TOTAL_BODY_REFERENCE),
        "comparisons": deepcopy(DEMO_BMD_COMPARISONS),
    }
