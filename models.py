"""
AEGIS Pydantic Models
NASA Space Apps Challenge 2026

Data models for API request/response schemas and database entities.
"""

from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────

class CrewRole(str, Enum):
    COMMANDER = "commander"
    PILOT = "pilot"
    FLIGHT_ENGINEER = "flight_engineer"
    MISSION_SPECIALIST = "mission_specialist"
    PAYLOAD_SPECIALIST = "payload_specialist"
    MEDICAL_OFFICER = "medical_officer"
    SCIENCE_OFFICER = "science_officer"


class HealthStatus(str, Enum):
    NOMINAL = "nominal"
    CAUTION = "caution"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertSeverity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RecommendationStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DEFERRED = "deferred"
    DISMISSED = "dismissed"
    COMPLETED = "completed"


class RecommendationPillar(str, Enum):
    PREDICT = "PREDICT"
    PREVENT = "PREVENT"
    PROTECT = "PROTECT"


class AgentType(str, Enum):
    # Canonical AEGIS agent names matching the pitch
    GENE = "GENE"          # Genomic/inflammatory markers (IL-6, CRP)
    PULSE = "PULSE"        # Heart rate + SpO2
    SOLARIS = "SOLARIS"    # Radiation monitoring (24 h dose + mission total)
    MIRROR = "MIRROR"      # Sleep quality
    AEGIS = "AEGIS"        # Coordinator/orchestrator
    # Keep OsteoWatch for backward compatibility until fully migrated
    OSTEO = "OsteoWatch"


# ─────────────────────────────────────────────────────────────────────────────
# Vitals & Biometrics
# ─────────────────────────────────────────────────────────────────────────────

class Vitals(BaseModel):
    """Real-time vital signs for a crew member."""
    heart_rate: int = Field(..., ge=30, le=220, description="BPM")
    blood_pressure_systolic: int = Field(..., ge=70, le=200)
    blood_pressure_diastolic: int = Field(..., ge=40, le=130)
    spo2: float = Field(..., ge=80, le=100, description="Blood oxygen %")
    temperature: float = Field(..., ge=35, le=42, description="Celsius")
    respiratory_rate: int = Field(..., ge=8, le=40, description="Breaths/min")
    
    # Extended vitals
    stress_index: Optional[float] = Field(None, ge=0, le=100)
    sleep_quality: Optional[float] = Field(None, ge=0, le=100)
    hydration_level: Optional[float] = Field(None, ge=0, le=100)
    radiation_dose_24h: Optional[float] = Field(None, ge=0, description="mSv in last 24h")


class CrewMember(BaseModel):
    """Crew member profile and current state."""
    id: str
    name: str
    role: CrewRole
    age: int
    status: HealthStatus = HealthStatus.NOMINAL
    vitals: Optional[Vitals] = None
    current_activity: Optional[str] = None
    mission_day: int = 1
    
    # Health tracking
    bone_density_baseline: float = Field(default=1.0, description="T-score baseline")
    muscle_mass_baseline: float = Field(default=100, description="% of pre-flight")
    cumulative_radiation: float = Field(default=0, description="Total mission mSv")
    
    # Recent conditions
    active_conditions: list[str] = Field(default_factory=list)
    medications: list[str] = Field(default_factory=list)


class CrewState(BaseModel):
    """Complete crew state snapshot."""
    crew: list[CrewMember]
    mission_elapsed_time: str  # "Day X, HH:MM:SS"
    timestamp: datetime


# ─────────────────────────────────────────────────────────────────────────────
# Recommendations & Alerts
# ─────────────────────────────────────────────────────────────────────────────

class Recommendation(BaseModel):
    """AI-generated recommendation from AEGIS agents."""
    id: str
    crew_member_id: str
    pillar: RecommendationPillar
    agent: AgentType
    severity: AlertSeverity
    title: str
    description: str
    rationale: str  # Why this recommendation
    evidence: list[str] = Field(default_factory=list)  # Citations/sources
    actions: list[str] = Field(default_factory=list)  # Concrete steps
    
    status: RecommendationStatus = RecommendationStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None
    
    # Tracking
    confidence: float = Field(default=0.85, ge=0, le=1)
    risk_if_ignored: Optional[str] = None


class Alert(BaseModel):
    """System alert for immediate attention."""
    id: str
    severity: AlertSeverity
    title: str
    message: str
    source: AgentType
    crew_member_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    acknowledged: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Event Log
# ─────────────────────────────────────────────────────────────────────────────

class EventType(str, Enum):
    VITALS_UPDATE = "vitals_update"
    RECOMMENDATION = "recommendation"
    ALERT = "alert"
    USER_ACTION = "user_action"
    AGENT_ANALYSIS = "agent_analysis"
    SYSTEM = "system"


class Event(BaseModel):
    """Event log entry for Agent Insights panel."""
    id: str
    type: EventType
    agent: Optional[AgentType] = None
    message: str
    details: Optional[dict] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# API Request/Response Models
# ─────────────────────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    """RAG query request."""
    query: str
    n_results: int = Field(default=5, ge=1, le=20)
    collections: Optional[list[str]] = None


class QueryResponse(BaseModel):
    """RAG query response."""
    query: str
    results: dict
    context_for_llm: str


class RecommendationAction(BaseModel):
    """User action on a recommendation."""
    recommendation_id: str
    action: RecommendationStatus
    notes: Optional[str] = None


class AnalysisRequest(BaseModel):
    """Request analysis for a specific crew member."""
    crew_member_id: str
    analysis_type: Optional[str] = None  # "full" | "vitals" | "trends"


class AnalysisResponse(BaseModel):
    """Agent analysis response."""
    crew_member_id: str
    timestamp: datetime
    predictions: list[dict]  # PREDICT pillar findings
    preventions: list[dict]  # PREVENT pillar recommendations  
    responses: list[dict]  # PROTECT pillar alerts (if any)
    summary: str


# ─────────────────────────────────────────────────────────────────────────────
# Simulation Control
# ─────────────────────────────────────────────────────────────────────────────

class SimulationConfig(BaseModel):
    """Configuration for vitals simulator."""
    speed_multiplier: float = Field(default=1.0, ge=0.1, le=100)
    scenario: Optional[str] = None  # "nominal" | "spe_event" | "medical_emergency"
    inject_anomaly: Optional[str] = None  # "cardiac" | "radiation" | "injury"
    inject_crew_id: Optional[str] = None  # H0/M1: Target crew for anomaly (None = all)


class SimulationState(BaseModel):
    """Current simulation state."""
    running: bool
    mission_day: int
    speed_multiplier: float
    active_scenario: Optional[str]
    last_update: datetime
    last_tick_at: Optional[datetime] = None  # M5: Last successful tick timestamp
    last_error: Optional[str] = None          # M5: Last error message if any
