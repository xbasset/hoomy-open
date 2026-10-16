import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { createGateway } from '../gateway.mjs';
import { validateConfig } from '../config.mjs';

async function listen(server) {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return `http://127.0.0.1:${server.address().port}`;
}

async function fixture(t) {
  const directory = await mkdtemp(join(tmpdir(), 'hoomy-security-review-'));
  const backendCalls = [];
  const backend = createServer((req, res) => {
    backendCalls.push(req.url);
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify(req.url === '/api/projects' ? { projects: [{ id: 'p', name: 'Example' }] } : { project: { id: 'p' }, revision: 'r1', sources: [] }));
  });
  const backendUrl = await listen(backend);
  let app;
  const server = createServer((req, res) => app(req, res));
  const origin = await listen(server);
  t.after(async () => {
    server.closeAllConnections(); backend.closeAllConnections();
    await Promise.all([new Promise(resolve => server.close(resolve)), new Promise(resolve => backend.close(resolve))]);
    await rm(directory, { recursive: true, force: true });
  });
  let identityCalls = 0;
  const config = {
    origin, backendUrl, databasePath: join(directory, 'auth.sqlite'), testMode: true,
    githubClientId: 'test-client', githubClientSecret: 'test-secret', allowedGithubIds: ['42'], cookieKeys: ['x'.repeat(32)],
    clients: [{ client_id: 'review-user', redirect_uris: [`${origin}/test-callback`] }],
  };
  const gateway = await createGateway(config, { githubIdentity: async () => { identityCalls++; return { id: 42, login: 'pilot' }; } });
  app = gateway.app;
  t.after(() => gateway.close());
  async function mint({ clientId = 'review-user', accountId = 'github:42', resource = gateway.resources.mcp, scopes = ['projects:read', 'projects:context:write'], device } = {}) {
    const grant = new gateway.provider.Grant({ accountId, clientId });
    grant.addResourceScope(resource, scopes.join(' '));
    const grantId = await grant.save();
    if (device) {
      gateway.store.put('device', device, { account_id: accountId, workspace_id: 'pilot', status: 'active', grant_id: grantId });
      gateway.store.put('device-grant', grantId, { device_id: device });
    }
    const token = new gateway.provider.AccessToken({
      clientId, accountId, grantId, scope: scopes.join(' '), expiresWithSession: false,
      resourceServer: { audience: resource, accessTokenFormat: 'opaque', accessTokenTTL: 900 },
    });
    return { value: await token.save(), grantId };
  }
  const request = (path, body, token) => fetch(`${origin}${path}`, {
    method: body === undefined ? 'GET' : 'POST', redirect: 'manual',
    headers: { ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  async function userTokens() {
    const jar = new Map();
    const browser = async (path, form) => {
      const response = await fetch(new URL(path, origin), {
        redirect: 'manual', method: form ? 'POST' : 'GET',
        headers: { Cookie: [...jar].map(([key, value]) => `${key}=${value}`).join('; '), ...(form ? { 'Content-Type': 'application/x-www-form-urlencoded' } : {}) },
        body: form ? new URLSearchParams(form) : undefined,
      });
      for (const cookie of response.headers.getSetCookie()) {
        const pair = cookie.split(';')[0]; const split = pair.indexOf('=');
        jar.set(pair.slice(0, split), pair.slice(split + 1));
      }
      return response;
    };
    const verifier = 'v'.repeat(43);
    const params = new URLSearchParams({
      client_id: 'review-user', response_type: 'code', redirect_uri: `${origin}/test-callback`,
      scope: 'projects:read projects:context:write', resource: gateway.resources.mcp, state: 'review-state',
      code_challenge: createHash('sha256').update(verifier).digest('base64url'), code_challenge_method: 'S256',
    });
    let response = await browser(`/oauth/authorize?${params}`);
    for (let i = 0; i < 20; i++) {
      if (response.status >= 300 && response.status < 400) {
        const url = new URL(response.headers.get('location'), origin);
        if (url.pathname === '/test-callback') {
          assert.equal(url.searchParams.get('state'), 'review-state');
          assert.ok(url.searchParams.get('code'), url.href);
          const exchangeBody = {
            client_id: 'review-user', grant_type: 'authorization_code', redirect_uri: `${origin}/test-callback`,
            code: url.searchParams.get('code'), code_verifier: verifier, resource: gateway.resources.mcp,
          };
          const exchanged = await fetch(`${origin}/oauth/token`, {
            method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams(exchangeBody),
          });
          assert.equal(exchanged.status, 200, await exchanged.clone().text());
          const tokens = await exchanged.json();
          const stored = await gateway.provider.AccessToken.find(tokens.access_token);
          return { value: tokens.access_token, grantId: stored.grantId, exchangeBody };
        }
        assert.equal(url.origin, origin);
        response = await browser(url); continue;
      }
      assert.equal(response.status, 200, await response.clone().text());
      const html = await response.text();
      const login = html.match(/href="(\/auth\/github\/start[^\"]+)"/);
      if (login) {
        const start = await browser(login[1]);
        const githubState = new URL(start.headers.get('location')).searchParams.get('state');
        response = await browser(`/auth/github/callback?state=${githubState}&code=review-code`); continue;
      }
      const confirm = html.match(/action="(\/interaction\/[^\"]+\/confirm)"/);
      assert.ok(confirm, 'Expected an explicit authorization consent form.');
      const hidden = Object.fromEntries([...html.matchAll(/<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"/g)].map(match => [match[1], match[2]]));
      response = await browser(confirm[1], { ...hidden, decision: 'approve' });
    }
    assert.fail('OAuth authorization exceeded the expected redirects.');
  }
  return { gateway, config, origin, request, mint, userTokens, backendCalls, identityCalls: () => identityCalls };
}

test('production configuration fails closed for HTTP, open pilot membership or remote private backend', () => {
  const base = {
    origin: 'https://my.hoomy.dev', backendUrl: 'http://127.0.0.1:8765', databasePath: '/tmp/test.sqlite',
    githubClientId: 'test-client', githubClientSecret: 'test-secret', allowedGithubIds: ['42'], cookieKeys: ['x'.repeat(32)],
  };
  assert.throws(() => validateConfig({ ...base, origin: 'http://my.hoomy.dev' }), /HTTPS/);
  assert.throws(() => validateConfig({ ...base, allowedGithubIds: [] }), /GitHub numeric/);
  assert.throws(() => validateConfig({ ...base, backendUrl: 'https://private.example' }), /loopback/);
  assert.throws(() => validateConfig({ ...base, cookieKeys: ['short'] }), /32/);
});

test('real provider-issued grants authorize remote MCP and immediate grant revocation blocks the next call', async t => {
  const f = await fixture(t);
  const token = await f.userTokens();
  const client = new Client({ name: 'security-review', version: '1.0.0' });
  await client.connect(new StreamableHTTPClientTransport(new URL(`${f.origin}/mcp`), {
    requestInit: { headers: { Authorization: `Bearer ${token.value}` } },
  }));
  t.after(() => client.close());
  const home = await client.callTool({ name: 'hoomy_home', arguments: {} });
  assert.equal(home.structuredContent.projects[0].id, 'p');
  assert.deepEqual(f.backendCalls, ['/api/projects']);
  f.gateway.store.put('revoked-grant', token.grantId, { revoked_at: new Date().toISOString() });
  await assert.rejects(client.callTool({ name: 'hoomy_home', arguments: {} }));
  assert.equal(f.backendCalls.length, 1);
});

test('replaying an actually used PKCE authorization code fails and revokes its grant', async t => {
  const f = await fixture(t);
  const token = await f.userTokens();
  const replay = await fetch(`${f.origin}/oauth/token`, {
    method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams(token.exchangeBody),
  });
  assert.equal(replay.status, 400);
  assert.equal((await replay.json()).error, 'invalid_grant');
  const response = await f.request('/mcp', {}, token.value);
  assert.equal(response.status, 401);
  assert.deepEqual(f.backendCalls, []);
});

test('collector credentials cannot query projects or MCP and device revocation is immediate', async t => {
  const f = await fixture(t);
  const collector = await f.mint({ clientId: 'hoomy-collector', resource: f.gateway.resources.collector, scopes: ['device:self'], device: 'review-device' });
  assert.equal((await f.request('/v1/devices/self', undefined, collector.value)).status, 200);
  assert.equal((await f.request('/v1/projects/p', undefined, collector.value)).status, 401);
  assert.equal((await f.request('/mcp', {}, collector.value)).status, 401);
  const user = await f.mint({ resource: f.gateway.resources.projects, scopes: ['devices:manage'] });
  assert.equal((await f.request('/v1/devices/review-device/revoke', {}, user.value)).status, 200);
  assert.equal((await f.request('/v1/devices/self', undefined, collector.value)).status, 401);
  assert.deepEqual(f.backendCalls, []);
});

test('uninvited provider identities and user grants with collector audience cannot cross API boundaries', async t => {
  const f = await fixture(t);
  const outsider = await f.mint({ accountId: 'github:999', resource: f.gateway.resources.projects });
  assert.equal((await f.request('/v1/projects/p', undefined, outsider.value)).status, 401);
  const user = await f.mint({ resource: f.gateway.resources.collector, scopes: ['device:self'] });
  const response = await f.request('/v1/devices/self', undefined, user.value);
  assert.ok([401, 403].includes(response.status));
  assert.deepEqual(f.backendCalls, []);
});

test('public client registration refuses device privileges and unsafe callbacks', async t => {
  const f = await fixture(t);
  for (const body of [
    { redirect_uris: [`${f.origin}/callback`], grant_types: ['urn:ietf:params:oauth:grant-type:device_code'] },
    { redirect_uris: [`${f.origin}/callback`], scope: 'device:self' },
    { redirect_uris: ['http://attacker.example/callback'] },
    { redirect_uris: [`${f.origin}/callback`], client_id: 'hoomy-collector' },
  ]) {
    assert.equal((await f.request('/oauth/register', body)).status, 400);
  }
});

test('unsolicited GitHub callbacks, missing CSRF and missing device tickets never establish credentials', async t => {
  const f = await fixture(t);
  const callback = await f.request('/auth/github/callback?state=attacker-state&code=attacker-code');
  assert.equal(callback.status, 400);
  assert.equal(f.identityCalls(), 0);
  const csrf = await fetch(`${f.origin}/interaction/untrusted/confirm`, {
    method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ decision: 'approve', csrf: 'attacker' }),
  });
  assert.equal(csrf.status, 403);
  const device = await fetch(`${f.origin}/oauth/device_authorization`, {
    method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ client_id: 'hoomy-collector', resource: f.gateway.resources.collector, scope: 'device:self' }),
  });
  assert.equal(device.status, 400);
  assert.equal((await device.json()).device_code, undefined);
});
