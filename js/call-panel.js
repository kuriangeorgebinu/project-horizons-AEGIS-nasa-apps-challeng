// LIVE CALL PANEL: AEGIS phoning the on-call flight surgeon (status followed on Twilio by the backend)
// ============================================
const CALLS = { id: null, rec: null, typed: 0, words: [], typeTimer: null, hideTimer: null, dismissed: new Set(),
                since: Date.now() - 60000 };
const _CALL_PROTOCOL = { 'the medical response': 'MEDIC-1', 'the storm-shelter plan': 'SHELTER-1', 'the recovery plan': 'RECOVER-3' };
function closeCallPanel() {
  const el = document.getElementById('call-panel');
  if (CALLS.id != null) CALLS.dismissed.add(CALLS.id);
  if (el) { el.classList.remove('show'); el.setAttribute('aria-hidden', 'true'); }
  clearInterval(CALLS.typeTimer); clearTimeout(CALLS.hideTimer);
  CALLS.id = null; CALLS.rec = null;
}
function _callStage(rec) {
  // -> [stepKey, failLabel | null, terminal?]
  if (rec.status === 'logged') return ['logged', null, true];
  if (rec.status === 'failed') return ['dial', 'Call failed', true];
  if (rec.status === 'skipped') return ['skipped', null, true];
  const cs = rec.call_status || 'queued';
  if (cs === 'queued' || cs === 'initiated') return ['dial', null, false];
  if (cs === 'ringing') return ['ring', null, false];
  if (cs === 'in-progress') return ['talk', null, false];
  if (cs === 'completed') return rec.answered_at || rec.duration_s ? ['done', null, true] : ['ring', 'No answer', true];
  if (cs === 'no-answer') return ['ring', 'No answer', true];
  if (cs === 'busy') return ['ring', 'Line busy', true];
  if (cs === 'canceled') return ['dial', 'Call canceled', true];
  return ['dial', 'Call failed', true];
}
function _startTyping(rec) {
  if (CALLS.words.length) return;
  CALLS.words = `${rec.intro || ''} ${rec.message || ''}`.trim().split(/\s+/);
  CALLS.typed = 0;
  const box = document.getElementById('cp-transcript');
  clearInterval(CALLS.typeTimer);
  CALLS.typeTimer = setInterval(() => {               // ~2.5 words/s, about the pace of the phone voice
    if (CALLS.typed >= CALLS.words.length) { clearInterval(CALLS.typeTimer); return; }
    CALLS.typed++;
    box.textContent = '“' + CALLS.words.slice(0, CALLS.typed).join(' ') + (CALLS.typed >= CALLS.words.length ? '”' : '');
    box.scrollTop = box.scrollHeight;
  }, 400);
}
function _renderCallPanel(rec, others, to) {
  const el = document.getElementById('call-panel');
  if (!el) return;
  const fresh = CALLS.id !== rec.id;
  if (fresh) {
    CALLS.id = rec.id; CALLS.words = []; CALLS.typed = 0;
    clearInterval(CALLS.typeTimer); clearTimeout(CALLS.hideTimer); CALLS.hideTimer = null;
    document.getElementById('cp-transcript').textContent = '';
  }
  CALLS.rec = rec;
  const [step, fail, terminal] = _callStage(rec);
  const name = _crewDisplayName(rec.crew_id);
  const plan = (rec.key || '').split(':').slice(2).join(':');
  const proto = rec.reason === 'requested' ? 'requested from mission control' : (_CALL_PROTOCOL[plan] || plan || rec.reason);
  const title = document.getElementById('cp-title');
  title.textContent = rec.status === 'logged' ? 'SIM · AEGIS would call the flight surgeon'
    : rec.status === 'skipped' ? (/turned off/i.test(rec.detail || '') ? 'Phone alarm off on this public demo · call logged' : 'Already called about this alert')
    : fail ? `Call to the flight surgeon: ${fail.toLowerCase()}`
    : step === 'done' ? 'Call complete: flight surgeon informed'
    : 'AEGIS is calling the flight surgeon';
  document.getElementById('cp-sub').textContent =
    `Alert for ${name} · ${proto}${to && rec.status === 'called' ? ` · to ${to}` : ''}${others ? ` · +${others} more call${others > 1 ? 's' : ''}` : ''}`;
  const steps = document.getElementById('cp-steps');
  steps.style.display = (rec.status === 'called' || rec.status === 'failed') ? '' : 'none';
  const order = ['dial', 'ring', 'talk', 'done'];
  const idx = order.indexOf(step);
  steps.querySelectorAll('li').forEach((li, i) => {
    li.className = i < idx || (step === 'done' && i === idx) ? 'done' : i === idx ? (fail ? 'fail' : 'now') : '';
    li.textContent = i === idx && fail ? fail : ['Dialling', 'Ringing', 'Answered', 'Call complete'][i];
  });
  el.classList.toggle('ringing', step === 'ring' && !fail);
  el.classList.toggle('fail', !!fail);
  el.classList.toggle('ok', step === 'done');
  // The spoken message appears as it is read out (straight away when the phone is not set up)
  if (step === 'talk' || step === 'logged') _startTyping(rec);
  if (step === 'done') { _startTyping(rec); CALLS.typed = CALLS.words.length;
    document.getElementById('cp-transcript').textContent = '“' + CALLS.words.join(' ') + '”'; }
  if (rec.status === 'skipped') {
    const wait = rec.next_call_in_s ? ` Next call allowed in ${Math.max(1, Math.ceil(rec.next_call_in_s / 60))} min.` : '';
    document.getElementById('cp-transcript').textContent = /turned off/i.test(rec.detail || '')
      ? 'On a real mission AEGIS would phone the on-call flight surgeon now. Calling is switched off on this public demo, so the call is only logged.'
      : `Not called again: ${rec.detail || 'already called about this alert'}.${wait}`;
  }
  const ago = Math.max(0, Math.round((Date.now() - _parseUtc(rec.at)) / 1000));
  const who = rec.source === 'AEGIS tool call' ? 'worded by AEGIS (cloud AI tool call, fact-checked)' : 'rule-based wording';
  const twilio = rec.status === 'called' ? `Twilio status: ${rec.call_status || 'queued'}${rec.duration_s ? `, ${rec.duration_s} s` : ''} (checked every 2 s). ` : '';
  document.getElementById('cp-foot').textContent =
    (rec.status === 'logged' ? 'SIMULATED: phone not set up, call logged only. ' : rec.status === 'failed' ? `${rec.detail || ''} ` : '') +
    twilio + `${ago < 60 ? ago + ' s' : Math.floor(ago / 60) + ' min'} ago · ${who}. Demo call over the phone network; ` +
    `on a Mars transit the first call would go to the medical officer on board.`;
  if (fresh) { el.classList.add('show'); el.setAttribute('aria-hidden', 'false'); }
  if (terminal && !CALLS.hideTimer) CALLS.hideTimer = setTimeout(closeCallPanel, rec.status === 'skipped' ? 8000 : 30000);
}
async function pollCalls() {
  try {
    const r = await fetch(`${DASH_API.baseUrl}/api/notify/status`);
    if (!r.ok) return;
    const s = await r.json();
    const recent = (s.history || []).filter(h => h.id != null && _parseUtc(h.at) >= CALLS.since && !CALLS.dismissed.has(h.id));
    if (!recent.length) { if (CALLS.id != null && !(s.history || []).some(h => h.id === CALLS.id)) closeCallPanel(); return; }
    const rec = recent[0];                                   // newest first
    const others = recent.filter(h => h.id !== rec.id && h.status === rec.status && _parseUtc(rec.at) - _parseUtc(h.at) < 60000).length;
    _renderCallPanel(rec, others, s.to);
    // Emergency strip: who AEGIS phoned
    const em = document.getElementById('mb-em-call');
    if (em) em.textContent = rec.status === 'called' ? `📞 Flight surgeon ${({ 'in-progress': 'on the line', completed: 'call complete', ringing: 'being called', 'no-answer': 'not answering', busy: 'line busy', failed: 'call failed', canceled: 'call canceled' })[rec.call_status] || 'being called'}`
      : rec.status === 'logged' ? '📞 Call logged (phone not set up)' : '';
  } catch (e) { /* backend offline */ }
}
setInterval(pollCalls, 2000);

