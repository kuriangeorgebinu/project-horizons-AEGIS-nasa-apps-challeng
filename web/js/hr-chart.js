let hrHistoryExtended = [68, 67, 69, 66, 68, 70, 69, 71, 68, 67, 65, 68, 70, 72, 69, 67, 68, 66, 69, 68];

function updateExpandedChart() {
  const svg = document.getElementById('expanded-svg');
  if (!svg) return;
  
  const width = 520, height = 160, padding = 30;
  const data = hrHistoryExtended;
  const min = Math.min(...data) - 5;
  const max = Math.max(...data) + 5;
  
  // Build SVG content
  let html = '';
  
  // Grid lines
  for (let i = 0; i <= 4; i++) {
    const y = padding + (height - 2 * padding) * i / 4;
    const val = Math.round(max - (max - min) * i / 4);
    html += `<line class="chart-grid" x1="${padding}" y1="${y}" x2="${width - padding}" y2="${y}"/>`;
    html += `<text class="chart-label" x="${padding - 5}" y="${y + 4}" text-anchor="end">${val}</text>`;
  }
  
  // Line and points
  const points = data.map((v, i) => {
    const x = padding + (width - 2 * padding) * i / (data.length - 1);
    const y = padding + (height - 2 * padding) * (1 - (v - min) / (max - min));
    return { x, y, v };
  });
  
  const linePoints = points.map(p => `${p.x},${p.y}`).join(' ');
  html += `<polyline class="chart-line" points="${linePoints}"/>`;
  
  // Dots with values
  points.forEach((p, i) => {
    html += `<circle class="chart-dot" cx="${p.x}" cy="${p.y}" r="4"/>`;
    if (i % 3 === 0 || i === data.length - 1) {
      html += `<text class="chart-value" x="${p.x}" y="${p.y - 10}" text-anchor="middle">${p.v}</text>`;
    }
  });
  
  // X-axis labels (time)
  for (let i = 0; i < data.length; i += 5) {
    const x = padding + (width - 2 * padding) * i / (data.length - 1);
    html += `<text class="chart-label" x="${x}" y="${height - 5}" text-anchor="middle">-${(data.length - 1 - i) * 3}s</text>`;
  }
  
  svg.innerHTML = html;
  
  // Update stats
  const avg = Math.round(data.reduce((a, b) => a + b, 0) / data.length);
  const minVal = Math.min(...data);
  const maxVal = Math.max(...data);
  document.getElementById('hr-avg').textContent = avg;
  document.getElementById('hr-min').textContent = minVal;
  document.getElementById('hr-max').textContent = maxVal;
}

// Update extended history when vitals animate
const origAnimateVitalsLocal = animateVitalsLocal;
animateVitalsLocal = function() {
  origAnimateVitalsLocal();
  const newHR = parseInt(document.getElementById('hr-value').textContent);
  hrHistoryExtended.push(newHR);
  if (hrHistoryExtended.length > 20) hrHistoryExtended.shift();
  if (document.getElementById('hrModal').classList.contains('open') && !TREND_OPEN) {
    updateExpandedChart();
  }
};

// Close modal on escape
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && document.getElementById('hrModal').classList.contains('open')) {
    closeHRGraph();
  }
  if (e.key === 'Escape' && document.getElementById('whatifModal').classList.contains('open')) {
    closeWhatIf();
  }
});

