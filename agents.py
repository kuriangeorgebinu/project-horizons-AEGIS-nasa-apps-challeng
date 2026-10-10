"""
AEGIS AI Agents Module
NASA Space Apps Challenge 2026

Implements the three pillars: PREDICT, PREVENT, PROTECT
Uses RAG knowledge base for evidence-based recommendations.
Optional LLM integration for enhanced reasoning.
"""

import os
import json
import time
from datetime import datetime, timedelta
from typing import Optional
import uuid

from models import (
    CrewMember, Vitals, HealthStatus, AlertSeverity,
    Recommendation, RecommendationPillar, RecommendationStatus,
    Alert, AgentType, EventType
)
from aegis_rag import get_knowledge_base
import database as db
import aegis_llm


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

# Optional LLM integration (set via environment variables)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

# Thresholds for vital signs
VITAL_THRESHOLDS = {
    "heart_rate": {"low": 50, "high": 100, "critical_low": 40, "critical_high": 150},
    "blood_pressure_systolic": {"low": 90, "high": 140, "critical_low": 80, "critical_high": 180},
    "blood_pressure_diastolic": {"low": 60, "high": 90, "critical_low": 50, "critical_high": 120},
    "spo2": {"low": 95, "critical_low": 90},
    "temperature": {"low": 36.1, "high": 37.8, "critical_low": 35, "critical_high": 39},
    "respiratory_rate": {"low": 12, "high": 20, "critical_low": 8, "critical_high": 30},
    "stress_index": {"high": 70, "critical_high": 85},
    "radiation_dose_24h": {"high": 0.5, "critical_high": 1.0}  # mSv
}


# ─────────────────────────────────────────────────────────────────────────────
# Base Agent Class
# ─────────────────────────────────────────────────────────────────────────────

class BaseAgent:
    """Base class for AEGIS specialist agents."""
    
    agent_type: AgentType = AgentType.AEGIS
    specialty: str = "general"
    
    def __init__(self):
        self.kb = get_knowledge_base()
    
    def analyze(self, crew_member: CrewMember) -> list[dict]:
        """Analyze crew member and return findings."""
        raise NotImplementedError
    
    def get_recommendations(self, crew_member: CrewMember, findings: list[dict]) -> list[Recommendation]:
        """Generate recommendations based on findings."""
        raise NotImplementedError
    
    # ── DeepSeek reasoning layer (runs in the background via aegis_llm.submit) ──────────────
    @staticmethod
    def _crew_context(crew_member: CrewMember) -> str:
        """Anonymised context for the model: ID + role, mission day, vitals. No names."""
        role = getattr(crew_member.role, "value", crew_member.role)
        v = crew_member.vitals
        # Dashboard-metrics scope: only the five Live Vitals metrics go to the model
        parts = []
        if v:
            parts.append(f"heart rate {v.heart_rate} bpm")
            parts.append(f"SpO2 {v.spo2}%")
            if v.sleep_quality is not None:
                parts.append(f"sleep quality {v.sleep_quality:.0f}/100")
            if v.radiation_dose_24h is not None:
                parts.append(f"radiation {v.radiation_dose_24h:.2f} mSv in last 24h")
            markers = GENEAgent.estimate_markers(crew_member)
            if markers:
                parts.append(f"IL-6 {markers['il6']} pg/mL (modelled)")
        return (f"Crew member {crew_member.id} (role: {role}), mission day {crew_member.mission_day}, "
                f"cumulative radiation {crew_member.cumulative_radiation:.0f} mSv. "
                f"Monitored metrics: {', '.join(parts) or 'no readings yet'}.")

    @staticmethod
    def _findings_text(findings: list[dict]) -> str:
        return "; ".join(f"[{f.get('type')}] {f.get('message', f.get('metric'))}" for f in findings) or "none"

    # ── RAG: retrieval for recommendations ────────────────────────────────────────────────
    def _rag_query(self, crew_member: CrewMember, rec: Recommendation, findings: list[dict]) -> str:
        """Search with the ACTUAL finding and context, not just the metric name."""
        role = getattr(crew_member.role, "value", crew_member.role).replace("_", " ")
        f_txt = "; ".join(f.get("message", "") for f in findings[:3]) or rec.description
        return (f"{rec.title}. {rec.description} Findings: {f_txt}. "
                f"Spaceflight health risk and countermeasures for a {role}, long-duration mission day {crew_member.mission_day}.")

    def retrieve_evidence_for(self, crew_member: CrewMember, rec: Recommendation,
                              findings: list[dict], n: int = 4) -> list[str]:
        """Retrieve grounding passages (source label + link) from ChromaDB; stored as JSON strings in rec.evidence."""
        try:
            hits = self.kb.retrieve_evidence(self._rag_query(crew_member, rec, findings), n_results=n)
        except Exception as e:
            self._log_analysis(f"RAG retrieval failed for {rec.id}: {e}", {"error": str(e)})
            return rec.evidence
        return [json.dumps({"source": h["source"], "url": h["url"], "text": h["text"][:400],
                            "collection": h["collection"], "score": round(1 - h["distance"], 3), "cited": False})
                for h in hits] or rec.evidence

    @staticmethod
    def parse_evidence(evidence: list[str]) -> list[dict]:
        """Structured evidence entries (JSON strings); plain legacy strings are skipped."""
        out = []
        for e in evidence or []:
            if isinstance(e, str) and e.startswith("{"):
                try:
                    out.append(json.loads(e))
                except ValueError:
                    pass
        return out

    @staticmethod
    def _parse_json_obj(raw: str) -> Optional[dict]:
        i, j = raw.find("{"), raw.rfind("}")
        if i < 0 or j <= i:
            return None
        try:
            obj = json.loads(raw[i:j + 1])
            return obj if isinstance(obj, dict) else None
        except ValueError:
            return None

    # ── RAG: generation ───────────────────────────────────────────────────────────────────
    def llm_enrich_recommendation(self, crew_member: CrewMember, rec: Recommendation, findings: list[dict]):
        """
        RAG-generated recommendation, written by this specialist via DeepSeek:
        retrieved passages + readings + the rule-approved action menu -> assessment citing sources [n],
        2-4 actions chosen/tailored ONLY from the approved menu, and the list of sources used.
        The rules still decide WHEN a recommendation exists and its severity; the template stays the
        fallback if the model is unavailable or returns something unusable.
        """
        agent = self.agent_type.value
        ev = self.parse_evidence(rec.evidence)
        if not ev:                                   # legacy recommendation (backfill): retrieve now
            rec.evidence = self.retrieve_evidence_for(crew_member, rec, findings)
            ev = self.parse_evidence(rec.evidence)
        rule_basis = rec.rationale.split("Rule basis:", 1)[1].strip() if "Rule basis:" in (rec.rationale or "") \
            else (rec.rationale or "")
        passages = "\n".join(f"[{i}] {e.get('source')}: {str(e.get('text', ''))[:350]}"
                             for i, e in enumerate(ev, 1)) or "none retrieved"
        menu = "\n".join(f"- {a}" for a in rec.actions) or "- (none)"
        prompt = (
            f"{self._crew_context(crew_member)}\n"
            f"Your findings: {self._findings_text(findings)}\n"
            f"Rule-based recommendation: {rec.title}. {rec.description}\n"
            f"Approved action menu:\n{menu}\n"
            f"Retrieved evidence:\n{passages}\n\n"
            "Return ONLY a JSON object, no other text: "
            '{"assessment": "<at most 60 words: why this matters for this crew member now and what to watch next; '
            'cite evidence as [n] where it supports a point>", '
            '"actions": ["2 to 4 actions chosen from the approved menu, most important first; you may tailor wording '
            'with specifics from the readings or evidence"], '
            '"sources": [numbers of the evidence items you actually relied on]}. '
            "Never introduce a medication, supplement, dose or procedure that is not in the approved menu or the evidence."
        )
        raw = aegis_llm.chat(prompt, persona=agent, words=150)
        if not raw:
            return
        data = self._parse_json_obj(raw)
        if data and str(data.get("assessment", "")).strip():
            assessment = " ".join(str(data["assessment"]).split())
            actions = [" ".join(str(a).split()) for a in (data.get("actions") or []) if str(a).strip()][:4] or rec.actions
            cited = []
            for n in data.get("sources") or []:
                try:
                    n = int(n)
                except (TypeError, ValueError):
                    continue
                if 1 <= n <= len(ev) and n not in cited:
                    cited.append(n)
            for i, e in enumerate(ev, 1):
                e["cited"] = i in cited
            ordered = [ev[i - 1] for i in cited] + [e for i, e in enumerate(ev, 1) if i not in cited]
        else:                                        # unusable JSON: keep template actions, use the text as assessment
            assessment, actions, ordered = raw, rec.actions, ev
        db.update_recommendation_rag(
            rec.id,
            f"{aegis_llm.AI_MARKER} ({agent}): {assessment}\n\nRule basis: {rule_basis}",
            actions,
            [json.dumps(e) for e in ordered] or rec.evidence,
        )
        self._log_analysis(f"RAG recommendation generated for {rec.id}",
                           {"model": aegis_llm.model(), "recommendation_id": rec.id,
                            "sources_cited": sum(1 for e in ordered if e.get("cited"))})

    def llm_enrich_alert(self, crew_member: CrewMember, alert: Alert, findings: list[dict]):
        """Ask DeepSeek, as this specialist, to assess a PROTECT-tier alert; append it to the message."""
        prompt = (
            f"{self._crew_context(crew_member)}\n"
            f"ALERT: {alert.title}. {alert.message}\n"
            f"Findings: {self._findings_text(findings)}\n"
            "In at most 50 words: the immediate risk if no action is taken, and the single most important "
            "thing the crew should confirm in the next few minutes."
        )
        text = aegis_llm.chat(prompt, persona=self.agent_type.value, words=50)
        if text:
            db.update_alert_message(alert.id, f"{alert.message}\n\n{aegis_llm.AI_MARKER} ({self.agent_type.value}): {text}")
            self._log_analysis(f"DeepSeek assessment added to alert {alert.id}",
                               {"model": aegis_llm.model(), "alert_id": alert.id})

    def _query_knowledge_base(self, query: str, n_results: int = 3) -> list[dict]:
        """Query the knowledge base for relevant information."""
        results = self.kb.query_countermeasures(query, n_results=n_results)
        return results
    
    def _create_recommendation(
        self,
        crew_member: CrewMember,
        pillar: RecommendationPillar,
        severity: AlertSeverity,
        title: str,
        description: str,
        rationale: str,
        actions: list[str],
        evidence: list[str] = None,
        confidence: float = 0.85
    ) -> Recommendation:
        """Helper to create a recommendation."""
        return Recommendation(
            id=f"REC-{uuid.uuid4().hex[:8].upper()}",
            crew_member_id=crew_member.id,
            pillar=pillar,
            agent=self.agent_type,
            severity=severity,
            title=title,
            description=description,
            rationale=rationale,
            evidence=evidence or [],
            actions=actions,
            status=RecommendationStatus.PENDING,
            confidence=confidence,
            created_at=datetime.utcnow()
        )
    
    def _log_analysis(self, message: str, details: dict = None):
        """Log agent analysis event."""
        db.log_event(EventType.AGENT_ANALYSIS, message, self.agent_type, details)


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
        
        # Dashboard-metrics scope: PULSE evaluates Heart and SpO2 only (no blood pressure / deconditioning model)
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
                    f"{finding['message']}. Immediate intervention required.",
                    finding["message"],
                    actions=[
                        "Cease current activity immediately",
                        "Move to medical bay for monitoring",
                        "Initiate emergency cardiac protocol if symptoms worsen",
                        "Document event in medical log"
                    ],
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


# ─────────────────────────────────────────────────────────────────────────────
# SOLARIS Agent (Radiation Monitoring)
# ─────────────────────────────────────────────────────────────────────────────

class SOLARISAgent(BaseAgent):
    """SOLARIS: Radiation monitoring and protection agent."""
    
    agent_type = AgentType.SOLARIS
    specialty = "radiation"
    
    def analyze(self, crew_member: CrewMember) -> list[dict]:
        """Analyze radiation exposure."""
        findings = []
        vitals = crew_member.vitals
        
        # Check 24h dose if available
        if vitals and vitals.radiation_dose_24h:
            dose = vitals.radiation_dose_24h
            
            if dose > VITAL_THRESHOLDS["radiation_dose_24h"]["critical_high"]:
                findings.append({
                    "type": "critical",
                    "metric": "radiation_24h",
                    "value": dose,
                    "message": f"Critical radiation exposure: {dose:.2f} mSv in 24h",
                    "pillar": "PROTECT"
                })
            elif dose > VITAL_THRESHOLDS["radiation_dose_24h"]["high"]:
                findings.append({
                    "type": "warning",
                    "metric": "radiation_24h",
                    "value": dose,
                    "message": f"Elevated radiation exposure: {dose:.2f} mSv in 24h",
                    "pillar": "PREVENT"
                })
        
        # Cumulative dose analysis
        cumulative = crew_member.cumulative_radiation
        days = crew_member.mission_day
        
        if days > 0:
            daily_avg = cumulative / days
            projected_annual = daily_avg * 365
            
            # NASA annual limit is ~500 mSv
            if projected_annual > 400:
                findings.append({
                    "type": "prediction",
                    "metric": "cumulative_radiation",
                    "value": projected_annual,
                    "message": f"Projected dose at Day 365: {projected_annual:.0f} mSv (based on {daily_avg:.1f} mSv/day avg)",
                    "pillar": "PREDICT"
                })
        
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
                        "Consider pharmaceutical radioprotectants if available",
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


# ─────────────────────────────────────────────────────────────────────────────
# OsteoWatch Agent (part of PULSE conceptually, kept separate for now)
# ─────────────────────────────────────────────────────────────────────────────

class OsteoWatchAgent(BaseAgent):
    """Musculoskeletal health monitoring agent (bone/muscle - part of PULSE domain)."""
    
    agent_type = AgentType.OSTEO
    specialty = "musculoskeletal"
    
    def analyze(self, crew_member: CrewMember) -> list[dict]:
        """Analyze musculoskeletal health."""
        findings = []
        
        # Bone loss prediction based on mission duration
        days = crew_member.mission_day
        
        # ~1-2% bone loss per month in microgravity
        estimated_bone_loss = (days / 30) * 1.5  # 1.5% per month average
        
        if estimated_bone_loss > 5:
            findings.append({
                "type": "prediction",
                "metric": "bone_density",
                "value": estimated_bone_loss,
                "message": f"Estimated bone loss: {estimated_bone_loss:.1f}% after {days} days",
                "pillar": "PREDICT"
            })
        
        # Muscle atrophy prediction
        estimated_muscle_loss = (days / 30) * 3  # ~3% per month without exercise
        
        if estimated_muscle_loss > 8:
            findings.append({
                "type": "prediction",
                "metric": "muscle_mass",
                "value": estimated_muscle_loss,
                "message": f"Estimated muscle loss risk: {estimated_muscle_loss:.1f}%",
                "pillar": "PREDICT"
            })
        
        # Check for back pain (common in space)
        if "back_pain" in crew_member.active_conditions:
            findings.append({
                "type": "warning",
                "metric": "spinal_health",
                "value": "back_pain",
                "message": "Active back pain reported - spinal elongation effects",
                "pillar": "PREVENT"
            })
        
        return findings
    
    def get_recommendations(self, crew_member: CrewMember, findings: list[dict]) -> list[Recommendation]:
        """Generate musculoskeletal recommendations."""
        recommendations = []
        
        for finding in findings:
            kb_results = self._query_knowledge_base(
                f"bone loss muscle atrophy {finding['metric']} countermeasures ARED",
                n_results=2
            )
            evidence = [r["text"][:200] for r in kb_results] if kb_results else []
            
            if finding["metric"] == "bone_density":
                rec = self._create_recommendation(
                    crew_member,
                    RecommendationPillar.PREDICT,
                    AlertSeverity.MEDIUM,
                    "Bone Health Management",
                    f"Predictive analysis indicates elevated bone loss trajectory.",
                    finding["message"],
                    actions=[
                        "Ensure daily ARED resistance exercise (2+ hours)",
                        "Verify vitamin D supplementation (800-1000 IU/day)",
                        "Maintain calcium intake (1000-1200 mg/day)",
                        "Schedule DXA scan if not done in 30 days"
                    ],
                    evidence=evidence,
                    confidence=0.82
                )
                recommendations.append(rec)
                
            elif finding["metric"] == "muscle_mass":
                rec = self._create_recommendation(
                    crew_member,
                    RecommendationPillar.PREDICT,
                    AlertSeverity.MEDIUM,
                    "Muscle Preservation Protocol",
                    "Mission duration indicates need for enhanced exercise protocol.",
                    finding["message"],
                    actions=[
                        "Increase protein intake to 1.5g/kg body weight",
                        "Add ARED squat sets to daily routine",
                        "Complete full T2 treadmill session daily",
                        "Log exercise completion in crew health system"
                    ],
                    evidence=evidence,
                    confidence=0.80
                )
                recommendations.append(rec)
                
            elif finding["metric"] == "spinal_health":
                rec = self._create_recommendation(
                    crew_member,
                    RecommendationPillar.PREVENT,
                    AlertSeverity.MEDIUM,
                    "Back Pain Management",
                    "Active back pain requires intervention to prevent chronic issues.",
                    finding["message"],
                    actions=[
                        "Apply core stabilization exercises",
                        "Use lumbar support during work activities",
                        "Consider NSAIDs if approved by CMO",
                        "Schedule physical therapy session"
                    ],
                    evidence=evidence,
                    confidence=0.85
                )
                recommendations.append(rec)
        
        return recommendations


# ─────────────────────────────────────────────────────────────────────────────
# GENE Agent (Inflammatory Markers - H2 fix)
# ─────────────────────────────────────────────────────────────────────────────

class GENEAgent(BaseAgent):
    """
    GENE: Genomic and inflammatory marker monitoring agent.
    Detects IL-6/CRP elevation 1-2 days after SPE or other stressors.
    Key to cascade detection as the immune response signals multi-system stress.
    """
    
    agent_type = AgentType.GENE
    specialty = "inflammatory"
    
    # GENE thresholds (shared by the agent rules and the dashboard read-out)
    IL6_CAUTION, IL6_WARNING = 8.0, 12.0     # pg/mL
    CRP_CAUTION, CRP_WARNING = 5.0, 8.0      # mg/L

    @classmethod
    def estimate_markers(cls, crew_member: CrewMember) -> Optional[dict]:
        """
        IL-6 / CRP for a crew member. No in-flight biomarker sensor feeds AEGIS yet, so these are
        MODELLED from stress and radiation (IL-6 rises with both; CRP lags and accumulates with mission
        day). Replace with real lab / sensor values when available; everything downstream stays the same.
        """
        v = crew_member.vitals
        if not v:
            return None
        stress = v.stress_index or 50
        radiation_24h = v.radiation_dose_24h or 0.3
        il6 = (stress / 10) + (radiation_24h * 3)
        crp = (stress / 15) + (radiation_24h * 2) + (crew_member.mission_day / 100)
        if il6 > cls.IL6_WARNING:          # status follows IL-6, the dashboard metric
            status = "elevated"
        elif il6 > cls.IL6_CAUTION:
            status = "mild"
        else:
            status = "normal"
        return {"il6": round(il6, 1), "crp": round(crp, 1), "il6_unit": "pg/mL", "crp_unit": "mg/L",
                "status": status, "source": "modelled",
                "thresholds": {"il6_caution": cls.IL6_CAUTION, "il6_warning": cls.IL6_WARNING,
                               "crp_caution": cls.CRP_CAUTION, "crp_warning": cls.CRP_WARNING}}

    def analyze(self, crew_member: CrewMember) -> list[dict]:
        """Analyze inflammatory markers (modelled from stress/radiation context, see estimate_markers)."""
        findings = []
        vitals = crew_member.vitals
        
        if not vitals:
            return findings
        
        # Dashboard-metrics scope: GENE evaluates IL-6 (the dashboard metric). CRP is shown as context only.
        markers = self.estimate_markers(crew_member)
        return self.il6_findings(markers["il6"])

    @classmethod
    def il6_findings(cls, il6: float) -> list[dict]:
        """GENE's IL-6 rules (also used by the What-If simulator with a hypothetical IL-6)."""
        if il6 > cls.IL6_WARNING:
            return [{"type": "warning", "metric": "il6", "value": il6,
                     "message": f"Elevated IL-6: {il6:.1f} pg/mL", "pillar": "PREVENT"}]
        if il6 > cls.IL6_CAUTION:
            return [{"type": "caution", "metric": "il6", "value": il6,
                     "message": f"Mildly elevated IL-6: {il6:.1f} pg/mL (monitoring)", "pillar": "PREDICT"}]
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


# ─────────────────────────────────────────────────────────────────────────────
# AEGIS Coordinator Agent (Orchestrator)
# ─────────────────────────────────────────────────────────────────────────────

class AEGISAgent(BaseAgent):
    """
    AEGIS coordinator agent that orchestrates all specialist agents.
    This is the main entry point for crew analysis.
    Implements cascade detection (T1) and recommendation deduplication (T8).
    """
    
    agent_type = AgentType.AEGIS
    specialty = "coordination"
    
    def __init__(self):
        super().__init__()
        self.specialists = [
            # Dashboard-metrics scope: each agent owns the metrics shown on the Live Vitals panel
            PULSEAgent(),      # Heart rate, SpO2
            SOLARISAgent(),    # Radiation exposure (24h dose + mission total)
            GENEAgent(),       # IL-6
            MIRRORAgent(),     # Sleep
            # OsteoWatchAgent() (bone/muscle) disabled: not a dashboard metric
        ]
        # Track agent statuses for cascade detection (T1)
        self._agent_status_history = {}
        # Crew members who already got a cascade in the current scenario run (one cascade per run)
        self._cascade_fired = set()
    
    def analyze_crew_member(self, crew_member: CrewMember) -> dict:
        """
        Run all specialist agents on a crew member and aggregate results.
        Includes cascade detection (T1), deduplication (T8), and alert routing (T9).
        """
        all_findings = []
        all_recommendations = []
        
        for agent in self.specialists:
            try:
                findings = agent.analyze(crew_member)
                all_findings.extend(findings)
                
                if findings:
                    recs = agent.get_recommendations(crew_member, findings)
                    all_recommendations.extend(recs)
                    
                    # Log the analysis
                    agent._log_analysis(
                        f"{agent.agent_type.value} analyzed {crew_member.name}: {len(findings)} findings",
                        {"findings": len(findings), "recommendations": len(recs)}
                    )
            except Exception as e:
                self._log_analysis(
                    f"Error in {agent.agent_type.value}: {str(e)}",
                    {"error": str(e)}
                )
        
        # T8: Deduplicate recommendations - skip if pending rec with same crew + title exists
        existing_recs = db.get_recommendations(crew_id=crew_member.id, status=RecommendationStatus.PENDING)
        existing_titles = {r.title for r in existing_recs}
        
        # R2: Also deduplicate alerts - skip if unacknowledged alert with same crew + title exists
        existing_alerts = db.get_alerts()
        existing_alert_titles = {a.title for a in existing_alerts if a.crew_member_id == crew_member.id and not a.acknowledged}
        
        new_recommendations = []
        for rec in all_recommendations:
            if rec.title not in existing_titles:
                # T9: Route PROTECT tier critical findings to alerts, not recommendations
                if rec.pillar == RecommendationPillar.PROTECT or (
                    rec.pillar == RecommendationPillar.PROTECT and rec.severity == AlertSeverity.CRITICAL
                ):
                    # R2: Skip if an unacknowledged alert with same title already exists
                    if rec.title in existing_alert_titles:
                        continue
                    # Create an alert instead
                    alert = Alert(
                        id=f"ALT-{uuid.uuid4().hex[:8].upper()}",
                        severity=rec.severity,
                        title=rec.title,
                        message=rec.description,
                        source=rec.agent,
                        crew_member_id=crew_member.id,
                        timestamp=datetime.utcnow()
                    )
                    db.create_alert(alert)
                    self._log_analysis(
                        f"PROTECT alert created for {crew_member.name}: {rec.title}",
                        {"alert_id": alert.id, "severity": rec.severity.value}
                    )
                    # DeepSeek: the specialist that raised it explains the immediate risk (background)
                    aegis_llm.submit(self._specialist_for(rec.agent).llm_enrich_alert, crew_member, alert,
                                     self._findings_for(rec.agent, all_findings))
                else:
                    new_recommendations.append(rec)
                    existing_titles.add(rec.title)  # Prevent duplicates within this batch
        
        # Determine overall health status
        new_status = self._calculate_health_status(all_findings)
        
        # Store recommendations in database
        for rec in new_recommendations:
            rec_findings = self._findings_for(rec.agent, all_findings)
            # RAG retrieval (local, ~tens of ms): evidence with source + link, searched with the actual finding.
            # Stored even when the LLM is off, so every recommendation shows real sources.
            rec.evidence = self.retrieve_evidence_for(crew_member, rec, rec_findings)
            db.create_recommendation(rec)
            # RAG generation (background): the specialist agent tailors actions + writes a cited assessment
            aegis_llm.submit(self._specialist_for(rec.agent).llm_enrich_recommendation, crew_member, rec,
                             rec_findings)
        
        # T1: Cascade detection - track agent statuses and check for convergence.
        # A PROTECT-tier emergency (e.g. SHELTER during an SPE) outranks a cascade: the other domains
        # are reacting to the same hazard, so don't count them toward a cascade for this crew member.
        # Only a radiation hazard (SOLARIS PROTECT / SHELTER, or a declared SPE) pauses cascade detection:
        # it drives every other domain. Other PROTECT findings (e.g. MIRROR critical stress) are part of a
        # cascade, not a reason to hide it.
        protect_active = getattr(self, "protect_mode", False) or any(
            r.pillar == RecommendationPillar.PROTECT and r.agent == AgentType.SOLARIS
            for r in all_recommendations) or any(
            "SHELTER" in (t or "").upper() for t in existing_alert_titles)
        cascade_alert = None
        if not protect_active:
            self._update_cascade_tracking(crew_member, all_findings)
            cascade_alert = self._check_cascade(crew_member)
        
        # Update crew status if changed
        if new_status != crew_member.status:
            db.log_event(
                EventType.SYSTEM,
                f"{crew_member.name} status changed: {crew_member.status.value} → {new_status.value}",
                details={"old": crew_member.status.value, "new": new_status.value}
            )
        
        return {
            "crew_member_id": crew_member.id,
            "crew_member_name": crew_member.name,
            "findings": all_findings,
            "recommendations": [r.model_dump() for r in new_recommendations],
            "status": new_status.value,
            "predictions": [f for f in all_findings if f.get("pillar") == "PREDICT"],
            "preventions": [f for f in all_findings if f.get("pillar") == "PREVENT"],
            "responses": [f for f in all_findings if f.get("pillar") == "PROTECT"],
            "cascade_alert": cascade_alert
        }
    
    def _update_cascade_tracking(self, crew_member: CrewMember, findings: list[dict]):
        """Track agent findings for cascade detection."""
        crew_id = crew_member.id
        if crew_id not in self._agent_status_history:
            self._agent_status_history[crew_id] = {}
        
        now = datetime.utcnow()
        
        # Group findings by agent type (from the pillar they originated from)
        for finding in findings:
            # Cascade counts actual out-of-range readings of the dashboard metrics (warning / critical /
            # caution). Long-range "prediction" findings (e.g. SOLARIS's day-365 dose projection, which is
            # almost always on) no longer count, otherwise that agent would be permanently amber.
            if finding.get("type") in ["warning", "critical", "caution"]:
                # Determine which agent this finding came from based on metric
                agent_name = self._metric_to_agent(finding.get("metric", ""))
                if agent_name:
                    if agent_name not in self._agent_status_history[crew_id]:
                        self._agent_status_history[crew_id][agent_name] = []
                    self._agent_status_history[crew_id][agent_name].append({
                        "timestamp": now,
                        "severity": finding["type"],
                        "metric": finding.get("metric"),
                        "value": finding.get("value")
                    })
    
    def _metric_to_agent(self, metric: str) -> Optional[str]:
        """Map a metric to its agent."""
        mapping = {
            "heart_rate": "PULSE",
            "blood_pressure": "PULSE",
            "spo2": "PULSE",
            "cv_deconditioning": "PULSE",
            "radiation_24h": "SOLARIS",
            "cumulative_radiation": "SOLARIS",
            "stress": "MIRROR",
            "sleep": "MIRROR",
            "isolation": "MIRROR",
            "bone_density": "PULSE",  # OsteoWatch folded into PULSE
            "muscle_mass": "PULSE",
            "spinal_health": "PULSE",
            "inflammatory": "GENE",
            "il6": "GENE",
            "crp": "GENE",
            "immune_suppression": "GENE"
        }
        return mapping.get(metric)
    
    # ── Voice Terminal / Q&A: a real agent answer from live data ──────────────────────────
    _SURNAMES = {"chen": "crew-001", "okonkwo": "crew-002", "kowalski": "crew-003", "nakamura": "crew-004",
                 "sarah": "crew-001", "david": "crew-002", "anna": "crew-003", "kenji": "crew-004"}

    def answer_question(self, question: str, agent_name: str = "AEGIS", crew_id: Optional[str] = None) -> dict:
        """
        Answer a question from mission control as one agent (or AEGIS for all of them):
        1. run that agent's rule-based analysis on the crew member's CURRENT vitals (no DB writes),
        2. add open alerts, whole-crew status and knowledge-base context,
        3. ask DeepSeek to answer in the agent's voice; fall back to the rule-based findings if the
           model is unavailable, so the terminal always gives a real, data-driven answer.
        """
        t0 = time.time()
        agent_name = (agent_name or "AEGIS").strip().upper()

        # Greetings / thanks: answer conversationally and instantly (no status dump, no model call)
        import re as _re
        q_norm = _re.sub(r"[^a-z' ]", " ", (question or "").lower()).split()
        greet = {"hi", "hello", "hey", "hiya", "yo", "morning", "afternoon", "evening", "good", "there",
                 "aegis", "solaris", "gene", "pulse", "mirror", "team", "everyone", "all"}
        thanks = {"thanks", "thank", "you", "cheers", "ok", "okay", "great", "cool", "got", "it", "perfect"}
        if q_norm and len(q_norm) <= 4 and (set(q_norm) <= greet or set(q_norm) <= thanks):
            intros = {
                "AEGIS": "Hi, AEGIS here. Ask me about any crew member, active alerts, or who needs attention most.",
                "SOLARIS": "Hi, SOLARIS here. Ask me about radiation dose, space weather, or whether the crew should shelter.",
                "GENE": "Hi, GENE here. Ask me about inflammation and immune markers like IL-6 and CRP.",
                "PULSE": "Hi, PULSE here. Ask me about heart rate, blood pressure, oxygen, or bone and muscle health.",
                "MIRROR": "Hi, MIRROR here. Ask me about stress, sleep, or behavioural health.",
            }
            is_thanks = set(q_norm) <= thanks and not set(q_norm) <= greet
            answer = "You're welcome. I'll keep monitoring." if is_thanks else intros.get(agent_name, intros["AEGIS"])
            return {"answer": answer, "agent": agent_name, "crew_member_id": crew_id, "source": "rules",
                    "model": None, "agents_consulted": [], "findings": [], "latency_s": round(time.time() - t0, 2)}

        crew = db.get_all_crew()
        by_id = {c.id: c for c in crew}
        q_lower = (question or "").lower()
        # A crew surname in the question overrides the dashboard's selected crew member
        named = next((cid for s, cid in self._SURNAMES.items() if s in q_lower), None)
        member = by_id.get(named or crew_id or "crew-001") or (crew[0] if crew else None)
        if member is None:
            return {"answer": "No crew data available.", "agent": agent_name, "source": "rules"}

        # 1. Live findings from the relevant specialist(s)
        consulted, findings = [], []
        for a in self.specialists:
            a_name = a.agent_type.value.upper()
            if agent_name == "AEGIS" or a_name == agent_name or (agent_name == "PULSE" and a_name == "OSTEOWATCH"):
                consulted.append(a.agent_type.value)
                try:
                    for f in a.analyze(member):
                        findings.append({**f, "agent": a.agent_type.value})
                except Exception as e:
                    self._log_analysis(f"Q&A: {a_name} analysis failed: {e}", {"error": str(e)})

        # 2. Context: open alerts, crew overview, knowledge base
        open_alerts = db.get_alerts(unacknowledged_only=True, limit=50)
        role = lambda c: getattr(c.role, "value", c.role).replace("_", " ")
        alerts_txt = "; ".join(f"{a.title.replace('🔺', '').strip()} ({role(by_id[a.crew_member_id]) if a.crew_member_id in by_id else 'crew'})"
                               for a in open_alerts[:6]) or "none"
        if agent_name == "AEGIS":
            # Crew-wide questions ("who needs attention?"): live abnormal findings for every crew member
            def _top(c):
                msgs = []
                for a in self.specialists:
                    try:
                        msgs += [f.get("message", "") for f in a.analyze(c) if f.get("type") in ("warning", "critical", "caution")]
                    except Exception:
                        pass
                return "; ".join(msgs[:3]) or "no abnormal findings"
            crew_txt = " | ".join(f"{role(c)} ({c.status.value}): {_top(c)}" for c in crew)
        else:
            crew_txt = "; ".join(f"{role(c)}: {c.status.value}" for c in crew)
        findings_txt = "; ".join(f"{f['agent']}: {f.get('message', f.get('metric'))}" for f in findings) or \
            "no abnormal findings (all monitored readings within thresholds)"
        try:
            kb_ctx = self.kb.get_context_for_llm(question, max_tokens=500, n_results=2)
        except Exception:
            kb_ctx = ""

        # Privacy: names never go to the model. Replace any crew name in the question with the role
        # ("How is Nakamura's radiation?" -> "How is the medical officer's radiation?").
        import re as _re
        q_model = question
        for name, cid in self._SURNAMES.items():
            if cid in by_id:
                q_model = _re.sub(rf"\b(?:dr\.?\s+|commander\s+|cmdr\.?\s+|eng\.?\s+)?{name}\b",
                                  f"the {role(by_id[cid])}", q_model, flags=_re.IGNORECASE)

        prompt = (
            f"Question from mission control: \"{q_model}\"\n\n"
            f"Focus crew member: {self._crew_context(member)}\n"
            f"Live findings ({', '.join(consulted) or agent_name}): {findings_txt}\n"
            f"Open alerts: {alerts_txt}\n"
            f"Whole crew status: {crew_txt}\n\n{kb_ctx}\n\n"
            "Answer the question directly, as spoken aloud to mission control, in at most 60 words. "
            "Only give health data the question asks for. If the message is small talk or not about crew health, "
            "reply in one short friendly sentence and say what you can help with; do not give a status report. "
            "If the question is outside your data, say so briefly and give what you do know."
        )
        answer = aegis_llm.chat(prompt, persona=agent_name, words=60)
        source = "deepseek"
        if not answer:
            source = "rules"
            if findings:
                answer = f"{agent_name} on the {role(member)}: " + "; ".join(
                    f.get("message", "") for f in findings[:3]) + "."
            else:
                answer = f"{agent_name}: all monitored readings for the {role(member)} are within normal thresholds."

        self._log_analysis(f"Q&A answered by {agent_name} ({source}) for {member.id}",
                           {"question": question[:200], "source": source})
        return {
            "answer": answer,
            "agent": agent_name,
            "crew_member_id": member.id,
            "source": source,
            "model": aegis_llm.model() if source == "deepseek" else None,
            "agents_consulted": consulted,
            "findings": [f.get("message") for f in findings],
            "latency_s": round(time.time() - t0, 2),
        }

    # ── What-If simulator: real agent rules on hypothetical dashboard values (no DB writes) ──
    def what_if(self, values: dict, crew_id: Optional[str] = None, explain: bool = True) -> dict:
        """
        values: heart_rate, spo2, il6, sleep_quality, radiation_dose_24h (the five dashboard metrics).
        Runs PULSE / GENE / MIRROR / SOLARIS on a hypothetical copy of the crew member, then applies
        AEGIS's decision rules: any PROTECT finding -> immediate protocol (SHELTER-1 for radiation);
        >=3 agents out of range -> CASCADE -> RECOVER-3. Optionally DeepSeek explains the verdict.
        """
        t0 = time.time()
        crew = {c.id: c for c in db.get_all_crew()}
        base = crew.get(crew_id or "crew-001") or next(iter(crew.values()), None)
        if base is None or base.vitals is None:
            return {"error": "no crew data"}
        v = base.vitals.model_copy(update={
            "heart_rate": int(round(values["heart_rate"])),
            "spo2": float(values["spo2"]),
            "sleep_quality": float(values["sleep_quality"]),
            "radiation_dose_24h": float(values["radiation_dose_24h"]),
        })
        hypo = base.model_copy(update={"vitals": v})
        il6 = float(values["il6"])

        # Levels match the dashboard colours: amber = "watch", red = "alert", emergency = "critical".
        # PULSE/SOLARIS: warning = amber, critical = emergency.  GENE/MIRROR: caution = amber, warning = red.
        red_tier = {"GENE": "warning", "MIRROR": "warning"}
        per_agent = {}
        for a in self.specialists:
            name = a.agent_type.value.upper()
            if name == "GENE":
                f = GENEAgent.il6_findings(il6)
            else:
                # Long-range predictions (e.g. day-365 dose projection) aren't part of a what-if on readings
                f = [x for x in a.analyze(hypo) if x.get("type") != "prediction"]
            if any(x.get("pillar") == "PROTECT" or x.get("type") == "critical" for x in f):
                level = "critical"
            elif name in red_tier and any(x.get("type") == red_tier[name] for x in f):
                level = "alert"
            elif name == "PULSE" and any(x.get("metric") == "heart_rate" and isinstance(x.get("value"), (int, float))
                                         and x["value"] > 120 for x in f):
                level = "alert"      # red tier for heart rate: >120 bpm
            else:
                level = "watch" if f else "ok"
            rec_title = None
            if level in ("alert", "watch") and f:
                try:
                    recs = a.get_recommendations(hypo, f)
                    rec_title = recs[0].title if recs else None
                except Exception:
                    rec_title = None
            per_agent[name] = {"level": level, "findings": [x.get("message") for x in f], "recommendation": rec_title}

        out_of_range = [n for n, r in per_agent.items() if r["level"] != "ok"]
        radiation_emergency = per_agent.get("SOLARIS", {}).get("level") == "critical"
        other_emergency = [n for n, r in per_agent.items() if r["level"] == "critical" and n != "SOLARIS"]
        alerting = [n for n, r in per_agent.items() if r["level"] == "alert"]
        if radiation_emergency:
            verdict, protocol = "PROTECT", "SHELTER-1"
            summary = "Radiation emergency: SOLARIS orders all crew to the storm shelter."
        elif other_emergency:
            verdict, protocol = "PROTECT", "MEDIC-1"
            summary = f"Critical reading from {', '.join(other_emergency)}: immediate medical response."
        elif len(out_of_range) >= 3:
            verdict, protocol = "CASCADE", "RECOVER-3"
            summary = f"Cascade: {len(out_of_range)} domains out of range ({', '.join(sorted(out_of_range))})."
        elif alerting:
            verdict, protocol = "ALERT", None
            parts = [f"{n}: {'; '.join(per_agent[n]['findings'])}"
                     + (f" → {per_agent[n]['recommendation']}" if per_agent[n]['recommendation'] else "")
                     for n in alerting]
            summary = "Agent alert — " + " | ".join(parts)
        elif out_of_range:
            verdict, protocol = "WATCH", None
            summary = f"{len(out_of_range)} domain{'s' if len(out_of_range) > 1 else ''} out of range " \
                      f"({', '.join(sorted(out_of_range))}); below the 3-domain cascade threshold."
        else:
            verdict, protocol, summary = "NOMINAL", None, "All five metrics within range."

        explanation, source = None, "rules"
        if explain and verdict != "NOMINAL":
            role = getattr(base.role, "value", base.role).replace("_", " ")
            findings_txt = "; ".join(f"{n}: {', '.join(r['findings'])}" for n, r in per_agent.items() if r["findings"])
            prompt = (
                f"WHAT-IF scenario for the {role}, mission day {base.mission_day}. Hypothetical readings: "
                f"heart rate {v.heart_rate} bpm, SpO2 {v.spo2}%, IL-6 {il6} pg/mL, sleep quality {v.sleep_quality:.0f}/100, "
                f"radiation {v.radiation_dose_24h:.2f} mSv in 24h.\nAgent findings: {findings_txt}\n"
                f"AEGIS decision: {verdict}{' -> protocol ' + protocol if protocol else ''}.\n"
                "In at most 50 words, explain to mission control why AEGIS reached this decision and what the "
                "crew should do first. This is a hypothetical scenario, not a live reading."
            )
            explanation = aegis_llm.chat(prompt, persona="AEGIS", words=50)
            source = "deepseek" if explanation else "rules"
        return {
            "crew_member_id": base.id, "agents": per_agent, "out_of_range": sorted(out_of_range),
            "verdict": verdict, "protocol": protocol, "summary": summary,
            "explanation": explanation, "source": source, "latency_s": round(time.time() - t0, 2),
        }

    def backfill_recommendation_assessments(self, limit: int = 40) -> int:
        """Queue DeepSeek assessments for pending recommendations created before the LLM layer existed."""
        crew = {c.id: c for c in db.get_all_crew()}
        queued = 0
        for rec in db.get_recommendations(status=RecommendationStatus.PENDING, limit=200):
            if queued >= limit:
                break
            # Skip only recommendations that are already RAG-generated (assessment + structured sources)
            if rec.crew_member_id not in crew or (
                    (rec.rationale or "").startswith(aegis_llm.AI_MARKER) and self.parse_evidence(rec.evidence)):
                continue
            spec = self._specialist_for(rec.agent)
            member = crew[rec.crew_member_id]
            try:
                findings = spec.analyze(member) if spec is not self else []
            except Exception:
                findings = []
            if aegis_llm.submit(spec.llm_enrich_recommendation, member, rec, findings):
                queued += 1
        if queued:
            self._log_analysis(f"DeepSeek backfill queued for {queued} recommendations", {"queued": queued})
        return queued

    def _specialist_for(self, agent_type) -> "BaseAgent":
        """The specialist agent instance that owns a recommendation/alert (falls back to AEGIS)."""
        for a in self.specialists:
            if a.agent_type == agent_type:
                return a
        return self

    def _findings_for(self, agent_type, findings: list[dict]) -> list[dict]:
        """Findings that belong to one agent's domain (all findings if none map)."""
        name = getattr(agent_type, "value", str(agent_type)).upper()
        name = "PULSE" if name == "OSTEOWATCH" else name
        mine = [f for f in findings if (self._metric_to_agent(f.get("metric", "")) or "") == name]
        return mine or findings

    def llm_explain_cascade(self, crew_member: CrewMember, alert: Alert, evidence: dict):
        """DeepSeek, as the AEGIS orchestrator, explains how the converging domains may be linked."""
        lines = "; ".join(f"{agent}: {', '.join(items)}" for agent, items in sorted(evidence.items()))
        prompt = (
            f"{self._crew_context(crew_member)}\n"
            f"CASCADE: {len(evidence)} health domains reached amber within 7 days. Latest signals per agent: {lines}\n"
            "Recommended protocol: RECOVER-3 (enhanced monitoring, reduced workload, hydration and "
            "antioxidant support, daily biomarker check-ins).\n"
            "In at most 70 words: explain how these domains are likely feeding each other in this crew "
            "member, and why acting now as a combined protocol beats treating each finding separately."
        )
        text = aegis_llm.chat(prompt, persona="AEGIS", words=70)
        if text:
            db.update_alert_message(alert.id, f"{alert.message}\n\n{aegis_llm.AI_MARKER} (AEGIS): {text}")
            self._log_analysis(f"DeepSeek cascade explanation added to {alert.id}",
                               {"model": aegis_llm.model(), "alert_id": alert.id})

    def rearm_cascades(self):
        """Allow cascades to fire again (called when a new scenario starts, e.g. cascade_demo)."""
        self._cascade_fired.clear()
        self._agent_status_history.clear()

    def _check_cascade(self, crew_member: CrewMember) -> Optional[dict]:
        """
        T1: Check if ≥3 agents have amber/warning status within 7 days.
        If so, create a CASCADE ALERT.
        """
        crew_id = crew_member.id
        if crew_id not in self._agent_status_history:
            return None
        
        now = datetime.utcnow()
        seven_days_ago = now - timedelta(days=7)
        
        # Count agents with recent warnings
        agents_with_warnings = set()
        for agent_name, events in self._agent_status_history[crew_id].items():
            recent_warnings = [
                e for e in events 
                if e["timestamp"] > seven_days_ago and e["severity"] in ["warning", "critical", "caution", "prediction"]
            ]
            if recent_warnings:
                agents_with_warnings.add(agent_name)
        
        # Cascade rule: ≥3 agents at amber within 7 days
        if len(agents_with_warnings) >= 3:
            cascade_alert = Alert(
                id=f"CASCADE-{uuid.uuid4().hex[:8].upper()}",
                severity=AlertSeverity.HIGH,
                title=f"🔺 CASCADE ALERT: {len(agents_with_warnings)} Domains Converging",
                message=f"Multi-domain health pattern detected for {crew_member.name}. "
                        f"Agents reporting: {', '.join(sorted(agents_with_warnings))}. "
                        f"Recommend RECOVER-3 protocol: enhanced monitoring, reduced workload, daily check-ins.",
                source=AgentType.AEGIS,
                crew_member_id=crew_id,
                timestamp=now
            )
            
            # Only create if no OPEN (unacknowledged) cascade alert exists for this crew member.
            # Acknowledging a cascade re-arms it, so the demo can be rehearsed without a DB reset.
            existing_alerts = db.get_alerts(unacknowledged_only=True, limit=200)
            recent_cascade = crew_id in self._cascade_fired or any(
                a.title.startswith("🔺 CASCADE ALERT") and 
                a.crew_member_id == crew_id
                for a in existing_alerts
            )
            
            if not recent_cascade:
                db.create_alert(cascade_alert)
                # Snapshot the evidence before the history is reset, then let DeepSeek explain it (background)
                evidence = {}
                for agent_name, events in self._agent_status_history[crew_id].items():
                    recent = [e for e in events if e["timestamp"] > seven_days_ago][-3:]
                    if recent:
                        evidence[agent_name] = [f"{e['metric']}={e['value']} ({e['severity']})" for e in recent]
                aegis_llm.submit(self.llm_explain_cascade, crew_member, cascade_alert, evidence)
                # Require fresh convergence for the next cascade, and allow only one cascade per
                # crew member per scenario run (re-armed by rearm_cascades() when a scenario starts)
                self._agent_status_history[crew_id] = {}
                self._cascade_fired.add(crew_id)
                self._log_analysis(
                    f"CASCADE ALERT triggered for {crew_member.name}",
                    {"agents": list(agents_with_warnings), "alert_id": cascade_alert.id}
                )
                return {
                    "triggered": True,
                    "agents": list(agents_with_warnings),
                    "alert_id": cascade_alert.id,
                    "recommended_protocol": "RECOVER-3"
                }
        
        return None
    
    def analyze_all_crew(self) -> list[dict]:
        """Analyze all crew members."""
        crew = db.get_all_crew()
        results = []
        
        for member in crew:
            result = self.analyze_crew_member(member)
            results.append(result)
        
        return results
    
    def _calculate_health_status(self, findings: list[dict]) -> HealthStatus:
        """Calculate overall health status from findings."""
        if any(f.get("type") == "critical" for f in findings):
            return HealthStatus.CRITICAL
        if any(f.get("type") == "warning" for f in findings):
            return HealthStatus.WARNING
        if any(f.get("type") in ["caution", "prediction"] for f in findings):
            return HealthStatus.CAUTION
        return HealthStatus.NOMINAL


# ─────────────────────────────────────────────────────────────────────────────
# Global Agent Instance
# ─────────────────────────────────────────────────────────────────────────────

_coordinator = None

def get_coordinator() -> AEGISAgent:
    """Get the global AEGIS coordinator agent instance."""
    global _coordinator
    if _coordinator is None:
        _coordinator = AEGISAgent()
    return _coordinator


def analyze_crew_member(crew_id: str) -> dict:
    """Convenience function to analyze a specific crew member."""
    member = db.get_crew_member(crew_id)
    if not member:
        raise ValueError(f"Crew member {crew_id} not found")
    return get_coordinator().analyze_crew_member(member)


def analyze_all_crew() -> list[dict]:
    """Convenience function to analyze all crew."""
    return get_coordinator().analyze_all_crew()
