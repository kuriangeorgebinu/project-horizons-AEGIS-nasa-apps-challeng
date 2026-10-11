"""Generates the Cascade Map SVG markup and splices it into aegis-mission-control.html (one-off build helper)."""
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
F = ROOT / "aegis-mission-control.html"
C = (320, 165)
NODES = [  # key, label, icon, x, y, aria
    ("solaris", "SOLARIS", "☀", 130, 80, "SOLARIS, radiation"),
    ("gene", "GENE", "🧬", 510, 80, "GENE, inflammation"),
    ("pulse", "PULSE", "💓", 510, 250, "PULSE, heart and oxygen"),
    ("mirror", "MIRROR", "🌙", 130, 250, "MIRROR, sleep"),
]
LINKS = [("solaris", "gene", "M130,80 Q320,10 510,80"), ("gene", "pulse", "M510,80 Q612,165 510,250"),
         ("pulse", "mirror", "M510,250 Q320,322 130,250"), ("mirror", "solaris", "M130,250 Q28,165 130,80")]
R_GAUGE = 44
CIRC = 2 * math.pi * R_GAUGE


def arc(a0, a1, r=60):
    p = lambda a: (r * math.cos(math.radians(a)), r * math.sin(math.radians(a)))
    (x0, y0), (x1, y1) = p(a0), p(a1)
    return f"M{x0:.2f},{y0:.2f} A{r},{r} 0 0 1 {x1:.2f},{y1:.2f}"


out = ['  <div class="graph-panel" aria-label="Cascade map">',
       '    <div class="cm-head">',
       '      <div class="graph-title">Cascade Map · <span id="cm-crew">Commander Chen</span></div>',
       '      <div class="cm-legend" aria-hidden="true"><span><i style="background:var(--green)"></i>Steady</span>'
       '<span><i style="background:var(--yellow)"></i>Heading for its limit</span>'
       '<span><i style="background:var(--red)"></i>Out of range</span></div>',
       '    </div>',
       '    <svg class="cm-svg" id="cascade-map" viewBox="0 0 640 336" role="img" aria-labelledby="cm-caption">']
for a, b, d in LINKS:
    out.append(f'      <path class="cm-link" id="link-{a}-{b}" d="{d}"/>')
for k, _, _, x, y, _ in NODES:
    d = f"M{x},{y} L{C[0]},{C[1]}"
    out.append(f'      <path class="conn-line" id="line-{k}" d="{d}"/>')
    for i, begin in enumerate(("0s", "0.8s")):
        out.append(f'      <circle class="cm-particle" data-agent="{k}" r="3.5"><animateMotion dur="1.6s" begin="{begin}" '
                   f'repeatCount="indefinite" path="{d}"/></circle>')
out.append('      <g id="cm-fx"></g>')
# AEGIS centre
segs = [(-82, 22), (38, 142), (158, 262)]
out.append(f'      <g class="agent-node node-aegis active" id="node-aegis" data-mode="none" transform="translate({C[0]},{C[1]})" '
           'tabindex="0" role="button" aria-label="AEGIS, the coordinator" aria-pressed="true" '
           'onclick="selectAgent(\'aegis\')" onkeydown="if(event.key===\'Enter\')selectAgent(\'aegis\')">')
out.append('        <title>AEGIS combines the four specialists. Three strained systems at once is a cascade.</title>')
for i, (a0, a1) in enumerate(segs):
    out.append(f'        <path class="cm-seg ok" id="cm-seg-{i}" d="{arc(a0, a1)}"/>')
out.append('        <circle class="cm-core" r="50"/>')
out.append('        <text class="cm-small" y="-20" text-anchor="middle" style="fill:var(--aegis)">◈ AEGIS</text>')
out.append('        <text class="cm-big" id="cm-big" y="6" text-anchor="middle">Steady</text>')
out.append('        <text class="cm-small" id="cm-sub" y="24" text-anchor="middle">ALL SYSTEMS</text>')
out.append('      </g>')
for k, label, icon, x, y, aria in NODES:
    out.append(f'      <g class="agent-node node-{k}" id="node-{k}" data-state="ok" transform="translate({x},{y})" tabindex="0" '
               f'role="button" aria-label="{aria}" aria-pressed="false" onclick="selectAgent(\'{k}\')" '
               f'onkeydown="if(event.key===\'Enter\')selectAgent(\'{k}\')">')
    out.append(f'        <title id="cm-tip-{k}">{label}</title>')
    out.append('        <circle class="cm-halo" r="52"/>')
    out.append(f'        <circle class="cm-track" r="{R_GAUGE}"/>')
    out.append(f'        <circle class="cm-gauge" id="cm-gauge-{k}" r="{R_GAUGE}" transform="rotate(-90)" '
               f'stroke-dasharray="0 {CIRC:.1f}"/>')
    out.append(f'        <line class="cm-tick" x1="0" y1="-{R_GAUGE + 6}" x2="0" y2="-{R_GAUGE - 6}"/>')
    out.append('        <circle class="cm-core" r="37"/>')
    out.append(f'        <text class="cm-icon" y="-13" text-anchor="middle">{icon}</text>')
    out.append(f'        <text class="cm-val" id="cm-val-{k}" y="9" text-anchor="middle">--</text>')
    out.append(f'        <text class="cm-unit" id="cm-unit-{k}" y="22" text-anchor="middle"></text>')
    out.append(f'        <text class="cm-name" y="63" text-anchor="middle">{label}</text>')
    out.append(f'        <text class="cm-state" id="cm-state-{k}" y="77" text-anchor="middle">steady</text>')
    out.append('      </g>')
out.append('    </svg>')
out.append('    <p class="cm-caption" id="cm-caption" aria-live="polite">Reading the crew\'s live data…</p>')
out.append('  </div>')
markup = "\n".join(out)

src = F.read_text(encoding="utf-8")
s = src.index('  <!-- Agent Network Graph -->\n  <div class="graph-panel">')
e = src.index('  <!-- Vitals Panel -->')
src = src[:s] + '  <!-- Cascade Map (hero): live state of the four specialists around AEGIS -->\n' + markup + "\n\n" + src[e:]
F.write_text(src, encoding="utf-8")
print("spliced", len(markup), "chars; circumference", round(CIRC, 2))
