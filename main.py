"""
AEGIS FastAPI Backend
NASA Space Apps Challenge 2026

Main API server for the AEGIS health monitoring system.
Provides endpoints for:
- Crew state management
- AI recommendations (PREDICT/PREVENT/RESPOND)
- RAG knowledge base queries
- Real-time vitals simulation
- Event logging
"""

# Load .env file before anything else
from dotenv import load_dotenv
load_dotenv()

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from models import (
    CrewMember, CrewState, Vitals, HealthStatus,
    Recommendation, RecommendationStatus, RecommendationPillar,
    Alert, AlertSeverity, AgentType,
    Event, EventType,
    QueryRequest, QueryResponse, RecommendationAction,
    AnalysisRequest, AnalysisResponse,
    SimulationConfig, SimulationState
)
import database as db
from aegis_rag import get_knowledge_base
from agents import get_coordinator, analyze_crew_member, analyze_all_crew
from simulator import get_simulator
import aegis_trends
import aegis_llm
import aegis_notify
import aegis_mission


# ─────────────────────────────────────────────────────────────────────────────
# Application Lifecycle
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown."""
    # Startup
    print("🚀 AEGIS Backend Starting...")
    
    # Initialize database
    db.init_database()
    
    # Initialize default crew if empty
    crew = db.get_all_crew()
    if not crew:
        print("📋 Initializing default crew...")
        db.init_default_crew()
    
    # Initialize knowledge base (warm up embeddings)
    print("🧠 Loading knowledge base...")
    kb = get_knowledge_base()
    stats = kb.get_stats()
    print(f"   📚 {stats['total']} documents indexed")
    
    # Start simulator as background task
    print("🔬 Starting vitals simulator...")
    simulator = get_simulator()
    simulator_task = asyncio.create_task(simulator.run())
    
    # E2E: run the agents automatically so alerts/cascades appear without a manual /api/analyze call.
    # Fast (10 s) while a scenario is active, slow (120 s) in nominal operations.
    async def auto_analyze_loop():
        await asyncio.sleep(15)  # let the simulator produce a few ticks first
        # One-off: give older pending recommendations their DeepSeek assessment (background, fail-safe)
        try:
            queued = await asyncio.to_thread(get_coordinator().backfill_recommendation_assessments)
            if queued:
                print(f"🧠 DeepSeek backfill queued for {queued} recommendations")
        except Exception as e:
            print(f"DeepSeek backfill error: {e}")
        last_run = 0.0
        last_scenario = None
        loop = asyncio.get_running_loop()
        while True:
            scenario = simulator.config.scenario
            interval = 10 if scenario else 120
            now = loop.time()
            if scenario is None and last_scenario is not None:
                # Back to nominal: smoothed vitals (EWMA) still carry the scenario for a few ticks,
                # so wait a full interval before analysing, or we'd raise stale alerts (e.g. SHELTER after an SPE).
                last_run = now
                last_scenario = None
                await asyncio.sleep(5)
                continue
            # Run on schedule, or immediately when a scenario starts (e.g. demo just started)
            if now - last_run >= interval or scenario != last_scenario:
                try:
                    await asyncio.to_thread(analyze_all_crew)
                except Exception as e:
                    print(f"Auto-analysis error: {e}")
                last_run = loop.time()
                last_scenario = scenario
            await asyncio.sleep(5)
    
    print("🤖 Starting auto-analysis loop...")
    analyze_task = asyncio.create_task(auto_analyze_loop())
    
    print("✅ AEGIS Backend Ready!")
    print("   Dashboard: http://localhost:8000")
    print("   API Docs:  http://localhost:8000/docs")
    
    yield
    
    # Shutdown - properly cancel the simulator task
    print("🛑 AEGIS Backend Shutting Down...")
    analyze_task.cancel()
    simulator.stop()
    simulator_task.cancel()
    try:
        await simulator_task
    except asyncio.CancelledError:
        pass
    print("   Simulator stopped.")


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI App
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="AEGIS API",
    description="Autonomous Expert Group for Integrated Space Health - NASA Space Apps 2026",
    version="1.0.0",
    lifespan=lifespan
)

# CORS for dashboard connection
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your dashboard origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────────────────
# Health & Status Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    """API root - system status."""
    return {
        "system": "AEGIS",
        "version": "1.0.0",
        "status": "operational",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    simulator = get_simulator()
    kb = get_knowledge_base()
    
    return {
        "status": "healthy",
        "components": {
            "database": "ok",
            "knowledge_base": kb.get_stats(),
            "simulator": {
                "running": simulator.running,
                "mission_day": int(simulator.mission_day)
            },
            "llm": {k: v for k, v in aegis_llm.status().items() if k in ("enabled", "provider", "model")}
        },
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/api/mission")
async def mission():
    """Mission profile: normal radiation background, SOLARIS thresholds, NASA limits, data source."""
    return aegis_mission.mission_info()


@app.post("/api/demo/reset")
async def demo_reset():
    """
    Clean, repeatable demo start (Step 0): nominal scenario, fixed crew baselines, re-seeded simulator,
    no alerts / recommendations / events / vitals history, agents' cascade and forecast memory cleared,
    phone-alarm log cleared. Use before every rehearsal or recording (dashboard: Shift+R).
    """
    simulator = get_simulator()
    simulator.reset_demo()
    removed = db.reset_demo_data()
    get_coordinator().reset_state()
    aegis_notify.reset_log()
    db.log_event(EventType.SYSTEM, "Demo reset: clean start", details={"removed": removed})
    return {"reset": True, "removed": removed, "mission_day": simulator.mission_day}


@app.get("/api/notify/status")
async def notify_status():
    """Phone alarm: configured or not (number masked), limits, and the recent call log."""
    return aegis_notify.status()


@app.post("/api/notify/test")
async def notify_test():
    """Place one test call to the on-call phone (or log it, if Twilio isn't configured yet)."""
    rec = await asyncio.to_thread(
        aegis_notify.call,
        "This is a test of the AEGIS phone alarm. If you can hear this, critical crew alerts will reach this phone.",
        f"test:{datetime.utcnow().strftime('%H%M%S')}", "test", "manual test")
    return rec


@app.get("/api/llm/status")
async def llm_status():
    """DeepSeek reasoning layer: enabled?, model, call counts, last latency / error (never the key)."""
    return aegis_llm.status()


@app.post("/api/llm/test")
async def llm_test():
    """One live DeepSeek round-trip, to check the key and model from the dashboard or /docs."""
    import asyncio as _asyncio
    text = await _asyncio.to_thread(
        aegis_llm.chat,
        "Crew member crew-001 (role: commander), mission day 54, radiation_dose_24h=4.2 mSv (2.3x the 1.84 mSv background), "
        "stress_index=72, sleep_quality=48. In one sentence, state the main combined risk.",
        "AEGIS", 30)
    return {"success": text is not None, "answer": text, "status": aegis_llm.status()}


from pydantic import BaseModel as _BaseModel, Field as _Field


class AskRequest(_BaseModel):
    question: str = _Field(..., min_length=2, max_length=500)
    agent: str = _Field("AEGIS", description="AEGIS, SOLARIS, GENE, PULSE or MIRROR")
    crew_id: Optional[str] = _Field(None, description="Focus crew member, e.g. crew-001 (a surname in the question overrides it)")


@app.post("/api/ask")
async def ask_agent(req: AskRequest):
    """
    Voice Terminal: ask an agent a question. The agent runs its live analysis on the crew member's
    current vitals, adds open alerts + knowledge-base context, and DeepSeek answers in that agent's
    voice (rule-based answer if the model is unavailable).
    """
    agent = req.agent.strip().upper()
    if agent not in {"AEGIS", "SOLARIS", "GENE", "PULSE", "MIRROR"}:
        raise HTTPException(status_code=400, detail="agent must be AEGIS, SOLARIS, GENE, PULSE or MIRROR")
    return await asyncio.to_thread(get_coordinator().answer_question, req.question.strip(), agent, req.crew_id)


class WhatIfRequest(_BaseModel):
    heart_rate: float = _Field(..., ge=30, le=220)
    spo2: float = _Field(..., ge=80, le=100)
    il6: float = _Field(..., ge=0, le=50)
    sleep_quality: float = _Field(..., ge=0, le=100)
    radiation_dose_24h: float = _Field(..., ge=0, le=100)     # up to solar-particle-event levels
    crew_id: Optional[str] = None
    explain: bool = True


@app.post("/api/whatif")
async def what_if(req: WhatIfRequest):
    """What-If simulator: run the real agent rules on hypothetical dashboard values (nothing is stored)."""
    values = req.model_dump(exclude={"crew_id", "explain"})
    return await asyncio.to_thread(get_coordinator().what_if, values, req.crew_id, req.explain)


@app.post("/api/llm/backfill")
async def llm_backfill(limit: int = 40, force: bool = False):
    """Queue DeepSeek assessments for pending recommendations that don't have one yet (force=true: rewrite all)."""
    queued = await asyncio.to_thread(get_coordinator().backfill_recommendation_assessments, limit, force)
    return {"queued": queued, "status": aegis_llm.status()}


# ─────────────────────────────────────────────────────────────────────────────
# Crew Endpoints
# ─────────────────────────────────────────────────────────────────────────────

_STATUS_RANK = {
    HealthStatus.NOMINAL: 0,
    HealthStatus.CAUTION: 1,
    HealthStatus.WARNING: 2,
    HealthStatus.CRITICAL: 3,
}


def _apply_alert_status(member: CrewMember, open_alerts: list) -> CrewMember:
    """H3: Raise crew status to reflect unacknowledged alerts for this member.

    CRITICAL alerts (incl. SHELTER) -> at least CRITICAL; HIGH alerts -> at least WARNING.
    Never lowers a status that is already worse.
    """
    mine = [a for a in open_alerts if a.crew_member_id == member.id]
    if not mine:
        return member
    target = member.status
    if any(a.severity == AlertSeverity.CRITICAL or "SHELTER" in (a.title or "").upper() for a in mine):
        target = HealthStatus.CRITICAL
    elif any(a.severity == AlertSeverity.HIGH for a in mine):
        target = HealthStatus.WARNING
    if _STATUS_RANK.get(target, 0) > _STATUS_RANK.get(member.status, 0):
        member.status = target
    return member


@app.get("/api/crew", response_model=list[CrewMember])
async def get_crew():
    """Get all crew members with current state."""
    open_alerts = db.get_alerts(unacknowledged_only=True, limit=200)
    return [_apply_alert_status(m, open_alerts) for m in db.get_all_crew()]


@app.get("/api/crew/state", response_model=CrewState)
async def get_crew_state():
    """Get complete crew state snapshot."""
    crew = db.get_all_crew()
    simulator = get_simulator()
    
    days = int(simulator.mission_day)
    hours = int((simulator.mission_day % 1) * 24)
    minutes = int(((simulator.mission_day % 1) * 24 % 1) * 60)
    
    return CrewState(
        crew=crew,
        mission_elapsed_time=f"Day {days}, {hours:02d}:{minutes:02d}:00",
        timestamp=datetime.utcnow()
    )


@app.get("/api/crew/{crew_id}", response_model=CrewMember)
async def get_crew_member(crew_id: str):
    """Get a specific crew member."""
    member = db.get_crew_member(crew_id)
    if not member:
        raise HTTPException(status_code=404, detail=f"Crew member {crew_id} not found")
    
    # H3: Elevate status if crew has unacknowledged high/critical alerts
    # (B1 fix: get_alerts() only accepts unacknowledged_only/limit; Alert has no .protocol)
    open_alerts = db.get_alerts(unacknowledged_only=True, limit=200)
    return _apply_alert_status(member, open_alerts)


@app.get("/api/crew/{crew_id}/biomarkers")
async def get_crew_biomarkers(crew_id: str):
    """GENE's modelled IL-6 (with personal baseline) for a crew member: the same values the GENE agent evaluates."""
    from agents import GENEAgent
    member = db.get_crew_member(crew_id)
    if not member:
        raise HTTPException(status_code=404, detail=f"Crew member {crew_id} not found")
    markers = GENEAgent.estimate_markers(member)
    if markers is None:
        raise HTTPException(status_code=404, detail="No vitals yet for this crew member")
    return {"crew_member_id": crew_id, **markers}


@app.get("/api/crew/{crew_id}/trends")
async def get_crew_trends(crew_id: str, series: bool = True):
    """PREDICT: per-metric trend (rising/falling/steady), slope, time-to-threshold forecast and
    ~10 min of history (series) for the dashboard's click-to-expand chart."""
    if not db.get_crew_member(crew_id):
        raise HTTPException(status_code=404, detail=f"Crew member {crew_id} not found")
    return await asyncio.to_thread(aegis_trends.crew_forecast, crew_id, series)


@app.post("/api/crew/{crew_id}/prevent")
async def start_prevention(crew_id: str):
    """PREDICT -> PREVENT: act on a forecast before the cascade. Records an accepted PREVENT-1 plan
    (one countermeasure per affected domain, RAG-grounded) and resets the crew member's cascade window."""
    member = db.get_crew_member(crew_id)
    if not member:
        raise HTTPException(status_code=404, detail=f"Crew member {crew_id} not found")
    result = await asyncio.to_thread(get_coordinator().start_prevention, member)
    if result.get("started"):
        # Demo telemetry: the countermeasures take effect and the drift reverses
        result["simulator_recovering"] = get_simulator().start_prevention(crew_id)
    return result


@app.get("/api/predict")
async def get_predictions():
    """PREDICT: forecasts for every crew member, and the most urgent predicted cascade ('top')."""
    return await asyncio.to_thread(aegis_trends.predict_all)


@app.put("/api/crew/{crew_id}/vitals")
async def update_crew_vitals(crew_id: str, vitals: Vitals):
    """Manually update crew member vitals (for testing)."""
    member = db.get_crew_member(crew_id)
    if not member:
        raise HTTPException(status_code=404, detail=f"Crew member {crew_id} not found")
    
    db.update_crew_vitals(crew_id, vitals)
    return {"status": "updated", "crew_id": crew_id}


@app.get("/api/crew/{crew_id}/vitals/history")
async def get_vitals_history(crew_id: str, hours: int = Query(default=24, le=168)):
    """Get vitals history for trend analysis."""
    member = db.get_crew_member(crew_id)
    if not member:
        raise HTTPException(status_code=404, detail=f"Crew member {crew_id} not found")
    
    history = db.get_vitals_history(crew_id, hours)
    return {
        "crew_id": crew_id,
        "hours": hours,
        "data_points": len(history),
        "history": history
    }


# ─────────────────────────────────────────────────────────────────────────────
# RAG Query Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/query", response_model=QueryResponse)
async def query_knowledge_base(request: QueryRequest):
    """Query the RAG knowledge base."""
    kb = get_knowledge_base()
    
    results = kb.query(
        request.query,
        collections=request.collections,
        n_results=request.n_results
    )
    
    context = kb.get_context_for_llm(request.query, n_results=request.n_results)
    
    return QueryResponse(
        query=request.query,
        results=results,
        context_for_llm=context
    )


@app.get("/api/query/countermeasures")
async def query_countermeasures(
    condition: str,
    n_results: int = Query(default=5, le=20)
):
    """Query countermeasures for a specific condition."""
    kb = get_knowledge_base()
    results = kb.query_countermeasures(condition, n_results=n_results)
    return {
        "condition": condition,
        "countermeasures": results
    }


@app.get("/api/query/risks")
async def query_risks(
    query: str,
    n_results: int = Query(default=5, le=20)
):
    """Query spaceflight health risks."""
    kb = get_knowledge_base()
    results = kb.query_risks(query, n_results=n_results)
    return {
        "query": query,
        "risks": results
    }


# ─────────────────────────────────────────────────────────────────────────────
# Recommendations Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/recommendations", response_model=list[Recommendation])
async def get_recommendations(
    crew_id: Optional[str] = None,
    status: Optional[RecommendationStatus] = None,
    pillar: Optional[RecommendationPillar] = None,
    limit: int = Query(default=50, le=200)
):
    """Get recommendations with optional filters."""
    return db.get_recommendations(
        crew_id=crew_id,
        status=status,
        pillar=pillar,
        limit=limit
    )


@app.get("/api/recommendations/pending", response_model=list[Recommendation])
async def get_pending_recommendations():
    """Get all pending recommendations."""
    return db.get_recommendations(status=RecommendationStatus.PENDING)


@app.post("/api/recommendations/{rec_id}/action")
async def action_recommendation(rec_id: str, action: RecommendationAction):
    """Take action on a recommendation (accept, defer, dismiss)."""
    success = db.update_recommendation_status(rec_id, action.action)
    
    if not success:
        raise HTTPException(status_code=404, detail=f"Recommendation {rec_id} not found")
    
    # Log the user action
    db.log_event(
        EventType.USER_ACTION,
        f"Recommendation {rec_id}: {action.action.value}",
        details={"recommendation_id": rec_id, "action": action.action.value, "notes": action.notes}
    )
    
    return {"status": "updated", "recommendation_id": rec_id, "new_status": action.action.value}


# ─────────────────────────────────────────────────────────────────────────────
# Analysis Endpoints (AI Agents)
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/analyze/{crew_id}")
async def analyze_crew_member_endpoint(crew_id: str):
    """Run AI analysis on a specific crew member."""
    try:
        result = analyze_crew_member(crew_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/analyze")
async def analyze_all_crew_endpoint():
    """Run AI analysis on all crew members."""
    results = analyze_all_crew()
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "crew_analyzed": len(results),
        "results": results
    }


@app.get("/api/analysis/summary")
async def get_analysis_summary():
    """Get summary of current health analysis across all pillars."""
    crew = db.get_all_crew()
    pending_recs = db.get_recommendations(status=RecommendationStatus.PENDING)
    
    # Count by pillar
    predict_count = len([r for r in pending_recs if r.pillar == RecommendationPillar.PREDICT])
    prevent_count = len([r for r in pending_recs if r.pillar == RecommendationPillar.PREVENT])
    respond_count = len([r for r in pending_recs if r.pillar == RecommendationPillar.RESPOND])
    
    # Count by status
    status_counts = {
        "nominal": len([c for c in crew if c.status == HealthStatus.NOMINAL]),
        "caution": len([c for c in crew if c.status == HealthStatus.CAUTION]),
        "warning": len([c for c in crew if c.status == HealthStatus.WARNING]),
        "critical": len([c for c in crew if c.status == HealthStatus.CRITICAL])
    }
    
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "crew_count": len(crew),
        "crew_status": status_counts,
        "pending_recommendations": {
            "total": len(pending_recs),
            "PREDICT": predict_count,
            "PREVENT": prevent_count,
            "RESPOND": respond_count
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
# Alerts Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/alerts", response_model=list[Alert])
async def get_alerts(
    unacknowledged_only: bool = Query(default=False),
    limit: int = Query(default=50, le=200)
):
    """Get alerts."""
    return db.get_alerts(unacknowledged_only=unacknowledged_only, limit=limit)


@app.post("/api/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: str):
    """Acknowledge an alert."""
    success = db.acknowledge_alert(alert_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")
    return {"status": "acknowledged", "alert_id": alert_id}


# ─────────────────────────────────────────────────────────────────────────────
# Events Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/events", response_model=list[Event])
async def get_events(
    event_type: Optional[EventType] = None,
    limit: int = Query(default=100, le=500)
):
    """Get event log."""
    return db.get_events(limit=limit, event_type=event_type)


@app.get("/api/events/agent-insights")
async def get_agent_insights(limit: int = Query(default=50, le=200)):
    """Get agent analysis events for the Agent Insights panel."""
    events = db.get_events(limit=limit, event_type=EventType.AGENT_ANALYSIS)
    return {
        "count": len(events),
        "events": events
    }


# ─────────────────────────────────────────────────────────────────────────────
# Simulation Control Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/simulation", response_model=SimulationState)
async def get_simulation_state():
    """Get current simulation state."""
    return get_simulator().get_state()


@app.post("/api/simulation/start")
async def start_simulation():
    """Start the vitals simulation."""
    simulator = get_simulator()
    if not simulator.running:
        asyncio.create_task(simulator.run())
    return {"status": "running"}


@app.post("/api/simulation/stop")
async def stop_simulation():
    """Stop the vitals simulation."""
    simulator = get_simulator()
    simulator.stop()
    return {"status": "stopped"}


@app.post("/api/simulation/config")
async def update_simulation_config(config: SimulationConfig):
    """Update simulation configuration."""
    simulator = get_simulator()
    simulator.set_config(config)
    return {"status": "updated", "config": config.model_dump()}


@app.post("/api/simulation/inject/{anomaly_type}")
async def inject_anomaly(
    anomaly_type: str,
    crew_id: Optional[str] = None
):
    """
    Inject an anomaly for demo purposes.
    
    Anomaly types:
    - cardiac: Heart rate abnormality
    - radiation: Elevated radiation exposure
    - hypoxia: Low oxygen saturation
    - fever: Elevated body temperature
    - dehydration: Low hydration levels
    """
    valid_anomalies = ["cardiac", "radiation", "hypoxia", "fever", "dehydration"]
    if anomaly_type not in valid_anomalies:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid anomaly type. Valid types: {valid_anomalies}"
        )
    
    simulator = get_simulator()
    simulator.inject_anomaly(anomaly_type, crew_id)
    
    return {
        "status": "injected",
        "anomaly": anomaly_type,
        "crew_id": crew_id,
        "message": f"Anomaly '{anomaly_type}' will appear in next vitals update"
    }


@app.post("/api/simulation/clear-anomaly")
async def clear_anomaly():
    """Clear any active anomaly injection."""
    simulator = get_simulator()
    simulator.clear_anomaly()
    return {"status": "cleared"}


@app.post("/api/simulation/scenario/{scenario}")
async def set_scenario(scenario: str, crew_id: Optional[str] = None):
    """
    Set simulation scenario.
    
    Scenarios:
    - nominal: Normal operations
    - spe_event: Solar Particle Event
    - medical_emergency: General medical emergency
    - cascade_demo: one crew member converges to a cascade within seconds
    - buildup_demo: the same cascade built up gradually (~6 min) so PREDICT forecasts it first
    """
    valid_scenarios = ["nominal", "spe_event", "medical_emergency", "cascade_demo", "buildup_demo"]
    if scenario not in valid_scenarios:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid scenario. Valid scenarios: {valid_scenarios}"
        )
    
    simulator = get_simulator()
    config = simulator.config.model_copy()  # Copy to preserve old scenario for set_config comparison
    config.scenario = scenario if scenario != "nominal" else None
    # Single-person scenarios (cascade, build-up, medical) target the crew member selected on the dashboard;
    # the solar storm hits everyone. Without crew_id each scenario keeps its default crew member.
    if scenario in ("cascade_demo", "buildup_demo", "medical_emergency"):
        if crew_id and not db.get_crew_member(crew_id):
            raise HTTPException(status_code=404, detail=f"Crew member {crew_id} not found")
        config.inject_crew_id = crew_id
    else:
        config.inject_crew_id = None
    simulator.set_config(config)
    # Any scenario change starts a fresh cascade window: re-arms one cascade per crew member and
    # drops warnings the previous scenario left in the cascade history (so an SPE can't add up to a cascade later).
    get_coordinator().rearm_cascades()
    # A declared solar particle event puts AEGIS in PROTECT mode: cascade detection pauses so the crew
    # gets one clear instruction (SHELTER-1) instead of a SHELTER + cascade mix while the dose is still rising.
    get_coordinator().protect_mode = (scenario == "spe_event")
    
    return {"status": "set", "scenario": scenario, "crew_id": config.inject_crew_id}


# ─────────────────────────────────────────────────────────────────────────────
# Knowledge Base Stats
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/knowledge-base/stats")
async def get_kb_stats():
    """Get knowledge base statistics."""
    kb = get_knowledge_base()
    return kb.get_stats()


# ─────────────────────────────────────────────────────────────────────────────
# Run Server
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        log_level="info"
    )
