import copy
import unittest

from hoomy import state_executor as executor
import test_state_executor as fixtures


class QualityClientTests(unittest.TestCase):
    def runner(self, final, *, both=False, extraction=None):
        remote, model, runner = fixtures.ExecutorTests().setup_runner()
        original_plan = remote.plan
        def plan(phase):
            value = copy.deepcopy(original_plan(phase))
            if phase == 'reconcile' and both:
                payload = value['input']
                payload['control']['required_machine_aliases'] = ['machine-1', 'machine-2']
                second = copy.deepcopy(fixtures.EVIDENCE)
                second['assertions'][0]['source_refs'] = [{'passage_id': 'p0002_01'}]
                payload['extractions'].append({'machine_alias': 'machine-2', 'shared_passage_ids': [], 'result': second})
                payload['passages'].append({'passage_id': 'p0002_01', 'text': 'A second historical instruction.'})
                import hashlib
                value['payload_hash'] = hashlib.sha256(executor.canonical(payload)).hexdigest()
            return value
        remote.plan = plan
        original_worker = remote.worker
        def worker(args):
            if args['action'] != 'finish':
                return original_worker(args)
            remote.actions.append(copy.deepcopy(args))
            remote.finishes.append(copy.deepcopy(args))
            remote.completed += 1
            remote.tokens += args['method']['usage']['total_tokens']
            if remote.completed == 2:
                remote.status = 'ready'
            return remote.control()
        remote.worker = worker
        original_call = model.call
        def call(payload, *args):
            _, method = original_call(payload, *args)
            value = final if payload['task'] == 'reconcile' else extraction or fixtures.EVIDENCE
            return copy.deepcopy(value), method
        model.call = call
        return remote, model, runner

    def test_partial_visible_output_forwards_original_candidate_unchanged(self):
        candidate = copy.deepcopy(fixtures.EVIDENCE)
        omitted = copy.deepcopy(candidate['assertions'][0])
        omitted.update(key='cut', text='An unfinished historical')
        candidate['assertions'].append(omitted)
        before = executor.canonical(candidate)
        remote, model, runner = self.runner(candidate)
        self.assertEqual(runner.run()['status'], 'ready')
        self.assertEqual(remote.finishes[-1]['output'], candidate)
        self.assertEqual(executor.canonical(candidate), before)
        self.assertEqual(len(model.calls), 2)

    def test_visible_device_loss_stops_once_and_retains_known_usage(self):
        candidate = copy.deepcopy(fixtures.EVIDENCE)
        omitted = copy.deepcopy(candidate['assertions'][0])
        omitted.update(key='second', text='A cut second machine')
        omitted['source_refs'] = [{'passage_id': 'p0002_01'}]
        candidate['assertions'].append(omitted)
        remote, model, runner = self.runner(candidate, both=True)
        captured = []
        runner.rejected_output = lambda *args: captured.append(args)
        result = runner.run()
        self.assertEqual(result['failure_code'], 'insufficient_device_evidence')
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(remote.tokens, 30)
        self.assertEqual(len(remote.finishes), 1)
        self.assertEqual(remote.actions[-1]['code'], 'insufficient_device_evidence')
        self.assertNotIn('output', remote.actions[-1])
        self.assertIn('method', remote.actions[-1])
        self.assertEqual(captured[0][0], candidate)

    def test_extraction_output_and_reconciliation_input_are_not_quality_rewritten(self):
        extraction = copy.deepcopy(fixtures.EVIDENCE)
        extraction['assertions'][0]['text'] = 'A cut extraction phrase'
        remote, model, runner = self.runner(fixtures.EVIDENCE, extraction=extraction)
        expected_plan = copy.deepcopy(remote.plan('reconcile'))
        self.assertEqual(runner.run()['status'], 'ready')
        self.assertEqual(remote.finishes[0]['output'], extraction)
        self.assertEqual(remote.plan('reconcile'), expected_plan)
        self.assertEqual(len(model.calls), 2)
