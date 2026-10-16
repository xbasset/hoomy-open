"""Immutable public evidence and durable receipts do not replace approved context."""

import copy
import hashlib
import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import test_store as fixtures
from hoomy.ingestion import canonical, digest
from hoomy.server import make_handler
from hoomy.store import Store, StoreError


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.StoreTests()
        self.fixture.setUp()
        self.store = self.fixture.store
        self.pid = self.fixture.analyzed()
        profile = self.store.dialogue.profile(self.pid)
        self.profile = self.store.dialogue.update_profile({"project_id": self.pid,
            "revision": profile["revision"], "fields": {"goal": "Preserve approved project intent."}})

    def tearDown(self):
        self.fixture.tearDown()

    def payload(self, sequence=1, device="device-a", text="Une preuve publique, conservée dans sa langue.", archive=False):
        projection = {"version": 1, "messages": [{"role": "user", "text": text,
            "line_start": 2, "line_end": 2, "timestamp": None}]}
        source = {"path": "/fictional-history/" + ("archived_sessions" if archive else "sessions") + "/source.jsonl",
            "cwd": "/fictional-project", "archive_occurrence": archive, "modified_at": "2026-10-12T12:00:00Z"}
        return {"principal": {"account_id": "github:42", "workspace_id": "personal:42", "device_id": device},
            "policy": {"id": device, "revision": 2, "content_tier": "conversation_visible_v1",
                "collection_enabled": True, "roots": ["/fictional-history/sessions", "/fictional-history/archived_sessions"],
                "exclusions": [], "project_bindings": [{"project_id": self.pid, "cwd_roots": ["/fictional-project"]}]},
            "event": {"schema_version": 1, "event_id": str(uuid.uuid4()), "type": "conversation.projected",
                "project_id": self.pid, "policy_revision": 2, "connector": "codex", "session_id": "s",
                "source_location": source, "sequence": sequence, "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "projected_sha256": digest(projection), "observed_at": "2026-10-12T12:01:00Z", "title": "Imported source title",
                "coverage": {"excluded_records": 3, "excluded_sensitive_messages": 1, "excluded_context_messages": 1, "excluded_oversized_records": 0},
                "projection": projection}}

    def read(self, source_id, **options):
        return self.store.ingestion.evidence({"principal": {"account_id": "github:42", "workspace_id": "personal:42"},
            "project_id": self.pid, "source_id": source_id, **options})

    def excluded(self, sequence=2, **options):
        payload = self.payload(sequence=sequence, **options)
        event = payload["event"]
        for key in ("projection", "coverage", "title", "source_sha256", "projected_sha256"):
            del event[key]
        event.update(type="conversation.source_excluded", exclusion={"reason": "internal_approval_review",
            "discriminator": "internal_guardian", "header_sha256": hashlib.sha256(b"fictional session header").hexdigest()})
        return payload

    def test_internal_source_exclusion_is_sticky_across_late_events_replays_and_restart(self):
        projected = self.payload(sequence=3)
        prior_receipt = self.store.ingestion.accept(projected)
        source_id = self.store.ingestion.metadata()[0]["id"]
        blobs = [tuple(row) for row in self.store.db.execute("SELECT * FROM ingestion_blobs")]
        excluded = self.excluded(sequence=1)
        receipt = self.store.ingestion.accept(excluded)
        self.assertEqual(set(receipt), {"receipt_id", "event_id", "project_id", "event_sha256", "state",
            "inference_state", "received_at", "source_version_id", "event_type", "header_sha256"})
        self.assertEqual(receipt["event_sha256"], digest(excluded["event"]))
        self.assertEqual(receipt["header_sha256"], excluded["event"]["exclusion"]["header_sha256"])
        self.store.close()
        self.store = self.fixture.store = Store(self.fixture.directory.name)
        self.assertEqual(self.store.ingestion.accept(excluded), receipt)
        self.assertEqual(self.store.ingestion.accept(projected), prior_receipt)
        self.assertEqual(self.store.ingestion.receipt({"principal": excluded["principal"], "event_id": receipt["event_id"]}), receipt)
        self.assertEqual(self.store.ingestion.metadata(), [])
        self.assertEqual(self.store.ingestion.metadata([source_id]), [])
        self.assertEqual(self.store.ingestion.source_ids(self.pid), [])
        with self.assertRaises(StoreError) as caught:
            self.read(source_id)
        self.assertEqual(caught.exception.status, 404)
        for sequence in (2, 3, 4):
            with self.assertRaises(StoreError) as caught:
                self.store.ingestion.accept(self.payload(sequence=sequence))
            self.assertEqual(caught.exception.status, 409)
        self.assertEqual([tuple(row) for row in self.store.db.execute("SELECT * FROM ingestion_blobs WHERE message_count>0")], blobs)
        profile = self.store.dialogue.profile(self.pid)
        self.assertEqual(profile["fields"], self.profile["fields"])
        self.assertEqual(profile["revision"], self.profile["revision"])
        self.assertFalse(any(source.get("cached") for source in profile["sources"]))
        coverage = profile["evidence_coverage"]["projected"]
        self.assertEqual(coverage["excluded_internal_sources"], 1)
        self.assertEqual(coverage["source_locations"], 0)
        self.assertEqual(coverage["unique_projected_messages"], 0)
        self.assertEqual(coverage["unique_projection_blobs"], 0)
        self.assertEqual(coverage["retained_history"]["unique_projection_blobs"], 1)
        self.assertEqual(coverage["retained_history"]["immutable_versions"], 2)

    def test_exclusions_are_exact_location_scoped_and_do_not_leak_across_accounts(self):
        excluded = self.excluded(sequence=1)
        receipt = self.store.ingestion.accept(excluded)
        for payload in (self.payload(device="device-b"), self.payload(archive=True)):
            self.store.ingestion.accept(payload)
        foreign = self.payload()
        foreign["principal"].update(account_id="another", workspace_id="another-workspace")
        self.store.ingestion.accept(foreign)
        self.assertEqual(len(self.store.ingestion.metadata()), 3)
        for key in ("account_id", "workspace_id", "device_id"):
            with self.assertRaises(StoreError) as caught:
                self.store.ingestion.receipt({"principal": dict(excluded["principal"], **{key: "another"}), "event_id": receipt["event_id"]})
            self.assertEqual(caught.exception.status, 404)
        source = self.store.ingestion.metadata()[0]["id"]
        with self.assertRaises(StoreError) as caught:
            self.store.ingestion.evidence({"principal": {"account_id": "unrelated", "workspace_id": "unrelated"},
                "project_id": self.pid, "source_id": source})
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(self.store.ingestion.coverage(self.pid)["excluded_internal_sources"], 1)

    def test_exclusion_schema_rejects_bodies_unproved_classifications_and_scope_changes(self):
        for alter in (lambda p: p["event"].update(projection={"version": 1, "messages": []}),
                      lambda p: p["event"].update(title="Not allowed"),
                      lambda p: p["event"].update(source_sha256="0" * 64),
                      lambda p: p["event"]["exclusion"].update(reason="erase_inventory"),
                      lambda p: p["event"]["exclusion"].update(discriminator="assistant"),
                      lambda p: p["event"]["exclusion"].update(header_sha256="invalid"),
                      lambda p: p["event"]["source_location"].update(cwd="/outside-project"),
                      lambda p: p["event"].update(project_id="another-project"),
                      lambda p: p["policy"].update(revision=3)):
            payload = self.excluded()
            alter(payload)
            with self.assertRaises(StoreError):
                self.store.ingestion.accept(payload)
        self.assertEqual(self.count("ingestion_events"), 0)
        self.assertEqual(self.count("ingestion_source_exclusions"), 0)

    def test_exclusion_receipt_failure_rolls_back_classification_and_all_version_rows(self):
        self.store.db.execute("CREATE TRIGGER reject_exclusion BEFORE INSERT ON ingestion_events BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.ingestion.accept(self.excluded(sequence=1))
        for table in ("ingestion_blobs", "ingestion_locations", "ingestion_versions", "ingestion_events", "ingestion_source_exclusions"):
            self.assertEqual(self.count(table), 0)

    def test_excluded_event_and_sequence_reuse_cannot_change_classification_proof(self):
        payload = self.excluded(sequence=1)
        receipt = self.store.ingestion.accept(payload)
        original = [tuple(row) for row in self.store.db.execute("SELECT * FROM ingestion_source_exclusions")]
        for preserve_id in (True, False):
            changed = copy.deepcopy(payload)
            if not preserve_id:
                changed["event"]["event_id"] = str(uuid.uuid4())
            changed["event"]["exclusion"]["header_sha256"] = "0" * 64
            with self.assertRaises(StoreError) as caught:
                self.store.ingestion.accept(changed)
            self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.store.ingestion.accept(payload), receipt)
        self.assertEqual([tuple(row) for row in self.store.db.execute("SELECT * FROM ingestion_source_exclusions")], original)
        self.assertEqual(self.count("ingestion_events"), 1)

    def count(self, table):
        return self.store.db.execute("SELECT count(*) FROM " + table).fetchone()[0]

    def test_receipt_replay_lost_ack_and_restart_preserve_exact_hashes_and_context(self):
        payload = self.payload()
        receipt = self.store.ingestion.accept(payload)
        self.assertEqual(receipt["event_sha256"], digest(payload["event"]))
        self.assertEqual(receipt["projected_sha256"], digest(payload["event"]["projection"]))
        self.assertEqual(receipt["state"], "stored")
        self.assertEqual(receipt["inference_state"], "not_requested")
        self.store.close()
        self.store = self.fixture.store = Store(self.fixture.directory.name)
        self.assertEqual(self.store.ingestion.accept(payload), receipt)
        self.assertEqual(self.store.ingestion.receipt({"principal": payload["principal"], "event_id": receipt["event_id"]}), receipt)
        profile = self.store.dialogue.profile(self.pid)
        self.assertEqual(profile["revision"], self.profile["revision"])
        self.assertEqual(profile["fields"], self.profile["fields"])
        self.assertEqual(self.count("snapshots"), 1)
        self.assertEqual(self.count("ingestion_blobs"), 1)
        source = next(s for s in profile["sources"] if s.get("cached"))
        self.assertEqual(self.read(source["id"])["messages"][0]["text"], payload["event"]["projection"]["messages"][0]["text"])

    def test_same_event_or_sequence_cannot_describe_different_content(self):
        payload = self.payload()
        self.store.ingestion.accept(payload)
        for retain_event in (True, False):
            changed = copy.deepcopy(payload)
            if not retain_event:
                changed["event"]["event_id"] = str(uuid.uuid4())
            changed["event"]["projection"]["messages"][0]["text"] = "Different public evidence."
            changed["event"]["projected_sha256"] = digest(changed["event"]["projection"])
            with self.assertRaises(StoreError) as caught:
                self.store.ingestion.accept(changed)
            self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.count("ingestion_events"), 1)
        self.assertEqual(self.count("ingestion_versions"), 1)

    def test_out_of_order_versions_and_clock_skew_do_not_choose_global_winner(self):
        newer = self.payload(sequence=2, text="Newer source sequence.")
        newer["event"]["source_location"]["modified_at"] = "2025-01-01T00:00:00Z"
        self.store.ingestion.accept(newer)
        older = self.payload(sequence=1, text="Older source sequence.")
        older["event"]["source_location"]["modified_at"] = "2027-01-01T00:00:00Z"
        self.store.ingestion.accept(older)
        other = self.payload(device="device-b", text="Divergent second machine.")
        self.store.ingestion.accept(other)
        sources = self.store.ingestion.metadata()
        self.assertEqual(len(sources), 2)
        a = next(s for s in sources if s["device_id"] == "device-a")
        self.assertEqual(a["sequence"], 2)
        self.assertEqual(a["version_count"], 2)
        self.assertEqual(self.read(a["id"])["messages"][0]["text"], "Newer source sequence.")
        self.assertEqual(self.count("ingestion_versions"), 3)

    def test_identical_projected_blob_keeps_devices_and_archive_locations(self):
        for payload in (self.payload(), self.payload(device="device-b"), self.payload(archive=True)):
            self.store.ingestion.accept(payload)
        self.assertEqual(self.count("ingestion_blobs"), 1)
        self.assertEqual(self.count("ingestion_locations"), 3)
        coverage = self.store.dialogue.profile(self.pid)["evidence_coverage"]
        self.assertEqual(coverage["legacy_unassigned_sources"], 1)
        self.assertEqual(coverage["projected"]["source_locations"], 3)
        self.assertEqual(coverage["projected"]["unique_projected_messages"], 1)
        self.assertEqual(self.store.session("s")["status"], "analyzed")

    def test_wrong_project_path_policy_revision_and_unknown_fields_fail_closed(self):
        for alter in (lambda p: p["event"].update(project_id="wrong-project"),
                      lambda p: p["event"]["source_location"].update(cwd="/fictional-project-other"),
                      lambda p: p["policy"].update(revision=3),
                      lambda p: p["policy"].update(collection_enabled=False),
                      lambda p: p["event"].update(raw_b64="forbidden"),
                      lambda p: p["event"]["projection"]["messages"][0].update(role="system"),
                      lambda p: p["event"]["projection"]["messages"][0].update(channel="analysis"),
                      lambda p: p["event"].update(projected_sha256="0" * 64)):
            payload = self.payload()
            alter(payload)
            with self.assertRaises(StoreError):
                self.store.ingestion.accept(payload)
        self.assertEqual(self.count("ingestion_events"), 0)
        self.assertEqual(self.count("ingestion_blobs"), 0)

    def test_receipt_and_cached_read_are_account_workspace_device_project_scoped(self):
        payload = self.payload()
        receipt = self.store.ingestion.accept(payload)
        source = self.store.ingestion.metadata()[0]["id"]
        for key in ("account_id", "workspace_id", "device_id"):
            principal = dict(payload["principal"], **{key: "another"})
            with self.assertRaises(StoreError) as caught:
                self.store.ingestion.receipt({"principal": principal, "event_id": receipt["event_id"]})
            self.assertEqual(caught.exception.status, 404)
        for principal in ({"account_id": "another", "workspace_id": "personal:42"},
                          {"account_id": "github:42", "workspace_id": "another"}):
            with self.assertRaises(StoreError):
                self.store.ingestion.evidence({"principal": principal, "project_id": self.pid, "source_id": source})
        with patch("pathlib.Path.open", side_effect=AssertionError("No Mac paths may be opened")):
            evidence = self.read(source)
        self.assertFalse(evidence["source_sha256_verified"])
        self.assertEqual(evidence["provenance"]["device_id"], "device-a")

    def test_receipt_failure_rolls_back_blob_location_version_and_association(self):
        self.store.db.execute("CREATE TRIGGER reject_receipt BEFORE INSERT ON ingestion_events BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
        payload = self.payload()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.ingestion.accept(payload)
        for table in ("ingestion_blobs", "ingestion_locations", "ingestion_versions", "ingestion_events"):
            self.assertEqual(self.count(table), 0)
        self.store.db.execute("DROP TRIGGER reject_receipt")
        self.store.ingestion.accept(payload)
        self.assertEqual(self.count("ingestion_events"), 1)

    def test_bounded_excerpts_preserve_lines_ids_and_explicit_partial_coverage(self):
        payload = self.payload(text="é" * 10000)
        self.store.ingestion.accept(payload)
        source = self.store.ingestion.metadata()[0]["id"]
        result = self.read(source, limit=1)
        message = result["messages"][0]
        self.assertEqual(len(message["text"].encode()), 8192)
        self.assertTrue(message["text_truncated"])
        self.assertEqual(message["line_start"], 2)
        self.assertTrue(message["id"].startswith(payload["event"]["projected_sha256"] + ":2:2:"))
        self.assertTrue(result["coverage"]["partial"])
        self.assertEqual(result["total_messages"], 1)
        self.assertEqual(self.read(source, offset=1)["messages"], [])
        with self.assertRaises(StoreError):
            self.read(source, limit=51)

    def test_oversized_record_omissions_remain_visible_in_profile_and_cached_reads(self):
        payload = self.payload()
        payload["event"]["coverage"]["excluded_oversized_records"] = 1
        self.store.ingestion.accept(payload)
        source = next(item for item in self.store.dialogue.profile(self.pid)["sources"] if item.get("cached"))
        self.assertTrue(source["partial"])
        self.assertTrue(source["warning"])
        self.assertTrue(self.read(source["id"])["coverage"]["source_partial"])
        self.assertTrue(self.read(source["id"])["coverage"]["partial"])

    def test_inventory_commands_cannot_erase_sources_or_enable_inference(self):
        self.store.control({"running": False, "inference_enabled": False})
        before = self.store._get("control")
        payload = self.payload()
        receipt = self.store.ingestion.accept(payload)
        inventory = self.payload(device="device-b")
        inventory["event"]["type"] = "inventory.replace"
        with self.assertRaises(StoreError):
            self.store.ingestion.accept(inventory)
        self.assertEqual(self.store._get("control"), before)
        self.assertEqual(self.count("ingestion_locations"), 1)
        self.assertEqual(self.store.ingestion.receipt({"principal": payload["principal"], "event_id": payload["event"]["event_id"]}), receipt)

    def test_additive_migration_rehearsal_on_consistent_fixture_copy(self):
        baseline = {table: [tuple(row) for row in self.store.db.execute("SELECT * FROM " + table)]
                    for table in ("projects", "associations", "snapshots", "analyses", "project_context_answers")}
        with tempfile.TemporaryDirectory() as directory:
            destination = sqlite3.connect(Path(directory) / "hoomy.sqlite3")
            self.store.db.backup(destination)
            for table in ("ingestion_source_exclusions", "ingestion_events", "ingestion_versions", "ingestion_locations", "ingestion_blobs"):
                destination.execute("DROP TABLE " + table)
            destination.commit()
            destination.close()
            migrated = Store(directory)
            try:
                for table, rows in baseline.items():
                    self.assertEqual([tuple(row) for row in migrated.db.execute("SELECT * FROM " + table)], rows)
                self.assertEqual(migrated.dialogue.profile(self.pid)["revision"], self.profile["revision"])
                self.assertEqual(migrated.db.execute("PRAGMA quick_check").fetchone()[0], "ok")
            finally:
                migrated.close()

    def test_http_fixed_routes_duplicate_keys_and_loopback_boundary(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.store))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            def post(route, body, headers=None):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                connection.request("POST", route, body=body, headers={"Content-Type": "application/json", **(headers or {})})
                response = connection.getresponse()
                value = response.status, json.loads(response.read())
                connection.close()
                return value
            payload = self.payload()
            status, receipt = post("/api/ingestion/events", canonical(payload))
            self.assertEqual(status, 200)
            lookup = {"principal": payload["principal"], "event_id": receipt["event_id"]}
            self.assertEqual(post("/api/ingestion/receipts/query", canonical(lookup)), (200, receipt))
            excluded = self.excluded()
            status, excluded_receipt = post("/api/ingestion/events", canonical(excluded))
            self.assertEqual(status, 200)
            self.assertEqual(excluded_receipt["event_type"], "conversation.source_excluded")
            self.assertEqual(excluded_receipt["event_sha256"], digest(excluded["event"]))
            self.assertEqual(excluded_receipt["header_sha256"], excluded["event"]["exclusion"]["header_sha256"])
            self.assertEqual(len(excluded_receipt), 10)
            self.assertEqual(post("/api/ingestion/events", canonical(excluded)), (200, excluded_receipt))
            self.assertEqual(post("/api/ingestion/receipts/query", canonical(lookup)), (200, receipt))
            self.assertEqual(post("/api/ingestion/events", b'{"event":{},"event":{}}')[0], 400)
            self.assertEqual(post("/api/ingestion/events", canonical(payload), {"Host": "attacker.example"})[0], 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_rehearsal_helper_never_changes_its_backup_and_keeps_legacy_fingerprints(self):
        from hoomy.ingestion_rehearsal import rehearse
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / "backup.sqlite3"
            connection = sqlite3.connect(backup)
            self.store.db.backup(connection)
            connection.close()
            original = hashlib.sha256(backup.read_bytes()).hexdigest()
            result = rehearse(backup, Path(directory) / "isolated", self.pid)
            self.assertEqual(result["integrity"], "ok")
            self.assertTrue(result["context_revision_preserved"])
            self.assertEqual(result["projected_coverage"]["unique_projection_blobs"], 1)
            self.assertEqual(result["projected_coverage"]["source_locations"], 2)
            self.assertTrue(result["existing_ingestion_preserved"])
            self.assertTrue(result["excluded_receipt_replay"])
            self.assertEqual(result["excluded_coverage"]["source_locations"], 1)
            self.assertEqual(result["excluded_coverage"]["excluded_internal_sources"], 1)
            self.assertEqual(result["excluded_coverage"]["retained_history"]["source_locations"], 2)
            self.assertEqual(hashlib.sha256(backup.read_bytes()).hexdigest(), original)

    def test_rehearsal_preserves_preexisting_projected_receipts_and_blobs(self):
        from hoomy.ingestion_rehearsal import rehearse
        payload = self.payload()
        receipt = self.store.ingestion.accept(payload)
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / "backup.sqlite3"
            connection = sqlite3.connect(backup)
            self.store.db.backup(connection)
            connection.close()
            result = rehearse(backup, Path(directory) / "isolated", self.pid)
            self.assertEqual(result["existing_ingestion_fingerprints"]["ingestion_events"]["rows"], 1)
            self.assertEqual(result["existing_ingestion_fingerprints"]["ingestion_blobs"]["rows"], 1)
            self.assertTrue(result["existing_ingestion_preserved"])
            migrated = Store(Path(directory) / "isolated")
            try:
                self.assertEqual(migrated.ingestion.receipt({"principal": payload["principal"], "event_id": receipt["event_id"]}), receipt)
                source = next(item for item in migrated.ingestion.metadata() if item["device_id"] == "device-a")
                evidence = migrated.ingestion.evidence({"principal": {"account_id": "github:42", "workspace_id": "personal:42"},
                    "project_id": self.pid, "source_id": source["id"]})
                self.assertEqual(evidence["messages"][0]["text"], payload["event"]["projection"]["messages"][0]["text"])
            finally:
                migrated.close()


if __name__ == "__main__":
    unittest.main()
