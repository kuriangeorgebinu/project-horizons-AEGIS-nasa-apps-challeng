"""AEGIS coordinator agent (split from root agents.py, 2026-11-23).

Orchestrates the specialist agents; also owns the module-level
get_coordinator / analyze_crew_member / analyze_all_crew helpers.
"""

import time
from datetime import datetime, timedelta
from typing import Optional
import uuid
import threading
import re as _re_mod

from app.models import (
    CrewMember, HealthStatus, AlertSeverity,
    Recommendation, RecommendationPillar, RecommendationStatus,
    Alert, AgentType, EventType
)
from app import database as db
from app.services import llm as aegis_llm
from app.services import trends as aegis_trends
from app.services import verify as aegis_verify
from app.services import notify as aegis_notify
from app import mission as aegis_mission
from .base import BaseAgent, _PLAIN_DOMAIN, _spoken
from app.agents.pulse import PULSEAgent
from app.agents.solaris import SOLARISAgent
from app.agents.gene import GENEAgent
from app.agents.mirror import MIRRORAgent
from app.agents.osseo import OsseoAgent

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
            # Dashboard health domains plus OSSEO's cohort-reference and synthetic DXA context
            PULSEAgent(),      # Heart rate, SpO2
            SOLARISAgent(),    # Radiation exposure (24h dose + mission total)
            GENEAgent(),       # IL-6
            MIRRORAgent(),     # Sleep
            OsseoAgent(),      # Bone density comparisons; demo-only, not a live cascade domain
        ]
        # Track agent statuses for cascade detection (T1)
        self._agent_status_history = {}
        # Crew members who already got a cascade in the current scenario run (one cascade per run)
        self._cascade_fired = set()
        # Crew members who already got a PREDICT (pre-cascade) warning in the current scenario run
        self._predict_fired = set()
        # One lock for all coordinator state (cascade / forecast memory, history, stale-recommendation counters):
        # the background analysis loop, manual /api/analyze, prevention and resets can run at the same time.
        self._lock = threading.RLock()
        self._rec_misses = {}

    def analyze_crew_member(self, crew_member: CrewMember) -> dict:
        with self._lock:
            return self._analyze_crew_member(crew_member)

    def _analyze_crew_member(self, crew_member: CrewMember) -> dict:
        """
        Run all specialist agents on a crew member and aggregate results.
        Includes cascade detection (T1), deduplication (T8), and alert routing (T9).
        """
        all_findings = []
        all_recommendations = []
        errored_agents = set()            # a specialist that failed this pass can't vouch that its readings are fine

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
                errored_agents.add(agent.agent_type)
                self._log_analysis(
                    f"Error in {agent.agent_type.value}: {str(e)}",
                    {"error": str(e)}
                )

        self._resolve_stale_recommendations(crew_member, all_findings, all_recommendations, errored_agents)
        # T8 dedupe: don't recreate what the crew already has or already decided on. Pending and accepted
        # cards block a duplicate while the condition lasts; a deferred card blocks it until its 4-hour timer runs
        # out; a dismissed one for 30 minutes.
        existing_titles = db.active_recommendation_titles(crew_member.id)
        
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
        # Exception: the crew member a one-person demo is happening to during a solar storm. Their cascade is
        # their own (heart, sleep, inflammation on top of the storm), so it is still detected and raised.
        try:
            from app.simulator import get_simulator as _gs
            if protect_active and _gs().spe_on() and _gs().personal_target() == crew_member.id \
                    and _gs().config.scenario in ("cascade_demo", "buildup_demo"):
                protect_active = False
        except Exception:
            pass
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
    
    _CONCERN = ("warning", "critical", "caution")

    def _resolve_stale_recommendations(self, crew_member: CrewMember, findings: list[dict],
                                       new_recs: list[Recommendation], errored_agents: set):
        """Close recommendations whose reason has gone, based on the agent's readings this pass:
          - the agent failed this pass               -> leave everything alone (no unchecked resolving);
          - same title raised again                  -> still needed;
          - same agent raised a different card        -> the old one is superseded (e.g. severity changed);
          - the agent still reports an out-of-range
            reading in its domain (PREVENT/PROTECT)   -> still needed, even if the title wording changed;
          - otherwise, on two passes in a row         -> resolved: the reading is back within range.
        Accepted plans finish the same way, deferred cards end when their 4-hour timer runs out."""
        now_utc = datetime.utcnow()
        titles_now = {r.title for r in new_recs}
        by_agent_now: dict = {}
        for r in new_recs:
            by_agent_now.setdefault(r.agent, set()).add(r.title)
        concerned = set()
        for f in findings:
            if f.get("type") in self._CONCERN:
                a = self._metric_to_agent(f.get("metric", ""))
                if a:
                    concerned.add(a)
        db.expire_deferred_recommendations(crew_member.id)
        for r in db.get_recommendations(crew_id=crew_member.id, limit=200):
            if r.status not in (RecommendationStatus.PENDING, RecommendationStatus.ACCEPTED):
                continue
            if r.agent == AgentType.AEGIS or r.agent in errored_agents:
                self._rec_misses.pop(r.id, None)
                continue
            if r.title in titles_now:
                self._rec_misses.pop(r.id, None)
                continue
            other = by_agent_now.get(r.agent, set()) - {r.title}
            reason = None
            if other and r.status == RecommendationStatus.PENDING:
                reason = f"superseded by '{sorted(other)[0]}'"
            elif r.pillar != RecommendationPillar.PREDICT and r.agent.value in concerned:
                self._rec_misses.pop(r.id, None)        # still out of range in this agent's domain
                continue
            created = r.created_at.replace(tzinfo=None) if r.created_at else now_utc
            if (now_utc - created).total_seconds() < 60:
                continue                                 # a brand-new card gets a minute before it can close
            self._rec_misses[r.id] = self._rec_misses.get(r.id, 0) + 1
            if self._rec_misses[r.id] < 2:              # two passes in a row: no flapping at a threshold
                continue
            self._rec_misses.pop(r.id, None)
            reason = reason or ("plan finished: the reading is back within range" if r.status == RecommendationStatus.ACCEPTED
                                else "the reading that raised it is back within range")
            db.update_recommendation_status(r.id, RecommendationStatus.COMPLETED, reason=reason)
            self._log_analysis(f"Resolved for {crew_member.name}: '{r.title}' ({reason})",
                               {"recommendation_id": r.id, "auto_resolved": True, "reason": reason})

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
                # OSSEO's aBMD comparison is illustrative context, not a validated real-time domain;
                # keep its attribution for reporting but never let it contribute to a cascade.
                if agent_name and agent_name != "OSSEO":
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
            "bone_density": "OSSEO",
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
                "PULSE": "Hey, PULSE here! Heart rate, oxygen, blood pressure, temperature and breathing are my thing. Who should I check on?",
                "MIRROR": "Hi, MIRROR here. I look after sleep and how everyone's holding up. What would you like to know?",
                "OSSEO": "OSSEO here. I show historical study cohort means and synthetic regional DXA examples; neither is a measurement for you, and I do not provide a live bone monitor or clinical alert.",
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
                        "source": "model" if paged["call"]["source"] != "rules" else "rules",
                        "model": aegis_llm.model(), "agents_consulted": [], "findings": [],
                        "call": {k: paged["call"][k] for k in ("status", "detail", "source", "at")},
                        "latency_s": round(time.time() - t0, 2)}

        # 1. Live findings from the relevant specialist(s)
        consulted, findings = [], []
        # OSSEO's "context" finding (historical cohort reference + synthetic DXA examples) is not a health reading:
        # it is used only when the question is about bones or is put to OSSEO, so it never replaces AEGIS's
        # summary of the person's actual readings.
        bone_q = agent_name == "OSSEO" or bool(_re_mod.search(r"\b(bones?|dxa|bmd|osseo|skelet\w*|osteopor\w*|bone density)\b",
                                                         question or "", _re_mod.I))
        for a in self.specialists:
            a_name = a.agent_type.value.upper()
            if agent_name == "AEGIS" or a_name == agent_name:
                if a_name == "OSSEO" and not bone_q:
                    continue
                consulted.append(a.agent_type.value)
                try:
                    for f in a.analyze(member):
                        findings.append({**f, "agent": a.agent_type.value})
                except Exception as e:
                    self._log_analysis(f"Q&A: {a_name} analysis failed: {e}", {"error": str(e)})

        # 2. Context: open alerts, crew overview, knowledge base
        open_alerts = db.get_alerts(unacknowledged_only=True, limit=50)
        role = lambda c: getattr(c.role, "value", c.role).replace("_", " ")
        # An open alert is not proof the problem is current: say whether that person's readings are abnormal NOW,
        # so the "top concern" in an answer reflects the current readings, not whatever was true when it was raised
        _now_cache = {}
        def _abnormal_now(cid):
            if cid not in _now_cache:
                c = by_id.get(cid); hits = 0
                for sp in (self.specialists if c else []):
                    try:
                        hits += sum(1 for f in sp.analyze(c) if f.get("type") in ("warning", "critical"))
                    except Exception:
                        pass
                _now_cache[cid] = hits > 0
            return _now_cache[cid]
        alerts_txt = "; ".join(
            f"{a.title.replace('🔺', '').strip()} ({role(by_id[a.crew_member_id]) if a.crew_member_id in by_id else 'crew'}"
            f"{'' if _abnormal_now(a.crew_member_id) else ', readings now back in range, awaiting a person to confirm'})"
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
            "If something should be done, only mention an action that already appears in the findings or open alerts above; "
            "never give new medical instructions, medicines, doses or procedures (say the flight surgeon will decide). "
            "Use only these units with numbers: mSv, pg/mL, bpm, percent, mmHg, °C, breaths per minute, hours, minutes, days. "
            "If you don't have the data for it, say so simply and share what you do know."
        )
        answer = aegis_llm.chat(prompt, persona=agent_name, words=60, mode="chat", timeout=20)   # 20 s total, retries included
        source = "model"
        # Grounding: every number in a spoken answer must come from the data in the prompt (aegis_verify).
        num_check, first_check = None, None
        # Also: only whitelisted units, and no medicine or dose that is not in the data given to the model.
        voice_menu = [f.get("message", "") for f in findings] + [a for a in alerts_txt.split("; ") if a and a != "none"] + \
                     ["Talk to the flight surgeon", "Keep monitoring the readings", "Rest", "Drink water"]
        def _voice_check(txt):
            n = aegis_verify.check_numbers(txt, prompt)
            u = aegis_verify.check_units(txt)
            m = aegis_verify.check_medicine(txt, prompt)
            pa = aegis_verify.check_prose_actions(txt, voice_menu)
            bad = n["unsupported"] + u["unsupported"] + m["unsupported"] + pa["unsupported"]
            return {**n, "units_ok": u["ok"], "medicine_ok": m["ok"], "actions_ok": pa["ok"],
                    "unsupported": bad, "ok": not bad}
        if answer:
            num_check = first_check = _voice_check(answer)
            if not num_check["ok"]:
                retry = aegis_llm.chat(prompt + "\n\nYour previous reply failed an automatic check ("
                                       + ", ".join(num_check["unsupported"]) + "). Answer again using only numbers "
                                       "that appear above (or none), only the allowed units, no medicines or doses, "
                                       "and no instruction that isn't already in the findings or alerts.",
                                       persona=agent_name, words=60, mode="chat", timeout=15)
                num_check = _voice_check(retry or "")
                answer = retry if (retry and num_check["ok"]) else None
        if not answer:
            source = "rules"
            if findings:
                answer = f"Here's what I'm seeing for the {role(member)}: " + "; ".join(
                    f.get("message", "").rstrip(".") for f in findings[:3]) + "."
                asked = [x for x in PULSEAgent.monitored_status(member.vitals)
                         if _re_mod.search({"blood": r"blood pressure|\bbp\b|systolic|diastolic",
                                            "temperature": r"temperature|\btemp\b|fever",
                                            "respiration": r"respirat|breath"}[x.split()[0]], q_lower)]
                if asked:
                    answer += " " + ("; ".join(asked)).capitalize() + "."
            else:
                answer = (f"Good news: everything I track for the {role(member)} looks normal right now. "
                          + self._rules_readout(agent_name, member))

        self._log_analysis(f"Q&A answered by {agent_name} ({source}) for {member.id}",
                           {"question": question[:200], "source": source})
        return {
            "answer": answer,
            "agent": agent_name,
            "crew_member_id": member.id,
            "source": source,
            "model": aegis_llm.model() if source == "model" else None,
            "agents_consulted": consulted,
            "findings": [f.get("message") for f in findings],
            "numbers_verified": bool(num_check and num_check["ok"]) if source == "model" else None,
            "latency_s": round(time.time() - t0, 2),
            **({"_context": prompt, "_first_check": first_check} if return_context else {}),
        }

    def _rules_readout(self, agent_name: str, member) -> str:
        """Rule-based answers quote the agent's own live readings (with units), not just "all normal"."""
        v = member.vitals
        if v is None:
            return ""
        th = aegis_mission.radiation_thresholds()
        parts = []
        if agent_name in ("SOLARIS", "AEGIS") and v.radiation_dose_24h is not None:
            parts.append(f"Radiation: {v.radiation_dose_24h:.2f} mSv in the last 24 hours against a normal "
                         f"{th['background']:.2f} mSv/day background, and {member.cumulative_radiation or 0:.0f} mSv "
                         f"for the mission so far, out of the {aegis_mission.CAREER_LIMIT_MSV:.0f} mSv career limit.")
        if agent_name in ("PULSE", "AEGIS"):
            parts.append(f"Heart rate {v.heart_rate} bpm, blood oxygen {v.spo2:.0f} percent.")
            extra = PULSEAgent.monitored_status(v)
            if extra:
                parts.append(("; ".join(extra)).capitalize() + ".")
        if agent_name in ("MIRROR", "AEGIS") and v.sleep_quality is not None:
            parts.append(f"Sleep score {v.sleep_quality:.0f} out of 100.")
        if agent_name in ("GENE",):
            parts.append("The modelled IL-6 inflammation marker is inside this person's own normal range.")
        if agent_name == "OSSEO":
            parts.append("OSSEO shows historical total-body DXA cohort means and synthetic regional site examples; these are not measurements for this crew member or NASA-wide norms, and no validated real-time in-flight bone monitor or clinical alert is available.")
        return " ".join(parts)

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
                f = GENEAgent.il6_findings(il6, aegis_mission.il6_baseline(base.id))
            else:
                # Long-range predictions (e.g. day-365 dose projection) aren't part of a what-if on readings
                f = [x for x in a.analyze(hypo) if x.get("type") not in ("prediction", "context")]
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
            source = "model" if explanation else "rules"
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
            self._log_analysis(f"Model backfill queued for {queued} recommendations", {"queued": queued})
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
        if not text:
            return
        menu = ["Enhanced monitoring", "Reduced workload", "Hydration and antioxidant support",
                "Daily biomarker check-ins", "Follow the recovery plan RECOVER-3"]
        context = f"{self._crew_context(crew_member)}\n{alert.message}\n{lines}"
        check = aegis_verify.verify_note(text, context, menu=menu)
        if check["ok"]:
            db.update_alert_message(alert.id, f"{alert.message}\n\n{aegis_llm.AI_MARKER} (AEGIS): {text}")
        self._log_analysis(f"Model cascade explanation {'added to' if check['ok'] else 'rejected for'} {alert.id}",
                           {"model": aegis_llm.model(), "alert_id": alert.id, "verified": check["ok"],
                            "problems": check["problems"][:4]})

    # PREVENT-1: countermeasures per domain, started when mission control acts on a PREDICT forecast
    _PREVENT_ACTIONS = {
        "MIRROR": "Sleep: block out a full 8 hours tonight, dim the lights early and push late tasks to tomorrow",
        "SOLARIS": "Radiation: work from the better-shielded part of the ship for the next day",
        "GENE": "Inflammation: take a recovery day with rest, plenty of water and good food, plus a quick blood check tomorrow",
        "PULSE": "Heart: go easy on exercise today, drink a big glass of water, and check heart rate every 30 minutes",
    }

    def start_prevention(self, crew_member: CrewMember) -> dict:
        with self._lock:
            return self._start_prevention(crew_member)

    def _start_prevention(self, crew_member: CrewMember) -> dict:
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
        if not text:
            return
        check = aegis_verify.verify_note(text, prompt, menu=list(self._PREVENT_ACTIONS.values()) +
                                         ["Start the prevention plan PREVENT-1"])
        if check["ok"]:
            aegis_trends.NOTES[crew_member.id] = {"text": text, "at": datetime.utcnow().isoformat()}
        self._log_analysis(f"Model PREDICT note for {crew_member.id} {'added' if check['ok'] else 'rejected'}",
                           {"chars": len(text), "verified": check["ok"], "problems": check["problems"][:4]})

    def rearm_cascades(self):
        """Allow cascades to fire again (called when a new scenario starts, e.g. cascade_demo)."""
        with self._lock:
            self._rearm_cascades()

    def _rearm_cascades(self):
        self._cascade_fired.clear()
        self._predict_fired.clear()
        aegis_trends.NOTES.clear()
        self._agent_status_history.clear()

    def reset_state(self):
        """Clean demo start: forget cascade / forecast / stale-recommendation memory (the DB is cleared separately)."""
        with self._lock:
            self._rearm_cascades()
            self._rec_misses = {}

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
            # Re-read each person just before analysing them: one pass can take several seconds, and a reading
            # taken at the start of the pass may already be stale (e.g. a storm-shelter alert after the storm ended)
            result = self.analyze_crew_member(db.get_crew_member(member.id) or member)
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
