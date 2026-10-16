"""Project routes avoid discovery summaries and retain source metadata semantics."""

import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import test_store as fixtures
from hoomy.server import make_handler


class ProjectIndexTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.StoreTests()
        self.fixture.setUp()
        self.store = self.fixture.store
        self.project_id = self.fixture.analyzed()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.store))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.fixture.tearDown()

    def request(self, path, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        connection.request("GET", path, headers=headers or {})
        response = connection.getresponse()
        result = response.status, json.loads(response.read()), dict(response.getheaders())
        connection.close()
        return result

    def test_project_index_preserves_metadata_without_discovery_or_source_reads(self):
        self.store.correct(self.project_id, {"name": "Confirmed project", "purpose": "Approved purpose"})
        expected = self.store.state()["projects"]
        queries = []
        self.store.db.set_trace_callback(queries.append)
        with patch.object(self.store, "state", side_effect=AssertionError("Broad state must not be loaded")), \
                patch.object(self.store.overview, "summary", side_effect=AssertionError("Discovery must not be loaded")):
            status, body, headers = self.request("/api/projects")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"projects": expected})
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertFalse(any("snapshots" in query or "catalog " in query or "changes" in query for query in queries))

    def test_health_checks_database_readiness_without_project_data(self):
        with patch.object(self.store, "state", side_effect=AssertionError("Broad state must not be loaded")), \
                patch.object(self.store.overview, "summary", side_effect=AssertionError("Discovery must not be loaded")):
            status, body, headers = self.request("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"status": "ok"})
        self.assertEqual(headers["Cache-Control"], "no-store")
        with self.store.lock, self.store.db:
            self.store.db.execute("DELETE FROM settings WHERE key='control'")
        self.assertEqual(self.request("/api/health")[0], 503)

    def test_project_and_health_routes_enforce_the_existing_loopback_boundary(self):
        for path in ("/api/projects", "/api/health"):
            for headers in ({"Host": "attacker.example"}, {"Origin": "https://attacker.example"},
                            {"Sec-Fetch-Site": "cross-site"}):
                with self.subTest(path=path, headers=headers):
                    self.assertEqual(self.request(path, headers)[0], 403)

    def test_profile_and_edits_keep_sources_and_revisions_without_broad_state(self):
        expected_sources = self.store.state()["sessions"]
        with patch.object(self.store, "state", side_effect=AssertionError("Broad state must not be loaded")), \
                patch.object(self.store.overview, "summary", side_effect=AssertionError("Discovery must not be loaded")):
            profile = self.store.dialogue.profile(self.project_id)
            self.assertEqual(profile["sources"], expected_sources)
            saved = self.store.dialogue.update_profile({"project_id": self.project_id,
                "revision": profile["revision"], "fields": {"goal": "Keep source provenance."}})
        self.assertNotEqual(saved["revision"], profile["revision"])
        self.assertEqual(saved["fields"]["goal"]["answer"], "Keep source provenance.")
        self.assertEqual(saved["sources"], expected_sources)

    def test_metadata_filter_retains_catalog_titles_status_and_state_coverage(self):
        self.fixture.ingest(sid="pending", snapshot="pending-snapshot")
        metadata = {"session_id": "s", "title": "Canonical source title", "cwd": "/scope",
                    "source_path": "/fictional-source/s", "size": 2, "modified_at": "2026-02-01T00:00:00Z",
                    "title_source": "codex_index"}
        with self.store.lock, self.store.db:
            self.store.db.execute("INSERT INTO catalog VALUES (?,?,?)", ("s", json.dumps(metadata), "test-scan"))
        state = self.store.state()
        selected = [item for item in state["sessions"] if item["id"] == "s"]
        self.assertEqual(self.store.session_metadata(["s"]), selected)
        self.assertEqual(selected[0]["title"], metadata["title"])
        self.assertEqual(selected[0]["status"], "updated")
        self.assertEqual(state["coverage"]["analyzed"], 1)
        self.assertEqual(state["coverage"]["pending"], 1)
        self.assertEqual(state["coverage"]["failed"], 0)
        self.assertEqual(self.store.session_metadata([]), [])
        self.assertEqual(self.store.session_metadata(), state["sessions"])


if __name__ == "__main__":
    unittest.main()
