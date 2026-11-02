"""Exercise tool recovery without executing a client, plugin, or real tool."""
from contextlib import ExitStack
import copy
from http.server import ThreadingHTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request

from echo_server import ArchiveSet, EchoState, Handler
from evoagent.echo_adapters import adapter_for
from evoagent.echo_context import LiveTranscript


class ToolRecoveryTests(unittest.TestCase):
    def test_internal_memory_cycles_stop_with_a_specific_incomplete_reason(self):
        for queries,expected_calls in [(['one','two','three','four'],12),([''],3)]:
            with self.subTest(queries=queries), ExitStack() as stack:
                root=Path(stack.enter_context(tempfile.TemporaryDirectory()))
                archives=ArchiveSet(root,idle_seconds=60);stack.callback(archives.close)
                state=EchoState(archives,'http://127.0.0.1:1',0,4,False);stack.callback(state.memory_controller.close)
                state._ctx_size=32768;state.count_tokens=lambda text:max(1,len(str(text))//4)
                state._memory_adapter=adapter_for({'architecture':'qwen','revision':'fixture'},state.count_tokens)
                state.memory_adapter=lambda:state._memory_adapter;state.backend_session_metrics=lambda *_:{}
                handler=Handler.__new__(Handler);handler.state=state;handler.headers={}
                requests=[]
                def generate(body,phase):
                    query=queries[len(requests)%len(queries)];requests.append(copy.deepcopy(body))
                    return {'choices':[{'message':{'role':'assistant','content':
                        '<echo>'+json.dumps({'op':'search','query':query})+'</echo>'},'finish_reason':'stop'}]}
                handler._generate_live=generate;handler._send_json=lambda code,value:(code,value)
                code,result=handler._controlled_context_serial({'messages':[{'role':'user',
                    'content':'Find the earlier project decision.'}],'max_tokens':512,'echo_max_calls':16},
                    'memory-cycle','off',archive_input=False)
                self.assertEqual(code,422)
                self.assertEqual(result['echo'].get('stop_reason'),'repeated_memory_command')
                self.assertEqual(len(requests),expected_calls)
                self.assertIn('incomplete',result['error']['message'].lower())
                if queries==['']:
                    self.assertTrue(any('query is required' in str(message.get('content'))
                                        for message in requests[1]['messages']))

    def test_repeated_read_guard_retains_history_across_compaction(self):
        with ExitStack() as stack:
            root=Path(stack.enter_context(tempfile.TemporaryDirectory()))
            archives=ArchiveSet(root,idle_seconds=60);stack.callback(archives.close)
            state=EchoState(archives,'http://127.0.0.1:1',0,4,False);stack.callback(state.memory_controller.close)
            state._ctx_size=8192;state.count_tokens=lambda text:max(1,len(str(text))//4)
            state._memory_adapter=adapter_for({'architecture':'qwen','revision':'fixture'},state.count_tokens)
            state.memory_adapter=lambda:state._memory_adapter;state.backend_session_metrics=lambda *_:{}
            conversation='cycle-after-compaction';live=LiveTranscript(archives.get(conversation),conversation)
            user={'role':'user','content':'Fix the game.'};live.start_turn(user['content'],state.count_tokens)
            history=[]
            for index in range(3):
                assistant={'role':'assistant','content':'','tool_calls':[{'id':str(index),'type':'function',
                    'function':{'name':'Read','arguments':'{"path":"games/snake.html"}'}}]}
                result={'role':'tool','tool_call_id':str(index),'content':'Same file contents'}
                history.extend([assistant,result]);live.append(assistant,state.count_tokens);live.append(result,state.count_tokens)
            live.append_generated({'role':'assistant','content':'old analysis '*1600},state.count_tokens)
            live.append_generated({'role':'assistant','content':'Continue.'},state.count_tokens)
            self.assertGreater(live.compact(2000,state.count_tokens),0);live.save()
            handler=Handler.__new__(Handler);handler.state=state;handler.headers={'x-opencore-timeline-owner':'app'}
            requests=[];handler._generate_live=lambda body,phase:(requests.append(copy.deepcopy(body)) or
                {'choices':[{'message':{'role':'assistant','content':'I will use the existing result.'},'finish_reason':'stop'}]})
            handler._send_json=lambda code,value:(code,value)
            tool={'type':'function','function':{'name':'Read','parameters':{'type':'object','properties':{}}}}
            handler._controlled_context_serial({'messages':[user,*history[-2:]],'tools':[tool],'max_tokens':512},conversation,'off',archive_input=False)
            self.assertTrue(any('repeated the same inspection' in str(m.get('content')) for m in requests[0]['messages']))

    def test_saved_checkpoint_does_not_evict_a_task_that_still_fits(self):
        with ExitStack() as stack:
            root=Path(stack.enter_context(tempfile.TemporaryDirectory()))
            archives=ArchiveSet(root,idle_seconds=60);stack.callback(archives.close)
            state=EchoState(archives,'http://127.0.0.1:1',0,4,False);stack.callback(state.memory_controller.close)
            state._ctx_size=32768;state.count_tokens=lambda text:max(1,len(str(text))//4)
            state._memory_adapter=adapter_for({'architecture':'qwen','revision':'fixture'},state.count_tokens)
            state.memory_adapter=lambda:state._memory_adapter;state.backend_session_metrics=lambda *_:{}
            conversation='saved-checkpoint';live=LiveTranscript(archives.get(conversation),conversation)
            user={'role':'user','content':'Fix the game.'};live.start_turn(user['content'],state.count_tokens)
            call={'id':'search','type':'function','function':{'name':'workspace','arguments':'{"action":"search"}'}}
            live.append({'role':'assistant','content':'','tool_calls':[call]},state.count_tokens)
            live.append({'role':'tool','tool_call_id':'search','content':'Found games/snake.html'},state.count_tokens)
            live.append_generated({'role':'assistant','content':'evidence '*4000},state.count_tokens);live.save()
            handler=Handler.__new__(Handler);handler.state=state;handler.headers={'x-opencore-timeline-owner':'app'}
            requests=[];handler._generate_live=lambda body,phase:(requests.append(copy.deepcopy(body)) or
                {'choices':[{'message':{'role':'assistant','content':'Proceeding.'},'finish_reason':'stop'}]})
            handler._send_json=lambda code,value:(code,value)
            tail=[{'role':'assistant','content':'','tool_calls':[{'id':'saved','type':'function','function':{'name':'workspace','arguments':'{"action":"checkpoint"}'}}]},
                  {'role':'tool','tool_call_id':'saved','content':'{"checkpointSaved":true}'}]
            tool={'type':'function','function':{'name':'workspace','parameters':{'type':'object','properties':{}}}}
            handler._controlled_context_serial({'messages':[user,*tail],'tools':[tool],'max_tokens':512},conversation,'off',archive_input=False)
            self.assertTrue(any(m.get('content')=='Found games/snake.html' for m in requests[0]['messages']))
            self.assertEqual(LiveTranscript(archives.get(conversation),conversation).compactions,0)

    def test_tool_history_replayed_by_client_is_not_readded_after_compaction(self):
        with ExitStack() as stack:
            root=Path(stack.enter_context(tempfile.TemporaryDirectory()))
            archives=ArchiveSet(root,idle_seconds=60);stack.callback(archives.close)
            state=EchoState(archives,'http://127.0.0.1:1',0,4,False);stack.callback(state.memory_controller.close)
            state._ctx_size=8192;state.count_tokens=lambda text:max(1,len(str(text))//4)
            state._memory_adapter=adapter_for({'architecture':'qwen','revision':'fixture'},state.count_tokens)
            state.memory_adapter=lambda:state._memory_adapter;state.backend_session_metrics=lambda *_:{}
            conversation='replayed-tools';live=LiveTranscript(archives.get(conversation),conversation)
            user={'role':'user','content':'Fix the game.'};live.start_turn(user['content'],state.count_tokens)
            first={'role':'assistant','content':'','tool_calls':[{'id':'search','type':'function','function':{'name':'workspace','arguments':'{"action":"search"}'}}]}
            result={'role':'tool','tool_call_id':'search','content':'Found games/snake.html'}
            live.append(first,state.count_tokens);live.append(result,state.count_tokens)
            live.append_generated({'role':'assistant','content':'old analysis '*1600},state.count_tokens)
            live.append_generated({'role':'assistant','content':'Proceed with the edit.'},state.count_tokens)
            self.assertGreater(live.compact(600,state.count_tokens),0);live.save()
            handler=Handler.__new__(Handler);handler.state=state;handler.headers={'x-opencore-timeline-owner':'app'}
            requests=[];handler._generate_live=lambda body,phase:(requests.append(copy.deepcopy(body)) or
                {'choices':[{'message':{'role':'assistant','content':'Proceeding.'},'finish_reason':'stop'}]})
            handler._send_json=lambda code,value:(code,value)
            tool={'type':'function','function':{'name':'workspace','parameters':{'type':'object','properties':{}}}}
            new_call={'role':'assistant','content':'','tool_calls':[{'id':'edit','type':'function','function':{'name':'workspace','arguments':'{"action":"edit"}'}}]}
            new_result={'role':'tool','tool_call_id':'edit','content':'Edit completed'}
            handler._controlled_context_serial({'messages':[user,first,result,new_call,new_result],'tools':[tool],'max_tokens':512},conversation,'off',archive_input=False)
            messages=requests[0]['messages']
            self.assertEqual(sum(m.get('tool_call_id')=='search' for m in messages),0)
            self.assertEqual(sum('Found games/snake.html' in str(m.get('content')) for m in messages),1)
            self.assertEqual(sum(m.get('tool_call_id')=='edit' for m in messages),1)

    def test_agent_receives_completed_tool_evidence_after_real_context_pressure(self):
        with ExitStack() as stack:
            root=Path(stack.enter_context(tempfile.TemporaryDirectory()))
            archives=ArchiveSet(root,idle_seconds=60);stack.callback(archives.close)
            state=EchoState(archives,'http://127.0.0.1:1',0,4,False);stack.callback(state.memory_controller.close)
            state._ctx_size=8192;state.count_tokens=lambda text:max(1,len(str(text))//4)
            state._memory_adapter=adapter_for({'architecture':'qwen','revision':'fixture'},state.count_tokens)
            state.memory_adapter=lambda:state._memory_adapter;state.backend_session_metrics=lambda *_:{}
            live=LiveTranscript(archives.get('pressure-fixture'),'pressure-fixture')
            user={'role':'user','content':'Fix the game.'};live.start_turn(user['content'],state.count_tokens)
            call={'id':'search','type':'function','function':{'name':'workspace','arguments':'{"action":"search","query":"snake"}'}}
            live.append({'role':'assistant','content':'','tool_calls':[call]},state.count_tokens)
            live.append({'role':'tool','tool_call_id':'search','content':'Found games/snake.html'},state.count_tokens)
            live.append_generated({'role':'assistant','content':'old analysis '*2400},state.count_tokens)
            live.save()
            handler=Handler.__new__(Handler);handler.state=state;handler.headers={'x-opencore-timeline-owner':'app'}
            requests=[]
            handler._generate_live=lambda body,phase:(requests.append(copy.deepcopy(body)) or
                {'choices':[{'message':{'role':'assistant','content':'Proceeding with the edit.'},'finish_reason':'stop'}]})
            handler._send_json=lambda code,value:(code,value)
            tail=[{'role':'assistant','content':'','tool_calls':[{'id':'next','type':'function','function':{'name':'workspace','arguments':'{"action":"status"}'}}]},
                  {'role':'tool','tool_call_id':'next','content':'ready'}]
            tool={'type':'function','function':{'name':'workspace','parameters':{'type':'object','properties':{}}}}
            handler._controlled_context_serial({'messages':[user,*tail],'tools':[tool],'max_tokens':2048},'pressure-fixture','off',archive_input=False)
            messages=requests[0]['messages']
            self.assertTrue(any('Found games/snake.html' in str(m.get('content')) for m in messages))
            self.assertTrue(any('working-task checkpoint' in str(m.get('content')) for m in messages))
            self.assertGreater(LiveTranscript(archives.get('pressure-fixture'),'pressure-fixture').compactions,0)
            self.assertLessEqual(requests[0]['max_tokens'],2048)

    def test_large_requested_output_does_not_evict_evidence_that_still_fits(self):
        with ExitStack() as stack:
            root=Path(stack.enter_context(tempfile.TemporaryDirectory()))
            archives=ArchiveSet(root,idle_seconds=60);stack.callback(archives.close)
            state=EchoState(archives,'http://127.0.0.1:1',0,4,False);stack.callback(state.memory_controller.close)
            state._ctx_size=32768;state.count_tokens=lambda text:max(1,len(str(text))//4)
            state._memory_adapter=adapter_for({'architecture':'qwen','revision':'fixture'},state.count_tokens)
            state.memory_adapter=lambda:state._memory_adapter
            state.backend_session_metrics=lambda *_:{}
            live=LiveTranscript(archives.get('capacity-fixture'),'capacity-fixture')
            user={'role':'user','content':'Fix the game.'};live.start_turn(user['content'],state.count_tokens)
            call={'id':'search','type':'function','function':{'name':'workspace','arguments':'{"action":"search"}'}}
            live.append({'role':'assistant','content':'','tool_calls':[call]},state.count_tokens)
            live.append({'role':'tool','tool_call_id':'search','content':'Found games/snake.html'},state.count_tokens)
            live.append_generated({'role':'assistant','content':'evidence '*4000},state.count_tokens)
            live.save()
            handler=Handler.__new__(Handler);handler.state=state;handler.headers={'x-opencore-timeline-owner':'app'}
            requests=[]
            handler._generate_live=lambda body,phase:(requests.append(copy.deepcopy(body)) or
                {'choices':[{'message':{'role':'assistant','content':'Proceeding with the edit.'},'finish_reason':'stop'}]})
            handler._send_json=lambda code,value:(code,value)
            tail=[{'role':'assistant','content':'','tool_calls':[{'id':'next','type':'function','function':{'name':'workspace','arguments':'{"action":"status"}'}}]},
                  {'role':'tool','tool_call_id':'next','content':'ready'}]
            tool={'type':'function','function':{'name':'workspace','parameters':{'type':'object','properties':{}}}}
            handler._controlled_context_serial({'messages':[user,*tail],'tools':[tool],'max_tokens':16384},'capacity-fixture','off',archive_input=False)
            messages=requests[0]['messages']
            self.assertTrue(any('Found games/snake.html' in str(m.get('content')) for m in messages),
                            'Output reservation must not discard a tool result that fits in the window')

    def test_json_agent_request_inspects_a_stream_and_returns_a_valid_json_tool_call(self):
        with ExitStack() as stack:
            root=Path(stack.enter_context(tempfile.TemporaryDirectory()))
            archives=ArchiveSet(root,idle_seconds=60);stack.callback(archives.close)
            state=EchoState(archives,'http://127.0.0.1:1',0,4,False);stack.callback(state.memory_controller.close)
            state._ctx_size=32768;state.count_tokens=lambda text:max(1,len(str(text))//4)
            state._memory_adapter=adapter_for({'architecture':'qwen','revision':'fixture'},state.count_tokens)
            state.memory_adapter=lambda:state._memory_adapter
            name='mcp__opencore__dev';requests=[]
            class TestHandler(Handler):
                def log_message(self,*_):pass
                def _upstream(self,path,body,stream):
                    requests.append(copy.deepcopy(body))
                    call={'index':0,'id':'actual-call','function':{'name':name,'arguments':json.dumps({'action':'status'})}}
                    events=[{'choices':[{'delta':{'tool_calls':[call]}}]},
                            {'choices':[{'delta':{},'finish_reason':'tool_calls'}]}]
                    return io.BytesIO((''.join('data: '+json.dumps(e)+'\n\n' for e in events)+'data: [DONE]\n\n').encode())
            TestHandler.state=state
            server=ThreadingHTTPServer(('127.0.0.1',0),TestHandler)
            stack.callback(server.server_close);stack.callback(server.shutdown)
            threading.Thread(target=server.serve_forever,daemon=True).start()
            tool={'type':'function','function':{'name':name,'parameters':{'type':'object','properties':{'action':{'enum':['status']}}}}}
            payload={'messages':[{'role':'user','content':'Check the workspace.'}],'tools':[tool],'max_tokens':1024,'stream':False}
            request=urllib.request.Request('http://127.0.0.1:%d/v1/chat/completions'%server.server_port,
                data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','X-OpenCore-Timeline-Owner':'app','X-Echo-Conversation':'buffered-fixture'})
            with urllib.request.urlopen(request,timeout=10) as response:
                self.assertEqual(response.headers.get_content_type(),'application/json')
                result=json.load(response)
            self.assertTrue(requests[0]['stream'])
            self.assertNotIn('_echo_buffer_preview',requests[0])
            self.assertEqual(result['choices'][0]['message']['tool_calls'][0]['id'],'actual-call')

    def test_codex_continuation_keeps_prior_answer_without_repeating_the_user_turn(self):
        with ExitStack() as stack:
            root=Path(stack.enter_context(tempfile.TemporaryDirectory()))
            archives=ArchiveSet(root,idle_seconds=60);stack.callback(archives.close)
            state=EchoState(archives,'http://127.0.0.1:1',0,4,False);stack.callback(state.memory_controller.close)
            state._ctx_size=32768;state.count_tokens=lambda text:max(1,len(str(text))//4)
            state._memory_adapter=adapter_for({'architecture':'qwen','revision':'fixture'},state.count_tokens)
            state.memory_adapter=lambda:state._memory_adapter
            state.backend_session_metrics=lambda *_:{}
            handler=Handler.__new__(Handler);handler.state=state;handler.headers={'x-opencore-timeline-owner':'app'}
            requests=[]
            replies=['I inspected the workspace.','I am writing the requested file.','I will handle the repeated request separately.']
            def generate(body,phase):
                requests.append(copy.deepcopy(body))
                return {'choices':[{'message':{'role':'assistant','content':replies[len(requests)-1]},'finish_reason':'stop'}]}
            handler._generate_live=generate;handler._send_json=lambda code,value:(code,value)
            tool={'type':'function','function':{'name':'mcp__opencore__dev','parameters':{'type':'object','properties':{}}}}
            user={'role':'user','content':'Build the snake game.'}
            first=handler._controlled_context_serial({'messages':[user],'tools':[tool],'max_tokens':1024},'same-turn','off',archive_input=False)[1]['choices'][0]['message']
            self.assertTrue(requests[0]['stream'],'Agent calls need an internally inspected stream')
            self.assertTrue(requests[0]['_echo_buffer_preview'],'A JSON client must not receive SSE previews')
            second=handler._controlled_context_serial({'messages':[user,first],'tools':[tool],'max_tokens':1024},'same-turn','off',archive_input=False)[1]['choices'][0]['message']
            visible=[(m['role'],m.get('content')) for m in requests[1]['messages'] if m.get('role') in ('user','assistant')]
            self.assertEqual(visible.count(('user',user['content'])),1)
            self.assertIn(('assistant',first['content']),visible)
            handler._controlled_context_serial({'messages':[user,first,second,user],'tools':[tool],'max_tokens':1024},'same-turn','off',archive_input=False)
            self.assertEqual(sum(m.get('role')=='user' and m.get('content')==user['content'] for m in requests[2]['messages']),2)

    def test_desktop_timeline_header_reaches_the_guard_over_http(self):
        with ExitStack() as stack:
            root=Path(stack.enter_context(tempfile.TemporaryDirectory()))
            archives=ArchiveSet(root,idle_seconds=60);stack.callback(archives.close)
            state=EchoState(archives,'http://127.0.0.1:1',0,4,False);stack.callback(state.memory_controller.close)
            state._ctx_size=32768;state.count_tokens=lambda text:max(1,len(str(text))//4)
            state._memory_adapter=adapter_for({'architecture':'qwen','revision':'fixture'},state.count_tokens)
            state.memory_adapter=lambda:state._memory_adapter
            name='mcp__opencore__dev';requests=[]
            class TestHandler(Handler):
                def log_message(self,*_):pass
                def _generate_live(self,body,phase):
                    requests.append(copy.deepcopy(body))
                    if any('previous attempt repeated a plan' in str(m.get('content','')).lower() for m in body['messages']):
                        call={'id':'action','type':'function','function':{'name':name,'arguments':json.dumps({'action':'status'})}}
                        return {'choices':[{'message':{'role':'assistant','content':'','tool_calls':[call]},'finish_reason':'tool_calls'}]}
                    return {'choices':[{'message':{'role':'assistant','content':"I'll inspect the workspace before building the snake game."},'finish_reason':'stop'}]}
            TestHandler.state=state
            server=ThreadingHTTPServer(('127.0.0.1',0),TestHandler)
            stack.callback(server.server_close);stack.callback(server.shutdown)
            threading.Thread(target=server.serve_forever,daemon=True).start()
            plan="I'll inspect the workspace before building the polished snake game."
            history=[{'role':'user','content':'Make an HTML snake game.'}]+[{'role':'assistant','content':plan} for _ in range(10)]
            tool={'type':'function','function':{'name':name,'parameters':{'type':'object','properties':{'action':{'enum':['status']}}}}}
            payload={'messages':history,'tools':[tool],'max_tokens':1024,'stream':False}
            request=urllib.request.Request('http://127.0.0.1:%d/v1/chat/completions'%server.server_port,
                data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','X-OpenCore-Timeline-Owner':'app','X-Echo-Conversation':'desktop-fixture'})
            with urllib.request.urlopen(request,timeout=10) as response:result=json.load(response)
            self.assertEqual(result['choices'][0]['message']['tool_calls'][0]['id'],'action')
            self.assertEqual(requests[0]['reasoning_budget_tokens'],0)

    def test_codex_repeated_workspace_cycle_is_not_executed_again(self):
        with ExitStack() as stack:
            root=Path(stack.enter_context(tempfile.TemporaryDirectory()))
            archives=ArchiveSet(root,idle_seconds=60);stack.callback(archives.close)
            state=EchoState(archives,'http://127.0.0.1:1',0,4,False);stack.callback(state.memory_controller.close)
            state._ctx_size=32768;state.count_tokens=lambda text:max(1,len(str(text))//4)
            state._memory_adapter=adapter_for({'architecture':'qwen','revision':'fixture'},state.count_tokens)
            state.memory_adapter=lambda:state._memory_adapter
            handler=Handler.__new__(Handler);handler.state=state;handler.headers={'x-opencore-timeline-owner':'app'}
            name='mcp__opencore__dev';requests=[]
            def generate(body,phase):
                requests.append(copy.deepcopy(body))
                call={'id':'duplicate','type':'function','function':{'name':name,'arguments':json.dumps({'action':'status'})}}
                return {'choices':[{'message':{'role':'assistant','content':'','tool_calls':[call]},'finish_reason':'tool_calls'}]}
            handler._generate_live=generate;handler._send_json=lambda code,value:(code,value)
            tool={'type':'function','function':{'name':name,'parameters':{'type':'object','properties':{'action':{'enum':['status']}}}}}
            history=[{'role':'user','content':'Make an HTML snake game.'}]
            for index in range(3):
                call={'id':str(index),'type':'function','function':{'name':name,'arguments':json.dumps({'action':'status'})}}
                history.extend([{'role':'assistant','content':'','tool_calls':[call]},
                                {'role':'tool','tool_call_id':str(index),'content':json.dumps({'workspace':'game','files':[]})}])
            with self.assertRaisesRegex(ValueError,'tool action with unchanged results.*task is incomplete'):
                handler._controlled_context_serial({'messages':history,'tools':[tool],'max_tokens':1024,'stream':False},'fixture','off')
            self.assertEqual(requests[0]['reasoning_budget_tokens'],0)
            self.assertTrue(any('repeated the same inspection' in str(m.get('content','')) for m in requests[0]['messages']))

    def test_codex_history_of_repeated_plans_recovers_before_another_plan_is_returned(self):
        with ExitStack() as stack:
            root=Path(stack.enter_context(tempfile.TemporaryDirectory()))
            archives=ArchiveSet(root,idle_seconds=60);stack.callback(archives.close)
            state=EchoState(archives,'http://127.0.0.1:1',0,4,False);stack.callback(state.memory_controller.close)
            state._ctx_size=32768;state.count_tokens=lambda text:max(1,len(str(text))//4)
            state._memory_adapter=adapter_for({'architecture':'qwen','revision':'fixture'},state.count_tokens)
            state.memory_adapter=lambda:state._memory_adapter
            # The native gateway translates Responses requests to chat requests
            # and forwards timeline ownership, not the original harness header.
            handler=Handler.__new__(Handler);handler.state=state;handler.headers={'x-opencore-timeline-owner':'app'}
            calls=[];name='mcp__opencore__dev'
            plans=["Empty workspace. I'll build a polished, feature-rich snake game.",
                   "Empty workspace, so I'll create a complete snake HTML game from scratch.",
                   "I'll build a polished single-file snake game with controls and a high score.",
                   "Empty workspace. Let me build a complete, polished snake game."]
            def generate(body,phase):
                calls.append(copy.deepcopy(body))
                if any('previous attempt repeated a plan' in str(m.get('content','')).lower() for m in body['messages']):
                    call={'id':'actual-action','type':'function','function':{'name':name,'arguments':json.dumps({'action':'status'})}}
                    return {'choices':[{'message':{'role':'assistant','content':'','tool_calls':[call]},'finish_reason':'tool_calls'}]}
                return {'choices':[{'message':{'role':'assistant','content':plans[0]},'finish_reason':'stop'}]}
            handler._generate_live=generate;handler._send_json=lambda code,value:(code,value)
            tool={'type':'function','function':{'name':name,'parameters':{'type':'object','properties':{'action':{'enum':['status']}}}}}
            history=[{'role':'user','content':'Make an HTML snake game.'}]+[{'role':'assistant','content':text} for text in plans*3]
            code,result=handler._controlled_context_serial({'messages':history,'tools':[tool],'max_tokens':1024,'stream':False},'fixture','off')
            self.assertEqual(code,200)
            self.assertIn('tool_calls',result['choices'][0]['message'])
            self.assertEqual(result['choices'][0]['message']['tool_calls'][0]['id'],'actual-action')
            self.assertEqual(calls[0]['reasoning_budget_tokens'],0)
            # Managed model proxies may pass tool definitions without forwarding
            # the desktop gateway's timeline-owner header.
            handler.headers={};calls.clear()
            code,result=handler._controlled_context_serial({'messages':history,'tools':[tool],'max_tokens':1024,'stream':False},'managed-fixture','off')
            self.assertEqual(code,200)
            self.assertIn('tool_calls',result['choices'][0]['message'])
            self.assertEqual(calls[0]['reasoning_budget_tokens'],0)

    def test_incomplete_write_resets_backend_and_only_returns_a_complete_retry(self):
        with ExitStack() as stack:
            root=Path(stack.enter_context(tempfile.TemporaryDirectory()))
            archives=ArchiveSet(root,idle_seconds=60);stack.callback(archives.close)
            state=EchoState(archives,'http://127.0.0.1:1',0,4,False)
            stack.callback(state.memory_controller.close)
            state._ctx_size=32768
            state.count_tokens=lambda text:max(1,len(str(text))//4)
            state._memory_adapter=adapter_for({'architecture':'qwen','revision':'fixture'},state.count_tokens)
            state.memory_adapter=lambda:state._memory_adapter
            handler=Handler.__new__(Handler);handler.state=state;handler.headers={}
            requests=[]
            name='mcp__opencore__dev'
            def generate(body,phase):
                requests.append(copy.deepcopy(body))
                if len(requests)==1:
                    call={'id':'partial','type':'function','function':{'name':name,'arguments':'{"action":"write","content":"unfinished'}}
                    return {'echo_incomplete_tool_call':'size','choices':[{'message':{'role':'assistant','content':'','tool_calls':[call]},'finish_reason':'length'}]}
                call={'id':'complete','type':'function','function':{'name':name,'arguments':json.dumps({'action':'status'})}}
                return {'choices':[{'message':{'role':'assistant','content':'','tool_calls':[call]},'finish_reason':'tool_calls'}]}
            handler._generate_live=generate
            handler._send_json=lambda code,value:(code,value)
            tool={'type':'function','function':{'name':name,'parameters':{'type':'object','properties':{'action':{'type':'string','enum':['status','write']}}}}}
            payload={'messages':[{'role':'user','content':'Check the workspace status.'}],
                     'tools':[tool],'max_tokens':4096,'stream':False}
            code,result=handler._controlled_context_serial(payload,'fixture','off')
            self.assertEqual(code,200)
            self.assertEqual(len(requests),2)
            self.assertTrue(requests[1].get('echo_reset'))
            self.assertFalse(requests[1].get('echo_append'))
            self.assertEqual(requests[1]['reasoning_budget_tokens'],0)
            self.assertLessEqual(requests[1]['max_tokens'],1200)
            self.assertEqual(result['choices'][0]['message']['tool_calls'][0]['id'],'complete')
            self.assertEqual(result['choices'][0]['finish_reason'],'tool_calls')

    def test_repeated_plans_across_continuations_recover_to_a_real_tool_call(self):
        with ExitStack() as stack:
            root=Path(stack.enter_context(tempfile.TemporaryDirectory()))
            archives=ArchiveSet(root,idle_seconds=60);stack.callback(archives.close)
            state=EchoState(archives,'http://127.0.0.1:1',0,4,False)
            stack.callback(state.memory_controller.close)
            state._ctx_size=32768;state.count_tokens=lambda text:max(1,len(str(text))//4)
            state._memory_adapter=adapter_for({'architecture':'qwen','revision':'fixture'},state.count_tokens)
            state.memory_adapter=lambda:state._memory_adapter
            handler=Handler.__new__(Handler);handler.state=state;handler.headers={}
            requests=[];name='mcp__opencore__dev'
            plans=["Empty workspace. I'll build a polished, feature-rich snake game.",
                   "Empty workspace, so I'll create a complete snake HTML game from scratch.",
                   "I'll build a polished single-file snake game with controls and a high score.",
                   "Empty workspace. Let me build a complete, polished snake game."]
            def generate(body,phase):
                requests.append(copy.deepcopy(body))
                if any('previous attempt repeated a plan' in str(m.get('content','')).lower() for m in body['messages']):
                    call={'id':'real-call','type':'function','function':{'name':name,'arguments':json.dumps({'action':'status'})}}
                    return {'choices':[{'message':{'role':'assistant','content':'','tool_calls':[call]},'finish_reason':'tool_calls'}]}
                return {'choices':[{'message':{'role':'assistant','content':plans[(len(requests)-1)%len(plans)]},'finish_reason':'length'}]}
            handler._generate_live=generate;handler._send_json=lambda code,value:(code,value)
            tool={'type':'function','function':{'name':name,'parameters':{'type':'object','properties':{'action':{'enum':['status']}}}}}
            code,result=handler._controlled_context_serial({'messages':[{'role':'user','content':'Make an HTML snake game.'}],
                'tools':[tool],'max_tokens':1024,'stream':False,'echo_max_calls':12},'fixture','off')
            self.assertEqual(code,200)
            self.assertLess(len(requests),12)
            self.assertEqual(requests[-1]['reasoning_budget_tokens'],0)
            self.assertEqual(result['choices'][0]['message']['tool_calls'][0]['id'],'real-call')


if __name__=='__main__':unittest.main()
