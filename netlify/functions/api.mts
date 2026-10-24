import type { Config } from '@netlify/functions';
import { getUser } from '@netlify/identity';
import { and, desc, eq, sql } from 'drizzle-orm';
import { ZodError } from 'zod';
import OpenAI from 'openai';
import { database } from '../../db/index.js';
import { investigations } from '../../db/schema.js';
import { ApiError, investigate, model, patchedSources, publicReport, requestSchema } from '../../server/investigation.js';

function json(data: unknown, status = 200) {
  return Response.json(data, { status, headers: { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' } });
}
async function readBody(req: Request) {
  if (!req.headers.get('content-type')?.includes('application/json')) throw new ApiError(415, 'Send a JSON request.');
  const text = await req.text();
  if (text.length > 30000) throw new ApiError(413, 'Request is too large.');
  try { return JSON.parse(text); } catch { throw new ApiError(400, 'Request body must contain valid JSON.'); }
}
export default async (req: Request) => {
  try {
    const path = new URL(req.url).pathname.replace(/\/$/, '');
    if (!['GET', 'POST'].includes(req.method)) return json({ detail: 'Method not allowed.' }, 405);
    if (req.method === 'POST') {
      const origin = req.headers.get('origin');
      if (origin && origin !== new URL(req.url).origin) throw new ApiError(403, 'Cross-origin request rejected.');
    }
    if (path === '/api/health' && req.method === 'GET') {
      try {
        await database().execute(sql`select 1`);
        return json({ status: 'ok', database: 'ok', provider: 'netlify-ai-gateway', verification_worker: 'disabled' });
      } catch { return json({ status: 'degraded', database: 'unavailable', detail: 'Database is temporarily unavailable.' }, 503); }
    }
    const user = await getUser();
    if (path === '/api/auth/me' && req.method === 'GET') return json({ authenticated: !!user, username: user?.email, auth_mode: 'required' });
    if (!user) throw new ApiError(401, 'Sign in with your email to save and view investigations.');
    if (path === '/api/diagnostics/provider' && req.method === 'POST') {
      try {
        await new OpenAI().models.retrieve(model, { timeout: 8000, maxRetries: 0 });
        return json({ provider: 'Netlify AI Gateway', model, status: 'connected' });
      } catch { throw new ApiError(503, 'AI connection could not be confirmed. Please retry later.'); }
    }
    const db = database();
    if ((path === '/api/jobs' || path === '/api/repositories/analyze') && req.method === 'POST') {
      const input = requestSchema.parse(await readBody(req));
      const job = await investigate(input);
      await db.insert(investigations).values({ id: job.job_id, ownerId: user.id, report: job });
      return json(publicReport(job), 201);
    }
    if (path === '/api/jobs' && req.method === 'GET') {
      const rows = await db.select().from(investigations).where(eq(investigations.ownerId, user.id)).orderBy(desc(investigations.createdAt)).limit(50);
      return json({ jobs: rows.map(row => publicReport(row.report)) });
    }
    if (path === '/api/metrics' && req.method === 'GET') {
      const [row] = await db.select({ count: sql<number>`count(*)::int` }).from(investigations).where(eq(investigations.ownerId, user.id));
      return json({ jobs: row.count });
    }
    const match = /^\/api\/jobs\/([a-f\d-]{36})(?:\/(verify|approve-patch))?$/.exec(path);
    if (!match) throw new ApiError(404, 'API endpoint not found.');
    const [row] = await db.select().from(investigations).where(and(eq(investigations.id, match[1]), eq(investigations.ownerId, user.id))).limit(1);
    if (!row) throw new ApiError(404, 'Investigation not found.');
    const job = row.report;
    if (!match[2] && req.method === 'GET') return json(publicReport(job));
    if (!match[2] || req.method !== 'POST') throw new ApiError(405, 'Method not allowed.');
    if (match[2] === 'approve-patch') {
      const body = await readBody(req);
      if (body?.approved !== true) throw new ApiError(400, 'Explicit patch approval is required.');
      if (job.patch_approved) throw new ApiError(409, 'This patch has already been approved.');
      job.approved_source_files = patchedSources(job.patch, job.source_files, job.evidence.map(item => item.path));
      job.approved_files = Object.entries(job.approved_source_files).filter(([path, content]) => content !== job.source_files[path]).map(([path, content]) => ({ path, content }));
      job.patch_approved = true;
      job.events.push({ stage: 'patch_approval', status: 'complete', at: new Date().toISOString(), detail: 'Approved patch applied to a disposable source snapshot. The original repository was unchanged.' });
    } else {
      job.verification.status = 'BLOCKED';
      job.verification.details = 'Tests were not executed. This deployment has no isolated Python execution worker.';
      job.events.push({ stage: 'verification', status: 'BLOCKED', at: new Date().toISOString(), detail: job.verification.details });
    }
    job.updated_at = new Date().toISOString();
    job.steps = job.events;
    const saved = await db.update(investigations).set({ report: job, version: row.version + 1 })
      .where(and(eq(investigations.id, row.id), eq(investigations.ownerId, user.id), eq(investigations.version, row.version))).returning({ id: investigations.id });
    if (!saved.length) throw new ApiError(409, 'Investigation changed in another request. Reload before retrying.');
    return json(publicReport(job));
  } catch (error) {
    if (error instanceof ApiError) return json({ detail: error.message }, error.status);
    if (error instanceof ZodError) return json({ detail: error.issues.map(issue => ({ msg: `${issue.path.join('.')}: ${issue.message}` })) }, 422);
    return json({ detail: 'The service could not complete the request. Please retry shortly.' }, 503);
  }
};
export const config: Config = { path: '/api/*', rateLimit: { windowLimit: 30, windowSize: 60, aggregateBy: ['domain', 'ip'] } };
