import express from 'express';
import { HttpError } from './backend-client.mjs';
import { strictJson, scalarString, canonicalDigest } from './ingestion.mjs';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const HASH = /^[0-9a-f]{64}$/;
const ID = /^[A-Za-z0-9._-]{1,128}$/;
const CITATION = /^citation:[0-9a-f]{64}$/;
const ASSERTION = /^assertion:[0-9a-f]{64}$/;
const QUALITY_REASONS = ['nonterminal_prose','unclosed_code','unsupported_version'];
const QUALITY_POLICY = {version:'project-state-reader-quality-v1',hash:'19f1e574d5b609cc7c9a6c82fac730e3095ddac848e901fdf96d6ebd6de57d0c'};
const FAILURES = ['cancelled','deadline_exceeded','lease_expired','model_unavailable','model_failed','invalid_output','usage_unknown','budget_exceeded','ambiguous_completion','input_ineligible','insufficient_device_evidence','unsupported_device_scope'];
const JOB_FIELDS = ['project_id','run_id','status','created_at','context_revision','corpus_revision','state_revision','completed_calls','dispatched_calls','failure_code','observed_tokens','unknown_usage_calls','retry_of_run_id','retry_run_id','method_version','method_hash','execution_compatible'];
const valid = (value, pattern) => typeof value === 'string' && pattern.test(value);
const integer = (value, max = Number.MAX_SAFE_INTEGER) => Number.isSafeInteger(value) && value >= 0 && value <= max;
const object = value => value && typeof value === 'object' && !Array.isArray(value);
const reject = (ok, status = 400, code = 'invalid_state_request') => { if (!ok) throw new HttpError(status, code, 'The project state operation is unavailable or does not match its contract.'); };
function keys(value, fields) { reject(object(value) && Object.keys(value).length === fields.length && fields.every(field => Object.hasOwn(value, field))); }
function methodIdentity(value, historical = false) {
  const missing = value.method_hash === null && (value.method_version === null || valid(value.method_version, ID));
  reject(historical && missing && value.execution_compatible === false || valid(value.method_version, ID) && valid(value.method_hash, HASH), 502, 'state_unconfirmed');
  if (historical) reject(typeof value.execution_compatible === 'boolean', 502, 'state_unconfirmed');
}
function digestMatches(value, hash) {
  try { return canonicalDigest(value) === hash; } catch { return false; }
}
function sameJson(left, right) {
  try { return canonicalDigest(left) === canonicalDigest(right); } catch { return false; }
}
function qualityProjection(state) {
  if (state == null) return;
  const confirm = ok => reject(ok, 502, 'state_unconfirmed');
  const closed = (value, fields) => object(value) && Object.keys(value).length === fields.length && fields.every(field => Object.hasOwn(value, field));
  confirm(object(state) && valid(state.state_revision, HASH) && valid(state.visible_revision, HASH));
  const quality = state.quality;
  confirm(closed(quality, ['policy_version','policy_hash','raw_assertions','raw_uncertainties','source_eligible_assertions','source_eligible_uncertainties','visible_assertions','visible_uncertainties','excluded_items']) &&
    quality.policy_version === QUALITY_POLICY.version && quality.policy_hash === QUALITY_POLICY.hash);
  for (const [section, maximum] of [['assertions',100],['uncertainties',20]]) {
    const raw = quality[`raw_${section}`], eligible = quality[`source_eligible_${section}`], visible = quality[`visible_${section}`];
    confirm(integer(raw, maximum) && integer(eligible, raw) && integer(visible, eligible) && Array.isArray(state[section]) && state[section].length === visible);
  }
  confirm(state.suppressed_assertions === quality.raw_assertions - quality.source_eligible_assertions &&
    Array.isArray(quality.excluded_items) && quality.excluded_items.length <= 120);
  const excludedIds = new Set(), excludedCounts = {assertions:0,uncertainties:0};
  for (const item of quality.excluded_items) {
    confirm(closed(item, ['id','section','reasons']) && valid(item.id, ASSERTION) && !excludedIds.has(item.id) && ['assertions','uncertainties'].includes(item.section) &&
      Array.isArray(item.reasons) && item.reasons.length >= 1 && item.reasons.length <= QUALITY_REASONS.length && new Set(item.reasons).size === item.reasons.length && item.reasons.every(reason => QUALITY_REASONS.includes(reason)));
    excludedIds.add(item.id); excludedCounts[item.section]++;
  }
  const citationIds = new Set(), keys = new Set(), visibleIds = new Set();
  for (const section of ['assertions','uncertainties']) {
    confirm(excludedCounts[section] === quality[`source_eligible_${section}`] - quality[`visible_${section}`]);
    for (const item of state[section]) {
      confirm(object(item) && valid(item.id, ASSERTION) && !visibleIds.has(item.id) && !excludedIds.has(item.id) && Array.isArray(item.citation_ids) && item.citation_ids.length >= 1 && item.citation_ids.length <= 500 && item.citation_ids.every(id => valid(id, CITATION)));
      visibleIds.add(item.id); item.citation_ids.forEach(id => citationIds.add(id));
      if (section === 'assertions') { confirm(typeof item.key === 'string' && scalarString(item.key) && !keys.has(item.key)); keys.add(item.key); }
    }
  }
  confirm(citationIds.size <= 500 && Array.isArray(state.contradictions) && state.contradictions.length <= 100 && state.contradictions.every(item => object(item) && Array.isArray(item.assertion_keys) && item.assertion_keys.length >= 2 && item.assertion_keys.length <= 100 && new Set(item.assertion_keys).size === item.assertion_keys.length && item.assertion_keys.every(key => keys.has(key))));
  const devices = state.device_coverage;
  confirm(closed(devices, ['provided_devices','cited_devices','uncited_provided_devices','cross_machine_complete']));
  for (const field of ['provided_devices','cited_devices','uncited_provided_devices']) confirm(Array.isArray(devices[field]) && devices[field].length <= 128 && new Set(devices[field]).size === devices[field].length && devices[field].every(id => typeof id === 'string' && scalarString(id) && Buffer.byteLength(id, 'utf8') >= 1 && Buffer.byteLength(id, 'utf8') <= 256 && !/[\x00-\x1f\x7f]/.test(id)));
  const provided = new Set(devices.provided_devices), cited = new Set(devices.cited_devices), uncited = devices.provided_devices.filter(id => !cited.has(id));
  confirm(devices.cited_devices.every(id => provided.has(id)) && devices.uncited_provided_devices.length === uncited.length && devices.uncited_provided_devices.every(id => uncited.includes(id)) &&
    devices.cross_machine_complete === Boolean(provided.size && uncited.length === 0) && (citationIds.size > 0 || cited.size === 0));
  // The visible digest is separate from the immutable stored-state revision.
  confirm(digestMatches({state_revision:state.state_revision,quality_policy:quality.policy_hash,assertions:state.assertions,uncertainties:state.uncertainties,contradictions:state.contradictions,citation_ids:[...citationIds].sort()}, state.visible_revision));
}
function workerPlan(result, operation) {
  if (operation === 'finish' && !Object.hasOwn(result, 'next_plan') && ['failed','needs_review'].includes(result.status)) {
    reject(result.next === null, 502, 'state_unconfirmed'); return;
  }
  reject(Object.hasOwn(result, 'next_plan'), 502, 'state_unconfirmed');
  const plan = result.next_plan;
  if (plan === null) { reject(result.next === null, 502, 'state_unconfirmed'); return; }
  const fields = ['phase','chunk_index','input','output_schema','phase_config','payload_hash','method_version','method_hash'];
  methodIdentity(result);
  reject(result.execution_compatible === true && object(plan) && Object.keys(plan).length === fields.length && fields.every(field => Object.hasOwn(plan, field)) &&
    (plan.phase === 'extract' ? integer(plan.chunk_index, 1) : plan.phase === 'reconcile' && plan.chunk_index === null) &&
    object(result.next) && result.next.phase === plan.phase && result.next.chunk_index === plan.chunk_index &&
    plan.method_version === result.method_version && plan.method_hash === result.method_hash &&
    object(plan.input) && plan.input.task === plan.phase && valid(plan.payload_hash, HASH) && digestMatches(plan.input, plan.payload_hash) &&
    object(plan.output_schema) && object(plan.phase_config) && object(result.execution_policy) && result.execution_policy.version === result.method_version &&
    digestMatches(result.execution_policy, result.method_hash) &&
    digestMatches(plan.output_schema, result.execution_policy.schema_hashes?.[plan.phase]) &&
    sameJson(plan.phase_config, result.execution_policy.phases?.[plan.phase]) &&
    Buffer.byteLength(JSON.stringify(plan), 'utf8') <= 64 * 1024, 502, 'state_unconfirmed');
}
function methodMetadata(method) {
  const effort = object(method) && Object.hasOwn(method, 'reasoning_effort');
  keys(method, ['adapter','model','elapsed_ms','usage', ...(effort ? ['reasoning_effort'] : [])]);
  if (effort) reject(['low','medium','high','xhigh','max','ultra'].includes(method.reasoning_effort));
  reject(method.adapter === 'codex-native-v1' && scalarString(method.model) && method.model.length >= 1 && method.model.length <= 200 && !['codex-default','unknown','default'].includes(method.model) && integer(method.elapsed_ms, 300000));
  keys(method.usage, ['input_tokens','output_tokens','total_tokens']);
  reject(Object.values(method.usage).every(value => integer(value)) && method.usage.total_tokens >= method.usage.input_tokens + method.usage.output_tokens);
}

export function validateStateRequest(action, body) {
  if (action === 'refresh') {
    const retry = object(body) && Object.hasOwn(body, 'retry_of_run_id');
    const hasMethod = object(body) && Object.hasOwn(body, 'method_hash');
    keys(body, ['request_id','context_revision','corpus_revision', ...(hasMethod ? ['method_hash'] : []), ...(retry ? ['retry_of_run_id'] : [])]);
    reject(valid(body.request_id, UUID) && valid(body.context_revision, HASH) && valid(body.corpus_revision, HASH));
    if (retry) reject(valid(body.retry_of_run_id, UUID));
    // A missing method means the client must reload, never select a default.
    reject(hasMethod, 409, 'state_method_required');
    reject(valid(body.method_hash, HASH));
    return body;
  }
  if (action === 'claim') {
    keys(body, ['worker_id']); reject(valid(body.worker_id, UUID)); return body;
  }
  const common = ['lease_id','input_hash'];
  const accounting = action === 'fail' && object(body) && (Object.hasOwn(body,'call_id') || Object.hasOwn(body,'method'));
  const extra = { heartbeat:[], attempt:['call_id','phase','chunk_index'], finish:['call_id','output','method'], fail:['code',...(accounting ? ['call_id','method'] : [])] }[action];
  reject(Boolean(extra)); keys(body, [...common, ...extra]);
  reject(valid(body.lease_id, UUID) && valid(body.input_hash, HASH));
  if (action === 'attempt' || action === 'finish' || accounting) reject(valid(body.call_id, UUID));
  if (action === 'attempt') reject(body.phase === 'extract' ? integer(body.chunk_index, 1) : body.phase === 'reconcile' && body.chunk_index === null);
  if (action === 'fail') reject(FAILURES.includes(body.code));
  if (accounting) methodMetadata(body.method);
  if (action === 'finish') {
    keys(body.output, ['schema_version','assertions','uncertainties','contradictions']);
    reject(body.output.schema_version === 1 && Array.isArray(body.output.assertions) && body.output.assertions.length <= 12 && Array.isArray(body.output.uncertainties) && body.output.uncertainties.length <= 4 && Array.isArray(body.output.contradictions) && body.output.contradictions.length <= 3);
    methodMetadata(body.method);
  }
  return body;
}

export function mountProjectState({ app, config, resources, authenticateRequest, protectedRoute, pilot }) {
  const principalBody = principal => ({account_id:principal.account_id, workspace_id:principal.workspace_id});
  function path(req, run = false) {
    reject(valid(req.params.id, ID) && (!run || valid(req.params.run, UUID)) && Object.keys(req.query).length === 0);
  }
  function enabled(req) { reject(Boolean(config.stateProjectId) && req.params.id === config.stateProjectId, 403, 'state_unavailable'); }
  async function forward(operation, body) {
    // Neither model text nor private exception details enter public errors.
    let result;
    try { result = await pilot(`/api/project-state/${operation}`, body); }
    catch (error) { throw new HttpError(Number.isInteger(error.status) && error.status >= 400 && error.status <= 599 ? error.status : 502, error.status === 409 ? 'state_conflict' : 'state_error', 'The central project state operation could not complete.'); }
    reject(object(result) && result.project_id === body.project_id && (!body.run_id || result.run_id === body.run_id) && (operation !== 'attempt' || result.call_id === body.call_id) && Buffer.byteLength(JSON.stringify(result), 'utf8') <= 2 * 1024 * 1024, 502, 'state_unconfirmed');
    if (operation === 'read') {
      methodIdentity(result);
      reject(object(result.execution_policy) && result.execution_policy.version === result.method_version && digestMatches(result.execution_policy, result.method_hash), 502, 'state_unconfirmed');
      qualityProjection(result.state);
    }
    if (operation === 'job' && body.run_id) {
      reject(Object.keys(result).every(field => JOB_FIELDS.includes(field)), 502, 'state_unconfirmed');
      methodIdentity(result, true);
    }
    if (['read','job','heartbeat'].includes(operation)) reject(!Object.hasOwn(result, 'next_plan') && !Object.hasOwn(result, 'input'), 502, 'state_unconfirmed');
    if (['claim','finish'].includes(operation)) workerPlan(result, operation);
    return result;
  }
  app.get('/v1/projects/:id/state', protectedRoute(resources.projects, ['projects:read'], async (req, res, principal) => {
    path(req);
    const result = await forward('read', {principal:principalBody(principal), project_id:req.params.id});
    res.json({...result, enabled:Boolean(config.stateProjectId && config.stateProjectId === req.params.id && result.enabled)});
  }));
  app.get('/v1/projects/:id/state/jobs', protectedRoute(resources.projects, ['projects:read'], async (req, res, principal) => {
    path(req);
    const result = await forward('job', {principal:principalBody(principal), project_id:req.params.id});
    const pending = result.pending_run;
    reject(Object.keys(result).length === 2 && Object.hasOwn(result,'pending_run') && (pending === null || object(pending) && pending.project_id === req.params.id && valid(pending.run_id, UUID) && pending.status === 'queued' && Object.keys(pending).every(field => JOB_FIELDS.includes(field)) && ['retry_of_run_id','retry_run_id'].every(field => !Object.hasOwn(pending,field) || pending[field] === null || valid(pending[field], UUID))), 502, 'state_unconfirmed');
    if (pending !== null) methodIdentity(pending, true);
    res.json(result);
  }));
  app.get('/v1/projects/:id/state/jobs/:run', protectedRoute(resources.projects, ['projects:read'], async (req, res, principal) => {
    path(req, true);
    res.json(await forward('job', {principal:principalBody(principal), project_id:req.params.id, run_id:req.params.run}));
  }));
  app.get('/v1/projects/:id/state/jobs/:run/citations/:citation', protectedRoute(resources.projects, ['projects:read'], async (req, res, principal) => {
    path(req, true); reject(valid(req.params.citation, CITATION));
    res.json(await forward('citation', {principal:principalBody(principal), project_id:req.params.id, run_id:req.params.run, citation_id:req.params.citation}));
  }));
  const raw = express.raw({type:'application/json', limit:2 * 1024 * 1024, inflate:false});
  for (const action of ['refresh','claim','heartbeat','attempt','finish','fail']) {
    const scope = action === 'refresh' ? 'projects:state:refresh' : 'projects:state:execute';
    const route = action === 'refresh' ? '/v1/projects/:id/state/refresh' : `/v1/projects/:id/state/jobs/:run/${action}`;
    const authorize = async (req, res, next) => {
      try { await authenticateRequest(req, {resource:resources.projects, scopes:[scope]}); }
      catch (error) {
        if (error.status === 401) res.set('WWW-Authenticate', `Bearer resource_metadata="${config.origin}/.well-known/oauth-protected-resource/v1/projects"`);
        throw error;
      }
      path(req, action !== 'refresh'); enabled(req); req.setTimeout(30000); next();
    };
    app.post(route, authorize, raw, protectedRoute(resources.projects, [scope], async (req, res, principal) => {
      enabled(req); reject(Buffer.isBuffer(req.body), 415, 'json_required');
      const body = validateStateRequest(action, strictJson(req.body));
      if (action === 'refresh') reject(req.get('Idempotency-Key') === body.request_id);
      const result = await forward(action, {principal:principalBody(principal), project_id:req.params.id, ...(action === 'refresh' ? {} : {run_id:req.params.run}), ...body});
      res.status(action === 'refresh' ? 202 : 200).json(result);
    }));
  }
}
