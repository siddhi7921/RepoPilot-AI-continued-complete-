import { test } from 'node:test';
import assert from 'node:assert/strict';
import handler from '../netlify/functions/api.mjs';

test('rejects cross-origin mutations before accessing authentication or storage', async () => {
  const response = await handler(new Request('https://example.test/api/jobs', { method: 'POST', headers: { Origin: 'https://other.test' } }));
  assert.equal(response.status, 403);
  assert.equal(response.headers.get('cache-control'), 'no-store');
});
test('rejects unsupported methods with a JSON response', async () => {
  const response = await handler(new Request('https://example.test/api/jobs', { method: 'DELETE' }));
  assert.equal(response.status, 405);
  assert.match((await response.json()).detail, /Method not allowed/);
});
test('anonymous callers cannot read investigations', async () => {
  const response = await handler(new Request('https://example.test/api/jobs'));
  assert.equal(response.status, 401);
});
