import hashlib
import importlib.util
import json
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('export_public', Path(__file__).parents[1] / 'export_public.py')
export = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(export)


class PublicExport(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / 'repo'
        self.repo.mkdir()
        (self.repo / 'run.sh').write_text('#!/bin/sh\nexit 0\n')
        (self.repo / 'run.sh').chmod(0o755)
        (self.repo / 'knowledge-base').mkdir()
        (self.repo / 'knowledge-base' / 'private.md').write_text('not exported')
        self.manifest = self.base / 'manifest.json'
        self.value = {'schema_version': 1, 'release': {'name': 'Hoomy', 'version': '0.0.1', 'status': 'review-candidate'}, 'base_source_commit': 'a' * 40, 'files': [{'path': 'run.sh', 'kind': 'code', 'license': 'Apache-2.0'}], 'excluded_prefixes': [{'path': 'knowledge-base', 'reason': 'private_knowledge'}], 'excluded_files': []}
        self.write_manifest()
        self.output = self.base / 'snapshot'

    def write_manifest(self):
        self.manifest.write_text(json.dumps(self.value))

    def run_export(self):
        return export.prepare_export(self.repo, self.manifest, self.output)

    def test_exact_snapshot_hashes_exec_and_no_private_read(self):
        original = Path.open
        def safe_open(path, *args, **kwargs):
            self.assertNotIn('private.md', str(path))
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', safe_open):
            result = self.run_export()
        self.assertEqual({p.name for p in self.output.iterdir()}, {'run.sh', 'EXPORT-PROVENANCE.json'})
        self.assertEqual(result['files'][0]['sha256'], hashlib.sha256((self.output / 'run.sh').read_bytes()).hexdigest())
        self.assertEqual(result['files'][0]['mode'], 0o755)
        self.assertEqual(json.loads((self.output / 'EXPORT-PROVENANCE.json').read_text()), result)
        self.assertNotIn('private.md', json.dumps(result))
        self.assertNotIn('EXPORT-PROVENANCE.json', {p['path'] for p in result['files']})

    def test_unexpected_file_and_missing_file_stop_before_output(self):
        (self.repo / 'new.py').write_text('unexpected')
        with self.assertRaisesRegex(ValueError, 'unclassified'):
            self.run_export()
        self.assertFalse(self.output.exists())
        (self.repo / 'new.py').unlink()
        (self.repo / 'run.sh').unlink()
        with self.assertRaisesRegex(ValueError, 'missing_approved'):
            self.run_export()

    def test_manifest_cannot_approve_auth_database_or_evidence(self):
        for name in ['secret/key.pem', '.codex/auth.json', 'data/project.db', 'input.jsonl', '../run.sh', '/run.sh', 'a/../run.sh', 'a\\b', 'private/input.json', 'github.env', 'auth.json']:
            with self.subTest(name=name):
                self.value['files'][0]['path'] = name
                self.write_manifest()
                with self.assertRaises(ValueError):
                    self.run_export()

    def test_symlink_leaf_ancestor_and_private_prefix_rejected(self):
        (self.repo / 'run.sh').unlink()
        (self.repo / 'run.sh').symlink_to(self.manifest)
        with self.assertRaisesRegex(ValueError, 'nonregular'):
            self.run_export()
        (self.repo / 'run.sh').unlink()
        (self.repo / 'run.sh').write_text('ok')
        (self.repo / 'knowledge-base' / 'private.md').unlink()
        (self.repo / 'knowledge-base').rmdir()
        (self.repo / 'knowledge-base').symlink_to(self.base, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.run_export()

    def test_output_never_overwrites_and_clean_pin_required(self):
        self.output.mkdir()
        (self.output / 'keep').write_text('preserved')
        with self.assertRaisesRegex(ValueError, 'unsafe_export_output'):
            self.run_export()
        self.assertEqual((self.output / 'keep').read_text(), 'preserved')
        with self.assertRaisesRegex(ValueError, 'source_not_clean'):
            export.prepare_export(self.repo, self.manifest, self.base / 'second', require_clean=True)

    def test_overlapping_and_duplicate_manifest_fields_rejected(self):
        self.value['excluded_files'] = [{'path': 'run.sh', 'reason': 'excluded'}]
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'overlapping'):
            self.run_export()
        self.manifest.write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaisesRegex(ValueError, 'duplicate_manifest_key'):
            self.run_export()

    def test_explicit_research_selection_does_not_open_unselected_records(self):
        (self.repo / 'research').mkdir()
        (self.repo / 'research' / 'public.md').write_text('Authored finding')
        (self.repo / 'research' / 'private.md').write_text('Excluded evidence')
        self.value['files'].append({'path': 'research/public.md', 'kind': 'research', 'license': 'CC-BY-4.0'})
        self.value['excluded_prefixes'].append({'path': 'research', 'reason': 'unselected_research'})
        self.write_manifest()
        result = self.run_export()
        self.assertEqual(result['excluded_summary']['unselected_research'], 1)
        self.assertTrue((self.output / 'research' / 'public.md').exists())
        self.assertFalse((self.output / 'research' / 'private.md').exists())

    def test_same_clean_commit_ignores_local_cache_and_artifact_counts(self):
        (self.repo / '.gitignore').write_text('__pycache__/\nnode_modules/\nartifacts/\nknowledge-base/\n')
        self.value['files'].append({'path': '.gitignore', 'kind': 'configuration', 'license': 'Apache-2.0'})
        self.write_manifest()
        def git(*args):
            return subprocess.check_output(['git', '-C', str(self.repo), *args], stderr=subprocess.DEVNULL)
        git('init', '-q')
        git('add', 'run.sh', '.gitignore')
        git('add', '-f', 'knowledge-base/private.md')
        git('-c', 'user.name=Fictional Contributor', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'test: fictional reviewed source')
        first = export.prepare_export(self.repo, self.manifest, self.output, require_clean=True)
        first_proof = (self.output / 'EXPORT-PROVENANCE.json').read_bytes()
        for name in ['__pycache__', 'node_modules', 'artifacts']:
            folder = self.repo / name
            folder.mkdir()
            (folder / 'ignored.txt').write_text('Different local generated state')
        (self.repo / 'knowledge-base' / 'ignored-local.md').write_text('Ignored private working input')
        second_output = self.base / 'second'
        second = export.prepare_export(self.repo, self.manifest, second_output, require_clean=True)
        self.assertEqual(first, second)
        self.assertEqual(first_proof, (second_output / 'EXPORT-PROVENANCE.json').read_bytes())
        self.assertEqual(first['source_commit'], git('rev-parse', 'HEAD').decode().strip())
        self.assertFalse(second['source_dirty'])
        self.assertNotIn('generated_or_git_state', second['excluded_summary'])
        self.assertEqual(second['excluded_summary']['private_knowledge'], 1)

    def test_destination_swap_cannot_write_outside_or_delete_replacement(self):
        outside = self.base / 'outside'
        outside.mkdir()
        (outside / 'keep').write_text('untouched')
        original = export._create
        swaps = 0
        def swap(root, name):
            nonlocal swaps
            if not swaps:
                swaps += 1
                self.output.rename(self.base / 'detached')
                self.output.symlink_to(outside, target_is_directory=True)
            return original(root, name)
        with patch.object(export, '_create', swap):
            with self.assertRaisesRegex(ValueError, 'symlink'):
                self.run_export()
        self.assertEqual({p.name for p in outside.iterdir()}, {'keep'})
        self.assertEqual((outside / 'keep').read_text(), 'untouched')
        self.assertFalse((outside / 'run.sh').exists())

    def test_source_path_replacement_cleans_partial_export(self):
        original = export._open
        calls = 0
        def replace(root, name):
            nonlocal calls
            calls += 1
            if calls == 2:
                path = self.repo / name
                path.unlink()
                path.write_text('different')
            return original(root, name)
        with patch.object(export, '_open', replace):
            with self.assertRaisesRegex(ValueError, 'source_changed'):
                self.run_export()
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
