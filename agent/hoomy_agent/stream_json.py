"""Bounded strict JSON validation for proving oversized records are excluded."""

import codecs
import re

from .client import AgentError

MAX_DEPTH = 32
MAX_KEYS = 256
MAX_KEY_BYTES = 1024
MAX_RETAINED_BYTES = 1024 * 1024
MAX_NODES = 250000
SPECIAL = re.compile(r'["\\\x00-\x1f]')
CRITICAL = {("type",), ("payload", "type"), ("payload", "role"), ("payload", "channel"), ("payload", "recipient")}
SCOPE_KEYS = {"cwd", "session_id", "session_meta", "turn_context", "working_directory", "workdir"}
TOOL_FIELDS = {
    "function_call": {"type", "name", "arguments", "call_id", "namespace", "id", "status"},
    "function_call_output": {"type", "call_id", "output", "id", "status"},
    "custom_tool_call": {"type", "name", "input", "call_id", "namespace", "id", "status"},
    "custom_tool_call_output": {"type", "call_id", "output", "id", "status", "internal_chat_message_metadata_passthrough"},
    "reasoning": {"type", "summary", "content", "encrypted_content", "id", "status"},
}
COMPACT_FIELDS = {"message", "replacement_history", "replacement_history_metadata", "guardian_history", "retained_context",
                  "mcp_resource_origins", "window_number", "first_window_id", "previous_window_id", "window_id",
                  "compaction_response_id", "latest_token_usage_record", "resume_metadata"}
USAGE_PATH = ("payload", "latest_token_usage_record")
USAGE_FIELDS = {"thread_id", "turn_id", "session_id", "root_turn_id", "response_id", "usage", "turn_token_usage", "thread_token_usage"}
TOKEN_FIELDS = {"input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens"}
RESPONSE_FIELDS = {**TOOL_FIELDS,
    "message": {"type", "role", "channel", "phase", "content", "id", "status", "end_turn", "recipient", "encrypted_content", "internal_chat_message_metadata_passthrough"},
    "local_shell_call": {"type", "id", "call_id", "status", "action", "encrypted_content", "internal_chat_message_metadata_passthrough"},
    "compaction": {"type", "id", "encrypted_content", "internal_chat_message_metadata_passthrough"},
    "compaction_summary": {"type", "id", "encrypted_content", "internal_chat_message_metadata_passthrough"},
    "agent_message": {"type", "id", "author", "recipient", "content", "internal_chat_message_metadata_passthrough"}}
for _kind in ("function_call", "function_call_output", "custom_tool_call", "reasoning"):
    RESPONSE_FIELDS[_kind] = RESPONSE_FIELDS[_kind] | {"internal_chat_message_metadata_passthrough"}
for _kind in ("function_call", "custom_tool_call"):
    RESPONSE_FIELDS[_kind] |= {"encrypted_content"}
RESPONSE_FIELDS["function_call_output"] |= {"name", "namespace"}
UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


class StreamJSONError(AgentError):
    def __init__(self, category):
        self.category = category
        super().__init__("Oversized record refused (" + category + ").")


class Reader:
    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self.buffer, self.position = "", 0
        self.ended, self.last_byte = False, None

    def peek(self):
        while self.position >= len(self.buffer) and not self.ended:
            try:
                chunk = next(self.chunks)
            except StopIteration:
                self.ended = True
                chunk = b""
            if chunk:
                self.last_byte = chunk[-1]
            try:
                self.buffer = self.decoder.decode(chunk, final=self.ended)
            except UnicodeError:
                raise StreamJSONError("oversized_invalid_utf8") from None
            self.position = 0
        return self.buffer[self.position] if self.position < len(self.buffer) else None

    def take(self):
        value = self.peek()
        if value is None:
            raise StreamJSONError("oversized_incomplete_json")
        self.position += 1
        return value


class Validator:
    def __init__(self, chunks, known_session_ids=None):
        self.reader = Reader(chunks)
        self.retained, self.nodes = 0, 0
        self.critical, self.fields = {}, {}
        self.scope_present = False
        self.invalid_scope = False
        self.known_session_ids = frozenset(known_session_ids or ())
        self.payload_types, self.usage_types, self.usage_identity = {}, {}, {}
        self.response_types = {}
        self.response_shapes, self.content_types, self.content_shapes, self.content_safe = {}, {}, {}, {}

    def whitespace(self):
        while self.reader.peek() in (" ", "\t", "\r", "\n"):
            self.reader.take()

    def expect(self, value):
        if self.reader.take() != value:
            raise StreamJSONError("oversized_invalid_json")

    def string(self, limit=None):
        self.expect('"')
        pieces, length = [], 0
        def append(value):
            nonlocal length
            if limit is None:
                return
            length += len(value.encode("utf-8"))
            if length > limit or self.retained + length > MAX_RETAINED_BYTES:
                raise StreamJSONError("oversized_parser_budget")
            pieces.append(value)
        while True:
            if self.reader.peek() is None:
                raise StreamJSONError("oversized_incomplete_json")
            match = SPECIAL.search(self.reader.buffer, self.reader.position)
            end = match.start() if match else len(self.reader.buffer)
            append(self.reader.buffer[self.reader.position:end])
            self.reader.position = end
            if match is None:
                continue
            char = self.reader.take()
            if char == '"':
                return "".join(pieces) if limit is not None else None
            if char != "\\":
                raise StreamJSONError("oversized_invalid_json")
            escape = self.reader.take()
            simple = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
            if escape in simple:
                append(simple[escape])
                continue
            if escape != "u":
                raise StreamJSONError("oversized_invalid_json")
            digits = "".join(self.reader.take() for _ in range(4))
            if not re.fullmatch(r"[0-9a-fA-F]{4}", digits):
                raise StreamJSONError("oversized_invalid_json")
            code = int(digits, 16)
            if 0xD800 <= code <= 0xDBFF:
                self.expect("\\")
                self.expect("u")
                low_digits = "".join(self.reader.take() for _ in range(4))
                if not re.fullmatch(r"[0-9a-fA-F]{4}", low_digits):
                    raise StreamJSONError("oversized_invalid_json")
                low = int(low_digits, 16)
                if not 0xDC00 <= low <= 0xDFFF:
                    raise StreamJSONError("oversized_invalid_json")
                code = 0x10000 + ((code - 0xD800) << 10) + low - 0xDC00
            elif 0xDC00 <= code <= 0xDFFF:
                raise StreamJSONError("oversized_invalid_json")
            append(chr(code))

    def number(self):
        if self.reader.peek() == "-":
            self.reader.take()
        char = self.reader.peek()
        if char == "0":
            self.reader.take()
        elif char is not None and "1" <= char <= "9":
            while self.reader.peek() is not None and "0" <= self.reader.peek() <= "9":
                self.reader.take()
        else:
            raise StreamJSONError("oversized_invalid_json")
        if self.reader.peek() == ".":
            self.reader.take()
            self.digits()
        if self.reader.peek() in ("e", "E"):
            self.reader.take()
            if self.reader.peek() in ("+", "-"):
                self.reader.take()
            self.digits()

    def digits(self):
        if self.reader.peek() is None or not "0" <= self.reader.peek() <= "9":
            raise StreamJSONError("oversized_invalid_json")
        while self.reader.peek() is not None and "0" <= self.reader.peek() <= "9":
            self.reader.take()

    def integer(self, signed=False):
        negative = self.reader.peek() == "-"
        if negative and not signed:
            raise StreamJSONError("oversized_compaction_shape")
        if negative:
            self.reader.take()
        digits = []
        while self.reader.peek() is not None and "0" <= self.reader.peek() <= "9":
            digits.append(self.reader.take())
            if len(digits) > 20:
                raise StreamJSONError("oversized_compaction_shape")
        value = "".join(digits)
        if not value or len(value) > 1 and value[0] == "0" or self.reader.peek() in (".", "e", "E"):
            raise StreamJSONError("oversized_compaction_shape")
        number = int(("-" if negative else "") + value)
        if not (-(2**63) <= number <= 2**63 - 1 if signed else 0 <= number <= 2**64 - 1):
            raise StreamJSONError("oversized_compaction_shape")

    def value(self, path=(), depth=0):
        self.nodes += 1
        if depth > MAX_DEPTH or self.nodes > MAX_NODES:
            raise StreamJSONError("oversized_parser_budget")
        self.whitespace()
        char = self.reader.peek()
        kind = {"{": "object", "[": "array", '"': "string", "n": "null", "t": "bool", "f": "bool"}.get(char, "number")
        response_item = len(path) == 3 and path[:1] == ("payload",) and path[1] in ("replacement_history", "guardian_history") and path[2] == "[]"
        if response_item and kind != "object":
            raise StreamJSONError("oversized_compaction_shape")
        response_type = len(path) == 4 and path[:1] == ("payload",) and path[1] in ("replacement_history", "guardian_history") and path[2:] == ("[]", "type")
        response_field = len(path) == 4 and path[:1] == ("payload",) and path[1] in ("replacement_history", "guardian_history") and path[2] == "[]"
        content_item = len(path) == 5 and path[:1] == ("payload",) and path[1] in ("replacement_history", "guardian_history") and path[2:] == ("[]", "content", "[]")
        content_field = len(path) == 6 and path[:1] == ("payload",) and path[1] in ("replacement_history", "guardian_history") and path[2:5] == ("[]", "content", "[]")
        content_type = content_field and path[-1] == "type"
        if content_item and kind != "object":
            self.content_safe[path[:3]] = False
        if response_field:
            self.response_shapes.setdefault(path[:3], {})[path[-1]] = kind
        if content_field:
            self.content_shapes.setdefault(path[:5], {})[path[-1]] = kind
        usage_identity = path in (USAGE_PATH + ("session_id",), USAGE_PATH + ("thread_id",))
        token_counter = len(path) == 4 and path[:2] == USAGE_PATH and path[2] in ("usage", "turn_token_usage", "thread_token_usage")
        if len(path) == 2 and path[:1] == ("payload",):
            self.payload_types[path[-1]] = kind
        if len(path) == 3 and path[:2] == USAGE_PATH:
            self.usage_types[path[-1]] = kind
        if token_counter or path in (("payload", "window_number"), ("payload", "window_id")) and kind == "number":
            if kind != "number":
                raise StreamJSONError("oversized_compaction_shape")
            self.integer(signed=token_counter)
            if not token_counter:
                self.payload_types[path[-1]] = "integer"
            return
        if path == ("ordinal",):
            if char == "n":
                for expected in "null":
                    self.expect(expected)
            elif char is not None and "0" <= char <= "9":
                digits = []
                while self.reader.peek() is not None and "0" <= self.reader.peek() <= "9":
                    digits.append(self.reader.take())
                    if len(digits) > 20:
                        raise StreamJSONError("oversized_ordinal_type")
                value = "".join(digits)
                if (len(value) > 1 and value[0] == "0") or int(value) > 2**64 - 1:
                    raise StreamJSONError("oversized_ordinal_type")
            else:
                raise StreamJSONError("oversized_ordinal_type")
            return
        native_context_type = path[-1:] == ("type",) and path[:2] in (("payload", "retained_context"), ("payload", "resume_metadata"), ("payload", "replacement_history_metadata"))
        captured = path in CRITICAL or usage_identity or response_type or native_context_type or content_type
        scalar = None
        if char == "{":
            self.reader.take()
            keys, key_bytes = set(), 0
            if self.reader.peek() != "}":
                self.whitespace()
                if self.reader.peek() != "}":
                    while True:
                        self.whitespace()
                        key = self.string(MAX_KEY_BYTES)
                        if key in keys:
                            raise StreamJSONError("oversized_duplicate_key")
                        if len(keys) >= MAX_KEYS:
                            raise StreamJSONError("oversized_parser_budget")
                        size = len(key.encode("utf-8"))
                        self.retained += size
                        key_bytes += size
                        if self.retained > MAX_RETAINED_BYTES:
                            raise StreamJSONError("oversized_parser_budget")
                        keys.add(key)
                        if key in SCOPE_KEYS:
                            self.scope_present = True
                            if path + (key,) != USAGE_PATH + ("session_id",):
                                self.invalid_scope = True
                        self.whitespace()
                        self.expect(":")
                        self.value(path + (key,), depth + 1)
                        self.whitespace()
                        if self.reader.peek() != ",":
                            break
                        self.reader.take()
            self.expect("}")
            if path == USAGE_PATH and not keys <= USAGE_FIELDS:
                raise StreamJSONError("oversized_compaction_shape")
            if len(path) == 3 and path[:2] == USAGE_PATH and path[-1] in ("usage", "turn_token_usage", "thread_token_usage"):
                if not keys <= TOKEN_FIELDS or not TOKEN_FIELDS - {"cache_write_input_tokens"} <= keys:
                    raise StreamJSONError("oversized_compaction_shape")
            if response_item:
                response_kind = self.response_types.pop(path, None)
                if response_kind not in RESPONSE_FIELDS or not keys <= RESPONSE_FIELDS[response_kind]:
                    raise StreamJSONError("oversized_compaction_shape")
                shapes = self.response_shapes.pop(path, {})
                content_safe = self.content_safe.pop(path, True)
                if response_kind in ("compaction", "compaction_summary", "agent_message"):
                    required = {"encrypted_content": "string"} if response_kind != "agent_message" else {"author": "string", "recipient": "string", "content": "array"}
                    if (any(shapes.get(key) != value for key, value in required.items())
                            or "id" in shapes and shapes["id"] not in ("string", "null")
                            or "internal_chat_message_metadata_passthrough" in shapes and shapes["internal_chat_message_metadata_passthrough"] not in ("object", "null")
                            or response_kind == "agent_message" and not content_safe):
                        raise StreamJSONError("oversized_compaction_shape")
            if content_item:
                content_kind = self.content_types.pop(path, None)
                shapes = self.content_shapes.pop(path, {})
                allowed = {"type", "text"} if content_kind == "input_text" else {"type", "encrypted_content"} if content_kind == "encrypted_content" else set()
                if not allowed or keys != allowed or any(shapes.get(key) != "string" for key in allowed):
                    self.content_safe[path[:3]] = False
            if path in ((), ("payload",)):
                self.fields[path] = keys
            else:
                self.retained -= key_bytes
        elif char == "[":
            self.reader.take()
            self.whitespace()
            if self.reader.peek() != "]":
                while True:
                    self.value(path + ("[]",), depth + 1)
                    self.whitespace()
                    if self.reader.peek() != ",":
                        break
                    self.reader.take()
            self.expect("]")
        elif char == '"':
            scalar = self.string(128 if usage_identity else 64 if captured else None)
            if captured:
                self.retained += len(scalar.encode("utf-8"))
        elif char in ("t", "f", "n"):
            word = {"t": "true", "f": "false", "n": "null"}[char]
            for expected in word:
                self.expect(expected)
        elif char == "-" or char is not None and "0" <= char <= "9":
            self.number()
        else:
            raise StreamJSONError("oversized_invalid_json")
        if captured:
            # Non-string critical values can never prove an excluded record type.
            if not isinstance(scalar, str):
                raise StreamJSONError("oversized_critical_type")
            if usage_identity:
                self.usage_identity[path[-1]] = scalar
            elif response_type:
                self.response_types[path[:-1]] = scalar
            elif content_type:
                if scalar in ("session_meta", "turn_context"):
                    self.invalid_scope = True
                self.content_types[path[:-1]] = scalar
            elif native_context_type:
                if scalar in ("session_meta", "turn_context"):
                    self.invalid_scope = True
            else:
                self.critical[path] = scalar

    def excluded(self):
        self.value()
        self.whitespace()
        if self.reader.peek() is not None or self.reader.last_byte != 10:
            raise StreamJSONError("oversized_invalid_json")
        top, payload = self.fields.get((), set()), self.fields.get(("payload",), set())
        kind = self.critical.get(("payload", "type"))
        if self.critical.get(("type",)) == "compacted":
            return self.compacted(top, payload)
        if (self.scope_present or self.critical.get(("type",)) != "response_item"
                or not top <= {"type", "timestamp", "payload", "ordinal"} or not {"type", "payload"} <= top):
            raise StreamJSONError("oversized_scope_unproven")
        if kind in TOOL_FIELDS and payload <= TOOL_FIELDS[kind]:
            return True
        if (kind == "message" and self.critical.get(("payload", "role")) == "assistant"
                and self.critical.get(("payload", "channel")) == "analysis"
                and self.critical.get(("payload", "recipient")) in (None, "all")
                and payload <= {"type", "role", "channel", "content", "id", "status", "end_turn", "recipient"}):
            return True
        raise StreamJSONError("oversized_scope_unproven")

    def compacted(self, top, payload):
        if (not self.known_session_ids or self.invalid_scope or not top <= {"type", "timestamp", "payload", "ordinal"}
                or not {"type", "payload"} <= top or not payload <= COMPACT_FIELDS or self.payload_types.get("message") != "string"):
            raise StreamJSONError("oversized_scope_unproven")
        shapes = {"replacement_history": {"array", "null"}, "replacement_history_metadata": {"array", "null"},
            "guardian_history": {"array", "null"}, "retained_context": {"object", "null"}, "mcp_resource_origins": {"object", "null"},
            "window_number": {"integer", "null"}, "window_id": {"string", "integer", "null"},
            "first_window_id": {"string", "null"}, "previous_window_id": {"string", "null"}, "compaction_response_id": {"string", "null"},
            "latest_token_usage_record": {"object", "null"}, "resume_metadata": {"object", "null"}}
        if any(self.payload_types[key] not in allowed for key, allowed in shapes.items() if key in payload):
            raise StreamJSONError("oversized_compaction_shape")
        if self.payload_types.get("latest_token_usage_record") == "object":
            if (not USAGE_FIELDS - {"thread_id"} <= self.usage_types.keys()
                    or any(self.usage_types[key] != "string" for key in ("turn_id", "session_id", "root_turn_id", "response_id"))
                    or any(self.usage_types[key] != "object" for key in ("usage", "turn_token_usage", "thread_token_usage"))
                    or not UUID.fullmatch(self.usage_identity.get("session_id", ""))
                    or "thread_id" in self.usage_types and self.usage_identity.get("thread_id") not in self.known_session_ids):
                raise StreamJSONError("oversized_scope_unproven")
        return True


def proven_excluded(chunks, known_session_ids=None):
    return Validator(chunks, known_session_ids).excluded()
