"""Adversarial bounded JSON classification; no source histories or live APIs."""

import json
import random
import tracemalloc
import unittest

from hoomy_agent.stream_json import StreamJSONError, proven_excluded


def chunks(raw, size=65536):
    return (raw[offset:offset + size] for offset in range(0, len(raw), size))


class StreamJSONTests(unittest.TestCase):
    def test_rollout_ordinal_is_only_null_or_unsigned_64_bit_integer(self):
        prefix = b'{"type":"response_item","payload":{"type":"custom_tool_call_output","output":"discarded"},"ordinal":'
        for value in (b"null", b"0", b"18446744073709551615"):
            self.assertTrue(proven_excluded(chunks(prefix + value + b"}\n", 3)))
        for value in (b"true", b'"0"', b"-1", b"1.0", b"1e2", b"01", b"18446744073709551616", b"[]", b"{}"):
            with self.subTest(value=value), self.assertRaises(StreamJSONError):
                proven_excluded(chunks(prefix + value + b"}\n", 3))

    def test_custom_tool_output_passthrough_is_excluded_but_scope_and_unknown_fields_are_not(self):
        payload = {"type": "custom_tool_call_output", "call_id": "fixture-call", "output": "x" * (1024 * 1024 + 1),
                   "internal_chat_message_metadata_passthrough": {"turn_id": "fixture-turn"}}
        def validate(value):
            return proven_excluded(chunks(json.dumps({"type": "response_item", "payload": value}).encode() + b"\n"))
        self.assertTrue(validate(payload))
        for changed in (
            {**payload, "internal_chat_message_metadata_passthrough": {"cwd": "/outside"}},
            {**payload, "unknown_field": None},
            {**payload, "type": "function_call_output"},
        ):
            with self.assertRaisesRegex(StreamJSONError, "oversized_scope_unproven"):
                validate(changed)

    def test_field_order_escaped_keys_and_utf8_boundaries(self):
        raw = b'{"payload":{"output":"' + "Unicode 😀\n".encode().replace(b"\n", b"\\n") + b'","\\u0074ype":"function_call_output"},"\\u0074ype":"response_item"}\n'
        for size in (1, 2, 7, 64):
            self.assertTrue(proven_excluded(chunks(raw, size)))

    def test_scope_unknown_public_and_invalid_critical_types_are_refused(self):
        records = [
            {"type": "turn_context", "payload": {"type": "function_call_output", "cwd": "/other", "output": "hidden"}},
            {"type": "session_meta", "payload": {"type": "reasoning", "id": "other"}},
            {"type": "event_msg", "payload": {"type": "agent_message", "message": "private"}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant", "channel": "final", "content": "public"}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant", "channel": "commentary", "content": "commentary"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "channel": "analysis", "content": "user"}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant", "channel": "analysis", "recipient": False, "content": "analysis"}},
            {"type": "response_item", "payload": {"type": "function_call_output", "output": {"cwd": "/other"}}},
            {"type": "response_item", "payload": {"type": "unknown", "output": "hidden"}},
            {"type": "response_item", "payload": {"type": {"kind": "reasoning"}}},
        ]
        for record in records:
            with self.subTest(record_type=record["type"]), self.assertRaises(StreamJSONError):
                proven_excluded(chunks(json.dumps(record).encode() + b"\n", 7))

    def test_duplicate_decoded_keys_even_inside_ignored_output(self):
        for raw in (
            b'{"type":"response_item","type":"turn_context","payload":{"type":"reasoning"}}\n',
            b'{"type":"response_item","payload":{"type":"reasoning","\\u0074ype":"message"}}\n',
            b'{"type":"response_item","payload":{"type":"function_call_output","output":{"key":1,"\\u006bey":2}}}\n',
        ):
            with self.assertRaisesRegex(StreamJSONError, "oversized_duplicate_key"):
                proven_excluded(chunks(raw, 3))

    def test_complete_grammar_escapes_utf8_and_newline_required(self):
        prefix = b'{"type":"response_item","payload":{"type":"function_call_output","output":'
        for value in (b'01', b'1.', b'1e+', b'+1', b'NaN', b'[1,]', b'{"key":1,}',
                      b'"bad\\xescape"', b'"bad\x01control"', b'"\xff"', b'"\\ud800"'):
            with self.subTest(value=value), self.assertRaises(StreamJSONError):
                proven_excluded(chunks(prefix + value + b'}}\n', 5))
        valid = prefix + b'{"array":[true,false,null,-1.25e+3,"\\ud83d\\ude00"]}}}\n'
        self.assertTrue(proven_excluded(chunks(valid, 1)))
        for raw in (valid[:-1], valid + b"garbage", valid[:-5]):
            with self.assertRaises(StreamJSONError):
                proven_excluded(chunks(raw, 11))

    def test_depth_key_count_key_length_and_retained_memory_are_bounded(self):
        prefix = b'{"type":"response_item","payload":{"type":"function_call_output","output":'
        for value in (b"[" * 40 + b"0" + b"]" * 40,
                      json.dumps({str(index): 0 for index in range(257)}).encode(),
                      json.dumps({"k" * 1025: 0}).encode()):
            with self.assertRaisesRegex(StreamJSONError, "oversized_parser_budget"):
                proven_excluded(chunks(prefix + value + b"}}\n"))
        # Four nested dictionaries retain their live duplicate-key sets simultaneously.
        keys = b",".join(json.dumps("k" * 990 + str(index)).encode() + b":0" for index in range(255))
        nested = b"0"
        for _ in range(5):
            nested = b"{" + keys + b',"nested":' + nested + b"}"
        with self.assertRaisesRegex(StreamJSONError, "oversized_parser_budget"):
            proven_excluded(chunks(prefix + nested + b"}}\n"))

    def test_large_output_is_validated_without_retaining_body(self):
        block = b"x" * 65536
        def stream():
            yield b'{"payload":{"output":"'
            for _ in range(256):
                yield block
            yield b'","type":"function_call_output"},"type":"response_item"}\n'
        tracemalloc.start()
        try:
            self.assertTrue(proven_excluded(stream()))
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 2 * 1024 * 1024)

    def test_generated_valid_json_shapes_agree_across_chunk_boundaries(self):
        randomizer = random.Random(20261012)
        def value(depth=0):
            choices = [None, True, False, -12345, 1.25e-7, "Unicode 😀 slash\\ quote\" controls\n\t\x00"]
            if depth < 4:
                choices += [[value(depth + 1) for _ in range(randomizer.randrange(4))],
                            {"field" + str(index): value(depth + 1) for index in range(randomizer.randrange(4))}]
            return randomizer.choice(choices)
        for _ in range(100):
            record = {"payload": {"output": value(), "type": "function_call_output"}, "type": "response_item"}
            raw = json.dumps(record, ensure_ascii=bool(randomizer.randrange(2)), separators=(",", ":"), allow_nan=False).encode() + b"\n"
            self.assertTrue(proven_excluded(chunks(raw, randomizer.choice((1, 3, 13, 64)))))


if __name__ == "__main__":
    unittest.main()
