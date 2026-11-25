"""CPU-only publisher-engine plans and real loopback proxy protocol. No model inference."""
import copy
import http.client
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[3];HERE=ROOT/'src-tauri/resources/runtime-setup/hf-extension'
sys.path.insert(0,str(HERE))
import hf_edge0
from hf_source_engine import cmake_commands,extract_source
from hf_runtime import runtime_plan
from managed_engines import engine_for,definition,select_engine


class CustomEnginePlanTests(unittest.TestCase):
    def test_edge0_only_admits_exact_reviewed_complete_bundles(self):
        self.assertFalse(hf_edge0.supports({}))
        pins=json.loads((HERE/'edge0-models.json').read_bytes())['models']
        for repo,revision in pins.items():
            self.assertTrue(hf_edge0.supports({'repository':repo,'revision':revision}))
            self.assertFalse(hf_edge0.supports({'repository':repo,'revision':'f'*40}))

    def test_native_and_echo_use_the_publisher_bundle_and_distinct_bounded_context(self):
        repo,revision=next(iter(json.loads((HERE/'edge0-models.json').read_bytes())['models'].items()))
        with tempfile.TemporaryDirectory() as folder,patch('hf_edge0.hardware') as hardware:
            root=Path(folder);entry={'id':'hf-'+'a'*24,'repository':repo,'revision':revision,'format':'gguf','category':'text',
                'localDirectory':str(root/'models/hub/hf-test'),'runtime':{'adapter':'edge0'},'publishedContextTokens':131072,
                'files':[{'filename':name,'role':'model'} for name in ('model.json','model-skeleton.gguf','experts.bin','latent_dense_q8.bin','tokenizer/tokenizer.json')]}
            for mode,context in [('native',131072),('echo',32768)]:
                plan=runtime_plan({**entry,'operatingMode':mode},root,root)
                self.assertEqual(plan['context'],context);self.assertEqual(plan['publisherEngine'],'edge0-cuda13-v1')
                self.assertIn('hf_edge0.py',' '.join(plan['command']));self.assertNotIn('llama-server.exe',' '.join(plan['command']))
            with self.assertRaises(ValueError):runtime_plan({**entry,'runtimePreference':'cpu'},root,root)
            with self.assertRaises(ValueError):runtime_plan({**entry,'files':[]},root,root)
            self.assertEqual(hardware.call_count,3)

    def test_raon_nano_is_not_sent_to_the_older_raon_mini_engine_and_builds_no_gpu_backends(self):
        entry={'format':'gguf','repository':'KRAFTON/Raon-1-nano-gguf','managedEngine':'raon-nano-cpu-v1'}
        self.assertEqual(engine_for(entry),'raon-nano-cpu-v1');self.assertEqual(select_engine('raon-nano-cpu-v1','auto'),'raon-nano-cpu-v1')
        self.assertEqual(engine_for({'format':'gguf','repository':'KRAFTON/Raon-1-mini-preview-GGUF'}),'raon-cpu-v1')
        command=' '.join(map(str,cmake_commands(Path('source'),Path('build'),'cmake')[0]))
        for flag in ('GGML_CUDA=OFF','GGML_VULKAN=OFF','GGML_OPENCL=OFF','GGML_SYCL=OFF'):self.assertIn(flag,command)
        self.assertEqual(definition('raon-nano-cpu-v1')['revision'],'4d72ff0d45d5795aa4bd9655a77501269b52e1bc')

    def test_adapter_preserves_user_text_and_tool_schemas_and_removes_host_control_fields(self):
        payload={'messages':[{'role':'tool','name':'browser_use','tool_call_id':'call','content':'Verified window'}],
            'tools':[{'type':'function','function':{'name':'real_tool','strict':True,'parameters':{'type':'object','required':['path']}}}],
            'conversation_id':'chat','echo_total_tokens':1000,'opencore_reasoning_effort':'off','reasoning_format':'none',
            'reasoning_budget_tokens':0,'reasoning_min_tokens':0,'temperature':.3,'repeat_penalty':1.1,'min_p':0}
        original=copy.deepcopy(payload);result=hf_edge0.adapt_request(payload)
        self.assertEqual(payload,original);self.assertEqual(result['reasoning_effort'],'none')
        self.assertEqual(result['messages'][0]['content'],'Verified window');self.assertEqual(result['messages'][0]['tool_call_id'],'call')
        self.assertEqual(result['tools'][0]['function']['parameters']['required'],['path'])
        self.assertFalse(any(key.startswith(('echo_','opencore_')) or key in ('conversation_id','reasoning_format','reasoning_budget_tokens','reasoning_min_tokens') for key in result))


class FakePublisher(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        raw=b'{"status":"ok"}';self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));self.server.requests.append(body)
        if body.get('stream'):
            raw=b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\ndata: [DONE]\n\n';kind='text/event-stream'
        else:raw=json.dumps({'choices':[{'message':{'content':'hello'}}]}).encode();kind='application/json'
        self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);self.wfile.flush()


class Edge0ProxyTests(unittest.TestCase):
    def setUp(self):
        self.publisher=ThreadingHTTPServer(('127.0.0.1',0),FakePublisher);self.publisher.requests=[]
        threading.Thread(target=self.publisher.serve_forever,daemon=True).start()
        tokenizer=SimpleNamespace(encode=lambda text,**kwargs:SimpleNamespace(ids=list(text.encode())),decode=lambda tokens,**kwargs:bytes(tokens).decode())
        self.proxy=hf_edge0.Proxy(('127.0.0.1',0),'http://127.0.0.1:'+str(self.publisher.server_port),'unit-secret',tokenizer=tokenizer)
        threading.Thread(target=self.proxy.serve_forever,daemon=True).start()
    def tearDown(self):
        self.proxy.shutdown();self.proxy.server_close();self.publisher.shutdown();self.publisher.server_close()
    def call(self,path,body=None,key='unit-secret'):
        client=http.client.HTTPConnection('127.0.0.1',self.proxy.server_port,timeout=3)
        client.request('POST' if body is not None else 'GET',path,body=json.dumps(body) if body is not None else None,
                       headers={'Content-Type':'application/json','Authorization':'Bearer '+key})
        response=client.getresponse();value=response.read();status=response.status;client.close();return status,value
    def test_authentication_and_unknown_endpoints_do_not_reach_inference(self):
        self.assertEqual(self.call('/health',key='wrong')[0],401)
        self.assertEqual(self.call('/arbitrary-path')[0],404)
        self.assertEqual(self.publisher.requests,[])
    def test_json_and_sse_generation_preserve_answers_and_native_tool_requests(self):
        for stream in (False,True):
            status,body=self.call('/v1/chat/completions',{'model':'model','messages':[{'role':'user','content':'hello'}],
                'conversation_id':'chat','reasoning_format':'none','opencore_reasoning_effort':'off','stream':stream})
            self.assertEqual(status,200);self.assertIn(b'hello',body)
            if stream:self.assertIn(b'data: [DONE]',body)
            request=self.publisher.requests[-1];self.assertNotIn('conversation_id',request);self.assertNotIn('reasoning_format',request)
            self.assertEqual(request['reasoning_effort'],'none')
    def test_token_counter_and_decoder_use_the_same_model_tokenizer_contract(self):
        self.assertEqual(json.loads(self.call('/tokenize',{'content':'abc'})[1])['tokens'],[97,98,99])
        self.assertEqual(json.loads(self.call('/detokenize',{'tokens':[97,98,99]})[1])['content'],'abc')


if __name__=='__main__':unittest.main()
