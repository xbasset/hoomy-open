"""Offline deployment checks never touch an installed service or production data."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import plistlib
import sys
import tempfile
import unittest
from unittest.mock import patch

from hoomy.store import Store


SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "project-state" / "rehearse_migration.py"
spec = importlib.util.spec_from_file_location("state_migration_rehearsal", SCRIPT)
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)
service_spec = importlib.util.spec_from_file_location("state_watch_service", SCRIPT.with_name("prepare_watch_service.py"))
service = importlib.util.module_from_spec(service_spec)
service_spec.loader.exec_module(service)


class RehearsalTests(unittest.TestCase):
    def test_migration_is_copy_only_additive_private_and_preserves_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            original = root / "snapshot"
            store = Store(str(original))
            store.close()
            snapshot = original / "hoomy.sqlite3"
            with sqlite3.connect(snapshot) as database:
                for table in ("project_state_retry_links", "project_state_citations", "project_state_assertions", "project_state_calls",
                              "project_state_inputs", "project_state_requests", "project_state_runs"):
                    database.execute("DROP TABLE " + table)
            database.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            database.close()
            before = hashlib.sha256(snapshot.read_bytes()).digest()
            result = subject.rehearse(snapshot, root / "new-copy")
            self.assertEqual(result["status"], "rehearsed")
            self.assertEqual(len(result["new_tables"]), 7)
            self.assertEqual(hashlib.sha256(snapshot.read_bytes()).digest(), before)
            self.assertEqual((root / "new-copy").stat().st_mode & 0o777, 0o700)
            self.assertEqual((root / "new-copy" / "hoomy.sqlite3").stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError):
                subject.rehearse(snapshot, root / "new-copy")

    def previous_state_snapshot(self, root):
        original = root / "snapshot"
        store = Store(str(original))
        store.close()
        snapshot = original / "hoomy.sqlite3"
        with sqlite3.connect(snapshot) as database:
            database.execute("DROP TABLE project_state_retry_links")
            database.execute("""INSERT INTO project_state_runs
                (id,account_id,workspace_id,project_id,context_revision,corpus_revision,input_hash,manifest,status,created_at,failure_code)
                VALUES ('prior-run','fictional-owner','fictional-workspace','fictional-project',?,?,?,'{}','needs_review','2026-01-01T00:00:00Z','ambiguous_completion')""", ("a" * 64, "b" * 64, "c" * 64))
            database.execute("INSERT INTO project_state_inputs VALUES ('prior-run',?)", ('{"messages":[]}',))
            database.execute("""INSERT INTO project_state_calls
                (run_id,call_id,phase,chunk_index,status,input,payload_hash,dispatched_at)
                VALUES ('prior-run','prior-call','extract',0,'interrupted',?,?, '2026-01-01T00:00:00Z')""", ('{"task":"extract","messages":[]}', "d" * 64))
        database.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        database.close()
        return snapshot

    def test_retry_addition_preserves_all_37_tables_and_unknown_dispatch_exactly(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            snapshot = self.previous_state_snapshot(root)
            before = subject.fingerprints(snapshot)
            self.assertEqual(len(before), 37)
            source_sha = subject.file_digest(snapshot)
            result = subject.rehearse(snapshot, root / "copy", expected_new_tables=["project_state_retry_links"], snapshot_sha256=source_sha)
            self.assertEqual(result["new_tables"], ["project_state_retry_links"])
            self.assertEqual(result["existing_tables_preserved"], 37)
            self.assertTrue(result["existing_table_fingerprints_unchanged"])
            after = subject.fingerprints(root / "copy" / "hoomy.sqlite3")
            self.assertEqual({key: after[key] for key in before}, before)
            self.assertEqual(subject.file_digest(snapshot), source_sha)
            with sqlite3.connect(root / "copy" / "hoomy.sqlite3") as database:
                self.assertEqual(database.execute("SELECT status,failure_code,output FROM project_state_runs WHERE id='prior-run'").fetchone(), ("needs_review", "ambiguous_completion", None))
                self.assertEqual(database.execute("SELECT status,method,output FROM project_state_calls WHERE call_id='prior-call'").fetchone(), ("interrupted", None, None))

    def test_same_count_mutation_of_old_unknown_ledger_is_a_failed_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            snapshot = self.previous_state_snapshot(root)
            source_sha = subject.file_digest(snapshot)
            class MutatingStore(Store):
                def __init__(self, directory):
                    super().__init__(directory)
                    self.db.execute("UPDATE project_state_calls SET status='observed' WHERE call_id='prior-call'")
                    self.db.commit()
            with patch.object(subject, "Store", MutatingStore):
                with self.assertRaisesRegex(ValueError, "existing_table_fingerprints_changed"):
                    subject.rehearse(snapshot, root / "copy", expected_new_tables=["project_state_retry_links"])
            self.assertEqual(subject.file_digest(snapshot), source_sha)

    def test_nonempty_snapshot_wal_and_wrong_sha_are_refused_before_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            snapshot = self.previous_state_snapshot(root)
            with self.assertRaisesRegex(ValueError, "snapshot_sha_mismatch"):
                subject.rehearse(snapshot, root / "copy", snapshot_sha256="0" * 64)
            self.assertFalse((root / "copy").exists())
            wal = snapshot.with_name(snapshot.name + "-wal")
            wal.write_bytes(b"uncheckpointed")
            with self.assertRaisesRegex(ValueError, "snapshot_wal_not_empty"):
                subject.rehearse(snapshot, root / "copy")
            self.assertFalse((root / "copy").exists())

    def test_unreviewed_additive_table_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            snapshot = self.previous_state_snapshot(root)
            with self.assertRaisesRegex(ValueError, "unexpected_migration_table"):
                subject.rehearse(snapshot, root / "copy", expected_new_tables=["project_state_unknown"])
            self.assertFalse((root / "copy").exists())
            with self.assertRaisesRegex(ValueError, "unexpected_migration_table"):
                subject.rehearse(snapshot, root / "other-copy", expected_new_tables=[])

    def test_auth_file_and_symlink_are_not_project_snapshots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            auth = root / "auth.sqlite"
            auth.write_bytes(b"not a project database")
            alias = root / "hoomy.sqlite3"
            alias.symlink_to(auth)
            for source in (auth, alias):
                with self.assertRaises(ValueError):
                    subject.rehearse(source, root / "new")
            self.assertFalse((root / "new").exists())


class WatchServiceTests(unittest.TestCase):
    def test_prepares_private_reviewable_unit_without_install_or_home_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            account = type("Account", (), {"pw_dir": str(root / "home"), "pw_name": "operator"})()
            with patch.object(service.pwd, "getpwuid", return_value=account):
                result = service.prepare(SCRIPT.parents[2], sys.executable, sys.executable, root / "candidate", project_id="11111111-1111-4111-8111-111111111111")
            self.assertFalse(result["installed"])
            self.assertFalse(result["started"])
            plist_path = Path(result["plist"])
            unit = plistlib.loads(plist_path.read_bytes())
            self.assertEqual(plist_path.stat().st_mode & 0o777, 0o600)
            self.assertFalse((root / "home").exists())
            self.assertEqual(unit["ProgramArguments"][-3:], ["watch", "--project-id", "11111111-1111-4111-8111-111111111111"])
            self.assertEqual(unit["Label"], "dev.hoomy.state-executor.11111111-1111-4111-8111-111111111111")
            self.assertTrue(unit["StandardOutPath"].endswith("/11111111-1111-4111-8111-111111111111/status.log"))
            self.assertEqual(unit["WorkingDirectory"], "/private/tmp")
            self.assertNotIn("run_id", json.dumps(unit))
            self.assertIs(unit["KeepAlive"], False)
            self.assertIs(unit["RunAtLoad"], True)
            self.assertFalse(any("sudo" in arg or "launchctl" in arg for arg in unit["ProgramArguments"]))

    def test_invalid_project_ids_fail_before_creating_unit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for project in ("../another-project", "project-alpha", "11111111-1111-4111-8111-11111111111A", None):
                with self.assertRaises((ValueError, TypeError, AttributeError)):
                    service.prepare(SCRIPT.parents[2], sys.executable, sys.executable, root / "candidate", project_id=project)
                self.assertFalse((root / "candidate").exists())

    def test_candidate_rerun_cannot_overwrite_reviewed_unit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            service.prepare(SCRIPT.parents[2], sys.executable, sys.executable, root / "candidate", project_id="11111111-1111-4111-8111-111111111111")
            with self.assertRaises(ValueError):
                service.prepare(SCRIPT.parents[2], sys.executable, sys.executable, root / "candidate", project_id="11111111-1111-4111-8111-111111111111")

    def test_insufficient_capacity_fails_before_creating_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            snapshot = root / "hoomy.sqlite3"
            snapshot.write_bytes(b"snapshot")
            with patch.object(subject.shutil, "disk_usage", return_value=type("Space", (), {"free": 0})()):
                with self.assertRaises(ValueError):
                    subject.rehearse(snapshot, root / "new")
            self.assertFalse((root / "new").exists())


if __name__ == "__main__":
    unittest.main()
