import assert from 'node:assert/strict';
import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { get } from 'node:http';
import { createGateway } from '/app/services/hoomy-gateway/gateway.mjs';

// Run inside the production image, sharing only the private backend network.
const directory = await mkdtemp(join(tmpdir(), 'hoomy-image-smoke-'));
const origin = 'https://hoomy.example';
const gateway = await createGateway({
  origin, backendUrl: 'http://127.0.0.1:8765', databasePath: join(directory, 'auth.sqlite'),
  githubClientId: 'image-smoke-client', githubClientSecret: 'image-smoke-secret',
  allowedGithubIds: ['1000000001'], cookieKeys: ['image-smoke-cookie-key-never-used-for-real-auth'],
  releaseDirectory: '/srv/hoomy-release',
});
const server = gateway.app.listen(0, '127.0.0.1');
await new Promise(resolve => server.once('listening', resolve));
const headers = { host: new URL(origin).host, 'x-forwarded-proto': 'https' };
function request(path, suppliedHeaders = headers) {
  // HTTP preserves the explicit canonical Host required by the proxy boundary.
  return new Promise((resolve, reject) => {
    const req = get({ hostname: '127.0.0.1', port: server.address().port, path, headers: suppliedHeaders }, res => {
      const chunks = [];
      res.on('data', chunk => chunks.push(chunk));
      res.on('end', () => resolve({ status: res.statusCode, headers: new Headers(res.headers), json: async () => JSON.parse(Buffer.concat(chunks).toString('utf8')) }));
      res.on('error', reject);
    });
    req.setTimeout(5000, () => req.destroy(new Error('Image smoke request timed out.')));
    req.on('error', reject);
  });
}
try {
  assert.equal((await request('/health')).status, 200);
  assert.equal((await request('/health', { host: 'wrong.example' })).status, 421);
  assert.equal((await request('/health', { host: headers.host })).status, 400);
  for (const path of ['/v1/projects', '/v1/devices/self']) assert.equal((await request(path)).status, 401);
  const mcp = await request('/mcp');
  assert.equal(mcp.status, 401);
  assert.match(mcp.headers.get('www-authenticate'), /oauth-protected-resource\/mcp/);
  const discovery = await (await request('/.well-known/hoomy')).json();
  assert.equal(discovery.canonical_server_url, origin);
  assert.equal(discovery.collection_available, false);
  assert.equal((await request('/install.sh')).status, 200);
  const manifest = JSON.parse(await readFile('/srv/hoomy-release/manifest.json', 'utf8'));
  const archive = await readFile(`/srv/hoomy-release${manifest.archive}`);
  assert.equal(createHash('sha256').update(archive).digest('hex'), manifest.sha256);
  const privateList = await (await fetch('http://127.0.0.1:8765/api/projects', { signal: AbortSignal.timeout(5000) })).json();
  assert.ok(privateList.projects.length > 0);
  const privateRoute = await request('/api/projects');
  assert.equal(privateRoute.status, 404);
  console.log(`Production image smoke passed; private projects: ${privateList.projects.length}. External GitHub identity was not exercised.`);
} finally {
  await new Promise(resolve => server.close(resolve));
  gateway.close();
}
