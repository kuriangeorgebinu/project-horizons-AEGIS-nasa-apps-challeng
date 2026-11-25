// EMERGENCY MODE: PROTECT alert -> red edge + emergency strip with a running timer; cascade -> amber
// ============================================
const EMG = { level: null, since: 0, alertId: null, acked: false, ackedAt: 0, crewId: null, protocol: null, label: '' };
function _parseUtc(ts) { return new Date(ts + (/[zZ]|[+-]\d\d:?\d\d$/.test(ts) ? '' : 'Z')).getTime(); }
function _setEmergency(e) {
  const body = document.body;
  if (!e) {
    // An emergency just ended (readings recovered): ECHO checks in on that person. The story demo does its own.
    if (EMG.level && EMG.echoedFor !== EMG.alertId && !(typeof STORY !== 'undefined' && STORY.on) && typeof echoRefresh === 'function') {
      const kind = EMG.level === 'protect' ? (EMG.protocol === 'SHELTER-1' ? 'shelter' : 'medical') : 'cascade';
      setTimeout(() => { if (typeof ECHO !== 'undefined') ECHO.lastProactive = null; echoRefresh(kind); }, 1500);
    }
    EMG.level = null; EMG.alertId = null; EMG.acked = false;
    body.classList.remove('emergency', 'cascade-alert', 'acked');
    const t = document.getElementById('alert-timer'); if (t) t.remove();
    return;
  }
  EMG.level = e.level; EMG.alertId = e.alertId; EMG.acked = false; EMG.crewId = e.crewId; EMG.protocol = e.protocol;
  body.classList.remove('acked');
  EMG.since = e.since && !isNaN(_parseUtc(e.since)) ? _parseUtc(e.since) : Date.now();
  body.classList.toggle('emergency', e.level === 'protect');
  body.classList.toggle('cascade-alert', e.level === 'cascade');
  const label = e.level === 'protect' ? (e.protocol === 'SHELTER-1' ? '☢ RADIATION EMERGENCY' : '⚕ MEDICAL EMERGENCY') : '⚡ HEALTH CASCADE';
  const txt = document.getElementById('mb-em-text');
  EMG.label = `${label} · ${e.protocol} · ${e.name}`;
  if (txt) txt.textContent = EMG.label;
  EMG.baseLabel = EMG.label;
  if (!document.getElementById('alert-timer')) {
    const hdr = document.querySelector('#alert-panel .alert-header');
    if (hdr) { const t = document.createElement('span'); t.id = 'alert-timer'; t.className = 'alert-timer'; hdr.appendChild(t); }
  }
  _tickEmergency();
}
function _tickEmergency() {
  if (!EMG.level) return;
  const em = document.getElementById('mb-em-text');
  const emRec = !EMG.acked && (Date.now() - EMG.since) / 1000 > 45 && _crewRecovered(EMG.crewId, EMG.protocol);
  if (em && EMG.baseLabel) em.textContent = EMG.baseLabel + (emRec ? ' · readings recovered, awaiting confirmation' : '');
  // ECHO checks in once the readings recover, whether or not anyone has confirmed yet: a check-in closes nothing,
  // the alert still waits for its person. (The 2-minute demo runs its own ECHO beat.)
  if (emRec && EMG.echoedFor !== EMG.alertId && !(typeof STORY !== 'undefined' && STORY.on) && typeof echoRefresh === 'function') {
    EMG.echoedFor = EMG.alertId;
    const kind = EMG.level === 'protect' ? (EMG.protocol === 'SHELTER-1' ? 'shelter' : 'medical') : 'cascade';
    setTimeout(() => { if (typeof ECHO !== 'undefined') ECHO.lastProactive = null; echoRefresh(kind); }, 1500);
  }
  const s = Math.max(0, Math.floor((Date.now() - EMG.since) / 1000));
  const txt = `Alert +${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
  const a = document.getElementById('mb-em-timer'); if (a) a.textContent = txt;
  const b = document.getElementById('alert-timer'); if (b) { b.textContent = txt; b.title = 'Time since the alert was raised'; }
}
setInterval(_tickEmergency, 1000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) _tickEmergency(); });
// Confirming a protocol acknowledges the alert; the strip stays (steady, not pulsing) until the readings that
// triggered it are back in a safe range, because a confirmed plan is not yet a solved problem.
function _ackEmergency() {
  if (!EMG.level) return;
  EMG.acked = true; EMG.ackedAt = Date.now(); EMG.ackSource = _ackSource();
  document.body.classList.add('acked');
  const txt = document.getElementById('mb-em-text');
  if (txt) txt.textContent = `${EMG.protocol} confirmed · ${EMG.label.split(' · ').pop()} · waiting for readings to return to a safe range`;
}
async function _checkResolved() {
  if (!EMG.level || !EMG.acked) return;
  if (Date.now() - EMG.ackedAt > 5 * 60000) { _setEmergency(null); return; }      // give up waiting after 5 min
  try {
    const B = DASH_API.baseUrl;
    let ok = false;
    if (EMG.protocol === 'SHELTER-1') {
      const crew = await (await fetch(`${B}/api/crew`)).json();
      ok = crew.every(c => !c.vitals || (c.vitals.radiation_dose_24h ?? 0) < MISSION.shelter);
    } else if (EMG.protocol === 'MEDIC-1') {
      const c = await (await fetch(`${B}/api/crew/${EMG.crewId}`)).json();
      ok = c.vitals && c.vitals.spo2 >= 90 && c.vitals.heart_rate >= 40 && c.vitals.heart_rate <= 150;
    } else {
      const p = await (await fetch(`${B}/api/predict`)).json();
      const me = (p.crew || []).find(x => x.crew_member_id === EMG.crewId);
      ok = !me || (me.out_now || []).length < 3;
    }
    if (ok) {
      LIVE_ALERT.completed = { alertId: EMG.alertId, protocol: EMG.protocol, crewId: EMG.crewId, at: EMG.ackedAt, doneAt: Date.now(),
                               until: Date.now() + 45000, name: EMG.label.split(' · ').pop(), source: EMG.ackSource || 'here' };
      _setEmergency(null);
    }
  } catch (e) { /* keep waiting */ }
}
setInterval(_checkResolved, 3000);

// ============================================
