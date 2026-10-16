import io
import json
import queue
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hoomy import state_executor as executor


PROJECT = '11111111-1111-4111-8111-111111111111'
RPC = {'operation': 'cached_tool', 'outcome': 'rpc_timeout', 'error_code': 'none',
       'duration_ms': 25000, 'timeout_ms': 25000, 'pending_before': 0, 'id_collision': False}


class FakeController:
    def __init__(self, *, initialization=False, dead=False):
        self.initialization, self.exit_status = initialization, 7 if dead else None
        self.rpc_local = SimpleNamespace(last={**RPC, 'operation': 'initialize' if initialization else 'cached_tool'})
        self.reader_terminal = 'eof' if dead else 'active'
        self.process = SimpleNamespace(poll=lambda: self.exit_status)
        self.closed = False

    def initialize(self, *, readiness_seconds=None):
        if self.initialization:
            raise executor.ExecutorError('ambiguous_completion')

    def close(self):
        self.closed = True
        self.exit_status = -15


class WatchDiagnosticsTests(unittest.TestCase):
    def main(self, controller):
        class FakeWatcher:
            successful_polls = 3
            def __init__(self, *_args, **_kwargs):
                pass
            def run(self):
                raise executor.ExecutorError('ambiguous_completion')
        output = io.StringIO()
        with patch.object(executor, 'native_binary', return_value='fictional'), \
                patch.object(executor, 'NativeController', return_value=controller), \
                patch.object(executor, 'Watcher', FakeWatcher), \
                patch.object(executor, 'RestrictedModel') as model, patch('sys.stdout', output):
            code = executor.main(['watch', '--project-id', PROJECT])
        self.assertEqual(code, 2)
        model.assert_not_called()
        self.assertTrue(controller.closed)
        return json.loads(output.getvalue())

    def test_initialize_failure_is_captured_before_cleanup(self):
        result = self.main(FakeController(initialization=True))
        detail = result['watch_diagnostic']
        self.assertEqual(detail['stage'], 'initialize')
        self.assertEqual(detail['successful_polls'], 0)
        self.assertTrue(detail['native_alive'])
        self.assertIsNone(detail['native_exit_status'])
        self.assertEqual(detail['rpc']['operation'], 'initialize')
        self.assertEqual(result['failure_code'], 'ambiguous_completion')

    def test_later_idle_poll_failure_has_observed_counter_and_live_timeout(self):
        detail = self.main(FakeController())['watch_diagnostic']
        self.assertEqual(detail['stage'], 'pending_poll')
        self.assertEqual(detail['successful_polls'], 3)
        self.assertEqual(detail['reader_terminal'], 'active')
        self.assertEqual(detail['rpc']['outcome'], 'rpc_timeout')
        self.assertTrue(detail['native_alive'])
        self.assertIsNone(detail['native_exit_status'])

    def test_real_exit_status_is_not_replaced_by_cleanup_signal(self):
        detail = self.main(FakeController(dead=True))['watch_diagnostic']
        self.assertFalse(detail['native_alive'])
        self.assertEqual(detail['native_exit_status'], 7)
        self.assertEqual(detail['reader_terminal'], 'eof')

    def test_unexpected_diagnostic_keys_values_and_types_are_not_emitted(self):
        for mutation in ({'secret': 'private-token'}, {'operation': ['private']}, {'duration_ms': True}, {'outcome': 'private-token'}):
            controller = FakeController()
            controller.rpc_local.last = {**RPC, **mutation}
            controller.reader_terminal = 'private-source-text'
            detail = self.main(controller)['watch_diagnostic']
            self.assertIsNone(detail['rpc'])
            self.assertEqual(detail['reader_terminal'], 'unknown')
            self.assertNotIn('private', json.dumps(detail))

    def test_successful_poll_counts_only_validated_metadata(self):
        calls = []
        controller = SimpleNamespace(pending=lambda _project: {'project_id': PROJECT, 'pending_run': None})
        watch = executor.Watcher(controller, lambda run: calls.append(run), PROJECT)
        self.assertIsNone(watch.step())
        self.assertEqual(watch.successful_polls, 1)
        controller.pending = lambda _project: {'project_id': PROJECT, 'pending_run': 'malformed'}
        with self.assertRaises(executor.ExecutorError):
            watch.step()
        self.assertEqual(watch.successful_polls, 1)
        self.assertEqual(calls, [])

    def test_reader_terminal_categories_preserve_refusal_and_eof_guards(self):
        for wire, reason in ((b'', 'eof'), (b'{bad}\n', 'invalid_json'), (b'[]\n', 'invalid_object'),
                (b'x' * 2000001, 'oversized_event'), (b'{"method":"private-capability","id":1}\n', 'server_request_refused')):
            controller = object.__new__(executor.NativeController)
            controller.process = SimpleNamespace(stdout=io.BytesIO(wire))
            controller.reader_terminal = 'active'
            controller.lock = threading.Lock()
            controller._waiters = {2: queue.Queue()}
            controller.notifications = queue.Queue()
            refused = []
            controller._send = refused.append
            controller._read()
            self.assertEqual(controller.reader_terminal, reason)
            self.assertIsNone(controller._waiters[2].get_nowait())
            self.assertEqual(bool(refused), reason == 'server_request_refused')
