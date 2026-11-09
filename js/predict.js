// ============================================
// PREDICT: trend chips, click-to-expand trend + forecast chart, predicted-cascade line in the alert panel
// Backend: GET /api/predict (all crew) and GET /api/crew/{id}/trends (series for the chart)
// ============================================
const TREND_META = {
  heart_rate:         { icon: '❤️', label: 'Heart rate',      unit: 'bpm',   agent: 'PULSE',   dp: 0 },
  spo2:               { icon: '🫁', label: 'SpO₂',            unit: '%',     agent: 'PULSE',   dp: 1 },
  il6:                { icon: '🧬', label: 'IL-6',            unit: 'pg/mL', agent: 'GENE',    dp: 1 },
  sleep_quality:      { icon: '🌙', label: 'Sleep',           unit: '/100',  agent: 'MIRROR',  dp: 0 },
  radiation_dose_24h: { icon: '☢',  label: 'Radiation (24 h)', unit: 'mSv',  agent: 'SOLARIS', dp: 2 }
};
let TREND_OPEN = null;
const PREDICT_STATE = { top: null, announcedKey: null, lastCrew: null, suppress: {}, prevented: null, preventedEntry: null };

function fmtEta(min) {
  if (min === null || min === undefined) return '';
  if (min < 1) return '<1 min';
  if (min < 60) return `~${Math.round(min)} min`;
  return `~${(min / 60).toFixed(1)} h`;
}

function renderTrendChips(fc) {
  Object.keys(TREND_META).forEach(m => {
    const el = document.getElementById('trend-' + m);
    if (!el) return;
    const t = fc && fc.metrics ? fc.metrics[m] : null;
    if (!t || (t.direction === 'steady' && t.eta_min === null)) { el.hidden = true; el.textContent = ''; return; }
    const arrow = t.direction === 'rising' ? '↗' : t.direction === 'falling' ? '↘' : '→';
    const meta = TREND_META[m];
    el.className = 'trend-chip' + (m === 'radiation_dose_24h' ? ' inline' : '') +
                   (t.eta_min !== null ? ' eta' : t.out_of_range ? ' out' : '');
    el.textContent = t.eta_min !== null ? `${arrow} ⏱ ${fmtEta(t.eta_min)}` : arrow;
    el.title = `${meta.agent} · ${meta.label} ${t.direction}` +
      (t.slope_per_min !== null ? ` (${t.slope_per_min > 0 ? '+' : ''}${Number(t.slope_per_min).toFixed(meta.dp + 1)} ${meta.unit}/min)` : '') +
      (t.eta_min !== null ? ` · forecast to cross ${t.threshold} ${meta.unit} in ${fmtEta(t.eta_min)}` : '');
    el.hidden = false;
  });
}

function renderAllClear() {
  const panel = document.getElementById('alert-panel');
  const icon = document.getElementById('alert-icon');
  const title = document.getElementById('alert-title');
  const desc = document.getElementById('alert-desc');
  const agentsEl = document.getElementById('alert-agents');
  const btn = document.getElementById('confirm-btn');
  if (!panel || !btn) return;
  panel.classList.remove('predict');
  // Same rule as the rings (sysState): never say "all clear" while the map shows systems heading for their limits
  const st = CM.prevStates || {};
  const strained = Object.keys(st).filter(k => st[k] !== 'ok');
  const who = (typeof _crewDisplayName === 'function') ? _crewDisplayName(DASH_API.selectedCrewId) : 'this crew member';
  if (CM.resetting) {
    panel.style.borderColor = 'var(--cm-amber)'; icon.textContent = '⟳';
    title.textContent = 'Resetting to a clean start…'; title.style.color = 'var(--cm-amber)';
    desc.textContent = 'The demo was stopped. Readings are settling back to normal; this card will say Steady once two fresh checks agree.';
    if (agentsEl) agentsEl.innerHTML = ''; btn.style.display = 'none';
    return;
  }
  if (strained.length) {
    const names = strained.map(k => k.toUpperCase()).join(', ');
    panel.style.borderColor = 'var(--cm-amber)';
    icon.textContent = '◔';
    const nOutSel = strained.filter(k => st[k] === 'out').length;
    title.textContent = nOutSel >= 3 ? `${nOutSel} systems out of range · AEGIS is confirming the cascade alert`
                      : nOutSel ? `${strained.length} system${strained.length > 1 ? 's' : ''} strained · ${nOutSel} out of range · agents checking`
                      : `${strained.length} system${strained.length > 1 ? 's' : ''} heading for ${strained.length > 1 ? 'their limits' : 'its limit'} · no action needed yet`;
    title.style.color = 'var(--cm-amber)';
    desc.textContent = `${names} ${strained.length > 1 ? 'are' : 'is'} heading toward ${strained.length > 1 ? 'their limits' : 'its limit'} for ${who}. ` +
      (strained.some(k => st[k] === 'out') ? 'At least one is already out of range; the agents are confirming it on their next check. '
                                              : 'Nothing has crossed yet. ') +
      'If three systems are forecast to cross together, the early warning appears here.';
  } else {
    panel.style.borderColor = 'var(--green)';
    icon.textContent = '✓';
    title.textContent = `Steady · no alerts · ${who}`;
    title.style.color = 'var(--green)';
    // Rings can sit past halfway at rest (e.g. a light sleeper): say so, so the words match what the map shows
    const mine = CM.last && (CM.last.crew || []).find(c => c.crew_member_id === DASH_API.selectedCrewId);
    const near = [];
    if (mine) Object.entries(CM_AGENTS).forEach(([a, ms]) => {
      const k = Math.max(0, ...ms.map(m => mine.metrics[m]).filter(Boolean).map(_cmStrain));
      if (k >= 0.6) near.push(`${PLAIN_DOMAIN[a.toUpperCase()]} (${Math.round(Math.min(k, 1) * 100)}% of the way)`);
    });
    desc.textContent = 'All four systems are inside their limits and none is moving toward one. ' +
      (near.length ? `Closest to a limit: ${plainList(near)}, holding steady. ` : '') +
      "If anything starts heading the wrong way, the early warning appears here first.";
  }
  title.title = ''; desc.title = '';          // no leftover tooltip from an earlier alert
  agentsEl.innerHTML = '';
  btn.style.display = 'none';
  _showPreventBtn(false);
  _renderAiNote('');
}

function _showPreventBtn(show) {
  const pb = document.getElementById('prevent-btn');
  if (pb) { pb.style.display = show ? '' : 'none'; if (show) pb.disabled = false; }
}

// PREVENT-1: act on the forecast. Records the plan in the backend, runs it on board (sim), and the panel
// tracks the outcome until the trends are back in range ("cascade averted").
async function startPrevention() {
  const crewId = PREDICT_STATE.lastCrew;
  if (!crewId) return;
  const pb = document.getElementById('prevent-btn');
  if (pb) { pb.disabled = true; pb.textContent = '🛡️ Starting the prevention plan…'; }
  try {
    const r = await fetch(`${DASH_API.baseUrl}/api/crew/${crewId}/prevent`, { method: 'POST' });
    const d = await r.json();
    if (!r.ok || !d.started) throw new Error(d.reason || r.status);
    const now = Date.now();
    PREDICT_STATE.prevented = { crew: crewId, at: now, actions: d.actions || [], domains: d.domains || [], avertedAt: null };
    PREDICT_STATE.suppress[crewId] = now + 10 * 60000;
    PREDICT_STATE.lastCrew = null;
    if (typeof playTone === 'function') { playTone(523, 0.12); setTimeout(() => playTone(784, 0.15), 150); }
    const crewKey = CREW_KEY_BY_ID[crewId] || 'chen';
    // No pop-up: the plan is shown in the card, with a link to the spacecraft view for anyone who wants it
    PREDICT_STATE.preventKey = crewKey;
    if (typeof typeResponse === 'function') {
      typeResponse(`Prevention plan started for ${_crewDisplayName(crewId)}. We're fixing their ${plainDomains(d.domains)} early, before anything piles up. I'll keep an eye on how it goes.`);
    }
    renderPreventedPanel(null);
  } catch (e) {
    if (pb) { pb.disabled = false; pb.textContent = '🛡️ Start prevention plan'; }
    if (typeof typeResponse === 'function') typeResponse("Sorry, I couldn't start the prevention plan just now: " + e.message);
  }
}

function renderPreventedPanel(entry) {
  const pv = PREDICT_STATE.prevented;
  if (!pv) return;
  const panel = document.getElementById('alert-panel');
  const icon = document.getElementById('alert-icon');
  const title = document.getElementById('alert-title');
  const desc = document.getElementById('alert-desc');
  const agentsEl = document.getElementById('alert-agents');
  const btn = document.getElementById('confirm-btn');
  if (!panel) return;
  const name = _crewDisplayName(pv.crew);
  const averted = !!pv.avertedAt;
  panel.classList.remove('predict');
  panel.style.borderColor = 'var(--green)';
  icon.textContent = averted ? '✓' : '🛡️';
  title.style.color = 'var(--green)';
  title.textContent = averted ? `Cascade avoided · ${name}` : `Prevention plan running · ${name}`;
  title.title = 'Protocol PREVENT-1';
  const outNow = entry && entry.out_now ? entry.out_now : [];
  desc.textContent = averted
    ? `All four systems are steady again. We spotted it early, acted, and it never turned into a cascade.`
    : `Small fixes started early.${outNow.length ? ` ${name}'s ${plainDomains(outNow)} ${outNow.length > 1 ? 'are' : 'is'} still out of range, so` : ''} I'll keep watching until everything is back to normal.`;
  desc.title = 'Demo scenario: the simulator reverses the drift once PREVENT-1 is accepted. With real telemetry AEGIS only reports whether readings recover.';
  agentsEl.innerHTML = `<ol class="prevent-steps">${pv.actions.map(a => `<li>${String(a).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]))}</li>`).join('')}</ol>` +
    pv.domains.map(n => `<span class="alert-badge" style="${AGENT_BADGE_STYLE[n] || AGENT_BADGE_STYLE.AEGIS}">${n}${outNow.includes(n) ? '' : ' ✓'}</span>`).join('') +
    (averted ? '' : ` <button type="button" class="link-btn" onclick="openSimForProtocol('PREVENT-1', '${CREW_KEY_BY_ID[pv.crew] || PREDICT_STATE.preventKey || 'chen'}')">See it on the spacecraft →</button>`);
  if (btn) btn.style.display = 'none';
  _showPreventBtn(false);
  _renderAiNote('');
}

// Shown only when there is no live alert: a predicted cascade (≥3 domains out of range or forecast to cross soon)
// The early-warning title and the map's centre clock read the same countdown, so they never disagree
// ONE countdown for every place that shows "time to cascade": the map centre, the early-warning title and text,
// AEGIS's spoken heads-up and the crew list. Whoever sees the forecast first starts it; later forecasts only move it
// if they disagree by more than 15 s.
function _syncEtaEnd(crewId, etaMin) {
  if (etaMin === null || etaMin === undefined) return;
  const end = Date.now() + etaMin * 60000;
  if (CM.etaCrew !== crewId || !CM.etaEnd || Math.abs(CM.etaEnd - end) > 15000) { CM.etaEnd = end; CM.etaCrew = crewId; }
}
function _etaText(crewId, etaMin) {
  if (CM.etaCrew === crewId && CM.etaEnd) { const c = _cmClock(); return c === 'NOW' ? 'moments' : c; }
  return fmtEta(etaMin);
}
function _predictTitleText() {
  const t = PREDICT_STATE.titleFor; if (!t) return '';
  const sameClock = CM.etaEnd && CM.etaCrew === t.crew;
  const c = sameClock ? _cmClock() : null;
  if (t.forming || c === 'NOW') return `Early warning: it's happening now · ${t.name}`;
  return `Early warning: cascade in ${c || fmtEta(t.eta)} · ${t.name}`;
}
function _syncPredictTitle() {
  const panel = document.getElementById('alert-panel'), title = document.getElementById('alert-title');
  if (panel && title && panel.classList.contains('predict') && PREDICT_STATE.titleFor) {
    title.textContent = _predictTitleText();
    const d = PREDICT_STATE.descFor, desc = document.getElementById('alert-desc');
    if (d && desc) desc.textContent = d.pre + _etaText(d.crew, d.eta) + d.post;
  }
}
function renderPredictPanel(p) {
  if (!p) { PREDICT_STATE.announcedKey = null; PREDICT_STATE.lastCrew = null; renderAllClear(); return; }
  PREDICT_STATE.lastCrew = p.crew_member_id;
  const panel = document.getElementById('alert-panel');
  const icon = document.getElementById('alert-icon');
  const title = document.getElementById('alert-title');
  const desc = document.getElementById('alert-desc');
  const agentsEl = document.getElementById('alert-agents');
  const btn = document.getElementById('confirm-btn');
  if (!panel || !btn) return;
  const name = _crewDisplayName(p.crew_member_id);
  panel.classList.add('predict');
  panel.style.borderColor = 'var(--yellow)';
  icon.textContent = '🔮';
  title.style.color = 'var(--yellow)';
  const forming = p.risk === 'converged';
  if (!forming) _syncEtaEnd(p.crew_member_id, p.cascade_eta_min);
  PREDICT_STATE.titleFor = { crew: p.crew_member_id, name, forming, eta: p.cascade_eta_min };
  title.textContent = _predictTitleText();
  title.title = 'PREDICT: forecast from the last few minutes of readings'; desc.title = '';
  // Plain sentence: what's already off + what's heading the wrong way
  const parts = [];
  if (p.out_now.length) parts.push(`${plainDomains(p.out_now)} ${p.out_now.length > 1 ? 'are' : 'is'} already off`);
  p.upcoming.forEach(u => parts.push(PLAIN_TREND[u.metric] || `${u.label.toLowerCase()} is drifting`));
  const upTxt = plainList(parts);
  desc.textContent = forming
    ? `The warning came true: ${name}'s ${plainDomains(p.out_now)} are now out of range. I'm raising the alert.`
    : `${name}'s ${upTxt}. If nothing changes, these will pile up into a bigger problem in ${_etaText(p.crew_member_id, p.cascade_eta_min)}. Acting now can stop it.`;
  PREDICT_STATE.descFor = forming ? null : { crew: p.crew_member_id, eta: p.cascade_eta_min,
    pre: `${name}'s ${upTxt}. If nothing changes, these will pile up into a bigger problem in `, post: '. Acting now can stop it.' };
  agentsEl.innerHTML =
    p.out_now.map(n => `<span class="alert-badge" style="${AGENT_BADGE_STYLE[n] || AGENT_BADGE_STYLE.AEGIS}">${n}</span>`).join('') +
    p.upcoming.map(u => `<span class="alert-badge forecast" title="Forecast to cross in ${fmtEta(u.eta_min)}" style="${AGENT_BADGE_STYLE[u.agent] || AGENT_BADGE_STYLE.AEGIS}">${u.agent} ⏱</span>`).join('');
  btn.style.display = 'none';
  // PREVENT: act on the forecast before the cascade (not once it is already forming)
  const pb = document.getElementById('prevent-btn');
  if (pb && pb.style.display === 'none') pb.textContent = '🛡️ Start prevention plan';
  _showPreventBtn(!forming);
  _renderAiNote(p.note && p.note.text ? `${AI_MARKER} (AEGIS): ${p.note.text}` : '');
  const key = p.crew_member_id;
  if (PREDICT_STATE.announcedKey !== key) {
    PREDICT_STATE.announcedKey = key;
    if (typeof playTone === 'function') { playTone(520, 0.1); setTimeout(() => playTone(620, 0.1), 140); }
    if (typeof typeResponse === 'function') {
      typeResponse(`AEGIS here, heads up. ${name}'s ${upTxt}. At this rate it could turn into a bigger problem in ${_etaText(p.crew_member_id, p.cascade_eta_min).replace(/^(\d+):(\d+)$/, (m, a, b) => +a ? `${a} minute${+a > 1 ? 's' : ''} ${+b} seconds` : `${+b} seconds`).replace('~', 'about ')}. Starting the prevention plan now should head it off.`);
    }
  }
}

