"""Research restitution and dated user intent, separate from task completion."""
import json
import uuid
import hashlib

from .store import StoreError, encode, now, string

DIMENSIONS = ('goal', 'motivation', 'priority', 'constraint', 'success_criterion')
PROFILE_DIMENSIONS = (*DIMENSIONS, 'situation')


def validate_research(value, work, sources):
    if not isinstance(value, dict):
        raise StoreError('Missing research restitution.')
    string(value.get('summary'), 'research summary', maximum=4000)
    gaps = value.get('unresolved')
    if not isinstance(gaps, list) or len(gaps) > 3:
        raise StoreError('Invalid research uncertainties.')
    for gap in gaps:
        string(gap, 'uncertainty', maximum=1500)
    question = value.get('question')
    if question is not None:
        if not isinstance(question, dict) or question.get('dimension') not in DIMENSIONS:
            raise StoreError('Invalid project question.')
        project = next((p for p in work['projects'] if p['id'] == question.get('project_id')), None)
        if not project:
            raise StoreError('Question outside project scope.')
        for key in ('prompt', 'why', 'changes_decision'):
            string(question.get(key), key, maximum=2000)
        refs = question.get('source_refs')
        allowed = {s['key'] for s in sources if s.get('messages') and s['key'] in project['session_ids']}
        if not isinstance(refs, list) or not refs or any(r not in allowed for r in refs):
            raise StoreError('Project question cites unread or unrelated evidence.')
        options = question.get('options')
        if not isinstance(options, list) or len(options) > 3:
            raise StoreError('Invalid question options.')
        for option in options:
            string(option, 'option', maximum=400)
    return value


def report(request):
    research = request.get('research') or {}
    checks=[]
    for source in request.get('sources',[]):
        if source['key']!='agent_verification':
            continue
        for message in source.get('messages',[]):
            try:
                finding=json.loads(message['text']).get('finding')
                if isinstance(finding,str) and finding:
                    checks.append(finding)
            except (ValueError,KeyError,AttributeError):
                continue
    return {'request_id': request['id'], 'summary': research.get('summary') or request.get('rationale') or request.get('error') or 'La recherche n’a pas encore produit de conclusion.',
            'unresolved': research.get('unresolved') or ([request['uncertainty']] if request.get('uncertainty') else []),
            'sources': [{'key': s['key'], 'title': s.get('title', s['key']),
                         'read': bool(s.get('messages')) or s.get('read') is True, 'modified_at': s.get('modified_at')}
                        for s in (request.get('sources') or request.get('progress_sources',[])) if s['key'] != 'agent_verification'],
            'checks': checks,
            'elapsed_seconds': request.get('elapsed_seconds')}


class ProjectDialogue:
    def __init__(self, store):
        self.store = store
        store.db.execute('CREATE TABLE IF NOT EXISTS project_context_answers '
                         '(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, request_id TEXT UNIQUE NOT NULL, data TEXT NOT NULL)')

    def context(self, project_ids):
        control = self.store._get('control')
        result, seen = [], set()
        for row in self.store.db.execute('SELECT project_id,data FROM project_context_answers ORDER BY rowid DESC'):
            item = json.loads(row['data'])
            if item['generation'] != control['generation'] or item['exclusions'] != control['exclusions']:
                continue
            pid = self.store._resolve(row['project_id'])['id']
            key = (pid, item['dimension'])
            if pid in project_ids and key not in seen:
                seen.add(key)
                result.append(dict(item, project_id=pid))
        return result

    def profile(self, project_id):
        with self.store.lock:
            project=self.store._project(self.store._resolve(project_id))
            if project['dismissed']:
                raise StoreError('Ce projet n’est plus disponible.',409)
            pid=project['id'];control=self.store._get('control')
            context=self.context({pid})
            revision=hashlib.sha256(encode([control['generation'],control['exclusions'],[c['id'] for c in context]]).encode()).hexdigest()
            fields={key:next((c for c in context if c['dimension']==key),None) for key in PROFILE_DIMENSIONS}
            ideas=[{'id':i['id'],'version':i['version'],'revision':i['revision'],'candidate':i['candidate'],
                    'state':i['effective_state'],'actor':i['actor'],'state_owner':i['state_owner'],
                    'evidence':i.get('evidence'),'confirmed_at':i.get('confirmed_at'),'valid_until':i.get('valid_until'),
                    'last_answer':i.get('last_answer')} for i in self.store.ideas._visible(pid)]
            for idea in ideas:
                evidence=idea.get('evidence') or {}
                observation=self.store.db.execute('SELECT data FROM idea_observations WHERE id=?',(evidence.get('observation_id'),)).fetchone()
                if observation:
                    idea['observation']=json.loads(observation[0])
            sessions=self.store.session_metadata(project['session_ids'])
            research=[];feedback=[]
            for row in self.store.db.execute('SELECT data FROM act_requests ORDER BY rowid DESC'):
                request=json.loads(row[0])
                if request.get('generation')!=control['generation']:
                    continue
                if request.get('project_scope')==pid and request.get('research') and len(research)<3:
                    research.append(dict(report(request),created_at=request['created_at']))
                chosen=next((c for c in request.get('candidates',[]) if c['id']==request.get('selected_id') and c['project_id']==pid),None)
                if chosen and len(feedback)<10:
                    saved=self.store.db.execute('SELECT data FROM act_feedback WHERE request_id=?',(request['id'],)).fetchone()
                    if saved:
                        feedback.append({'title':chosen['title'],'feedback':json.loads(saved[0])})
            profile = {'project':project,'revision':revision,'fields':fields,'ideas':ideas,
                       'sources':sessions,'research':research,'feedback':feedback}
            remote = self.store.ingestion.coverage(pid)
            if remote['source_locations'] or remote['excluded_internal_sources']:
                profile['evidence_coverage'] = {'legacy_unassigned_sources': sum(not s.get('cached') for s in sessions),
                                               'projected': remote}
            authority = self.store.project_authority(pid)
            if authority:
                profile['authority'] = authority
            return profile

    def update_profile(self, payload):
        with self.store.lock, self.store.db:
            self.store.require_project_writable(payload.get('project_id'))
            current=self.profile(payload.get('project_id'))
            if payload.get('revision')!=current['revision']:
                raise StoreError('La fiche a changé. Recharge-la avant d’enregistrer tes modifications.',409)
            values=payload.get('fields')
            if not isinstance(values,dict) or not values or set(values)-set(PROFILE_DIMENSIONS):
                raise StoreError('Invalid project context fields.')
            values={key:string(value,key,empty=True,maximum=4000).strip() for key,value in values.items()}
            pid=current['project']['id'];control=self.store._get('control');changed=False
            for key,value in values.items():
                previous=current['fields'][key]
                if (previous and previous.get('answer')==value and previous.get('response') in ('answer','cleared')) or (not previous and not value):
                    continue
                item={'id':str(uuid.uuid4()),'project_id':pid,'request_id':'profile:'+str(uuid.uuid4()),
                      'dimension':key,'question':'Direct project profile edit','answer':value,
                      'response':'answer' if value else 'cleared','source':'user_project_profile','answered_at':now(),
                      'supersedes_id':previous['id'] if previous else None,
                      'generation':control['generation'],'exclusions':control['exclusions']}
                self.store.db.execute('INSERT INTO project_context_answers VALUES (?,?,?,?)',
                                     (item['id'],pid,item['request_id'],encode(item)))
                changed=True
            if changed:
                # Reject late choices made against the old goals, without changing task facts.
                for row in self.store.db.execute('SELECT data FROM idea_jobs'):
                    job=json.loads(row[0])
                    if job.get('generation')==control['generation'] and job.get('context',{}).get('project_id') in (None,pid) and job['status'] in ('queued','running','preparing','ready'):
                        job.update(status='cancelled',kind='none',activity=None,questions=[],
                                   message='Les objectifs du projet ont changé. Demande une proposition avec ce nouveau contexte.')
                        job.pop('context_question',None)
                        self.store.ideas._job_save(job)
                for idea in self.store.ideas._visible(pid):
                    stored=self.store.ideas._idea(idea['id'])
                    if stored.pop('question_review',None):
                        self.store.ideas._save(stored)
            return self.profile(pid)

    def available(self, request):
        question = (request.get('research') or {}).get('question')
        if not question or self.store.db.execute('SELECT 1 FROM project_context_answers WHERE request_id=?', (request['id'],)).fetchone():
            return None
        project = self.store._project(self.store._resolve(question['project_id']))
        if project['dismissed']:
            return None
        if any(c['dimension'] == question['dimension'] and c['answered_at'] > request['created_at']
               for c in self.context({project['id']})):
            return None
        return dict(question, request_id=request['id'], project_name=project['name'])

    def answer(self, request, payload):
        question = self.available(request)
        if not question:
            raise StoreError('Cette question a déjà reçu une réponse ou son contexte a changé.', 409)
        self.store.require_project_writable(question['project_id'])
        response = payload.get('response', 'answer')
        if response not in ('answer', 'unknown', 'skip'):
            raise StoreError('Invalid context response.')
        answer = string(payload.get('answer', ''), 'answer', empty=response != 'answer', maximum=4000)
        if response != 'answer':
            answer = ''
        previous = next((c for c in self.context({question['project_id']}) if c['dimension'] == question['dimension']), None)
        control = self.store._get('control')
        item = {'id': str(uuid.uuid4()), 'project_id': question['project_id'], 'request_id': request['id'],
                'dimension': question['dimension'], 'question': question['prompt'], 'answer': answer,
                'response': response, 'source': 'user_project_context', 'answered_at': now(),
                'supersedes_id': previous['id'] if previous else None,
                'generation': control['generation'], 'exclusions': control['exclusions']}
        self.store.db.execute('INSERT INTO project_context_answers VALUES (?,?,?,?)',
                             (item['id'], item['project_id'], request['id'], encode(item)))
        return item
