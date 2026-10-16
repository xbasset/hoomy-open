import json
import tempfile
import time
import unittest

from hoomy.collector import normalize
from hoomy.live_discovery import checkpoint, context, publish
from hoomy import recognition
from hoomy.store import Store, StoreError, encode, now


class LiveDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.store.control({"consent": True, "inference_enabled": True})
        self.generation = self.store.state()["control"]["generation"]
        self.request = self.store.overview.start({})["request_id"]
        records = [{"session_id": str(i), "title": f"Unusual irrigation task {i}", "cwd": "/orchid",
                    "source_path": f"/source/{i}", "size": 10, "modified_at": "2026-01-01T00:00:00Z", "title_source": "index"} for i in range(5)]
        self.store.overview.catalog(self.env(records=records, complete=True))
        self.store.overview.result(self.env(projects=[], model="gpt-5.6-luna", input_ids=[r["session_id"] for r in self.store.overview.inputs()], elapsed_seconds=1, input_chars=1))
        with self.store.db:
            for pid in ("orchid", "garden"):
                self.store.db.execute("INSERT INTO projects VALUES (?,?,?,NULL,?,?)", (pid, encode({"name": pid, "purpose": pid}), encode({"name": pid, "purpose": pid}), now(), now()))
        self.store.overview.complete({"include_content": True, "automatic_associations": True})
        self.request = self.store._get("overview")["request_id"]
        self.evidence = {}
        for sid in ("0", "1", "2"):
            raw = (json.dumps({"type": "session_meta", "payload": {"id": sid, "cwd": "/orchid"}}) + "\n" +
                   json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "Orchid irrigation evidence."}}) + "\n").encode()
            item = normalize(raw, f"/source/{sid}", "2026-01-01T00:00:00Z")
            self.store.ingest(dict(item, generation=self.generation))
            self.evidence[sid] = {"snapshot_id": item["snapshot_id"], "excerpts": [{"message_id": item["messages"][0]["id"], "start": 0, "text": "Orchid irrigation evidence."}]}

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def env(self, **payload):
        return dict(payload, generation=self.generation, request_id=self.request)

    def finding(self, sid="0", pid="orchid", alt=None):
        return self.env(proposal={"name": "Orchid", "purpose": "Manage irrigation", "rationale": "The excerpt names the irrigation project.",
            "session_ids": [sid], "existing_project_id": pid, "alternative_project_id": alt, "ambiguity_reason": "Both projects cover irrigation." if alt else ""}, excerpt_evidence={sid: self.evidence[sid]})

    def test_automatic_links_are_live_not_human_examples(self):
        publish(self.store, self.finding(alt="garden"))
        state = self.store.state()
        self.assertTrue(state["control"]["running"])
        self.assertEqual(state["overview"]["linked"], 1)
        self.assertEqual(state["overview"]["automatic_associations"][0]["alternative_id"], "garden")
        self.assertFalse(self.store.db.execute("SELECT 1 FROM title_decisions WHERE session_id='0'").fetchone())
        self.assertEqual(next(p for p in recognition.memory_context(self.store) if p["id"] == "orchid")["examples"], [])

    def test_one_click_correction_is_authoritative_and_feeds_agent(self):
        publish(self.store, self.finding(alt="garden"))
        revision = context(self.store)["revision"]
        recognition.assign(self.store, "0", {"decision": "project", "project_id": "garden"})
        self.assertGreater(context(self.store)["revision"], revision)
        publish(self.store, self.finding())
        self.assertEqual([r[0] for r in self.store.db.execute("SELECT project_id FROM catalog_links WHERE session_id='0'")], ["garden"])
        self.assertEqual(self.store.state()["overview"]["automatic_associations"][0]["corrected"], 1)
        self.assertTrue(next(p for p in recognition.memory_context(self.store) if p["id"] == "garden")["examples"])

    def test_new_project_waits_for_live_approval_then_accepts_more_sources(self):
        publish(self.store, self.finding(pid=None))
        outline = self.store.state()["overview"]["candidates"][0]
        self.assertEqual(self.store.state()["overview"]["linked"], 0)
        result = self.store.overview.review(outline["id"], {"decision": "keep", "name": "New irrigation project"})
        self.assertTrue(self.store.state()["control"]["running"])
        publish(self.store, self.finding(sid="1", pid="outline:" + outline["id"]))
        self.assertEqual(self.store.db.execute("SELECT project_id FROM catalog_links WHERE session_id='1'").fetchone()[0], result["project_id"])

    def test_checkpoint_continues_without_repeating_inspected_sources(self):
        publish(self.store, self.finding())
        result = checkpoint(self.store, self.env(attempted_ids=["0", "1"], excerpt_evidence={k: self.evidence[k] for k in ("0", "1")}))
        self.assertTrue(result["continuing"])
        self.assertEqual(set(self.store._get("overview")["queue"]), {"2", "3", "4"})
        self.store.close(); self.store = Store(self.temp.name)
        self.assertEqual(set(self.store._get("overview")["queue"]), {"2", "3", "4"})

    def test_no_progress_or_global_limit_stops_instead_of_looping(self):
        result = checkpoint(self.store, self.env(attempted_ids=[], excerpt_evidence={}))
        self.assertFalse(result["continuing"])

    def test_global_deadline_stops_after_saving_progress(self):
        with self.store.db:
            state = self.store._get("overview")
            state["agent_deadline"] = time.time() - 1
            self.store._set("overview", state)
        publish(self.store, self.finding())
        result = checkpoint(self.store, self.env(attempted_ids=["0"], excerpt_evidence={"0": self.evidence["0"]}))
        self.assertFalse(result["continuing"])
        self.assertEqual(self.store.state()["overview"]["linked"], 1)

    def test_ambiguous_target_requires_another_validated_project(self):
        with self.assertRaises(StoreError):
            publish(self.store, self.finding(alt="orchid"))
        self.assertEqual(self.store.state()["overview"]["linked"], 0)

    def test_forged_evidence_is_atomic_and_pause_rejects_late_findings(self):
        data = self.finding()
        data["excerpt_evidence"]["0"]["excerpts"][0]["text"] = "Invented evidence"
        with self.assertRaises(StoreError):
            publish(self.store, data)
        self.assertEqual(self.store.state()["overview"]["linked"], 0)
        self.store.control({"running": False})
        self.store.control({"running": True})
        with self.assertRaises(StoreError):
            publish(self.store, self.finding())

    def test_repeated_new_finding_is_not_duplicated(self):
        publish(self.store, self.finding(pid=None))
        publish(self.store, self.finding(pid=None))
        self.assertEqual(len(self.store.state()["overview"]["candidates"]), 1)

    def test_reset_removes_automatic_associations(self):
        publish(self.store, self.finding())
        self.store.reset({"confirm": "DELETE_HOOMY_DATA"})
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM automatic_links").fetchone()[0], 0)
