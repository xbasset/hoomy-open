"""User-authored project intent remains durable and distinct from task facts."""
import unittest
import test_act as fixtures
from hoomy.store import Store, StoreError, encode, now
from hoomy.decisions import MODEL
from hoomy.idea_worker import questions


class ProjectProfileTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.ActTests();self.fixture.setUp()
        self.store=self.fixture.store;self.fixture.complete()

    def tearDown(self):
        self.fixture.tearDown()

    def update(self, fields):
        profile=self.store.dialogue.profile('p')
        return self.store.dialogue.update_profile({'project_id':'p','revision':profile['revision'],'fields':fields})

    def test_patch_persists_and_does_not_mark_work_pending_or_complete(self):
        profile=self.update({'goal':'Ship a personal listening prototype','motivation':'Listen during my commute'})
        self.assertEqual(profile['fields']['goal']['source'],'user_project_profile')
        self.assertEqual(profile['ideas'][0]['state'],'unknown')
        self.assertEqual(self.store.act.state()['metrics']['completed'],0)
        reopened=Store(self.fixture.root/'db')
        try:self.assertEqual(reopened.dialogue.profile('p')['fields']['goal']['answer'],'Ship a personal listening prototype')
        finally:reopened.close()

    def test_partial_update_clear_and_history(self):
        first=self.update({'goal':'Share a working prototype','priority':'Fix playback'})
        result=self.update({'goal':''})
        self.assertEqual(result['fields']['goal']['response'],'cleared')
        self.assertEqual(result['fields']['goal']['supersedes_id'],first['fields']['goal']['id'])
        self.assertEqual(result['fields']['priority']['answer'],'Fix playback')
        self.assertEqual(next(c for c in self.store.dialogue.context({'p'}) if c['dimension']=='goal')['answer'],'')

    def test_stale_revision_and_invalid_patch_cannot_overwrite(self):
        before=self.store.dialogue.profile('p');self.update({'goal':'Current goal'})
        with self.assertRaises(StoreError):
            self.store.dialogue.update_profile({'project_id':'p','revision':before['revision'],'fields':{'goal':'Old draft'}})
        current=self.store.dialogue.profile('p')
        with self.assertRaises(StoreError):
            self.store.dialogue.update_profile({'project_id':'p','revision':current['revision'],'fields':{'goal':'Partial write','priority':'x'*4001}})
        self.assertEqual(self.store.dialogue.profile('p')['fields']['goal']['answer'],'Current goal')

    def test_goal_change_rejects_late_choice_and_enters_both_inference_paths(self):
        idea=self.store.ideas.state()['ideas'][0]
        self.store.ideas.answer({'id':idea['id'],'version':idea['version'],'revision':idea['revision'],'answer':'todo'})
        job=self.store.ideas.choose({'project_id':'p','instruction':'Help me move forward','attention_budget':30})
        self.store.ideas.claim({'id':job['id']})
        self.update({'goal':'A reliable personal prototype','situation':'Playback still needs a check'})
        question=questions(job)[0]
        decision={'model':MODEL,'answers':[{'name':question['name'],'type':'choice','choice':'abstain','confidence':1,
                  'probabilities':[{'value':c['value'],'probability':int(c['value']=='abstain')} for c in question['choices']]}]}
        with self.assertRaises(StoreError):self.store.ideas.finish({'id':job['id'],'decision':decision})
        new=self.store.ideas.choose(job['context'])
        self.assertIn('A reliable personal prototype',[c['answer'] for c in new['project_context']])
        request=self.store.act.request({'project_id':'p','instruction':'Prepare a useful activity','attention_budget':30})
        self.assertIn('A reliable personal prototype',[c['answer'] for c in request['project_context']])
        self.assertEqual(self.store.dialogue.profile('p')['ideas'][0]['state'],'pending')

    def test_sources_do_not_include_another_project(self):
        with self.store.db:
            self.store.db.execute('INSERT INTO projects VALUES (?,?,?,?,?,?)',('other',encode({'name':'Other'}),'{}',None,now(),now()))
            self.store.db.execute('INSERT INTO catalog VALUES (?,?,?)',('other-source',encode({'session_id':'other-source','title':'Other source','modified_at':now()}),'fixture'))
            self.store.db.execute('INSERT INTO catalog_links VALUES (?,?)',('other','other-source'))
        self.assertEqual([s['id'] for s in self.store.dialogue.profile('p')['sources']],['s'])
