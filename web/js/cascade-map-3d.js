// ============================================
// CASCADE MAP · 3D VIEW (optional). The 2D map stays the source of truth: it computes every state, word and
// countdown; this view only reads them (CM.prevStates, CM.strain, CM.words, #cm-big/#cm-sub) and draws them in
// three.js. three.js is loaded only when someone turns 3D on; if it can't load, the 2D map simply stays.
// ============================================
const CM3D = { on: false, ready: false, loading: null, raf: 0, nodes: {}, lastMode: null, ripples: [], t0: 0 };
const CM3D_POS = { solaris: [-3.5, -1.9], gene: [3.5, -1.9], pulse: [3.5, 1.9], mirror: [-3.5, 1.9], osseo: [0, 4.4] };
const CM3D_NAMES = { solaris: 'SOLARIS', gene: 'GENE', pulse: 'PULSE', mirror: 'MIRROR', osseo: 'OSSEO' };
// Strain links between neighbouring health domains; OSSEO is context only and sits outside the ring.
const CM3D_LINKS = [['solaris', 'gene'], ['gene', 'pulse'], ['pulse', 'mirror'], ['mirror', 'solaris']];
function _cm3dLoad() {
  if (window.THREE && THREE.OrbitControls) return Promise.resolve();
  if (CM3D.loading) return CM3D.loading;
  const add = src => new Promise((res, rej) => { const s = document.createElement('script'); s.src = src; s.onload = res; s.onerror = rej; document.head.appendChild(s); });
  CM3D.loading = add('https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js')
    .then(() => add('https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js'));
  return CM3D.loading;
}
function _cm3dVar(name, fb) { const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim(); return v || fb; }
function _cm3dGlowTex(THREE) {
  const c = document.createElement('canvas'); c.width = c.height = 128;
  const g = c.getContext('2d'), r = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  r.addColorStop(0, 'rgba(255,255,255,1)'); r.addColorStop(.25, 'rgba(255,255,255,.55)'); r.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = r; g.fillRect(0, 0, 128, 128);
  const t = new THREE.Texture(c); t.needsUpdate = true; return t;
}
function _cm3dBuild() {
  const T = THREE, host = document.getElementById('cm3d'), canvas = host.querySelector('canvas');
  const reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const renderer = new T.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  const scene = new T.Scene();
  scene.fog = new T.FogExp2(0x050a16, 0.035);
  const camera = new T.PerspectiveCamera(38, 2, 0.1, 200);
  camera.position.set(0, 4.4, 8.0);
  const controls = new T.OrbitControls(camera, canvas);
  Object.assign(controls, { enableDamping: true, dampingFactor: 0.08, enablePan: false, minDistance: 7, maxDistance: 16,
    minPolarAngle: 0.35, maxPolarAngle: 1.35, autoRotate: false });
  controls.target.set(0, -0.25, 0);
  // Idle: a gentle sway around the front view (the layout stays where the 2D map has it). Drag/zoom: the person
  // takes over, and the sway eases back in 8 s after they let go.
  const touch = () => { CM3D.userAt = performance.now(); };
  canvas.addEventListener('pointerdown', touch); canvas.addEventListener('wheel', touch, { passive: true });
  controls.addEventListener('change', () => { if (CM3D.dragging) touch(); });
  canvas.addEventListener('pointerdown', () => { CM3D.dragging = true; }); window.addEventListener('pointerup', () => { CM3D.dragging = false; });
  scene.add(new T.AmbientLight(0x6b8cff, 0.35));
  const key = new T.DirectionalLight(0xffffff, 0.6); key.position.set(4, 8, 6); scene.add(key);
  const coreLight = new T.PointLight(0x22d3ee, 2.2, 12, 2); coreLight.position.set(0, 0.6, 0); scene.add(coreLight);
  const glowTex = _cm3dGlowTex(T);
  // Floor: concentric rings + radial spokes, like a console plotting table
  const floor = new T.Group();
  for (let r = 1; r <= 5; r++) {
    const g = new T.RingGeometry(r * 0.95 - 0.006, r * 0.95 + 0.006, 96);
    const m = new T.Mesh(g, new T.MeshBasicMaterial({ color: 0x3a6ea8, transparent: true, opacity: 0.16 - r * 0.02, side: T.DoubleSide }));
    m.rotation.x = -Math.PI / 2; floor.add(m);
  }
  for (let i = 0; i < 12; i++) {
    const a = i / 12 * Math.PI * 2, pts = [new T.Vector3(Math.cos(a) * 0.9, 0, Math.sin(a) * 0.9), new T.Vector3(Math.cos(a) * 4.9, 0, Math.sin(a) * 4.9)];
    floor.add(new T.Line(new T.BufferGeometry().setFromPoints(pts), new T.LineBasicMaterial({ color: 0x3a6ea8, transparent: true, opacity: 0.08 })));
  }
  floor.position.y = -0.9; scene.add(floor);
  // Stars
  const sg = new T.BufferGeometry(), sp = [];
  for (let i = 0; i < 700; i++) { const r = 30 + Math.random() * 50, th = Math.random() * Math.PI * 2, ph = Math.acos(2 * Math.random() - 1);
    sp.push(r * Math.sin(ph) * Math.cos(th), r * Math.cos(ph), r * Math.sin(ph) * Math.sin(th)); }
  sg.setAttribute('position', new T.Float32BufferAttribute(sp, 3));
  scene.add(new T.Points(sg, new T.PointsMaterial({ color: 0xbfd7ff, size: 0.14, transparent: true, opacity: 0.75, fog: false })));
  // AEGIS core: faceted shell + inner glow + orbit ring
  const core = new T.Group(); core.position.y = 0.4; scene.add(core);
  const shellMat = new T.MeshStandardMaterial({ color: 0x0b1b33, emissive: 0x22d3ee, emissiveIntensity: 0.35, metalness: 0.15, roughness: 0.7, flatShading: true, transparent: true, opacity: 0.9 });
  const shell = new T.Mesh(new T.IcosahedronGeometry(0.78, 1), shellMat); core.add(shell);
  const wire = new T.LineSegments(new T.WireframeGeometry(new T.IcosahedronGeometry(0.84, 1)), new T.LineBasicMaterial({ color: 0x22d3ee, transparent: true, opacity: 0.55 }));
  core.add(wire);
  const coreGlow = new T.Sprite(new T.SpriteMaterial({ map: glowTex, color: 0x22d3ee, transparent: true, opacity: 0.55, depthWrite: false, blending: T.AdditiveBlending }));
  coreGlow.scale.set(3.4, 3.4, 1); core.add(coreGlow);
  const segs = [];
  for (let i = 0; i < 3; i++) {     // three segments = three strained systems make a cascade (same as 2D)
    const seg = new T.Mesh(new T.TorusGeometry(1.15, 0.045, 8, 48, Math.PI * 2 / 3 - 0.18),
      new T.MeshBasicMaterial({ color: 0x22d3ee, transparent: true, opacity: 0.35 }));
    seg.rotation.x = Math.PI / 2; seg.rotation.z = i * Math.PI * 2 / 3 + 0.09; core.add(seg); segs.push(seg);
  }
  // Agents
  const nodeMeshes = [];
  Object.entries(CM3D_POS).forEach(([agent, [x, z]]) => {
    const col = new T.Color(_cm3dVar('--' + agent, '#7aa7ff'));
    const isOsseo = agent === 'osseo';
    const g = new T.Group(); g.position.set(x, 0.3, z); scene.add(g);
    const orb = new T.Mesh(new T.SphereGeometry(isOsseo ? 0.3 : 0.42, 32, 24),
      new T.MeshStandardMaterial({ color: col.clone().multiplyScalar(isOsseo ? 0.9 : 0.35),
        emissive: isOsseo ? 0x9aa3af : col, emissiveIntensity: isOsseo ? 0.12 : 0.55,
        metalness: isOsseo ? 0.03 : 0.3, roughness: isOsseo ? 0.62 : 0.35 }));
    orb.userData.agent = agent; g.add(orb); nodeMeshes.push(orb);
    const halo = new T.Sprite(new T.SpriteMaterial({ map: glowTex, color: col, transparent: true, opacity: 0.45, depthWrite: false, blending: T.AdditiveBlending }));
    halo.scale.set(1.9, 1.9, 1); g.add(halo);
    const track = new T.Mesh(new T.TorusGeometry(0.72, 0.025, 8, 64), new T.MeshBasicMaterial({ color: 0x9fb4d8, transparent: true, opacity: 0.18 }));
    track.rotation.x = Math.PI / 2; g.add(track);
    const gauge = new T.Mesh(new T.TorusGeometry(0.72, 0.06, 10, 64, 0.01), new T.MeshBasicMaterial({ color: 0x22d3ee }));
    gauge.rotation.x = Math.PI / 2; g.add(gauge);
    const beam = new T.Mesh(new T.CylinderGeometry(0.02, 0.02, 1.2, 8), new T.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.25 }));
    beam.position.y = -0.6; g.add(beam);
    // Spoke to the core
    const spokePts = [new T.Vector3(x, 0.3, z), new T.Vector3(x * 0.55, 0.75, z * 0.55), new T.Vector3(0, 0.4, 0)];
    const spokeCurve = new T.QuadraticBezierCurve3(...spokePts);
    const spoke = new T.Mesh(new T.TubeGeometry(spokeCurve, 40, 0.018, 6, false), new T.MeshBasicMaterial({ color: 0x5b7fb5, transparent: true, opacity: 0.4 }));
    scene.add(spoke);
    const parts = [0, 1, 2].map(i => { const p = new T.Mesh(new T.SphereGeometry(0.06, 10, 8), new T.MeshBasicMaterial({ color: 0xf5b544 }));
      const s = new T.Sprite(new T.SpriteMaterial({ map: glowTex, color: 0xf5b544, transparent: true, opacity: 0.8, depthWrite: false, blending: T.AdditiveBlending }));
      s.scale.set(0.45, 0.45, 1); p.add(s); p.visible = false; scene.add(p); return { m: p, s, off: i / 3 }; });
    const label = document.createElement('div'); label.className = 'cm3d-label';
    label.innerHTML = `<b>${CM3D_NAMES[agent]}</b><span class="v"></span><span class="w"></span>`;
    label.onclick = () => { try { selectAgent(agent); } catch (e) {} };
    host.querySelector('.cm3d-labels').appendChild(label);
    CM3D.nodes[agent] = { g, orb, halo, gauge, spoke, spokeCurve, parts, label, col, strain: -1, state: null };
  });
  // Neighbour links (strain spreads between neighbouring systems in the 2D map too)
  CM3D.links = CM3D_LINKS.map(([a, b]) => {
    const [ax, az] = CM3D_POS[a], [bx, bz] = CM3D_POS[b];
    const mid = new T.Vector3((ax + bx) / 2 * 1.25, 0.9, (az + bz) / 2 * 1.45);
    const curve = new T.QuadraticBezierCurve3(new T.Vector3(ax, 0.3, az), mid, new T.Vector3(bx, 0.3, bz));
    const tube = new T.Mesh(new T.TubeGeometry(curve, 50, 0.012, 6, false), new T.MeshBasicMaterial({ color: 0x5b7fb5, transparent: true, opacity: 0.22 }));
    scene.add(tube);
    const comet = new T.Sprite(new T.SpriteMaterial({ map: glowTex, color: 0xff4d5e, transparent: true, opacity: 0.9, depthWrite: false, blending: T.AdditiveBlending }));
    comet.scale.set(0.5, 0.5, 1); comet.visible = false; scene.add(comet);
    return { a, b, curve, tube, comet };
  });
  // Click a node = same as clicking it in 2D
  const ray = new T.Raycaster(), ptr = new T.Vector2();
  let down = null;
  canvas.addEventListener('pointerdown', e => { down = [e.clientX, e.clientY]; });
  canvas.addEventListener('pointerup', e => {
    if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 5) return;
    const r = canvas.getBoundingClientRect(); ptr.set((e.clientX - r.left) / r.width * 2 - 1, -(e.clientY - r.top) / r.height * 2 + 1);
    ray.setFromCamera(ptr, camera); const hit = ray.intersectObjects(nodeMeshes)[0];
    if (hit) { try { selectAgent(hit.object.userData.agent); } catch (err) {} }
  });
  const resize = () => { const w = host.clientWidth, h = host.clientHeight; if (!w || !h) return; renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
    // Narrow screens: pull the camera back so the four monitored domains and OSSEO remain in frame
    CM3D.baseR = 9.2 * Math.max(1, Math.pow(1.9 / camera.aspect, 0.9)); controls.maxDistance = Math.max(16, CM3D.baseR + 4); };
  new ResizeObserver(resize).observe(host); resize();
  camera.position.sub(controls.target).setLength(CM3D.baseR || 9.2).add(controls.target);   // start already framed
  controls.target.y = -0.25;   // lift the scene a little so the front-row labels stay inside the frame
  Object.assign(CM3D, { T, renderer, scene, camera, controls, core, shell, shellMat, wire, coreGlow, coreLight, segs, glowTex, reduce, ready: true, t0: performance.now() });
}
const CM3D_COL = { ok: 0x22d3ee, watch: 0xf5b544, out: 0xff4d5e };
function _cm3dModeColor(mode) { return mode === 'alert' ? 0xff4d5e : mode === 'cascade' || mode === 'watch' || mode === 'prevent' ? 0xf5b544 : 0x22d3ee; }
function _cm3dRipple(color) {
  const T = CM3D.T, m = new T.Mesh(new T.RingGeometry(0.9, 1.0, 96), new T.MeshBasicMaterial({ color, transparent: true, opacity: 0.8, side: T.DoubleSide, depthWrite: false }));
  m.rotation.x = -Math.PI / 2; m.position.y = -0.85; CM3D.scene.add(m); CM3D.ripples.push({ m, t: performance.now() });
}
function _cm3dFrame(now) {
  if (!CM3D.on) return;
  CM3D.raf = requestAnimationFrame(_cm3dFrame);
  if (document.hidden) return;
  const T = CM3D.T, t = (now - CM3D.t0) / 1000, states = CM.prevStates || {}, strainAll = CM.strain || {}, words = CM.words || {};
  const mode = document.getElementById('node-aegis')?.dataset.mode || 'none';
  const modeCol = _cm3dModeColor(mode);
  // Core reflects the map's mode; a new alert or a forming cascade sends a ripple across the floor
  CM3D.shellMat.emissive.setHex(modeCol); CM3D.wire.material.color.setHex(modeCol); CM3D.coreGlow.material.color.setHex(modeCol); CM3D.coreLight.color.setHex(modeCol);
  const hot = mode === 'alert';
  const beat = hot ? 0.5 + 0.5 * Math.sin(t * 6) : 0.5 + 0.5 * Math.sin(t * 1.6);
  CM3D.shellMat.emissiveIntensity = (hot ? 0.55 : 0.3) + beat * 0.25;
  CM3D.coreGlow.material.opacity = (hot ? 0.6 : 0.4) + beat * 0.2;
  CM3D.coreLight.intensity = (hot ? 2.8 : 1.8) + beat * 0.8;
  if (!CM3D.reduce) { CM3D.shell.rotation.y += hot ? 0.012 : 0.004; CM3D.wire.rotation.y -= 0.003; CM3D.core.position.y = 0.4 + Math.sin(t * 1.2) * 0.05; }
  if (CM3D.lastMode !== null && mode !== CM3D.lastMode && (mode === 'alert' || mode === 'cascade')) _cm3dRipple(modeCol);
  CM3D.lastMode = mode;
  const strained = Object.values(states).filter(s => s !== 'ok').sort((x, y) => (y === 'out') - (x === 'out'));
  CM3D.segs.forEach((seg, i) => { const s = strained[i]; seg.material.color.setHex(s ? CM3D_COL[s] : 0x22d3ee); seg.material.opacity = s ? 0.95 : 0.3; });
  // Agents
  Object.entries(CM3D.nodes).forEach(([agent, n]) => {
    const st = states[agent] || 'ok', strain = Math.min(1, Math.max(0, strainAll[agent] || 0)), col = CM3D_COL[st];
    if (Math.abs(strain - n.strain) > 0.01) {          // the gauge arc = how far the reading is toward its limit
      n.gauge.geometry.dispose(); n.gauge.geometry = new T.TorusGeometry(0.72, 0.06, 10, 64, Math.max(0.01, strain * Math.PI * 2)); n.strain = strain;
    }
    n.gauge.material.color.setHex(col);
    n.gauge.rotation.z = -Math.PI / 2;
    const pulse = st === 'out' ? 0.5 + 0.5 * Math.sin(t * 7) : st === 'watch' ? 0.5 + 0.5 * Math.sin(t * 3) : 0.5;
    n.orb.material.emissiveIntensity = agent === 'osseo' ? 0.12 : st === 'ok' ? 0.5 : 0.6 + pulse * 0.6;
    n.halo.material.color.setHex(st === 'ok' ? n.col.getHex() : col);
    const hs = st === 'out' ? 2.2 + pulse * 0.8 : st === 'watch' ? 2.0 + pulse * 0.3 : 1.8;
    n.halo.scale.set(hs, hs, 1);
    if (!CM3D.reduce) n.g.position.y = 0.3 + Math.sin(t * 1.4 + n.g.position.x) * 0.06 + (st === 'out' ? 0.15 : 0);
    n.spoke.material.color.setHex(st === 'ok' ? 0x5b7fb5 : col); n.spoke.material.opacity = st === 'ok' ? 0.35 : 0.85;
    n.parts.forEach(p => {
      p.m.visible = st !== 'ok';
      if (!p.m.visible) return;
      const u = ((t * (st === 'out' ? 0.55 : 0.32)) + p.off) % 1;
      p.m.position.copy(n.spokeCurve.getPoint(u)); p.m.material.color.setHex(col); p.s.material.color.setHex(col);
    });
    // Label follows the node on screen
    // Back row labels sit above their sphere, front row below, so a label never covers the other row
    const front = CM3D_POS[agent][1] > 0;
    const v = n.g.position.clone(); v.y += front ? -0.75 : 0.95; v.project(CM3D.camera);
    const host = document.getElementById('cm3d');
    const lw = n.label.offsetWidth || 110, lh = n.label.offsetHeight || 46;
    let lx = (v.x * 0.5 + 0.5) * host.clientWidth, ly = (-v.y * 0.5 + 0.5) * host.clientHeight;
    lx = Math.min(host.clientWidth - lw / 2 - 4, Math.max(lw / 2 + 4, lx));                       // keep labels inside the frame
    ly = front ? Math.min(host.clientHeight - lh - 4, ly) : Math.max(lh + 4 + (host.clientWidth < 480 ? 46 : 0), ly);
    n.label.style.transform = `translate(-50%,${front ? '0' : '-100%'}) translate(${lx}px,${ly}px)`;
    n.label.dataset.state = st;
    n.label.querySelector('.v').textContent = (document.getElementById('cm-val-' + agent)?.textContent || '') + ' ' + (document.getElementById('cm-unit-' + agent)?.textContent || '');
    n.label.querySelector('.w').textContent = words[agent] || '';
  });
  // Strain spreading: a comet runs along a link when both neighbours are strained
  CM3D.links.forEach((L, i) => {
    const sa = states[L.a] || 'ok', sb = states[L.b] || 'ok', on = sa !== 'ok' && sb !== 'ok';
    L.tube.material.opacity = on ? 0.7 : 0.22; L.tube.material.color.setHex(on ? (sa === 'out' || sb === 'out' ? 0xff4d5e : 0xf5b544) : 0x5b7fb5);
    L.comet.visible = on;
    if (on) { L.comet.position.copy(L.curve.getPoint((t * 0.4 + i * 0.25) % 1)); L.comet.material.color.copy(L.tube.material.color); }
  });
  // Ripples
  CM3D.ripples = CM3D.ripples.filter(r => { const k = (now - r.t) / 1600; if (k >= 1) { CM3D.scene.remove(r.m); r.m.geometry.dispose(); return false; }
    const s = 1 + k * 4; r.m.scale.set(s, s, s); r.m.material.opacity = 0.8 * (1 - k); return true; });
  // Centre readout: the same text as the 2D centre (countdown, SHELTER, NOW, Done...)
  const big = document.getElementById('cm-big')?.textContent || '', sub = document.getElementById('cm-sub')?.textContent || '';
  const cb = document.getElementById('cm3d-big'), cs = document.getElementById('cm3d-sub');
  if (cb.textContent !== big) cb.textContent = big; if (cs.textContent !== sub) cs.textContent = sub;
  const centre = document.querySelector('#cm3d .cm3d-centre'); centre.dataset.mode = mode;
  const cv = CM3D.core.position.clone(); cv.y += 1.45; cv.project(CM3D.camera);
  const hostEl = document.getElementById('cm3d');
  centre.style.transform = hostEl.clientWidth < 480 ? `translate(-50%,0) translate(${hostEl.clientWidth / 2}px,6px)`     // phone: pinned to the top
    : `translate(-50%,-100%) translate(${(cv.x * 0.5 + 0.5) * hostEl.clientWidth}px,${(-cv.y * 0.5 + 0.5) * hostEl.clientHeight}px)`;
  if (!CM3D.reduce && now - (CM3D.userAt ?? -1e9) > 8000) {
    const c = CM3D.camera, tg = CM3D.controls.target, off = c.position.clone().sub(tg);
    const sph = new T.Spherical().setFromVector3(off);
    const wantTheta = 0.32 * Math.sin(t * 0.18), wantPhi = 1.0 + 0.06 * Math.sin(t * 0.11), wantR = CM3D.baseR || 9.2;
    sph.theta += (wantTheta - sph.theta) * 0.02; sph.phi += (wantPhi - sph.phi) * 0.02; sph.radius += (wantR - sph.radius) * 0.02;
    c.position.copy(new T.Vector3().setFromSpherical(sph).add(tg));
  }
  CM3D.controls.update();
  CM3D.renderer.render(CM3D.scene, CM3D.camera);
}
async function toggleCm3d(force) {
  const want = typeof force === 'boolean' ? force : !CM3D.on;
  const svg = document.getElementById('cascade-map'), host = document.getElementById('cm3d'), btn = document.getElementById('cm3d-btn');
  if (!want) {
    CM3D.on = false; cancelAnimationFrame(CM3D.raf); host.hidden = true; svg.style.display = '';
    btn.setAttribute('aria-pressed', 'false'); btn.textContent = '◈ 3D';
    try { localStorage.setItem('aegis.cm3d', '0'); } catch (e) {}
    return;
  }
  btn.textContent = 'Loading 3D…'; btn.disabled = true;
  try {
    await _cm3dLoad();
    host.hidden = false;
    // Minimum = the 2D map's height; on wide screens the view also grows to fill its panel (flex), so a tall
    // vitals column next to it doesn't leave an empty band under the map.
    host.style.minHeight = (innerWidth < 640 ? 320 : Math.max(260, Math.round(svg.getBoundingClientRect().height || 340))) + 'px';
    host.style.height = '';
    svg.style.display = 'none';
    if (!CM3D.ready) _cm3dBuild();
    CM3D.on = true; CM3D.raf = requestAnimationFrame(_cm3dFrame);
    btn.setAttribute('aria-pressed', 'true'); btn.textContent = '◇ 2D';
    try { localStorage.setItem('aegis.cm3d', '1'); } catch (e) {}
  } catch (e) {
    console.warn('3D view unavailable', e);
    host.hidden = true; svg.style.display = ''; btn.textContent = '◈ 3D unavailable';
  } finally { btn.disabled = false; }
}
// 3D is the default view; 2D is one click away and the choice is remembered. If three.js or WebGL is unavailable
// the toggle falls back to 2D on its own.
(function () {
  let pref = null; try { pref = localStorage.getItem('aegis.cm3d'); } catch (e) {}
  let webgl = false; try { const c = document.createElement('canvas'); webgl = !!(window.WebGLRenderingContext && (c.getContext('webgl') || c.getContext('experimental-webgl'))); } catch (e) {}
  if (pref !== '0' && webgl) setTimeout(() => toggleCm3d(true), 600);
})();

