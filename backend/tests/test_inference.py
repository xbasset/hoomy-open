"""Protocol and shape assertions; not a generated conversation dataset."""

import copy
import json
import os
import queue
import time
import unittest
from unittest.mock import patch

from hoomy import inference as subject


def empty_result():
    return {"brief": {section: [] for section in subject.SECTIONS}, "projects": []}


class InferenceTests(unittest.TestCase):
    def test_telemetry_measures_stream_and_usage_without_recording_text(self):
        reports = []
        with patch.object(subject.time, "monotonic", return_value=100):
            meter = subject.TurnTelemetry("t", reports.append)
        with patch.object(subject.time, "monotonic", return_value=103):
            meter.observe({"method": "item/agentMessage/delta", "params": {"threadId": "foreign", "delta": "private"}})
            self.assertEqual(meter.chars, 0)
            meter.observe({"method": "item/agentMessage/delta", "params": {"threadId": "t", "delta": "private"}})
            meter.observe({"method": "thread/tokenUsage/updated", "params": {"threadId": "t", "tokenUsage": {"last": {
                "inputTokens": 123, "outputTokens": 12, "cachedInputTokens": 0, "untrusted": "secret"}}}})
            result = meter.finish()
        self.assertEqual(result["first_response_seconds"], 3)
        self.assertEqual(result["token_usage"]["inputTokens"], 123)
        self.assertNotIn("private", json.dumps(result))
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(reports[-1]["phase"], "receiving")

    def test_valid_then_missing_usage_does_not_fabricate_zero_or_reuse_old_metrics(self):
        seen = []
        meter = subject.TurnTelemetry("thread", usage_observer=seen.append)
        meter.observe({"method": "thread/tokenUsage/updated", "params": {"threadId": "thread",
            "tokenUsage": {"last": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15}}}})
        meter.observe({"method": "thread/tokenUsage/updated", "params": {"threadId": "thread", "tokenUsage": {"last": {}}}})
        self.assertEqual(seen, [{"inputTokens": 10, "outputTokens": 5, "totalTokens": 15}, None])
        self.assertIsNone(meter.finish()["token_usage"])

    def test_waiting_is_elapsed_time_not_fabricated_progress(self):
        reports = []
        with patch.object(subject.time, "monotonic", return_value=100):
            meter = subject.TurnTelemetry("t", reports.append)
        with patch.object(subject.time, "monotonic", return_value=105):
            meter.observe(None)
            result = meter.finish()
        self.assertEqual(reports[-1]["elapsed_seconds"], 5)
        self.assertEqual(reports[-1]["response_chars"], 0)
        self.assertIsNone(result["token_usage"])
        self.assertIsNone(result["first_response_seconds"])

    def test_content_free_live_check_uses_production_boundary(self):
        calls = []

        class FakeClient:
            def __init__(self, *args): pass
            def initialize(self): calls.append("initialize")
            def close(self): calls.append("close")
            def extract(self, payload, model):
                calls.append((payload, model))
                return empty_result(), "default-model"

        with patch.object(subject, "_binary", return_value="codex"), \
             patch.object(subject, "_check_capabilities", return_value="codex test"), \
             patch.object(subject, "_Client", FakeClient):
            result = subject.check_inference()
        self.assertEqual(calls, ["initialize", ({"task": "Protocol check only. Return an empty brief and no projects.",
                         "messages": [], "existing_projects": []}, None), "close"])
        self.assertEqual(result, {"authentication": "chatgpt", "codex_version": "codex test",
                         "model": "default-model", "restricted_boundary": "passed",
                         "structured_turn": "passed"})

    def test_content_free_live_check_rejects_generated_content(self):
        class FakeClient:
            def __init__(self, *args): pass
            def initialize(self): pass
            def close(self): pass
            def extract(self, payload, model):
                result = empty_result()
                result["projects"] = [{"existing_project_id": None, "name": "invented",
                    "purpose": "invented", "status": "unclear", "rationale": "invented",
                    "evidence": [{"message_id": "missing", "quote": "invented"}]}]
                return result, "default-model"

        with patch.object(subject, "_binary", return_value="codex"), \
             patch.object(subject, "_check_capabilities", return_value="codex test"), \
             patch.object(subject, "_Client", FakeClient):
            with self.assertRaises(subject.InferenceError):
                subject.check_inference()

    def test_chunks_preserve_every_character_and_original_id(self):
        messages = [{"id": "a", "text": "0123456789", "role": "user"},
                    {"id": "b", "text": "abc", "role": "assistant"}]
        chunks = subject._chunks(messages, size=4)
        for message in messages:
            pieces = [part for chunk in chunks for part in chunk if part["id"] == message["id"]]
            self.assertEqual("".join(part["text"] for part in pieces), message["text"])
        self.assertTrue(all(sum(len(part["text"]) for part in chunk) <= 4 for chunk in chunks))

    def test_rejects_fabricated_quote_or_wrong_source(self):
        source = [{"id": "a", "text": "abc"}, {"id": "b", "text": "def"}]
        for evidence in ([{"message_id": "a", "quote": "def"}],
                         [{"message_id": "x", "quote": "abc"}], []):
            result = empty_result()
            result["brief"]["intentions"] = [{"text": "assertion", "kind": "inferred", "evidence": evidence}]
            with self.assertRaises(subject.InferenceError):
                subject.validate_result(result, source, [])

    def test_valid_citation_is_not_promoted_to_verified_fact(self):
        result = empty_result()
        result["brief"]["outcomes"] = [{"text": "assertion", "kind": "reported",
            "evidence": [{"message_id": "a", "quote": "abc"}]}]
        self.assertIs(subject.validate_result(result, [{"id": "a", "text": "abc"}], []), result)

    def test_unknown_project_id_rejected(self):
        result = empty_result()
        result["projects"] = [{"existing_project_id": "unknown", "name": "name", "purpose": "purpose",
            "status": "unclear", "rationale": "rationale", "evidence": [{"message_id": "a", "quote": "abc"}]}]
        with self.assertRaises(subject.InferenceError):
            subject.validate_result(result, [{"id": "a", "text": "abc"}], [])

    def test_environment_does_not_forward_api_keys_or_agent_context(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "secret", "CODEX_API_KEY": "secret",
                "CODEX_THREAD_ID": "private", "CODEX_HOME": "/auth/location", "HOME": "/user"}, clear=True):
            result = subject._environment()
        self.assertEqual(result, {"CODEX_HOME": "/auth/location", "HOME": "/user", "RUST_LOG": "off"})

    def test_safe_config_disables_integrations_without_disabling_auth(self):
        config = subject._safe_config("/runtime")
        self.assertEqual(config["forced_login_method"], "chatgpt")
        self.assertEqual(config["model_provider"], "openai")
        for feature in subject._DISABLED_FEATURES:
            self.assertIs(config[f"features.{feature}"], False)
        for key in ("mcp_servers", "hooks", "plugins"):
            self.assertEqual(config[key], {})
        self.assertEqual(config["history.persistence"], "none")

    def test_server_tool_request_is_rejected(self):
        client = object.__new__(subject._Client)
        client.cancelled = lambda: False
        client.events = queue.Queue()
        client.events.put({"id": 99, "method": "item/tool/call", "params": {}})
        sent = []
        client.send = sent.append
        with self.assertRaisesRegex(subject.InferenceError, "outside the analysis boundary"):
            client.event(time.monotonic() + 1)
        self.assertEqual(sent[0]["error"]["code"], -32601)

    def test_tool_notification_fails_closed(self):
        client = object.__new__(subject._Client)
        client.cancelled = lambda: False
        client.events = queue.Queue()
        client.events.put({"method": "item/started", "params": {"item": {"type": "commandExecution"}}})
        with self.assertRaises(subject.InferenceError):
            client.event(time.monotonic() + 1)

    def test_cancellation_interrupts_wait(self):
        client = object.__new__(subject._Client)
        client.cancelled = lambda: True
        client.events = queue.Queue()
        with self.assertRaises(subject.InferenceCancelled):
            client.event(time.monotonic() + 20)

    def test_provider_error_never_echoes_content(self):
        for error in ({"message": "PRIVATE auth failure"}, {"message": "PRIVATE quota exceeded"},
                      {"message": "PRIVATE syntax"}):
            result = subject._classify_error(error)
            self.assertNotIn("PRIVATE", str(result))
            self.assertNotIsInstance(result, subject._TransientError)

    def test_api_key_sign_in_rejected_before_any_turn(self):
        client = object.__new__(subject._Client)
        client.send = lambda message: None
        calls = []

        def request(method, params):
            calls.append(method)
            return {"account": {"type": "apiKey"}} if method == "account/read" else {}

        client.request = request
        with self.assertRaisesRegex(subject.InferenceError, "ChatGPT"):
            client.initialize()
        self.assertNotIn("thread/start", calls)

    def test_extraction_has_no_environment_or_dynamic_tools(self):
        client = object.__new__(subject._Client)
        client.instruction_source_count = 0
        requests = []

        def request(method, params):
            requests.append((method, params))
            if method == "mcpServerStatus/list":
                return {"data": [], "nextCursor": None}
            return {"thread": {"id": "thread", "ephemeral": True}, "model": "selected"} if method == "thread/start" else {"turn": {"id": "turn"}}

        events = iter([
            {"method": "thread/tokenUsage/updated", "params": {"threadId": "thread", "turnId": "turn",
                "tokenUsage": {"last": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15}}}},
            {"method": "item/completed", "params": {"threadId": "thread", "turnId": "turn", "item": {"type": "agentMessage", "text": json.dumps(empty_result())}}},
            {"method": "turn/completed", "params": {"threadId": "thread", "turn": {"id": "turn", "status": "completed"}}},
        ])
        client.request = request
        def event(deadline, *, available_only=False):
            value=next(events, None)
            if value is not None:client.telemetry.observe(value)
            return value
        client.event = event
        result, model = client.extract({}, None)
        self.assertEqual(result, empty_result())
        self.assertEqual(model, "selected")
        for method, params in requests:
            if method == "mcpServerStatus/list":
                continue
            self.assertEqual(params["environments"], [])
        self.assertEqual(requests[0][1]["dynamicTools"], [])
        self.assertIs(requests[0][1]["ephemeral"], True)
        self.assertNotIn("model", requests[0][1])
        self.assertEqual(requests[-1][1]["outputSchema"], subject.OUTPUT_SCHEMA)

    def test_external_tool_inventory_stops_before_prompt(self):
        client = object.__new__(subject._Client)
        client.instruction_source_count = 0
        requests = []

        def request(method, params):
            requests.append(method)
            if method == "thread/start":
                return {"thread": {"id": "t", "ephemeral": True}}
            return {"data": [{"tools": {"unexpected": {}}}]}

        client.request = request
        with self.assertRaisesRegex(subject.InferenceError, "external tool"):
            client.extract({}, None)
        self.assertNotIn("turn/start", requests)

    def test_persisted_thread_stops_before_prompt(self):
        client = object.__new__(subject._Client)
        client.request = lambda method, params: {"thread": {"id": "t", "ephemeral": False}}
        with self.assertRaisesRegex(subject.InferenceError, "ephemeral"):
            client.extract({}, None)

    def test_quota_failure_does_not_retry_and_closes_process(self):
        counts = {"calls": 0, "closed": 0}

        class FakeClient:
            def __init__(self, *args): pass
            def initialize(self): pass
            def close(self): counts["closed"] += 1
            def extract(self, payload, model):
                counts["calls"] += 1
                raise subject._classify_error({"message": "quota exceeded"})

        with patch.object(subject, "_binary", return_value="codex"), \
             patch.object(subject, "_check_capabilities", return_value="tested"), \
             patch.object(subject, "_Client", FakeClient):
            with self.assertRaises(subject.InferenceError):
                subject.analyze([{"id": "a", "text": "x"}], [], "")
        self.assertEqual(counts, {"calls": 1, "closed": 1})

    def test_analyze_maps_all_chunks_and_reduces_with_all_projects(self):
        messages = [{"id": "a", "text": "x" * (subject.CHUNK_CHARS + 1)}]
        projects = [{"id": str(i), "name": str(i), "purpose": "p"} for i in range(3)]
        calls = []

        class FakeClient:
            def __init__(self, *args): pass
            def initialize(self): pass
            def close(self): pass
            def extract(self, payload, model):
                calls.append(copy.deepcopy(payload))
                return empty_result(), "selected"

        with patch.object(subject, "_binary", return_value="codex"), \
             patch.object(subject, "_check_capabilities", return_value="tested"), \
             patch.object(subject, "_Client", FakeClient):
            result = subject.analyze(messages, projects, "hint")
        self.assertEqual(result["method"]["chunks"], 2)
        self.assertEqual(result["method"]["processed_text_chars"], len(messages[0]["text"]))
        self.assertEqual(len(calls), 3)
        for call in calls:
            self.assertEqual([p["id"] for p in call["existing_projects"]], ["0", "1", "2"])

    def test_oversized_project_context_fails_before_sending_anything(self):
        with patch.object(subject, "_binary") as binary:
            with self.assertRaisesRegex(subject.InferenceError, "no projects were omitted"):
                subject.analyze([{"id": "a", "text": "x"}], [{"id": "p", "purpose": "x" * (subject.CONTEXT_CHARS + 1)}], "")
        binary.assert_not_called()


if __name__ == "__main__":
    unittest.main()
