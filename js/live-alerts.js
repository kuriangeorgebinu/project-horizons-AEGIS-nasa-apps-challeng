// ============================================
// E2E WIRING: live alerts, roster status, demo controls (Claude, Sep 28)
// ============================================
// Alerts acknowledged from THIS tab: id -> 'here' (a person clicked) or 'script' (the 2-minute demo confirmed it)
const ACKED_HERE = new Map();
function _ackSource() { return (typeof STORY !== 'undefined' && STORY.on) ? 'script' : 'here'; }
const LIVE_ALERT = { id: null, protocol: 'RECOVER-3', crewName: null, crewKey: 'chen', pauseUntil: 0, other: null, spoken: new Set() };

// Plain-language wording for the panel and the voice (protocol codes stay in tooltips and the on-board sim)
const PLAIN_PROTOCOL = { 'RECOVER-3': 'recovery plan', 'SHELTER-1': 'storm-shelter plan', 'MEDIC-1': 'medical response', 'PREVENT-1': 'prevention plan' };
const PLAIN_BUTTON = { 'RECOVER-3': 'Start recovery plan', 'SHELTER-1': 'Send everyone to the storm shelter',
                       'MEDIC-1': 'Start medical response', 'PREVENT-1': 'Start prevention plan' };
const PLAIN_DOMAIN = { PULSE: 'heart', GENE: 'inflammation', MIRROR: 'sleep', SOLARIS: 'radiation' };
const PLAIN_TREND = { heart_rate: 'heart rate is climbing', spo2: 'oxygen level is dropping', il6: 'inflammation is rising',
                      sleep_quality: 'sleep is getting worse', radiation_dose_24h: 'radiation dose is going up' };
function plainList(a) {
  a = (a || []).filter(Boolean);
  return a.length <= 1 ? (a[0] || '') : a.slice(0, -1).join(', ') + ' and ' + a[a.length - 1];
}
function plainDomains(agents) { return plainList([...new Set((agents || []).map(a => PLAIN_DOMAIN[a] || a.toLowerCase()))]); }
const CREW_KEY_BY_ID = Object.fromEntries(Object.entries(DASH_API.crewIdMap).map(([k, v]) => [v, k]));
const AGENT_BADGE_STYLE = {
  SOLARIS: 'background:rgba(200,176,112,.15);color:var(--solaris)',
  GENE:    'background:rgba(160,152,200,.15);color:var(--gene)',
  PULSE:   'background:rgba(200,136,136,.15);color:var(--pulse)',
  MIRROR:  'background:rgba(122,173,154,.15);color:var(--mirror)',
  AEGIS:   'background:rgba(90,159,215,.15);color:var(--aegis)'
};

function _crewDisplayName(crewId) {
  const key = CREW_KEY_BY_ID[crewId];
  return (key && CREW_DATA[key]) ? CREW_DATA[key].firstName : (crewId || 'crew');
}

// DeepSeek reasoning layer: the backend appends "🧠 DeepSeek (AGENT): ..." to alert messages
const AI_MARKER = '🧠 DeepSeek';
function _splitAi(text) {
  const i = (text || '').indexOf(AI_MARKER);
  return i < 0 ? [String(text || '').trim(), ''] : [text.slice(0, i).trim(), text.slice(i).trim()];
}
function _renderAiNote(aiText) {
  let el = document.getElementById('alert-ai');
  if (!el) {
    const desc = document.getElementById('alert-desc');
    if (!desc) return;
    el = document.createElement('div');
    el.id = 'alert-ai';
    el.className = 'alert-ai';
    el.setAttribute('aria-live', 'polite');
    desc.insertAdjacentElement('afterend', el);
  }
  if (!aiText) { el.style.display = 'none'; el.textContent = ''; return; }
  const m = aiText.match(/^🧠 DeepSeek\s*\(([^)]+)\):\s*([\s\S]*)$/);
  el.innerHTML = '';
  const tag = document.createElement('span');
  tag.className = 'alert-ai-tag';
  tag.textContent = m ? `🧠 Cloud AI · ${m[1]}` : '🧠 Cloud AI';
  const body = document.createElement('span');
  body.textContent = m ? m[2] : aiText.replace(AI_MARKER, '').trim();
  el.append(tag, body);
  el.style.display = '';
}

function _alertRank(a) {
  if (a.severity === 'critical' || /SHELTER/i.test(a.title)) return 2;   // PROTECT tier
  if (/CASCADE/i.test(a.title)) return 1;                                 // cascade (PREVENT/RECOVER)
  return 0;
}

function _alertKind(a) {
  const isProtect = _alertRank(a) === 2;
  const isShelter = isProtect && (/SHELTER|RADIATION/i.test(a.title || '') || String(a.source || '').toUpperCase() === 'SOLARIS');
  const isMedic = isProtect && !isShelter;
  const m = (a.message || '').match(/(RECOVER-\d|SHELTER-\d|CALM-\d|SLEEP-\d|MEDIC-\d)/);
  return { isProtect, isShelter, isMedic, protocol: m ? m[1] : (isShelter ? 'SHELTER-1' : isMedic ? 'MEDIC-1' : 'RECOVER-3') };
}
function _bannerFor(a) {
  const k = _alertKind(a);
  if (EMG.alertId === a.id && !EMG.acked) {
    // Same alert: only the "who" can change (the other crew's shelter alerts arrive a few seconds later)
    const who = k.isShelter && LIVE_ALERT.shelterAll ? 'all crew' : _crewDisplayName(a.crew_member_id);
    if (EMG.baseLabel && !EMG.baseLabel.endsWith(' · ' + who)) {
      EMG.label = EMG.baseLabel = EMG.baseLabel.replace(/ · [^·]+$/, ' · ' + who);
      _tickEmergency();
    }
    return;
  }
  if (EMG.alertId === a.id || (EMG.acked && EMG.protocol === k.protocol)) return;
  _setEmergency({ level: k.isProtect ? 'protect' : 'cascade', protocol: k.protocol,
                  name: k.isShelter && LIVE_ALERT.shelterAll ? 'all crew' : _crewDisplayName(a.crew_member_id), since: a.timestamp, alertId: a.id, crewId: a.crew_member_id });
}
// An alert for someone other than the selected crew member: one line in the card with a button to switch to them
function _renderOtherAlert() {
  const o = LIVE_ALERT.other;
  let el = document.getElementById('alert-other');
  if (!o) { if (el) el.remove(); return; }
  if (!el) {
    el = document.createElement('div'); el.id = 'alert-other'; el.className = 'alert-other';
    document.getElementById('alert-desc')?.insertAdjacentElement('afterend', el);
  }
  const k = _alertKind(o), who = _crewDisplayName(o.crew_member_id), key = CREW_KEY_BY_ID[o.crew_member_id];
  const what = k.isShelter ? 'radiation above the shelter level' : k.isMedic ? 'medical emergency' : 'health cascade';
  el.innerHTML = `⚠ ${_insEsc(who)} has an open alert (${what}). <button type="button" onclick="selectCrew('${key}')">Show ${_insEsc(who)}</button>`;
}
// After a person confirms a protocol the alert is closed, but the card says what is going on instead of jumping to
// "Steady": "<protocol> confirmed · in effect" while the readings recover, then "<protocol> complete" for 45 s.
function _renderProtocolState() {
  const sel = DASH_API.selectedCrewId, done = LIVE_ALERT.completed;
  const forSel = c => c && (c.protocol === 'SHELTER-1' || c.crewId === sel);
  let mode = null, info = null;
  if (EMG.level && EMG.acked && forSel({ protocol: EMG.protocol, crewId: EMG.crewId })) {
    mode = 'confirmed'; info = { protocol: EMG.protocol, at: EMG.ackedAt, name: EMG.label.split(' · ').pop(), source: EMG.ackSource || 'here' };
  } else if (done && Date.now() < done.until && forSel(done)) { mode = 'complete'; info = done; }
  if (!mode) return false;
  const panel = document.getElementById('alert-panel'), icon = document.getElementById('alert-icon'), title = document.getElementById('alert-title'),
        desc = document.getElementById('alert-desc'), agentsEl = document.getElementById('alert-agents'), btn = document.getElementById('confirm-btn');
  if (!panel || !btn) return false;
  const hhmm = t => new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  panel.classList.remove('predict', 'recovered');
  btn.style.display = 'none'; if (agentsEl) agentsEl.innerHTML = '';
  if (mode === 'confirmed') {
    panel.style.borderColor = 'var(--cm-amber)'; icon.textContent = '⛨'; title.style.color = 'var(--cm-amber)';
    title.textContent = `${info.protocol} confirmed · in effect · ${info.name}`;
    desc.textContent = (info.source === 'script' ? `Confirmed by the demo script at ${hhmm(info.at)} (on a mission a crew member would press Confirm). `
                                                : `Confirmed by the crew at ${hhmm(info.at)}. `) +
      'The protocol stays in effect until the readings that raised it are back in a safe range.';
  } else {
    panel.style.borderColor = 'var(--green)'; icon.textContent = '✓'; title.style.color = 'var(--green)';
    if (info.elsewhere) {
      title.textContent = `${info.protocol} confirmed on another screen · ${info.name}`;
      desc.textContent = `Someone confirmed this alert from another open dashboard at about ${hhmm(info.at)} (the demo is shared). It did not close on its own.`;
    } else {
      const settling = Object.values(CM.prevStates || {}).some(v => v !== 'ok');   // say only what the rings show now
      title.textContent = `${info.protocol} complete · ${settling ? 'back below the alert level, still settling' : 'readings recovered'} · ${info.name}`;
      desc.textContent = info.source === 'script'
        ? `Confirmed by the demo script at ${hhmm(info.at)}; the readings that raised it were back in range at ${hhmm(info.doneAt)}. On a mission a crew member presses Confirm; nothing closes on its own.`
        : `Confirmed by the crew at ${hhmm(info.at)}; the readings that raised it were back in range at ${hhmm(info.doneAt)}. Nothing closed on its own: a person confirmed, and the readings confirmed it worked.`;
    }
  }
  return true;
}
function renderLiveAlert(a) {
  const panel = document.getElementById('alert-panel');
  const icon = document.getElementById('alert-icon');
  const title = document.getElementById('alert-title');
  const desc = document.getElementById('alert-desc');
  const agentsEl = document.getElementById('alert-agents');
  const btn = document.getElementById('confirm-btn');
  if (!panel || !btn) return;

  if (!a) {
    // No live alert: show the PREDICT forecast if AEGIS sees a cascade coming, otherwise all clear
    LIVE_ALERT.id = null;
    // Nothing for the selected person. The banner still shows another crew member's emergency (it names them).
    if (!EMG.acked) { if (LIVE_ALERT.other) _bannerFor(LIVE_ALERT.other); else _setEmergency(null); }
    if (_renderProtocolState()) return;      // a confirmed protocol in effect, or just completed
    const pvNow = PREDICT_STATE.prevented && PREDICT_STATE.prevented.crew === DASH_API.selectedCrewId ? PREDICT_STATE.prevented : null;
    if (pvNow && !(pvNow.avertedAt && PREDICT_STATE.preventedEntry && !sysAllOk(PREDICT_STATE.preventedEntry))) renderPreventedPanel(PREDICT_STATE.preventedEntry);
    else renderPredictPanel(PREDICT_STATE.top);
    return;
  }

  panel.classList.remove('predict');
  PREDICT_STATE.lastCrew = null;
  if (typeof _showPreventBtn === 'function') _showPreventBtn(false);
  // A live alert for the crew member under PREVENT-1 supersedes it (the countermeasures weren't enough)
  if (PREDICT_STATE.prevented && PREDICT_STATE.prevented.crew === a.crew_member_id) {
    PREDICT_STATE.prevented = null; PREDICT_STATE.preventedEntry = null;
  }
  const isNew = a.id !== LIVE_ALERT.id;
  // PROTECT tier: radiation (SOLARIS / SHELTER) -> SHELTER-1; any other critical reading -> MEDIC-1
  const { isProtect, isShelter, isMedic, protocol } = _alertKind(a);
  LIVE_ALERT.id = a.id;
  LIVE_ALERT.protocol = protocol;
  LIVE_ALERT.crewName = _crewDisplayName(a.crew_member_id);
  LIVE_ALERT.crewKey = CREW_KEY_BY_ID[a.crew_member_id] || 'chen';
  _bannerFor(a);

  panel.style.borderColor = 'var(--red)';
  icon.textContent = isShelter ? '☢' : isMedic ? '⚕' : '⚡';
  title.style.color = '';
  const cleanTitle = (a.title || 'ALERT').replace(/^🔺\s*/, '').replace(/^Immediate Action:\s*/i, '');
  const agentsMatch = (a.message || '').match(/Agents reporting:\s*([^.]*)/);
  const agents = agentsMatch ? agentsMatch[1].split(',').map(s => s.trim()).filter(Boolean)
                             : [String(a.source || 'AEGIS').toUpperCase()];
  // Rule-based text vs. DeepSeek assessment (appended by the backend a few seconds after the alert)
  const [ruleMsg, aiMsg] = _splitAi(a.message || '');
  const name = LIVE_ALERT.crewName;
  // Plain-language headline, explanation and spoken line for each kind of alert
  let headline, plain, spoken;
  if (isShelter) {
    const casc = LIVE_ALERT.alsoCascade;
    if (casc) {
      const cAgents = ((casc.message || '').match(/Agents reporting:\s*([^.]*)/) || [])[1];
      (cAgents ? cAgents.split(',').map(x => x.trim()).filter(Boolean) : []).forEach(n => { if (!agents.includes(n)) agents.push(n); });
      headline = `Radiation storm + health cascade · ${name}`;
      plain = `The solar storm has ${LIVE_ALERT.shelterAll ? 'everyone' : name} over the shelter level, and ${name}'s ${plainDomains(agents)} are all out of range at the same time. ` +
              `First the storm shelter (SHELTER-1); once the dose comes down, the recovery plan for the cascade (RECOVER-3).`;
      spoken = `${name} has a radiation storm and a health cascade at once. Storm shelter first, then the recovery plan.`;
    } else if (LIVE_ALERT.shelterAll) {
      headline = `Radiation storm: take shelter now · ${name}`;
      plain = 'A solar storm is pushing radiation up fast. The storm shelter is the best-protected spot on the ship, so the crew should head there now.';
      spoken = 'Radiation storm. Everyone needs to get to the storm shelter now.';
    } else {       // only this crew member's dose is over the line (e.g. the cascade demo)
      headline = `Radiation above the shelter level · ${name}`;
      plain = `${name}'s radiation dose rate has passed the shelter level. ${name} should move to the storm shelter, the best-protected spot on the ship, until it comes down.`;
      spoken = `${name}'s radiation is above the shelter level. Please move to the storm shelter now.`;
    }
  } else if (isMedic) {
    const pct = (ruleMsg.match(/(\d+(?:\.\d+)?)\s*%/) || [])[1];
    const bpm = (ruleMsg.match(/(\d+)\s*BPM/i) || [])[1];
    plain = /oxygen|spo2|hypox/i.test(cleanTitle + ruleMsg) && pct
      ? `${name}'s oxygen level was ${Math.round(pct) === 90 && Number(pct) < 90 ? Number(pct).toFixed(1) : Math.round(pct)}%, below the safe line of 90%. They need extra oxygen and a check-up right away.`
      : bpm ? `${name}'s heart rate was ${bpm} beats per minute when this was raised, which is in the danger zone. They should stop what they're doing and get checked now.`
            : `${name} has a reading in the danger zone. They need to be checked right away.`;
    headline = `${cleanTitle.charAt(0) + cleanTitle.slice(1).toLowerCase()} · ${name}`;
    spoken = plain + ' I recommend starting a medical response.';
  } else {
    const nOut = _crewOutCount(a.crew_member_id) ?? agents.length;     // current rings, not the count when it was raised
    headline = nOut >= 2 ? `Health cascade: ${nOut} systems out of range now · ${name}`
             : nOut === 1 ? `Health cascade: 1 system still out of range · ${name}` : `Health cascade · ${name}`;
    plain = `${name}'s ${plainDomains(agents)} are all out of range at the same time, and they tend to make each other worse. Tackling them together works better than one at a time.`;
    spoken = `${name} needs attention: ${plainDomains(agents)} are all out of range at once. I recommend starting the recovery plan.`;
  }
  title.textContent = headline;
  title.title = cleanTitle + (LIVE_ALERT.protocol ? ` · Protocol ${LIVE_ALERT.protocol}` : ''); desc.title = '';
  const raisedAt = _parseUtc(a.timestamp);
  const raisedTxt = isNaN(raisedAt) ? '' : new Date(raisedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  desc.textContent = plain + (raisedTxt ? ` (Raised ${raisedTxt}; numbers are the readings at that moment, Live Vitals shows them now.)` : '');
  // Protocol alerts never close themselves: a person confirms them. Once the readings are back in range, say so,
  // so an open alert reads as "waiting for sign-off", not as a stale one.
  const nowEntry = CM.last && (CM.last.crew || []).find(c => c.crew_member_id === a.crew_member_id);
  // Only once the alert is 45 s old: right after it is raised, the last forecast can still show the readings from
  // just before it (which would flash "recovered" at the start of a storm)
  const alertAgeS = isNaN(raisedAt) ? 0 : (Date.now() - raisedAt) / 1000;
  const recovered = !!(alertAgeS > 45 && nowEntry && _crewRecovered(a.crew_member_id, LIVE_ALERT.protocol));
  panel.classList.toggle('recovered', recovered);
  if (recovered) {
    panel.style.borderColor = 'var(--cm-amber)';
    title.textContent = `Readings recovered · awaiting confirmation · ${headline}`;
    title.style.color = 'var(--cm-amber)';
    desc.textContent = `${name}'s readings are back within range. The ${LIVE_ALERT.protocol} alert stays open until a person confirms it: ` +
      `a recovered reading does not close an emergency order on its own. Raised ${raisedTxt || 'earlier'}.`;
  }
  LIVE_ALERT.recovered = recovered;
  if (!btn.disabled) btn.textContent = recovered ? `Confirm ${LIVE_ALERT.protocol} · readings have recovered`
                                                 : (PLAIN_BUTTON[LIVE_ALERT.protocol] || `Confirm ${LIVE_ALERT.protocol}`);
  _renderAiNote(aiMsg);
  agentsEl.innerHTML = agents.map(n => `<span class="alert-badge" style="${AGENT_BADGE_STYLE[n] || AGENT_BADGE_STYLE.AEGIS}">${n}</span>`).join('');

  btn.style.display = '';
  const speakKey = a.id + (LIVE_ALERT.alsoCascade ? '+' + LIVE_ALERT.alsoCascade.id : '');
  if (isNew || (LIVE_ALERT.alsoCascade && !LIVE_ALERT.spoken.has(speakKey))) {
    btn.disabled = false;
    btn.style.background = '';
    btn.textContent = PLAIN_BUTTON[LIVE_ALERT.protocol] || `Confirm ${LIVE_ALERT.protocol}`;
    btn.title = `Protocol ${LIVE_ALERT.protocol}`;
    if (typeof playTone === 'function') { playTone(880, 0.12); setTimeout(() => playTone(660, 0.12), 150); }
    // The agent that owns the hazard speaks: SOLARIS for radiation, PULSE for a medical emergency, AEGIS for a cascade
    if (!LIVE_ALERT.spoken.has(speakKey)) {
      LIVE_ALERT.spoken.add(speakKey);
      VOICE.nextSpeaker = isShelter ? 'solaris' : isMedic ? 'pulse' : 'aegis';
      if (typeof typeResponse === 'function') typeResponse(withIntro(VOICE.nextSpeaker, spoken));
    }
  }
}

async function refreshLiveAlerts() {
  if (Date.now() < LIVE_ALERT.pauseUntil) return;
  try {
    const r = await fetch(`${DASH_API.baseUrl}/api/alerts?unacknowledged_only=true&limit=50`);
    if (!r.ok) return;                       // keep current display on error
    const alerts = await r.json();
    const ranked = alerts.filter(a => _alertRank(a) > 0)
      .sort((x, y) => _alertRank(y) - _alertRank(x) || new Date(y.timestamp) - new Date(x.timestamp));
    // The card is about the selected crew member only. In a real solar storm every crew member gets their own
    // shelter alert, so each person's card still shows it; a demo that raises one person's dose stays with them.
    const sel = DASH_API.selectedCrewId;
    LIVE_ALERT.shelterAll = new Set(ranked.filter(a => _alertKind(a).isShelter).map(a => a.crew_member_id)).size > 1;
    const mine = ranked.find(a => a.crew_member_id === sel) || null;
    // Solar storm + this person's own cascade (e.g. Shift+S then Shift+D): the card names both
    LIVE_ALERT.alsoCascade = mine && _alertKind(mine).isShelter
      ? ranked.find(a => a.crew_member_id === sel && /CASCADE/i.test(a.title || '')) || null : null;
    // During a ship-wide storm everyone's shelter alert is the same news: only point out what is different
    LIVE_ALERT.other = ranked.find(a => a.crew_member_id !== sel && !(LIVE_ALERT.shelterAll && _alertKind(a).isShelter)) || null;
    const prevId = LIVE_ALERT.id, prevProto = LIVE_ALERT.protocol, prevCrew = sel, prevName = LIVE_ALERT.crewName;
    if (prevId && !mine && !EMG.acked && !ACKED_HERE.has(prevId) && !(LIVE_ALERT.completed && LIVE_ALERT.completed.alertId === prevId)) {
      // The shared demo: someone may have confirmed it on another screen. Ask the backend before saying so.
      fetch(`${DASH_API.baseUrl}/api/alerts/${encodeURIComponent(prevId)}/state`).then(r => r.ok ? r.json() : null).then(st => {
        if (st && st.acknowledged) {
          LIVE_ALERT.completed = { alertId: prevId, protocol: prevProto, crewId: prevCrew, at: Date.now(), doneAt: Date.now(),
                                   until: Date.now() + 45000, name: prevProto === 'SHELTER-1' && LIVE_ALERT.shelterAll ? 'all crew' : prevName, elsewhere: true };
          if (!LIVE_ALERT.id) _renderProtocolState();
        }
      }).catch(() => {});
    }
    renderLiveAlert(mine);
    _renderOtherAlert();
    _renderCallLine(mine);
  } catch (e) { /* backend offline: keep static demo content */ }
}

// Phone alarm: show whether AEGIS called the on-call flight surgeon about the alert on screen
async function _renderCallLine(a) {
  let el = document.getElementById('alert-call');
  if (!a) { if (el) el.remove(); return; }
  try {
    const s = await (await fetch(`${DASH_API.baseUrl}/api/notify/status`)).json();
    const since = new Date(a.timestamp + (/[zZ]|[+-]\d\d:?\d\d$/.test(a.timestamp) ? '' : 'Z')).getTime() - 5000;
    const rec = (s.history || []).find(h => h.key.startsWith(a.crew_member_id + ':') &&
                                            new Date(h.at + 'Z').getTime() >= since);
    if (!rec) { if (el) el.remove(); return; }
    if (!el) {
      el = document.createElement('div');
      el.id = 'alert-call';
      el.className = 'alert-call';
      document.getElementById('alert-agents').insertAdjacentElement('afterend', el);
    }
    const t = new Date(rec.at + 'Z').toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const who = rec.source === 'AEGIS tool call' ? 'AEGIS' : 'AEGIS (rules)';
    el.className = 'alert-call ' + rec.status;
    el.textContent = rec.status === 'called' ? `📞 ${who} phoned the flight surgeon at ${t}`
      : rec.status === 'logged' ? `📞 ${who} would phone the flight surgeon now (phone not set up yet)`
      : rec.status === 'skipped' ? (/turned off/i.test(rec.detail || '') ? `📞 ${who} would phone the flight surgeon now (phone alarm is off on this public demo)` : `📞 Flight surgeon already called about this`)
      : `📞 Call to the flight surgeon failed: ${rec.detail}`;
    el.title = `What the call says: "${rec.message}"`;
  } catch (e) { /* notify endpoint unavailable */ }
}

// Same words as the Cascade Map and Crew at a glance, so one person never shows two different labels
const STATUS_BADGE = {
  nominal:  { text: 'Steady',   cls: 'nominal'  },
  caution:  { text: 'Watch',    cls: 'elevated' },
  warning:  { text: 'Warning',  cls: 'alert'    },
  critical: { text: 'Critical', cls: 'alert'    }
};

async function refreshRosterStatus() {
  try {
    const r = await fetch(`${DASH_API.baseUrl}/api/crew`);
    if (!r.ok) return;
    const crew = await r.json();
    crew.forEach(m => {
      const key = CREW_KEY_BY_ID[m.id];
      if (!key) return;
      const card = document.querySelector(`.crew-card[onclick*="'${key}'"]`);
      const badge = card && card.querySelector('.status-badge');
      const s = STATUS_BADGE[m.status] || STATUS_BADGE.nominal;
      if (badge) { badge.textContent = s.text; badge.className = `status-badge ${s.cls}`; }
      if (CREW_DATA[key]) CREW_DATA[key].status = s.cls;
      // Roster cards show the LIVE simulated readings (they used to be fixed sample numbers)
      const v = m.vitals, cells = card ? card.querySelectorAll('.crew-vital-value') : [];
      if (v && cells.length >= 4) {
        const set = (el, text, color) => { el.textContent = text; el.style.color = color; };
        set(cells[0], `${v.heart_rate} BPM`, v.heart_rate > 100 || v.heart_rate < 50 ? 'var(--red)' : v.heart_rate > 90 ? 'var(--yellow)' : 'var(--green)');
        set(cells[1], `${Number(v.spo2).toFixed(0)}%`, v.spo2 < 90 ? 'var(--red)' : v.spo2 < 95 ? 'var(--yellow)' : 'var(--green)');
        const st = v.stress_index ?? 0;
        set(cells[2], `${Math.round(st)}%`, st > 85 ? 'var(--red)' : st > 70 ? 'var(--yellow)' : 'var(--green)');
        const sl = v.sleep_quality ?? 0;
        set(cells[3], `${Math.round(sl) === 70 && sl < 70 ? sl.toFixed(1) : Math.round(sl)}%`, sl < 50 ? 'var(--red)' : sl < 70 ? 'var(--orange)' : 'var(--green)');
      }
    });
  } catch (e) { /* offline: keep static badges */ }
}

// Demo controls: Shift+B slow build-up (PREDICT -> PREVENT), Shift+D the cascade demo, Shift+S a solar particle
// event (SHELTER-1), Shift+M a medical emergency (MEDIC-1), Shift+N returns to nominal
// The backend's reason when it refuses (public demo rate limit 429, switched-off 403), else a network message
async function _apiRefusal(r, what) {
  if (!r) return `Could not reach the AEGIS backend to ${what}.`;
  let d = ''; try { d = (await r.json()).detail || ''; } catch (x) {}
  if (r.status === 429) return d || 'The public demo is limiting how often this can run. Please wait a minute and try again.';
  if (r.status === 403) return d || 'That control is switched off on the public demo.';
  return `The backend could not ${what} (${d || 'HTTP ' + r.status}).`;
}
async function setDemoScenario(name) {
  let r = null;
  try {
    // Single-person demos happen to the crew member selected on the dashboard; the solar storm hits everyone
    const perPerson = ['cascade_demo', 'buildup_demo', 'medical_emergency'].includes(name);
    const crewId = DASH_API.selectedCrewId || 'crew-001';
    const who = _crewDisplayName(crewId);
    const qs = perPerson ? `?crew_id=${encodeURIComponent(crewId)}` : '';
    r = await fetch(`${DASH_API.baseUrl}/api/simulation/scenario/${name}${qs}`, { method: 'POST' });
    if (!r.ok) throw new Error(r.status);
    const label = { cascade_demo: `Demo: several of ${who}'s readings start drifting toward their limits together. Watch AEGIS forecast the cascade and offer a prevention plan before it forms.`,
                    buildup_demo: `Demo: ${who}'s readings will slowly drift over the next few minutes. Let's see if I can spot it before it becomes a problem.`,
                    medical_emergency: `Demo: ${who}'s oxygen level is about to drop. I'll flag it the moment it gets dangerous.`,
                    spe_event: 'Demo: a solar storm is on its way.',
                    nominal: "Back to normal. Everyone's readings are settling down." }[name] || `Scenario ${name} set.`;
    if (typeof typeResponse === 'function') typeResponse(label);
  } catch (e) {
    const msg = await _apiRefusal(r && !r.ok ? r : null, 'change the scenario');
    if (typeof typeResponse === 'function') typeResponse(msg);
  }
}
document.addEventListener('keydown', (e) => {
  if (!e.shiftKey || e.ctrlKey || e.altKey || e.metaKey) return;
  const tag = (e.target && e.target.tagName) || '';
  if (e.repeat || tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (e.target && e.target.isContentEditable)) return;
  // Physical key (e.code), so the shortcuts work with Caps Lock on and on keyboard layouts/automation that report a lowercase key
  const k = /^Key[A-Z]$/.test(e.code || '') ? e.code.slice(3) : String(e.key || '').toUpperCase();
  const act = { D: () => setDemoScenario('cascade_demo'), S: () => setDemoScenario('spe_event'), B: () => setDemoScenario('buildup_demo'),
                M: () => setDemoScenario('medical_emergency'), N: () => setDemoScenario('nominal'), R: () => resetDemo() }[k];
  if (act) { e.preventDefault(); act(); }
});

// Shift+R: clean, repeatable demo start (clears alerts, cards, history and agent memory; fixed crew baselines)
async function resetDemo() {
  let r = null;
  try {
    r = await fetch(`${DASH_API.baseUrl}/api/demo/reset`, { method: 'POST' });
    if (!r.ok) throw new Error(r.status);
    _setEmergency(null); closeCallPanel(); CALLS.dismissed.clear(); CALLS.since = Date.now();
    // The page reloads: keep ECHO's conversations and show "settling" (not Steady) until two fresh checks agree
    echoPersist();
    try { sessionStorage.setItem('aegis.echo.resetNote', '1'); sessionStorage.setItem('aegis.resettingAt', String(Date.now())); } catch (e) {}
    CM.resetting = { started: Date.now(), doneAt: Date.now(), fresh: 0 };
    if (typeof typeResponse === 'function') typeResponse('Demo reset. Readings are settling back to normal; the map will say Steady once two fresh checks agree.');
    setTimeout(() => location.reload(), 1800);
  } catch (e) {
    const msg = await _apiRefusal(r && !r.ok ? r : null, 'reset the demo');
    if (typeof typeResponse === 'function') typeResponse(msg);
  }
}
// Also allow ?demo=cascade in the URL
if (new URLSearchParams(location.search).get('demo') === 'cascade') setTimeout(() => setDemoScenario('cascade_demo'), 1500);

setInterval(refreshLiveAlerts, 5000);
setInterval(refreshRosterStatus, 5000);
refreshRosterStatus();
setTimeout(() => { refreshLiveAlerts(); refreshRosterStatus(); }, 300);

// Init
bootSequence();
setTimeout(() => {
  typeResponse("Good morning, I'm AEGIS. Pick a crew member and I'll tell you how they're doing, or just ask me anything.");
}, 1200);

// Expanded HR Graph Modal
function openHRGraph() { openTrend('heart_rate'); }

function closeHRGraph() {
  TREND_OPEN = null;
  document.getElementById('hrModal').classList.remove('open');
  announce('Graph closed');
}

