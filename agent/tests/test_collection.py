"""Fixture-only projection, durable replay, positive scope and source boundaries."""

import json
import io
import os
import re
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hoomy_agent.client import Agent, AgentError, APIError
from hoomy_agent.collector import SourceError, project, read_source, reconcile, source_paths
from hoomy_agent.outbox import Backpressure, Outbox
from hoomy_agent.policy import approved_policy, canonical, project_for_cwd, sha256
from hoomy_agent.storage import State


def encoded(*records):
    return b"".join(json.dumps(record, ensure_ascii=False).encode() + b"\n" for record in records)


def message(text, role="user", channel=None):
    payload = {"type": "message", "role": role, "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}]}
    if channel is not None:
        payload["channel"] = channel
    return {"type": "response_item", "timestamp": "2026-10-12T12:00:00Z", "payload": payload}


class CollectionFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.root = self.home / ".codex/sessions"
        self.root.mkdir(parents=True)
        self.cwd = str(self.home / "work/alpha")
        self.policy = approved_policy({"content_tier": "conversation_visible_v1", "collection_enabled": True,
            "roots": [str(self.root)], "exclusions": [], "project_bindings": [{"project_id": "project-alpha", "cwd_roots": [self.cwd]}]})
        self.outbox = Outbox(self.home / "private")
        self.addCleanup(self.outbox.close)
        self.meta = {"type": "session_meta", "payload": {"id": "session-alpha", "cwd": self.cwd}}
        self.path = self.root / "rollout-alpha.jsonl"

    def write(self, *records):
        raw = encoded(self.meta, *records)
        self.path.write_bytes(raw)
        return raw


class CollectorTests(CollectionFixture):
    def test_guardian_framing_is_excluded_before_title_and_size_but_quoted_discussion_is_kept(self):
        full = 'The following is the Codex agent history whose request action you are assessing.'
        delta = 'The following is the Codex agent history added since your last approval assessment.'
        discussion = 'Explain this reviewer framing: "' + full + '"'
        raw = self.write(message(full + ' private transcript ' * 20000), message(delta + ' private transcript delta'),
                         message('Actual project request'), message(discussion))
        self.assertEqual(reconcile(self.outbox, self.policy, 2, 1000)['captured'], 1)
        event = self.outbox.pending(2, 1000)[0][0]
        self.assertEqual(event['title'], 'Actual project request')
        self.assertEqual([item['text'] for item in event['projection']['messages']], ['Actual project request', discussion])
        self.assertEqual([item['line_start'] for item in event['projection']['messages']], [4, 5])
        self.assertEqual(event['coverage']['excluded_context_messages'], 2)
        self.assertEqual(event['source_sha256'], sha256(raw))
        self.assertEqual(self.path.read_bytes(), raw)
        invalid = (json.dumps(self.meta) + '\n' + json.dumps(message(full + '\ud800')) + '\n').encode()
        with self.assertRaisesRegex(SourceError, 'invalid_utf8'):
            project(invalid, self.cwd, 'session-alpha')

    def test_legacy_checkpoint_adapter_migration_recaptures_once_without_mutating_old_event(self):
        prefix = 'The following is the Codex agent history whose request action you are assessing.'
        self.write(message(prefix), message('Actual request'))
        with mock.patch('hoomy_agent.collector.PROJECTION_ADAPTER_REVISION', 1), mock.patch('hoomy_agent.collector.CONTEXT', re.compile(r'<environment_context>')):
            self.assertEqual(reconcile(self.outbox, self.policy, 2, 1000)['captured'], 1)
        old_event, old_digest = self.outbox.pending(2, 1000)[0]
        checkpoint = self.outbox.db.execute('SELECT source_key,sequence,source_digest,policy_revision,fingerprint FROM checkpoints').fetchone()
        database_path = self.outbox.path
        self.outbox.close()
        with sqlite3.connect(database_path) as database:
            database.execute('ALTER TABLE checkpoints DROP COLUMN projection_adapter_revision')
        self.outbox = Outbox(database_path.parent)
        self.addCleanup(self.outbox.close)
        self.assertEqual(self.outbox.db.execute('SELECT projection_adapter_revision FROM checkpoints').fetchone(), (1,))
        self.outbox.max_bytes = 0
        with self.assertRaises(Backpressure): reconcile(self.outbox, self.policy, 2, 1001)
        self.assertEqual(self.outbox.db.execute('SELECT source_key,sequence,source_digest,policy_revision,fingerprint FROM checkpoints').fetchone(), checkpoint)
        self.assertEqual(self.outbox.db.execute('SELECT projection_adapter_revision FROM checkpoints').fetchone(), (1,))
        self.assertEqual(self.outbox.pending(2, 1001), [(old_event, old_digest)])
        self.outbox.max_bytes = 256 * 1024 * 1024
        self.assertEqual(reconcile(self.outbox, self.policy, 2, 1002)['captured'], 1)
        pending = self.outbox.pending(2, 1002)
        self.assertEqual(pending[0], (old_event, old_digest))
        new_event = pending[1][0]
        self.assertEqual(new_event['source_sha256'], old_event['source_sha256'])
        self.assertNotEqual(new_event['projected_sha256'], old_event['projected_sha256'])
        self.assertNotEqual(new_event['event_id'], old_event['event_id'])
        self.assertEqual(new_event['sequence'], old_event['sequence'] + 1)
        self.assertEqual(new_event['title'], 'Actual request')
        self.assertEqual(self.outbox.db.execute('SELECT projection_adapter_revision FROM checkpoints').fetchone(), (3,))
        self.assertEqual(reconcile(self.outbox, self.policy, 2, 1003)['unchanged'], 1)
        self.assertEqual(self.outbox.counts()['outbox_count'], 2)

    def test_public_projection_omits_analysis_tools_context_and_credentials(self):
        raw = self.write(message("Réviser le changement."), message("Completed.", "assistant", "final"),
            message("private reasoning", "assistant", "analysis"), message("progress", "assistant", "commentary"),
            {"type": "response_item", "payload": {"type": "function_call_output", "output": "private tool output"}},
            message("<environment_context>private environment</environment_context>"),
            message("api_key=privatecredentialmaterial0123456789"),
            message("private system instruction", "system"),
            {"type": "event_msg", "payload": {"type": "agent_message", "message": "private reasoning event"}})
        projection, digest, coverage = project(raw, self.cwd, "session-alpha")
        self.assertEqual([item["text"] for item in projection["messages"]], ["Réviser le changement.", "Completed."])
        self.assertEqual([item["line_start"] for item in projection["messages"]], [2, 3])
        self.assertEqual(digest, sha256(canonical(projection)))
        self.assertNotEqual(digest, sha256(raw))
        self.assertEqual(coverage["excluded_sensitive_messages"], 1)
        self.assertEqual(coverage["excluded_context_messages"], 1)
        reconcile(self.outbox, self.policy, 2, 1000)
        payload = self.outbox.pending(2, 1000)[0][0]
        for withheld in ("private reasoning", "private tool output", "private environment", "privatecredentialmaterial", "private system instruction"):
            self.assertNotIn(withheld, canonical(payload).decode())
        self.assertEqual(payload["source_sha256"], sha256(raw))

    def test_quoted_short_credentials_and_bearer_headers_are_withheld(self):
        raw = self.write(message('Configuration: {"password": "abc"}'), message("Authorization: Bearer abcdefghijklmnopqrstuvwxyz"), message("visible"))
        projection, _, coverage = project(raw, self.cwd, "session-alpha")
        self.assertEqual([item["text"] for item in projection["messages"]], ["visible"])
        self.assertEqual(coverage["excluded_sensitive_messages"], 2)

    def test_timestamp_precision_matches_wire_and_preserves_supported_values(self):
        records = [message("nanosecond"), message("microsecond"), message("offset")]
        values = ["2026-10-12T12:00:00.123456789Z", "2026-10-12T12:00:00.123456Z", "2026-10-12T14:00:00+02:00"]
        for record, value in zip(records, values):
            record["timestamp"] = value
        projection, _, _ = project(self.write(*records), self.cwd, "session-alpha")
        self.assertEqual([item["timestamp"] for item in projection["messages"]], [None, values[1], values[2]])

    def test_positive_cwd_and_component_exclusions(self):
        self.assertIsNone(project_for_cwd(self.policy, ""))
        self.assertIsNone(project_for_cwd(self.policy, self.cwd + "-other"))
        self.assertEqual(project_for_cwd(self.policy, self.cwd + "/nested"), "project-alpha")
        self.policy["exclusions"] = [self.cwd + "/private"]
        self.assertIsNone(project_for_cwd(self.policy, self.cwd + "/private/nested"))
        for cwd in (None, "", self.cwd + "-other"):
            self.path.write_bytes(encoded({"type": "session_meta", "payload": {"id": "s", "cwd": cwd}}, message("must not capture")))
            stats = reconcile(self.outbox, self.policy, 2, 1000)
            self.assertEqual(stats["failures"], {"cwd_not_approved": 1})
        self.assertEqual(self.outbox.counts()["outbox_count"], 0)

    def test_rejected_cwd_physically_reads_only_first_metadata_line(self):
        header = encoded({"type": "session_meta", "payload": {"id": "outside-session", "cwd": "/outside"}})
        body = encoded(message("other project body must not be read"))
        self.path.write_bytes(header + body)
        observed = bytearray()
        real_fdopen = os.fdopen
        class ObservedRaw(io.RawIOBase):
            def __init__(self, descriptor, mode, closefd):
                self.raw = real_fdopen(descriptor, mode, buffering=0, closefd=closefd)
            def readable(self):
                return True
            def readinto(self, destination):
                data = self.raw.read(len(destination))
                observed.extend(data)
                destination[:len(data)] = data
                return len(data)
            def close(self):
                self.raw.close()
                super().close()
        def observe(descriptor, mode, buffering=-1, closefd=True):
            raw = ObservedRaw(descriptor, mode, closefd)
            return raw if buffering == 0 else io.BufferedReader(raw)
        with mock.patch("hoomy_agent.collector.os.fdopen", side_effect=observe):
            with self.assertRaisesRegex(SourceError, "cwd_not_approved"):
                read_source(self.path, self.root, self.policy)
        self.assertEqual(bytes(observed), header)

    def test_malformed_source_identifiers_and_scalar_paths_are_skipped(self):
        for identity in ("id:with-colon", "id/with-slash", "snowman☃", "bad\ud800", "bad\x7f"):
            header = (json.dumps({"type": "session_meta", "payload": {"id": identity, "cwd": self.cwd}}) + "\n").encode()
            self.path.write_bytes(header + encoded(message("not captured")))
            self.assertIn("unsupported_session_header", reconcile(self.outbox, self.policy, 2, 1000)["failures"])
        for cwd in (self.cwd + "/bad\ud800", self.cwd + "/bad\x7f", self.cwd + "/" + "😀" * 1100):
            # Source JSON can encode surrogate escapes; it is not an admissible path.
            self.path.write_bytes((json.dumps({"type": "session_meta", "payload": {"id": "valid-id", "cwd": cwd}}) + "\n").encode() + encoded(message("not captured")))
            self.assertIn("cwd_not_approved", reconcile(self.outbox, self.policy, 2, 1000)["failures"])
        for path in (self.root / "bad\udcff.jsonl", self.root / "bad\x7f.jsonl"):
            with self.assertRaisesRegex(SourceError, "invalid_source_path"):
                read_source(path, self.root, self.policy)
        self.assertEqual(self.outbox.counts()["outbox_count"], 0)

    def test_stable_read_rejects_symlink_ancestors_and_source(self):
        self.write(message("visible"))
        alias = self.home / "alias"
        alias.symlink_to(self.home / ".codex", target_is_directory=True)
        with self.assertRaises(SourceError):
            read_source(alias / "sessions/rollout-alpha.jsonl", alias / "sessions", self.policy)
        linked = self.root / "rollout-link.jsonl"
        linked.symlink_to(self.path)
        self.assertEqual(list(source_paths(self.root, [])), [self.path])
        with self.assertRaises(SourceError):
            read_source(linked, self.root, self.policy)

    def test_partial_json_and_changed_metadata_do_not_advance_checkpoint(self):
        self.path.write_bytes(encoded(self.meta, message("visible")) + b'{"partial":')
        self.assertEqual(reconcile(self.outbox, self.policy, 2, 1000)["failures"], {"incomplete_or_invalid_json": 1})
        self.write(message("visible"), {"type": "turn_context", "payload": {"cwd": self.cwd + "-other"}})
        self.assertEqual(reconcile(self.outbox, self.policy, 2, 1000)["failures"], {"conflicting_session_scope": 1})
        self.assertEqual(self.outbox.db.execute("SELECT count(*) FROM checkpoints").fetchone()[0], 0)
        for payload in ({}, None, {"cwd": None}):
            self.write({"type": "turn_context", "payload": payload}, message("unknown scope"))
            self.assertIn("unknown_context_scope", reconcile(self.outbox, self.policy, 2, 1000)["failures"])
            self.assertEqual(self.outbox.counts()["outbox_count"], 0)

    def test_changing_descriptor_is_deferred(self):
        self.write(message("visible"))
        actual = os.fstat
        calls = [0]
        def changed(descriptor):
            info = actual(descriptor)
            if info.st_ino == self.path.stat().st_ino:
                calls[0] += 1
                if calls[0] == 2:
                    with self.path.open("ab") as stream:
                        stream.write(b"\n")
                    return actual(descriptor)
            return info
        with mock.patch("hoomy_agent.collector.os.fstat", side_effect=changed):
            with self.assertRaisesRegex(SourceError, "source_changed"):
                read_source(self.path, self.root, self.policy)

    def test_oversized_source_message_and_duplicate_json_are_explicit(self):
        self.write(message("visible"))
        with self.path.open("ab") as stream:
            stream.truncate(256 * 1024 * 1024 + 1)
        self.assertIn("source_oversized", reconcile(self.outbox, self.policy, 2, 1000)["failures"])
        self.write(message("x" * (256 * 1024 + 1)))
        self.assertIn("message_oversized", reconcile(self.outbox, self.policy, 2, 1000)["failures"])
        self.path.write_bytes(encoded(self.meta) + b'{"type":"response_item","type":"event_msg"}\n')
        self.assertIn("duplicate_json_key", reconcile(self.outbox, self.policy, 2, 1000)["failures"])
        self.assertEqual(self.outbox.counts()["outbox_count"], 0)

    def test_streaming_large_total_source_preserves_lines_and_full_source_hash(self):
        hasher = __import__("hashlib").sha256()
        prefix = encoded(self.meta, message("before"))
        with self.path.open("wb") as stream:
            stream.write(prefix)
            hasher.update(prefix)
            opening = b'{"type":"response_item","payload":{"type":"function_call_output","output":"'
            block = opening + b"x" * (512 * 1024) + b'"}}\n'
            # Each record is bounded even when the original source exceeds 16 MiB.
            for _ in range(36):
                stream.write(block)
                hasher.update(block)
            tail = encoded(message("after", "assistant", "final"))
            stream.write(tail)
            hasher.update(tail)
        stats = reconcile(self.outbox, self.policy, 2, 1000)
        self.assertEqual(stats["captured"], 1)
        event = self.outbox.pending(2, 1000)[0][0]
        self.assertEqual(event["source_sha256"], hasher.hexdigest())
        self.assertEqual(event["coverage"]["excluded_oversized_records"], 0)
        self.assertEqual([item["line_start"] for item in event["projection"]["messages"]], [2, 39])
        self.assertLess(len(canonical(event)), 2048)

    def test_unterminated_oversized_record_is_not_accepted_as_partial_capture(self):
        self.write(message("before"))
        with self.path.open("ab") as stream:
            stream.write(b'{"unfinished":"' + b"x" * (2 * 1024 * 1024))
        self.assertIn("incomplete_or_invalid_json", reconcile(self.outbox, self.policy, 2, 1000)["failures"])
        self.assertEqual(self.outbox.counts()["outbox_count"], 0)

    def test_oversized_scope_records_reject_source_before_later_public_text(self):
        for scope in (b'{"type":"turn_context","payload":{"cwd":"/other","padding":"',
                      b'{"payload":{"padding":"',
                      b'{"\\u0074ype":"turn_context","payload":{"cwd":"/other","padding":"'):
            suffix = b'","cwd":"/other"},"type":"turn_context"}\n' if scope == b'{"payload":{"padding":"' else b'"}}\n'
            self.path.write_bytes(encoded(self.meta) + scope + b"x" * (1024 * 1024) + suffix + encoded(message("out of scope")))
            self.assertIn("oversized_scope_unproven", reconcile(self.outbox, self.policy, 2, 1000)["failures"])
            self.assertEqual(self.outbox.counts()["outbox_count"], 0)
            self.assertEqual(self.outbox.db.execute("SELECT count(*) FROM checkpoints").fetchone()[0], 0)

    def test_cwd_moves_within_same_positive_project_remain_eligible(self):
        self.write(message("before"), {"type": "turn_context", "payload": {"cwd": self.cwd + "/app"}}, message("after"))
        self.assertEqual(reconcile(self.outbox, self.policy, 2, 1000)["captured"], 1)
        self.policy["exclusions"] = [self.cwd + "/private"]
        for cwd in (self.cwd + "-other", self.cwd + "/private", "/unknown", self.cwd + "/../other"):
            self.write({"type": "turn_context", "payload": {"cwd": cwd}}, message("outside"))
            self.assertIn("conflicting_session_scope", reconcile(self.outbox, self.policy, 2, 1001)["failures"])
        changed_meta = {"type": "session_meta", "payload": {"id": "another-session", "cwd": self.cwd}}
        self.write(changed_meta, message("another identity"))
        self.assertIn("conflicting_session_scope", reconcile(self.outbox, self.policy, 2, 1001)["failures"])

    def test_declared_copied_fork_ancestry_preserves_canonical_identity(self):
        child, parent, grandparent = ("00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002", "00000000-0000-0000-0000-000000000003")
        self.meta["payload"].update({"id": child, "forked_from_id": parent})
        raw = self.write({"type": "session_meta", "payload": {"id": parent, "cwd": self.cwd + "/app", "forked_from_id": grandparent}},
                         {"type": "session_meta", "payload": {"id": grandparent, "cwd": self.cwd}}, message("inherited public text"),
                         {"type": "session_meta", "payload": dict(self.meta["payload"])}, message("current public text"))
        self.assertEqual(reconcile(self.outbox, self.policy, 2, 1000)["captured"], 1)
        event = self.outbox.pending(2, 1000)[0][0]
        self.assertEqual(event["session_id"], child)
        self.assertEqual(event["source_sha256"], sha256(raw))
        self.assertEqual([message["line_start"] for message in event["projection"]["messages"]], [4, 6])

    def test_fork_lineage_undeclared_cycle_changed_parent_and_external_cwd_refuse(self):
        child, parent, grandparent = ("00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002", "00000000-0000-0000-0000-000000000003")
        self.meta["payload"].update({"id": child, "forked_from_id": parent})
        cases = [
            [{"id": grandparent, "cwd": self.cwd}],
            [{"id": parent, "cwd": self.cwd, "forked_from_id": child}],
            [{"id": child, "cwd": self.cwd, "forked_from_id": grandparent}],
            [{"id": child, "cwd": self.cwd}],
            [{"id": parent, "cwd": "/outside"}],
            [{"id": parent}],
            [{"id": parent, "cwd": self.cwd, "forked_from_id": "bad-reference"}],
            [{"id": parent, "cwd": self.cwd, "forked_from_id": []}],
            [{"id": parent, "cwd": self.cwd, "forked_from_id": grandparent}, {"id": parent, "cwd": self.cwd}],
        ]
        for records in cases:
            self.write(*({"type": "session_meta", "payload": record} for record in records), message("must not capture"))
            stats = reconcile(self.outbox, self.policy, 2, 1000)
            self.assertEqual(stats["captured"], 0)
            self.assertTrue(stats["failures"])
            self.assertEqual(self.outbox.db.execute("SELECT count(*) FROM checkpoints").fetchone()[0], 0)
        self.policy["exclusions"] = [self.cwd + "/private"]
        self.write({"type": "session_meta", "payload": {"id": parent, "cwd": self.cwd + "/private"}}, message("must not capture"))
        self.assertIn("conflicting_session_scope", reconcile(self.outbox, self.policy, 2, 1000)["failures"])

    def test_fork_lineage_depth_is_bounded(self):
        ids = ["00000000-0000-0000-0000-" + format(index, "012x") for index in range(1, 66)]
        self.meta["payload"].update({"id": ids[0], "forked_from_id": ids[1]})
        records = [{"type": "session_meta", "payload": {"id": ids[index], "cwd": self.cwd, "forked_from_id": ids[index + 1]}} for index in range(1, 64)]
        self.write(*records, message("must not capture"))
        self.assertIn("fork_lineage_limit", reconcile(self.outbox, self.policy, 2, 1000)["failures"])
        self.assertEqual(self.outbox.counts()["outbox_count"], 0)

    def test_proven_oversized_tool_and_analysis_are_partial_with_original_lines(self):
        raw = self.write(message("before"),
            {"type": "response_item", "payload": {"output": "x" * (2 * 1024 * 1024), "type": "function_call_output"}},
            message("a" * (2 * 1024 * 1024), "assistant", "analysis"), message("after"))
        self.assertEqual(reconcile(self.outbox, self.policy, 2, 1000)["captured"], 1)
        event = self.outbox.pending(2, 1000)[0][0]
        self.assertEqual(event["source_sha256"], sha256(raw))
        self.assertEqual(event["coverage"]["excluded_oversized_records"], 2)
        self.assertEqual([value["line_start"] for value in event["projection"]["messages"]], [2, 5])

    def test_oversized_custom_tool_passthrough_never_enters_projection(self):
        raw = self.write(message("before"), {"type": "response_item", "payload": {
            "type": "custom_tool_call_output", "output": "private tool output " * 100000,
            "internal_chat_message_metadata_passthrough": {"turn_id": "private-turn"}}}, message("after"))
        self.assertEqual(reconcile(self.outbox, self.policy, 2, 1000)["captured"], 1)
        event = self.outbox.pending(2, 1000)[0][0]
        self.assertEqual(event["source_sha256"], sha256(raw))
        self.assertEqual(event["coverage"]["excluded_oversized_records"], 1)
        self.assertEqual([value["line_start"] for value in event["projection"]["messages"]], [2, 4])
        self.assertNotIn("private tool output", canonical(event).decode())
        self.assertNotIn("private-turn", canonical(event).decode())

    def test_capture_restart_replay_sequence_and_archive_occurrence(self):
        self.write(message("visible"))
        self.assertEqual(reconcile(self.outbox, self.policy, 2, 1000)["captured"], 1)
        event, digest = self.outbox.pending(2, 1000)[0]
        self.outbox.close()
        self.outbox = Outbox(self.home / "private")
        self.addCleanup(self.outbox.close)
        self.assertEqual(self.outbox.pending(2, 1000)[0], (event, digest))
        self.assertEqual(reconcile(self.outbox, self.policy, 2, 1001)["unchanged"], 1)
        self.write(message("changed"))
        reconcile(self.outbox, self.policy, 2, 1002)
        self.assertEqual([row[0]["sequence"] for row in self.outbox.pending(2, 1002)], [1, 2])
        archived = self.home / ".codex/archived_sessions"
        archived.mkdir()
        (archived / self.path.name).write_bytes(self.path.read_bytes())
        self.policy["roots"].append(str(archived))
        reconcile(self.outbox, self.policy, 2, 1003)
        archive_event = [row[0] for row in self.outbox.pending(2, 1003) if row[0]["source_location"]["archive_occurrence"]][0]
        self.assertEqual(archive_event["sequence"], 1)

    def test_backpressure_and_invalid_receipt_retain_immutable_capture(self):
        self.write(message("visible"))
        self.outbox.max_bytes = 1
        with self.assertRaises(Backpressure):
            reconcile(self.outbox, self.policy, 2, 1000)
        self.assertEqual(self.outbox.db.execute("SELECT count(*) FROM checkpoints").fetchone()[0], 0)
        self.outbox.max_bytes = 256 * 1024 * 1024
        reconcile(self.outbox, self.policy, 2, 1001)
        event, digest = self.outbox.pending(2, 1001)[0]
        with self.assertRaises(AgentError):
            self.outbox.accepted(event, digest, {"event_id": event["event_id"]})
        self.assertEqual(self.outbox.pending(2, 1001)[0], (event, digest))
        with self.outbox.db:
            self.outbox.db.execute("UPDATE events SET payload=?", (b"{}",))
        with self.assertRaises(AgentError):
            self.outbox.pending(2, 1001)

    def test_scan_cursor_eventually_covers_more_than_first_batch(self):
        for index in range(7):
            (self.root / ("rollout-%02d.jsonl" % index)).write_bytes(encoded(self.meta, message("visible %d" % index)))
        for _ in range(4):
            reconcile(self.outbox, self.policy, 2, 1000, max_files=2)
        self.assertEqual(self.outbox.counts()["outbox_count"], 7)

    def test_fingerprint_skips_unchanged_body_but_recaptures_changed_or_revised_source(self):
        self.write(message("visible"))
        reconcile(self.outbox, self.policy, 2, 1000)
        with mock.patch("hoomy_agent.collector._project_records", side_effect=AssertionError("unchanged body must not be read")):
            self.assertEqual(reconcile(self.outbox, self.policy, 2, 1001)["unchanged"], 1)
        self.write(message("changed"))
        self.assertEqual(reconcile(self.outbox, self.policy, 2, 1002)["captured"], 1)
        self.assertEqual(reconcile(self.outbox, self.policy, 3, 1003)["captured"], 1)
        self.assertEqual([event["sequence"] for event, _ in self.outbox.pending(3, 1003)], [3])

    def test_outbox_refuses_symlink_and_shared_database(self):
        directory = self.home / "other-private"
        directory.mkdir(mode=0o700)
        (directory / "outbox.sqlite3").symlink_to(self.home / "not-a-database")
        with self.assertRaises(AgentError):
            Outbox(directory)
        (directory / "outbox.sqlite3").unlink()
        (directory / "outbox.sqlite3").write_bytes(b"")
        (directory / "outbox.sqlite3").chmod(0o644)
        with self.assertRaises(AgentError):
            Outbox(directory)


class TransportTests(CollectionFixture):
    def setUp(self):
        super().setUp()
        self.state = State(self.home / "state", "https://server.example", credential_backend=False)
        self.state.save_credentials({"state": "authorized", "access_token": "fixture-access", "refresh_token": "fixture-refresh", "expires_at": 10000, "scope": "device:self evidence:ingest"})
        self.state.save_config({"collection_enabled": True, "approved_policy": self.policy, "policy_revision": 2,
            "discovery": {"resource": "https://server.example/v1/collector", "endpoints": {"events": "/v1/events", "receipts": "/v1/receipts"}}})
        self.now = 1000
        self.receipts = {}
        self.posts = []
        self.available = True
        self.lose_ack = False
        self.agent = Agent(self.state, self, clock=lambda: self.now)

    def call(self, method, url, payload=None, token=None, form=False, idempotency_key=None):
        if url == "/v1/devices/self":
            return {"policy": self.policy, "policy_revision": 2, "collection_available": self.available}
        if url.startswith("/v1/receipts/"):
            if url.rsplit("/", 1)[1] not in self.receipts:
                raise APIError(404, "receipt_not_found")
            return self.receipts[url.rsplit("/", 1)[1]]
        if url == "/v1/events":
            self.assertEqual(idempotency_key, payload["event_id"])
            self.posts.append(canonical(payload))
            receipt = {"receipt_id": payload["event_id"], "event_id": payload["event_id"], "project_id": payload["project_id"],
                       "projected_sha256": payload["projected_sha256"], "event_sha256": sha256(canonical(payload)),
                       "state": "stored", "inference_state": "not_requested", "source_version_id": "fixture-version", "received_at": "2026-10-12T12:00:00Z"}
            self.receipts[payload["event_id"]] = receipt
            if self.lose_ack:
                self.lose_ack = False
                raise AgentError("Fixture lost acknowledgement")
            return receipt
        return {"collection_available": self.available}

    def test_lost_ack_restart_reuses_exact_event_and_receipt(self):
        self.write(message("visible"))
        self.lose_ack = True
        with self.assertRaises(AgentError):
            self.agent.cycle()
        self.now += 6
        self.agent = Agent(self.state, self, clock=lambda: self.now)
        self.agent.cycle()
        self.assertEqual(len(self.posts), 1)
        outbox = Outbox(self.state.path / "outbox")
        self.addCleanup(outbox.close)
        self.assertEqual(outbox.counts()["outbox_count"], 0)

    def test_presence_grant_and_disabled_policy_never_read_source(self):
        self.write(message("visible"))
        with mock.patch("hoomy_agent.collector.read_source", side_effect=AssertionError("body must not be read")):
            self.available = False
            self.agent.cycle()
            self.available = True
            credentials = self.state.credentials()
            credentials.pop("scope")
            self.state.save_credentials(credentials)
            self.agent.cycle()
        self.assertFalse((self.state.path / "outbox").exists())

    def test_changed_policy_revision_stops_before_source_reads(self):
        configuration = self.state.config()
        configuration["policy_revision"] = 1
        self.state.save_config(configuration)
        with mock.patch("hoomy_agent.collector.read_source", side_effect=AssertionError("body must not be read")):
            with self.assertRaises(AgentError):
                self.agent.cycle()


if __name__ == "__main__":
    unittest.main()
