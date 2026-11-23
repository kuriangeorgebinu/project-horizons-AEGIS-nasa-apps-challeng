"""PULSE agent: cardiovascular + physiological monitoring (split from root agents.py, 2026-11-23)."""

from .base import BaseAgent, VITAL_THRESHOLDS
from app.models import CrewMember, AlertSeverity, Recommendation, RecommendationPillar, Alert, AgentType


# ─────────────────────────────────────────────────────────────────────────────
# PULSE Agent (Cardiovascular + Physiological)
# ─────────────────────────────────────────────────────────────────────────────

class PULSEAgent(BaseAgent):
    """PULSE: Cardiovascular and physiological monitoring agent."""
    
    agent_type = AgentType.PULSE
    specialty = "cardiovascular"
    
    def analyze(self, crew_member: CrewMember) -> list[dict]:
        """Analyze cardiovascular health."""
        findings = []
        vitals = crew_member.vitals
        
        if not vitals:
            return findings
        
        # Heart rate analysis
        hr = vitals.heart_rate
        if hr > VITAL_THRESHOLDS["heart_rate"]["critical_high"]:
            findings.append({
                "type": "critical",
                "metric": "heart_rate",
                "value": hr,
                "message": f"Critical tachycardia: {hr} BPM",
                "pillar": "PROTECT"
            })
        elif hr > VITAL_THRESHOLDS["heart_rate"]["high"]:
            findings.append({
                "type": "warning",
                "metric": "heart_rate",
                "value": hr,
                "message": f"Elevated heart rate: {hr} BPM",
                "pillar": "PREVENT"
            })
        elif hr < VITAL_THRESHOLDS["heart_rate"]["critical_low"]:
            findings.append({
                "type": "critical",
                "metric": "heart_rate",
                "value": hr,
                "message": f"Critical bradycardia: {hr} BPM",
                "pillar": "PROTECT"
            })
        
        # PULSE raises alerts on Heart and SpO2. Blood pressure, temperature and respiration are in its monitored
        # set too (see monitored_status): reported against their normal-range bands, without new alert rules.
        # SpO2 analysis
        spo2 = vitals.spo2
        if spo2 < VITAL_THRESHOLDS["spo2"]["critical_low"]:
            findings.append({
                "type": "critical",
                "metric": "spo2",
                "value": spo2,
                "message": f"Critical hypoxemia: SpO2 {spo2}%",
                "pillar": "PROTECT"
            })
        elif spo2 < VITAL_THRESHOLDS["spo2"]["low"]:
            findings.append({
                "type": "warning",
                "metric": "spo2",
                "value": spo2,
                "message": f"Low oxygen saturation: SpO2 {spo2}%",
                "pillar": "PREVENT"
            })
        
        return findings

    # Everything PULSE monitors. Heart rate and SpO2 raise findings (analyze); blood pressure, temperature and
    # respiration are reported with their normal-range status from VITAL_THRESHOLDS (same bands as the panel).
    MONITORED = ("heart_rate", "spo2", "blood_pressure", "temperature", "respiratory_rate")

    @staticmethod
    def _band(value, th) -> str:
        if value is None:
            return "no reading"
        if value < th["low"] or value > th["high"]:
            return "out of normal range"
        return "within normal limits"

    @classmethod
    def monitored_status(cls, vitals) -> list[str]:
        """Blood pressure, temperature and respiration as the panel shows them, with their WNL status."""
        if not vitals:
            return []
        out = []
        sys_, dia = vitals.blood_pressure_systolic, vitals.blood_pressure_diastolic
        if sys_ is not None and dia is not None:
            ts, td = VITAL_THRESHOLDS["blood_pressure_systolic"], VITAL_THRESHOLDS["blood_pressure_diastolic"]
            st = "within normal limits" if cls._band(sys_, ts) == cls._band(dia, td) == "within normal limits" \
                else "out of normal range"
            out.append(f"blood pressure {sys_}/{dia} mmHg ({st}; normal {ts['low']}-{ts['high']} systolic, "
                       f"{td['low']}-{td['high']} diastolic)")
        if vitals.temperature is not None:
            t = VITAL_THRESHOLDS["temperature"]
            out.append(f"temperature {vitals.temperature:.1f} °C ({cls._band(vitals.temperature, t)}; "
                       f"normal {t['low']}-{t['high']} °C)")
        if vitals.respiratory_rate is not None:
            r = VITAL_THRESHOLDS["respiratory_rate"]
            out.append(f"respiration {vitals.respiratory_rate} breaths per minute "
                       f"({cls._band(vitals.respiratory_rate, r)}; normal {r['low']}-{r['high']} breaths per minute)")
        return out

    # Fixed titles per metric (the live value stays in the description/rationale), so the
    # "same crew + same title" de-duplication works instead of creating a new card per reading.
    _TITLES = {
        ("heart_rate", "PROTECT"): "Immediate Action: Critical Heart Rhythm",
        ("heart_rate", "PREVENT"): "Preventive Care: Elevated Heart Rate",
        ("spo2", "PROTECT"): "Immediate Action: Critical Low Blood Oxygen",
        ("spo2", "PREVENT"): "Preventive Care: Low Blood Oxygen",
    }
    
    def get_recommendations(self, crew_member: CrewMember, findings: list[dict]) -> list[Recommendation]:
        """Generate cardiovascular recommendations."""
        recommendations = []
        
        for finding in findings:
            # Query knowledge base for evidence
            kb_results = self._query_knowledge_base(
                f"cardiovascular {finding['metric']} countermeasures",
                n_results=2
            )
            evidence = [r["text"][:200] for r in kb_results] if kb_results else []
            
            if finding["pillar"] == "PROTECT":
                rec = self._create_recommendation(
                    crew_member,
                    RecommendationPillar.PROTECT,
                    AlertSeverity.CRITICAL if finding["type"] == "critical" else AlertSeverity.HIGH,
                    self._TITLES.get((finding["metric"], "PROTECT"), "Immediate Action: Critical Vital Sign"),
                    f"{finding['message']}. Immediate intervention required: Protocol MEDIC-1.",
                    finding["message"],
                    actions=([
                        "Start supplemental oxygen and continuous pulse oximetry",
                        "Check cabin ppO2 / CO2 and suit or mask fit",
                        "Move to medical bay; crew medical officer attends",
                        "Consult ground flight surgeon (MEDIC-1)"
                    ] if finding["metric"] == "spo2" else [
                        "Cease current activity immediately",
                        "Move to medical bay; crew medical officer attends",
                        "Record ECG and initiate emergency cardiac protocol if symptoms worsen",
                        "Consult ground flight surgeon (MEDIC-1)"
                    ]),
                    evidence=evidence,
                    confidence=0.92
                )
                recommendations.append(rec)
                
            elif finding["pillar"] == "PREVENT":
                rec = self._create_recommendation(
                    crew_member,
                    RecommendationPillar.PREVENT,
                    AlertSeverity.MEDIUM,
                    self._TITLES.get((finding["metric"], "PREVENT"), "Preventive Care: Vital Sign Out of Range"),
                    f"{finding['message']}. Early intervention recommended to prevent complications.",
                    finding["message"],
                    actions=[
                        "Reduce physical exertion for next 2 hours",
                        "Increase fluid intake (500mL water)",
                        "Perform relaxation breathing exercises",
                        "Schedule follow-up vital check in 30 minutes"
                    ],
                    evidence=evidence,
                    confidence=0.85
                )
                recommendations.append(rec)
                
            elif finding["pillar"] == "PREDICT":
                rec = self._create_recommendation(
                    crew_member,
                    RecommendationPillar.PREDICT,
                    AlertSeverity.LOW,
                    f"Predictive Alert: Cardiovascular Deconditioning Risk",
                    f"Based on mission duration, cardiovascular deconditioning risk is elevated.",
                    finding["message"],
                    actions=[
                        "Maintain aerobic exercise schedule (CEVIS/T2)",
                        "Consider LBNP session this week",
                        "Review orthostatic tolerance test results",
                        "Ensure adequate fluid and salt intake"
                    ],
                    evidence=evidence,
                    confidence=0.78
                )
                recommendations.append(rec)
        
        return recommendations
