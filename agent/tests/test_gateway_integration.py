"""Python agent against the real OAuth provider, with fixture browser identity."""

import base64
import hashlib
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import re
import select
import shutil
import ssl
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from urllib import error, parse, request

from hoomy_agent.client import Agent, AgentError, APIError, HTTP, NoRedirect
from hoomy_agent.storage import State
from hoomy_agent.outbox import Outbox


ROOT = Path(__file__).resolve().parents[2]
BUNDLED_NODE = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"


def node_runtime():
    candidates = [os.environ.get("HOOMY_TEST_NODE"), str(BUNDLED_NODE), shutil.which("node")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            version = subprocess.run([candidate, "--version"], capture_output=True, text=True, check=True).stdout
            if int(version.lstrip("v").split(".")[0]) >= 24:
                return candidate
    return None


class Browser:
    def __init__(self, origin, ca_file):
        self.origin = origin
        self.cookies = CookieJar()
        self.opener = request.build_opener(NoRedirect(), request.HTTPCookieProcessor(self.cookies), request.HTTPSHandler(context=ssl.create_default_context(cafile=ca_file)))

    def call(self, path, form=None):
        url = parse.urljoin(self.origin + "/", path)
        if parse.urlsplit(url).netloc != parse.urlsplit(self.origin).netloc:
            raise AssertionError("Fixture browser must not access an external provider")
        req = request.Request(url, data=parse.urlencode(form).encode() if form is not None else None)
        try:
            response = self.opener.open(req, timeout=10)
        except error.HTTPError as exc:
            response = exc
        with response:
            return response.status, response.headers, response.read().decode()

    @staticmethod
    def hidden(html):
        return dict(re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', html))

    def consent(self, response):
        for _ in range(20):
            status, headers, html = response
            if 300 <= status < 400:
                target = parse.urljoin(self.origin, headers["Location"])
                if parse.urlsplit(target).netloc != parse.urlsplit(self.origin).netloc:
                    return target
                response = self.call(target)
                continue
            if status != 200:
                raise AssertionError("Fixture approval failed with HTTP " + str(status))
            github = re.search(r'href="(/auth/github/start[^\"]+)"', html)
            if github:
                _, start_headers, _ = self.call(github[1])
                state = parse.parse_qs(parse.urlsplit(start_headers["Location"]).query)["state"][0]
                response = self.call("/auth/github/callback?" + parse.urlencode({"state": state, "code": "fixture-good"}))
                continue
            confirm = re.search(r'action="(/interaction/[^\"]+/confirm)"', html)
            if confirm:
                response = self.call(confirm[1], dict(self.hidden(html), decision="approve"))
                continue
            if "Machine autorisée" in html:
                return None
            raise AssertionError("Unexpected fixture browser page")
        raise AssertionError("Fixture approval exceeded redirect bound")

    def approve_device(self, user_code):
        status, _, html = self.call("/activate")
        if status != 200:
            raise AssertionError("Missing device input form")
        response = self.call("/activate", dict(self.hidden(html), user_code=user_code))
        status, _, html = response
        if status != 200 or user_code not in html:
            raise AssertionError("Browser code confirmation does not match terminal")
        response = self.call("/activate", self.hidden(html))
        return self.consent(response)


@unittest.skipUnless(node_runtime() and (ROOT / "services/hoomy-gateway/node_modules/oidc-provider").is_dir(), "Install gateway dependencies and use Node 24+ to run real gateway integration")
class GatewayIntegrationTests(unittest.TestCase):
    def test_real_collection_approval_capture_lost_ack_and_backend_receipt(self):
        with tempfile.TemporaryDirectory(prefix="hoomy-agent-ingestion-") as directory:
            root = Path(directory).resolve()
            processes = []
            backend = subprocess.Popen([sys.executable, "-u", str(ROOT / "agent/tests/backend-fixture.py"), str(root / "pilot")],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE, text=True,
                env={**os.environ, "PYTHONPATH": str(ROOT / "backend")})
            processes.append(backend)
            try:
                self.assertTrue(select.select([backend.stdout], [], [], 15)[0], "Backend fixture startup timed out")
                backend_info = json.loads(backend.stdout.readline())
                cert, key = root / "cert.pem", root / "key.pem"
                subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                gateway = subprocess.Popen([node_runtime(), str(ROOT / "agent/tests/gateway-fixture.mjs"), directory, backend_info["url"], backend_info["project_id"]], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                processes.append(gateway)
                self.assertTrue(select.select([gateway.stdout], [], [], 15)[0], "Gateway fixture startup timed out")
                server = json.loads(gateway.stdout.readline())["origin"]
                state = State(root / "agent-state", server, credential_backend=False)
                transport = HTTP(server, str(cert))
                agent = Agent(state, transport, sleep=lambda _: None)
                browser = Browser(server, str(cert))
                approve = lambda complete, uri, code, duration: browser.approve_device(code)
                device = agent.login(approve, "Fixture Mac")
                original_device = device["device_id"]
                history = root / ".codex/sessions"
                history.mkdir(parents=True)
                cwd = str(root / "work/alpha")
                records = [{"type": "session_meta", "payload": {"id": "fixture-session", "cwd": cwd}},
                    {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Français 😀 public fixture."}]}},
                    {"type": "response_item", "payload": {"type": "function_call_output", "output": "withheld tool fixture"}},
                    {"type": "response_item", "payload": {"type": "message", "role": "assistant", "channel": "analysis", "content": [{"type": "output_text", "text": "withheld reasoning fixture"}]}}]
                (history / "rollout-fixture.jsonl").write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records))
                with mock.patch("hoomy_agent.collector.read_source", side_effect=AssertionError("presence must not read bodies")):
                    agent.cycle()
                old_access = state.credentials()["access_token"]
                policy = {"content_tier": "conversation_visible_v1", "collection_enabled": True, "roots": [str(history)], "exclusions": [],
                          "project_bindings": [{"project_id": backend_info["project_id"], "cwd_roots": [cwd]}]}
                upgraded = agent.login(approve, collection_policy=policy)
                self.assertEqual(upgraded["device_id"], original_device)
                self.assertEqual(state.credentials()["scope"], "device:self evidence:ingest")
                with self.assertRaises(APIError) as refused:
                    transport.call("GET", "/v1/devices/self", token=old_access)
                self.assertEqual(refused.exception.status, 401)
                original_call = transport.call
                lost = [False]
                def lose_ack(method, url, payload=None, token=None, form=False, **kwargs):
                    result = original_call(method, url, payload, token, form, **kwargs)
                    if url.endswith("/v1/events") and not lost[0]:
                        lost[0] = True
                        raise AgentError("Fixture acknowledgement interrupted")
                    return result
                transport.call = lose_ack
                with self.assertRaises(AgentError):
                    agent.cycle()
                outbox = Outbox(state.path / "outbox")
                try:
                    event, digest = outbox.pending(2, agent.clock() + 6)[0]
                    self.assertEqual(len(event["projection"]["messages"]), 1)
                    self.assertNotIn("withheld", json.dumps(event))
                    receipt = original_call("GET", "/v1/receipts/" + event["event_id"], token=state.credentials()["access_token"])
                    self.assertEqual(receipt["event_sha256"], digest)
                    self.assertEqual(receipt["state"], "stored")
                    self.assertEqual(receipt["inference_state"], "not_requested")
                finally:
                    outbox.close()
                restored = Agent(state, HTTP(server, str(cert)), clock=lambda: __import__("time").time() + 6)
                restored.cycle()
                outbox = Outbox(state.path / "outbox")
                try:
                    self.assertEqual(outbox.counts()["outbox_count"], 0)
                finally:
                    outbox.close()
                profile = json.loads(request.urlopen(backend_info["url"] + "/api/projects/context/" + backend_info["project_id"]).read())
                cached = [source for source in profile["sources"] if source.get("cached")]
                self.assertEqual(len(cached), 1)
                self.assertEqual(cached[0]["device_id"], original_device)
                self.assertFalse(cached[0]["source_sha256_verified"])
                projected_event, projected_receipt = event, receipt
                records[0]["payload"]["source"] = {"subagent": {"other": "guardian"}}
                (history / "rollout-fixture.jsonl").write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records))
                real_exclusion_call = restored.http.call
                lost_exclusion = [False]
                def lose_exclusion_ack(method, url, payload=None, token=None, form=False, **kwargs):
                    result = real_exclusion_call(method, url, payload, token, form, **kwargs)
                    if method == "POST" and url.endswith("/v1/events") and payload.get("type") == "conversation.source_excluded" and not lost_exclusion[0]:
                        lost_exclusion[0] = True
                        raise AgentError("Fixture exclusion acknowledgement interrupted")
                    return result
                restored.http.call = lose_exclusion_ack
                with self.assertRaises(AgentError): restored.cycle()
                outbox = Outbox(state.path / "outbox")
                try:
                    excluded, excluded_digest = outbox.pending(2, restored.clock() + 6)[0]
                    self.assertEqual(excluded["type"], "conversation.source_excluded")
                    self.assertEqual(excluded["sequence"], projected_event["sequence"] + 1)
                    self.assertNotIn("projection", excluded)
                    self.assertNotIn("Français", json.dumps(excluded))
                    excluded_receipt = real_exclusion_call("GET", "/v1/receipts/" + excluded["event_id"], token=state.credentials()["access_token"])
                    self.assertEqual(excluded_receipt["event_sha256"], excluded_digest)
                    self.assertEqual(excluded_receipt["event_type"], excluded["type"])
                    self.assertEqual(excluded_receipt["header_sha256"], excluded["exclusion"]["header_sha256"])
                    self.assertNotIn("projected_sha256", excluded_receipt)
                    old_receipt = real_exclusion_call("GET", "/v1/receipts/" + projected_event["event_id"], token=state.credentials()["access_token"])
                    self.assertEqual(old_receipt, projected_receipt)
                finally:
                    outbox.close()
                recovered = Agent(state, HTTP(server, str(cert)), clock=lambda: __import__("time").time() + 12)
                recovered.cycle()
                outbox = Outbox(state.path / "outbox")
                try: self.assertEqual(outbox.counts()["outbox_count"], 0)
                finally: outbox.close()
                excluded_profile = json.loads(request.urlopen(backend_info["url"] + "/api/projects/context/" + backend_info["project_id"]).read())
                self.assertEqual([source for source in excluded_profile["sources"] if source.get("cached")], [])
            finally:
                for process in reversed(processes):
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    for stream in (process.stdout, process.stderr, process.stdin):
                        if stream:
                            stream.close()

    def test_real_device_approval_activation_heartbeat_refresh_and_revocation(self):
        with tempfile.TemporaryDirectory(prefix="hoomy-agent-gateway-") as directory:
            root = Path(directory)
            cert, key = root / "cert.pem", root / "key.pem"
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            process = subprocess.Popen([node_runtime(), str(ROOT / "agent/tests/gateway-fixture.mjs"), directory], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                ready = select.select([process.stdout], [], [], 15)[0]
                self.assertTrue(ready, "Gateway fixture startup timed out")
                line = process.stdout.readline()
                if not line:
                    self.fail("Gateway fixture did not start")
                server = json.loads(line)["origin"]
                state = State(root / "agent-state", server, credential_backend=False)
                transport = HTTP(server, str(cert))
                agent = Agent(state, transport)
                browser = Browser(server, str(cert))
                shown = []
                pending_seen = []
                original = transport.call

                def track(method, url, payload=None, token=None, form=False):
                    try:
                        return original(method, url, payload, token, form)
                    except APIError as exc:
                        if exc.code == "authorization_pending":
                            pending_seen.append(True)
                        raise

                transport.call = track
                def display(complete, uri, code, duration):
                    self.assertIn("user_code", complete)
                    self.assertNotIn("device_code", complete)
                    shown.append(code)

                # Approval happens only after the real provider reports pending.
                sleep_count = 0
                original_sleep = agent.sleep
                def wait(seconds):
                    nonlocal sleep_count
                    sleep_count += 1
                    if sleep_count == 2:
                        self.assertTrue(pending_seen)
                        browser.approve_device(shown[0])
                    original_sleep(seconds)
                agent.sleep = wait
                device = agent.login(display, "Fixture VPS")
                self.assertEqual(device["status"], "authorized")
                self.assertFalse(device["collection_available"])
                self.assertEqual(agent.status()["status"], "active")
                heartbeat = agent.heartbeat()
                self.assertEqual(heartbeat["device_id"], device["device_id"])
                self.assertFalse(heartbeat["collection_available"])
                old_refresh = state.credentials()["refresh_token"]
                credentials = state.credentials()
                credentials["expires_at"] = 0
                state.save_credentials(credentials)
                agent.heartbeat()
                self.assertNotEqual(state.credentials()["refresh_token"], old_refresh)

                # A real user grant revokes the device, independently of its tokens.
                discovery = transport.call("GET", "/.well-known/hoomy")
                user_resource = discovery["resources"]["projects"]
                client = transport.call("POST", "/oauth/register", {"client_name": "Fixture user client", "application_type": "native", "redirect_uris": ["http://127.0.0.1:4567/callback"], "token_endpoint_auth_method": "none"})
                verifier = "f" * 43
                challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
                parameters = {"client_id": client["client_id"], "response_type": "code", "redirect_uri": client["redirect_uris"][0], "resource": user_resource, "scope": "projects:read devices:manage", "state": "fixture-state", "code_challenge": challenge, "code_challenge_method": "S256"}
                callback = browser.consent(browser.call("/oauth/authorize?" + parse.urlencode(parameters)))
                returned = parse.parse_qs(parse.urlsplit(callback).query)
                self.assertEqual(returned["state"], ["fixture-state"])
                user = transport.call("POST", "/oauth/token", {"grant_type": "authorization_code", "client_id": client["client_id"], "redirect_uri": client["redirect_uris"][0], "code": returned["code"][0], "code_verifier": verifier, "resource": user_resource}, form=True)
                revoked = transport.call("POST", "/v1/devices/" + device["device_id"] + "/revoke", {}, token=user["access_token"])
                self.assertEqual(revoked["status"], "revoked")
                with self.assertRaises(AgentError):
                    agent.heartbeat()
                self.assertIn(state.credentials()["state"], ("revoked", "needs_login"))
                with self.assertRaisesRegex(AgentError, "requires login"):
                    agent.heartbeat()
                projects = transport.call("GET", "/v1/projects/fixture-project", token=user["access_token"])
                self.assertEqual(projects["revision"], "fixture-revision")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                process.stdout.close()
                process.stderr.close()


if __name__ == "__main__":
    unittest.main()
