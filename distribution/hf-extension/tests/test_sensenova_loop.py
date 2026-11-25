"""CPU-only exact-release admission and native streaming/tool protocol checks."""
import ast
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[3]
DIST=ROOT/'distribution';EXT=ROOT/'src-tauri/resources/runtime-setup/hf-extension'
TEXT=ROOT/'src-tauri/resources/text-runtime'
for folder in (DIST,EXT,TEXT):sys.path.insert(0,str(folder))
from sensenova_loop.protocol import OutputStream,messages_prompt,parse_call
from hf_runtime import runtime_plan
from model_modes import mode_availability, supports_text_generation

TOOLS=[{'type':'function','function':{'name':'open_app','parameters':{'type':'object','additionalProperties':False,
        'properties':{'name':{'type':'string'},'wait':{'type':'boolean'},'count':{'type':'integer'},'items':{'type':'array','items':{'type':'string'}}},
        'required':['name','wait']}}}]
CALL='<tool_call>\n<function=open_app>\n<parameter=name>\nChrome\n</parameter>\n<parameter=wait>true</parameter>\n<parameter=count>2</parameter><parameter=items>["one","two"]</parameter>\n</function>\n</tool_call>'


class SenseNovaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('sensenova_publisher_builder',DIST/'build-publisher-catalog.py')
        builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
        cls.entries=[row for row in builder.build()['entries'] if row['familyId']=='publisher-sensenova-loop-9b']

    def test_exact_full_release_keeps_all_eleven_shards_and_two_modes_without_claiming_ready(self):
        self.assertEqual(len(self.entries),1);row=self.entries[0]
        self.assertEqual(row['repository'],'sensenova/SenseNova-Loop-9B')
        self.assertEqual(row['revision'],'329a65f701ba8c48b73c30d0cbaa6f0449733673')
        shards=[pin for pin in row['files'] if pin['role']=='model']
        self.assertEqual(len(shards),11)
        self.assertEqual(next(pin['bytes'] for pin in shards if pin['filename']=='model-00002.safetensors'),8328)
        self.assertFalse(any(pin['filename'].endswith('.py') for pin in row['files']))
        self.assertTrue(row['gated']);self.assertFalse(row['runtime']['inferenceVerified'])
        self.assertTrue(supports_text_generation(row))
        self.assertEqual({mode['id']:mode['scope'] for mode in mode_availability(row)}, {'native':'text','echo':'text'})
        self.assertEqual({mode['id']:mode['contextTokens'] for mode in row['operatingModes']},{'native':524288,'echo':32768})

    def test_bundled_source_matches_release_blob_and_preserves_fifty_six_execution_slots(self):
        raw=(TEXT/'sensenova_loop/modeling_sensenova_loop.py').read_bytes()
        self.assertEqual(hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),'b437a10fa8d157bcbde656b967015da7dc86ea94')
        # Execute only the pure plan validator; no torch import, tensors, GPU or weights.
        tree=ast.parse(raw);node=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='execution_plan')
        module=ast.Module(body=[node],type_ignores=[]);namespace={'math':math};exec(compile(module,'publisher-plan','exec'),namespace)
        config=json.loads((DIST/'runtime-research/sensenova-loop/preview-config.json').read_bytes())
        plan=namespace['execution_plan'](config)
        self.assertEqual(len(plan),56);self.assertEqual(plan.count(4),2);self.assertEqual(plan.count(31),1)
        config['mhc']['streams']=3
        with self.assertRaises(ValueError):namespace['execution_plan'](config)

    def test_runtime_selects_isolated_cpu_and_cuda_recipes_without_a_gpu_probe(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);directory=root/'models/hub/fixture';directory.mkdir(parents=True)
            (directory/'config.json').write_text('{"model_type":"sensenova_loop"}')
            entry={**self.entries[0],'localDirectory':str(directory)}
            for backend in ('cpu','cuda'):
                with patch('managed_engines.cuda_driver_version',return_value=13000):
                    plan=runtime_plan({**entry,'runtimePreference':backend},root,ROOT/'src-tauri/resources')
                self.assertEqual(plan['recipe'],'sensenova-loop-'+backend+'-v1')
                self.assertIn(plan['recipe'],' '.join(plan['command']))
            with self.assertRaises(ValueError):runtime_plan({**entry,'revision':'0'*40,'runtimePreference':'cpu'},root,root)

    def test_native_prompt_preserves_tool_history_and_thinking_switch(self):
        captured={}
        class Tokenizer:
            def apply_chat_template(self,messages,**options):captured.update(messages=messages,options=options);return 'native prompt'
        messages=[{'role':'developer','content':'Follow the user.'},
                  {'role':'assistant','content':'','tool_calls':[{'function':{'name':'open_app','arguments':'{"name":"Chrome","wait":true}'}}]},
                  {'role':'tool','tool_call_id':'previous','content':'Window verified.'},{'role':'user','content':'Search for cats in it.'}]
        self.assertEqual(messages_prompt(Tokenizer(),{'messages':messages,'tools':TOOLS,'reasoning_budget_tokens':0}),'native prompt')
        self.assertEqual(captured['options']['tools'],TOOLS);self.assertFalse(captured['options']['enable_thinking'])
        self.assertEqual(captured['messages'][1]['tool_calls'][0]['function']['arguments']['name'],'Chrome')
        self.assertEqual(captured['messages'][2]['role'],'tool');self.assertEqual(messages[0]['role'],'developer')

    def test_character_stream_separates_reasoning_and_only_publishes_validated_actions(self):
        stream=OutputStream({'tools':TOOLS});deltas=[]
        for char in 'Need the current window.</think>Opening it now.\n'+CALL:
            deltas.extend(stream.feed(char))
        self.assertFalse(any('tool_calls' in delta for delta in deltas))
        deltas.extend(stream.feed('',final=True));message=stream.message()
        self.assertEqual(message['reasoning_content'],'Need the current window.')
        self.assertIn('Opening it now.',message['content'])
        arguments=json.loads(message['tool_calls'][0]['function']['arguments'])
        self.assertEqual(arguments,{'name':'Chrome','wait':True,'count':2,'items':['one','two']})
        self.assertTrue(any('tool_calls' in delta for delta in deltas))

    def test_invalid_tool_name_schema_and_partial_calls_never_become_actions(self):
        for raw in (CALL.replace('open_app','invented_tool'),CALL.replace('true</parameter>','"yes"</parameter>'),CALL[:-12]):
            stream=OutputStream({'tools':TOOLS,'reasoning_budget_tokens':0})
            with self.assertRaises(ValueError):stream.feed(raw,final=True)
        stream=OutputStream({'tools':TOOLS,'reasoning_budget_tokens':0})
        deltas=stream.feed('Hello.',final=True)
        self.assertEqual(stream.message(),{'role':'assistant','content':'Hello.'})
        self.assertFalse(any('reasoning_content' in delta for delta in deltas))
        # A stopped reasoning-only response remains reasoning, not a bogus tool or parser error.
        stream=OutputStream({});stream.feed('Planning the next step.',final=True)
        self.assertEqual(stream.message()['reasoning_content'],'Planning the next step.')


if __name__=='__main__':unittest.main()
