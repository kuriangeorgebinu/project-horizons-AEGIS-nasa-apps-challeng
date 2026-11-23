"""SOLARIS agent: radiation monitoring and protection (split from root agents.py, 2026-11-23)."""

from typing import Optional

from .base import BaseAgent
from app.models import CrewMember, AlertSeverity, Recommendation, RecommendationPillar, AgentType
from app import mission as aegis_mission


# ─────────────────────────────────────────────────────────────────────────────
# SOLARIS Agent (Radiation Monitoring)
# ─────────────────────────────────────────────────────────────────────────────

class SOLARISAgent(BaseAgent):
    """SOLARIS: Radiation monitoring and protection agent."""
    
    agent_type = AgentType.SOLARIS
    specialty = "radiation"
    
    def analyze(self, crew_member: CrewMember, bg: Optional[float] = None) -> list[dict]:
        """
        Radiation, judged against the mission's normal background (aegis_mission): warning at 2 x background,
        storm shelter on a solar-particle-event-sized step (>= 5 x background and >= background + 5 mSv).
        `bg` overrides the mission background (used by the accuracy evaluation to replay other environments).
        """
        findings = []
        vitals = crew_member.vitals
        th = aegis_mission.radiation_thresholds(bg)
        dose = vitals.radiation_dose_24h if vitals else None

        if dose is not None:
            ratio = dose / th["background"]
            if dose >= th["shelter"]:
                findings.append({
                    "type": "critical", "metric": "radiation_24h", "value": dose,
                    "message": (f"Solar particle event: {dose:.1f} mSv in 24 h, {ratio:.0f}x the normal "
                                f"{th['background']:.2f} mSv/day background (NASA limit: "
                                f"{aegis_mission.SPE_EVENT_LIMIT_MSV:.0f} mGy-Eq to the blood-forming organs in any 30 days)"),
                    "pillar": "PROTECT"})
            elif dose >= th["warning"]:
                findings.append({
                    "type": "warning", "metric": "radiation_24h", "value": dose,
                    "message": (f"Elevated radiation: {dose:.2f} mSv in 24 h, {ratio:.1f}x the normal "
                                f"{th['background']:.2f} mSv/day background"),
                    "pillar": "PREVENT"})

        # Mission-phase projection against NASA's 600 mSv career limit, from the measured daily dose
        cumulative = crew_member.cumulative_radiation or 0.0
        if dose is not None and bg is None:
            p = aegis_mission.profile()
            remaining = max(0, p["phase_days"] - crew_member.mission_day)
            projected = cumulative + dose * remaining
            if projected > 0.8 * aegis_mission.CAREER_LIMIT_MSV:
                findings.append({
                    "type": "prediction", "metric": "cumulative_radiation", "value": round(projected),
                    "message": (f"On track for {projected:.0f} mSv by day {p['phase_days']} of the "
                                f"{p['label'].lower()} ({projected / aegis_mission.CAREER_LIMIT_MSV:.0%} of the "
                                f"{aegis_mission.CAREER_LIMIT_MSV:.0f} mSv career limit), at {dose:.2f} mSv/day"),
                    "pillar": "PREDICT"})

        return findings
    
    def get_recommendations(self, crew_member: CrewMember, findings: list[dict]) -> list[Recommendation]:
        """Generate radiation protection recommendations."""
        recommendations = []
        
        for finding in findings:
            kb_results = self._query_knowledge_base(
                "radiation exposure protection countermeasures SPE",
                n_results=2
            )
            evidence = [r["text"][:200] for r in kb_results] if kb_results else []
            
            if finding["pillar"] == "PROTECT":
                rec = self._create_recommendation(
                    crew_member,
                    RecommendationPillar.PROTECT,
                    AlertSeverity.CRITICAL,
                    "SHELTER IMMEDIATELY: Solar Particle Event",
                    "Elevated radiation levels detected. Immediate protective action required.",
                    finding["message"],
                    actions=[
                        "Move to designated storm shelter area NOW",
                        "Don personal radiation monitoring device",
                        "Await all-clear from Mission Control",
                        "Log exposure event in radiation tracking system"
                    ],
                    evidence=evidence,
                    confidence=0.95
                )
                recommendations.append(rec)
                
            elif finding["pillar"] == "PREVENT":
                rec = self._create_recommendation(
                    crew_member,
                    RecommendationPillar.PREVENT,
                    AlertSeverity.MEDIUM,
                    "Radiation Exposure Management",
                    "Take precautionary measures to reduce radiation exposure.",
                    finding["message"],
                    actions=[
                        "Limit time in high-exposure areas",
                        "Postpone non-essential EVA activities",
                        "Check the space-weather forecast before scheduling EVAs",
                        "Review shielding in current location"
                    ],
                    evidence=evidence,
                    confidence=0.88
                )
                recommendations.append(rec)
                
            elif finding["pillar"] == "PREDICT":
                rec = self._create_recommendation(
                    crew_member,
                    RecommendationPillar.PREDICT,
                    AlertSeverity.LOW,
                    "Long-term Radiation Exposure Trend",
                    "Cumulative radiation trending toward career limits.",
                    finding["message"],
                    actions=[
                        "Review mission radiation budget",
                        "Optimize work schedules for low-exposure periods",
                        "Consider crew rotation for high-exposure tasks",
                        "Schedule biodosimetry assessment"
                    ],
                    evidence=evidence,
                    confidence=0.75
                )
                recommendations.append(rec)
        
        return recommendations
