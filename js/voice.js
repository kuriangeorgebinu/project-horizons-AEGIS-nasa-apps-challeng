// ============================================
// VOICE: natural speech, in sync with the on-screen text
// The response is spoken sentence by sentence while the words appear as they are said
// (word-boundary events when the voice provides them, otherwise paced to the speaking rate).
// The best available voice is picked automatically (Edge "Natural"/neural and Chrome "Google"
// voices first, robotic SAPI voices like David/Zira last) and can be changed in the Voice Terminal.
// ============================================
let typingTimer = null;
const VOICE = { muted: false, voice: null, rate: 1.0, pitch: 1.0, run: 0, byAgent: {}, userChoice: {}, nextSpeaker: null };
// Neural voices (Amazon Polly via /api/tts); declared early because the voice picker reads it at load
const TTS = { on: false, voices: {}, current: null, cache: new Map(), fails: 0, MAX: 600 };
const _VOICE_PREF = [/natural/i, /neural/i, /online/i, /google us english/i, /google uk english/i, /google/i,
                     /samantha|ava|allison|serena|karen|daniel|moira|tessa|evan|nathan/i,
                     /\bmark\b|zira/i];   // best of the built-in Windows voices (David sounds the most robotic)

function _voiceScore(v) {
  let s = 0;
  _VOICE_PREF.forEach((re, i) => { if (re.test(v.name)) s = Math.max(s, 100 - i * 10); });
  if (/en-US/i.test(v.lang)) s += 3; else if (/en-(GB|CA|AU|IE|NZ)/i.test(v.lang)) s += 2;
  if (/aria|jenny|ava|emma|sonia|libby|michelle|female/i.test(v.name)) s += 1;
  return s;
}

function englishVoices() {
  if (!('speechSynthesis' in window)) return [];
  return speechSynthesis.getVoices().filter(v => /^en([-_]|$)/i.test(v.lang))
    .sort((a, b) => _voiceScore(b) - _voiceScore(a));
}

// ── A different voice for every agent ─────────────────────────────────────────────────
// Each agent prefers voices that fit its personality (names cover Edge "Natural", Chrome "Google" and the
// built-in Windows voices). Voices are handed out so no two agents share one when the browser has enough;
// a small pitch/speed difference keeps them distinct even when voices run out.
const AGENT_VOICE = {
  aegis:   { pref: /aria|jenny|ava\b|google us english|samantha|zira/i,                     rate: 1.0,  pitch: 1.0  }, // steady lead
  solaris: { pref: /guy|davis|christopher|andrew|brian|eric|google uk english male|\bmark\b|daniel|david/i, rate: 0.95, pitch: 0.9 }, // calm, dry wit
  gene:    { pref: /sonia|libby|maisie|natasha|clara|google uk english female|linda|hazel|susan|karen/i,  rate: 0.98, pitch: 1.08 }, // gentle
  pulse:   { pref: /ryan|tony|jason|brandon|roger|steffan|richard|thomas|george|\bmark\b|david/i,        rate: 1.08, pitch: 1.05 }, // upbeat coach
  mirror:  { pref: /emma|michelle|ana\b|jane|nancy|sara|moira|tessa|linda|zira/i,                     rate: 0.94, pitch: 1.0  }, // warm
  echo:    { pref: /libby|sonia|serena|emma|ava\b|michelle|hazel|susan|samantha|zira/i,              rate: 0.92, pitch: 1.18 }, // companion: soft, friendly, unlike AEGIS
};
const _shortVoiceName = v => v ? v.name.replace(/^Microsoft\s+/, '').replace(/\s*Online.*$/, '').replace(/\s*-\s*English.*$/, '') : 'Default';

function assignAgentVoices() {
  const vs = englishVoices();
  const used = new Set(Object.values(VOICE.userChoice || {}));
  VOICE.byAgent = {};
  ['aegis', 'solaris', 'gene', 'pulse', 'mirror', 'echo'].forEach(a => {
    const chosen = VOICE.userChoice && VOICE.userChoice[a] && vs.find(v => v.name === VOICE.userChoice[a]);
    if (chosen) { VOICE.byAgent[a] = chosen; return; }
    const ranked = vs.map(v => ({ v, s: _voiceScore(v) + (AGENT_VOICE[a].pref.test(v.name) ? 60 : 0) }))
                     .sort((x, y) => y.s - x.s);
    let pick = (ranked.find(r => !used.has(r.v.name)) || null);
    // Few voices installed: agents may share, but ECHO never sounds like AEGIS if any other voice exists
    if (!pick) pick = (a === 'echo' && ranked.find(r => VOICE.byAgent.aegis && r.v.name !== VOICE.byAgent.aegis.name)) || ranked[0] || null;
    pick = pick ? pick.v : null;
    if (pick) used.add(pick.name);
    VOICE.byAgent[a] = pick;
  });
  VOICE.voice = VOICE.byAgent.aegis;
}

// The voice picker shows (and changes) the voice of the agent currently selected
function refreshVoicePicker() {
  const sel = document.getElementById('voiceSelect');
  if (!sel) return;
  const vs = englishVoices();
  const agent = (typeof currentAgent !== 'undefined' && AGENT_VOICE[currentAgent]) ? currentAgent : 'aegis';
  const mine = VOICE.byAgent && VOICE.byAgent[agent];
  const neural = (TTS.on && TTS.voices[agent])
    ? `<option value="neural">✨ ${TTS.voices[agent]} (natural)</option>` : '';
  sel.innerHTML = neural + vs.map((v, i) => `<option value="${i}">${_shortVoiceName(v)}${v === mine ? ' ★' : ''}</option>`).join('')
    || '<option>Default voice</option>';
  const i = vs.indexOf(mine);
  if (neural && ttsFor(agent)) sel.value = 'neural';
  else if (i >= 0) sel.value = String(i);
  sel.title = `Voice for ${agent.toUpperCase()} (each agent has its own)`;
}

function pickVoice() {
  assignAgentVoices();
  refreshVoicePicker();
  return VOICE.voice;
}
if ('speechSynthesis' in window) {
  pickVoice();
  // Chrome fills the voice list a few seconds after load and does not always fire 'voiceschanged':
  // listen for the event AND poll until the list stops changing, so agents always get their voices.
  speechSynthesis.addEventListener('voiceschanged', pickVoice);
  let _vCount = speechSynthesis.getVoices().length, _vTries = 0;
  const _vPoll = setInterval(() => {
    const n = speechSynthesis.getVoices().length;
    if (n !== _vCount) { _vCount = n; pickVoice(); }
    if (++_vTries > 50 || (n > 0 && _vTries > 12)) clearInterval(_vPoll);   // ~15 s max
  }, 300);
}

// Browsers only allow speech after the user has interacted with the page. Until then answers are typed,
// and the Voice Terminal says how to turn the voice on; the first click / key press unlocks it.
VOICE.unlocked = false;
try {
  VOICE.muted = localStorage.getItem('aegis-muted') === '1';
  const muteButton = document.getElementById('muteBtn');
  if (muteButton) {
    muteButton.textContent = VOICE.muted ? '🔇' : '🔊';
    muteButton.setAttribute('aria-pressed', String(VOICE.muted));
  }
} catch (e) {}
function _voiceHint(show) {
  const st = document.getElementById('voiceStatus');
  if (!st) return;
  if (show && !VOICE.unlocked) { st.textContent = 'Click anywhere on the page to turn on the voice'; st.classList.add('voice-hint'); }
  else if (!show && st.classList.contains('voice-hint')) {
    st.classList.remove('voice-hint');
    st.textContent = 'Tap to speak with ' + ((typeof AGENTS !== 'undefined' && AGENTS[currentAgent]) ? AGENTS[currentAgent].name : 'AEGIS');
  }
}
function _unlockVoice() {
  if (VOICE.unlocked || !('speechSynthesis' in window)) return;
  VOICE.unlocked = true;
  try {
    speechSynthesis.resume();
    const u = new SpeechSynthesisUtterance(' ');
    u.volume = 0;
    speechSynthesis.speak(u);                     // a silent utterance inside the gesture unlocks audio
  } catch (e) { /* ignore */ }
  if (!englishVoices().length) pickVoice();
  _voiceHint(false);
  ['pointerdown', 'keydown', 'touchstart'].forEach(ev => document.removeEventListener(ev, _unlockVoice, true));
}
['pointerdown', 'keydown', 'touchstart'].forEach(ev => document.addEventListener(ev, _unlockVoice, true));

function chooseVoice(i) {
  const agent = AGENT_VOICE[currentAgent] ? currentAgent : 'aegis';
  if (i === 'neural') {                              // back to the natural (Polly) voice
    const uc = { ...(VOICE.userChoice || {}) }; delete uc[agent]; VOICE.userChoice = uc;
    assignAgentVoices(); refreshVoicePicker(); VOICE.nextSpeaker = agent;
    typeResponse(`Hi, this is ${agent === 'aegis' ? 'AEGIS' : agent.toUpperCase()}. This is my natural voice.`);
    return;
  }
  const v = englishVoices()[Number(i)];
  if (!v) return;
  VOICE.userChoice = { ...(VOICE.userChoice || {}), [agent]: v.name };
  assignAgentVoices();
  refreshVoicePicker();
  VOICE.nextSpeaker = agent;
  typeResponse(`Hi, this is ${agent === 'aegis' ? 'AEGIS' : agent.toUpperCase()}. This is my new voice.`);
}

// ── Neural voices: Amazon Polly through /api/tts (each agent its own voice) ───────────────────────
// Used whenever the backend offers it; the browser's built-in voices stay as the fallback.
fetch(AEGIS_API_ORIGIN + '/api/tts/status').then(r => r.ok ? r.json() : null).then(d => {
  if (d && d.enabled) { TTS.on = true; TTS.voices = d.voices || {}; refreshVoicePicker(); }
}).catch(() => {});
function ttsFor(agent) { return TTS.on && !(VOICE.userChoice && VOICE.userChoice[agent]); }
function ttsAudio(text, agent) {
  const key = agent + '|' + text;
  let a = TTS.cache.get(key);
  if (!a) {
    a = new Audio(AEGIS_API_ORIGIN + '/api/tts?agent=' + encodeURIComponent(agent) + '&text=' + encodeURIComponent(text));
    a.preload = 'auto';
    TTS.cache.set(key, a);
    if (TTS.cache.size > 80) TTS.cache.delete(TTS.cache.keys().next().value);
  }
  return a;
}
function ttsFailed(text, agent) {
  TTS.cache.delete(agent + '|' + text);
  if (++TTS.fails >= 2) { TTS.on = false; refreshVoicePicker(); }
}
function ttsStop() {
  const a = TTS.current; TTS.current = null;
  if (a) { try { a.onended = a.onerror = a.onplaying = null; a.pause(); } catch (e) {} }
}
// Say several sentences in one agent's neural voice (no on-screen text). Calls fallback() if it can't.
function ttsSayAll(text, agent, { onstart, onend, fallback } = {}) {
  if (VOICE.muted) { onend && onend(); return; }
  const parts = splitSentences(text).map(speechify).filter(Boolean);
  if (!ttsFor(agent) || !parts.length || parts.some(p => p.length > TTS.MAX)) { fallback && fallback(); return; }
  VOICE.run++;
  const run = VOICE.run;
  parts.forEach(p => ttsAudio(p, agent));            // fetch them all now so there are no gaps
  let i = 0, first = true;
  const step = () => {
    if (run !== VOICE.run) return;
    if (VOICE.muted) { onend && onend(); return; }
    if (i >= parts.length) { onend && onend(); return; }
    const p = parts[i], a = ttsAudio(p, agent);
    a.onplaying = () => { if (first) { first = false; onstart && onstart(); } };
    a.onended = () => { if (TTS.current === a) TTS.current = null; i++; step(); };
    a.onerror = () => {
      ttsFailed(p, agent);
      if (run !== VOICE.run) return;
      onend && onend();
      if (i === 0 && !VOICE.muted && fallback) fallback();
    };
    TTS.current = a;
    try { a.currentTime = 0; } catch (e) {}
    a.play().catch(() => {
      if (run !== VOICE.run) return;
      onend && onend();
      if (i === 0 && !VOICE.muted && fallback) fallback();
    });
  };
  step();
}

// Rewrite dashboard text the way a person would say it (the screen keeps the original text)
const _SAY_AS_WORD_KEEP = new Set(['NASA', 'NOAA', 'GOES', 'UTC', 'DONKI', 'OSDR']);
function speechify(t) {
  let s = ' ' + String(t || '') + ' ';
  const rules = [
    [/\[\d+(?:\s*,\s*\d+)*\]/g, ''],                                   // citation markers [1], [2, 3]
    [/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{FE0F}\u{200D}\u{23F0}-\u{23FF}]/gu, ''],  // emoji, ⏱ ✓ ☢
    [/SpO₂|SpO2/g, 'oxygen saturation'],
    [/\bIL-6\b/g, 'I L 6'], [/\bCRP\b/g, 'C R P'], [/\bHRV\b/g, 'H R V'], [/\bEVA\b/g, 'E V A'],
    [/(\d)\s*mSv\b/g, '$1 millisieverts'], [/\bmSv\b/g, 'millisieverts'], [/µGy/g, 'micrograys'],
    [/(\d)\s*(?:bpm|BPM)\b/g, '$1 beats per minute'], [/pg\/mL/g, 'picograms per millilitre'], [/mg\/L/g, 'milligrams per litre'],
    [/\/100\b/g, ' out of 100'], [/(\d)\s*%/g, '$1 percent'],
    [/<\s*1\s*min\b/g, 'under a minute'], [/[~≈]\s*/g, 'about '],
    [/\b1\s*min\b/g, '1 minute'], [/(\d)\s*min\b/g, '$1 minutes'], [/(\d)\s*h\b/g, '$1 hours'], [/(\d)s\b/g, '$1 seconds'],
    [/≥\s*/g, 'at least '], [/≤\s*/g, 'at most '], [/\s*(?:→|->)\s*/g, ' to '],
    [/\s*[·•|]\s*/g, ', '], [/\s*[—–]\s*/g, ', '], [/(\d)\s*\/\s*(\d)/g, '$1 of $2'], [/\s*\/\s*/g, ' or '],
    [/\bCmdr\.\s*/g, 'Commander '], [/\bEng\.\s*/g, 'Engineer '], [/\bDr\.\s*/g, 'Doctor '],
    [/\bvs\.?\s/g, 'versus '], [/\be\.g\.\s*/g, 'for example '], [/\bi\.e\.\s*/g, 'that is '],
    [/\b([A-Z]{3,})-(\d)\b/g, (m, w, d) => w[0] + w.slice(1).toLowerCase() + ' ' + d],   // RECOVER-3 -> Recover 3
    [/\bcrew-0*(\d+)\b/g, 'crew member $1'],
  ];
  rules.forEach(([re, to]) => { s = s.replace(re, to); });
  // ALL-CAPS names (AEGIS, SOLARIS, CASCADE, PREDICT…) are read as words instead of being spelled out
  s = s.replace(/\b[A-Z]{4,}\b/g, w => _SAY_AS_WORD_KEEP.has(w) ? w : w[0] + w.slice(1).toLowerCase());
  return s.replace(/\s+/g, ' ').replace(/\s+([,.;:!?])/g, '$1').replace(/,(\s*,)+/g, ',').replace(/^[\s,]+/, '').trim();
}

function splitSentences(text) {
  return String(text || '').trim()
    .split(/(?<!\b(?:Dr|Cmdr|Eng|Mr|Ms|Mrs|St|vs|e\.g|i\.e)\.)(?<=[.!?])\s+(?=\S)/)
    .map(s => s.trim()).filter(Boolean);
}

// Stops the voice. Whatever was being typed is completed on screen at once (never left cut off mid-word),
// and the floor passes to the next speaker in line.
function stopVoice() {
  VOICE.run++;
  if (typingTimer) { clearTimeout(typingTimer); typingTimer = null; }
  ttsStop();
  if ('speechSynthesis' in window) speechSynthesis.cancel();
  const cur = FLOOR.current; FLOOR.current = null;
  if (cur) { try { cur.flush && cur.flush(); } catch (e) {} cur.release && cur.release(); }
}

// ── THE FLOOR: agents take turns. A new message waits until the current speaker has finished; nobody cuts in.
// An emergency message goes to the front of the line (still without cutting anyone off). A message that has
// waited too long is shown as text without being read out, so the voice never falls far behind the ship.
const FLOOR = { current: null, q: [], lastSpeaker: null, lastEchoAt: 0, recent: [] };
const FLOOR_STALE_MS = 25000;
function floorRequest(item) {
  // Sense: the same agent saying the same thing again within a minute is dropped
  const sig = `${item.speaker}|${String(item.text || '').slice(0, 120)}`;
  const now = Date.now();
  FLOOR.recent = FLOOR.recent.filter(r => now - r.at < 60000);
  if (item.text && !item.force && FLOOR.recent.some(r => r.sig === sig)) { item.onSkip && item.onSkip(); return; }
  FLOOR.recent.push({ sig, at: now });
  item.at = now;
  if (FLOOR.current) {
    if (item.urgent) { const i = FLOOR.q.findIndex(x => !x.urgent); FLOOR.q.splice(i < 0 ? FLOOR.q.length : i, 0, item); }
    else FLOOR.q.push(item);
    return;
  }
  _floorStart(item);
}
function _floorStart(item) {
  let released = false;
  const slot = { item, flush: null, release: () => {
    if (released) return; released = true;
    if (FLOOR.current === slot) FLOOR.current = null;
    FLOOR.lastSpeaker = item.speaker; if (item.speaker === 'echo') FLOOR.lastEchoAt = Date.now();
    const next = FLOOR.q.shift();
    if (next) setTimeout(() => (FLOOR.current ? FLOOR.q.unshift(next) : _floorStart(next)), 450);   // a breath between speakers
  } };
  FLOOR.current = slot;
  const stale = Date.now() - item.at > FLOOR_STALE_MS;
  try { item.run(slot, stale); } catch (e) { slot.release(); }
}

function typeResponse(text, isLiveAnswer = false, onDone = null, opts = {}) {
  // Who is talking: set by askAgent / alerts via VOICE.nextSpeaker (AEGIS by default)
  const speaker = AGENT_VOICE[VOICE.nextSpeaker] ? VOICE.nextSpeaker : 'aegis';
  VOICE.nextSpeaker = null;
  const tag = isLiveAnswer ? VOICE.nextTag : null;
  VOICE.nextTag = null;
  floorRequest({ speaker, text, urgent: !!opts.urgent, force: !!opts.force || isLiveAnswer,
                 onSkip: () => { if (typeof onDone === 'function') onDone(); },
                 run: (slot, stale) => _typeResponseNow(text, speaker, tag, onDone, slot, stale) });
}

function _typeResponseNow(text, speaker, tag, onDone, slot, stale) {
  VOICE.run++;
  if (typingTimer) { clearTimeout(typingTimer); typingTimer = null; }
  document.querySelector('.voice-panel')?.classList.add('has-reply');
  const run = VOICE.run;
  const sVoice = (VOICE.byAgent && VOICE.byAgent[speaker]) || VOICE.voice;
  const sRate = VOICE.rate * AGENT_VOICE[speaker].rate, sPitch = VOICE.pitch * AGENT_VOICE[speaker].pitch;
  const el = chatAgentBubble(speaker, tag);
  el.innerHTML = '';
  const cursor = document.createElement('span');
  cursor.className = 'typing-cursor';
  const sentences = splitSentences(text);
  let shown = '';                                   // sentences already spoken in full
  const render = partial => { el.textContent = shown + partial; el.appendChild(cursor); };
  // onDone runs once the whole answer has been shown (and spoken), also if it was finished early
  const done = () => { if (typeof onDone === 'function') { const f = onDone; onDone = null; f(); } };
  slot.flush = () => { el.textContent = sentences.join(' ') || String(text || ''); cursor.remove(); done(); };
  const finish = () => { if (run !== VOICE.run) return; el.textContent = sentences.join(' '); cursor.remove(); done(); slot.release(); };
  if (stale) { finish(); return; }                  // waited too long in line: show it, don't read it out

  // No speech available (or blocked): type the remaining text at reading speed
  const typeRest = () => {
    if (run !== VOICE.run) return;
    const rest = sentences.join(' ').slice(shown.length);
    let i = 0;
    const step = () => {
      if (run !== VOICE.run) return;
      if (i < rest.length) { render(rest.slice(0, ++i)); typingTimer = setTimeout(step, 22); }
      else finish();
    };
    step();
  };
  if (VOICE.muted || (!('speechSynthesis' in window) && !ttsFor(speaker)) || !sentences.length) { typeRest(); return; }

  let idx = 0;
  const next = () => {
    if (run !== VOICE.run) return;
    if (VOICE.muted) { typeRest(); return; }
    if (idx >= sentences.length) { finish(); return; }
    const disp = sentences[idx];
    const words = disp.split(/\s+/).filter(Boolean);
    const spoken = speechify(disp);
    const spokenWords = Math.max(1, spoken.split(/\s+/).filter(Boolean).length);
    const showFrac = f => render(words.slice(0, Math.min(words.length, Math.ceil(f * words.length))).join(' '));
    const advance = () => { shown += disp + ' '; idx++; render(''); next(); };
    if (!spoken) { advance(); return; }

    // Neural voice: words appear in step with the audio; prefetch the next sentence to avoid gaps
    if (ttsFor(speaker) && spoken.length <= TTS.MAX) {
      const a = ttsAudio(spoken, speaker);
      const nxt = sentences[idx + 1] && speechify(sentences[idx + 1]);
      if (nxt && nxt.length <= TTS.MAX) ttsAudio(nxt, speaker);
      let tp = null, began = false;
      const cleanup = () => { clearInterval(tp); a.onplaying = a.onended = a.onerror = null; };
      a.onplaying = () => {
        began = true; clearInterval(tp);
        tp = setInterval(() => {
          if (run !== VOICE.run) { clearInterval(tp); return; }
          const d = a.duration;
          if (d && isFinite(d)) showFrac(Math.min(1, a.currentTime / d));
        }, 60);
      };
      a.onended = () => { cleanup(); if (TTS.current === a) TTS.current = null; if (run === VOICE.run) advance(); };
      a.onerror = () => { cleanup(); ttsFailed(spoken, speaker); if (run === VOICE.run) browserSay(); };
      TTS.current = a;
      try { a.currentTime = 0; } catch (e) {}
      if (VOICE.muted || run !== VOICE.run) { if (TTS.current === a) TTS.current = null; return; }
      a.play().catch(err => {
        cleanup();
        if (run !== VOICE.run || VOICE.muted) return;
        if (err && err.name === 'NotAllowedError') typeRest(); else browserSay();   // no click yet: just type
      });
      setTimeout(() => {                             // voice service too slow: use the browser voice
        if (run === VOICE.run && !began && TTS.current === a) { cleanup(); a.pause(); TTS.current = null; browserSay(); }
      }, 8000);
      return;
    }
    browserSay();

    function browserSay() {
    if (run !== VOICE.run) return;
    if (VOICE.muted) { typeRest(); return; }
    if (!('speechSynthesis' in window)) { typeRest(); return; }
    const u = new SpeechSynthesisUtterance(spoken);
    if (sVoice) { u.voice = sVoice; u.lang = sVoice.lang; }
    u.rate = sRate; u.pitch = sPitch;
    let started = false, gotBoundary = false, pacer = null;
    u.onstart = () => {
      started = true;
      // Pace the words to the speaking rate until (unless) the voice reports word boundaries
      const est = spokenWords * 60000 / (165 * sRate) + 200, t0 = performance.now();
      pacer = setInterval(() => {
        if (run !== VOICE.run) { clearInterval(pacer); return; }
        if (!gotBoundary) showFrac(Math.min(0.96, (performance.now() - t0) / est));
      }, 60);
    };
    u.onboundary = e => {
      if (run !== VOICE.run || (e.name && e.name !== 'word')) return;
      gotBoundary = true;
      const done = spoken.slice(0, e.charIndex + (e.charLength || 1)).split(/\s+/).filter(Boolean).length;
      showFrac(done / spokenWords);
    };
    u.onend = () => { clearInterval(pacer); if (run === VOICE.run) advance(); };
    u.onerror = () => {
      clearInterval(pacer);
      if (run !== VOICE.run || VOICE.muted) return;
      if (started) advance(); else typeRest();      // e.g. browser blocks audio before the first click
    };
    if (VOICE.muted || run !== VOICE.run) return;
    speechSynthesis.speak(u);
    const myIdx = idx;
    setTimeout(() => {                               // speech never started: fall back to typing
      if (run === VOICE.run && idx === myIdx && !started) { speechSynthesis.cancel(); VOICE.run++; const r = VOICE.run; typeRestFrom(r); }
    }, 2500);
    }
  };
  // typeRest bound to a fresh run id after a silent speech failure
  const typeRestFrom = r => {
    const rest = sentences.join(' ').slice(shown.length);
    let i = 0;
    const step = () => {
      if (r !== VOICE.run) return;
      if (i < rest.length) { el.textContent = shown + rest.slice(0, ++i); el.appendChild(cursor); typingTimer = setTimeout(step, 22); }
      else { el.textContent = sentences.join(' '); cursor.remove(); done(); slot.release(); }
    };
    step();
  };
  // Chrome drops an utterance queued right after cancel(); give it a moment
  setTimeout(next, 60);
}

// Speak without changing the response text (kept for callers that only want audio)
function speak(text) {
  const browser = () => {
    if (VOICE.muted || !('speechSynthesis' in window)) return;
    const u = new SpeechSynthesisUtterance(speechify(text));
    if (VOICE.voice) { u.voice = VOICE.voice; u.lang = VOICE.voice.lang; }
    u.rate = VOICE.rate; u.pitch = VOICE.pitch;
    if (!VOICE.muted) speechSynthesis.speak(u);
  };
  if (VOICE.muted) return;
  ttsSayAll(text, 'aegis', { fallback: browser });
}

// Voice input was removed: the agents are chatted with by typing. These stay as no-ops for old callers.
function toggleListening() { document.getElementById('askInput')?.focus(); }
function startListening() {}
function stopListening() {}

function processCommand(raw) {
  raw = (raw || '').trim();
  const cmd = raw.toLowerCase();                   // routing on lower case, the question itself as typed
  // Wake word: "hey solaris, what's the dose?" → SOLARIS answers the rest.
  // A wake word on its own → that agent gives a live status report.
  const wake = cmd.match(/^(?:hey\s+|ok\s+)?(aegis|solaris|gene|pulse|mirror)\b[\s,.:!?-]*(.*)$/);
  if (wake) {
    const rest = wake[2].trim();
    if (rest.split(/\s+/).filter(Boolean).length >= 2) {
      selectAgent(wake[1], { silent: true });
      askAgent(wake[1], raw.slice(raw.length - rest.length));
    } else {
      selectAgent(wake[1]);
    }
    return;
  }
  // No wake word: route obvious domain questions to the specialist, otherwise ask the current agent
  let agent = currentAgent;
  if (currentAgent === 'aegis') {
    if (/radiation|solar|dose|shelter|sievert|msv/.test(cmd)) agent = 'solaris';
    else if (/il-?6|crp|inflamm|immune|biomarker/.test(cmd)) agent = 'gene';
    else if (/heart|pulse|blood pressure|spo2|oxygen|bone|muscle/.test(cmd)) agent = 'pulse';
    else if (/stress|sleep|mood|mental|behavio|isolation|fatigue/.test(cmd)) agent = 'mirror';
  }
  if (agent !== currentAgent) {
    // AEGIS hands the question to the specialist, and takes the conversation back once it has answered
    selectAgent(agent, { silent: true });
    askAgent(agent, raw, undefined, { handBackTo: 'aegis' });
    return;
  }
  askAgent(agent, raw);
}

// After a routed answer has been spoken, return the conversation to AEGIS
// (only if nothing else happened meanwhile: no newer question, no manual agent change)
function handBack(fromAgent, toAgent, seq) {
  if (seq !== _askSeq || currentAgent !== fromAgent || fromAgent === toAgent) return;
  selectAgent(toAgent, { silent: true });
  const status = document.getElementById('voiceStatus');
  if (status) status.textContent = `${AGENTS[fromAgent].name} handed back to ${AGENTS[toAgent].name}`;
}

// Cascade animation
function triggerCascade() {
  const agents = ['solaris', 'gene', 'pulse', 'mirror'];
  agents.forEach((agent, i) => {
    setTimeout(() => {
      const line = document.getElementById('line-' + agent);
      const node = document.getElementById('node-' + agent);
      if (line) { line.classList.add('active'); setTimeout(() => line.classList.remove('active'), 600); }
      if (node) { node.classList.add('active'); setTimeout(() => node.classList.remove('active'), 600); }
      playTone(300 + i * 100, 0.2);
    }, i * 400);
  });
}

function confirmProtocol() {
  // E2E: acknowledge the live backend alert (if any) so the confirmation is recorded
  const ackId = LIVE_ALERT.id;
  const ackProtocol = LIVE_ALERT.protocol || 'RECOVER-3';
  const ackCrew = LIVE_ALERT.crewName || 'Commander Chen';
  const ackCrewKey = LIVE_ALERT.crewKey || 'chen';
  LIVE_ALERT.pauseUntil = Date.now() + 6000;  // keep the success state visible briefly
  _ackEmergency();
  // PREDICT: the crew member is now recovering under the protocol; don't re-forecast from the trend tail
  if (typeof PREDICT_STATE !== 'undefined') PREDICT_STATE.suppress[DASH_API.crewIdMap[ackCrewKey] || 'crew-001'] = Date.now() + 90000;
  // Show the protocol being carried out on board (spacecraft sim pop-up)
  if (!(typeof STORY !== 'undefined' && STORY.on)) setTimeout(() => openSimForProtocol(ackProtocol, ackCrewKey), 1700);
  if (ackId) {
    const B = DASH_API.baseUrl;
    const src = _ackSource();
    const ack = id => { ACKED_HERE.set(id, src); return fetch(`${B}/api/alerts/${encodeURIComponent(id)}/acknowledge`, { method: 'POST' }); };
    ACKED_HERE.set(ackId, src);
    (async () => {
      await ack(ackId);
      // RECOVER-3 resolves the cascade (story: Day 61 "all indicators normalized");
      // SHELTER-1 rides out the SPE, and the sim ends on "Await all-clear". Both return the simulator to nominal.
      if (ackProtocol.startsWith('RECOVER') || ackProtocol.startsWith('SHELTER') || ackProtocol.startsWith('MEDIC')) {
        await fetch(`${B}/api/simulation/scenario/nominal`, { method: 'POST' }).catch(() => {});
      }
      if (ackProtocol.startsWith('RECOVER') || ackProtocol.startsWith('MEDIC')) {
        // RECOVER-3 / MEDIC-1 cover every domain for that crew member (e.g. MIRROR's alert raised during the cascade)
        const crewSweep = async () => {
          try {
            const crewId = DASH_API.crewIdMap[ackCrewKey];
            const open = await (await fetch(`${B}/api/alerts?unacknowledged_only=true&limit=200`)).json();
            await Promise.all(open.filter(a => a.crew_member_id === crewId && !/SHELTER/i.test(a.title || '')).map(a => ack(a.id)));
          } catch (e) {}
        };
        await crewSweep();
        // Second sweep catches an alert from an analysis pass that was already in flight when we confirmed
        await new Promise(r => setTimeout(r, 4000));
        await crewSweep();
      }
      if (ackProtocol.startsWith('SHELTER')) {
        // SHELTER-1 is crew-wide: one confirmation covers every open SHELTER alert (SPE raises one per crew member)
        const sweep = async () => {
          try {
            const open = await (await fetch(`${B}/api/alerts?unacknowledged_only=true&limit=200`)).json();
            await Promise.all(open.filter(a => /SHELTER/i.test(a.title || '')).map(a => ack(a.id)));
          } catch (e) {}
        };
        await sweep();
        // Second sweep catches alerts from an analysis run that was already in flight when we confirmed
        await new Promise(r => setTimeout(r, 4000));
        await sweep();
      }
      setTimeout(refreshLiveAlerts, 6000);
    })().catch(() => {});
  }

  // WOW: Screen celebration pulse
  const overlay = document.createElement('div');
  overlay.className = 'celebration-overlay';
  document.body.appendChild(overlay);
  setTimeout(() => overlay.remove(), 1000);
  
  // WOW: Cascade with checkmarks
  const agents = ['solaris', 'gene', 'pulse', 'mirror'];
  agents.forEach((agent, i) => {
    setTimeout(() => {
      const line = document.getElementById('line-' + agent);
      const node = document.getElementById('node-' + agent);
      if (line) { line.classList.add('active'); setTimeout(() => line.classList.remove('active'), 800); }
      if (node) {
        node.classList.add('active');
        createParticleBurst(node);           // Cascade Map: each specialist confirms in turn
        setTimeout(() => node.classList.remove('active'), 800);
      }
      playTone(400 + i * 100, 0.15);
    }, i * 300);
  });
  
  // Victory sound sequence
  setTimeout(() => {
    playTone(523, 0.15); // C
    setTimeout(() => playTone(659, 0.15), 100); // E
    setTimeout(() => playTone(784, 0.3), 200); // G
  }, 1200);
  
  setTimeout(() => {
    if (ackProtocol.startsWith('SHELTER')) {
      typeResponse(`Done. Everyone is heading to the storm shelter now. SOLARIS will give the all-clear once the solar storm passes.`);
    } else if (ackProtocol.startsWith('MEDIC')) {
      typeResponse(`Done. ${ackCrew} is on the way to the medical bay with extra oxygen, and a crewmate is staying with them. I've also asked the flight surgeon back on Earth to take a look.`);
    } else {
      typeResponse(`Done. ${ackCrew}'s recovery plan has started: more fluids, lighter work, a bit of extra rest, and a quick check-in every day. I'll keep watching how they're doing.`);
    }
    document.querySelector('.confirm-btn').textContent = '✓ Plan started';
    document.querySelector('.confirm-btn').style.background = 'rgba(90,173,140,.3)';
    document.querySelector('.confirm-btn').disabled = true;
  }, 1500);
}

// Recommendation actions (PREVENT Tier)
function acceptRec(id) {
  const cards = document.querySelectorAll('.rec-card');
  const card = cards[id - 1];
  if (!card) return;
  
  card.style.borderColor = 'var(--green)';
  card.style.background = 'rgba(90,173,140,.1)';
  card.querySelector('.rec-intervention').style.color = 'var(--green)';
  card.querySelector('.rec-actions').innerHTML = '<div style="text-align:center;color:var(--green);font-weight:600;padding:.5rem;">✓ Accepted — Scheduling intervention</div>';
  
  playTone(523, 0.1);
  setTimeout(() => playTone(659, 0.15), 100);
  
  typeResponse("Got it. I'll get that started and let the crew member know on their display.");
}

function deferRec(id) {
  const cards = document.querySelectorAll('.rec-card');
  const card = cards[id - 1];
  if (!card) return;
  
  card.style.opacity = '0.7';
  card.querySelector('.rec-actions').innerHTML = '<div style="text-align:center;color:var(--yellow);font-size:.8rem;padding:.5rem;">◷ Deferred — Will re-evaluate in 4 hours</div>';
  
  playTone(350, 0.1);
}

function dismissRec(id) {
  const cards = document.querySelectorAll('.rec-card');
  const card = cards[id - 1];
  if (!card) return;
  
  card.style.opacity = '0.4';
  card.style.transform = 'scale(0.98)';
  card.querySelector('.rec-actions').innerHTML = '<div style="text-align:center;color:var(--muted);font-size:.8rem;padding:.5rem;">✕ Dismissed — Logged for review</div>';
  
  playTone(250, 0.1);
}

// H4: Fetch live vitals from API for selected crew member
let hrHistory = [68, 67, 69, 66, 68, 70];
async function fetchAndUpdateVitals() {
  const forCrew = DASH_API.selectedCrewId;     // a reply for the previously selected person must not overwrite the tiles
  try {
    const response = await fetch(`${DASH_API.baseUrl}/api/crew/${forCrew}`);
    if (!response.ok) throw new Error('API unavailable');
    const crew = await response.json();
    if (forCrew !== DASH_API.selectedCrewId) return;
    const vitals = crew.vitals;
    if (!vitals) return;
    
    // Update HR
    const hrEl = document.getElementById('hr-value');
    const hrFill = document.getElementById('hr-fill');
    const hrStatus = document.getElementById('hr-status');
    if (hrEl) {
      const hr = Math.round(vitals.heart_rate);
      hrEl.textContent = hr;
      if (hrFill) hrFill.style.width = Math.min(100, hr) + '%';
      // Update sparkline history
      hrHistory.push(hr);
      if (hrHistory.length > 6) hrHistory.shift();
      const sparkLine = document.getElementById('spark-line');
      if (sparkLine) {
        // Scale 40-160 bpm into the 24px-high sparkline (clamped so high rates stay visible)
        const points = hrHistory.map((v, i) => `${i * 10},${Math.max(2, Math.min(22, 22 - (v - 40) * 20 / 120)).toFixed(1)}`).join(' ');
        sparkLine.setAttribute('points', points);
      }
      // Same thresholds as the PULSE agent: >150 or <40 critical, >100 warning
      const hrBand = setVitalBand('hr', hr, (hr > 120 || hr < 40) ? 'out' : hr > 100 ? 'watch' : 'ok');
      const hrColor = hrBand.color;
      const hrLabel = hr > 150 ? '↑ Critical' : hr < 40 ? '↓ Critical' : hr > 120 ? '↑ Very high' : hrBand.word;
      hrEl.style.color = hrColor;
      if (hrFill) hrFill.style.background = hrColor;
      if (hrStatus) { hrStatus.textContent = hrLabel; hrStatus.style.color = hrColor; }
    }
    
    // GENE: modelled IL-6 from the backend (the exact value and personal-baseline limit the GENE agent uses)
    try {
      const br = await fetch(`${DASH_API.baseUrl}/api/crew/${forCrew}/biomarkers`);
      if (br.ok && forCrew === DASH_API.selectedCrewId) {
        const bm = await br.json();
        const il6El = document.getElementById('il6-value');
        const il6Fill = document.getElementById('il6-fill');
        const il6Status = document.getElementById('il6-status');
        const il6Row = document.getElementById('il6-row');
        if (bm.personal_limit) IL6_LIMIT = bm.personal_limit;
        const il6Band = setVitalBand('il6', bm.il6, bm.status === 'elevated' ? 'out' : bm.status === 'mild' ? 'watch' : 'ok');
        const color = il6Band.color;
        const label = bm.status === 'elevated' ? '↑ Above baseline' : bm.status === 'mild' ? '↑ Rising' : il6Band.word;
        if (il6El) { il6El.textContent = bm.il6.toFixed(1); il6El.style.color = color; }
        if (il6Fill) { il6Fill.style.width = Math.min(100, bm.il6 / (bm.personal_limit * 1.5) * 100) + '%'; il6Fill.style.background = color; }
        if (il6Status) { il6Status.textContent = label; il6Status.style.color = color; }
        if (il6Row) il6Row.title = `GENE · IL-6 ${bm.il6} ${bm.il6_unit} (MODELLED from stress + radiation, no blood sensor)` +
          ` · pre-flight baseline ${bm.baseline} pg/mL · flags at ${bm.personal_limit} pg/mL (1.5× baseline and +1.5)` +
          ` · baseline seeded inside the Inspiration4 crew's real pre-flight range (2.5-5.9 pg/mL, NASA OSDR OSD-575)` +
          ` · smoothed (at most 0.12 pg/mL per second); demo time is compressed, so a real rise would take hours, not minutes`;
      }
    } catch (e) { /* keep last value */ }
    
    // H1: Update per-crew radiation from API
    const radEl = document.getElementById('rad-value');
    const radFill = document.getElementById('rad-fill');
    if (radEl && crew.cumulative_radiation !== undefined) {
      const rad = Math.round(crew.cumulative_radiation);
      radEl.textContent = `${rad} / 600 mSv`;
      radEl.title = `Mission total so far: ${rad} mSv. Seeded at day 54 of the cruise × 1.84 mSv/day ≈ 95–103 mSv (Curiosity RAD); ` +
        'every simulated solar storm and every minute of the demo adds to it, so it reads higher after storms have run.';
      const radPct = Math.min(100, (rad / 600) * 100);
      if (radFill) radFill.style.width = radPct + '%';
      const radColor = rad > 400 ? 'var(--red)' : rad > 250 ? 'var(--yellow)' : 'var(--green)';
      radEl.style.color = radColor;
      if (radFill) radFill.style.background = radColor;
    }
    // SOLARIS: 24-hour dose against the mission background (same rule as the agent, from /api/mission)
    const rad24El = document.getElementById('rad24-value');
    const rad24Status = document.getElementById('rad24-status');
    if (rad24El && vitals.radiation_dose_24h !== undefined && vitals.radiation_dose_24h !== null) {
      const d = vitals.radiation_dose_24h;
      const c = d >= MISSION.shelter ? 'var(--red)' : d >= MISSION.warning ? 'var(--yellow)' : 'var(--green)';
      const ratio = d / MISSION.background;
      rad24El.textContent = d.toFixed(2) + ' mSv';
      rad24El.style.color = c;
      if (rad24Status) {
        rad24Status.textContent = d >= MISSION.shelter ? `· SHELTER (${ratio.toFixed(1)}× background)`
          : d >= MISSION.warning ? `· Elevated (${ratio.toFixed(1)}× background)` : `· WNL (normal background)`;
        rad24Status.style.color = c;
        rad24Status.title = `${MISSION.label}: normal background ${MISSION.background} mSv/day (${MISSION.source}). ` +
          `SOLARIS warns at ${MISSION.warning} and orders the shelter at ${MISSION.shelter} mSv/24 h. ` +
          'This is the current dose rate (mSv per 24 h), so it falls again once a solar storm passes; the mission total dose above only ever goes up.';
        rad24El.title = rad24Status.title;
      }
    }
    
    // Update SpO2
    const spo2El = document.getElementById('spo2-value');
    const spo2Fill = document.getElementById('spo2-fill');
    const spo2Status = document.getElementById('spo2-status');
    if (spo2El && vitals.spo2 !== undefined) {
      const spo2 = Math.round(vitals.spo2);
      spo2El.textContent = spo2 + '%';
      if (spo2Fill) spo2Fill.style.width = spo2 + '%';
      // Same thresholds as the PULSE agent: <90 critical, <95 low
      const spo2Band = setVitalBand('spo2', spo2, spo2 < 90 ? 'out' : spo2 < 95 ? 'watch' : 'ok');
      const spo2Color = spo2Band.color;
      const spo2Label = spo2 < 90 ? '↓ Critical' : spo2Band.word;
      spo2El.style.color = spo2Color;
      if (spo2Fill) spo2Fill.style.background = spo2Color;
      if (spo2Status) { spo2Status.textContent = spo2Label; spo2Status.style.color = spo2Color; }
    }
    
    // Blood pressure, temperature, respiration rate: shown against their normal limits (same limits as
    // agents.VITAL_THRESHOLDS); amber outside WNL, red beyond the critical limits. Not part of PULSE's alert rules.
    const showExtra = (k, value, text, state, word) => {
      const el = document.getElementById(`${k}-value`), st = document.getElementById(`${k}-status`);
      if (!el || value === undefined || value === null) return;
      const band = setVitalBand(k, value, state);
      const color = state === 'out' ? 'var(--red)' : band.color;
      el.innerHTML = text; el.style.color = color;
      if (st) { st.textContent = word || band.word; st.style.color = color; }
    };
    if (vitals.blood_pressure_systolic !== undefined) {
      const sys = vitals.blood_pressure_systolic, dia = vitals.blood_pressure_diastolic;
      const crit = sys > 180 || sys < 80 || dia > 120 || dia < 50;
      const diaOut = dia > 90 || dia < 60;
      const sysIn = sys >= 90 && sys <= 140;
      showExtra('bp', sys, `${sys}/${dia}<span class="vv-unit">mmHg</span>`, crit ? 'out' : diaOut ? 'watch' : 'ok',
        crit ? 'Out of range' : (sysIn && diaOut) ? (dia > 90 ? 'Above WNL ↑' : 'Below WNL ↓') : null);
    }
    if (vitals.temperature !== undefined) {
      const tc = vitals.temperature;
      showExtra('temp', tc, `${tc.toFixed(1)}<span class="vv-unit">°C</span>`, (tc >= 39 || tc < 35) ? 'out' : 'ok',
        tc >= 39 ? '↑ Fever' : tc < 35 ? '↓ Hypothermia' : null);
    }
    if (vitals.respiratory_rate !== undefined) {
      const rr = vitals.respiratory_rate;
      showExtra('rr', rr, `${rr}<span class="vv-unit">/min</span>`, (rr > 30 || rr < 8) ? 'out' : 'ok',
        rr > 30 ? '↑ Critical' : rr < 8 ? '↓ Critical' : null);
    }

    // Update Sleep
    const sleepEl = document.getElementById('sleep-value');
    const sleepFill = document.getElementById('sleep-fill');
    const sleepStatus = document.getElementById('sleep-status');
    if (sleepEl && vitals.sleep_quality !== undefined) {
      const raw = vitals.sleep_quality;           // classify the raw value, exactly as MIRROR does (69.6 is below 70)
      const sleep = Math.round(raw) === 70 && raw < 70 ? raw.toFixed(1) : Math.round(raw);
      sleepEl.textContent = sleep + '%';
      if (sleepFill) sleepFill.style.width = raw + '%';
      const sleepBand = setVitalBand('sleep', raw, raw < 50 ? 'out' : raw < 70 ? 'watch' : 'ok');
      const sleepColor = sleepBand.color;
      const sleepLabel = raw < 50 ? '↓ Poor' : sleepBand.word;
      sleepEl.style.color = sleepColor;
      if (sleepFill) sleepFill.style.background = sleepColor;
      if (sleepStatus) { sleepStatus.textContent = sleepLabel; sleepStatus.style.color = sleepColor; }
    }
    if (Date.now() - VITALS_SYNCED_AT < 8000 && CM.last) {
      syncVitalsToMap((CM.last.crew || []).find(c => c.crew_member_id === DASH_API.selectedCrewId));
    }
  } catch (e) {
    // Backend unreachable: say so plainly instead of animating made-up numbers
    showVitalsOffline();
  }
}

function showVitalsOffline() {
  ['hr', 'spo2', 'bp', 'temp', 'rr', 'il6', 'sleep'].forEach(k => {
    const v = document.getElementById(`${k}-value`), st = document.getElementById(`${k}-status`);
    if (v) { v.textContent = '—'; v.style.color = 'var(--muted)'; }
    if (st) { st.textContent = 'Offline'; st.style.color = 'var(--muted)'; }
    const bar = document.getElementById(`${k}-bar`); if (bar) bar.classList.add('empty');
  });
}

// Fallback local animation (original behavior)
function animateVitalsLocal() {
  const hrEl = document.getElementById('hr-value');
  const hrFill = document.getElementById('hr-fill');
  const sparkLine = document.getElementById('spark-line');
  
  const currentHR = parseInt(hrEl.textContent);
  const newHR = Math.max(62, Math.min(75, currentHR + Math.floor(Math.random() * 7) - 3));
  hrEl.textContent = newHR;
  if (hrFill) hrFill.style.width = newHR + '%';
  
  hrHistory.push(newHR);
  if (hrHistory.length > 6) hrHistory.shift();
  const points = hrHistory.map((v, i) => `${i * 10},${24 - (v - 60) * 1.5}`).join(' ');
  if (sparkLine) sparkLine.setAttribute('points', points);
}

// H4: Use API fetch instead of pure local animation
setInterval(fetchAndUpdateVitals, 5000);
// Also fetch immediately on load (and once more shortly after, in case the first request raced the page)
fetchAndUpdateVitals();
setTimeout(fetchAndUpdateVitals, 1500);

