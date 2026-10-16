import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { createRemoteMcpHandler, mcpResourceMetadata } from '../mcp.mjs';

const resource = 'https://my.hoomy.dev/mcp';
const scopes = ['projects:read', 'projects:context:write'];

test('remote discovery advertises ongoing consent without widening tool access', () => {
  assert.deepEqual(mcpResourceMetadata(resource, 'https://my.hoomy.dev').scopes_supported,
    [...scopes, 'projects:state:refresh', 'projects:state:execute', 'offline_access']);
});

async function listen(server) {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return `http://127.0.0.1:${server.address().port}`;
}

async function fixture(t) {
  const calls = [];
  let revision = 'r1';
  const backend = createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = chunks.length ? JSON.parse(Buffer.concat(chunks)) : undefined;
    calls.push({ path: req.url, body, headers: req.headers });
    res.setHeader('Content-Type', 'application/json');
    if (req.url === '/api/projects') {
      res.end(JSON.stringify({ projects: [{ id: 'p', name: 'Example' }], control: { inference_enabled: true } })); return;
    }
    if (req.url === '/api/ingestion/evidence/query') {
      res.end(JSON.stringify({ project_id: body.project_id, source_id: body.source_id, messages: [] })); return;
    }
    if (req.url.startsWith('/api/project-state/')) {
      res.end(JSON.stringify({project_id:body.project_id,run_id:body.run_id||'11111111-1111-4111-8111-111111111111',status:'queued',enabled:true,context_revision:'a'.repeat(64),corpus_revision:'b'.repeat(64)}));return;
    }
    if (body && body.revision !== revision) {
      res.writeHead(409).end(JSON.stringify({ error: 'The project changed; reload its current revision.' })); return;
    }
    if (body) revision = 'r2';
    res.end(JSON.stringify({ project: { id: 'p', name: 'Example' }, revision, fields: {}, sources: [] }));
  });
  const backendUrl = await listen(backend);
  const grants = new Map([
    ['user', { account_id: 'u', workspace_id: 'w', scopes, audience: resource }],
    ['read', { account_id: 'u', workspace_id: 'w', scopes: ['projects:read'], audience: resource }],
    ['device', { account_id: 'u', workspace_id: 'w', device_id: 'd', scopes, audience: resource }],
    ['other-resource', { account_id: 'u', workspace_id: 'w', scopes, audience: 'https://my.hoomy.dev/v1' }],
    ['refresh',{account_id:'u',workspace_id:'w',scopes:['projects:read','projects:state:refresh'],audience:resource}],
    ['execute',{account_id:'u',workspace_id:'w',scopes:['projects:read','projects:state:execute'],audience:resource}],
  ]);
  const audit = [];
  let auditFailure = false;
  const handler = createRemoteMcpHandler({
    backendUrl, resourceUrl: resource, authorizationServerUrl: 'https://my.hoomy.dev',stateProjectId:'p',
    onProjectUpdated(event) {
      if (auditFailure) throw new Error('An internal audit credential must never appear in the response.');
      audit.push(event);
    },
    authenticateRequest(req, options) {
      assert.equal(options.resource, resource);
      assert.deepEqual(options.scopes, ['projects:read']);
      const principal = grants.get(req.headers.authorization?.replace(/^Bearer /, ''));
      if (!principal) throw Object.assign(new Error('Sensitive verification detail'), { status: 401, code: 'invalid_token' });
      return principal;
    },
  });
  const gateway = createServer(handler);
  const url = `${await listen(gateway)}/mcp`;
  t.after(async () => {
    backend.closeAllConnections(); gateway.closeAllConnections();
    await Promise.all([new Promise(resolve => backend.close(resolve)), new Promise(resolve => gateway.close(resolve))]);
  });
  async function client(token = 'user') {
    const client = new Client({ name: 'hoomy-test', version: '1.0.0' });
    await client.connect(new StreamableHTTPClientTransport(new URL(url), {
      requestInit: { headers: { Authorization: `Bearer ${token}` } },
    }));
    t.after(() => client.close());
    return client;
  }
  return { url, calls, grants, client, audit, failAudit: () => { auditFailure = true; } };
}

test('remote MCP rejects missing, revoked, device and wrong-resource credentials before backend access', async t => {
  const f = await fixture(t);
  for (const token of ['', 'device', 'other-resource', 'revoked']) {
    const response = await fetch(f.url, {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list' }),
    });
    assert.equal(response.status, 401);
    assert.match(response.headers.get('www-authenticate'), /oauth-protected-resource\/mcp/);
    assert.doesNotMatch(await response.text(), /Sensitive verification detail/);
  }
  assert.deepEqual(f.calls, []);
});

test('state refresh and execution permissions are separate and cannot bypass the pilot or actor binding',async t=>{
  const f=await fixture(t),request={project_id:'p',request_id:'11111111-1111-4111-8111-111111111111',context_revision:'a'.repeat(64),corpus_revision:'b'.repeat(64),method_hash:'c'.repeat(64)};
  const read=await f.client('read'),refresh=await f.client('refresh'),execute=await f.client('execute');
  assert.equal((await read.callTool({name:'hoomy_refresh_project_state',arguments:request})).isError,true);
  assert.equal((await execute.callTool({name:'hoomy_refresh_project_state',arguments:request})).isError,true);
  const worker={action:'claim',project_id:'p',run_id:request.request_id,worker_id:request.request_id};
  assert.equal((await refresh.callTool({name:'hoomy_project_state_worker',arguments:worker})).isError,true);
  assert.equal(f.calls.length,0);
  assert.equal((await refresh.callTool({name:'hoomy_refresh_project_state',arguments:request})).isError,undefined);
  assert.deepEqual(f.calls.at(-1).body.principal,{account_id:'u',workspace_id:'w'});
  assert.equal(f.calls.at(-1).body.method_hash,request.method_hash);
  assert.equal(Object.hasOwn(f.calls.at(-1).body,'retry_of_run_id'),false);
  const retry={...request,retry_of_run_id:'22222222-2222-4222-8222-222222222222'};
  assert.equal((await execute.callTool({name:'hoomy_refresh_project_state',arguments:retry})).isError,true);
  assert.equal((await refresh.callTool({name:'hoomy_refresh_project_state',arguments:retry})).isError,undefined);
  assert.equal(f.calls.at(-1).body.retry_of_run_id,retry.retry_of_run_id);
  assert.deepEqual(f.calls.at(-1).body.principal,{account_id:'u',workspace_id:'w'});
  assert.equal((await execute.callTool({name:'hoomy_project_state_worker',arguments:worker})).isError,undefined);
  const unavailable=await refresh.callTool({name:'hoomy_refresh_project_state',arguments:{...request,project_id:'other'}});
  assert.equal(unavailable.isError,true);
  assert.equal(unavailable._meta?.['mcp/www_authenticate'],undefined);
  const before=f.calls.length;
  assert.equal((await refresh.callTool({name:'hoomy_refresh_project_state',arguments:{...request,account_id:'forged'}})).isError,true);
  assert.equal((await refresh.callTool({name:'hoomy_refresh_project_state',arguments:{...retry,retry_of_run_id:'not-a-run'}})).isError,true);
  assert.equal(f.calls.length,before);
});

test('native SDK advertises and enforces the current two-extraction slot limit before backend dispatch',async t=>{
  const f=await fixture(t),execute=await f.client('execute');
  const schema=(await execute.listTools()).tools.find(tool=>tool.name==='hoomy_project_state_worker').inputSchema;
  const attempt=schema.anyOf.find(branch=>branch.properties.action.const==='attempt');
  assert.deepEqual(attempt.properties.chunk_index.anyOf,[{type:'integer',minimum:0,maximum:1},{type:'null'}]);
  const request={action:'attempt',project_id:'p',run_id:'11111111-1111-4111-8111-111111111111',lease_id:'22222222-2222-4222-8222-222222222222',
    call_id:'33333333-3333-4333-8333-333333333333',input_hash:'a'.repeat(64),phase:'extract',chunk_index:1};
  assert.equal((await execute.callTool({name:'hoomy_project_state_worker',arguments:request})).isError,undefined);
  assert.equal(f.calls.at(-1).body.chunk_index,1);
  const before=f.calls.length;request.chunk_index=2;
  assert.equal((await execute.callTool({name:'hoomy_project_state_worker',arguments:request})).isError,true);
  assert.equal(f.calls.length,before);
});

test('native SDK finish preserves Unicode scalar limits and acknowledged effort without accepting excess text',async t=>{
  const f=await fixture(t),execute=await f.client('execute');
  const request={action:'finish',project_id:'p',run_id:'11111111-1111-4111-8111-111111111111',lease_id:'22222222-2222-4222-8222-222222222222',
    call_id:'33333333-3333-4333-8333-333333333333',input_hash:'a'.repeat(64),
    method:{adapter:'codex-native-v1',model:'gpt-6.1-sol',reasoning_effort:'low',elapsed_ms:1,usage:{input_tokens:10,output_tokens:20,total_tokens:30}},
    output:{schema_version:1,assertions:[{key:'𠀀'.repeat(40),topic:'Fictional Unicode report',text:'Historical record only.',kind:'reported_status',reported_state:'unknown',limitations:[],
      source_refs:[{passage_id:'p0001_01'}]}],uncertainties:[],contradictions:[]}};
  assert.equal((await execute.callTool({name:'hoomy_project_state_worker',arguments:request})).isError,undefined);
  assert.deepEqual(f.calls.at(-1).body.output,request.output);
  assert.equal(f.calls.at(-1).body.method.reasoning_effort,'low');
  const valid=structuredClone(request),prior=f.calls.length;
  valid.output.assertions[0].source_refs=[{message_alias:'m1',quote:'A generated substitute quote'}];
  assert.equal((await execute.callTool({name:'hoomy_project_state_worker',arguments:valid})).isError,true);
  assert.equal(f.calls.length,prior);
  const before=f.calls.length;request.output.assertions[0].key+='𠀀';
  assert.equal((await execute.callTool({name:'hoomy_project_state_worker',arguments:request})).isError,true);
  assert.equal(f.calls.length,before);
});

test('remote tools and existing embedded UI use one authenticated streamable HTTP connection', async t => {
  const f = await fixture(t);
  const client = await f.client();
  const { tools } = await client.listTools();
  assert.deepEqual(tools.map(tool => tool.name).sort(), ['hoomy_home', 'hoomy_project', 'hoomy_project_guide', 'hoomy_update_project', 'hoomy_project_evidence',
    'hoomy_project_state', 'hoomy_refresh_project_state', 'hoomy_project_state_job', 'hoomy_project_state_citation', 'hoomy_project_state_worker'].sort());
  assert.ok(tools.every(tool=>tool.inputSchema.type==='object'));
  const home = tools.find(tool => tool.name === 'hoomy_home');
  assert.deepEqual(home._meta.securitySchemes, [{ type: 'oauth2', scopes: ['projects:read'] }]);
  // The SDK client strips extension fields; verify the host-facing wire contract.
  const listed = await fetch(f.url, {
    method: 'POST', headers: { Authorization: 'Bearer user', 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 3, method: 'tools/list' }),
  });
  const wire = await listed.json();
  assert.deepEqual(wire.result.tools.find(tool => tool.name === 'hoomy_home').securitySchemes, home._meta.securitySchemes);
  assert.deepEqual(home._meta['openai/ui'].entrypoints, [{ type: 'global' }]);
  const html = await client.readResource({ uri: home._meta.ui.resourceUri });
  assert.match(html.contents[0].text, /tools\/call/);
  assert.doesNotMatch(html.contents[0].text, /Bearer user|refresh_token/);
  const result = await client.callTool({ name: 'hoomy_home', arguments: {} });
  assert.equal(result.structuredContent.projects[0].id, 'p');
  assert.equal(result.structuredContent.capabilities.research, false);
  assert.equal(result.structuredContent.capabilities.cached_project_evidence, true);
  assert.deepEqual(result.structuredContent.connection, { mode: 'central', origin: 'https://my.hoomy.dev', resource });
  assert.match(html.contents[0].text, /Projets centralisés/);
  assert.deepEqual(f.calls.map(call => call.path), ['/api/projects']);
  assert.equal(f.calls[0].headers.authorization, undefined);
  const unavailable = await client.callTool({ name: 'hoomy_choose', arguments: { instruction: 'Start research' } });
  assert.equal(unavailable.isError, true);
  assert.equal(f.calls.length, 1);
});

test('cached evidence uses the authenticated user principal and refuses identity-bearing tool arguments', async t => {
  const f = await fixture(t);
  const client = await f.client('read');
  const result = await client.callTool({ name:'hoomy_project_evidence', arguments:{project_id:'p',source_id:'remote:hash'} });
  assert.equal(result.isError, undefined);
  assert.deepEqual(f.calls[0].body,{principal:{account_id:'u',workspace_id:'w'},project_id:'p',source_id:'remote:hash',offset:0,limit:20});
  assert.equal(f.calls[0].headers.authorization, undefined);
  const forged = await client.callTool({ name:'hoomy_project_evidence', arguments:{project_id:'p',source_id:'remote:hash',account_id:'victim'} });
  assert.equal(forged.isError,true);
  assert.equal(f.calls.length,1);
});

test('read-only user grant cannot edit context and receives a tool-level OAuth challenge', async t => {
  const f = await fixture(t);
  const client = await f.client('read');
  const result = await client.callTool({ name: 'hoomy_update_project', arguments: { project_id: 'p', revision: 'r1', fields: { goal: 'Approved goal' } } });
  assert.equal(result.isError, true);
  assert.match(result._meta['mcp/www_authenticate'][0], /insufficient_scope/);
  assert.deepEqual(f.calls, []);
});

test('remote context edits preserve backend revisions and never overwrite a stale edit', async t => {
  const f = await fixture(t);
  const client = await f.client();
  const edit = { project_id: 'p', revision: 'r1', fields: { goal: 'Approved goal' } };
  const saved = await client.callTool({ name: 'hoomy_update_project', arguments: edit });
  assert.equal(saved.structuredContent.revision, 'r2');
  assert.deepEqual(f.calls[0].body, edit);
  assert.equal(f.audit.length, 1);
  assert.equal(f.audit[0].principal.account_id, 'u');
  assert.equal(f.audit[0].project_id, 'p');
  assert.equal(f.audit[0].previous_revision, 'r1');
  assert.equal(f.audit[0].revision, 'r2');
  assert.deepEqual(f.audit[0].field_names, ['goal']);
  assert.doesNotMatch(JSON.stringify(f.audit), /Approved goal|Bearer user/);
  const stale = await client.callTool({ name: 'hoomy_update_project', arguments: edit });
  assert.equal(stale.isError, true);
  assert.equal(f.audit.length, 1);
  assert.match(stale.content[0].text, /reload/);
  const profile = await client.callTool({ name: 'hoomy_project', arguments: { project_id: 'p' } });
  assert.equal(profile.structuredContent.revision, 'r2');
  f.grants.delete('user');
  await assert.rejects(client.callTool({ name: 'hoomy_project', arguments: { project_id: 'p' } }));
  assert.equal(f.calls.length, 3);
});

test('audit failure preserves the committed edit and reports a safe warning instead of an ambiguous retry', async t => {
  const f = await fixture(t);
  f.failAudit();
  const client = await f.client();
  const saved = await client.callTool({ name: 'hoomy_update_project', arguments: { project_id: 'p', revision: 'r1', fields: { goal: 'Approved goal' } } });
  assert.equal(saved.isError, undefined);
  assert.equal(saved.structuredContent.revision, 'r2');
  assert.equal(saved.structuredContent.warnings[0].code, 'audit_unavailable');
  assert.match(saved.structuredContent.warnings[0].message, /modification est enregistrée/);
  assert.doesNotMatch(JSON.stringify(saved), /internal audit credential/);
  const reread = await client.callTool({ name: 'hoomy_project', arguments: { project_id: 'p' } });
  assert.equal(reread.structuredContent.revision, 'r2');
  assert.equal(f.calls.filter(call => call.body).length, 1);
});

test('foreign browser origins cannot operate remote MCP with an otherwise valid bearer token', async t => {
  const f = await fixture(t);
  const response = await fetch(f.url, { method: 'POST', headers: { Authorization: 'Bearer user', Origin: 'https://attacker.example' } });
  assert.equal(response.status, 403);
  assert.deepEqual(f.calls, []);
});

test('actual SDK carries device-evidence refusals and removes model coverage-gap taxonomy',async t=>{
  const f=await fixture(t),execute=await f.client('execute');
  const schema=(await execute.listTools()).tools.find(tool=>tool.name==='hoomy_project_state_worker').inputSchema;
  const failure=schema.anyOf.find(branch=>branch.properties.action.const==='fail');
  assert.ok(failure.properties.code.enum.includes('insufficient_device_evidence'));
  assert.ok(failure.properties.code.enum.includes('unsupported_device_scope'));
  const finish=schema.anyOf.find(branch=>branch.properties.action.const==='finish');
  assert.equal(JSON.stringify(finish.properties.output).includes('coverage_gap'),false);
  const request={action:'fail',project_id:'p',run_id:'11111111-1111-4111-8111-111111111111',lease_id:'22222222-2222-4222-8222-222222222222',input_hash:'a'.repeat(64),code:'insufficient_device_evidence'};
  assert.equal((await execute.callTool({name:'hoomy_project_state_worker',arguments:request})).isError,undefined);
  assert.equal(f.calls.at(-1).body.code,'insufficient_device_evidence');
  const count=f.calls.length;request.code='invent_device_fact';
  assert.equal((await execute.callTool({name:'hoomy_project_state_worker',arguments:request})).isError,true);
  assert.equal(f.calls.length,count);
});
