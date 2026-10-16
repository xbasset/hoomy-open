import { posix } from 'node:path';
import { createHash } from 'node:crypto';
import { HttpError } from './backend-client.mjs';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const HASH = /^[0-9a-f]{64}$/;
const validHash = value => typeof value === 'string' && HASH.test(value);
const IDENTIFIER = /^[A-Za-z0-9._-]{1,128}$/;
const bad = (ok, code = 'invalid_event', message = 'Provide an event matching the ingestion contract.') => {
  if (!ok) throw new HttpError(400, code, message);
};
function keys(value, expected, code = 'invalid_event') {
  bad(value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length === expected.length && expected.every(key => Object.hasOwn(value, key)), code);
}
export function scalarString(value) {
  if (typeof value !== 'string') return false;
  for (let i = 0; i < value.length; i++) {
    const unit = value.charCodeAt(i);
    if (unit >= 0xd800 && unit <= 0xdbff) { const next = value.charCodeAt(++i); if (!(next >= 0xdc00 && next <= 0xdfff)) return false; }
    else if (unit >= 0xdc00 && unit <= 0xdfff) return false;
  }
  return true;
}
export function canonicalJson(value) {
  if (Array.isArray(value)) return '[' + value.map(canonicalJson).join(',') + ']';
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + canonicalJson(value[key])).join(',') + '}';
  return JSON.stringify(value);
}
export const canonicalDigest = value => createHash('sha256').update(canonicalJson(value), 'utf8').digest('hex');

// Validate raw tokens before JSON.parse can collapse duplicate keys or numbers.
export function strictJson(bytes) {
  let input;
  try { input = new TextDecoder('utf-8', { fatal:true }).decode(bytes); }
  catch { throw new HttpError(400, 'invalid_json', 'Use valid UTF-8 JSON.'); }
  let index = 0;
  const invalid = () => { throw new HttpError(400, 'invalid_json', 'Use unique JSON keys, scalar Unicode and safe integer values.'); };
  const space = () => { while (/\s/.test(input[index] || '') && index < input.length) index++; };
  function string() {
    const start = index++;
    while (index < input.length) {
      if (input[index] === '\\') { index += 2; continue; }
      if (input[index++] === '"') {
        let value; try { value = JSON.parse(input.slice(start, index)); } catch { invalid(); }
        if (!scalarString(value)) invalid(); return value;
      }
    }
    invalid();
  }
  function value(depth = 0) {
    if (depth > 24) invalid(); space();
    const character = input[index];
    if (character === '"') { string(); return; }
    if (character === '{') {
      index++; space(); const names = new Set();
      if (input[index] === '}') { index++; return; }
      for (;;) {
        space(); if (input[index] !== '"') invalid(); const name = string();
        if (names.has(name)) invalid(); names.add(name); space(); if (input[index++] !== ':') invalid();
        value(depth + 1); space(); const separator = input[index++]; if (separator === '}') return; if (separator !== ',') invalid();
      }
    }
    if (character === '[') {
      index++; space(); if (input[index] === ']') { index++; return; }
      for (;;) { value(depth + 1); space(); const separator = input[index++]; if (separator === ']') return; if (separator !== ',') invalid(); }
    }
    const literal = input.slice(index).match(/^(?:true|false|null|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)/)?.[0];
    if (!literal || (!['true','false','null'].includes(literal) && (!/^-?(?:0|[1-9][0-9]*)$/.test(literal) || !Number.isSafeInteger(Number(literal))))) invalid();
    index += literal.length;
  }
  value(); space(); if (index !== input.length) invalid();
  try { return JSON.parse(input); } catch { invalid(); }
}
export function absolutePath(value) {
  return scalarString(value) && value.length <= 4096 && value.startsWith('/') && !value.startsWith('//') && !/[\x00-\x1f\x7f]/.test(value) && posix.normalize(value) === value && !value.endsWith('/');
}
export const within = (root, path) => path === root || path.startsWith(root + '/');
export function validatePolicy(policy, projectId) {
  keys(policy, ['content_tier','collection_enabled','roots','exclusions','project_bindings'], 'invalid_policy');
  bad(projectId && policy.content_tier === 'conversation_visible_v1' && policy.collection_enabled === true, 'collection_unavailable', 'Conversation ingestion requires the configured pilot and explicit consent.');
  bad(Array.isArray(policy.roots) && policy.roots.length >= 1 && policy.roots.length <= 8 && policy.roots.every(root => absolutePath(root) && /\/\.codex\/(?:sessions|archived_sessions)$/.test(root)) && new Set(policy.roots).size === policy.roots.length, 'invalid_policy', 'Approve only explicit Codex session history roots.');
  bad(Array.isArray(policy.exclusions) && policy.exclusions.length <= 32 && policy.exclusions.every(absolutePath) && new Set(policy.exclusions).size === policy.exclusions.length, 'invalid_policy');
  bad(Array.isArray(policy.project_bindings) && policy.project_bindings.length === 1, 'invalid_policy');
  const binding = policy.project_bindings[0]; keys(binding, ['project_id','cwd_roots'], 'invalid_policy');
  bad(binding.project_id === projectId && Array.isArray(binding.cwd_roots) && binding.cwd_roots.length >= 1 && binding.cwd_roots.length <= 8 && binding.cwd_roots.every(root => absolutePath(root) && root !== '/') && new Set(binding.cwd_roots).size === binding.cwd_roots.length, 'invalid_policy', 'Approve one pilot project and its exact working roots.');
  return policy;
}
function timestamp(value) {
  return scalarString(value) && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
}
export function validateEvent(event, policy, projectId, idempotencyKey) {
  const excluded = event?.type === 'conversation.source_excluded';
  bad(excluded || event?.type === 'conversation.projected');
  const common = ['schema_version','event_id','type','project_id','policy_revision','connector','session_id','source_location','sequence','observed_at'];
  keys(event, [...common, ...(excluded ? ['exclusion'] : ['source_sha256','projected_sha256','title','coverage','projection'])]);
  bad(event.schema_version === 1 && UUID.test(event.event_id) && idempotencyKey === event.event_id && event.connector === 'codex' && scalarString(event.session_id) && IDENTIFIER.test(event.session_id));
  bad(Number.isSafeInteger(event.sequence) && event.sequence > 0 && timestamp(event.observed_at));
  if (event.policy_revision !== policy.revision) throw new HttpError(409, 'policy_conflict', 'Reload the current approved collection policy.');
  if (event.project_id !== projectId) throw new HttpError(403, 'project_not_approved', 'This project is outside the approved pilot.');
  keys(event.source_location, ['path','cwd','archive_occurrence','modified_at']);
  const location = event.source_location;
  bad(absolutePath(location.path) && location.path.endsWith('.jsonl') && absolutePath(location.cwd) && typeof location.archive_occurrence === 'boolean' && timestamp(location.modified_at));
  const root = policy.roots.find(root => within(root, location.path));
  if (!root || !policy.project_bindings[0].cwd_roots.some(root => within(root, location.cwd)) || policy.exclusions.some(root => within(root, location.path) || within(root, location.cwd))) throw new HttpError(403, 'source_not_approved', 'This source is outside the approved paths.');
  bad(location.archive_occurrence === root.endsWith('/archived_sessions'));
  if (excluded) {
    keys(event.exclusion, ['reason','discriminator','header_sha256']);
    bad(event.exclusion.reason === 'internal_approval_review' && ['legacy_subagent_guardian','internal_guardian'].includes(event.exclusion.discriminator) && validHash(event.exclusion.header_sha256));
    // The agent declares a header classification; no source body enters this event.
    return { eventDigest:canonicalDigest(event) };
  }
  bad(validHash(event.source_sha256) && validHash(event.projected_sha256));
  bad(scalarString(event.title) && [...event.title].length <= 512 && Buffer.byteLength(event.title) <= 4096);
  keys(event.coverage, ['excluded_records','excluded_sensitive_messages','excluded_context_messages','excluded_oversized_records']);
  bad(Object.values(event.coverage).every(value => Number.isSafeInteger(value) && value >= 0));
  keys(event.projection, ['version','messages']);
  bad(event.projection.version === 1 && Array.isArray(event.projection.messages) && event.projection.messages.length >= 1 && event.projection.messages.length <= 10000);
  let previousLine = 0;
  for (const message of event.projection.messages) {
    keys(message, ['role','text','line_start','line_end','timestamp']);
    bad(['user','assistant'].includes(message.role) && scalarString(message.text) && Number.isSafeInteger(message.line_start) && message.line_start > 0 && Number.isSafeInteger(message.line_end) && message.line_end >= message.line_start && (message.timestamp === null || timestamp(message.timestamp)));
    bad(message.line_start >= previousLine); previousLine = message.line_start;
    if (Buffer.byteLength(message.text) > 256 * 1024) throw new HttpError(413, 'message_too_large', 'A visible message exceeds the pilot limit.');
  }
  if (Buffer.byteLength(canonicalJson(event.projection)) > 18 * 1024 * 1024) throw new HttpError(413, 'projection_too_large', 'The visible projection exceeds the pilot limit.');
  bad(canonicalDigest(event.projection) === event.projected_sha256, 'digest_mismatch', 'The projection digest does not match.');
  return { eventDigest:canonicalDigest(event) };
}
export function verifiedReceipt(receipt, event, eventDigest) {
  const excluded = event.type === 'conversation.source_excluded';
  const fields = ['receipt_id','event_id','project_id','event_sha256','state','inference_state','received_at','source_version_id', ...(excluded ? ['event_type','header_sha256'] : ['projected_sha256'])];
  const correlated = excluded ? receipt?.event_type === event.type && receipt.header_sha256 === event.exclusion?.header_sha256 && validHash(receipt.header_sha256) : receipt?.projected_sha256 === event.projected_sha256 && validHash(receipt.projected_sha256);
  if (!receipt || Array.isArray(receipt) || Object.keys(receipt).length !== fields.length || !fields.every(key => Object.hasOwn(receipt,key)) || !correlated || receipt.receipt_id !== event.event_id || receipt.event_id !== event.event_id || receipt.project_id !== event.project_id || receipt.event_sha256 !== eventDigest || !validHash(receipt.event_sha256) || receipt.state !== 'stored' || receipt.inference_state !== 'not_requested' || typeof receipt.source_version_id !== 'string' || !/^remote-version:[0-9a-f]{64}$/.test(receipt.source_version_id) || !timestamp(receipt.received_at)) throw new HttpError(502, 'receipt_unconfirmed', 'The central service did not confirm durable storage.');
  return receipt;
}
