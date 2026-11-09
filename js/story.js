// ============================================
// ONE-CLICK STORY DEMO: Predict → Prevent → Protect → ECHO (about 3 minutes). Calls are logged, never dialled.
// ============================================
const STORY = { on: false, run: 0, t0: 0, allowEcho: true, est: 175 };
const _sleep = ms => new Promise(res => setTimeout(res, ms));
function _storyAlive(run) { if (!STORY.on || run !== STORY.run) throw new Error('stopped'); }
async function _storyWait(run, cond, timeoutMs) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) { _storyAlive(run); try { if (cond()) return true; } catch (e) {} await _sleep(500); }
  _storyAlive(run); return false;
}
function storyCaption(ch, html) {
  const bar = document.getElementById('story-bar'); if (!bar) return;
  bar.hidden = false;
  document.querySelectorAll('#story-chapters span').forEach(sp => {
    const n = +sp.dataset.ch; sp.classList.toggle('now', n === ch); sp.classList.toggle('done', n < ch);
  });
  document.getElementById('story-text').innerHTML = html;
  announce(document.getElementById('story-text').textContent);
}
function _storyProgress() {
  if (!STORY.on) return;
  const k = Math.min(.98, (Date.now() - STORY.t0) / (STORY.est * 1000));
  const i = document.getElementById('story-prog'); if (i) i.style.width = (k * 100).toFixed(1) + '%';
}
setInterval(_storyProgress, 800);

async function _storyAckShelter() {
  try {
    const B = DASH_API.baseUrl;
    const open = await (await fetch(`${B}/api/alerts?unacknowledged_only=true&limit=200`)).json();
    await Promise.all(open.filter(a => /SHELTER|radiation storm/i.test(`${a.title} ${a.message}`))
      .map(a => { ACKED_HERE.set(a.id, 'script'); return fetch(`${B}/api/alerts/${encodeURIComponent(a.id)}/acknowledge`, { method: 'POST' }); }));
  } catch (e) {}
}
function _storyLocalReset() {
  FLOOR.q.length = 0; stopVoice();                 // nothing queued from before the reset keeps talking
  _setEmergency(null); closeCallPanel(); closeSimModal();
  CALLS.dismissed.clear(); CALLS.since = Date.now();
  Object.assign(PREDICT_STATE, { top: null, announcedKey: null, lastCrew: null, suppress: {}, prevented: null, preventedEntry: null });
  LIVE_ALERT.id = null; LIVE_ALERT.pauseUntil = 0; LIVE_ALERT.completed = null;
  CM.etaEnd = null; CM.prevStates = null;
  const echoChat = document.getElementById('echo-chat');
  if (ECHO.crew && echoChat) ECHO.chats[ECHO.crew] = echoChat.innerHTML;   // save before the switch marker is cleared
  ECHO.crew = null; ECHO.lastProactive = null; ECHO.pickedAt = 0;   // conversations are kept (the backend keeps them too)
}
// Two cuts, both ending on ECHO's check-in: 'short' = the 2-minute judging cut (default), 'full' = about 3 minutes
const STORY_CUTS = {
  short: { est: 125, warn: 5000, prevSim: 6000, averted: 12000, emergency: 6000, shelterSim: 7000, settle: 1000, solaris: 4500, echo: 7000 },
  full:  { est: 175, warn: 9000, prevSim: 9000, averted: 7000, emergency: 9000, shelterSim: 13000, settle: 2500, solaris: 6500, echo: 9000 },
};
async function runStory(cut) {
  if (STORY.on) { stopStory(); return; }
  cut = STORY_CUTS[cut] ? cut : (new URLSearchParams(location.search).get('demo') === 'full' ? 'full' : 'short');
  const T = STORY_CUTS[cut]; STORY.est = T.est; STORY.cut = cut;
  const _sb = document.getElementById('story-stop'); if (_sb) { _sb.style.visibility = ''; _sb.textContent = '■ Stop'; }
  const run = ++STORY.run;
  STORY.on = true; STORY.t0 = Date.now(); STORY.allowEcho = false;
  const btn = document.getElementById('run-demo-btn'); if (btn) { btn.classList.add('on'); btn.textContent = '■ Stop demo'; }
  if (typeof _unlockVoice === 'function') _unlockVoice();
  try {
    switchPage('mission');
    storyCaption(1, 'Resetting the ship to a clean start…');
    _storyLocalReset();
    if (selectedCrew !== 'chen') selectCrew('chen');
    const r = await fetch(`${DASH_API.baseUrl}/api/demo/story/start?cut=${cut}`, { method: 'POST' });
    if (!r.ok) throw new Error('backend ' + r.status);
    setTimeout(() => { if (STORY.run === run) echoRefresh(); }, 6000);   // ECHO's daily greeting, once the first readings are in
    window.scrollTo({ top: 0, behavior: 'smooth' });
    VOICE.nextSpeaker = 'aegis';
    typeResponse("Day 54 on the way to Mars. Everyone is steady. I'm watching the trends, not just the limits.");
    storyCaption(1, '<b>PREDICT.</b> Day 54. Commander Chen\'s radiation dose starts to creep up, and her sleep and inflammation follow. AEGIS watches the <b>trends</b>, not just the limits.');
    await _storyWait(run, () => CM.mode === 'cascade' || (PREDICT_STATE.lastCrew === 'crew-001'), 150000);
    storyCaption(1, '<b>Early warning.</b> AEGIS forecasts a health cascade (three systems out of range at once) <b>before it happens</b>. The countdown in the centre is live.');
    await _sleep(T.warn); _storyAlive(run);
    storyCaption(2, '<b>PREVENT.</b> The crew accepts the prevention plan: small, approved fixes now instead of an emergency later. It runs on board straight away.');
    if (PREDICT_STATE.lastCrew) await startPrevention();
    await _sleep(T.prevSim); _storyAlive(run); closeSimModal();
    storyCaption(2, '<b>PREVENT.</b> Watch the rings come back down…');
    const averted = await _storyWait(run, () => PREDICT_STATE.prevented && PREDICT_STATE.prevented.avertedAt, 70000);
    storyCaption(2, averted ? '<b>Cascade avoided.</b> Every ring is back to green. AEGIS acted before anything went wrong.'
                            : '<b>PREVENT.</b> The prevention plan is still bringing the readings down. The cascade never formed.');
    await _sleep(T.averted); _storyAlive(run);
    storyCaption(3, '<b>PROTECT.</b> A solar storm hits the whole crew. You can\'t prevent space weather, only protect against it.');
    await setDemoScenario('spe_event');
    await _storyWait(run, () => LIVE_ALERT.id && String(LIVE_ALERT.protocol || '').startsWith('SHELTER'), 60000);
    storyCaption(3, '<b>Radiation emergency.</b> SOLARIS orders the storm shelter and AEGIS phones the flight surgeon <i>(logged, not dialled, in this demo)</i>. No waiting for Earth.');
    await _sleep(T.emergency); _storyAlive(run);
    if (LIVE_ALERT.id) confirmProtocol();
    // SHELTER-1 covers the whole crew: confirm the shelter alerts raised for everyone else too
    const sweep = setInterval(_storyAckShelter, 4000);
    setTimeout(() => clearInterval(sweep), 40000);
    storyCaption(3, '<b>SHELTER-1 confirmed.</b> The crew moves into the shelter on board. The alert stays up until the readings are safe again.');
    await _sleep(T.shelterSim); _storyAlive(run); closeSimModal();
    await _storyWait(run, () => !EMG.level && !LIVE_ALERT.id, 60000);
    storyCaption(3, '<b>The storm passes.</b> Doses fall back toward the normal background…');
    const safe = await _storyWait(run, () => CM.last && (CM.last.crew || []).every(c => !(c.metrics.radiation_dose_24h || {}).out_of_range), 45000);
    await _sleep(T.settle); _storyAlive(run);
    closeCallPanel(); CALLS.since = Date.now();
    VOICE.nextSpeaker = 'solaris';
    typeResponse(safe ? "The storm has passed. Everyone's dose is back below the warning line, and the crew can leave the shelter."
                      : "The storm is easing. Doses are falling but not all are below the warning line yet, so the crew stays sheltered a little longer.");
    await _sleep(T.solaris); _storyAlive(run);
    storyCaption(4, (safe ? '<b>ECHO.</b> The storm is over and the doses are back below the warning line. '
                          : '<b>ECHO.</b> The worst of the storm is over. ') + 'AEGIS looked after the body. <b>ECHO checks on the person.</b>');
    STORY.allowEcho = true;
    focusEcho();
    await _sleep(900);
    ECHO.lastProactive = null;
    await echoRefresh('shelter');
    await _sleep(T.echo); _storyAlive(run);
    storyCaption(4, '<b>End of demo.</b> ECHO is waiting for an answer. Your turn: tell ECHO how you feel.');
    const sbtn = document.getElementById('story-stop'); if (sbtn) sbtn.textContent = '✕ Close';
    const i = document.getElementById('story-prog'); if (i) i.style.width = '100%';
    await fetch(`${DASH_API.baseUrl}/api/demo/story/stop`, { method: 'POST' }).catch(() => {});
    STORY.on = false;
  } catch (e) {
    if (e.message !== 'stopped') storyCaption(STORY.ch || 1, `The demo couldn't continue: ${_echoEsc(e.message)}. Is the backend running?`);
    STORY.on = false; STORY.allowEcho = true;
  }
  if (btn) { btn.classList.remove('on'); btn.textContent = '▶ Run demo · 2 min'; }
}
function stopStory() {
  const bar = document.getElementById('story-bar');
  if (!STORY.on) { if (bar) bar.hidden = true; return; }
  STORY.on = false; STORY.run++; STORY.allowEcho = true;
  closeSimModal(); closeCallPanel();
  const stopBtn = document.getElementById('story-stop'); if (stopBtn) stopBtn.style.visibility = 'hidden';
  storyCaption(1, 'Demo stopped. Resetting the ship to a clean start…');
  CM.resetting = { started: Date.now(), doneAt: 0, fresh: 0 };
  if (typeof renderAllClear === 'function') renderAllClear();
  // Stop the story AND reset, so nothing is left drifting after "stop"
  fetch(`${DASH_API.baseUrl}/api/demo/story/stop`, { method: 'POST' })
    .then(() => fetch(`${DASH_API.baseUrl}/api/demo/reset`, { method: 'POST' }))
    .catch(() => {})
    .finally(() => {
      _storyLocalReset();
      if (CM.resetting) CM.resetting.doneAt = Date.now();
      storyCaption(1, 'Demo stopped. Readings are settling back to normal.');
      setTimeout(() => { if (!STORY.on && bar) { bar.hidden = true; if (stopBtn) stopBtn.style.visibility = ''; } }, 2500);
    });
  const btn = document.getElementById('run-demo-btn'); if (btn) { btn.classList.remove('on'); btn.textContent = '▶ Run demo · 2 min'; }
}
