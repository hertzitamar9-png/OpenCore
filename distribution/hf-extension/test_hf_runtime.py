import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import types
from unittest.mock import patch

from hf_runtime import RuntimeManager, checked_model_files, conversation_key, runtime_plan, system_prefix, _worker_main, admitted_context


class RuntimeSafetyTests(unittest.TestCase):
    def test_owned_worker_can_start_with_isolated_python_imports(self):
        import subprocess,sys,hf_runtime
        result=subprocess.run([sys.executable,'-I',hf_runtime.__file__,'--help'],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('--worker',result.stdout)

    def test_live_metrics_read_only_the_active_managed_model_without_exposing_its_key(self):
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        import threading
        seen=[]
        class Context(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                seen.append((self.path,self.headers.get('Authorization')))
                value=b'{"modelOutputTokens":42,"modelContextTokens":32768}'
                self.send_response(200);self.send_header('Content-Length',str(len(value)));self.end_headers();self.wfile.write(value)
        server=ThreadingHTTPServer(('127.0.0.1',0),Context)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        runtime=RuntimeManager(self.root,self.resources)
        self.assertIsNone(runtime.telemetry_source('hf-fixture','chat 1'))
        runtime._active={'id':'hf-fixture','url':'http://127.0.0.1:'+str(server.server_port),'key':'managed-test-key'}
        self.assertIsNone(runtime.telemetry_source('hf-other','chat 1'))
        descriptor,read=runtime.telemetry_source('hf-fixture','chat 1')
        self.assertNotIn('key',descriptor)
        self.assertEqual(read(),{'modelOutputTokens':42,'modelContextTokens':32768})
        self.assertEqual(seen,[('/echo/context?conversation=chat%201','Bearer managed-test-key')])

    def test_imported_models_launch_the_main_echo_context_loop(self):
        import sys
        seen=[]
        module=types.SimpleNamespace(Handler=object,main=lambda:seen.extend(sys.argv) or 0)
        args=['hf_runtime.py','--worker','echo','--resources','unused','--port','1234',
              '--upstream','http://127.0.0.1:1235','--archive','unused-archive','--context','32768']
        with patch('hf_runtime._load',return_value=module),patch.object(sys,'argv',args):
            self.assertEqual(_worker_main(),0)
        self.assertNotIn('--legacy-context',seen)
        self.assertNotIn('--no-model-search',seen)
        self.assertEqual(seen[seen.index('--context-size')+1],'32768')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.resources = self.root / 'resources'
        self.folder = self.root / 'models/hub/hf-fixture'
        self.folder.mkdir(parents=True)
        self.data = b'GGUF-test-fixture'
        self.path = self.folder / 'model-Q4_K_M.gguf'
        self.path.write_bytes(self.data)
        pin = {'filename': self.path.name, 'bytes': len(self.data), 'role': 'model',
               'sha256': hashlib.sha256(self.data).hexdigest(), 'localPath': str(self.path),
               'actualSha256': hashlib.sha256(self.data).hexdigest()}
        self.entry = {'id': 'hf-fixture', 'repository': 'test/model', 'revision': 'a' * 40,
                      'format': 'gguf', 'status': 'installed', 'files': [pin],
                      'localDirectory': str(self.folder), 'contextTokens': 262144,
                      'runtime': {'adapter': 'gguf'}}
        self.receipt = self.folder / 'install-receipt.json'
        self.receipt.write_text(json.dumps({'schema': 1, 'id': self.entry['id'],
            'repository': self.entry['repository'], 'revision': self.entry['revision'], 'files': [pin]}))
        self.entry['receiptPath'] = str(self.receipt)
        (self.root / 'runtime').mkdir()
        (self.root / 'runtime/llama-server.exe').write_bytes(b'owned-engine')
        (self.resources / 'echo').mkdir(parents=True)
        (self.resources / 'echo/echo_server.py').write_text('# installed ECHO')

    def test_files_require_matching_installed_receipt_and_content(self):
        self.assertEqual(checked_model_files(self.entry, self.root), [self.path])
        self.path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed|size|checksum'):
            checked_model_files(self.entry, self.root)

    def test_relocated_or_traversing_files_are_rejected(self):
        self.entry['localDirectory'] = str(self.root.parent)
        with self.assertRaisesRegex(ValueError, 'managed|outside'):
            checked_model_files(self.entry, self.root)

    def test_receipt_for_another_revision_cannot_prove_readiness(self):
        self.entry['revision'] = 'b' * 40
        with self.assertRaisesRegex(ValueError, 'receipt|revision'):
            checked_model_files(self.entry, self.root)

    def test_gguf_plan_bounds_context_threads_and_uses_installed_engine(self):
        with patch('hf_runtime._bundled_cuda_available',return_value=True):
            plan = runtime_plan(self.entry, self.root, self.resources)
        self.assertEqual(Path(plan['command'][0]), self.root / 'runtime/llama-server.exe')
        self.assertLessEqual(plan['context'], 32768)
        self.assertIn('--fit', plan['command'])
        self.assertIn('--no-webui', plan['command'])
        self.assertNotIn('--trust-remote-code', plan['command'])

    def test_automatic_tries_the_other_gpu_without_falling_back_to_cpu(self):
        import managed_engines
        for preference, expected in [('auto',['lfm3-cuda13-v1','lfm3-vulkan-v1']),('cuda',['lfm3-cuda13-v1'])]:
            self.entry['runtimePreference']=preference;calls=[]
            def fail(_root, engine_id, *args):
                calls.append(engine_id)
                raise OSError('Unavailable test runtime')
            runtime=RuntimeManager(self.root,self.resources)
            with patch('hf_runtime._available_memory',return_value=None), patch('hf_runtime._bundled_cuda_available',return_value=False), \
                 patch.object(managed_engines,'cuda_driver_version',return_value=13040), patch.object(managed_engines,'vulkan_gpu_available',return_value=True), \
                 patch.object(managed_engines,'ensure',side_effect=fail):
                with self.assertRaisesRegex(OSError,'Unavailable test runtime'):runtime.prepare(self.entry)
            self.assertEqual(calls,expected)
            self.assertTrue(self.path.is_file(), 'Runtime preparation never removes the downloaded checkpoint')

    def test_generic_gguf_runtime_reserves_the_full_context_for_one_echo_slot(self):
        plan=runtime_plan(self.entry,self.root,self.resources,prefer_gpu=False)
        command=plan['command']
        self.assertEqual(command[command.index('--parallel')+1],'1')

    def test_native_requests_the_published_window_without_clamping_to_echo(self):
        self.entry['operatingMode']='native'
        self.entry['publishedContextTokens']=1048576
        plan=runtime_plan(self.entry,self.root,self.resources,prefer_gpu=False)
        self.assertEqual(plan['context'],1048576)
        self.assertEqual(plan['command'][plan['command'].index('--ctx-size')+1],'1048576')
        self.assertEqual(plan['requestedContextTokens'],1048576)
        self.assertEqual(admitted_context(1048576,{'default_generation_settings':{'n_ctx':131072}}),(131072,True))
        self.entry['publishedContextTokens']=65536
        self.assertEqual(runtime_plan(self.entry,self.root,self.resources,prefer_gpu=False)['context'],65536)
        self.entry['operatingMode']='echo'
        self.assertEqual(runtime_plan(self.entry,self.root,self.resources,prefer_gpu=False)['context'],32768)

    def test_native_generation_is_verified_directly_without_an_echo_worker(self):
        self.entry['operatingMode']='native'
        runtime=RuntimeManager(self.root,self.resources)
        spawned=[];calls=[]
        runtime._spawn=lambda command,*args,**kwargs:spawned.append(command) or types.SimpleNamespace(poll=lambda:None)
        runtime._wait_health=lambda *args,**kwargs:None
        def request(url,payload=None,key=None,**kwargs):
            calls.append((url,payload))
            value={'n_ctx':131072} if url.endswith('/props') else {'choices':[{'message':{'content':'Hello'}}],'usage':{'completion_tokens':1}}
            return io.BytesIO(json.dumps(value).encode())
        runtime._request=request
        before=self.path.stat()
        with patch('hf_runtime._available_memory',return_value=None),patch('hf_runtime._bundled_cuda_available',return_value=True),patch('hf_runtime.gpu_offloaded_layers',return_value=8):
            proof=runtime.prepare(self.entry)
        self.assertEqual(len(spawned),1)
        self.assertEqual(proof['operatingMode'],'native')
        self.assertEqual(proof['contextTokens'],131072)
        self.assertTrue(proof['inferenceVerified'])
        self.assertEqual(proof['gpuOffloadedLayers'],8)
        self.assertFalse(proof['echoReady'])
        self.assertIsNone(proof['echoMode'])
        self.assertEqual(runtime._active['url'],runtime._active['upstream'])
        self.assertEqual(self.path.stat().st_mtime_ns,before.st_mtime_ns)
        self.assertEqual(len(list(self.folder.glob('*.gguf'))),1)
        self.assertFalse(any(payload and payload.get('conversation_id') for _,payload in calls))
        runtime.stop()

    def test_operating_mode_cannot_interrupt_generation_or_a_context_lease(self):
        runtime=RuntimeManager(self.root,self.resources)
        runtime._generation.acquire()
        with self.assertRaisesRegex(RuntimeError,'active'):runtime.change_mode(self.entry,'native')
        runtime._generation.release()
        runtime.set_context(self.entry['id'],'chat-1','request-1')
        with self.assertRaisesRegex(RuntimeError,'active'):runtime.change_mode(self.entry,'native')
        runtime.release_context('request-1')
        runtime._active={'id':self.entry['id'],'mode':'echo'}
        self.assertEqual(runtime.change_mode(self.entry,'native'),{'operatingMode':'native'})
        self.assertIsNone(runtime._active)

    def test_native_stream_retains_actual_final_token_usage_for_live_metrics(self):
        runtime=RuntimeManager(self.root,self.resources);self.entry['operatingMode']='native'
        runtime._active={'id':self.entry['id'],'mode':'native','url':'http://127.0.0.1:1234','key':'private-test','proof':{'contextTokens':65536},'usage':{}}
        seen=[]
        def request(url,payload=None,*args,**kwargs):
            seen.append(payload)
            return io.BytesIO(b'data: {"choices":[{"delta":{"content":"BLUE"}}]}\n\ndata: {"choices":[],"usage":{"prompt_tokens":15,"completion_tokens":37}}\n\ndata: [DONE]\n\n')
        runtime._request=request
        chunks,kind=runtime.chat(self.entry,{'messages':[{'role':'user','content':'Say BLUE'}],'stream':True,'echo_max_total_tokens':512})
        self.assertEqual(kind,'text/event-stream');self.assertIn(b'BLUE',b''.join(chunks))
        self.assertEqual(runtime._active['usage'],{'prompt_tokens':15,'completion_tokens':37})
        self.assertTrue(seen[0]['stream_options']['include_usage'])
        self.assertNotIn('echo_max_total_tokens',seen[0])
        self.assertFalse(runtime._generation.locked())

    def test_echo_uses_the_actual_single_slot_context_and_rejects_too_small_models(self):
        self.assertEqual(admitted_context(32768,{'default_generation_settings':{'n_ctx':16384}}),(16384,True))
        self.assertEqual(admitted_context(32768,{'n_ctx':8192}),(8192,True))
        self.assertEqual(admitted_context(16384,{'n_ctx':32768}),(16384,True))
        self.assertEqual(admitted_context(32768,{}),(32768,False))
        with self.assertRaisesRegex(ValueError,'2048|2,048'):
            admitted_context(32768,{'default_generation_settings':{'n_ctx':2048}})

    def test_model_list_reports_the_verified_runtime_context(self):
        self.entry.update(runtimeReady=True,runtimePreparation={'contextTokens':8192})
        models=RuntimeManager(self.root,self.resources).models([self.entry])
        self.assertEqual(models['data'][0]['context_length'],8192)

    def test_echo_installation_probe_has_a_bounded_total_generation_budget(self):
        runtime=RuntimeManager(self.root,self.resources)
        runtime._spawn=lambda *args,**kwargs:types.SimpleNamespace(poll=lambda:None)
        runtime._wait_health=lambda *args,**kwargs:None
        calls=[]
        def request(url,payload=None,key=None,**kwargs):
            calls.append((url,payload))
            if url.endswith('/props'):
                return io.BytesIO(json.dumps({'default_generation_settings':{'n_ctx':8192}}).encode())
            if payload and payload.get('conversation_id'):
                if payload.get('echo_max_total_tokens')!=payload.get('max_tokens'):
                    raise RuntimeError('ECHO smoke test did not bound its total output')
                self.assertEqual(payload.get('opencore_reasoning_effort'),'off')
            return io.BytesIO(json.dumps({'choices':[{'message':{'content':'Hello'}}],'usage':{'completion_tokens':1}}).encode())
        runtime._request=request
        with patch('hf_runtime._available_memory',return_value=None),patch('hf_runtime._bundled_cuda_available',return_value=True),patch('hf_runtime.gpu_offloaded_layers',return_value=8):
            proof=runtime.prepare(self.entry)
        self.assertTrue(proof['echoReady'])
        self.assertTrue(proof['contextVerified'])
        self.assertEqual(proof['contextTokens'],8192)
        self.assertTrue(any(payload and payload.get('echo_max_total_tokens')==512 for _,payload in calls))
        runtime.stop()

    def test_transformers_runtime_also_uses_its_actual_reported_context(self):
        runtime=RuntimeManager(self.root,self.resources)
        runtime._spawn=lambda *args,**kwargs:types.SimpleNamespace(poll=lambda:None)
        runtime._wait_health=lambda *args,**kwargs:None
        runtime._ensure_transformers=lambda *args,**kwargs:None
        def request(url,payload=None,key=None,**kwargs):
            value={'n_ctx':8192} if url.endswith('/props') else {'choices':[{'message':{'content':'Hello'}}]}
            return io.BytesIO(json.dumps(value).encode())
        runtime._request=request
        plan={'format':'safetensors','context':32768,'folder':str(self.folder),'command':['fake-worker','{port}']}
        with patch('hf_runtime._available_memory',return_value=None),patch('hf_runtime.runtime_plan',return_value=plan):
            proof=runtime.prepare(self.entry)
        self.assertEqual(proof['contextTokens'],8192)
        self.assertTrue(proof['contextVerified'])
        runtime.stop()

    def test_unsupported_format_is_downloadable_but_not_executable(self):
        self.entry['format'] = 'mlx'
        with self.assertRaisesRegex(ValueError, 'runtime|format|adapter'):
            runtime_plan(self.entry, self.root, self.resources)

    def test_fresh_install_uses_the_bundled_engine_without_another_model_download(self):
        (self.root/'runtime/llama-server.exe').unlink()
        bundled=self.resources/'opencore/runtime/llama-server.exe'
        bundled.parent.mkdir(parents=True)
        bundled.write_bytes(b'owned-engine')
        with patch('hf_runtime._bundled_cuda_available',return_value=True):
            plan=runtime_plan(self.entry,self.root,self.resources)
        self.assertEqual(Path(plan['command'][0]),bundled)

    def test_explicit_nvfp4_checkpoint_does_not_fall_back_to_plain_transformers(self):
        self.entry.update(format='safetensors', quantization='NVFP4')
        with self.assertRaisesRegex(ValueError, 'NVFP4|hardware|runtime'):
            runtime_plan(self.entry, self.root, self.resources)

    def test_automatic_transformers_uses_isolated_cuda_with_explicit_cpu_available(self):
        self.entry.update(format='safetensors', quantization='BF16')
        (self.folder/'config.json').write_text('{"model_type":"llama"}')
        with patch('managed_engines.cuda_driver_version',return_value=13040):
            plan=runtime_plan(self.entry,self.root,self.resources)
        self.assertEqual(plan['recipe'],'managed-transformers-text-cuda-v1')
        self.assertEqual(plan['runtimeBackend'],'cuda')
        self.assertIn('cuda-hybrid',plan['command'])
        with patch('managed_engines.cuda_driver_version',return_value=0):
            with self.assertRaisesRegex(ValueError,'compatible NVIDIA'):
                runtime_plan(self.entry,self.root,self.resources)
            self.entry['runtimePreference']='cpu'
            cpu=runtime_plan(self.entry,self.root,self.resources)
        self.assertEqual(cpu['recipe'],'managed-transformers-text-v1')
        self.assertEqual(cpu['command'][-1],'cpu')

    def test_echo_conversations_have_separate_stable_identity(self):
        self.assertEqual(conversation_key('chat-1'), conversation_key('chat-1'))
        self.assertNotEqual(conversation_key('chat-1'), conversation_key('chat-2'))
        with self.assertRaises(ValueError):
            conversation_key('')
        with self.assertRaises(ValueError):
            conversation_key('../outside')

    def test_context_admission_prevents_cross_chat_replacement(self):
        runtime = RuntimeManager(self.root, self.resources)
        runtime.set_context('hf-fixture', 'chat-1', 'request-1')
        with self.assertRaisesRegex(RuntimeError, 'active|finish|conversation'):
            runtime.set_context('hf-fixture', 'chat-2', 'request-2')
        runtime.release_context('request-1')
        runtime.set_context('hf-fixture', 'chat-2', 'request-2')
        self.assertEqual(runtime._context['conversationId'], 'chat-2')
        runtime.stop()

    def test_verified_multimodal_text_generator_is_listed_as_chat_backend(self):
        runtime=RuntimeManager(self.root,self.resources)
        vl={**self.entry,'category':'omni','status':'installed','inputModalities':['image','text'],
            'outputModalities':['text'],'runtimeReady':True}
        audio={**self.entry,'id':'asr-model','category':'speech','inputModalities':['audio'],
            'outputModalities':['text'],'runtimeReady':True}
        unverified={**vl,'id':'vl-unverified','runtimeReady':False,'runtimePreparation':{}}
        catalog=runtime.models([vl,audio,unverified])
        self.assertEqual([item['id'] for item in catalog['data']],[self.entry['id']])
        self.assertEqual(catalog['data'][0]['context_length'],32768)

    def test_models_does_not_call_unverified_download_runtime_ready(self):
        runtime = RuntimeManager(self.root, self.resources)
        models = runtime.models([self.entry])
        self.assertEqual(models['data'], [])
        runtime.stop()

    def test_stop_releases_only_managed_worker_processes(self):
        runtime = RuntimeManager(self.root, self.resources)
        class Child:
            def __init__(self): self.stopped = False
            def poll(self): return None
            def terminate(self): self.stopped = True
            def wait(self, timeout=None): return 0
        child = Child()
        runtime._children.append(child)
        runtime.stop()
        self.assertTrue(child.stopped)
        self.assertIsNone(runtime._context)

    def test_echo_memory_and_policy_share_one_initial_system_without_reordering_turns(self):
        payload = {'messages': [{'role': 'system', 'content': 'Policy'},
            {'role': 'user', 'content': 'Earlier question'},
            {'role': 'assistant', 'content': 'Earlier answer'},
            {'role': 'system', 'content': 'Retrieved archive'},
            {'role': 'developer', 'content': 'Developer context'},
            {'role': 'user', 'content': 'Current question'}]}
        adapted = system_prefix(payload)
        self.assertEqual([x['role'] for x in adapted['messages']], ['system', 'user', 'assistant', 'user'])
        self.assertEqual(adapted['messages'][0]['content'], 'Policy\n\nRetrieved archive\n\nDeveloper context')
        self.assertEqual(adapted['messages'][-1]['content'], 'Current question')
        self.assertEqual(len(payload['messages']), 6)
        self.assertEqual(system_prefix(adapted), adapted)


if __name__ == '__main__':
    unittest.main()
