import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const html=await readFile(new URL('../ui/home.html',import.meta.url),'utf8');
const script=html.split('<script type="module">')[1].split('</script>')[0];
const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;

// Execute the actual UI flow with a minimal DOM, rather than a copy of its logic.
async function fixture(){
  const nodes=new Map();
  const created=[];
  class Element{
    constructor(tag){this.tagName=tag;this.children=[];this.dataset={};this.style={};this.value='';this.disabled=false;this.readOnly=false;this._text='';created.push(tag);}
    set id(value){this._id=value;nodes.set(value,this);} get id(){return this._id;}
    set textContent(value){this._text=String(value);this.children=[];} get textContent(){return this._text+this.children.map(node=>typeof node==='string'?node:node.textContent).join(' ');}
    append(...children){this.children.push(...children);}
    replaceChildren(...children){this._text='';this.children=[...children];}
    setAttribute(name,value){this[name]=value;}
    querySelectorAll(selector){const output=[];const visit=node=>{for(const child of node.children){if(typeof child==='string')continue;if(child.tagName===selector)output.push(child);visit(child);}};visit(this);return output;}
  }
  const document={createElement:tag=>new Element(tag),getElementById:id=>nodes.get(id),createTextNode:text=>Object.assign(new Element('#text'),{textContent:text}),
    querySelectorAll:selector=>[...new Set(selector.split(',').flatMap(part=>{const [root,tag]=part.trim().split(' ');return nodes.get(root.slice(1)).querySelectorAll(tag);} ))]};
  for(const [,id]of html.matchAll(/\bid="([^"]+)"/g)){const node=new Element('div');node.id=id;}
  const window={addEventListener(){}};window.parent=window;
  const coverage={excluded_records:7,excluded_sensitive_messages:2,excluded_context_messages:3,excluded_oversized_records:1};
  const source=(device,partial)=>({id:'remote:'+device.repeat(64),title:'Fixture evidence',cached:true,device_id:device==='a'?'d0fd07d4-fixture':'61d2b993-fixture',source_location:{archive_occurrence:false},sequence:1,version_count:1,message_count:2,modified_at:null,received_at:null,partial,coverage:{...coverage,excluded_oversized_records:partial?1:0}});
  const profile={project:{id:'p',name:'Fixture project'},fields:{},ideas:[],research:[],feedback:[],revision:'r',
    authority:{mode:'central_read_only'},capabilities:{source_chat_navigation:false,cached_project_evidence:true},
    evidence_coverage:{legacy_unassigned_sources:1,projected:{source_locations:2,unique_projection_blobs:1,excluded_internal_sources:4}},sources:[source('a',true),source('b',false),{id:'legacy-fixture',title:'The following is the Codex agent history…',modified_at:null}]};
  const calls=[];
  const transport={lostRetryAcks:0};
  const central={mode:'central',origin:'https://central.example'};
  const stateData={project_id:'p',connection:central,enabled:true,method_version:'project-state-v3',method_hash:'d'.repeat(64),context_revision:'a'.repeat(64),corpus_revision:'b'.repeat(64),job:null,
    state:{run_id:'run-fixture',generated_at:null,stale:false,stale_reasons:[],suppressed_assertions:0,
      coverage:{provided_messages:3,provided_passages:5,eligible_messages:5,provided_sources:2,provided_sources_by_device:{'d0fd07d4-fixture':1,'61d2b993-fixture':1},partial:true,legacy_unassigned_sources:1,excluded_internal_sources:4,partial_sources:1},
      assertions:[{topic:'Fixture project report',kind:'reported_status',reported_state:'unknown',text:'<script>Historical reported statement</script>',limitations:['Historical only.'],citation_ids:['citation-fixture']}],uncertainties:[],contradictions:[]}};
  const fetch=async(_url,options)=>{const call=JSON.parse(options.body);calls.push(call);let data;
    if(call.name==='hoomy_home')data={projects:[{id:'p',name:'Fixture project'}],can_search:false,selection:null,connection:{mode:'central',origin:'https://central.example'},capabilities:{research:false}};
    else if(call.name==='hoomy_project')data=profile;
    else if(call.name==='hoomy_update_project'){profile.revision='saved';for(const [key,answer]of Object.entries(call.arguments.fields))profile.fields[key]={answer};stateData.context_revision='c'.repeat(64);stateData.state.stale=true;data=profile;}
    else if(call.name==='hoomy_project_evidence')data={messages:[{role:'user',text:'<script>untrusted historical text</script>',line_start:2,line_end:2,text_truncated:true}],total_messages:2,coverage,next_offset:1};
    else if(call.name==='hoomy_project_state')data=stateData;
    else if(call.name==='hoomy_refresh_project_state'){stateData.job={run_id:'run-fixture-2',status:'queued',retry_of_run_id:call.arguments.retry_of_run_id||null};data=stateData.job;if(call.arguments.retry_of_run_id&&transport.lostRetryAcks-- >0)throw new Error('Fixture lost retry acknowledgement');}
    else if(call.name==='hoomy_project_state_citation')data={connection:central,quote:'Exact original language quote.',excerpt:'Exact original language quote.',excerpt_truncated:true,line_start:22,line_end:22,message_timestamp:null,
      origins:[{device_id:'61d2b993-fixture',title:'Preserved original title',session_id:'native-fixture-id',sequence:4,source_location:{modified_at:null},received_at:null}]};
    else throw new Error('Unexpected fixture tool');
    return {json:async()=>({structuredContent:structuredClone(data)})};};
  const Option=function(text,value){const node=new Element('option');node.textContent=text;node.value=value;return node;};
  const ui=await new AsyncFunction('window','document','fetch','navigator','Option','setTimeout','clearTimeout',script+'\nreturn {run,openProfile,setBusy,readEvidence};')(window,document,fetch,{},Option,()=>0,()=>{});
  return {ui,nodes,profile,created,calls,Element,stateData,transport};
}

test('authority-disabled actions survive the real busy lifecycle while other project forms remain writable',async()=>{
  const f=await fixture();
  assert.equal(f.nodes.get('connection-origin').hidden,false);
  assert.equal(f.nodes.get('connection-origin').textContent,'Projets centralisés · https://central.example');
  await f.ui.run(()=>f.ui.openProfile('p'));
  const controls=f.nodes.get('profile-result').querySelectorAll('button');
  const save=controls.find(node=>node.textContent==='Enregistrer mes objectifs');
  const guide=controls.find(node=>node.textContent==='M’aider à formuler mes objectifs');
  assert.equal(save.disabled,true);
  assert.equal(guide.disabled,true);
  assert.ok(f.nodes.get('profile-form').querySelectorAll('textarea').every(node=>node.readOnly&&!node.disabled));
  await f.ui.run(async()=>{});
  assert.equal(save.disabled,true);
  assert.equal(guide.disabled,true);
  delete f.profile.authority;
  await f.ui.run(()=>f.ui.openProfile('p'));
  assert.equal(f.nodes.get('profile-result').querySelectorAll('button').find(node=>node.textContent==='Enregistrer mes objectifs').disabled,false);
  assert.ok(f.nodes.get('profile-form').querySelectorAll('textarea').every(node=>!node.readOnly));
});

test('central cited state is read-only until explicit refresh and quotes remain inert and versioned',async()=>{
  const f=await fixture();f.profile.capabilities.project_state=true;
  await f.ui.run(()=>f.ui.openProfile('p'));
  const text=f.nodes.get('project-state').textContent;
  assert.match(text,/Service central Hoomy · https:\/\/central.example/);
  const origin=f.nodes.get('project-state').children.find(node=>node.textContent==='Service central Hoomy · https://central.example');
  assert.equal(origin.tagName,'p');
  assert.equal(origin.className,'muted');
  assert.match(text,/État déclaré/);
  assert.match(text,/État actuel inconnu/);
  assert.match(text,/Couverture partielle/);
  assert.match(text,/5 passage\(s\) exact\(s\) fourni\(s\)/);
  assert.match(text,/Machine 61d2b993/);
  assert.ok(f.calls.every(call=>!call.name.includes('refresh')&&!call.name.includes('worker')));
  const why=f.nodes.get('project-state').querySelectorAll('button').find(node=>node.textContent.startsWith('Pourquoi'));
  await why.onclick();
  assert.equal(f.calls.at(-1).name,'hoomy_project_state_citation');
  assert.equal(f.calls.at(-1).arguments.run_id,'run-fixture');
  const after=f.nodes.get('project-state').textContent;
  assert.match(after,/native-fixture-id · version 4/);
  assert.match(after,/Lignes 22–22/);
  assert.match(after,/Exact original language quote/);
  assert.equal(f.created.includes('script'),false);
  const refresh=f.nodes.get('project-state').querySelectorAll('button').find(node=>node.textContent==='Actualiser la synthèse');
  await refresh.onclick();
  assert.equal(f.calls.filter(call=>call.name==='hoomy_refresh_project_state').length,1);
  assert.equal(f.calls.find(call=>call.name==='hoomy_refresh_project_state').arguments.method_hash,'d'.repeat(64));
  assert.match(f.nodes.get('project-state').textContent,/En attente de l’exécuteur Mac/);
  assert.ok(f.calls.every(call=>call.name!=='hoomy_project_state_worker'));
});

test('expired completed confirmations are historical while fresh or unknown dates and human records stay unchanged',async()=>{
  const f=await fixture();
  const dates=['2000-01-08T00:00:00Z','2100-01-08T00:00:00Z',undefined,'not-a-date'];
  f.profile.ideas=dates.map((valid_until,index)=>({state:'completed',confirmed_at:'2000-01-07T00:00:00Z',valid_until,
    candidate:{title:'Fixture beta release '+index,action:'Original recorded release action.',completion_claim:'Original recorded completion claim.'},
    evidence:{source:'user_confirmation'},observation:{evidence:{answer:'already_done'}}}));
  const before=structuredClone(f.profile);
  await f.ui.run(()=>f.ui.openProfile('p'));
  const cards=f.nodes.get('profile-result').querySelectorAll('div').filter(node=>node.className==='idea');
  assert.equal(cards.length,4);
  assert.equal(cards[0].children[0].textContent,'Déclaration historique · confirmation expirée');
  assert.match(cards[0].textContent,/Le statut enregistré reste conservé ; il ne vérifie pas l’état actuel/);
  assert.match(cards[0].textContent,/Ta réponse : déjà fait/);
  assert.equal(cards[0].querySelectorAll('h3')[0].textContent,'Fixture beta release 0');
  for(const card of cards.slice(1)){
    assert.equal(card.children[0].textContent,'Déjà fait');
    assert.doesNotMatch(card.textContent,/confirmation expirée|Confirmation expirée/);
  }
  assert.deepEqual(f.profile,before);
  assert.deepEqual(f.calls.map(call=>call.name),['hoomy_home','hoomy_project']);
});

test('saving approved context reloads state freshness before a subsequent explicit refresh',async()=>{
  const f=await fixture();delete f.profile.authority;f.profile.capabilities.project_state=true;
  await f.ui.run(()=>f.ui.openProfile('p'));
  const goal=f.nodes.get('field-goal');goal.value='Owner-authored fixture goal';goal.oninput();
  await f.nodes.get('profile-form').onsubmit({preventDefault(){}});
  assert.deepEqual(f.calls.slice(-2).map(call=>call.name),['hoomy_update_project','hoomy_project_state']);
  assert.match(f.nodes.get('project-state').textContent,/Le contexte ou les sources ont changé/);
  assert.equal(f.calls.some(call=>call.name==='hoomy_refresh_project_state'),false);
  const refresh=f.nodes.get('project-state').querySelectorAll('button').find(node=>node.textContent==='Actualiser la synthèse');
  await refresh.onclick();
  assert.equal(f.calls.find(call=>call.name==='hoomy_refresh_project_state').arguments.context_revision,'c'.repeat(64));
});

test('reviewed new attempt requires two explicit steps and preserves its request identity after a lost acknowledgement',async()=>{
  const f=await fixture();f.profile.capabilities.project_state=true;
  f.stateData.state=null;
  f.stateData.job={run_id:'d3f17481-faca-4ece-a81b-5d478dec3a3a',status:'needs_review',execution_compatible:true,unknown_usage_calls:1,retry_run_id:null};
  const before=structuredClone(f.profile);
  await f.ui.run(()=>f.ui.openProfile('p'));
  assert.ok(f.calls.every(call=>!call.name.includes('refresh')&&!call.name.includes('worker')));
  const retry=f.nodes.get('project-state').querySelectorAll('button').find(node=>node.textContent==='Nouvel essai après revue');
  await retry.onclick();
  assert.match(f.nodes.get('project-state').textContent,/Un nouveau coût est possible/);
  assert.match(f.nodes.get('project-state').textContent,/L’usage du précédent appel reste inconnu/);
  assert.equal(f.calls.some(call=>call.name==='hoomy_refresh_project_state'),false);
  const confirm=f.nodes.get('project-state').querySelectorAll('button').find(node=>node.textContent==='Confirmer le nouvel essai');
  f.transport.lostRetryAcks=1;
  await confirm.onclick();await confirm.onclick();
  const requests=f.calls.filter(call=>call.name==='hoomy_refresh_project_state');
  assert.equal(requests.length,2);
  assert.deepEqual(requests[0].arguments,requests[1].arguments);
  assert.equal(requests[0].arguments.retry_of_run_id,'d3f17481-faca-4ece-a81b-5d478dec3a3a');
  assert.equal(requests[0].arguments.context_revision,'a'.repeat(64));
  assert.equal(requests[0].arguments.method_hash,'d'.repeat(64));
  assert.match(f.nodes.get('project-state').textContent,/En attente de l’exécuteur Mac/);
  assert.deepEqual(f.profile,before);
  assert.equal(f.calls.some(call=>call.name==='hoomy_project_state_worker'),false);
});

test('failed generation distinguishes an absent result from a preserved earlier synthesis without creating work',async()=>{
  for(const hasState of [false,true]){
    const f=await fixture();f.profile.capabilities.project_state=true;
    f.stateData.job={run_id:'failed-run',status:'failed',execution_compatible:true};
    if(!hasState)f.stateData.state=null;
    const before=structuredClone(f.profile);
    await f.ui.run(()=>f.ui.openProfile('p'));
    const text=f.nodes.get('project-state').textContent;
    assert.match(text,/La génération s’est arrêtée/);
    if(hasState){assert.match(text,/La dernière synthèse reste conservée/);assert.match(text,/Historical reported statement/);}
    else{assert.match(text,/Aucun résultat n’a été publié/);assert.match(text,/Aucune synthèse enregistrée/);assert.doesNotMatch(text,/La dernière synthèse reste conservée|Historical reported statement/);}
    assert.ok(f.calls.every(call=>!call.name.includes('refresh')&&!call.name.includes('worker')));
    assert.deepEqual(f.profile,before);
  }
});

test('method changes preserve historical results and require explicit refresh, with partial inputs disclosed',async()=>{
  const f=await fixture();f.profile.capabilities.project_state=true;
  f.stateData.job={run_id:'old-run',status:'needs_review',method_version:'project-state-v1',method_hash:null,execution_compatible:false};
  f.stateData.state.stale=true;f.stateData.state.stale_reasons=['method_changed'];
  Object.assign(f.stateData.state.coverage,{provided_messages:85,eligible_messages:656,unread_messages:571,truncated_messages:13,context_truncated_dimensions:['goal']});
  const before=structuredClone(f.profile);
  await f.ui.run(()=>f.ui.openProfile('p'));
  const panel=f.nodes.get('project-state'),text=panel.textContent;
  assert.equal(panel.children.find(node=>node.textContent==='Méthode de synthèse : project-state-v3').tagName,'p');
  assert.match(text,/synthèse historique utilise une ancienne méthode/);
  assert.match(text,/85 message\(s\).*656/);
  assert.match(text,/571 message\(s\) non lu\(s\) et 13 message\(s\) fourni\(s\) sous forme de préfixe limité/);
  assert.match(text,/ne couvre pas l’ensemble du projet/);
  assert.match(text,/déclarations enregistrées restent intactes/);
  assert.match(text,/Historical reported statement/);
  assert.equal(panel.querySelectorAll('button').some(node=>node.textContent==='Nouvel essai après revue'),false);
  assert.ok(f.calls.every(call=>!call.name.includes('refresh')&&!call.name.includes('worker')));
  assert.deepEqual(f.profile,before);
});

test('per-machine source view and cached excerpts disclose omissions and keep historical text inert',async()=>{
  const f=await fixture();
  await f.ui.run(()=>f.ui.openProfile('p'));
  const text=f.nodes.get('profile-result').textContent;
  assert.match(text,/Machine d0fd07d4/);
  assert.match(text,/Machine 61d2b993/);
  assert.match(text,/historique.*non attribuée/);
  assert.match(text,/2 emplacement.*1 contenu/);
  assert.match(text,/2 message\(s\) retiré\(s\) pour confidentialité/);
  assert.match(text,/3 message\(s\) de configuration exclus/);
  assert.match(text,/Projection partielle.*1 enregistrement/);
  const container=new f.Element('section');
  await f.ui.readEvidence(container,'p','remote:'+'a'.repeat(64));
  assert.match(container.textContent,/confidentialité/);
  assert.match(container.textContent,/configuration exclus/);
  assert.match(container.textContent,/Projection partielle/);
  assert.match(container.textContent,/Extrait limité/);
  assert.ok(container.querySelectorAll('pre')[0].textContent.includes('<script>'));
  assert.equal(f.created.includes('script'),false);
  const next=container.querySelectorAll('button').find(node=>node.textContent==='Échanges suivants');
  await next.onclick();
  assert.equal(f.calls.at(-1).arguments.offset,1);
  assert.ok(f.calls.every(call=>['hoomy_home','hoomy_project','hoomy_project_evidence'].includes(call.name)));
});

test('source metadata has separate readable blocks, verified provenance labels and visible-message wording',async()=>{
  const f=await fixture();
  await f.ui.run(()=>f.ui.openProfile('p'));
  const sections=f.nodes.get('profile-result').querySelectorAll('section').filter(node=>node.className==='source-card');
  assert.equal(sections.length,3);
  const synced=sections[0];
  assert.equal(synced.children[0].className,'source-badges');
  assert.match(synced.children[0].textContent,/Machine d0fd07d4.*Source synchronisée/);
  const metadata=synced.children.find(node=>node.className==='source-meta');
  assert.equal(metadata.children.length,5);
  assert.match(metadata.children[0].textContent,/2 messages visibles en cache/);
  assert.match(metadata.children[1].textContent,/Dernière modification indiquée par la machine/);
  assert.match(metadata.children[2].textContent,/Reçu par Hoomy/);
  assert.match(metadata.children[3].textContent,/Aucune analyse demandée/);
  const legacy=sections[2];
  assert.match(legacy.textContent,/Historique importé · machine non attribuée/);
  assert.match(legacy.textContent,/Provenance de la machine non vérifiée/);
  assert.equal(legacy.querySelectorAll('h3')[0].textContent,'The following is the Codex agent history…');
  assert.equal(legacy.querySelectorAll('button').length,0);
  assert.doesNotMatch(f.nodes.get('profile-result').textContent,/public\(s\)|échanges publics/);
  assert.match(f.nodes.get('profile-result').textContent,/Sessions internes de contrôle exclues : 4/);
  const container=new f.Element('section');
  await f.ui.readEvidence(container,'p','remote:'+'a'.repeat(64));
  assert.equal(container.className,'evidence-cache');
  assert.match(container.textContent,/Messages visibles dans la conversation/);
  assert.match(container.textContent,/2 messages visibles en cache/);
  assert.match(html,/\.source-meta,\.evidence-cache\{display:grid;gap:10px/);
  assert.match(html,/\.source-meta small,\.source-card details small,\.evidence-cache small\{display:block/);
});

test('historical Mac-only conclusions expose uncited VPS provenance without changing saved state or dispatching work',async()=>{
  const f=await fixture();f.profile.capabilities.project_state=true;
  f.stateData.state.device_coverage={provided_devices:['d0fd07d4-fixture','61d2b993-fixture'],cited_devices:['d0fd07d4-fixture'],uncited_provided_devices:['61d2b993-fixture'],cross_machine_complete:false};
  f.stateData.state.stale=true;f.stateData.state.stale_reasons=['method_changed'];
  const before=structuredClone(f.stateData.state);
  await f.ui.run(()=>f.ui.openProfile('p'));
  let text=f.nodes.get('project-state').textContent;
  assert.match(text,/2 machine\(s\) représentée\(s\).*1 machine\(s\) citée\(s\)/);
  assert.match(text,/aucune conclusion citée pour Machine 61d2b993/);
  assert.match(text,/ne décrit pas un état global Mac \+ VPS/);
  assert.deepEqual(f.stateData.state,before);
  assert.ok(f.calls.every(c=>!c.name.includes('refresh')&&!c.name.includes('worker')));
  f.stateData.state.device_coverage.cited_devices.push('61d2b993-fixture');
  f.stateData.state.device_coverage.uncited_provided_devices=[];f.stateData.state.device_coverage.cross_machine_complete=true;
  await f.ui.run(()=>f.ui.openProfile('p'));
  text=f.nodes.get('project-state').textContent;
  assert.doesNotMatch(text,/aucune conclusion citée pour/);
  assert.match(text,/2 machine\(s\) citée\(s\)/);
});

test('reader quality distinguishes raw and visible counts, exclusions and source withdrawal without regenerating prose',async()=>{
  const f=await fixture();f.profile.capabilities.project_state=true;
  f.stateData.state.visible_revision='e'.repeat(64);
  f.stateData.state.quality={policy_version:'project-state-reader-quality-v1',policy_hash:'f'.repeat(64),raw_assertions:11,raw_uncertainties:4,source_eligible_assertions:10,source_eligible_uncertainties:4,visible_assertions:9,visible_uncertainties:3,
    excluded_items:[{id:'rejected-assertion-id',section:'assertions',reasons:['nonterminal_prose','unclosed_code']},{id:'rejected-uncertainty-id',section:'uncertainties',reasons:['unsupported_version']}]};
  const before=structuredClone(f.stateData.state);
  await f.ui.run(()=>f.ui.openProfile('p'));
  const text=f.nodes.get('project-state').textContent;
  assert.match(text,/Vue de lecture filtrée : 9 conclusion\(s\) · 3 incertitude\(s\)/);
  assert.match(text,/Synthèse brute conservée : 11 conclusion\(s\) · 4 incertitude\(s\)/);
  assert.match(text,/2 élément\(s\) écarté\(s\) pour qualité/);
  assert.match(text,/1 élément\(s\) masqué\(s\) après retrait ou modification/);
  assert.match(text,/rejected-assertion-id · Phrase non terminée · Code ou délimiteur non fermé/);
  assert.match(text,/Version absente des citations propres à cet élément/);
  assert.match(text,/ne prouvent pas à eux seuls le sens/);
  assert.deepEqual(f.stateData.state,before);
  assert.ok(f.calls.every(c=>!c.name.includes('refresh')&&!c.name.includes('worker')));
});
