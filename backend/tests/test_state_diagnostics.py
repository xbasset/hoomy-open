"""Closed native facts distinguish failed setup, transport and received output."""
import copy
import json
import unittest

from hoomy import inference
from hoomy import state_executor as executor
from test_state_executor import Model, Remote, PROJECT, RUN, USAGE, EMPTY


def client_fixture(events=(), turn_response=None, rejection=None, ephemeral=True, tools=None):
    client = object.__new__(inference._Client)
    client.instruction_source_count = 0
    client.requests = []
    client.metrics = {}
    def request(method, params):
        client.requests.append(method)
        if method == 'thread/start':
            return {'thread': {'id': 'thread', 'ephemeral': ephemeral}, 'model': 'native-model'}
        if method == 'mcpServerStatus/list':
            return {'data': [{'tools': tools}] if tools else [], 'nextCursor': None}
        if rejection:
            raise rejection
        return turn_response if turn_response is not None else {'turn': {'id': 'turn'}}
    client.request = request
    stream = iter(events)
    def event(deadline, *, available_only=False):
        value = next(stream, None)
        if value is None:
            if available_only:return None
            raise inference._TransientError('fixture transport ended')
        if isinstance(value, Exception):
            raise value
        client.telemetry.observe(value)
        return value
    client.event = event
    return client


def completion(thread='thread', turn='turn', status='completed', error=None):
    return {'method': 'turn/completed', 'params': {'threadId': thread,
        'turn': {'id': turn, 'status': status, 'error': error}}}


class NativeDiagnosticTests(unittest.TestCase):
    def test_boundary_refusal_is_before_turn_and_contains_no_private_values(self):
        for client in (client_fixture(ephemeral=False), client_fixture(tools={'private-tool': {'secret': 'private'}})):
            with self.assertRaises(inference.InferenceError):
                client.extract({'private': 'source content'}, None)
            diag=executor.safe_diagnostic(client.diagnostic)
            self.assertEqual(diag['category'], 'boundary_refusal')
            self.assertFalse(diag['turn_start_requested'])
            self.assertFalse(diag['turn_started'])
            self.assertNotIn('turn/start', client.requests)
            self.assertNotIn('private', json.dumps(diag))

    def test_native_rejection_requires_a_documented_discriminator(self):
        for error, expected in (({'message':'private token quota connection'}, 'unknown'),
                ({'codexErrorInfo':'unauthorized','message':'private'}, 'auth_required'),
                ({'codexErrorInfo':'usageLimitExceeded'}, 'capacity_limited'),
                ({'codexErrorInfo':{'responseStreamDisconnected': {'httpStatusCode':502}}}, 'transport_or_timeout')):
            client=client_fixture(rejection=inference._classify_error(error))
            with self.assertRaises(inference.InferenceError): client.extract({}, None)
            self.assertEqual(client.diagnostic['category'], expected)
            self.assertTrue(client.diagnostic['turn_start_requested'])
            self.assertFalse(client.diagnostic['turn_started'])
            self.assertFalse(client.diagnostic['completion_received'])
            self.assertNotIn('private', json.dumps(client.diagnostic))

    def test_malformed_ack_does_not_claim_native_turn_started(self):
        for response in ({}, {'turn':None}, {'turn':{}}, {'turn':{'id':''}}, {'turn':{'id':42}}):
            client=client_fixture(turn_response=response)
            with self.assertRaises(inference.InferenceError): client.extract({}, None)
            self.assertTrue(client.diagnostic['turn_start_requested'])
            self.assertFalse(client.diagnostic['turn_started'])
            self.assertFalse(client.diagnostic['completion_received'])

    def test_wrong_thread_and_turn_completion_then_timeout_stays_unconfirmed(self):
        client=client_fixture([completion(thread='other'),completion(turn='other'),
            inference._TransientError('safe local timeout')])
        with self.assertRaises(inference.InferenceError): client.extract({}, None)
        self.assertEqual(client.diagnostic['stage'], 'turn_wait')
        self.assertEqual(client.diagnostic['category'], 'transport_or_timeout')
        self.assertTrue(client.diagnostic['turn_started'])
        self.assertFalse(client.diagnostic['completion_received'])

    def test_failed_terminal_event_is_received_even_without_final_output(self):
        client=client_fixture([completion(status='failed',error={'codexErrorInfo':'badRequest','message':'private'})])
        with self.assertRaises(inference.InferenceError): client.extract({}, None)
        self.assertTrue(client.diagnostic['completion_received'])
        self.assertEqual(client.diagnostic['category'], 'native_rejection')
        self.assertNotIn('private', json.dumps(client.diagnostic))

    def test_received_malformed_json_is_distinct_from_missing_completion(self):
        client=client_fixture([{'method':'thread/tokenUsage/updated','params':{'threadId':'thread','tokenUsage':{'last':USAGE}}},
            {'method':'item/completed','params':{'threadId':'thread','item':{'type':'agentMessage','text':'private invalid json'}}},completion()])
        with self.assertRaises(inference.EvidenceError): client.extract({}, None)
        self.assertEqual(client.diagnostic['stage'], 'output_parse')
        self.assertEqual(client.diagnostic['category'], 'invalid_output')
        self.assertTrue(client.diagnostic['completion_received'])
        self.assertEqual(client.metrics['token_usage'], USAGE)
        self.assertNotIn('private', json.dumps(client.diagnostic))

    def test_each_extract_resets_previous_native_facts(self):
        client=client_fixture(rejection=inference.InferenceError('private'))
        client.diagnostic={'stage':'output_parse','category':'invalid_output','turn_start_requested':True,
            'turn_started':True,'completion_received':True,'usage_known':True}
        with self.assertRaises(inference.InferenceError): client.extract({}, None)
        self.assertFalse(client.diagnostic['completion_received'])
        self.assertFalse(client.diagnostic['usage_known'])

    def test_diagnostic_rejects_non_scalar_enums_extra_content_and_fake_booleans(self):
        valid={'stage':'turn_wait','category':'unknown','turn_start_requested':True,
            'turn_started':True,'completion_received':False,'usage_known':False}
        for key,value in (('stage',[]),('category',{}),('usage_known',1),('private','payload')):
            bad={**valid,key:value}
            self.assertIsNone(executor.safe_diagnostic(bad))
        self.assertEqual(executor.safe_diagnostic(valid),valid)

    def test_failed_finish_does_not_mislabel_valid_output_and_ledger_is_unchanged(self):
        remote=Remote()
        base=remote.worker
        def worker(args):
            if args['action']=='finish': raise executor.ExecutorError('ambiguous_completion')
            return base(args)
        remote.worker=worker
        model=Model(remote)
        base_call=model.call
        def call(*args):
            output=base_call(*args)
            model.diagnostic={'stage':'output_parse','category':'unknown','turn_start_requested':True,
                'turn_started':True,'completion_received':True,'usage_known':True}
            return output
        model.call=call
        result=executor.Executor(remote,model,PROJECT,RUN).run()
        self.assertEqual(result['failure_code'],'ambiguous_completion')
        self.assertEqual(result['diagnostic']['category'],'unknown')
        self.assertEqual(result['diagnostic']['stage'],'output_validation')
        failure=[item for item in remote.actions if item['action']=='fail'][0]
        self.assertNotIn('diagnostic',failure)
        self.assertEqual(len(model.calls),1)

    def test_lost_next_attempt_cannot_reuse_previous_native_completion(self):
        remote=Remote(); model=Model(remote)
        base_worker=remote.worker
        def worker(args):
            if args['action']=='attempt' and remote.completed:
                raise executor.ExecutorError('ambiguous_completion')
            return base_worker(args)
        remote.worker=worker
        base_call=model.call
        def call(*args):
            output=base_call(*args)
            model.diagnostic={'stage':'output_parse','category':'unknown','turn_start_requested':True,
                'turn_started':True,'completion_received':True,'usage_known':True}
            return output
        model.call=call
        result=executor.Executor(remote,model,PROJECT,RUN).run()
        self.assertEqual(result['failure_code'],'ambiguous_completion')
        self.assertNotIn('diagnostic',result)
        self.assertEqual(len(model.calls),1)

    def test_configuration_failure_reports_setup_without_any_dispatch(self):
        remote=Remote(); model=Model(remote)
        def prepare():
            model.diagnostic={'stage':'configuration','category':'unknown','turn_start_requested':False,
                'turn_started':False,'completion_received':False,'usage_known':False}
            raise inference.InferenceError('private config detail')
        model.prepare=prepare
        result=executor.Executor(remote,model,PROJECT,RUN).run()
        self.assertEqual(result['failure_code'],'model_unavailable')
        self.assertEqual(result['diagnostic']['stage'],'configuration')
        self.assertNotIn('attempt',[item['action'] for item in remote.actions])
        self.assertNotIn('private',json.dumps(result))
