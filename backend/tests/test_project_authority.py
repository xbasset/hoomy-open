"""A local project fence preserves central context while other projects stay writable."""

import json
import http.client
import importlib.util
from pathlib import Path
import threading
import unittest
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

import test_store as fixtures
from hoomy.recognition import assign
from hoomy.store import Store, StoreError, encode, now
from hoomy.server import make_handler


class ProjectAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.StoreTests()
        self.fixture.setUp()
        self.store = self.fixture.store
        self.pid = self.fixture.analyzed()
        self.fixture.ingest(sid="other-source", snapshot="other-version")
        self.store.analysis("other-source", self.fixture.analysis_payload(snapshot="other-version", name="Other project"))
        self.other = next(p["id"] for p in self.store.project_list()["projects"] if p["id"] != self.pid)
        self.store.control({"running": False})

    def tearDown(self):
        self.fixture.tearDown()

    def fence(self):
        return self.store.configure_project_authority(self.pid, "https://my.hoomy.dev")

    def denied(self, operation):
        with self.assertRaises(StoreError) as caught:
            operation()
        self.assertEqual(caught.exception.status, 409)
        self.assertIn("centralisé", str(caught.exception))

    def test_default_absent_and_operator_only_fence_preserves_context_revision_and_control(self):
        self.assertIsNone(self.store.db.execute("SELECT value FROM settings WHERE key='project_authorities'").fetchone())
        before = self.store.dialogue.profile(self.pid)
        control = self.store._get("control")
        self.fence()
        after = self.store.dialogue.profile(self.pid)
        self.assertEqual(after["revision"], before["revision"])
        self.assertEqual(after["fields"], before["fields"])
        self.assertEqual(after["authority"], {"mode": "central_read_only", "origin": "https://my.hoomy.dev"})
        self.assertEqual(self.store._get("control"), control)
        self.denied(lambda: self.store.dialogue.update_profile({"project_id": self.pid, "revision": after["revision"], "fields": {"goal": "Forbidden local change."}}))
        other = self.store.dialogue.profile(self.other)
        saved = self.store.dialogue.update_profile({"project_id": self.other, "revision": other["revision"], "fields": {"goal": "Other project stays locally writable."}})
        self.assertEqual(saved["fields"]["goal"]["answer"], "Other project stays locally writable.")
        self.store.close()
        self.store = self.fixture.store = Store(self.fixture.directory.name)
        self.assertEqual(self.store.dialogue.profile(self.pid)["authority"], after["authority"])

    def test_identity_merge_both_sides_split_and_redirect_cannot_bypass_fence(self):
        with self.store.db:
            self.store.db.execute("INSERT INTO projects VALUES (?,?,?, ?,?,?)", ("redirect-source", encode({"name": "Redirect"}), "{}", self.pid, now(), now()))
        self.fence()
        for operation in (lambda: self.store.correct(self.pid, {"name": "Forbidden rename"}),
                          lambda: self.store.correct("redirect-source", {"purpose": "Forbidden purpose"}),
                          lambda: self.store.merge({"source_ids": [self.pid], "target_id": self.other}),
                          lambda: self.store.merge({"source_ids": [self.other], "target_id": "redirect-source"}),
                          lambda: self.store.split("redirect-source", {"session_ids": ["s"], "name": "Split", "purpose": ""}),
                          lambda: self.store.reset({"confirm": "DELETE_HOOMY_DATA"})):
            self.denied(operation)
        self.store.correct(self.other, {"name": "Allowed other rename"})
        self.assertEqual(self.store.dialogue.profile(self.other)["project"]["name"], "Allowed other rename")
        self.assertEqual(self.store._resolve("redirect-source")["id"], self.pid)
        self.assertEqual(self.store.dialogue.profile(self.pid)["project"]["name"], "A")

    def test_manual_source_reassignment_into_or_out_of_fenced_project_is_atomic(self):
        with self.store.db:
            for sid in ("s", "other-source"):
                self.store.db.execute("INSERT INTO catalog VALUES (?,?,?)", (sid, json.dumps({"session_id": sid}), "fixture"))
        self.fence()
        before = [tuple(row) for row in self.store.db.execute("SELECT * FROM associations ORDER BY session_id")]
        self.denied(lambda: assign(self.store, "s", {"decision": "project", "project_id": self.other}))
        self.denied(lambda: assign(self.store, "s", {"decision": "none"}))
        self.denied(lambda: assign(self.store, "other-source", {"decision": "project", "project_id": self.pid}))
        self.assertEqual([tuple(row) for row in self.store.db.execute("SELECT * FROM associations ORDER BY session_id")], before)

    def test_pending_guided_context_answer_cannot_write_a_fenced_project(self):
        self.fence()
        request = {"id": "pending-context-request", "created_at": now(),
            "research": {"question": {"project_id": self.pid, "dimension": "goal", "prompt": "What outcome?"}}}
        self.denied(lambda: self.store.dialogue.answer(request, {"answer": "Forbidden local guided goal."}))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM project_context_answers").fetchone()[0], 0)

    def test_manual_outline_review_cannot_rename_deactivate_or_retarget_fenced_project(self):
        with self.store.db:
            self.store.db.execute("INSERT INTO outlines VALUES (?,?,?,?,?)", ("fenced-outline",
                encode({"name": "Proposed name", "purpose": "Proposed purpose", "existing_project_id": self.pid,
                        "session_ids": ["s"]}), "pending", None, now()))
        self.fence()
        for payload in ({"decision": "keep", "name": "Forbidden outline rename"},
                        {"decision": "inactive"}, {"decision": "skip"},
                        {"decision": "keep", "project_id": self.other}):
            self.denied(lambda payload=payload: self.store.overview.review("fenced-outline", payload))
        self.assertEqual(self.store.db.execute("SELECT decision FROM outlines WHERE id='fenced-outline'").fetchone()[0], "pending")
        self.assertEqual(self.store.dialogue.profile(self.pid)["project"]["activity"], "active")

    def test_operator_origin_rejects_credentials_and_non_https_without_mutation(self):
        for origin in ("http://my.hoomy.dev", "https://secret:password@my.hoomy.dev", "https://my.hoomy.dev/path"):
            with self.assertRaises(StoreError):
                self.store.configure_project_authority(self.pid, origin)
        self.assertIsNone(self.store.db.execute("SELECT value FROM settings WHERE key='project_authorities'").fetchone())
        self.fence()
        self.store.configure_project_authority(self.pid, None)
        self.assertNotIn("authority", self.store.dialogue.profile(self.pid))
        self.store.correct(self.pid, {"name": "Local again"})

    def business_rows(self):
        tables = [row[0] for row in self.store.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('settings','sqlite_sequence')")]
        return {name: [tuple(row) for row in self.store.db.execute('SELECT * FROM "' + name + '" ORDER BY rowid')] for name in tables}

    def test_global_operator_archive_is_idempotent_and_preserves_scope_and_project_fences(self):
        self.fence()
        profile = self.store.dialogue.profile(self.other)
        request = {"id":"historical-ready", "generation":self.store._get("control")["generation"], "status":"ready", "created_at":now(), "candidates":[]}
        with self.store.db:
            self.store.db.execute("INSERT INTO act_requests VALUES (?,?,?,?,?,?)", (request["id"],request["generation"],"ready",request["created_at"],None,encode(request)))
        before = self.business_rows()
        control = self.store._get("control")
        per_project = self.store._get("project_authorities")
        authority = self.store.configure_instance_authority("https://my.hoomy.dev/")
        self.assertEqual(self.store.configure_instance_authority("https://my.hoomy.dev"), authority)
        self.assertEqual(self.business_rows(), before)
        self.assertEqual(self.store._get("project_authorities"), per_project)
        self.assertEqual(self.store._get("control"), dict(control, running=False, inference_enabled=False))
        current = self.store.dialogue.profile(self.other)
        self.assertEqual(current["authority"], authority)
        self.assertEqual(current["revision"], profile["revision"])
        self.assertEqual(current["fields"], profile["fields"])
        self.assertEqual(self.store.project_authority("future-project-not-created"), authority)
        self.denied(lambda: self.store.require_project_writable("future-project-not-created"))
        self.denied(lambda: self.store.correct(self.other, {"name": "Forbidden local identity"}))
        self.store.close()
        with patch('hoomy.ideas.Ideas.ingest', side_effect=AssertionError("Archive startup must not import suggestions")):
            self.store = self.fixture.store = Store(self.fixture.directory.name)
        self.assertEqual(self.business_rows(), before)
        self.assertEqual(self.store.health()["authority"], authority)
        self.assertIsNone(self.store.configure_instance_authority(None))
        self.assertIsNone(self.store.configure_instance_authority(None))
        self.assertEqual(self.store._get("project_authorities"), per_project)
        self.denied(lambda: self.store.correct(self.pid, {"name": "Still individually fenced"}))
        self.store.correct(self.other, {"name": "Explicit operator rollback"})

    def test_global_operator_origin_and_malformed_configuration_fail_closed(self):
        for origin in ("http://my.hoomy.dev", "https://secret@my.hoomy.dev", "https://my.hoomy.dev/path", "https://my.hoomy.dev?x=1", "https://my.hoomy.dev/#x", "https://my.hoomy.dev:bad", "https://my.hoomy.dev\\bad", "https://my.hoomy.dev bad"):
            with self.assertRaises(StoreError): self.store.configure_instance_authority(origin)
        self.assertIsNone(self.store.instance_authority())
        with self.store.db:
            self.store.db.execute("INSERT INTO settings VALUES ('instance_authority',?)", ('{"mode":"unknown","origin":"https://my.hoomy.dev"}',))
        with self.assertRaises(StoreError) as caught: self.store.require_instance_writable()
        self.assertEqual(caught.exception.status, 503)
        self.store.configure_instance_authority(None)

    def test_global_http_archive_blocks_every_write_before_body_parsing_and_preserves_reads(self):
        request = {"id":"expired-running", "generation":self.store._get("control")["generation"], "status":"running", "created_at":now(), "instruction":"Fixture research", "attention_budget":5, "exclusions":[], "sources":[], "candidates":[], "selected_id":None, "projects":[]}
        with self.store.db:
            self.store.db.execute("INSERT INTO act_requests VALUES (?,?,?,?,?,?)", (request["id"],request["generation"],"running",request["created_at"],0,encode(request)))
            job = {"id":"dependent-job", "status":"preparing", "kind":"preparing", "preparation_id":request["id"], "created_at":now()}
            self.store.db.execute("INSERT INTO idea_jobs VALUES (?,?,?,?)", (job["id"],job["status"],0,encode(job)))
        self.store.configure_instance_authority("https://my.hoomy.dev")
        before = self.business_rows()
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.store))
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(path, method="GET"):
            client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
            # A non-JSON body proves the authority guard precedes parsing.
            client.request(method, path, body=b"not JSON" if method == "POST" else None, headers={"Content-Type": "text/plain"})
            response = client.getresponse()
            result = response.status, json.loads(response.read())
            client.close()
            return result
        try:
            for path in ("/api/control", "/api/reset", "/api/worker", "/api/ingest", "/api/discovery", "/api/projects/context", "/api/projects/merge", "/api/projects/future/correct", "/api/agent/publish", "/api/agent/checkpoint", "/api/now/answer", "/api/now/claim", "/api/now/finish", "/api/act/result", "/api/act/feedback", "/api/ingestion/events", "/api/ingestion/receipts/query", "/api/ingestion/evidence/query", "/api/configure-instance-authority", "/future-write-route"):
                with self.subTest(path=path): self.assertEqual(request(path, "POST")[0], 409)
            for path in ("/api/act", "/api/act/work", "/api/now", "/api/now/work", "/api/now/question-work", "/api/catalog", "/api/act/explore/expired-running"):
                self.assertEqual(request(path)[0], 409)
            for path in ("/api/health", "/api/state", "/api/projects", "/api/projects/context/" + self.other, "/api/sessions/s", "/api/snapshots/" + self.fixture.digest("v1")):
                status, body = request(path)
                self.assertEqual(status, 200)
                if path in ("/api/health", "/api/state", "/api/projects/context/" + self.other): self.assertEqual(body["authority"]["mode"], "central_read_only")
            self.assertEqual(self.business_rows(), before)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_launch_utility_refuses_archive_before_build_or_after_read_backend_start(self):
        path = Path(__file__).resolve().parents[1] / "run.py"
        spec = importlib.util.spec_from_file_location("hoomy_run_fixture", path)
        launcher = importlib.util.module_from_spec(spec); spec.loader.exec_module(launcher)
        authority = {"mode": "central_read_only", "origin": "https://my.hoomy.dev"}
        with patch.object(launcher.API, 'get', return_value={"status":"ok", "authority":authority}), patch.object(launcher, 'call') as call, patch.object(launcher, 'bundle') as bundle, patch.object(launcher, 'edge_pid') as edge:
            with self.assertRaises(launcher.BackendError): launcher.start(no_open=True)
            call.assert_not_called(); bundle.assert_not_called(); edge.assert_not_called()
        with patch.object(launcher, 'RUNTIME', Path(self.fixture.directory.name) / 'runtime'), patch.object(launcher.API, 'get', side_effect=[launcher.BackendError("Stopped backend"), {"status":"ok", "authority":authority}]), patch.object(launcher, 'call') as call, patch.object(launcher, 'bundle') as bundle, patch.object(launcher, 'edge_pid', return_value=None), patch.object(launcher.os, 'umask'), patch.object(launcher.subprocess, 'run', return_value=SimpleNamespace(returncode=0)), patch.object(launcher.subprocess, 'Popen') as worker:
            with self.assertRaises(launcher.BackendError): launcher.start(no_open=True)
            self.assertIn(("docker", "compose", "up", "--no-build", "-d", "--wait"), [entry.args for entry in call.call_args_list])
            bundle.assert_not_called(); worker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
