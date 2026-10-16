import test from 'node:test';
import assert from 'node:assert/strict';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js';
import { backendClient, createBridge } from '../bridge.mjs';
import { mcpServer } from '../server.mjs';

const sid = '11111111-1111-4111-8111-111111111111';
const unrelated = '22222222-2222-4222-8222-222222222222';
function fixture(kind = 'activity') {
  const candidate = { id: 'candidate', project_id: 'project', title: 'Compare the two options', action: 'List one benefit of each option.',
    source_refs: [sid, unrelated], state_question: 'Is the comparison still pending?', question_reason: 'The current status is not known.' };
  const state = { control: { scope_confirmed: true, inference_enabled: true, running: false },
    projects: [{ id: 'project', name: 'Example project', session_ids: [sid] }] };
  const job = { id: 'job', kind, status: 'ready', created_at: '2026-10-08T10:00:00Z', context: { instruction: 'I have five minutes', attention_budget: 5, project_id: 'project', questions_answered: 0 },
    activity: { id: 'activity', selected_id: 'candidate', candidates: [candidate], origin_request_id: 'origin', uncertainty: 'Confirmed pending today.' },
    context_question: { prompt: 'What is the desired outcome?' },
    questions: [{ id: 'idea', version: 1, revision: 3, candidate, private_packet: 'not for the UI' }] };
  const now = { selections: [job] };
  const act = { requests: [{ id: 'origin', catalog: [{ session_id: sid, title: 'Related source', cwd: '/tmp' },
    { session_id: unrelated, title: 'Unrelated project' }], project_context: [{ project_id: 'project', response: 'answer', answer: 'Reduce uncertainty', answered_at: '2026-10-08' }],
    sources: [{ messages: [{ text: 'private raw source body' }] }] }] };
  const writes = [];
  const api = async (path, body) => {
    if (body !== undefined) { writes.push({ path, body }); return { id: 'job' }; }
    return structuredClone({ '/api/state': state, '/api/now': now, '/api/act': act }[path]);
  };
  return { bridge: createBridge(api), state, now, act, job, writes };
}

test('opening Hoomy reads only and does not start research or resume an old activity', async () => {
  const f = fixture(); const home = await f.bridge.home();
  assert.equal(home.selection, null); assert.equal(home.last_selection.id, 'job'); assert.deepEqual(f.writes, []);
});
test('handoff preserves intent and includes only source chats belonging to the activity project', async () => {
  const f = fixture(); const result = await f.bridge.handoff({ selection_id: 'job' });
  assert.deepEqual(result.threads.map(t => t.id), [sid]);
  assert.match(result.brief, /Reduce uncertainty/); assert.doesNotMatch(result.brief, /private raw source body/);
  const url = new URL(result.new_url); assert.equal(url.searchParams.get('prompt'), result.brief);
  assert.equal(url.searchParams.get('path'), '/tmp'); assert.deepEqual(f.writes, []);
});
test('a stale activity cannot be handed off', async () => {
  const f = fixture('none'); await assert.rejects(f.bridge.handoff({ selection_id: 'job' }), /plus prête/);
});
test('a failed choice can be retried without resetting the saved request or answer count', async () => {
  const f = fixture('none'); f.job.status = 'failed'; f.job.failure = { code: 'timeout' };
  f.job.context.questions_answered = 1;
  const view = await f.bridge.home({ selection_id: 'job' });
  assert.equal(view.selection.failure.code, 'timeout');
  await f.bridge.retry({ selection_id: 'job' });
  assert.deepEqual(f.writes, [{ path: '/api/now/choose', body: { ...f.job.context, continuation_id: 'job', recover: true } }]);
});
test('the existing unavailable message is recognized without rewriting the historical job', async () => {
  const f = fixture('none'); f.job.message = 'Decisions API est indisponible. Tes idées sont conservées ; réessaie.';
  assert.equal((await f.bridge.home({ selection_id: 'job' })).selection.failure.code, 'legacy_unavailable');
  assert.equal(f.job.failure, undefined);
});
test('question answers use current revisions and preserve the selection context', async () => {
  const f = fixture('question');
  await assert.rejects(f.bridge.answer({ selection_id: 'job', idea_id: 'idea', version: 1, revision: 2, response: 'todo' }), /changé/);
  assert.deepEqual(f.writes, []);
  await f.bridge.answer({ selection_id: 'job', idea_id: 'idea', version: 1, revision: 3, response: 'unknown' });
  assert.equal(f.writes[0].body.answer, 'unknown'); assert.equal(f.writes[1].body.questions_answered, 1);
  assert.equal(f.writes[1].body.continuation_id, 'job'); assert.equal(f.writes[1].body.project_id, 'project');
});
test('user intent is persisted separately from task completion', async () => {
  const f = fixture('context_question');
  await f.bridge.answer({ selection_id: 'job', response: 'answer', answer: 'Reduce uncertainty' });
  assert.deepEqual(f.writes, [{ path: '/api/now/context-answer', body: { id: 'job', response: 'answer', answer: 'Reduce uncertainty' } }]);
  await assert.rejects(f.bridge.answer({ selection_id: 'job', response: 'already_done' }), /réponse/);
});
test('UI does not receive question internals', async () => {
  const f = fixture('question'); const view = await f.bridge.home({ selection_id: 'job' });
  assert.equal(view.selection.state_question.private_packet, undefined);
});
test('backend endpoint remains loopback only', () => {
  for (const url of ['https://example.com', 'http://127.0.0.1:8765/private', 'http://user:secret@localhost']) assert.throws(() => backendClient(url), /local/);
});
test('MCP initializes, advertises the global UI, serves HTML and dispatches read calls', async () => {
  const f = fixture(); const server = await mcpServer(f.bridge);
  const client = new Client({ name: 'hoomy-test', version: '1.0.0' });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  await server.connect(serverTransport); await client.connect(clientTransport);
  try {
    const { tools } = await client.listTools(); const home = tools.find(t => t.name === 'hoomy_home');
    assert.deepEqual(home._meta['openai/ui'].entrypoints, [{ type: 'global' }]);
    const resource = await client.readResource({ uri: home._meta.ui.resourceUri });
    assert.match(resource.contents[0].text, /Un petit pas qui compte/);
    const result = await client.callTool({ name: 'hoomy_home', arguments: {} });
    assert.equal(result.structuredContent.projects[0].id, 'project');
    const bad = await client.callTool({ name: 'hoomy_answer', arguments: { selection_id: 'job', response: 'unknown' } });
    assert.equal(bad.isError, true); assert.deepEqual(f.writes, []);
  } finally { await client.close(); await server.close(); }
});

test('project edits retain the revision and patch, while guidance only prepares a chat', async () => {
  const calls=[];
  const bridge=createBridge(async(path,body)=>{calls.push({path,body});return {project:{id:'p',name:'Example'},revision:'r',fields:{}};});
  await bridge.project({project_id:'p'});
  await bridge.updateProject({project_id:'p',revision:'r',fields:{goal:'Reliable playback',constraint:''}});
  const guide=await bridge.guide({project_id:'p'});
  assert.deepEqual(calls[1],{path:'/api/projects/context',body:{project_id:'p',revision:'r',fields:{goal:'Reliable playback',constraint:''}}});
  assert.equal(calls.filter(c=>c.body!==undefined).length,1);
  assert.match(guide.brief,/hoomy_update_project/);
  assert.equal(new URL(guide.url).searchParams.get('prompt'),guide.brief);
});
