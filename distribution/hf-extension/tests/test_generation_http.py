"""Real authenticated HTTP and stdio MCP, using CPU fixtures instead of model execution."""
import base64
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[3]
HERE=ROOT/'src-tauri/resources/runtime-setup/hf-extension'
sys.path.insert(0,str(HERE))
from hf_bridge import BridgeState,BridgeServer,RuntimeUnavailable
from hf_generation import MODEL


class GenerationHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.state=BridgeState(self.root,self.root,runtime=RuntimeUnavailable())
        self.secret='unit-test-secret-'*4
        self.server=BridgeServer(('127.0.0.1',0),self.state,self.secret)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.state.close();self.temp.cleanup()

    def request(self,path,body=None,*,secret=True):
        client=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        headers={'Content-Type':'application/json','Origin':'http://tauri.localhost'}
        if secret:headers['X-OpenCore-Hub-Secret']=self.secret
        client.request('POST' if body is not None else 'GET','/api'+path,body=json.dumps(body) if body is not None else None,headers=headers)
        response=client.getresponse();data=response.read();status=response.status;headers=dict(response.getheaders());client.close()
        return status,headers,data

    def test_generation_and_pipeline_routes_keep_desktop_authentication(self):
        for route,body in [('/generation/jobs',None),('/generation/input',{'image':'AAAA'}),('/generation/generate',{'request':{}}),
                           ('/generation/artifact',{'id':'x','path':'private.txt'}),('/pipelines/catalog',None),
                           ('/pipelines/connection',{}),('/pipelines/pending',None),('/pipelines/admit',{'jobId':'fake'})]:
            self.assertEqual(self.request(route,body,secret=False)[0],401,route)
        status,headers,data=self.request('/generation/jobs')
        self.assertEqual(status,200);self.assertEqual(json.loads(data),{'jobs':[]})
        self.assertEqual(headers['Access-Control-Allow-Origin'],'http://tauri.localhost')
        self.assertEqual(self.request('/pipelines/admit',{'jobId':'fake'})[0],400)

    def test_native_generate_route_calls_the_adapter_and_never_the_text_runtime(self):
        entry={'id':'hf-'+'1'*24,'repository':MODEL[0],'revision':MODEL[1],'status':'installed'}
        self.state.entry=lambda *args,**kwargs:entry
        with patch.object(self.state.generation,'start',return_value={'id':'adapter-job','status':'queued'}) as start:
            status,_,data=self.request('/generation/generate',{'request':{'modelId':entry['id'],'settings':{'inputPath':'example.png'}}})
        self.assertEqual(status,202);self.assertEqual(json.loads(data)['id'],'adapter-job')
        self.assertEqual(start.call_args.args[0],entry)

    def test_stdio_mcp_calls_the_same_authenticated_local_pipeline_dispatch(self):
        descriptor=self.root/'runtime-setup/hub-bridge/service.json'
        descriptor.parent.mkdir(parents=True,exist_ok=True)
        descriptor.write_text(json.dumps({'schema':1,'root':str(self.root),'resources':str(self.root),'secret':self.secret,
                              'url':'http://127.0.0.1:'+str(self.server.server_port)}))
        messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{}},
                  {'jsonrpc':'2.0','id':2,'method':'tools/list'},
                  {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'pipeline_catalog','arguments':{}}}]
        with patch('hf_pipeline_tools.catalog',return_value=[{'id':'model','pipeline':'3d','runtimeReady':False}]):
            result=subprocess.run([sys.executable,'-I',str(HERE/'hf_pipeline_mcp.py'),'--root',str(self.root),'--resources',str(self.root)],
                  input='\n'.join(map(json.dumps,messages))+'\n',text=True,capture_output=True,timeout=10,
                  env={**os.environ,'CUDA_VISIBLE_DEVICES':''},creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.assertEqual(result.returncode,0,result.stderr)
        values=[json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(values),3)
        self.assertTrue(any(tool['name']=='pipeline_run' for tool in values[1]['result']['tools']))
        self.assertEqual(values[2]['result']['structuredContent']['models'][0]['pipeline'],'3d')
        self.assertNotIn(self.secret,result.stdout)


if __name__=='__main__':unittest.main()
