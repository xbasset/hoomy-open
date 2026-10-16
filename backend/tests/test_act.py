"""Exercise the daily feedback loop without live inference or private history."""
import copy
import json
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path
from hoomy.store import Store, StoreError, encode, now
from hoomy.act_inference import suggest, validate, read_sources, InferenceError
from hoomy.collector import snapshot
from hoomy.edge import Edge


class ActTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.store = Store(self.root/'db')
        self.store.control({'consent':True,'inference_enabled':True,'running':False})
        self.generation = self.store.state()['control']['generation']
        with self.store.db:
            self.store.db.execute('INSERT INTO projects VALUES (?,?,?,?,?,?)',
                ('p',encode({'name':'Example','purpose':'Ship a working prototype'}),'{}',None,now(),now()))
        path = self.root/'sessions'/'s.jsonl'
        path.parent.mkdir()
        path.write_text(json.dumps({'type':'session_meta','payload':{'id':'s','cwd':'/example'}})+'\n'+
                        json.dumps({'type':'event_msg','payload':{'type':'user_message','message':'Playback still fails. Reproduce it on the current build.'}})+'\n')
        snap = snapshot(path)
        self.record = {'session_id':'s','title':'Playback check','cwd':'/example','source_path':str(path),'modified_at':snap['modified_at']}
        with self.store.db:
            self.store.db.execute('INSERT INTO catalog VALUES (?,?,?)',('s',encode(self.record),'fixture'))
            self.store.db.execute('INSERT INTO catalog_links VALUES (?,?)',('p','s'))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def request(self):
        return self.store.act.request({'project_id':'p','instruction':'I have time to check playback.','attention_budget':30})

    def response(self):
        return {'candidates':[{'id':'a','project_id':'p','title':'Check playback','action':'Reproduce playback on the current build.',
            'why_now':'The last recorded report describes playback failure.', 'completion_claim':'Playback has been checked on the current build.', 'needs_confirmation':True, 'expected_impact':3,'expected_attention_minutes':20,
            'actor':'user','state_owner':'user','state_question':'Have you checked playback on the current build?',
            'question_reason':'You reported a playback issue during your own listening test.',
            'source_refs':['s']}], 'selected_id':'a','rationale':'Useful within the available time.','uncertainty':'The report may be stale.',
            'research':{'summary':'Read the playback report.','unresolved':['Current playback behavior is unknown.'],'question':None}}

    def complete(self):
        work = self.request()
        seen = []
        def call(stage,payload,schema):
            seen.append(payload)
            return {'session_ids':['s'],'reason':'Check recent playback evidence.'} if stage=='retrieve' else self.response()
        result = suggest(work,self.root,call=call)
        envelope = {'id':work['id'],'generation':self.generation}
        self.store.act.claim(envelope)
        self.store.act.finish(dict(envelope,result=result))
        return work,seen,result

    def test_real_source_to_feedback_to_next_prompt(self):
        before = self.store.state()['projects']
        work,seen,result = self.complete()
        self.assertIn('Playback still fails',seen[1]['evidence'][0]['messages'][0]['text'])
        self.store.act.feedback({'id':work['id'],'decision':'done','impact':4,'attention_minutes':10,'reason':'Faster than expected.'})
        metrics = self.store.act.state()['metrics']
        self.assertEqual(metrics,{'completed':1,'impact':4.0,'attention':10.0,'impact_error':1.0,'attention_error':10.0})
        next_work = self.request()
        self.assertEqual(next_work['history'][0]['feedback']['impact'],4)
        self.assertEqual(next_work['history'][0]['activity']['expected_impact'],3)
        self.assertEqual(self.store.state()['projects'],before)
        persisted = self.store.act.state()['requests'][-1]
        self.assertEqual(persisted['candidates'][0]['expected_impact'],3)
        self.store.close()
        self.store=Store(self.root/'db')
        self.assertEqual(self.store.act.state()['metrics']['completed'],1)

    def test_unknown_deferral_and_feedback_edits(self):
        work,_,_ = self.complete()
        self.assertEqual(self.store.act.state()['metrics']['completed'],0)
        self.store.act.feedback({'id':work['id'],'decision':'deferred','impact':0,'attention_minutes':0})
        state=self.store.act.state()
        self.assertIsNone(state['requests'][0]['feedback']['impact'])
        self.assertEqual(state['metrics']['completed'],0)
        with self.assertRaises(StoreError):
            self.store.act.feedback({'id':work['id'],'decision':'done','impact':2})
        self.store.act.feedback({'id':work['id'],'decision':'done','impact':0,'attention_minutes':0})
        self.assertEqual(self.store.act.state()['metrics']['completed'],1)
        self.store.act.feedback({'id':work['id'],'decision':'done','impact':5,'attention_minutes':5})
        self.assertEqual(self.store.act.state()['metrics']['completed'],1)

    def test_duplicate_claim_cancel_reset_and_lease(self):
        work = self.request()
        with self.assertRaises(StoreError): self.request()
        envelope={'id':work['id'],'generation':self.generation}
        self.store.act.claim(envelope)
        with self.assertRaises(StoreError): self.store.act.claim(envelope)
        self.store.act.cancel(envelope)
        with self.assertRaises(StoreError): self.store.act.finish(dict(envelope,result=self.response()))
        work=self.request(); envelope['id']=work['id']; self.store.act.claim(envelope)
        with self.store.db:
            self.store.db.execute('UPDATE act_requests SET started_at=?',(time.time()-1000,))
        self.assertEqual(self.store.act.state()['requests'][0]['status'],'failed')
        self.store.reset({'confirm':'DELETE_HOOMY_DATA'})
        with self.assertRaises(StoreError): self.store.act.finish(dict(envelope,result=self.response()))
        self.assertEqual(self.store.act.state()['requests'],[])

    def test_output_validation_and_identity_preservation(self):
        work=self.request()
        sources=read_sources(work,['s'],self.root,lambda:False)
        for field,value in [('project_id','unknown'),('source_refs',['unread']),('expected_impact',True),('expected_attention_minutes',float('nan')),('expected_attention_minutes',31),('expected_impact',1)]:
            response=self.response();response['candidates'][0][field]=value
            with self.assertRaises(StoreError):validate(response,work,sources)
        response=self.response();response['id']='replacement'
        self.assertNotIn('id',validate(response,work,sources))

    def test_path_scope_and_invalid_retrieval(self):
        work=self.request()
        work['exclusions']=['/example']
        self.assertEqual(read_sources(work,['s'],self.root,lambda:False)[0]['messages'],[])
        work['exclusions']=[]
        work['catalog'][0]['source_path']=str(self.root/'db'/'hoomy.sqlite3')
        self.assertEqual(read_sources(work,['s'],self.root,lambda:False)[0]['messages'],[])
        def invalid(*args):return {'session_ids':['unlinked']}
        with self.assertRaises(InferenceError):suggest(work,self.root,call=invalid)

    def test_consent_and_discovery_activity(self):
        self.store.control({'inference_enabled':False})
        with self.assertRaises(StoreError):self.request()
        self.store.control({'inference_enabled':True,'running':True,'consent':True})
        with self.assertRaises(StoreError):self.request()

    def test_inactive_project_only_on_explicit_selection(self):
        with self.store.db:
            self.store.db.execute('UPDATE projects SET overrides=?',(encode({'activity':'inactive'}),))
        with self.assertRaises(StoreError):self.store.act.request({'attention_budget':20})
        self.assertEqual(self.request()['projects'][0]['id'],'p')

    def test_host_worker_runs_only_requested_act_while_discovery_is_paused(self):
        work=self.request()
        result=suggest(work,self.root,call=lambda stage,*_: {'session_ids':['s'],'reason':'Relevant'} if stage=='retrieve' else self.response())
        store=self.store
        class Client:
            def get(self,path):
                return store.state() if path=='/api/state' else store.act.work()
            def post(self,path,payload):
                operation=path.rsplit('/',1)[-1]
                if operation=='result':return store.act.finish(payload)
                return getattr(store.act,operation)(payload)
        edge=Edge(Client(),self.root)
        with patch('hoomy.act_inference.suggest',return_value=result) as model:
            edge.tick()
            model.assert_called_once()
        self.assertEqual(store.act.state()['requests'][0]['status'],'ready')
        self.assertFalse(store.state()['control']['running'])
        self.assertEqual(store.state()['coverage']['analyzed'],0)

    def test_confirmation_is_durable_and_next_uses_it(self):
        work,_,_=self.complete()
        fact=self.store.act.confirm({'id':work['id'],'answer':'already_done'})
        self.assertEqual(fact['source'],'user_confirmation')
        self.assertEqual(self.store.act.state()['metrics']['completed'],0)
        self.assertIsNone(self.store.act.state()['requests'][0]['feedback'])
        next_work=self.store.act.next({'id':work['id']})
        self.assertEqual(next_work['facts'][0]['answer'],'already_done')
        self.assertEqual(next_work['followup_depth'],1)
        self.assertEqual(next_work['project_scope'],'p')
        self.store.act.confirm({'id':work['id'],'answer':'unknown'})
        self.assertEqual(self.store.act.facts({'p'})[0]['answer'],'unknown')
        self.assertEqual(next_work['facts'][0]['answer'],'already_done')
        self.store.close();self.store=Store(self.root/'db')
        self.assertEqual(self.store.act.facts({'p'})[0]['answer'],'unknown')
        self.store.reset({'confirm':'DELETE_HOOMY_DATA'})
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM act_facts').fetchone()[0],0)

    def test_duration_from_request_overrides_old_default(self):
        work=self.store.act.request({'project_id':'p','instruction':"I have 15 minutes",'attention_budget':30})
        self.assertEqual(work['attention_budget'],15)

    def test_large_history_preserves_human_exchanges(self):
        path=Path(self.record['source_path'])
        with path.open('a') as f:
            f.write(json.dumps({'type':'response_item','payload':{'type':'function_call_output','output':'x'*(17*1024*1024)}})+'\n')
            f.write(json.dumps({'type':'event_msg','payload':{'type':'user_message','message':'The DNS change was completed yesterday.'}})+'\n')
            for i in range(30):
                f.write(json.dumps({'type':'response_item','payload':{'type':'function_call_output','output':str(i)*1000}})+'\n')
        sources=read_sources(self.request(),['s'],self.root,lambda:False)
        self.assertEqual(sources[0]['skipped_records'],1)
        self.assertTrue(any('completed yesterday' in m['text'] for m in sources[0]['messages']))
        self.assertLessEqual(sum(len(m['text']) for m in sources[0]['messages'] if m['role']=='tool'),800)

    def test_verification_causes_one_reconsideration(self):
        work=self.request()
        stages=[]
        def call(stage,payload,schema):
            stages.append(stage)
            if stage=='retrieve':return {'session_ids':['s'],'reason':'Relevant'}
            response=self.response()
            if stage=='suggest_after_check':
                self.assertEqual(payload['evidence'][-1]['key'],'agent_verification')
                response.update(selected_id=None,candidates=[],rationale='The condition is already satisfied.')
            return response
        observation={'key':'agent_verification','messages':[{'text':'confirmed','role':'tool','id':'check','truncated':False}]}
        result=suggest(work,self.root,call=call,verifier=lambda *_:observation)
        self.assertEqual(stages,['retrieve','suggest','suggest_after_check'])
        self.assertIsNone(result['selected_id'])

    def test_unavailable_verification_preserves_unknown(self):
        from hoomy.act_verification import verify
        with patch('hoomy.act_verification._binary', side_effect=OSError('unavailable')):
            result=verify({'completion_claim':'The release is ready.'},{})
        observation=json.loads(result['messages'][0]['text'])
        self.assertEqual(observation['status'],'unknown')
        self.assertEqual(observation['tools'],[])

    def test_verification_respects_cancellation(self):
        from hoomy.act_verification import verify
        from hoomy.inference import InferenceCancelled
        with self.assertRaises(InferenceCancelled):
            verify({'completion_claim':'The release is ready.'},{},cancelled=lambda:True)
