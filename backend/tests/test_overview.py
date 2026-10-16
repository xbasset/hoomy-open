import tempfile
import unittest

from hoomy.overview import initial_overview
from hoomy.store import Store, StoreError


class OverviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.generation = self.store.state()["control"]["generation"]

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def start(self, reexamine=False):
        self.store.control({"consent": True, "inference_enabled": True})
        self.request = self.store.overview.start({"reexamine": reexamine})["request_id"]

    def envelope(self, **payload):
        return {"generation": self.generation, "request_id": self.request, **payload}

    def catalog(self, count=4, reexamine=False):
        self.start(reexamine)
        records = [{"session_id": str(i), "title": "t", "cwd": "/a", "source_path": "/source/" + str(i),
                    "size": 4, "modified_at": f"2026-01-0{i+1}T00:00:00Z", "title_source": "codex_index"} for i in range(count)]
        self.store.overview.catalog(self.envelope(records=records, complete=True, files_seen=count, excluded=0, failures=[]))

    def result(self, count=3):
        return self.envelope(input_ids=[r["session_id"] for r in self.store.overview.inputs()], model="gpt-5.6-luna", elapsed_seconds=1, input_chars=100,
            projects=[{"name": "p" + str(i), "purpose": "x", "rationale": "x", "session_ids": [str(i)], "existing_project_id": None} for i in range(count)])

    def recognized(self):
        self.catalog()
        payload = self.result(1)
        payload["projects"][0]["session_ids"] = ["0", "1", "2", "3"]
        self.store.overview.result(payload)
        oid = self.store.state()["overview"]["candidates"][0]["id"]
        return self.store.overview.review(oid, {"decision": "keep"})["project_id"]

    def test_scope_and_external_processing_are_required(self):
        with self.assertRaises(StoreError):
            self.store.overview.start({})
        self.store.control({"consent": True})
        with self.assertRaises(StoreError):
            self.store.overview.start({})

    def test_inventory_does_not_create_snapshots_or_projects(self):
        self.catalog()
        state = self.store.state()
        self.assertEqual(state["overview"]["indexed"], 4)
        self.assertEqual(state["projects"], [])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM snapshots").fetchone()[0], 0)
        self.assertEqual(self.store.session("0")["messages"], [])
        self.assertEqual(self.store.session("0")["status"], "indexed")

    def test_three_results_stop_for_review_and_remain_provisional(self):
        self.catalog()
        self.store.overview.result(self.result())
        state = self.store.state()
        self.assertFalse(state["control"]["running"])
        self.assertEqual(state["overview"]["stage"], "review")
        self.assertEqual(len(state["overview"]["candidates"]), 3)
        self.assertEqual(state["projects"], [])
        self.assertEqual(state["overview"]["remaining"], 1)

    def test_more_than_three_results_are_allowed(self):
        self.catalog()
        self.store.overview.result(self.result(4))
        self.assertEqual(len(self.store.state()["overview"]["candidates"]), 4)

    def test_discard_warning_finishes_pass_even_without_valid_proposals(self):
        for count in (1, 0):
            self.catalog(reexamine=True)
            payload = self.result(count)
            payload["discarded_proposals"] = 2
            self.store.overview.result(payload)
            state = self.store.state()
            self.assertEqual(state["overview"]["stage"], "review")
            self.assertEqual(state["overview"]["considered"], 4)
            self.assertIn("2 proposition(s)", state["overview"]["message"])
            self.assertEqual(len(state["overview"]["candidates"]), count)
            self.assertFalse(state["control"]["running"])

    def test_untraceable_results_are_atomic(self):
        self.catalog()
        for payload in (self.result(4), self.result(1)):
            payload["projects"][0]["session_ids"] = ["absent"]
            with self.assertRaises(StoreError):
                self.store.overview.result(payload)
        self.assertEqual(self.store.state()["overview"]["candidates"], [])

    def test_new_pass_replaces_pending_drafts_but_preserves_decisions(self):
        self.catalog()
        self.store.overview.result(self.result(3))
        candidates = self.store.state()["overview"]["candidates"]
        self.store.overview.review(candidates[0]["id"], {"decision": "keep"})
        self.store.overview.review(candidates[1]["id"], {"decision": "skip"})
        self.catalog(reexamine=True)
        self.assertEqual({r["session_id"] for r in self.store.overview.inputs()}, {"2", "3"})
        payload = self.result(1)
        payload["input_ids"] = ["3", "2"]
        payload["projects"][0]["session_ids"] = ["2", "3"]
        self.store.overview.result(payload)
        self.assertEqual(len(self.store.state()["overview"]["candidates"]), 1)
        self.assertEqual(len(self.store.state()["projects"]), 1)

    def test_paused_or_replaced_results_are_rejected(self):
        self.catalog()
        payload = self.result()
        self.store.control({"running": False})
        with self.assertRaises(StoreError):
            self.store.overview.result(payload)
        self.store.overview.start({})
        with self.assertRaises(StoreError):
            self.store.overview.result(payload)

    def test_keep_confirms_only_name_and_purpose(self):
        pid = self.recognized()
        project = self.store.state()["projects"][0]
        self.assertEqual(project["id"], pid)
        self.assertEqual(set(project["confirmed_fields"]), {"name", "purpose"})
        self.assertEqual(project["status"], "unclear")
        self.assertEqual(set(project["session_ids"]), {"0", "1", "2", "3"})

    def test_skip_is_not_privacy_deletion(self):
        self.catalog()
        self.store.overview.result(self.result(1))
        oid = self.store.state()["overview"]["candidates"][0]["id"]
        self.store.overview.review(oid, {"decision": "skip"})
        self.assertEqual(self.store.state()["projects"], [])
        self.assertEqual(self.store.state()["overview"]["indexed"], 4)

    def test_recognition_locks_an_already_ingested_session(self):
        self.catalog()
        with self.store.db:
            self.store.db.execute("INSERT INTO sessions(id,snapshot_id,status) VALUES ('0','shape','pending')")
        self.store.overview.result(self.result(1))
        oid = self.store.state()["overview"]["candidates"][0]["id"]
        pid = self.store.overview.review(oid, {"decision": "keep"})["project_id"]
        self.assertEqual(self.store.db.execute("SELECT assignment_locked FROM sessions WHERE id='0'").fetchone()[0], 1)
        self.assertEqual(self.store.db.execute("SELECT project_id FROM associations WHERE session_id='0'").fetchone()[0], pid)

    def test_deepening_is_bounded_and_survives_restart(self):
        pid = self.recognized()
        self.assertEqual(self.store.overview.deepen(pid, {})["queued"], 3)
        self.store.close()
        self.store = Store(self.temp.name)
        plan = self.store._get("overview")
        self.assertEqual(len(plan["deep_queue"]), 3)
        self.assertEqual(plan["deep_queue"][0]["session_id"], "3")

    def test_failed_deep_item_does_not_stop_other_items(self):
        pid = self.recognized()
        self.store.overview.deepen(pid, {})
        self.request = self.store._get("overview")["request_id"]
        self.store.overview.advance(self.envelope(session_id="3", reason="invalid citation"))
        self.assertTrue(self.store.state()["control"]["running"])
        self.assertEqual(self.store._get("overview")["deep_done"], 1)
        self.store.overview.advance(self.envelope(session_id="2"))
        self.store.overview.advance(self.envelope(session_id="1"))
        self.assertFalse(self.store.state()["control"]["running"])

    def test_transient_recovery_is_bounded_and_resume_preserves_inventory(self):
        self.catalog()
        for _ in range(3):
            self.store.overview.interruption(self.envelope())
            self.assertTrue(self.store.state()["control"]["running"])
        self.store.overview.interruption(self.envelope())
        self.assertFalse(self.store.state()["control"]["running"])
        self.store.control({"running": True})
        self.assertEqual(self.store._get("overview")["stage"], "classifying")
        self.assertEqual(self.store._get("overview")["retry_count"], 0)
        self.assertEqual(self.store.state()["overview"]["indexed"], 4)

    def test_reset_removes_catalog_but_rejects_old_writes(self):
        self.recognized()
        payload = self.result()
        self.store.reset({"confirm": "DELETE_HOOMY_DATA"})
        self.assertEqual(self.store._get("overview"), initial_overview())
        self.assertEqual(self.store.state()["overview"]["indexed"], 0)
        self.assertEqual(self.store.state()["projects"], [])
        with self.assertRaises(StoreError):
            self.store.overview.result(payload)

    def test_scope_cannot_change_after_metadata_ingestion(self):
        self.catalog()
        with self.assertRaises(StoreError):
            self.store.control({"exclusions": ["/a"]})

    def test_recognized_metadata_links_can_merge_and_split(self):
        pid = self.recognized()
        other = self.store.split(pid, {"session_ids": ["0"], "name": "other", "purpose": "x"})
        self.assertEqual(other["session_ids"], ["0"])
        self.assertNotIn("0", self.store._project(self.store._resolve(pid))["session_ids"])
        merged = self.store.merge({"source_ids": [other["id"]], "target_id": pid})
        self.assertEqual(set(merged["session_ids"]), {"0", "1", "2", "3"})
