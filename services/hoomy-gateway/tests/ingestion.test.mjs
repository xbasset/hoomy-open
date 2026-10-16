import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { strictJson, canonicalDigest, validatePolicy, validateEvent, verifiedReceipt } from '../ingestion.mjs';

const policy = { id:'device', revision:2, content_tier:'conversation_visible_v1', collection_enabled:true, roots:['/home/alex/.codex/sessions'], exclusions:['/home/alex/work/alpha/private'], project_bindings:[{project_id:'project-alpha', cwd_roots:['/home/alex/work/alpha']}] };
function event() {
  const projection = { version:1, messages:[{role:'assistant',text:'A visible final answer. 😀',line_start:3,line_end:3,timestamp:null}] };
  return { schema_version:1, event_id:randomUUID(), type:'conversation.projected', project_id:'project-alpha', policy_revision:2, connector:'codex', session_id:randomUUID(), source_location:{path:'/home/alex/.codex/sessions/a.jsonl',cwd:'/home/alex/work/alpha',archive_occurrence:false,modified_at:'2026-10-12T12:00:00Z'}, sequence:1, source_sha256:'a'.repeat(64), projected_sha256:canonicalDigest(projection), observed_at:'2026-10-12T12:01:00Z',title:'Visible evidence',coverage:{excluded_records:4,excluded_sensitive_messages:0,excluded_context_messages:1,excluded_oversized_records:0},projection };
}

test('strict event JSON rejects duplicate keys, unsafe numbers, invalid UTF-8 and surrogate-only text', () => {
  for (const text of ['{"a":1,"a":2}', '{"a":{"role":"user","role":"assistant"}}', '{"n":9007199254740993}', '{"n":1.0}', '{"n":NaN}', '{"text":"\\ud800"}', '{"x":1} trailing']) assert.throws(() => strictJson(Buffer.from(text)), {code:'invalid_json'});
  assert.throws(() => strictJson(Buffer.from([0x22,0xff,0x22])), {code:'invalid_json'});
  assert.deepEqual(strictJson(Buffer.from('{"text":"😀","n":4}')), {text:'😀',n:4});
});

test('visible projections keep declared source provenance separate and enforce path and content boundaries', () => {
  const valid = event(); assert.ok(validateEvent(valid, policy, 'project-alpha', valid.event_id).eventDigest);
  for (const change of [
    value => value.source_location.cwd = '/home/alex/work/alpha-other',
    value => value.source_location.cwd = '/home/alex/work/alpha/private',
    value => value.source_location.path = '/home/alex/.codex/auth.json',
    value => value.source_location.path = '/home/alex/.codex/sessions/../auth.jsonl',
    value => value.source_location.archive_occurrence = true,
    value => value.project_id = 'other-project',
    value => value.source_sha256 = ['a'.repeat(64)],
    value => value.policy_revision = 1,
    value => value.projection.messages[0].role = 'analysis',
    value => value.projection.messages[0].reasoning = 'Excluded private reasoning',
    value => delete value.coverage.excluded_oversized_records,
    value => value.coverage.excluded_oversized_records = -1,
    value => value.projection.messages[0].text = 'changed without matching digest',
  ]) { const invalid = structuredClone(valid); change(invalid); assert.throws(() => validateEvent(invalid, policy, 'project-alpha', invalid.event_id)); }
  const oversized = event(); oversized.projection.messages[0].text = 'é'.repeat(128 * 1024 + 1);
  assert.throws(() => validateEvent(oversized, policy, 'project-alpha', oversized.event_id), {status:413});
  assert.throws(() => validateEvent(valid, policy, 'project-alpha', randomUUID()), {code:'invalid_event'});
});

test('pilot policy is disabled by default and never admits credential directories or unrelated projects', () => {
  const { id,revision,...proposed } = policy;
  assert.equal(validatePolicy(proposed, 'project-alpha'), proposed);
  assert.throws(() => validatePolicy(proposed, ''), {code:'collection_unavailable'});
  for (const roots of [['/home/alex/.codex'], ['/home/alex/.codex/auth'], ['/home/alex/.codex/sessions/'], ['/home/alex/.codex/sessions/../sessions']]) assert.throws(() => validatePolicy({...proposed,roots}, 'project-alpha'));
  assert.throws(() => validatePolicy({...proposed,project_bindings:[{project_id:'other',cwd_roots:['/home/alex/work/alpha']}]}, 'project-alpha'));
});

test('header-only exclusions are closed variants with the same policy and path boundaries', () => {
  const {source_sha256,projected_sha256,title,coverage,projection,...common} = event();
  const excluded = {...common,type:'conversation.source_excluded',exclusion:{reason:'internal_approval_review',discriminator:'internal_guardian',header_sha256:'b'.repeat(64)}};
  const eventDigest = validateEvent(excluded,policy,'project-alpha',excluded.event_id).eventDigest;
  assert.equal(eventDigest,canonicalDigest(excluded));
  const legacy = {...excluded,exclusion:{...excluded.exclusion,discriminator:'legacy_subagent_guardian'}};
  assert.ok(validateEvent(legacy,policy,'project-alpha',legacy.event_id));
  for (const change of [
    value => value.type = 'conversation.unknown',
    value => value.exclusion.reason = 'user_requested',
    value => value.exclusion.discriminator = 'thread_source_guardian',
    value => value.exclusion.header_sha256 = 'invalid',
    value => value.exclusion.header_sha256 = ['b'.repeat(64)],
    value => value.exclusion.verified = true,
    value => value.source_sha256 = source_sha256,
    value => value.projection = projection,
    value => value.title = title,
    value => value.coverage = coverage,
    value => value.projected_sha256 = projected_sha256,
    value => value.session_id = 123,
    value => value.source_location.cwd = '/home/alex/work/alpha-other',
    value => value.source_location.path = '/home/alex/.codex/auth.jsonl',
    value => value.policy_revision = 1,
  ]) { const invalid = structuredClone(excluded); change(invalid); assert.throws(() => validateEvent(invalid,policy,'project-alpha',invalid.event_id)); }
  const receipt = {receipt_id:excluded.event_id,event_id:excluded.event_id,project_id:excluded.project_id,event_sha256:eventDigest,state:'stored',inference_state:'not_requested',received_at:'2026-10-12T12:01:01Z',source_version_id:'remote-version:'+eventDigest,event_type:excluded.type,header_sha256:excluded.exclusion.header_sha256};
  assert.equal(verifiedReceipt(receipt,excluded,eventDigest),receipt);
  for (const changes of [{header_sha256:'c'.repeat(64)},{header_sha256:[receipt.header_sha256]},{event_type:'conversation.projected'},{projected_sha256},{event_sha256:'d'.repeat(64)},{event_sha256:[eventDigest]},{source_version_id:[receipt.source_version_id]}]) {
    assert.throws(() => verifiedReceipt({...receipt,...changes},excluded,eventDigest),{code:'receipt_unconfirmed'});
  }
});
