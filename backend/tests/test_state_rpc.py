import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

from hoomy.state_executor import NativeController,ExecutorError

PROGRAM='''import json,sys
mode=MODE
held=None
for line in sys.stdin:
 v=json.loads(line)
 if 'id'not in v:continue
 method=v['method']
 if method=='initialize':result={}
 elif method=='thread/start':result={'thread':{'id':'fictional-thread','ephemeral':True}}
 else:
  if mode=='timeout':continue
  if mode=='eof':sys.exit(0)
  if mode=='error':
   print(json.dumps({'id':v['id'],'error':{'code':-32603,'message':'PRIVATE_PROVIDER_PROSE_TOKEN'}}),flush=True);continue
  if mode=='unknown_error':
   print(json.dumps({'id':v['id'],'error':{'code':['PRIVATE_VALUE'],'message':'PRIVATE_PROVIDER_PROSE_TOKEN'}}),flush=True);continue
  result={'structuredContent':{'project_id':'fictional-project','pending_run':None}} if mode=='pending'else {'structuredContent':{'status':'running'}}
  if mode=='tool_error':result={'isError':True,'content':[{'type':'text','text':'PRIVATE_TOOL_CONTENT_TOKEN'}]}
  if mode=='malformed':result={'content':[{'type':'text','text':'PRIVATE_TOOL_CONTENT_TOKEN'}]}
  if mode=='bad_result':result=['PRIVATE_RPC_RESULT_TOKEN']
  if mode=='concurrent':
   event={'id':v['id'],'result':result}
   if held is None:held=event;continue
   print(json.dumps(event)+'\\n'+json.dumps(held),flush=True);held=None;continue
 print(json.dumps({'id':v['id'],'result':result}),flush=True)
'''


class RpcDiagnosticsTests(unittest.TestCase):
 def setup_client(self,root,mode,observer):
  binary=Path(root)/'fake-native';binary.write_text('#!'+sys.executable+'\n'+PROGRAM.replace('MODE',repr(mode)));binary.chmod(0o700)
  client=NativeController(str(binary),root,rpc_observer=observer);client.initialize();return client

 def test_closed_outcomes_never_export_native_prose_values_or_params(self):
  cases={'success':'rpc_success','error':'native_rpc_error','unknown_error':'native_rpc_error',
   'tool_error':'tool_error','malformed':'malformed_tool_result','bad_result':'malformed_result','eof':'process_eof'}
  for mode,outcome in cases.items():
   with self.subTest(mode=mode),tempfile.TemporaryDirectory(dir='/private/tmp')as root:
    records=[];client=self.setup_client(root,mode,records.append);stdout=io.StringIO()
    try:
     with contextlib.redirect_stdout(stdout):
      if mode=='success':client.worker({'action':'heartbeat','private_secret':'PRIVATE_INPUT_TOKEN'})
      else:
       with self.assertRaises(ExecutorError):client.worker({'action':'heartbeat','private_secret':'PRIVATE_INPUT_TOKEN'})
     record=records[-1];self.assertEqual(record['outcome'],outcome);self.assertEqual(record['operation'],'heartbeat')
     self.assertEqual(set(record),{'operation','outcome','error_code','timeout_ms','pending_before','id_collision','duration_ms'})
     self.assertEqual(record['error_code'],'internal_error'if mode=='error'else'unknown'if mode=='unknown_error'else'none')
     self.assertNotIn('PRIVATE',json.dumps(records));self.assertEqual(stdout.getvalue(),'')
     self.assertEqual(client.rpc_local.last,record)
    finally:client.close()

 def test_timeout_and_bad_input_leave_no_waiter_or_content(self):
  with tempfile.TemporaryDirectory(dir='/private/tmp')as root:
   records=[];client=self.setup_client(root,'timeout',records.append)
   try:
    with self.assertRaises(ExecutorError):client.request('mcpServer/tool/call',{'arguments':{'action':'heartbeat'}},timeout=0.03)
    self.assertEqual(records[-1]['outcome'],'rpc_timeout');self.assertEqual(records[-1]['timeout_ms'],30)
    self.assertEqual(client._waiters,{})
    for bad in (True,0,float('nan'),[],None):
     with self.assertRaises(ExecutorError):client.request('initialize',{},timeout=bad)
    self.assertEqual(client._waiters,{})
   finally:client.close()

 def test_concurrent_waiters_demultiplex_reversed_responses_without_collision(self):
  with tempfile.TemporaryDirectory(dir='/private/tmp')as root:
   records=[];client=self.setup_client(root,'concurrent',records.append);results=[];local=[]
   def request():
    results.append(client.worker({'action':'heartbeat'}));local.append(client.rpc_local.last)
   try:
    threads=[threading.Thread(target=request)for _ in range(2)]
    for thread in threads:thread.start()
    for thread in threads:thread.join(timeout=3);self.assertFalse(thread.is_alive())
    self.assertEqual(len(results),2);self.assertEqual(client._waiters,{})
    observed=[r for r in records if r['operation']=='heartbeat']
    self.assertEqual(sorted(r['pending_before']for r in observed),[0,1])
    self.assertTrue(all(not r['id_collision']for r in observed));self.assertEqual(len(local),2)
   finally:client.close()

 def test_observer_failure_cannot_change_rpc_or_error_outcome(self):
  def broken(record):raise RuntimeError('PRIVATE_CALLBACK_TOKEN')
  for mode in ('success','error'):
   with tempfile.TemporaryDirectory(dir='/private/tmp')as root:
    client=self.setup_client(root,mode,broken)
    try:
     if mode=='success':self.assertEqual(client.worker({'action':'heartbeat'}),{'status':'running'})
     else:
      with self.assertRaises(ExecutorError)as error:client.worker({'action':'heartbeat'})
      self.assertEqual(error.exception.code,'ambiguous_completion')
     self.assertEqual(client._waiters,{})
    finally:client.close()

 def test_actual_pending_discovery_method_is_not_shadowed_by_waiter_map(self):
  with tempfile.TemporaryDirectory(dir='/private/tmp')as root:
   records=[];client=self.setup_client(root,'pending',records.append)
   try:
    result=client.pending('fictional-project')
    self.assertEqual(result,{'project_id':'fictional-project','pending_run':None})
    self.assertEqual(client._waiters,{})
    self.assertEqual(records[-1]['operation'],'cached_tool')
   finally:client.close()
