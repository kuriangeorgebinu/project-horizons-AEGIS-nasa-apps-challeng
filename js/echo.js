// ============================================
// ECHO: crew companion against isolation. MIRROR detects, ECHO responds as a presence.
// Modes (set by the backend rules): companion · checkin · support (crisis). Private by default.
// ============================================
const ECHO = { crew: null, mode: null, level: null, reason: null, lastProactive: null, busy: false, esc: null, ready: false, chats: {}, pickedAt: 0 };
// Conversations are kept in this browser tab (sessionStorage), so a reset (which reloads the page), a storm or a
// demo chapter adds to the conversation instead of replacing it. Nothing leaves the browser.
const _ECHO_KEY = 'aegis.echo.chats.v1';
function echoPersist() {
  try {
    const chat = document.getElementById('echo-chat');
    if (ECHO.crew && chat) { const c = chat.cloneNode(true); c.querySelector('#echo-typing')?.remove(); ECHO.chats[ECHO.crew] = c.innerHTML; }
    sessionStorage.setItem(_ECHO_KEY, JSON.stringify(ECHO.chats));
  } catch (e) { /* storage blocked: the conversation still lives in memory for this page */ }
}
try { Object.assign(ECHO.chats, JSON.parse(sessionStorage.getItem(_ECHO_KEY) || '{}')); } catch (e) {}
window.addEventListener('pagehide', echoPersist);
const ECHO_MOOD_FACE = { 1: '😣 Rough', 2: '😔 Low', 3: '😐 Okay', 4: '🙂 Good', 5: '😄 Great' };
const _echoEsc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
function echoCrewId() { return DASH_API.selectedCrewId || 'crew-001'; }

function echoBubble(who, text, cls = '') {
  const chat = document.getElementById('echo-chat'); if (!chat) return null;
  const b = document.createElement('div');
  b.className = `eb ${who} ${cls}`.trim();
  b.innerHTML = who === 'note' ? _echoEsc(text)
    : `<span class="eb-who">${who === 'echo' ? 'ECHO' : _echoEsc(_crewDisplayName(echoCrewId()))}</span>${_echoEsc(text)}`;
  chat.appendChild(b);
  chat.scrollTop = chat.scrollHeight;
  echoPersist();
  return b;
}
function echoTyping(on) {
  const chat = document.getElementById('echo-chat'); if (!chat) return;
  let t = document.getElementById('echo-typing');
  if (on && !t) {
    t = document.createElement('div'); t.id = 'echo-typing'; t.className = 'eb echo';
    t.innerHTML = '<span class="eb-who">ECHO</span><span class="eb-typing"><i></i><i></i><i></i></span>';
    chat.appendChild(t); chat.scrollTop = chat.scrollHeight;
  } else if (!on && t) t.remove();
}
// ECHO introduces itself when it takes the floor ("ECHO here." / "Hi, ECHO here!"): when someone else spoke last,
// or it has been quiet for a while. Inside an ongoing one-to-one chat it doesn't repeat its name every line.
function echoIntro(text) {
  const t = String(text || '').trim();
  if (!t || /^\W*(hi|hey|hello)?[,!\s]*echo here\b/i.test(t)) return t;
  const ownTurn = FLOOR.lastSpeaker === 'echo' && Date.now() - FLOOR.lastEchoAt < 90000;
  if (ownTurn) return t;
  const greet = t.match(/^(hi|hey|hello|good (?:morning|afternoon|evening))(?:\s+there)?\s*[!,.]?\s*/i);
  if (greet) return `${greet[1].charAt(0).toUpperCase() + greet[1].slice(1).toLowerCase()}, ECHO here! ${t.slice(greet[0].length)}`.trim();
  return `ECHO here. ${t}`;
}
// ECHO's message waits for its turn (typing dots meanwhile), then appears and is spoken. Returns nothing;
// `after(bubble)` runs when it is shown.
function echoSay(text, cls = '', { speak = true, after = null } = {}) {
  const line = echoIntro(text);
  echoTyping(true);
  floorRequest({ speaker: 'echo', text: line, force: true, run: (slot, stale) => {
    echoTyping(false);
    const b = echoBubble('echo', line, cls);
    try { after && after(b); } catch (e) {}
    if (!speak || stale) { slot.release(); return; }
    slot.flush = null;
    echoSpeak(line, () => slot.release());
  } });
}
function echoSpeak(text, onDone) {
  const panel = document.getElementById('echo-panel');
  const fin = () => { panel && panel.classList.remove('speaking'); if (typeof onDone === 'function') { const f = onDone; onDone = null; f(); } };
  if ((!('speechSynthesis' in window) && !TTS.on) || !VOICE.unlocked || VOICE.muted) {
    if (panel) { panel.classList.add('speaking'); setTimeout(fin, 1600); } else fin();
    return;
  }
  const on = () => panel && panel.classList.add('speaking');
  ttsSayAll(text, 'echo', { onstart: on, onend: fin, fallback: () => echoSpeakBrowser(text, panel, fin) });
}
function echoSpeakBrowser(text, panel, onDone) {
  const fin = () => { panel && panel.classList.remove('speaking'); onDone && onDone(); };
  if (!('speechSynthesis' in window)) { fin(); return; }
  try {
    VOICE.run++;
    const u = new SpeechSynthesisUtterance(speechify(text));
    const v = (VOICE.byAgent && VOICE.byAgent.echo) || VOICE.voice;
    if (v) { u.voice = v; u.lang = v.lang; }
    u.rate = VOICE.rate * AGENT_VOICE.echo.rate; u.pitch = VOICE.pitch * AGENT_VOICE.echo.pitch;
    let started = false;
    u.onstart = () => { started = true; panel && panel.classList.add('speaking'); };
    u.onend = u.onerror = fin;
    setTimeout(() => speechSynthesis.speak(u), 80);
    setTimeout(() => { if (!started) { speechSynthesis.cancel(); fin(); } }, 4000);   // blocked audio: don't hold the floor
  } catch (e) { fin(); }
}
// "Talk to the medical officer": names come from the backend's escalation path (medic first, the Commander for the medic)
function echoRenderSupport(d) {
  const box = document.getElementById('echo-support'); if (!box) return;
  const show = d && d.mode === 'support' && (d.coping || []).length;
  box.hidden = !show;
  if (!show) return;
  document.getElementById('es-steps').innerHTML = d.coping.map(c => `<li>${_echoEsc(c)}</li>`).join('');
  const esc = d.escalation || ECHO.esc;
  if (esc) {
    ECHO.esc = esc;
    document.getElementById('es-onboard').textContent = `Talk to ${esc.onboard.name}`;
    document.getElementById('es-ground').textContent = `Private note to the flight surgeon (${(HERO.oneway || '').replace('≈ ', '≈') || 'signal delay'} away)`;
    document.getElementById('es-path').textContent =
      `Escalation: ${esc.onboard.name} on board first, then the flight surgeon at mission control, ${HERO.oneway || 'a signal delay'} away one way. ECHO only shares if you say yes.`;
  }
}
function echoSetMode(mode, level) {
  const panel = document.getElementById('echo-panel'); if (!panel) return;
  ECHO.mode = mode || 'companion'; ECHO.level = level || 'calm';
  panel.dataset.mode = ECHO.mode; panel.dataset.level = ECHO.level;
  panel.querySelectorAll('.echo-mode').forEach(m => m.classList.toggle('on', m.dataset.m === ECHO.mode));
  const mini = document.getElementById('node-echo'); if (mini) mini.dataset.mode = ECHO.mode;
  const feel = document.getElementById('echo-feel');
  if (feel && ECHO.mode === 'support' && Date.now() - ECHO.pickedAt > 60000) feel.classList.add('open');
}
function echoToggleMoods() {   // always opens (a toggle closed it when support mode had already opened it)
  const f = document.getElementById('echo-feel'); if (!f) return;
  f.classList.add('open');
  f.querySelector('.echo-moods button')?.focus({ preventScroll: true });
}
function focusEcho() {
  const p = document.getElementById('echo-panel'); if (!p) return;
  // During the demo, line ECHO up just under the (sticky) demo bar so the bar never covers the conversation
  p.scrollIntoView({ behavior: 'smooth', block: (typeof STORY !== 'undefined' && STORY.on) ? 'start' : 'center' });
  p.classList.remove('spotlight'); void p.offsetWidth; p.classList.add('spotlight');
  setTimeout(() => document.getElementById('echo-input')?.focus({ preventScroll: true }), 600);
}

function echoSwitchCrew() {
  const crew = echoCrewId(); if (ECHO.crew === crew) return;
  const chat = document.getElementById('echo-chat');
  if (ECHO.crew && chat) ECHO.chats[ECHO.crew] = chat.innerHTML;
  const withEl = document.getElementById('echo-with');
  if (withEl) withEl.textContent = `with ${_crewDisplayName(crew)} · private`;
  document.getElementById('echo-support')?.setAttribute('hidden', '');
  document.getElementById('echo-feel')?.classList.remove('open');
  if (ECHO.chats[crew] && chat) { chat.innerHTML = ECHO.chats[crew]; chat.scrollTop = chat.scrollHeight; ECHO.crew = crew; }
  else { if (chat) chat.innerHTML = ''; ECHO.crew = null; echoTyping(true); }
  echoRefresh();
}
// Poll ECHO's mode. A new crew member = the daily greeting; a new support reason = ECHO checks in on its own.
async function echoRefresh(event, opts = {}) {
  const crew = echoCrewId();
  try {
    const r = await fetch(`${DASH_API.baseUrl}/api/echo/${crew}${event ? `?event=${encodeURIComponent(event)}` : ''}`);
    if (!r.ok) return null;
    const d = await r.json();
    if (crew !== echoCrewId()) return null;
    const withEl = document.getElementById('echo-with');
    if (withEl) withEl.textContent = `with ${d.name} · Day ${d.facts.mission_day} of ${d.facts.mission_days} · private`;
    if (ECHO.crew !== crew) {                       // switched person: their own conversation comes back
      const chat = document.getElementById('echo-chat');
      if (ECHO.crew && chat) ECHO.chats[ECHO.crew] = chat.innerHTML;
      ECHO.crew = crew; ECHO.lastProactive = d.reason;
      if (ECHO.chats[crew] && chat) {
        chat.innerHTML = ECHO.chats[crew]; chat.scrollTop = chat.scrollHeight;
        let resetNote = null; try { resetNote = sessionStorage.getItem('aegis.echo.resetNote'); sessionStorage.removeItem('aegis.echo.resetNote'); } catch (e) {}
        if (resetNote) echoBubble('note', 'Demo reset · your conversation with ECHO is kept');
      }
      else {
        if (chat) chat.innerHTML = '';
        echoBubble('note', `Daily check-in · Day ${d.facts.mission_day} · readings as of ${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} (Live Vitals has the current values)`);
        if (ECHO.ready && opts.speak) echoSay(d.text, d.level === 'urgent' ? 'urgent' : '');
        else echoBubble('echo', echoIntro(d.text), d.level === 'urgent' ? 'urgent' : '');
      }
      ECHO.ready = true;
    } else if (d.reason && d.reason !== ECHO.lastProactive && ['after_emergency', 'poor_sleep'].includes(d.reason)) {
      ECHO.lastProactive = d.reason;
      echoBubble('note', d.reason === 'after_emergency' ? 'ECHO is checking in after the emergency' : 'MIRROR flagged poor sleep · ECHO checks in');
      setTimeout(() => echoSay(d.text), 1100);
    } else if (!d.reason) ECHO.lastProactive = null;
    echoSetMode(d.mode, d.level);
    echoRenderSupport(d);
    return d;
  } catch (e) { return null; }
}

async function echoPost(path, body) {
  const r = await fetch(`${DASH_API.baseUrl}/api/echo/${echoCrewId()}/${path}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}
async function echoReply(promise) {
  echoTyping(true);
  try {
    const [d] = await Promise.all([promise, new Promise(res => setTimeout(res, 700))]);
    echoTyping(false);
    echoSetMode(d.mode, d.level);
    echoRenderSupport(d);
    echoSay(d.text, d.level === 'urgent' ? 'urgent' : '', { after: eb => {
      if (eb && d.source === 'deepseek') { const t = document.createElement('span'); t.className = 'eb-src'; t.textContent = '🧠 DeepSeek'; t.title = 'Worded by DeepSeek, checked before it was shown'; eb.querySelector('.eb-who')?.appendChild(t); }
      if (d.alerted) echoBubble('note', `ECHO alerted ${d.alerted.name} · nothing from the chat was shared`);
      if (d.shared_with) echoBubble('note', `Shared with ${d.shared_with.name} · only that you'd like to talk`);
    } });
  } catch (e) {
    echoTyping(false);
    echoBubble('echo', "I can't reach the ship's systems right now, but I'm still here. Try me again in a moment.");
  }
}
function echoCheckin(mood) {
  ECHO.pickedAt = Date.now();
  document.getElementById('echo-feel')?.classList.remove('open');
  echoBubble('crew', ECHO_MOOD_FACE[mood]);
  echoReply(echoPost('checkin', { mood }));
}
function echoSend() {
  const input = document.getElementById('echo-input');
  const msg = (input.value || '').trim(); if (!msg) return;
  input.value = '';
  echoBubble('crew', msg);
  echoReply(echoPost('chat', { message: msg }));
}
function echoShare(to) { echoBubble('crew', to === 'ground' ? 'Yes, send a private note to the flight surgeon.' : 'Yes, please let them know.'); echoReply(echoPost('share', { to, consent: true })); }   // the click is the crew member's yes
function echoDismiss() { echoBubble('crew', "I'm okay for now."); echoReply(echoPost('dismiss', {})); }

setInterval(() => { if (!STORY.on || STORY.allowEcho) echoRefresh(); }, 8000);
setTimeout(() => echoRefresh(), 1800);

