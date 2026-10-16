"""Explicit approval, preserved old grants and no permission gain on refresh."""

import tempfile
from pathlib import Path
import unittest

from hoomy_agent.client import Agent, AgentError, APIError
from hoomy_agent.policy import canonical
from hoomy_agent.storage import State


class AuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = State(self.root / "state", "https://server.example", credential_backend=False)
        self.now = 1000
        self.policy = {"content_tier": "conversation_visible_v1", "collection_enabled": True,
            "roots": [str(self.root / ".codex/sessions")], "exclusions": [],
            "project_bindings": [{"project_id": "project-alpha", "cwd_roots": [str(self.root / "work/alpha")]}]}
        self.endpoints = {name: "/v1/" + name for name in ("enrolments", "device_authorization", "token", "policy_proposals", "events", "receipts")}
        self.state.save_config({"discovery": {"resource": "https://server.example/v1/collector", "endpoints": self.endpoints},
                                "device_id": "device-fixture", "policy_revision": 1, "collection_enabled": False})
        self.state.save_credentials({"state": "authorized", "access_token": "fixture-old-access", "refresh_token": "fixture-old-refresh", "expires_at": 10000})
        self.calls = []
        self.denied = False
        self.scope = "device:self evidence:ingest"
        self.agent = Agent(self.state, self, clock=lambda: self.now, sleep=self.sleep)

    def sleep(self, delay):
        self.now += delay

    def trusted_url(self, value):
        return value if value.startswith("https://") else "https://server.example" + value

    def call(self, method, url, payload=None, token=None, form=False, **kwargs):
        path = url.replace("https://server.example", "")
        self.calls.append((method, path, payload))
        if path == "/.well-known/hoomy":
            return {"canonical_server_url": "https://server.example", "resources": {"collector": "https://server.example/v1/collector"}, "endpoints": self.endpoints}
        if path == "/v1/policy_proposals":
            self.assertEqual(payload, {"proposed_policy": self.policy})
            return {"enrolment_ticket": "fixture-ticket", "enrolment_id": "proposal-fixture", "policy_revision": 2}
        if path == "/v1/device_authorization":
            self.assertEqual(set(payload["scope"].split()), {"device:self", "evidence:ingest"})
            return {"device_code": "fixture-device-code", "user_code": "ABCD-EFGH", "verification_uri": "https://server.example/activate", "verification_uri_complete": "https://server.example/activate?user_code=ABCD-EFGH", "expires_in": 600, "interval": 5}
        if path == "/v1/token":
            if self.denied:
                raise APIError(400, "access_denied")
            return {"access_token": "fixture-new-access", "refresh_token": "fixture-new-refresh", "scope": self.scope, "expires_in": 900, "token_type": "Bearer"}
        if path == "/v1/devices/self":
            enabled = "evidence:ingest" in (self.state.credentials() or {}).get("scope", "").split()
            return {"device_id": "device-fixture", "policy_revision": 2 if enabled else 1,
                    "collection_available": enabled, "policy": self.policy if enabled else {"content_tier": "metadata"}}
        return {"device_id": "device-fixture"}

    def test_explicit_proposal_obtains_new_scopes_and_server_owned_policy(self):
        seen = []
        self.agent.login(lambda *args: seen.append(args), collection_policy=self.policy)
        self.assertEqual(len(seen), 1)
        self.assertEqual(self.state.credentials()["scope"], "device:self evidence:ingest")
        self.assertEqual(self.state.config()["approved_policy"], self.policy)
        self.assertEqual(self.state.config()["policy_revision"], 2)
        self.assertFalse((self.state.path / "outbox").exists())

    def test_denied_proposal_preserves_prior_authorization_and_config(self):
        before_credentials, before_config = canonical(self.state.credentials()), canonical(self.state.config())
        self.denied = True
        with self.assertRaises(AgentError):
            self.agent.login(lambda *args: None, collection_policy=self.policy)
        self.assertEqual(canonical(self.state.credentials()), before_credentials)
        self.assertEqual(canonical(self.state.config()), before_config)

    def test_ordinary_login_retains_presence_without_scope_request(self):
        self.agent.login(lambda *args: self.fail("ordinary upgrade must not request a code"))
        self.assertNotIn("scope", self.state.credentials())
        self.assertFalse(self.state.config()["collection_enabled"])
        self.assertNotIn("/v1/policy_proposals", [call[1] for call in self.calls])

    def test_presence_refresh_cannot_gain_ingestion_scope(self):
        credentials = self.state.credentials()
        credentials["expires_at"] = 0
        self.state.save_credentials(credentials)
        with self.assertRaises(AgentError):
            self.agent.heartbeat()
        self.assertEqual(self.state.credentials()["state"], "needs_login")
        self.assertNotIn("scope", self.state.credentials())


if __name__ == "__main__":
    unittest.main()
