"""Versioned state uses only eligible cache inputs and preserves approved intent."""

import copy
import json
import unittest
import uuid
from unittest.mock import patch

import test_ingestion as ingestion_fixtures
from hoomy.ingestion import canonical, digest
from hoomy.project_state import CHUNK_BYTES, ProjectState
from hoomy.project_state_contract import METHOD, METHOD_HASH, METHOD_CONFIG, PHASES, fits_input, serialized_bytes, envelope_bytes, SCHEMAS, allowed_passage_ids, model_payload, serialized, STATE_INSTRUCTIONS, native_request_bytes
from hoomy.store import Store, StoreError


class ProjectStateTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ingestion_fixtures.IngestionTests()
        self.fixture.setUp()
        self.store = self.fixture.store
        self.pid = self.fixture.pid
        self.scope = {"principal": {"account_id": "github:42", "workspace_id": "personal:42"}, "project_id": self.pid}
        self.store.project_state.enabled_project_id = self.pid
        self.store.ingestion.accept(self.fixture.payload())
        self.store.ingestion.accept(self.fixture.payload(device="device-b"))

    def tearDown(self):
        self.fixture.fixture.store = self.store
        self.fixture.tearDown()

    def refresh(self):
        current = self.store.project_state.read(self.scope)
        return self.store.project_state.refresh({**self.scope, "request_id": str(uuid.uuid4()),
            "context_revision": current["context_revision"], "corpus_revision": current["corpus_revision"], "method_hash": current["method_hash"]})

    def claim(self):
        run = self.refresh()
        return self.store.project_state.claim({**self.scope, "run_id": run["run_id"], "worker_id": str(uuid.uuid4())})

    def base(self, control):
        return {**self.scope, **{key: control[key] for key in ("run_id", "lease_id", "input_hash")}}

    def attempt(self, control):
        return self.store.project_state.attempt({**self.base(control), "call_id": str(uuid.uuid4()), **control["next"]})

    def output(self, attempt):
        if attempt["phase"] == "reconcile":
            result={"schema_version":1,"assertions":[],"uncertainties":[],"contradictions":[]}
            for index,group in enumerate(attempt["input"]["extractions"]):
                output=copy.deepcopy(group["result"])
                for item in output["assertions"]:
                    if index:item["key"]=item["key"][:39]+str(index)
                for section in ("assertions","uncertainties"):result[section].extend(output[section])
            return result
        message = attempt["input"]["messages"][0]
        return {"schema_version": 1, "assertions": [{"key": "a1", "topic": "Project report",
            "text": "A participant recorded this report.", "kind": "reported_status", "reported_state": "unknown",
            "limitations": ["Historical report only."], "source_refs": [{"passage_id": message["passages"][0]["passage_id"]}]}], "uncertainties": [], "contradictions": []}

    def finish_payload(self, control, attempt, output=None, tokens=30):
        return {**self.base(control), "call_id": attempt["call_id"], "output": output or self.output(attempt),
            "method": {"adapter": "codex-native-v1", "model": "gpt-6.1-sol", "reasoning_effort": PHASES[attempt["phase"]]["effort"], "elapsed_ms": 1,
                       "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": tokens}}}

    def publish(self):
        control = self.claim()
        while control["next"]:
            attempt = self.attempt(control)
            control = self.store.project_state.finish(self.finish_payload(control, attempt))
        self.assertEqual(control["status"], "ready")
        return control, self.store.project_state.read(self.scope)

    def test_reads_and_explicit_allowlist_are_independent_from_collection_and_context(self):
        before = self.store.dialogue.profile(self.pid)
        self.store.project_state.enabled_project_id = ""
        with (patch.object(self.store, "state", side_effect=AssertionError("No bulk legacy state")),
                patch.object(self.store.dialogue, "profile", side_effect=AssertionError("No legacy profile join"))):
            result = self.store.project_state.read(self.scope)
        self.assertFalse(result["enabled"])
        self.assertEqual(result["context_revision"], before["revision"])
        self.assertIsNone(result["state"])
        self.assertIsNone(result["job"])
        with self.assertRaises(StoreError):
            self.refresh()
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM project_state_runs").fetchone()[0], 0)
        self.assertEqual(self.store.dialogue.profile(self.pid)["fields"], before["fields"])

    def test_v2_plan_precedes_journal_and_heartbeat_stays_metadata_only(self):
        current = self.store.project_state.read(self.scope)
        self.assertEqual(current["method_version"],METHOD)
        self.assertEqual(digest(current["execution_policy"]),current["method_hash"])
        control = self.claim()
        plan = control["next_plan"]
        self.assertEqual(plan["method_hash"],METHOD_HASH)
        self.assertEqual(plan["output_schema"],SCHEMAS["extract"])
        self.assertTrue(fits_input(plan["input"],"extract"))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM project_state_calls").fetchone()[0],0)
        beat = self.store.project_state.heartbeat(self.base(control))
        self.assertNotIn("next_plan",beat)
        self.assertNotIn("input",beat)
        self.assertNotIn("provided_context",beat)
        self.assertNotIn("next_plan",self.store.project_state.job(self.scope)["pending_run"] or {})
        attempt = self.attempt(control)
        for key in plan:self.assertEqual(attempt[key],plan[key])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM project_state_calls").fetchone()[0],1)
        with self.assertRaises(StoreError):self.store.project_state.attempt({**self.base(control),"call_id":attempt["call_id"],**control["next"]})

    def test_method_expectation_and_legacy_run_are_fail_closed_without_rewriting_history(self):
        control = self.claim()
        self.attempt(control)
        self.store.project_state.fail({**self.base(control),"code":"ambiguous_completion"})
        with self.store.db:
            row = self.store.db.execute("SELECT manifest FROM project_state_runs WHERE id=?",(control["run_id"],)).fetchone()
            old = json.loads(row[0]);old["method"]="project-state-v1";old.pop("method_hash");old.pop("method_config")
            self.store.db.execute("UPDATE project_state_runs SET manifest=? WHERE id=?",(json.dumps(old),control["run_id"]))
        rows = {table:[tuple(row) for row in self.store.db.execute('SELECT * FROM '+table+' ORDER BY rowid')]
            for table in ("project_state_runs","project_state_inputs","project_state_calls")}
        legacy = self.store.project_state.job({**self.scope,"run_id":control["run_id"]})
        self.assertEqual(legacy["method_version"],"project-state-v1")
        self.assertIsNone(legacy["method_hash"])
        self.assertFalse(legacy["execution_compatible"])
        with self.assertRaises(StoreError) as caught:self.store.project_state.refresh(self.retry_request(control["run_id"]))
        self.assertIn("method changed",str(caught.exception))
        with self.assertRaises(StoreError):self.store.project_state.claim({**self.scope,"run_id":control["run_id"],"worker_id":str(uuid.uuid4())})
        with self.assertRaises(StoreError):self.attempt(control)
        request = self.retry_request(control["run_id"]);request.pop("retry_of_run_id")
        for value in (None,"0"*64):
            body = dict(request)
            if value is None:body.pop("method_hash")
            else:body["method_hash"]=value
            with self.assertRaises(StoreError) as caught:self.store.project_state.refresh(body)
            self.assertEqual(caught.exception.status,409)
        self.assertEqual({table:[tuple(row) for row in self.store.db.execute('SELECT * FROM '+table+' ORDER BY rowid')] for table in rows},rows)
        self.store.close()
        self.store=self.fixture.store=Store(self.fixture.fixture.directory.name)
        self.store.project_state.enabled_project_id=self.pid
        self.assertEqual({table:[tuple(row) for row in self.store.db.execute('SELECT * FROM '+table+' ORDER BY rowid')] for table in rows},rows)
        self.assertFalse(self.store.project_state.job({**self.scope,"run_id":control["run_id"]})["execution_compatible"])

    def test_passages_keep_original_prefix_and_offsets_but_blank_evidence_cannot_publish(self):
        text='X'+' '*223+'A dated statement. 😀'
        self.store.ingestion.accept(self.fixture.payload(sequence=2,text=text))
        control=self.claim();attempt=self.attempt(control)
        message=next(m for m in attempt['input']['messages'] if m['passages'][0]['text'].startswith('X'))
        self.assertNotIn('text',message)
        self.assertEqual(''.join(p['text'] for p in message['passages']),text)
        blank=message['passages'][1]
        self.assertEqual(blank['text'],' '*112)
        self.assertNotIn(blank['passage_id'],allowed_passage_ids(attempt['input']))
        records=json.loads(self.store.db.execute('SELECT data FROM project_state_inputs WHERE run_id=?',(control['run_id'],)).fetchone()[0])
        last=message['passages'][2]
        self.assertEqual(records['passages'][last['passage_id']],{'message_alias':message['message_alias'],'quote':text[224:],'char_start':224,'char_end':len(text)})
        output=self.output(attempt);output['assertions'][0]['source_refs']=[{'passage_id':blank['passage_id']}]
        result=self.store.project_state.finish(self.finish_payload(control,attempt,output))
        self.assertEqual((result['status'],result['observed_tokens']),('failed',30))
        self.assertIsNone(self.store.project_state.read(self.scope)['state'])

    def test_reconcile_receives_each_referenced_original_passage_once_and_derives_immutable_citation(self):
        text='X'+' '*223+'A dated statement. 😀'
        self.store.ingestion.accept(self.fixture.payload(sequence=2,text=text))
        before=self.store.dialogue.profile(self.pid)
        self.store.ingestion.accept(self.fixture.payload(device="device-b",sequence=2,text=text))
        control=self.claim();attempt=self.attempt(control)
        message=next(m for m in attempt['input']['messages'] if m['passages'][0]['text'].startswith('X'))
        selected=message['passages'][2]
        output=self.output(attempt);output['assertions'][0]['source_refs']=[{'passage_id':selected['passage_id']},{'passage_id':selected['passage_id']}]
        control=self.store.project_state.finish(self.finish_payload(control,attempt,output))
        reconcile=self.attempt(control)
        self.assertEqual(reconcile['input']['passages'],[selected])
        self.assertEqual(allowed_passage_ids(reconcile['input']),{selected['passage_id']})
        control=self.store.project_state.finish(self.finish_payload(control,reconcile))
        state=self.store.project_state.read(self.scope)['state']
        cid=state['assertions'][0]['citation_ids'][0]
        citation=self.store.project_state.citation({**self.scope,'run_id':control['run_id'],'citation_id':cid})
        self.assertEqual((citation['quote'],citation['char_start'],citation['char_end']),(text[224:],224,len(text)))
        self.assertEqual(citation['passage_id'],selected['passage_id'])
        self.assertEqual(self.store.dialogue.profile(self.pid)['fields'],before['fields'])

    def test_v3_reconcile_complete_bound_accounts_for_all_escaping_and_maximum_wrapper(self):
        empty_passages=[{'passage_id':f'p{i+1:04d}_01','text':''}for i in range(32)]
        framing=serialized_bytes(serialized(model_payload('reconcile',{}, {},extractions=[{},{}],passages=empty_passages)))
        # Outer JSON can double serialized records; a raw scalar costs at most seven bytes across two encodings.
        bound=(2*2*PHASES['extract']['output_bytes']+2*4096+2*512+32*112*7+framing+
            serialized_bytes(SCHEMAS['reconcile'])+serialized_bytes(STATE_INSTRUCTIONS)+1024+1)
        self.assertLess(bound,65536)
        self.assertLessEqual(serialized_bytes(SCHEMAS['reconcile']),3072)
        self.assertLessEqual(serialized_bytes(STATE_INSTRUCTIONS),1024)
        wrapper={'id':9007199254740991,'method':'turn/start','params':{'threadId':'\x01'*128,'environments':[],
            'input':[{'type':'text','text':''}],'outputSchema':None,'approvalPolicy':'never','effort':'medium'}}
        self.assertLessEqual(serialized_bytes(wrapper),1024)

    def test_failed_v2_full_descriptor_inputs_and_known_ledger_survive_v3_reopen_unchanged(self):
        control=self.claim();attempt=self.attempt(control)
        reporting={**self.base(control),'code':'invalid_output','call_id':attempt['call_id'],
            'method':self.finish_payload(control,attempt,tokens=12606)['method']}
        self.store.project_state.fail(reporting)
        previous=copy.deepcopy(METHOD_CONFIG)
        previous['version']='project-state-v2';previous['selection']['version']='machine-reserved-round-robin-recent-opening-v2'
        previous.pop('passages');previous.pop('machine_evidence')
        for key in ('max_devices','shared_projection_assignment','spill_between_devices'):previous['selection'].pop(key)
        previous['phases']['extract']['output_bytes']=12288
        previous['instructions_sha256']='d3c980ae985d1fef5a7079d5c7ae62dfac4526ef559ea185291780f0ed528855'
        schemas=copy.deepcopy(SCHEMAS)
        for schema in schemas.values():schema['properties']['uncertainties']['items']['properties']['reason']['enum'].insert(0,'coverage_gap')
        for schema in schemas.values():
            for section in ('assertions','uncertainties'):
                refs=schema['properties'][section]['items']['properties']['source_refs']['items']
                refs['properties']={'message_alias':{'type':'string','minLength':1,'maxLength':40},'quote':{'type':'string','minLength':1,'maxLength':160}}
                refs['required']=['message_alias','quote']
        previous['schema_hashes']={phase:digest(schema)for phase,schema in schemas.items()}
        self.assertEqual(digest(previous),'cabc1ab9ff1b1b2f9dceeced996ee597affb98fc10865bd808f209e50572c4ca')
        with self.store.db:
            row=self.store.db.execute('SELECT manifest FROM project_state_runs WHERE id=?',(control['run_id'],)).fetchone()
            manifest=json.loads(row[0]);manifest.update(method='project-state-v2',method_hash=digest(previous),method_config=previous)
            self.store.db.execute('UPDATE project_state_runs SET manifest=?,input_hash=? WHERE id=?',(json.dumps(manifest),digest(manifest),control['run_id']))
            records=json.loads(self.store.db.execute('SELECT data FROM project_state_inputs WHERE run_id=?',(control['run_id'],)).fetchone()[0]);records.pop('passages')
            for chunk in records['chunks']:
                for message in chunk:message['text']=''.join(part['text']for part in message.pop('passages'))
            self.store.db.execute('UPDATE project_state_inputs SET data=? WHERE run_id=?',(json.dumps(records),control['run_id']))
            source=json.loads(self.store.db.execute('SELECT input FROM project_state_calls WHERE run_id=?',(control['run_id'],)).fetchone()[0])
            for message in source['messages']:message['text']=''.join(part['text']for part in message.pop('passages'))
            self.store.db.execute('UPDATE project_state_calls SET input=?,payload_hash=? WHERE run_id=?',(json.dumps(source),digest(source),control['run_id']))
        before=self.historical_rows(control['run_id'])
        self.store.close();self.store=self.fixture.store=Store(self.fixture.fixture.directory.name);self.store.project_state.enabled_project_id=self.pid
        old=self.store.project_state.job({**self.scope,'run_id':control['run_id']})
        self.assertEqual((old['status'],old['observed_tokens'],old['unknown_usage_calls']),('failed',12606,0))
        self.assertEqual(old['method_hash'],digest(previous));self.assertFalse(old['execution_compatible'])
        with self.assertRaises(StoreError):self.store.project_state.claim({**self.scope,'run_id':control['run_id'],'worker_id':str(uuid.uuid4())})
        with self.assertRaises(StoreError):self.store.project_state.refresh(self.retry_request(control['run_id']))
        self.assertEqual(self.historical_rows(control['run_id']),before)

    def legacy_active(self, running=False):
        old=self.refresh();control=None;attempt=None
        if running:
            control=self.store.project_state.claim({**self.scope,"run_id":old["run_id"],"worker_id":str(uuid.uuid4())})
            attempt=self.attempt(control)
        with self.store.db:
            row=self.store.db.execute("SELECT manifest FROM project_state_runs WHERE id=?",(old["run_id"],)).fetchone()
            manifest=json.loads(row[0]);manifest["method"]="project-state-v1";manifest.pop("method_hash");manifest.pop("method_config")
            old_hash=digest(manifest)
            self.store.db.execute("UPDATE project_state_runs SET manifest=?,input_hash=? WHERE id=?",(json.dumps(manifest),old_hash,old["run_id"]))
        if control:control["input_hash"]=old_hash
        return old,control,attempt

    def historical_rows(self, run_id):
        return {table:[tuple(row) for row in self.store.db.execute('SELECT * FROM '+table+' WHERE '+column+'=? ORDER BY rowid',(run_id,))]
            for table,column in (("project_state_runs","id"),("project_state_inputs","run_id"),("project_state_calls","run_id"))}

    def test_upgrade_ignores_incompatible_queued_history_without_rewriting_or_dispatching_it(self):
        old,_,_=self.legacy_active();before=self.historical_rows(old["run_id"])
        self.assertIsNone(self.store.project_state.job(self.scope)["pending_run"])
        with self.assertRaises(StoreError):self.store.project_state.claim({**self.scope,"run_id":old["run_id"],"worker_id":str(uuid.uuid4())})
        request=self.retry_request(old["run_id"]);request.pop("retry_of_run_id")
        current=self.store.project_state.refresh(request)
        self.assertNotEqual(current["run_id"],old["run_id"])
        self.assertEqual(self.store.project_state.job(self.scope)["pending_run"]["run_id"],current["run_id"])
        self.assertEqual(self.store.project_state.refresh(request)["run_id"],current["run_id"])
        self.assertEqual(self.historical_rows(old["run_id"]),before)

    def test_upgrade_running_history_blocks_overlap_until_stop_and_still_accepts_actual_late_accounting(self):
        old,control,attempt=self.legacy_active(running=True);before=self.historical_rows(old["run_id"])
        with self.assertRaises(StoreError):self.refresh()
        for operation in (lambda:self.store.project_state.heartbeat(self.base(control)),lambda:self.attempt(control),
                lambda:self.store.project_state.finish(self.finish_payload(control,attempt))):
            with self.assertRaises(StoreError):operation()
        self.assertEqual(self.historical_rows(old["run_id"]),before)
        reporting={**self.base(control),"code":"ambiguous_completion","call_id":attempt["call_id"],"method":self.finish_payload(control,attempt)["method"]}
        stopped=self.store.project_state.fail(reporting)
        self.assertEqual(stopped["status"],"needs_review")
        self.assertEqual(stopped["observed_tokens"],30)
        self.assertEqual(self.store.project_state.fail(reporting)["observed_tokens"],30)
        retained=self.historical_rows(old["run_id"])
        self.assertEqual(retained["project_state_inputs"],before["project_state_inputs"])
        self.assertEqual(retained["project_state_runs"][0][6:8],before["project_state_runs"][0][6:8])
        current=self.refresh()
        self.assertEqual(current["status"],"queued")
        self.assertNotEqual(current["run_id"],old["run_id"])
        self.assertEqual(self.historical_rows(old["run_id"]),retained)

    def test_context_unicode_clips_are_disclosed_and_model_never_gets_context_ids_or_source_registry(self):
        profile = self.store.dialogue.profile(self.pid)
        self.store.dialogue.update_profile({"project_id":self.pid,"revision":profile["revision"],"fields":{"goal":"𠀀"*3000,"priority":"\\"*3000,"constraint":"Original constraint."}})
        before = self.store.dialogue.profile(self.pid)
        control = self.claim()
        plan = control["next_plan"]
        self.assertLessEqual(serialized_bytes(plan["input"]["approved_context"]),4096)
        self.assertLessEqual(envelope_bytes(plan["input"],plan["output_schema"],"low"),32768)
        records=json.loads(self.store.db.execute("SELECT data FROM project_state_inputs WHERE run_id=?",(control["run_id"],)).fetchone()[0])
        self.assertEqual(records["coverage"]["context_truncated_dimensions"],["goal","priority"])
        self.assertTrue(before["fields"]["goal"]["answer"].startswith(plan["input"]["approved_context"]["goal"]["answer"]))
        self.assertNotIn("id",plan["input"]["approved_context"]["goal"])
        self.assertNotIn("selected_sources",plan["input"]["control"])
        self.assertEqual(self.store.dialogue.profile(self.pid)["fields"],before["fields"])

    def test_phase_reserve_limits_and_acknowledged_effort_reject_without_losing_known_usage(self):
        control=self.claim()
        with patch("hoomy.project_state.time.time",return_value=control["deadline_at"]-100):
            with self.assertRaises(StoreError):self.attempt(control)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM project_state_calls").fetchone()[0],0)
        attempt=self.attempt(control)
        payload=self.finish_payload(control,attempt)
        payload["method"]["reasoning_effort"]="high"
        result=self.store.project_state.finish(payload)
        self.assertEqual((result["status"],result["failure_code"],result["observed_tokens"]),("failed","invalid_output",30))
        self.assertIsNone(self.store.project_state.read(self.scope)["state"])

    def test_unicode_output_over_phase_bytes_is_rejected_with_every_observed_token_preserved(self):
        self.store.ingestion.accept(self.fixture.payload(sequence=2,text="𠀀"*1000))
        control=self.claim();attempt=self.attempt(control)
        message=next(m for m in attempt["input"]["messages"] if m["passages"][0]["text"].startswith("𠀀"))
        refs=[{"passage_id":message["passages"][0]["passage_id"]}]*2
        output={"schema_version":1,"assertions":[{"key":"a"+str(index),"topic":"𠀀"*80,"text":"𠀀"*240,
            "kind":"reported_status","reported_state":"unknown","limitations":["𠀀"*120],"source_refs":copy.deepcopy(refs)} for index in range(6)],
            "uncertainties":[{"reason":"unclear_scope","text":"𠀀"*180,"source_refs":copy.deepcopy(refs)} for _ in range(2)],"contradictions":[]}
        self.assertGreater(serialized_bytes(output),12288)
        result=self.store.project_state.finish(self.finish_payload(control,attempt,output))
        self.assertEqual((result["status"],result["observed_tokens"],result["unknown_usage_calls"]),("failed",30,0))
        call=self.store.db.execute("SELECT output,method FROM project_state_calls WHERE call_id=?",(attempt["call_id"],)).fetchone()
        self.assertIsNone(call["output"])
        self.assertEqual(json.loads(call["method"])["usage"]["total_tokens"],30)

    def test_valid_unicode_character_limits_match_schema_and_exact_quote_provenance(self):
        self.store.ingestion.accept(self.fixture.payload(sequence=2,text="𠀀"*200))
        self.store.ingestion.accept(self.fixture.payload(device="device-b",sequence=2,text="𠀀"*200))
        control=self.claim();attempt=self.attempt(control)
        output=self.output(attempt)
        message=next(m for m in attempt["input"]["messages"] if m["passages"][0]["text"].startswith("𠀀"))
        output["assertions"][0]["key"]="𠀀"*40
        output["assertions"][0]["source_refs"]=[{"passage_id":message["passages"][0]["passage_id"]}]
        control=self.store.project_state.finish(self.finish_payload(control,attempt,output))
        reconcile=self.attempt(control)
        control=self.store.project_state.finish(self.finish_payload(control,reconcile))
        self.assertEqual(control["status"],"ready")
        state=self.store.project_state.read(self.scope)["state"]
        citation=self.store.project_state.citation({**self.scope,"run_id":control["run_id"],"citation_id":state["assertions"][0]["citation_ids"][0]})
        self.assertEqual(citation["quote"],message["passages"][0]["text"])
        self.assertEqual(citation["char_start"],0)
        self.assertEqual(citation["char_end"],len(citation["quote"]))
        self.assertTrue(all(origin["source_version_id"].startswith("remote-version:") for origin in citation["origins"]))

    def test_v1_ready_state_is_stale_but_eligible_citations_remain_original(self):
        control,read=self.publish()
        quote_id=read["state"]["assertions"][0]["citation_ids"][0]
        original=self.store.project_state.citation({**self.scope,"run_id":control["run_id"],"citation_id":quote_id})
        with self.store.db:
            row=self.store.db.execute("SELECT manifest,output FROM project_state_runs WHERE id=?",(control["run_id"],)).fetchone()
            manifest,output=json.loads(row[0]),json.loads(row[1]);manifest["method"]="project-state-v1";manifest.pop("method_config");manifest.pop("method_hash");output["method"]["version"]="project-state-v1"
            self.store.db.execute("UPDATE project_state_runs SET manifest=?,output=? WHERE id=?",(json.dumps(manifest),json.dumps(output),control["run_id"]))
        read=self.store.project_state.read(self.scope)
        self.assertIn("method_changed",read["state"]["stale_reasons"])
        self.assertEqual(self.store.project_state.citation({**self.scope,"run_id":control["run_id"],"citation_id":quote_id}),original)

    def test_two_machine_manifest_deduplicates_input_and_derives_exact_citation(self):
        control, read = self.publish()
        self.assertEqual(read["coverage"]["source_locations"], 2)
        self.assertEqual(read["coverage"]["unique_projection_blobs"], 1)
        self.assertEqual(read["state"]["coverage"]["provided_messages"], 1)
        assertion = read["state"]["assertions"][0]
        citation = self.store.project_state.citation({**self.scope, "run_id": control["run_id"],
            "citation_id": assertion["citation_ids"][0]})
        self.assertEqual({origin["device_id"] for origin in citation["origins"]}, {"device-a", "device-b"})
        self.assertEqual(citation["line_start"], 2)
        self.assertEqual(citation["message_timestamp"], None)
        self.assertIn(citation["quote"], citation["excerpt"])
        self.assertTrue(all(origin["source_version_id"].startswith("remote-version:") for origin in citation["origins"]))
        self.assertFalse(any(origin["source_sha256_verified"] for origin in citation["origins"]))
        self.assertEqual(self.store.dialogue.profile(self.pid)["revision"], self.fixture.profile["revision"])
        self.assertEqual(self.store.dialogue.profile(self.pid)["fields"], self.fixture.profile["fields"])
        self.assertEqual(read["state"]["method"]["observed_tokens"], 60)

    def test_refresh_and_finish_lost_ack_replay_do_not_duplicate_calls_or_publication(self):
        current = self.store.project_state.read(self.scope)
        request = {**self.scope, "request_id": str(uuid.uuid4()), "context_revision": current["context_revision"],
            "corpus_revision": current["corpus_revision"], "method_hash": current["method_hash"]}
        run = self.store.project_state.refresh(request)
        self.assertEqual(self.store.project_state.refresh(request), run)
        changed = {**request, "corpus_revision": "0" * 64}
        with self.assertRaises(StoreError):
            self.store.project_state.refresh(changed)
        control = self.store.project_state.claim({**self.scope, "run_id": run["run_id"], "worker_id": str(uuid.uuid4())})
        attempt = self.attempt(control)
        payload = self.finish_payload(control, attempt)
        completed = self.store.project_state.finish(payload)
        self.assertEqual(self.store.project_state.finish(payload), completed)
        with self.assertRaises(StoreError):
            self.store.project_state.attempt({**self.base(control), "call_id": attempt["call_id"], **control["next"]})
        self.store.close()
        self.store = self.fixture.store = Store(self.fixture.fixture.directory.name)
        self.store.project_state.enabled_project_id = self.pid
        self.assertEqual(self.store.project_state.finish(payload), completed)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM project_state_calls").fetchone()[0], 1)

    def retry_request(self, run_id):
        current = self.store.project_state.read(self.scope)
        return {**self.scope, "request_id": str(uuid.uuid4()), "context_revision": current["context_revision"],
            "corpus_revision": current["corpus_revision"], "method_hash": current["method_hash"], "retry_of_run_id": run_id}

    def failed_attempt(self):
        control = self.claim()
        attempt = self.attempt(control)
        self.store.project_state.fail({**self.base(control), "code": "ambiguous_completion"})
        return control, attempt

    def test_reviewed_retry_is_new_once_and_preserves_exact_original_manifest_inputs_and_unknown_ledger(self):
        control, attempt = self.failed_attempt()
        parent_id = control["run_id"]
        def parent_rows():
            return {table: [tuple(row) for row in self.store.db.execute('SELECT * FROM '+table+' WHERE '+key+'=? ORDER BY rowid',(parent_id,))]
                for table,key in (("project_state_runs","id"),("project_state_inputs","run_id"),
                                  ("project_state_calls","run_id"),("project_state_requests","run_id"))}
        before = parent_rows()
        approved = self.store.dialogue.profile(self.pid)
        self.assertEqual(self.refresh()["run_id"],parent_id)
        before = parent_rows()
        request = self.retry_request(parent_id)
        with patch.object(self.store.project_state,"_inputs",side_effect=AssertionError("Copy already-reviewed inputs exactly")):
            child = self.store.project_state.refresh(request)
        self.assertNotEqual(child["run_id"],parent_id)
        self.assertEqual((child["status"],child["retry_of_run_id"],child["dispatched_calls"]),("queued",parent_id,0))
        self.assertEqual(parent_rows(),before)
        for column,table,key in (("manifest","project_state_runs","id"),("data","project_state_inputs","run_id")):
            original = self.store.db.execute('SELECT '+column+' FROM '+table+' WHERE '+key+'=?',(parent_id,)).fetchone()[0]
            copied = self.store.db.execute('SELECT '+column+' FROM '+table+' WHERE '+key+'=?',(child["run_id"],)).fetchone()[0]
            self.assertEqual(copied,original)
        self.assertEqual(self.store.project_state.refresh(request),child)
        with self.assertRaises(StoreError):
            self.store.project_state.refresh({**request,"request_id":str(uuid.uuid4())})
        with self.assertRaises(StoreError):
            self.store.project_state.refresh({key:value for key,value in request.items() if key!='retry_of_run_id'})
        self.store.close()
        self.store = self.fixture.store = Store(self.fixture.fixture.directory.name)
        self.store.project_state.enabled_project_id = self.pid
        self.assertEqual(self.store.project_state.refresh(request),child)
        self.assertEqual(parent_rows(),before)
        job = self.store.project_state.job({**self.scope,"run_id":parent_id})
        self.assertEqual((job["status"],job["unknown_usage_calls"],job["retry_run_id"]),("needs_review",1,child["run_id"]))
        child_control = self.store.project_state.claim({**self.scope,"run_id":child["run_id"],"worker_id":str(uuid.uuid4())})
        child_attempt = self.attempt(child_control)
        self.assertEqual(child_attempt["input"],attempt["input"])
        self.assertEqual(child_attempt["payload_hash"],attempt["payload_hash"])
        self.assertNotEqual(child_attempt["call_id"],attempt["call_id"])
        self.assertEqual(parent_rows(),before)
        after = self.store.dialogue.profile(self.pid)
        self.assertEqual((after["revision"],after["fields"]),(approved["revision"],approved["fields"]))

    def test_retry_rejects_nonterminal_computed_expiry_foreign_scope_and_changed_corpus(self):
        control = self.claim()
        request = self.retry_request(control["run_id"])
        with self.assertRaises(StoreError):self.store.project_state.refresh(request)
        with self.store.db:
            self.store.db.execute("UPDATE project_state_runs SET deadline=0,lease_expires=0 WHERE id=?",(control["run_id"],))
        self.assertEqual(self.store.project_state.job({**self.scope,"run_id":control["run_id"]})["status"],"needs_review")
        with self.assertRaises(StoreError):self.store.project_state.refresh(request)
        self.store.project_state.fail({**self.base(control),"code":"ambiguous_completion"})
        for principal in ({"account_id":"another-owner","workspace_id":"personal:42"},
                          {"account_id":"github:42","workspace_id":"another-workspace"}):
            with self.assertRaises(StoreError):self.store.project_state.refresh({**request,"principal":principal})
        self.store.ingestion.accept(self.fixture.payload(sequence=2,text="New fictional evidence after review."))
        with self.assertRaises(StoreError):self.store.project_state.refresh(request)
        with self.assertRaises(StoreError):self.store.project_state.refresh(self.retry_request(control["run_id"]))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM project_state_retry_links").fetchone()[0],0)

    def test_retry_rejects_ready_parent_and_rolls_back_when_child_audit_insert_fails(self):
        control,_ = self.publish()
        with self.assertRaises(StoreError):self.store.project_state.refresh(self.retry_request(control["run_id"]))
        with self.store.db:
            self.store.db.execute("UPDATE project_state_runs SET status='failed',failure_code='model_failed' WHERE id=?",(control["run_id"],))
            self.store.db.execute("CREATE TRIGGER retry_audit_failure BEFORE INSERT ON project_state_retry_links BEGIN SELECT RAISE(ABORT,'fixture audit failure'); END")
        before = self.store.db.execute("SELECT count(*) FROM project_state_runs").fetchone()[0]
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):self.store.project_state.refresh(self.retry_request(control["run_id"]))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM project_state_runs").fetchone()[0],before)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM project_state_retry_links").fetchone()[0],0)

    def test_retry_rejects_other_project_active_job_and_changed_human_context(self):
        control,_ = self.failed_attempt()
        request = self.retry_request(control["run_id"])
        other = str(uuid.uuid4())
        with self.store.db:
            self.store.db.execute("INSERT INTO projects SELECT ?,inferred,overrides,NULL,created_at,updated_at FROM projects WHERE id=?",(other,self.pid))
        evidence = self.fixture.payload()
        evidence["event"]["project_id"] = other
        evidence["policy"]["project_bindings"][0]["project_id"] = other
        self.store.ingestion.accept(evidence)
        other_scope = {**self.scope,"project_id":other}
        current = self.store.project_state.read(other_scope)
        self.store.project_state.enabled_project_id = other
        with self.assertRaises(StoreError) as caught:
            self.store.project_state.refresh({**request,**other_scope,"context_revision":current["context_revision"],"corpus_revision":current["corpus_revision"]})
        self.assertEqual(caught.exception.status,404)
        self.store.project_state.enabled_project_id = self.pid
        active = str(uuid.uuid4())
        with self.store.db:
            self.store.db.execute("INSERT INTO project_state_runs(id,account_id,workspace_id,project_id,context_revision,corpus_revision,input_hash,manifest,status,created_at) SELECT ?,account_id,workspace_id,project_id,context_revision,corpus_revision,input_hash,manifest,'queued',created_at FROM project_state_runs WHERE id=?",(active,control["run_id"]))
        with self.assertRaises(StoreError):self.store.project_state.refresh(request)
        with self.store.db:self.store.db.execute("DELETE FROM project_state_runs WHERE id=?",(active,))
        profile = self.store.dialogue.profile(self.pid)
        self.store.dialogue.update_profile({"project_id":self.pid,"revision":profile["revision"],"fields":{"priority":"A newly approved fixture priority."}})
        with self.assertRaises(StoreError):self.store.project_state.refresh(self.retry_request(control["run_id"]))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM project_state_retry_links").fetchone()[0],0)

    def test_invalid_quotes_aliases_kinds_and_contradictions_fail_without_losing_usage(self):
        for change in (lambda o: o["assertions"][0]["source_refs"][0].update(quote="Invented quote"),
                       lambda o: o["assertions"][0]["source_refs"][0].update(passage_id="p9999_99"),
                       lambda o: o["assertions"][0].update(kind="verified_observation"),
                       lambda o: o["assertions"][0].update(actor="owner"),
                       lambda o: o["contradictions"].append({"assertion_keys":["a1","missing"],"text":"Unsupported conflict"})):
            with self.subTest(change=change):
                # Each independent fixture gets a fresh corpus revision, not an automatic retry.
                payload = self.fixture.payload(sequence=self.store.db.execute("SELECT count(*) FROM project_state_runs").fetchone()[0] + 2,
                    text="Changing fictional corpus " + str(uuid.uuid4()))
                self.store.ingestion.accept(payload)
                control = self.claim()
                attempt = self.attempt(control)
                output = self.output(attempt)
                change(output)
                result = self.store.project_state.finish(self.finish_payload(control, attempt, output))
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["failure_code"], "invalid_output")
                self.assertEqual(result["observed_tokens"], 30)
                self.assertEqual(result["unknown_usage_calls"], 0)
                self.assertIsNone(self.store.project_state.read(self.scope)["state"])

    def test_excluded_sources_suppress_dependent_text_and_saved_citation_after_publication(self):
        control, read = self.publish()
        citation_id = read["state"]["assertions"][0]["citation_ids"][0]
        self.store.ingestion.accept(self.fixture.excluded())
        current = self.store.project_state.read(self.scope)
        self.assertTrue(current["state"]["stale"])
        self.assertEqual(current["state"]["assertions"], [])
        self.assertEqual(current["state"]["suppressed_assertions"], 1)
        self.assertNotIn("A participant recorded", json.dumps(current))
        with self.assertRaises(StoreError) as caught:
            self.store.project_state.citation({**self.scope, "run_id": control["run_id"], "citation_id": citation_id})
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(self.store.dialogue.profile(self.pid)["fields"], self.fixture.profile["fields"])

    def test_exclusion_during_model_call_prevents_publish_but_retains_known_spend(self):
        control = self.claim()
        attempt = self.attempt(control)
        self.store.ingestion.accept(self.fixture.excluded())
        result = self.store.project_state.finish(self.finish_payload(control, attempt))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure_code"], "input_ineligible")
        self.assertEqual(result["observed_tokens"], 30)
        self.assertIsNone(self.store.project_state.read(self.scope)["state"])

    def test_budget_unknown_usage_and_late_failure_accounting_never_redispatch(self):
        control = self.claim()
        attempt = self.attempt(control)
        payload = self.finish_payload(control, attempt, tokens=200001)
        result = self.store.project_state.finish(payload)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["observed_tokens"], 200001)
        self.assertEqual(result["unknown_usage_calls"], 0)
        self.assertEqual(self.store.project_state.finish(payload), result)
        with self.assertRaises(StoreError):
            self.attempt(control)
        self.assertIsNone(self.store.project_state.read(self.scope)["state"])

    def test_late_known_telemetry_and_exact_reporting_replay_preserve_accounting_only(self):
        control = self.claim()
        attempt = self.attempt(control)
        with self.store.db:
            self.store.db.execute("UPDATE project_state_runs SET lease_expires=0,deadline=0 WHERE id=?", (control["run_id"],))
        report = {**self.base(control), "call_id": attempt["call_id"], "code": "invalid_output",
            "method": self.finish_payload(control, attempt)["method"]}
        result = self.store.project_state.fail(report)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["observed_tokens"], 30)
        self.assertEqual(self.store.project_state.fail(report), result)
        changed = copy.deepcopy(report); changed["method"]["usage"]["total_tokens"] = 31
        with self.assertRaises(StoreError):
            self.store.project_state.fail(changed)
        self.assertIsNone(self.store.project_state.read(self.scope)["state"])
        row = self.store.db.execute("SELECT output,method FROM project_state_calls WHERE run_id=?", (control["run_id"],)).fetchone()
        self.assertIsNone(row["output"])
        self.assertIsNotNone(row["method"])

    def test_unknown_usage_preserves_prior_known_subtotal_and_marks_unknown_call(self):
        control = self.claim()
        attempt = self.attempt(control)
        control = self.store.project_state.finish(self.finish_payload(control, attempt))
        reconcile = self.attempt(control)
        payload = self.finish_payload(control, reconcile)
        payload["method"]["usage"] = None
        result = self.store.project_state.finish(payload)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["unknown_usage_calls"], 1)
        self.assertEqual(result["observed_tokens"], 30)
        self.assertEqual(self.store.project_state.finish(payload), result)

    def test_pending_discovery_is_queued_only_owner_scoped_and_never_dispatches(self):
        self.assertIsNone(self.store.project_state.job(self.scope)["pending_run"])
        queued = self.refresh()
        pending = self.store.project_state.job(self.scope)
        self.assertEqual(pending["pending_run"]["run_id"], queued["run_id"])
        self.assertEqual(pending["pending_run"]["status"], "queued")
        self.assertNotIn("lease_id", pending["pending_run"])
        self.assertNotIn("input", pending["pending_run"])
        foreign = {**self.scope, "principal": {"account_id": "foreign", "workspace_id": "foreign"}}
        self.assertIsNone(self.store.project_state.job(foreign)["pending_run"])
        control = self.store.project_state.claim({**self.scope, "run_id": queued["run_id"], "worker_id": str(uuid.uuid4())})
        self.assertIsNone(self.store.project_state.job(self.scope)["pending_run"])
        attempt = self.attempt(control)
        self.store.project_state.fail({**self.base(control), "code": "ambiguous_completion"})
        self.assertIsNone(self.store.project_state.job(self.scope)["pending_run"])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM project_state_calls").fetchone()[0], 1)

    def test_lease_input_hash_account_and_context_revision_are_bound(self):
        control = self.claim()
        for replacement in ({"lease_id": str(uuid.uuid4())}, {"input_hash": "0" * 64},
                            {"principal": {"account_id": "another", "workspace_id": "personal:42"}}):
            with self.assertRaises(StoreError):
                self.store.project_state.heartbeat({**self.base(control), **replacement})
        new = self.store.dialogue.update_profile({"project_id": self.pid, "revision": self.fixture.profile["revision"],
            "fields": {"priority": "Explicit new owner priority."}})
        self.assertNotEqual(new["revision"], self.fixture.profile["revision"])
        attempt = self.attempt(control)
        self.assertNotIn("Explicit new owner priority", json.dumps(attempt["input"]))
        with self.assertRaises(StoreError):
            self.store.project_state.refresh({**self.scope, "request_id": str(uuid.uuid4()),
                "context_revision": self.fixture.profile["revision"], "corpus_revision": control["corpus_revision"]})

    def test_chunks_are_bounded_and_partial_coverage_does_not_claim_all_messages_read(self):
        payload = self.fixture.payload(sequence=2, text="x" * 200000)
        self.store.ingestion.accept(payload)
        control = self.claim()
        attempt = self.attempt(control)
        self.assertLessEqual(sum(len(canonical(m)) for m in attempt["input"]["messages"]), CHUNK_BYTES)
        self.assertTrue(attempt["input"]["control"]["selection_partial"])
        records=json.loads(self.store.db.execute("SELECT data FROM project_state_inputs WHERE run_id=?",(control["run_id"],)).fetchone()[0])
        self.assertGreater(records["coverage"]["truncated_messages"],0)
        self.assertNotIn("lease_id", json.dumps(attempt["input"]))
        self.assertNotIn('"source_location":', json.dumps(attempt["input"]))

    def test_large_mac_sources_cannot_starve_the_other_machine_and_selected_spans_are_recorded(self):
        for index in range(16):
            payload = self.fixture.payload(text="Mac fixture " + str(index) + " " + "x" * 200000)
            payload["event"]["session_id"] = "session-" + str(index)
            payload["event"]["source_location"]["path"] = "/fictional-history/sessions/source-" + str(index) + ".jsonl"
            self.store.ingestion.accept(payload)
        control = self.claim()
        records = json.loads(self.store.db.execute("SELECT data FROM project_state_inputs WHERE run_id=?", (control["run_id"],)).fetchone()[0])
        coverage = records["coverage"]
        self.assertEqual(set(coverage["provided_sources_by_device"]), {"device-a", "device-b"})
        self.assertGreater(coverage["provided_sources_by_device"]["device-b"], 0)
        self.assertTrue(coverage["selected_sources"])
        self.assertTrue(all(record["spans"] and record["source_version_id"].startswith("remote-version:") for record in coverage["selected_sources"]))
        self.assertTrue(all(sum(len(canonical(message)) for message in chunk) <= CHUNK_BYTES for chunk in records["chunks"]))

    def test_quote_in_full_cache_but_outside_delivered_truncated_span_is_rejected(self):
        payload = self.fixture.payload(sequence=2, text="Prefix " + "x" * 100000 + "UNREAD_TAIL_LITERAL")
        self.store.ingestion.accept(payload)
        control = self.claim()
        attempt = self.attempt(control)
        alias = next(message["message_alias"] for message in attempt["input"]["messages"] if message["passages"][0]["text"].startswith("Prefix"))
        output = self.output(attempt)
        output["assertions"][0]["source_refs"] = [{"message_alias": alias, "quote": "UNREAD_TAIL_LITERAL"}]
        result = self.store.project_state.finish(self.finish_payload(control, attempt, output))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure_code"], "invalid_output")

    def test_other_slot_alias_and_new_reconciliation_quote_cannot_be_cited(self):
        payload = self.fixture.payload(sequence=2)
        payload["event"]["projection"]["messages"] = [
            {"role":"user","text":label + "x" * 30000,"line_start":index+2,"line_end":index+2,"timestamp":None}
            for index,label in enumerate(("First ", "Second ", "Third "))]
        payload["event"]["projected_sha256"] = digest(payload["event"]["projection"])
        self.store.ingestion.accept(payload)
        for index in range(24):
            extra = self.fixture.payload(text="Additional slot fixture " + str(index) + "x" * 1500)
            extra["event"]["session_id"] = "slot-" + str(index)
            extra["event"]["source_location"]["path"] = "/fictional-history/sessions/slot-"+str(index)+".jsonl"
            self.store.ingestion.accept(extra)
        control = self.claim()
        attempt = self.attempt(control)
        records = json.loads(self.store.db.execute("SELECT data FROM project_state_inputs WHERE run_id=?", (control["run_id"],)).fetchone()[0])
        delivered = allowed_passage_ids(attempt["input"])
        unprovided = next(key for key in records["passages"] if key not in delivered)
        output = self.output(attempt)
        output["assertions"][0]["source_refs"] = [{"passage_id": unprovided}]
        result = self.store.project_state.finish(self.finish_payload(control, attempt, output))
        self.assertEqual(result["failure_code"], "invalid_output")
        # A separate corpus supplies a valid extraction, then cites an unused passage.
        self.store.ingestion.accept(self.fixture.payload(sequence=3,text="The complete visible statement."))
        control = self.claim()
        while control["next"]["phase"] == "extract":
            extract = self.attempt(control)
            control = self.store.project_state.finish(self.finish_payload(control, extract))
        reconcile = self.attempt(control)
        output = self.output(reconcile)
        records=json.loads(self.store.db.execute("SELECT data FROM project_state_inputs WHERE run_id=?",(control["run_id"],)).fetchone()[0])
        unused=next(key for key in records["passages"] if key not in allowed_passage_ids(reconcile["input"]))
        output["assertions"][0]["source_refs"]=[{"passage_id":unused}]
        result = self.store.project_state.finish(self.finish_payload(control, reconcile, output))
        self.assertEqual(result["failure_code"], "invalid_output")

    def test_v4_device_slots_do_not_mix_or_spill_and_counters_stay_server_only(self):
        for device,label in (("device-a","Mac"),("device-b","VPS")):
            payload=self.fixture.payload(device=device,sequence=2,text=label+"x"*15000)
            payload["event"]["projection"]["messages"]=[{**payload["event"]["projection"]["messages"][0],"text":label+str(i)+"x"*15000,"line_start":i+2,"line_end":i+2}for i in range(60)]
            payload["event"]["projected_sha256"]=digest(payload["event"]["projection"])
            self.store.ingestion.accept(payload)
        control=self.claim()
        records=json.loads(self.store.db.execute("SELECT data FROM project_state_inputs WHERE run_id=?",(control["run_id"],)).fetchone()[0])
        self.assertEqual(records["slot_machines"],["machine-1","machine-2"])
        self.assertEqual(len(records["chunks"]),2)
        for index,chunk in enumerate(records["chunks"]):
            self.assertTrue(all(m["machine_aliases"]==[records["slot_machines"][index]]for m in chunk))
        self.assertGreater(records["coverage"]["unread_messages"],0)
        plan=control["next_plan"]["input"]
        self.assertEqual(plan["control"],{"selection_partial":True,"required_machine_aliases":["machine-1"]})
        self.assertNotIn("coverage",plan)

    def test_v4_empty_extraction_stops_with_known_usage_without_inventing_machine_facts(self):
        control=self.claim();attempt=self.attempt(control)
        empty={"schema_version":1,"assertions":[],"uncertainties":[],"contradictions":[]}
        result=self.store.project_state.finish({**self.finish_payload(control,attempt),"output":empty})
        self.assertEqual((result["status"],result["failure_code"],result["observed_tokens"],result["unknown_usage_calls"]),("needs_review","insufficient_device_evidence",30,0))
        self.assertEqual(result["completed_calls"],0)
        self.assertIsNone(self.store.project_state.read(self.scope)["state"])
        self.assertEqual(self.store.project_state.finish({**self.finish_payload(control,attempt),"output":empty}),result)

    def test_v4_scoped_uncertainty_can_represent_device_but_reconcile_may_not_drop_it(self):
        self.store.ingestion.accept(self.fixture.payload(device="device-b",sequence=2,text="A separate VPS report is historical."))
        control=self.claim()
        while control["next"]["phase"]=="extract":
            attempt=self.attempt(control)
            output=self.output(attempt)
            if attempt["chunk_index"]==1:
                output={"schema_version":1,"assertions":[],"uncertainties":[{"reason":"missing_current_observation","text":"This historical report does not verify the present situation.","source_refs":output["assertions"][0]["source_refs"]}],"contradictions":[]}
            control=self.store.project_state.finish(self.finish_payload(control,attempt,output))
        attempt=self.attempt(control)
        self.assertEqual(attempt["input"]["control"]["required_machine_aliases"],["machine-1","machine-2"])
        output=copy.deepcopy(attempt["input"]["extractions"][0]["result"])
        result=self.store.project_state.finish(self.finish_payload(control,attempt,output))
        self.assertEqual((result["status"],result["failure_code"],result["observed_tokens"]),("needs_review","insufficient_device_evidence",90))
        self.assertEqual(result["completed_calls"],2)
        self.assertIsNone(self.store.project_state.read(self.scope)["state"])

    def test_v4_shared_projection_is_one_witness_with_all_device_origins(self):
        control,read=self.publish()
        calls=list(self.store.db.execute("SELECT phase,input FROM project_state_calls WHERE run_id=?",(control["run_id"],)))
        self.assertEqual(sum(c["phase"]=="extract"for c in calls),1)
        plan=json.loads(next(c["input"]for c in calls if c["phase"]=="reconcile"))
        group=plan["extractions"][0]
        self.assertEqual(len(plan["passages"]),1)
        self.assertEqual(group["shared_passage_ids"],[plan["passages"][0]["passage_id"]])
        self.assertTrue(read["state"]["device_coverage"]["cross_machine_complete"])
        self.assertEqual(read["state"]["coverage"]["provided_messages"],1)
        self.assertEqual(len(read["state"]["device_coverage"]["cited_devices"]),2)

    def test_v4_third_device_refused_before_blob_read_or_dispatch(self):
        self.store.ingestion.accept(self.fixture.payload(device="device-c"))
        original=self.store.db
        class NoBodyConnection:
            def execute(inner,sql,*args):
                self.assertNotIn("SELECT b.projection",sql)
                return original.execute(sql,*args)
            def __enter__(inner):return original.__enter__()
            def __exit__(inner,*args):return original.__exit__(*args)
        self.store.db=NoBodyConnection()
        try:
            run=self.refresh()
            self.assertEqual((run["status"],run["failure_code"],run["dispatched_calls"]),("needs_review","unsupported_device_scope",0))
            self.assertIsNone(self.store.project_state.job(self.scope)["pending_run"])
        finally:self.store.db=original

    def test_v4_device_scope_change_stops_before_attempt_and_keeps_ledger_empty(self):
        control=self.claim()
        self.store.ingestion.accept(self.fixture.payload(device="device-c"))
        with self.assertRaises(StoreError):self.attempt(control)
        job=self.store.project_state.job({**self.scope,"run_id":control["run_id"]})
        self.assertEqual((job["status"],job["failure_code"],job["dispatched_calls"]),("needs_review","unsupported_device_scope",0))

    def test_v4_read_qualifies_immutable_mac_only_historical_ready_result(self):
        control,read=self.publish()
        with self.store.db:
            run=self.store.db.execute("SELECT manifest,output FROM project_state_runs WHERE id=?",(control["run_id"],)).fetchone()
            manifest,output=json.loads(run[0]),json.loads(run[1])
            manifest["method"]="project-state-v3";manifest.pop("method_config");manifest.pop("method_hash")
            output["method"]["version"]="project-state-v3";output["method"]["observed_tokens"]=34753
            self.store.db.execute("UPDATE project_state_runs SET manifest=?,output=? WHERE id=?",(json.dumps(manifest),json.dumps(output),control["run_id"]))
            for row in self.store.db.execute("SELECT id,data FROM project_state_citations WHERE run_id=?",(control["run_id"],)):
                citation=json.loads(row["data"]);citation["origins"]=[o for o in citation["origins"]if o["device_id"]=="device-a"]
                self.store.db.execute("UPDATE project_state_citations SET data=? WHERE run_id=? AND id=?",(json.dumps(citation),control["run_id"],row["id"]))
        before=[tuple(row)for row in self.store.db.execute("SELECT * FROM project_state_runs WHERE id=?",(control["run_id"],))]
        calls_before=[tuple(row)for row in self.store.db.execute("SELECT * FROM project_state_calls WHERE run_id=?",(control["run_id"],))]
        result=self.store.project_state.read(self.scope)["state"]
        self.assertEqual(calls_before,[tuple(row)for row in self.store.db.execute("SELECT * FROM project_state_calls WHERE run_id=?",(control["run_id"],))])
        self.assertEqual(result["device_coverage"],{"provided_devices":["device-a","device-b"],"cited_devices":["device-a"],"uncited_provided_devices":["device-b"],"cross_machine_complete":False})
        self.assertEqual(result["method"]["observed_tokens"],34753)
        self.assertIn("method_changed",result["stale_reasons"])
        self.assertEqual(before,[tuple(row)for row in self.store.db.execute("SELECT * FROM project_state_runs WHERE id=?",(control["run_id"],))])

    def test_v4_model_cannot_cite_server_coverage_taxonomy(self):
        control=self.claim();attempt=self.attempt(control);output=self.output(attempt)
        output["uncertainties"]=[{"reason":"coverage_gap","text":"This is server corpus metadata.","source_refs":output["assertions"][0]["source_refs"]}]
        result=self.store.project_state.finish(self.finish_payload(control,attempt,output))
        self.assertEqual((result["status"],result["failure_code"],result["observed_tokens"]),("failed","invalid_output",30))

    def test_v4_scope_check_and_dispatch_are_atomic_against_a_waiting_third_device_writer(self):
        import threading
        control=self.claim();original_lock=self.store.lock
        writer_started,writer_done=threading.Event(),threading.Event()
        releases=[];errors=[];armed=[False]
        third=self.fixture.payload(device="device-c")
        def ingest():
            writer_started.set()
            try:self.store.ingestion.accept(third)
            except BaseException as error:errors.append(error)
            finally:writer_done.set()
        class ObservedLock:
            def __enter__(inner):original_lock.acquire();return inner
            def __exit__(inner,*args):
                main=threading.current_thread() is threading.main_thread()
                if main and armed[0]:releases.append(self.store.db.execute("SELECT count(*) FROM project_state_calls").fetchone()[0])
                original_lock.release()
                if main and armed[0]:self.assertTrue(writer_done.wait(2))
        self.store.lock=ObservedLock()
        original_scope=self.store.project_state._unsupported_scope
        threads=[]
        def scope(principal,pid):
            result=original_scope(principal,pid)
            if not armed[0]:
                armed[0]=True;thread=threading.Thread(target=ingest);threads.append(thread);thread.start()
                self.assertTrue(writer_started.wait(2))
            return result
        try:
            with patch.object(self.store.project_state,"_unsupported_scope",side_effect=scope):self.attempt(control)
        finally:
            for thread in threads:thread.join(2)
            self.store.lock=original_lock
        self.assertFalse(errors)
        self.assertEqual(releases,[1])
        self.assertEqual(len(self.store.project_state.read(self.scope)["coverage"]["locations_by_device"]),3)

    def test_v4_third_device_after_dispatch_retains_known_spend_and_cannot_publish(self):
        control=self.claim();attempt=self.attempt(control)
        self.store.ingestion.accept(self.fixture.payload(device="device-c"))
        payload=self.finish_payload(control,attempt,tokens=45)
        result=self.store.project_state.finish(payload)
        self.assertEqual((result["status"],result["failure_code"],result["observed_tokens"],result["unknown_usage_calls"]),("needs_review","unsupported_device_scope",45,0))
        self.assertEqual(result["completed_calls"],0)
        self.assertIsNone(self.store.project_state.read(self.scope)["state"])
        self.assertEqual(self.store.project_state.finish(payload),result)

    def test_v4_third_device_stops_claim_preparation(self):
        queued=self.refresh()
        self.store.ingestion.accept(self.fixture.payload(device="device-c"))
        result=self.store.project_state.claim({**self.scope,"run_id":queued["run_id"],"worker_id":str(uuid.uuid4())})
        self.assertEqual((result["status"],result["failure_code"]),("needs_review","unsupported_device_scope"))
        self.assertIsNone(result["next_plan"])
        self.assertIsNone(result["lease_id"])
        self.assertEqual(result["dispatched_calls"],0)

    def test_reader_quality_retains_raw_result_ledger_and_separates_source_withdrawal(self):
        self.store.ingestion.accept(self.fixture.payload(device="device-b",sequence=2,text="VPS historical report v0.3.0."))
        control=self.claim()
        while control["next"]["phase"]=="extract":
            attempt=self.attempt(control);control=self.store.project_state.finish(self.finish_payload(control,attempt))
        reconcile=self.attempt(control);output=self.output(reconcile)
        bad={**copy.deepcopy(output["assertions"][0]),"key":"invalid-version","text":"The report installed v999.0.0."}
        output["assertions"].append(bad)
        output["contradictions"]=[{"assertion_keys":[output["assertions"][0]["key"],bad["key"]],"text":"These reported details conflict."}]
        payload=self.finish_payload(control,reconcile,output)
        result=self.store.project_state.finish(payload)
        self.assertEqual(result["status"],"ready")
        raw=self.store.db.execute("SELECT output,state_revision FROM project_state_runs WHERE id=?",(control["run_id"],)).fetchone()
        ledger=[tuple(r)for r in self.store.db.execute("SELECT * FROM project_state_calls WHERE run_id=?",(control["run_id"],))]
        public=self.store.project_state.read(self.scope)["state"]
        self.assertEqual((len(public["assertions"]),len(public["contradictions"])),(2,0))
        quality=public["quality"]
        self.assertEqual((quality["raw_assertions"],quality["source_eligible_assertions"],quality["visible_assertions"]),(3,3,2))
        self.assertEqual(quality["excluded_items"][0]["reasons"],["unsupported_version"])
        self.assertEqual(public["suppressed_assertions"],0)
        self.assertTrue(public["device_coverage"]["cross_machine_complete"])
        self.assertNotEqual(public["visible_revision"],public["state_revision"])
        self.assertEqual(raw["state_revision"],public["state_revision"])
        self.assertEqual(len(json.loads(raw["output"])["assertions"]),3)
        call=self.store.db.execute("SELECT output FROM project_state_calls WHERE call_id=?",(reconcile["call_id"],)).fetchone()
        self.assertEqual(json.loads(call[0]),output)
        self.assertEqual(tuple(raw),tuple(self.store.db.execute("SELECT output,state_revision FROM project_state_runs WHERE id=?",(control["run_id"],)).fetchone()))
        self.assertEqual(ledger,[tuple(r)for r in self.store.db.execute("SELECT * FROM project_state_calls WHERE run_id=?",(control["run_id"],))])
        self.assertEqual(self.store.project_state.finish(payload)["observed_tokens"],90)
        self.store.ingestion.accept(self.fixture.excluded(device="device-b",sequence=3))
        withdrawn=self.store.project_state.read(self.scope)["state"]
        self.assertEqual((withdrawn["quality"]["raw_assertions"],withdrawn["quality"]["source_eligible_assertions"],withdrawn["quality"]["visible_assertions"]),(3,2,1))
        self.assertEqual(withdrawn["suppressed_assertions"],1)
        self.assertFalse(withdrawn["device_coverage"]["cross_machine_complete"])
        self.assertEqual(withdrawn["device_coverage"]["uncited_provided_devices"],["device-b"])
        self.assertNotEqual(withdrawn["visible_revision"],public["visible_revision"])

    def test_future_publication_cannot_count_a_device_only_supported_by_quality_excluded_prose(self):
        self.store.ingestion.accept(self.fixture.payload(device="device-b",sequence=2,text="A separate VPS report."))
        control=self.claim()
        while control["next"]["phase"]=="extract":
            attempt=self.attempt(control);control=self.store.project_state.finish(self.finish_payload(control,attempt))
        reconcile=self.attempt(control);output=self.output(reconcile)
        output["assertions"][1]["text"]="The reported behavior and explicit"
        result=self.store.project_state.finish(self.finish_payload(control,reconcile,output))
        self.assertEqual((result["status"],result["failure_code"],result["observed_tokens"],result["unknown_usage_calls"]),("needs_review","insufficient_device_evidence",90,0))
        self.assertIsNone(self.store.project_state.read(self.scope)["state"])
        raw=self.store.db.execute("SELECT output,status FROM project_state_calls WHERE call_id=?",(reconcile["call_id"],)).fetchone()
        self.assertEqual(json.loads(raw["output"]),output)
        self.assertEqual(raw["status"],"observed")
        self.assertEqual(self.store.project_state.finish(self.finish_payload(control,reconcile,output)),result)


if __name__ == "__main__":
    unittest.main()
