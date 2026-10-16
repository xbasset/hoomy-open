import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { createGateway } from '../gateway.mjs';
import { HttpError } from '../backend-client.mjs';
import { configFromEnv } from '../config.mjs';
import { validateStateRequest } from '../project-state-routes.mjs';
import { canonicalDigest } from '../ingestion.mjs';

const policy = {version:'project-state-v4',model:{name:'fictional-model'},phases:{extract:{effort:'low'},reconcile:{effort:'medium'}}};
const currentMethod = {method_version:policy.version,method_hash:canonicalDigest(policy),execution_policy:policy};

async function fixture(t, stateProjectId = 'project') {
  const directory = await mkdtemp(join(tmpdir(), 'hoomy-project-state-'));
  let app, failure = false, wrongProject = false, backendEnabled = true, methodOverride = {};
  const calls = [];
  const server = createServer((req,res) => app(req,res));
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const gateway = await createGateway({testMode:true, origin, backendUrl:'http://127.0.0.1:8765',databasePath:join(directory,'auth.sqlite'),githubClientId:'fixture',githubClientSecret:'fake',allowedGithubIds:['42'],cookieKeys:['x'.repeat(32)],stateProjectId,clients:[{client_id:'fixture-user',redirect_uris:['http://127.0.0.1:4567/callback']}]}, {pilot:async(path,body) => {
    calls.push({path,body});
    if (failure) throw new HttpError(409,'private-model-error','Private fixture source text',{raw:'Private fixture source text'});
    const identity = path.endsWith('/job') ? {method_version:currentMethod.method_version,method_hash:currentMethod.method_hash} : currentMethod;
    return {project_id:wrongProject?'other':body.project_id, ...(body.run_id?{run_id:body.run_id}:{}), ...(path.endsWith('/attempt')?{call_id:body.call_id}:{}), ...identity, execution_compatible:true, ...(!path.endsWith('/job') ? {enabled:backendEnabled} : {}), status:'queued', ...(['/claim','/finish'].some(suffix => path.endsWith(suffix)) ? {next:null,next_plan:null} : {}),...methodOverride};
  }});
  app = gateway.app;
  t.after(async () => {server.closeAllConnections();await new Promise(resolve => server.close(resolve));gateway.close();await rm(directory,{recursive:true,force:true});});
  async function token(scope, resource = gateway.resources.projects, collector = false) {
    const clientId = collector?'hoomy-collector':'fixture-user';
    const grant = new gateway.provider.Grant({accountId:'github:42',clientId}); grant.addResourceScope(resource,scope);
    const grantId = await grant.save();
    if (collector) {const device = randomUUID();gateway.store.put('device',device,{account_id:'github:42',workspace_id:'pilot',status:'active',grant_id:grantId});gateway.store.put('device-grant',grantId,{device_id:device});}
    const access = new gateway.provider.AccessToken({accountId:'github:42',clientId,grantId,scope,expiresWithSession:false,resourceServer:{audience:resource,accessTokenFormat:'opaque',accessTokenTTL:900}});
    return {value:await access.save(),grant};
  }
  const request = (path, authorization, body, options = {}) => fetch(origin+path,{method:body===undefined?'GET':'POST',headers:{...(authorization?{Authorization:`Bearer ${authorization.value}`} :{}),...(body===undefined?{}:{'Content-Type':'application/json'}),...options.headers},body:body===undefined?undefined:typeof body==='string'?body:JSON.stringify(body),redirect:'manual'});
  return {...gateway,calls,token,request,setFailure:value=>{failure=value;},setWrongProject:value=>{wrongProject=value;},setBackendEnabled:value=>{backendEnabled=value;},setMethod:value=>{methodOverride=value;}};
}
const run = randomUUID(), lease = randomUUID(), worker = randomUUID(), call = randomUUID(), hash = 'a'.repeat(64);
const refresh = {request_id:randomUUID(),context_revision:hash,corpus_revision:'b'.repeat(64),method_hash:'c'.repeat(64)};
const heartbeat = {lease_id:lease,input_hash:hash};
const output = {schema_version:1,assertions:[],uncertainties:[],contradictions:[]};
const method = {adapter:'codex-native-v1',model:'configured-model',elapsed_ms:42,usage:{input_tokens:5,output_tokens:3,total_tokens:10}};

test('actual provider scopes separate cost creation, execution, reads, collectors and immediate revocation', async t => {
  const h = await fixture(t), read = await h.token('projects:read'), creator = await h.token('projects:state:refresh'), executor = await h.token('projects:state:execute');
  const collector = await h.token('device:self evidence:ingest',h.resources.collector,true);
  const path = `/v1/projects/project/state/jobs/${run}`;
  assert.equal((await h.request('/v1/projects/project/state')).status,401);
  assert.equal((await h.request('/v1/projects/project/state',collector)).status,401);
  assert.equal((await h.request('/v1/projects/project/state/refresh',read,refresh)).status,403);
  assert.equal((await h.request('/v1/projects/project/state/refresh',executor,refresh)).status,403);
  assert.equal((await h.request(path+'/claim',creator,{worker_id:worker})).status,403);
  assert.equal((await h.request('/v1/projects/project/context',executor,{revision:hash,fields:{goal:'Disallowed'}})).status,403);
  assert.equal(h.calls.length,0);
  const state = await h.request('/v1/projects/project/state',read);assert.equal(state.status,200);assert.equal((await state.json()).enabled,true);
  assert.equal((await h.request('/v1/projects/project/state/refresh',creator,refresh,{headers:{'Idempotency-Key':refresh.request_id}})).status,202);
  assert.equal((await h.request(path+'/claim',executor,{worker_id:worker})).status,200);
  for (const [action,body] of Object.entries({heartbeat,attempt:{...heartbeat,call_id:call,phase:'extract',chunk_index:0},finish:{...heartbeat,call_id:call,output,method},fail:{...heartbeat,code:'usage_unknown'}})) assert.equal((await h.request(path+'/'+action,executor,body)).status,200);
  assert.equal((await h.request(path,read)).status,200);
  assert.equal((await h.request(path+'/citations/citation:'+hash,read)).status,200);
  assert.deepEqual(h.calls.map(value=>value.path),['read','refresh','claim','heartbeat','attempt','finish','fail','job','citation'].map(value=>'/api/project-state/'+value));
  for (const {body} of h.calls) assert.deepEqual(body.principal,{account_id:'github:42',workspace_id:'pilot'});
  const total = h.calls.length;await executor.grant.destroy();
  assert.equal((await h.request(path+'/heartbeat',executor,heartbeat)).status,401);assert.equal(h.calls.length,total);
  assert.equal((await h.request('/v1/projects/project/state',read)).status,200);
});

test('independent state allowlist is disabled by default, denies other projects before bodies, and requires both layers for UI availability', async t => {
  const disabled = await fixture(t,'');const executor = await disabled.token('projects:state:execute');
  assert.equal((await disabled.request(`/v1/projects/project/state/jobs/${run}/claim`,executor,'not-json')).status,403);assert.equal(disabled.calls.length,0);
  const read = await disabled.token('projects:read');assert.equal((await (await disabled.request('/v1/projects/project/state',read)).json()).enabled,false);
  const h = await fixture(t), permitted = await h.token('projects:state:execute'), reader = await h.token('projects:read');
  assert.equal((await h.request(`/v1/projects/another/state/jobs/${run}/claim`,permitted,'not-json')).status,403);assert.equal(h.calls.length,0);
  h.setBackendEnabled(false);assert.equal((await (await h.request('/v1/projects/project/state',reader)).json()).enabled,false);
  const discovery = await (await h.request('/.well-known/hoomy')).json();assert.equal(discovery.collection_available,false);
});

test('closed public state wire rejects identity injection, coercions, duplicate keys, lease/hash mismatch and oversized bodies before private calls', async t => {
  const h = await fixture(t), actor = await h.token('projects:state:execute projects:state:refresh'), path = `/v1/projects/project/state/jobs/${run}`;
  const invalid = [
    ['claim',{worker_id:worker,principal:{account_id:'other'}}],['claim',{worker_id:[worker]}],
    ['claim',{worker_id:worker,next_plan:{input:{messages:[]}}}],
    ['heartbeat',{...heartbeat,input_hash:[hash]}],['heartbeat',{...heartbeat,lease_id:'bad'}],
    ['attempt',{...heartbeat,call_id:call,phase:'reconcile',chunk_index:0}],
    ['attempt',{...heartbeat,call_id:call,phase:'extract',chunk_index:2}],
    ['finish',{...heartbeat,call_id:call,output,method:{...method,usage:null}}],
    ['finish',{...heartbeat,call_id:call,output:{...output,hidden_reasoning:'private'},method}],
    ['fail',{...heartbeat,code:'private-provider-error'}],
  ];
  for (const [action,body] of invalid) assert.equal((await h.request(path+'/'+action,actor,body)).status,400);
  assert.equal((await h.request(path+'/heartbeat?principal=other',actor,heartbeat)).status,400);
  assert.equal((await h.request(path+'/heartbeat',actor,JSON.stringify(heartbeat).replace('"input_hash":','"input_hash":"duplicate","input_hash":'))).status,400);
  assert.equal((await h.request(path+'/finish',actor,' '.repeat(2*1024*1024+1))).status,413);
  assert.equal((await h.request(path+'/heartbeat',actor,'{}',{headers:{'Content-Encoding':'gzip'}})).status,415);
  assert.equal((await h.request('/v1/projects/project/state/refresh',actor,refresh,{headers:{'Idempotency-Key':randomUUID()}})).status,400);
  assert.equal(h.calls.length,0);
  assert.equal((await h.request(path+'/attempt',actor,{...heartbeat,call_id:call,phase:'reconcile',chunk_index:null})).status,200);
  h.setFailure(true);const failed = await h.request(path+'/heartbeat',actor,heartbeat);assert.equal(failed.status,409);
  assert.ok(!(await failed.text()).includes('Private fixture'));h.setFailure(false);h.setWrongProject(true);
  assert.equal((await h.request(path+'/heartbeat',actor,heartbeat)).status,502);
});

test('method metadata preserves observed reasoning accounting and rejects unknown usage instead of inventing it', () => {
  const body = {...heartbeat,call_id:call,output,method};assert.deepEqual(validateStateRequest('finish',body),body);
  for (const usage of [{input_tokens:5,output_tokens:3,total_tokens:7},{input_tokens:5,output_tokens:3},{input_tokens:5,output_tokens:3,total_tokens:Number.MAX_SAFE_INTEGER+1}]) assert.throws(()=>validateStateRequest('finish',{...body,method:{...method,usage}}));
});

test('insufficient device evidence is an execute-only closed failure preserving known call accounting', async t => {
  const h=await fixture(t),actor=await h.token('projects:state:execute'),reader=await h.token('projects:read');
  const path=`/v1/projects/project/state/jobs/${run}/fail`;
  const body={...heartbeat,code:'insufficient_device_evidence',call_id:call,method};
  assert.equal((await h.request(path,reader,body)).status,403);
  for (const invalid of [{...body,required_devices:['caller-supplied']},{...body,code:'insufficient_source_evidence'},{...body,method:undefined}]) {
    assert.equal((await h.request(path,actor,invalid)).status,400);
  }
  assert.equal(h.calls.length,0);
  h.setMethod({status:'needs_review',failure_code:'insufficient_device_evidence'});
  const response=await h.request(path,actor,body);assert.equal(response.status,200);
  assert.equal((await response.json()).status,'needs_review');
  assert.deepEqual(h.calls[0],{path:'/api/project-state/fail',body:{principal:{account_id:'github:42',workspace_id:'pilot'},project_id:'project',run_id:run,...body}});
  const unsupported={...heartbeat,code:'unsupported_device_scope'};
  h.setMethod({status:'needs_review',failure_code:'unsupported_device_scope'});
  const scoped=await h.request(path,actor,unsupported);assert.equal(scoped.status,200);
  assert.equal((await scoped.json()).failure_code,'unsupported_device_scope');
  assert.deepEqual(h.calls.at(-1).body,{principal:{account_id:'github:42',workspace_id:'pilot'},project_id:'project',run_id:run,...unsupported});
  assert.ok(!Object.hasOwn(h.calls.at(-1).body,'method'));
});

test('phase effort telemetry is closed without discarding known rejected-call accounting', async t => {
  const h=await fixture(t),actor=await h.token('projects:state:execute'),path=`/v1/projects/project/state/jobs/${run}/fail`;
  const body={...heartbeat,code:'invalid_output',call_id:call,method};
  for (const reasoning_effort of ['low','medium','high','xhigh','max','ultra']) {
    const observed={...body,method:{...method,reasoning_effort}};
    assert.equal((await h.request(path,actor,observed)).status,200);
    assert.equal(h.calls.at(-1).body.method.reasoning_effort,reasoning_effort);
  }
  const before=h.calls.length;
  for (const reasoning_effort of [null,['low'],true,'none','minimal','unknown']) assert.equal((await h.request(path,actor,{...body,method:{...method,reasoning_effort}})).status,400);
  assert.equal(h.calls.length,before);
  assert.equal((await h.request(path,actor,body)).status,200);
});

test('operator state activation is independent of existing collection configuration and validates its exact identifier', () => {
  const environment={HOOMY_AUTH_DB:'/tmp/fictional-auth.sqlite',HOOMY_GITHUB_CLIENT_ID:'fixture',HOOMY_GITHUB_CLIENT_SECRET:'fixture-secret',HOOMY_ALLOWED_GITHUB_IDS:'42',HOOMY_COOKIE_KEYS:JSON.stringify(['x'.repeat(32)]),HOOMY_INGEST_PROJECT_ID:'collection-project'};
  const off=configFromEnv(environment);assert.equal(off.stateProjectId,'');assert.equal(off.ingestionProjectId,'collection-project');
  const on=configFromEnv({...environment,HOOMY_STATE_PROJECT_ID:'state-project'});assert.equal(on.stateProjectId,'state-project');assert.equal(on.ingestionProjectId,'collection-project');
  assert.throws(()=>configFromEnv({...environment,HOOMY_STATE_PROJECT_ID:'../another-project'}));
});

test('failed-call accounting accepts only a complete known method/call pair and does not forward partial or fabricated metadata', async t => {
  const h=await fixture(t),actor=await h.token('projects:state:execute'),path=`/v1/projects/project/state/jobs/${run}/fail`;
  const base={...heartbeat,code:'invalid_output'};
  for (const fields of [{call_id:call},{method},{call_id:[call],method},{call_id:call,method:{...method,usage:null}},{call_id:call,method:{...method,model:'codex-default'}},{call_id:call,method,account_id:'forged'}]) assert.equal((await h.request(path,actor,{...base,...fields})).status,400);
  assert.equal(h.calls.length,0);
  const observed={...base,call_id:call,method:{...method,usage:{input_tokens:200000,output_tokens:1,total_tokens:200001}}};
  assert.equal((await h.request(path,actor,observed)).status,200);
  assert.deepEqual(h.calls[0].body,{principal:{account_id:'github:42',workspace_id:'pilot'},project_id:'project',run_id:run,...observed});
});

test('pending job discovery forwards only scoped identity and refuses nonqueued or private metadata', async t => {
  let result={project_id:'project',pending_run:null},calls=[];
  const directory=await mkdtemp(join(tmpdir(),'hoomy-pending-state-'));
  let app;const server=createServer((req,res)=>app(req,res));await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const origin=`http://127.0.0.1:${server.address().port}`;
  const h=await createGateway({testMode:true,origin,backendUrl:'http://127.0.0.1:8765',databasePath:join(directory,'auth.sqlite'),githubClientId:'fixture',githubClientSecret:'fake',allowedGithubIds:['42'],cookieKeys:['x'.repeat(32)],clients:[{client_id:'user',redirect_uris:['http://127.0.0.1:4567/callback']}]},{pilot:async(path,body)=>{calls.push({path,body});return result;}});app=h.app;
  t.after(async()=>{server.closeAllConnections();await new Promise(resolve=>server.close(resolve));h.close();await rm(directory,{recursive:true,force:true});});
  const grant=new h.provider.Grant({accountId:'github:42',clientId:'user'});grant.addResourceScope(h.resources.projects,'projects:read');const token=new h.provider.AccessToken({accountId:'github:42',clientId:'user',grantId:await grant.save(),scope:'projects:read',expiresWithSession:false,resourceServer:{audience:h.resources.projects,accessTokenFormat:'opaque',accessTokenTTL:900}});const headers={Authorization:`Bearer ${await token.save()}`};
  const url=origin+'/v1/projects/project/state/jobs';
  assert.equal((await fetch(url)).status,401);assert.equal((await fetch(url+'?run_id='+run,{headers})).status,400);assert.equal(calls.length,0);
  assert.deepEqual(await (await fetch(url,{headers})).json(),result);
  const identity={method_version:'project-state-v4',method_hash:hash,execution_compatible:true};
  result={project_id:'project',pending_run:{project_id:'project',run_id:run,status:'queued',retry_of_run_id:randomUUID(),retry_run_id:null,...identity}};
  assert.deepEqual(await (await fetch(url,{headers})).json(),result);
  assert.deepEqual(calls[0],{path:'/api/project-state/job',body:{principal:{account_id:'github:42',workspace_id:'pilot'},project_id:'project'}});
  result={project_id:'project',pending_run:{project_id:'project',run_id:run,status:'queued',method_version:null,method_hash:null,execution_compatible:false}};
  assert.deepEqual(await (await fetch(url,{headers})).json(),result);
  result.pending_run.method_version='project-state-v1';
  assert.deepEqual(await (await fetch(url,{headers})).json(),result);
  for (const extra of [{status:'running'},{project_id:'other'},{input:{messages:['private']}},{lease_id:lease},{run_id:[run]},{retry_of_run_id:[run]},{retry_run_id:'bad'},{method_hash:[hash]},{method_version:['project-state-v4']},{execution_compatible:'true'},{method_hash:null},{method_version:null},{method_hash:null,method_version:null,execution_compatible:true}]) {result={project_id:'project',pending_run:{project_id:'project',run_id:run,status:'queued',...identity,...extra}};assert.equal((await fetch(url,{headers})).status,502);}
});

test('explicit retry remains a closed refresh operation and ordinary refresh never fabricates a parent', async t => {
  const h=await fixture(t),creator=await h.token('projects:state:refresh'),executeOnly=await h.token('projects:state:execute');
  const path='/v1/projects/project/state/refresh';
  const request=(actor,body)=>h.request(path,actor,body,{headers:{'Idempotency-Key':body.request_id}});
  for (const parent of [null,[run],'../'+run,42]) assert.equal((await request(creator,{...refresh,retry_of_run_id:parent})).status,400);
  assert.equal((await request(creator,{...refresh,retry_of_run_id:run,actor:'forged'})).status,400);
  assert.equal((await request(executeOnly,{...refresh,retry_of_run_id:run})).status,403);
  assert.equal(h.calls.length,0);
  assert.equal((await request(creator,refresh)).status,202);assert.ok(!Object.hasOwn(h.calls[0].body,'retry_of_run_id'));
  const retry={...refresh,request_id:randomUUID(),retry_of_run_id:run};
  assert.equal((await request(creator,retry)).status,202);
  assert.deepEqual(h.calls[1],{path:'/api/project-state/refresh',body:{principal:{account_id:'github:42',workspace_id:'pilot'},project_id:'project',...retry}});
});

test('refresh requires an explicit current method hash and never forwards missing or coerced identities', async t => {
  const h=await fixture(t),creator=await h.token('projects:state:refresh');
  const request=body=>h.request('/v1/projects/project/state/refresh',creator,body,{headers:{'Idempotency-Key':body.request_id}});
  const {method_hash,...missing}=refresh;
  assert.equal((await request(missing)).status,409);
  assert.equal((await request({...missing,retry_of_run_id:run})).status,409);
  for (const value of [null,[method_hash],true,'A'.repeat(64),'bad']) assert.equal((await request({...refresh,method_hash:value})).status,400);
  assert.equal(h.calls.length,0);
  assert.equal((await request(refresh)).status,202);
  assert.equal(h.calls[0].body.method_hash,method_hash);
  h.setFailure(true);
  assert.equal((await request({...refresh,method_hash:'d'.repeat(64)})).status,409);
});

test('current descriptor is correlated while historical method labels never acquire an invented hash', async t => {
  const h=await fixture(t),actor=await h.token('projects:read');
  const read='/v1/projects/project/state',job=read+'/jobs/'+run;
  const response=await (await h.request(read,actor)).json();
  assert.equal(canonicalDigest(response.execution_policy),response.method_hash);
  for (const value of [{method_hash:'d'.repeat(64)},{method_version:null},{execution_policy:{...policy,version:'other'}}]) {
    h.setMethod(value);assert.equal((await h.request(read,actor)).status,502);
  }
  h.setMethod({method_version:'project-state-v1',method_hash:null,execution_compatible:false});
  const historical=await h.request(job,actor);assert.equal(historical.status,200);
  assert.equal((await historical.json()).method_hash,null);
  h.setMethod({method_version:'project-state-v2',method_hash:hash,execution_compatible:false});
  const knownHistorical=await h.request(job,actor);assert.equal(knownHistorical.status,200);
  assert.equal((await knownHistorical.json()).method_hash,hash);
  h.setMethod({method_version:'project-state-v3',method_hash:hash,execution_compatible:false});
  assert.equal((await h.request(job,actor)).status,200);
  h.setMethod({method_version:'project-state-v1',method_hash:null,execution_compatible:true});
  assert.equal((await h.request(job,actor)).status,502);
});

test('only execute claim and finish expose a correlated bounded next plan', async t => {
  const h=await fixture(t),actor=await h.token('projects:read projects:state:execute');
  const schema={type:'object',properties:{},required:[],additionalProperties:false};
  const phase_config={input_bytes:24576,envelope_bytes:32768,output_bytes:12288,effort:'low',call_seconds:75};
  const descriptor={...policy,phases:{extract:phase_config},schema_hashes:{extract:canonicalDigest(schema)}};
  const input={task:'extract',messages:[]},method_hash=canonicalDigest(descriptor);
  const plan={phase:'extract',chunk_index:0,input,output_schema:schema,phase_config,payload_hash:canonicalDigest(input),method_version:descriptor.version,method_hash};
  const result={method_version:descriptor.version,method_hash,execution_policy:descriptor,next:{phase:'extract',chunk_index:0},next_plan:plan};
  const base='/v1/projects/project/state/jobs/'+run;
  h.setMethod(result);
  const response=await h.request(base+'/claim',actor,{worker_id:worker});assert.equal(response.status,200);
  assert.deepEqual((await response.json()).next_plan,plan);
  assert.equal((await h.request(base+'/finish',actor,{...heartbeat,call_id:call,output,method})).status,200);
  assert.equal((await h.request(base+'/heartbeat',actor,heartbeat)).status,502);
  assert.equal((await h.request(base,actor)).status,502);
  assert.equal((await h.request('/v1/projects/project/state',actor)).status,502);
  for (const changed of [{method_hash:'0'.repeat(64)},{payload_hash:'0'.repeat(64)},{phase_config:{...phase_config,effort:'medium'}},{output_schema:{type:'string'}},{chunk_index:1},{extra:'untrusted'}]) {
    h.setMethod({...result,next_plan:{...plan,...changed}});
    assert.equal((await h.request(base+'/claim',actor,{worker_id:worker})).status,502);
  }
});

test('reader-quality metadata binds the visible revision, exclusions, source counts and surviving device coverage', async t => {
  const h=await fixture(t),reader=await h.token('projects:read'),path='/v1/projects/project/state';
  const id=n=>'assertion:'+String(n).repeat(64),citation=n=>'citation:'+String(n).repeat(64);
  const state={state_revision:'9'.repeat(64),suppressed_assertions:1,
    assertions:[{id:id(1),key:'historical',text:'A fictional historical report.',citation_ids:[citation(1)]}],
    uncertainties:[{id:id(2),text:'The current situation is unverified.',citation_ids:[citation(2)]}],contradictions:[],
    quality:{policy_version:'project-state-reader-quality-v1',policy_hash:'19f1e574d5b609cc7c9a6c82fac730e3095ddac848e901fdf96d6ebd6de57d0c',raw_assertions:3,raw_uncertainties:1,source_eligible_assertions:2,source_eligible_uncertainties:1,visible_assertions:1,visible_uncertainties:1,excluded_items:[{id:id(3),section:'assertions',reasons:['nonterminal_prose']}]},
    device_coverage:{provided_devices:['fictional-mac','fictional-vps'],cited_devices:['fictional-mac','fictional-vps'],uncited_provided_devices:[],cross_machine_complete:true}};
  state.visible_revision=canonicalDigest({state_revision:state.state_revision,quality_policy:state.quality.policy_hash,assertions:state.assertions,uncertainties:state.uncertainties,contradictions:state.contradictions,citation_ids:[citation(1),citation(2)]});
  h.setMethod({state});const response=await h.request(path,reader);assert.equal(response.status,200);assert.deepEqual((await response.json()).state,state);
  const historical=structuredClone(state);historical.quality.raw_assertions=100;historical.quality.raw_uncertainties=20;historical.suppressed_assertions=98;
  h.setMethod({state:historical});assert.equal((await h.request(path,reader)).status,200);
  for(const mutate of [
    s=>{s.visible_revision='0'.repeat(64);},s=>{s.quality.policy_hash=[s.quality.policy_hash];},s=>{s.quality.policy_hash='0'.repeat(64);},
    s=>{s.quality.policy_version='unknown-policy';},s=>{s.quality.visible_assertions=2;},s=>{s.quality.source_eligible_assertions=0;},s=>{s.quality.raw_assertions=101;},
    s=>{s.quality.excluded_items[0].index=0;},s=>{s.quality.excluded_items[0].reasons=['private-provider-error'];},s=>{s.quality.excluded_items[0].id=id(1);},
    s=>{s.quality.excluded_items.push(structuredClone(s.quality.excluded_items[0]));},s=>{s.suppressed_assertions=0;},
    s=>{s.device_coverage.cited_devices=['unknown-device'];},s=>{s.device_coverage.uncited_provided_devices=['fictional-vps'];},
    s=>{s.device_coverage.cross_machine_complete=false;},s=>{s.contradictions=[{assertion_keys:['historical','excluded'],text:'A fictional contradiction.'}];}
  ]){const invalid=structuredClone(state);mutate(invalid);h.setMethod({state:invalid});assert.equal((await h.request(path,reader)).status,502);}
});
