import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hoomy.collector import SCOPE_UNKNOWN_FAILURE
from hoomy.edge import Client, Edge
from hoomy.store import Store


class LocalClient:
    def __init__(self, store):
        self.store = store

    def get(self, path):
        if path == "/api/state":
            return self.store.state()
        return self.store.session(path.rsplit("/", 1)[-1])

    def post(self, path, payload):
        return getattr(self.store, path.rsplit("/", 1)[-1])(payload)


class EdgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "db")
        # Retain the original full-reading invariants alongside overview tests.
        self.store.control({"mode": "full"})
        self.edge = Edge(LocalClient(self.store), self.root / "codex")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def source(self, name="unit", cwd="/a"):
        directory = self.root / "codex/sessions"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (name + ".jsonl")
        metadata = {"id": name}
        if cwd is not None:
            metadata["cwd"] = cwd
        path.write_text(json.dumps({"type": "session_meta", "payload": metadata}) + "\n" +
                        json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "a"}}) + "\n")
        os.utime(path, (time.time() - 20, time.time() - 20))
        return path

    def test_no_read_before_scope_confirmation(self):
        with patch("hoomy.edge.session_paths") as paths:
            self.edge.tick()
            paths.assert_not_called()

    def test_incremental_ingest_and_reset_replay(self):
        self.source()
        self.store.control({"running": True, "consent": True})
        self.edge.tick()
        self.assertEqual(self.store.state()["coverage"]["pending"], 1)
        self.edge.scan_at = 0
        self.edge.tick()
        self.assertEqual(self.store.state()["coverage"]["discovered"], 1)
        generation = self.edge.generation
        self.store.reset({"confirm": "DELETE_HOOMY_DATA"})
        self.edge.tick()
        self.assertNotEqual(self.edge.generation, generation)
        self.assertEqual(self.store.state()["coverage"]["discovered"], 0)
        self.store.control({"running": True, "consent": True, "mode": "full"})
        self.edge.tick()
        self.assertEqual(self.store.state()["coverage"]["discovered"], 1)

    def test_exclusion_prevents_raw_ingestion(self):
        self.source()
        self.store.control({"running": True, "consent": True, "exclusions": ["/a"]})
        self.edge.tick()
        self.assertEqual(self.store.state()["coverage"]["excluded"], 1)
        self.assertEqual(self.store.state()["sessions"], [])

    def test_ambiguous_cwd_is_quarantined_and_remains_visible(self):
        self.source("missing", None)
        self.source("relative", "relative/private-value")
        self.store.control({"running": True, "consent": True, "exclusions": ["/excluded"]})
        self.edge.tick()
        state = self.store.state()
        self.assertEqual(state["coverage"]["failed"], 2)
        self.assertEqual(state["coverage"]["excluded"], 0)
        self.assertEqual(state["sessions"], [])
        self.assertEqual({failure["reason"] for failure in state["discovery_failures"]},
                         {SCOPE_UNKNOWN_FAILURE})
        self.assertNotIn("relative/private-value", json.dumps(state["discovery_failures"]))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 0)

        self.edge.scan_at = 0
        self.edge.tick()
        self.assertEqual(len(self.store.state()["discovery_failures"]), 2)

    def test_reject_remote_backend(self):
        with self.assertRaises(ValueError):
            Client("http://example.com:8765")


if __name__ == "__main__":
    unittest.main()
