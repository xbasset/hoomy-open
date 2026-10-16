import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from hoomy.server import MAX_BODY, make_handler
from hoomy.store import Store


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(self.directory.name)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.store))
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.store.close()
        self.directory.cleanup()

    def request(self, path="/api/state", method="GET", payload=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        request_headers = dict(headers or {})
        body = None
        if payload is not None:
            body = json.dumps(payload)
            request_headers.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=body, headers=request_headers)
        response = conn.getresponse()
        result = (response.status, json.loads(response.read()), dict(response.getheaders()))
        conn.close()
        return result

    def test_state_private_no_cache(self):
        status, body, headers = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(body["projects"], [])
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_remote_host_and_origin_rejected(self):
        self.assertEqual(self.request(headers={"Host": "attacker.example"})[0], 403)
        self.assertEqual(self.request(headers={"Origin": "https://attacker.example"})[0], 403)
        self.assertEqual(self.request(headers={"Origin": "null"})[0], 403)
        self.assertEqual(self.request(headers={"Origin": "http://127.0.0.1:1"})[0], 403)
        self.assertEqual(self.request(headers={"Origin": "http://127.0.0.1:" + str(self.port)})[0], 200)

    def test_json_and_size_required(self):
        self.assertEqual(self.request("/api/control", "POST", {}, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("/api/control", "POST", {}, {"Content-Length": str(MAX_BODY + 1)})[0], 413)

    def test_missing_resources_and_wrong_method(self):
        self.assertEqual(self.request("/api/sessions/missing")[0], 404)
        self.assertEqual(self.request("/api/state?unexpected=1")[0], 404)
        self.assertEqual(self.request(method="DELETE")[0], 405)

    def test_control_consent_and_reset_endpoint(self):
        self.assertEqual(self.request("/api/control", "POST", {"running": True})[0], 409)
        status, body, _ = self.request("/api/control", "POST", {"running": True, "consent": True})
        self.assertEqual(status, 200)
        self.assertTrue(body["scope_confirmed"])
        status, body, _ = self.request("/api/reset", "POST", {"confirm": "DELETE_HOOMY_DATA"})
        self.assertEqual(status, 200)
        self.assertFalse(body["control"]["scope_confirmed"])

    def test_ready_routes_and_consent(self):
        self.assertEqual(self.request('/api/now')[1]['ideas'], [])
        self.assertEqual(self.request('/api/now/work')[1], None)
        self.assertEqual(self.request('/api/now/choose','POST',{'attention_budget':5})[0],409)
        self.assertEqual(self.request('/api/now/prepare','POST',{'attention_budget':5})[0],409)

    def test_act_routes_and_consent(self):
        status, body, _ = self.request("/api/act")
        self.assertEqual(status, 200)
        self.assertEqual(body["requests"], [])
        self.assertEqual(body["metrics"]["completed"], 0)
        self.assertEqual(self.request("/api/act/work")[1], None)
        self.assertEqual(self.request("/api/act/request", "POST", {"attention_budget": 30})[0], 409)
        self.assertEqual(self.request("/api/act/feedback", "POST", {"id": "missing", "decision": "done"})[0], 404)

    def test_errors_do_not_expose_exception_details(self):
        with patch.object(self.store, "state", side_effect=RuntimeError("PRIVATE")):
            status, body, _ = self.request()
        self.assertEqual(status, 500)
        self.assertNotIn("PRIVATE", json.dumps(body))


if __name__ == "__main__":
    unittest.main()
