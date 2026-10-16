import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hoomy import state_executor as executor


class ReadinessTests(unittest.TestCase):
    def fake(self, duration):
        clock = [100.0]
        client = object.__new__(executor.NativeController)
        client.runtime = '/private/tmp/fictional-controller'
        calls, notices = [], []
        def request(method, _params, **options):
            calls.append((method, options))
            clock[0] += duration if method == 'initialize' else 0
            return {'thread': {'ephemeral': True, 'id': 'fictional'}}
        client.request = request
        client._send = lambda value, **options: notices.append((value, options))
        return client, clock, calls, notices

    def test_watch_shares_one_absolute_budget_and_preserves_thread_cap(self):
        for elapsed, expected in ((10, 25), (30, 10)):
            client, clock, calls, _ = self.fake(elapsed)
            with patch.object(executor.time, 'monotonic', side_effect=lambda: clock[0]):
                client.initialize(readiness_seconds=40)
            self.assertEqual(calls[0][1], {'timeout': 40, 'readiness_deadline': 140})
            self.assertEqual(calls[1][1], {'timeout': expected, 'readiness_deadline': 140})
            self.assertEqual(len(calls), 2)

    def test_exhausted_budget_stops_before_notification_or_thread_dispatch(self):
        client, clock, calls, notices = self.fake(40)
        with patch.object(executor.time, 'monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(executor.ExecutorError):
                client.initialize(readiness_seconds=40)
        self.assertEqual([name for name, _ in calls], ['initialize'])
        self.assertEqual(notices, [])

    def test_default_manual_initialization_keeps_existing_request_defaults(self):
        client, clock, calls, _ = self.fake(10)
        with patch.object(executor.time, 'monotonic', side_effect=lambda: clock[0]):
            client.initialize()
        self.assertEqual([options for _, options in calls], [{}, {}])

    def test_deadline_is_rechecked_after_serialization_before_write(self):
        client = object.__new__(executor.NativeController)
        import threading
        client.write_lock = threading.Lock()
        wire = io.BytesIO()
        client.process = SimpleNamespace(stdin=wire)
        clock = [139.0]
        original = executor.canonical
        def encoding(value):
            result = original(value)
            clock[0] = 140.0
            return result
        with patch.object(executor, 'canonical', side_effect=encoding), \
                patch.object(executor.time, 'monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(executor.ExecutorError):
                client._send({'method': 'initialized', 'params': {}}, readiness_deadline=140)
        self.assertEqual(wire.getvalue(), b'')

    def test_thread_ack_processed_after_absolute_deadline_cannot_mark_ready(self):
        client, clock, calls, _ = self.fake(30)
        original = client.request
        def late(method, params, **options):
            result = original(method, params, **options)
            if method == 'thread/start':
                clock[0] = 140.0
            return result
        client.request = late
        with patch.object(executor.time, 'monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(executor.ExecutorError):
                client.initialize(readiness_seconds=40)
        self.assertEqual(len(calls), 2)
        self.assertFalse(hasattr(client, 'thread_id'))

    def test_waiter_delivery_after_deadline_is_closed_timeout_not_success(self):
        import threading
        client = object.__new__(executor.NativeController)
        client.lock = threading.Lock()
        client._waiters, client.serial = {}, 0
        client.rpc_local, client.rpc_observer = SimpleNamespace(), None
        client._send = lambda *_args, **_kwargs: None
        clock = [100.0]
        class LateWaiter:
            def __init__(self, **_kwargs):
                pass
            def get(self, *, timeout):
                clock[0] = 140.0
                return {'result': {}}
        with patch.object(executor.queue, 'Queue', LateWaiter), \
                patch.object(executor.time, 'monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(executor.ExecutorError):
                client.request('initialize', {}, timeout=40, readiness_deadline=140)
        self.assertEqual(client.rpc_local.last['outcome'], 'rpc_timeout')
        self.assertEqual(client._waiters, {})

    def test_noncontract_budgets_cannot_widen_startup(self):
        for budget in (True, 41, 400, '40'):
            client, _, calls, _ = self.fake(0)
            with self.assertRaises(executor.ExecutorError):
                client.initialize(readiness_seconds=budget)
            self.assertEqual(calls, [])
