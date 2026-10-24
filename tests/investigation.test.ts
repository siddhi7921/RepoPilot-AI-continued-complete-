import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createPatch } from 'diff';
import { ApiError, createReport, githubRepository, ingest, patchedSources, publicReport, rankEvidence, requestSchema, validateAnalysis } from '../server/investigation.js';

const evidence = [{ path: 'src/a.py', line_start: 1, line_end: 2, snippet: 'old\n', matched_terms: ['old'], score: 1 }];
const ref = { file_path: 'src/a.py', line_start: 1, line_end: 1 };
const analysis = { summary: 'A cited summary', summary_citations: [ref], root_cause: 'A cited cause', root_cause_citation: ref,
  hypotheses: [{ description: 'Potential cause', citation: ref, confidence: 0.5, confidence_reason: 'Source evidence' }], patch: '' };

test('accepts canonical public GitHub URLs', () => {
  assert.deepEqual(githubRepository('https://github.com/acme/project.git'), ['acme', 'project']);
});
test('rejects malformed, credentialed, insecure, and extra-path repository URLs', () => {
  for (const url of ['http://github.com/acme/project', 'https://github.com.evil.test/acme/project', 'https://user:pass@github.com/acme/project', 'https://github.com/acme/project/issues', 'https://github.com/acme//project', 'https://github.com/acme/project?ref=main', 'file:///etc/passwd']) {
    assert.throws(() => githubRepository(url), ApiError);
  }
});
test('applies approved patches without losing unchanged tests or trailing spaces', () => {
  const sources = { 'src/a.py': 'old\n', 'tests/test_a.py': 'assert True\n' };
  const result = patchedSources('--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-old\n+new  ', sources, ['src/a.py']);
  assert.equal(result['src/a.py'], 'new  \n');
  assert.equal(result['tests/test_a.py'], 'assert True\n');
  assert.equal(sources['src/a.py'], 'old\n');
});
test('rejects patches outside source evidence, traversal, renames, and invalid hunks', () => {
  const patch = '--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-old\n+new\n';
  assert.throws(() => patchedSources(patch, { 'src/a.py': 'old\n' }, []), ApiError);
  assert.throws(() => patchedSources(patch.replaceAll('src/a.py', '../a.py'), { '../a.py': 'old\n' }, ['../a.py']), ApiError);
  assert.throws(() => patchedSources(patch.replace('+++ b/src/a.py', '+++ b/src/b.py'), { 'src/a.py': 'old\n' }, ['src/a.py']), ApiError);
  assert.throws(() => patchedSources(patch.replace('-old', '-wrong'), { 'src/a.py': 'old\n' }, ['src/a.py']), ApiError);
  assert.throws(() => patchedSources('invalid', { 'src/a.py': 'old\n' }, ['src/a.py']), ApiError);
  assert.throws(() => patchedSources(patch.replace('@@ -1 +1 @@', '@@ -1,10 +1 @@'), { 'src/a.py': 'old\n' }, ['src/a.py']), ApiError);
});
test('handles multi-file patches while preserving the baseline', () => {
  const sources = { 'src/a.py': 'old\n', 'src/b.py': 'before\n' };
  const patch = createPatch('src/a.py', sources['src/a.py'], 'new\n') + createPatch('src/b.py', sources['src/b.py'], 'after\n');
  assert.deepEqual(patchedSources(patch, sources, Object.keys(sources)), { 'src/a.py': 'new\n', 'src/b.py': 'after\n' });
});
test('validates that every AI citation lies within retrieved source ranges', () => {
  assert.equal(validateAnalysis(analysis, evidence).summary, analysis.summary);
  assert.throws(() => validateAnalysis({ ...analysis, root_cause_citation: { ...ref, line_end: 3 } }, evidence));
  assert.throws(() => validateAnalysis({ ...analysis, summary_citations: [{ ...ref, file_path: 'unseen.py' }] }, evidence));
  assert.throws(() => validateAnalysis({ ...analysis, root_cause_citation: { ...ref, line_start: 2, line_end: 1 } }, evidence));
});
test('retrieves bounded evidence with exact source line numbers', () => {
  const source = Array.from({ length: 130 }, (_, i) => `line_${i}`).join('\n');
  const result = rankEvidence({ 'src/a.py': source }, 'line_77');
  assert.equal(result[0].line_start, 51);
  assert.equal(result[0].snippet.split('\n')[27], 'line_77');
  assert.ok(result.every(item => item.snippet.length <= 6000));
});
test('demo snapshot reflects the included corrected expiration guard', async () => {
  const snapshot = await ingest('demo://session-expiry');
  assert.equal(snapshot.mode, 'demo');
  assert.match(snapshot.source_files['src/session_manager.py'], /now >= expires_at/);
  assert.match(snapshot.revision, /^[a-f\d]{64}$/);
  const payload = requestSchema.parse({ repo_url: snapshot.repo_url, bug_report: 'Session expiration boundary is behaving incorrectly.' });
  const job = createReport(payload, snapshot, [], null, ['No AI analysis.']);
  assert.equal(job.verification.status, 'NOT_RUN');
  assert.equal(job.patch, '');
  assert.ok(!('source_files' in publicReport(job)));
  assert.ok(!('approved_source_files' in publicReport(job)));
});
test('invalid investigation input produces validation errors', () => {
  assert.equal(requestSchema.safeParse({ repo_url: 'x', bug_report: 'short' }).success, false);
});
test('GitHub ingestion pins commits and excludes symlinks and dependency directories', async () => {
  const originalFetch = globalThis.fetch;
  const sha = 'a'.repeat(40), blob = 'b'.repeat(40);
  const paths: string[] = [];
  globalThis.fetch = async input => {
    const url = String(input); paths.push(url);
    let body: unknown;
    if (url.endsWith('/repos/acme/project')) body = { default_branch: 'main', private: false, full_name: 'acme/project' };
    else if (url.endsWith('/commits/main')) body = { sha };
    else if (url.includes('/git/trees/')) body = { tree: [
      { type: 'blob', mode: '100644', path: 'src/a.py', sha: blob, size: 4 },
      { type: 'blob', mode: '120000', path: 'src/link.py', sha: blob, size: 4 },
      { type: 'blob', mode: '100644', path: 'node_modules/a.js', sha: blob, size: 4 },
    ] };
    else body = { encoding: 'base64', content: Buffer.from('old\n').toString('base64') };
    return Response.json(body);
  };
  try {
    const result = await ingest('https://github.com/acme/project');
    assert.equal(result.revision, sha);
    assert.deepEqual(Object.keys(result.source_files), ['src/a.py']);
    assert.ok(paths.some(path => path.endsWith(`/git/trees/${sha}?recursive=1`)));
  } finally { globalThis.fetch = originalFetch; }
});
test('GitHub rate limits and missing repositories remain errors instead of fabricated demo results', async () => {
  const originalFetch = globalThis.fetch;
  try {
    for (const status of [404, 429]) {
      globalThis.fetch = async () => Response.json({}, { status });
      await assert.rejects(ingest('https://github.com/acme/project'), (error: unknown) => error instanceof ApiError && error.status === status);
    }
  } finally { globalThis.fetch = originalFetch; }
});
