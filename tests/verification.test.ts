import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createReport, ingest } from '../server/investigation.js';
import { verify } from '../server/verification.js';

test('verification fails closed and binds successful results to the approved snapshot', async () => {
  const snapshot = await ingest('demo://session-expiry');
  const job = createReport({ repo_url: snapshot.repo_url, bug_report: 'Expiration boundary regression' }, snapshot, [], null, []);
  const oldFetch = globalThis.fetch;
  const oldUrl = process.env.REPOPILOT_RUNNER_URL;
  const oldToken = process.env.REPOPILOT_RUNNER_TOKEN;
  try {
    delete process.env.REPOPILOT_RUNNER_URL;
    assert.equal((await verify(job)).status, 'BLOCKED');
    process.env.REPOPILOT_RUNNER_URL = 'https://runner.example.test';
    process.env.REPOPILOT_RUNNER_TOKEN = 'test-placeholder';
    let currentRevision = '';
    globalThis.fetch = async (_, options) => {
      const payload = JSON.parse(String(options?.body));
      currentRevision = payload.revision;
      return Response.json({ status: 'PASSED', tested_commit: payload.revision, exit_code: 0, command: 'python -m pytest -q', logs: 'untrusted output' });
    };
    const baseline = await verify(job);
    assert.equal(baseline.status, 'PASSED');
    assert.equal(baseline.logs, '');
    job.patch_approved = true;
    job.approved_source_files = { ...job.source_files, 'src/session_manager.py': '# changed\n' };
    const patched = await verify(job);
    assert.equal(patched.status, 'PASSED');
    assert.notEqual(baseline.tested_commit, patched.tested_commit);
    for (const invalid of [
      { status: 'PASSED', tested_commit: 'wrong', exit_code: 0 },
      { status: 'PASSED', tested_commit: currentRevision, exit_code: 1 },
      { status: 'invented', tested_commit: currentRevision, exit_code: 0 },
    ]) {
      globalThis.fetch = async () => Response.json({ ...invalid, command: 'python -m pytest -q' });
      assert.equal((await verify(job)).status, 'BLOCKED');
    }
    globalThis.fetch = async () => { throw new Error('private diagnostic'); };
    assert.equal((await verify(job)).status, 'BLOCKED');
  } finally {
    globalThis.fetch = oldFetch;
    if (oldUrl === undefined) delete process.env.REPOPILOT_RUNNER_URL; else process.env.REPOPILOT_RUNNER_URL = oldUrl;
    if (oldToken === undefined) delete process.env.REPOPILOT_RUNNER_TOKEN; else process.env.REPOPILOT_RUNNER_TOKEN = oldToken;
  }
});
