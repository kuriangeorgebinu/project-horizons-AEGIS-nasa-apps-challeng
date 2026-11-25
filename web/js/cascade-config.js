// ============================================
// CASCADE MAP: live view of the selected crew member's four systems around AEGIS (data: /api/predict)
// Ring = how far a reading has moved from a healthy value toward its agent's limit (full ring = at the limit).
// ============================================
const CM = { last: null, mode: null, etaEnd: null, prevMode: null, tick: null };
try {      // a reset just reloaded the page: keep showing "settling" until the readings have actually recovered
  const t = Number(sessionStorage.getItem('aegis.resettingAt') || 0);
  sessionStorage.removeItem('aegis.resettingAt');
  if (t && Date.now() - t < 60000) CM.resetting = { started: t, doneAt: t, fresh: 0 };
} catch (e) {}
const CM_AGENTS = { solaris: ['radiation_dose_24h'], gene: ['il6'], pulse: ['heart_rate', 'spo2'], mirror: ['sleep_quality'] };
const CM_HEALTHY = { heart_rate: 60, spo2: 99, il6: 3, sleep_quality: 90, radiation_dose_24h: 1.84 };   // ring = 0 here
// Mission profile (radiation background + SOLARIS thresholds) from the backend; Mars-transit defaults offline
const MISSION = { label: 'Mars transit (deep-space cruise)', background: 1.84, warning: 3.68, shelter: 9.2,
                  source: 'Curiosity RAD, cruise to Mars', career_limit_msv: 600 };
(async function loadMission() {
  try {
    const r = await fetch(`${DASH_API.baseUrl}/api/mission`);
    if (r.ok) { Object.assign(MISSION, await r.json()); CM_HEALTHY.radiation_dose_24h = MISSION.background; }
    renderMissionBar();
  } catch (e) { /* keep defaults */ }
})();
// ============================================
