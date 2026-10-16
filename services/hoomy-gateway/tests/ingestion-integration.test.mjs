import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import { createGateway } from '../gateway.mjs';
import { canonicalDigest } from '../ingestion.mjs';

const repository = fileURLToPath(new URL('../../../', import.meta.url));
const python = process.env.HOOMY_TEST_PYTHON || 'python3';
const pythonServer = `
import base64, hashlib, json, sys, threading
from http.server import ThreadingHTTPServer
from hoomy.server import make_handler
from hoomy.store import Store, SECTIONS
store = Store(sys.argv[1])
if sys.argv[2] in ('seed', 'state-seed'):
    generation = store.state()['control']['generation']
    store.control({'consent': True, 'running': False})
    text = 'Invented isolated collection fixture.'
    raw = json.dumps({'type': 'message', 'role': 'user', 'text': text}).encode()
    snapshot = hashlib.sha256(raw).hexdigest()
    store.ingest({'generation': generation, 'session_id': 'fixture-source', 'snapshot_id': snapshot,
                  'source_path': '/fictional/source.jsonl', 'cwd': '/fictional/project',
                  'modified_at': '2026-10-12T12:00:00Z', 'raw_b64': base64.b64encode(raw).decode(),
                  'warnings': [], 'messages': [{'id':'fixture-message','role':'user','text':text,
                  'line_start':1,'line_end':1,'timestamp':None}]})
    evidence = [{'message_id': 'fixture-message', 'quote': text}]
    store.analysis('fixture-source', {'generation': generation, 'snapshot_id':snapshot,
                   'brief':{section:[] for section in SECTIONS}, 'method':{'adapter':'fixture','schema':1},
                   'projects':[{'name':'Fixture project','purpose':text,'status':'active','rationale':text,'evidence':evidence}]})
project_id = store.project_list()['projects'][0]['id']
if sys.argv[2] == 'state-seed':
    import uuid
    from hoomy.ingestion import digest
    store.project_state.enabled_project_id = project_id
    projection = {'version':1,'messages':[{'role':'user','text':'Fictional delivery is reported open — français 😀.', 'line_start':2,'line_end':2,'timestamp':None}]}
    policy = {'id':None,'revision':2,'content_tier':'conversation_visible_v1','collection_enabled':True,
              'roots':['/fictional/.codex/sessions'],'exclusions':[],
              'project_bindings':[{'project_id':project_id,'cwd_roots':['/fictional/project']}]}
    for machine in ('11111111-1111-4111-8111-111111111111','22222222-2222-4222-8222-222222222222'):
        policy['id'] = machine
        event = {'schema_version':1,'event_id':str(uuid.uuid4()),'type':'conversation.projected',
                 'project_id':project_id,'policy_revision':2,'connector':'codex','session_id':str(uuid.uuid4()),
                 'source_location':{'path':'/fictional/.codex/sessions/source.jsonl','cwd':'/fictional/project',
                                    'archive_occurrence':False,'modified_at':'2026-10-13T12:00:00Z'},
                 'sequence':1,'source_sha256':'a'*64,'projected_sha256':digest(projection),
                 'observed_at':'2026-10-13T12:01:00Z','title':'Fictional state input',
                 'coverage':{'excluded_records':0,'excluded_sensitive_messages':0,'excluded_context_messages':0,'excluded_oversized_records':0},
                 'projection':projection}
        store.ingestion.accept({'principal':{'account_id':'github:42','workspace_id':'pilot','device_id':machine},'policy':policy,'event':event})
server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(store))
server.daemon_threads = True
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
print(json.dumps({'url':'http://127.0.0.1:'+str(server.server_port),'project_id':project_id}), flush=True)
try:
    for line in sys.stdin:
        if line.strip() == 'stop': break
finally:
    server.shutdown(); server.server_close(); thread.join(timeout=2); store.close()
`;
async function startPilot(directory, mode) {
  const child = spawn(python, ['-u','-c',pythonServer,directory,mode], {cwd:repository,env:{...process.env,PYTHONPATH:resolve(repository,'backend')},stdio:['pipe','pipe','pipe']});
  let errors = ''; child.stderr.on('data', chunk => { errors = (errors + chunk).slice(-2000); });
  const exited = new Promise(resolveExit => child.once('exit', resolveExit));
  const ready = await new Promise((resolveReady, reject) => {
    let output = '';
    const timer = setTimeout(() => { child.kill(); reject(new Error('Isolated ingestion pilot startup timed out.')); },10000);
    child.once('error', error => { clearTimeout(timer); reject(error); });
    child.once('exit', code => { clearTimeout(timer); reject(new Error(`Isolated pilot exited (${code}): ${errors}`)); });
    child.stdout.on('data', chunk => { output += chunk; if (output.includes('\n')) { clearTimeout(timer); resolveReady(JSON.parse(output.split('\n')[0])); } });
  });
  return {...ready,stop:async () => { if (child.exitCode === null) { child.stdin.end('stop\n'); await exited; } }};
}

test('public ingestion commits through real Python with matching Unicode hashes, survives restart and keeps locations separate', async t => {
  const directory = await mkdtemp(join(tmpdir(),'hoomy-ingestion-integration-'));
  const pilotDirectory = join(directory,'pilot');
  let backend = await startPilot(pilotDirectory,'seed');
  let gateway, app;
  const server = createServer((req,res) => app(req,res));
  t.after(async () => {
    server.closeAllConnections(); await new Promise(resolveClose => server.close(resolveClose));
    gateway?.close(); await backend?.stop(); await rm(directory,{recursive:true,force:true});
  });
  await new Promise(resolveListen => server.listen(0,'127.0.0.1',resolveListen));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const configuration = {testMode:true,origin,backendUrl:backend.url,databasePath:join(directory,'auth.sqlite'),githubClientId:'fixture',githubClientSecret:'fake-secret',allowedGithubIds:['42'],cookieKeys:['x'.repeat(32)],ingestionProjectId:backend.project_id,clients:[{client_id:'fixture-user',redirect_uris:['http://127.0.0.1:4567/callback']}]};
  gateway = await createGateway(configuration); app = gateway.app;
  async function approvedDevice() {
    const id = randomUUID();
    const grant = new gateway.provider.Grant({accountId:'github:42',clientId:'hoomy-collector'});
    grant.addResourceScope(gateway.resources.collector,'device:self evidence:ingest'); const grantId = await grant.save();
    gateway.store.put('device',id,{account_id:'github:42',installation_id:randomUUID(),grant_id:grantId,status:'active',policy_revision:2,policy:{content_tier:'conversation_visible_v1',collection_enabled:true,roots:['/home/alex/.codex/sessions','/home/alex/.codex/archived_sessions'],exclusions:[],project_bindings:[{project_id:backend.project_id,cwd_roots:['/home/alex/work/alpha']}]}});
    gateway.store.put('device-grant',grantId,{device_id:id});
    const token = new gateway.provider.AccessToken({accountId:'github:42',clientId:'hoomy-collector',grantId,scope:'device:self evidence:ingest',expiresWithSession:false,resourceServer:{audience:gateway.resources.collector,accessTokenFormat:'opaque',accessTokenTTL:900}});
    return {id,headers:{Authorization:`Bearer ${await token.save()}`}};
  }
  const first = await approvedDevice(), second = await approvedDevice();
  const userGrant = new gateway.provider.Grant({accountId:'github:42',clientId:'fixture-user'});
  userGrant.addResourceScope(gateway.resources.projects,'projects:read');
  const userToken = new gateway.provider.AccessToken({accountId:'github:42',clientId:'fixture-user',grantId:await userGrant.save(),scope:'projects:read',expiresWithSession:false,resourceServer:{audience:gateway.resources.projects,accessTokenFormat:'opaque',accessTokenTTL:900}});
  const userHeaders = {Authorization:`Bearer ${await userToken.save()}`};
  const projection = {version:1,messages:[{role:'assistant',text:'Français 😀 — quote " slash / tab\t line\n separator\u2028',line_start:2,line_end:2,timestamp:null}]};
  const event = {schema_version:1,event_id:randomUUID(),type:'conversation.projected',project_id:backend.project_id,policy_revision:2,connector:'codex',session_id:randomUUID(),source_location:{path:'/home/alex/.codex/sessions/shared.jsonl',cwd:'/home/alex/work/alpha',archive_occurrence:false,modified_at:'2026-10-12T12:00:00Z'},sequence:1,source_sha256:'a'.repeat(64),projected_sha256:canonicalDigest(projection),observed_at:'2026-10-12T12:01:00Z',title:'Invented Unicode projection',coverage:{excluded_records:2,excluded_sensitive_messages:0,excluded_context_messages:1,excluded_oversized_records:0},projection};
  const post = (device,value) => fetch(`${origin}/v1/events`,{method:'POST',headers:{...device.headers,'Content-Type':'application/json','Idempotency-Key':value.event_id},body:JSON.stringify(value)});
  const context = () => fetch(`${backend.url}/api/projects/context/${backend.project_id}`).then(response => response.json());
  const initialRevision = (await context()).revision;
  const accepted = await post(first,event); assert.equal(accepted.status,202,await accepted.clone().text());
  const receipt = await accepted.json(); assert.equal(receipt.event_sha256,canonicalDigest(event)); assert.equal(receipt.state,'stored');
  assert.deepEqual(await (await post(first,event)).json(),receipt);
  const another = {...event,event_id:randomUUID()}; assert.equal((await post(second,another)).status,202);
  let profile = await context(); assert.equal(profile.revision,initialRevision);
  const remote = profile.sources.filter(source => source.cached);
  assert.equal(remote.length,2); assert.notEqual(remote[0].id,remote[1].id);
  assert.deepEqual(new Set(remote.map(source => source.device_id)),new Set([first.id,second.id]));
  assert.equal(remote[0].projected_sha256,remote[1].projected_sha256); assert.equal(remote[0].source_sha256_verified,false);
  const firstSource = remote.find(source => source.device_id === first.id);
  const evidence = source => fetch(`${origin}/v1/projects/${backend.project_id}/evidence?source_id=${source}`,{headers:userHeaders});
  assert.equal((await evidence(firstSource.id)).status,200);
  const {source_sha256,projected_sha256,title,coverage,projection:omittedProjection,...common} = event;
  const excluded = {...common,event_id:randomUUID(),type:'conversation.source_excluded',sequence:2,exclusion:{reason:'internal_approval_review',discriminator:'internal_guardian',header_sha256:'b'.repeat(64)}};
  const exclusionResponse = await post(first,excluded); assert.equal(exclusionResponse.status,202,await exclusionResponse.clone().text());
  const excludedReceipt = await exclusionResponse.json();
  assert.equal(Object.keys(excludedReceipt).length,10); assert.equal(excludedReceipt.event_type,excluded.type);
  assert.equal(excludedReceipt.header_sha256,excluded.exclusion.header_sha256); assert.equal(excludedReceipt.event_sha256,canonicalDigest(excluded));
  assert.ok(!Object.hasOwn(excludedReceipt,'projected_sha256'));
  assert.equal((await evidence(firstSource.id)).status,404);
  assert.equal((await post(first,{...event,event_id:randomUUID(),sequence:3})).status,409);
  assert.deepEqual(await (await post(first,event)).json(),receipt);
  // A separate archive location needs its own header classification.
  const archived = {...event,event_id:randomUUID(),source_location:{...event.source_location,path:'/home/alex/.codex/archived_sessions/shared.jsonl',archive_occurrence:true}};
  assert.equal((await post(first,archived)).status,202);
  const archiveSource = (await context()).sources.find(source => source.cached && source.source_location.archive_occurrence);
  assert.ok(archiveSource);
  const excludedArchive = {...excluded,event_id:randomUUID(),source_location:archived.source_location};
  assert.equal((await post(first,excludedArchive)).status,202);
  assert.equal((await evidence(archiveSource.id)).status,404);
  profile = await context(); assert.equal(profile.revision,initialRevision);
  assert.deepEqual(profile.sources.filter(source => source.cached).map(source => source.device_id),[second.id]);
  await backend.stop(); gateway.close();
  backend = await startPilot(pilotDirectory,'reopen');
  gateway = await createGateway({...configuration,backendUrl:backend.url}); app = gateway.app;
  const persisted = await fetch(`${origin}/v1/receipts/${event.event_id}`,{headers:first.headers}); assert.equal(persisted.status,200); assert.deepEqual(await persisted.json(),receipt);
  assert.deepEqual(await (await post(first,event)).json(),receipt);
  const excludedPersisted = await fetch(`${origin}/v1/receipts/${excluded.event_id}`,{headers:first.headers});
  assert.equal(excludedPersisted.status,200); assert.deepEqual(await excludedPersisted.json(),excludedReceipt);
  assert.deepEqual(await (await post(first,excluded)).json(),excludedReceipt);
  assert.equal((await evidence(firstSource.id)).status,404);
  assert.equal((await fetch(`${origin}/v1/receipts/${event.event_id}`,{headers:second.headers})).status,404);
  assert.equal((await post(first,{...event,title:'Different payload with reused key'})).status,409);
  profile = await context(); assert.equal(profile.revision,initialRevision); assert.equal(profile.sources.filter(source => source.cached).length,1);
});

test('public state facades validate a real two-device Python job, idempotent completion and version-pinned citations without changing context', async t => {
  const directory = await mkdtemp(join(tmpdir(),'hoomy-state-integration-'));
  const backend = await startPilot(join(directory,'pilot'),'state-seed');
  let gateway, app;
  const server = createServer((req,res)=>app(req,res));
  t.after(async()=>{server.closeAllConnections();await new Promise(resolve=>server.close(resolve));gateway?.close();await backend.stop();await rm(directory,{recursive:true,force:true});});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  gateway = await createGateway({testMode:true,origin,backendUrl:backend.url,databasePath:join(directory,'auth.sqlite'),githubClientId:'fixture',githubClientSecret:'fake',allowedGithubIds:['42'],cookieKeys:['x'.repeat(32)],stateProjectId:backend.project_id,clients:[{client_id:'fixture-user',redirect_uris:['http://127.0.0.1:4567/callback']}]});app=gateway.app;
  const scope = 'projects:read projects:state:refresh projects:state:execute';
  const grant = new gateway.provider.Grant({accountId:'github:42',clientId:'fixture-user'});grant.addResourceScope(gateway.resources.projects,scope);
  const token = new gateway.provider.AccessToken({accountId:'github:42',clientId:'fixture-user',grantId:await grant.save(),scope,expiresWithSession:false,resourceServer:{audience:gateway.resources.projects,accessTokenFormat:'opaque',accessTokenTTL:900}});
  const headers = {Authorization:`Bearer ${await token.save()}`};
  const base = `/v1/projects/${backend.project_id}/state`;
  const request = (suffix,body,extra={}) => fetch(origin+base+suffix,{method:body===undefined?'GET':'POST',headers:{...headers,'Content-Type':'application/json',...extra},body:body===undefined?undefined:JSON.stringify(body)});
  const ok = async(response,status=200)=>{assert.equal(response.status,status,await response.clone().text());return response.json();};
  const context = ()=>fetch(`${backend.url}/api/projects/context/${backend.project_id}`).then(response=>response.json());
  const initialRevision=(await context()).revision;
  const initial = await ok(await request(''));
  assert.equal(initial.enabled,true);assert.equal(initial.state,null);assert.equal(initial.coverage.source_locations,2);assert.equal(initial.coverage.unique_projection_blobs,1);
  assert.equal(initial.method_version,'project-state-v4');assert.equal(canonicalDigest(initial.execution_policy),initial.method_hash);
  const refresh={request_id:randomUUID(),context_revision:initial.context_revision,corpus_revision:initial.corpus_revision,method_hash:initial.method_hash};
  const {method_hash,...missingMethod}=refresh;
  assert.equal((await request('/refresh',missingMethod,{'Idempotency-Key':refresh.request_id})).status,409);
  assert.equal((await request('/refresh',{...refresh,method_hash:'0'.repeat(64)},{'Idempotency-Key':refresh.request_id})).status,409);
  const job=await ok(await request('/refresh',refresh,{'Idempotency-Key':refresh.request_id}),202);
  assert.deepEqual(await ok(await request('/refresh',refresh,{'Idempotency-Key':refresh.request_id}),202),job);
  const pending=await ok(await request('/jobs'));assert.equal(pending.pending_run.run_id,job.run_id);assert.equal(pending.pending_run.status,'queued');assert.ok(!Object.hasOwn(pending.pending_run,'lease_id'));
  const workerBase='/jobs/'+job.run_id;
  const claim=await ok(await request(workerBase+'/claim',{worker_id:randomUUID()}));
  assert.equal((await ok(await request('/jobs'))).pending_run,null);
  const control={lease_id:claim.lease_id,input_hash:claim.input_hash};
  const heartbeat=await ok(await request(workerBase+'/heartbeat',control));assert.equal(heartbeat.input_hash,claim.input_hash);
  const call_id=randomUUID();
  const attempt=await ok(await request(workerBase+'/attempt',{...control,call_id,phase:'extract',chunk_index:0}));
  for (const [field,value] of Object.entries(claim.next_plan)) assert.deepEqual(attempt[field],value);
  assert.equal(canonicalDigest(attempt.input),attempt.payload_hash);assert.equal(attempt.input.messages.length,1);
  assert.equal((await request(workerBase+'/attempt',{...control,call_id,phase:'extract',chunk_index:0})).status,409);
  const message=attempt.input.messages[0];
  const passage=message.passages[0];assert.match(passage.passage_id,/^p[0-9]{4}_[0-9]{2}$/);assert.ok(!Object.hasOwn(message,'text'));
  assert.equal(attempt.input.control.selection_partial,true);assert.equal(attempt.input.control.required_machine_aliases.length,1);
  assert.ok(!Object.hasOwn(attempt.input,'coverage'));
  const output={schema_version:1,assertions:[{key:'delivery',topic:'Fictional delivery',text:'The participant reports that delivery is open.',kind:'reported_status',reported_state:'reported_open',limitations:['Conversation report; no current verification.'],source_refs:[{passage_id:passage.passage_id}]}],uncertainties:[],contradictions:[]};
  const method={adapter:'codex-native-v1',model:'gpt-6.1-sol',reasoning_effort:'low',elapsed_ms:1,usage:{input_tokens:10,output_tokens:5,total_tokens:15}};
  const finish={...control,call_id,output,method};
  const completed=await ok(await request(workerBase+'/finish',finish));assert.equal(completed.status,'running');
  assert.deepEqual(await ok(await request(workerBase+'/finish',finish)),completed);
  const reconcile_id=randomUUID();
  const reconciliation=await ok(await request(workerBase+'/attempt',{...control,call_id:reconcile_id,phase:'reconcile',chunk_index:null}));
  assert.deepEqual(reconciliation.input.extractions,[{machine_alias:attempt.input.control.required_machine_aliases[0],shared_passage_ids:[passage.passage_id],result:output}]);assert.equal(canonicalDigest(reconciliation.input),reconciliation.payload_hash);
  assert.equal(reconciliation.input.control.required_machine_aliases.length,2);assert.ok(!Object.hasOwn(reconciliation.input,'coverage'));
  assert.deepEqual(reconciliation.input.passages,[passage]);
  const final=await ok(await request(workerBase+'/finish',{...finish,call_id:reconcile_id,method:{...method,reasoning_effort:'medium'}}));assert.equal(final.status,'ready');
  const cached=await ok(await request(''));assert.equal(cached.state.assertions.length,1);assert.equal(cached.context_revision,initial.context_revision);
  assert.equal(cached.state.device_coverage.cross_machine_complete,true);assert.equal(cached.state.device_coverage.cited_devices.length,2);
  const citation_id=cached.state.assertions[0].citation_ids[0];
  const citation=await ok(await request(workerBase+'/citations/'+citation_id));assert.equal(citation.quote,passage.text);assert.equal(citation.origins.length,2);
  assert.equal(citation.passage_id,passage.passage_id);assert.equal(citation.char_start,0);assert.equal(citation.char_end,Array.from(passage.text).length);
  assert.deepEqual(new Set(citation.origins.map(value=>value.device_id)),new Set(['11111111-1111-4111-8111-111111111111','22222222-2222-4222-8222-222222222222']));
  assert.equal((await context()).revision,initialRevision);
  const progress=await ok(await request(workerBase));assert.equal(progress.dispatched_calls,2);assert.equal(progress.completed_calls,2);assert.equal(progress.observed_tokens,30);
  assert.ok(!Object.hasOwn(progress,'lease_id'));assert.ok(!Object.hasOwn(progress,'input'));
  const source=citation.origins[0];
  const exclusion={schema_version:1,event_id:randomUUID(),type:'conversation.source_excluded',project_id:backend.project_id,policy_revision:2,connector:'codex',session_id:source.session_id,source_location:source.source_location,sequence:2,observed_at:'2026-10-13T12:02:00Z',exclusion:{reason:'internal_approval_review',discriminator:'internal_guardian',header_sha256:'b'.repeat(64)}};
  const policy={id:source.device_id,revision:2,content_tier:'conversation_visible_v1',collection_enabled:true,roots:['/fictional/.codex/sessions'],exclusions:[],project_bindings:[{project_id:backend.project_id,cwd_roots:['/fictional/project']}]};
  await ok(await fetch(backend.url+'/api/ingestion/events',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({principal:{account_id:'github:42',workspace_id:'pilot',device_id:source.device_id},policy,event:exclusion})}));
  assert.equal((await request(workerBase+'/citations/'+citation_id)).status,404);
  const withdrawn=await ok(await request(''));assert.equal(withdrawn.state.assertions.length,0);assert.equal(withdrawn.state.suppressed_assertions,1);assert.equal(withdrawn.state.stale,true);
  assert.ok(!JSON.stringify(withdrawn).includes(passage.text));assert.equal((await context()).revision,initialRevision);
  const retryRequest={request_id:randomUUID(),context_revision:withdrawn.context_revision,corpus_revision:withdrawn.corpus_revision,method_hash:withdrawn.method_hash};
  const retryJob=await ok(await request('/refresh',retryRequest,{'Idempotency-Key':retryRequest.request_id}),202);
  const retryBase='/jobs/'+retryJob.run_id;
  const retryClaim=await ok(await request(retryBase+'/claim',{worker_id:randomUUID()}));
  const retryControl={lease_id:retryClaim.lease_id,input_hash:retryClaim.input_hash};
  const failedCall=randomUUID();
  const failedAttempt=await ok(await request(retryBase+'/attempt',{...retryControl,call_id:failedCall,phase:'extract',chunk_index:0}));
  const failure={...retryControl,call_id:failedCall,code:'budget_exceeded',method:{...method,usage:{input_tokens:200000,output_tokens:1,total_tokens:200001}}};
  const recorded=await ok(await request(retryBase+'/fail',failure));assert.equal(recorded.status,'needs_review');assert.equal(recorded.observed_tokens,200001);
  assert.deepEqual(await ok(await request(retryBase+'/fail',failure)),recorded);
  assert.equal((await request(retryBase+'/fail',{...failure,method:{...failure.method,elapsed_ms:2}})).status,409);
  assert.equal((await request(retryBase+'/attempt',{...retryControl,call_id:randomUUID(),phase:'extract',chunk_index:0})).status,409);
  const kept=await ok(await request(''));assert.equal(kept.state.run_id,job.run_id);assert.equal(kept.job.run_id,retryJob.run_id);assert.equal(kept.job.observed_tokens,200001);assert.equal((await context()).revision,initialRevision);
  const ordinary={...retryRequest,request_id:randomUUID()};
  const deduplicated=await ok(await request('/refresh',ordinary,{'Idempotency-Key':ordinary.request_id}),202);assert.equal(deduplicated.run_id,retryJob.run_id);assert.equal(deduplicated.status,'needs_review');
  const rejectedReady={...retryRequest,request_id:randomUUID(),retry_of_run_id:job.run_id};
  assert.equal((await request('/refresh',rejectedReady,{'Idempotency-Key':rejectedReady.request_id})).status,409);
  const reviewed={...retryRequest,request_id:randomUUID(),retry_of_run_id:retryJob.run_id};
  const child=await ok(await request('/refresh',reviewed,{'Idempotency-Key':reviewed.request_id}),202);assert.notEqual(child.run_id,retryJob.run_id);assert.equal(child.retry_of_run_id,retryJob.run_id);assert.equal(child.status,'queued');
  assert.deepEqual(await ok(await request('/refresh',reviewed,{'Idempotency-Key':reviewed.request_id}),202),child);
  const secondChild={...reviewed,request_id:randomUUID()};assert.equal((await request('/refresh',secondChild,{'Idempotency-Key':secondChild.request_id})).status,409);
  const oldJob=await ok(await request(retryBase));assert.equal(oldJob.status,'needs_review');assert.equal(oldJob.observed_tokens,200001);assert.equal(oldJob.dispatched_calls,1);assert.equal(oldJob.retry_run_id,child.run_id);
  const pendingChild=await ok(await request('/jobs'));assert.equal(pendingChild.pending_run.run_id,child.run_id);assert.equal(pendingChild.pending_run.retry_of_run_id,retryJob.run_id);
  const childBase='/jobs/'+child.run_id;const childClaim=await ok(await request(childBase+'/claim',{worker_id:randomUUID()}));
  const childAttempt=await ok(await request(childBase+'/attempt',{lease_id:childClaim.lease_id,input_hash:childClaim.input_hash,call_id:randomUUID(),phase:'extract',chunk_index:0}));
  assert.deepEqual(childAttempt.input,failedAttempt.input);assert.equal(childAttempt.payload_hash,failedAttempt.payload_hash);assert.equal(childClaim.input_hash,retryClaim.input_hash);
  const wrongEffort={lease_id:childClaim.lease_id,input_hash:childClaim.input_hash,call_id:childAttempt.call_id,output,method:{...method,reasoning_effort:'high'}};
  const rejected=await ok(await request(childBase+'/finish',wrongEffort));
  assert.equal(rejected.status,'failed');assert.equal(rejected.failure_code,'invalid_output');assert.equal(rejected.observed_tokens,15);
  assert.ok(!Object.hasOwn(rejected,'next_plan'));assert.equal(rejected.next,null);
  assert.deepEqual(await ok(await request(childBase+'/finish',wrongEffort)),rejected);
  assert.equal((await context()).revision,initialRevision);
});
