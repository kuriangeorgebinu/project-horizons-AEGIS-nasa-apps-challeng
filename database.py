"""
AEGIS Database Module
NASA Space Apps Challenge 2026

SQLite-based state persistence for crew state, recommendations, and events.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional
import uuid

from models import (
    CrewMember, CrewRole, HealthStatus, Vitals,
    Recommendation, RecommendationStatus, RecommendationPillar,
    Event, EventType, Alert, AlertSeverity, AgentType
)

# Database path
DB_PATH = Path(__file__).parent / "aegis.db"


def get_connection() -> sqlite3.Connection:
    """Get a database connection with row factory."""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_database():
    """Initialize database schema."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Crew state table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS crew_state (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                age INTEGER,
                status TEXT DEFAULT 'nominal',
                vitals_json TEXT,
                current_activity TEXT,
                mission_day INTEGER DEFAULT 1,
                bone_density_baseline REAL DEFAULT 1.0,
                muscle_mass_baseline REAL DEFAULT 100.0,
                cumulative_radiation REAL DEFAULT 0.0,
                active_conditions_json TEXT DEFAULT '[]',
                medications_json TEXT DEFAULT '[]',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Recommendations table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS recommendations (
                id TEXT PRIMARY KEY,
                crew_member_id TEXT,
                pillar TEXT NOT NULL,
                agent TEXT NOT NULL,
                severity TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                rationale TEXT,
                evidence_json TEXT DEFAULT '[]',
                actions_json TEXT DEFAULT '[]',
                status TEXT DEFAULT 'pending',
                confidence REAL DEFAULT 0.85,
                risk_if_ignored TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                FOREIGN KEY (crew_member_id) REFERENCES crew_state(id)
            )
        """)
        
        # Event log table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS event_log (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                agent TEXT,
                message TEXT NOT NULL,
                details_json TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Alerts table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id TEXT PRIMARY KEY,
                severity TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT,
                source TEXT,
                crew_member_id TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                acknowledged INTEGER DEFAULT 0,
                FOREIGN KEY (crew_member_id) REFERENCES crew_state(id)
            )
        """)
        
        # Vitals history table (for trend analysis)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS vitals_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                crew_member_id TEXT NOT NULL,
                vitals_json TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (crew_member_id) REFERENCES crew_state(id)
            )
        """)
        
        # Create indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_recommendations_crew ON recommendations(crew_member_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_recommendations_status ON recommendations(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_timestamp ON event_log(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_vitals_history_crew ON vitals_history(crew_member_id)")
        
        conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Crew State Operations
# ─────────────────────────────────────────────────────────────────────────────

def get_all_crew() -> list[CrewMember]:
    """Get all crew members."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM crew_state")
        rows = cursor.fetchall()
        
        crew = []
        for row in rows:
            vitals = json.loads(row["vitals_json"]) if row["vitals_json"] else None
            crew.append(CrewMember(
                id=row["id"],
                name=row["name"],
                role=CrewRole(row["role"]),
                age=row["age"],
                status=HealthStatus(row["status"]),
                vitals=Vitals(**vitals) if vitals else None,
                current_activity=row["current_activity"],
                mission_day=row["mission_day"],
                bone_density_baseline=row["bone_density_baseline"],
                muscle_mass_baseline=row["muscle_mass_baseline"],
                cumulative_radiation=row["cumulative_radiation"],
                active_conditions=json.loads(row["active_conditions_json"]),
                medications=json.loads(row["medications_json"])
            ))
        return crew


def get_crew_member(crew_id: str) -> Optional[CrewMember]:
    """Get a specific crew member."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM crew_state WHERE id = ?", (crew_id,))
        row = cursor.fetchone()
        
        if not row:
            return None
        
        vitals = json.loads(row["vitals_json"]) if row["vitals_json"] else None
        return CrewMember(
            id=row["id"],
            name=row["name"],
            role=CrewRole(row["role"]),
            age=row["age"],
            status=HealthStatus(row["status"]),
            vitals=Vitals(**vitals) if vitals else None,
            current_activity=row["current_activity"],
            mission_day=row["mission_day"],
            bone_density_baseline=row["bone_density_baseline"],
            muscle_mass_baseline=row["muscle_mass_baseline"],
            cumulative_radiation=row["cumulative_radiation"],
            active_conditions=json.loads(row["active_conditions_json"]),
            medications=json.loads(row["medications_json"])
        )


def upsert_crew_member(member: CrewMember):
    """Insert or update a crew member."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO crew_state (
                id, name, role, age, status, vitals_json, current_activity,
                mission_day, bone_density_baseline, muscle_mass_baseline,
                cumulative_radiation, active_conditions_json, medications_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                role = excluded.role,
                age = excluded.age,
                status = excluded.status,
                vitals_json = excluded.vitals_json,
                current_activity = excluded.current_activity,
                mission_day = excluded.mission_day,
                bone_density_baseline = excluded.bone_density_baseline,
                muscle_mass_baseline = excluded.muscle_mass_baseline,
                cumulative_radiation = excluded.cumulative_radiation,
                active_conditions_json = excluded.active_conditions_json,
                medications_json = excluded.medications_json,
                updated_at = excluded.updated_at
        """, (
            member.id,
            member.name,
            member.role.value,
            member.age,
            member.status.value,
            json.dumps(member.vitals.model_dump() if member.vitals else None),
            member.current_activity,
            member.mission_day,
            member.bone_density_baseline,
            member.muscle_mass_baseline,
            member.cumulative_radiation,
            json.dumps(member.active_conditions),
            json.dumps(member.medications),
            datetime.utcnow().isoformat()
        ))


def update_crew_vitals(crew_id: str, vitals: Vitals, status: Optional[HealthStatus] = None):
    """Update a crew member's vitals and optionally status."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        if status:
            cursor.execute("""
                UPDATE crew_state 
                SET vitals_json = ?, status = ?, updated_at = ?
                WHERE id = ?
            """, (json.dumps(vitals.model_dump()), status.value, datetime.utcnow().isoformat(), crew_id))
        else:
            cursor.execute("""
                UPDATE crew_state 
                SET vitals_json = ?, updated_at = ?
                WHERE id = ?
            """, (json.dumps(vitals.model_dump()), datetime.utcnow().isoformat(), crew_id))
        
        # Also store in history for trend analysis
        cursor.execute("""
            INSERT INTO vitals_history (crew_member_id, vitals_json)
            VALUES (?, ?)
        """, (crew_id, json.dumps(vitals.model_dump())))


def update_cumulative_radiation(crew_id: str, increment: float):
    """Add to a crew member's cumulative radiation dose (T7 fix)."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE crew_state 
            SET cumulative_radiation = cumulative_radiation + ?,
                updated_at = ?
            WHERE id = ?
        """, (increment, datetime.utcnow().isoformat(), crew_id))


# ─────────────────────────────────────────────────────────────────────────────
# Recommendations Operations
# ─────────────────────────────────────────────────────────────────────────────

def create_recommendation(rec: Recommendation) -> str:
    """Create a new recommendation."""
    with get_db() as conn:
        cursor = conn.cursor()
        rec_id = rec.id or f"REC-{uuid.uuid4().hex[:8].upper()}"
        
        cursor.execute("""
            INSERT INTO recommendations (
                id, crew_member_id, pillar, agent, severity, title, description,
                rationale, evidence_json, actions_json, status, confidence,
                risk_if_ignored, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            rec_id,
            rec.crew_member_id,
            rec.pillar.value,
            rec.agent.value,
            rec.severity.value,
            rec.title,
            rec.description,
            rec.rationale,
            json.dumps(rec.evidence),
            json.dumps(rec.actions),
            rec.status.value,
            rec.confidence,
            rec.risk_if_ignored,
            rec.created_at.isoformat(),
            rec.expires_at.isoformat() if rec.expires_at else None
        ))
        return rec_id


def get_recommendations(
    crew_id: Optional[str] = None,
    status: Optional[RecommendationStatus] = None,
    pillar: Optional[RecommendationPillar] = None,
    limit: int = 50
) -> list[Recommendation]:
    """Get recommendations with optional filters."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        query = "SELECT * FROM recommendations WHERE 1=1"
        params = []
        
        if crew_id:
            query += " AND crew_member_id = ?"
            params.append(crew_id)
        if status:
            query += " AND status = ?"
            params.append(status.value)
        if pillar:
            query += " AND pillar = ?"
            params.append(pillar.value)
        
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        recs = []
        for row in rows:
            recs.append(Recommendation(
                id=row["id"],
                crew_member_id=row["crew_member_id"],
                pillar=RecommendationPillar(row["pillar"]),
                agent=AgentType(row["agent"]),
                severity=AlertSeverity(row["severity"]),
                title=row["title"],
                description=row["description"],
                rationale=row["rationale"],
                evidence=json.loads(row["evidence_json"]),
                actions=json.loads(row["actions_json"]),
                status=RecommendationStatus(row["status"]),
                confidence=row["confidence"],
                risk_if_ignored=row["risk_if_ignored"],
                created_at=datetime.fromisoformat(row["created_at"]),
                expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None
            ))
        return recs


def update_recommendation_status(rec_id: str, status: RecommendationStatus) -> bool:
    """Update a recommendation's status."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE recommendations SET status = ? WHERE id = ?",
            (status.value, rec_id)
        )
        return cursor.rowcount > 0


# ─────────────────────────────────────────────────────────────────────────────
# Event Log Operations
# ─────────────────────────────────────────────────────────────────────────────

def log_event(
    event_type: EventType,
    message: str,
    agent: Optional[AgentType] = None,
    details: Optional[dict] = None
) -> str:
    """Log an event."""
    with get_db() as conn:
        cursor = conn.cursor()
        event_id = f"EVT-{uuid.uuid4().hex[:8].upper()}"
        
        cursor.execute("""
            INSERT INTO event_log (id, type, agent, message, details_json)
            VALUES (?, ?, ?, ?, ?)
        """, (
            event_id,
            event_type.value,
            agent.value if agent else None,
            message,
            json.dumps(details) if details else None
        ))
        return event_id


def get_events(limit: int = 100, event_type: Optional[EventType] = None) -> list[Event]:
    """Get recent events."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        if event_type:
            cursor.execute(
                "SELECT * FROM event_log WHERE type = ? ORDER BY timestamp DESC LIMIT ?",
                (event_type.value, limit)
            )
        else:
            cursor.execute(
                "SELECT * FROM event_log ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            )
        
        rows = cursor.fetchall()
        events = []
        for row in rows:
            events.append(Event(
                id=row["id"],
                type=EventType(row["type"]),
                agent=AgentType(row["agent"]) if row["agent"] else None,
                message=row["message"],
                details=json.loads(row["details_json"]) if row["details_json"] else None,
                timestamp=datetime.fromisoformat(row["timestamp"])
            ))
        return events


# ─────────────────────────────────────────────────────────────────────────────
# Alerts Operations
# ─────────────────────────────────────────────────────────────────────────────

def create_alert(alert: Alert) -> str:
    """Create a new alert."""
    with get_db() as conn:
        cursor = conn.cursor()
        alert_id = alert.id or f"ALT-{uuid.uuid4().hex[:8].upper()}"
        
        cursor.execute("""
            INSERT INTO alerts (id, severity, title, message, source, crew_member_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            alert_id,
            alert.severity.value,
            alert.title,
            alert.message,
            alert.source.value if alert.source else None,
            alert.crew_member_id
        ))
        return alert_id


def get_alerts(unacknowledged_only: bool = False, limit: int = 50) -> list[Alert]:
    """Get alerts."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        if unacknowledged_only:
            cursor.execute(
                "SELECT * FROM alerts WHERE acknowledged = 0 ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            )
        else:
            cursor.execute(
                "SELECT * FROM alerts ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            )
        
        rows = cursor.fetchall()
        alerts = []
        for row in rows:
            alerts.append(Alert(
                id=row["id"],
                severity=AlertSeverity(row["severity"]),
                title=row["title"],
                message=row["message"],
                source=AgentType(row["source"]) if row["source"] else None,
                crew_member_id=row["crew_member_id"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                acknowledged=bool(row["acknowledged"])
            ))
        return alerts


def acknowledge_alert(alert_id: str) -> bool:
    """Acknowledge an alert."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE alerts SET acknowledged = 1 WHERE id = ?", (alert_id,))
        return cursor.rowcount > 0


def update_alert_message(alert_id: str, message: str) -> bool:
    """Replace an alert's message (used to attach the DeepSeek assessment)."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE alerts SET message = ? WHERE id = ?", (message, alert_id))
        return cursor.rowcount > 0


def update_recommendation_rag(rec_id: str, rationale: str, actions: list[str], evidence: list[str]) -> bool:
    """Store a RAG-generated recommendation: DeepSeek assessment, tailored actions and cited sources."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE recommendations SET rationale = ?, actions_json = ?, evidence_json = ? WHERE id = ?",
            (rationale, json.dumps(actions), json.dumps(evidence), rec_id))
        return cursor.rowcount > 0


def update_recommendation_rationale(rec_id: str, rationale: str) -> bool:
    """Replace a recommendation's rationale (used to attach the DeepSeek assessment)."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE recommendations SET rationale = ? WHERE id = ?", (rationale, rec_id))
        return cursor.rowcount > 0


# ─────────────────────────────────────────────────────────────────────────────
# Vitals History (for trends)
# ─────────────────────────────────────────────────────────────────────────────

def get_vitals_history(crew_id: str, hours: int = 24) -> list[dict]:
    """Get vitals history for trend analysis."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT vitals_json, timestamp FROM vitals_history
            WHERE crew_member_id = ?
            AND timestamp >= datetime('now', ? || ' hours')
            ORDER BY timestamp ASC
        """, (crew_id, f"-{hours}"))
        
        rows = cursor.fetchall()
        return [
            {"vitals": json.loads(row["vitals_json"]), "timestamp": row["timestamp"]}
            for row in rows
        ]


# ─────────────────────────────────────────────────────────────────────────────
# Initialize Default Crew
# ─────────────────────────────────────────────────────────────────────────────

def reset_demo_data() -> dict:
    """Clean demo start: clear alerts, recommendations, events and vitals history, then reseed the crew.
    Returns how many rows were removed per table."""
    removed = {}
    with get_db() as conn:
        cur = conn.cursor()
        for table in ("alerts", "recommendations", "event_log", "vitals_history"):
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            removed[table] = cur.fetchone()[0]
            cur.execute(f"DELETE FROM {table}")
    init_default_crew()                     # back to the canonical crew, day 54, starting radiation dose
    return removed


def init_default_crew():
    """Initialize default crew for demo."""
    # Canonical crew roster: Chen, Okonkwo, Kowalski, Nakamura
    # Matches the spacecraft sim and dashboard
    default_crew = [
        CrewMember(
            id="crew-001",
            name="Sarah Chen",
            role=CrewRole.COMMANDER,
            age=42,
            status=HealthStatus.NOMINAL,
            current_activity="Command Station",
            mission_day=54,
            cumulative_radiation=97.0  # day 54 x ~1.84 mSv/day Mars-transit background; 600 mSv career limit
        ),
        CrewMember(
            id="crew-002", 
            name="David Okonkwo",
            role=CrewRole.SCIENCE_OFFICER,
            age=33,
            status=HealthStatus.NOMINAL,
            current_activity="Lab Work",
            mission_day=54,
            cumulative_radiation=101.0
        ),
        CrewMember(
            id="crew-003",
            name="Anna Kowalski",
            role=CrewRole.FLIGHT_ENGINEER,
            age=38,
            status=HealthStatus.CAUTION,
            current_activity="EVA Prep",
            mission_day=54,
            cumulative_radiation=103.0,
            active_conditions=["elevated_stress"]
        ),
        CrewMember(
            id="crew-004",
            name="Kenji Nakamura",
            role=CrewRole.MEDICAL_OFFICER,
            age=35,
            status=HealthStatus.NOMINAL,
            current_activity="Medical Bay",
            mission_day=54,
            cumulative_radiation=95.0
        )
    ]
    
    for member in default_crew:
        upsert_crew_member(member)
    
    # Log initialization event
    log_event(
        EventType.SYSTEM,
        "AEGIS system initialized with 4 crew members",
        details={"crew_count": len(default_crew)}
    )


# Initialize on import
init_database()
