"""Deterministic integration checks for the adapted GVS5H orchestration."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src-tauri/resources/echo'))
from gvs5h.project import ProjectHarness, status
from echo_server import ArchiveSet, EchoState, Handler

PLAN = '### PLAN\nRead the existing file, patch the requested function, and run assertions.\n### TASKS\n- Read movement.py\n- Patch walking only\n- Verify both functions'
DONE = '### STATUS\ndone\n### NEXT\n\n### TASKS\n- [done] Read movement.py\n- [done] Patch walking only\n- [done] Verify both functions'


def exchange(call_id, args, result):
    return [dict(role='assistant', tool_calls=[dict(id=call_id, type='function', function=dict(name='dev', arguments=json.dumps(args)))]),
            dict(role='tool', tool_call_id=call_id, content=json.dumps(result))]


class GvsTests(unittest.TestCase):
    def test_planner_worker_and_reviewer_receive_the_same_image(self):
        with tempfile.TemporaryDirectory() as root:
            archives = ArchiveSet(Path(root), 0)
            try:
                state = EchoState(archives, 'http://127.0.0.1:1', 10000, 4, False)
                state._ctx_size = 32768
                state.count_tokens = lambda text: max(1,len(text)//4)
                handler = object.__new__(Handler)
                handler.state = state
                image = {'type':'image_url','image_url':{'url':'data:image/png;base64,'+'AAAB'*20000}}
                phases = []
                def generate(body, phase):
                    phases.append(phase)
                    if phase == 'working':
                        self.assertNotIn('tools',body)
                        self.assertNotIn('tool_choice',body)
                    self.assertTrue(any(isinstance(m.get('content'),list) and image in m['content'] for m in body['messages']))
                    text = '### PLAN\nDescribe the image directly.\n### TOOLS\nnone\n### TASKS\n- Describe its colors and shapes' if phase == 'planning' else DONE if phase == 'managing' else 'A blue spiral.'
                    return {'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':text}}]}
                handler._generate_live = generate
                handler._send_json = lambda code,result: result
                result = handler._controlled_context({'messages':[{'role':'user','content':[{'type':'text','text':'Describe this picture.'},image]}],
                    'echo_harness':'gvs5h','echo_harness_turn':'photo','max_tokens':1024,'tools':[{'type':'function','function':{'name':'desktop_use','parameters':{'type':'object'}}}],'tool_choice':'auto'}, 'visual')
                self.assertEqual(phases,['planning','working','managing'])
                self.assertEqual(result['choices'][0]['message']['content'],'A blue spiral.')
            finally:
                archives.close()

    def test_restart_deduplicates_receipts_and_requires_current_file_hash(self):
        with tempfile.TemporaryDirectory() as root:
            harness = ProjectHarness(root, 'conversation', 'turn1', 'Change walking only')
            self.assertIn('Read the existing file', harness.plan('', lambda *_: PLAN))
            harness.observe(exchange('edit1', {'action':'edit','path':'movement.py'}, {'path':'movement.py','sha256':'v2'}))
            harness.observe(exchange('run1', {'action':'run'}, {'exitCode':0,'checked':{'movement.py':'v1'}}))
            decision, next_task = harness.review('Everything done', lambda *_: DONE)
            self.assertEqual(decision, 'continue')
            self.assertIn('movement.py', next_task)
            reloaded = ProjectHarness(root, 'conversation', 'turn1', 'Change walking only')
            self.assertIsNone(reloaded.plan('', lambda *_: self.fail('Planner ran twice')))
            reloaded.observe(exchange('edit1', {'action':'edit','path':'movement.py'}, {'path':'movement.py','sha256':'v2'}))
            reloaded.observe(exchange('run2', {'action':'run'}, {'exitCode':1,'checked':{'movement.py':'v2'}}))
            self.assertEqual(reloaded.unverified(), ['movement.py'])
            reloaded.observe(exchange('run3', {'action':'run'}, {'exitCode':0,'checked':{'movement.py':'v2'}}))
            self.assertEqual(reloaded.review('Actual checks passed', lambda *_: DONE)[0], 'done')
            self.assertEqual(status(root, 'conversation')['toolCount'], 4)
            self.assertEqual(status(root, 'conversation')['status'], 'complete')
            next_turn = ProjectHarness(root, 'conversation', 'turn2', 'Explain the code')
            self.assertEqual(next_turn.state['prior']['status'], 'complete')
            self.assertNotEqual(next_turn.folder, reloaded.folder)
            self.assertTrue((reloaded.folder / 'transcript.jsonl').exists())

    def test_manager_loop_continues_to_tools_and_reviews_actual_execution(self):
        with tempfile.TemporaryDirectory() as root:
            archives = ArchiveSet(Path(root), 0)
            try:
                state = EchoState(archives, 'http://127.0.0.1:1', 10000, 4, False)
                state._ctx_size = 32768
                state.count_tokens = lambda text: max(1, len(text)//4)
                handler = object.__new__(Handler)
                handler.state = state
                phases = []
                workers = 0
                def generate(body, phase):
                    nonlocal workers
                    phases.append(phase)
                    if phase != 'working':
                        self.assertNotIn('tools', body)
                        self.assertLessEqual(body['reasoning_budget_tokens'],512)
                        content = PLAN if phase == 'planning' else DONE
                        if phase == 'managing':
                            self.assertIn('exitCode', body['messages'][1]['content'])
                    else:
                        workers += 1
                        if workers == 2:
                            return {'choices':[{'finish_reason':'tool_calls','message':exchange('check', {'action':'run'}, {})[0]}]}
                        content = 'Updated walking; running remains unchanged.'
                    return {'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':content}}]}
                handler._generate_live = generate
                handler._send_json = lambda code, result: result
                base = dict(echo_harness='gvs5h', echo_harness_turn='turn', max_tokens=2048,
                            reasoning_budget_tokens=6000, tools=[{'type':'function','function':{'name':'dev','parameters':{'required':['action']}}}])
                request = dict(role='user', content='Change walking only')
                # The manager cannot approve a stale passing test after the file changed.
                tail = exchange('write', {'action':'edit','path':'movement.py'}, {'path':'movement.py','sha256':'new'})
                tail += exchange('oldtest', {'action':'run'}, {'exitCode':0,'checked':{'movement.py':'old'}})
                result = handler._controlled_context({**base,'messages':[request]+tail}, 'case')
                self.assertTrue(result['choices'][0]['message']['tool_calls'])
                self.assertEqual(phases, ['planning','working','managing','working'])
                tail += exchange('check', {'action':'run'}, {'exitCode':0,'checked':{'movement.py':'new'}})
                result = handler._controlled_context({**base,'messages':[request]+tail}, 'case')
                self.assertEqual(result['echo']['harness']['status'],'complete')
                self.assertEqual(phases.count('planning'),1)
                self.assertEqual(phases.count('managing'),2)
            finally:
                archives.close()


if __name__ == '__main__':
    unittest.main()
