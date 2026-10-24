import { login, signup, logout, getUser, handleAuthCallback, requestPasswordRecovery, updateUser, acceptInvite } from '/identity.js';

const form = document.getElementById('investigationForm');
const summaryBox = document.getElementById('jobSummary');
const outputBox = document.getElementById('jobOutput');
const statusBox = document.getElementById('serviceStatus');

function errorDetail(detail, fallback) {
  if (Array.isArray(detail)) return detail.map(item => item.msg || 'Invalid input').join('; ');
  return typeof detail === 'string' ? detail : fallback;
}
async function readJson(response) {
  try { return await response.json(); }
  catch { throw new Error(`The service returned an unexpected response (HTTP ${response.status}). Please retry.`); }
}

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
    const data = await readJson(response);
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
      ? '<p>Reviewed and applied to a disposable source snapshot. The original repository was not changed.</p>'
      : '<button type="button" id="approvePatch">Approve and apply in disposable workspace</button>')
    : '<p>No validated candidate patch is available. Review the analysis and investigation notes below.</p>';
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
    <div class="metric-box"><h3>Investigation notes</h3><ul class="step-list">${(job.errors || []).map(error => `<li>${escapeHtml(error)}</li>`).join('') || '<li>No recorded errors.</li>'}</ul></div>
    <div class="metric-box"><h3>Workflow events</h3><ul class="step-list">${steps}</ul></div>
  `;

  const verifyButton = document.getElementById('verifyJob');
  if (verifyButton) {
    verifyButton.addEventListener('click', async () => {
      verifyButton.disabled = true;
      try {
        const response = await fetch(`/api/jobs/${encodeURIComponent(job.job_id)}/verify`, { method: 'POST' });
        const data = await readJson(response);
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
        const data = await readJson(response);
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
      const data = await readJson(response);
      if (!response.ok) {
        const detail = Array.isArray(data.detail) ? data.detail.map((item) => item.msg || 'Invalid input').join('; ') : data.detail;
        throw new Error(detail || 'The investigation failed');
      }
      renderJob(data);
      await refreshJobs();
    } catch (error) {
      summaryBox.textContent = error.message;
    } finally {
      if (submitButton) submitButton.disabled = false;
    }
  });
}

updateHealthStatus();


const loginForm = document.getElementById('loginForm');
const authStatus = document.getElementById('authStatus');
const resetForm = document.getElementById('resetPasswordForm');
let inviteToken = null;

async function accountAction(action) {
  const buttons = document.querySelectorAll('#loginForm button');
  buttons.forEach(button => { button.disabled = true; });
  authStatus.textContent = 'Working…';
  try {
    const emailInput = document.getElementById('authEmail');
    const passwordInput = document.getElementById('authPassword');
    if (action !== 'logout' && !emailInput.reportValidity()) return;
    if (['login', 'signup'].includes(action) && !passwordInput.reportValidity()) return;
    const email = emailInput.value.trim();
    if (action === 'logout') {
      await logout();
      outputBox.replaceChildren();
      summaryBox.textContent = 'Signed out.';
      authStatus.textContent = 'Signed out.';
    } else if (action === 'recover') {
      await requestPasswordRecovery(email);
      authStatus.textContent = 'Check your email for a password reset link.';
    } else if (action === 'signup') {
      const user = await signup(email, passwordInput.value);
      authStatus.textContent = user.emailVerified ? `Signed in as ${user.email}.` : 'Check your email to confirm your account, then sign in.';
    } else {
      const user = await login(email, passwordInput.value);
      authStatus.textContent = `Signed in as ${user.email}.`;
    }
    passwordInput.value = '';
    await refreshJobs();
    if (action === 'login') await loadLinkedJob();
  } catch (error) { authStatus.textContent = error.message || 'Account request failed. Please retry.'; }
  finally { buttons.forEach(button => { button.disabled = false; }); }
}
loginForm.addEventListener('submit', event => { event.preventDefault(); accountAction('login'); });
document.getElementById('registerAccount').addEventListener('click', () => accountAction('signup'));
document.getElementById('logoutAccount').addEventListener('click', () => accountAction('logout'));
document.getElementById('recoverAccount').addEventListener('click', () => accountAction('recover'));
resetForm.addEventListener('submit', async event => {
  event.preventDefault();
  const button = resetForm.querySelector('button');
  button.disabled = true;
  try {
    const password = document.getElementById('newPassword').value;
    if (inviteToken) await acceptInvite(inviteToken, password);
    else await updateUser({ password });
    inviteToken = null;
    resetForm.reset();
    resetForm.hidden = true;
    authStatus.textContent = 'Password saved. You are signed in.';
    await refreshJobs();
    await loadLinkedJob();
  } catch (error) { authStatus.textContent = error.message; }
  finally { button.disabled = false; }
});

async function refreshJobs() {
  const list = document.getElementById('recentJobs');
  try {
    const user = await getUser();
    if (!user) { list.innerHTML = '<li>Sign in to view your saved investigations.</li>'; return; }
    const response = await fetch('/api/jobs');
    const data = await readJson(response);
    if (!response.ok) throw new Error(errorDetail(data.detail, 'Could not load saved investigations.'));
    list.innerHTML = data.jobs.map(job => `<li><a href="/jobs/${encodeURIComponent(job.job_id)}">${escapeHtml(job.repository_name)}</a> · ${escapeHtml(new Date(job.created_at).toLocaleString())}</li>`).join('') || '<li>No saved investigations yet.</li>';
  } catch (error) { list.textContent = error.message; }
}
async function loadLinkedJob() {
  const match = /^\/jobs\/([a-f\d-]{36})\/?$/.exec(window.location.pathname);
  if (!match) return;
  summaryBox.textContent = 'Loading investigation…';
  try {
    const response = await fetch(`/api/jobs/${match[1]}`);
    const data = await readJson(response);
    if (!response.ok) throw new Error(errorDetail(data.detail, 'Could not load investigation.'));
    renderJob(data);
  } catch (error) { summaryBox.textContent = error.message; }
}

const providerDiagnosticButton = document.getElementById('providerDiagnostic');
const providerStatus = document.getElementById('providerStatus');
providerDiagnosticButton?.addEventListener('click', async () => {
  providerDiagnosticButton.disabled = true;
  providerStatus.textContent = 'Checking configured provider…';
  try {
    const response = await fetch('/api/diagnostics/provider', { method: 'POST' });
    const data = await readJson(response);
    if (!response.ok) throw new Error(errorDetail(data.detail, 'AI connection could not be confirmed.'));
    providerStatus.textContent = `Connected to ${data.provider} (${data.model}).`;
  } catch (error) {
    providerStatus.textContent = error.message;
  } finally {
    providerDiagnosticButton.disabled = false;
  }
});

async function updateAccountStatus() {
  try {
    const callback = await handleAuthCallback();
    if (callback?.type === 'invite') { inviteToken = callback.token; resetForm.hidden = false; authStatus.textContent = 'Set a password to accept your invitation.'; }
    else if (callback?.type === 'recovery') { resetForm.hidden = false; authStatus.textContent = 'Choose a new password below.'; }
    else {
      const user = await getUser();
      if (user) authStatus.textContent = `Signed in as ${user.email}.`;
    }
    await refreshJobs();
    await loadLinkedJob();
  } catch (error) { authStatus.textContent = error.message || 'Account session could not be restored.'; }
}
updateAccountStatus();
