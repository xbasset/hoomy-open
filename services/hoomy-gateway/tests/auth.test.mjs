import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer, request as httpRequest } from 'node:http';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { EventEmitter } from 'node:events';
import { createGateway } from '../gateway.mjs';
import { HttpError } from '../backend-client.mjs';
import { canonicalDigest } from '../ingestion.mjs';

const nodeDeviceGrant = 'urn:ietf:params:oauth:grant-type:device_code';
const hidden = html => Object.fromEntries([...html.matchAll(/<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"/g)].map(match => [match[1], match[2]]));

export async function harness(t, overrides = {}, dependencies = {}) {
  const reserve = createServer(); await new Promise(resolve => reserve.listen(0, '127.0.0.1', resolve));
  const port = reserve.address().port; await new Promise(resolve => reserve.close(resolve));
  const dir = mkdtempSync(join(tmpdir(), 'hoomy-gateway-test-'));
  const origin = `http://127.0.0.1:${port}`;
  const config = { testMode: true, origin, backendUrl: 'http://127.0.0.1:8765', databasePath: join(dir, 'auth.sqlite'), githubClientId: 'pilot-app', githubClientSecret: 'fake-secret', allowedGithubIds: ['42'], cookieKeys: ['a'.repeat(32)], ...overrides };
  const g = await createGateway(config, { githubIdentity: async code => ({ id: code === 'other-user' ? 43 : 42, login: 'pilot' }), pilot: async (_path, body) => { if (body?.revision === 'old') throw new HttpError(409, 'revision_conflict', 'The profile changed.', { revision: 'new' }); return { revision: 'new', project: { id: 'project' } }; }, ...dependencies });
  const server = g.app.listen(port, '127.0.0.1'); await new Promise(resolve => server.once('listening', resolve));
  t.after(async () => { await new Promise(resolve => server.close(resolve)); g.close(); rmSync(dir, { recursive: true, force: true }); });
  const jar = new Map();
  async function request(path, options = {}, browser = false) {
    const response = await fetch(new URL(path, origin), { redirect: 'manual', ...options, headers: { ...(browser ? { Cookie: [...jar].map(([key, value]) => `${key}=${value}`).join('; ') } : {}), ...options.headers } });
    if (browser) for (const cookie of response.headers.getSetCookie()) { const [pair] = cookie.split(';'); const split = pair.indexOf('='); jar.set(pair.slice(0, split), pair.slice(split + 1)); }
    return response;
  }
  const post = (path, body, browser = false, type = 'application/json') => request(path, { method:'POST', headers: { 'Content-Type':type }, body: type === 'application/json' ? JSON.stringify(body) : new URLSearchParams(body) }, browser);
  const form = (path, body, browser = false) => post(path, body, browser, 'application/x-www-form-urlencoded');
  async function consent(response, decision = 'approve') {
    for (let i = 0; i < 15; i++) {
      if (response.status >= 300 && response.status < 400) {
        const target = new URL(response.headers.get('location'), origin);
        if (target.origin !== origin) return target;
        response = await request(target, {}, true); continue;
      }
      if (decision === 'deny' && response.status === 400 && (await response.clone().text()).includes('op.deviceInputForm')) return null;
      assert.equal(response.status, 200, await response.clone().text());
      const html = await response.text();
      const github = html.match(/href="(\/auth\/github\/start[^\"]+)"/);
      if (github) {
        const start = await request(github[1], {}, true);
        const state = new URL(start.headers.get('location')).searchParams.get('state');
        response = await request(`/auth/github/callback?state=${state}&code=good`, {}, true); continue;
      }
      const confirm = html.match(/action="(\/interaction\/[^\"]+\/confirm)"/);
      if (confirm && decision === 'review') return {html,confirm:confirm[1]};
      if (confirm) { response = await form(confirm[1], { ...hidden(html), decision }, true); continue; }
      if (html.includes('Machine autorisée')) return null;
      if (decision === 'deny' && html.includes('op.deviceInputForm')) return null;
      assert.fail(`Unexpected auth page: ${html.slice(-400)}`);
    }
    assert.fail('Authentication exceeded the expected redirects.');
  }
  async function register() {
    const response = await post('/oauth/register', { client_name:'Pilot Codex', application_type:'native', redirect_uris:['http://127.0.0.1:4567/callback'], grant_types:['authorization_code','refresh_token'], token_endpoint_auth_method:'none' });
    assert.equal(response.status, 201, await response.clone().text()); return response.json();
  }
  async function userTokens(resource = g.resources.projects) {
    const client = await register(); const verifier = 'v'.repeat(43);
    const params = new URLSearchParams({ client_id:client.client_id, response_type:'code', redirect_uri:client.redirect_uris[0], scope:'projects:read projects:context:write devices:manage', resource, state:'client-state', code_challenge: createHash('sha256').update(verifier).digest('base64url'), code_challenge_method:'S256' });
    const callback = await consent(await request(`/oauth/authorize?${params}`, {}, true));
    assert.equal(callback.searchParams.get('state'), 'client-state');
    const response = await form('/oauth/token', { client_id:client.client_id, grant_type:'authorization_code', code:callback.searchParams.get('code'), redirect_uri:client.redirect_uris[0], code_verifier:verifier, resource });
    assert.equal(response.status, 200, await response.clone().text()); return { ...await response.json(), client, resource };
  }
  async function deviceRequest() {
    const enrolled = await post('/v1/enrolments', { installation_id:randomUUID(), label:'Pilot VPS', platform:'linux', architecture:'x64', agent_version:'0.1.0', proposed_policy: { content_tier:'metadata', roots:[], exclusions:[] } });
    assert.equal(enrolled.status, 201); const pending = await enrolled.json();
    const response = await form('/oauth/device_authorization', { client_id:'hoomy-collector', scope:'device:self', resource:g.resources.collector, enrolment_ticket:pending.enrolment_ticket });
    assert.equal(response.status, 200, await response.clone().text()); return { ...await response.json(), ...pending };
  }
  async function approveDevice(device, decision = 'approve') {
    // Start at the code input form; exercise the provider's own XSRF validation.
    const page = await request('/activate', {}, true); const input = hidden(await page.text());
    const confirm = await form('/activate', { ...input, user_code:device.user_code }, true);
    const html = await confirm.text();
    const response = await form('/activate', hidden(html), true);
    return consent(response, decision);
  }
  const poll = device => form('/oauth/token', { client_id:'hoomy-collector', grant_type:nodeDeviceGrant, device_code:device.device_code, resource:g.resources.collector });
  return { ...g, config, server, request, post, form, consent, register, userTokens, deviceRequest, approveDevice, poll };
}

test('GitHub browser identity, PKCE and revision conflicts preserve central semantics', async t => {
  const h = await harness(t); const tokens = await h.userTokens();
  assert.ok(tokens.refresh_token); assert.equal(tokens.token_type, 'Bearer');
  const read = await h.request('/v1/projects/project', { headers: { Authorization:`Bearer ${tokens.access_token}` } });
  assert.equal(read.status, 200);
  const write = await h.request('/v1/projects/project/context', { method:'POST', headers: { Authorization:`Bearer ${tokens.access_token}`, 'Content-Type':'application/json' }, body:JSON.stringify({ revision:'old', fields:{ goal:'Approved goal' } }) });
  assert.equal(write.status, 409); assert.equal((await write.json()).details.revision, 'new');
  assert.equal(h.store.list('project-update-audit').length, 0);
  const accepted = await h.request('/v1/projects/project/context', { method:'POST', headers: { Authorization:`Bearer ${tokens.access_token}`, 'Content-Type':'application/json' }, body:JSON.stringify({ revision:'new', fields:{ goal:'Approved goal' } }) });
  assert.equal(accepted.status, 200);
  const audit = h.store.list('project-update-audit'); assert.equal(audit.length, 1);
  assert.equal(audit[0].account_id, 'github:42'); assert.deepEqual(audit[0].field_names, ['goal']);
  assert.ok(!JSON.stringify(audit).includes('Approved goal'));
  const put = h.store.put.bind(h.store);
  h.store.put = (kind, ...args) => { if (kind === 'project-update-audit') throw new Error('Private diagnostic'); return put(kind, ...args); };
  const committed = await h.request('/v1/projects/project/context', { method:'POST', headers: { Authorization:`Bearer ${tokens.access_token}`, 'Content-Type':'application/json' }, body:JSON.stringify({ revision:'new', fields:{ goal:'Another approved goal' } }) });
  assert.equal(committed.status, 200);
  const confirmed = await committed.json(); assert.equal(confirmed.revision, 'new'); assert.equal(confirmed.warnings[0].code, 'audit_unavailable');
  assert.ok(!JSON.stringify(confirmed).includes('Private diagnostic'));
  const replay = await h.form('/oauth/token', { client_id:tokens.client.client_id, grant_type:'authorization_code', code:'unrecognized', code_verifier:'v'.repeat(43), resource:tokens.resource });
  assert.equal(replay.status, 400);
});

test('device request stays pending until explicit policy approval; scopes and revocation separate', async t => {
  const h = await harness(t); const user = await h.userTokens(); const device = await h.deviceRequest();
  assert.ok(!device.verification_uri_complete.includes(device.device_code));
  const pending = await h.poll(device); assert.equal((await pending.json()).error, 'authorization_pending');
  await h.approveDevice(device);
  const response = await h.poll(device); assert.equal(response.status, 200, await response.clone().text()); const tokens = await response.json();
  assert.equal(tokens.scope, 'device:self'); assert.ok(tokens.refresh_token);
  const headers = { Authorization:`Bearer ${tokens.access_token}` };
  const self = await h.request('/v1/devices/self', { headers }); assert.equal(self.status, 200); const info = await self.json();
  assert.equal(info.status, 'authorized'); assert.equal(info.collection_available, false);
  const wrongAudience = await h.request('/v1/projects/project', { headers }); assert.equal(wrongAudience.status, 401);
  const activate = await h.request('/v1/devices/self/activate', { method:'POST', headers:{...headers,'Content-Type':'application/json'}, body:JSON.stringify({ policy_revision:1, capabilities:['heartbeat'] }) }); assert.equal(activate.status, 200);
  const revoke = await h.request(`/v1/devices/${info.device_id}/revoke`, { method:'POST', headers:{Authorization:`Bearer ${user.access_token}`,'Content-Type':'application/json'}, body:'{}' }); assert.equal(revoke.status, 200);
  assert.equal((await h.request('/v1/devices/self', {headers})).status, 401);
  const refresh = await h.form('/oauth/token', { client_id:'hoomy-collector', grant_type:'refresh_token', refresh_token:tokens.refresh_token, resource:h.resources.collector }); assert.equal(refresh.status, 400);
  assert.equal((await h.request('/v1/projects/project',{headers:{Authorization:`Bearer ${user.access_token}`}})).status, 200);
});

test('device denial and expiry prevent token issuance', async t => {
  const h = await harness(t); const denied = await h.deviceRequest(); await h.approveDevice(denied, 'deny');
  const refusal = await h.poll(denied); assert.equal((await refusal.json()).error, 'access_denied');
  const expired = await h.deviceRequest();
  h.store.db.prepare("UPDATE oauth SET payload=json_set(payload,'$.exp',?) WHERE model='DeviceCode'").run(Math.floor(Date.now() / 1000) - 1);
  assert.equal((await (await h.poll(expired)).json()).error, 'expired_token');
});

test('RFC refresh revocation updates the current device registry while preserving other and replacement grants', async t => {
  const h = await harness(t);
  const user = await h.userTokens();
  const native = await h.userTokens(h.resources.mcp);
  async function enrolled() {
    const request = await h.deviceRequest(); await h.approveDevice(request);
    const issued = await h.poll(request); assert.equal(issued.status, 200);
    const tokens = await issued.json();
    const headers = { Authorization:`Bearer ${tokens.access_token}` };
    const self = await h.request('/v1/devices/self', { headers }); assert.equal(self.status, 200);
    const device = await self.json();
    const active = await h.request('/v1/devices/self/activate', { method:'POST', headers:{ ...headers, 'Content-Type':'application/json' }, body:JSON.stringify({ policy_revision:1, capabilities:['heartbeat'] }) });
    assert.equal(active.status, 200);
    return { tokens, headers, device };
  }
  const target = await enrolled(), independent = await enrolled();
  const oldGrantId = h.store.get('device', target.device.device_id).grant_id;
  const revoked = await h.form('/oauth/revoke', { client_id:'hoomy-collector', token:target.tokens.refresh_token, token_type_hint:'refresh_token' });
  assert.equal(revoked.status, 200);
  const record = h.store.get('device', target.device.device_id);
  assert.equal(record.status, 'revoked'); assert.ok(record.revoked_at);
  const list = await h.request('/v1/devices', { headers:{ Authorization:`Bearer ${user.access_token}` } });
  assert.equal(list.status, 200);
  assert.equal((await list.json()).devices.find(device => device.id === target.device.device_id).status, 'revoked');
  const page = await h.request('/devices', {}, true); assert.equal(page.status, 200);
  assert.match(await page.text(), /linux · revoked/);
  assert.equal((await h.request('/v1/devices/self', { headers:target.headers })).status, 401);
  const refresh = await h.form('/oauth/token', { client_id:'hoomy-collector', grant_type:'refresh_token', refresh_token:target.tokens.refresh_token, resource:h.resources.collector });
  assert.equal(refresh.status, 400); assert.equal((await refresh.json()).error, 'invalid_grant');
  assert.equal((await h.request('/v1/devices/self', { headers:independent.headers })).status, 200);
  assert.equal(h.store.get('device', independent.device.device_id).status, 'active');
  assert.equal((await h.request('/v1/projects/project', { headers:{ Authorization:`Bearer ${user.access_token}` } })).status, 200);
  assert.equal((await h.authenticateRequest({ headers:{ authorization:`Bearer ${native.access_token}` } }, { resource:h.resources.mcp })).account_id, 'github:42');
  // Simulate a pre-fix persisted status without mutating its revoked family.
  h.store.put('device', target.device.device_id, { ...record, status:'active' });
  const restarted = await createGateway(h.config);
  const server = restarted.app.listen(0, '127.0.0.1'); await new Promise(resolve => server.once('listening', resolve));
  t.after(async () => { await new Promise(resolve => server.close(resolve)); restarted.close(); });
  const restartOrigin = `http://127.0.0.1:${server.address().port}`;
  const restartedList = await new Promise((resolve, reject) => {
    const request = httpRequest(restartOrigin+'/v1/devices', { headers:{ Host:new URL(h.config.origin).host, Authorization:`Bearer ${user.access_token}` } }, response => {
      let body = ''; response.setEncoding('utf8'); response.on('data', part => { body += part; });
      response.on('end', () => resolve({ status:response.statusCode, body:JSON.parse(body) }));
    });
    request.on('error', reject); request.end();
  });
  assert.equal(restartedList.status, 200);
  assert.equal(restartedList.body.devices.find(device => device.id === target.device.device_id).status, 'revoked');
  const restoredPage = await h.request('/devices', {}, true); assert.match(await restoredPage.text(), /linux · revoked/);
  assert.equal(h.store.get('device', target.device.device_id).status, 'active');
  assert.equal((await restarted.authenticateRequest({ headers:{ authorization:`Bearer ${independent.tokens.access_token}` } }, { resource:h.resources.collector })).device_id, independent.device.device_id);
  assert.equal((await restarted.authenticateRequest({ headers:{ authorization:`Bearer ${native.access_token}` } }, { resource:h.resources.mcp })).account_id, 'github:42');
  // Re-approve the same installation; old registry bindings still refer to this ID.
  const oldDevice = h.store.get('device', target.device.device_id);
  const proposal = await h.post('/v1/enrolments', { installation_id:oldDevice.installation_id, label:'Replacement pilot', platform:'linux', proposed_policy:{ content_tier:'metadata', roots:[], exclusions:[] } });
  const pending = await proposal.json();
  const auth = await h.form('/oauth/device_authorization', { client_id:'hoomy-collector', scope:'device:self', resource:h.resources.collector, enrolment_ticket:pending.enrolment_ticket });
  const request = await auth.json(); await h.approveDevice(request);
  const replacementIssued = await h.poll(request); assert.equal(replacementIssued.status, 200);
  const replacement = await replacementIssued.json();
  const current = h.store.get('device', target.device.device_id);
  assert.notEqual(current.grant_id, oldGrantId); assert.equal(current.status, 'authorized');
  h.provider.emit('grant.revoked', undefined, oldGrantId);
  assert.equal(h.store.get('device', target.device.device_id).status, 'authorized');
  assert.equal((await h.request('/v1/devices/self', { headers:{ Authorization:`Bearer ${replacement.access_token}` } })).status, 200);
});

test('refresh rotates and replay revokes the family', async t => {
  const h = await harness(t); const tokens = await h.userTokens();
  const refresh = await h.form('/oauth/token', { client_id:tokens.client.client_id, grant_type:'refresh_token', refresh_token:tokens.refresh_token, resource:tokens.resource });
  assert.equal(refresh.status, 200, await refresh.clone().text()); const rotated = await refresh.json();
  assert.notEqual(rotated.refresh_token, tokens.refresh_token);
  const replay = await h.form('/oauth/token', { client_id:tokens.client.client_id, grant_type:'refresh_token', refresh_token:tokens.refresh_token, resource:tokens.resource }); assert.equal(replay.status, 400);
  const last = await h.form('/oauth/token', { client_id:tokens.client.client_id, grant_type:'refresh_token', refresh_token:rotated.refresh_token, resource:tokens.resource }); assert.equal(last.status, 400);
});

test('PKCE is required and a wrong verifier cannot redeem an approved code', async t => {
  const h = await harness(t); const client = await h.register();
  const base = { client_id:client.client_id, response_type:'code', redirect_uri:client.redirect_uris[0], scope:'projects:read', resource:h.resources.mcp };
  const missing = await h.request(`/oauth/authorize?${new URLSearchParams(base)}`);
  assert.equal(missing.status, 303);
  assert.equal(new URL(missing.headers.get('location')).searchParams.get('error'), 'invalid_request');
  const verifier = 'p'.repeat(43);
  const callback = await h.consent(await h.request(`/oauth/authorize?${new URLSearchParams({ ...base, code_challenge:createHash('sha256').update(verifier).digest('base64url'), code_challenge_method:'S256' })}`, {}, true));
  const wrong = await h.form('/oauth/token', { client_id:client.client_id, grant_type:'authorization_code', code:callback.searchParams.get('code'), redirect_uri:client.redirect_uris[0], code_verifier:'w'.repeat(43), resource:h.resources.mcp });
  assert.equal(wrong.status, 400); assert.equal((await wrong.json()).error, 'invalid_grant');
});

test('OAuth secrets, client registration and account policy survive a fresh provider instance', async t => {
  const h = await harness(t); const tokens = await h.userTokens();
  const replacement = await createGateway(h.config);
  t.after(() => replacement.close());
  const principal = await replacement.authenticateRequest({ headers:{ authorization:`Bearer ${tokens.access_token}` } }, { resource:h.resources.projects, scopes:['projects:read'] });
  assert.equal(principal.account_id, 'github:42');
  assert.equal((await replacement.provider.Client.find(tokens.client.client_id)).clientId, tokens.client.client_id);
  assert.ok(await replacement.provider.RefreshToken.find(tokens.refresh_token));
  const revokedMembership = await createGateway({ ...h.config, allowedGithubIds:['43'] });
  t.after(() => revokedMembership.close());
  await assert.rejects(() => revokedMembership.authenticateRequest({ headers:{ authorization:`Bearer ${tokens.access_token}` } }, { resource:h.resources.projects }), error => error.status === 401);
});

test('public activation attempts are bounded and alternate Host headers are rejected', async t => {
  const h = await harness(t);
  const alternateHost = await new Promise((resolve, reject) => {
    const request = httpRequest(new URL('/health', h.config.origin), { headers:{Host:'another.example'} }, response => { response.resume(); resolve(response.statusCode); });
    request.on('error', reject); request.end();
  });
  assert.equal(alternateHost, 421);
  for (let i = 0; i < 20; i++) {
    const invalid = await h.form('/activate', { user_code:'WRONG-CODE', xsrf:'unrecognized' });
    assert.equal(invalid.status, 400);
  }
  const limited = await h.form('/activate', { user_code:'WRONG-CODE', xsrf:'unrecognized' });
  assert.equal(limited.status, 429); assert.equal(limited.headers.get('retry-after'), '60');
});

test('invalid, forged and expired activation sessions return controlled errors without provider failures', async t => {
  const h = await harness(t); const errors = [];
  h.provider.on('server_error', (_ctx, error) => errors.push(error));
  h.provider.on('error', error => errors.push(error));
  const api = await h.request('/activate', { method:'POST', headers:{ Accept:'application/json', 'Content-Type':'application/x-www-form-urlencoded' }, body:new URLSearchParams({ user_code:'WRONG-CODE', xsrf:'unrecognized' }) });
  assert.equal(api.status, 400);
  assert.equal((await api.json()).error, 'activation_session_required');
  const name = h.provider.cookieName('session');
  const forged = await h.request('/activate', { method:'POST', headers: { 'Content-Type':'application/x-www-form-urlencoded', Cookie:`${name}=fabricated; ${name}.sig=fabricated` }, body:new URLSearchParams({ user_code:'WRONG-CODE', xsrf:'unrecognized' }) });
  assert.equal(forged.status, 400);
  const initial = await h.request('/activate', {}, true); assert.equal(initial.status, 200);
  const fields = hidden(await initial.text());
  const invalid = await h.form('/activate', { ...fields, user_code:'WRONG-CODE' }, true);
  assert.equal(invalid.status, 400);
  h.store.db.prepare("UPDATE oauth SET expires=? WHERE model='Session'").run(Date.now() - 1);
  const expired = await h.form('/activate', { ...fields, user_code:'WRONG-CODE' }, true);
  assert.equal(expired.status, 400);
  assert.deepEqual(errors, []);
});

test('authenticated project listing uses only the lightweight private route and returns active summaries', async t => {
  const paths = [];
  const h = await harness(t, {}, { pilot:async path => {
    paths.push(path);
    return { projects:[
      { id:'project', name:'Pilot project', note:'Private context', session_ids:['source'], confirmed_fields:{ goal:'Private goal' } },
      { id:'dismissed', name:'Dismissed project', dismissed:true },
      { id:'archived', name:'Archived project', activity:'archived' },
    ] };
  } });
  assert.equal((await h.request('/v1/projects')).status, 401); assert.deepEqual(paths, []);
  const tokens = await h.userTokens();
  const list = await h.request('/v1/projects', { headers:{Authorization:`Bearer ${tokens.access_token}`} });
  assert.equal(list.status, 200);
  assert.deepEqual(await list.json(), { projects:[{ id:'project', name:'Pilot project' }] });
  assert.deepEqual(paths, ['/api/projects']);
});

test('MCP discovery and issued tokens exclude device administration while the project API retains it', async t => {
  const h = await harness(t);
  const metadata = await h.request('/.well-known/oauth-protected-resource/mcp');
  assert.deepEqual((await metadata.json()).scopes_supported, ['projects:read', 'projects:context:write', 'projects:state:refresh', 'projects:state:execute', 'offline_access']);
  const projects = await h.request('/.well-known/oauth-protected-resource/v1/projects');
  assert.ok((await projects.json()).scopes_supported.includes('devices:manage'));
  const mcp = await h.userTokens(h.resources.mcp);
  assert.deepEqual(mcp.scope.split(' ').sort(), ['projects:context:write', 'projects:read']);
  const principal = await h.authenticateRequest({ headers:{ authorization:`Bearer ${mcp.access_token}` } }, { resource:h.resources.mcp });
  assert.ok(!principal.scopes.includes('devices:manage'));
  const user = await h.userTokens(h.resources.projects);
  assert.ok(user.scope.split(' ').includes('devices:manage'));
});

test('both authorization discovery documents allow automatic user registration without collector scopes', async t => {
  const h = await harness(t);
  for (const path of ['/.well-known/openid-configuration', '/.well-known/oauth-authorization-server']) {
    const metadata = await (await h.request(path)).json();
    assert.equal(metadata.issuer, h.config.origin);
    assert.deepEqual(metadata.scopes_supported, ['openid', 'projects:read', 'projects:context:write', 'projects:state:refresh', 'projects:state:execute', 'offline_access']);
    const response = await h.post('/oauth/register', { client_name:'Discovery-selected native client', redirect_uris:['http://127.0.0.1:4567/callback'], scope:metadata.scopes_supported.join(' '), token_endpoint_auth_method:'none' });
    assert.equal(response.status, 201);
  }
  const collector = await (await h.request('/.well-known/oauth-protected-resource/v1/collector')).json();
  assert.deepEqual(collector.scopes_supported, ['device:self', 'evidence:ingest']);
  const rejected = await h.post('/oauth/register', {client_name:'Collector escalation', redirect_uris:['http://127.0.0.1:4567/callback'], scope:collector.scopes_supported.join(' ')});
  assert.equal(rejected.status, 400);
  assert.equal((await rejected.json()).error, 'invalid_client_metadata');
});

test('browser consent preserves same-origin form Origin and rejects null or foreign origins with a valid CSRF token', async t => {
  const h = await harness(t);
  await h.userTokens();
  const client = await h.register();
  const verifier = 'b'.repeat(43);
  const params = new URLSearchParams({ client_id:client.client_id, response_type:'code', redirect_uri:client.redirect_uris[0], scope:'projects:read', resource:h.resources.mcp, code_challenge:createHash('sha256').update(verifier).digest('base64url'), code_challenge_method:'S256' });
  let response = await h.request(`/oauth/authorize?${params}`, {}, true);
  for (let i = 0; i < 10 && response.status >= 300 && response.status < 400; i++) response = await h.request(response.headers.get('location'), {}, true);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('referrer-policy'), 'same-origin');
  const html = await response.text();
  const path = html.match(/action="(\/interaction\/[^\"]+\/confirm)"/)[1];
  const body = new URLSearchParams({ ...hidden(html), decision:'approve' });
  for (const origin of ['null', 'https://another.example']) {
    const denied = await h.request(path, { method:'POST', headers:{ 'Content-Type':'application/x-www-form-urlencoded', Origin:origin }, body }, true);
    assert.equal(denied.status, 403);
    assert.equal((await denied.json()).error, 'csrf_failed');
  }
  const approved = await h.request(path, { method:'POST', headers:{ 'Content-Type':'application/x-www-form-urlencoded', Origin:h.config.origin }, body }, true);
  assert.equal(approved.status, 303);
  const callback = await h.consent(approved);
  assert.ok(callback.searchParams.get('code'));
  const token = await h.form('/oauth/token', { client_id:client.client_id, grant_type:'authorization_code', code:callback.searchParams.get('code'), redirect_uri:client.redirect_uris[0], code_verifier:verifier, resource:h.resources.mcp });
  assert.equal(token.status, 200);
});

test('optional bounded token diagnostics report renewal outcomes without secret-bearing context', async t => {
  const ordinary = await harness(t);
  assert.equal(ordinary.provider.listenerCount('grant.success'), 0);
  assert.equal(ordinary.provider.listenerCount('grant.error'), 0);
  const outcomes = [];
  const h = await harness(t, {}, { tokenOutcomeObserver:outcome => outcomes.push(outcome) });
  const tokens = await h.userTokens(h.resources.mcp);
  const refreshed = await h.form('/oauth/token', { client_id:tokens.client.client_id, grant_type:'refresh_token', refresh_token:tokens.refresh_token, resource:h.resources.mcp });
  assert.equal(refreshed.status, 200);
  const renewed = await refreshed.json();
  const invalidScope = await h.form('/oauth/token', { client_id:tokens.client.client_id, grant_type:'refresh_token', refresh_token:renewed.refresh_token, resource:h.resources.mcp, scope:'projects:read offline_access device:self private-ungranted-scope' });
  assert.equal(invalidScope.status, 400);
  const mismatch = outcomes.find(outcome => outcome.error_code === 'invalid_scope');
  assert.equal(mismatch.requested_scope_membership.known['projects:read'], true);
  assert.equal(mismatch.requested_scope_membership.known.offline_access, true);
  assert.equal(mismatch.missing_scope_membership.known.offline_access, true);
  assert.equal(mismatch.missing_scope_membership.known['device:self'], true);
  assert.equal(mismatch.missing_scope_membership.known['projects:read'], false);
  assert.equal(mismatch.missing_scope_membership.unknown, true);
  const failed = await h.form('/oauth/token', { client_id:tokens.client.client_id, grant_type:'refresh_token', refresh_token:'private-invalid-refresh-value', resource:h.resources.mcp });
  assert.equal(failed.status, 400);
  assert.ok(outcomes.some(outcome => outcome.refresh_attempt && outcome.client_type === 'native' && outcome.status === 200 && outcome.error_code === null));
  assert.ok(outcomes.some(outcome => outcome.refresh_attempt && outcome.client_type === 'native' && outcome.status === 400 && outcome.error_code === 'invalid_grant'));
  const response = Object.assign(new EventEmitter(), { statusCode:400 });
  h.provider.emit('grant.error', { res:response, oidc:{ route:'token', client:{ clientId:'private-client-id', applicationType:'native' }, params:{ grant_type:'refresh_token', refresh_token:'private-refresh', scope:'private-scope' } }, body:{ access_token:'private-access' }, headers:{ Authorization:'private-header' } }, { error:'private-raw-error', message:'private-message' });
  response.emit('finish');
  assert.equal(outcomes.at(-1).error_code, 'other_error');
  for (const outcome of outcomes) assert.deepEqual(Object.keys(outcome).sort(), outcome.error_code === 'invalid_scope' ? ['client_type', 'error_code', 'missing_scope_membership', 'refresh_attempt', 'requested_scope_membership', 'status'] : ['client_type', 'error_code', 'refresh_attempt', 'status']);
  assert.ok(!JSON.stringify(outcomes).includes('private-'));
  for (let i = 0; i < 65; i++) {
    const res = Object.assign(new EventEmitter(), { statusCode:200 });
    h.provider.emit('grant.success', { res, oidc:{ route:'token', params:{ grant_type:'refresh_token' } } });
    res.emit('finish');
  }
  assert.equal(outcomes.length, 60);
});

test('explicit offline access requests get real consent and renew only granted project permissions', async t => {
  const h = await harness(t);
  const metadata = await (await h.request('/.well-known/oauth-authorization-server')).json();
  const scope = metadata.scopes_supported.join(' ');
  const registered = await h.post('/oauth/register', { client_name:'Pilot offline client', redirect_uris:['http://127.0.0.1:4567/callback'], token_endpoint_auth_method:'none', scope });
  assert.equal(registered.status, 201);
  const client = await registered.json(), verifier = 'o'.repeat(43);
  const params = { client_id:client.client_id, response_type:'code', redirect_uri:client.redirect_uris[0], scope, resource:h.resources.mcp, code_challenge:createHash('sha256').update(verifier).digest('base64url'), code_challenge_method:'S256' };
  const initial = await h.request(`/oauth/authorize?${new URLSearchParams(params)}`, {}, true);
  const interaction = h.store.db.prepare("SELECT payload FROM oauth WHERE model='Interaction'").get();
  assert.equal(JSON.parse(interaction.payload).params.prompt, 'consent');
  let explained = false;
  const originalSend = h.app.response.send;
  t.after(() => { h.app.response.send = originalSend; });
  h.app.response.send = function(body) { if (typeof body === 'string' && body.includes('action="/interaction/') && body.includes('/confirm')) explained ||= body.includes('accès continu') && body.includes('révocable'); return originalSend.call(this, body); };
  const callback = await h.consent(initial);
  assert.equal(explained, true);
  const response = await h.form('/oauth/token', { client_id:client.client_id, grant_type:'authorization_code', code:callback.searchParams.get('code'), redirect_uri:client.redirect_uris[0], code_verifier:verifier, resource:h.resources.mcp });
  assert.equal(response.status, 200);
  const tokens = await response.json();
  const refresh = await h.provider.RefreshToken.find(tokens.refresh_token);
  assert.ok(refresh.scopes.has('offline_access'));
  for (const extra of ['devices:manage', 'device:self', 'ungranted-permission']) {
    const denied = await h.form('/oauth/token', { client_id:client.client_id, grant_type:'refresh_token', refresh_token:tokens.refresh_token, resource:h.resources.mcp, scope:scope+' '+extra });
    assert.equal(denied.status, 400);
    assert.equal((await denied.json()).error, 'invalid_scope');
  }
  const renewed = await h.form('/oauth/token', { client_id:client.client_id, grant_type:'refresh_token', refresh_token:tokens.refresh_token, resource:h.resources.mcp, scope });
  assert.equal(renewed.status, 200);
  const result = await renewed.json();
  assert.deepEqual(result.scope.split(' ').sort(), ['projects:context:write', 'projects:read', 'projects:state:execute', 'projects:state:refresh']);
  assert.ok((await h.provider.RefreshToken.find(result.refresh_token)).scopes.has('offline_access'));
  const passive = await h.request(`/oauth/authorize?${new URLSearchParams({ ...params, prompt:'none' })}`, {}, true);
  assert.equal(passive.status, 303);
  assert.equal(new URL(passive.headers.get('location')).origin, 'http://127.0.0.1:4567');
  const interactive = await h.request(`/oauth/authorize?${new URLSearchParams({ ...params, prompt:'login' })}`, {}, true);
  assert.equal(interactive.status, 303);
  const explicit = h.store.db.prepare("SELECT payload FROM oauth WHERE model='Interaction'").get();
  assert.equal(JSON.parse(explicit.payload).params.prompt, 'login');
});

const collectionProposal = () => ({ content_tier:'conversation_visible_v1', collection_enabled:true, roots:['/home/alex/.codex/sessions'], exclusions:['/home/alex/work/alpha/private'], project_bindings:[{project_id:'project',cwd_roots:['/home/alex/work/alpha']}] });
const bearer = token => ({Authorization:`Bearer ${token.access_token}`});
async function presence(h) {
  const request = await h.deviceRequest(); await h.approveDevice(request);
  const response = await h.poll(request); assert.equal(response.status, 200); return response.json();
}
async function proposal(h, tokens, policy = collectionProposal()) {
  const response = await h.request('/v1/devices/self/policy-proposals', {method:'POST',headers:{...bearer(tokens),'Content-Type':'application/json'},body:JSON.stringify({proposed_policy:policy})});
  assert.equal(response.status, 201, await response.clone().text());
  const pending = await response.json();
  const authorization = await h.form('/oauth/device_authorization', {client_id:'hoomy-collector',scope:'device:self evidence:ingest',resource:h.resources.collector,enrolment_ticket:pending.enrolment_ticket});
  assert.equal(authorization.status, 200, await authorization.clone().text());
  return {...await authorization.json(),...pending};
}
async function collection(h, tokens) {
  const request = await proposal(h, tokens); await h.approveDevice(request);
  const response = await h.poll(request); assert.equal(response.status, 200, await response.clone().text());
  return response.json();
}
function projectedEvent() {
  const projection = {version:1,messages:[{role:'user',text:'Review visible project evidence.',line_start:2,line_end:2,timestamp:null}]};
  return {schema_version:1,event_id:randomUUID(),type:'conversation.projected',project_id:'project',policy_revision:2,connector:'codex',session_id:randomUUID(),source_location:{path:'/home/alex/.codex/sessions/a.jsonl',cwd:'/home/alex/work/alpha',archive_occurrence:false,modified_at:'2026-10-12T12:00:00Z'},sequence:1,source_sha256:'a'.repeat(64),projected_sha256:canonicalDigest(projection),observed_at:'2026-10-12T12:01:00Z',title:'Pilot evidence',coverage:{excluded_records:1,excluded_sensitive_messages:0,excluded_context_messages:0,excluded_oversized_records:0},projection};
}
const upload = (h, tokens, event, raw) => h.request('/v1/events', {method:'POST',headers:{...bearer(tokens),'Content-Type':'application/json','Idempotency-Key':event.event_id},body:raw ?? JSON.stringify(event)});

test('collection requires a new reviewed grant; denial and expiration preserve presence and never widen refresh', async t => {
  const h = await harness(t, {ingestionProjectId:'project'}, {pilot:async () => ({projects:[{id:'project',name:'NoFlinch'}]})});
  const old = await presence(h);
  assert.equal((await upload(h, old, projectedEvent())).status, 403);
  const declined = await proposal(h, old); await h.approveDevice(declined, 'deny');
  assert.equal((await (await h.poll(declined)).json()).error, 'access_denied');
  const self = await h.request('/v1/devices/self', {headers:bearer(old)}); assert.equal(self.status, 200);
  const previous = await self.json(); assert.equal(previous.policy_revision, 1); assert.equal(previous.collection_available, false);
  const scopeAttempt = await h.form('/oauth/token', {client_id:'hoomy-collector',grant_type:'refresh_token',refresh_token:old.refresh_token,resource:h.resources.collector,scope:'device:self evidence:ingest'});
  assert.equal(scopeAttempt.status, 400); assert.equal((await scopeAttempt.json()).error, 'invalid_scope');
  const expiring = await proposal(h, old);
  h.store.db.prepare("UPDATE oauth SET payload=json_set(payload,'$.exp',?) WHERE model='DeviceCode' AND id=?").run(Math.floor(Date.now()/1000)-1, expiring.device_code);
  assert.equal((await (await h.poll(expiring)).json()).error, 'expired_token');
  assert.equal((await h.request('/v1/devices/self', {headers:bearer(old)})).status, 200);
  const upgraded = await collection(h, old);
  assert.deepEqual(upgraded.scope.split(' ').sort(), ['device:self','evidence:ingest']);
  assert.equal((await h.request('/v1/devices/self', {headers:bearer(old)})).status, 401);
  const now = await (await h.request('/v1/devices/self', {headers:bearer(upgraded)})).json();
  assert.equal(now.device_id, previous.device_id); assert.equal(now.policy_revision, 2); assert.equal(now.collection_available, true);
  assert.deepEqual(now.policy, collectionProposal());
});

test('collector tickets cannot escalate metadata scope and a revoked previous grant cannot approve a pending collection policy', async t => {
  const h = await harness(t,{ingestionProjectId:'project'},{pilot:async () => ({projects:[{id:'project',name:'NoFlinch'}]})});
  const metadata = await h.deviceRequest();
  const escalation = await h.form('/oauth/device_authorization',{client_id:'hoomy-collector',scope:'device:self evidence:ingest',resource:h.resources.collector,enrolment_ticket:metadata.enrolment_ticket});
  assert.equal(escalation.status,400); assert.equal((await escalation.json()).error,'invalid_scope');
  await h.approveDevice(metadata); const old = await (await h.poll(metadata)).json();
  const pending = await proposal(h,old); const review = await h.approveDevice(pending,'review');
  assert.match(review.html,/NoFlinch/); assert.match(review.html,/\/home\/alex\/work\/alpha/); assert.match(review.html,/révocable/); assert.ok(!review.html.includes('project_bindings'));
  const revoked = await h.form('/oauth/revoke',{client_id:'hoomy-collector',token:old.refresh_token,token_type_hint:'refresh_token'}); assert.equal(revoked.status,200);
  const confirmation = await h.form(review.confirm,{...hidden(review.html),decision:'approve'},true); assert.equal(confirmation.status,409);
  assert.equal((await (await h.poll(pending)).json()).error,'authorization_pending');
  assert.equal(h.store.list('device')[0].status,'revoked');
  const client = await h.post('/oauth/register',{client_name:'Escalating user',redirect_uris:['http://127.0.0.1:4567/callback'],scope:'projects:read evidence:ingest'}); assert.equal(client.status,400);
});

test('ingestion verifies durable correlated receipts, strict wire data, ownership and immediate revocation', async t => {
  const receipts = new Map(), envelopes = [];
  let incomplete = false;
  const h = await harness(t, {ingestionProjectId:'project'}, {pilot:async (path, body) => {
    if (path === '/api/projects') return {projects:[{id:'project',name:'NoFlinch'}]};
    if (path === '/api/ingestion/events') {
      envelopes.push(body);
      const key = `${body.principal.device_id}:${body.event.event_id}`;
      const hash = canonicalDigest(body.event);
      if (receipts.has(key)) { const existing = receipts.get(key); if (existing.event_sha256 !== hash) throw new HttpError(409, 'idempotency_conflict', 'Different event bytes.'); return existing; }
      const receipt = {receipt_id:body.event.event_id,event_id:body.event.event_id,project_id:body.event.project_id,projected_sha256:body.event.projected_sha256,event_sha256:hash,state:incomplete?'pending':'stored',inference_state:'not_requested',received_at:'2026-10-12T12:01:01Z',source_version_id:'remote-version:'+hash};
      if (!incomplete) receipts.set(key, receipt); return receipt;
    }
    if (path === '/api/ingestion/receipts/query') {
      const value = receipts.get(`${body.principal.device_id}:${body.event_id}`);
      if (!value) throw new HttpError(404, 'receipt_not_found', 'No receipt for this machine.'); return value;
    }
    return {project:{id:'project'},revision:'current'};
  }});
  const user = await h.userTokens(); const tokens = await collection(h, await presence(h));
  const event = projectedEvent();
  incomplete = true; assert.equal((await upload(h, tokens, event)).status, 502); assert.equal(receipts.size, 0);
  incomplete = false; const stored = await upload(h, tokens, event); assert.equal(stored.status, 202);
  const receipt = await stored.json(); assert.equal(receipt.state, 'stored');
  const again = await upload(h, tokens, event); assert.equal(again.status, 202); assert.deepEqual(await again.json(), receipt); assert.equal(receipts.size, 1);
  assert.equal((await upload(h, tokens, {...event,title:'Changed same key'})).status, 409);
  assert.equal((await upload(h, tokens, event, JSON.stringify(event).replace('"schema_version":1','"schema_version":1,"schema_version":1'))).status, 400);
  assert.equal((await upload(h, tokens, {...event,policy_revision:1})).status, 409);
  assert.equal((await upload(h, tokens, {...event,project_id:'another'})).status, 403);
  assert.equal((await upload(h, tokens, {...event,principal:{account_id:'github:other'}})).status, 400);
  const compressed = await h.request('/v1/events',{method:'POST',headers:{...bearer(tokens),'Content-Type':'application/json','Content-Encoding':'gzip','Idempotency-Key':event.event_id},body:'not-compressed'}); assert.equal(compressed.status,415);
  const oversized = await h.request('/v1/events',{method:'POST',headers:{...bearer(tokens),'Content-Type':'application/json','Idempotency-Key':event.event_id},body:' '.repeat(24*1024*1024+1)}); assert.equal(oversized.status,413);
  const forwarded = envelopes.at(-1); assert.equal(forwarded.principal.account_id, 'github:42'); assert.equal(forwarded.policy.revision, 2); assert.equal(forwarded.policy.id, forwarded.principal.device_id);
  assert.equal((await h.request(`/v1/receipts/${event.event_id}`, {headers:bearer(tokens)})).status, 200);
  const other = await collection(h, await presence(h));
  assert.equal((await h.request(`/v1/receipts/${event.event_id}`, {headers:bearer(other)})).status, 404);
  const self = await (await h.request('/v1/devices/self',{headers:bearer(tokens)})).json();
  const revoked = await h.request(`/v1/devices/${self.device_id}/revoke`,{method:'POST',headers:{...bearer(user),'Content-Type':'application/json'},body:'{}'}); assert.equal(revoked.status,200);
  assert.equal((await upload(h,tokens,projectedEvent())).status,401);
  assert.equal((await h.request(`/v1/receipts/${event.event_id}`,{headers:bearer(tokens)})).status,401);
  assert.equal((await h.request('/v1/devices/self',{headers:bearer(other)})).status,200);
  assert.equal((await h.request('/v1/projects/project',{headers:bearer(user)})).status,200);
});

test('cached project evidence accepts only user project scope and sends bounded verified identity to one private route', async t => {
  const calls = [];
  const h = await harness(t, {}, {pilot:async (path, body) => {calls.push({path,body}); return {project_id:body.project_id,source_id:body.source_id,messages:[{role:'user',text:'Cached excerpt'}]};}});
  const user = await h.userTokens(); const collector = await presence(h); const source = 'remote:'+'b'.repeat(64);
  const url = `/v1/projects/project/evidence?source_id=${source}&offset=1&limit=2`;
  assert.equal((await h.request(url,{headers:bearer(collector)})).status,401);
  assert.equal((await h.request(url+'&limit=1000',{headers:bearer(user)})).status,400);
  assert.equal((await h.request(url+'&principal=outsider',{headers:bearer(user)})).status,400);
  assert.equal(calls.length,0);
  assert.equal((await h.request(url,{headers:bearer(user)})).status,200);
  assert.deepEqual(calls, [{path:'/api/ingestion/evidence/query',body:{principal:{account_id:'github:42',workspace_id:'pilot'},project_id:'project',source_id:source,offset:1,limit:2}}]);
});

test('source exclusion receipts require durable correlation, current collection scope and own-device access', async t => {
  const receipts = new Map(), envelopes = [];
  let wrongHash = false, mixedReceipt = false;
  const h = await harness(t,{ingestionProjectId:'project'},{pilot:async (path,body) => {
    if (path === '/api/projects') return {projects:[{id:'project',name:'NoFlinch'}]};
    const key = `${body.principal.device_id}:${body.event?.event_id || body.event_id}`;
    if (path === '/api/ingestion/events') {
      envelopes.push(body);
      const hash = canonicalDigest(body.event);
      const receipt = {receipt_id:body.event.event_id,event_id:body.event.event_id,project_id:body.event.project_id,event_sha256:hash,state:'stored',inference_state:'not_requested',received_at:'2026-10-12T12:01:01Z',source_version_id:'remote-version:'+hash,event_type:body.event.type,header_sha256:wrongHash?'c'.repeat(64):body.event.exclusion.header_sha256};
      if (mixedReceipt) receipt.projected_sha256 = 'd'.repeat(64);
      if (!wrongHash && !mixedReceipt) receipts.set(key,receipt);
      return receipt;
    }
    const receipt = receipts.get(key);
    if (!receipt) throw new HttpError(404,'receipt_not_found','No receipt for this machine.');
    return receipt;
  }});
  const old = await presence(h);
  const {source_sha256,projected_sha256,title,coverage,projection,...common} = projectedEvent();
  const event = {...common,type:'conversation.source_excluded',exclusion:{reason:'internal_approval_review',discriminator:'internal_guardian',header_sha256:'b'.repeat(64)}};
  assert.equal((await upload(h,old,event)).status,403); assert.equal(envelopes.length,0);
  const tokens = await collection(h,old);
  wrongHash = true; assert.equal((await upload(h,tokens,event)).status,502);
  wrongHash = false; mixedReceipt = true; assert.equal((await upload(h,tokens,event)).status,502);
  mixedReceipt = false;
  const response = await upload(h,tokens,event); assert.equal(response.status,202);
  const receipt = await response.json(); assert.equal(Object.keys(receipt).length,10); assert.ok(!Object.hasOwn(receipt,'projected_sha256'));
  assert.deepEqual(await (await upload(h,tokens,event)).json(),receipt);
  assert.deepEqual(await (await h.request(`/v1/receipts/${event.event_id}`,{headers:bearer(tokens)})).json(),receipt);
  const admitted = envelopes.length;
  for (const change of [{projection},{exclusion:{...event.exclusion,discriminator:'thread_source_guardian'}},{exclusion:{...event.exclusion,header_sha256:[event.exclusion.header_sha256]}}]) assert.equal((await upload(h,tokens,{...event,...change})).status,400);
  assert.equal((await upload(h,tokens,{...event,policy_revision:1})).status,409);
  assert.equal((await upload(h,tokens,{...event,source_location:{...event.source_location,cwd:'/outside'}})).status,403);
  assert.equal(envelopes.length,admitted);
  const other = await collection(h,await presence(h));
  assert.equal((await h.request(`/v1/receipts/${event.event_id}`,{headers:bearer(other)})).status,404);
  const revoked = await h.form('/oauth/revoke',{client_id:'hoomy-collector',token:tokens.refresh_token,token_type_hint:'refresh_token'});
  assert.equal(revoked.status,200);
  assert.equal((await upload(h,tokens,event)).status,401);
  assert.equal((await h.request(`/v1/receipts/${event.event_id}`,{headers:bearer(tokens)})).status,401);
});

test('state generation and execution require concrete new native consent and cannot widen an existing refresh grant', async t => {
  const h = await harness(t);
  const old = await h.userTokens(h.resources.mcp);
  const expansion = await h.form('/oauth/token', {client_id:old.client.client_id,grant_type:'refresh_token',refresh_token:old.refresh_token,resource:h.resources.mcp,scope:'projects:read projects:context:write projects:state:refresh projects:state:execute'});
  assert.equal(expansion.status,400);assert.equal((await expansion.json()).error,'invalid_scope');
  const untouched = await h.authenticateRequest({headers:{authorization:`Bearer ${old.access_token}`}},{resource:h.resources.mcp});
  assert.ok(!untouched.scopes.includes('projects:state:refresh'));assert.ok(!untouched.scopes.includes('projects:state:execute'));
  const verifier = 's'.repeat(43), scope = 'projects:read projects:context:write projects:state:refresh projects:state:execute offline_access';
  const registered = await h.post('/oauth/register',{client_name:'State native client',redirect_uris:['http://127.0.0.1:4567/callback'],scope,token_endpoint_auth_method:'none'});assert.equal(registered.status,201);const client=await registered.json();
  let concrete = false, markers;
  const send = h.app.response.send;t.after(()=>{h.app.response.send=send;});
  h.app.response.send = function(body) {
    if (typeof body==='string' && body.includes('/confirm')) { markers = {scope:body.includes('projects:state:execute'),quota:body.includes('quota Codex'),input:body.includes('extraits centraux sélectionnés'),context:body.includes('objectifs approuvés')}; concrete ||= Object.values(markers).every(Boolean); }
    return send.call(this,body);
  };
  const initial = await h.request('/oauth/authorize?'+new URLSearchParams({client_id:client.client_id,response_type:'code',redirect_uri:client.redirect_uris[0],scope,resource:h.resources.mcp,code_challenge:createHash('sha256').update(verifier).digest('base64url'),code_challenge_method:'S256'}),{},true);
  const callback = await h.consent(initial);assert.equal(callback.searchParams.get('error'),null);assert.deepEqual(markers,{scope:true,quota:true,input:true,context:true});assert.equal(concrete,true);
  const response = await h.form('/oauth/token',{client_id:client.client_id,grant_type:'authorization_code',code:callback.searchParams.get('code'),redirect_uri:client.redirect_uris[0],code_verifier:verifier,resource:h.resources.mcp});
  assert.equal(response.status,200);const issued = await response.json();
  const principal = await h.authenticateRequest({headers:{authorization:`Bearer ${issued.access_token}`}},{resource:h.resources.mcp,scopes:['projects:state:refresh','projects:state:execute']});
  assert.ok(!principal.device_id);assert.ok(!principal.scopes.includes('evidence:ingest'));assert.ok(!principal.scopes.includes('devices:manage'));
  assert.ok((await h.provider.RefreshToken.find(issued.refresh_token)).scopes.has('offline_access'));
  assert.equal((await h.authenticateRequest({headers:{authorization:`Bearer ${old.access_token}`}},{resource:h.resources.mcp})).grant_id,untouched.grant_id);
});
