"""Typed checkpoint omission preserves scope and never reconstructs cached context."""
import json
from pathlib import Path
import tempfile
import unittest

from hoomy_agent.collector import SourceError, read_source
from hoomy_agent.policy import approved_policy, sha256
from hoomy_agent.stream_json import StreamJSONError, proven_excluded

CANONICAL = '10000000-0000-0000-0000-000000000001'
PARENT = '10000000-0000-0000-0000-000000000002'
TELEMETRY = '20000000-0000-0000-0000-000000000001'
COUNTERS = {key: 1 for key in ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens', 'total_tokens')}


def checkpoint():
    return {'type': 'compacted', 'ordinal': None, 'payload': {
        'message': 'private derived summary', 'replacement_history': [{'type': 'message', 'role': 'user', 'content': 'private cached text'}],
        'latest_token_usage_record': {'turn_id': 'turn', 'response_id': 'response', 'root_turn_id': 'root',
            'session_id': TELEMETRY, 'thread_id': CANONICAL, 'usage': dict(COUNTERS),
            'turn_token_usage': dict(COUNTERS), 'thread_token_usage': dict(COUNTERS)}}}


def raw(record):
    return json.dumps(record, separators=(',', ':')).encode() + b'\n'


def validate(record, size=65536):
    encoded = raw(record)
    return proven_excluded((encoded[i:i + size] for i in range(0, len(encoded), size)), known_session_ids={CANONICAL})


class CompactionTests(unittest.TestCase):
    def test_internal_compaction_and_agent_message_variants_stay_private_and_typed(self):
        for variant in ('compaction', 'compaction_summary', 'agent_message'):
            item = {'type': variant, 'encrypted_content': 'private checkpoint'} if variant != 'agent_message' else {
                'type': variant, 'author': 'fixture-author', 'recipient': 'all', 'content': [
                    {'type': 'input_text', 'text': 'private agent text'}, {'type': 'encrypted_content', 'encrypted_content': 'private encrypted content'}]}
            record = checkpoint()
            record['payload']['replacement_history'] = [item]
            self.assertTrue(validate(record, 3))
            for changed in ({**item, 'unknown': None}, {**item, 'session_id': TELEMETRY},
                            {**item, 'encrypted_content': None} if variant != 'agent_message' else {**item, 'content': [{'type': 'input_text', 'text': None}]}):
                record['payload']['replacement_history'] = [changed]
                with self.assertRaises(StreamJSONError): validate(record)

    def test_typed_telemetry_id_is_distinct_and_field_order_chunking_do_not_change_scope(self):
        record = checkpoint()
        usage = record['payload']['latest_token_usage_record']
        record['payload']['latest_token_usage_record'] = dict(reversed(list(usage.items())))
        record['payload'] = dict(reversed(list(record['payload'].items())))
        for size in (1, 7, 64, 65536):
            self.assertTrue(validate(record, size))
        del record['payload']['latest_token_usage_record']['thread_id']
        self.assertTrue(validate(record))

    def test_unknown_fields_native_context_and_other_session_paths_fail_closed(self):
        for mutation in ('payload_unknown', 'usage_unknown', 'outside_thread', 'invalid_session', 'other_session', 'outside_cwd', 'native_context', 'resume_context', 'nested_native_content', 'reasoning_unknown_field'):
            record = checkpoint()
            payload, usage = record['payload'], record['payload']['latest_token_usage_record']
            if mutation == 'payload_unknown': payload['unknown'] = None
            elif mutation == 'usage_unknown': usage['unknown'] = None
            elif mutation == 'outside_thread': usage['thread_id'] = PARENT
            elif mutation == 'invalid_session': usage['session_id'] = 'not-a-uuid'
            elif mutation == 'other_session': payload['resume_metadata'] = {'session_id': TELEMETRY}
            elif mutation == 'outside_cwd': payload['replacement_history'][0]['content'] = {'cwd': '/outside'}
            elif mutation == 'native_context': payload['replacement_history'] = [{'type': 'session_meta', 'id': CANONICAL}]
            elif mutation == 'resume_context': payload['resume_metadata'] = {'nested': {'type': 'turn_context'}}
            elif mutation == 'nested_native_content': payload['replacement_history'][0]['content'] = [{'type': 'session_meta', 'id': PARENT}]
            elif mutation == 'reasoning_unknown_field': payload['replacement_history'] = [{'type': 'reasoning', 'namespace': 'unknown'}]
            with self.subTest(mutation=mutation), self.assertRaises(StreamJSONError):
                validate(record, 11)

    def test_counter_and_record_types_and_numeric_bounds_are_strict(self):
        for invalid in (None, True, '1', 1.5, 2**63, -(2**63) - 1):
            record = checkpoint()
            record['payload']['latest_token_usage_record']['usage']['input_tokens'] = invalid
            with self.subTest(value=invalid), self.assertRaises(StreamJSONError): validate(record)
        for invalid in ('history', {}, False):
            record = checkpoint()
            record['payload']['replacement_history'] = invalid
            with self.assertRaises(StreamJSONError): validate(record)
        for invalid in (42, None, [{'type': 'session_meta', 'id': PARENT}]):
            for field in ('replacement_history', 'guardian_history'):
                record = checkpoint()
                record['payload'][field] = [invalid]
                with self.subTest(field=field, value=invalid), self.assertRaises(StreamJSONError): validate(record)
        record = checkpoint()
        record['payload']['window_number'] = 2**64 - 1
        record['payload']['window_id'] = None
        self.assertTrue(validate(record))
        record['payload']['window_number'] = -1
        with self.assertRaises(StreamJSONError): validate(record)

    def test_duplicate_keys_tail_and_resource_bounds_are_validated_before_omission(self):
        encoded = raw(checkpoint())
        for changed in (encoded.replace(b'"session_id":', b'"session_id":"duplicate","session_id":'), encoded[:-1], encoded + b'garbage'):
            with self.assertRaises(StreamJSONError): proven_excluded([changed], {CANONICAL})
        record = checkpoint()
        value = 0
        for _ in range(40): value = [value]
        record['payload']['resume_metadata'] = {'nested': value}
        with self.assertRaisesRegex(StreamJSONError, 'oversized_parser_budget'): validate(record)
        tool = {'type': 'response_item', 'payload': {'type': 'custom_tool_call_output', 'output': 'hidden',
            'internal_chat_message_metadata_passthrough': {'session_id': TELEMETRY}}}
        with self.assertRaises(StreamJSONError): validate(tool)

    def test_lazy_source_uses_only_prior_declared_lineage_and_keeps_hash_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / '.codex/sessions'
            root.mkdir(parents=True)
            cwd = str(Path(directory).resolve() / 'project')
            policy = approved_policy({'content_tier': 'conversation_visible_v1', 'collection_enabled': True, 'roots': [str(root)],
                'exclusions': [], 'project_bindings': [{'project_id': 'fixture-project', 'cwd_roots': [cwd]}]})
            meta = {'type': 'session_meta', 'payload': {'id': CANONICAL, 'cwd': cwd, 'forked_from_id': PARENT}}
            compact = checkpoint()
            compact['payload']['message'] = 'private compaction text ' * 60000
            compact['payload']['replacement_history'] = [
                {'type': 'compaction', 'encrypted_content': 'private encrypted checkpoint'},
                {'type': 'agent_message', 'author': 'fixture-author', 'recipient': 'all',
                 'content': [{'type': 'input_text', 'text': 'private agent message'}]}]
            compact['payload']['latest_token_usage_record']['thread_id'] = PARENT
            message = {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': 'visible'}}
            path = root / 'rollout-fixture.jsonl'
            source = raw(meta) + raw(compact) + raw(message)
            path.write_bytes(source)
            result = read_source(path, root, policy)
            self.assertEqual([item['text'] for item in result[0]['messages']], ['visible'])
            self.assertEqual(result[0]['messages'][0]['line_start'], 3)
            self.assertEqual(result[2]['excluded_oversized_records'], 1)
            self.assertEqual(result[3], sha256(source))
            del meta['payload']['forked_from_id']
            later = {'type': 'session_meta', 'payload': {'id': CANONICAL, 'cwd': cwd, 'forked_from_id': PARENT}}
            path.write_bytes(raw(meta) + raw(compact) + raw(later) + raw(message))
            with self.assertRaises(SourceError): read_source(path, root, policy)
