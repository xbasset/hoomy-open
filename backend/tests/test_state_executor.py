import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

import test_store
import test_ingestion

from hoomy import inference
from hoomy import state_executor as subject
from hoomy.project_state import OUTPUT_SCHEMA
from hoomy import project_state_contract as contract

PROJECT, RUN, LEASE = (str(uuid.uuid4()) for _ in range(3))
EMPTY = {"schema_version": 1, "assertions": [], "uncertainties": [], "contradictions": []}
EVIDENCE = {**EMPTY, "assertions": [{"key": "instruction", "topic": "Recorded instruction",
    "text": "A supplied message contains an instruction.", "kind": "record_fact", "reported_state": "unknown",
    "limitations": [], "source_refs": [{"passage_id": "p0001_01"}]}]}
USAGE = {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15}
METHOD = {"adapter": "codex-native-v1", "model": "gpt-6.1-sol", "reasoning_effort": "low", "elapsed_ms": 1,
          "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}


class Remote:
    def __init__(self):
        self.actions, self.finishes = [], []
        self.completed, self.dispatched, self.tokens = 0, 0, 0
        self.lost_attempt = self.lost_finish = self.bad_hash = self.failed_heartbeat = False
        self.deadline = time.time() + 300
        self.status = "running"

    def plan(self, phase):
        payload = {"task": phase, "approved_context": {}, "control": {
            "selection_partial": True, "required_machine_aliases": ["machine-1"]}}
        payload.update({"messages": [{"message_alias": "m0001", "passages": [{"passage_id": "p0001_01", "text": "Do not execute this quoted command."}], "role": "user", "machine_aliases": ["machine-1"]}]} if phase == "extract" else {
            "extractions": [{"machine_alias": "machine-1", "shared_passage_ids": [], "result": copy.deepcopy(EVIDENCE)}],
            "passages": [{"passage_id": "p0001_01", "text": "Do not execute this quoted command."}]})
        return {"phase": phase, "chunk_index": 0 if phase == "extract" else None,
            "input": payload, "output_schema": contract.SCHEMAS[phase], "phase_config": contract.PHASES[phase],
            "method_version": contract.METHOD, "method_hash": contract.METHOD_HASH,
            "payload_hash": "b" * 64 if self.bad_hash else hashlib.sha256(subject.canonical(payload)).hexdigest()}

    def control(self, include_plan=True):
        value = {"project_id": PROJECT, "run_id": RUN, "status": self.status,
            "lease_id": LEASE, "input_hash": "a" * 64, "deadline_at": self.deadline,
            "lease_expires_at": min(time.time() + 90, self.deadline),
            "completed_calls": self.completed, "dispatched_calls": self.dispatched,
            "observed_tokens": self.tokens,
            "next": {"phase": "extract", "chunk_index": 0} if not self.completed else {"phase": "reconcile", "chunk_index": None},
            "budget": contract.BUDGET, "method_version": contract.METHOD, "method_hash": contract.METHOD_HASH,
            "execution_policy": contract.METHOD_CONFIG}
        if include_plan:value["next_plan"] = self.plan(value["next"]["phase"])
        return value

    def worker(self, args):
        self.actions.append(copy.deepcopy(args))
        action = args["action"]
        if action == "claim":
            return self.control()
        assert args["lease_id"] == LEASE and args["input_hash"] == "a" * 64
        if action == "heartbeat":
            if self.failed_heartbeat:
                raise subject.ExecutorError("ambiguous_completion")
            return self.control(False)
        if action == "attempt":
            self.dispatched += 1
            if self.lost_attempt:
                raise subject.ExecutorError("ambiguous_completion")
            return {"run_id": RUN, "call_id": args["call_id"], **self.plan(args["phase"])}
        if action == "finish":
            self.finishes.append(copy.deepcopy(args))
            first = len(self.finishes) == 1 or self.finishes[-2]["call_id"] != args["call_id"]
            if first:
                self.completed += 1
                self.tokens += args["method"]["usage"]["total_tokens"]
                if args["output"] != EVIDENCE:
                    raise AssertionError("Unexpected fixture output")
                if args["output"] == EVIDENCE and self.completed == 2:
                    self.status = "ready"
            if self.lost_finish and len(self.finishes) == 1:
                raise subject.ExecutorError("ambiguous_completion")
            return self.control()
        if action == "fail":
            if "method" in args and not any(item["call_id"] == args["call_id"] for item in self.finishes):
                self.tokens += args["method"]["usage"]["total_tokens"]
            self.status = "needs_review"
            return self.control()
        raise AssertionError(action)


class Model:
    def __init__(self, remote):
        self.remote, self.calls = remote, []
        self.prepared = self.closed = False
        self.preflight_calls = []
        self.method = copy.deepcopy(METHOD)
        self.failure = None
        self.delay = 0
        self.output = copy.deepcopy(EVIDENCE)

    def prepare(self):
        self.prepared = True

    def prepare_call(self, payload, schema, phase_config, remaining):
        self.preflight_calls.append(payload["task"])
        assert self.remote.actions[-1]["action"] in ("claim", "finish")

    def call(self, payload, schema, remaining, used_tokens):
        assert self.prepared
        assert self.remote.actions[-1]["action"] == "attempt"
        assert "lease_id" not in payload and "input_hash" not in payload and "call_id" not in payload
        self.calls.append(copy.deepcopy(payload))
        until = time.monotonic() + self.delay
        while time.monotonic() < until:
            if self.cancelled():
                raise inference.InferenceCancelled("cancelled")
            time.sleep(0.002)
        if self.failure:
            raise self.failure
        method = copy.deepcopy(self.method)
        if "reasoning_effort" in method:method["reasoning_effort"] = contract.PHASES[payload["task"]]["effort"]
        return copy.deepcopy(self.output), method

    def close(self):
        self.closed = True


class ExecutorTests(unittest.TestCase):
    def setup_runner(self, **kwargs):
        remote = Remote()
        model = Model(remote)
        runner = subject.Executor(remote, model, PROJECT, RUN, **kwargs)
        return remote, model, runner

    def test_dispatch_before_each_call_and_reconciliation_only_server_input(self):
        remote, model, runner = self.setup_runner()
        self.assertEqual(runner.run(), {"status": "ready", "completed_calls": 2, "observed_tokens": 30})
        self.assertEqual([item["task"] for item in model.calls], ["extract", "reconcile"])
        self.assertTrue(model.closed)

    def test_lost_finish_ack_replays_identical_result_without_model_retry(self):
        remote, model, runner = self.setup_runner()
        remote.lost_finish = True
        self.assertEqual(runner.run()["status"], "ready")
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(remote.finishes[0], remote.finishes[1])

    def test_lost_dispatch_ack_does_not_invoke_model(self):
        remote, model, runner = self.setup_runner()
        remote.lost_attempt = True
        self.assertEqual(runner.run()["failure_code"], "ambiguous_completion")
        self.assertEqual(model.calls, [])

    def test_inflight_call_on_claim_is_not_redispatched(self):
        remote, model, runner = self.setup_runner()
        remote.dispatched = 1
        self.assertEqual(runner.run()["failure_code"], "ambiguous_completion")
        self.assertEqual(model.calls, [])

    def test_payload_digest_mismatch_prevents_model_call(self):
        remote, model, runner = self.setup_runner()
        remote.bad_hash = True
        self.assertEqual(runner.run()["failure_code"], "input_ineligible")
        self.assertEqual(model.calls, [])

    def test_unknown_usage_requires_review_no_fake_zero(self):
        remote, model, runner = self.setup_runner()
        model.method["usage"] = None
        self.assertEqual(runner.run()["failure_code"], "usage_unknown")
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(remote.finishes, [])

    def test_observed_prior_usage_counts_toward_budget(self):
        remote, model, runner = self.setup_runner()
        remote.tokens = 199999
        self.assertEqual(runner.run()["failure_code"], "budget_exceeded")
        self.assertEqual(remote.finishes, [])
        self.assertEqual(remote.tokens, 200014)
        self.assertEqual(remote.actions[-1]["method"], METHOD)

    def test_invalid_output_preserves_real_usage_without_fabricated_output(self):
        remote, model, runner = self.setup_runner()
        model.output = {**EMPTY, "owner_confirmed": True}
        self.assertEqual(runner.run()["failure_code"], "invalid_output")
        self.assertEqual(remote.tokens, 15)
        self.assertEqual(remote.actions[-1]["method"], METHOD)
        self.assertNotIn("output", remote.actions[-1])

    def test_unknown_usage_keeps_prior_subtotal_and_sends_no_method(self):
        remote, model, runner = self.setup_runner()
        remote.tokens = 27
        model.method["usage"] = {"input_tokens": 10, "output_tokens": 5}
        self.assertEqual(runner.run()["failure_code"], "usage_unknown")
        self.assertEqual(remote.tokens, 27)
        self.assertNotIn("method", remote.actions[-1])

    def test_known_interrupted_usage_is_reported_on_its_dispatch_only(self):
        remote, model, runner = self.setup_runner()
        def interrupted(*args):
            model.last_method = copy.deepcopy(METHOD)
            raise inference._TransientError("interrupted")
        model.call = interrupted
        self.assertEqual(runner.run()["failure_code"], "ambiguous_completion")
        self.assertEqual(remote.tokens, 15)
        dispatch = next(item for item in remote.actions if item["action"] == "attempt")
        self.assertEqual(remote.actions[-1]["call_id"], dispatch["call_id"])

    def test_ambiguous_model_failure_never_retries(self):
        remote, model, runner = self.setup_runner()
        model.failure = inference._TransientError("private source must not escape")
        result = runner.run()
        self.assertEqual(result["failure_code"], "ambiguous_completion")
        self.assertNotIn("private", json.dumps(result))
        self.assertEqual(len(model.calls), 1)

    def test_heartbeat_continues_during_model_and_failure_cancels_call(self):
        remote, model, runner = self.setup_runner(heartbeat_seconds=0.01)
        remote.failed_heartbeat = True
        model.delay = 0.1
        result=runner.run()
        self.assertEqual(result["failure_code"], "ambiguous_completion")
        self.assertEqual(result["heartbeat_diagnostic"]["outcome"], "uncertain")
        self.assertTrue(any(item["action"] == "heartbeat" for item in remote.actions))
        self.assertEqual(remote.finishes, [])

    def test_deadline_stops_before_dispatch(self):
        remote, model, runner = self.setup_runner()
        remote.deadline = time.time() - 1
        self.assertEqual(runner.run()["failure_code"], "deadline_exceeded")
        self.assertEqual(model.calls, [])

    def test_output_schema_citation_and_unknown_fields(self):
        subject.validate_output(EMPTY, OUTPUT_SCHEMA)
        bad = {**EMPTY, "owner_confirmed": True}
        with self.assertRaises(subject.ExecutorError):
            subject.validate_output(bad, OUTPUT_SCHEMA)
        bad = copy.deepcopy(EMPTY)
        bad["uncertainties"] = [{"reason": "coverage_gap", "text": "Unknown", "source_refs": [{"passage_id": "p9999_01"}]}]
        with self.assertRaises(subject.ExecutorError):
            subject.validate_citations(bad, {"task": "extract", "messages": []})

    def test_native_usage_rejects_bool_missing_and_inconsistent(self):
        self.assertEqual(subject.native_usage(USAGE), METHOD["usage"])
        for value in (None, {}, {**USAGE, "inputTokens": True}, {**USAGE, "totalTokens": 1}):
            with self.assertRaises(subject.ExecutorError):
                subject.native_usage(value)

    def test_telemetry_observer_ignores_other_thread_and_is_fresh_per_call(self):
        seen = []
        first = inference.TurnTelemetry("one", usage_observer=seen.append)
        first.observe({"method": "thread/tokenUsage/updated", "params": {"threadId": "other", "tokenUsage": {"last": USAGE}}})
        self.assertEqual(seen, [])
        first.observe({"method": "thread/tokenUsage/updated", "params": {"threadId": "one", "tokenUsage": {"last": USAGE}}})
        self.assertEqual(seen, [USAGE])
        second = inference.TurnTelemetry("two", usage_observer=seen.append)
        self.assertIsNone(second.finish()["token_usage"])

    def test_restricted_provider_calls_extract_once_default_model_and_real_usage(self):
        class Client:
            def __init__(self, *args, **kwargs):
                self.args, self.kwargs = args, kwargs
                self.metrics = {"token_usage": USAGE}
                self.calls = []
            def initialize(self):
                pass
            def request(self, *args):
                return {"data": [{"model": "gpt-6.1-sol", "supportedReasoningEfforts": [{"reasoningEffort": x} for x in ("low", "medium")]}]}
            def prepare_call(self, *args, **kwargs):
                self.diagnostic = None
            def extract(self, payload, model, **kwargs):
                self.calls.append((payload, model, kwargs))
                self.actual_model = "gpt-6.1-sol"
                self.actual_effort = kwargs["effort"]
                self.metrics = {"token_usage": USAGE}
                if getattr(self, "invalid_json", False):
                    raise inference.EvidenceError("invalid json")
                return EMPTY, "gpt-6.1-sol"
            def close(self):
                pass
        with tempfile.TemporaryDirectory(dir="/private/tmp" if Path("/private/tmp").exists() else "/tmp") as runtime, \
             patch.object(inference, "_check_capabilities"), patch.object(inference, "_Client", Client):
            provider = subject.RestrictedModel("binary", runtime, lambda: False)
            provider.prepare()
            provider.prepare_call({"task": "extract"}, contract.SCHEMAS["extract"], contract.PHASES["extract"], 300)
            output, method = provider.call({"task": "extract"}, contract.SCHEMAS["extract"], 300, 0)
            self.assertEqual(output, EMPTY)
            self.assertEqual(method["usage"], METHOD["usage"])
            self.assertEqual(len(provider.client.calls), 1)
            self.assertIsNone(provider.client.calls[0][1])
            self.assertEqual(provider.client.args[1], str(Path(runtime).resolve()))
            self.assertEqual(provider.client.kwargs["instructions"], subject.STATE_INSTRUCTIONS)
            with self.assertRaises(subject.ExecutorError):
                provider.client.usage_observer({"totalTokens": 200001})
            provider.client.invalid_json = True
            with self.assertRaises(inference.EvidenceError):
                provider.prepare_call({"task": "extract"}, contract.SCHEMAS["extract"], contract.PHASES["extract"], 300)
                provider.call({"task": "extract"}, contract.SCHEMAS["extract"], 300, 0)
            self.assertEqual(provider.last_method["usage"], METHOD["usage"])

    def test_actual_model_identity_required_and_old_metrics_cannot_be_reused(self):
        class Client:
            metrics = {"token_usage": USAGE}
            actual_model = "old-model"
            def extract(self, *args, **kwargs):
                self.metrics = {"token_usage": USAGE}
                return EMPTY, "codex-default"
        provider = subject.RestrictedModel("binary", "/tmp", lambda: False)
        provider.client = Client()
        provider.phase = "extract"
        provider.phase_started = time.monotonic()
        provider.phase_deadline = provider.phase_started + 75
        with self.assertRaises(subject.ExecutorError) as result:
            provider.call({}, OUTPUT_SCHEMA, 100, 0)
        self.assertEqual(result.exception.code, "invalid_output")
        self.assertIsNone(provider.last_method)


class NativeTests(unittest.TestCase):
    def test_real_subprocess_protocol_has_no_model_turns_or_temporary_mcp_alias(self):
        with tempfile.TemporaryDirectory() as runtime:
            binary = Path(runtime) / "fake-codex"
            binary.write_text("#!" + sys.executable + "\n" + '''
import json, sys
for line in sys.stdin:
    value=json.loads(line)
    if "id" not in value: continue
    method=value["method"]; p=value["params"]
    assert method != "turn/start"
    if method == "initialize": result={}
    elif method == "thread/start":
        assert p["ephemeral"] is True and p["environments"] == []
        assert p["cwd"] == __import__("os").getcwd()
        result={"thread":{"id":"private-native-thread","ephemeral":True}}
    elif method == "mcpServer/tool/call":
        assert p["server"] == "hoomy_remote_acceptance"
        assert p["tool"] == "hoomy_project_state_worker"
        result={"structuredContent":{"status":"fixture"}}
    else: raise AssertionError(method)
    print(json.dumps({"id":value["id"],"result":result}),flush=True)
''')
            binary.chmod(0o700)
            controller = subject.NativeController(str(binary), runtime)
            try:
                controller.initialize()
                self.assertEqual(controller.worker({"action": "claim"}), {"status": "fixture"})
                with self.assertRaises(subject.ExecutorError):
                    controller.request("turn/start", {})
            finally:
                controller.close()


class WatchTests(unittest.TestCase):
    def test_idle_never_constructs_executor_or_requests_refresh(self):
        calls = []
        class Controller:
            def pending(self, project):
                self_test.assertEqual(project, PROJECT)
                return {"project_id": project, "pending_run": None}
        self_test = self
        watcher = subject.Watcher(Controller(), calls.append, PROJECT)
        for _ in range(3):
            self.assertIsNone(watcher.step())
        self.assertEqual(calls, [])

    def test_explicit_queued_job_runs_once_even_with_repeated_pending_or_ambiguous_outcome(self):
        calls = []
        class Controller:
            def pending(self, project):
                return {"project_id": project, "pending_run": {"run_id": RUN, "status": "queued"}}
        def execute(run_id):
            calls.append(run_id)
            return {"status": "needs_review", "failure_code": "ambiguous_completion"}
        watcher = subject.Watcher(Controller(), execute, PROJECT)
        self.assertEqual(watcher.step()["status"], "needs_review")
        self.assertIsNone(watcher.step())
        self.assertEqual(calls, [RUN])

    def test_nonqueued_jobs_and_mismatched_project_responses_cannot_dispatch(self):
        calls = []
        class Controller:
            status = "running"
            def pending(self, project):
                return {"project_id": project, "pending_run": {"run_id": RUN, "status": self.status}}
        controller = Controller()
        watcher = subject.Watcher(controller, calls.append, PROJECT)
        for status in ("running", "ready", "failed", "needs_review"):
            controller.status = status
            with self.assertRaises(subject.ExecutorError):
                watcher.step()
        controller.pending = lambda project: {"project_id": str(uuid.uuid4()), "pending_run": {"run_id": RUN, "status": "queued"}}
        with self.assertRaises(subject.ExecutorError):
            watcher.step()
        self.assertEqual(calls, [])

    def test_explicit_project_uuid_is_forwarded_without_local_pilot_binding(self):
        other_project = "22222222-2222-4222-8222-222222222222"
        calls = []
        class Controller:
            def pending(self, project):
                calls.append(project)
                return {"project_id": project, "pending_run": None}
        watcher = subject.Watcher(Controller(), lambda _: self.fail("idle model execution"), other_project)
        self.assertIsNone(watcher.step())
        self.assertEqual(calls, [other_project])
        for project in (None, "project-alpha", "../another-project", "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"):
            with self.assertRaises(subject.ExecutorError):
                subject.Watcher(Controller(), calls.append, project)


    def test_pending_native_tool_is_exact_installed_job_tool_without_refresh(self):
        controller = object.__new__(subject.NativeController)
        controller.thread_id = "private-thread"
        calls = []
        controller.request = lambda method, args: calls.append((method, args)) or {"structuredContent": {"project_id": PROJECT, "pending_run": None}}
        controller.pending(PROJECT)
        self.assertEqual(calls, [("mcpServer/tool/call", {"threadId": "private-thread", "server": subject.SERVER,
            "tool": "hoomy_project_state_job", "arguments": {"project_id": PROJECT}})])


class StoreIntegrationTests(unittest.TestCase):
    def test_actual_backend_dispatch_persistence_finish_replay_and_pinned_citation(self):
        fixture = test_store.StoreTests()
        fixture.setUp()
        try:
            store = fixture.store
            pid = fixture.analyzed()
            seed = test_ingestion.IngestionTests()
            seed.pid = pid
            receipt = store.ingestion.accept(seed.payload(text="The build was reported complete."))
            state = store.project_state
            state.enabled_project_id = pid
            principal = {"account_id": "github:42", "workspace_id": "personal:42"}
            base = {"principal": principal, "project_id": pid}
            view = state.read(base)
            run = state.refresh({**base, "request_id": str(uuid.uuid4()),
                "context_revision": view["context_revision"], "corpus_revision": view["corpus_revision"],
                "method_hash": view["method_hash"]})
            pending = state.job(base)
            self.assertEqual(pending["pending_run"]["run_id"], run["run_id"])
            self.assertNotIn("input", pending["pending_run"])
            self.assertNotIn("messages", pending["pending_run"])

            class Controller:
                finishes = []
                def worker(self, arguments):
                    fields = dict(arguments)
                    action = fields.pop("action")
                    result = getattr(state, action)({"principal": principal, **fields})
                    if action == "finish":
                        self.finishes.append(copy.deepcopy(arguments))
                        if len(self.finishes) == 1:
                            raise subject.ExecutorError("ambiguous_completion")
                    return result

            class Provider:
                calls = 0
                def prepare(self):
                    pass
                def prepare_call(self, payload, schema, policy, remaining):
                    self_test.assertEqual(store.db.execute("SELECT count(*) FROM project_state_calls WHERE run_id=? AND status='dispatched'", (run["run_id"],)).fetchone()[0], 0)
                def call(self, payload, schema, remaining, used_tokens):
                    self.calls += 1
                    dispatched = store.db.execute("SELECT status FROM project_state_calls WHERE run_id=? ORDER BY rowid DESC LIMIT 1", (run["run_id"],)).fetchone()[0]
                    self_test.assertEqual(dispatched, "dispatched")
                    if payload["task"] == "extract":
                        message = payload["messages"][0]
                        result = copy.deepcopy(EMPTY)
                        result["assertions"] = [{"key": "build", "topic": "Build", "text": "Completion was reported.",
                            "kind": "reported_status", "reported_state": "reported_done", "limitations": ["Historical report only."],
                            "source_refs": [{"passage_id": message["passages"][0]["passage_id"]}]}]
                    else:
                        result = copy.deepcopy(payload["extractions"][0]["result"])
                    method=copy.deepcopy(METHOD)
                    method['reasoning_effort']=contract.PHASES[payload['task']]['effort']
                    return result, method
                def close(self):
                    pass

            self_test = self
            controller, provider = Controller(), Provider()
            runner = subject.Executor(controller, provider, pid, run["run_id"])
            self.assertEqual(runner.run()["status"], "ready")
            self.assertEqual(provider.calls, 2)
            self.assertEqual(controller.finishes[0], controller.finishes[1])
            published = state.read(base)
            self.assertIsNone(state.job(base)["pending_run"])
            self.assertEqual(published["job"]["observed_tokens"], 30)
            assertion = published["state"]["assertions"][0]
            self.assertEqual(assertion["reported_state"], "reported_done")
            rows = store.db.execute("SELECT data FROM project_state_citations WHERE run_id=?", (run["run_id"],)).fetchall()
            self.assertEqual(len(rows), 1)
            citation = json.loads(rows[0][0])
            self.assertEqual(citation["origins"][0]["source_version_id"], receipt["source_version_id"])
        finally:
            fixture.tearDown()


if __name__ == "__main__":
    unittest.main()
