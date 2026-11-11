// Live vitals: each bar shows the "within normal limits" (WNL) band and a marker for the reading.
// Bands: adult resting heart rate 60-100 bpm and SpO2 95-100% (standard clinical ranges); sleep score >= 70
// (MIRROR's "good" line); IL-6 up to the person's own pre-flight limit (1.5x baseline + 1.5, set by GENE).
const VITAL_WNL = {
  hr:    { min: 30, max: 180, lo: 60, hi: 100, unit: 'bpm', note: 'adult resting heart rate' },
  spo2:  { min: 80, max: 100, lo: 95, hi: 100, unit: '%',   note: 'blood oxygen saturation' },
  il6:   { min: 0,  max: null, lo: 0, hi: null, unit: 'pg/mL', note: "this person's pre-flight limit (modelled value)" },
  sleep: { min: 0,  max: 100, lo: 70, hi: 100, unit: '/100', note: 'sleep-quality score' },
};
let IL6_LIMIT = null;
function setVitalBand(k, value, agentState) {
  const c = Object.assign({}, VITAL_WNL[k]);
  if (k === 'il6') { c.hi = IL6_LIMIT || 5.8; c.max = Math.max(c.hi * 2, (value || 0) * 1.1); }
  const pct = x => Math.max(0, Math.min(100, (x - c.min) / (c.max - c.min) * 100));
  const bar = document.getElementById(`${k}-bar`), band = document.getElementById(`${k}-band`), mk = document.getElementById(`${k}-marker`);
  const inBand = value >= c.lo && value <= c.hi;
  const color = agentState === 'out' ? 'var(--red)' : (!inBand || agentState === 'watch') ? 'var(--yellow)' : 'var(--green)';
  if (band) { band.style.left = pct(c.lo) + '%'; band.style.width = (pct(c.hi) - pct(c.lo)) + '%'; }
  if (mk) { mk.style.left = pct(value) + '%'; mk.style.background = color; mk.style.color = color; }
  if (bar) { bar.classList.remove('empty'); bar.title = `WNL ${c.lo}–${+c.hi.toFixed ? +c.hi.toFixed(1) : c.hi} ${c.unit} (${c.note}) · now ${+Number(value).toFixed(1)} ${c.unit}`; }
  const word = inBand ? 'WNL' : value < c.lo ? 'Below WNL ↓' : 'Above WNL ↑';
  return { inBand, color, word, lo: c.lo, hi: c.hi };
}
const VITAL_ROWS = { heart_rate: 'hr', spo2: 'spo2', il6: 'il6', sleep_quality: 'sleep' };
function syncVitalsToMap(entry) {
  if (!entry || !entry.metrics) return;
  const col = { ok: 'var(--green)', watch: 'var(--yellow)', out: 'var(--red)' };
  Object.entries(VITAL_ROWS).forEach(([metric, k]) => {
    const t = entry.metrics[metric]; if (!t) return;
    const state = sysState([t]);
    const v = document.getElementById(`${k}-value`), fill = document.getElementById(`${k}-fill`), stEl = document.getElementById(`${k}-status`);
    const val = metric === 'heart_rate' ? Math.round(t.value) : metric === 'spo2' ? `${Math.round(t.value)}%`
              : metric === 'il6' ? Number(t.value).toFixed(1)
              : `${Math.round(t.value) === t.threshold && t.value < t.threshold ? Number(t.value).toFixed(1) : Math.round(t.value)}%`;
    if (metric === 'il6' && t.threshold) IL6_LIMIT = t.threshold;
    const band = setVitalBand(k, Number(t.value), state);
    const word = state === 'out' ? 'Out of range' : t.eta_min !== null && t.eta_min !== undefined ? `Limit in ${fmtEta(t.eta_min)}`
               : state === 'watch' ? (_cmToward(t) ? (t.risk_direction === 'up' ? 'Rising ↗' : 'Falling ↘') : `${Math.round(Math.min(1, _cmStrain(t)) * 100)}% to limit`) : band.word;
    if (v) { v.textContent = val; v.style.color = band.color; }
    if (fill) { fill.style.width = `${Math.round(Math.min(1, _cmStrain(t)) * 100)}%`; fill.style.background = band.color; }
    if (stEl) { stEl.textContent = word; stEl.style.color = band.color; stEl.title = `${t.label}: ${t.value} ${t.unit || ''} · limit ${t.threshold} · ${Math.round(Math.min(1, _cmStrain(t)) * 100)}% of the way to the limit`; }
  });
  const r = entry.metrics.radiation_dose_24h, r24 = document.getElementById('rad24-value'), r24s = document.getElementById('rad24-status');
  if (r && r24) {
    const state = sysState([r]);
    r24.textContent = `${Number(r.value).toFixed(2)} mSv`; r24.style.color = col[state];
    if (r24s) { r24s.textContent = `· ${state === 'out' ? (r.value >= MISSION.shelter ? 'SHELTER' : 'Out of range') : state === 'watch' ? 'Heading for limit' : 'WNL'} (${(r.value / MISSION.background).toFixed(1)}× background)`; r24s.style.color = col[state]; }
  }
  VITALS_SYNCED_AT = Date.now();
}
let VITALS_SYNCED_AT = 0;

async function refreshPredict() {
  const t0 = Date.now();
  if (typeof _tickEmergency === 'function') _tickEmergency();
  try {
    const r = await fetch(`${DASH_API.baseUrl}/api/predict`);
    if (!r.ok) return;
    const d = await r.json();
    CM.last = d;
    const now = Date.now();
    if (CM.resetting) {
      const allOk = (d.crew || []).every(c => sysAllOk(c) && c.risk !== 'cascade' && c.risk !== 'converged'
        && (!c.metrics.radiation_dose_24h || c.metrics.radiation_dose_24h.value <= MISSION.background * 1.03));   // storm residue drained
      if (CM.resetting.doneAt && t0 > CM.resetting.doneAt) CM.resetting.fresh = allOk ? CM.resetting.fresh + 1 : 0;
      if (CM.resetting.fresh >= 2 || now - CM.resetting.started > 90000) CM.resetting = null;
    }
    const forSel = c => (typeof STORY !== 'undefined' && STORY.on) || c.crew_member_id === DASH_API.selectedCrewId;
    let top = (d.crew || [])
      .filter(c => forSel(c) && c.risk === 'cascade' && !((PREDICT_STATE.suppress[c.crew_member_id] || 0) > now))
      .sort((a, b) => a.cascade_eta_min - b.cascade_eta_min)[0] || null;
    // The forecast came true (≥3 domains now out of range) but AEGIS hasn't raised the cascade yet
    // (next analysis pass, ≤10 s): keep the PREDICT card up as "cascade forming" instead of flashing ALL CLEAR.
    if (!top && PREDICT_STATE.lastCrew) {
      const c = (d.crew || []).find(x => x.crew_member_id === PREDICT_STATE.lastCrew && forSel(x) && x.risk === 'converged');
      if (c) top = c;
    }
    PREDICT_STATE.top = top;
    const mine = (d.crew || []).find(c => c.crew_member_id === DASH_API.selectedCrewId);
    renderTrendChips(mine);
    // PREVENT-1 in progress: track the outcome until every domain is back in range (cascade averted)
    const pv = PREDICT_STATE.prevented;
    if (pv) {
      const e = (d.crew || []).find(c => c.crew_member_id === pv.crew);
      PREDICT_STATE.preventedEntry = e || null;
      const inRange = e && e.out_now.length === 0 && !e.upcoming.length && sysAllOk(e) && Date.now() - pv.at > 20000;
      // After "avoided", a new strain (e.g. the solar storm) is a new event: drop the old outcome instead of showing it
      // (only a real new event ends it: a system out of range, like the storm; a brief amber flicker while readings settle
      //  just shows the live picture for a moment and the outcome comes back)
      if (pv.avertedAt && e && Object.values(sysStates(e)).includes('out')) { PREDICT_STATE.prevented = null; PREDICT_STATE.preventedEntry = null; }
      if (inRange && !pv.avertedAt) {
        pv.avertedAt = Date.now();
        if (typeof playTone === 'function') { playTone(523, 0.12); setTimeout(() => playTone(659, 0.12), 150); setTimeout(() => playTone(784, 0.2), 300); }
        if (typeof typeResponse === 'function') typeResponse(`Good news: all four of ${_crewDisplayName(pv.crew)}'s systems are steady again. The prevention plan worked, and we avoided a bigger problem.`);
      }
      // Keep the outcome on screen for 45 s, then return to normal monitoring (failsafe: 8 min)
      if (PREDICT_STATE.prevented && (pv.avertedAt && Date.now() - pv.avertedAt > 45000) || Date.now() - pv.at > 8 * 60000) {
        PREDICT_STATE.prevented = null;
        PREDICT_STATE.preventedEntry = null;
        PREDICT_STATE.suppress[pv.crew] = Date.now() + 60000;
      }
    }
    // The map first: the status card reads the ring states it sets, so both always describe the same moment
    renderCascadeMap(mine);
    syncVitalsToMap(mine);
    renderCrewStrip();
    if (!LIVE_ALERT.id && Date.now() >= LIVE_ALERT.pauseUntil && !_renderProtocolState()) {
      const pvNow = PREDICT_STATE.prevented && (STORY.on || PREDICT_STATE.prevented.crew === DASH_API.selectedCrewId) ? PREDICT_STATE.prevented : null;
      if (pvNow && !(pvNow.avertedAt && PREDICT_STATE.preventedEntry && !sysAllOk(PREDICT_STATE.preventedEntry))) renderPreventedPanel(PREDICT_STATE.preventedEntry);
      else renderPredictPanel(PREDICT_STATE.top);
    }
    if (CM_DETAIL.agent) renderNodeDetail();
    if (TREND_OPEN) drawTrend();
  } catch (e) { /* backend offline: keep current display */ }
}

async function openTrend(metric) {
  if (!TREND_META[metric]) return;
  TREND_OPEN = metric;
  document.getElementById('hrModal').classList.add('open');
  await drawTrend();
  announce(`${TREND_META[metric].label} trend expanded`);
}

async function drawTrend() {
  const m = TREND_OPEN;
  const svg = document.getElementById('expanded-svg');
  if (!m || !svg) return;
  const meta = TREND_META[m];
  let t;
  try {
    const r = await fetch(`${DASH_API.baseUrl}/api/crew/${DASH_API.selectedCrewId}/trends?series=true`);
    if (!r.ok) return;
    t = (await r.json()).metrics[m];
  } catch (e) { return; }
  if (!t || !t.series || t.series.length < 2 || TREND_OPEN !== m) return;
  const crewName = (typeof CREW_DATA !== 'undefined' && CREW_DATA[selectedCrew]) ? CREW_DATA[selectedCrew].firstName : '';
  document.getElementById('modal-title').textContent = `${meta.icon} ${meta.label} — ${crewName} · ${meta.agent}`;

  const W = 520, H = 160, P = 34;
  const ser = t.series;                          // [[minutes (0 = now), value], ...]
  const hasFc = t.slope_per_min !== null && t.fitted_now !== null && t.direction !== 'steady';
  const fcEnd = hasFc ? Math.min(t.eta_min !== null ? Math.max(t.eta_min * 1.2, 1) : 3, 20) : 0;
  const x0 = ser[0][0], x1 = Math.max(0.01, fcEnd);
  const fcVal = hasFc ? t.fitted_now + t.slope_per_min * fcEnd : null;
  const vals = ser.map(p => p[1]).concat([t.threshold]).concat(hasFc ? [fcVal] : []);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = (hi - lo) * 0.12 || 1; lo -= pad; hi += pad;
  const X = v => P + (W - 2 * P) * (v - x0) / (x1 - x0);
  const Y = v => P / 2 + (H - 1.5 * P) * (1 - (v - lo) / (hi - lo));
  const f = v => Number(v).toFixed(meta.dp);
  let html = '';
  for (let i = 0; i <= 4; i++) {
    const v = hi - (hi - lo) * i / 4, y = Y(v);
    html += `<line class="chart-grid" x1="${P}" y1="${y}" x2="${W - P}" y2="${y}"/>`;
    html += `<text class="chart-label" x="${P - 5}" y="${y + 4}" text-anchor="end">${f(v)}</text>`;
  }
  // Agent threshold, "now" marker, history, forecast
  html += `<line class="chart-threshold" x1="${P}" y1="${Y(t.threshold)}" x2="${W - P}" y2="${Y(t.threshold)}"/>`;
  html += `<text class="chart-label" x="${P + 4}" y="${Y(t.threshold) - 4}" text-anchor="start" style="fill:var(--orange)">${meta.agent} threshold ${t.threshold}</text>`;
  html += `<line class="chart-now" x1="${X(0)}" y1="${P / 2}" x2="${X(0)}" y2="${H - P}"/>`;
  html += `<polyline class="chart-line" points="${ser.map(p => `${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(' ')}"/>`;
  if (hasFc) {
    html += `<polyline class="chart-forecast" points="${X(0)},${Y(t.fitted_now)} ${X(fcEnd)},${Y(fcVal)}"/>`;
    if (t.eta_min !== null) html += `<circle cx="${X(t.eta_min)}" cy="${Y(t.threshold)}" r="4" style="fill:var(--yellow)"/>`;
  }
  const last = ser[ser.length - 1];
  html += `<circle class="chart-dot" cx="${X(last[0])}" cy="${Y(last[1])}" r="4"/>`;
  html += `<text class="chart-value" x="${X(last[0])}" y="${Y(last[1]) - 10}" text-anchor="middle">${f(last[1])}</text>`;
  const tickVals = [x0, x0 * 2 / 3, x0 / 3, 0].concat(x1 > 0.5 ? [x1] : []);
  tickVals.forEach(v => {
    html += `<text class="chart-label" x="${X(v)}" y="${H - 6}" text-anchor="middle">${Math.abs(v) < 0.05 ? 'now' : (v > 0 ? '+' : '') + v.toFixed(1) + ' min'}</text>`;
  });
  svg.innerHTML = html;

  const ys = ser.map(p => p[1]);
  document.getElementById('hr-avg').textContent = f(ys.reduce((a, b) => a + b, 0) / ys.length);
  document.getElementById('hr-min').textContent = f(Math.min(...ys));
  document.getElementById('hr-max').textContent = f(Math.max(...ys));
  document.getElementById('hr-avg-label').textContent = `Average ${meta.unit}`;
  document.getElementById('hr-min-label').textContent = `Min ${meta.unit}`;
  document.getElementById('hr-max-label').textContent = `Max ${meta.unit}`;
  const fcEl = document.getElementById('trend-forecast');
  if (t.eta_min !== null) { fcEl.textContent = `${fmtEta(t.eta_min)}`; fcEl.style.color = 'var(--yellow)'; }
  else if (t.out_of_range) { fcEl.textContent = 'Out of range'; fcEl.style.color = 'var(--orange)'; }
  else { fcEl.textContent = t.direction === 'steady' ? 'Steady' : (t.direction === 'rising' ? '↗ Rising' : '↘ Falling'); fcEl.style.color = 'var(--green)'; }
  const mins = Math.round(-x0);
  document.getElementById('trend-note').textContent =
    `Last ${mins} min of telemetry. Trend fitted on the last 2 min` +
    (t.slope_per_min !== null ? ` (${t.slope_per_min > 0 ? '+' : ''}${Number(t.slope_per_min).toFixed(meta.dp + 1)} ${meta.unit}/min, R² ${t.r2})` : '') +
    (t.eta_min !== null ? `. ${meta.agent} forecasts it crosses ${t.threshold} ${meta.unit} in ${fmtEta(t.eta_min)} if the trend continues.`
                        : t.out_of_range ? `. Already beyond the ${meta.agent} threshold (${t.threshold} ${meta.unit}).`
                        : `. No threshold crossing forecast within ${20} min.`);
}

setInterval(refreshPredict, 5000);
setTimeout(refreshPredict, 300);   // first screen fills straight away

// Extended history for expanded view (last 20 readings)
