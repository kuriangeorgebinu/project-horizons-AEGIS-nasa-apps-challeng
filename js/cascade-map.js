const CM_FMT = {
  heart_rate: v => [Math.round(v), 'bpm'], spo2: v => [v.toFixed(1), '% SpO₂'], il6: v => [v.toFixed(1), 'IL-6 · modelled'],
  sleep_quality: v => [Math.round(v), '/100 sleep'], radiation_dose_24h: v => [v.toFixed(2), 'mSv / 24 h']
};
const CM_CIRC = 276.46;
const CM_PAIRS = [['solaris', 'gene'], ['gene', 'pulse'], ['pulse', 'mirror'], ['mirror', 'solaris']];

function _cmStrain(t) {
  const h = t.baseline != null ? t.baseline : CM_HEALTHY[t.metric], thr = t.threshold;   // personal / mission baseline
  const s = (t.value - h) / (thr - h);              // works for both directions (thr < h for SpO2 / sleep)
  return Math.max(0, Math.min(1.15, s));
}
function _cmToward(t) { return t.risk_direction === 'up' ? t.direction === 'rising' : t.direction === 'falling'; }
// THE status rule. The Cascade Map rings, the status card, Live Vitals, the crew strip and the captions all use it,
// so the dashboard speaks with one voice: out of range > limit forecast > moving toward the limit > steady.
const CM_DRIFT_AT = 0.35;                         // a third of the way to the limit AND moving toward it
function sysState(ts) {
  if (!ts || !ts.length) return 'ok';
  if (ts.some(t => t.out_of_range)) return 'out';
  if (ts.some(t => t.eta_min !== null && t.eta_min !== undefined)) return 'watch';
  if (ts.some(t => _cmToward(t) && _cmStrain(t) > CM_DRIFT_AT)) return 'watch';
  // Radiation and IL-6 are measured from each person's own baseline (they sit near 0 % at rest), so past halfway
  // to the limit is already worth watching, even before the trend fit has enough points to call a direction
  if (ts.some(t => t.baseline != null && _cmStrain(t) >= 0.5)) return 'watch';
  return 'ok';
}
function sysStates(entry) {
  const st = {};
  if (!entry || !entry.metrics) return st;
  Object.entries(CM_AGENTS).forEach(([a, ms]) => { const ts = ms.map(m => entry.metrics[m]).filter(Boolean); if (ts.length) st[a] = sysState(ts); });
  return st;
}
const sysAllOk = entry => { const st = sysStates(entry); return Object.keys(st).length > 0 && Object.values(st).every(v => v === 'ok'); };
// One source for "have the readings recovered?": the alert crew member's current forecast entry, whoever is selected.
// A shelter order is crew-wide, so it counts as recovered only when every crew member's radiation is steady.
function _crewRecovered(crewId, protocol) {
  const crew = (CM.last && CM.last.crew) || [];
  if (String(protocol || '').startsWith('SHELTER')) {
    return crew.length > 0 && crew.every(c => (sysStates(c).solaris || 'ok') === 'ok');
  }
  const e = crew.find(c => c.crew_member_id === crewId);
  return !!(e && sysAllOk(e) && !Object.values(sysStates(e)).includes('watch'));
}
function _crewOutCount(crewId) {
  const e = CM.last && (CM.last.crew || []).find(c => c.crew_member_id === crewId);
  return e ? Object.values(sysStates(e)).filter(v => v === 'out').length : null;
}
const SYS_WORD = { ok: 'Steady', watch: 'Heading for limit', out: 'Out of range' };

function renderCascadeMap(fc) {
  const svg = document.getElementById('cascade-map');
  if (!svg || !fc || !fc.metrics) return;
  const name = _crewDisplayName(fc.crew_member_id);
  const crewEl = document.getElementById('cm-crew'); if (crewEl) crewEl.textContent = name;
  const states = {};
  Object.entries(CM_AGENTS).forEach(([agent, metrics]) => {
    const ts = metrics.map(m => fc.metrics[m]).filter(Boolean);
    if (!ts.length) return;
    const strain = Math.max(...ts.map(_cmStrain));
    const out = ts.find(t => t.out_of_range);
    const eta = ts.filter(t => t.eta_min !== null).sort((a, b) => a.eta_min - b.eta_min)[0];
    const drift = ts.find(t => _cmToward(t) && _cmStrain(t) > CM_DRIFT_AT);
    const state = sysState(ts);
    states[agent] = state;
    const node = document.getElementById('node-' + agent);
    node.dataset.state = state;
    const main = ts[0];
    let [val, unit] = CM_FMT[main.metric](main.value);
    // 69.6 shown as "70" next to "out of range" (limit 70) reads as a contradiction: show the decimal at the line
    if (Number(val) === main.threshold && main.value !== main.threshold) val = main.value.toFixed(1);
    document.getElementById('cm-val-' + agent).textContent = val;
    document.getElementById('cm-unit-' + agent).textContent =
      agent === 'pulse' && ts[1] ? `bpm · ${ts[1].value.toFixed(0)}% O₂` : unit;
    document.getElementById('cm-gauge-' + agent).setAttribute('stroke-dasharray',
      `${(Math.min(strain, 1) * CM_CIRC).toFixed(1)} ${CM_CIRC}`);
    const word = out ? 'out of range' : eta ? `limit in ${fmtEta(eta.eta_min)}` :
                 drift ? (main.risk_direction === 'up' ? 'climbing ↗' : 'dropping ↘') :
                 state === 'watch' ? `${Math.round(Math.min(strain, 1) * 100)}% to limit` : 'steady';
    document.getElementById('cm-state-' + agent).textContent = word;
    (CM.strain = CM.strain || {})[agent] = strain; (CM.words = CM.words || {})[agent] = word;   // read by the 3D view
    const tip = document.getElementById('cm-tip-' + agent);
    if (tip) tip.textContent = ts.map(t => `${t.label}: ${t.value} ${t.unit} (limit ${t.threshold})`).join(' · ') +
      ` · ${Math.round(strain * 100)}% of the way to the limit · ${word}`;
    const line = document.getElementById('line-' + agent);
    if (line) { line.classList.toggle('s-watch', state === 'watch'); line.classList.toggle('s-out', state === 'out'); }
    svg.querySelectorAll(`.cm-particle[data-agent="${agent}"]`).forEach(p => {
      p.classList.toggle('on', state !== 'ok');
      p.style.fill = state === 'out' ? 'var(--red)' : 'var(--yellow)';
    });
  });
  // Strain spreading between neighbouring systems: glowing comets travel along strained links
  _cmEnsureComets();
  CM_PAIRS.forEach(([a, b]) => {
    const l = document.getElementById(`link-${a}-${b}`); if (!l) return;
    const sa = states[a], sb = states[b];
    const hot = sa === 'out' && sb === 'out', warm = sa !== 'ok' && sb !== 'ok' && !hot;
    l.classList.toggle('hot', hot);
    l.classList.toggle('warm', warm);
    document.querySelectorAll(`.cm-comet[data-pair="${a}-${b}"]`).forEach(c => {
      c.classList.toggle('on', hot || warm); c.classList.toggle('hot', hot);
    });
  });
  // A spike: when a system newly heads for (or crosses) its limit, a pulse fires from it into AEGIS and its neighbours
  const RANK = { ok: 0, watch: 1, out: 2 };
  if (CM.prevStates && CM.prevCrew === fc.crew_member_id) {
    Object.entries(states).forEach(([agent, st]) => {
      if (RANK[st] > RANK[CM.prevStates[agent] || 'ok']) _cmPropagate(agent, st);
    });
  }
  CM.prevStates = states; CM.prevCrew = fc.crew_member_id;
  // AEGIS centre: three segments = three strained systems make a cascade
  const strained = Object.values(states).filter(s => s !== 'ok').sort((x, y) => (y === 'out') - (x === 'out'));
  for (let i = 0; i < 3; i++) {
    const seg = document.getElementById('cm-seg-' + i);
    if (seg) seg.setAttribute('class', 'cm-seg ' + (strained.length ? (strained[i] || '') : 'ok'));
  }
  // Mode, in priority order: live alert for this person > prevention plan > forecast cascade > watching > steady
  const key = CREW_KEY_BY_ID[fc.crew_member_id];
  let pv = PREDICT_STATE.prevented && PREDICT_STATE.prevented.crew === fc.crew_member_id ? PREDICT_STATE.prevented : null;
  if (pv && pv.avertedAt && Object.values(states).some(v => v !== 'ok')) pv = null;   // never claim "healthy" over strained rings
  let mode, big, sub, cap;
  const outDom = Object.keys(states).filter(a => states[a] === 'out').map(a => a.toUpperCase());
  const watchDom = Object.keys(states).filter(a => states[a] === 'watch').map(a => a.toUpperCase());
  // A storm-shelter order is crew-wide: it takes over every crew member's map, not only the person on the alert card
  if (LIVE_ALERT.id && (LIVE_ALERT.crewKey === key || String(LIVE_ALERT.protocol || '').startsWith('SHELTER'))) {
    const p = LIVE_ALERT.protocol || '';
    mode = 'alert';
    big = p.startsWith('SHELTER') ? 'SHELTER' : p.startsWith('MEDIC') ? 'MEDICAL' : 'CASCADE';
    sub = 'ACT NOW';
    cap = p.startsWith('SHELTER') ? `<b>Radiation storm.</b> ${name} and the crew should be in the storm shelter.`
        : p.startsWith('MEDIC') ? `<b>${name} needs medical attention now.</b> See the alert panel.`
        : `<b>Cascade:</b> ${name}'s ${plainDomains(outDom)} are out of range together and feeding each other.`;
    if (LIVE_ALERT.recovered || (EMG.level && !EMG.acked && (Date.now() - EMG.since) / 1000 > 45 && _crewRecovered(EMG.crewId, EMG.protocol))) {   // same rule as the card and the banner: current readings decide the words
      sub = 'CONFIRM TO CLOSE';
      cap = `<b>Readings recovered.</b> The ${p || 'protocol'} alert stays open until a person confirms it.`;
    }
  } else if (pv) {
    mode = pv.avertedAt ? 'averted' : 'prevent';
    big = pv.avertedAt ? 'Avoided' : 'Fixing';
    sub = pv.avertedAt ? 'CASCADE ✓' : 'PREVENTION ON';
    cap = (pv.avertedAt ? `<b>Cascade avoided.</b> All four of ${name}'s systems are steady again. We acted before it happened.`
                        : `<b>Prevention plan running for ${name}.</b> Watch the rings come back down.`) +
          ` <span class="demo-note" title="In this demo the simulator is scripted to reverse the drift once PREVENT-1 is accepted. On a mission AEGIS would only report whether the readings actually recover.">Demo: the simulated readings are scripted to recover after PREVENT-1</span>`;
  } else if (fc.risk === 'cascade' && fc.cascade_eta_min !== null &&
             !((PREDICT_STATE.suppress[fc.crew_member_id] || 0) > Date.now())) {
    mode = 'cascade';
    _syncEtaEnd(fc.crew_member_id, fc.cascade_eta_min);
    big = outDom.length >= 3 ? 'NOW' : _cmClock(); sub = big === 'NOW' ? 'CASCADE FORMING' : 'TO CASCADE';
    const parts = fc.upcoming.map(u => PLAIN_TREND[u.metric]).filter(Boolean);
    cap = `<b>Early warning:</b> ${name}'s ${plainList(parts) || plainDomains(watchDom) + ' are drifting'}. ` +
          `Three systems will be strained in ${_etaText(fc.crew_member_id, fc.cascade_eta_min)} unless we act.`;
  } else if (fc.risk === 'converged') {
    mode = 'alert'; big = 'NOW'; sub = 'CASCADE FORMING';
    cap = `<b>It's happening:</b> ${name}'s ${plainDomains(outDom)} are out of range together. AEGIS is raising the alert.`;
  } else if (strained.length) {
    mode = 'watch'; big = `${strained.length} of 4`; sub = 'STRAINED';
    if (outDom.length >= 3) { mode = 'alert'; big = 'NOW'; sub = 'CASCADE FORMING'; }
    cap = `Keeping an eye on ${name}'s ${plainDomains([...outDom, ...watchDom])}. ` +
          (strained.length >= 2 ? 'One more strained system would make a cascade.' : 'Nothing needs action yet.');
  } else {
    mode = 'none'; big = 'Steady'; sub = 'ALL SYSTEMS';
    cap = `All four systems are steady for ${name}. Each ring shows how close a reading is to its limit.`;
  }
  // A confirmed protocol is still news: the map says so instead of "Steady" / "N of 4" (same rule as the alert card)
  if (mode !== 'alert' && !CM.resetting) {
    const forMe = c => c && (String(c.protocol || '').startsWith('SHELTER') || c.crewId === fc.crew_member_id);
    const done = LIVE_ALERT.completed;
    if (EMG.level && EMG.acked && forMe({ protocol: EMG.protocol, crewId: EMG.crewId })) {
      mode = 'watch'; big = String(EMG.protocol || '').split('-')[0]; sub = 'IN EFFECT';
      cap = `<b>${EMG.protocol} confirmed</b> and in effect. Waiting for ${name}'s readings to return to a safe range.`;
    } else if (done && Date.now() < done.until && forMe(done) && !outDom.length) {
      mode = strained.length ? 'watch' : 'averted'; big = 'Done'; sub = strained.length ? 'SETTLING' : `${done.protocol} ✓`;
      cap = strained.length ? `<b>${done.protocol} complete.</b> ${name}'s readings are back below the alert level and still settling.`
                            : `<b>${done.protocol} complete.</b> The readings that raised it are back in range for ${name}.`;
    }
  }
  if (CM.resetting) {        // Stop / reset in progress: the readings are still settling, so don't call them steady yet
    mode = 'watch'; big = 'RESET'; sub = 'SETTLING';
    cap = 'Resetting to a clean start… The readings are settling back to normal; the map will say Steady once two fresh checks agree.';
  }
  const centre = document.getElementById('node-aegis');
  centre.dataset.mode = mode;
  const bigEl = document.getElementById('cm-big');
  bigEl.textContent = big;
  bigEl.style.fontSize = big.length > 6 ? '15px' : '';     // CASCADE / SHELTER / MEDICAL fit inside the ring
  document.getElementById('cm-sub').textContent = sub;
  document.getElementById('cm-caption').innerHTML = cap;
  if (mode === 'averted' && CM.prevMode !== 'averted') _cmRipple();
  CM.prevMode = CM.mode = mode;
}

// Propagation effects on the Cascade Map
const CM_POS = { solaris: [130, 80], gene: [510, 80], pulse: [510, 250], mirror: [130, 250] };
function _cmEnsureComets() {
  const g = document.getElementById('cm-comets');
  if (!g || g.dataset.built) return;
  CM_PAIRS.forEach(([a, b]) => {
    const d = document.getElementById(`link-${a}-${b}`)?.getAttribute('d'); if (!d) return;
    [0, 0.9, 1.8].forEach(begin => {
      const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      c.setAttribute('class', 'cm-comet'); c.setAttribute('r', '3.6'); c.dataset.pair = `${a}-${b}`;
      c.innerHTML = `<animateMotion dur="2.7s" begin="${begin}s" repeatCount="indefinite" path="${d}"/>`;
      g.appendChild(c);
    });
  });
  g.dataset.built = '1';
}
function _cmShot(d, cls, reverse, dur) {
  const fx = document.getElementById('cm-fx'); if (!fx || !d) return;
  const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
  c.setAttribute('class', 'cm-spark ' + cls); c.setAttribute('r', '5');
  const m = document.createElementNS('http://www.w3.org/2000/svg', 'animateMotion');
  m.setAttribute('dur', dur + 's'); m.setAttribute('fill', 'freeze'); m.setAttribute('path', d); m.setAttribute('begin', 'indefinite');
  if (reverse) { m.setAttribute('keyPoints', '1;0'); m.setAttribute('keyTimes', '0;1'); m.setAttribute('calcMode', 'linear'); }
  c.appendChild(m); fx.appendChild(c);
  try { m.beginElement(); } catch (e) {}
  setTimeout(() => c.remove(), dur * 1000 + 80);
}
function _cmPropagate(agent, state) {
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const fx = document.getElementById('cm-fx'); if (!fx) return;
  const [x, y] = CM_POS[agent] || [320, 165];
  for (let i = 0; i < 2; i++) setTimeout(() => {
    const r = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    r.setAttribute('class', 'cm-burst'); r.setAttribute('cx', x); r.setAttribute('cy', y);
    r.style.stroke = state === 'out' ? 'var(--red)' : 'var(--cm-amber)';
    fx.appendChild(r); setTimeout(() => r.remove(), 700);
  }, i * 220);
  _cmShot(document.getElementById('line-' + agent)?.getAttribute('d'), state, false, 0.9);       // into AEGIS
  CM_PAIRS.forEach(([a, b]) => {                                                                   // out to neighbours
    if (a !== agent && b !== agent) return;
    _cmShot(document.getElementById(`link-${a}-${b}`)?.getAttribute('d'), state, b === agent, 1.3);
  });
  if (typeof playTone === 'function') playTone(state === 'out' ? 300 : 440, 0.12);
}

function _cmClock() {
  const s = Math.max(0, Math.round((CM.etaEnd - Date.now()) / 1000));
  if (s === 0) return 'NOW';
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}
function _cmRipple() {
  const fx = document.getElementById('cm-fx'); if (!fx) return;
  [0, 350].forEach(delay => setTimeout(() => {
    const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    c.setAttribute('class', 'cm-ripple'); c.setAttribute('cx', 320); c.setAttribute('cy', 165);
    fx.appendChild(c); setTimeout(() => c.remove(), 1500);
  }, delay));
}
// Countdown ticks every second between data refreshes
setInterval(() => {
  if (CM.mode === 'cascade' && CM.etaEnd) {
    const b = document.getElementById('cm-big'), c = _cmClock();
    if (b && b.textContent !== 'NOW') b.textContent = c;
    const sub = document.getElementById('cm-sub'); if (sub && c === 'NOW') sub.textContent = 'CASCADE FORMING';
  }
  if (CM.etaEnd) _syncPredictTitle();
}, 1000);

// Crew at a glance: one row per crew member, a dot per system (shown in the alert panel when all is clear)
function renderCrewGlance() {
  const host = document.getElementById('alert-agents');
  if (!host || !CM.last) return;
  const order = ['solaris', 'gene', 'pulse', 'mirror'];
  const rows = (CM.last.crew || []).map(c => {
    const key = CREW_KEY_BY_ID[c.crew_member_id];
    const st = sysStates(c);
    const worst = Object.values(st).includes('out') ? 'out' : Object.values(st).includes('watch') ? 'watch' : 'ok';
    const off = order.filter(a => st[a] === 'out').map(a => a.toUpperCase());
    const word = c.risk === 'cascade' ? `cascade in ${_etaText(c.crew_member_id, c.cascade_eta_min)}` :
                 worst === 'out' ? `${plainDomains(off)} off` : worst === 'watch' ? 'drifting' : 'steady';
    const role = String(c.role || '').replace(/_/g, ' ');
    return `<button type="button" class="cg-row${c.crew_member_id === DASH_API.selectedCrewId ? ' sel' : ''}" data-key="${key || ''}"
      title="Show ${_crewDisplayName(c.crew_member_id)} on the dashboard">
      <span class="cg-name">${_crewDisplayName(c.crew_member_id)}<small>${role}</small></span>
      <span class="cg-dots">${order.map(a => `<i class="cg-dot ${st[a]}" title="${a.toUpperCase()}: ${st[a] === 'ok' ? 'steady' : st[a] === 'out' ? 'out of range' : 'heading for its limit'}"></i>`).join('')}</span>
      <span class="cg-state ${worst}">${word}</span></button>`;
  }).join('');
  host.innerHTML = `<div class="crew-glance"><div class="cg-head">Crew at a glance · radiation · inflammation · heart · sleep</div>${rows}</div>`;
  host.querySelectorAll('.cg-row').forEach(b => b.addEventListener('click', () => { if (b.dataset.key) selectCrew(b.dataset.key); }));
}

// Crew strip: names, roles, heart rate and sleep, and the four ring states, from the same data as the Cascade Map
function renderCrewStrip() {
  const host = document.getElementById('crew-strip');
  if (!host || !CM.last) return;
  const order = ['solaris', 'gene', 'pulse', 'mirror'];
  (CM.last.crew || []).forEach(c => {
    const key = CREW_KEY_BY_ID[c.crew_member_id];
    const chip = host.querySelector(`.cs-chip[data-key="${key}"]`); if (!chip) return;
    const st = sysStates(c), m = c.metrics || {};
    const hr = m.heart_rate ? Math.round(m.heart_rate.value) : '–', sl = m.sleep_quality ? Math.round(m.sleep_quality.value) : '–';
    chip.querySelector('.cs-v').innerHTML = `<span>${hr} bpm · sleep ${sl}</span><span class="cg-dots">${order.map(a =>
      `<i class="cg-dot ${st[a] || 'ok'}" title="${a.toUpperCase()}: ${SYS_WORD[st[a] || 'ok']}"></i>`).join('')}</span>`;
    const off = order.filter(a => st[a] && st[a] !== 'ok');
    chip.title = `${_crewDisplayName(c.crew_member_id)}: ` + (off.length ? off.map(a => `${a.toUpperCase()} ${SYS_WORD[st[a]].toLowerCase()}`).join(', ') : 'all four systems steady') + ' · click to show';
    chip.classList.toggle('selected', c.crew_member_id === DASH_API.selectedCrewId);
  });
}

// Live Vitals: the values and words come from the same trend data and rule as the rings (one voice)
