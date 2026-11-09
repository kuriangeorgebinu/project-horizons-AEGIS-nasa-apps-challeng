// ============================================
// WHAT-IF SCENARIO SIMULATOR — the five dashboard metrics, evaluated by the real agents
// (POST /api/whatif runs PULSE / GENE / MIRROR / SOLARIS rules + AEGIS decision; nothing is stored)
// ============================================
const WHATIF_METRICS = [
  { key: 'heart_rate', label: 'Heart rate', icon: '💓', agent: 'pulse', unit: ' bpm', min: 40, max: 160, step: 1, dec: 0,
    level: v => (v > 120 || v < 40) ? 'critical' : v > 100 ? 'warning' : 'normal', hint: 'PULSE: >100 high · >120 alert · >150 emergency' },
  { key: 'spo2', label: 'SpO₂', icon: '🫁', agent: 'pulse', unit: '%', min: 85, max: 100, step: 0.5, dec: 1,
    level: v => v < 90 ? 'critical' : v < 95 ? 'warning' : 'normal', hint: 'PULSE: <95 low · <90 critical' },
  { key: 'il6', label: 'IL-6 (modelled)', icon: '🧬', agent: 'gene', unit: ' pg/mL', min: 0, max: 20, step: 0.1, dec: 1,
    level: v => v > 12 ? 'critical' : v >= WI_IL6.limit ? 'warning' : 'normal',
    hint: () => `GENE: flags ≥ ${WI_IL6.limit} (1.5× baseline ${WI_IL6.base}) · >12 always` },
  { key: 'sleep_quality', label: 'Sleep quality', icon: '😴', agent: 'mirror', unit: '/100', min: 0, max: 100, step: 1, dec: 0,
    level: v => v < 50 ? 'critical' : v < 70 ? 'warning' : 'normal', hint: 'MIRROR: <70 fair · <50 poor' },
  { key: 'radiation_dose_24h', label: 'Radiation (last 24 h)', icon: '☢️', agent: 'solaris', unit: ' mSv', min: 0, max: 30, step: 0.1, dec: 2,
    level: v => v >= MISSION.shelter ? 'critical' : v >= MISSION.warning ? 'warning' : 'normal',
    hint: () => `SOLARIS: background ${MISSION.background} · ≥${MISSION.warning} elevated · ≥${MISSION.shelter} shelter` },
];
const WI_IL6 = { base: 3.85, limit: 5.78 };   // the selected crew member's pre-flight IL-6 baseline / GENE flag level
function _wiHint(m) { return typeof m.hint === 'function' ? m.hint() : m.hint; }
function _refreshWhatIfHints() {
  WHATIF_METRICS.forEach(m => { const el = document.getElementById('slider-' + m.key + '-hint'); if (el) el.textContent = _wiHint(m); });
}
const WHATIF_AGENTS = [
  { key: 'PULSE', css: 'pulse', icon: '💓', metrics: 'Heart rate · SpO₂' },
  { key: 'GENE', css: 'gene', icon: '🧬', metrics: 'IL-6' },
  { key: 'MIRROR', css: 'mirror', icon: '🪞', metrics: 'Sleep' },
  { key: 'SOLARIS', css: 'solaris', icon: '☀️', metrics: 'Radiation' },
];
const WHATIF_COLORS = { normal: 'var(--green)', warning: 'var(--yellow)', critical: 'var(--red)' };
let cascadeTimeout = null;   // debounce timer for the backend evaluation
let _whatifSeq = 0;
let _whatifBuilt = false;
let _whatifLastRank = 99;    // 99 = just reset: don't sound the alarm for the starting readings

function _buildWhatIf() {
  if (_whatifBuilt) return;
  const sliders = document.getElementById('whatif-sliders');
  sliders.innerHTML = WHATIF_METRICS.map((m, i) => `
    <div class="slider-group"${i === WHATIF_METRICS.length - 1 ? ' style="grid-column:1/-1"' : ''}>
      <div class="slider-header">
        <span class="slider-label"><span class="icon">${m.icon}</span> ${m.label}
          <span class="agent-tag ${m.agent}" style="margin-left:.35rem">${m.agent.toUpperCase()}</span></span>
        <span class="slider-value normal" id="slider-${m.key}-value">--</span>
      </div>
      <div class="slider-track"><div class="slider-fill" id="slider-${m.key}-fill"></div></div>
      <input type="range" class="slider-input" id="slider-${m.key}" min="${m.min}" max="${m.max}" step="${m.step}"
             oninput="updateSlider('${m.key}')" aria-label="${m.label}">
      <div class="slider-range"><span>${m.min}</span><span class="slider-hint" id="slider-${m.key}-hint">${_wiHint(m)}</span><span>${m.max}</span></div>
    </div>`).join('');
  const timeline = document.getElementById('cascade-timeline');
  timeline.innerHTML = WHATIF_AGENTS.map(a => `
    <div class="cascade-step" id="wi-step-${a.key}" style="--agent-color:var(--${a.css})">
      <div class="agent-icon" style="color:var(--${a.css});border-color:var(--${a.css})">${a.icon}</div>
      <span class="step-text" id="wi-text-${a.key}"><strong>${a.key}</strong> · ${a.metrics}</span>
      <span class="step-status" id="wi-status-${a.key}">—</span>
    </div>`).join('') + `
    <div class="cascade-step" id="wi-step-AEGIS" style="--agent-color:var(--aegis)">
      <div class="agent-icon" style="color:var(--aegis);border-color:var(--aegis)">◈</div>
      <span class="step-text" id="wi-text-AEGIS"><strong>AEGIS</strong> · combines all four</span>
      <span class="step-status" id="wi-status-AEGIS">—</span>
    </div>`;
  _whatifBuilt = true;
}

function openWhatIf() {
  _buildWhatIf();
  const crew = CREW_DATA[selectedCrew];
  document.getElementById('whatif-title').textContent = `What-If Simulator · ${crew ? crew.firstName : 'Crew'}`;
  document.getElementById('whatifModal').classList.add('open');
  _whatifSeq++;                         // drop any verdict still in flight from the last time the simulator was open
  const st = document.getElementById('status-text');
  if (st) st.innerHTML = '<strong>Checking with the agents…</strong> loading this crew member\'s current readings';
  const dot = document.getElementById('status-dot'); if (dot) dot.className = 'status-dot';
  document.querySelectorAll('[id^="wi-status-"]').forEach(el => { el.textContent = '…'; el.className = 'step-status'; });
  const rb = document.getElementById('aegis-rec'); if (rb) rb.style.display = 'none';
  resetSliders();
  announce('What-If Scenario Simulator opened');
}

function closeWhatIf() {
  document.getElementById('whatifModal').classList.remove('open');
  if (cascadeTimeout) clearTimeout(cascadeTimeout);
  announce('Simulator closed');
}

function _whatifValues() {
  const vals = {};
  WHATIF_METRICS.forEach(m => { vals[m.key] = parseFloat(document.getElementById('slider-' + m.key).value); });
  return vals;
}

function updateSlider(key, skipEvaluate) {
  const m = WHATIF_METRICS.find(x => x.key === key);
  if (!m) return;
  const slider = document.getElementById('slider-' + key);
  const v = parseFloat(slider.value);
  const lvl = m.level(v);
  const valueEl = document.getElementById('slider-' + key + '-value');
  const fillEl = document.getElementById('slider-' + key + '-fill');
  valueEl.textContent = v.toFixed(m.dec) + m.unit;
  valueEl.className = 'slider-value ' + lvl;
  fillEl.style.width = ((v - m.min) / (m.max - m.min) * 100) + '%';
  fillEl.style.background = WHATIF_COLORS[lvl];
  if (!skipEvaluate) {
    playTone({ normal: 300, warning: 400, critical: 500 }[lvl], 0.04);
    _scheduleWhatIf();
  }
}

function _scheduleWhatIf() {
  if (cascadeTimeout) clearTimeout(cascadeTimeout);
  document.getElementById('status-text').innerHTML = '<strong>Checking with the agents…</strong> the verdict appears when they answer';
  const dot = document.getElementById('status-dot'); if (dot) dot.className = 'status-dot';
  WHATIF_AGENTS.forEach(a => { const st = document.getElementById('wi-status-' + a.key); if (st) { st.textContent = '…'; st.className = 'step-status'; } });
  const ast = document.getElementById('wi-status-AEGIS'); if (ast) { ast.textContent = 'Checking…'; ast.className = 'step-status'; }
  cascadeTimeout = setTimeout(evaluateWhatIf, 700);
}

async function evaluateWhatIf() {
  const seq = ++_whatifSeq;
  const crewId = DASH_API.crewIdMap[selectedCrew] || 'crew-001';
  const statusDot = document.getElementById('status-dot');
  const statusText = document.getElementById('status-text');
  let data;
  try {
    const r = await fetch(`${DASH_API.baseUrl}/api/whatif`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ..._whatifValues(), crew_id: crewId, explain: true })
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    data = await r.json();
  } catch (e) {
    if (seq !== _whatifSeq) return;
    statusDot.className = 'status-dot warning';
    statusText.innerHTML = '<strong>Backend offline</strong> — start the AEGIS backend to evaluate scenarios';
    return;
  }
  if (seq !== _whatifSeq) return;   // a newer slider change is already being evaluated

  // Per-agent rows
  const chip = { ok: ['NORMAL', 'ok'], watch: ['OUT OF RANGE', 'watch'], alert: ['ALERT', 'alert'], critical: ['CRITICAL', 'alert'] };
  WHATIF_AGENTS.forEach(a => {
    const res = (data.agents || {})[a.key] || { level: 'ok', findings: [] };
    const [label, cls] = chip[res.level] || chip.ok;
    document.getElementById('wi-step-' + a.key).classList.toggle('active', res.level !== 'ok');
    document.getElementById('wi-text-' + a.key).innerHTML =
      `<strong>${a.key}</strong> · ${res.findings.length ? res.findings.map(escWI).join('; ') : a.metrics + ' within range'}`;
    const st = document.getElementById('wi-status-' + a.key);
    st.textContent = label; st.className = 'step-status ' + cls;
    if (res.level !== 'ok') flashAgent(a.css);
  });
  // AEGIS decision
  const vCls = { NOMINAL: 'ok', WATCH: 'watch', ALERT: 'alert', CASCADE: 'alert', PROTECT: 'alert' }[data.verdict] || 'ok';
  document.getElementById('wi-step-AEGIS').classList.toggle('active', data.verdict !== 'NOMINAL');
  document.getElementById('wi-text-AEGIS').innerHTML = `<strong>AEGIS</strong> · ${escWI(data.summary)}`;
  const ast = document.getElementById('wi-status-AEGIS');
  ast.textContent = data.verdict + (data.protocol ? ' → ' + data.protocol : ''); ast.className = 'step-status ' + vCls;

  statusDot.className = 'status-dot ' + (vCls === 'ok' ? '' : vCls === 'watch' ? 'warning' : 'critical');
  statusText.innerHTML = `<strong>${data.verdict}</strong> — ${escWI(data.summary)}`;

  // Escalation feedback: alarm tone + spoken read-out when the verdict gets worse (ALERT or above)
  const rank = { NOMINAL: 0, WATCH: 1, ALERT: 2, CASCADE: 3, PROTECT: 4 }[data.verdict] ?? 0;
  if (rank >= 2 && rank > _whatifLastRank) {
    playTone(880, 0.12); setTimeout(() => playTone(660, 0.12), 150); setTimeout(() => playTone(880, 0.18), 300);
    if (typeof typeResponse === 'function') {
      const plainVerdict = data.protocol === 'SHELTER-1' ? "that would be a radiation emergency, and everyone should head to the storm shelter."
        : data.protocol === 'MEDIC-1' ? "that would be a medical emergency, so I'd start a medical response right away."
        : data.verdict === 'CASCADE' ? "several problems would be stacking up at once, so I'd start the recovery plan."
        : "that would need someone to take a closer look.";
      // Clearly marked as a hypothetical in the chat log, so nobody reads it as a live reading
      VOICE.nextTag = { label: 'What-If · hypothetical', ai: false, title: 'Answer to slider values in the What-If simulator. Nothing here is a live reading.' };
      typeResponse(data.explanation ? `What-If (hypothetical): if that happened, ${data.explanation}` : `What-If (hypothetical): if that happened, ${plainVerdict}`, true);
    }
  }
  _whatifLastRank = rank;

  // Recommendation (DeepSeek explanation when available)
  const recBox = document.getElementById('aegis-rec');
  if (data.verdict === 'NOMINAL') { recBox.style.display = 'none'; return; }
  document.getElementById('rec-title').textContent =
    (data.protocol ? `AEGIS suggests: ${PLAIN_PROTOCOL[data.protocol] || data.protocol} (${data.protocol})`
                   : data.verdict === 'ALERT' ? 'Agent Alert' : 'AEGIS Assessment') +
    (data.source === 'deepseek' ? ' · 🧠 DeepSeek' : '');
  document.getElementById('rec-message').textContent = data.explanation || data.summary;
  recBox.style.display = 'flex';
}

function escWI(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function flashAgent(agent) {
  const node = document.getElementById('node-' + agent);
  const line = document.getElementById('line-' + agent);
  if (node) {
    node.classList.add('active');
    setTimeout(() => node.classList.remove('active'), 800);
  }
  if (line) {
    line.classList.add('active', 'flowing');
    setTimeout(() => line.classList.remove('active', 'flowing'), 800);
  }
}

// Reset = start from the selected crew member's CURRENT live readings
async function resetSliders() {
  _buildWhatIf();
  const crewId = DASH_API.crewIdMap[selectedCrew] || 'crew-001';
  const fallback = { heart_rate: 70, spo2: 98, il6: 4, sleep_quality: 78, radiation_dose_24h: MISSION.background };
  let vals = { ...fallback };
  try {
    const [c, b] = await Promise.all([
      fetch(`${DASH_API.baseUrl}/api/crew/${crewId}`).then(r => r.json()),
      fetch(`${DASH_API.baseUrl}/api/crew/${crewId}/biomarkers`).then(r => r.json()),
    ]);
    if (b && b.baseline != null) { WI_IL6.base = b.baseline; WI_IL6.limit = b.personal_limit; }
    if (c && c.vitals) {
      vals = { heart_rate: c.vitals.heart_rate, spo2: c.vitals.spo2, il6: b && b.il6 != null ? b.il6 : fallback.il6,
               sleep_quality: c.vitals.sleep_quality ?? fallback.sleep_quality,
               radiation_dose_24h: c.vitals.radiation_dose_24h ?? fallback.radiation_dose_24h };
    }
  } catch (e) { /* offline: typical values */ }
  _refreshWhatIfHints();
  WHATIF_METRICS.forEach(m => {
    const s = document.getElementById('slider-' + m.key);
    s.value = Math.min(m.max, Math.max(m.min, vals[m.key]));
    updateSlider(m.key, true);
  });
  playTone(300, 0.1);
  _whatifLastRank = 99;
  evaluateWhatIf();
}
