"""MIRROR agent: sleep / mental health (split from root agents.py, 2026-11-23)."""

from .base import BaseAgent
from app.models import CrewMember, AlertSeverity, Recommendation, RecommendationPillar, Alert, AgentType


# ─────────────────────────────────────────────────────────────────────────────
# MIRROR Agent (Behavioral/Cognitive Health)
# ─────────────────────────────────────────────────────────────────────────────

class MIRRORAgent(BaseAgent):
    """MIRROR: Psychological and behavioral health agent."""
    
    agent_type = AgentType.MIRROR
    specialty = "behavioral_health"
    
    def analyze(self, crew_member: CrewMember) -> list[dict]:
        """Analyze psychological health indicators."""
        findings = []
        vitals = crew_member.vitals
        
        # Dashboard-metrics scope: MIRROR evaluates Sleep only.
        # Thresholds match the dashboard: <70 "Fair" (caution), <50 "Poor" (warning).
        if vitals and vitals.sleep_quality is not None:
            sleep = vitals.sleep_quality
            
            if sleep < 50:
                findings.append({
                    "type": "warning",
                    "metric": "sleep",
                    "value": sleep,
                    "message": f"Poor sleep quality: {sleep:.0f}/100",
                    "pillar": "PREVENT"
                })
            elif sleep < 70:
                findings.append({
                    "type": "caution",
                    "metric": "sleep",
                    "value": sleep,
                    "message": f"Below-par sleep quality: {sleep:.0f}/100",
                    "pillar": "PREVENT"
                })
        
        return findings
    
    def get_recommendations(self, crew_member: CrewMember, findings: list[dict]) -> list[Recommendation]:
        """Generate psychological health recommendations."""
        recommendations = []
        
        for finding in findings:
            kb_results = self._query_knowledge_base(
                f"psychological health {finding['metric']} stress countermeasures",
                n_results=2
            )
            evidence = [r["text"][:200] for r in kb_results] if kb_results else []
            
            if finding["metric"] == "stress" and finding["pillar"] == "PROTECT":
                rec = self._create_recommendation(
                    crew_member,
                    RecommendationPillar.PROTECT,
                    AlertSeverity.HIGH,
                    "Immediate Stress Intervention",
                    "Critical stress levels detected requiring immediate support.",
                    finding["message"],
                    actions=[
                        "Initiate private psychological support session",
                        "Reduce workload for next 24 hours",
                        "Schedule private family conference",
                        "Consider VR relaxation therapy"
                    ],
                    evidence=evidence,
                    confidence=0.88
                )
                recommendations.append(rec)
                
            elif finding["metric"] == "stress" and finding["pillar"] == "PREVENT":
                rec = self._create_recommendation(
                    crew_member,
                    RecommendationPillar.PREVENT,
                    AlertSeverity.MEDIUM,
                    "Stress Management Protocol",
                    "Elevated stress indicators warrant preventive intervention.",
                    finding["message"],
                    actions=[
                        "Take 15-minute break in cupola/viewing area",
                        "Practice guided breathing exercise",
                        "Engage in preferred leisure activity",
                        "Journal thoughts in private crew log"
                    ],
                    evidence=evidence,
                    confidence=0.82
                )
                recommendations.append(rec)
                
            elif finding["metric"] == "sleep":
                rec = self._create_recommendation(
                    crew_member,
                    RecommendationPillar.PREVENT,
                    AlertSeverity.MEDIUM,
                    "Sleep Quality Improvement",
                    "Poor sleep quality affecting crew performance and health.",
                    finding["message"],
                    actions=[
                        "Adjust lighting to support circadian rhythm",
                        "Consider melatonin supplementation (physician approved)",
                        "Ensure 8-hour protected sleep period",
                        "Review sleep hygiene practices"
                    ],
                    evidence=evidence,
                    confidence=0.85
                )
                recommendations.append(rec)
                
            elif finding["metric"] == "isolation":
                rec = self._create_recommendation(
                    crew_member,
                    RecommendationPillar.PREDICT,
                    AlertSeverity.LOW,
                    "Third-Quarter Phenomenon Alert",
                    "Mission duration indicates psychological monitoring period.",
                    finding["message"],
                    actions=[
                        "Increase social activities with crew",
                        "Schedule extra family video calls",
                        "Plan crew appreciation/celebration event",
                        "Engage in plant growing or hobby activities"
                    ],
                    evidence=evidence,
                    confidence=0.75
                )
                recommendations.append(rec)
        
        return recommendations
