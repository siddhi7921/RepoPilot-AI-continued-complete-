import { createHash } from 'node:crypto';
import { admin } from '@netlify/identity';
import { and, eq } from 'drizzle-orm';
import { z } from 'zod';
import { database } from '../db/index.js';
import { investigations, legacyAccounts, migrationLog } from '../db/schema.js';
import { ApiError, createReport, safePath } from './investigation.js';

const timestamp = z.string().datetime({ offset: true });
const id = z.string().regex(/^[A-Za-z0-9_-]{1,100}$/);
const citation = z.object({ file_path: z.string().max(500).refine(safePath), line_start: z.number().int().positive(), line_end: z.number().int().positive() });
const event = z.object({ stage: z.string().max(200), status: z.string().max(100), at: timestamp, detail: z.string().max(12000) });
const sources = z.record(z.string().refine(safePath), z.string().max(262144)).refine(value => Object.keys(value).length <= 1000 && JSON.stringify(value).length <= 3000000);
const record = z.object({
  id, username: z.string().min(1).max(100), account_created_at: timestamp,
  report: z.object({
    legacy_history: z.object({ status: z.string().max(100), verification_status: z.string().max(100), patch_was_approved: z.boolean() }).nullable().default(null),
    job_id: id, repo_url: z.string().max(500), revision: z.string().max(200),
    created_at: timestamp, updated_at: timestamp,
    summary: z.string().max(20000), root_cause: z.string().max(20000),
    patch: z.string().max(64000).default(''),
    source_files: sources.default({}),
    evidence: z.array(z.object({ path: z.string().refine(safePath), line_start: z.number().int().positive(), line_end: z.number().int().positive(), snippet: z.string().max(20000), matched_terms: z.array(z.string().max(200)).max(100).default([]), score: z.number().finite().default(0) })).max(1000),
    events: z.array(event).max(2000).default([]), steps: z.array(event).max(2000).default([]),
    indexing_status: z.enum(['complete', 'partial', 'unknown']).default('unknown'),
    summary_citations: z.array(citation).max(100).default([]), root_cause_citation: citation.nullable().default(null),
    hypotheses: z.array(z.object({ description: z.string().max(3000), citation, confidence: z.number().min(0).max(1), confidence_reason: z.string().max(2000) })).max(100).default([]),
    expected_behavior: z.string().max(4000).nullish(), reproduction_steps: z.string().max(4000).nullish(),
  }),
}).refine(value => value.id === value.report.job_id);
export const migrationRequest = z.object({
  source_id: z.string().regex(/^[a-f0-9]{64}$/),
  mappings: z.record(z.string().min(1).max(100), z.string().uuid()),
  records: z.array(z.unknown()).max(5),
  accounts: z.array(z.object({ username: z.string().min(1).max(100), created_at: timestamp })).max(5).default([]),
  commit: z.boolean().default(false), plan_digest: z.string().optional(),
});
function canonical(value: any): any {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
  return value;
}
function digest(value: unknown) { return createHash('sha256').update(JSON.stringify(canonical(value))).digest('hex'); }
export function normalizeLegacy(input: unknown) {
  const row = record.parse(input); // Unknown fields, including passwords and tokens, are discarded.
  const r = row.report;
  const job = createReport({ repo_url: r.repo_url, bug_report: 'Imported legacy investigation', expected_behavior: r.expected_behavior, reproduction_steps: r.reproduction_steps }, {
    repo_url: r.repo_url, repository_name: r.repo_url, revision: r.revision, mode: 'legacy', indexing_status: r.indexing_status, source_files: r.source_files,
  }, r.evidence, null, ['Imported historical analysis requires review; previous approvals and test claims were not trusted.']);
  Object.assign(job, r, { job_id: row.id, events: r.events.length ? r.events : r.steps, steps: r.events.length ? r.events : r.steps, status: 'inconclusive' });
  return { row, job };
}
export async function migrate(input: unknown, actorId: string) {
  const request = migrationRequest.parse(input);
  if (new Set(Object.values(request.mappings)).size !== Object.keys(request.mappings).length) throw new ApiError(409, 'Each legacy account must map to a distinct Identity account.');
  const valid: { row: z.infer<typeof record>; job: ReturnType<typeof createReport>; digest: string; key: string; accountKey: string }[] = [];
  const skipped: { index: number; reason: string }[] = [];
  const seen = new Set<string>();
  for (const [index, raw] of request.records.entries()) {
    try {
      const { row, job } = normalizeLegacy(raw);
      if (seen.has(row.id)) { skipped.push({ index, reason: 'duplicate_in_batch' }); continue; }
      seen.add(row.id);
      if (!Object.hasOwn(request.mappings, row.username)) { skipped.push({ index, reason: 'explicit_mapping_required' }); continue; }
      valid.push({ row, job, digest: digest(row), key: digest([request.source_id, row.id]), accountKey: digest([request.source_id, row.username]) });
    } catch { skipped.push({ index, reason: 'malformed_record' }); }
  }
  const accounts = request.accounts.map(account => {
    if (!Object.hasOwn(request.mappings, account.username)) throw new ApiError(409, 'Explicit mapping required for account metadata.');
    return { key: digest([request.source_id, account.username]), ownerId: request.mappings[account.username], metadata: account };
  });
  // Verify exact IDs against Identity on BOTH dry-run and commit. Never infer ownership from email.
  for (const owner of new Set([...valid.map(item => request.mappings[item.row.username]), ...accounts.map(account => account.ownerId)])) {
    let user;
    try { user = await admin.getUser(owner); } catch { throw new ApiError(409, 'A mapped Identity account could not be verified.'); }
    if (user.id !== owner || !user.confirmedAt) throw new ApiError(409, 'Every mapped Identity account must exist and be confirmed.');
  }
  const db = database();
  const plan: (typeof valid[number] & { owner: string; duplicate: boolean })[] = [];
  for (const item of valid) {
    const owner = request.mappings[item.row.username];
    const [account] = await db.select().from(legacyAccounts).where(eq(legacyAccounts.key, item.accountKey));
    if (account && account.ownerId !== owner) throw new ApiError(409, 'Legacy account mapping conflicts with an earlier import.');
    const [log] = await db.select().from(migrationLog).where(eq(migrationLog.key, item.key));
    const [existing] = await db.select().from(investigations).where(eq(investigations.id, item.row.id));
    if (log && (log.ownerId !== owner || log.digest !== item.digest || !existing || existing.ownerId !== owner || digest(existing.report) !== digest(item.job))) throw new ApiError(409, 'An earlier import differs or has changed. Manual review is required.');
    if (!log && existing) throw new ApiError(409, 'Report ID already exists. No record was overwritten.');
    plan.push({ ...item, owner, duplicate: !!log });
  }
  for (const account of accounts) {
    const [prior] = await db.select().from(legacyAccounts).where(eq(legacyAccounts.key, account.key));
    if (prior && (prior.ownerId !== account.ownerId || digest(prior.metadata) !== digest(account.metadata))) throw new ApiError(409, 'Account metadata or ownership conflicts with an earlier import.');
  }
  const planDigest = digest({ accounts, source: request.source_id, mappings: request.mappings, records: valid.map(item => item.digest), skipped });
  const summary = { accounts: accounts.length, total: request.records.length, eligible: plan.length, already_imported: plan.filter(item => item.duplicate).length, skipped, plan_digest: planDigest };
  if (!request.commit) return { ...summary, committed: 0, verified: false };
  if (request.plan_digest !== planDigest) throw new ApiError(409, 'Run a dry run and confirm its exact plan digest before committing.');
  // A transaction makes the whole batch atomic, including its audit records.
  await db.transaction(async tx => {
    for (const account of accounts) {
      await tx.insert(legacyAccounts).values(account).onConflictDoNothing();
      await tx.insert(migrationLog).values({ key: digest(['account', request.source_id, account.metadata.username]), sourceId: request.source_id, legacyId: 'account:' + digest(account.metadata.username), ownerId: account.ownerId, reportId: '', digest: digest(account.metadata), actorId }).onConflictDoNothing();
      const [saved] = await tx.select().from(legacyAccounts).where(eq(legacyAccounts.key, account.key));
      if (saved.ownerId !== account.ownerId || digest(saved.metadata) !== digest(account.metadata)) throw new ApiError(409, 'Concurrent account metadata conflict.');
    }
    for (const item of plan) {
      if (item.duplicate) continue;
      await tx.insert(legacyAccounts).values({ key: item.accountKey, ownerId: item.owner, metadata: { username: item.row.username, created_at: item.row.account_created_at } }).onConflictDoNothing();
      const [account] = await tx.select().from(legacyAccounts).where(eq(legacyAccounts.key, item.accountKey));
      if (account.ownerId !== item.owner) throw new ApiError(409, 'Concurrent account ownership conflict.');
      await tx.insert(investigations).values({ id: item.row.id, ownerId: item.owner, report: item.job, createdAt: new Date(item.row.report.created_at) });
      await tx.insert(migrationLog).values({ key: item.key, sourceId: request.source_id, legacyId: item.row.id, ownerId: item.owner, reportId: item.row.id, digest: item.digest, actorId });
    }
  });
  for (const account of accounts) {
    const [saved] = await db.select().from(legacyAccounts).where(eq(legacyAccounts.key, account.key));
    if (!saved || saved.ownerId !== account.ownerId || digest(saved.metadata) !== digest(account.metadata)) throw new ApiError(409, 'Account verification failed.');
  }
  for (const item of plan) {
    const [saved] = await db.select().from(investigations).where(and(eq(investigations.id, item.row.id), eq(investigations.ownerId, item.owner)));
    if (!saved || digest(saved.report) !== digest(item.job)) throw new ApiError(409, 'Post-import verification failed. Review audit records before continuing.');
  }
  return { ...summary, committed: plan.filter(item => !item.duplicate).length, verified: true };
}
