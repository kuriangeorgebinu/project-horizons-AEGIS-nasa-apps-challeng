// ============================================
// AGENT INSIGHTS: live feed from the backend (alerts + agent events), newest first. No sample data.
// ============================================
const INS_AGENTS = ['aegis', 'pulse', 'gene', 'solaris', 'mirror', 'osseo'];
function _insAgent(text, fallback) {
  // Agent names are written in capitals; whole words only ("generated" is not GENE)
  const m = String(text || '').match(/\b(SOLARIS|PULSE|GENE|MIRROR|OSSEO|ECHO)\b/);
  return m ? m[1].toLowerCase() : (fallback || 'aegis');
}
function _insTime(ts) {
  const t = _parseUtc(ts); if (!isFinite(t)) return '';
  const ago = Math.max(0, Math.round((Date.now() - t) / 60000));
  return `${new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })} · ${ago < 1 ? 'just now' : ago + ' min ago'}`;
}
const _insEsc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
async function refreshInsights() {
  if (!document.getElementById('page-insights')?.classList.contains('active')) return;
  const B = DASH_API.baseUrl;
  try {
    const [alerts, events] = await Promise.all([
      fetch(`${B}/api/alerts?unacknowledged_only=false&limit=100`).then(r => r.json()),
      fetch(`${B}/api/events?limit=200`).then(r => r.json()),
    ]);
    const items = { aegis: [], pulse: [], gene: [], solaris: [], mirror: [], osseo: [], echo: [] };
    const open = { aegis: 0, pulse: 0, gene: 0, solaris: 0, mirror: 0, osseo: 0 };
    (alerts || []).forEach(a => {
      const ag = _insAgent(String(a.source || '').toUpperCase(), 'aegis');
      if (!items[ag]) return;
      items[ag].push({ t: _parseUtc(a.timestamp), ts: a.timestamp, sev: a.severity,
        action: `${a.acknowledged ? '' : '● '}Alert: ${a.title}`, why: a.message });
      if (!a.acknowledged && open[ag] != null) open[ag]++;
    });
    (events || []).forEach(e => {
      const msg = e.message || '';
      if (/^(Demo reset|Story demo|AEGIS system initialized)/i.test(msg)) return;
      const ag = /^ECHO/i.test(msg) ? 'echo' : _insAgent(msg, 'aegis');
      if (!items[ag]) return;
      const d = e.details || {};
      const why = d.question ? `Question: "${d.question}"` + (d.source ? ` · answered by ${d.source === 'rules' ? 'the rules' : d.source}` : '')
                : d.domains ? `Domains: ${d.domains.join(', ')}` : '';
      items[ag].push({ t: _parseUtc(e.timestamp), ts: e.timestamp, sev: '', action: msg, why });
    });
    INS_AGENTS.concat(['echo']).forEach(ag => {
      const log = document.getElementById(`ins-log-${ag}`); if (!log) return;
      const list = items[ag].sort((x, y) => y.t - x.t).slice(0, 25);
      log.innerHTML = list.length ? list.map(i => `<div class="insight-entry sev-${_insEsc(i.sev)}">
          <div class="insight-time">${_insEsc(_insTime(i.ts))}</div>
          <div class="insight-action">${_insEsc(i.action)}</div>
          ${i.why ? `<div class="insight-reasoning">${_insEsc(i.why)}</div>` : ''}</div>`).join('')
        : (ag === 'echo' ? '' : '<div class="insight-empty">Nothing yet. This agent is watching; its alerts and analyses will appear here.</div>');
      const st = document.getElementById(`ins-status-${ag}`);
      if (st) {
        const busy = list.length && (Date.now() - list[0].t) < 120000;
        st.textContent = open[ag] ? `${open[ag]} open alert${open[ag] > 1 ? 's' : ''}` : busy ? 'Active' : 'Monitoring';
        st.className = 'insight-status' + (open[ag] ? ' alerting' : busy ? ' busy' : '');
      }
    });
    const up = document.getElementById('insights-updated'); if (up) up.textContent = `Updated ${new Date().toLocaleTimeString()}.`;
  } catch (e) {
    INS_AGENTS.forEach(ag => { const log = document.getElementById(`ins-log-${ag}`); if (log && !log.querySelector('.insight-entry')) log.innerHTML = '<div class="insight-empty">Backend offline: no live activity to show.</div>'; });
  }
}
setInterval(refreshInsights, 5000);


// Honesty chip: is DeepSeek wording the explanations right now, or are they written by the rules?
async function refreshAiMode() {
  const el = document.getElementById('ai-mode'); if (!el) return;
  try {
    const d = await (await fetch(`${DASH_API.baseUrl}/api/llm/status`)).json();
    el.hidden = false;
    if (d.enabled) {
      el.textContent = '🧠 AI: DeepSeek'; el.className = 'ai-mode on';
      el.title = 'DeepSeek words the agents\' explanations, Voice Terminal answers and ordinary ECHO chat. The rules still decide every alert and protocol, and every AI reply is fact-checked before it is shown.';
    } else {
      el.textContent = '⚙ AI: rules only'; el.className = 'ai-mode';
      el.title = 'The AI layer (DeepSeek) is off on this server. Every alert, protocol and recommendation still comes from the agents\' rules; explanations, Voice Terminal answers and ECHO replies are written by the rules (scripted), not by an AI model.';
    }
  } catch (e) { el.hidden = true; }
}
setTimeout(refreshAiMode, 0); setInterval(refreshAiMode, 60000);   // after DASH_API is defined

// Page Navigation
function switchPage(pageName) {
  // Hide all pages
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-tab').forEach(t => {
    t.classList.remove('active');
    t.setAttribute('aria-selected', 'false');
  });
  
  // Show selected page
  const page = document.getElementById('page-' + pageName);
  const tab = document.getElementById('tab-' + pageName);
  if (page) page.classList.add('active');
  if (tab) {
    tab.classList.add('active');
    tab.setAttribute('aria-selected', 'true');
  }
  
  playTone(350, 0.1);
  announce(pageName.charAt(0).toUpperCase() + pageName.slice(1) + ' page');
  if (pageName === 'insights') refreshInsights();
}

// H4: API configuration
// Local development: the page is served on :8080 (or opened as a file) and the API runs on 127.0.0.1:8000.
// Deployed (AWS): one web server serves both, so the API is on the same origin as the page.
const AEGIS_API_ORIGIN = (location.protocol === 'file:' || location.port === '8080') ? 'http://127.0.0.1:8000' : location.origin;
const DASH_API = {
  baseUrl: AEGIS_API_ORIGIN,
  selectedCrewId: 'crew-001',  // Maps to backend IDs
  crewIdMap: {
    'chen': 'crew-001',
    'okonkwo': 'crew-002',
    'kowalski': 'crew-003',
    'nakamura': 'crew-004'
  }
};

// Crew Selection
const CREW_DATA = {
  chen: { name: 'Commander Sarah Chen', firstName: 'Commander Chen', hr: 68, spo2: 97, stress: 34, sleep: 71, status: 'nominal', greeting: 'Commander Chen selected. Her live readings are on the Cascade Map and in Live Vitals.' },
  kowalski: { name: 'Dr. Anna Kowalski', firstName: 'Dr. Kowalski', hr: 62, spo2: 98, stress: 22, sleep: 89, status: 'nominal', greeting: 'Dr. Kowalski selected. Her live readings are on the Cascade Map and in Live Vitals.' },
  nakamura: { name: 'Dr. Kenji Nakamura', firstName: 'Dr. Nakamura', hr: 88, spo2: 95, stress: 67, sleep: 48, status: 'nominal', greeting: 'Dr. Nakamura selected. His live readings are on the Cascade Map and in Live Vitals.' },
  okonkwo: { name: 'Dr. David Okonkwo', firstName: 'Dr. Okonkwo', hr: 58, spo2: 99, stress: 18, sleep: 92, status: 'nominal', greeting: 'Dr. Okonkwo selected. His live readings are on the Cascade Map and in Live Vitals.' }
};

let selectedCrew = 'chen';

function selectCrew(crewId) {
  selectedCrew = crewId;
  const crew = CREW_DATA[crewId];
  
  // H4: Set API crew ID for live vitals fetch
  DASH_API.selectedCrewId = DASH_API.crewIdMap[crewId] || 'crew-001';
  hrHistory.length = 0;
  // Re-draw the map, status card, trend chips and strip for the new person from the last forecast straight away,
  // then fetch a fresh one (no waiting for the next 5-second refresh)
  const mineNow = CM.last && (CM.last.crew || []).find(c => c.crew_member_id === DASH_API.selectedCrewId);
  if (mineNow) { try { renderCascadeMap(mineNow); renderTrendChips(mineNow); } catch (x) {} }
  if (typeof refreshPredict === 'function') setTimeout(refreshPredict, 0);
  if (typeof refreshLiveAlerts === 'function') setTimeout(refreshLiveAlerts, 0);
  if (typeof AEGIS_API !== 'undefined' && AEGIS_API._lastRecs) AEGIS_API.updateRecommendationsUI(AEGIS_API._lastRecs);   // const global, not on window
  
  // Update selection everywhere a crew member can be picked (crew strip, roster cards)
  document.querySelectorAll('.crew-card').forEach(card => card.classList.toggle('selected', (card.getAttribute('onclick') || '').includes(`'${crewId}'`)));
  document.querySelectorAll('.cs-chip').forEach(chip => chip.classList.toggle('selected', chip.dataset.key === crewId));
  if (typeof renderCrewStrip === 'function') renderCrewStrip();
  
  // Update vitals panel title
  const vitalsTitle = document.querySelector('.vitals-title');
  if (vitalsTitle) vitalsTitle.textContent = 'Live Vitals · ' + crew.firstName;
  
  // Update vitals values (on mission page)
  const hrVal = document.getElementById('hr-value');
  ['hr', 'spo2', 'il6', 'sleep'].forEach(k => { const v = document.getElementById(`${k}-value`); if (v) { v.textContent = '…'; v.style.color = 'var(--muted)'; } });
  fetchAndUpdateVitals();                    // the selected person's live readings, straight away
  if (typeof echoSwitchCrew === 'function') echoSwitchCrew();   // ECHO follows at once
  
  playTone(400, 0.15);
  announce('Selected ' + crew.name);
  
  // Switch to mission page and have AEGIS respond
  setTimeout(() => {
    switchPage('mission');
    if (typeof STORY !== 'undefined' && STORY.on) return;      // the demo narrates itself
    setTimeout(() => {
      askAgent(currentAgent, 'Summarise this crew member\'s current status in two sentences.', crew.greeting);
    }, 300);
  }, 200);
}

function toggleRecs() {
  const list = document.querySelector('.alert-panel .recs-list'); if (!list) return;
  list.classList.toggle('all'); _updateRecsMore();
}
function _updateRecsMore() {
  const list = document.querySelector('.alert-panel .recs-list'), btn = document.getElementById('recs-more');
  if (!list || !btn) return;
  const n = list.querySelectorAll('.rec-card').length;
  btn.hidden = n <= 2;
  btn.textContent = list.classList.contains('all') ? 'Show fewer' : `Show all ${n}`;
}
new MutationObserver(_updateRecsMore).observe(document.querySelector('.alert-panel .recs-list') || document.body, { childList: true });

// Evidence & data: open the expander and bring it into view (linked from the demo bar)
function openEvidence(ev) {
  if (ev) ev.preventDefault();
  const d = document.getElementById('nasa-panel'); if (!d) return;
  d.open = true; d.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// Accessibility: Screen reader announcements
function announce(message) {
  const live = document.getElementById('a11y-live');
  if (live) { live.textContent = ''; setTimeout(() => live.textContent = message, 50); }
}

// Agent data
const AGENTS = {
  aegis: { name: 'AEGIS', color: '#5a9fd7', greeting: 'AEGIS online. All systems monitored. How can I assist you today, Commander?' },
  solaris: { name: 'SOLARIS', color: '#c8b070', greeting: 'SOLARIS here. Current solar activity is moderate. No immediate radiation threats detected.' },
  gene: { name: 'GENE', color: '#a098c8', greeting: 'GENE here. I track a modelled inflammation marker, IL-6, against each crew member\'s own pre-flight baseline.' },
  pulse: { name: 'PULSE', color: '#c88888', greeting: 'PULSE active. Heart rate variability is below optimal. Recommend stress reduction protocol.' },
  mirror: { name: 'MIRROR', color: '#7aad9a', greeting: 'MIRROR online. Cognitive performance trending 14% below baseline. Sleep quality may be a factor.' },
  osseo: { name: 'OSSEO', color: '#d7dce5', greeting: 'OSSEO here. I show historical NASA-study cohort means and synthetic regional examples; neither is your measurement.' }
};

let currentAgent = 'aegis';
let isListening = false;
let recognition = null;

// L7: Audio context for tones - lazy init on first click
let audioCtx = null;
let userHasInteracted = false;
document.addEventListener('click', () => { userHasInteracted = true; }, { once: true });
document.addEventListener('keydown', () => { userHasInteracted = true; }, { once: true });

const activeToneOscillators = new Set();

function playTone(freq, duration) {
  // M3: Skip audio until user has interacted
  if (VOICE.muted || !userHasInteracted) return;
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }
  if (audioCtx.state === 'suspended') {
    audioCtx.resume();
  }
  const osc = audioCtx.createOscillator();
  const gain = audioCtx.createGain();
  activeToneOscillators.add(osc);
  osc.onended = () => activeToneOscillators.delete(osc);
  osc.frequency.value = freq;
  gain.gain.setValueAtTime(0.1, audioCtx.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + duration);
  osc.connect(gain);
  gain.connect(audioCtx.destination);
  osc.start();
  osc.stop(audioCtx.currentTime + duration);
}

function stopTones() {
  for (const osc of activeToneOscillators) {
    try { osc.stop(); } catch (e) {}
  }
  activeToneOscillators.clear();
}

// ── Live agent answers (Voice Terminal) ─────────────────────────────────────────────
// Every spoken/typed question and every agent/crew selection goes to POST /api/ask: the chosen agent
// analyses the selected crew member's CURRENT vitals and DeepSeek answers in that agent's voice
// (rule-based answer if the model is unavailable; the canned line only if the backend is offline).
let _askSeq = 0;
// ── Chat log helpers ──
function _chatScroll() { const log = document.getElementById('ask-log'); if (log) log.scrollTop = log.scrollHeight; }
function _chatTrim() { const log = document.getElementById('ask-log'); while (log && log.children.length > 40) log.firstElementChild.remove(); }
function chatUserBubble(text) {
  const log = document.getElementById('ask-log'); if (!log) return;
  const b = document.createElement('div'); b.className = 'ab user'; b.textContent = text;
  log.appendChild(b); _chatTrim(); _chatScroll();
}
function chatAgentBubble(agentKey, tag) {
  const log = document.getElementById('ask-log');
  const think = document.getElementById('ask-thinking'); if (think) think.remove();
  const b = document.createElement('div');
  b.className = 'ab agent'; b.style.setProperty('--who', `var(--${AGENTS[agentKey] ? agentKey : 'aegis'})`);
  const who = document.createElement('div'); who.className = 'ab-who';
  who.textContent = (AGENTS[agentKey] || AGENTS.aegis).name;
  if (tag && tag.title) who.title = tag.title;   // no visible AI/model label in the chat (Nov 13); the source stays in the hover text
  const txt = document.createElement('div'); txt.className = 'ab-text';
  b.append(who, txt);
  if (log) { log.appendChild(b); _chatTrim(); }
  document.querySelector('.voice-panel')?.classList.add('has-reply');
  const obs = new MutationObserver(_chatScroll); obs.observe(txt, { childList: true, characterData: true, subtree: true });
  setTimeout(() => obs.disconnect(), 60000);
  return txt;
}
function chatThinking(agentKey) {
  const log = document.getElementById('ask-log'); if (!log) return;
  document.getElementById('ask-thinking')?.remove();
  const b = document.createElement('div'); b.id = 'ask-thinking'; b.className = 'ab agent thinking';
  b.style.setProperty('--who', `var(--${agentKey})`);
  b.innerHTML = `<div class="ab-who">${AGENTS[agentKey].name}</div><div class="ab-text">thinking…</div>`;
  log.appendChild(b); _chatScroll();
}
function toggleMute() {
  VOICE.muted = !VOICE.muted;
  const b = document.getElementById('muteBtn');
  if (b) { b.textContent = VOICE.muted ? '🔇' : '🔊'; b.setAttribute('aria-pressed', String(VOICE.muted)); }
  if (VOICE.muted) {
    stopVoice();
    stopTones();
  }
  try { localStorage.setItem('aegis-muted', VOICE.muted ? '1' : '0'); } catch (e) {}
}

async function askAgent(agentKey, question, fallbackText, opts = {}) {
  const seq = ++_askSeq;                       // ignore answers to older questions
  // opts.handBackTo: agent to return the conversation to once this answer has been spoken
  const onDone = opts.handBackTo ? () => setTimeout(() => handBack(agentKey, opts.handBackTo, seq), 600) : null;
  const agent = AGENTS[agentKey] ? agentKey : 'aegis';
  const agentName = AGENTS[agent].name;
  const crewId = DASH_API.crewIdMap[selectedCrew] || 'crew-001';
  const askBtn = document.querySelector('.ask-btn');
  if (typingTimer) { clearTimeout(typingTimer); typingTimer = null; }
  chatThinking(agent);
  if (askBtn) askBtn.disabled = true;
  try {
    const r = await fetch(`${DASH_API.baseUrl}/api/ask`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, agent: agentName, crew_id: crewId })
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    if (seq !== _askSeq) return;
    VOICE.nextTag = { ai: data.source === 'deepseek',
      title: (data.source === 'deepseek' ? 'Worded by DeepSeek from the live readings' : 'Written by the rules (DeepSeek off or unavailable)') +
             ` · agents consulted: ${(data.agents_consulted || []).join(', ')} · ${data.latency_s}s` };
    VOICE.nextSpeaker = agent;                 // each agent answers in its own voice
    typeResponse(withIntro(agent, data.answer || fallbackText || 'No answer available.'), true, onDone);
  } catch (e) {
    if (seq !== _askSeq) return;
    VOICE.nextSpeaker = agent;
    typeResponse(fallbackText || `${agentName} could not reach the AEGIS backend.`, false, onDone);
  } finally {
    if (seq === _askSeq && askBtn) askBtn.disabled = false;
  }
}

// Agents introduce themselves when they report: "SOLARIS here. …", "Hi, PULSE here! …"
function withIntro(agentKey, text) {
  const name = (AGENTS[agentKey] && AGENTS[agentKey].name) || 'AEGIS';
  const t = String(text || '').trim();
  if (!t || new RegExp(`\\b${name}\\b`, 'i').test(t.slice(0, 40))) return t;     // already says who it is
  const greet = t.match(/^(hi|hey|hello|good (?:morning|afternoon|evening))(?:\s+there)?\s*[!,.]?\s*/i);
  if (greet) {
    const word = greet[1].charAt(0).toUpperCase() + greet[1].slice(1).toLowerCase();
    return `${word}, ${name} here! ${t.slice(greet[0].length)}`.trim();
  }
  return `${name} here. ${t}`;
}

function submitTypedQuestion() {
  const input = document.getElementById('askInput');
  const q = (input.value || '').trim();
  if (q.length < 2) return;
  input.value = '';
  chatUserBubble(q);
  processCommand(q);
}

// Select agent - glow the connection line
// opts.silent: switch agent visually without asking for a status report (a question follows)
function selectAgent(name, opts = {}) {
  currentAgent = name;
  
  // Reset all lines and nodes
  document.querySelectorAll('.conn-line').forEach(l => { l.classList.remove('active'); l.classList.remove('flowing'); });
  document.querySelectorAll('.agent-node').forEach(n => { 
    n.classList.remove('active'); 
    n.setAttribute('aria-pressed', 'false');
  });
  
  // Remove vital pulse
  document.querySelectorAll('.vital-pulse').forEach(el => el.classList.remove('vital-pulse'));
  
  // Activate selected
  document.querySelector('.node-aegis').classList.add('active');
  document.querySelector('.node-aegis').setAttribute('aria-pressed', 'true');
  
  if (name !== 'aegis') {
    const line = document.getElementById('line-' + name);
    const node = document.getElementById('node-' + name);
    if (line) { line.classList.add('active'); line.classList.add('flowing'); }
    if (node) { 
      node.classList.add('active'); 
      node.setAttribute('aria-pressed', 'true');
      // WOW: Particle burst effect
      createParticleBurst(node);
    }
  } else {
    // Particle burst on AEGIS too
    createParticleBurst(document.querySelector('.node-aegis'));
  }
  
  // WOW: Pulse animation when PULSE agent selected
  if (name === 'pulse') {
    document.querySelectorAll('.vital-value').forEach((el, i) => {
      if (i === 0) el.classList.add('vital-pulse'); // HR value pulses
    });
  }
  
  CM_DETAIL.agent = name === 'aegis' ? null : name;
  renderNodeDetail();
  playTone(400 + Math.random() * 200, 0.15);
  announce(AGENTS[name].name + ' selected');
  const ct = document.getElementById('chatTo');
  if (ct) ct.innerHTML = `talking to <b style="color:var(--${name})">${AGENTS[name].name}</b> · click an agent on the map to switch`;
  const ai = document.getElementById('askInput');
  if (ai) ai.placeholder = `Message ${AGENTS[name].name}…`;
  if (typeof refreshVoicePicker === 'function') refreshVoicePicker();   // picker shows this agent's voice
  if (!opts.silent) {
    const q = name === 'aegis'
      ? 'Give a brief overall status report for this crew member and flag anything that needs attention.'
      : 'Give your current status report for this crew member from your domain.';
    askAgent(name, q, AGENTS[name].greeting);
  }
}

// Click a system on the map: its reading and limit, plus what that agent has done lately (was the Agent Insights page)
const CM_DETAIL = { agent: null, items: [], fetchedAt: 0 };
const CD_DOMAIN = { solaris: 'radiation', gene: 'inflammation (IL-6, modelled)', pulse: 'heart and oxygen', mirror: 'sleep', osseo: 'cohort reference + synthetic DXA examples · no live monitor' };
async function _cdFetch() {
  const B = DASH_API.baseUrl;
  const [alerts, events] = await Promise.all([
    fetch(`${B}/api/alerts?unacknowledged_only=false&limit=60`).then(r => r.json()).catch(() => []),
    fetch(`${B}/api/events?limit=120`).then(r => r.json()).catch(() => []),
  ]);
  const items = [];
  (alerts || []).forEach(a => items.push({ ag: _insAgent(String(a.source || '').toUpperCase(), 'aegis'), t: _parseUtc(a.timestamp), ts: a.timestamp,
    crew: a.crew_member_id, text: `Alert: ${String(a.title || '').replace(/^🔺\s*/, '')}${a.acknowledged ? ' (handled)' : ''}` }));
  (events || []).forEach(e => { const m = e.message || ''; if (/^(Demo reset|Story demo|AEGIS system initialized)/i.test(m)) return;
    items.push({ ag: _insAgent(m, 'aegis'), t: _parseUtc(e.timestamp), ts: e.timestamp, crew: e.crew_member_id, text: m }); });
  CM_DETAIL.items = items; CM_DETAIL.fetchedAt = Date.now();
}
async function renderNodeDetail() {
  const box = document.getElementById('cm-detail'), ag = CM_DETAIL.agent;
  if (!box) return;
  if (!ag || ag === 'aegis') { box.hidden = true; document.getElementById('cm-hint')?.removeAttribute('hidden'); return; }
  if (ag === 'osseo') {
    const name = _crewDisplayName(DASH_API.selectedCrewId);
    box.style.setProperty('--node', 'var(--osseo)');
    box.innerHTML = `<div class="cd-head"><span class="cd-name">OSSEO</span><span class="cd-state">${name} · context only</span>
      <button type="button" class="cd-close" onclick="closeNodeDetail()" aria-label="Close">×</button></div>
      <div class="cd-reading">The OSSEO panel shows historical total-body DXA cohort means and synthetic regional examples in g/cm². Neither is a measurement for this crew member or a NASA-wide norm. No validated real-time in-flight bone monitor or clinical alert is represented.</div>`;
    box.hidden = false;
    document.getElementById('cm-hint')?.setAttribute('hidden', '');
    return;
  }
  if (Date.now() - CM_DETAIL.fetchedAt > 5000) { try { await _cdFetch(); } catch (e) {} }
  const mine = CM.last && (CM.last.crew || []).find(c => c.crew_member_id === DASH_API.selectedCrewId);
  const ts = mine ? CM_AGENTS[ag].map(m => mine.metrics[m]).filter(Boolean) : [];
  const state = sysState(ts);
  const reading = ts.map(t => `${t.label} <b>${t.value}</b> ${_insEsc(t.unit || '')} · limit ${t.threshold}`).join(' · ') || 'Waiting for readings…';
  const pct = ts.length ? Math.round(Math.min(1, Math.max(...ts.map(_cmStrain))) * 100) : 0;
  const name = _crewDisplayName(DASH_API.selectedCrewId);
  const sel = DASH_API.selectedCrewId;
  const about = i => i.crew ? i.crew === sel : (!/crew-00\d/.test(i.text) || i.text.includes(sel));   // this crew member only
  const recent = CM_DETAIL.items.filter(i => i.ag === ag && about(i)).sort((a, b) => b.t - a.t).slice(0, 4)
    .map(i => ({ ...i, text: i.text.replace(/\bfor crew-00\d\b/g, '').replace(/\s+/g, ' ').trim() }));
  const hhmm = t => new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  box.style.setProperty('--node', `var(--${ag})`);
  box.innerHTML = `<div class="cd-head"><span class="cd-name">${ag.toUpperCase()}</span><span class="cd-state">${CD_DOMAIN[ag]} · ${name} · ${SYS_WORD[state]}</span>
      <button type="button" class="cd-close" onclick="closeNodeDetail()" aria-label="Close">×</button></div>
    <div class="cd-reading">${reading} · ${pct}% of the way to the limit</div>
    ${recent.length ? `<ul>${recent.map(i => `<li><span>${_insEsc(hhmm(i.t))}</span><b>${_insEsc(i.text)}</b></li>`).join('')}</ul>`
                    : '<div class="cd-empty">No alerts or analyses from this agent for this crew member yet. It is watching.</div>'}`;
  box.hidden = false;
  document.getElementById('cm-hint')?.setAttribute('hidden', '');
}
function closeNodeDetail() { CM_DETAIL.agent = null; renderNodeDetail(); if (typeof selectAgent === 'function') selectAgent('aegis', { silent: true }); }

// WOW: Particle burst animation
function createParticleBurst(node) {
  if (!node) return;
  if (node instanceof SVGElement) {          // Cascade Map node: a ring that expands from the node
    const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    c.setAttribute('class', 'cm-burst');
    node.appendChild(c);
    setTimeout(() => c.remove(), 650);
    return;
  }
  const ring = document.createElement('div');
  ring.className = 'particle-ring';
  node.appendChild(ring);
  setTimeout(() => ring.remove(), 600);
}

// WOW: Boot sequence - staggered fade-in
function bootSequence() {
  const order = ['node-aegis', 'node-solaris', 'node-gene', 'node-pulse', 'node-mirror', 'node-osseo'];
  
  order.forEach((cls, i) => {
    const node = document.querySelector('.' + cls);
    if (node) {
      node.style.opacity = '0';
      setTimeout(() => {
        node.style.transition = 'opacity 0.4s ease-out';
        node.style.opacity = '1';
        playTone(220 + i * 80, 0.1);
      }, i * 200);
    }
  });
}

