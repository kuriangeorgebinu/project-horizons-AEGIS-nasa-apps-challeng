"""
AEGIS Vitals Simulator
NASA Space Apps Challenge 2026

Generates realistic real-time vital signs for crew members.
Supports scenarios: nominal, SPE event, medical emergencies.
"""

import asyncio
import random
from datetime import datetime
from typing import Optional
import math

from models import (
    CrewMember, Vitals, HealthStatus, SimulationConfig, SimulationState
)
import database as db
import aegis_mission


# ─────────────────────────────────────────────────────────────────────────────
# Simulation State
# ─────────────────────────────────────────────────────────────────────────────

BUILDUP_SECONDS = 360   # buildup_demo: time for the metrics to drift from baseline to the cascade end state
RECOVERY_SECONDS = 120  # prevent_recovery: time for the drift to reverse back to baseline after PREVENT-1

# Repeatable demos: every random draw in the simulator comes from this seeded generator, and each crew
# member has FIXED baseline vitals (no hardware is connected; these stand in for a wearable's readings).
SIM_SEED = 54
_rng = random.Random(SIM_SEED)
FIXED_BASELINES = {
    # sleep sits well above MIRROR's 70 limit so it does not flicker in and out of range at rest
    "crew-001": {"heart_rate": 66, "bp_systolic": 118, "bp_diastolic": 76, "spo2": 98.2,
                 "temperature": 36.6, "respiratory_rate": 14, "stress_base": 28, "sleep_quality": 80},
    "crew-002": {"heart_rate": 70, "bp_systolic": 122, "bp_diastolic": 78, "spo2": 97.8,
                 "temperature": 36.7, "respiratory_rate": 15, "stress_base": 32, "sleep_quality": 78},
    "crew-003": {"heart_rate": 72, "bp_systolic": 120, "bp_diastolic": 79, "spo2": 97.6,
                 "temperature": 36.6, "respiratory_rate": 15, "stress_base": 36, "sleep_quality": 76},
    "crew-004": {"heart_rate": 64, "bp_systolic": 116, "bp_diastolic": 74, "spo2": 98.4,
                 "temperature": 36.5, "respiratory_rate": 14, "stress_base": 24, "sleep_quality": 82},
}
DEFAULT_BASELINE = {"heart_rate": 68, "bp_systolic": 118, "bp_diastolic": 76, "spo2": 98.0,
                    "temperature": 36.6, "respiratory_rate": 15, "stress_base": 30, "sleep_quality": 78}


class VitalsSimulator:
    """Real-time vitals simulator for AEGIS demo."""
    
    def __init__(self):
        self.running = False
        self.config = SimulationConfig()
        self.mission_day = 54  # Day 54 matches the cascade story
        self.start_time = datetime.utcnow()
        self.scenario_started_at = datetime.utcnow()   # for gradual scenarios (buildup_demo)
        self.recovery_from_p = 0.0                     # build-up progress when PREVENT-1 started
        self._task: Optional[asyncio.Task] = None
        
        # M5: Track simulator health
        self.last_tick_at: Optional[datetime] = None
        self.last_error: Optional[str] = None
        
        # Base vital ranges for each crew member (individual variation)
        self._crew_baselines = {}
        # Store previous vitals for smoothing (T6 fix)
        self._previous_vitals = {}
    
    def _get_baseline(self, crew_id: str) -> dict:
        """Get or create baseline vitals for a crew member."""
        if crew_id not in self._crew_baselines:
            # Fixed, repeatable baseline per crew member (see FIXED_BASELINES)
            self._crew_baselines[crew_id] = dict(FIXED_BASELINES.get(crew_id, DEFAULT_BASELINE))
        return self._crew_baselines[crew_id]
    
    def generate_vitals(self, crew_member: CrewMember) -> Vitals:
        """Generate realistic vitals for a crew member with EWMA smoothing."""
        baseline = self._get_baseline(crew_member.id)
        
        # Time-based variations (circadian rhythm simulation)
        hour = datetime.utcnow().hour
        circadian_factor = math.sin((hour - 6) * math.pi / 12)  # Peak at noon
        
        # Activity-based variations
        activity_factor = self._get_activity_factor(crew_member.current_activity)
        
        # Scenario-based modifications (M1: pass crew_id to filter anomaly)
        scenario_mods = self._get_scenario_modifiers(crew_member.id)
        
        # Get previous vitals for smoothing (T6 fix)
        prev = self._previous_vitals.get(crew_member.id, {})
        alpha = 0.3  # EWMA smoothing factor (lower = smoother)
        
        # R3: Check if anomaly or scenario is active - bypass smoothing for affected vitals
        anomaly_active = self.config.inject_anomaly is not None
        scenario_active = self.config.scenario is not None
        
        # H1: Per-vital noise scaling to prevent false readings
        noise_scale = {
            "heart_rate": 1.5,
            "bp_systolic": 2.0,
            "bp_diastolic": 1.5,
            "spo2": 0.2,
            "temperature": 0.05,
            "respiratory_rate": 0.5,
            "stress_index": 1.0,
            "sleep_quality": 0.5,
            "hydration_level": 0.5,
            "radiation_dose_24h": 0.0,  # No noise for radiation - too sensitive
        }
        
        # H1: Clamp ranges per vital to prevent validation errors
        clamp_ranges = {
            "heart_rate": (40, 180),
            "bp_systolic": (85, 190),
            "bp_diastolic": (50, 120),
            "spo2": (88, 100),
            "temperature": (35.5, 40),
            "respiratory_rate": (10, 35),
            "stress_index": (0, 100),
            "sleep_quality": (0, 100),
            "hydration_level": (0, 100),
            "radiation_dose_24h": (0, 60),
        }
        
        def smooth(key: str, new_val: float, bypass: bool = False) -> float:
            """Apply EWMA smoothing with per-vital noise and clamping."""
            if bypass or key not in prev:
                result = new_val
            else:
                smoothed = alpha * new_val + (1 - alpha) * prev[key]
                # H1: Use per-vital noise scale
                ns = noise_scale.get(key, 0)
                noise = _rng.uniform(-ns, ns) if ns > 0 else 0
                result = smoothed + noise
            
            # H1: Clamp to valid range
            lo, hi = clamp_ranges.get(key, (float('-inf'), float('inf')))
            return max(lo, min(hi, result))
        
        # Generate vitals with natural variation and smoothing
        hr_base = baseline["heart_rate"] + (circadian_factor * 5) + (activity_factor * 20)
        hr_raw = self._bounded_random(
            hr_base + scenario_mods.get("heart_rate", 0),
            3, 40, 180  # Reduced spread from 5 to 3
        )
        
        bp_sys_raw = self._bounded_random(
            baseline["bp_systolic"] + (activity_factor * 15) + scenario_mods.get("bp_systolic", 0),
            4, 85, 190  # Reduced spread
        )
        
        bp_dia_raw = self._bounded_random(
            baseline["bp_diastolic"] + (activity_factor * 8) + scenario_mods.get("bp_diastolic", 0),
            3, 50, 120
        )
        
        spo2_raw = self._bounded_random(
            baseline["spo2"] + scenario_mods.get("spo2", 0),
            0.3, 88, 100  # Reduced spread
        )
        
        temp_raw = self._bounded_random(
            baseline["temperature"] + (activity_factor * 0.3) + scenario_mods.get("temperature", 0),
            0.1, 35.5, 40  # Reduced spread
        )
        
        rr_raw = self._bounded_random(
            baseline["respiratory_rate"] + (activity_factor * 4) + scenario_mods.get("respiratory_rate", 0),
            1, 10, 35
        )
        
        stress_raw = self._bounded_random(
            baseline["stress_base"] + (activity_factor * 15) + scenario_mods.get("stress", 0),
            5, 0, 100
        )
        
        sleep_raw = self._bounded_random(
            baseline["sleep_quality"] + scenario_mods.get("sleep", 0),
            3, 0, 100
        )
        
        hydration_raw = self._bounded_random(
            75 + scenario_mods.get("hydration", 0),
            5, 0, 100
        )
        
        # Daily dose centred on the mission's normal background (aegis_mission: 1.84 mSv/day in Mars
        # transit, Curiosity RAD) plus any scenario enhancement.
        radiation_raw = self._bounded_random(
            aegis_mission.background() + scenario_mods.get("radiation", 0),
            0.1, 0, 60
        )
        
        # Apply smoothing (R3: bypass for affected vitals during anomaly/scenario)
        cardiac_bypass = (anomaly_active and self.config.inject_anomaly == "cardiac") or \
            self.config.scenario == "cascade_demo"   # heart rate reacts at once in the cascade demo
        radiation_bypass = (anomaly_active and self.config.inject_anomaly == "radiation") or scenario_active
        
        vitals = Vitals(
            heart_rate=int(smooth("heart_rate", hr_raw, bypass=cardiac_bypass)),
            blood_pressure_systolic=int(smooth("bp_systolic", bp_sys_raw, bypass=cardiac_bypass)),
            blood_pressure_diastolic=int(smooth("bp_diastolic", bp_dia_raw, bypass=cardiac_bypass)),
            spo2=round(smooth("spo2", spo2_raw), 1),
            temperature=round(smooth("temperature", temp_raw), 1),
            respiratory_rate=int(smooth("respiratory_rate", rr_raw)),
            stress_index=round(smooth("stress_index", stress_raw, bypass=scenario_active), 1),
            sleep_quality=round(smooth("sleep_quality", sleep_raw,
                                       bypass=self.config.scenario == "cascade_demo"), 1),  # MIRROR joins at once
            hydration_level=round(smooth("hydration_level", hydration_raw), 1),
            radiation_dose_24h=round(smooth("radiation_dose_24h", radiation_raw, bypass=radiation_bypass), 2)
        )
        
        # Store for next iteration
        self._previous_vitals[crew_member.id] = {
            "heart_rate": vitals.heart_rate,
            "bp_systolic": vitals.blood_pressure_systolic,
            "bp_diastolic": vitals.blood_pressure_diastolic,
            "spo2": vitals.spo2,
            "temperature": vitals.temperature,
            "respiratory_rate": vitals.respiratory_rate,
            "stress_index": vitals.stress_index,
            "sleep_quality": vitals.sleep_quality,
            "hydration_level": vitals.hydration_level,
            "radiation_dose_24h": vitals.radiation_dose_24h
        }
        
        return vitals
    
    def reset_demo(self):
        """Clean demo start: nominal scenario, fixed baselines, no smoothing history, re-seeded randomness."""
        _rng.seed(SIM_SEED)
        self.config = SimulationConfig()
        self.mission_day = 54
        self.start_time = datetime.utcnow()
        self.scenario_started_at = datetime.utcnow()
        self.recovery_from_p = 0.0
        self._crew_baselines = {}
        self._previous_vitals = {}

    def buildup_progress(self) -> float:
        """0..1 progress of the build-up toward the cascade end state (reverses during prevent_recovery)."""
        elapsed = (datetime.utcnow() - self.scenario_started_at).total_seconds()
        if self.config.scenario == "buildup_demo":
            return min(1.0, elapsed / BUILDUP_SECONDS)
        if self.config.scenario == "prevent_recovery":
            return self.recovery_from_p * max(0.0, 1.0 - elapsed / RECOVERY_SECONDS)
        return 0.0

    def start_prevention(self, crew_id: str) -> bool:
        """PREVENT-1 started for crew_id: in the demo scenarios the countermeasures take effect and the drift
        reverses. Returns True if the simulator changed (a real telemetry feed would simply show the effect)."""
        if self.config.scenario not in ("buildup_demo", "cascade_demo"):
            return False
        if crew_id != (self.config.inject_crew_id or "crew-001"):
            return False
        p = self.buildup_progress() if self.config.scenario == "buildup_demo" else 1.0
        self.recovery_from_p = p
        config = self.config.model_copy()
        config.scenario = "prevent_recovery"
        self.set_config(config)
        return True

    def _bounded_random(self, center: float, spread: float, min_val: float, max_val: float) -> float:
        """Generate random value within bounds."""
        value = center + _rng.gauss(0, spread / 2)
        return max(min_val, min(max_val, value))
    
    def _get_activity_factor(self, activity: Optional[str]) -> float:
        """Get activity-based vital sign modifier (0-1 scale)."""
        if not activity:
            return 0.2
        
        activity_levels = {
            "Sleep": 0.0,
            "Rest": 0.1,
            "Command Station": 0.2,
            "Lab Work": 0.3,
            "Navigation Check": 0.25,
            "Medical Bay": 0.2,
            "Cargo Bay": 0.4,
            "EVA Prep": 0.5,
            "EVA": 0.8,
            "Exercise": 0.9,
            "ARED Workout": 0.85,
            "Treadmill": 0.75
        }
        
        return activity_levels.get(activity, 0.3)
    
    def _get_scenario_modifiers(self, crew_id: str = None) -> dict:
        """Get vital sign modifiers based on active scenario."""
        mods = {}
        
        if self.config.scenario == "spe_event":
            # Solar Particle Event: daily dose jumps well above the shelter level (5 x background,
            # >= background + 5 mSv): ~20-32 mSv/day, the order of a moderate SPE behind spacecraft shielding.
            mods["radiation"] = _rng.uniform(18.0, 30.0)
            mods["stress"] = 15
            
        elif self.config.scenario == "medical_emergency":
            # PROTECT (medical) demo: acute hypoxia in one crew member (e.g. a CO2-scrubber / mask fault).
            # SpO2 falls below PULSE's critical 90 % within ~30 s, with a compensating heart-rate rise ->
            # PULSE raises a critical PROTECT alert -> Protocol MEDIC-1. Everyone else stays nominal.
            target = self.config.inject_crew_id or "crew-004"
            if crew_id is None or crew_id == target:
                spo2_base = self._crew_baselines.get(crew_id or target, {}).get("spo2", 98)
                mods["spo2"] = 87.5 - spo2_base
                mods["heart_rate"] = 22
                mods["stress"] = 20
            
        elif self.config.scenario == "cascade_demo":
            # H2: Cascade demo - drives SOLARIS, GENE, PULSE, MIRROR to amber
            # This simulates the Day 47-54 story from the pitch
            # Story: the cascade builds in ONE crew member (Commander Chen by default, or the
            # inject_crew_id target); everyone else stays nominal so the demo is readable.
            target = self.config.inject_crew_id or "crew-001"
            if crew_id is None or crew_id == target:
                # Keep radiation amber: background 1.84 + 2.2-2.8 = ~4.0-4.6 mSv/24h, above the SOLARIS
                # warning (2 x background = 3.68) but well below shelter (9.2), so it builds a cascade
                # (RECOVER-3) rather than an acute SHELTER alert.
                mods["radiation"] = _rng.uniform(2.2, 2.8)   # SOLARIS warning
                # MIRROR warning is >70, critical >85. Aim for ~76 (+ activity load ~3) relative to THIS
                # crew member's random baseline, so the demo is amber on every restart instead of
                # sometimes tipping into a critical stress alert.
                stress_base = self._crew_baselines.get(crew_id or target, {}).get("stress_base", 30)
                mods["stress"] = max(30, 76 - stress_base)
                # PULSE warning is >100 bpm: aim for ~108 relative to this crew member's baseline
                # (+ ~4 activity load), so PULSE joins the cascade from a real heart-rate reading.
                hr_base = self._crew_baselines.get(crew_id or target, {}).get("heart_rate", 68)
                mods["heart_rate"] = max(15, 104 - hr_base)
                mods["sleep"] = -25                             # MIRROR sleep warning (<70%)

        elif self.config.scenario in ("buildup_demo", "prevent_recovery"):
            # PREDICT demo: the same end state as cascade_demo, reached gradually over BUILDUP_SECONDS, so the
            # trend engine can forecast the cascade minutes before 3 domains actually go out of range.
            # Order the metrics cross (360 s build-up): MIRROR sleep (~2.4 min), GENE modelled IL-6 (~3.1),
            # SOLARIS radiation (~3.7), PULSE heart rate (~4.7) -> AEGIS forecasts the cascade before the
            # third domain crosses at ~3.7 min.
            # prevent_recovery (PREVENT-1 started): the drift reverses back to baseline over RECOVERY_SECONDS.
            target = self.config.inject_crew_id or "crew-001"
            if crew_id is None or crew_id == target:
                p = self.buildup_progress()
                b = self._crew_baselines.get(crew_id or target, {})
                mods["radiation"] = 3.0 * p     # 1.84 -> ~4.8 mSv/24h (amber, below shelter)
                mods["stress"] = p * max(30, 76 - b.get("stress_base", 30))
                mods["heart_rate"] = p * max(15, 104 - b.get("heart_rate", 68))
                mods["sleep"] = -25 * p
        
        # M1: Only apply anomaly to target crew (or all if inject_crew_id is None)
        target_crew = getattr(self.config, 'inject_crew_id', None)
        if target_crew is not None and crew_id != target_crew:
            # This crew member is not the target, skip anomaly modifiers
            return mods
            
        # Handle specific anomaly injections
        if self.config.inject_anomaly == "cardiac":
            mods["heart_rate"] = _rng.choice([30, -20])  # Tachy or brady
            mods["bp_systolic"] = 20
            
        elif self.config.inject_anomaly == "radiation":
            mods["radiation"] = _rng.uniform(10.0, 20.0)   # above shelter level
            
        elif self.config.inject_anomaly == "hypoxia":
            mods["spo2"] = -5
            mods["respiratory_rate"] = 8
            
        elif self.config.inject_anomaly == "fever":
            mods["temperature"] = _rng.uniform(1.0, 2.5)
            mods["heart_rate"] = 15
            
        elif self.config.inject_anomaly == "dehydration":
            mods["hydration"] = -30
            mods["heart_rate"] = 10
            mods["bp_systolic"] = -15
        
        return mods
    
    def determine_status(self, vitals: Vitals) -> HealthStatus:
        """Determine health status from the dashboard metrics only (Heart, SpO2, Sleep, Radiation).
        IL-6 and open alerts raise the status further in the API layer (_apply_alert_status)."""
        rad = vitals.radiation_dose_24h or 0
        rt = aegis_mission.radiation_thresholds()
        sleep = vitals.sleep_quality if vitals.sleep_quality is not None else 100

        # Critical conditions
        if (vitals.heart_rate > 150 or vitals.heart_rate < 40 or
                vitals.spo2 < 90 or rad >= rt["shelter"]):
            return HealthStatus.CRITICAL
        
        # Warning conditions
        if (vitals.heart_rate > 120 or vitals.heart_rate < 50 or
                vitals.spo2 < 94 or sleep < 50):
            return HealthStatus.WARNING
        
        # Caution conditions
        if (vitals.heart_rate > 100 or vitals.heart_rate < 55 or
                vitals.spo2 < 96 or sleep < 70 or rad >= rt["warning"]):
            return HealthStatus.CAUTION
        
        return HealthStatus.NOMINAL
    
    async def update_all_crew(self):
        """Update vitals for all crew members."""
        # PREVENT-1 recovery finished: back to nominal operations
        if self.config.scenario == "prevent_recovery" and \
                (datetime.utcnow() - self.scenario_started_at).total_seconds() > RECOVERY_SECONDS + 60:
            config = self.config.model_copy()
            config.scenario = None
            self.set_config(config)
        crew = db.get_all_crew()
        
        for member in crew:
            vitals = self.generate_vitals(member)
            status = self.determine_status(vitals)
            db.update_crew_vitals(member.id, vitals, status)
            
            # T7 fix: Accumulate radiation dose
            # Convert 24h dose to per-update increment (update every ~5 seconds)
            # radiation_dose_24h is mSv/day, we update ~17280 times/day at 1x
            if vitals.radiation_dose_24h:
                daily_dose = vitals.radiation_dose_24h
                # Scale by speed multiplier so higher speeds = more accumulation per tick
                increment = (daily_dose / 17280) * self.config.speed_multiplier
                db.update_cumulative_radiation(member.id, increment)
        
        # Increment mission time
        self.mission_day += (1 / (24 * 60 * 60)) * self.config.speed_multiplier
    
    async def run(self):
        """Main simulation loop."""
        self.running = True
        update_interval = 5.0 / self.config.speed_multiplier  # Base: 5 seconds
        
        while self.running:
            try:
                await self.update_all_crew()
                self.last_tick_at = datetime.utcnow()  # M5: Track successful tick
                self.last_error = None  # Clear error on success
                await asyncio.sleep(max(1.0, update_interval))
            except Exception as e:
                self.last_error = str(e)  # M5: Track error
                print(f"Simulator error: {e}")
                await asyncio.sleep(5)
    
    def start(self):
        """Start the simulation in background."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run())
    
    def stop(self):
        """Stop the simulation."""
        self.running = False
        if self._task:
            self._task.cancel()
    
    def set_config(self, config: SimulationConfig):
        """Update simulation configuration."""
        old_scenario = self.config.scenario
        self.config = config
        if config.scenario != old_scenario:
            self.scenario_started_at = datetime.utcnow()
        
        # SPE onset: the hours before the crew reaches the shelter add a one-time dose (fixed 3 mSv so the
        # demo is repeatable; the demo reset restores the seeded cumulative dose).
        if config.scenario == "spe_event" and old_scenario != "spe_event":
            crew = db.get_all_crew()
            for member in crew:
                db.update_cumulative_radiation(member.id, 3.0)
    
    def get_state(self) -> SimulationState:
        """Get current simulation state."""
        return SimulationState(
            running=self.running,
            mission_day=int(self.mission_day),
            speed_multiplier=self.config.speed_multiplier,
            active_scenario=self.config.scenario,
            last_update=datetime.utcnow(),
            last_tick_at=self.last_tick_at,
            last_error=self.last_error
        )
    
    def inject_anomaly(self, anomaly_type: str, crew_id: Optional[str] = None):
        """Inject an anomaly for demo purposes."""
        self.config.inject_anomaly = anomaly_type
        self.config.inject_crew_id = crew_id  # M1: Store target crew (None = all)
        
        # Log the injection
        db.log_event(
            db.EventType.SYSTEM,
            f"Anomaly injected: {anomaly_type}" + (f" for crew {crew_id}" if crew_id else " for all crew"),
            details={"anomaly": anomaly_type, "crew_id": crew_id}
        )
    
    def clear_anomaly(self):
        """Clear any active anomaly injection."""
        self.config.inject_anomaly = None
        self.config.inject_crew_id = None  # M1: Clear target crew


# ─────────────────────────────────────────────────────────────────────────────
# Global Simulator Instance
# ─────────────────────────────────────────────────────────────────────────────

_simulator: Optional[VitalsSimulator] = None

def get_simulator() -> VitalsSimulator:
    """Get the global simulator instance."""
    global _simulator
    if _simulator is None:
        _simulator = VitalsSimulator()
    return _simulator
