import json
import tempfile
import unittest
from pathlib import Path

from hoomy.collector import SCOPE_UNKNOWN, SnapshotError, excluded, normalize, scope_status


def encoded(*values):
    return ("\n".join(json.dumps(v) for v in values) + "\n").encode()


class CollectorTests(unittest.TestCase):
    def test_stable_ids_and_duplicate_suppression(self):
        raw = encoded({"type": "session_meta", "payload": {"id": "s", "cwd": "/project"}},
                      {"type": "event_msg", "payload": {"type": "user_message", "message": "a"}},
                      {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "a"}]}})
        one = normalize(raw, "/a", "now")
        two = normalize(raw, "/archive/a", "later")
        self.assertEqual(one["messages"], two["messages"])
        self.assertEqual(len(one["messages"]), 1)
        self.assertEqual(one["messages"][0]["line_start"], 3)
        self.assertEqual(one["session_id"], "s")

    def test_duplicate_suppression_requires_nearby_exact_role_and_text(self):
        raw = encoded(
            {"type": "session_meta", "payload": {"id": "s", "cwd": "/project"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "near"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "near"}},
            {"type": "event_msg", "payload": {"type": "agent_message", "message": "near"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "unique"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "other"}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "far"}},
            *({"type": "ignored", "payload": {}} for _ in range(5)),
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "far"}},
        )
        messages = normalize(raw, "/a", "now")["messages"]
        self.assertEqual([message["line_start"] for message in messages], [3, 4, 5, 6, 7, 13])
        self.assertEqual([message["text"] for message in messages],
                         ["near", "near", "unique", "other", "far", "far"])

    def test_reasoning_and_platform_instructions_are_not_analysis_input(self):
        raw = encoded(*({"type": "response_item", "payload": {"type": "message", "role": role, "channel": channel, "content": "a"}}
                        for role, channel in (("system", None), ("developer", None), ("assistant", "analysis"), ("assistant", "final"))))
        self.assertEqual(len(normalize(raw, "/a", "now")["messages"]), 1)

    def test_invalid_and_partial_records_fail_visibly(self):
        for raw in (b"{", b"[]\n", b"\xff", b""):
            with self.assertRaises(SnapshotError):
                normalize(raw, "/a", "now")

    def test_media_limitation_reported(self):
        raw = encoded({"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_image", "image_url": "local"}]}})
        self.assertEqual(len(normalize(raw, "/a", "now")["warnings"]), 2)

    def test_exclusions_have_directory_boundaries(self):
        self.assertTrue(excluded("/work/project", ["/work"]))
        self.assertFalse(excluded("/workshop/project", ["/work"]))

    def test_scope_paths_are_resolved_on_host_and_unknown_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            protected = root / "protected"
            protected.mkdir()
            alias = root / "alias"
            alias.symlink_to(protected, target_is_directory=True)
            self.assertTrue(excluded(str(alias / "repo"), [str(protected)]))
            self.assertTrue(excluded(str(protected / "repo"), [str(alias)]))
            self.assertFalse(excluded(str(root / "protected-other"), [str(protected)]))
            self.assertEqual(scope_status("", [str(protected)]), SCOPE_UNKNOWN)
            self.assertEqual(scope_status("relative/repo", [str(protected)]), SCOPE_UNKNOWN)
            self.assertEqual(scope_status("", []), "included")


if __name__ == "__main__":
    unittest.main()
