// MISSION BAR: ship position + light-time to Earth (why AEGIS has to act on board)
// ============================================
function _mbDistanceAU(frac) {
  // Earth-ship distance on a Hohmann transfer (two-body, circular coplanar orbits): ellipse a = 1.262 AU, e = 0.208,
  // 259-day transfer, with this mission phase mapped onto it by fraction of the trip. Kepler's equation is solved for
  // the ship's position. An estimate (good to roughly 10-20 %), not an ephemeris.
  const a = 1.262, e = 0.208, T = 259;
  const M = Math.PI * frac;                                       // mean anomaly (half an orbit over the transfer)
  let E = M;
  for (let i = 0; i < 30; i++) E -= (E - e * Math.sin(E) - M) / (1 - e * Math.cos(E));
  const f = 2 * Math.atan2(Math.sqrt(1 + e) * Math.sin(E / 2), Math.sqrt(1 - e) * Math.cos(E / 2));
  const r = a * (1 - e * Math.cos(E));
  const thE = 2 * Math.PI * (frac * T) / 365.25;                  // Earth's progress over the same time
  return Math.sqrt(1 + r * r - 2 * r * Math.cos(f - thE));
}
function _mbFmt(sec) {
  if (sec < 60) return sec < 10 ? `${sec.toFixed(1)} s` : `${Math.round(sec)} s`;
  const m = Math.floor(sec / 60), s = Math.round(sec % 60);
  return `${m} min ${String(s).padStart(2, '0')} s`;
}
function renderMissionBar() {
  const day = parseFloat((document.getElementById('mission-day-num') || {}).textContent) || 54;
  const total = MISSION.phase_days || 180;
  const prof = MISSION.profile || 'mars_transit';
  const frac = Math.min(1, Math.max(0, day / total));
  const label = (MISSION.label || 'Mars transit').replace(/\s*\(.*\)/, '');
  const phase = document.getElementById('mb-phase');
  const left = Math.max(0, Math.round(total - day));
  if (phase) phase.innerHTML = prof === 'mars_transit' ? `<b>${Math.round(frac * 100)}%</b> of the way to Mars · arrival in ${left} days`
                                                       : `<b>${label}</b> · Day ${Math.floor(day)} of ${total}`;
  const kick = document.getElementById('mb-kicker'); if (kick) kick.textContent = (MISSION.label || 'Mars transit').replace(/\s*\((.*)\)/, ' · $1');
  const hd = document.getElementById('hero-day'); if (hd) hd.textContent = Math.floor(day);
  const ht = document.getElementById('hero-total'); if (ht) ht.textContent = total;
  // Ship on the transfer arc
  const path = document.getElementById('hero-path'), done = document.getElementById('hero-done'), ship = document.getElementById('hero-ship');
  if (path && path.getTotalLength) {
    const L = path.getTotalLength(), at = L * frac;
    if (done) { done.style.strokeDasharray = `${at.toFixed(1)} ${L.toFixed(1)}`; }
    const p = path.getPointAtLength(at), q = path.getPointAtLength(Math.min(L, at + 1));
    const ang = Math.atan2(q.y - p.y, q.x - p.x) * 180 / Math.PI;
    if (ship) ship.setAttribute('transform', `translate(${p.x.toFixed(1)},${p.y.toFixed(1)}) rotate(${ang.toFixed(1)})`);
    const lab = document.getElementById('hero-ship-label');
    if (lab) { lab.setAttribute('x', p.x.toFixed(1)); lab.setAttribute('y', Math.max(10, p.y - 16).toFixed(1)); }
    HERO.L = L; HERO.at = at;
  }
  // One-way light-time to Earth for this mission profile
  let oneway, note, later = '';
  if (prof === 'mars_transit') {
    const au = _mbDistanceAU(frac), auEnd = _mbDistanceAU(1);
    oneway = `≈ ${_mbFmt(au * 499.0)}`;
    later = `now · ≈ ${Math.round(auEnd * 499 / 60)} min by arrival · 3–22 min while at Mars (estimate)`;
    note = `About ${au.toFixed(2)} AU from Earth now and about ${auEnd.toFixed(1)} AU at arrival: an estimate from a ` +
           `two-body Hohmann-transfer model (Kepler solve, circular orbits), not a real trajectory. ` +
           `At Mars the delay is 3 to 22 minutes each way, depending on where the planets are.`;
  } else if (prof === 'mars_surface') { oneway = '3–22 min'; note = 'Earth-Mars light-time varies with the planets\' positions.'; }
  else if (prof === 'lunar_surface') { oneway = '1.3 s'; note = 'Earth-Moon light-time.'; }
  else { oneway = '< 1 s'; note = 'Low Earth orbit: near-real-time contact with mission control.'; }
  const ow = document.getElementById('mb-oneway'); if (ow) ow.textContent = oneway;
  const lt = document.getElementById('mb-later'); if (lt) lt.textContent = later || 'one way';
  const dl = document.getElementById('mb-delay');
  if (dl) dl.title = `${note} A message sent now reaches mission control after this delay, and any reply takes as long again.`;
  HERO.oneway = oneway;
}
// The signal pulse travels from the ship back to Earth, over and over
const HERO = { L: 0, at: 0, oneway: '' };
(function heroSignal() {
  const sig = document.getElementById('hero-signal'), path = document.getElementById('hero-path');
  if (!sig || !path || !path.getPointAtLength || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  let t0 = performance.now();
  const step = now => {
    // The signal travels the whole course, Earth -> ship -> Mars, about every 3 s (brand moment: rust)
    const period = 3000, k = ((now - t0) % (period + 600)) / period;
    const L = HERO.L || (path.getTotalLength ? path.getTotalLength() : 0);
    if (k <= 1 && L > 0) {
      const p = path.getPointAtLength(L * k);
      sig.setAttribute('cx', p.x.toFixed(1)); sig.setAttribute('cy', p.y.toFixed(1));
      sig.style.opacity = String(k < .06 ? k / .06 : k > .94 ? (1 - k) / .06 : 1);
    } else sig.style.opacity = '0';
    requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
})();
renderMissionBar();

// ============================================
