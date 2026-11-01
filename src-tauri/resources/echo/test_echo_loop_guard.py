import io
import types
import unittest
import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from echo_server import Handler,ReasoningLoopGuard,recover_embedded_tool_call,recover_requested_html_file,tool_call_error


class LoopGuardTests(unittest.TestCase):
    def test_short_paraphrased_plans_with_single_newlines_do_not_run_forever(self):
        guard=ReasoningLoopGuard()
        plans=["Empty workspace. I'll build a polished, feature-rich snake game.",
               "Empty workspace, so I'll create a complete snake HTML game from scratch.",
               "I'll build a polished single-file snake game with controls and a high score.",
               "Empty workspace. Let me build a complete, polished snake game."]
        detected=False
        for line in plans*4:
            detected=guard.feed(line+'\n') or detected
        self.assertTrue(detected)

    def test_repeated_planning_is_detected_across_stream_chunks(self):
        guard=ReasoningLoopGuard()
        block='The user wants me to find the snake game file and fix movement. I need to search the workspace for the snake game before editing it. I should locate the current snake HTML file and then inspect its actual contents.\n\n'
        detected=False
        for _ in range(12):
            for offset in range(0,len(block),11):detected=guard.feed(block[offset:offset+11]) or detected
        self.assertTrue(detected)

    def test_repeated_plans_without_line_breaks_are_detected_during_streaming(self):
        guard=ReasoningLoopGuard()
        text=('I need to find the snake game in the workspace before editing it. '
              'I should search the workspace for the snake game before I edit it. ')*8
        detected=False
        for offset in range(0,len(text),13):
            detected=guard.feed(text[offset:offset+13]) or detected
        self.assertTrue(detected)

    def test_distinct_steps_and_one_recheck_are_not_a_loop(self):
        guard=ReasoningLoopGuard()
        for block in [
            'First I will inspect the configuration and compare its context allocation with the upstream metadata. '*6,
            'The published context limit is larger, so the catalog can allocate thirty two thousand tokens while preserving the exact archive. '*6,
            'Next the generated HTML file needs a keyboard listener for movement, a fixed animation timestep, and a restart state. '*6,
            'The output receipt now points to the real file and its size, which lets the user open it through the chat. '*6]:
            self.assertFalse(guard.feed(block+'\n\n'))

    def test_requested_html_is_written_as_exact_source_and_not_reported_as_already_created(self):
        tools=[{'type':'function','function':{'name':'dev','parameters':{'properties':{'action':{'enum':['read','write']}}}}}]
        source='<!doctype html>\n<html><body>Snake</body></html>'
        message={'content':'Here is the source:\n```html\n'+source+'\n```'}
        self.assertTrue(recover_requested_html_file(message,tools,'Make a snake HTML game named snake.html',[]))
        args=json.loads(message['tool_calls'][0]['function']['arguments'])
        self.assertEqual(args['path'],'snake.html');self.assertEqual(args['content'],source)
        self.assertEqual(message['content'],'Here is the source:\n```html\n'+source+'\n```')
        second={'content':message['content']}
        self.assertFalse(recover_requested_html_file(second,tools,'Make a snake HTML game named snake.html',[message]))

    def test_prose_partial_html_and_missing_write_tool_do_not_create_a_file(self):
        tools=[{'function':{'name':'dev','parameters':{'properties':{'action':{'enum':['write']}}}}}]
        self.assertFalse(recover_requested_html_file({'content':'```html\n<html>\n```'},tools,'Make an HTML file',[]))
        self.assertFalse(recover_requested_html_file({'content':'```html\n<html></html>\n```'},[],'Make an HTML file',[]))
        self.assertFalse(recover_requested_html_file({'content':'```html\n<html></html>\n```'},tools,'Explain this HTML snippet',[]))

    def test_namespaced_dev_calls_receive_the_same_write_validation(self):
        name='mcp__opencore__dev'
        tools=[{'type':'function','function':{'name':name,'parameters':{'properties':{}}}}]
        message={'tool_calls':[{'function':{'name':name,'arguments':json.dumps({'action':'write','content':'x'*6001})}}]}
        self.assertIn('too large',tool_call_error(message,tools))

    def test_complete_qwen_json_call_uses_the_declared_namespaced_tool(self):
        name='mcp__opencore__dev'
        tools=[{'type':'function','function':{'name':name,'parameters':{'properties':{}}}}]
        message={'reasoning_content':'<tool_call>{"name":"dev","arguments":{"action":"status"}}</tool_call>'}
        self.assertTrue(recover_embedded_tool_call(message,tools))
        self.assertEqual(message['tool_calls'][0]['function']['name'],name)
        self.assertEqual(json.loads(message['tool_calls'][0]['function']['arguments']),{'action':'status'})

    def test_tool_markup_in_a_code_example_is_not_executed(self):
        tools=[{'type':'function','function':{'name':'mcp__opencore__dev','parameters':{'properties':{}}}}]
        message={'content':'Example:\n```xml\n<tool_call>{"name":"dev","arguments":{"action":"status"}}</tool_call>\n```'}
        self.assertFalse(recover_embedded_tool_call(message,tools))
        self.assertNotIn('tool_calls',message)

    def test_multiple_embedded_calls_across_fields_are_ambiguous(self):
        tools=[{'type':'function','function':{'name':'mcp__opencore__dev','parameters':{'properties':{}}}}]
        call='<tool_call>{"name":"dev","arguments":{"action":"status"}}</tool_call>'
        message={'reasoning_content':call,'content':call}
        self.assertFalse(recover_embedded_tool_call(message,tools))
        self.assertNotIn('tool_calls',message)

    def test_namespaced_html_write_uses_the_declared_tool(self):
        name='mcp__opencore__dev'
        tools=[{'type':'function','function':{'name':name,'parameters':{'properties':{'action':{'enum':['write']}}}}}]
        message={'content':'```html\n<html></html>\n```'}
        self.assertTrue(recover_requested_html_file(message,tools,'Make an HTML file',[]))
        self.assertEqual(message['tool_calls'][0]['function']['name'],name)

    def test_unfinished_large_dev_write_is_interrupted_without_becoming_a_tool_call(self):
        handler=Handler.__new__(Handler)
        handler.state=types.SimpleNamespace(log=lambda *_:None)
        handler._live_event=lambda *_:None
        argument='{"action":"write","path":"index.html","content":"'+'x'*7000
        event={'choices':[{'delta':{'tool_calls':[{'index':0,'id':'call-1','function':{'name':'mcp__opencore__dev','arguments':argument}}]}}]}
        handler._upstream=lambda *args,**kwargs:io.BytesIO(('data: '+json.dumps(event)+'\n\n').encode())
        result=handler._generate_live({'stream':True,'max_tokens':8000},'working')
        self.assertTrue(result.get('echo_incomplete_tool_call'))
        self.assertEqual(result['choices'][0]['finish_reason'],'length')

    def test_a_complete_short_tool_call_remains_executable(self):
        handler=Handler.__new__(Handler);handler.state=types.SimpleNamespace(log=lambda *_:None);handler._live_event=lambda *_:None
        args=json.dumps({'action':'status'})
        events=[{'choices':[{'delta':{'tool_calls':[{'index':0,'id':'call-1','function':{'name':'mcp__opencore__dev','arguments':args}}]}}]},
                {'choices':[{'delta':{},'finish_reason':'tool_calls'}]}]
        stream=''.join('data: '+json.dumps(event)+'\n\n' for event in events)+'data: [DONE]\n\n'
        handler._upstream=lambda *a,**k:io.BytesIO(stream.encode())
        result=handler._generate_live({'stream':True,'max_tokens':1024},'working')
        self.assertEqual(result['choices'][0]['finish_reason'],'tool_calls')
        self.assertEqual(json.loads(result['choices'][0]['message']['tool_calls'][0]['function']['arguments']),{'action':'status'})
        self.assertFalse(result.get('echo_incomplete_tool_call'))

    def test_large_complete_non_dev_call_is_not_cut_off(self):
        handler=Handler.__new__(Handler);handler.state=types.SimpleNamespace(log=lambda *_:None);handler._live_event=lambda *_:None
        args=json.dumps({'query':'x'*7000})
        events=[{'choices':[{'delta':{'tool_calls':[{'index':0,'id':'call-1','function':{'name':'search','arguments':args}}]}}]},
                {'choices':[{'delta':{},'finish_reason':'tool_calls'}]}]
        handler._upstream=lambda *a,**k:io.BytesIO((''.join('data: '+json.dumps(e)+'\n\n' for e in events)+'data: [DONE]\n\n').encode())
        result=handler._generate_live({'stream':True,'max_tokens':8000},'working')
        self.assertEqual(result['choices'][0]['finish_reason'],'tool_calls')
        self.assertEqual(json.loads(result['choices'][0]['message']['tool_calls'][0]['function']['arguments'])['query'],'x'*7000)
        self.assertFalse(result.get('echo_incomplete_tool_call'))

    def test_a_stalled_incomplete_tool_call_is_bounded_even_for_other_tools(self):
        handler=Handler.__new__(Handler);handler.state=types.SimpleNamespace(log=lambda *_:None);handler._live_event=lambda *_:None
        event={'choices':[{'delta':{'tool_calls':[{'index':0,'id':'call-1','function':{'name':'read','arguments':'{"path":"unfinished'}}]}}]}
        handler._upstream=lambda *a,**k:io.BytesIO(('data: '+json.dumps(event)+'\n\n').encode())
        with patch('echo_server.time.monotonic',side_effect=[0,201]):
            result=handler._generate_live({'stream':True,'max_tokens':1024},'working')
        self.assertEqual(result.get('echo_incomplete_tool_call'),'time')
        self.assertEqual(result['choices'][0]['finish_reason'],'length')

    def test_a_silent_partial_tool_stream_times_out_without_another_chunk(self):
        stopped=threading.Event()
        event={'choices':[{'delta':{'tool_calls':[{'index':0,'id':'call-1','function':{'name':'mcp__opencore__dev','arguments':'{"action":"write","content":"unfinished'}}]}}]}
        class SilentStream(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                self.wfile.write(('data: '+json.dumps(event)+'\n\n').encode());self.wfile.flush()
                stopped.wait(3)
            def log_message(self,*_): pass
        server=ThreadingHTTPServer(('127.0.0.1',0),SilentStream)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown);self.addCleanup(stopped.set)
        handler=Handler.__new__(Handler);handler.state=types.SimpleNamespace(log=lambda *_:None);handler._live_event=lambda *_:None
        handler._upstream=lambda *a,**k:urllib.request.urlopen('http://127.0.0.1:%d/'%server.server_port,timeout=5)
        started=time.monotonic()
        with patch('echo_server.TOOL_CALL_SECONDS',0.2):
            result=handler._generate_live({'stream':True,'max_tokens':1024},'working')
        self.assertLess(time.monotonic()-started,2)
        self.assertEqual(result.get('echo_incomplete_tool_call'),'time')
        self.assertEqual(result['choices'][0]['finish_reason'],'length')

    def test_a_slow_answer_without_a_partial_tool_is_not_cut_off(self):
        class SlowAnswer(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                self.wfile.write(b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n');self.wfile.flush()
                time.sleep(0.35)
                self.wfile.write(b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n');self.wfile.flush()
            def log_message(self,*_): pass
        server=ThreadingHTTPServer(('127.0.0.1',0),SlowAnswer)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        handler=Handler.__new__(Handler);handler.state=types.SimpleNamespace(log=lambda *_:None);handler._live_event=lambda *_:None
        handler._upstream=lambda *a,**k:urllib.request.urlopen('http://127.0.0.1:%d/'%server.server_port,timeout=5)
        with patch('echo_server.TOOL_CALL_SECONDS',0.2):
            result=handler._generate_live({'stream':True,'max_tokens':1024},'working')
        self.assertFalse(result.get('echo_incomplete_tool_call'))
        self.assertEqual(result['choices'][0]['message']['content'],'Hello')
        self.assertEqual(result['choices'][0]['finish_reason'],'stop')

if __name__=='__main__':unittest.main()
