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
import aegis_trends
import aegis_verify
import aegis_notify
import re as _re_mod
import threading

_PLAIN_DOMAIN = {"PULSE": "heart and oxygen", "GENE": "inflammation", "MIRROR": "sleep", "SOLARIS": "radiation"}


def _spoken(text: str) -> str:
    """Text read out on a phone call: no emoji or codes, units spelled out."""
    s = str(text or "")
    for pat, rep in [(r"[\U0001F000-\U0001FAFF☀-➿️‍]", ""), (r"SpO2|SpO₂", "oxygen saturation"),
                     (r"(\d)\s*BPM\b", r"\1 beats per minute"), (r"(\d)\s*bpm\b", r"\1 beats per minute"),
                     (r"(\d)\s*mSv\b", r"\1 millisieverts"), (r"pg/mL", "picograms per millilitre"),
                     (r"(\d)\s*%", r"\1 percent"), (r"/100\b", " out of 100"), (r"\bIL-6\b", "I L 6"),
                     (r"\b([A-Z]{3,})-(\d)\b", r"\1 \2"), (r"\bcrew-0*(\d+)\b", r"crew member \1")]:
        s = _re_mod.sub(pat, rep, s)
    return " ".join(s.split()).strip(" .")


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
        # PREDICT: trends + forecasts, so the agents can answer "what's coming?" too
        trend_txt = ""
        try:
            fc = aegis_trends.crew_forecast(crew_member.id)
            moving = [f"{t['label']} {t['direction']}"
                      + (f" (forecast to cross {t['threshold']} in ~{t['eta_min']:.0f} min)" if t["eta_min"] is not None else "")
                      for t in fc["metrics"].values() if t["direction"] != "steady"]
            trend_txt = " Trends (last 2 min): " + ("; ".join(moving) if moving else "all steady") + "."
            if fc["risk"] == "cascade":
                trend_txt += f" AEGIS PREDICT: 3-domain cascade forecast in ~{fc['cascade_eta_min']:.0f} min."
        except Exception:
            pass
        return (f"Crew member {crew_member.id} (role: {role}), mission day {crew_member.mission_day}, "
                f"cumulative radiation {crew_member.cumulative_radiation:.0f} mSv. "
                f"Monitored metrics: {', '.join(parts) or 'no readings yet'}.{trend_txt}")

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
        return [json.dumps({"source": h["source"], "url": h["url"], "text": h["text"][:700],
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
    def llm_enrich_recommendation(self, crew_member: CrewMember, rec: Recommendation, findings: list[dict],
                                  return_trace: bool = False):
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
        passage_texts = [f"{e.get('source')}: {str(e.get('text', ''))[:600]}" for e in ev]
        passages = "\n".join(f"[{i}] {t}" for i, t in enumerate(passage_texts, 1)) or "none retrieved"
        menu = "\n".join(f"{i}. {a}" for i, a in enumerate(rec.actions, 1)) or "- (none)"
        prompt = (
            f"{self._crew_context(crew_member)}\n"
            f"Your findings: {self._findings_text(findings)}\n"
            f"Rule-based recommendation: {rec.title}. {rec.description}\n"
            f"Approved action menu:\n{menu}\n"
            f"Retrieved evidence:\n{passages}\n\n"
            "Return ONLY a JSON object, no other text: "
            '{"assessment": "<at most 50 words in plain everyday language, explaining to mission control about this crew member (third person, by role): what is going '
            'on, why it matters now, and what to watch next; no jargon or codes; cite evidence as [n] where it supports a point>", '
            '"actions": [{"step": <number of the approved menu item>, "text": "<that item rewritten as one short, '
            'friendly, plain instruction anyone could follow, for example \'Take it easy for the next couple of hours\'>"}, '
            '... 2 to 4 of these, most important first], '
            '"sources": [numbers of the evidence items you actually relied on]}. '
            "Keep each action's meaning exactly as in the menu. Never introduce a medication, supplement, dose or procedure "
            "that is not in the approved menu or the evidence."
        )
        # Grounding check (aegis_verify): numbers, citations, actions and medicines are checked against
        # exactly what the model was given. One retry with the problems listed; then fall back to the rules.
        facts = (f"{self._crew_context(crew_member)}\n{self._findings_text(findings)}\n"
                 f"{rec.title}. {rec.description}\n{rule_basis}")
        weak = [e.get("collection") == "citations" and "Abstract:" not in str(e.get("text", "")) for e in ev]
        attempt_prompt, result, data, raw, tries = prompt, None, None, None, 0
        history = []                                  # every attempt, kept for the accuracy evaluation
        for tries in (1, 2):
            raw = aegis_llm.chat(attempt_prompt, persona=agent, words=150)
            if not raw:
                if return_trace:
                    return {"error": "llm unavailable", "history": history}
                return
            data = self._parse_json_obj(raw)
            if not (data and str(data.get("assessment", "")).strip()):
                result = {"ok": False, "problems": ["answer was not valid JSON"], "checks": None}
            else:
                data["assessment"] = " ".join(str(data["assessment"]).split())
                acts = []
                for a in (data.get("actions") or [])[:4]:
                    if isinstance(a, dict):
                        acts.append({"step": a.get("step"), "text": " ".join(str(a.get("text", "")).split())})
                    elif str(a).strip():
                        acts.append({"step": None, "text": " ".join(str(a).split())})
                data["actions"] = [a for a in acts if a["text"]]
                result = aegis_verify.verify(data["assessment"], data["actions"], list(rec.actions),
                                             passage_texts, facts, weak)
            history.append({"data": data, "raw": None if data else raw, "result": result})
            if result["ok"]:
                break
            attempt_prompt = (prompt + "\n\nYour previous answer FAILED an automatic fact check:\n- "
                              + "\n- ".join(result["problems"][:6])
                              + "\nRewrite it. Use only numbers that appear above. Cite [n] only where passage n "
                                "really says that. Keep every action's meaning from the approved menu. "
                                "No medicines or doses.")
        if return_trace:                              # evaluation mode: no database write
            return {"history": history, "menu": list(rec.actions), "passages": passage_texts,
                    "facts": facts, "weak": weak, "title": rec.title}
        verification = aegis_verify.summary(result) if result.get("checks") else \
            {"ok": False, "problems": result["problems"]}
        verification["attempts"] = tries
        vline = f"Verification: {json.dumps(verification)}"
        if result["ok"]:
            assessment, actions = data["assessment"], [a["text"] for a in data["actions"]] or rec.actions
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
            rationale = f"{aegis_llm.AI_MARKER} ({agent}): {assessment}\n\n{vline}\n\nRule basis: {rule_basis}"
        else:                                        # failed twice: show the rule-based recommendation only
            actions, ordered = rec.actions, ev
            for e in ev:
                e["cited"] = False
            rationale = f"{rec.description}\n\n{vline}\n\nRule basis: {rule_basis}"
        db.update_recommendation_rag(rec.id, rationale, actions, [json.dumps(e) for e in ordered] or rec.evidence)
        self._log_analysis(f"RAG recommendation generated for {rec.id}",
                           {"model": aegis_llm.model(), "recommendation_id": rec.id, "verified": result["ok"],
                            "attempts": tries, "problems": result["problems"][:4],
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
        # Crew members who already got a PREDICT (pre-cascade) warning in the current scenario run
        self._predict_fired = set()
    
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

        # Resolve stale recommendations: if the rule that raised a pending recommendation no longer fires
        # (the reading is back to normal), close it, so the panel only shows what still needs doing.
        current_titles = {r.title for r in all_recommendations}
        now_utc = datetime.utcnow()
        misses = getattr(self, "_rec_misses", None)
        if misses is None:
            misses = self._rec_misses = {}
        for r in existing_recs:
            if r.agent == AgentType.AEGIS or r.title in current_titles:
                misses.pop(r.id, None)
                continue
            created = r.created_at.replace(tzinfo=None) if r.created_at else now_utc
            if (now_utc - created).total_seconds() < 60:
                continue                      # give a brand-new recommendation a minute before resolving it
            misses[r.id] = misses.get(r.id, 0) + 1
            if misses[r.id] < 2:              # normal on two analysis passes in a row: avoids flapping at a threshold
                continue
            misses.pop(r.id, None)
            db.update_recommendation_status(r.id, RecommendationStatus.COMPLETED)
            self._log_analysis(f"Resolved for {crew_member.name}: '{r.title}' (reading back to normal)",
                               {"recommendation_id": r.id, "auto_resolved": True})
        existing_titles = {r.title for r in db.get_recommendations(crew_id=crew_member.id,
                                                                     status=RecommendationStatus.PENDING)}
        
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
                    rb = rec.rationale.split("Rule basis:", 1)[1].strip() if "Rule basis:" in (rec.rationale or "") \
                        else (rec.rationale or rec.description)
                    self.page_on_call(crew_member, alert, kind="protect", detail=rb)
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

        # PREDICT: trends + time-to-threshold forecasts on the dashboard metrics. Forecast findings are
        # reported per agent but never counted as cascade domains (only real out-of-range readings are).
        try:
            fc = aegis_trends.crew_forecast(crew_member.id)
            all_findings.extend(aegis_trends.as_findings(fc))
            if (fc["risk"] == "cascade" and not protect_active and cascade_alert is None
                    and crew_member.id not in self._predict_fired and crew_member.id not in self._cascade_fired):
                self._predict_fired.add(crew_member.id)
                ups = ", ".join(f"{u['agent']} {u['label']} ~{u['eta_min']:.0f} min" for u in fc["upcoming"])
                self._log_analysis(
                    f"PREDICT: cascade risk for {crew_member.name} in ~{fc['cascade_eta_min']:.0f} min "
                    f"(out of range: {', '.join(fc['out_now']) or 'none'}; forecast: {ups})",
                    {"cascade_eta_min": fc["cascade_eta_min"], "out_now": fc["out_now"],
                     "upcoming": fc["upcoming"]})
                aegis_llm.submit(self.llm_explain_prediction, crew_member, fc)
        except Exception as e:
            self._log_analysis(f"PREDICT error: {e}", {"error": str(e)})
        
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

    def answer_question(self, question: str, agent_name: str = "AEGIS", crew_id: Optional[str] = None,
                        return_context: bool = False) -> dict:
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
        # (With DeepSeek on, small talk goes to the model so it sounds like a person; this is the offline fallback.)
        if not aegis_llm.enabled() and q_norm and len(q_norm) <= 4 and (set(q_norm) <= greet or set(q_norm) <= thanks):
            intros = {
                "AEGIS": "Hey! AEGIS here. Ask me how anyone on the crew is doing, or who needs a hand most.",
                "SOLARIS": "Hi, SOLARIS here. Ask me about radiation or solar storms, and whether anyone should take cover.",
                "GENE": "Hi, GENE here. I watch for signs of inflammation in the blood. Ask me how anyone's doing.",
                "PULSE": "Hey, PULSE here! Heart rate and oxygen are my thing. Who should I check on?",
                "MIRROR": "Hi, MIRROR here. I look after sleep and how everyone's holding up. What would you like to know?",
            }
            is_thanks = set(q_norm) <= thanks and not set(q_norm) <= greet
            answer = "Anytime. I'll keep an eye on everyone." if is_thanks else intros.get(agent_name, intros["AEGIS"])
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

        # "Call / page the doctor": AEGIS phones the on-call flight surgeon (tool call), whoever was asked
        if _re_mod.search(r"\b(call|phone|page|ring|notify|wake)\b.{0,40}\b(doctor|surgeon|medic|on[- ]?call|physician)\b",
                          q_lower):
            q_safe = question                       # names never go to the model: use the role instead
            for nm, cid in self._SURNAMES.items():
                if cid in by_id:
                    r_ = getattr(by_id[cid].role, "value", by_id[cid].role).replace("_", " ")
                    q_safe = _re_mod.sub(rf"\b(?:dr\.?\s+|commander\s+|cmdr\.?\s+|eng\.?\s+)?{nm}\b", f"the {r_}",
                                         q_safe, flags=_re_mod.IGNORECASE)
            paged = self.page_from_chat(q_safe, member)
            if paged:
                return {"answer": paged["answer"], "agent": "AEGIS", "crew_member_id": member.id,
                        "source": "deepseek" if paged["call"]["source"] != "rules" else "rules",
                        "model": aegis_llm.model(), "agents_consulted": [], "findings": [],
                        "call": {k: paged["call"][k] for k in ("status", "detail", "source", "at")},
                        "latency_s": round(time.time() - t0, 2)}

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
            "Reply out loud, like a person talking, in at most 60 words. It is a conversation, not a report: "
            "if it's small talk, a joke request or any other topic, just respond naturally (you don't have to bring up "
            "health at all). If it's about the crew's health, answer only what was asked, in plain everyday words, "
            "and say what to do if anything. If you don't have the data for it, say so simply and share what you do know."
        )
        answer = aegis_llm.chat(prompt, persona=agent_name, words=60, mode="chat")
        source = "deepseek"
        # Grounding: every number in a spoken answer must come from the data in the prompt (aegis_verify).
        num_check, first_check = None, None
        if answer:
            num_check = first_check = aegis_verify.check_numbers(answer, prompt)
            if not num_check["ok"]:
                retry = aegis_llm.chat(prompt + "\n\nYour previous reply used numbers that are not in the data above ("
                                       + ", ".join(num_check["unsupported"]) + "). Answer again using only numbers "
                                       "that appear above, or no numbers.", persona=agent_name, words=60, mode="chat")
                num_check = aegis_verify.check_numbers(retry or "", prompt)
                answer = retry if (retry and num_check["ok"]) else None
        if not answer:
            source = "rules"
            if findings:
                answer = f"Here's what I'm seeing for the {role(member)}: " + "; ".join(
                    f.get("message", "") for f in findings[:3]) + "."
            else:
                answer = f"Good news: everything I track for the {role(member)} looks normal right now."

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
            "numbers_verified": bool(num_check and num_check["ok"]) if source == "deepseek" else None,
            "latency_s": round(time.time() - t0, 2),
            **({"_context": prompt, "_first_check": first_check} if return_context else {}),
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

    def backfill_recommendation_assessments(self, limit: int = 40, force: bool = False) -> int:
        """Queue DeepSeek assessments for pending recommendations created before the LLM layer existed
        (force=True rewrites existing ones too, e.g. after a change of writing style)."""
        crew = {c.id: c for c in db.get_all_crew()}
        queued = 0
        for rec in db.get_recommendations(status=RecommendationStatus.PENDING, limit=200):
            if queued >= limit:
                break
            # Skip only recommendations that are already RAG-generated (assessment + structured sources)
            if rec.crew_member_id not in crew or (not force and
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
            "In at most 50 plain words: how these problems are probably feeding each other in this crew member, "
            "and why tackling them together with the recovery plan works better than one at a time."
        )
        text = aegis_llm.chat(prompt, persona="AEGIS", words=50)
        if text:
            db.update_alert_message(alert.id, f"{alert.message}\n\n{aegis_llm.AI_MARKER} (AEGIS): {text}")
            self._log_analysis(f"DeepSeek cascade explanation added to {alert.id}",
                               {"model": aegis_llm.model(), "alert_id": alert.id})

    # PREVENT-1: countermeasures per domain, started when mission control acts on a PREDICT forecast
    _PREVENT_ACTIONS = {
        "MIRROR": "Sleep: block out a full 8 hours tonight, dim the lights early and push late tasks to tomorrow",
        "SOLARIS": "Radiation: work from the better-shielded part of the ship for the next day",
        "GENE": "Inflammation: take a recovery day with rest, plenty of water and good food, plus a quick blood check tomorrow",
        "PULSE": "Heart: go easy on exercise today, drink a big glass of water, and check heart rate every 30 minutes",
    }

    def start_prevention(self, crew_member: CrewMember) -> dict:
        """
        PREDICT -> PREVENT: mission control acts on a forecast before the cascade happens.
        Records an accepted PREVENT-1 plan (one countermeasure per domain that is out of range or forecast to
        cross), grounded with RAG evidence, and resets this crew member's cascade window so the cascade
        only fires again if the domains converge despite the countermeasures.
        """
        fc = aegis_trends.crew_forecast(crew_member.id)
        domains = list(dict.fromkeys(fc["out_now"] + [u["agent"] for u in fc["upcoming"]]))
        if not domains:
            return {"started": False, "reason": "No out-of-range or forecast domains for this crew member."}
        order = ["MIRROR", "SOLARIS", "GENE", "PULSE"]
        domains.sort(key=lambda d: order.index(d) if d in order else 9)
        ups = "; ".join(f"{u['agent']} {u['label']} → {u['threshold']} {u['unit']} in ~{u['eta_min']:.0f} min"
                        for u in fc["upcoming"])
        eta = fc["cascade_eta_min"]
        rec = self._create_recommendation(
            crew_member,
            RecommendationPillar.PREVENT,
            AlertSeverity.HIGH,
            "Prevention plan: head off a health cascade (PREVENT-1)",
            (f"AEGIS saw {len(domains)} health problems building up at once"
             + (f", likely to pile up in about {max(1, round(eta))} minutes" if eta is not None else "")
             + ", so small fixes started early, before it gets serious."),
            (f"Out of range: {', '.join(fc['out_now']) or 'none'}. Forecast: {ups or 'none'}. "
             "Rule basis: >=3 domains out of range or forecast to cross within 20 min -> act before convergence."),
            actions=[self._PREVENT_ACTIONS[d] for d in domains if d in self._PREVENT_ACTIONS],
            confidence=0.8,
        )
        rec.status = RecommendationStatus.ACCEPTED
        rec.evidence = self.retrieve_evidence_for(crew_member, rec, aegis_trends.as_findings(fc))
        db.create_recommendation(rec)
        # Fresh cascade window: earlier amber readings no longer add up to a cascade once countermeasures start
        self._agent_status_history.pop(crew_member.id, None)
        self._predict_fired.add(crew_member.id)
        aegis_trends.NOTES.pop(crew_member.id, None)
        db.log_event(EventType.USER_ACTION,
                     f"PREVENT-1 started for {crew_member.name}: countermeasures for {', '.join(domains)}",
                     details={"recommendation_id": rec.id, "domains": domains, "cascade_eta_min": eta})
        aegis_llm.submit(self.llm_enrich_recommendation, crew_member, rec, aegis_trends.as_findings(fc))
        return {"started": True, "protocol": "PREVENT-1", "recommendation_id": rec.id,
                "domains": domains, "actions": rec.actions, "cascade_eta_min": eta}

    def llm_explain_prediction(self, crew_member: CrewMember, fc: dict):
        """DeepSeek: why AEGIS forecasts a cascade, and the one preventive step to take now (background)."""
        role = getattr(crew_member.role, "value", crew_member.role).replace("_", " ")
        parts = []
        for t in fc["metrics"].values():
            if t["out_of_range"]:
                state = "already out of range"
            elif t["eta_min"] is not None:
                state = f"{t['direction']}, forecast to cross {t['threshold']} in ~{t['eta_min']:.0f} min"
            else:
                state = t["direction"]
            parts.append(f"{t['agent']} {t['label']} {t['value']} {t['unit']} ({state})")
        prompt = (
            f"PREDICT forecast for the {role}, mission day {crew_member.mission_day}. Trends over the last "
            f"2 minutes of telemetry: {'; '.join(parts)}.\n"
            f"AEGIS forecasts a {aegis_trends.CASCADE_DOMAINS}-domain CASCADE in ~{fc['cascade_eta_min']:.0f} min "
            "if these trends continue.\nIn at most 40 words, tell mission control which trend matters most and "
            "the single preventive step to take now, before the cascade."
        )
        text = aegis_llm.chat(prompt, persona="AEGIS", words=40)
        if text:
            aegis_trends.NOTES[crew_member.id] = {"text": text, "at": datetime.utcnow().isoformat()}
            self._log_analysis(f"DeepSeek PREDICT note for {crew_member.id}", {"chars": len(text)})

    def rearm_cascades(self):
        """Allow cascades to fire again (called when a new scenario starts, e.g. cascade_demo)."""
        self._cascade_fired.clear()
        self._predict_fired.clear()
        aegis_trends.NOTES.clear()
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
                self.page_on_call(crew_member, cascade_alert, kind="cascade",
                                  detail=f"{len(agents_with_warnings)} systems out of range together: "
                                         f"{', '.join(_PLAIN_DOMAIN.get(a, a.lower()) for a in sorted(agents_with_warnings))}")
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
    
    # ── Phone alarm: AEGIS calls the on-call flight surgeon (aegis_notify, Twilio) ─────────────────────
    def page_on_call(self, crew_member: CrewMember, alert: Alert, kind: str, detail: str = ""):
        """
        A critical or cascade alert was just raised. In the background, AEGIS (DeepSeek, with the
        call_flight_surgeon tool) decides on and words the call; if the model is off, fails or doesn't call,
        the rules place the call anyway, so a real emergency never depends on the AI. Names stay local:
        the model only sees the role, and the crew name is added to the spoken message here.
        """
        role = getattr(crew_member.role, "value", crew_member.role).replace("_", " ")
        plan = {"cascade": "the recovery plan", "protect": "the medical response"}.get(kind, "a response")
        if "SHELTER" in (alert.title or "").upper() or getattr(alert.source, "value", alert.source) == "solaris":
            plan = "the storm-shelter plan"
        rule_msg = (f"Alert for {crew_member.name}, the {role}. {_spoken(detail or alert.title)}. "
                    f"AEGIS recommends {plan}. Please confirm it on the dashboard.")
        key = f"{crew_member.id}:{kind}:{plan}"

        def _work():
            msg, source = rule_msg, "rules"
            if aegis_llm.enabled():
                prompt = (f"An alert was just raised. Crew member: the {role}. Alert: {_spoken(alert.title)}. "
                          f"Reading: {_spoken(detail)}. Recommended: {plan}.\n"
                          f"{self._crew_context(crew_member)}\n"
                          "Decide whether the on-call flight surgeon must be phoned now. If yes, call the "
                          "call_flight_surgeon tool with a short spoken message (who by role, what is wrong with the "
                          "reading, what AEGIS recommends). Use only the numbers given above.")
                res = aegis_llm.chat_tools(prompt, [aegis_notify.CALL_TOOL], persona="AEGIS", mode="report")
                tc = next((t for t in (res or {}).get("tool_calls", []) if t.get("name") == "call_flight_surgeon"), None)
                if tc and str(tc["arguments"].get("message", "")).strip():
                    ai_msg = " ".join(str(tc["arguments"]["message"]).split())
                    # The same grounding rule as everything else: every number must come from the data
                    if aegis_verify.check_numbers(ai_msg, prompt)["ok"]:
                        msg, source = f"Alert for {crew_member.name}. {ai_msg}", "AEGIS tool call"
            rec = aegis_notify.call(msg, key=key, reason=kind, source=source)
            self._log_analysis(f"On-call phone: {rec['status']} ({source}) for {crew_member.name}",
                               {"alert_id": alert.id, "status": rec["status"], "detail": rec["detail"],
                                "source": source})

        threading.Thread(target=_work, daemon=True, name="aegis-page").start()

    def page_from_chat(self, question: str, member: CrewMember) -> Optional[dict]:
        """'Call the doctor' / 'page the flight surgeon' from the voice terminal: AEGIS uses the tool."""
        role = getattr(member.role, "value", member.role).replace("_", " ")
        ctx = self._crew_context(member)
        prompt = (f"Mission control says: \"{question}\"\nFocus crew member: {ctx}\n"
                  "If they are asking you to phone or page the flight surgeon, call the call_flight_surgeon tool with "
                  "a short spoken message about this crew member (by role, using only the numbers above), then "
                  "reply in one sentence confirming. Otherwise just answer.")
        res = aegis_llm.chat_tools(prompt, [aegis_notify.CALL_TOOL], persona="AEGIS", mode="chat")
        tc = next((t for t in (res or {}).get("tool_calls", []) if t.get("name") == "call_flight_surgeon"), None)
        if not tc:
            if res is None:                              # model unavailable: rules place the call as asked
                msg = f"Mission control asked AEGIS to call about {member.name}, the {role}. {_spoken(ctx)}"
                rec = aegis_notify.call(msg, key=f"{member.id}:chat:{int(time.time() // 60)}", reason="requested", source="rules")
                return {"answer": f"Calling the flight surgeon about the {role} now." if rec["status"] in ("called", "logged")
                        else f"I couldn't place the call: {rec['detail']}.", "call": rec}
            return None
        ai_msg = " ".join(str(tc["arguments"].get("message", "")).split())
        if not aegis_verify.check_numbers(ai_msg, prompt)["ok"]:
            ai_msg = f"The {role} needs your attention. {_spoken(ctx)}"
        rec = aegis_notify.call(f"Request from mission control about {member.name}. {ai_msg}",
                                key=f"{member.id}:chat:{int(time.time() // 60)}", reason="requested", source="AEGIS tool call")
        said = (res.get("text") or "").strip()
        answer = (said or f"Calling the flight surgeon about the {role} now.") if rec["status"] in ("called", "logged") \
            else f"I tried to call the flight surgeon but it didn't go through: {rec['detail']}."
        return {"answer": answer, "call": rec}

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
