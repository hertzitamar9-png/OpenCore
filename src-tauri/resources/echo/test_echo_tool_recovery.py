"""Exercise tool recovery without executing a client, plugin, or real tool."""
from contextlib import ExitStack
import copy
import json
from pathlib import Path
import tempfile
import unittest

from echo_server import ArchiveSet, EchoState, Handler
from evoagent.echo_adapters import adapter_for


class ToolRecoveryTests(unittest.TestCase):
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
