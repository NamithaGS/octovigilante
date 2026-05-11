/* ─── HealthCheck AI Frontend ─────────────────────────────────────────────── */

let currentJobId = null;
let eventSource = null;
let finalResult = null;

// ─── Entry Point ──────────────────────────────────────────────────────────────

async function startAnalysis() {
  const url = document.getElementById('videoUrl').value.trim();
  if (!url) {
    shakeInput();
    return;
  }

  // Reset state
  finalResult = null;
  document.getElementById('analyzeBtn').disabled = true;

  // Show progress, hide others
  show('progressSection');
  hide('inputSection');
  hide('resultsSection');
  hide('errorSection');

  clearAgentLog();
  setProgress(2, 'Sending request...');

  try {
    const response = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });

    const data = await response.json();

    if (!response.ok || data.error) {
      showError(data.error || 'Failed to start analysis.');
      return;
    }

    currentJobId = data.job_id;
    connectSSE(currentJobId);

  } catch (err) {
    showError('Network error. Please check your connection and try again.');
    console.error(err);
  }
}

// ─── SSE Connection ───────────────────────────────────────────────────────────

function connectSSE(jobId) {
  if (eventSource) {
    eventSource.close();
  }

  eventSource = new EventSource(`/api/stream/${jobId}`);

  eventSource.onmessage = (e) => {
    const event = JSON.parse(e.data);
    handleProgressEvent(event);
  };

  eventSource.onerror = () => {
    eventSource.close();
    // Try to fetch result anyway
    fetchFinalResult(jobId);
  };
}

function handleProgressEvent(event) {
  const { stage, message, progress_pct, data } = event;

  if (message) {
    setProgress(progress_pct || 0, message);
    addAgentLog(message, ['claims_extracted', 'research_progress', 'deep_research_complete', 'scoring_complete', 'complete'].includes(stage));
  }

  if (stage === 'complete' && data) {
    eventSource && eventSource.close();
    finalResult = data;
    setTimeout(() => renderResults(data), 400);
    return;
  }

  if (stage === 'error') {
    eventSource && eventSource.close();
    showError(message || 'An error occurred during analysis.');
    return;
  }

  if (stage === 'keepalive') return;
}

async function fetchFinalResult(jobId) {
  try {
    const resp = await fetch(`/api/result/${jobId}`);
    const data = await resp.json();
    if (data.status === 'complete' && data.result) {
      finalResult = data.result;
      renderResults(data.result);
    } else if (data.status === 'error') {
      showError(data.error || 'Analysis failed.');
    }
  } catch (err) {
    showError('Failed to retrieve results.');
  }
}

// ─── Progress UI ─────────────────────────────────────────────────────────────

function setProgress(pct, message) {
  document.getElementById('progressFill').style.width = `${Math.max(pct, 2)}%`;
  document.getElementById('progressPct').textContent = `${Math.round(pct)}%`;
  if (message) {
    document.getElementById('progressMessage').textContent = message;
  }
}

function addAgentLog(msg, highlight = false) {
  const log = document.getElementById('agentLog');
  const entry = document.createElement('div');
  entry.className = 'agent-log-entry' + (highlight ? ' highlight' : '');
  entry.textContent = msg;
  log.appendChild(entry);
  log.scrollTop = log.scrollHeight;
}

function clearAgentLog() {
  document.getElementById('agentLog').innerHTML = '';
}

// ─── Results Rendering ────────────────────────────────────────────────────────

function renderResults(result) {
  hide('progressSection');
  show('resultsSection');
  document.getElementById('analyzeBtn').disabled = false;

  renderOverview(result);
  renderClaims(result.claims || []);
}

function renderOverview(result) {
  const el = document.getElementById('overviewCard');
  const score = result.video_trustworthiness_score || 50;
  const color = scoreColor(score);
  const circumference = 2 * Math.PI * 30;
  const offset = circumference - (score / 100) * circumference;

  el.innerHTML = `
    <div class="overview-title">Analysis Complete</div>
    <div class="video-title">${escHtml(result.title || 'Video Analysis')}</div>

    <div class="overview-meta">
      <div class="overview-badge">
        <span class="badge-label">Claims analyzed</span>
        <span class="badge-val" style="color:var(--cyan)">${result.claims_analyzed || 0}</span>
      </div>
      <div class="overview-badge">
        <span class="badge-label">Overall</span>
        <span class="badge-val">${escHtml(result.overall_assessment || 'Mixed')}</span>
      </div>
      <div class="overview-badge">
        <span class="badge-label">Analyzed in</span>
        <span class="badge-val">${result.elapsed_seconds || '?'}s</span>
      </div>
    </div>

    <div class="trust-score-wrap">
      <div class="trust-ring">
        <svg viewBox="0 0 72 72" width="72" height="72">
          <circle class="trust-ring-bg" cx="36" cy="36" r="30" stroke-width="5"/>
          <circle class="trust-ring-fill" cx="36" cy="36" r="30" stroke-width="5"
            stroke="${color}"
            stroke-dasharray="${circumference}"
            stroke-dashoffset="${offset}"
          />
        </svg>
        <div class="trust-ring-label">
          <span class="trust-pct" style="color:${color}">${score}%</span>
          <span class="trust-ring-sublabel">trust</span>
        </div>
      </div>
      <div class="trust-info">
        <div class="trust-verdict">${escHtml(result.overall_video_verdict || '')}</div>
        <div class="trust-detail">${escHtml(result.overall_context || '')}</div>
      </div>
    </div>

    ${result.top_recommendation ? `
      <div class="top-recommendation">
        <strong>Key takeaway:</strong> ${escHtml(result.top_recommendation)}
      </div>
    ` : ''}
  `;
}

function renderClaims(claims) {
  const container = document.getElementById('claimsContainer');
  container.innerHTML = `
    <h2 class="claims-section-title">Claim Verification</h2>
    ${claims.map((claim, i) => renderClaimCard(claim, i)).join('')}
    <div class="reset-wrap">
      <button class="reset-btn" onclick="resetApp()">← Analyze another video</button>
    </div>
  `;
}

function renderClaimCard(claim, i) {
  const score = claim.confidence_score || 0;
  const color = scoreColor(score);
  const verdict = claim.verdict || 'Needs Context';
  const verdictColor = verdictToColor(verdict);
  const circumference = 2 * Math.PI * 24;
  const offset = circumference - (score / 100) * circumference;

  const supportingCount = (claim.supporting_evidence || []).length + (claim.meta_analyses || []).length;
  const contradictingCount = (claim.contradicting_evidence || []).length;
  const pubmedCount = (claim.pubmed_articles || []).length;
  const guidelineCount = (claim.authoritative_guidelines || []).length;

  return `
    <div class="claim-card" id="claim-${claim.id}">
      <div class="claim-header" onclick="toggleClaim(${claim.id})">
        <span class="claim-number">CLAIM ${String(i + 1).padStart(2, '0')}</span>
        <div class="claim-main">
          <div class="claim-text">${escHtml(claim.claim || '')}</div>
          <div class="claim-chips">
            <span class="chip chip-category">${escHtml(formatCategory(claim.category))}</span>
            <span class="chip chip-strength">${escHtml(claim.assertion_strength || 'moderate')} assertion</span>
          </div>
        </div>
        <div class="claim-score-wrap">
          <div class="score-ring">
            <svg viewBox="0 0 56 56" width="56" height="56">
              <circle class="score-ring-bg" cx="28" cy="28" r="24" stroke-width="4"/>
              <circle class="score-ring-fill" cx="28" cy="28" r="24" stroke-width="4"
                stroke="${color}"
                stroke-dasharray="${circumference}"
                stroke-dashoffset="${offset}"
              />
            </svg>
            <div class="score-ring-label">
              <span class="score-pct" style="color:${color}">${score}%</span>
            </div>
          </div>
          <div class="verdict-badge" style="background:${verdictColor}22; color:${verdictColor}; border:1px solid ${verdictColor}44">
            ${escHtml(verdict)}
          </div>
        </div>
        <span class="toggle-icon">›</span>
      </div>

      <div class="claim-body">
        <div class="claim-body-inner">

          <!-- Verdict summary -->
          <div class="verdict-row">
            <span class="verdict-icon">${verdictIcon(verdict)}</span>
            <div class="verdict-text">
              <div class="verdict-label">Verdict</div>
              <div class="verdict-summary">${escHtml(claim.verdict_summary || '')}</div>
              <div class="recommended-action">
                Action: <strong>${escHtml(claim.recommended_action || 'Consult doctor first')}</strong>
              </div>
            </div>
            <div>
              <div class="grade-wrap">
                <span class="grade-label">Evidence Grade</span>
                <span class="grade-badge grade-${claim.evidence_grade || 'C'}">${claim.evidence_grade || 'C'}</span>
              </div>
            </div>
          </div>

          <!-- Key points -->
          ${renderKeyPoints(claim)}

          <!-- Caveats -->
          ${renderCaveats(claim)}

          <!-- Red flags -->
          ${renderRedFlags(claim)}

          <!-- Evidence dropdowns -->
          ${renderEvidenceDropdown(claim.id, 'supporting', claim.supporting_evidence || [], claim.meta_analyses || [], supportingCount)}
          ${renderEvidenceDropdown(claim.id, 'contradicting', claim.contradicting_evidence || [], [], contradictingCount)}
          ${pubmedCount > 0 ? renderPubmedDropdown(claim.id, claim.pubmed_articles || []) : ''}
          ${guidelineCount > 0 ? renderGuidelinesDropdown(claim.id, claim.authoritative_guidelines || []) : ''}

          <!-- Quote from video -->
          ${claim.quote ? `
            <div style="background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:var(--radius);padding:14px 16px;margin-top:16px;">
              <div style="font-size:11px;font-family:var(--font-mono);color:var(--text-dimmer);text-transform:uppercase;letter-spacing:0.08em;margin-bottom:6px;">From the video</div>
              <div style="font-size:13px;color:var(--text-dim);line-height:1.7;font-style:italic;">"${escHtml(claim.quote)}"</div>
            </div>
          ` : ''}

        </div>
      </div>
    </div>
  `;
}

function renderKeyPoints(claim) {
  const sup = claim.key_supporting_points || [];
  const con = claim.key_contradicting_points || [];
  if (!sup.length && !con.length) return '';

  return `
    <div class="points-row">
      <div class="points-box supporting">
        <div class="points-box-title">✓ Supporting points</div>
        ${sup.length ? sup.map(p => `
          <div class="point-item">
            <span class="point-dot" style="color:var(--green)">·</span>
            <span>${escHtml(p)}</span>
          </div>
        `).join('') : '<div class="point-item" style="color:var(--text-dimmer)">None found</div>'}
      </div>
      <div class="points-box contradicting">
        <div class="points-box-title">✗ Contradicting points</div>
        ${con.length ? con.map(p => `
          <div class="point-item">
            <span class="point-dot" style="color:var(--red)">·</span>
            <span>${escHtml(p)}</span>
          </div>
        `).join('') : '<div class="point-item" style="color:var(--text-dimmer)">None found</div>'}
      </div>
    </div>
  `;
}

function renderCaveats(claim) {
  const caveats = claim.notable_caveats || [];
  if (!caveats.length) return '';
  return `
    <div class="caveats-wrap">
      <div class="caveats-title">⚠ Important caveats</div>
      ${caveats.map(c => `<div class="caveat-item"><span>·</span><span>${escHtml(c)}</span></div>`).join('')}
    </div>
  `;
}

function renderRedFlags(claim) {
  const flags = claim.red_flags || [];
  if (!flags.length) return '';
  return `
    <div class="red-flags-wrap">
      <div class="red-flags-title">⛔ Red flags</div>
      ${flags.map(f => `<div class="caveat-item"><span>·</span><span>${escHtml(formatFlag(f))}</span></div>`).join('')}
    </div>
  `;
}

function renderEvidenceDropdown(claimId, type, items, extraItems = [], count) {
  const allItems = [...items, ...extraItems];
  if (!allItems.length) return '';

  const isSupporting = type === 'supporting';
  const color = isSupporting ? 'var(--green)' : 'var(--red)';
  const icon = isSupporting ? '✓' : '✗';
  const label = isSupporting ? 'Supporting Evidence' : 'Contradicting Evidence';
  const domId = `ev-${claimId}-${type}`;

  return `
    <div class="evidence-section" id="${domId}">
      <button class="evidence-toggle" onclick="toggleEvidence('${domId}')">
        <span class="evidence-toggle-left">
          <span style="color:${color}">${icon}</span>
          <span>${label}</span>
          <span class="evidence-count">${allItems.length} source${allItems.length !== 1 ? 's' : ''}</span>
        </span>
        <span class="evidence-toggle-arrow">›</span>
      </button>
      <div class="evidence-items">
        ${allItems.map(ev => renderEvidenceItem(ev)).join('')}
      </div>
    </div>
  `;
}

function renderPubmedDropdown(claimId, articles) {
  const domId = `ev-${claimId}-pubmed`;
  return `
    <div class="evidence-section" id="${domId}">
      <button class="evidence-toggle" onclick="toggleEvidence('${domId}')">
        <span class="evidence-toggle-left">
          <span style="color:var(--blue)">📄</span>
          <span>PubMed Articles</span>
          <span class="evidence-count">${articles.length} article${articles.length !== 1 ? 's' : ''}</span>
        </span>
        <span class="evidence-toggle-arrow">›</span>
      </button>
      <div class="evidence-items">
        ${articles.map(art => `
          <div class="evidence-item">
            <div class="evidence-source-row">
              <span class="evidence-quality-tag quality-review">PubMed</span>
              <span class="evidence-title">${escHtml(art.title || '')}</span>
            </div>
            <div class="evidence-meta">${escHtml(art.journal || '')} · ${escHtml(art.year || '')} · ${escHtml(art.authors || '')}</div>
            <a class="evidence-link" href="${escHtml(art.abstract_url || '#')}" target="_blank" rel="noopener">
              View on PubMed ↗
            </a>
          </div>
        `).join('')}
      </div>
    </div>
  `;
}

function renderGuidelinesDropdown(claimId, guidelines) {
  const domId = `ev-${claimId}-guidelines`;
  return `
    <div class="evidence-section" id="${domId}">
      <button class="evidence-toggle" onclick="toggleEvidence('${domId}')">
        <span class="evidence-toggle-left">
          <span style="color:var(--purple)">🏛</span>
          <span>Authoritative Guidelines</span>
          <span class="evidence-count">${guidelines.length}</span>
        </span>
        <span class="evidence-toggle-arrow">›</span>
      </button>
      <div class="evidence-items">
        ${guidelines.map(g => `
          <div class="evidence-item">
            <div class="evidence-source-row">
              <span class="evidence-quality-tag quality-guideline">${escHtml(g.body || 'Guideline')}</span>
              <span class="evidence-title">${escHtml(g.body || '')} Stance</span>
            </div>
            <div class="evidence-finding">${escHtml(g.stance || '')}</div>
            ${g.url ? `<a class="evidence-link" href="${escHtml(g.url)}" target="_blank" rel="noopener">View guideline ↗</a>` : ''}
          </div>
        `).join('')}
      </div>
    </div>
  `;
}

function renderEvidenceItem(ev) {
  const quality = (ev.evidence_quality || 'review').replace('-', '_');
  return `
    <div class="evidence-item">
      <div class="evidence-source-row">
        <span class="evidence-quality-tag quality-${quality}">${escHtml(ev.evidence_quality || 'review')}</span>
        <span class="evidence-title">${escHtml(ev.title || ev.source || '')}</span>
      </div>
      <div class="evidence-meta">
        ${escHtml(ev.source || '')} · ${escHtml(ev.year || '')}
        ${ev.sample_size ? ` · ${escHtml(ev.sample_size)}` : ''}
      </div>
      <div class="evidence-finding">${escHtml(ev.finding || '')}</div>
      ${ev.url && ev.url !== 'null' ? `
        <a class="evidence-link" href="${escHtml(ev.url)}" target="_blank" rel="noopener">
          View source ↗
        </a>
      ` : ''}
    </div>
  `;
}

// ─── Toggle Handlers ──────────────────────────────────────────────────────────

function toggleClaim(claimId) {
  const card = document.getElementById(`claim-${claimId}`);
  card.classList.toggle('open');
}

function toggleEvidence(domId) {
  const section = document.getElementById(domId);
  section.classList.toggle('open');
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function scoreColor(score) {
  if (score >= 75) return 'var(--green)';
  if (score >= 55) return 'var(--cyan)';
  if (score >= 35) return 'var(--yellow)';
  if (score >= 20) return 'var(--orange)';
  return 'var(--red)';
}

function verdictToColor(verdict) {
  const map = {
    'Largely True': 'var(--green)',
    'Partially True': 'var(--cyan)',
    'Needs Context': 'var(--yellow)',
    'Unproven': 'var(--orange)',
    'Misleading': 'var(--red)',
    'False': 'var(--red)',
  };
  return map[verdict] || 'var(--text-dim)';
}

function verdictIcon(verdict) {
  const map = {
    'Largely True': '✅',
    'Partially True': '🔷',
    'Needs Context': '🔶',
    'Unproven': '❓',
    'Misleading': '⚠️',
    'False': '❌',
  };
  return map[verdict] || '🔍';
}

function formatCategory(cat) {
  if (!cat) return 'Health';
  return cat.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function formatFlag(flag) {
  return flag.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function escHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function show(id) {
  document.getElementById(id).classList.remove('hidden');
}

function hide(id) {
  document.getElementById(id).classList.add('hidden');
}

function showError(msg) {
  hide('progressSection');
  hide('resultsSection');
  show('errorSection');
  document.getElementById('errorMessage').textContent = msg;
  document.getElementById('analyzeBtn').disabled = false;
}

function shakeInput() {
  const input = document.getElementById('videoUrl');
  input.style.animation = 'none';
  void input.offsetHeight; // reflow
  input.style.animation = 'shake 0.4s ease';
  setTimeout(() => { input.style.animation = ''; }, 400);
}

function resetApp() {
  if (eventSource) { eventSource.close(); eventSource = null; }
  currentJobId = null; finalResult = null;
  document.getElementById('videoUrl').value = '';
  document.getElementById('analyzeBtn').disabled = false;
  hide('progressSection');
  hide('resultsSection');
  hide('errorSection');
  show('inputSection');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

// Shake animation
const shakeStyle = document.createElement('style');
shakeStyle.textContent = `
  @keyframes shake {
    0%, 100% { transform: translateX(0); }
    20% { transform: translateX(-8px); }
    40% { transform: translateX(8px); }
    60% { transform: translateX(-6px); }
    80% { transform: translateX(6px); }
  }
`;
document.head.appendChild(shakeStyle);

// Allow Enter key to submit
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('videoUrl').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') startAnalysis();
  });
});
