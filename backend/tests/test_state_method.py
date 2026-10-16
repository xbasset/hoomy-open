import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from hoomy import inference
from hoomy import project_state_contract as contract
from hoomy import state_executor as executor
import test_state_executor as fixtures

PROGRAM='''import json,sys
marker=MARKER
for line in sys.stdin:
 v=json.loads(line);method=v['method']
 if method=='thread/start':
  with open(marker+'-threads','a')as f:f.write('thread\\n')
  result={'thread':{'id':'thread','ephemeral':True},'model':'gpt-6.1-sol'}
 elif method=='mcpServerStatus/list':result={'data':[],'nextCursor':None}
 elif method=='turn/start':
  with open(marker,'a')as f:f.write('turn\\n')
  events=[{'id':v['id'],'result':{'turn':{'id':'turn'}}},
   {'method':'thread/tokenUsage/updated','params':{'threadId':'thread','turnId':'turn','tokenUsage':{'last':{'inputTokens':10,'outputTokens':5,'totalTokens':15}}}},
   {'method':'item/completed','params':{'threadId':'thread','turnId':'turn','item':{'type':'agentMessage','text':'{"schema_version":1,"assertions":[],"uncertainties":[],"contradictions":[]}'}}},
   {'method':'turn/completed','params':{'threadId':'thread','turn':{'id':'turn','status':'completed'}}}]
  sys.stdout.write(''.join(json.dumps(e)+'\\n'for e in events));sys.stdout.flush();continue
 else:raise RuntimeError('unexpected fictional request')
 print(json.dumps({'id':v['id'],'result':result}),flush=True)
'''


class MethodTests(unittest.TestCase):
 def test_shared_policy_is_pinned_and_legacy_running_work_is_not_dispatched(self):
  for mutate in (lambda v:v.update(method_version='project-state-v1'),lambda v:v.update(method_hash='0'*64),
    lambda v:v['execution_policy'].update(initialization_seconds=41)):
   remote,model,runner=fixtures.ExecutorTests().setup_runner();original=remote.control
   def control():
    value=copy.deepcopy(original());mutate(value);return value
   remote.control=control
   self.assertEqual(runner.run()['failure_code'],'input_ineligible');self.assertFalse(model.prepared);self.assertEqual(model.calls,[])
   self.assertEqual(remote.dispatched,0)

 def test_invalid_required_machine_control_stops_before_intent(self):
  for machines in ([], ['machine-3'], ['machine-1','machine-2'], ['machine-1','machine-1'], [True]):
   remote,model,runner=fixtures.ExecutorTests().setup_runner();original=remote.plan
   def plan(phase):
    result=copy.deepcopy(original(phase));result['input']['control']['required_machine_aliases']=machines
    import hashlib
    result['payload_hash']=hashlib.sha256(executor.canonical(result['input'])).hexdigest()
    return result
   remote.plan=plan
   self.assertEqual(runner.run()['failure_code'],'input_ineligible');self.assertEqual(remote.dispatched,0)
   self.assertEqual(model.preflight_calls,[])

 def test_terminal_device_scope_claim_has_no_model_preparation(self):
  remote,model,runner=fixtures.ExecutorTests().setup_runner();original=remote.control
  def control(*args):
   value=original(*args);value.update(status='needs_review',failure_code='unsupported_device_scope');return value
  remote.control=control
  self.assertEqual(runner.run(),{'status':'needs_review','failure_code':'unsupported_device_scope'})
  self.assertFalse(model.prepared);self.assertEqual(remote.dispatched,0)

 def test_readonly_plan_preflight_precedes_intent_and_ack_plan_changes_stop(self):
  remote,model,runner=fixtures.ExecutorTests().setup_runner()
  self.assertEqual(runner.run()['status'],'ready');self.assertEqual(model.preflight_calls,['extract','reconcile'])
  remote,model,runner=fixtures.ExecutorTests().setup_runner();original=remote.worker
  def worker(args):
   result=original(args)
   if args['action']=='attempt':result['phase_config']={**result['phase_config'],'effort':'high'}
   return result
  remote.worker=worker
  self.assertEqual(runner.run()['failure_code'],'input_ineligible');self.assertEqual(remote.dispatched,1);self.assertEqual(model.calls,[])

 def test_preflight_refusal_has_no_intent_or_fake_usage(self):
  remote,model,runner=fixtures.ExecutorTests().setup_runner()
  def reject(*args):raise executor.ExecutorError('model_unavailable')
  model.prepare_call=reject
  self.assertEqual(runner.run()['failure_code'],'model_unavailable');self.assertEqual(remote.dispatched,0);self.assertEqual(remote.tokens,0)
  self.assertNotIn('method',remote.actions[-1])

 def model(self,root):
  binary=Path(root)/'fake-native';marker=Path(root)/'native-turn'
  binary.write_text('#!'+sys.executable+'\n'+PROGRAM.replace('MARKER',repr(str(marker))));binary.chmod(0o700)
  provider=executor.RestrictedModel(str(binary),root,lambda:False)
  provider.client=inference._Client(str(binary),root,lambda:False,instructions=contract.STATE_INSTRUCTIONS)
  return provider,marker

 def test_actual_thread_prepare_is_zero_turn_and_reused_only_after_intent(self):
  with tempfile.TemporaryDirectory(dir='/private/tmp')as root:
   provider,marker=self.model(root);payload={'task':'extract','messages':[]}
   try:
    provider.prepare_call(payload,contract.SCHEMAS['extract'],contract.PHASES['extract'],300)
    self.assertFalse(marker.exists());self.assertIsNone(provider.last_method)
    # The fictional central ACK comes here; only call() may send turn/start.
    output,method=provider.call(payload,contract.SCHEMAS['extract'],300,0)
    self.assertEqual(output,fixtures.EMPTY);self.assertEqual(method['reasoning_effort'],'low');self.assertEqual(method['model'],'gpt-6.1-sol')
    self.assertEqual(marker.read_text(),'turn\n');self.assertEqual(Path(str(marker)+'-threads').read_text(),'thread\n')
   finally:provider.close()

 def test_changed_prepared_input_cannot_send_after_intent(self):
  with tempfile.TemporaryDirectory(dir='/private/tmp')as root:
   provider,marker=self.model(root);payload={'task':'extract','messages':[]}
   try:
    provider.prepare_call(payload,contract.SCHEMAS['extract'],contract.PHASES['extract'],300)
    with self.assertRaises(inference.InferenceError):provider.call({**payload,'changed':True},contract.SCHEMAS['extract'],300,0)
    self.assertFalse(marker.exists())
   finally:provider.close()

 def test_exact_native_write_matches_shared_envelope_and_dangerous_shapes_refuse(self):
  with tempfile.TemporaryDirectory(dir='/private/tmp')as root:
   provider,marker=self.model(root)
   try:
    provider.phase='extract';provider.client.actual_model='gpt-6.1-sol'
    for text in ('😀'*10,'\\'*10,'"'*10,'\x01'*10):
     request={'id':1,'method':'turn/start','params':{'threadId':'thread','environments':[],
      'input':[{'type':'text','text':json.dumps({'task':'extract','text':text},ensure_ascii=False)}],
      'outputSchema':contract.SCHEMAS['extract'],'approvalPolicy':'never','effort':'low'}}
     provider._guard_request(request)
     wire=io.StringIO();fake=object.__new__(inference._Client);fake.process=SimpleNamespace(stdin=wire);fake.send(request)
     self.assertEqual(contract.native_request_bytes(request),len(wire.getvalue().encode())+contract.serialized_bytes(contract.STATE_INSTRUCTIONS))
    for change in ({'id':True},{'id':9007199254740992},{'params':[]},
      {'params':{**request['params'],'threadId':'x'*129}}, {'params':{**request['params'],'threadId':'\ud800'}},
      {'params':{**request['params'],'effort':'high'}},{'params':{**request['params'],'input':[{'type':'text','text':'\\'*32768}]}}):
     with self.assertRaises(executor.ExecutorError):provider._guard_request({**request,**change})
   finally:provider.close()

 def test_phase_budget_preserves_reconciliation_reserve_and_caps_actual_turn(self):
  with tempfile.TemporaryDirectory(dir='/private/tmp')as root:
   provider,marker=self.model(root)
   try:
    with self.assertRaises(executor.ExecutorError):provider.prepare_call({'task':'extract'},contract.SCHEMAS['extract'],contract.PHASES['extract'],100)
    self.assertFalse(marker.exists())
    provider.prepare_call({'task':'extract'},contract.SCHEMAS['extract'],contract.PHASES['extract'],120)
    self.assertLessEqual(provider.phase_deadline-time.monotonic(),20)
    self.assertEqual(provider.client.call_deadline,provider.phase_deadline)
   finally:provider.close()

 def test_unsupported_native_effort_or_selected_model_has_no_fallback(self):
  class Client:
   def __init__(self,*args,**kwargs):self.diagnostic=None
   def initialize(self):pass
   def request(self,*args):return {'data':[{'model':self.model,'supportedReasoningEfforts':[{'reasoningEffort':x}for x in self.efforts]}]}
   def close(self):pass
  for model,efforts in (('gpt-6.1-sol',['high']),('other-model',['low','medium'])):
   Client.model=model;Client.efforts=efforts
   with tempfile.TemporaryDirectory(dir='/private/tmp')as root,patch.object(inference,'_check_capabilities'),patch.object(inference,'_Client',Client):
    provider=executor.RestrictedModel('fictional',root,lambda:False)
    with self.assertRaises(executor.ExecutorError)as error:provider.prepare()
    self.assertEqual(error.exception.code,'model_unavailable');self.assertIsNone(provider.last_method)

 def test_expiry_after_provisional_guard_prevents_actual_native_write(self):
  with tempfile.TemporaryDirectory(dir='/private/tmp')as root:
   provider,marker=self.model(root);payload={'task':'extract','messages':[]}
   try:
    provider.prepare_call(payload,contract.SCHEMAS['extract'],contract.PHASES['extract'],300)
    guard=provider.client.native_request_guard
    def expire_after_guard(message):
     guard(message);provider.client.call_deadline=time.monotonic()-1
    provider.client.native_request_guard=expire_after_guard
    with self.assertRaises(inference.InferenceCancelled):provider.call(payload,contract.SCHEMAS['extract'],300,0)
    self.assertFalse(marker.exists());self.assertFalse(provider.client.diagnostic['turn_start_requested'])
    self.assertFalse(provider.client.diagnostic['turn_started']);self.assertIsNone(provider.last_method)
   finally:provider.close()

 def test_initialization_cap_rejects_before_constructing_native_client(self):
  clock=[0]
  def capabilities(*args):clock[0]=41
  with tempfile.TemporaryDirectory(dir='/private/tmp')as root,patch.object(executor.time,'monotonic',lambda:clock[0]), \
    patch.object(inference,'_check_capabilities',side_effect=capabilities),patch.object(inference,'_Client')as client:
   provider=executor.RestrictedModel('fictional',root,lambda:False)
   with self.assertRaises(executor.ExecutorError)as error:provider.prepare()
   self.assertEqual(error.exception.code,'deadline_exceeded');client.assert_not_called()
