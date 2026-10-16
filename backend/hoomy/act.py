"""On-demand activity decisions; inputs and predictions remain reproducible."""
import json
import math
import re
import time
import uuid
from .store import StoreError, encode, now, require_object, string

POLICY = 'act-learn-v2'
LEASE_SECONDS = 900


def number(value, label, minimum=0, maximum=1440, integer=False):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or not minimum <= value <= maximum or (integer and type(value) is not int)):
        raise StoreError('Invalid ' + label + '.')
    return value


def attention_budget(payload):
    minutes = number(payload.get('attention_budget',30), 'attention budget', 1, 480, integer=True)
    instruction = string(payload.get('instruction',''), 'request', empty=True, maximum=4000)
    explicit = re.search(r'(?<![\d.,-])(\d+)\s*(minutes?|mins?|heures?|h)(?!\w)', instruction, re.I)
    if explicit:
        minutes = number(int(explicit[1]) * (60 if explicit[2].lower().startswith('h') else 1),
                         'durée dans la demande (1–480 minutes)',1,480,integer=True)
    return minutes


class Act:
    def __init__(self, store):
        self.store = store
        store.db.executescript('''
            CREATE TABLE IF NOT EXISTS act_requests (
                id TEXT PRIMARY KEY, generation TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, started_at REAL, data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS act_facts (
                request_id TEXT PRIMARY KEY REFERENCES act_requests(id), project_id TEXT NOT NULL, data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS act_feedback (
                request_id TEXT PRIMARY KEY REFERENCES act_requests(id), data TEXT NOT NULL
            );
        ''')

    def _expire(self):
        for row in self.store.db.execute("SELECT * FROM act_requests WHERE status='running' AND started_at < ?", (time.time()-LEASE_SECONDS,)).fetchall():
            data = json.loads(row['data'])
            data.update(status='failed', error='La recherche a été interrompue. Demande une nouvelle suggestion.')
            self._save(data)

    def _save(self, data):
        self.store.db.execute('UPDATE act_requests SET status=?,data=? WHERE id=?',
                              (data['status'], encode(data), data['id']))
        if data['status'] in ('ready','failed','cancelled') and hasattr(self.store,'ideas'):
            self.store.ideas.resolve_preparation(data)

    def _row(self, rid):
        row = self.store.db.execute('SELECT * FROM act_requests WHERE id=?', (rid,)).fetchone()
        if not row:
            raise StoreError('Suggestion introuvable.', 404)
        return row

    def state(self):
        with self.store.lock, self.store.db:
            self._expire()
            requests = []
            for row in self.store.db.execute('SELECT data FROM act_requests ORDER BY rowid DESC LIMIT 50'):
                data = json.loads(row[0])
                feedback = self.store.db.execute('SELECT data FROM act_feedback WHERE request_id=?', (data['id'],)).fetchone()
                data['feedback'] = json.loads(feedback[0]) if feedback else None
                fact = self.store.db.execute('SELECT data FROM act_facts WHERE request_id=?', (data['id'],)).fetchone()
                data['confirmation'] = json.loads(fact[0]) if fact else None
                if data.get('purpose')=='prepare_ideas' and data['status']=='ready':
                    from .project_dialogue import report
                    data['research_report']=report(data)
                requests.append(data)
            samples = []
            for row in self.store.db.execute('SELECT r.data,f.data FROM act_feedback f JOIN act_requests r ON r.id=f.request_id'):
                request, feedback = json.loads(row[0]), json.loads(row[1])
                selected = next((c for c in request.get('candidates', []) if c['id'] == request.get('selected_id')), None)
                if selected and feedback['decision'] == 'done':
                    samples.append((selected, feedback))
            n = len(samples)
            def mean(fn):
                return round(sum(fn(c, f) for c, f in samples)/n, 2) if n else None
            return {'requests': requests, 'metrics': {'completed': n,
                'impact': mean(lambda c,f: f['impact']), 'attention': mean(lambda c,f: f['attention_minutes']),
                'impact_error': mean(lambda c,f: abs(f['impact']-c['expected_impact'])),
                'attention_error': mean(lambda c,f: abs(f['attention_minutes']-c['expected_attention_minutes']))}}

    def request(self, payload):
        require_object(payload)
        with self.store.lock, self.store.db:
            self._expire()
            control = self.store._get('control')
            if not control['scope_confirmed'] or not control['inference_enabled']:
                raise StoreError('Confirme le périmètre et le traitement Codex dans Confidentialité.', 409)
            if control['running']:
                raise StoreError('Mets Discover en pause avant de demander une activité.', 409)
            if self.store.db.execute("SELECT 1 FROM act_requests WHERE status IN ('queued','running')").fetchone():
                raise StoreError('Une suggestion est déjà en préparation.', 409)
            projects = [self.store._project(r) for r in self.store.db.execute('SELECT * FROM projects WHERE merged_into IS NULL')]
            pid = payload.get('project_id')
            if pid is not None and not isinstance(pid, str):
                raise StoreError('Invalid project.')
            projects = [p for p in projects if not p['dismissed'] and (p['id'] == pid if pid else p['activity'] != 'inactive')]
            if not projects:
                raise StoreError('Choisis au moins un projet reconnu.', 409)
            minutes = attention_budget(payload)
            instruction = string(payload.get('instruction', ''), 'request', empty=True, maximum=4000)
            ids = {sid for p in projects for sid in p['session_ids']}
            catalog = [json.loads(r[0]) for r in self.store.db.execute('SELECT metadata FROM catalog')]
            catalog = sorted([c for c in catalog if c['session_id'] in ids], key=lambda c:c['modified_at'], reverse=True)
            # Preserve the exact bounded selection and feedback available at decision time.
            history = []
            for row in self.store.db.execute('SELECT r.data,f.data FROM act_requests r JOIN act_feedback f ON f.request_id=r.id ORDER BY r.rowid DESC LIMIT 30'):
                previous, feedback = json.loads(row[0]), json.loads(row[1])
                chosen = next((c for c in previous.get('candidates', []) if c['id']==previous.get('selected_id')), None)
                if chosen:
                    history.append({'activity': chosen, 'feedback': feedback})
            data = {'id':str(uuid.uuid4()), 'created_at':now(), 'status':'queued', 'policy':POLICY,
                    'instruction':instruction, 'attention_budget':minutes, 'project_scope':pid, 'projects':projects,
                    'followup_depth':0, 'facts':self.facts({p['id'] for p in projects}),
                    'project_context':self.store.dialogue.context({p['id'] for p in projects}),
                    'research_feedback':self.store.explorer.context({p['id'] for p in projects}),
                    'catalog':catalog[:160], 'catalog_total':len(catalog), 'history':history,
                    'exclusions':control['exclusions'], 'generation':control['generation'],
                    'sources':[], 'candidates':[], 'selected_id':None,
                    'rationale':'', 'error':None, 'model':None}
            self.store.db.execute('INSERT INTO act_requests VALUES (?,?,?,?,?,?)',
                (data['id'], control['generation'], 'queued', data['created_at'], None, encode(data)))
            return data

    def work(self):
        with self.store.lock, self.store.db:
            self._expire()
            row = self.store.db.execute("SELECT data FROM act_requests WHERE status IN ('queued','running') ORDER BY rowid LIMIT 1").fetchone()
            return json.loads(row[0]) if row else None

    def claim(self, payload):
        with self.store.lock, self.store.db:
            self.store._generation(payload)
            row = self._row(payload.get('id'))
            if row['status'] != 'queued':
                raise StoreError('La demande est déjà traitée.', 409)
            data = json.loads(row['data'])
            data['status'] = 'running'
            self._save(data)
            self.store.db.execute('UPDATE act_requests SET started_at=? WHERE id=?', (time.time(),data['id']))
            return data

    def cancel(self, payload):
        with self.store.lock, self.store.db:
            row = self._row(payload.get('id'))
            data = json.loads(row['data'])
            if row['status'] in ('queued','running'):
                data.update(status='cancelled', error=None)
                self._save(data)
                self.store.ideas.cancel_checks()
            return data

    def progress(self, payload):
        with self.store.lock, self.store.db:
            self.store._generation(payload)
            data=json.loads(self._row(payload.get('id'))['data'])
            if data['status']!='running':
                raise StoreError('Recherche arrêtée.',409)
            stage=payload.get('stage')
            if stage not in ('retrieve','read','orient','suggest','verify','reconsider'):
                raise StoreError('Invalid preparation stage.')
            data.update(progress_stage=stage,progress_at=now())
            if 'sources' in payload:
                sources=payload['sources']
                allowed={c['session_id']:c for c in data['catalog']}
                if not isinstance(sources,list) or len(sources)>8 or any(s.get('key') not in allowed for s in sources):
                    raise StoreError('Invalid progress sources.')
                data['progress_sources']=[{'key':s['key'],'title':allowed[s['key']]['title'],
                                           'read':s.get('read') is True} for s in sources]
            self._save(data)
            return {'id':data['id'],'stage':stage}

    def finish(self, payload, failed=False):
        with self.store.lock, self.store.db:
            self.store._generation(payload)
            row = self._row(payload.get('id'))
            if row['status'] != 'running':
                raise StoreError('La demande a été arrêtée.', 409)
            data = json.loads(row['data'])
            control = self.store._get('control')
            if not control['scope_confirmed'] or not control['inference_enabled'] or control['exclusions'] != data['exclusions']:
                data.update(status='cancelled', error='Le périmètre a changé.')
                self._save(data)
                return data
            if failed:
                data.update(status='failed',error=string(payload.get('error'), 'error', maximum=2000))
                data['failure_code']=string(payload.get('failure_code','unknown'),'failure code',maximum=100)
            else:
                from .act_inference import validate
                result = validate(payload['result'], data, payload['result'].get('sources', []))
                data.update(result, status='ready', finished_at=now())
                self.store.ideas.ingest(data)
            self._save(data)
            return data

    def feedback(self, payload):
        with self.store.lock, self.store.db:
            row = self._row(payload.get('id'))
            data = json.loads(row['data'])
            if row['status'] != 'ready' or not data['selected_id']:
                raise StoreError('Aucune activité proposée à évaluer.', 409)
            decision = payload.get('decision')
            if decision not in ('done','deferred','rejected'):
                raise StoreError('Invalid decision.')
            feedback = {'decision':decision, 'impact':None, 'attention_minutes':None,
                        'reason':string(payload.get('reason',''), 'reason', empty=True, maximum=4000), 'updated_at':now()}
            if decision == 'done':
                feedback.update(impact=number(payload.get('impact'),'impact',0,5,integer=True),
                                attention_minutes=number(payload.get('attention_minutes'),'attention'))
            self.store.db.execute('INSERT OR REPLACE INTO act_feedback VALUES (?,?)',(data['id'],encode(feedback)))
            candidate=next(c for c in data['candidates'] if c['id']==data['selected_id'])
            if decision=='done':
                fact={'claim':candidate.get('completion_claim') or 'Activité déjà réalisée : '+candidate['title'],
                      'answer':'already_done','project_id':candidate['project_id'],'source':'user_assessment',
                      'confirmed_at':now(),'request_id':data['id']}
                self.store.db.execute('INSERT OR REPLACE INTO act_facts VALUES (?,?,?)',
                                     (data['id'],candidate['project_id'],encode(fact)))
            else:
                previous=self.store.db.execute('SELECT data FROM act_facts WHERE request_id=?',(data['id'],)).fetchone()
                if previous and json.loads(previous[0]).get('source')=='user_assessment':
                    self.store.db.execute('DELETE FROM act_facts WHERE request_id=?',(data['id'],))
            self.store.ideas.assessed(data, feedback)
            return feedback


    def facts(self, project_ids):
        facts=[]
        for row in self.store.db.execute('SELECT project_id,data FROM act_facts ORDER BY rowid'):
            resolved=self.store._resolve(row['project_id'])['id']
            if resolved in project_ids:
                facts.append(dict(json.loads(row['data']),project_id=resolved))
        return facts

    def confirm(self, payload):
        with self.store.lock, self.store.db:
            data=json.loads(self._row(payload.get('id'))['data'])
            candidate=next((c for c in data['candidates'] if c['id']==data['selected_id']),None)
            if data['status']!='ready' or not candidate:
                raise StoreError('Aucune activité à confirmer.',409)
            answer=payload.get('answer')
            if answer not in ('already_done','todo','unknown'):
                raise StoreError('Invalid confirmation.')
            fact={'claim':candidate.get('completion_claim') or 'Activité déjà réalisée : '+candidate['title'],
                  'answer':answer,'project_id':candidate['project_id'],'source':'user_confirmation',
                  'confirmed_at':now(),'request_id':data['id']}
            self.store.db.execute('INSERT OR REPLACE INTO act_facts VALUES (?,?,?)',
                                 (data['id'],candidate['project_id'],encode(fact)))
            return fact

    def next(self, payload):
        with self.store.lock:
            data=json.loads(self._row(payload.get('id'))['data'])
            if not self.store.db.execute('SELECT 1 FROM act_facts WHERE request_id=?',(data['id'],)).fetchone():
                raise StoreError('Confirme d’abord l’état de cette activité.',409)
            followup=self.request({'project_id':data.get('project_scope',data['projects'][0]['id'] if len(data['projects'])==1 else None), 'instruction':data['instruction'],
                                   'attention_budget':data['attention_budget']})
            with self.store.db:
                followup['followup_depth']=data.get('followup_depth',0)+1
                followup['previous_request_id']=data['id']
                self._save(followup)
            return followup
