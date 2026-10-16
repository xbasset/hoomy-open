import unittest
from unittest.mock import patch

from hoomy.classifier import EFFORT, MODEL, SCHEMA, classify, validate, compact_input, expand_result, preview_projects
import json
from hoomy.inference import InferenceError


class ClassifierTests(unittest.TestCase):
    def test_compact_references_round_trip_and_unknowns_are_not_guessed(self):
        payload = {"conversations": [{"id": "original-a"}, {"id": "original-b"}, {"id": "original-c"}], "known_projects": []}
        wire, ids = compact_input(payload)
        project = dict(self.project(), session_ids=["c0"])
        result = expand_result({"projects": [project], "no_project": ["c1"], "ambiguous": ["unknown"]}, wire, ids)
        self.assertEqual(result["projects"][0]["session_ids"], ["original-a"])
        self.assertEqual([(x["session_id"], x["outcome"]) for x in result["unassigned"]],
                         [("original-b", "no_project"), ("original-c", "ambiguous")])
        self.assertEqual(payload["conversations"][0]["id"], "original-a")

    def test_stream_preview_requires_complete_valid_project(self):
        project = self.project()
        text = '{"projects":[' + json.dumps(project)
        self.assertEqual(preview_projects(text[:-1], self.payload()), [])
        self.assertEqual(preview_projects(text, self.payload()), ["x"])
        invalid = dict(project, session_ids=["unknown"])
        self.assertEqual(preview_projects('{"projects":[' + json.dumps(invalid), self.payload()), [])
        self.assertEqual(preview_projects(text + ',' + json.dumps(project), self.payload()), ["x"])

    def payload(self):
        return {"conversations": [{"id": "s", "title": "x", "folder": "a"}], "known_projects": []}

    def project(self):
        return {"name": "x", "purpose": "x", "rationale": "x", "session_ids": ["s"], "existing_project_id": None}

    def test_unknown_evidence_discards_only_affected_proposal(self):
        project = self.project()
        project["session_ids"] = ["absent"]
        result = validate({"projects": [project, self.project()]}, self.payload())
        self.assertEqual(result["projects"], [self.project()])
        self.assertEqual(result["discarded_proposals"], 1)

    def test_mixed_sources_do_not_keep_unreliable_rationale(self):
        project = self.project()
        project["session_ids"] = ["s", "absent"]
        result = validate({"projects": [project]}, self.payload())
        self.assertEqual(result["projects"], [])
        self.assertEqual(result["discarded_proposals"], 1)
        self.assertEqual(result["unassigned"][0]["outcome"], "ambiguous")

    def test_unknown_project_ids_discard_only_the_proposal(self):
        project = self.project()
        project["existing_project_id"] = "absent"
        result = validate({"projects": [project]}, self.payload())
        self.assertEqual(result["projects"], [])
        self.assertEqual(result["discarded_proposals"], 1)

    def test_more_than_three_allowed_but_no_status_or_next_steps(self):
        projects = [dict(self.project(), session_ids=[str(i)]) for i in range(4)]
        payload = {"conversations": [{"id": str(i)} for i in range(4)], "known_projects": []}
        self.assertEqual(len(validate({"projects": projects}, payload)["projects"]), 4)
        project = self.project()
        project["status"] = "active"
        with self.assertRaises(InferenceError):
            validate({"projects": [project]}, self.payload())

    def test_luna_low_is_explicit_and_no_model_fallback(self):
        calls = []
        class FakeClient:
            def __init__(self, *args, **kwargs):
                self.closed = False
            def initialize(self):
                pass
            def request(self, method, params):
                return {"data": [{"model": MODEL, "supportedReasoningEfforts": [{"reasoningEffort": EFFORT}]}]}
            def extract(self, payload, model, **kwargs):
                calls.append((model, kwargs))
                return {"projects": []}, MODEL
            def close(self):
                self.closed = True
        with patch("hoomy.classifier._Client", FakeClient), patch("hoomy.classifier._binary", return_value="codex"), patch("hoomy.classifier._check_capabilities"):
            result = classify(self.payload())
        self.assertEqual(result["model"], MODEL)
        self.assertEqual(calls, [(MODEL, {"schema": SCHEMA, "effort": EFFORT})])

    def test_unavailable_model_does_not_run_another(self):
        with patch("hoomy.classifier._Client") as client, patch("hoomy.classifier._binary", return_value="codex"), patch("hoomy.classifier._check_capabilities"):
            client.return_value.request.return_value = {"data": [], "nextCursor": None}
            with self.assertRaises(InferenceError):
                classify(self.payload())
            client.return_value.extract.assert_not_called()
            client.return_value.close.assert_called_once()

    def test_empty_census_does_not_consume_inference(self):
        with patch("hoomy.classifier._Client") as client:
            self.assertEqual(classify({"conversations": [], "known_projects": []})["projects"], [])
            client.assert_not_called()
