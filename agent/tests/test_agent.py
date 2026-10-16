import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import io
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
import unittest
from unittest import mock
from urllib.parse import parse_qs

from hoomy_agent.client import Agent, AgentError, APIError, HTTP, origin
from hoomy_agent.service import install_service, launchagent_definition, systemd_unit
from hoomy_agent.storage import State, StorageError, read_private
from hoomy_agent.__main__ import main


class Clock:
    def __init__(self):
        self.now = 1000
        self.delays = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.delays.append(seconds)
        self.now += seconds


class HTTPSFixture:
    def __init__(self, directory):
        self.calls = []
        self.poll_errors = ["authorization_pending", "slow_down"]
        self.refresh_failure = False
        self.revoked = False
        cert, key = Path(directory) / "cert.pem", Path(directory) / "key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.handle_request()

            def do_POST(self):
                self.handle_request()

            def handle_request(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if "application/x-www-form-urlencoded" in self.headers.get("Content-Type", ""):
                    payload = {key: values[0] for key, values in parse_qs(body.decode()).items()}
                else:
                    payload = json.loads(body) if body else {}
                fixture.calls.append((self.command, self.path, payload, self.headers.get("Authorization")))
                status, response = fixture.response(self.path, payload)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if response is not None:
                    self.wfile.write(json.dumps(response).encode())

        self.server = ThreadingHTTPServer(("localhost", 0), Handler)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key)
        self.server.socket = tls.wrap_socket(self.server.socket, server_side=True)
        self.origin = "https://localhost:" + str(self.server.server_port)
        self.ca_file = str(cert)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def response(self, path, payload):
        if path == "/.well-known/hoomy":
            return 200, {"canonical_server_url": self.origin, "issuer": self.origin, "resources": {"collector": self.origin + "/v1/collector"}, "endpoints": {"enrolments": "/v1/enrolments", "device_authorization": "/oidc/device", "token": "/oidc/token", "revoke": "/oidc/revoke"}}
        if path == "/v1/enrolments":
            return 201, {"enrolment_id": "enrolment-1", "enrolment_ticket": "private-ticket", "policy_revision": 1}
        if path == "/oidc/device":
            return 200, {"device_code": "private-polling-code", "user_code": "ABCD-EFGH", "verification_uri": self.origin + "/activate", "verification_uri_complete": self.origin + "/activate?user_code=ABCD-EFGH", "expires_in": 600, "interval": 5}
        if path == "/oidc/token":
            if payload["grant_type"] == "refresh_token":
                if self.refresh_failure:
                    return 400, {"error": "invalid_grant", "error_description": "sensitive detail must not be printed"}
            elif self.poll_errors:
                failure = self.poll_errors.pop(0)
                return failure if isinstance(failure, tuple) else (400, {"error": failure})
            return 200, {"access_token": "private-access", "refresh_token": "private-refresh", "expires_in": 900, "token_type": "Bearer", "scope": "device:self"}
        if path == "/oidc/revoke":
            return 200, None
        if self.revoked:
            return 403, {"code": "device_revoked"}
        return 200, {"device_id": "device-1", "workspace_id": "workspace-1", "status": "active", "policy_revision": 1}

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class AgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_directory = tempfile.TemporaryDirectory()
        cls.https = HTTPSFixture(cls.fixture_directory.name)

    @classmethod
    def tearDownClass(cls):
        cls.https.close()
        cls.fixture_directory.cleanup()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = State(self.directory.name, self.https.origin, credential_backend=False)
        self.clock = Clock()
        self.agent = Agent(self.state, HTTP(self.https.origin, self.https.ca_file), self.clock.time, self.clock.sleep)
        self.https.calls.clear()
        self.https.poll_errors = ["authorization_pending", "slow_down"]
        self.https.refresh_failure = False
        self.https.revoked = False

    def login(self):
        displayed = []
        result = self.agent.login(lambda *arguments: displayed.append(arguments), "Test machine")
        self.assertEqual(result["device_id"], "device-1")
        self.assertEqual(self.clock.delays, [5, 5, 10])
        self.assertEqual(len(displayed), 1)
        self.assertNotIn("private", repr(displayed))
        return result

    def test_https_login_then_heartbeat_without_history_access(self):
        self.login()
        self.agent.heartbeat()
        call = self.https.calls[-1]
        self.assertEqual(call[1], "/v1/devices/self/heartbeat")
        self.assertFalse(call[2]["collection_enabled"])
        self.assertEqual(call[2]["outbox_count"], 0)
        proposal = next(call[2] for call in self.https.calls if call[1] == "/v1/enrolments")
        self.assertEqual(proposal["proposed_policy"]["roots"], [])
        credentials = Path(self.state.path) / "credentials.json"
        self.assertEqual(credentials.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("private-access", json.dumps(self.state.config()))

    def test_rerun_preserves_device_and_installation(self):
        self.login()
        installation = self.state.installation_id()
        self.https.calls.clear()
        self.agent.login(lambda *args: self.fail("No approval expected"))
        self.assertFalse(any(call[1] == "/v1/enrolments" for call in self.https.calls))
        self.assertEqual(self.state.installation_id(), installation)

    def test_denial_and_interrupt_do_not_store_credentials(self):
        self.https.poll_errors = ["access_denied"]
        with self.assertRaisesRegex(AgentError, "denied"):
            self.agent.login(lambda *args: None)
        self.assertIsNone(self.state.credentials())
        with self.assertRaises(KeyboardInterrupt):
            self.agent.login(lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()))
        self.assertIsNone(self.state.credentials())

    def test_expiry_stops_polling(self):
        self.https.poll_errors = ["authorization_pending"] * 1000
        with self.assertRaisesRegex(AgentError, "expired"):
            self.agent.login(lambda *args: None)
        self.assertLessEqual(self.clock.now, 1600)
        self.assertIsNone(self.state.credentials())

    def test_device_polling_survives_temporary_gateway_restart(self):
        self.https.poll_errors = [(502, {"error": "request_failed"}), (503, {"error": "unavailable"}), (504, {"error": "timeout"}), "authorization_pending"]
        displayed = []
        device = self.agent.login(lambda *args: displayed.append(args))
        self.assertEqual(device["device_id"], "device-1")
        self.assertEqual(self.clock.delays, [5, 10, 20, 40, 40])
        self.assertEqual(len(displayed), 1)
        self.assertEqual(self.state.credentials()["state"], "authorized")

    def test_persistent_gateway_failure_obeys_device_deadline(self):
        self.https.poll_errors = [(502, {"error": "request_failed"})] * 1000
        with self.assertRaisesRegex(AgentError, "expired"):
            self.agent.login(lambda *args: None)
        self.assertEqual(self.clock.now, 1600)
        self.assertLessEqual(max(self.clock.delays), 60)
        self.assertIsNone(self.state.credentials())

    def test_device_polling_does_not_retry_permanent_http_error(self):
        self.https.poll_errors = [(403, {"error": "forbidden"})]
        with self.assertRaises(APIError) as refused:
            self.agent.login(lambda *args: None)
        self.assertEqual(refused.exception.status, 403)
        self.assertEqual(self.clock.delays, [5])
        self.assertIsNone(self.state.credentials())

    def test_interrupted_refresh_cannot_replay_old_token(self):
        self.login()
        value = self.state.credentials()
        value["state"] = "refresh_in_progress"
        self.state.save_credentials(value)
        self.https.calls.clear()
        with self.assertRaisesRegex(AgentError, "requires login"):
            self.agent.heartbeat()
        self.assertEqual(self.https.calls, [])

    def test_injected_renewal_clock_persists_real_time_expiry(self):
        self.login()
        before = self.state.credentials()
        renewal_clock = mock.Mock(side_effect=[before["expires_at"] + 1, self.clock.now])
        client = Agent(self.state, HTTP(self.https.origin, self.https.ca_file), clock=renewal_clock)
        self.https.calls.clear()
        device = client.status()
        self.assertEqual(device["device_id"], "device-1")
        self.assertEqual(renewal_clock.call_count, 2)
        self.assertEqual(self.state.credentials()["expires_at"], self.clock.now + 900)
        refreshes = [payload for _, path, payload, _ in self.https.calls if path == "/oidc/token"]
        self.assertEqual(len(refreshes), 1)
        self.assertEqual(refreshes[0]["grant_type"], "refresh_token")

    def test_refresh_refusal_does_not_expose_details_or_retry(self):
        self.login()
        self.clock.now += 1000
        self.https.refresh_failure = True
        with self.assertRaisesRegex(AgentError, "renewal was refused") as raised:
            self.agent.heartbeat()
        self.assertNotIn("private", str(raised.exception))
        self.assertEqual(self.state.credentials()["state"], "needs_login")
        count = len(self.https.calls)
        with self.assertRaises(AgentError):
            self.agent.heartbeat()
        self.assertEqual(len(self.https.calls), count)

    def test_refresh_is_serialized_between_cli_and_service(self):
        self.login()
        self.clock.now += 1000
        results = []
        def heartbeat():
            results.append(self.agent.heartbeat())
        threads = [threading.Thread(target=heartbeat) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        refreshes = [call for call in self.https.calls if call[2].get("grant_type") == "refresh_token"]
        self.assertEqual(len(refreshes), 1)
        self.assertEqual(len(results), 2)

    def test_revocation_stops_device(self):
        self.login()
        self.https.revoked = True
        with self.assertRaises(APIError):
            self.agent.heartbeat()
        self.assertEqual(self.state.credentials()["state"], "revoked")

    def test_logout_accepts_empty_revocation_response(self):
        self.login()
        self.assertTrue(self.agent.logout())
        self.assertIsNone(self.state.credentials())

    def test_tls_and_cross_origin_credentials_are_not_bypassed(self):
        with self.assertRaises(AgentError):
            HTTP(self.https.origin).call("GET", "/.well-known/hoomy")
        with self.assertRaises(AgentError):
            HTTP(self.https.origin, self.https.ca_file).call("POST", "https://other.example/token", {"refresh_token": "secret"}, form=True)
        with self.assertRaises(AgentError):
            origin("http://my.hoomy.dev")

    def test_private_state_refuses_symlinks_and_world_readable_files(self):
        target = Path(self.directory.name) / "target"
        target.write_text("{}")
        (self.state.path / "credentials.json").symlink_to(target)
        with self.assertRaises(StorageError):
            self.state.credentials()
        (self.state.path / "credentials.json").unlink()
        (self.state.path / "credentials.json").write_text("{}")
        (self.state.path / "credentials.json").chmod(0o644)
        with self.assertRaises(StorageError):
            self.state.credentials()

    def test_service_definitions_include_no_credentials(self):
        self.login()
        self.assertNotIn("private-access", systemd_unit(self.state))
        self.assertNotIn("private-refresh", repr(launchagent_definition(self.state)))
        self.assertIn("RestartPreventExitStatus=3", systemd_unit(self.state))

    def test_mac_keychain_identity_isolated_by_server_and_state_directory(self):
        with mock.patch("hoomy_agent.storage.sys.platform", "darwin"), mock.patch("hoomy_agent.storage.Keychain") as keychain:
            State(Path(self.directory.name) / "a", "https://one.example")
            State(Path(self.directory.name) / "b", "https://one.example")
            State(Path(self.directory.name) / "a", "https://two.example")
        self.assertEqual(len({call.args[0] for call in keychain.call_args_list}), 3)

    def test_linux_without_linger_is_not_reported_installed(self):
        calls = []
        def runner(arguments):
            calls.append(arguments)
            return "no"
        with self.assertRaisesRegex(AgentError, "lingering"):
            install_service(self.state, runner=runner, home=self.directory.name, platform="linux", user_service=True)
        self.assertFalse(any(arguments[0] == "systemctl" for arguments in calls))

    def test_linux_service_checks_linger_and_active_state(self):
        calls = []
        def runner(arguments):
            calls.append(arguments)
            return "yes" if arguments[0] == "loginctl" else ""
        result = install_service(self.state, runner=runner, home=self.directory.name, platform="linux", user_service=True)
        self.assertTrue(result["survives_ssh_logout"])
        self.assertEqual(calls[-1][:4], ["systemctl", "--user", "is-active", "--quiet"])

    def test_one_shot_heartbeat_failure_has_nonzero_exit_on_mac_and_linux(self):
        for platform in ("darwin", "linux"):
            for credential_state in ("authorized", "revoked"):
                with self.subTest(platform=platform, state=credential_state):
                    state = mock.Mock()
                    state.config.return_value = {}
                    state.locked.return_value = contextlib.nullcontext()
                    state.credentials.return_value = {"state": credential_state}
                    connection = mock.Mock()
                    connection.cycle.side_effect = AgentError("Fixture connection failure")
                    with mock.patch("hoomy_agent.__main__.State", return_value=state), mock.patch("hoomy_agent.__main__.Agent", return_value=connection), mock.patch("hoomy_agent.__main__.HTTP"), mock.patch("hoomy_agent.__main__.signal.signal"), mock.patch("hoomy_agent.__main__.sys.platform", platform), contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(main(["run", "--once"]), 1)
                    connection.cycle.assert_called_once()


if __name__ == "__main__":
    unittest.main()
