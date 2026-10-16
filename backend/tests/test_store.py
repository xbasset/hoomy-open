"""Shape-only assertions; no representative or generated conversation corpus."""

import base64
import hashlib
import tempfile
import unittest

from hoomy.store import SECTIONS, Store, StoreError


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(self.directory.name)
        self.generation = self.store.state()["control"]["generation"]

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def approve(self):
        return self.store.control({"running": True, "inference_enabled": True, "consent": True})

    def ingest(self, sid="s", snapshot="v1", modified="2026-01-01T00:00:00Z", cwd="/scope"):
        return self.store.ingest({"generation": self.generation, "session_id": sid, "snapshot_id": self.digest(snapshot),
                                 "source_path": "/private-test/" + sid, "cwd": cwd, "modified_at": modified,
                                 "raw_b64": base64.b64encode(snapshot.encode()).decode(), "warnings": [],
                                 "messages": [{"id": snapshot + ":1", "role": "user", "text": "x",
                                               "line_start": 1, "line_end": 1, "timestamp": None}]})

    def analysis_payload(self, snapshot="v1", name="A", project_id=None, status="active"):
        evidence = [{"message_id": snapshot + ":1", "quote": "x"}]
        brief = {section: [] for section in SECTIONS}
        brief["intentions"] = [{"text": "x", "kind": "reported", "evidence": evidence}]
        return {"generation": self.generation, "snapshot_id": self.digest(snapshot), "brief": brief,
                "method": {"adapter": "test-shape", "schema": 1}, "projects": [
                    {"existing_project_id": project_id, "name": name, "purpose": "x", "status": status,
                     "rationale": "x", "evidence": evidence}]}

    def digest(self, label):
        return hashlib.sha256(label.encode()).hexdigest()

    def analyzed(self):
        self.approve()
        self.ingest()
        self.store.analysis("s", self.analysis_payload())
        return self.store.state()["projects"][0]["id"]

    def test_default_stopped_and_scope_required(self):
        self.assertFalse(self.store.state()["control"]["running"])
        self.assertFalse(self.store.state()["control"]["inference_enabled"])
        with self.assertRaises(StoreError):
            self.ingest()
        with self.assertRaises(StoreError):
            self.store.control({"running": True})
        self.approve()
        self.ingest()
        self.assertEqual(self.store.state()["coverage"]["pending"], 1)

    def test_external_inference_needs_own_consent(self):
        self.store.control({"running": True, "consent": True})
        with self.assertRaises(StoreError):
            self.store.control({"inference_enabled": True})

    def test_snapshot_idempotency_preserves_analysis(self):
        self.analyzed()
        self.assertFalse(self.ingest()["changed"])
        self.assertEqual(self.store.session("s")["status"], "analyzed")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 1)
        with self.assertRaises(StoreError):
            self.ingest(sid="different")

    def test_snapshot_raw_private_and_versions_immutable(self):
        self.analyzed()
        self.ingest(snapshot="v2", modified="2026-02-01T00:00:00Z")
        session = self.store.session("s")
        self.assertIsNone(session["brief"])
        self.assertNotIn("raw", session)
        self.assertNotIn("raw_b64", session)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 2)
        with self.assertRaises(StoreError):
            self.store.analysis("s", self.analysis_payload())

    def test_quote_and_id_validation_is_atomic(self):
        self.approve()
        self.ingest()
        for kind in ("quote", "id", "empty"):
            payload = self.analysis_payload()
            evidence = payload["brief"]["intentions"][0]["evidence"]
            if kind == "quote":
                evidence[0]["quote"] = "y"
            elif kind == "id":
                evidence[0]["message_id"] = "missing"
            else:
                evidence.clear()
            with self.assertRaises(StoreError):
                self.store.analysis("s", payload)
            self.assertEqual(self.store.state()["projects"], [])
            self.assertIsNone(self.store.session("s")["brief"])

    def test_proposal_must_link_evidence_and_valid_project(self):
        self.approve()
        self.ingest()
        payload = self.analysis_payload(project_id="missing")
        with self.assertRaises(StoreError):
            self.store.analysis("s", payload)
        payload = self.analysis_payload()
        payload["projects"][0]["evidence"] = []
        with self.assertRaises(StoreError):
            self.store.analysis("s", payload)

    def test_correction_survives_analysis_and_restart(self):
        pid = self.analyzed()
        self.store.correct(pid, {"name": "B", "status": "paused", "note": "x", "dismissed": True})
        self.ingest(snapshot="v2", modified="2026-02-01T00:00:00Z")
        self.store.analysis("s", self.analysis_payload("v2", project_id=pid))
        self.store.close()
        self.store = Store(self.directory.name)
        project = self.store.state()["projects"][0]
        self.assertEqual(project["name"], "B")
        self.assertEqual(project["status"], "paused")
        self.assertTrue(project["dismissed"])
        self.assertIn("name", project["confirmed_fields"])
        self.assertTrue(self.store.state()["control"]["scope_confirmed"])

    def test_recent_state_not_overwritten_by_backfill(self):
        pid = self.analyzed()
        self.ingest("old", "old1", "2025-01-01T00:00:00Z")
        self.store.analysis("old", self.analysis_payload("old1", project_id=pid, status="paused"))
        project = self.store.state()["projects"][0]
        self.assertEqual(project["status"], "active")
        self.assertEqual(project["session_ids"], ["s", "old"])

    def test_merge_redirects_and_old_names_do_not_recreate_project(self):
        first = self.analyzed()
        self.ingest("t", "t1")
        self.store.analysis("t", self.analysis_payload("t1", name="B"))
        second = self.store.state()["projects"][1]["id"]
        self.store.correct(second, {"note": "x"})
        with self.assertRaises(StoreError):
            self.store.merge({"source_ids": [second], "target_id": first})
        self.store.control({"running": False})
        self.store.merge({"source_ids": [second], "target_id": first})
        self.store.control({"running": True})
        for sid, snapshot, project_id in (("u", "u1", second), ("v", "v1b", None)):
            self.ingest(sid, snapshot)
            self.store.analysis(sid, self.analysis_payload(snapshot, name="B", project_id=project_id))
        projects = self.store.state()["projects"]
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["name"], "A")
        self.assertEqual(projects[0]["note"], "x")
        self.assertEqual(set(projects[0]["session_ids"]), {"s", "t", "u", "v"})

    def test_split_assignments_survive_inference(self):
        pid = self.analyzed()
        split = self.store.split(pid, {"session_ids": ["s"], "name": "B", "purpose": "x"})
        self.ingest(snapshot="v2", modified="2026-02-01T00:00:00Z")
        self.store.analysis("s", self.analysis_payload("v2", project_id=pid))
        projects = {p["id"]: p for p in self.store.state()["projects"]}
        self.assertEqual(projects[pid]["session_ids"], [])
        self.assertEqual(projects[split["id"]]["session_ids"], ["s"])

    def test_exclusions_enforced_and_frozen_after_ingestion(self):
        self.store.control({"running": True, "consent": True, "exclusions": ["/scope"]})
        with self.assertRaises(StoreError):
            self.ingest()
        self.store.control({"exclusions": []})
        self.ingest()
        with self.assertRaises(StoreError):
            self.store.control({"exclusions": ["/scope"]})

    def test_scope_defense_normalizes_paths_and_rejects_unknown_cwd(self):
        with self.assertRaises(StoreError):
            self.store.control({"running": True, "consent": True, "exclusions": ["relative"]})
        control = self.store.control({"running": True, "consent": True,
                                      "exclusions": ["/scope/../private/", "/private"]})
        self.assertEqual(control["exclusions"], ["/private"])
        for cwd in ("", "relative/private-value"):
            with self.subTest(cwd=cwd), self.assertRaises(StoreError) as error:
                self.ingest(cwd=cwd)
            self.assertEqual(error.exception.status, 403)
            if cwd:
                self.assertNotIn(cwd, str(error.exception))
        with self.assertRaises(StoreError):
            self.ingest(cwd="/private/project")

        self.ingest(cwd="/private-work/./repo/..")
        self.assertEqual(self.store.session("s")["cwd"], "/private-work")

    def test_review_overlay_retains_original_claim(self):
        self.analyzed()
        review = {"snapshot_id": self.digest("v1"), "verdict": "minor_edit", "seconds": 2.5, "evidence_valid": True,
                  "edits": [{"section": "intentions", "index": 0, "text": None}]}
        self.store.review("s", review)
        session = self.store.session("s")
        self.assertEqual(session["brief"]["intentions"][0]["text"], "x")
        self.assertEqual(session["review"], review)
        aggregate = self.store.pilot()
        self.assertEqual(aggregate["reviewed_sessions"], 1)
        self.assertEqual(aggregate["median_review_seconds"], 2.5)
        self.assertIsNone(aggregate["pass"])
        self.assertEqual(aggregate["status"], "development")

    def test_invalid_review_and_method_are_rejected(self):
        self.approve()
        self.ingest()
        payload = self.analysis_payload()
        payload["method"] = {}
        with self.assertRaises(StoreError):
            self.store.analysis("s", payload)
        self.store.analysis("s", self.analysis_payload())
        for seconds in (True, -1, float("nan"), 90000):
            with self.assertRaises(StoreError):
                self.store.review("s", {"verdict": "accept", "seconds": seconds, "evidence_valid": True})

    def test_failure_retry_and_discovery_counts(self):
        self.approve()
        self.ingest()
        self.store.failure("s", {"generation": self.generation, "snapshot_id": self.digest("v1"), "reason": "Unavailable"})
        self.store.discovery({"generation": self.generation, "excluded": 2, "failures": [{"source_id": "bad", "reason": "Unreadable"}]})
        coverage = self.store.state()["coverage"]
        self.assertEqual(coverage, {"discovered": 4, "analyzed": 0, "pending": 0, "failed": 2, "excluded": 2})
        self.store.retry("s")
        self.assertEqual(self.store.session("s")["status"], "pending")

    def test_reset_removes_only_stored_data_and_rejects_inflight_work(self):
        self.analyzed()
        with self.assertRaises(StoreError):
            self.store.reset({})
        original_generation = self.generation
        reset = self.store.reset({"confirm": "DELETE_HOOMY_DATA"})
        self.assertNotEqual(reset["control"]["generation"], original_generation)
        self.assertFalse(reset["control"]["scope_confirmed"])
        self.assertEqual(self.store.state()["projects"], [])
        self.assertEqual(self.store.state()["sessions"], [])
        self.assertEqual(self.store.state()["changes"], [])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 0)
        with self.assertRaises(StoreError):
            self.ingest()
        with self.assertRaises(StoreError):
            self.store.worker({"generation": original_generation, "status": "running", "message": ""})
        self.generation = reset["control"]["generation"]
        self.approve()
        self.ingest()

    def test_historical_snapshot_evidence_and_stale_review(self):
        self.analyzed()
        self.ingest(snapshot="v2")
        old = self.store.snapshot(self.digest("v1"))
        self.assertFalse(old["is_current"])
        self.assertEqual(old["messages"][0]["id"], "v1:1")
        self.assertIsNotNone(old["brief"])
        with self.assertRaises(StoreError):
            self.store.review("s", {"snapshot_id": self.digest("v1"), "verdict": "accept",
                                    "seconds": 1, "evidence_valid": True, "edits": []})

    def test_matching_names_alone_do_not_merge_projects(self):
        self.analyzed()
        self.ingest("t", "t1")
        self.store.analysis("t", self.analysis_payload("t1", name="A"))
        self.assertEqual(len(self.store.state()["projects"]), 2)


if __name__ == "__main__":
    unittest.main()
