"""Inspectable execution graph from persisted evidence, not invented reasoning."""
import json
import uuid
from .store import StoreError, encode, now, string
from .project_dialogue import report
from .ideas import responsibility


class ResearchExplorer:
    def __init__(self, store):
        self.store=store
        store.db.execute('CREATE TABLE IF NOT EXISTS research_feedback '
                         '(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, request_id TEXT NOT NULL, data TEXT NOT NULL)')

    def context(self, project_ids):
        control=self.store._get('control');items=[]
        for row in self.store.db.execute('SELECT project_id,data FROM research_feedback ORDER BY rowid DESC LIMIT 100'):
            item=json.loads(row['data'])
            if (self.store._resolve(row['project_id'])['id'] in project_ids and item['generation']==control['generation']
                    and item['exclusions']==control['exclusions']):
                items.append(item)
        return items[:20]

    def graph(self, rid):
        with self.store.lock:
            request=json.loads(self.store.act._row(rid)['data'])
            control=self.store._get('control')
            if (not control['scope_confirmed'] or request['generation']!=control['generation']
                    or request.get('exclusions',[])!=control['exclusions']):
                raise StoreError('Cette recherche appartient à un ancien périmètre.',409)
            nodes=[];edges=[]
            phase_status={'queued':'waiting','running':'running','ready':'done','failed':'blocked','cancelled':'blocked'}[request['status']]
            def node(key,column,title,status,detail):
                if not any(n['id']==key for n in nodes):
                    nodes.append({'id':key,'column':column,'title':title,'status':status,'detail':detail})
            def edge(start,end):
                edges.append({'source':start,'target':end})
            node('request',0,'Ta demande',phase_status,
                 request['instruction']+'\n'+str(request['attention_budget'])+' minutes · '+request['created_at'])
            sources=request.get('sources') or request.get('progress_sources',[])
            source_ids=set()
            for source in sources:
                if source['key']=='agent_verification':continue
                key='source:'+source['key'];source_ids.add(source['key'])
                messages=source.get('messages',[])
                detail='\n\n'.join(m.get('role','')+' : '+m.get('text','')[:900] for m in messages[-6:])
                node(key,1,source.get('title',source['key']),'done' if messages or source.get('read') else 'blocked',
                     (source.get('modified_at') or '')+'\nExtraits partiels.\n'+(detail or 'Lecture signalée ; contenu disponible à la fin de cette étape.'))
                edge('request',key)
            if not source_ids:
                node('sources',1,'Sources en préparation' if request['status'] in ('queued','running') else 'Aucune source lue',
                     'waiting' if request['status'] in ('queued','running') else 'blocked','Aucune lecture enregistrée à ce stade.');edge('request','sources')
            stages={'retrieve':'Repérage des sources','orient':'Clarification de l’objectif','suggest':'Recherche de pistes',
                    'suggest_after_check':'Réévaluation après vérification','read':'Lecture','verify':'Vérification',
                    'reconsider':'Réévaluation','queued':'En attente','running':'Traitement'}
            timings=[stages.get(t['stage'],t['stage'])+' : '+str(t.get('metrics',{}).get('turn_seconds','?'))+' s'
                     for t in request.get('trace',[])]
            node('analysis',2,'Analyse du contexte',phase_status,
                 request.get('rationale','')+'\n\nDurée totale : '+str(request.get('elapsed_seconds','en cours'))+' s\n'+'\n'.join(timings))
            for sid in source_ids:edge('source:'+sid,'analysis')
            candidates=request.get('candidates',[])
            related=set();candidate_nodes={}
            for c in candidates:
                key='candidate:'+c['id'];candidate_nodes[c['id']]=key
                node(key,2,c['title'],'done',c['action']+'\n\n'+c['why_now'])
                edge('analysis',key)
                for ref in c['source_refs']:
                    if ref in source_ids:edge('source:'+ref,key)
                row=self.store.db.execute("SELECT id,data FROM ideas WHERE project_id=? AND json_extract(data,'$.candidate.completion_claim')=?",
                                          (c['project_id'],c.get('completion_claim'))).fetchone()
                idea=json.loads(row['data']) if row else {}
                if row:related.add(row['id'])
                state=idea.get('state','unknown')
                explanation={'unknown':'État inconnu : cette piste ne peut pas être proposée comme travail restant.',
                    'completed':'Déjà terminé : exclu des suggestions.', 'archived':'Écarté des suggestions.',
                    'blocked':'Bloqué : une condition manque.', 'pending':'Reste à faire selon une observation datée ; le choix vérifie encore la fraîcheur et le contexte.'}.get(state,state)
                owners={'user':'toi','agent':'Hoomy / un agent','external':'un tiers','unknown':'inconnu'}
                current=idea or {'candidate':c}
                if state=='pending':
                    if not idea.get('valid_until') or idea['valid_until']<=now():explanation='Confirmation expirée : état à vérifier à nouveau.'
                    elif responsibility(current,'actor')!='user':explanation='Cette activité n’est pas destinée à la personne.'
                    elif c['expected_attention_minutes']>request['attention_budget']:explanation='Durée supérieure au temps disponible.'
                    elif not idea.get('pool_active',True):explanation='Piste conservée en réserve, hors du pool actif.'
                detail=explanation+'\n\nQui agit : '+owners.get(responsibility(current,'actor'),'inconnu')+'\nQui peut vérifier : '+owners.get(responsibility(current,'state_owner'),'inconnu')
                if idea.get('snoozed_until'):detail+='\nMis de côté jusqu’au '+idea['snoozed_until']
                context={'project_id':request.get('project_scope'),'attention_budget':request['attention_budget']}
                eligible=bool(idea) and self.store.ideas._eligible(idea,context)
                node('state:'+c['id'],3,'État · '+c['title'],'done' if eligible else 'blocked',detail)
                edge(key,'state:'+c['id'])
            if not candidates:
                node('candidates',2,'Pistes en préparation' if request['status'] in ('queued','running') else 'Aucune piste retenue',
                     'waiting' if request['status'] in ('queued','running') else 'blocked',request.get('rationale') or request.get('error') or 'La génération n’a pas encore produit de résultat.')
                edge('analysis','candidates')
            checks=[request]
            for row in self.store.db.execute("SELECT data FROM act_requests WHERE json_extract(data,'$.purpose')='verify_idea' ORDER BY rowid DESC"):
                check=json.loads(row[0])
                if check.get('target_idea_id') in related:
                    related.remove(check['target_idea_id']);checks.append(check)
            for check in checks:
                for source in check.get('sources',[]):
                    if source['key']!='agent_verification':continue
                    for message in source.get('messages',[]):
                        try:record=json.loads(message['text'])
                        except (ValueError,KeyError):continue
                        if not isinstance(record,dict):continue
                        key='check:'+check['id']
                        node(key,3,'Vérification · '+{'unknown':'non concluante','confirmed':'affirmation confirmée','contradicted':'affirmation contredite'}.get(record.get('status'),'inconnue'),
                             'blocked' if record.get('status')=='unknown' else 'done',
                             '\n\n'.join(str(record.get(k,'')) for k in ('finding','evidence','limits','checked_at')))
                        edge(candidate_nodes.get(record.get('candidate_id'),'request'),key)
            jobs=[json.loads(r[0]) for r in self.store.db.execute("SELECT data FROM idea_jobs WHERE json_extract(data,'$.preparation_id')=? ORDER BY rowid DESC LIMIT 4",(rid,))]
            for job in jobs:
                key='decision:'+job['id'];decision=job.get('decision') or {}
                answers=[a.get('name','')+' → '+str(a.get('choice',a.get('type'))) for a in decision.get('answers',[])]
                label={'none':'aucune activité retenue','context_question':'clarifier le projet','question':'confirmer un état',
                       'activity':'activité proposée','preparing':'recherche en cours','checking_questions':'contrôle des questions','choosing':'choix en cours'}.get(job['kind'],job['kind'])
                node(key,4,'Décision · '+label,'running' if job['status'] in ('queued','running','preparing') else ('blocked' if job['kind']=='none' else 'done'),
                     job.get('message','')+'\n\n'+'\n'.join(answers))
                edge('request',key)
            question=self.store.dialogue.available(request)
            if question:
                review=(request.get('context_question_review') or {}).get('decision')
                node('intent',4,'Question sur le projet','done' if review=='ask_user' else 'waiting',
                     question['prompt']+'\n\n'+question['why']+'\n\n'+question['changes_decision'])
                for ref in question['source_refs']:edge('source:'+ref,'intent')
            summary=report(request)
            node('outcome',4,'Bilan et points ouverts','blocked' if summary['unresolved'] else 'done',
                 summary['summary']+'\n\n'+'\n'.join(summary['unresolved']))
            for n in nodes:
                if n['column']==3:edge(n['id'],'outcome')
            edge('analysis','outcome')
            active=self.store.act.work()
            queued=sum(bool(i.get('verification_due')) for i in self.store.ideas._visible() if i.get('pool_active',True))
            background=('Recherche en cours · '+stages.get(active.get('progress_stage',active['status']),active['status']) if active else
                        (str(queued)+' vérification(s) en attente' if queued else 'Aucun traitement en cours. Hoomy attend ton choix.'))
            feedback=[json.loads(r[0]) for r in self.store.db.execute('SELECT data FROM research_feedback WHERE request_id=? ORDER BY rowid DESC',(rid,))]
            return {'request_id':rid,'status':request['status'],'background':background,'nodes':nodes,'edges':edges,
                    'projects':[{'id':p['id'],'name':p['name']} for p in request.get('projects',[])],
                    'feedback':feedback,'next_step':'Réponds à la question sur le projet dans l’écran principal.' if question and (request.get('context_question_review') or {}).get('decision')=='ask_user'
                    else 'Sélectionne une étape pour comprendre le résultat ou signaler le contexte manquant.'}

    def feedback(self,payload):
        with self.store.lock,self.store.db:
            graph=self.graph(payload.get('request_id'))
            selected=next((n for n in graph['nodes'] if n['id']==payload.get('node_id')),None)
            if not selected or payload.get('project_id') not in {p['id'] for p in graph['projects']}:
                raise StoreError('Retour hors du périmètre de la recherche.')
            control=self.store._get('control')
            item={'id':str(uuid.uuid4()),'request_id':graph['request_id'],'node_id':selected['id'],
                  'node_title':selected['title'],'project_id':payload['project_id'],
                  'text':string(payload.get('text'),'feedback',maximum=4000),'at':now(),
                  'source':'user_research_feedback','generation':control['generation'],'exclusions':control['exclusions']}
            self.store.db.execute('INSERT INTO research_feedback VALUES (?,?,?,?)',
                                 (item['id'],item['project_id'],graph['request_id'],encode(item)))
            return item
