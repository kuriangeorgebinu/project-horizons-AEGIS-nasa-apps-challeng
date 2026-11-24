"""AEGIS specialist agents (split from root agents.py, 2026-11-23).

Re-exports are lazy (PEP 562): importing ``app.agents`` itself must not
pull in the coordinator. ``app.database`` imports ``app.agents.osseo``
(the OSSEO demo data), the coordinator imports ``app.database``, and the
specialists subclass ``app.agents.base`` — eager imports here would be a
circular import (``app.agents.base`` -> ``app.database`` ->
``app.agents.osseo`` -> ``app.agents.base``). ``from app.agents import
GENEAgent`` and ``import app.agents as A; A.GENEAgent`` both still work.
"""

import importlib as _importlib

__all__ = [
    "BaseAgent", "VITAL_THRESHOLDS", "PULSEAgent", "SOLARISAgent",
    "GENEAgent", "MIRRORAgent", "OsseoAgent", "AEGISAgent",
    "get_coordinator", "analyze_crew_member", "analyze_all_crew",
]

_LAZY = {
    "BaseAgent": "app.agents.base",
    "VITAL_THRESHOLDS": "app.agents.base",
    "PULSEAgent": "app.agents.pulse",
    "SOLARISAgent": "app.agents.solaris",
    "GENEAgent": "app.agents.gene",
    "MIRRORAgent": "app.agents.mirror",
    "OsseoAgent": "app.agents.osseo",
    "AEGISAgent": "app.agents.coordinator",
    "get_coordinator": "app.agents.coordinator",
    "analyze_crew_member": "app.agents.coordinator",
    "analyze_all_crew": "app.agents.coordinator",
}


def __getattr__(name):
    if name in _LAZY:
        return getattr(_importlib.import_module(_LAZY[name]), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(__all__)
