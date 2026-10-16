import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hoomy import state_executor as executor
from hoomy import state_rejection as archive
from hoomy.project_state import OUTPUT_SCHEMA
import test_state_executor as fixtures
EMPTY, METHOD = fixtures.EMPTY, fixtures.METHOD


def assertion():
    return {'key':'a','topic':'topic','text':'historical claim','kind':'record_fact','reported_state':'unknown',
        'limitations':[],'source_refs':[{'passage_id':'p0001_01'}]}


def extract_payload(parts, machine='machine-1'):
    return {'task':'extract','control':{'selection_partial':True,'required_machine_aliases':[machine]},
        'messages':[{'message_alias':'m0001','machine_aliases':[machine],'passages':parts}]}


def reconcile_payload(result, required=('machine-1',), shared=()):
    return {'task':'reconcile','control':{'selection_partial':True,'required_machine_aliases':list(required)},
        'extractions':[{'machine_alias':'machine-1','shared_passage_ids':list(shared),'result':copy.deepcopy(result)}],
        'passages':[{'passage_id':'p0001_01','text':'unchanged'}]}


class ValidationTests(unittest.TestCase):
    def detail(self, candidate, *, payload=None):
        try:
            executor.validate_output(candidate, OUTPUT_SCHEMA)
            if payload is not None:executor.validate_citations(candidate,payload)
        except executor.ValidationError as error:return error.detail
        self.fail('rejection expected')

    def test_valid_shape_selects_only_supplied_passage_ids(self):
        output={**EMPTY,'assertions':[assertion()]}
        executor.validate_output(output,OUTPUT_SCHEMA)
        executor.validate_citations(output,extract_payload([{'passage_id':'p0001_01','text':'an exact statement'}]))

    def test_schema_rules_point_to_known_fields_without_values(self):
        cases=[({**EMPTY,'private-token-secret':1},'unknown_property',['unknown_property']),
            ({**EMPTY,'assertions':'private'},'value_type',['assertions']),
            ({**EMPTY,'schema_version':True},'value_type',['schema_version']),
            ({'assertions':[],'uncertainties':[],'contradictions':[]},'required_property',['schema_version'])]
        for output,rule,pointer in cases:
            detail=self.detail(output)
            self.assertEqual(detail,{'substage':'schema','rule':rule,'pointer':pointer})
            self.assertNotIn('private',json.dumps(detail))
        output={**EMPTY,'assertions':[assertion()]};output['assertions'][0]['topic']='x'*201
        self.assertEqual(self.detail(output)['pointer'],['assertions',0,'topic'])
        output['assertions'][0]['topic']='ok';output['assertions'][0]['kind']='private-invalid-kind'
        self.assertEqual(self.detail(output)['rule'],'enum_value')

    def test_passage_and_duplicate_key_diagnostics_do_not_expose_values(self):
        payload=extract_payload([{'passage_id':'p0001_01','text':'exact'}])
        output={**EMPTY,'assertions':[assertion()]}
        output['assertions'][0]['source_refs'][0]['passage_id']='private-passage'
        detail=self.detail(output,payload=payload);self.assertEqual(detail['rule'],'passage_membership')
        self.assertEqual(detail['pointer'],['assertions',0,'source_refs',0,'passage_id'])
        self.assertNotIn('private',json.dumps(detail))
        output['assertions']=[assertion(),assertion()]
        self.assertEqual(self.detail(output,payload=payload)['rule'],'duplicate_assertion_key')

    def test_quote_alias_or_replacement_text_cannot_modify_registered_evidence(self):
        for ref in ({'message_alias':'m0001','quote':'invented'},
                {'passage_id':'p0001_01','quote':'changed'}, {'passage_id':'p0001_01','text':'changed'}):
            output={**EMPTY,'assertions':[assertion()]};output['assertions'][0]['source_refs']=[ref]
            self.assertIn(self.detail(output)['rule'],{'unknown_property','required_property'})

    def test_extract_cross_slot_and_reconciliation_unvalidated_ids_are_rejected(self):
        output={**EMPTY,'assertions':[assertion()]}
        payload={'task':'extract','messages':[{'passages':[{'passage_id':'p0002_01','text':'same text'}]}]}
        self.assertEqual(self.detail(output,payload=payload)['rule'],'passage_membership')
        executor.validate_citations(output,reconcile_payload(output))
        self.assertEqual(self.detail(output,payload=reconcile_payload(EMPTY))['rule'],'passage_membership')

    def test_whitespace_only_passage_is_retained_but_not_usable_as_evidence(self):
        output={**EMPTY,'assertions':[assertion()]}
        payload={'task':'extract','messages':[{'passages':[{'passage_id':'p0001_01','text':' \t\n'}]}]}
        self.assertEqual(self.detail(output,payload=payload)['rule'],'passage_membership')
        self.assertEqual(payload['messages'][0]['passages'][0]['text'],' \t\n')

    def test_unknown_passage_stops_once_and_preserves_known_usage_and_private_capture(self):
        remote,model,runner=fixtures.ExecutorTests().setup_runner()
        model.output={**EMPTY,'assertions':[assertion()]}
        model.output['assertions'][0]['source_refs'][0]['passage_id']='not-supplied'
        captured=[];runner.rejected_output=lambda *args:captured.append(args)
        result=runner.run()
        self.assertEqual(result['failure_code'],'invalid_output')
        self.assertEqual(result['validation_diagnostic']['rule'],'passage_membership')
        self.assertEqual(len(model.calls),1);self.assertEqual(len(captured),1)
        self.assertEqual(remote.tokens,15);self.assertNotIn('output',remote.actions[-1])

    def test_empty_output_is_structurally_valid_but_device_evidence_stops_once(self):
        executor.validate_output(EMPTY,OUTPUT_SCHEMA)
        remote,model,runner=fixtures.ExecutorTests().setup_runner();model.output=copy.deepcopy(EMPTY)
        captured=[];runner.rejected_output=lambda *args:captured.append(args)
        result=runner.run()
        self.assertEqual(result['failure_code'],'insufficient_device_evidence')
        self.assertEqual(result['status'],'needs_review')
        self.assertEqual(result['validation_diagnostic']['rule'],'required_machine_evidence')
        self.assertEqual(len(model.calls),1);self.assertEqual(remote.tokens,15)
        self.assertEqual(remote.finishes,[]);self.assertEqual(len(captured),1)
        self.assertEqual(remote.actions[-1]['code'],'insufficient_device_evidence')
        self.assertIn('method',remote.actions[-1]);self.assertNotIn('output',remote.actions[-1])

    def test_machine_coverage_is_derived_from_refs_not_claimed_in_text(self):
        output={**EMPTY,'assertions':[assertion()]}
        output['assertions'][0]['text']='Both machines are represented.'
        payload=extract_payload([{'passage_id':'p0001_01','text':'Known message'}],'machine-2')
        payload['control']['required_machine_aliases']=['machine-1']
        with self.assertRaises(executor.DeviceEvidenceError)as error:executor.validate_citations(output,payload)
        self.assertEqual(error.exception.code,'insufficient_device_evidence')
        self.assertEqual(error.exception.detail['pointer'],['source_refs'])
        with self.assertRaises(executor.DeviceEvidenceError):
            executor.validate_citations(output,reconcile_payload(output,required=('machine-1','machine-2')))

    def test_source_backed_uncertainty_and_shared_copy_are_not_invented_facts(self):
        output={**EMPTY,'uncertainties':[{'reason':'missing_current_observation','text':'Current work is unverified.',
            'source_refs':[{'passage_id':'p0001_01'}]}]}
        executor.validate_citations(output,extract_payload([{'passage_id':'p0001_01','text':'A historical statement.'}]))
        executor.validate_citations(output,reconcile_payload(output,required=('machine-1','machine-2'),shared=('p0001_01',)))

    def test_executor_failure_callback_is_optional_and_never_sends_output_to_fail(self):
        remote,model,runner=fixtures.ExecutorTests().setup_runner()
        model.output={**EMPTY,'private-extra-key':'not disclosed'}
        captured=[];runner.rejected_output=lambda *args:captured.append(args)
        result=runner.run()
        self.assertEqual(result['failure_code'],'invalid_output')
        self.assertEqual(result['validation_diagnostic']['rule'],'unknown_property')
        self.assertEqual(len(captured),1);self.assertEqual(captured[0][0],model.output)
        self.assertEqual(captured[0][1],fixtures.contract.SCHEMAS['extract'])
        self.assertNotIn('private',json.dumps(result))
        self.assertEqual(remote.tokens,15);self.assertNotIn('output',remote.actions[-1])

    def test_callback_failure_preserves_original_rule_and_actual_usage(self):
        remote,model,runner=fixtures.ExecutorTests().setup_runner();model.output={**EMPTY,'extra':1}
        def broken(*args):raise RuntimeError('private secret')
        runner.rejected_output=broken
        result=runner.run()
        self.assertEqual(result['validation_diagnostic']['artifact_capture'],'failed')
        self.assertEqual(result['validation_diagnostic']['rule'],'unknown_property')
        self.assertEqual(remote.tokens,15);self.assertNotIn('secret',json.dumps(result))

    def test_method_substage_and_bytecap_keep_existing_guards(self):
        remote,model,runner=fixtures.ExecutorTests().setup_runner();model.method['elapsed_ms']=True
        self.assertEqual(runner.run()['validation_diagnostic']['substage'],'method')
        remote,model,runner=fixtures.ExecutorTests().setup_runner()
        # Isolate the bytecap after otherwise valid shape/citation checks.
        with patch.object(executor,'validate_output'),patch.object(executor,'validate_citations'):
            model.output={'large':'x'*(512*1024)}
            result=runner.run()
        self.assertEqual(result['validation_diagnostic']['rule'],'output_byte_limit')
        self.assertEqual(remote.tokens,15)

    def test_bytecap_uses_actual_spaced_output_not_compact_diagnostic_encoding(self):
        remote,model,runner=fixtures.ExecutorTests().setup_runner()
        cap=fixtures.contract.PHASES['extract']['output_bytes']
        # Deliberately isolate quota accounting after shape and citation checks.
        model.output={'a':'x'*(cap-30),'b':0,'c':0,'d':0}
        self.assertLessEqual(len(executor.canonical(model.output)),cap)
        self.assertGreater(fixtures.contract.serialized_bytes(model.output),cap)
        with patch.object(executor,'validate_output'),patch.object(executor,'validate_citations'):
            result=runner.run()
        self.assertEqual(result['validation_diagnostic']['rule'],'output_byte_limit')
        self.assertEqual(remote.tokens,15);self.assertEqual(remote.finishes,[])

    def test_unencodable_and_deep_metadata_are_sanitized(self):
        result=archive.rejection_metadata({'value':'\ud800'},OUTPUT_SCHEMA)
        self.assertFalse(result['output']['encoding_valid'])
        self.assertIsNone(result['output']['sha256'])
        value=[]
        for _ in range(1100):value=[value]
        self.assertFalse(archive.rejection_metadata(value,OUTPUT_SCHEMA)['output']['encoding_valid'])


class ArchiveTests(unittest.TestCase):
    detail={'substage':'schema','rule':'unknown_property','pointer':['unknown_property']}

    def test_private_outputs_and_schema_only_never_overwrite(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            capture=archive.RejectedOutputArchive(directory)
            output={**EMPTY,'extra':'public generated output'}
            capture(output,OUTPUT_SCHEMA,self.detail);first=Path(capture.last_path)
            capture(output,OUTPUT_SCHEMA,self.detail);second=Path(capture.last_path)
            self.assertNotEqual(first,second);self.assertEqual(first.stat().st_mode&0o777,0o700)
            for path in first.iterdir():self.assertEqual(path.stat().st_mode&0o777,0o600)
            self.assertEqual(json.loads((first/'output.json').read_text()),output)
            self.assertEqual(json.loads((first/'schema.json').read_text()),OUTPUT_SCHEMA)
            manifest=json.loads((first/'manifest.json').read_text())
            self.assertFalse(manifest['output']['truncated'])
            self.assertEqual(manifest['output']['sha256'],hashlib.sha256(executor.canonical(output)).hexdigest())
            self.assertLessEqual(sum(p.stat().st_size for p in first.iterdir()),archive.CAP)
            self.assertEqual(set(manifest),{'format_version','output','schema','diagnostic'})

    def test_large_output_and_schema_have_explicit_truncation_size_and_hash(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            capture=archive.RejectedOutputArchive(directory)
            output={'text':'😀'*(archive.CAP//4)};schema={'known':'x'*100000}
            capture(output,schema,self.detail);path=Path(capture.last_path)
            manifest=json.loads((path/'manifest.json').read_text())
            self.assertTrue(manifest['output']['truncated']);self.assertTrue(manifest['schema']['truncated'])
            self.assertEqual(manifest['output']['bytes'],len(executor.canonical(output)))
            self.assertEqual(manifest['output']['sha256'],hashlib.sha256(executor.canonical(output)).hexdigest())
            self.assertLessEqual(sum(p.stat().st_size for p in path.iterdir()),archive.CAP)

    def test_symlink_leaf_ancestor_repository_and_nonprivate_directory_are_denied(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            root=Path(directory);(root/'link').symlink_to(root,target_is_directory=True)
            for path in (root/'link',root/'link'/'child'):
                with self.assertRaises(ValueError):archive.RejectedOutputArchive(path)
            public=root/'public';public.mkdir(mode=0o755)
            with self.assertRaises(ValueError):archive.RejectedOutputArchive(public)
            with self.assertRaises(ValueError):archive.RejectedOutputArchive(Path(executor.__file__).resolve().parents[2]/'captures')

    def test_partial_write_cleans_only_its_new_artifact_and_exclusive_flags(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            capture=archive.RejectedOutputArchive(directory);actual_open=os.open;writes=[]
            def fail_schema(path,flags,*args,**kwargs):
                if path in ('output.json','schema.json','manifest.json'):
                    writes.append(flags);self.assertTrue(flags&os.O_EXCL);self.assertTrue(flags&os.O_NOFOLLOW)
                if path=='schema.json':raise OSError('fictional private error')
                return actual_open(path,flags,*args,**kwargs)
            with patch.object(archive.os,'open',fail_schema):
                with self.assertRaises(OSError):capture(EMPTY,OUTPUT_SCHEMA,self.detail)
            self.assertEqual(list(Path(directory).iterdir()),[]);self.assertIsNone(capture.last_path)
            self.assertTrue(writes)

    def test_manifest_rejects_unknown_diagnostic_values_without_persisting_them(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            capture=archive.RejectedOutputArchive(directory)
            for bad in ({**self.detail,'rule':'private'}, {**self.detail,'pointer':['private-token']}):
                with self.assertRaises(ValueError):capture(EMPTY,OUTPUT_SCHEMA,bad)
            self.assertEqual(list(Path(directory).iterdir()),[])

    def test_replaced_base_or_ancestor_never_redirects_artifacts(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            root=Path(directory);base=root/'base';capture=archive.RejectedOutputArchive(base)
            moved=root/'moved';base.rename(moved);target=root/'target';target.mkdir(mode=0o700)
            base.symlink_to(target,target_is_directory=True)
            with self.assertRaises(ValueError):capture(EMPTY,OUTPUT_SCHEMA,self.detail)
            self.assertEqual(list(target.iterdir()),[]);self.assertEqual(list(moved.iterdir()),[])
            capture.close()
            parent=root/'parent';parent.mkdir(mode=0o700);base=parent/'base'
            capture=archive.RejectedOutputArchive(base);parent.rename(root/'renamed')
            parent.symlink_to(target,target_is_directory=True)
            with self.assertRaises(ValueError):capture(EMPTY,OUTPUT_SCHEMA,self.detail)
            self.assertEqual(list(target.iterdir()),[])


class HeartbeatTests(unittest.TestCase):
    def runner(self, behavior):
        remote,model,runner=fixtures.ExecutorTests().setup_runner(heartbeat_seconds=0.01)
        original=remote.worker
        def worker(args):
            if args['action']=='heartbeat':return behavior()
            return original(args)
        remote.worker=worker;model.delay=0.1
        return runner

    def test_opaque_failure_never_claims_confirmed_expiry(self):
        def opaque():raise executor.ExecutorError('lease_expired')
        result=self.runner(opaque).run()
        self.assertEqual(result['failure_code'],'ambiguous_completion')
        self.assertEqual(result['heartbeat_diagnostic']['outcome'],'uncertain')

    def test_server_stop_without_typed_expiry_remains_uncertain(self):
        result=self.runner(lambda:{'project_id':fixtures.PROJECT,'run_id':fixtures.RUN,'status':'needs_review','failure_code':None}).run()
        self.assertEqual(result['failure_code'],'ambiguous_completion')
        self.assertEqual(result['heartbeat_diagnostic']['outcome'],'server_stopped')

    def test_explicit_server_expiry_preserves_its_code(self):
        result=self.runner(lambda:{'project_id':fixtures.PROJECT,'run_id':fixtures.RUN,'status':'needs_review','failure_code':'lease_expired'}).run()
        self.assertEqual(result['failure_code'],'lease_expired')
        self.assertEqual(result['heartbeat_diagnostic']['outcome'],'server_stopped')

    def test_malformed_or_foreign_response_cannot_confirm_expiry(self):
        base={'project_id':fixtures.PROJECT,'run_id':fixtures.RUN,'status':'needs_review','failure_code':'lease_expired'}
        for response in ({**base,'status':'unknown'}, {**base,'status':None}, {**base,'run_id':'foreign'},
                {**base,'project_id':'foreign'}, {'failure_code':'lease_expired'}):
            result=self.runner(lambda:response).run()
            self.assertEqual(result['failure_code'],'ambiguous_completion')
            self.assertEqual(result['heartbeat_diagnostic']['outcome'],'uncertain')

    def test_explicit_server_device_scope_stop_preserves_closed_code(self):
        for code in ('unsupported_device_scope', 'insufficient_device_evidence'):
            result=self.runner(lambda:{'project_id':fixtures.PROJECT,'run_id':fixtures.RUN,'status':'needs_review','failure_code':code}).run()
            self.assertEqual(result['failure_code'],code)
            self.assertEqual(result['heartbeat_diagnostic']['outcome'],'server_stopped')
