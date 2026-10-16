"""Integrated store policy: no unknown, stale or completed work reaches the activity card."""
import copy
import json
import unittest
from unittest.mock import patch
import test_act as fixtures
from hoomy.store import StoreError
from hoomy.decisions import MODEL
from hoomy.idea_worker import questions


class ReadyIdeasTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.ActTests()
        self.fixture.setUp()
        self.store=self.fixture.store
        self.work,_,self.result=self.fixture.complete()
        self.ideas=self.store.ideas
        self.context={'instruction':'I have 30 minutes','attention_budget':30}

    def tearDown(self):
        self.fixture.tearDown()

    def idea(self):
        return self.ideas.state()['ideas'][0]

    def answer(self, answer):
        idea=self.idea()
        return self.ideas.answer({'id':idea['id'],'version':idea['version'],'revision':idea['revision'],'answer':answer})

    def decision(self,job,choice=None):
        answers=[]
        for question in questions(job):
            selected=choice or ('ask_user' if job.get('decision_kind')=='question_batch' else job['eligible'][0]['id'])
            answers.append({'name':question['name'],'type':'choice','choice':selected,'confidence':.99,
                'probabilities':[{'value':o['value'],'probability':1 if o['value']==selected else 0} for o in question['choices']]})
        return {'model':MODEL,'answers':answers}

    def finish_job(self, job, choice=None):
        self.ideas.claim({'id':job['id']})
        return self.ideas.finish({'id':job['id'],'decision':self.decision(job,choice)})

    def test_import_unknown_questions_then_fast_choice_and_feedback(self):
        self.assertEqual(self.idea()['state'],'unknown')
        before=len(self.store.act.state()['requests'])
        result=self.ideas.choose(self.context)
        self.assertEqual(result['kind'],'checking_questions')
        self.assertEqual(result['questions'],[])
        result=self.finish_job(result)
        self.assertEqual(result['kind'],'question')
        self.assertIsNone(self.ideas.work())
        self.answer('todo')
        job=self.ideas.choose(self.context)
        self.assertEqual(job['kind'],'choosing')
        self.ideas.claim({'id':job['id']})
        selected=self.ideas.finish({'id':job['id'],'decision':self.decision(job)})
        self.assertEqual(selected['kind'],'activity')
        self.assertEqual(len(self.store.act.state()['requests']),before+1)
        self.store.act.feedback({'id':selected['activity']['id'],'decision':'done','impact':3,'attention_minutes':12})
        self.assertEqual(self.idea()['state'],'completed')
        self.assertNotEqual(self.ideas.state()['selections'][0]['kind'],'activity')
        self.assertEqual(self.store.act.state()['metrics']['completed'],1)

    def test_two_questions_max_unknown_and_skip_do_not_become_pending(self):
        self.assertEqual(self.ideas.choose(dict(self.context,questions_answered=2))['kind'],'none')
        self.answer('unknown')
        self.assertEqual(self.ideas.choose(self.context)['kind'],'none')
        self.assertEqual(self.idea()['state'],'unknown')
        self.assertEqual(self.store.act.state()['metrics']['completed'],0)

    def test_expired_state_and_budget_are_filtered_before_network(self):
        self.answer('todo')
        self.assertEqual(self.ideas.choose({'instruction':'5 minutes','attention_budget':30})['kind'],'none')
        with self.store.db:
            idea=self.ideas._idea(self.idea()['id']);idea['valid_until']='2000-01-01T00:00:00Z';self.ideas._save(idea)
        check=self.ideas.choose(self.context)
        self.assertEqual(check['kind'],'checking_questions')
        self.assertEqual(self.finish_job(check)['kind'],'question')
        self.assertIsNone(self.ideas.work())

    def test_completion_during_decision_and_stale_answers_are_rejected(self):
        self.answer('todo');old=self.idea()
        job=self.ideas.choose(self.context);self.ideas.claim({'id':job['id']})
        self.answer('already_done')
        selected=self.ideas.finish({'id':job['id'],'decision':self.decision(job)})
        self.assertEqual(selected['kind'],'none')
        with self.assertRaises(StoreError):
            self.ideas.answer({'id':old['id'],'version':old['version'],'revision':old['revision'],'answer':'todo'})
        with self.assertRaises(StoreError):self.answer('todo')

    def test_timeout_discards_late_result(self):
        self.answer('todo');job=self.ideas.choose(self.context);self.ideas.claim({'id':job['id']})
        with self.store.db:self.store.db.execute('UPDATE idea_jobs SET deadline=0 WHERE id=?',(job['id'],))
        self.assertEqual(self.ideas.state()['selections'][0]['kind'],'none')
        with self.assertRaises(StoreError):self.ideas.finish({'id':job['id'],'decision':self.decision(job)})

    def test_choice_failure_is_not_an_empty_recommendation_and_can_be_retried(self):
        self.answer('todo');job=self.ideas.choose(dict(self.context,questions_answered=1))
        self.ideas.claim({'id':job['id']})
        failed=self.ideas.finish({'id':job['id'],'failure':{'code':'timeout','unsafe':'private text'}})
        self.assertEqual(failed['status'],'failed');self.assertEqual(failed['failure'],{'code':'timeout'})
        self.assertEqual(self.idea()['state'],'pending')
        retried=self.ideas.choose(dict(failed['context'],continuation_id=job['id']))
        self.assertEqual(retried['kind'],'choosing');self.assertEqual(retried['context']['questions_answered'],1)
        self.assertEqual(self.finish_job(retried)['kind'],'activity')

    def test_failed_question_review_does_not_suppress_the_question_for_an_hour(self):
        job=self.ideas.choose(self.context);self.ideas.claim({'id':job['id']})
        self.ideas.finish({'id':job['id'],'failure':{'code':'connection'}})
        self.assertNotIn('question_review',self.idea())
        self.assertEqual(self.ideas.choose(self.context)['kind'],'checking_questions')

    def test_consent_and_scope_change_prevent_claim_or_delivery(self):
        self.answer('todo');job=self.ideas.choose(self.context)
        self.store.control({'inference_enabled':False})
        with self.assertRaises(StoreError):self.ideas.claim({'id':job['id']})
        with self.assertRaises(StoreError):self.ideas.choose(self.context)

    def test_old_completion_import_and_restart_preserve_dates(self):
        self.store.act.confirm({'id':self.work['id'],'answer':'already_done'})
        with self.store.db:
            for table in ('idea_observations','idea_jobs','ideas'):
                self.store.db.execute('DROP TABLE '+table)
        from hoomy.ideas import Ideas
        self.store.ideas=Ideas(self.store);self.ideas=self.store.ideas
        self.assertEqual(self.idea()['state'],'completed')
        first=self.idea()['confirmed_at']
        self.store.ideas=Ideas(self.store);self.ideas=self.store.ideas
        self.assertEqual(self.idea()['confirmed_at'],first)
        self.assertEqual(self.ideas.choose(self.context)['kind'],'none')

    def test_store_restart_never_replays_old_requests_over_existing_idea_decisions(self):
        from hoomy.store import Store
        self.answer('unknown')
        idea=self.ideas._idea(self.idea()['id'])
        idea['pool_active']=False
        idea['candidate']['actor']='external'
        idea['metadata_request_at']='2000-01-01T00:00:00Z'
        with self.store.db:self.ideas._save(idea)
        def rows():
            return {table:[tuple(row) for row in self.store.db.execute('SELECT * FROM '+table+' ORDER BY rowid')]
                    for table in ('ideas','idea_observations','idea_jobs','act_requests','settings')}
        before=rows()
        self.store.close()
        self.store=self.fixture.store=Store(self.fixture.root/'db')
        self.ideas=self.store.ideas
        self.assertEqual(rows(),before)

    def test_failed_first_legacy_import_rolls_back_tables_and_can_retry(self):
        from hoomy.ideas import Ideas
        with self.store.db:
            for table in ('idea_observations','idea_jobs','ideas'):
                self.store.db.execute('DROP TABLE '+table)
        requests=[tuple(row) for row in self.store.db.execute('SELECT * FROM act_requests ORDER BY rowid')]
        original=Ideas.ingest
        def interrupted(subject,request):
            original(subject,request)
            raise RuntimeError('Fixture interrupted after import writes')
        with patch.object(Ideas,'ingest',interrupted),self.assertRaises(RuntimeError):
            Ideas(self.store)
        for table in ('ideas','idea_observations','idea_jobs'):
            self.assertIsNone(self.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone())
        self.assertEqual([tuple(row) for row in self.store.db.execute('SELECT * FROM act_requests ORDER BY rowid')],requests)
        self.store.ideas=Ideas(self.store);self.ideas=self.store.ideas
        self.assertEqual(self.idea()['state'],'unknown')

    def test_preparation_does_not_block_choice(self):
        self.answer('todo')
        preparation=self.ideas.prepare(self.context)
        job=self.ideas.choose(self.context)
        self.assertEqual(job['kind'],'choosing')
        self.assertEqual(self.store.act.work()['purpose'],'prepare_ideas')
        self.assertEqual(self.ideas.state()['preparation']['id'],preparation['id'])
        self.store.act.claim({'id':preparation['id'],'generation':self.work['generation']})
        self.store.act.finish({'id':preparation['id'],'generation':self.work['generation'],'error':'Synthetic preparation failure'},failed=True)
        self.assertIsNone(self.ideas.state()['preparation'])
        self.assertEqual(self.ideas.state()['preparation_error'],'Synthetic preparation failure')

    def test_verification_bound_to_exact_candidate_and_current_observation(self):
        request=copy.deepcopy(self.work);request.update(self.result,purpose='prepare_ideas')
        candidate=request['candidates'][0]
        check={'candidate_id':candidate['id'],'project_id':candidate['project_id'],'completion_claim':candidate['completion_claim'],
            'basis':'current_observation','evidence':'A successful current observation shows unfinished work.',
            'status':'contradicted','prerequisites':'satisfied','checked_at':self.work['created_at'],
            'tools':[{'type':'commandExecution','status':'completed','exitCode':'0'}]}
        request['sources']=[{'key':'agent_verification','messages':[{'text':json.dumps(dict(check,candidate_id='another'))}]}]
        with self.store.db:self.ideas.ingest(request)
        self.assertEqual(self.idea()['state'],'unknown')
        request['sources'][0]['messages'][0]['text']=json.dumps(dict(check,basis='historical'))
        with self.store.db:self.ideas.ingest(request)
        self.assertEqual(self.idea()['state'],'unknown')
        request['id'] += '-new-observation'
        request['sources'][0]['messages'][0]['text']=json.dumps(check)
        with self.store.db:self.ideas.ingest(request)
        self.assertEqual(self.idea()['state'],'pending')
        expiry=self.idea()['valid_until']
        with self.store.db:self.ideas.ingest(request)
        self.assertEqual(self.idea()['valid_until'],expiry)
        self.answer('unknown')
        request['id'] += '-late'
        with self.store.db:self.ideas.ingest(request)
        self.assertEqual(self.idea()['state'],'unknown')
        from hoomy.store import now
        check['checked_at']=now()
        request['id'] += '-fresh'
        request['sources'][0]['messages'][0]['text']=json.dumps(check)
        with self.store.db:self.ideas.ingest(request)
        self.assertEqual(self.idea()['state'],'pending')
        self.assertIsNone(self.idea()['snoozed_until'])

    def test_reset_clears_new_tables(self):
        self.answer('todo');self.ideas.choose(self.context)
        self.store.reset({'confirm':'DELETE_HOOMY_DATA'})
        for table in ('ideas','idea_observations','idea_jobs'):
            self.assertEqual(self.store.db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)

    def test_batch_rejects_question_and_recovers_once_without_inventing_state(self):
        job=self.ideas.choose(dict(self.context,recover=True))
        result=self.finish_job(job,'investigate')
        self.assertEqual(result['kind'],'preparing')
        self.assertEqual(self.idea()['state'],'unknown')
        work=self.store.act.work()
        self.store.act.claim({'id':work['id'],'generation':work['generation']})
        self.store.act.finish({'id':work['id'],'generation':work['generation'],
            'result':{'candidates':[],'selected_id':None,'rationale':'No supported lead.','uncertainty':'Unknown.',
                      'research':{'summary':'No further source was available.','unresolved':['Missing current evidence.'],'question':None}}})
        result=next(j for j in self.ideas.state()['selections'] if j['id']==job['id'])
        self.assertEqual(result['kind'],'none')
        next_choice=self.ideas.choose(dict(self.context,recover=True,continuation_id=job['id']))
        self.assertNotEqual(next_choice['kind'],'preparing')
        self.assertIsNone(self.store.act.work())
        check=self.ideas.check_request({})
        self.assertEqual(check['purpose'],'verify_idea')
        self.store.act.cancel({'id':check['id']})
        self.assertIsNone(self.ideas.check_request({}))

    def test_unknown_continues_to_preparation_without_reasking(self):
        question=self.finish_job(self.ideas.choose(dict(self.context,recover=True)))
        self.answer('unknown')
        result=self.ideas.choose(dict(self.context,questions_answered=2,recover=True,continuation_id=question['id']))
        self.assertEqual(result['kind'],'preparing')
        duplicate=self.ideas.choose(dict(self.context,recover=True))
        self.assertEqual(duplicate['preparation_id'],result['preparation_id'])
        self.store.act.cancel({'id':result['preparation_id']})
        self.assertTrue(all(j['kind']!='preparing' for j in self.ideas.state()['selections']))

    def test_responsibility_feedback_enters_next_preparation_context(self):
        for answer in ('agent','external'):
            self.answer(answer)
            self.assertEqual(self.ideas.choose(self.context)['kind'],'none')
            prep=self.ideas.prepare(self.context)
            context=self.store.act.work()['prepared_ideas'][0]
            self.assertEqual(context['actor'],'user')
            self.assertEqual(context['state_owner'],answer)
            self.assertEqual(context['last_answer'],answer)
            self.store.act.cancel({'id':prep['id']})

    def test_preparation_batches_and_caps_pool_and_ready_questions(self):
        prep=self.ideas.prepare(self.context)
        work=self.store.act.work()
        result=copy.deepcopy(self.result)
        original=result['candidates'][0]
        result['candidates']=[dict(original,id='candidate-'+str(n),completion_claim='Review target '+str(n)+' is completed.') for n in range(10)]
        result['selected_id']=None
        self.store.act.claim({'id':prep['id'],'generation':work['generation']})
        self.store.act.finish({'id':prep['id'],'generation':work['generation'],'result':result})
        job=self.ideas.work()
        self.assertEqual(job['decision_kind'],'question_batch')
        self.assertEqual(len(job['question_candidates']),10)
        self.finish_job(job)
        state=self.ideas.state()
        self.assertEqual(state['question_pool']['active'],10)
        self.assertEqual(state['question_pool']['ready'],5)
        self.assertTrue(all(i['state']=='unknown' for i in state['ideas']))
        self.assertEqual(self.ideas.choose(self.context)['kind'],'question')
        from hoomy.ideas import Ideas
        self.store.ideas=Ideas(self.store)
        self.ideas=self.store.ideas
        self.assertEqual(self.ideas.state()['question_pool']['ready'],5)

    def test_human_update_during_batch_prevents_question_delivery(self):
        job=self.ideas.choose(self.context)
        self.ideas.claim({'id':job['id']})
        self.answer('external')
        result=self.ideas.finish({'id':job['id'],'decision':self.decision(job)})
        self.assertEqual(result['questions'],[])
        self.assertEqual(result['kind'],'none')

    def test_preparation_failure_resumes_same_interaction_with_diagnostic(self):
        self.answer('skip')
        result=self.ideas.choose(dict(self.context,recover=True))
        work=self.store.act.work()
        envelope={'id':work['id'],'generation':work['generation']}
        self.store.act.claim(envelope)
        self.store.act.progress(dict(envelope,stage='suggest'))
        self.store.act.finish(dict(envelope,error='Synthetic invalid result.',failure_code='invalid_result'),failed=True)
        shown=next(j for j in self.ideas.state()['selections'] if j['id']==result['id'])
        self.assertEqual(shown['kind'],'none')
        self.assertEqual(shown['message'],'Synthetic invalid result.')
        self.assertEqual(self.store.act.state()['requests'][0]['failure_code'],'invalid_result')

    def test_idle_host_picks_up_bounded_verification_without_an_act_request(self):
        from unittest.mock import patch
        from hoomy.edge import Edge
        self.answer('agent')
        idea=self.ideas._idea(self.idea()['id'])
        with self.store.db:
            idea.update(verification_due=True,pool_context=dict(self.context,project_id=None,questions_answered=0))
            self.ideas._save(idea)
        self.assertTrue(self.store.state()['idea_checks_pending'])
        self.assertFalse(self.store.state()['act_pending'])
        store=self.store
        class Client:
            def get(self,path):
                return store.state() if path=='/api/state' else store.act.work()
            def post(self,path,payload):
                if path=='/api/now/check-request':return store.ideas.check_request(payload)
                if path=='/api/act/result':return store.act.finish(payload)
                if path=='/api/worker':return store.worker(payload)
                return getattr(store.act,path.rsplit('/',1)[-1])(payload)
        with patch('hoomy.act_inference.suggest',return_value=self.result) as model:
            Edge(Client(),self.fixture.root).tick()
            self.assertEqual(model.call_args.args[0]['purpose'],'verify_idea')
        self.assertFalse(self.store.state()['idea_checks_pending'])
        self.assertIsNone(self.ideas.check_request({}))

    def test_legacy_candidate_is_normalized_before_background_result_submission(self):
        from hoomy.act_inference import verify_prepared
        work=copy.deepcopy(self.work)
        candidate=copy.deepcopy(self.result['candidates'][0])
        for key in ('needs_confirmation','actor','state_owner','state_question','question_reason'):
            candidate.pop(key,None)
        work.update(purpose='verify_idea',target_candidate=candidate)
        observation={'key':'agent_verification','messages':[{'text':'Synthetic inconclusive check.'}]}
        result=verify_prepared(work,self.fixture.root,lambda:False,lambda *a,**kw:observation,lambda s:None)
        self.assertTrue(result['candidates'][0]['needs_confirmation'])
        self.assertEqual(result['candidates'][0]['actor'],'unknown')
        self.assertIsNone(result['selected_id'])

    def test_rejected_worker_result_becomes_failed_not_stuck_running(self):
        from unittest.mock import patch
        from hoomy.edge import Edge,BackendError
        work=self.ideas.prepare(self.context)
        store=self.store
        class Client:
            def get(self,path):
                return store.state() if path=='/api/state' else store.act.work()
            def post(self,path,payload):
                if path=='/api/act/result':raise BackendError('Backend rejected operation (HTTP 400).')
                if path=='/api/act/failure':return store.act.finish(payload,failed=True)
                return getattr(store.act,path.rsplit('/',1)[-1])(payload)
        with patch('hoomy.act_inference.suggest',return_value=self.result):
            Edge(Client(),self.fixture.root).tick()
        result=next(r for r in self.store.act.state()['requests'] if r['id']==work['id'])
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['failure_code'],'result_rejected')
