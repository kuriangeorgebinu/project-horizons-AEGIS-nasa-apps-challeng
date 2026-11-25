// ── Protocol → spacecraft sim link (Claude, Sep 29) ──
const SIM_PAGE = 'aegis-spacecraft-sim-2d.html';
let _simLoaded = false;

function openSimForProtocol(protocol, crewKey) {
  const modal = document.getElementById('sim-modal');
  const frame = document.getElementById('sim-frame');
  if (!modal || !frame) return;
  const qs = `protocol=${encodeURIComponent(protocol)}&crew=${encodeURIComponent(crewKey || 'chen')}`;
  document.getElementById('sim-modal-title').textContent = `Protocol ${protocol}`;
  const status = document.getElementById('sim-modal-status');
  status.textContent = 'Running…'; status.classList.remove('done');
  document.getElementById('sim-modal-newtab').href = `${SIM_PAGE}?${qs}`;
  modal.classList.add('open');
  if (_simLoaded && frame.contentWindow) {
    // Sim already loaded: send the protocol without reloading (crew keep their positions)
    frame.contentWindow.postMessage({ type: 'aegis-protocol', protocol, crew: crewKey }, location.origin);
  } else {
    frame.onload = () => { _simLoaded = true; };
    frame.src = `${SIM_PAGE}?${qs}`;
  }
  document.querySelector('#sim-modal button')?.focus();
}

function closeSimModal() {
  document.getElementById('sim-modal')?.classList.remove('open');
}

window.addEventListener('message', e => {
  if (e.origin !== location.origin) return;
  if (e.data && e.data.type === 'aegis-protocol-complete') {
    const status = document.getElementById('sim-modal-status');
    if (status) { status.textContent = '✓ Complete'; status.classList.add('done'); }
  }
});
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeSimModal(); });
document.getElementById('sim-modal')?.addEventListener('click', e => { if (e.target.id === 'sim-modal') closeSimModal(); });
