"""Transport diagnostics must be useful without leaking provider or input text."""
import http.client
import json
import unittest
from unittest.mock import Mock, patch

from hoomy.decisions import DecisionsClient, DecisionError, MODEL, failure_details


class DecisionsTests(unittest.TestCase):
    def setUp(self):
        self.api=DecisionsClient('fictional-test-key')
        self.connection=Mock()
        self.api.connection=self.connection
        self.questions=[{'name':'pick','type':'choice','instructions':'Choose one fictional option.',
                         'choices':[{'value':'a'},{'value':'b'}]}]

    def response(self):
        self.connection.getresponse.return_value.status=200
        self.connection.getresponse.return_value.read.return_value=json.dumps({
            'model':MODEL,'answers':[{'name':'pick','type':'choice','choice':'a','confidence':.9,
                                     'probabilities':[{'value':'a','probability':.95},{'value':'b','probability':.05}]}]}).encode()

    def test_timeout_and_connection_errors_have_safe_distinct_codes(self):
        for error,code in [(TimeoutError('private request text'),'timeout'),
                           (http.client.RemoteDisconnected('private provider text'),'connection')]:
            with self.subTest(code=code):
                self.connection.request.side_effect=error
                with self.assertRaises(DecisionError) as raised:self.api.evaluate('fictional input',self.questions)
                self.assertEqual(failure_details(raised.exception),{'code':code})
                self.assertNotIn('private',str(raised.exception))
                self.connection.close.assert_called()

    def test_http_failure_keeps_status_but_discards_provider_body(self):
        self.connection.getresponse.return_value.status=429
        self.connection.getresponse.return_value.read.return_value=b'private provider detail'
        with self.assertRaises(DecisionError) as raised:self.api.evaluate('fictional input',self.questions)
        self.assertEqual(failure_details(raised.exception),{'code':'rate_limit','http_status':429})
        self.assertNotIn('private',str(raised.exception))

    def test_idle_connection_is_closed_before_reuse(self):
        self.response();self.api.last_used=1
        with patch('hoomy.decisions.time.monotonic',return_value=50):
            self.api.evaluate('fictional input',self.questions)
        self.assertEqual(self.connection.method_calls[0][0],'close')

    def test_unexpected_errors_do_not_log_arbitrary_fields(self):
        self.assertEqual(failure_details(ValueError('private secret')),{'code':'worker_error'})
