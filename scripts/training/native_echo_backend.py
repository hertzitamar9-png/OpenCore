"""An owned native backend for qualification/training, separate from app/GSM8K."""
import json
import os
from pathlib import Path
import subprocess
import socket
import time
import urllib.request


def request(base,path,value=None,timeout=180):
    payload=json.dumps(value).encode() if value is not None else None
    with urllib.request.urlopen(urllib.request.Request(base+path,data=payload,headers={'Content-Type':'application/json'}),timeout=timeout) as response:
        return json.load(response)


class NativeBackend:
    def __init__(self,home,folder,model=None,port=8888):
        self.home=Path(home);self.folder=Path(folder);self.model=Path(model or self.home/'OpenCore-Code-Single-File.gguf')
        self.base=f'http://127.0.0.1:{port}';self.port=port;self.child=None

    def __enter__(self):
        with socket.socket() as probe:
            probe.settimeout(1)
            if probe.connect_ex(('127.0.0.1',self.port))==0:
                raise RuntimeError('Training backend port occupied; refusing to attach')
        self.folder.mkdir(parents=True,exist_ok=True)
        self.log=(self.folder/'native-server.log').open('ab')
        server=self.home/'runtime/llama-server.exe'
        env=dict(os.environ,OPENCORE_BF16_RESIDENT_POOL='1',OPENCORE_BF16_EXPERT_GGUF=str(self.model),
            OPENCORE_BACKEND_DIR=str(server.parent),OPENCORE_ACTIVE_EXPERTS='5',OPENCORE_WORKFLOW_STAGES='18',
            OPENCORE_Q8_STAGE_EXPERTS='10000',OPENCORE_STAGE_FILE=str(self.home/'opencore-stage.txt'),
            OPENCORE_FUSED_PRIVATE_SHARED='1',OPENCORE_CARRIER_GRAPH_INPUTS='1')
        args=[str(server),'-m',str(self.model),'--host','127.0.0.1','--port',str(self.port),'-ngl','99','-c','16384',
            '-b','512','-ub','512','-np','1','-t','1','--flash-attn','on','--cache-type-k','q4_0','--cache-type-v','q4_0',
            '--no-kv-offload','-sm','none','-mg','0','--reasoning','off']
        self.child=subprocess.Popen(args,cwd=self.home,env=env,stdout=self.log,stderr=self.log,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        (self.folder/'native-process.json').write_text(json.dumps({'pid':self.child.pid,'model':str(self.model),'started':time.time()},indent=2))
        try:
            end=time.monotonic()+180
            while time.monotonic()<end:
                if self.child.poll() is not None: raise RuntimeError(f'Native backend exited {self.child.returncode}')
                try: request(self.base,'/health',timeout=2);return self
                except Exception: time.sleep(1)
            raise TimeoutError('Native backend startup timed out')
        except BaseException:
            self.__exit__();raise

    def __exit__(self,*args):
        if self.child and self.child.poll() is None:
            self.child.terminate()
            try: self.child.wait(timeout=10)
            except subprocess.TimeoutExpired: self.child.kill();self.child.wait(timeout=10)
        self.log.close()

    def chat(self,messages,max_tokens=512):
        return request(self.base,'/v1/chat/completions',{'messages':messages,'temperature':0,'seed':42,'max_tokens':max_tokens,'cache_prompt':False})

    def probabilities(self,tokens):
        result=request(self.base,'/completion',{'prompt':tokens,'n_predict':1,'n_probs':64,'temperature':-1,
            'post_sampling_probs':False,'cache_prompt':False,'seed':42})
        probabilities=result.get('probs') or result.get('completion_probabilities')
        if not probabilities: raise RuntimeError('Backend did not expose next-token probabilities')
        top=probabilities[0].get('top_logprobs')
        if not top or any('id' not in item or 'logprob' not in item for item in top):
            raise RuntimeError('Unrecognized native probability schema; cannot qualify training view')
        return {'top':top,'timings':result.get('timings'),'raw':result}
