"""Real stdio bursts from a fictional native process, never provider calls."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

from hoomy import inference


PROGRAM = '''import json,os,sys
options=OPTIONS
marker=MARKER
generation=0
tid='thread'
turn_id='turn'
for line in sys.stdin:
 value=json.loads(line)
 if 'method' not in value:
  if value.get('id')==99 and value.get('error',{}).get('code')==-32601:
   with open(marker,'w') as stream:stream.write('refused')
  continue
 method=value['method']
 if method=='thread/start':
  if generation:
   late={'method':'thread/tokenUsage/updated','params':{'threadId':tid,'turnId':turn_id,'tokenUsage':{'last':{'inputTokens':9000,'outputTokens':9000,'totalTokens':18000}}}}
   print(json.dumps(late),flush=True)
   tid='thread-next';turn_id='turn-next'
  generation+=1
  result={'thread':{'id':tid,'ephemeral':True},'model':'fixture-model'}
 elif method=='mcpServerStatus/list':result={'data':[],'nextCursor':None}
 elif method=='turn/start':
  result={'turn':{'id':'' if options['malformed_ack'] else turn_id}}
  messages=[{'id':value['id'],'result':result}]
  if options['permission']:messages.append({'id':99,'method':'item/tool/call','params':{'private':'never export this fixture'}})
  else:
   usage={'method':'thread/tokenUsage/updated','params':{'threadId':tid,'turnId':turn_id,'tokenUsage':{'last':{'inputTokens':10,'outputTokens':5,'totalTokens':15}}}}
   events={
    'permission_event':{'id':99,'method':'item/tool/call','params':{'private':'never export this fixture'}},
    'started':{'method':'turn/started','params':{'threadId':tid,'turn':{'id':turn_id,'status':'inProgress'}}},
    'usage':usage,
    'wrong_usage':{'method':'thread/tokenUsage/updated','params':{'threadId':tid,'turnId':'foreign','tokenUsage':{'last':{'inputTokens':9000,'outputTokens':9000,'totalTokens':18000}}}},
    'wrong_final':{'method':'item/completed','params':{'threadId':tid,'turnId':'foreign','item':{'type':'agentMessage','text':'private invalid output'}}},
    'wrong_thread':{'method':'turn/completed','params':{'threadId':'foreign','turn':{'id':turn_id,'status':'completed'}}},
    'wrong_turn':{'method':'turn/completed','params':{'threadId':tid,'turn':{'id':'foreign','status':'completed'}}},
    'final':{'method':'item/completed','params':{'threadId':tid,'turnId':turn_id,'item':{'type':'agentMessage','text':'{"brief":{"intentions":[],"outcomes":[],"open_questions":[],"next_steps":[]},"projects":[]}'}}},
    'large_final':{'method':'item/completed','params':{'threadId':tid,'turnId':turn_id,'item':{'type':'agentMessage','text':'x'*1048576}}},
    'complete':{'method':'turn/completed','params':{'threadId':tid,'turn':{'id':turn_id,'status':'completed'}}}}
   messages.extend(events[name] for name in options['order'] if not (options['missing_completion'] and name=='complete') and not (generation==2 and options['second_without_usage'] and name=='usage'))
  if options['missing_ack']:messages=messages[1:]
  elif options['ack_last']:messages=messages[1:]+messages[:1]
  os.write(sys.stdout.fileno(),(''.join(json.dumps(message)+'\\n' for message in messages)).encode())
  if options['exit_after_burst']:sys.exit(0)
  continue
 else:raise RuntimeError('unexpected_fixture_request')
 print(json.dumps({'id':value['id'],'result':result}),flush=True)
'''


class NativeTransportTests(unittest.TestCase):
    def run_burst(self, order, *, permission=False, missing_completion=False, ack_last=False,
                  missing_ack=False, malformed_ack=False, overflow=False, second_without_usage=False,
                  usage_expected=True, exit_after_burst=False, terminal_permission=False):
        with tempfile.TemporaryDirectory() as directory:
            runtime=Path(directory);binary=runtime/'fake-native';marker=runtime/'permission-denied'
            options={key:value for key,value in locals().copy().items() if key in
                {'order','permission','missing_completion','ack_last','missing_ack','malformed_ack','second_without_usage','exit_after_burst'}}
            binary.write_text('#!'+sys.executable+'\n'+PROGRAM.replace('OPTIONS',repr(options)).replace('MARKER',repr(str(marker))))
            binary.chmod(0o700)
            client=inference._Client(str(binary),str(runtime),lambda:False)
            client.turn_timeout=0.2
            original_request=client.request
            client.request=lambda method,params:original_request(method,params,timeout=0.2 if method=="turn/start" and missing_ack else 5)
            seen=[];client.usage_observer=seen.append
            try:
                if permission or terminal_permission or missing_completion or missing_ack or malformed_ack or overflow or not usage_expected:
                    with self.assertRaises(inference.InferenceError):client.extract({'messages':[]},None)
                    diagnostic=dict(client.diagnostic)
                    if permission or terminal_permission:
                        # The fake process must receive an actual refusal, not a timeout.
                        import time
                        deadline=time.monotonic()+1
                        while not marker.exists() and time.monotonic()<deadline:time.sleep(0.01)
                        self.assertTrue(marker.exists())
                    if missing_ack or malformed_ack or overflow:
                        self.assertFalse(diagnostic['turn_started'])
                        self.assertFalse(diagnostic['completion_received'])
                        self.assertEqual(seen,[])
                    return diagnostic
                output,model=client.extract({'messages':[]},None)
                self.assertEqual(output,{'brief':{key:[] for key in inference.SECTIONS},'projects':[]})
                self.assertEqual(model,'fixture-model')
                self.assertEqual(client.metrics['token_usage']['totalTokens'],15)
                self.assertTrue(client.diagnostic['turn_started'])
                self.assertTrue(client.diagnostic['completion_received'])
                self.assertEqual(len(seen),1 if usage_expected else 0)
                if second_without_usage:
                    with self.assertRaises(inference.InferenceError):client.extract({'messages':[]},None)
                    self.assertEqual(client.diagnostic['category'],'usage_unknown')
                    self.assertEqual(len(seen),1)
            finally:client.close()

    def test_ack_notifications_and_completion_one_write_then_idle_are_consumed(self):
        for order in (['usage','final','complete'],['final','usage','complete'],
                ['wrong_thread','wrong_turn','usage','final','complete']):
            with self.subTest(order=order):self.run_burst(order)

    def test_notifications_before_valid_rpc_ack_are_not_lost(self):
        self.run_burst(['started','usage','final','complete'],ack_last=True)

    def test_wrong_turn_usage_and_final_are_ignored_before_and_after_ack(self):
        for ack_last in (False,True):
            self.run_burst(['usage','final','wrong_usage','wrong_final','complete'],ack_last=ack_last)

    def test_no_ack_or_malformed_ack_never_replay_unconfirmed_output(self):
        for settings in ({'missing_ack':True},{'malformed_ack':True,'ack_last':True}):
            self.run_burst(['started','usage','final','complete'],**settings)

    def test_pre_ack_event_count_and_byte_overflow_stop_without_fake_completion(self):
        self.run_burst(['usage']*65,ack_last=True,overflow=True)
        self.run_burst(['large_final','complete'],ack_last=True,overflow=True)

    def test_duplicate_and_buffered_late_usage_are_observed_once(self):
        self.run_burst(['usage','final','usage','complete','usage'],ack_last=True)
        self.run_burst(['final','complete','usage'],ack_last=True)
        self.run_burst(['usage','usage','final','complete'])

    def test_post_ack_usage_after_terminal_is_observed(self):
        self.run_burst(['final','complete','wrong_usage','usage'])

    def test_completion_without_usage_remains_unknown(self):
        diagnostic=self.run_burst(['final','complete'],usage_expected=False)
        self.assertTrue(diagnostic['completion_received'])
        self.assertEqual(diagnostic['category'],'usage_unknown')

    def test_eof_after_terminal_preserves_known_snapshot_only(self):
        self.run_burst(['usage','final','complete'],exit_after_burst=True)
        diagnostic=self.run_burst(['final','complete'],exit_after_burst=True,usage_expected=False)
        self.assertEqual(diagnostic['category'],'usage_unknown')

    def test_permission_after_terminal_still_receives_explicit_refusal(self):
        diagnostic=self.run_burst(['usage','final','complete','permission_event'],terminal_permission=True)
        self.assertTrue(diagnostic['completion_received'])
        self.assertEqual(diagnostic['category'],'boundary_refusal')

    def test_next_call_drops_old_deferred_and_late_usage(self):
        self.run_burst(['usage','final','complete','usage','complete'],ack_last=True,second_without_usage=True)

    def test_reasoning_and_aggregated_turn_items_are_not_retained(self):
        secret='private hidden fixture'
        event={'method':'item/completed','params':{'threadId':'thread','turnId':'turn',
            'item':{'type':'reasoning','text':secret,'summary':secret}}}
        self.assertNotIn(secret,json.dumps(inference._Client._turn_notification(event,'thread')))
        event={'method':'turn/completed','params':{'threadId':'thread','turn':{'id':'turn',
            'status':'completed','items':[secret]}}}
        self.assertNotIn(secret,json.dumps(inference._Client._turn_notification(event,'thread')))

    def test_missing_terminal_times_out_without_fabricating_completion(self):
        diagnostic=self.run_burst(['usage','final','complete'],missing_completion=True)
        self.assertEqual(diagnostic['stage'],'turn_wait')
        self.assertEqual(diagnostic['category'],'transport_or_timeout')
        self.assertTrue(diagnostic['turn_started'])
        self.assertFalse(diagnostic['completion_received'])

    def test_server_permission_request_is_replied_refused_and_stops(self):
        for ack_last in (False,True):
            diagnostic=self.run_burst([],permission=True,ack_last=ack_last)
            self.assertEqual(diagnostic['category'],'boundary_refusal')
            self.assertEqual(diagnostic['turn_started'],not ack_last)
            self.assertFalse(diagnostic['completion_received'])
