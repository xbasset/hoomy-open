"""Metadata shape assertions, not a generated conversation dataset."""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hoomy.catalog import HEADER_BYTES, classification_input, clean_title, inventory, title_index


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "sessions").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def source(self, sid="s", cwd="/a", tail=b"not JSON; this body must not be parsed"):
        path = self.root / "sessions" / (sid + ".jsonl")
        path.write_bytes(json.dumps({"type": "session_meta", "payload": {"id": sid, "cwd": cwd}}).encode() + b"\n" + tail)
        return path

    def test_title_index_not_body_and_latest_title_wins(self):
        self.source()
        (self.root / "session_index.jsonl").write_text('\n'.join(json.dumps(r) for r in [
            {"id": "s", "thread_name": "old"}, {"id": "s", "thread_name": "current"}]))
        census = inventory(self.root, [])
        self.assertEqual(census["records"][0]["title"], "current")
        self.assertEqual(census["failures"], [])
        self.assertNotIn("not JSON", json.dumps(census))

    def test_huge_body_is_not_read_or_rejected(self):
        path = self.source(tail=b"")
        with path.open("ab") as source:
            source.truncate(32 * 1024 * 1024)
        census = inventory(self.root, [])
        self.assertEqual(len(census["records"]), 1)
        self.assertEqual(census["records"][0]["size"], 32 * 1024 * 1024)

    def test_exclusions_and_unknown_cwd_admit_no_metadata(self):
        self.source("excluded", "/a")
        self.source("unknown", None)
        census = inventory(self.root, ["/a"])
        self.assertEqual(census["records"], [])
        self.assertEqual(census["excluded"], 1)
        self.assertEqual(len(census["failures"]), 1)

    def test_bad_or_oversized_header_is_visible(self):
        self.source().write_bytes(b"x" * (HEADER_BYTES + 5))
        census = inventory(self.root, [])
        self.assertEqual(census["records"], [])
        self.assertEqual(len(census["failures"]), 1)

    def test_cancel_does_not_return_a_complete_census(self):
        self.source()
        self.assertIsNone(inventory(self.root, [], lambda: True))

    def test_same_session_archived_copy_is_counted_once(self):
        path = self.source()
        archived = self.root / "archived_sessions"
        archived.mkdir()
        (archived / "copy.jsonl").write_bytes(path.read_bytes())
        census = inventory(self.root, [])
        self.assertEqual(census["files_seen"], 2)
        self.assertEqual(len(census["records"]), 1)

    def test_registry_only_title_columns_and_index_precedence(self):
        database = self.root / "state_5.sqlite"
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE threads(id TEXT, title TEXT, preview TEXT)")
            db.execute("INSERT INTO threads VALUES ('s','title','PRIVATE_BODY')")
        self.assertEqual(title_index(self.root), {"s": "title"})
        (self.root / "session_index.jsonl").write_text(json.dumps({"id": "s", "thread_name": "renamed"}))
        self.assertEqual(title_index(self.root), {"s": "renamed"})

    def test_scaffolding_is_not_a_title(self):
        self.assertEqual(clean_title("<recommended_plugins>"), "")
        self.assertEqual(clean_title("# AGENTS.md instructions"), "")
        self.assertEqual(clean_title("A\nB"), "A B")

    def test_classification_has_no_raw_text_or_absolute_source_path(self):
        self.source()
        records = inventory(self.root, [])["records"]
        payload, chars = classification_input(records, [])
        self.assertGreater(chars, 0)
        self.assertEqual(payload["folders"], [{"id": "f0", "name": "a"}])
        self.assertEqual(payload["conversations"][0]["folder_id"], "f0")
        self.assertNotIn("source_path", json.dumps(payload))
        self.assertNotIn(str(self.root), json.dumps(payload))
        with patch("hoomy.catalog.INPUT_CHARS", 1):
            from hoomy.inference import InferenceError
            with self.assertRaises(InferenceError):
                classification_input(records, [])

    def test_all_642_titles_are_included_past_old_budget(self):
        records = [{"session_id": str(i), "title": "x" * 240, "cwd": "/a", "modified_at": str(i)} for i in range(642)]
        payload, chars = classification_input(records, [])
        self.assertEqual(len(payload["conversations"]), 642)
        self.assertGreater(chars, 120_000)
        self.assertNotIn("maximum_new_projects", payload)
