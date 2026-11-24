"""GENE agent: inflammatory markers (split from root agents.py, 2026-11-23)."""

from typing import Optional

from .base import BaseAgent
from app.models import CrewMember, AlertSeverity, Recommendation, RecommendationPillar, AgentType
from app import mission as aegis_mission


# ─────────────────────────────────────────────────────────────────────────────
# GENE Agent (Inflammatory Markers - H2 fix)
# ─────────────────────────────────────────────────────────────────────────────

class GENEAgent(BaseAgent):
    """
    GENE: Genomic and inflammatory marker monitoring agent.
    Flags a rise in modelled IL-6 against the crew member's own pre-flight baseline.
    Key to cascade detection as the immune response signals multi-system stress.
    """
    
    agent_type = AgentType.GENE
    specialty = "inflammatory"
    
    # Absolute backstop thresholds (pg/mL); the main rule is the personal baseline (aegis_mission)
    IL6_CAUTION, IL6_WARNING = aegis_mission.IL6_ABS_CAUTION, aegis_mission.IL6_ABS_WARNING

    @classmethod
    def estimate_markers(cls, crew_member: CrewMember) -> Optional[dict]:
        """
        IL-6 for a crew member. No in-flight biomarker sensor feeds AEGIS, so IL-6 is MODELLED from stress and
        radiation above the mission background (aegis_mission.il6_model). Its personal pre-flight baseline is
        seeded inside the Inspiration4 crew's real pre-flight range. Replace with real lab values when available.
        """
        v = crew_member.vitals
        if not v:
            return None
        base = aegis_mission.il6_baseline(crew_member.id)
        il6 = aegis_mission.il6_smoothed(crew_member.id,
                                         aegis_mission.il6_model(v.stress_index, v.radiation_dose_24h, baseline=base))
        limit = aegis_mission.il6_personal_limit(base)
        if il6 >= limit or il6 > cls.IL6_WARNING:
            status = "elevated"
        elif il6 >= base + 0.5 * (limit - base) or il6 > cls.IL6_CAUTION:
            status = "mild"
        else:
            status = "normal"
        return {"il6": round(il6, 1), "il6_unit": "pg/mL", "baseline": base, "personal_limit": limit,
                "status": status, "source": "modelled",
                "thresholds": {"il6_personal": limit, "il6_baseline": base,
                               "il6_caution": cls.IL6_CAUTION, "il6_warning": cls.IL6_WARNING}}

    def analyze(self, crew_member: CrewMember) -> list[dict]:
        """IL-6 against this crew member's own pre-flight baseline (modelled value, see estimate_markers)."""
        if not crew_member.vitals:
            return []
        m = self.estimate_markers(crew_member)
        return self.il6_findings(m["il6"], m["baseline"])

    @classmethod
    def il6_findings(cls, il6: float, baseline: Optional[float] = None) -> list[dict]:
        """
        GENE's IL-6 rules (also used by the What-If simulator and the accuracy evaluation).
        1. Personal baseline: >= 1.5 x own pre-flight baseline AND >= +1.5 pg/mL above it. On the real
           Inspiration4 data this caught 3 of 4 return-day rises (the fixed threshold alone caught 0 of 4).
        2. Absolute backstop: > 12 pg/mL warning, > 8 pg/mL caution.
        """
        if baseline is not None and il6 >= aegis_mission.il6_personal_limit(baseline):
            return [{"type": "warning", "metric": "il6", "value": il6, "baseline": baseline,
                     "message": (f"Inflammation signal up: IL-6 {il6:.1f} pg/mL, {il6 / baseline:.1f}x this crew "
                                 f"member's pre-flight baseline of {baseline:.1f} pg/mL (modelled)"),
                     "pillar": "PREVENT"}]
        if il6 > cls.IL6_WARNING:
            return [{"type": "warning", "metric": "il6", "value": il6,
                     "message": f"Elevated IL-6: {il6:.1f} pg/mL (modelled)", "pillar": "PREVENT"}]
        if il6 > cls.IL6_CAUTION:
            return [{"type": "caution", "metric": "il6", "value": il6,
                     "message": f"Mildly elevated IL-6: {il6:.1f} pg/mL (modelled, monitoring)", "pillar": "PREDICT"}]
        return []
    
    def get_recommendations(self, crew_member: CrewMember, findings: list[dict]) -> list[Recommendation]:
        """Generate inflammatory marker recommendations."""
        recommendations = []
        
        for finding in findings:
            kb_results = self._query_knowledge_base(
                "inflammatory markers IL-6 CRP immune response radiation",
                n_results=2
            )
            evidence = [r["text"][:200] for r in kb_results] if kb_results else []
            
            if finding["pillar"] == "PREVENT":
                rec = self._create_recommendation(
                    crew_member,
                    RecommendationPillar.PREVENT,
                    AlertSeverity.MEDIUM,
                    "Inflammatory Response Management",
                    "Elevated inflammatory markers indicate systemic stress response.",
                    finding["message"],
                    actions=[
                        "Reduce physical exertion for 24-48 hours",
                        "Increase rest periods between activities",
                        "Monitor for secondary symptoms (fatigue, myalgia)",
                        "Consider anti-inflammatory nutrition protocol"
                    ],
                    evidence=evidence,
                    confidence=0.82
                )
                recommendations.append(rec)
                
            elif finding["pillar"] == "PREDICT":
                rec = self._create_recommendation(
                    crew_member,
                    RecommendationPillar.PREDICT,
                    AlertSeverity.LOW,
                    "Immune Response Monitoring",
                    "Predictive markers suggest upcoming inflammatory response.",
                    finding["message"],
                    actions=[
                        "Schedule follow-up biomarker check in 24h",
                        "Maintain hydration and nutrition",
                        "Avoid additional stressors if possible",
                        "Log any symptoms of malaise or fatigue"
                    ],
                    evidence=evidence,
                    confidence=0.75
                )
                recommendations.append(rec)
        
        return recommendations
