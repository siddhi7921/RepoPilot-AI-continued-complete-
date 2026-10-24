import { createHash } from 'node:crypto';
import { z } from 'zod';
import type { Job } from './investigation.js';
import { setting } from './runtime.js';

const resultSchema = z.object({
  status: z.enum(['PASSED', 'FAILED', 'TIMED_OUT', 'BLOCKED', 'INCONCLUSIVE']),
  tested_commit: z.string(), exit_code: z.number().int().nullable(),
  command: z.literal('python -m pytest -q'),
});
export function runnerConfigured() {
  try {
    const url = new URL(setting('REPOPILOT_RUNNER_URL') || '');
    return url.protocol === 'https:' && !url.username && !url.password && !url.search && !url.hash && !!setting('REPOPILOT_RUNNER_TOKEN');
  } catch { return false; }
}
export async function verify(job: Job): Promise<Job['verification']> {
  const files = job.patch_approved ? job.approved_source_files : job.source_files;
  const hash = createHash('sha256').update(JSON.stringify(Object.entries(files || {}).sort(([a], [b]) => a.localeCompare(b)))).digest('hex');
  const revision = `${job.revision}:${hash}`;
  const base = { status: 'BLOCKED', command: null, tested_commit: revision, exit_code: null, logs: '', details: 'An authenticated HTTPS isolated runner is not configured.' };
  if (!runnerConfigured()) return base;
  if (!files || !Object.keys(files).length) return { ...base, details: 'No source snapshot is available.' };
  try {
    const response = await fetch(`${setting('REPOPILOT_RUNNER_URL')!.replace(/\/$/, '')}/run`, {
      method: 'POST', redirect: 'error', signal: AbortSignal.timeout(40000),
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${setting('REPOPILOT_RUNNER_TOKEN')}` },
      body: JSON.stringify({ revision, source_files: files }),
    });
    if (!response.ok) throw new Error('Runner unavailable');
    const text = await response.text();
    if (text.length > 20000) throw new Error('Oversized runner response');
    const result = resultSchema.parse(JSON.parse(text));
    if (result.tested_commit !== revision || (result.status === 'PASSED' && result.exit_code !== 0)) throw new Error('Invalid result');
    // Repository test output is untrusted and may contain secrets; do not persist it.
    return { ...result, logs: '', details: `Runner returned ${result.status} for the ${job.patch_approved ? 'approved patch' : 'baseline'} snapshot (${job.indexing_status} index). This result does not establish patch correctness or full repository coverage.` };
  } catch {
    return { ...base, details: 'Runner was unavailable, timed out, or returned an invalid result. No test success was recorded.' };
  }
}
