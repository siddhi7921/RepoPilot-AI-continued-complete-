import { test } from 'node:test';
import assert from 'node:assert/strict';
import { normalizeLegacy, migrationRequest } from '../server/migration.js';
const row = {
  id: 'legacy-id', username: 'legacy-user', account_created_at: '2025-01-01T00:00:00Z',
  report: {
    job_id: 'legacy-id', repo_url: 'https://github.com/acme/repo', revision: 'abc', created_at: '2025-01-02T00:00:00Z', updated_at: '2025-01-03T00:00:00Z',
    summary: 'Historical finding', root_cause: 'Historical cause', source_files: { 'src/a.py': 'x = 1\n' },
    evidence: [{ path: 'src/a.py', line_start: 1, line_end: 1, snippet: 'x = 1', score: 1 }],
    events: [{ stage: 'analysis', status: 'complete', at: '2025-01-02T00:00:00Z', detail: 'Historical event' }],
  },
};
test('legacy normalization preserves evidence, history, IDs and timestamps without transferring auth or trust', () => {
  const result = normalizeLegacy({ ...row, password_hash: 'discard', session: 'discard', report: { ...row.report, owner: 'spoof', patch_approved: true, approved_source_files: { 'a.py': 'spoof' }, verification: { status: 'PASSED' } } });
  assert.equal(result.job.job_id, row.id);
  assert.equal(result.job.created_at, row.report.created_at);
  assert.equal(result.job.updated_at, row.report.updated_at);
  assert.deepEqual(result.job.source_files, row.report.source_files);
  assert.deepEqual(result.job.events, row.report.events);
  assert.equal(result.job.verification.status, 'NOT_RUN');
  assert.equal(result.job.patch_approved, false);
  assert.equal(result.job.approved_source_files, null);
  assert.ok(!JSON.stringify(result).includes('discard'));
  assert.ok(!('owner' in result.job));
  assert.deepEqual(normalizeLegacy(row), normalizeLegacy(row));
});
test('legacy normalization rejects mismatched IDs, invalid timestamps and unsafe source paths', () => {
  for (const report of [
    { ...row.report, job_id: 'other-id' },
    { ...row.report, created_at: 'invalid' },
    { ...row.report, source_files: { '../escape.py': 'bad' } },
  ]) assert.throws(() => normalizeLegacy({ ...row, report }));
});
test('migration defaults to dry run and requires explicit Identity IDs', () => {
  const base = { source_id: 'a'.repeat(64), records: [row], mappings: {} };
  assert.equal(migrationRequest.parse(base).commit, false);
  assert.equal(migrationRequest.safeParse({ ...base, mappings: { legacy: 'email@example.test' } }).success, false);
});
