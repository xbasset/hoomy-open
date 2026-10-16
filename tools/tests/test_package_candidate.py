import hashlib
import json
import importlib.util
from pathlib import Path
import tarfile
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch


MODULE = Path(__file__).resolve().parents[1] / "package_candidate.py"
spec = importlib.util.spec_from_file_location("package_candidate", MODULE)
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.snapshot = self.root / "snapshot"
        self.snapshot.mkdir()
        (self.snapshot / "README.md").write_text("# Fictional candidate\n", encoding="utf-8")
        (self.snapshot / "run.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (self.snapshot / "run.sh").chmod(0o755)
        self.files = {name: hashlib.sha256((self.snapshot / name).read_bytes()).hexdigest() for name in ("README.md", "run.sh")}
        self.provenance = {"source_commit": "a" * 40, "manifest_sha256": "b" * 64}

    def package(self, name):
        return subject.package_snapshot(self.snapshot, self.files, self.root / name, self.provenance)

    def test_same_content_yields_identical_archive_and_safe_members(self):
        first = self.package("first")
        (self.snapshot / "README.md").touch()
        second = self.package("second")
        self.assertEqual(first, second)
        archive = self.root / "first" / first["archive"]["name"]
        self.assertEqual(archive.read_bytes(), (self.root / "second" / archive.name).read_bytes())
        with tarfile.open(archive) as stream:
            self.assertEqual(stream.getnames(), sorted(self.files))
            for member in stream:
                self.assertEqual((member.uid, member.gid, member.mtime), (0, 0, 0))
                self.assertTrue(member.isfile())
                self.assertNotIn(".git", member.name)
        self.assertEqual((self.root / "first").stat().st_mode & 0o777, 0o700)

    def test_tamper_rejects_and_removes_partial_archive(self):
        (self.snapshot / "README.md").write_text("Changed reviewed source", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "snapshot_digest_changed"):
            self.package("candidate")
        self.assertFalse((self.root / "candidate").exists())

    def test_extra_or_missing_file_refused(self):
        (self.snapshot / "unexpected.md").write_text("Unapproved", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "snapshot_file_set_changed"):
            self.package("candidate")
        (self.snapshot / "unexpected.md").unlink()
        (self.snapshot / "run.sh").unlink()
        with self.assertRaisesRegex(ValueError, "snapshot_file_set_changed"):
            self.package("candidate")

    def test_private_and_history_paths_refused_even_if_manifest_claims_approval(self):
        for name in ("secret/github.env", ".git/config", "private/capture.json", "knowledge-base/source.json", "signing.pem"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                subject.approved_path(name)

    def test_symlink_leaf_or_directory_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "README.md").write_text("Not reviewed", encoding="utf-8")
        (self.snapshot / "README.md").unlink()
        (self.snapshot / "README.md").symlink_to(outside / "README.md")
        with self.assertRaisesRegex(ValueError, "nonregular_snapshot"):
            self.package("candidate")
        (self.snapshot / "README.md").unlink()
        (self.snapshot / "linked").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink_snapshot"):
            self.package("candidate")

    def test_existing_destination_never_overwritten(self):
        self.package("candidate")
        sentinel = self.root / "candidate" / "sentinel"
        sentinel.write_text("Preserved", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "output_exists"):
            self.package("candidate")
        self.assertEqual(sentinel.read_text(), "Preserved")

    def test_symlink_output_parent_and_invalid_pin_refused(self):
        linked = self.root / "linked"
        linked.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink_directory"):
            subject.package_snapshot(self.snapshot, self.files, linked / "candidate", self.provenance)
        with self.assertRaisesRegex(ValueError, "invalid_source_commit"):
            subject.package_snapshot(self.snapshot, self.files, self.root / "candidate", {"source_commit": "not-a-commit"})

    def test_export_wrapper_requires_clean_gate_and_keeps_proof_outside_self_hash(self):
        module = types.ModuleType("export_public")
        def prepare(repository, manifest, destination, *, require_clean):
            self.assertTrue(require_clean)
            destination.mkdir()
            payload = b"# Fictional clean source\n"
            (destination / "README.md").write_bytes(payload)
            report = {"source_commit": "c" * 40, "source_dirty": False,
                      "files": [{"path": "README.md", "sha256": hashlib.sha256(payload).hexdigest()}]}
            (destination / "EXPORT-PROVENANCE.json").write_text(json.dumps(report), encoding="utf-8")
            return report
        module.prepare_export = prepare
        with patch.dict("sys.modules", {"export_public": module}):
            report = subject.package_candidate(self.snapshot, self.root / "manifest.json", self.root / "candidate")
        self.assertEqual(report["source_commit"], "c" * 40)
        self.assertEqual(report["file_count"], 2)
        self.assertEqual(len(report["provenance"]["files"]), 1)
        self.assertFalse(list(self.root.glob(".hoomy-export-*")))

    def test_dirty_export_never_becomes_candidate(self):
        module = types.ModuleType("export_public")
        module.prepare_export = lambda *args, **kwargs: {"source_commit": None, "source_dirty": True}
        with patch.dict("sys.modules", {"export_public": module}), self.assertRaisesRegex(ValueError, "source_not_clean"):
            subject.package_candidate(self.snapshot, self.root / "manifest.json", self.root / "candidate")
        self.assertFalse((self.root / "candidate").exists())

    def test_parent_replaced_with_symlink_before_archive_read_is_refused(self):
        nested = self.snapshot / "nested"
        nested.mkdir()
        (nested / "public.md").write_text("Approved", encoding="utf-8")
        self.files["nested/public.md"] = hashlib.sha256(b"Approved").hexdigest()
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "public.md").write_text("Unapproved", encoding="utf-8")
        original = subject._snapshot_files
        calls = []
        def swap(descriptor):
            found = original(descriptor)
            if not calls:
                nested.rename(self.snapshot / "moved")
                nested.symlink_to(outside, target_is_directory=True)
            calls.append(True)
            return found
        with patch.object(subject, "_snapshot_files", swap), self.assertRaises(OSError):
            self.package("candidate")
        self.assertFalse((self.root / "candidate").exists())

    def actual_export_fixture(self):
        spec = importlib.util.spec_from_file_location("export_public", MODULE.with_name("export_public.py"))
        export = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(export)
        subprocess.run(["git", "init", "-q", str(self.snapshot)], check=True)
        subprocess.run(["git", "-C", str(self.snapshot), "add", "README.md", "run.sh"], check=True)
        subprocess.run(["git", "-C", str(self.snapshot), "-c", "user.name=Fictional", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "test: fictional snapshot"], check=True)
        private = self.snapshot / "knowledge-base"
        private.mkdir()
        (private / "record.md").write_text("Fictional excluded record", encoding="utf-8")
        (self.snapshot / ".git/info/exclude").write_text("knowledge-base/\n", encoding="utf-8")
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"schema_version": 1,
            "release": {"name": "Hoomy", "version": "0.0.1", "status": "review-candidate"},
            "base_source_commit": "a" * 40,
            "files": [{"path": name, "kind": "code", "license": "Apache-2.0"} for name in self.files],
            "excluded_prefixes": [{"path": "knowledge-base", "reason": "private_knowledge"}], "excluded_files": []}), encoding="utf-8")
        with patch.dict("sys.modules", {"export_public": export}):
            report = subject.package_candidate(self.snapshot, manifest, self.root / "candidate")
        expected = subprocess.check_output(["git", "-C", str(self.snapshot), "rev-parse", "HEAD"], text=True).strip()
        self.assertEqual(report["source_commit"], expected)
        self.assertFalse(report["provenance"]["source_dirty"])
        with tarfile.open(self.root / "candidate" / report["archive"]["name"]) as stream:
            self.assertEqual(stream.getnames(), ["EXPORT-PROVENANCE.json", "README.md", "run.sh"])
            self.assertEqual(stream.getmember("run.sh").mode, 0o755)
        self.assertNotIn("record.md", json.dumps(report))
        return export, manifest, report

    def test_actual_export_gate_packages_clean_git_pin_without_private_records(self):
        self.actual_export_fixture()

    def test_ignored_cache_changes_do_not_change_actual_source_archive(self):
        export, manifest, first = self.actual_export_fixture()
        exclude = self.snapshot / ".git/info/exclude"
        exclude.write_text(exclude.read_text() + "node_modules/\n__pycache__/\nartifacts/\n", encoding="utf-8")
        for name in ("node_modules", "__pycache__", "artifacts"):
            folder = self.snapshot / name
            folder.mkdir()
            (folder / "ignored-file").write_text("Fictional generated cache", encoding="utf-8")
        (self.snapshot / "knowledge-base/another-ignored.md").write_text("Fictional private ignored addition", encoding="utf-8")
        with patch.dict("sys.modules", {"export_public": export}):
            second = subject.package_candidate(self.snapshot, manifest, self.root / "cached-candidate")
        self.assertEqual(first["source_commit"], second["source_commit"])
        self.assertEqual(first["archive"], second["archive"])
        self.assertEqual(first["provenance"], second["provenance"])
        self.assertNotIn("generated_or_git_state", first["provenance"]["excluded_summary"])

    def test_output_swap_cannot_redirect_archive_or_metadata(self):
        outside = self.root / "outside"
        outside.mkdir()
        original = subject._open_snapshot_file
        swapped = []
        def swap(descriptor, name):
            if not swapped:
                candidate = self.root / "candidate"
                candidate.rename(self.root / "moved-candidate")
                candidate.symlink_to(outside, target_is_directory=True)
                swapped.append(True)
            return original(descriptor, name)
        with patch.object(subject, "_open_snapshot_file", swap), self.assertRaisesRegex(ValueError, "output_directory_changed"):
            self.package("candidate")
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((self.root / "moved-candidate/candidate.json").exists())


if __name__ == "__main__":
    unittest.main()
