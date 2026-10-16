import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash, randomUUID } from 'node:crypto';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { createGateway } from '../gateway.mjs';

const repositoryRoot = fileURLToPath(new URL('../../../', import.meta.url));
const python = process.env.HOOMY_TEST_PYTHON || 'python3';

// Seed through public Store methods, with invented evidence and no inference call.
const pythonServer = `
import base64, hashlib, json, sys, threading
from http.server import ThreadingHTTPServer
from hoomy.server import make_handler
from hoomy.store import Store, SECTIONS

store = Store(sys.argv[1])
if sys.argv[2] == 'seed':
    generation = store.state()['control']['generation']
    store.control({'consent': True, 'running': False})
    text = 'Build an isolated gateway integration test.'
    raw = json.dumps({'type': 'message', 'role': 'user', 'text': text}).encode()
    snapshot = hashlib.sha256(raw).hexdigest()
    store.ingest({'generation': generation, 'session_id': 'pilot-test-source',
                  'snapshot_id': snapshot, 'source_path': '/fictional-test/source.jsonl',
                  'cwd': '/fictional-test/project', 'modified_at': '2026-10-10T10:00:00Z',
                  'raw_b64': base64.b64encode(raw).decode(), 'warnings': [],
                  'messages': [{'id': 'pilot-message', 'role': 'user', 'text': text,
                                'line_start': 1, 'line_end': 1, 'timestamp': None}]})
    evidence = [{'message_id': 'pilot-message', 'quote': text}]
    store.analysis('pilot-test-source', {
        'generation': generation, 'snapshot_id': snapshot,
        'brief': {section: [] for section in SECTIONS},
        'method': {'adapter': 'isolated-integration-fixture', 'schema': 1},
        'projects': [{'name': 'Gateway integration project', 'purpose': text,
                      'status': 'active', 'rationale': text, 'evidence': evidence}]})
project = store.project_list()['projects'][0]
def forbid_broad_state():
    raise AssertionError('Project routes must not load broad discovery state.')
store.state = forbid_broad_state
store.overview.summary = forbid_broad_state
server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(store))
server.daemon_threads = True
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
print(json.dumps({'url': 'http://127.0.0.1:' + str(server.server_port), 'project_id': project['id']}), flush=True)
try:
    for line in sys.stdin:
        if line.strip() == 'stop':
            break
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
    store.close()
`;

async function startPilot(directory, mode) {
  const child = spawn(python, ['-u', '-c', pythonServer, directory, mode], {
    cwd: repositoryRoot, env: { ...process.env, PYTHONPATH: resolve(repositoryRoot, 'backend') }, stdio: ['pipe', 'pipe', 'pipe'],
  });
  let errors = '';
  child.stderr.on('data', chunk => { errors = (errors + chunk).slice(-4000); });
  const exited = new Promise(resolveExit => child.once('exit', resolveExit));
  const ready = await new Promise((resolveReady, reject) => {
    let output = '';
    const timer = setTimeout(() => { child.kill(); reject(new Error('The isolated Python pilot did not start.')); }, 10000);
    child.once('error', error => { clearTimeout(timer); reject(error); });
    child.once('exit', code => { clearTimeout(timer); reject(new Error(`The isolated Python pilot exited (${code}): ${errors}`)); });
    child.stdout.on('data', chunk => {
      output += chunk;
      if (output.includes('\n')) {
        clearTimeout(timer);
        resolveReady(JSON.parse(output.split('\n')[0]));
      }
    });
  });
  return { ...ready, stop: async () => {
    if (child.exitCode !== null) return;
    child.stdin.end('stop\n');
    await exited;
  } };
}

test('authenticated MCP uses the real Python pilot, rejects stale revisions and persists approved context', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'hoomy-real-pilot-'));
  const pilotDirectory = join(directory, 'pilot');
  let pilot;
  let reopened;
  let gateway;
  let client;
  let server;
  t.after(async () => {
    await client?.close();
    if (server) { server.closeAllConnections(); await new Promise(resolveClose => server.close(resolveClose)); }
    gateway?.close();
    await reopened?.stop();
    await pilot?.stop();
    await rm(directory, { recursive: true, force: true });
  });
  pilot = await startPilot(pilotDirectory, 'seed');
  const health = await fetch(`${pilot.url}/api/health`);
  assert.equal(health.status, 200);
  assert.deepEqual(await health.json(), { status: 'ok' });
  const index = await (await fetch(`${pilot.url}/api/projects`)).json();
  assert.equal(index.projects[0].id, pilot.project_id);
  let app;
  server = createServer((req, res) => app(req, res));
  await new Promise(resolveListen => server.listen(0, '127.0.0.1', resolveListen));
  const origin = `http://127.0.0.1:${server.address().port}`;
  gateway = await createGateway({
    origin, testMode: true, backendUrl: pilot.url, databasePath: join(directory, 'auth.sqlite'),
    githubClientId: 'test-client', githubClientSecret: 'test-secret', allowedGithubIds: ['42'], cookieKeys: ['x'.repeat(32)],
    clients: [{ client_id: 'pilot-integration', redirect_uris: [`${origin}/test-callback`] }],
  });
  app = gateway.app;
  const grant = new gateway.provider.Grant({ accountId: 'github:42', clientId: 'pilot-integration' });
  grant.addResourceScope(gateway.resources.mcp, 'projects:read projects:context:write');
  const grantId = await grant.save();
  const token = new gateway.provider.AccessToken({
    accountId: 'github:42', clientId: 'pilot-integration', grantId,
    scope: 'projects:read projects:context:write', expiresWithSession: false,
    resourceServer: { audience: gateway.resources.mcp, accessTokenFormat: 'opaque', accessTokenTTL: 900 },
  });
  client = new Client({ name: 'actual-pilot-integration', version: '1.0.0' });
  await client.connect(new StreamableHTTPClientTransport(new URL(`${origin}/mcp`), {
    requestInit: { headers: { Authorization: `Bearer ${await token.save()}` } },
  }));
  const home = await client.callTool({ name: 'hoomy_home', arguments: {} });
  assert.deepEqual(home.structuredContent.projects, [{ id: pilot.project_id, name: 'Gateway integration project' }]);
  const initial = await client.callTool({ name: 'hoomy_project', arguments: { project_id: pilot.project_id } });
  const profile = initial.structuredContent;
  assert.equal(profile.project.id, pilot.project_id);
  assert.equal(profile.fields.goal, null);
  assert.equal(profile.sources.length, 1);
  assert.equal(profile.capabilities.source_chat_navigation, false);
  const edit = { project_id: pilot.project_id, revision: profile.revision, fields: { goal: 'Keep project knowledge available across machines.', constraint: 'Preserve evidence provenance.' } };
  const saved = await client.callTool({ name: 'hoomy_update_project', arguments: edit });
  assert.equal(saved.isError, undefined);
  assert.notEqual(saved.structuredContent.revision, profile.revision);
  assert.equal(saved.structuredContent.fields.goal.answer, edit.fields.goal);
  assert.equal(saved.structuredContent.fields.constraint.answer, edit.fields.constraint);
  assert.equal(saved.structuredContent.ideas.length, 0);
  const stale = await client.callTool({ name: 'hoomy_update_project', arguments: { ...edit, fields: { goal: 'An obsolete competing edit.' } } });
  assert.equal(stale.isError, true);
  assert.match(stale.content[0].text, /fiche a changé/);
  // MCP tool failures use a normal result envelope; the private API retains 409.
  const conflict = await fetch(`${pilot.url}/api/projects/context`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(edit),
  });
  assert.equal(conflict.status, 409);
  const canonical = value => JSON.stringify(value, function(key, item) {
    return item && typeof item === 'object' && !Array.isArray(item)
      ? Object.fromEntries(Object.keys(item).sort().map(name => [name,item[name]])) : item;
  });
  const sha = value => createHash('sha256').update(canonical(value)).digest('hex');
  const projection = {version:1,messages:[{role:'user',text:'Une preuve conservée dans sa langue. 🌍',line_start:2,line_end:2,timestamp:null}]};
  const sourcePath='/fictional-history/sessions/source.jsonl';
  const event={schema_version:1,event_id:randomUUID(),type:'conversation.projected',project_id:pilot.project_id,policy_revision:2,
    connector:'codex',session_id:'pilot-test-source',source_location:{path:sourcePath,cwd:'/fictional-test/project',archive_occurrence:false,modified_at:'2026-10-12T12:00:00Z'},
    sequence:1,source_sha256:createHash('sha256').update('declared original fixture bytes').digest('hex'),projected_sha256:sha(projection),
    observed_at:'2026-10-12T12:01:00Z',title:'Cached integration evidence',coverage:{excluded_records:1,excluded_sensitive_messages:0,excluded_context_messages:0,excluded_oversized_records:0},projection};
  const envelope={principal:{account_id:'github:42',workspace_id:'pilot',device_id:'fixture-device'},
    policy:{id:'fixture-device',revision:2,content_tier:'conversation_visible_v1',collection_enabled:true,roots:['/fictional-history/sessions'],exclusions:[],project_bindings:[{project_id:pilot.project_id,cwd_roots:['/fictional-test/project']}]},event};
  const admitted=await fetch(`${pilot.url}/api/ingestion/events`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(envelope)});
  assert.equal(admitted.status,200);
  const receipt=await admitted.json();
  assert.equal(receipt.event_sha256,sha(event));
  const reflected=await client.callTool({name:'hoomy_project',arguments:{project_id:pilot.project_id}});
  assert.equal(reflected.structuredContent.revision,saved.structuredContent.revision);
  const remoteSource=reflected.structuredContent.sources.find(source=>source.cached);
  assert.ok(remoteSource.id.startsWith('remote:'));
  const cached=await client.callTool({name:'hoomy_project_evidence',arguments:{project_id:pilot.project_id,source_id:remoteSource.id}});
  assert.equal(cached.isError,undefined);
  assert.equal(cached.structuredContent.messages[0].text,projection.messages[0].text);
  assert.equal(cached.structuredContent.provenance.device_id,'fixture-device');
  assert.equal(cached.structuredContent.source_sha256_verified,false);
  const foreign=await client.callTool({name:'hoomy_project_evidence',arguments:{project_id:pilot.project_id,source_id:'remote:'+'0'.repeat(64)}});
  assert.equal(foreign.isError,true);
  await client.close(); client = undefined;
  await pilot.stop();
  reopened = await startPilot(pilotDirectory, 'reopen');
  const persisted = await (await fetch(`${reopened.url}/api/projects/context/${pilot.project_id}`)).json();
  assert.equal(persisted.revision, saved.structuredContent.revision);
  assert.equal(persisted.fields.goal.answer, edit.fields.goal);
  assert.equal(persisted.fields.constraint.answer, edit.fields.constraint);
  assert.equal(persisted.fields.goal.source, 'user_project_profile');
  assert.equal(persisted.ideas.length, 0);
});
