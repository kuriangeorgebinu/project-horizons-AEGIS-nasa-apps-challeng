/**
 * AEGIS API Client
 * NASA Space Apps Challenge 2026
 * 
 * Frontend integration layer for the AEGIS backend API.
 * Include this script in aegis-mission-control.html to connect to live data.
 */

const AEGIS_API = {
  BASE_URL: 'http://127.0.0.1:8000',
  
  // State
  crew: [],
  recommendations: [],
  events: [],
  isConnected: false,
  updateInterval: null,
  
  // ─────────────────────────────────────────────────────────────────────────
  // Core API Methods
  // ─────────────────────────────────────────────────────────────────────────
  
  async fetch(endpoint, options = {}) {
    try {
      const response = await fetch(`${this.BASE_URL}${endpoint}`, {
        ...options,
        headers: {
          'Content-Type': 'application/json',
          ...options.headers
        }
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return await response.json();
    } catch (error) {
      console.error(`AEGIS API Error [${endpoint}]:`, error);
      throw error;
    }
  },
  
  // ─────────────────────────────────────────────────────────────────────────
  // Crew Endpoints
  // ─────────────────────────────────────────────────────────────────────────
  
  async getCrew() {
    return await this.fetch('/api/crew');
  },
  
  async getCrewMember(id) {
    return await this.fetch(`/api/crew/${id}`);
  },
  
  async getCrewState() {
    return await this.fetch('/api/crew/state');
  },
  
  // ─────────────────────────────────────────────────────────────────────────
  // RAG Query Endpoints  
  // ─────────────────────────────────────────────────────────────────────────
  
  async query(queryText, nResults = 5) {
    return await this.fetch('/api/query', {
      method: 'POST',
      body: JSON.stringify({ query: queryText, n_results: nResults })
    });
  },
  
  async queryCountermeasures(condition) {
    return await this.fetch(`/api/query/countermeasures?condition=${encodeURIComponent(condition)}`);
  },
  
  // ─────────────────────────────────────────────────────────────────────────
  // Recommendations Endpoints
  // ─────────────────────────────────────────────────────────────────────────
  
  async getRecommendations(filters = {}) {
    const params = new URLSearchParams(filters);
    return await this.fetch(`/api/recommendations?${params}`);
  },
  
  async getPendingRecommendations() {
    return await this.fetch('/api/recommendations/pending');
  },
  
  async acceptRecommendation(id) {
    return await this.fetch(`/api/recommendations/${id}/action`, {
      method: 'POST',
      body: JSON.stringify({ recommendation_id: id, action: 'accepted' })
    });
  },
  
  async deferRecommendation(id) {
    return await this.fetch(`/api/recommendations/${id}/action`, {
      method: 'POST',
      body: JSON.stringify({ recommendation_id: id, action: 'deferred' })
    });
  },
  
  async dismissRecommendation(id) {
    return await this.fetch(`/api/recommendations/${id}/action`, {
      method: 'POST',
      body: JSON.stringify({ recommendation_id: id, action: 'dismissed' })
    });
  },
  
  // ─────────────────────────────────────────────────────────────────────────
  // Analysis Endpoints (AI Agents)
  // ─────────────────────────────────────────────────────────────────────────
  
  async analyzeCrewMember(crewId) {
    return await this.fetch(`/api/analyze/${crewId}`, { method: 'POST' });
  },
  
  async analyzeAllCrew() {
    return await this.fetch('/api/analyze', { method: 'POST' });
  },
  
  async getAnalysisSummary() {
    return await this.fetch('/api/analysis/summary');
  },
  
  // ─────────────────────────────────────────────────────────────────────────
  // Events Endpoints
  // ─────────────────────────────────────────────────────────────────────────
  
  async getEvents(limit = 50) {
    return await this.fetch(`/api/events?limit=${limit}`);
  },
  
  async getAgentInsights() {
    return await this.fetch('/api/events/agent-insights');
  },
  
  // ─────────────────────────────────────────────────────────────────────────
  // Simulation Control
  // ─────────────────────────────────────────────────────────────────────────
  
  async getSimulationState() {
    return await this.fetch('/api/simulation');
  },
  
  async injectAnomaly(type, crewId = null) {
    const url = crewId 
      ? `/api/simulation/inject/${type}?crew_id=${crewId}`
      : `/api/simulation/inject/${type}`;
    return await this.fetch(url, { method: 'POST' });
  },
  
  async clearAnomaly() {
    return await this.fetch('/api/simulation/clear-anomaly', { method: 'POST' });
  },
  
  async setScenario(scenario) {
    return await this.fetch(`/api/simulation/scenario/${scenario}`, { method: 'POST' });
  },
  
  // ─────────────────────────────────────────────────────────────────────────
  // UI Update Methods
  // ─────────────────────────────────────────────────────────────────────────
  
  getStatusColor(status) {
    const colors = {
      'nominal': 'var(--green)',
      'caution': 'var(--yellow)',
      'warning': 'var(--orange)',
      'critical': 'var(--red)'
    };
    return colors[status] || 'var(--muted)';
  },
  
  getVitalColor(value, type) {
    // Returns appropriate color based on vital type and value
    const thresholds = {
      heart_rate: { low: 50, normal: [60, 100], high: 120 },
      spo2: { critical: 90, low: 95, normal: 97 },
      stress_index: { normal: 40, elevated: 70 },
      sleep_quality: { poor: 50, fair: 70 }
    };
    
    if (type === 'heart_rate') {
      if (value < 50 || value > 120) return 'var(--red)';
      if (value < 60 || value > 100) return 'var(--yellow)';
      return 'var(--green)';
    }
    if (type === 'spo2') {
      if (value < 90) return 'var(--red)';
      if (value < 95) return 'var(--yellow)';
      return 'var(--green)';
    }
    if (type === 'stress_index') {
      if (value > 70) return 'var(--red)';
      if (value > 40) return 'var(--yellow)';
      return 'var(--green)';
    }
    if (type === 'sleep_quality') {
      if (value < 50) return 'var(--red)';
      if (value < 70) return 'var(--yellow)';
      return 'var(--green)';
    }
    return 'var(--green)';
  },
  
  // ─────────────────────────────────────────────────────────────────────────
  // Live Data Updates
  // ─────────────────────────────────────────────────────────────────────────
  
  async updateDashboard() {
    try {
      // Fetch latest crew data
      const crew = await this.getCrew();
      this.crew = crew;
      
      // Update crew vitals display
      this.updateCrewVitalsUI(crew);
      
      // Fetch recommendations
      const recs = await this.getPendingRecommendations();
      this.recommendations = recs;
      this.updateRecommendationsUI(recs);
      
      // Update connection status
      this.isConnected = true;
      this.updateConnectionStatus(true);
      
    } catch (error) {
      console.error('Dashboard update failed:', error);
      this.isConnected = false;
      this.updateConnectionStatus(false);
    }
  },
  
  updateCrewVitalsUI(crew) {
    // Update vitals panel if it exists
    const vitalsPanel = document.querySelector('.vitals-panel');
    if (!vitalsPanel) return;
    
    // Find selected crew member or first one
    const selected = crew.find(c => c.id === this.selectedCrewId) || crew[0];
    if (!selected || !selected.vitals) return;
    
    const v = selected.vitals;
    
    // Update HR
    const hrValue = document.getElementById('hr-value');
    if (hrValue) {
      hrValue.textContent = v.heart_rate;
      hrValue.style.color = this.getVitalColor(v.heart_rate, 'heart_rate');
    }
    
    // Update SpO2
    const spo2El = document.querySelector('[data-vital="spo2"]');
    if (spo2El) {
      spo2El.textContent = `${v.spo2.toFixed(0)}%`;
      spo2El.style.color = this.getVitalColor(v.spo2, 'spo2');
    }
    
    // Update stress
    const stressEl = document.querySelector('[data-vital="stress"]');
    if (stressEl && v.stress_index) {
      stressEl.textContent = `${v.stress_index.toFixed(0)}%`;
      stressEl.style.color = this.getVitalColor(v.stress_index, 'stress_index');
    }
  },
  
  updateRecommendationsUI(recs) {
    const recsList = document.querySelector('.recs-list');
    if (!recsList) return;
    
    // Merge duplicates (same crew member + same recommendation) and show up to 8, most urgent first
    const sevRank = { critical: 0, high: 1, moderate: 2, medium: 2, low: 3 };
    const seen = new Set();
    const unique = recs.filter(r => {
      const k = `${r.crew_member_id}|${r.title}`;
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    }).sort((a, b) => (sevRank[a.severity] ?? 9) - (sevRank[b.severity] ?? 9));
    const MAX_SHOWN = 8;
    const countEl = document.getElementById('recs-count');
    if (countEl) {
      countEl.textContent = unique.length > MAX_SHOWN
        ? `Showing ${MAX_SHOWN} of ${unique.length}`
        : (unique.length ? `${unique.length} active` : '');
    }
    if (unique.length === 0) {
      recsList.innerHTML = '<div class="recs-empty">No pending recommendations. AEGIS agents are monitoring the crew.</div>';
      return;
    }
    
    // M7: Map crew IDs to display names
    const crewNames = {
      'crew-001': 'Cmdr. Chen',
      'crew-002': 'D. Okonkwo',
      'crew-003': 'Dr. Kowalski',
      'crew-004': 'Dr. Nakamura'
    };
    
    // DeepSeek assessment (stored by the backend at the start of the rationale). Escaped: model output is untrusted.
    const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const aiHtml = rec => {
      const m = (rec.rationale || '').match(/^🧠 DeepSeek\s*\(([^)]+)\):\s*([\s\S]*?)(?:\n\nRule basis:|$)/);
      return m ? `<div class="rec-ai" title="${esc(m[2].trim())}"><span class="rec-ai-tag">🧠 DeepSeek · ${esc(m[1])}</span>${esc(m[2].trim())}</div>` : '';
    };
    
    // RAG evidence: JSON strings {source, url, text, cited}; legacy entries are plain text snippets
    const parseEv = rec => (rec.evidence || []).map(e => {
      if (typeof e === 'string' && e.startsWith('{')) { try { return JSON.parse(e); } catch (x) { /* fall through */ } }
      return { source: null, text: String(e) };
    });
    const safeUrl = u => (typeof u === 'string' && /^https?:\/\//i.test(u)) ? u : null;
    const stepsHtml = rec => {
      const acts = (rec.actions || []).slice(0, 3);
      return acts.length ? `<ul class="rec-steps">${acts.map(a => `<li>${esc(a)}</li>`).join('')}</ul>` : '';
    };
    const sourcesHtml = rec => {
      const ev = parseEv(rec);
      const structured = ev.filter(e => e.source);
      if (!structured.length) {
        return ev.length ? `<div class="rec-evidence">${esc(ev[0].text.substring(0, 100))}…</div>` : '';
      }
      const cited = structured.filter(e => e.cited);
      const show = (cited.length ? cited : structured).slice(0, 3);
      const items = show.map(e => {
        const label = esc(String(e.source).length > 70 ? String(e.source).slice(0, 68) + '…' : e.source);
        const tip = esc(String(e.text || '').slice(0, 300));
        const url = safeUrl(e.url);
        return url ? `<a href="${esc(url)}" target="_blank" rel="noopener" title="${tip}">${label} ↗</a>`
                   : `<span title="${tip}">${label}</span>`;
      }).join('');
      return `<div class="rec-sources"><span class="rec-sources-tag">${cited.length ? 'Cited sources' : 'Retrieved sources'}</span>${items}</div>`;
    };
    
    // Clear existing and add new
    recsList.innerHTML = unique.slice(0, MAX_SHOWN).map((rec, i) => `
      <div class="rec-card ${rec.severity === 'critical' || rec.severity === 'high' ? 'urgent' : ''}" data-rec-id="${rec.id}">
        <div class="rec-header">
          <div class="rec-agents">
            <span class="rec-agent" style="background:rgba(90,159,215,.2);color:var(--aegis);">${rec.pillar}</span>
            <span class="rec-arrow">→</span>
            <span class="rec-agent" style="background:rgba(200,136,136,.2);color:var(--pulse);">${rec.agent}</span>
          </div>
          <div class="rec-target">👨‍🚀 ${crewNames[rec.crew_member_id] || rec.crew_member_id}</div>
        </div>
        <div class="rec-body">
          <div class="rec-detection">"${rec.description}"</div>
          <div class="rec-intervention">↳ ${rec.title}</div>
          ${aiHtml(rec)}
          ${stepsHtml(rec)}
          ${sourcesHtml(rec)}
        </div>
        <div class="rec-actions">
          <button class="rec-btn accept" onclick="AEGIS_API.acceptRecommendation('${rec.id}').then(() => AEGIS_API.updateDashboard())">✓ Accept</button>
          <button class="rec-btn defer" onclick="AEGIS_API.deferRecommendation('${rec.id}').then(() => AEGIS_API.updateDashboard())">◷ Defer</button>
          <button class="rec-btn dismiss" onclick="AEGIS_API.dismissRecommendation('${rec.id}').then(() => AEGIS_API.updateDashboard())">✕ Dismiss</button>
        </div>
      </div>
    `).join('');
  },
  
  updateConnectionStatus(connected) {
    const indicator = document.getElementById('api-status');
    if (indicator) {
      indicator.textContent = connected ? '🟢 Live' : '🔴 Offline';
      indicator.title = connected 
        ? 'Connected to AEGIS backend' 
        : 'Backend not responding - using demo data';
    }
  },
  
  // ─────────────────────────────────────────────────────────────────────────
  // Initialization
  // ─────────────────────────────────────────────────────────────────────────
  
  async init() {
    console.log('🚀 AEGIS API Client initializing...');
    
    // Check backend health
    try {
      const health = await this.fetch('/health');
      console.log('✅ Backend connected:', health);
      this.isConnected = true;
      
      // Start live updates every 5 seconds
      this.startLiveUpdates(5000);
      
      // Initial dashboard update
      await this.updateDashboard();
      
    } catch (error) {
      console.warn('⚠️ Backend not available, using demo mode');
      this.isConnected = false;
    }
    
    return this.isConnected;
  },
  
  startLiveUpdates(intervalMs = 5000) {
    if (this.updateInterval) clearInterval(this.updateInterval);
    this.updateInterval = setInterval(() => this.updateDashboard(), intervalMs);
    console.log(`📡 Live updates started (${intervalMs/1000}s interval)`);
  },
  
  stopLiveUpdates() {
    if (this.updateInterval) {
      clearInterval(this.updateInterval);
      this.updateInterval = null;
      console.log('📡 Live updates stopped');
    }
  }
};

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => AEGIS_API.init());
} else {
  AEGIS_API.init();
}

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
  module.exports = AEGIS_API;
}
