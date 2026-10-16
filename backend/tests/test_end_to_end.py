"""Data-free lifecycle check across the edge, loopback API, and persistent store."""

import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from hoomy.collector import snapshot
from hoomy.edge import Client, Edge
from hoomy.server import make_handler
from hoomy.store import SECTIONS, Store


class EndToEndTests(unittest.TestCase):
    def test_wizard_local_inventory_requires_separate_inference_consent(self):
        self.client.post('/api/control', dict(consent=True, running=False, inference_enabled=False))
        self.client.post('/api/overview/start', dict(local_only=True))
        with patch('hoomy.classifier.classify') as inference:
            self.edge.tick()
            inference.assert_not_called()
        state = self.client.get('/api/state')
        self.assertFalse(state['control']['running'])
        self.assertFalse(state['control']['inference_enabled'])
        self.assertEqual(state['overview']['stage'], 'awaiting_consent')
        self.assertEqual(state['overview']['indexed'], 1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM snapshots').fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM title_assessments').fetchone()[0], 0)
        self.client.post('/api/control', dict(consent=True, running=True, inference_enabled=True))
        result = dict(projects=[], model='fixture', elapsed_seconds=0.1)
        with patch('hoomy.classifier.classify', return_value=result) as inference, patch('hoomy.edge.snapshot') as raw:
            self.edge.tick()
            inference.assert_called_once()
            raw.assert_not_called()
        state = self.client.get('/api/state')
        self.assertFalse(state['control']['running'])
        self.assertEqual(state['overview']['stage'], 'review')
        self.assertEqual(state['overview']['examined'], 1)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.codex_root = self.root / "shape-only-codex-root"
        self.source = self.codex_root / "sessions" / "shape.jsonl"
        self.source.parent.mkdir(parents=True)
        self._write_source([
            {"type": "session_meta", "payload": {"id": "shape-session", "cwd": "/shape"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                      "content": [{"type": "input_text", "text": "x"}]}},
        ], age=30)

        self.store = Store(self.root / "backend")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.store))
        self.server.daemon_threads = True
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.port = self.server.server_address[1]
        self.client = Client(f"http://127.0.0.1:{self.port}")
        self.edge = Edge(self.client, self.codex_root)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)
        self.store.close()
        self.temporary.cleanup()

    def _write_source(self, records, *, age):
        raw = "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records).encode()
        self.source.write_bytes(raw)
        observed = time.time() - age
        os.utime(self.source, (observed, observed))
        return raw

    def _request(self, path, method="GET", payload=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        body = None if payload is None else json.dumps(payload)
        headers = {} if payload is None else {"Content-Type": "application/json"}
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def _fake_analysis(self, messages, projects, _cwd, _cancelled, _model):
        self.analysis_calls += 1
        citation = {"message_id": messages[0]["id"], "quote": "x"}
        brief = {section: [] for section in SECTIONS}
        brief["intentions"] = [{"text": f"assertion-{self.analysis_calls}",
                                 "kind": "reported", "evidence": [citation]}]
        return {
            "brief": brief,
            "projects": [{
                "existing_project_id": projects[0]["id"] if projects else None,
                "name": f"model-name-{self.analysis_calls}",
                "purpose": f"model-purpose-{self.analysis_calls}",
                "status": "active",
                "rationale": f"shape-{self.analysis_calls}",
                "evidence": [citation],
            }],
            "method": {"adapter": "test-fake", "schema": 1},
        }

    def test_consent_discovery_correction_reanalysis_and_reset(self):
        # A source can exist without even enumerating history before confirmation.
        initial_source = self.source.read_bytes()
        with patch("hoomy.edge.session_paths") as paths:
            self.edge.tick()
        paths.assert_not_called()
        state = self.client.get("/api/state")
        self.assertFalse(state["control"]["running"])
        self.assertFalse(state["control"]["scope_confirmed"])
        self.assertEqual(state["coverage"]["discovered"], 0)

        # Scope consent starts collection, but inference remains independently off.
        control = self.client.post("/api/control", {"running": True, "consent": True, "mode": "full"})
        self.assertTrue(control["scope_confirmed"])
        self.assertFalse(control["inference_enabled"])
        self.edge.tick()
        state = self.client.get("/api/state")
        self.assertEqual(state["coverage"], {"discovered": 1, "analyzed": 0,
                                             "pending": 1, "failed": 0, "excluded": 0})
        self.assertEqual(self.source.read_bytes(), initial_source)
        first_snapshot = state["sessions"][0]["snapshot_id"]

        # A fake adapter exercises the real worker/storage path without Codex usage.
        self.analysis_calls = 0
        self.client.post("/api/control", {"inference_enabled": True, "consent": True})
        with patch("hoomy.inference.analyze", side_effect=self._fake_analysis) as inference:
            self.edge.tick()
        inference.assert_called_once()
        state = self.client.get("/api/state")
        self.assertEqual(state["coverage"]["analyzed"], 1)
        self.assertEqual(len(state["projects"]), 1)
        project = state["projects"][0]
        project_id = project["id"]
        self.assertEqual(project["session_ids"], ["shape-session"])
        detail = self.client.get("/api/sessions/shape-session")
        statement = detail["brief"]["intentions"][0]
        self.assertEqual(statement["evidence"], [{"message_id": detail["messages"][0]["id"],
                                                   "quote": "x"}])
        discovered = next(change for change in state["changes"] if change["kind"] == "discovered")
        self.assertEqual(discovered["evidence"][0]["session_id"], "shape-session")
        self.assertEqual(discovered["evidence"][0]["snapshot_id"], first_snapshot)

        # Confirmed fields remain authoritative when a newer snapshot is reanalyzed.
        corrected = self.client.post(f"/api/projects/{project_id}/correct", {
            "name": "confirmed-name", "status": "paused", "note": "confirmed-note"
        })
        self.assertEqual(corrected["name"], "confirmed-name")
        updated_source = self._write_source([
            {"type": "session_meta", "payload": {"id": "shape-session", "cwd": "/shape"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                      "content": [{"type": "input_text", "text": "x"}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                                                      "content": [{"type": "output_text", "text": "y"}]}},
        ], age=10)
        self.edge.scan_at = 0
        with patch("hoomy.inference.analyze", side_effect=self._fake_analysis) as inference:
            self.edge.tick()
        inference.assert_called_once()
        state = self.client.get("/api/state")
        current = state["sessions"][0]
        self.assertNotEqual(current["snapshot_id"], first_snapshot)
        self.assertEqual(current["status"], "analyzed")
        project = state["projects"][0]
        self.assertEqual(project["name"], "confirmed-name")
        self.assertEqual(project["status"], "paused")
        self.assertEqual(project["note"], "confirmed-note")
        self.assertEqual(project["purpose"], "model-purpose-2")
        self.assertTrue({"name", "status", "note"}.issubset(project["confirmed_fields"]))
        historical = self.client.get(f"/api/snapshots/{first_snapshot}")
        self.assertFalse(historical["is_current"])
        self.assertIsNotNone(historical["brief"])

        # Reset clears derived/copied Hoomy state and invalidates in-flight writes.
        old_generation = state["control"]["generation"]
        stale_ingest = snapshot(self.source)
        stale_ingest["generation"] = old_generation
        stale_analysis = self._fake_analysis(detail["messages"], [project], "/shape", lambda: False, None)
        stale_analysis.update({"generation": old_generation, "snapshot_id": current["snapshot_id"]})
        status, reset = self._request("/api/reset", "POST", {"confirm": "DELETE_HOOMY_DATA"})
        self.assertEqual(status, 200)
        self.assertNotEqual(reset["control"]["generation"], old_generation)
        state = self.client.get("/api/state")
        self.assertEqual(state["coverage"], {"discovered": 0, "analyzed": 0,
                                             "pending": 0, "failed": 0, "excluded": 0})
        self.assertEqual(state["sessions"], [])
        self.assertEqual(state["projects"], [])
        self.assertEqual(state["changes"], [])
        self.assertFalse(state["control"]["running"])
        self.assertFalse(state["control"]["scope_confirmed"])
        self.assertEqual(self._request(f"/api/snapshots/{first_snapshot}")[0], 404)
        self.assertEqual(self._request("/api/ingest", "POST", stale_ingest)[0], 409)
        self.assertEqual(self._request("/api/sessions/shape-session/analysis", "POST", stale_analysis)[0], 409)
        self.assertEqual(self.client.get("/api/state")["sessions"], [])
        self.assertTrue(self.source.exists())
        self.assertEqual(self.source.read_bytes(), updated_source)

    def test_overview_then_selected_deepening_and_bad_citation_is_local(self):
        from hoomy.inference import EvidenceError
        for suffix in ("b", "c"):
            path = self.source.parent / (suffix + ".jsonl")
            path.write_bytes(self.source.read_bytes().replace(b"shape-session", ("shape-" + suffix).encode()))
        self.client.post("/api/control", {"consent": True, "inference_enabled": True})

        def classify_metadata(payload, _cancelled, progress=None):
            self.assertEqual(len(payload["conversations"]), 3)
            self.assertNotIn("messages", payload)
            return {"projects": [{"name": "shape", "purpose": "x", "rationale": "x",
                    "session_ids": [r["id"] for r in payload["conversations"]], "existing_project_id": None}],
                    "model": "gpt-5.6-luna", "elapsed_seconds": 0.1}

        self.client.post("/api/overview/start", {})
        with patch("hoomy.classifier.classify", side_effect=classify_metadata), patch("hoomy.edge.snapshot") as raw:
            self.edge.tick()
        raw.assert_not_called()
        state = self.client.get("/api/state")
        self.assertFalse(state["control"]["running"])
        self.assertEqual(state["overview"]["indexed"], 3)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM snapshots").fetchone()[0], 0)
        oid = state["overview"]["candidates"][0]["id"]
        pid = self.client.post(f"/api/outlines/{oid}/review", {"decision": "keep"})["project_id"]
        self.client.post(f"/api/projects/{pid}/deepen", {})
        with patch("hoomy.inference.analyze", side_effect=EvidenceError("bad citation")):
            self.edge.tick()
        self.assertTrue(self.client.get("/api/state")["control"]["running"])
        self.analysis_calls = 0
        with patch("hoomy.inference.analyze", side_effect=self._fake_analysis):
            self.edge.tick()
            self.edge.tick()
        state = self.client.get("/api/state")
        self.assertFalse(state["control"]["running"])
        self.assertEqual(state["coverage"]["analyzed"], 2)
        self.assertEqual(len(state["overview"]["deep_failures"]), 1)
        self.assertEqual(state["projects"][0]["name"], "shape")


    def test_completion_uses_agent_after_title_review_without_reclassifying_titles(self):
        for suffix in ("b", "c"):
            path = self.source.parent / (suffix + ".jsonl")
            path.write_bytes(self.source.read_bytes().replace(b"shape-session", ("shape-" + suffix).encode()))
        original = self.source.read_bytes()
        self.client.post("/api/control", {"consent": True, "inference_enabled": True})
        calls = []
        target = None
        def classify(payload, cancelled, progress=None):
            calls.append(payload)
            ids = [row["id"] for row in payload["conversations"]]
            projects = []
            if len(calls) == 1:
                projects = [{"name": "Shape", "purpose": "x", "rationale": "x", "session_ids": ids[:1], "existing_project_id": None}]
            if len(calls) == 3:
                self.assertTrue(all(row.get("excerpts") for row in payload["conversations"]))
                self.assertLessEqual(sum(len(text) for row in payload["conversations"] for text in row["excerpts"]), 36_000)
                projects = [{"name": "Shape", "purpose": "x", "rationale": "x", "session_ids": ids, "existing_project_id": target}]
            return {"projects": projects, "model": "gpt-5.6-luna", "elapsed_seconds": 0.1}
        with patch("hoomy.classifier.classify", side_effect=classify):
            self.client.post("/api/overview/start", {})
            self.edge.tick()
            oid = self.client.get("/api/state")["overview"]["candidates"][0]["id"]
            target = self.client.post(f"/api/outlines/{oid}/review", {"decision": "keep"})["project_id"]
            self.client.post("/api/overview/complete", {"include_content": True, "automatic_associations": True})
            self.assertEqual(self.client.get("/api/state")["overview"]["completion_phase"], "agent")
            def explore(records, known, root, exclusions, cancelled, progress, **options):
                from hoomy.exploration import EvidenceTools
                tools = EvidenceTools(records, root, exclusions, cancelled, lambda *args: None)
                for record in records:
                    tools.inspect(record["session_id"], "")
                progress({"elapsed_seconds": 1, "events": [{"kind": "tool", "text": "Reading selected excerpts", "elapsed_seconds": 1}], "plan": [], "usage": None})
                options["publish"]({"name": "Shape", "purpose": "x", "rationale": "x", "session_ids": list(tools.evidence), "existing_project_id": target}, tools.evidence, list(tools.snapshots.values()))
                self.assertTrue(self.client.get("/api/state")["control"]["running"])
                self.assertEqual(self.client.get("/api/state")["overview"]["linked"], 3)
                return {"projects": [{"name": "Shape", "purpose": "x", "rationale": "x", "session_ids": list(tools.evidence), "existing_project_id": target}],
                        "model": "gpt-5.6-luna", "elapsed_seconds": 1, "input_chars": 100,
                        "snapshots": list(tools.snapshots.values()), "excerpt_evidence": tools.evidence, "attempted_ids": list(tools.attempted)}
            with patch("hoomy.exploration.explore", side_effect=explore):
                self.edge.tick()
        self.assertEqual(len(calls), 1)
        state = self.client.get("/api/state")
        self.assertFalse(state["control"]["running"])
        self.assertEqual(state["overview"]["sampled"], 2)
        self.assertEqual(state["coverage"]["analyzed"], 0)
        self.assertEqual(state["overview"]["linked"], 3)
        self.assertEqual(state["overview"]["agent_progress"]["events"][0]["kind"], "tool")
        self.assertEqual(len(state["overview"]["automatic_associations"]), 2)
        self.assertEqual(self.source.read_bytes(), original)
        self.client.post("/api/reset", {"confirm": "DELETE_HOOMY_DATA"})
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM recognition_excerpts").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
