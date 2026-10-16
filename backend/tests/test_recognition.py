"""Coverage, corrections and bounded memory on metadata-only shape fixtures."""

import tempfile
import json
import unittest
from unittest.mock import patch

from hoomy import recognition
from hoomy.catalog import clean_title
from hoomy.classifier import validate
from hoomy.store import Store, StoreError, encode


class RecognitionTests(unittest.TestCase):
    def test_agent_events_survive_pause_but_late_updates_are_rejected(self):
        self.kept_project(2)
        self.census(4); self.submit()
        self.store.overview.complete({"include_content": True})
        old_request = self.store._get("overview")["request_id"]
        payload = {"generation": self.store.state()["control"]["generation"], "request_id": old_request,
                   "elapsed_seconds": 3, "events": [{"kind": "summary", "text": "Inspecting evidence", "elapsed_seconds": 2}],
                   "plan": [{"step": "Inspect", "status": "inProgress"}], "usage": {"inputTokens": 10}}
        self.store.overview.agent_progress(payload)
        with self.assertRaises(StoreError):
            self.store.overview.agent_progress(dict(payload, events=[{"kind": "raw_reasoning", "text": "x", "elapsed_seconds": 2}]))
        self.store.control({"running": False})
        self.store.control({"running": True})
        self.assertNotEqual(old_request, self.store._get("overview")["request_id"])
        with self.assertRaises(StoreError):
            self.store.overview.agent_progress(payload)
        self.assertEqual(self.store.state()["overview"]["agent_progress"]["events"][0]["text"], "Inspecting evidence")

    def test_completion_hands_all_residuals_to_agent_and_preserves_uninspected_titles(self):
        self.kept_project(2)
        self.census(30); self.submit()
        with self.assertRaises(StoreError):
            self.store.overview.complete({})
        result = self.store.overview.complete({"include_content": True})
        self.request = self.store._get("overview")["request_id"]
        self.assertEqual(result["titles"], 28)
        self.assertEqual(len(self.store.overview.inputs()), 28)
        work = self.store._get("overview")
        self.assertEqual(work["completion_phase"], "agent")
        self.assertEqual(len(work["queue"]), 28)
        before = [tuple(r) for r in self.store.db.execute("SELECT * FROM title_assessments ORDER BY session_id")]
        self.store.control({"running": False})
        self.store.close(); self.store = Store(self.temp.name)
        self.store.control({"running": True})
        self.request = self.store._get("overview")["request_id"]
        self.assertEqual(len(self.store.overview.inputs()), 28)
        self.submit()
        state = self.store.state()
        self.assertFalse(state["control"]["running"])
        self.assertEqual(state["overview"]["completion_phase"], "done")
        self.assertEqual(state["overview"]["pass_done"], 0)
        self.assertEqual(before, [tuple(r) for r in self.store.db.execute("SELECT * FROM title_assessments ORDER BY session_id")])
        self.assertEqual(len(state["projects"]), 1)

    def test_outline_can_join_selected_project_preserving_user_fields(self):
        pid = self.kept_project(2, inactive=True)
        before = self.store._project(self.store._resolve(pid))
        self.census(4); self.submit(True)
        outline = self.store.state()["overview"]["candidates"][0]
        self.store.overview.review(outline["id"], {"decision": "keep", "project_id": pid})
        after = self.store._project(self.store._resolve(pid))
        self.assertEqual(len(self.store.state()["projects"]), 1)
        self.assertEqual(len(after["session_ids"]), 4)
        for field in ("name", "purpose", "activity"):
            self.assertEqual(before[field], after[field])
        self.assertEqual(self.store.state()["overview"]["candidates"], [])

    def test_second_read_requires_selection_and_excludes_human_decisions(self):
        self.census(5); self.submit()
        recognition.assign(self.store, "0", {"decision": "none"})
        recognition.assign(self.store, "1", {"decision": "defer"})
        self.assertEqual({row["id"] for row in self.store.overview.clarification_inputs()}, {"2", "3", "4"})
        for ids in ([], ["0"], ["2", "2"], ["1", "2", "3", "4"]):
            with self.assertRaises(StoreError):
                self.store.overview.clarify({"session_ids": ids})
        self.assertFalse(self.store.state()["control"]["running"])
        self.store.overview.clarify({"session_ids": ["2", "3"]})
        work = self.store._get("overview")
        self.assertEqual([row["session_id"] for row in work["deep_queue"]], ["2", "3"])
        self.assertIsNone(work["deep_project_id"])
        self.assertEqual(self.store.state()["control"]["mode"], "deep")
        with self.assertRaises(StoreError):
            self.store.overview.clarify({"session_ids": ["4"]})

    def test_first_pass_679_titles_is_one_call_and_plan_survives_restart(self):
        self.census(679)
        self.assertEqual(len(self.store.overview.inputs()), 679)
        self.assertEqual(self.store.state()["overview"]["batches_total"], 1)
        self.store.close(); self.store = Store(self.temp.name)
        self.assertEqual(len(self.store.overview.inputs()), 679)
        self.submit()
        self.census(800)
        self.assertEqual(len(self.store.overview.inputs()), 80)
        self.assertEqual(self.store.state()["overview"]["batches_total"], 2)

    def test_initial_budget_falls_back_without_omitting_titles(self):
        with patch("hoomy.recognition.FIRST_PASS_CHARS", 1):
            self.census(100)
        self.assertEqual(len(self.store.overview.inputs()), 80)
        self.assertEqual(self.store.state()["overview"]["pass_total"], 100)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.store.control({"consent": True, "inference_enabled": True})
        self.generation = self.store.state()["control"]["generation"]

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def envelope(self, **values):
        return dict(values, generation=self.generation, request_id=self.request)

    def census(self, count=5, reexamine=False, title=None):
        self.request = self.store.overview.start({"reexamine": reexamine})["request_id"]
        rows = [{"session_id": str(i), "title": title or f"Shape task {i}", "cwd": "/shape", "source_path": f"/source/{i}",
                 "size": 4, "modified_at": "2026-01-01T00:00:00Z", "title_source": "codex_index"} for i in range(count)]
        for offset in range(0, max(1, len(rows)), 500):
            self.store.overview.catalog(self.envelope(records=rows[offset:offset + 500],
                complete=offset + 500 >= len(rows), files_seen=count))

    def submit(self, project=False, existing=None):
        ids = [r["session_id"] for r in self.store.overview.inputs()]
        proposals = [{"name": "Shape", "purpose": "Build shape", "rationale": "Shared purpose", "session_ids": ids,
                      "existing_project_id": existing}] if project else []
        self.store.overview.result(self.envelope(input_ids=ids, model="gpt-5.6-luna", elapsed_seconds=1, input_chars=100,
            projects=proposals, unassigned=[] if project else [{"session_id": sid, "outcome": "no_project", "reason": "One-off task"} for sid in ids]))

    def kept_project(self, count=5, inactive=False):
        self.census(count)
        self.submit(True)
        oid = self.store.state()["overview"]["candidates"][0]["id"]
        return self.store.overview.review(oid, {"decision": "inactive" if inactive else "keep"})["project_id"]

    def test_batches_resume_and_finish_without_rereading(self):
        with patch("hoomy.recognition.BATCH_SIZE", 2), patch("hoomy.recognition.FIRST_PASS_CHARS", 0):
            self.census()
            self.submit()
            state = self.store.state()
            self.assertTrue(state["control"]["running"])
            self.assertEqual(state["overview"]["pass_done"], 2)
            self.assertEqual(state["overview"]["batches_total"], 3)
            self.store.close()
            self.store = Store(self.temp.name)
            self.submit(); self.submit()
            overview = self.store.state()["overview"]
            self.assertEqual(overview["examined"], 5)
            self.assertEqual(overview["no_project"], 5)
            self.assertEqual(overview["unexamined"], 0)
            self.assertFalse(self.store.state()["control"]["running"])
            self.census()
            self.assertEqual(self.store.overview.inputs(), [])
            self.assertEqual(self.store.state()["overview"]["considered"], 5)
            self.assertFalse(self.store.state()["control"]["running"])
            self.census(reexamine=True)
            self.assertEqual(self.store.state()["overview"]["pass_reexamined"], 5)

    def test_new_and_changed_titles_only(self):
        self.census(2); self.submit()
        self.census(3)
        self.assertEqual([r["session_id"] for r in self.store.overview.inputs()], ["2"])
        self.submit()
        self.census(3, title="Changed metadata")
        self.assertEqual(len(self.store.overview.inputs()), 3)

    def test_provisional_groups_merge_across_batches(self):
        with patch("hoomy.recognition.BATCH_SIZE", 2), patch("hoomy.recognition.FIRST_PASS_CHARS", 0):
            self.census(4); self.submit(True)
            context = recognition.memory_context(self.store)
            self.assertEqual(len(context), 1)
            self.assertTrue(context[0]["provisional"])
            self.assertEqual(context[0]["examples"], [])
            self.submit(True, context[0]["id"])
            candidates = self.store.state()["overview"]["candidates"]
            self.assertEqual(len(candidates), 1)
            self.assertEqual(len(candidates[0]["sources"]), 4)

    def test_assignment_removes_pending_source_and_survives_reclassification(self):
        pid = self.kept_project(2)
        self.census(3); self.submit(True)
        recognition.assign(self.store, "2", {"decision": "project", "project_id": pid})
        self.assertEqual(self.store.state()["overview"]["candidates"], [])
        self.assertIn("2", self.store.state()["projects"][0]["session_ids"])
        recognition.assign(self.store, "0", {"decision": "none"})
        self.assertNotIn("0", self.store.state()["projects"][0]["session_ids"])
        memory = recognition.memory_context(self.store)[0]
        self.assertTrue(any(not x["belongs"] for x in memory["examples"]))
        self.census(3, reexamine=True)
        self.assertEqual(self.store.overview.inputs(), [])
        self.store.close(); self.store = Store(self.temp.name)
        self.assertNotIn("0", self.store.state()["projects"][0]["session_ids"])

    def test_activity_is_explicit_and_does_not_mean_complete(self):
        pid = self.kept_project(inactive=True)
        project = self.store.state()["projects"][0]
        self.assertEqual(project["activity"], "inactive")
        self.assertEqual(project["status"], "unclear")
        with self.assertRaises(StoreError):
            self.store.overview.deepen(pid, {})
        self.store.correct(pid, {"activity": "active"})
        self.assertEqual(self.store.overview.deepen(pid, {})["queued"], 3)
        self.store.correct(pid, {"activity": "inactive"})
        self.assertFalse(self.store.state()["control"]["running"])
        with self.assertRaises(StoreError):
            self.store.control({"running": True})

    def test_memory_is_bounded_diverse_and_contains_no_body_or_path(self):
        pid = self.kept_project(20)
        with self.store.db:
            for row in self.store.db.execute("SELECT session_id,metadata FROM catalog").fetchall():
                item = json.loads(row[1])
                item["title"] = "Distinct " + chr(97 + int(row[0])) * 4
                self.store.db.execute("UPDATE catalog SET metadata=? WHERE session_id=?", (encode(item), row[0]))
        self.store.correct(pid, {"recognition_rules": "Use shared purpose, not only folders."})
        memory = recognition.memory_context(self.store)[0]
        self.assertLessEqual(len(memory["examples"]), 10)
        self.assertGreater(len(memory["examples"]), 1)
        self.assertEqual(memory["user_rules"], "Use shared purpose, not only folders.")
        self.assertNotIn("/source", encode(memory))
        self.assertNotIn("/shape", encode(memory))
        self.assertNotIn("messages", memory)

    def test_explicit_none_remains_locked_on_first_content_ingest(self):
        from pathlib import Path
        from hoomy.collector import snapshot
        self.kept_project()
        recognition.assign(self.store, "0", {"decision": "none"})
        path = Path(self.temp.name) / "source.jsonl"
        path.write_text(json.dumps({"type": "session_meta", "payload": {"id": "0", "cwd": "/shape"}}) + "\n" +
                        json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "shape"}}) + "\n")
        self.store.ingest(dict(snapshot(path), generation=self.generation))
        self.assertEqual(self.store.db.execute("SELECT assignment_locked FROM sessions WHERE id='0'").fetchone()[0], 1)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM associations WHERE session_id='0'").fetchone()[0], 0)

    def test_migration_does_not_fabricate_legacy_coverage(self):
        self.census()
        with self.store.db:
            self.store.db.execute("DELETE FROM settings WHERE key IN ('recognition_tracking_started','legacy_coverage_note')")
        self.store.close(); self.store = Store(self.temp.name)
        state = self.store.state()["overview"]
        self.assertEqual(state["examined"], 0)
        self.assertEqual(state["unexamined"], 5)
        self.assertIn("anciennes passes", state["coverage_note"])

    def test_pause_then_assignment_prunes_future_batch(self):
        with patch("hoomy.recognition.BATCH_SIZE", 2), patch("hoomy.recognition.FIRST_PASS_CHARS", 0):
            self.census(4); self.submit()
            pending = [r["session_id"] for r in self.store.overview.inputs()]
            self.store.control({"running": False})
            recognition.assign(self.store, pending[0], {"decision": "defer"})
            self.store.control({"running": True})
            self.assertEqual(len(self.store.overview.inputs()), 1)
            self.submit()
            self.assertEqual(self.store.state()["overview"]["unexamined"], 0)

    def test_unassigned_and_missing_outputs_are_visible_not_retried(self):
        payload = {"conversations": [{"id": "a"}, {"id": "b"}], "known_projects": []}
        result = validate({"projects": [], "unassigned": [{"session_id": "a", "outcome": "no_project", "reason": "One-off"}]}, payload)
        self.assertEqual({x["session_id"] for x in result["unassigned"]}, {"a", "b"})
        self.assertEqual(result["unassigned"][1]["outcome"], "ambiguous")

    def test_terminal_titles_are_not_examples(self):
        self.assertEqual(clean_title("[41] user: root@host:/tmp# docker ps"), "")
        self.assertEqual(clean_title("CONTAINER ID IMAGE COMMAND"), "")
        self.assertEqual(clean_title("The following is the Codex agent history whose request action you are assessing"), "")
        self.assertEqual(clean_title("Review deployment documentation"), "Review deployment documentation")

    def test_replayed_batch_and_running_assignment_are_rejected(self):
        self.census()
        with self.assertRaises(StoreError):
            recognition.assign(self.store, "0", {"decision": "none"})
        ids = [r["session_id"] for r in self.store.overview.inputs()]
        payload = self.envelope(input_ids=ids, projects=[], model="gpt-5.6-luna", elapsed_seconds=1, input_chars=10)
        self.store.overview.result(payload)
        with self.assertRaises(StoreError):
            self.store.overview.result(payload)

    def test_reset_clears_tracking_and_human_memory(self):
        self.kept_project()
        recognition.assign(self.store, "0", {"decision": "none"})
        self.store.reset({"confirm": "DELETE_HOOMY_DATA"})
        self.assertEqual(recognition.memory_context(self.store), [])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM title_assessments").fetchone()[0], 0)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM title_decisions").fetchone()[0], 0)
