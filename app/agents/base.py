"""AEGIS base agent (split from root agents.py, 2026-11-23).

Shared helpers for every specialist: BaseAgent, VITAL_THRESHOLDS, _spoken,
_PLAIN_DOMAIN. The two specialist lookups in _crew_context are imported
lazily inside that method: a top-level import would be circular, because
PULSEAgent and GENEAgent subclass BaseAgent.
"""

import os
import json
import time
from datetime import datetime, timedelta
from typing import Optional
import uuid

from app.models import (
    CrewMember, Vitals, HealthStatus, AlertSeverity,
    Recommendation, RecommendationPillar, RecommendationStatus,
    Alert, AgentType, EventType
)
from app.services.rag import get_knowledge_base
from app import database as db
from app.services import llm as aegis_llm
from app.services import trends as aegis_trends
from app.services import verify as aegis_verify
from app.services import notify as aegis_notify
from app import mission as aegis_mission
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
    # Radiation: not a fixed number any more; judged against the mission background (aegis_mission.py)
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
        # Dashboard-metrics scope: only the Live Vitals metrics go to the model (the same readings as the panel)
        parts = []
        if v:
            parts.append(f"heart rate {v.heart_rate} bpm")
            parts.append(f"SpO2 {v.spo2}%")
            from app.agents.pulse import PULSEAgent as _PULSEAgent  # lazy: avoids a circular import
            parts += _PULSEAgent.monitored_status(v)    # blood pressure, temperature, respiration (PULSE, simulated)
            if v.sleep_quality is not None:
                parts.append(f"sleep quality {v.sleep_quality:.0f}/100")
            if v.radiation_dose_24h is not None:
                th = aegis_mission.radiation_thresholds()
                parts.append(f"radiation {v.radiation_dose_24h:.2f} mSv in last 24h (normal background on this "
                             f"{aegis_mission.profile()['label'].lower()} is {th['background']:.2f} mSv/day; "
                             f"SOLARIS warns at {th['warning']:.2f} and orders the storm shelter at {th['shelter']:.1f})")
            from app.agents.gene import GENEAgent as _GENEAgent  # lazy: avoids a circular import
            markers = _GENEAgent.estimate_markers(crew_member)
            if markers:
                parts.append(f"IL-6 {markers['il6']} pg/mL (modelled, not measured; this crew member's pre-flight "
                             f"baseline {markers['baseline']} pg/mL, GENE flags at {markers['personal_limit']})")
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
                f"cumulative radiation {crew_member.cumulative_radiation:.0f} mSv "
                f"(NASA career limit {aegis_mission.CAREER_LIMIT_MSV:.0f} mSv). "
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
                                             passage_texts, facts, weak, verbatim_actions=True)
            history.append({"data": data, "raw": None if data else raw, "result": result})
            if result["ok"]:
                break
            attempt_prompt = (prompt + "\n\nYour previous answer FAILED an automatic fact check:\n- "
                              + "\n- ".join(result["problems"][:6])
                              + "\nRewrite it. Use only numbers that appear above. Cite [n] only where passage n "
                                "really says that; if no passage supports a sentence, leave it uncited. "
                                "Give every action the number of its approved menu step. "
                                "No medicines or doses.")
        if return_trace:                              # evaluation mode: no database write
            return {"history": history, "menu": list(rec.actions), "passages": passage_texts,
                    "facts": facts, "weak": weak, "title": rec.title}
        verification = aegis_verify.summary(result) if result.get("checks") else \
            {"ok": False, "problems": result["problems"]}
        verification["attempts"] = tries
        vline = f"Verification: {json.dumps(verification)}"
        if result["ok"]:
            # The card shows the APPROVED steps verbatim (the model only chooses and orders them); its
            # plain-language wording stays in the explanation, so no instruction reaches the crew unreviewed.
            menu_items, actions = list(rec.actions), []
            for a in data["actions"]:
                try:
                    k = int(a.get("step"))
                except (TypeError, ValueError):
                    continue
                if 1 <= k <= len(menu_items) and menu_items[k - 1] not in actions:
                    actions.append(menu_items[k - 1])
            assessment, actions = data["assessment"], actions or menu_items
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
        if not text:
            return
        # Same grounding rules as recommendations: numbers bound to the readings, no medicine or dose,
        # spec units only, no instruction outside the alert's own protocol text. Otherwise the alert keeps
        # only its rule-based text.
        context = f"{self._crew_context(crew_member)}\n{alert.title}. {alert.message}\n{self._findings_text(findings)}"
        check = aegis_verify.verify_note(text, context, menu=[alert.message or "", alert.title])
        if check["ok"]:
            db.update_alert_message(alert.id, f"{alert.message}\n\n{aegis_llm.AI_MARKER} ({self.agent_type.value}): {text}")
        self._log_analysis(f"Model assessment {'added to' if check['ok'] else 'rejected for'} alert {alert.id}",
                           {"model": aegis_llm.model(), "alert_id": alert.id, "verified": check["ok"],
                            "problems": check["problems"][:4]})

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
