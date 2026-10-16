"""Prepared ideas, dated human facts, and a fail-closed fast selection queue."""
import hashlib
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

from .act import number
from .store import StoreError, encode, now, string, require_object

POLICY = 'ready-ideas-v2'


def scope_key(control):
    return hashlib.sha256(encode([control['generation'], control['exclusions']]).encode()).hexdigest()


def later(hours=24):
    return (datetime.now(timezone.utc)+timedelta(hours=hours)).isoformat().replace('+00:00', 'Z')


def responsibility(idea, field):
    return idea.get(field+'_override') or idea.get('routing_override') or idea['candidate'].get(field,'unknown')


class Ideas:
    def __init__(self, store):
        self.store = store
        with store.lock:
            legacy_import = store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ideas'").fetchone() is None
            # Keep table creation and the first import atomic, including failed imports.
            store.db.execute('SAVEPOINT ideas_initialize')
            try:
                for definition in (
                    'ideas(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, data TEXT NOT NULL)',
                    'idea_observations(id TEXT PRIMARY KEY, idea_id TEXT NOT NULL REFERENCES ideas(id), data TEXT NOT NULL)',
                    'idea_jobs(id TEXT PRIMARY KEY, status TEXT NOT NULL, deadline REAL NOT NULL, data TEXT NOT NULL)',
                ):
                    store.db.execute('CREATE TABLE IF NOT EXISTS '+definition)
                # Existing ideas contain later decisions; never replay old requests over them.
                if legacy_import and not store.instance_authority():
                    for row in store.db.execute("SELECT data FROM act_requests WHERE status='ready'").fetchall():
                        self.ingest(json.loads(row[0]))
            except Exception:
                store.db.execute('ROLLBACK TO ideas_initialize')
                store.db.execute('RELEASE ideas_initialize')
                raise
            store.db.execute('RELEASE ideas_initialize')

    def _save(self, idea):
        self.store.db.execute('INSERT OR REPLACE INTO ideas VALUES (?,?,?)',
                              (idea['id'], idea['project_id'], encode(idea)))

    def _idea(self, iid):
        row = self.store.db.execute('SELECT data FROM ideas WHERE id=?', (iid,)).fetchone()
        if not row:
            raise StoreError('Idée introuvable.', 404)
        return json.loads(row[0])

    def _guard(self):
        control = self.store._get('control')
        if not control['scope_confirmed'] or not control['inference_enabled']:
            raise StoreError('Active le traitement dans Confidentialité.', 409)
        return control

    def ingest(self, request):
        control = self.store._get('control')
        if request.get('policy','').startswith('ready-ideas-'):
            return
        for candidate in request.get('candidates', []):
            claim = candidate.get('completion_claim') or 'Activité déjà réalisée : '+candidate['title']
            identity = encode([candidate['project_id'], ' '.join(claim.casefold().split())])
            iid = hashlib.sha256(identity.encode()).hexdigest()[:32]
            if self.store.db.execute('SELECT 1 FROM ideas WHERE id=?', (iid,)).fetchone():
                existing = self._idea(iid)
                if (request.get('purpose') in ('prepare_ideas','verify_idea') and existing['state'] not in ('completed','archived')
                        and existing['candidate']['action']==candidate['action']):
                    if request.get('purpose')=='verify_idea' and request.get('target_revision')!=existing['revision']:
                        continue
                    if request['created_at']>existing.get('metadata_request_at',''):
                        changed=False
                        for key in ('actor','state_owner','state_question','question_reason'):
                            if key in candidate:
                                changed = changed or existing['candidate'].get(key)!=candidate[key]
                                existing['candidate'][key] = candidate[key]
                        if changed:
                            existing['revision'] += 1
                        existing['metadata_request_at']=request['created_at']
                    self._save(existing)
                    self._verification(existing,candidate,request)
                continue
            idea = {'id': iid, 'project_id': candidate['project_id'], 'version': 1, 'revision': 0,
                    'candidate': dict(candidate, completion_claim=claim), 'state': 'unknown',
                    'confirmed_at': None, 'valid_until': None, 'snoozed_until': None,
                    'origin_request_id': request['id'], 'scope_key': scope_key(dict(control,
                        generation=request['generation'], exclusions=request.get('exclusions', []))),
                    'created_at': now(), 'evidence': None,'metadata_request_at':request['created_at']}
            self._save(idea)
            if request.get('purpose') in ('prepare_ideas','verify_idea'):
                self._verification(idea, candidate, request)
            if candidate['id'] == request.get('selected_id'):
                fact = self.store.db.execute('SELECT data FROM act_facts WHERE request_id=?', (request['id'],)).fetchone()
                if fact:
                    value = json.loads(fact[0])
                    state = 'completed' if value['answer']=='already_done' else 'unknown'
                    # Import the original date, not a fresh validity period at every restart.
                    expires = (datetime.fromisoformat(value['confirmed_at'].replace('Z','+00:00')) + timedelta(hours=24)).isoformat().replace('+00:00','Z')
                    self._observe(idea, state, 'user_confirmation', value['confirmed_at'], expires,
                                  {'request_id': request['id'], 'claim': claim})
                feedback = self.store.db.execute('SELECT data FROM act_feedback WHERE request_id=?', (request['id'],)).fetchone()
                if feedback and json.loads(feedback[0])['decision'] == 'rejected' and not fact:
                    idea['state'] = 'archived'
                    self._save(idea)
        self._bound_pool()

    def _bound_pool(self):
        active=[i for i in self._visible() if i['state'] not in ('completed','archived')]
        active.sort(key=lambda i: (i['state']=='pending',i['created_at']),reverse=True)
        kept={i['id'] for i in active[:10]}
        for idea in active:
            stored=self._idea(idea['id'])
            stored['pool_active']=idea['id'] in kept
            self._save(stored)

    def _verification(self, idea, candidate, request):
        if self.store.db.execute("SELECT 1 FROM idea_observations WHERE idea_id=? AND json_extract(data,'$.source')='agent_observation' AND json_extract(data,'$.evidence.request_id')=?",(idea['id'],request['id'])).fetchone():
            return
        for source in request.get('sources', []):
            if source.get('key') != 'agent_verification':
                continue
            for message in source.get('messages', []):
                try:
                    record = json.loads(message['text'])
                except (ValueError, KeyError):
                    continue
                if (record.get('candidate_id') != candidate['id'] or record.get('project_id') != candidate['project_id']
                        or record.get('completion_claim') != idea['candidate']['completion_claim']):
                    continue
                supported = record.get('basis')=='current_observation' and bool(record.get('evidence')) and any(
                    t.get('type')=='webSearch' or (t.get('status')=='completed' and str(t.get('exitCode'))=='0')
                    for t in record.get('tools',[]))
                state = 'unknown'
                if supported and record.get('status')=='confirmed':
                    state='completed'
                if supported and record.get('status')=='contradicted' and record.get('prerequisites')=='satisfied':
                    state='pending'
                from .store import timestamp
                try:
                    observed=timestamp(record.get('checked_at'))
                except StoreError:
                    continue
                if observed > now():
                    continue
                if idea.get('confirmed_at') and observed < idea['confirmed_at']:
                    continue
                # New evidence may resolve uncertainty, but a late check cannot undo a newer answer.
                if state == 'pending' and idea['state'] == 'unknown':
                    idea['snoozed_until'] = None
                expires=(datetime.fromisoformat(observed.replace('Z','+00:00'))+timedelta(hours=1)).isoformat().replace('+00:00','Z')
                self._observe(idea,state,'agent_observation',observed,expires,
                              {'request_id':request['id'], 'verification':record})

    def _observe(self, idea, state, source, at, expires, evidence):
        oid = str(uuid.uuid4())
        observation = {'id': oid, 'idea_id': idea['id'], 'idea_version': idea['version'], 'state': state,
                       'source': source, 'observed_at': at, 'valid_until': expires, 'evidence': evidence,
                       'supersedes_id': (idea.get('evidence') or {}).get('observation_id')}
        self.store.db.execute('INSERT INTO idea_observations VALUES (?,?,?)', (oid, idea['id'], encode(observation)))
        idea.update(state=state, revision=idea['revision']+1, confirmed_at=at, valid_until=expires,
                    evidence={'observation_id': oid, 'source': source, 'observed_at': at})
        self._save(idea)

    def _visible(self, project_id=None):
        control = self.store._get('control')
        if not control['scope_confirmed']:
            return []
        result = []
        for row in self.store.db.execute('SELECT data FROM ideas ORDER BY rowid DESC'):
            idea = json.loads(row[0])
            project = self.store._project(self.store._resolve(idea['project_id']))
            if project['dismissed'] or (project_id and project['id'] != project_id) or (not project_id and project['activity'] == 'inactive'):
                continue
            if idea['scope_key'] != scope_key(control):
                continue
            idea['project_name'] = project['name']
            idea['actor'] = responsibility(idea,'actor')
            idea['state_owner'] = responsibility(idea,'state_owner')
            idea['effective_state'] = idea['state']
            if idea['state'] == 'pending' and (not idea['valid_until'] or idea['valid_until'] <= now()):
                idea['effective_state'] = 'stale'
            result.append(idea)
        return result

    def state(self):
        with self.store.lock, self.store.db:
            self._expire()
            preparation = self.store.act.work()
            jobs = [json.loads(r[0]) for r in self.store.db.execute('SELECT data FROM idea_jobs ORDER BY rowid DESC LIMIT 20')]
            for job in jobs:
                if job.get('preparation_id'):
                    request=json.loads(self.store.act._row(job['preparation_id'])['data'])
                    if request['status'] in ('ready','failed','cancelled'):
                        from .project_dialogue import report
                        job['research_report']=report(request)
                    if job.get('kind')=='context_question' and not self.store.dialogue.available(request):
                        job.update(kind='none',context_question=None,message='Ta réponse est enregistrée dans le contexte du projet.')
                if job.get('kind')=='question':
                    candidate=job.get('question_candidate',{})
                    current=self._valid_question(candidate,job['context'])
                    if not current or not self._review_current(current,job['context']):
                        job.update(kind='none',questions=[],message='Cette question n’est plus pertinente. Tu peux chercher d’autres pistes.')
                activity = job.get('activity')
                if activity:
                    try:
                        idea = self._idea(job['selected_idea_id'])
                        if not self._eligible(idea, job['context']) or idea['revision'] != job['selected_revision']:
                            job.update(kind='none', activity=None, message='L’état de cette idée a changé. Fais un nouveau choix.')
                    except StoreError:
                        job.update(kind='none', activity=None)
            latest = self.store.db.execute("SELECT data FROM act_requests WHERE json_extract(data,'$.purpose')='prepare_ideas' ORDER BY rowid DESC LIMIT 1").fetchone()
            latest = json.loads(latest[0]) if latest else {}
            active=[i for i in self._visible() if i.get('pool_active',True) and i['state'] not in ('completed','archived')]
            ready=[i for i in active if (i.get('question_review') or {}).get('decision')=='ask_user'
                   and i['question_review'].get('revision')==i['revision'] and i['question_review'].get('valid_until','')>now()
                   and not (i.get('snoozed_until') and i['snoozed_until']>now())]
            return {'ideas': self._visible(), 'selections': jobs,'ready_questions':ready[:5],
                    'question_pool':{'active':len(active),'ready':min(5,len(ready)),'maximum':10,'target':5,
                                     'checking':sum(bool(i.get('verification_due')) for i in active)},
                    'preparation': {'id': preparation['id'], 'status': preparation['status'],
                                    'stage': preparation.get('progress_stage'),
                                    'sources':preparation.get('progress_sources',[])} if preparation else None,
                    'preparation_error': latest.get('error') if latest.get('status') == 'failed' and not preparation else None,
                    'policy': POLICY}

    def _context(self, payload):
        from .act import attention_budget
        instruction = string(payload.get('instruction',''), 'request', empty=True, maximum=4000)
        pid = payload.get('project_id')
        if pid is not None:
            string(pid, 'project id', maximum=128)
        asked = number(payload.get('questions_answered',0), 'questions answered', 0, 2, integer=True)
        return {'instruction': instruction, 'attention_budget': attention_budget(payload),
                'project_id': pid, 'questions_answered': asked}

    def _eligible(self, idea, context):
        if not idea.get('pool_active',True):
            return False
        if responsibility(idea,'actor') != 'user':
            return False
        if idea['state'] != 'pending' or not idea.get('valid_until') or idea['valid_until'] <= now():
            return False
        if idea.get('snoozed_until') and idea['snoozed_until'] > now():
            return False
        if idea['candidate']['expected_attention_minutes'] > context['attention_budget']:
            return False
        return any(i['id'] == idea['id'] for i in self._visible(context.get('project_id')))

    def _route(self, packet):
        packet.pop('context_question_candidate',None)
        packet.pop('context_question',None)
        context = packet['context']
        ideas = self._visible(context['project_id'])
        packet['project_context']=self.store.dialogue.context({i['project_id'] for i in ideas} | ({context['project_id']} if context['project_id'] else set()))
        usable = [i for i in ideas if i['candidate']['expected_attention_minutes'] <= context['attention_budget']
                  and not (i.get('snoozed_until') and i['snoozed_until'] > now())]
        eligible = [i for i in usable if self._eligible(i, context)]
        packet.update(kind='none',status='ready',activity=None,questions=[],eligible=[],decision_kind='activity',
                      message='Je n’ai pas encore de proposition assez étayée pour ce créneau. Choisis un projet ou précise ce qui compte pour orienter la suite.')
        if eligible:
            eligible.sort(key=lambda i: (-i['candidate']['expected_impact'], i['candidate']['expected_attention_minutes']))
            packet.update(kind='choosing',status='queued',decision_started_at=now(),
                          eligible=[{'id':i['id'],'version':i['version'],'revision':i['revision'],
                                     'project':i['project_name'],'candidate':i['candidate']} for i in eligible[:8]])
        else:
            if self._context_route(packet):
                return
            questions = [i for i in usable if i['effective_state'] in ('unknown','stale') and i.get('pool_active',True)
                         and i['actor']=='user' and i['state_owner']=='user'
                         and i['candidate'].get('state_question') and i['candidate'].get('question_reason')]
            approved=[i for i in questions if self._review_current(i,context)
                      and i['question_review']['decision']=='ask_user'][:5]
            if approved and context['questions_answered'] < 2:
                packet.update(kind='question',questions=approved[:1],message='Une précision pour toi.')
                packet['question_candidate']=self._question_snapshot(approved[0])
            elif questions and context['questions_answered'] < 2:
                self._batch(packet,[i for i in questions if not self._review_current(i,context)])

    def _context_route(self, packet):
        if not packet.get('preparation_id'):
            context=packet['context']
            latest=self.store.db.execute("SELECT data FROM act_requests WHERE status='ready' AND json_extract(data,'$.purpose')='prepare_ideas' AND json_extract(data,'$.instruction')=? AND json_extract(data,'$.attention_budget')=? AND json_extract(data,'$.project_scope') IS ? ORDER BY rowid DESC LIMIT 1",
                                        (context['instruction'],context['attention_budget'],context['project_id'])).fetchone()
            if not latest:
                return False
            previous=json.loads(latest[0])
            if previous['generation']!=packet['generation'] or previous['exclusions']!=self.store._get('control')['exclusions']:
                return False
            packet['preparation_id']=previous['id']
        request=json.loads(self.store.act._row(packet['preparation_id'])['data'])
        if request['status']!='ready':
            return False
        from .project_dialogue import report
        packet['research_report']=report(request)
        question=self.store.dialogue.available(request)
        if not question or packet['context']['questions_answered']>=2:
            return False
        review=request.get('context_question_review')
        if review and review.get('decision')!='unavailable':
            if review['decision']=='ask_user':
                packet.update(kind='context_question',context_question=question,
                              message='Une précision sur ce que tu veux pour ce projet.')
                return True
            return False
        packet.update(kind='checking_questions',status='queued',decision_kind='question_batch',
                      decision_started_at=now(),context_question_candidate=question,
                      context_evidence=[{'source':s['key'],'role':m.get('role'),'text':m.get('text','')[:800]}
                          for s in request['sources'] if s['key'] in question['source_refs']
                          for m in s.get('messages',[]) if m.get('role') in ('user','assistant')][-8:],
                      project_context=self.store.dialogue.context({question['project_id']}),
                      message='Je vérifie la pertinence de cette question sur ton projet…')
        return True

    def _context_key(self, context):
        return hashlib.sha256(encode({k:context.get(k) for k in ('instruction','project_id','attention_budget')}).encode()).hexdigest()

    def _review_current(self, idea, context):
        review=idea.get('question_review') or {}
        return (review.get('revision')==idea['revision'] and review.get('version')==idea['version']
                and review.get('decision') in ('ask_user','investigate','skip')
                and review.get('context_key')==self._context_key(context) and review.get('valid_until','')>now())

    def _question_snapshot(self, idea):
        return {'id':idea['id'],'version':idea['version'],'revision':idea['revision'],
                'project':idea['project_name'],'candidate':idea['candidate'],'evidence_excerpt':self._question_evidence(idea)}

    def _batch(self, packet, candidates):
        if not candidates:
            return
        packet.update(kind='checking_questions',status='queued',decision_kind='question_batch',decision_started_at=now(),
                      question_candidates=[self._question_snapshot(i) for i in candidates[:10]],
                      recent_answers=[{'project':i['project_name'],'claim':i['candidate']['completion_claim'],
                                       'answer':i.get('last_answer'),'actor':i['actor'],'state_owner':i['state_owner']}
                                      for i in self._visible() if i.get('last_answer')][-20:],
                      message='Je prépare un petit lot de questions pertinentes…')

    def _question_evidence(self, idea):
        row=self.store.db.execute('SELECT data FROM act_requests WHERE id=?',(idea['origin_request_id'],)).fetchone()
        if not row:
            return []
        sources=json.loads(row[0]).get('sources',[])
        refs=idea['candidate'].get('source_refs',[])
        excerpts=[{'source':s['key'],'role':m.get('role'),'text':m.get('text','')[:600]}
                for s in sources if s['key'] in refs and s['key']!='agent_verification'
                for m in s.get('messages',[]) if m.get('role')=='user'][-4:]
        observation=self.store.db.execute('SELECT data FROM idea_observations WHERE idea_id=? ORDER BY rowid DESC LIMIT 1',(idea['id'],)).fetchone()
        if observation:
            evidence=json.loads(observation[0])['evidence']
            check=evidence.get('verification')
            if check:
                excerpts.append({'source':'current_verification','text':encode({k:check.get(k) for k in ('status','finding','limits')})[:1200]})
        return excerpts

    def _valid_question(self, candidate, context):
        idea=next((i for i in self._visible(context.get('project_id')) if i['id']==candidate.get('id')),None)
        if (idea and idea['version']==candidate.get('version') and idea['revision']==candidate.get('revision')
                and idea.get('pool_active',True) and idea['actor']=='user' and idea['state_owner']=='user'
                and idea['effective_state'] in ('unknown','stale')
                and not (idea.get('snoozed_until') and idea['snoozed_until']>now())
                and idea['candidate']['expected_attention_minutes']<=context['attention_budget']):
            return idea
        return None

    def _recover(self, packet):
        if packet.get('recovery_attempted'):
            return
        packet['recovery_attempted'] = True
        try:
            work = self.store.act.work()
            context = packet['context']
            if work and work.get('purpose')=='verify_idea':
                # A new explicit search takes priority over an optional background check.
                work.update(status='cancelled',error='Vérification interrompue pour traiter la nouvelle demande.')
                self.store.act._save(work)
                work=None
            if work:
                if (work.get('purpose')!='prepare_ideas' or work['instruction']!=context['instruction']
                        or work['attention_budget']!=context['attention_budget'] or work.get('project_scope')!=context['project_id']):
                    packet['message']='Une recherche est déjà en cours pour une autre demande. Tu peux la laisser finir ou l’arrêter avant de réorienter la recherche.'
                    return
            else:
                prepared = self.prepare(context)
                work = {'id':prepared['id']}
            packet.update(kind='preparing',status='preparing',preparation_id=work['id'],
                          message='Je cherche d’autres pistes et vérifie ce que je peux, sans te renvoyer les questions passées.')
            packet.pop('research_report',None)
        except StoreError as error:
            packet['message']=str(error)

    def resolve_preparation(self, request):
        # Resume the same interaction once; never start another investigation from this callback.
        for row in self.store.db.execute("SELECT data FROM idea_jobs WHERE status='preparing'").fetchall():
            packet=json.loads(row[0])
            if packet.get('preparation_id') != request['id']:
                continue
            control=self.store._get('control')
            packet.update(kind='none',status='ready',message='Recherche arrêtée. Tu peux préciser ta demande puis relancer.')
            if (request['status']=='ready' and control['inference_enabled'] and control['scope_confirmed']
                    and packet['scope_key']==scope_key(control)):
                self._route(packet)
            elif request['status']=='failed':
                packet['message']=request.get('error') or 'La recherche a échoué. Tu peux réessayer ou changer de projet.'
            self._job_save(packet)
            self.store.db.execute('UPDATE idea_jobs SET deadline=? WHERE id=?',(self._deadline(packet),packet['id']))
        if request.get('purpose') in ('prepare_ideas','verify_idea') and request['status']=='ready':
            self._prepare_pool(request)

    def _prepare_pool(self, request):
        context=request.get('pool_context') or {'instruction':request['instruction'],
            'project_id':request.get('project_scope'),'attention_budget':request['attention_budget'],'questions_answered':0}
        control=self.store._get('control')
        active=[i for i in self._visible(context['project_id']) if i.get('pool_active',True)
                and i['effective_state'] in ('unknown','stale')]
        for idea in active:
            if (idea['state_owner']!='user' and idea.get('last_answer') not in ('skip','external')
                    and idea.get('verification_attempts',0)<1):
                stored=self._idea(idea['id'])
                stored.update(verification_due=True,pool_context=context)
                self._save(stored)
        waiting=self.store.db.execute("SELECT 1 FROM idea_jobs WHERE status IN ('queued','running') AND json_extract(data,'$.decision_kind')='question_batch'").fetchone()
        if waiting:
            return
        candidates=[i for i in active if i['actor']=='user' and i['state_owner']=='user'
                    and i['candidate'].get('state_question') and i['candidate'].get('question_reason')
                    and not self._review_current(i,context)
                    and not (i.get('snoozed_until') and i['snoozed_until']>now())]
        if candidates:
            packet={'id':str(uuid.uuid4()),'created_at':now(),'context':context,'scope_key':scope_key(control),
                    'generation':control['generation'],'pool_only':True,'recovery_attempted':True,'recover':False,
                    'activity':None,'questions':[],'eligible':[],'elapsed_seconds':None}
            self._batch(packet,candidates)
            self.store.db.execute('INSERT INTO idea_jobs VALUES (?,?,?,?)',(packet['id'],packet['status'],self._deadline(packet),encode(packet)))

    def check_request(self, payload):
        with self.store.lock, self.store.db:
            control=self.store._get('control')
            if not control['scope_confirmed'] or not control['inference_enabled'] or control['running'] or self.store.act.work():
                return None
            due=[i for i in self._visible() if i.get('pool_active',True) and i.get('verification_due')
                 and i.get('verification_attempts',0)<1 and i['effective_state'] in ('unknown','stale')]
            if not due:
                return None
            idea=due[0];context=idea['pool_context']
            work=self.store.act.request(dict(context,project_id=idea['project_id']))
            work.update(purpose='verify_idea',target_idea_id=idea['id'],target_revision=idea['revision'],
                        target_candidate=idea['candidate'],pool_context=context)
            stored=self._idea(idea['id']);stored.update(verification_due=False,verification_attempts=1)
            self._save(stored);self.store.act._save(work)
            return work

    def cancel_checks(self):
        for idea in self._visible():
            if idea.get('verification_due'):
                stored=self._idea(idea['id']);stored['verification_due']=False;self._save(stored)

    def choose(self, payload):
        require_object(payload)
        with self.store.lock, self.store.db:
            control = self._guard()
            self._expire()
            context = self._context(payload)
            recovery_attempted=False
            if payload.get('continuation_id'):
                row=self.store.db.execute('SELECT data FROM idea_jobs WHERE id=?',(payload['continuation_id'],)).fetchone()
                previous=json.loads(row[0]) if row else {}
                if all(previous.get('context',{}).get(k)==context[k] for k in ('instruction','project_id','attention_budget')):
                    recovery_attempted=previous.get('recovery_attempted',False)
            packet = {'id': str(uuid.uuid4()), 'kind': 'none', 'status': 'ready', 'message': '', 'created_at': now(),
                      'context': context, 'generation': control['generation'], 'scope_key': scope_key(control),
                      'activity': None, 'questions': [], 'eligible': [], 'elapsed_seconds': None,
                      'recovery_attempted':recovery_attempted,'recover':payload.get('recover') is True}
            self._route(packet)
            if packet['status']=='queued':
                prior = self.store.db.execute("SELECT data FROM idea_jobs WHERE status IN ('queued','running')").fetchone()
                if prior:
                    old = json.loads(prior[0]); old.update(status='cancelled',kind='none',message='Choix remplacé par une nouvelle demande.')
                    self._job_save(old)
            if packet['kind']=='none' and payload.get('recover') is True:
                self._recover(packet)
            self.store.db.execute('INSERT INTO idea_jobs VALUES (?,?,?,?)',
                                  (packet['id'],packet['status'],self._deadline(packet),encode(packet)))
            return packet

    def answer(self, payload):
        with self.store.lock, self.store.db:
            self._guard()
            idea = self._idea(payload.get('id'))
            if idea['revision'] != payload.get('revision') or idea['version'] != payload.get('version'):
                raise StoreError('Cette idée a changé ; actualise avant de répondre.', 409)
            if not any(i['id']==idea['id'] for i in self._visible(payload.get('project_id'))):
                raise StoreError('Cette idée est hors périmètre.', 409)
            answer = payload.get('answer')
            if answer not in ('todo','already_done','unknown','blocked','skip','agent','external'):
                raise StoreError('Invalid state answer.')
            if idea['state'] == 'completed' and answer != 'already_done':
                raise StoreError('Une activité terminée doit être redéfinie avant réouverture.', 409)
            if answer != 'skip':
                state = {'todo':'pending','already_done':'completed','blocked':'blocked','unknown':'unknown',
                         'agent':'unknown','external':'unknown'}[answer]
                self._observe(idea,state,'user_confirmation',now(),later(),{'claim':idea['candidate']['completion_claim'],
                              'answer':answer,'note':string(payload.get('note',''),'note',empty=True,maximum=2000),
                              'prerequisites_confirmed':answer=='todo'})
            else:
                idea['revision'] += 1
            if answer in ('agent','external','todo'):
                idea['state_owner_override'] = 'user' if answer=='todo' else answer
                if answer=='todo':
                    idea['actor_override']='user'
            idea['last_answer'] = answer
            idea['snoozed_until'] = later() if answer in ('unknown','blocked','skip','agent','external') else None
            self._save(idea)
            return idea

    def prepare(self, payload):
        with self.store.lock, self.store.db:
            self._guard()
            work = self.store.act.request(payload)
            work.update(purpose='prepare_ideas',dialogue_version=1, prepared_ideas=[{'id':i['id'], 'claim':i['candidate']['completion_claim'],
                         'action':i['candidate']['action'],'state':i['effective_state'],'actor':i['actor'],
                         'state_owner':i['state_owner'],'last_answer':i.get('last_answer'),
                         'snoozed_until':i.get('snoozed_until')} for i in self._visible(payload.get('project_id'))][:60])
            if payload.get('resume_from'):
                previous=json.loads(self.store.act._row(payload['resume_from'])['data'])
                if (previous['status']!='ready' or previous['generation']!=work['generation']
                        or previous['exclusions']!=work['exclusions']):
                    raise StoreError('Le périmètre de la recherche a changé.',409)
                allowed={c['session_id'] for c in work['catalog']}
                sources=[s for s in previous['sources'] if s['key'] in allowed or s['key']=='agent_verification']
                # Snapshots keep their original dates; intent never refreshes completion evidence.
                work['research_seed']={'request_id':previous['id'],'sources':sources}
            self.store.act._save(work)
            return {'id':work['id'], 'status':work['status']}

    def context_answer(self, payload):
        with self.store.lock, self.store.db:
            control=self._guard()
            row=self.store.db.execute('SELECT data FROM idea_jobs WHERE id=?',(payload.get('id'),)).fetchone()
            packet=json.loads(row[0]) if row else {}
            if packet.get('kind')!='context_question' or packet.get('scope_key')!=scope_key(control):
                raise StoreError('Cette question n’est plus disponible.',409)
            request=json.loads(self.store.act._row(packet['preparation_id'])['data'])
            answer=self.store.dialogue.answer(request,payload)
            packet.update(kind='none',status='ready',context_question=None,context_answer=answer,
                          message='Réponse enregistrée dans le contexte du projet.')
            packet.pop('context_question_candidate',None)
            packet['context']['questions_answered']=min(2,packet['context']['questions_answered']+1)
            if answer['response']=='answer':
                work=self.store.act.work()
                if work and work.get('purpose')=='verify_idea':
                    work.update(status='cancelled',error='Priorité à la réponse sur le projet.')
                    self.store.act._save(work);work=None
                if not work:
                    continuation=self.prepare(dict(packet['context'],project_id=answer['project_id'],resume_from=request['id']))
                    packet.update(kind='preparing',status='preparing',preparation_id=continuation['id'],
                                  message='Réponse enregistrée. Je reprends les éléments déjà consultés avec cette précision.')
                else:
                    packet['message']='Réponse enregistrée. La recherche déjà en cours utilise son contexte initial ; ta précision servira à la suivante.'
            else:
                packet['message']='Je garde ce point ouvert sans te reposer la même question. Tu peux choisir un autre projet ou préciser ta demande.'
            self._job_save(packet)
            return packet

    def _deadline(self, job):
        from .decisions import CHOICE_TIMEOUT, QUESTION_TIMEOUT
        timeout=QUESTION_TIMEOUT if job.get('decision_kind')=='question_batch' else CHOICE_TIMEOUT
        return time.time()+2*timeout+2

    def _job_save(self, job):
        self.store.db.execute('UPDATE idea_jobs SET status=?,data=? WHERE id=?',(job['status'],encode(job),job['id']))

    def _expire(self):
        for row in self.store.db.execute("SELECT data FROM idea_jobs WHERE status IN ('queued','running') AND deadline < ?", (time.time(),)).fetchall():
            job=json.loads(row[0])
            job.update(status='failed',kind='none',message='La décision a pris trop de temps. Ta demande et tes réponses sont conservées.',
                       activity=None,failure={'code':'timeout'})
            self._job_save(job)

    def work(self, kind=None):
        with self.store.lock, self.store.db:
            self._expire()
            for row in self.store.db.execute("SELECT data FROM idea_jobs WHERE status='queued' ORDER BY rowid"):
                job=json.loads(row[0])
                if kind is None or job.get('decision_kind','activity')==kind:
                    return job
            return None

    def claim(self, payload):
        with self.store.lock, self.store.db:
            self._guard();self._expire()
            row=self.store.db.execute('SELECT data FROM idea_jobs WHERE id=?',(payload.get('id'),)).fetchone()
            if not row or json.loads(row[0])['status']!='queued':
                raise StoreError('Choix expiré ou déjà pris en charge.',409)
            job=json.loads(row[0])
            if job['scope_key']!=scope_key(self._guard()):
                raise StoreError('Périmètre modifié.',409)
            job['status']='running';self._job_save(job)
            return job

    def finish(self, payload):
        with self.store.lock, self.store.db:
            self._expire()
            row=self.store.db.execute('SELECT data FROM idea_jobs WHERE id=?',(payload.get('id'),)).fetchone()
            if not row or json.loads(row[0])['status']!='running':
                raise StoreError('Choix expiré ou remplacé.',409)
            job=json.loads(row[0]);control=self._guard()
            if job['scope_key']!=scope_key(control):
                raise StoreError('Périmètre modifié.',409)
            job.update(status='ready',kind='none',message='Aucune idée ne correspond assez bien à ta demande.',
                       elapsed_seconds=round(time.time()-datetime.fromisoformat(job.get('decision_started_at',job['created_at']).replace('Z','+00:00')).timestamp(),3))
            decision=payload.get('decision')
            if not decision:
                from .decisions import FAILURE_CODES
                details=payload.get('failure') or {}
                code=details.get('code') if isinstance(details,dict) else None
                failure={'code':code if isinstance(code,str) and code in FAILURE_CODES else 'worker_error'}
                if isinstance(details,dict) and type(details.get('http_status')) is int and 400<=details['http_status']<=599:
                    failure['http_status']=details['http_status']
                message=('Le service de décision a mis trop de temps à répondre.' if failure['code']=='timeout'
                         else 'Le service de décision n’a pas pu terminer le choix.')
                job.update(status='failed',kind='none',activity=None,questions=[],failure=failure,
                           message=message+' Ta demande et tes réponses sont conservées ; tu peux réessayer.')
                self._job_save(job)
                return job
            answers={}
            if decision:
                from .decisions import validate
                from .idea_worker import questions
                answers=validate(decision,questions(job))
                job['decision']=decision
            if job.get('decision_kind')=='question_batch':
                if job.get('context_question_candidate'):
                    request=json.loads(self.store.act._row(job['preparation_id'])['data'])
                    answer=answers.get('project_intent',{})
                    choice=answer.get('choice') if answer.get('type')=='choice' else 'unavailable'
                    request['context_question_review']={'decision':choice,'at':now(),'job_id':job['id']}
                    self.store.db.execute('UPDATE act_requests SET data=? WHERE id=?',(encode(request),request['id']))
                    if choice=='ask_user' and self.store.dialogue.available(request):
                        job.update(kind='context_question',context_question=self.store.dialogue.available(request),
                                   message='Une précision sur ce que tu veux pour ce projet.')
                    else:
                        job.update(kind='none',message='Voici ce que la recherche a établi. Tu peux préciser ton objectif pour orienter la suite.')
                    self._job_save(job)
                    return job
                for candidate in job['question_candidates']:
                    answer=answers.get('audience_'+candidate['id'],{})
                    idea=self._valid_question(candidate,job['context'])
                    if not idea:
                        continue
                    choice=answer.get('choice','unavailable') if answer.get('type')=='choice' else 'unavailable'
                    stored=self._idea(idea['id'])
                    stored['question_review']={'job_id':job['id'],'decision':choice,'at':now(),'valid_until':later(hours=1),
                                              'context_key':self._context_key(job['context']),'context':job['context'],
                                              'version':idea['version'],'revision':idea['revision']}
                    if choice=='investigate' and stored.get('verification_attempts',0)<1:
                        stored.update(verification_due=True,pool_context=job['context'])
                    self._save(stored)
                self._route(job)
                if job['kind']=='none' and job.get('recover'):
                    self._recover(job)
                if job.get('pool_only'):
                    job.update(kind='none',status='ready',questions=[],eligible=[],message='Questions préparées en arrière-plan.')
                self._job_save(job)
                self.store.db.execute('UPDATE idea_jobs SET deadline=? WHERE id=?',(self._deadline(job),job['id']))
                return job
            if decision:
                answer=answers['next_idea']
                iid=answer.get('choice') if answer.get('type')=='choice' else None
                admitted=next((i for i in job['eligible'] if i['id']==iid),None)
                if admitted:
                    idea=self._idea(iid)
                    if idea['revision']==admitted['revision'] and idea['version']==admitted['version'] and self._eligible(idea,job['context']):
                        activity=self._activity(job,idea)
                        job.update(kind='activity',activity=activity,selected_idea_id=iid,selected_revision=idea['revision'],message='')
            else:
                job['message']='Decisions API est indisponible. Tes idées sont conservées ; réessaie.'
            self._job_save(job)
            return job

    def _activity(self, job, idea):
        rid=str(uuid.uuid4());candidate=dict(idea['candidate'],id=idea['id'],needs_confirmation=False)
        data={'id':rid,'created_at':now(),'status':'ready','generation':job['generation'],'policy':POLICY,
              'instruction':job['context']['instruction'],'attention_budget':job['context']['attention_budget'],
              'candidates':[candidate],'selected_id':idea['id'],'rationale':candidate['why_now'],
              'uncertainty':'Reste à faire et réalisable · '+('ta confirmation' if idea['evidence']['source']=='user_confirmation' else 'observation de l’agent')+' du '+str(idea['confirmed_at']),
              'sources':[],'model':'gpt-6-luna','elapsed_seconds':job['elapsed_seconds'],'idea_id':idea['id'],
              'idea_version':idea['version'],'state_revision':idea['revision'],'evidence':idea['evidence'],'origin_request_id':idea['origin_request_id'],
              'decision':job.get('decision'),'error':None,'feedback':None,'confirmation':None}
        self.store.db.execute('INSERT INTO act_requests VALUES (?,?,?,?,?,?)',
                             (rid,job['generation'],'ready',data['created_at'],None,encode(data)))
        return data

    def assessed(self, request, feedback):
        iid=request.get('idea_id')
        if not iid:
            return
        idea=self._idea(iid)
        if feedback['decision']=='done':
            self._observe(idea,'completed','user_assessment',now(),None,{'request_id':request['id']})
        else:
            idea.update(snoozed_until=later(),revision=idea['revision']+1)
            self._save(idea)
