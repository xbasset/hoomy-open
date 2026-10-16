"""Exact Guardian provenance excludes a source before physical body reads."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hoomy_agent.client import AgentError
from hoomy_agent.collector import SourceError, SourceExclusion, read_source, reconcile
from hoomy_agent.outbox import Backpressure, Outbox
from hoomy_agent.policy import approved_policy, canonical, sha256


class SourceExclusionTests(unittest.TestCase):
    def test_projected_ack_refuses_wrong_type_mixed_fields_and_unknown_events(self):
        header = self.write('cli')
        self.path.write_bytes(header + json.dumps({'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': 'Visible fixture'}}).encode() + b'\n')
        reconcile(self.outbox, self.policy, 2, 1000)
        event, digest = self.outbox.pending(2, 1000)[0]
        receipt = {'receipt_id': event['event_id'], 'event_id': event['event_id'], 'project_id': event['project_id'], 'event_sha256': digest,
            'state': 'stored', 'inference_state': 'not_requested', 'received_at': '2026-10-12T00:00:00Z', 'source_version_id': 'fixture-version',
            'projected_sha256': event['projected_sha256']}
        for changed in ({**receipt, 'event_type': 'conversation.source_excluded'}, {**receipt, 'header_sha256': '0' * 64},
                        {**receipt, 'event_id': 'wrong-event'}):
            with self.assertRaises(AgentError): self.outbox.accepted(event, digest, changed)
        with self.assertRaises(AgentError): self.outbox.accepted({**event, 'type': 'unsupported'}, digest, receipt)
        self.assertEqual(self.outbox.pending(2, 1000), [(event, digest)])
        self.outbox.accepted(event, digest, receipt)
        self.assertEqual(self.outbox.counts()['outbox_count'], 0)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.root = self.home / '.codex/sessions'
        self.root.mkdir(parents=True)
        self.path = self.root / 'rollout-fixture.jsonl'
        self.cwd = str(self.home / 'project')
        self.policy = approved_policy({'content_tier': 'conversation_visible_v1', 'collection_enabled': True,
            'roots': [str(self.root)], 'exclusions': [], 'project_bindings': [{'project_id': 'fixture-project', 'cwd_roots': [self.cwd]}]})
        self.outbox = Outbox(self.home / 'private')
        self.addCleanup(self.outbox.close)

    def write(self, source, **extra):
        meta = {'id': 'fixture-session', 'cwd': self.cwd, 'source': source, **extra}
        header = json.dumps({'type': 'session_meta', 'payload': meta}).encode() + b'\n'
        self.path.write_bytes(header + b'\xffunparseable private body must not be read')
        return header

    def test_exact_guardian_headers_emit_metadata_only_and_physically_read_only_header(self):
        for source, discriminator in (({'subagent': {'other': 'guardian'}}, 'legacy_subagent_guardian'), ({'internal': 'guardian'}, 'internal_guardian')):
            header = self.write(source, base_instructions={'text': 'withheld header instructions'})
            observed = bytearray()
            fdopen = os.fdopen
            class Observed(io.RawIOBase):
                def __init__(self, descriptor, mode, closefd): self.raw = fdopen(descriptor, mode, buffering=0, closefd=closefd)
                def readable(self): return True
                def readinto(self, destination):
                    data = self.raw.read(len(destination)); observed.extend(data); destination[:len(data)] = data
                    return len(data)
                def close(self): self.raw.close(); super().close()
            with mock.patch('hoomy_agent.collector.os.fdopen', side_effect=lambda descriptor, mode, buffering=-1, closefd=True: Observed(descriptor, mode, closefd)):
                result = read_source(self.path, self.root, self.policy)
            self.assertIsInstance(result, SourceExclusion)
            self.assertEqual(bytes(observed), header)
            self.assertEqual(result.header_sha256, sha256(header))
            self.assertEqual(result.discriminator, discriminator)
            stats = reconcile(self.outbox, self.policy, 2, 1000)
            self.assertEqual(stats['excluded'], 1)
            self.assertEqual(stats['failures'], {})
            event, _ = self.outbox.pending(2, 1000)[-1]
            self.assertEqual(set(event), {'schema_version', 'event_id', 'type', 'project_id', 'policy_revision', 'connector', 'session_id', 'source_location', 'sequence', 'observed_at', 'exclusion'})
            self.assertEqual(event['type'], 'conversation.source_excluded')
            self.assertEqual(event['exclusion'], {'reason': 'internal_approval_review', 'discriminator': discriminator, 'header_sha256': sha256(header)})
            self.assertNotIn('withheld', canonical(event).decode())
            self.assertEqual(reconcile(self.outbox, self.policy, 2, 1001)['unchanged'], 1)

    def test_non_exact_sources_and_thread_source_alone_do_not_exclude_real_dialogue(self):
        for source in ('cli', 'vscode', {'subagent': {'thread_spawn': {'parent_thread_id': 'fixture-parent'}}},
                       {'subagent': {'other': 'guardian', 'extra': True}}, {'internal': 'guardian', 'extra': True}):
            self.write(source, thread_source='guardian_review')
            with self.assertRaises(SourceError): read_source(self.path, self.root, self.policy)
        self.write({'internal': 'guardian'}, cwd='/outside')
        with self.assertRaisesRegex(SourceError, 'cwd_not_approved'): read_source(self.path, self.root, self.policy)
        alias = self.root / 'rollout-symlink.jsonl'
        alias.symlink_to(self.path)
        with self.assertRaises(SourceError): read_source(alias, self.root, self.policy)

    def test_classification_preserves_prior_immutable_capture_and_sequence_and_backpressure(self):
        header = self.write({'internal': 'guardian'})
        fingerprint = list((self.path.stat().st_dev, self.path.stat().st_ino, self.path.stat().st_size, self.path.stat().st_mtime_ns, self.path.stat().st_ctime_ns))
        key = sha256(canonical(['fixture-project', 'codex', 'fixture-session', str(self.path), False]))
        def old_event(sequence, event_id):
            return {'event_id': event_id, 'policy_revision': 2, 'sequence': sequence, 'type': 'conversation.projected', 'private_fixture': 'prior bytes retained'}
        self.outbox.capture(key, 'a' * 64, 2, old_event, fingerprint, 2)
        prior = self.outbox.pending(2, 1000)[0]
        self.outbox.max_bytes = 0
        with self.assertRaises(Backpressure): reconcile(self.outbox, self.policy, 2, 1000)
        self.assertEqual(self.outbox.db.execute('SELECT sequence,projection_adapter_revision FROM checkpoints').fetchone(), (1, 2))
        self.outbox.max_bytes = 256 * 1024 * 1024
        reconcile(self.outbox, self.policy, 2, 1001)
        pending = self.outbox.pending(2, 1001)
        self.assertEqual(pending[0], prior)
        event, digest = pending[1]
        self.assertEqual(event['sequence'], 2)
        self.assertEqual(event['exclusion']['header_sha256'], sha256(header))
        receipt = {'receipt_id': event['event_id'], 'event_id': event['event_id'], 'project_id': event['project_id'], 'event_sha256': digest,
            'state': 'stored', 'inference_state': 'not_requested', 'received_at': '2026-10-12T00:00:00Z', 'source_version_id': 'fixture-version',
            'event_type': 'conversation.source_excluded', 'header_sha256': sha256(header)}
        for changed in ({**receipt, 'header_sha256': '0' * 64}, {**receipt, 'projected_sha256': '0' * 64}, {**receipt, 'event_type': 'conversation.projected'}):
            with self.assertRaises(AgentError): self.outbox.accepted(event, digest, changed)
        self.assertEqual(self.outbox.pending(2, 1001), pending)
        self.outbox.close()
        self.outbox = Outbox(self.home / 'private')
        self.addCleanup(self.outbox.close)
        self.assertEqual(self.outbox.pending(2, 1001), pending)
        self.outbox.accepted(event, digest, receipt)
        self.assertEqual(self.outbox.pending(2, 1001), [prior])
