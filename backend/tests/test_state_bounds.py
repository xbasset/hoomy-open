import copy
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from hoomy import inference, project_state_contract as contract, state_executor as executor
import test_state_method


def full_extract(first):
    ids = iter(f"p{index:04d}_01" for index in range(first, first + 16))
    output = {"schema_version": 1, "assertions": [{"key": f"{index:02d}" + "\\" * 38,
        "topic": "\\" * 80, "text": "\\" * 240, "kind": "reported_status",
        "reported_state": "reported_blocked", "limitations": ["\\" * 120],
        "source_refs": [{"passage_id": next(ids)}, {"passage_id": next(ids)}]}
        for index in range(6)], "uncertainties": [{"reason": "missing_current_observation",
        "text": "\\" * 180, "source_refs": [{"passage_id": next(ids)}, {"passage_id": next(ids)}]}
        for _ in range(2)], "contradictions": []}
    cap = contract.PHASES["extract"]["output_bytes"]
    for item in output["assertions"] + output["uncertainties"]:
        for field in ("topic", "text"):
            if field not in item:
                continue
            while len(item[field]) > 1 and contract.serialized_bytes(output) > cap:
                item[field] = item[field][:-1]
    difference = cap - contract.serialized_bytes(output)
    assert difference in (0, 1)
    if difference:
        output["assertions"][-1]["text"] = "☃" + output["assertions"][-1]["text"][1:]
    assert contract.serialized_bytes(output) == cap
    executor.validate_output(output, contract.SCHEMAS["extract"])
    return output


def full_context():
    context = {name: {"answer": "", "response": "answer", "answered_at": "2026-10-13T00:00:00Z"}
        for name in ("goal", "motivation", "priority", "constraint", "success_criterion")}
    for item in context.values():
        for _ in range(512):
            difference = 4096 - contract.serialized_bytes(context)
            if difference == 0:
                break
            item["answer"] += "\\" if difference >= 2 else "x"
        if contract.serialized_bytes(context) == 4096:
            break
    assert contract.serialized_bytes(context) == 4096
    assert all(len(item["answer"].encode()) <= 512 for item in context.values())
    return context


def request(payload):
    return {"id": 9007199254740991, "method": "turn/start", "params": {
        "threadId": "\x01" * 128, "environments": [],
        "input": [{"type": "text", "text": contract.serialized(payload)}],
        "outputSchema": contract.SCHEMAS["reconcile"], "approvalPolicy": "never", "effort": "medium"}}


def composition():
    parts = [{"passage_id": f"p{index:04d}_01", "text": "\x01" * 112} for index in range(1, 33)]
    outputs = [full_extract(1), full_extract(17)]
    control = {"selection_partial": True, "required_machine_aliases": ["machine-1", "machine-2"]}
    groups = [{"machine_alias": f"machine-{i+1}", "shared_passage_ids": [part["passage_id"] for part in parts[:16]] if i == 0 else [],
        "result": output} for i, output in enumerate(outputs)]
    headers = [{k: v for k, v in group.items() if k != "result"} for group in groups]
    assert contract.serialized_bytes({"control": control, "extractions": headers}) <= 512
    payload = contract.model_payload("reconcile", full_context(), control, extractions=groups, passages=parts)
    # Header/control bytes are bounded separately; only fixed grouping syntax remains here.
    skeleton = contract.model_payload("reconcile", {}, {}, extractions=[{"result": {}}, {"result": {}}],
        passages=[{**part, "text": ""} for part in parts])
    # One more JSON layer expands any existing byte by at most two. A source
    # scalar expands by at most seven bytes through both layers (e.g. U+0001).
    upper_frame = (contract.native_request_bytes(request(skeleton)) +
        2 * 2 * contract.PHASES["extract"]["output_bytes"] + 2 * 4096 + 2 * 512 + 32 * 112 * 7)
    upper_input = (contract.serialized_bytes(skeleton) +
        2 * contract.PHASES["extract"]["output_bytes"] + 4096 + 512 + 32 * 112 * 6)
    return payload, upper_input, upper_frame


class StateBoundsTests(unittest.TestCase):
    def test_all_escaped_layers_have_a_formal_bound_within_unchanged_caps(self):
        self.assertEqual(contract.METHOD_CONFIG["passages"]["characters"], 112)
        self.assertEqual(contract.PHASES["extract"]["output_bytes"], 6144)
        payload, upper_input, upper_frame = composition()
        self.assertEqual(len(contract.allowed_passage_ids(payload)), 32)
        self.assertLessEqual(contract.serialized_bytes(payload), upper_input)
        self.assertLessEqual(contract.native_request_bytes(request(payload)), upper_frame)
        self.assertLessEqual(upper_input, 57344)
        self.assertLessEqual(upper_frame, 65536)
        self.assertTrue(contract.fits_input(payload, "reconcile"))

    def test_real_prepared_params_and_send_serializer_match_bound_without_a_turn(self):
        payload, _, upper = composition()
        with tempfile.TemporaryDirectory(dir="/private/tmp") as runtime:
            binary, marker = Path(runtime) / "fictional-native", Path(runtime) / "turn"
            program = test_state_method.PROGRAM.replace("MARKER", repr(str(marker)))
            program = program.replace("'id':'thread'", "'id':" + repr("\x01" * 128))
            import sys
            binary.write_text("#!" + sys.executable + "\n" + program)
            binary.chmod(0o700)
            provider = executor.RestrictedModel(str(binary), runtime, lambda: False)
            provider.client = inference._Client(str(binary), runtime, lambda: False,
                instructions=contract.STATE_INSTRUCTIONS)
            try:
                provider.prepare_call(payload, contract.SCHEMAS["reconcile"], contract.PHASES["reconcile"], 300)
                self.assertFalse(marker.exists())
                _, params, _ = provider.client.prepared_turn
                frame = {"id": 9007199254740991, "method": "turn/start", "params": copy.deepcopy(params)}
                self.assertEqual(frame, request(payload))
                provider._guard_request(frame)
                wire = io.StringIO()
                sender = object.__new__(inference._Client)
                sender.process = SimpleNamespace(stdin=wire)
                sender.send(frame)
                observed = len(wire.getvalue().encode()) + contract.serialized_bytes(contract.STATE_INSTRUCTIONS)
                self.assertEqual(observed, contract.native_request_bytes(frame))
                self.assertLessEqual(observed, upper)
                self.assertFalse(marker.exists())
            finally:
                provider.close()
