import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hoomy.exploration import EvidenceTools, LiveProgress, MAX_CALLS, explore
from hoomy.inference import InferenceCancelled


class ExplorationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        folder = self.root / "sessions"
        folder.mkdir()
        self.path = folder / "source.jsonl"
        rows = [{"type": "session_meta", "payload": {"id": "fixture", "cwd": "/fictional/orchid"}}]
        rows += [{"type": "event_msg", "payload": {"type": "user_message", "message": f"Orchid irrigation project step {i}. " + "garden " * 160}} for i in range(8)]
        self.path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        self.records = [{"session_id": "fixture", "title": "Configure irrigation", "cwd": "/fictional/orchid",
                         "source_path": str(self.path), "modified_at": "2026-01-01T00:00:00Z", "size": self.path.stat().st_size}]
        self.tools = EvidenceTools(self.records, self.root, [], lambda: False, lambda *args: None)

    def tearDown(self):
        self.temp.cleanup()

    def test_bounded_cumulative_excerpts_have_exact_provenance(self):
        before = self.path.read_bytes()
        result = self.tools.inspect("fixture", "irrigation")
        self.assertEqual(len(result["excerpts"]), 4)
        self.tools.inspect("fixture", "garden")
        sample = self.tools.evidence["fixture"]
        self.assertLessEqual(sum(len(p["text"]) for p in sample["excerpts"]), 3000)
        originals = {m["id"]: m["text"] for m in self.tools.snapshots["fixture"]["messages"]}
        for piece in sample["excerpts"]:
            self.assertEqual(piece["text"], originals[piece["message_id"]][piece["start"]:piece["start"] + len(piece["text"])])
        self.assertEqual(before, self.path.read_bytes())

    def test_unknown_ids_and_excluded_paths_never_read(self):
        with patch("hoomy.exploration.snapshot") as read:
            self.assertIn("error", self.tools.inspect("unknown", ""))
            self.assertIn("error", self.tools.inspect([], ""))
            self.tools.records["fixture"]["source_path"] = "/not-authorized/history.jsonl"
            self.assertIn("error", self.tools.inspect("fixture", ""))
            read.assert_not_called()

    def test_scope_and_identity_are_rechecked(self):
        self.tools.exclusions = ["/fictional"]
        self.assertIn("error", self.tools.inspect("fixture", ""))
        self.assertEqual(self.tools.evidence, {})

    def test_empty_matches_do_not_become_evidence(self):
        self.tools.inspect("fixture", "a literal term that does not exist")
        self.assertEqual(self.tools.evidence, {})

    def test_budget_and_cancellation(self):
        self.tools.calls = MAX_CALLS
        with patch("hoomy.exploration.snapshot") as read:
            self.assertIn("error", self.tools.inspect("fixture", ""))
            read.assert_not_called()
        self.tools.cancelled = lambda: True
        with self.assertRaises(InferenceCancelled):
            self.tools.inspect("fixture", "")

    def test_source_budget_blocks_new_files_but_allows_existing_sources(self):
        self.tools.attempted = {str(i) for i in range(24)}
        with patch("hoomy.exploration.snapshot") as read:
            self.assertIn("error", self.tools.inspect("fixture", ""))
            read.assert_not_called()
        self.tools.attempted.remove("0")
        self.tools.attempted.add("fixture")
        self.assertIn("excerpts", self.tools.inspect("fixture", ""))

    def test_symlink_source_is_not_followed(self):
        link = self.path.parent / "alias.jsonl"
        link.symlink_to(self.path)
        self.tools.records["fixture"]["source_path"] = str(link)
        with patch("hoomy.exploration.snapshot") as read:
            self.assertIn("error", self.tools.inspect("fixture", ""))
            read.assert_not_called()

    def test_public_stream_does_not_include_raw_reasoning_or_tool_output(self):
        frames = []
        live = LiveProgress(frames.append)
        live.observe("item/reasoning/textDelta", {"delta": "PRIVATE"})
        live.observe("item/commandExecution/outputDelta", {"delta": "SECRET"})
        live.observe("item/agentMessage/delta", {"phase": "final_answer", "delta": "RAW JSON"})
        live.observe("item/reasoning/summaryTextDelta", {"itemId": "s", "delta": "Public summary"})
        live.observe("item/agentMessage/delta", {"itemId": "m", "phase": "commentary", "delta": "Searching "})
        live.observe("item/agentMessage/delta", {"itemId": "m", "phase": "commentary", "delta": "titles"})
        live.flush(force=True)
        text = json.dumps(frames[-1])
        for secret in ("PRIVATE", "SECRET", "RAW JSON"):
            self.assertNotIn(secret, text)
        self.assertIn("Searching titles", text)
        self.assertIn("Public summary", text)

    @unittest.skipUnless(os.environ.get("HOOMY_AGENT_SMOKE") == "1", "Explicit synthetic live SDK test only")
    def test_live_sdk_on_fictional_data(self):
        frames = []
        known = [{"id": "orchid", "name": "Orchid", "purpose": "Build a garden irrigation system", "examples": [], "activity": "active"}]
        findings = []
        def publish(proposal, evidence, snapshots):
            self.assertTrue(set(proposal["session_ids"]).issubset(evidence))
            self.assertTrue(snapshots)
            findings.append(proposal)
            return {"published": 1, "kind": "association"}
        result = explore(self.records, known, self.root, [], lambda: False, frames.append,
                         publish=publish, feedback=lambda: {"revision": len(findings), "known_projects": known, "human_decisions": []})
        self.assertEqual(result["model"], "gpt-5.6-luna")
        self.assertIn("fixture", result["excerpt_evidence"])
        self.assertTrue(any(e["kind"] == "tool" for frame in frames for e in frame["events"]))
        self.assertTrue(result["projects"])
        self.assertTrue(findings)
