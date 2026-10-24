const form = document.getElementById('investigationForm');
const summaryBox = document.getElementById('jobSummary');
const outputBox = document.getElementById('jobOutput');
const statusBox = document.getElementById('serviceStatus');

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (character) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[character]);
}

async function updateHealthStatus() {
  try {
    const response = await fetch('/api/health');
    const data = await response.json();
    statusBox.textContent = response.ok && data.status === 'ok' ? 'Service online' : 'Service degraded';
  } catch {
    statusBox.textContent = 'Service offline';
  }
}

function renderJob(job) {
  const verification = job.verification || {};
  const evidence = (job.evidence || []).map((item) => `
    <li><strong>${escapeHtml(item.path)}:${escapeHtml(item.line_start)}-${escapeHtml(item.line_end)}</strong>
      <div>${escapeHtml(item.matched_terms?.join(', ') || '')}</div>
      <pre>${escapeHtml(item.snippet)}</pre>
    </li>
  `).join('');
  const steps = (job.events || job.steps || []).map((step) =>
    `<li><strong>${escapeHtml(step.stage || step.name)}</strong> — ${escapeHtml(step.status)}: ${escapeHtml(step.detail || step.evidence)}</li>`
  ).join('');
  const hypotheses = (job.hypotheses || []).map((item) =>
    `<li>${escapeHtml(item.description)} — ${escapeHtml(item.citation?.file_path)}:${escapeHtml(item.citation?.line_start)}-${escapeHtml(item.citation?.line_end)} (${escapeHtml(item.confidence)})</li>`
  ).join('');
  const summaryCitations = (job.summary_citations || []).map((item) =>
    `${escapeHtml(item.file_path)}:${escapeHtml(item.line_start)}-${escapeHtml(item.line_end)}`
  ).join(', ');
  const rootCitation = job.root_cause_citation
    ? `${escapeHtml(job.root_cause_citation.file_path)}:${escapeHtml(job.root_cause_citation.line_start)}-${escapeHtml(job.root_cause_citation.line_end)}`
    : '';
  const patchAction = job.patch
    ? (job.patch_approved
      ? '<p>Reviewed and applied to a disposable workspace. The original repository was not changed.</p>'
      : '<button type="button" id="approvePatch">Approve and apply in disposable workspace</button>')
    : '<p>No candidate patch available. Configure a model provider to generate one.</p>';
  const verifyAction = verification.status === 'PASSED'
    ? '<p>Tests passed for the recorded snapshot.</p>'
    : verification.status === 'RUNNING'
      ? '<p>Verification is currently running.</p>'
      : `<button type="button" id="verifyJob">${verification.status && verification.status !== 'NOT_RUN' ? 'Retry verification' : 'Run verification'}</button>`;

  summaryBox.textContent = job.summary;
  outputBox.innerHTML = `
    <div class="metric-box">
      <h3>Repository</h3>
      <div>${escapeHtml(job.repository_name || job.repo_url)}</div>
      <div>Mode: ${escapeHtml(job.repository_mode)} · Revision: <code>${escapeHtml(job.revision)}</code></div>
      <div>Indexed ${escapeHtml(job.file_count)} files (${escapeHtml(job.indexing_status)})</div>
      <a href="/jobs/${encodeURIComponent(job.job_id)}">Open persistent investigation details</a>
    </div>
    <div class="metric-box">
      <h3>Verification</h3>
      <p>Status: <strong>${escapeHtml(verification.status || 'NOT_RUN')}</strong></p>
      <p>Command: ${escapeHtml(verification.command || 'Not executed')}</p>
      <p>${escapeHtml(verification.details)}</p>
      ${verifyAction}
    </div>
    <div class="metric-box"><h3>Root cause</h3><p>${escapeHtml(job.root_cause || 'No model-generated root cause is available.')}</p><small>${rootCitation}</small></div>
    <div class="metric-box"><h3>Summary source</h3><small>${summaryCitations || 'No model summary citations.'}</small></div>
    <div class="metric-box"><h3>Hypotheses</h3><ul class="step-list">${hypotheses || '<li>No hypotheses available.</li>'}</ul></div>
    <div class="metric-box"><h3>Retrieved source evidence</h3><ul class="step-list">${evidence || '<li>No relevant source section matched.</li>'}</ul></div>
    <div class="metric-box"><h3>Candidate patch</h3><pre>${escapeHtml(job.patch || 'No patch generated.')}</pre>${patchAction}</div>
    <div class="metric-box"><h3>Workflow events</h3><ul class="step-list">${steps}</ul></div>
  `;

  const verifyButton = document.getElementById('verifyJob');
  if (verifyButton) {
    verifyButton.addEventListener('click', async () => {
      verifyButton.disabled = true;
      try {
        const response = await fetch(`/api/jobs/${encodeURIComponent(job.job_id)}/verify`, { method: 'POST' });
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Verification request failed');
        renderJob(data);
      } catch (error) {
        summaryBox.textContent = error.message;
        verifyButton.disabled = false;
      }
    });
  }

  const approveButton = document.getElementById('approvePatch');
  if (approveButton) {
    approveButton.addEventListener('click', async () => {
      if (!window.confirm('Apply this patch only to a temporary disposable workspace? Your original repository will not be changed.')) return;
      approveButton.disabled = true;
      try {
        const response = await fetch(`/api/jobs/${encodeURIComponent(job.job_id)}/approve-patch`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ approved: true }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Patch approval failed');
        renderJob(data);
      } catch (error) {
        summaryBox.textContent = error.message;
        approveButton.disabled = false;
      }
    });
  }
}

if (form) {
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const formData = new FormData(form);
    const payload = {
      repo_url: formData.get('repo_url'),
      bug_report: formData.get('bug_report'),
      expected_behavior: formData.get('expected_behavior') || '',
      reproduction_steps: formData.get('reproduction_steps') || '',
    };

    const submitButton = form.querySelector('button[type="submit"]');
    if (submitButton) submitButton.disabled = true;
    summaryBox.textContent = 'Investigating repository…';
    outputBox.replaceChildren();

    try {
      const response = await fetch('/api/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok) {
        const detail = Array.isArray(data.detail) ? data.detail.map((item) => item.msg || 'Invalid input').join('; ') : data.detail;
        throw new Error(detail || 'The investigation failed');
      }
      renderJob(data);
    } catch (error) {
      summaryBox.textContent = error.message;
    } finally {
      if (submitButton) submitButton.disabled = false;
    }
  });
}

updateHealthStatus();


// Cookie-based account flow. Session cookies are HttpOnly and are never exposed to JS.
const loginForm = document.getElementById('loginForm');
const authStatus = document.getElementById('authStatus');
async function submitAuth(path) {
  const username = document.getElementById('authUsername').value;
  const password = document.getElementById('authPassword').value;
  const registrationCode = document.getElementById('registrationCode').value;
  const payload = { username, password };
  if (path === '/api/auth/register') payload.registration_code = registrationCode;
  authStatus.textContent = 'Working…';
  try {
    const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Account request failed');
    authStatus.textContent = data.authenticated ? `Signed in as ${data.username}.` : 'Signed out.';
  } catch (error) { authStatus.textContent = error.message; }
}
if (loginForm) loginForm.addEventListener('submit', (event) => { event.preventDefault(); submitAuth('/api/auth/login'); });
document.getElementById('registerAccount')?.addEventListener('click', () => submitAuth('/api/auth/register'));
document.getElementById('logoutAccount')?.addEventListener('click', async () => {
  try { await fetch('/api/auth/logout', { method: 'POST' }); authStatus.textContent = 'Signed out.'; }
  catch { authStatus.textContent = 'Sign-out request failed.'; }
});

const providerDiagnosticButton = document.getElementById('providerDiagnostic');
const providerStatus = document.getElementById('providerStatus');
providerDiagnosticButton?.addEventListener('click', async () => {
  providerDiagnosticButton.disabled = true;
  providerStatus.textContent = 'Checking configured provider…';
  try {
    const response = await fetch('/api/diagnostics/provider', { method: 'POST' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Provider diagnostic failed');
    providerStatus.textContent = `Connected to ${data.provider}; credential accepted for configured model ${data.model}.`;
  } catch (error) {
    providerStatus.textContent = error.message;
  } finally {
    providerDiagnosticButton.disabled = false;
  }
});

async function updateAccountStatus() {
  try {
    const response = await fetch('/api/auth/me');
    if (!response.ok) return;
    const data = await response.json();
    if (data.authenticated && authStatus) authStatus.textContent = `Signed in as ${data.username}.`;
  } catch { /* Health status reports service availability separately. */ }
}
updateAccountStatus();
