import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRemoteBridge } from '../remote-bridge.mjs';
import { dispatcher, definitions } from '../server.mjs';

test('remote home exposes only projects and never reads or resumes research', async () => {
  const calls = [];
  const bridge = createRemoteBridge(async path => {
    calls.push(path);
    return { projects: [{ id: 'p', name: 'Example' }, { id: 'a', name: 'Archived', activity: 'archived' }] };
  }, { resourceUrl: 'https://central.example/mcp' });
  const home = await bridge.home({ selection_id: 'old-job' });
  assert.deepEqual(calls, ['/api/projects']);
  assert.deepEqual(home.projects, [{ id: 'p', name: 'Example' }]);
  assert.equal(home.can_search, false);
  assert.equal(home.capabilities.research, false);
  assert.equal(home.last_selection, null);
  assert.deepEqual(home.connection, { mode: 'central', origin: 'https://central.example', resource: 'https://central.example/mcp' });
  assert.equal(home.capabilities.cached_project_evidence, true);
});

test('remote profile preserves provenance without claiming chats open on this host', async () => {
  const source = { id: '11111111-1111-4111-8111-111111111111', cwd: '/another-machine/repo' };
  const bridge = createRemoteBridge(async () => ({ project: { id: 'p', name: 'Example' }, revision: 'r', sources: [source] }));
  const profile = await bridge.project({ project_id: 'p' });
  assert.deepEqual(profile.sources, [source]);
  assert.equal(profile.capabilities.source_chat_navigation, false);
  const guide = await bridge.guide({ project_id: 'p' });
  assert.equal(new URL(guide.url).searchParams.has('path'), false);
});

test('remote candidate uses native discovery without parser-incompatible auth extensions or local fallback', async () => {
  const local = JSON.parse(await readFile(new URL('../mcp.json', import.meta.url)));
  const remote = JSON.parse(await readFile(new URL('../mcp.remote.json', import.meta.url)));
  assert.equal(local.mcpServers.hoomy.type, 'stdio');
  assert.equal(remote.mcpServers.hoomy.type, 'streamable-http');
  assert.equal(remote.mcpServers.hoomy.url, 'https://my.hoomy.dev/mcp');
  assert.equal(remote.mcpServers.hoomy.extensions, undefined);
  assert.doesNotMatch(JSON.stringify(remote), /clientSecret|bearer_token|Authorization/);
  const candidate = JSON.parse(await readFile(new URL('../../hoomy-remote-acceptance/mcp.json', import.meta.url)));
  assert.deepEqual(Object.keys(candidate.mcpServers), ['hoomy_remote_acceptance']);
  assert.deepEqual(candidate.mcpServers.hoomy_remote_acceptance, { type: 'streamable-http', url: 'https://my.hoomy.dev/mcp' });
  const skill = await readFile(new URL('../../hoomy-remote-acceptance/skills/hoomy/SKILL.md', import.meta.url), 'utf8');
  assert.match(skill, /Never call tools from server `hoomy`/);
  assert.match(skill, /connection.mode: central/);
  assert.match(skill, /Stop without opening local Hoomy/);
});

test('cached evidence passes only authenticated user identity and bounded source parameters', async () => {
  const calls=[];
  const principal={account_id:'github:42',workspace_id:'personal:42',device_id:undefined,grant_id:'private-grant'};
  const bridge=createRemoteBridge(async(path,body)=>{calls.push({path,body});return {messages:[]};},{principal});
  await dispatcher(bridge)('hoomy_project_evidence',{project_id:'p',source_id:'remote:hash'});
  assert.deepEqual(calls,[{path:'/api/ingestion/evidence/query',body:{principal:{account_id:'github:42',workspace_id:'personal:42'},project_id:'p',source_id:'remote:hash',offset:0,limit:20}}]);
  await assert.rejects(()=>dispatcher(bridge)('hoomy_project_evidence',{project_id:'p',source_id:'remote:hash',limit:51}));
  await assert.rejects(()=>createRemoteBridge(async()=>{throw new Error('must not call');}).evidence({project_id:'p',source_id:'remote:hash'}),/connexion utilisateur/);
  await assert.rejects(()=>createRemoteBridge(async()=>{throw new Error('must not call');},{principal:{...principal,device_id:'collector'}}).evidence({project_id:'p',source_id:'remote:hash'}),/connexion utilisateur/);
  assert.equal(definitions.hoomy_project_evidence.read,true);
  assert.equal(definitions.hoomy_project_evidence.remoteOnly,true);
});

test('central state reads preserve the origin, scope identity and independent pilot without local fallback',async()=>{
  const calls=[],principal={account_id:'u',workspace_id:'w'};
  const bridge=createRemoteBridge(async(path,body)=>{calls.push({path,body});return {enabled:true};},{principal,stateProjectId:'p',resourceUrl:'https://central.example/mcp'});
  const state=await bridge.projectState({project_id:'p'});
  assert.equal(state.connection.origin,'https://central.example');
  assert.equal(state.enabled,true);
  assert.deepEqual(calls[0].body,{principal,project_id:'p'});
  await assert.rejects(()=>bridge.refreshProjectState({project_id:'other'}),/service central Hoomy/);
  assert.equal(calls.length,1);
  const down=createRemoteBridge(async()=>{throw new Error('Le service local Hoomy ne répond pas. Vérifie qu’il est démarré, puis réessaie.');},{principal});
  await assert.rejects(()=>down.projectState({project_id:'p'}),/service central Hoomy ne répond pas/);
});
