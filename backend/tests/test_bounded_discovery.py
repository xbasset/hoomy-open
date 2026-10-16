import unittest
import base64
import threading
import time
from pathlib import Path
from unittest.mock import patch
from hoomy.bounded_discovery import run
from hoomy.evidence_selection import select
from test_live_discovery import LiveDiscoveryTests
from hoomy.live_discovery import checkpoint
from hoomy.store import StoreError


class SelectionTests(unittest.TestCase):
    def test_user_and_explicit_summary_not_long_answers_or_tools(self):
        messages = [dict(id='u', role='user', text='Build irrigation schedules.'),
                    dict(id='a', role='assistant', text='Here is the implementation. ' * 100),
                    dict(id='s', role='assistant', text='Summary:\nIrrigation scheduling is implemented.'),
                    dict(id='t', role='tool', text='irrigation log')]
        result = select(messages, [])
        self.assertEqual({p['message_id'] for p in result}, {'u', 's'})
        self.assertLessEqual(sum(len(p['text']) for p in result), 600)

    def test_fallback_is_explicit_and_offsets_are_exact(self):
        text = 'Assistant-only context. ' * 100
        messages = [dict(id='a', role='assistant', text=text)]
        self.assertEqual(select(messages, []), [])
        p = select(messages, [], fallback=True)[0]
        self.assertEqual(text[p['start']:p['start']+len(p['text'])], p['text'])

    def test_runner_bounds_parallelism_and_checkpoints_each_classified_source(self):
        rows = [dict(session_id=str(i), title='Irrigation', cwd='/orchid', modified_at='2026-01-01') for i in range(201)]
        class Reader:
            def __init__(self, rows, *args):
                self.sid = rows[0]['session_id']
                self.snapshots = {}
            def inspect(self, sid, query):
                self.snapshots[sid] = dict(snapshot_id=sid, raw_b64=base64.b64encode(b'raw').decode(),
                    messages=[dict(id=sid, role='user', text='Plan irrigation')])
        active = 0
        peak = 0
        lock = threading.Lock()
        def classify(payload, cancelled):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(.04)
            with lock: active -= 1
            return dict(projects=[], unassigned=[dict(session_id=r['id'],outcome='ambiguous') for r in payload['conversations']])
        saved = []
        with patch('hoomy.bounded_discovery.EvidenceTools', Reader):
            result = run(rows, [], Path('/fictional'), [], lambda:False, lambda _:None,
                ingest=lambda _:None, publish=lambda *args:None,
                feedback=lambda:dict(known_projects=[], remaining_ids=[]),
                checkpoint=lambda ids,*args,**kwargs:saved.extend(ids), classify_fn=classify)
        self.assertEqual(result['classified'], 201)
        self.assertEqual(len(set(saved)), 201)
        self.assertLessEqual(peak, 3)
        self.assertGreater(peak, 1)


class BoundedCheckpointTests(LiveDiscoveryTests):
    def enable(self):
        with self.store.db:
            s = self.store._get('overview')
            s['bounded_discovery'] = True
            self.store._set('overview', s)

    def test_bounded_checkpoint_preserves_ambiguity_and_finishes_honestly(self):
        self.enable()
        checkpoint(self.store, self.env(bounded=True, attempted_ids=['0', '1'],
            excerpt_evidence={k:self.evidence[k] for k in ('0','1')},
            unassigned=[{'session_id':'0','outcome':'no_project'}, {'session_id':'1','outcome':'ambiguous'}]))
        self.assertNotIn('0', self.store._get('overview')['queue'])
        self.assertIn('1', self.store._get('overview')['queue'])
        checkpoint(self.store, self.env(bounded=True, finish=True))
        self.assertFalse(self.store.state()['control']['running'])
        self.assertEqual(len(self.store._get('overview')['queue']), 4)

    def test_bounded_requires_explicit_new_mode(self):
        with self.assertRaises(StoreError):
            checkpoint(self.store, self.env(bounded=True, finish=True))
