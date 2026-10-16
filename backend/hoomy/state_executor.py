"""Explicit Mac-first runner: native OAuth RPC and isolated inference processes."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import signal
import subprocess
import tempfile
import threading
import time
import uuid

from . import inference
from . import project_state_contract as contract

SERVER = "hoomy_remote_acceptance"
TOOL = "hoomy_project_state_worker"
JOB_TOOL = "hoomy_project_state_job"
SCOPES = ["projects:read", "projects:context:write", "projects:state:refresh",
          "projects:state:execute", "offline_access"]
MAX_TOKENS = contract.MAX_TOKENS
UNKNOWN_MODELS = {"codex-default", "unknown", "default"}
HEARTBEAT_SECONDS = 20
STATE_INSTRUCTIONS = contract.STATE_INSTRUCTIONS



class ExecutorError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError):
        raise ExecutorError("invalid_output") from None


def identifier(value):
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ExecutorError("input_ineligible") from None
    return value


def native_binary():
    try:
        return inference._binary()
    except inference.InferenceError:
        candidate = Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex")
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
        raise ExecutorError("model_unavailable") from None


class NativeController:
    """The controller uses fixed installed read/worker tools, never model turns."""

    METHODS = {"initialize", "thread/start", "mcpServerStatus/list",
               "mcpServer/tool/call", "mcpServer/oauth/login"}

    def __init__(self, binary, runtime, *, rpc_observer=None):
        self.runtime = str(Path(runtime).resolve())
        self._waiters, self.serial = {}, 0
        self.lock, self.write_lock = threading.Lock(), threading.Lock()
        self.notifications = queue.Queue(maxsize=8)
        self.thread_id = None
        self.rpc_observer = rpc_observer
        self.rpc_local = threading.local()
        self.process = subprocess.Popen([binary, "app-server", "--stdio",
            "-c", 'history.persistence="none"', "-c", "log_dir=" + json.dumps(self.runtime),
            "-c", "sqlite_home=" + json.dumps(self.runtime), "-c", "otel.log_user_prompt=false"],
            cwd=self.runtime, env=inference._environment(), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        self.reader_terminal = "active"
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _send(self, value, *, readiness_deadline=None):
        try:
            with self.write_lock:
                wire = canonical(value) + b"\n"
                if readiness_deadline is not None and time.monotonic() >= readiness_deadline:
                    raise ExecutorError("ambiguous_completion")
                self.process.stdin.write(wire)
                self.process.stdin.flush()
        except (OSError, ValueError):
            raise ExecutorError("ambiguous_completion") from None

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(2_000_001)
                if not line:
                    self.reader_terminal = "eof"
                    break
                if len(line) > 2_000_000:
                    self.reader_terminal = "oversized_event"
                    break
                try:
                    event = json.loads(line)
                except ValueError:
                    self.reader_terminal = "invalid_json"
                    raise
                if not isinstance(event, dict):
                    self.reader_terminal = "invalid_object"
                    break
                if "method" in event and "id" in event:
                    self.reader_terminal = "server_request_refused"
                    self._send({"id": event["id"], "error": {"code": -32601,
                        "message": "The Hoomy controller does not permit server requests."}})
                    break
                with self.lock:
                    waiter = self._waiters.get(event.get("id"))
                if waiter:
                    try:
                        waiter.put_nowait(event)
                    except queue.Full:
                        self.reader_terminal = "queue_overflow"
                        break
                elif event.get("method") == "mcpServer/oauthLogin/completed":
                    try:
                        self.notifications.put_nowait(event.get("params", {}))
                    except queue.Full:
                        self.reader_terminal = "queue_overflow"
                        break
        except (OSError, ValueError, ExecutorError):
            if self.reader_terminal == "active":
                self.reader_terminal = "reader_error"
        finally:
            with self.lock:
                for waiter in self._waiters.values():
                    try:
                        waiter.put_nowait(None)
                    except queue.Full:
                        pass

    def request(self, method, params, timeout=25, *, readiness_deadline=None):
        if method not in self.METHODS or not isinstance(params, dict) or type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ExecutorError("input_ineligible")
        if readiness_deadline is not None and (type(readiness_deadline) not in (int, float) or not math.isfinite(readiness_deadline)):
            raise ExecutorError("input_ineligible")
        with self.lock:
            self.serial += 1
            serial = self.serial
            pending_count, collision = len(self._waiters), serial in self._waiters
            waiter = self._waiters[serial] = queue.Queue(maxsize=1)
        arguments = params.get("arguments")
        action = arguments.get("action") if isinstance(arguments, dict) else None
        operation = action if type(action) is str and action in {"claim", "heartbeat", "attempt", "finish", "fail"} else {
            "initialize": "initialize", "thread/start": "thread_start",
            "mcpServer/oauth/login": "oauth_login", "mcpServerStatus/list": "inventory"}.get(method, "cached_tool")
        started = time.monotonic()
        diagnostic = {"operation": operation, "outcome": "send_failure", "error_code": "none",
            "timeout_ms": min(300000, max(0, round(timeout * 1000))),
            "pending_before": min(100000, pending_count), "id_collision": collision}
        try:
            frame = {"id": serial, "method": method, "params": params}
            if readiness_deadline is None:
                self._send(frame)
            else:
                self._send(frame, readiness_deadline=readiness_deadline)
            wait_seconds = timeout if readiness_deadline is None else min(timeout, max(0, readiness_deadline - time.monotonic()))
            try:
                event = waiter.get(timeout=wait_seconds)
            except queue.Empty:
                diagnostic["outcome"] = "rpc_timeout"
                raise ExecutorError("ambiguous_completion") from None
            if readiness_deadline is not None and time.monotonic() >= readiness_deadline:
                diagnostic["outcome"] = "rpc_timeout"
                raise ExecutorError("ambiguous_completion")
            if not event:
                diagnostic["outcome"] = "process_eof"
                raise ExecutorError("ambiguous_completion")
            if "error" in event:
                diagnostic["outcome"] = "native_rpc_error"
                error = event["error"]
                code = error.get("code") if isinstance(error, dict) else None
                diagnostic["error_code"] = {-32600: "invalid_request", -32601: "method_not_found",
                    -32602: "invalid_params", -32603: "internal_error", -32700: "parse_error"}.get(code, "unknown") if type(code) is int else "unknown"
                raise ExecutorError("ambiguous_completion")
            if not isinstance(event.get("result"), dict):
                diagnostic["outcome"] = "malformed_result"
                raise ExecutorError("ambiguous_completion")
            result = event["result"]
            diagnostic["outcome"] = "tool_error" if result.get("isError") else "rpc_success"
            if method == "mcpServer/tool/call" and not result.get("isError") and not isinstance(result.get("structuredContent"), dict):
                diagnostic["outcome"] = "malformed_tool_result"
            return result
        finally:
            with self.lock:
                self._waiters.pop(serial, None)
            diagnostic["duration_ms"] = min(300000, max(0, int((time.monotonic() - started) * 1000)))
            self.rpc_local.last = diagnostic
            if self.rpc_observer:
                try:
                    self.rpc_observer(dict(diagnostic))
                except Exception:
                    pass

    def initialize(self, *, readiness_seconds=None):
        if readiness_seconds is not None and (type(readiness_seconds) is not int or readiness_seconds != contract.METHOD_CONFIG["initialization_seconds"]):
            raise ExecutorError("input_ineligible")
        deadline = time.monotonic() + readiness_seconds if readiness_seconds is not None else None
        def options(cap):
            if deadline is None:
                return {}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ExecutorError("ambiguous_completion")
            return {"timeout": min(cap, remaining), "readiness_deadline": deadline}
        self.request("initialize", {"clientInfo": {"name": "hoomy_state_controller", "version": "1"},
            "capabilities": {"experimentalApi": True, "requestAttestation": False,
                             "explicitGatewayOauth": True}}, **options(readiness_seconds or 25))
        if deadline is None:
            self._send({"method": "initialized", "params": {}})
        else:
            options(25)
            self._send({"method": "initialized", "params": {}}, readiness_deadline=deadline)
        thread = self.request("thread/start", {"cwd": self.runtime, "ephemeral": True,
            "sandbox": "read-only", "approvalPolicy": "never", "environments": []}, **options(25))
        if deadline is not None and time.monotonic() >= deadline:
            raise ExecutorError("ambiguous_completion")
        if thread.get("thread", {}).get("ephemeral") is not True:
            raise ExecutorError("input_ineligible")
        self.thread_id = thread.get("thread", {}).get("id")
        if not isinstance(self.thread_id, str) or not self.thread_id:
            raise ExecutorError("input_ineligible")

    def tool(self, name, arguments):
        if name not in (TOOL, JOB_TOOL):
            raise ExecutorError("input_ineligible")
        result = self.request("mcpServer/tool/call", {"threadId": self.thread_id,
            "server": SERVER, "tool": name, "arguments": arguments})
        if result.get("isError") or not isinstance(result.get("structuredContent"), dict):
            raise ExecutorError("ambiguous_completion")
        return result["structuredContent"]

    def worker(self, arguments):
        return self.tool(TOOL, arguments)

    def pending(self, project_id):
        return self.tool(JOB_TOOL, {"project_id": project_id})

    def login(self, emit):
        result = self.request("mcpServer/oauth/login", {"name": SERVER,
            "clientRegistration": "dcr", "scopes": SCOPES, "timeoutSecs": 1800})
        url = result.get("authorizationUrl")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ExecutorError("input_ineligible")
        emit({"status": "authorization_required", "authorization_url": url})
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            try:
                completed = self.notifications.get(timeout=min(1, deadline - time.monotonic()))
            except queue.Empty:
                continue
            if completed.get("name") not in (None, SERVER):
                continue
            if completed.get("loginId") not in (None, result.get("loginId")):
                continue
            if completed.get("success") is not True:
                raise ExecutorError("model_unavailable")
            return {"status": "authorized"}
        raise ExecutorError("deadline_exceeded")

    def close(self):
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=2)
            except ProcessLookupError:
                pass
        self.reader.join(timeout=2)
        for stream in (self.process.stdin, self.process.stdout):
            stream.close()


def native_usage(value):
    names = {"input_tokens": "inputTokens", "output_tokens": "outputTokens", "total_tokens": "totalTokens"}
    if not isinstance(value, dict) or any(type(value.get(key)) is not int or not 0 <= value[key] <= 2**53-1
                                         for key in names.values()):
        raise ExecutorError("usage_unknown")
    result = {key: value[native] for key, native in names.items()}
    if result["total_tokens"] < result["input_tokens"] + result["output_tokens"]:
        raise ExecutorError("usage_unknown")
    return result


DIAGNOSTIC_STAGES = {"capability_configuration", "initialize", "configuration", "thread_start", "workspace_check", "mcp_inventory",
    "turn_start", "turn_wait", "output_parse", "usage_validation", "model_identity", "output_validation"}
DIAGNOSTIC_CATEGORIES = {"native_rejection", "transport_or_timeout", "auth_required", "capacity_limited",
    "boundary_refusal", "invalid_output", "insufficient_device_evidence", "usage_unknown", "budget_exceeded", "cancelled", "unknown"}


def safe_diagnostic(value):
    keys = {"stage", "category", "turn_start_requested", "turn_started", "completion_received", "usage_known"}
    if not isinstance(value, dict) or set(value) != keys or type(value["stage"]) is not str or type(value["category"]) is not str or value["stage"] not in DIAGNOSTIC_STAGES or value["category"] not in DIAGNOSTIC_CATEGORIES:
        return None
    if any(type(value[key]) is not bool for key in keys - {"stage", "category"}):
        return None
    return dict(value)


class RestrictedModel:
    def __init__(self, binary, runtime, cancelled):
        self.binary, self.runtime, self.cancelled = binary, str(Path(runtime).resolve()), cancelled
        self.client = None
        self.last_method = None
        self.last_usage = None
        self.diagnostic = None
        self.phase = None
        self.phase_deadline = None

    def prepare(self):
        self.diagnostic = {"stage": "capability_configuration", "category": "unknown",
            "turn_start_requested": False, "turn_started": False,
            "completion_received": False, "usage_known": False}
        initialization_deadline = time.monotonic() + contract.INITIALIZATION_SECONDS
        inference._check_capabilities(self.binary, self.runtime, inference._environment())
        if time.monotonic() >= initialization_deadline:
            raise ExecutorError("deadline_exceeded")
        self.client = inference._Client(self.binary, self.runtime, self.cancelled,
                                        instructions=STATE_INSTRUCTIONS)
        self.diagnostic["stage"] = "initialize"
        self.client.cancelled = lambda: self.cancelled() or time.monotonic() >= initialization_deadline
        try:
            self.client.initialize()
            inventory = self.client.request("model/list", {"includeHidden": False})
            selected = next((item for item in inventory.get("data", []) if isinstance(item, dict) and
                item.get("model") == contract.METHOD_CONFIG["model"]["name"]), None)
            self.supported_efforts = {item.get("reasoningEffort") for item in selected.get("supportedReasoningEfforts", [])
                if isinstance(item, dict) and type(item.get("reasoningEffort")) is str} if selected else set()
            if not {phase["effort"] for phase in contract.PHASES.values()} <= self.supported_efforts:
                raise ExecutorError("model_unavailable")
            if time.monotonic() >= initialization_deadline:
                raise ExecutorError("deadline_exceeded")
        except Exception:
            self.diagnostic = safe_diagnostic(getattr(self.client, "diagnostic", None)) or self.diagnostic
            if time.monotonic() >= initialization_deadline and not self.cancelled():
                raise ExecutorError("deadline_exceeded") from None
            raise
        finally:
            self.client.cancelled = lambda: self.cancelled() or self.phase_deadline is not None and time.monotonic() >= self.phase_deadline

    def _guard_request(self, message):
        if not isinstance(message, dict) or not isinstance(message.get("params"), dict):
            raise ExecutorError("input_ineligible")
        params = message["params"]
        thread = params.get("threadId")
        identifier = message.get("id")
        try:
            thread_size = len(thread.encode("utf-8")) if isinstance(thread, str) else 0
        except UnicodeError:
            raise ExecutorError("input_ineligible") from None
        if (type(identifier) is not int or not 1 <= identifier <= 9007199254740991 or not isinstance(thread, str)
                or not thread or thread_size > contract.METHOD_CONFIG["protocol_identifier_bytes"]
                or self.client.actual_model != contract.METHOD_CONFIG["model"]["name"]
                or params.get("effort") != contract.PHASES[self.phase]["effort"]
                or params.get("outputSchema") != contract.SCHEMAS[self.phase]):
            raise ExecutorError("input_ineligible")
        try:
            size = contract.native_request_bytes(message, self.client.instructions)
            if (size > contract.PHASES[self.phase]["envelope_bytes"]
                    or contract.serialized_bytes(params["outputSchema"]) > contract.METHOD_CONFIG["schema_bytes"]
                    or contract.serialized_bytes(self.client.instructions) > contract.METHOD_CONFIG["instructions_bytes"]):
                raise ExecutorError("input_ineligible")
        except (ValueError, TypeError, UnicodeError, KeyError):
            raise ExecutorError("input_ineligible") from None

    def prepare_call(self, payload, schema, phase_config, remaining):
        phase = payload.get("task") if isinstance(payload, dict) else None
        if type(phase) is not str or phase not in contract.PHASES or phase_config != contract.PHASES[phase] or schema != contract.SCHEMAS[phase]:
            raise ExecutorError("input_ineligible")
        try:
            fits = contract.fits_input(payload, phase)
        except (ValueError, TypeError, UnicodeError):
            fits = False
        if not fits:
            raise ExecutorError("input_ineligible")
        available = remaining - (contract.RECONCILIATION_RESERVE if phase == "extract" else 0)
        if available <= 0:
            raise ExecutorError("deadline_exceeded")
        self.phase, self.phase_started = phase, time.monotonic()
        self.phase_deadline = self.phase_started + min(phase_config["call_seconds"], available)
        self.client.call_deadline = self.phase_deadline
        self.client.native_request_guard = self._guard_request
        self.last_usage = self.last_method = None
        self.client.actual_effort = None
        self.client.prepare_call(payload, None, schema=schema, effort=phase_config["effort"])
        self.diagnostic = safe_diagnostic(self.client.diagnostic)

    def call(self, payload, schema, remaining, used_tokens):
        if remaining <= 0 or self.cancelled() or self.phase not in contract.PHASES or self.phase_deadline is None:
            raise ExecutorError("deadline_exceeded")
        if time.monotonic() >= self.phase_deadline:
            raise ExecutorError("deadline_exceeded")
        self.client.metrics = {}
        self.client.actual_model = None
        self.last_method = self.last_usage = None
        self.diagnostic = None
        self.client.diagnostic = None
        self.client.turn_timeout = max(0.1, min(contract.PHASES[self.phase]["call_seconds"], remaining,
            self.phase_deadline - time.monotonic()))
        self.client.usage_observer = lambda value: self._observe(value, used_tokens)
        started = self.phase_started
        try:
            output, _ = self.client.extract(payload, None, schema=schema, effort=contract.PHASES[self.phase]["effort"])
            self.diagnostic = safe_diagnostic(getattr(self.client, "diagnostic", None))
            if self.diagnostic:
                self.diagnostic["stage"] = "usage_validation"
            self.last_usage = native_usage(self.client.metrics.get("token_usage"))
        except inference.InferenceCancelled:
            if time.monotonic() >= self.phase_deadline:
                raise ExecutorError("deadline_exceeded") from None
            raise
        except ExecutorError as exc:
            if self.diagnostic:
                self.diagnostic["category"] = exc.code if exc.code in DIAGNOSTIC_CATEGORIES else "unknown"
            raise
        finally:
            if self.last_usage is None:
                try:
                    self.last_usage = native_usage(self.client.metrics.get("token_usage"))
                except ExecutorError:
                    pass
            self.diagnostic = self.diagnostic or safe_diagnostic(getattr(self.client, "diagnostic", None))
            if self.diagnostic:
                self.diagnostic["usage_known"] = self.last_usage is not None
            model = self.client.actual_model
            effort = getattr(self.client, "actual_effort", None)
            if model == contract.METHOD_CONFIG["model"]["name"] and effort == contract.PHASES[self.phase]["effort"] and self.last_usage:
                self.last_method = {"adapter": "codex-native-v1", "model": model, "reasoning_effort": effort,
                    "elapsed_ms": min(300000, int((time.monotonic() - started) * 1000)), "usage": self.last_usage}
            self.client.call_deadline = None
        usage = self.last_usage
        if used_tokens + usage["total_tokens"] > MAX_TOKENS:
            if self.diagnostic:
                self.diagnostic.update(stage="usage_validation", category="budget_exceeded")
            raise ExecutorError("budget_exceeded")
        if self.last_method is None:
            if self.diagnostic:
                self.diagnostic.update(stage="model_identity", category="invalid_output")
            raise ExecutorError("invalid_output")
        return output, self.last_method

    def _observe(self, value, used_tokens):
        # A missing notification is handled at completion, never estimated.
        self.last_usage = native_usage(value)
        if used_tokens + self.last_usage["total_tokens"] > MAX_TOKENS:
            raise ExecutorError("budget_exceeded")

    def close(self):
        if self.client:
            self.client.close()


VALIDATION_FIELDS = {"schema_version", "assertions", "uncertainties", "contradictions", "key", "topic", "text", "kind", "reported_state", "limitations", "source_refs", "passage_id", "reason", "assertion_keys", "adapter", "model", "elapsed_ms", "usage", "input_tokens", "output_tokens", "total_tokens"}


class ValidationError(ExecutorError):
    def __init__(self, substage, rule, pointer=()):
        super().__init__("invalid_output")
        self.detail = {"substage": substage, "rule": rule, "pointer": [
            part if type(part) is int and 0 <= part <= 9999 else
            part if type(part) is str and part in VALIDATION_FIELDS else "unknown_property"
            for part in pointer[:16]]}


class DeviceEvidenceError(ValidationError):
    def __init__(self):
        super().__init__("citations", "required_machine_evidence", ("source_refs",))
        self.code = "insufficient_device_evidence"
        self.args = (self.code,)


def validate_output(value, schema, depth=0, pointer=()):
    """Validate the server's closed schema without adding an inference runtime."""
    def reject(rule):
        raise ValidationError("schema", rule, pointer)
    if depth > 16 or not isinstance(schema, dict):
        reject("schema_depth")
    kind = schema.get("type")
    valid = {"object": isinstance(value, dict), "array": isinstance(value, list),
             "string": isinstance(value, str), "integer": type(value) is int}
    if not valid.get(kind):
        reject("value_type")
    if "enum" in schema and value not in schema["enum"]:
        reject("enum_value")
    if kind == "object":
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is not False:
            reject("schema_object")
        if set(value) != set(props):
            missing = next((key for key in props if key not in value), None)
            raise ValidationError("schema", "required_property" if missing else "unknown_property",
                pointer + (missing or "unknown_property",))
        for key, child in value.items():
            validate_output(child, props[key], depth + 1, pointer + (key,))
    elif kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 500):
            reject("array_length")
        for index, child in enumerate(value):
            validate_output(child, schema["items"], depth + 1, pointer + (index,))
    elif kind == "string":
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 1500):
            reject("string_length")
        if schema.get("minLength", 0) and not value.strip():
            reject("blank_string")
        try:
            canonical(value)
        except ExecutorError:
            reject("scalar_encoding")


def validate_citations(output, payload):
    refs = [(section, index, ref_index, ref)
        for section in ("assertions", "uncertainties") for index, item in enumerate(output[section])
        for ref_index, ref in enumerate(item["source_refs"])]
    if len(refs) > 500:
        raise ValidationError("citations", "reference_limit")
    # The server owns passage text and provenance; the model only selects IDs.
    allowed = contract.allowed_passage_ids(payload)
    for section, index, ref_index, ref in refs:
        pointer = (section, index, "source_refs", ref_index)
        if ref["passage_id"] not in allowed:
            raise ValidationError("citations", "passage_membership", pointer + ("passage_id",))
    if not contract.required_machine_aliases(payload) <= contract.referenced_machine_aliases(payload, output):
        raise DeviceEvidenceError()
    keys = [item["key"] for item in output["assertions"]]
    if len(set(keys)) != len(keys):
        raise ValidationError("citations", "duplicate_assertion_key", ("assertions",))
    for index, item in enumerate(output["contradictions"]):
        if len(set(item["assertion_keys"])) != len(item["assertion_keys"]):
            raise ValidationError("citations", "duplicate_contradiction_key", ("contradictions", index, "assertion_keys"))
        if any(key not in keys for key in item["assertion_keys"]):
            raise ValidationError("citations", "unknown_assertion_key", ("contradictions", index, "assertion_keys"))


def validate_method(method, used_tokens=None):
    if not isinstance(method, dict) or not {"adapter", "model", "elapsed_ms", "usage"} <= set(method) or set(method) - {"adapter", "model", "elapsed_ms", "usage", "reasoning_effort"}:
        raise ExecutorError("usage_unknown")
    if method["adapter"] != "codex-native-v1" or not isinstance(method["model"], str) or not 1 <= len(method["model"]) <= 200 or method["model"] in UNKNOWN_MODELS:
        raise ExecutorError("invalid_output")
    canonical(method["model"])
    if "reasoning_effort" in method and (type(method["reasoning_effort"]) is not str or method["reasoning_effort"] not in contract.EFFORTS):
        raise ExecutorError("invalid_output")
    if type(method["elapsed_ms"]) is not int or not 0 <= method["elapsed_ms"] <= 300000:
        raise ExecutorError("invalid_output")
    usage = method["usage"]
    if not isinstance(usage, dict) or set(usage) != {"input_tokens", "output_tokens", "total_tokens"}:
        raise ExecutorError("usage_unknown")
    native_usage({"inputTokens": usage["input_tokens"], "outputTokens": usage["output_tokens"], "totalTokens": usage["total_tokens"]})
    if used_tokens is not None and used_tokens + usage["total_tokens"] > MAX_TOKENS:
        raise ExecutorError("budget_exceeded")


class Executor:
    def __init__(self, controller, model, project_id, run_id, *, clock=time.time,
                 heartbeat_seconds=HEARTBEAT_SECONDS, cancelled=None, rejected_output=None):
        self.controller, self.model = controller, model
        self.project_id, self.run_id = identifier(project_id), identifier(run_id)
        self.clock, self.heartbeat_seconds = clock, heartbeat_seconds
        if not 0 < heartbeat_seconds <= 30:
            raise ExecutorError("input_ineligible")
        self.external_cancelled = cancelled or (lambda: False)
        self.stop = threading.Event()
        self.heartbeat_failure = None
        self.control = None
        self.active_call_id = self.active_method = None
        self.rejected_output = rejected_output
        self.validation_diagnostic = None
        self.heartbeat_diagnostic = None

    def rpc(self, action, **fields):
        args = {"action": action, "project_id": self.project_id, "run_id": self.run_id}
        if action != "claim":
            args.update({key: self.control[key] for key in ("lease_id", "input_hash")})
        return self.controller.worker({**args, **fields})

    def _control(self, value):
        if not isinstance(value, dict) or value.get("project_id") != self.project_id or value.get("run_id") != self.run_id:
            raise ExecutorError("input_ineligible")
        if value.get("status") in ("ready", "failed", "needs_review"):
            return value
        if value.get("status") != "running" or value.get("budget") != contract.BUDGET or value.get("method_version") != contract.METHOD or value.get("method_hash") != contract.METHOD_HASH or value.get("execution_policy") != contract.METHOD_CONFIG:
            raise ExecutorError("input_ineligible")
        identifier(value.get("lease_id"))
        digest = value.get("input_hash")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ExecutorError("input_ineligible")
        for key in ("deadline_at", "lease_expires_at"):
            if type(value.get(key)) not in (int, float) or not math.isfinite(value[key]):
                raise ExecutorError("input_ineligible")
        if value["deadline_at"] - self.clock() > 301:
            raise ExecutorError("input_ineligible")
        if type(value.get("observed_tokens")) is not int or not 0 <= value["observed_tokens"] <= MAX_TOKENS:
            raise ExecutorError("budget_exceeded")
        for key in ("completed_calls", "dispatched_calls"):
            if type(value.get(key)) is not int or not 0 <= value[key] <= contract.MAX_CHUNKS + 1:
                raise ExecutorError("input_ineligible")
        if value["dispatched_calls"] != value["completed_calls"]:
            raise ExecutorError("ambiguous_completion")
        next_call = value.get("next")
        if not isinstance(next_call, dict) or set(next_call) != {"phase", "chunk_index"}:
            raise ExecutorError("input_ineligible")
        if next_call["phase"] == "extract":
            if type(next_call["chunk_index"]) is not int or not 0 <= next_call["chunk_index"] < contract.MAX_CHUNKS:
                raise ExecutorError("input_ineligible")
        elif next_call["phase"] != "reconcile" or next_call["chunk_index"] is not None:
            raise ExecutorError("input_ineligible")
        return value

    def is_cancelled(self):
        return bool(self.heartbeat_failure or self.external_cancelled() or
                    self.control and self.clock() >= self.control["deadline_at"])

    def _heartbeat(self):
        successes = 0
        while not self.stop.wait(self.heartbeat_seconds):
            started = time.monotonic()
            server_stopped = False
            try:
                response = self.rpc("heartbeat")
                if not isinstance(response, dict) or response.get("project_id") != self.project_id or response.get("run_id") != self.run_id:
                    raise ExecutorError("ambiguous_completion")
                self.heartbeat_diagnostic = {"outcome": "running", "duration_ms": min(300000,
                    int((time.monotonic() - started) * 1000)), "successful_responses": min(successes + 1, 100000)}
                if response.get("status") != "running":
                    server_stopped = response.get("status") in ("ready", "failed", "needs_review")
                    if not server_stopped:
                        raise ExecutorError("ambiguous_completion")
                    self.heartbeat_diagnostic["outcome"] = "server_stopped"
                    code = response.get("failure_code")
                    raise ExecutorError(code if type(code) is str and code in
                        {"lease_expired", "deadline_exceeded", "cancelled", "unsupported_device_scope", "insufficient_device_evidence"} else "ambiguous_completion")
                successes += 1
            except Exception as exc:
                code = exc.code if server_stopped and isinstance(exc, ExecutorError) and exc.code in {
                    "lease_expired", "deadline_exceeded", "cancelled", "unsupported_device_scope", "insufficient_device_evidence"} else "ambiguous_completion"
                self.heartbeat_diagnostic = {"outcome": "server_stopped" if self.heartbeat_diagnostic and
                    self.heartbeat_diagnostic["outcome"] == "server_stopped" else "uncertain",
                    "duration_ms": min(300000, int((time.monotonic() - started) * 1000)),
                    "successful_responses": min(successes, 100000), "failure_code": code}
                self.heartbeat_failure = code
                return

    def _failure(self, code):
        diagnostic = safe_diagnostic(getattr(self.model, "diagnostic", None))
        extra = {"diagnostic": diagnostic} if diagnostic else {}
        if self.validation_diagnostic:
            extra["validation_diagnostic"] = self.validation_diagnostic
        if self.heartbeat_diagnostic:
            extra["heartbeat_diagnostic"] = self.heartbeat_diagnostic
        try:
            fields = {}
            method = self.active_method or getattr(self.model, "last_method", None)
            if self.active_call_id and method:
                try:
                    validate_method(method)
                    fields = {"call_id": self.active_call_id, "method": method}
                except ExecutorError:
                    pass
            result = self.rpc("fail", code=code, **fields)
            return {"status": result.get("status", "needs_review"), "failure_code": code, **extra}
        except Exception:
            return {"status": "needs_review", "failure_code": code, "server_acknowledged": False, **extra}

    def _terminal_result(self):
        result = {"status": self.control["status"]}
        code = self.control.get("failure_code")
        if type(code) is str and code in {"unsupported_device_scope", "insufficient_device_evidence"}:
            result["failure_code"] = code
        return result

    def run(self):
        heartbeat = None
        try:
            self.control = self._control(self.rpc("claim", worker_id=str(uuid.uuid4())))
            if self.control["status"] != "running":
                return self._terminal_result()
            self.model.cancelled = self.is_cancelled
            heartbeat = threading.Thread(target=self._heartbeat, daemon=True)
            heartbeat.start()
            try:
                self.model.prepare()
            except ExecutorError:
                raise
            except Exception:
                raise ExecutorError("model_unavailable") from None
            calls = 0
            while self.control["status"] == "running":
                if self.is_cancelled():
                    raise ExecutorError(self.heartbeat_failure or
                        ("deadline_exceeded" if self.clock() >= self.control["deadline_at"] else "cancelled"))
                calls += 1
                if calls > contract.MAX_CHUNKS + 1 or self.control["completed_calls"] >= contract.MAX_CHUNKS + 1:
                    raise ExecutorError("budget_exceeded")
                if self.control["observed_tokens"] >= MAX_TOKENS:
                    raise ExecutorError("budget_exceeded")
                next_call, call_id = self.control["next"], str(uuid.uuid4())
                plan = self.control.get("next_plan")
                required = {"phase", "chunk_index", "input", "output_schema", "phase_config", "payload_hash", "method_version", "method_hash"}
                if (not isinstance(plan, dict) or set(plan) != required or any(plan.get(key) != next_call[key] for key in next_call)
                        or plan["method_version"] != contract.METHOD or plan["method_hash"] != contract.METHOD_HASH
                        or plan["phase_config"] != contract.PHASES[next_call["phase"]]
                        or plan["output_schema"] != contract.SCHEMAS[next_call["phase"]]
                        or not isinstance(plan["input"], dict) or plan["input"].get("task") != next_call["phase"]
                        or hashlib.sha256(canonical(plan["input"])).hexdigest() != plan["payload_hash"]):
                    raise ExecutorError("input_ineligible")
                control = plan["input"].get("control")
                if (not isinstance(control, dict) or set(control) != {"selection_partial", "required_machine_aliases"}
                        or control["selection_partial"] is not True):
                    raise ExecutorError("input_ineligible")
                machines = control["required_machine_aliases"]
                if (not isinstance(machines, list) or not 1 <= len(machines) <= (1 if next_call["phase"] == "extract" else 2)
                        or any(type(alias) is not str or alias not in {"machine-1", "machine-2"} for alias in machines)
                        or len(set(machines)) != len(machines)):
                    raise ExecutorError("input_ineligible")
                self.active_call_id, self.active_method = None, None
                self.model.last_method = None
                self.model.diagnostic = None
                self.validation_diagnostic = None
                try:
                    self.model.prepare_call(plan["input"], plan["output_schema"], plan["phase_config"], self.control["deadline_at"] - self.clock())
                except ExecutorError:
                    raise
                except Exception:
                    raise ExecutorError("model_unavailable") from None
                self.active_call_id = call_id
                # This acknowledged write is the only permission to dispatch a model call.
                attempt = self.rpc("attempt", call_id=call_id, **next_call)
                if any(attempt.get(key) != expected for key, expected in {
                        "run_id": self.run_id, "call_id": call_id, **next_call}.items()):
                    raise ExecutorError("input_ineligible")
                if any(attempt.get(key) != expected for key, expected in plan.items()):
                    raise ExecutorError("input_ineligible")
                payload, schema = attempt.get("input"), attempt.get("output_schema")
                if not isinstance(payload, dict) or payload.get("task") != next_call["phase"] or not isinstance(schema, dict):
                    raise ExecutorError("input_ineligible")
                if hashlib.sha256(canonical(payload)).hexdigest() != attempt.get("payload_hash"):
                    raise ExecutorError("input_ineligible")
                if self.is_cancelled():
                    raise ExecutorError(self.heartbeat_failure or "deadline_exceeded")
                try:
                    output, method = self.model.call(payload, schema,
                        self.control["deadline_at"] - self.clock(), self.control["observed_tokens"])
                    self.active_method = method
                except ExecutorError:
                    raise
                except inference.EvidenceError:
                    raise ExecutorError("invalid_output") from None
                except Exception:
                    code = self.heartbeat_failure or ("deadline_exceeded" if self.clock() >= self.control["deadline_at"]
                        else "cancelled" if self.external_cancelled() else "ambiguous_completion")
                    raise ExecutorError(code) from None
                diagnostic = safe_diagnostic(getattr(self.model, "diagnostic", None))
                if diagnostic:
                    diagnostic.update(stage="output_validation", category="unknown")
                    self.model.diagnostic = diagnostic
                try:
                    validate_output(output, schema)
                    validate_citations(output, payload)
                    if next_call["phase"] == "reconcile":
                        from .project_state_quality import filter_model_output, model_quote_records
                        visible, _ = filter_model_output(output, model_quote_records(payload))
                        # Publication retains raw output; only visible evidence proves machine coverage.
                        validate_citations(visible, payload)
                    try:
                        validate_method(method, self.control["observed_tokens"])
                        if method.get("reasoning_effort") != contract.PHASES[next_call["phase"]]["effort"] or method.get("model") != contract.METHOD_CONFIG["model"]["name"]:
                            raise ExecutorError("invalid_output")
                    except ExecutorError as exc:
                        self.validation_diagnostic = {"substage": "method", "rule": "method_metadata", "pointer": []}
                        raise
                    if contract.serialized_bytes(output) > contract.PHASES[next_call["phase"]]["output_bytes"]:
                        raise ValidationError("bytecap", "output_byte_limit")
                except ExecutorError as exc:
                    from .state_rejection import rejection_metadata
                    detail = exc.detail if isinstance(exc, ValidationError) else self.validation_diagnostic or {
                        "substage": "bytecap", "rule": "scalar_encoding", "pointer": []}
                    self.validation_diagnostic = {**detail, **rejection_metadata(output, schema)}
                    if self.rejected_output:
                        try:
                            self.rejected_output(output, schema, self.validation_diagnostic)
                        except Exception:
                            self.validation_diagnostic["artifact_capture"] = "failed"
                    if diagnostic:
                        self.model.diagnostic["category"] = exc.code if exc.code in DIAGNOSTIC_CATEGORIES else "unknown"
                    raise
                if self.is_cancelled():
                    raise ExecutorError(self.heartbeat_failure or "deadline_exceeded")
                # A lost finish ACK retries the exact result, never the model invocation.
                for retry in range(2):
                    try:
                        response = self.rpc("finish", call_id=call_id, output=output, method=method)
                        break
                    except ExecutorError:
                        if retry:
                            raise
                self.control = self._control(response)
                self.active_call_id = self.active_method = None
            return {**self._terminal_result(), "completed_calls": self.control.get("completed_calls"),
                    "observed_tokens": self.control.get("observed_tokens")}
        except ExecutorError as exc:
            return self._failure(exc.code) if self.control else {"status": "needs_review", "failure_code": exc.code}
        except KeyboardInterrupt:
            return self._failure("cancelled") if self.control else {"status": "needs_review", "failure_code": "cancelled"}
        except Exception:
            return self._failure("invalid_output") if self.control else {"status": "needs_review", "failure_code": "input_ineligible"}
        finally:
            self.stop.set()
            if heartbeat:
                heartbeat.join(timeout=26)
            try:
                self.model.close()
            except Exception:
                pass


class Watcher:
    """Consume owner-queued jobs only; idle polling never constructs a model client."""

    def __init__(self, controller, execute, project_id, *, emit=None, stopped=None, poll_seconds=15):
        project_id = identifier(project_id)
        if not 5 <= poll_seconds <= 300:
            raise ExecutorError("input_ineligible")
        self.controller, self.execute, self.project_id = controller, execute, project_id
        self.emit, self.stopped = emit or (lambda _: None), stopped or threading.Event()
        self.poll_seconds, self.seen = poll_seconds, set()
        self.successful_polls = 0

    def step(self):
        value = self.controller.pending(self.project_id)
        if not isinstance(value, dict) or value.get("project_id") != self.project_id or "pending_run" not in value:
            raise ExecutorError("input_ineligible")
        pending = value["pending_run"]
        if pending is None:
            self.successful_polls = min(100000, self.successful_polls + 1)
            return None
        if not isinstance(pending, dict) or pending.get("status") != "queued":
            raise ExecutorError("input_ineligible")
        run_id = identifier(pending.get("run_id"))
        self.successful_polls = min(100000, self.successful_polls + 1)
        if run_id in self.seen:
            return None
        if len(self.seen) >= 1024:
            self.stopped.set()
            result = {"status": "stopped", "reason": "watch_capacity_reached"}
            self.emit(result)
            return result
        self.seen.add(run_id)
        result = self.execute(run_id)
        self.emit(result)
        return result

    def run(self):
        while not self.stopped.is_set():
            self.step()
            if self.stopped.wait(self.poll_seconds):
                break
        return {"status": "stopped"}


def watch_failure_diagnostic(controller, watcher, stage):
    """Capture observed transport facts before cleanup changes native exit status."""
    last = getattr(getattr(controller, "rpc_local", None), "last", None)
    fields = {"operation", "outcome", "error_code", "timeout_ms", "duration_ms", "pending_before", "id_collision"}
    operations = {"initialize", "thread_start", "inventory", "oauth_login", "cached_tool", "claim", "heartbeat", "attempt", "finish", "fail"}
    outcomes = {"send_failure", "rpc_timeout", "process_eof", "native_rpc_error", "malformed_result", "tool_error", "rpc_success", "malformed_tool_result"}
    errors = {"none", "invalid_request", "method_not_found", "invalid_params", "internal_error", "parse_error", "unknown"}
    valid = (isinstance(last, dict) and set(last) == fields and
        type(last["operation"]) is str and last["operation"] in operations and
        type(last["outcome"]) is str and last["outcome"] in outcomes and
        type(last["error_code"]) is str and last["error_code"] in errors and
        type(last["id_collision"]) is bool and all(type(last[key]) is int and 0 <= last[key] <= limit
            for key, limit in (("timeout_ms", 300000), ("duration_ms", 300000), ("pending_before", 100000))))
    native_alive = native_exit = None
    try:
        status = controller.process.poll()
        if status is None:
            native_alive = True
        elif type(status) is int and -255 <= status <= 255:
            native_alive, native_exit = False, status
    except Exception:
        pass
    reader = getattr(controller, "reader_terminal", "unknown")
    if type(reader) is not str or reader not in {"active", "eof", "oversized_event", "invalid_json", "invalid_object", "server_request_refused", "queue_overflow", "reader_error"}:
        reader = "unknown"
    polls = getattr(watcher, "successful_polls", 0)
    return {"stage": stage if stage in ("initialize", "pending_poll") else "unknown",
        "successful_polls": polls if type(polls) is int and 0 <= polls <= 100000 else 0,
        "native_alive": native_alive, "native_exit_status": native_exit, "reader_terminal": reader,
        "rpc": dict(last) if valid else None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("login", "run", "watch"))
    parser.add_argument("--project-id")
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    emit = lambda value: print(json.dumps(value), flush=True)
    watch_diagnostic = None
    try:
        if args.action in ("run", "watch"):
            identifier(args.project_id)
            if args.action == "run":
                identifier(args.run_id)
            elif args.run_id is not None:
                raise ExecutorError("input_ineligible")
        binary = native_binary()
        # Neither temporary cwd is a collector-approved project root.
        temporary_root = "/private/tmp" if Path("/private/tmp").is_dir() else "/tmp"
        with tempfile.TemporaryDirectory(prefix="hoomy-state-controller-", dir=temporary_root) as controller_dir:
            controller = NativeController(binary, controller_dir)
            watcher, watch_stage = None, "initialize"
            try:
                if args.action == "watch":
                    controller.initialize(readiness_seconds=contract.METHOD_CONFIG["initialization_seconds"])
                else:
                    controller.initialize()
                if args.action == "login":
                    result = controller.login(emit)
                else:
                    cancelled = threading.Event()
                    previous = {sig: signal.signal(sig, lambda *_: cancelled.set())
                                for sig in (signal.SIGINT, signal.SIGTERM)}
                    try:
                        def execute(run_id):
                            with tempfile.TemporaryDirectory(prefix="hoomy-state-model-", dir=temporary_root) as model_dir:
                                model = RestrictedModel(binary, model_dir, lambda: False)
                                executor = Executor(controller, model, args.project_id, run_id,
                                                    cancelled=cancelled.is_set)
                                return executor.run()
                        if args.action == "watch":
                            watcher = Watcher(controller, execute, args.project_id, emit=emit, stopped=cancelled)
                            watch_stage = "pending_poll"
                            result = watcher.run()
                        else:
                            result = execute(args.run_id)
                    finally:
                        for sig, handler in previous.items():
                            signal.signal(sig, handler)
                emit(result)
                return 0 if result["status"] in ("ready", "authorized", "stopped") else 2
            except BaseException:
                if args.action == "watch":
                    watch_diagnostic = watch_failure_diagnostic(controller, watcher, watch_stage)
                raise
            finally:
                controller.close()
    except (ExecutorError, inference.InferenceError) as exc:
        emit({"status": "needs_review", "failure_code": getattr(exc, "code", "model_unavailable"),
              **({"watch_diagnostic": watch_diagnostic} if watch_diagnostic else {})})
        return 2
    except KeyboardInterrupt:
        emit({"status": "needs_review", "failure_code": "cancelled"})
        return 2
    except Exception:
        emit({"status": "needs_review", "failure_code": "input_ineligible"})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
