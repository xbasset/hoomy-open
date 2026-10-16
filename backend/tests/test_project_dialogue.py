"""An inconclusive search returns evidence and an answerable intent dialogue."""
import copy
import unittest
from unittest.mock import Mock
import test_act as fixtures
from hoomy.act_inference import read_sources, suggest, validate
from hoomy.idea_worker import questions
from hoomy.decisions import MODEL
from hoomy.store import Store, StoreError


class ProjectDialogueTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.ActTests();self.fixture.setUp()
        self.store=self.fixture.store
        self.context={'instruction':'I have 5 minutes','attention_budget':5,'project_id':'p','recover':True}

    def tearDown(self):
        self.fixture.tearDown()

    def research(self):
        return {'summary':'Read the playback conversation; it does not identify the current product goal.',
                'unresolved':['Whether playback is intended for a personal prototype or a shared test.'],
                'question':{'project_id':'p','dimension':'goal',
                    'prompt':'What would make this playback project useful to you now?',
                    'why':'The conversation describes a failure but no current outcome you want.',
                    'changes_decision':'Your goal determines which small review would be worthwhile.',
                    'options':['A personal listening prototype','A test I can share'],'source_refs':['s']}}

    def prepared(self):
        job=self.store.ideas.choose(self.context);work=self.store.act.work()
        result={'candidates':[],'selected_id':None,'rationale':'The next outcome is unclear.',
                'uncertainty':'Current intention is missing.','research':self.research(),
                'sources':read_sources(work,['s'],self.fixture.root,lambda:False)}
        env={'id':work['id'],'generation':work['generation']}
        self.store.act.claim(env);self.store.act.finish(dict(env,result=result))
        return work,next(j for j in self.store.ideas.state()['selections'] if j['id']==job['id'])

    def approve(self,job,choice='ask_user'):
        self.store.ideas.claim({'id':job['id']});q=questions(job)[0]
        decision={'model':MODEL,'answers':[{'name':q['name'],'type':'choice','choice':choice,'confidence':.99,
            'probabilities':[{'value':o['value'],'probability':int(o['value']==choice)} for o in q['choices']]}]}
        return self.store.ideas.finish({'id':job['id'],'decision':decision})

    def test_failed_intent_review_is_retryable_without_research_or_user_rephrasing(self):
        work,job=self.prepared();self.store.ideas.claim({'id':job['id']})
        count=len(self.store.act.state()['requests'])
        failed=self.store.ideas.finish({'id':job['id'],'failure':{'code':'timeout'}})
        self.assertEqual(failed['status'],'failed')
        retried=self.store.ideas.choose(dict(job['context'],continuation_id=job['id'],recover=True))
        self.assertEqual(retried['kind'],'checking_questions')
        self.assertEqual(retried['preparation_id'],work['id'])
        self.assertEqual(len(self.store.act.state()['requests']),count)
        self.assertEqual(self.approve(retried)['kind'],'context_question')

    def test_empty_search_reports_evidence_and_reviews_intent_before_asking(self):
        work,job=self.prepared()
        self.assertEqual(job['kind'],'checking_questions');self.assertNotIn('context_question',job)
        self.assertEqual(job['research_report']['sources'][0]['title'],'Playback check')
        self.assertTrue(job['research_report']['unresolved'])
        approved=self.approve(job)
        self.assertEqual(approved['kind'],'context_question')
        self.assertEqual(approved['context_question']['dimension'],'goal')
        self.assertEqual(self.store.ideas.state()['ideas'],[])
        self.assertEqual(self.store.ideas.choose(self.context)['kind'],'context_question')
        self.assertIsNone(self.store.ideas.work());self.assertIsNone(self.store.act.work())

    def test_intent_answer_is_durable_reused_and_never_completion(self):
        work,job=self.prepared();self.approve(job)
        answer='I want a personal evening listening prototype.'
        continued=self.store.ideas.context_answer({'id':job['id'],'answer':answer})
        self.assertEqual(continued['kind'],'preparing')
        followup=self.store.act.work()
        self.assertEqual(followup['project_context'][0]['answer'],answer)
        self.assertEqual(followup['research_seed']['request_id'],work['id'])
        self.assertEqual(self.store.act.facts({'p'}),[]);self.assertEqual(self.store.ideas.state()['ideas'],[])
        calls=[]
        def model(stage,payload,schema):
            calls.append(stage)
            self.assertEqual(payload['project_context'][0]['answer'],answer)
            self.assertEqual(payload['evidence'][0]['key'],'s')
            return {'candidates':[],'selected_id':None,'rationale':'Need a current observation.',
                    'uncertainty':'No completion evidence.','research':dict(self.research(),question=None)}
        result=suggest(followup,self.fixture.root,call=model)
        self.assertEqual(calls,['suggest'])
        env={'id':followup['id'],'generation':followup['generation']}
        self.store.act.claim(env);self.store.act.finish(dict(env,result=result))
        self.store.close();self.fixture.store=Store(self.fixture.root/'db');self.store=self.fixture.store
        self.assertEqual(self.store.dialogue.context({'p'})[0]['answer'],answer)
        self.assertEqual(self.store.act.facts({'p'}),[])
        self.assertEqual(self.store.act.state()['metrics']['completed'],0)
        self.store.reset({'confirm':'DELETE_HOOMY_DATA'})
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM project_context_answers').fetchone()[0],0)

    def test_unknown_intent_keeps_question_open_without_loop_or_task_fact(self):
        work,job=self.prepared();self.approve(job)
        reply=self.store.ideas.context_answer({'id':job['id'],'response':'unknown'})
        self.assertEqual(reply['kind'],'none');self.assertIsNone(self.store.act.work())
        self.assertEqual(self.store.dialogue.context({'p'})[0]['response'],'unknown')
        self.assertEqual(self.store.act.facts({'p'}),[])
        with self.assertRaises(StoreError):self.store.ideas.context_answer({'id':job['id'],'answer':'Duplicate'})

    def test_rejected_question_still_returns_research(self):
        _,job=self.prepared();reply=self.approve(job,'skip')
        self.assertEqual(reply['kind'],'none');self.assertTrue(reply['research_report']['summary'])
        with self.assertRaises(StoreError):self.store.ideas.context_answer({'id':job['id'],'answer':'Not admitted'})

    def test_scope_change_prevents_intent_write(self):
        _,job=self.prepared();self.approve(job)
        self.store.control({'inference_enabled':False})
        with self.assertRaises(StoreError):self.store.ideas.context_answer({'id':job['id'],'answer':'New goal'})
        self.assertEqual(self.store.dialogue.context({'p'}),[])

    def test_question_citations_are_bound_to_read_project_sources(self):
        work=self.fixture.request();sources=read_sources(work,['s'],self.fixture.root,lambda:False)
        response=self.fixture.response();response['research']=self.research()
        for field,value in [('project_id','other'),('source_refs',['unread']),('dimension','completion')]:
            bad=copy.deepcopy(response);bad['research']['question'][field]=value
            with self.assertRaises(StoreError):validate(bad,work,sources)

    def test_intent_question_precedes_expensive_technical_check(self):
        work=self.fixture.request();response=self.fixture.response();response['research']=self.research()
        verifier=Mock()
        result=suggest(work,self.fixture.root,call=lambda stage,*args: {'session_ids':['s'],'reason':'Read playback'} if stage=='retrieve' else response,verifier=verifier)
        verifier.assert_not_called();self.assertIsNotNone(result['research']['question'])

    def test_consulted_sources_are_visible_while_model_is_running(self):
        work=self.fixture.request();env={'id':work['id'],'generation':work['generation']}
        self.store.act.claim(env)
        self.store.act.progress(dict(env,stage='suggest',sources=[{'key':'s','read':True}]))
        source=self.store.ideas.state()['preparation']['sources'][0]
        self.assertEqual(source['title'],'Playback check')
        with self.assertRaises(StoreError):self.store.act.progress(dict(env,stage='suggest',sources=[{'key':'unlinked'}]))

    def test_unoriented_project_asks_before_generating_technical_work(self):
        self.store.ideas.prepare(self.context);work=self.store.act.work();stages=[]
        def model(stage,payload,schema):
            stages.append(stage)
            return {'session_ids':['s'],'reason':'Read playback'} if stage=='retrieve' else self.research()
        verifier=Mock()
        result=suggest(work,self.fixture.root,call=model,verifier=verifier)
        self.assertEqual(stages,['retrieve','orient'])
        self.assertEqual(result['candidates'],[])
        verifier.assert_not_called()

    def test_graph_exposes_empty_search_and_feedback_enters_next_context(self):
        work,job=self.prepared();self.approve(job)
        graph=self.store.explorer.graph(work['id'])
        ids={n['id'] for n in graph['nodes']}
        self.assertTrue({'request','source:s','candidates','intent','outcome'}.issubset(ids))
        self.assertTrue(all(e['source'] in ids and e['target'] in ids for e in graph['edges']))
        self.assertIn('Aucun traitement',graph['background'])
        self.store.explorer.feedback({'request_id':work['id'],'node_id':'source:s','project_id':'p',
                                     'text':'This old technical conversation misses the current goal.'})
        next_work=self.fixture.request()
        self.assertEqual(len(next_work['research_feedback']),1)
        self.assertEqual(next_work['research_feedback'][0]['node_id'],'source:s')
        self.assertEqual(self.store.act.facts({'p'}),[])
        with self.assertRaises(StoreError):
            self.store.explorer.feedback({'request_id':work['id'],'node_id':'missing','project_id':'p','text':'Invalid node'})

    def test_invalid_orientation_gets_one_bounded_repair(self):
        self.store.ideas.prepare(self.context);work=self.store.act.work();attempts=[]
        def model(stage,payload,schema):
            if stage=='retrieve':return {'session_ids':['s'],'reason':'Read playback'}
            attempts.append(payload)
            result=self.research()
            if len(attempts)==1:result['question']['source_refs']=['unread']
            return result
        result=suggest(work,self.fixture.root,call=model)
        self.assertEqual(len(attempts),2)
        self.assertIn('validation_error',attempts[1])
        self.assertEqual(result['research']['question']['source_refs'],['s'])
