"""Publisher Edge0/Strata adapter, with an authenticated OpenCore compatibility proxy."""
import argparse
import copy
import ctypes
import hmac
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError,URLError
from urllib.request import Request,build_opener,ProxyHandler,HTTPRedirectHandler

HERE=Path(__file__).resolve().parent
if str(HERE) not in sys.path:sys.path.insert(0,str(HERE))
ENGINE='edge0-cuda13-v1'


def supports(entry):
    try:
        pins=json.loads((HERE/'edge0-models.json').read_bytes())
        revision=pins['models'].get(entry.get('repository'))
        return isinstance(revision,str) and entry.get('revision')==revision
    except (OSError,ValueError,TypeError):return False


def hardware():
    from managed_engines import cuda_driver_version
    from hf_runtime import _available_memory
    if os.name!='nt' or cuda_driver_version()<13000:
        raise ValueError('The bundled Edge0 engine requires Windows x64, an NVIDIA GPU and a CUDA 13-compatible driver. Model files are kept.')
    driver=ctypes.WinDLL('nvcuda.dll');device=ctypes.c_int();total=ctypes.c_size_t()
    if driver.cuDeviceGet(ctypes.byref(device),0)!=0 or driver.cuDeviceTotalMem_v2(ctypes.byref(total),device)!=0:
        raise ValueError('The Edge0 GPU memory requirement could not be checked')
    if total.value<16*1024**3:
        raise ValueError('Edge0 120B/145B requires at least 16 GiB of GPU memory. This GPU has '+f'{total.value/1024**3:.1f} GiB'+'. The model files are kept.')
    available=_available_memory()
    if available is not None and available<20*1024**3:
        raise ValueError('Edge0 needs at least 20 GiB of available system RAM. Free memory and retry; model files are kept.')


def plan(entry,root,resources):
    if not supports(entry):raise ValueError('This is not a reviewed complete Edge0 release bundle')
    if entry.get('runtimePreference','auto') not in ('auto','cuda'):
        raise ValueError('The integrated Edge0 120B/145B runtime requires CUDA; this bundle has no CPU backend')
    hardware()
    from managed_engines import folder
    from hf_runtime import _weight_folder
    from model_modes import mode_context,NATIVE_TARGET,ECHO_TARGET
    engine=folder(root,ENGINE);weights=_weight_folder(entry,root);context=mode_context(entry)
    required={'model.json','model-skeleton.gguf','experts.bin','latent_dense_q8.bin','tokenizer/tokenizer.json'}
    if not required<={file['filename'] for file in entry['files']}:raise ValueError('Install the complete Edge0 expert and tokenizer bundle')
    python=engine/'edge0-strata/.venv/Scripts/python.exe'
    return {'command':[str(python),'-I',str(HERE/'hf_edge0.py'),'--serve','--engine',str(engine),'--model',str(weights),
                       '--model-id',entry['id'],'--context',str(context),'--port','{port}','--keyfile','{keyfile}',
                       '--resources',str(resources)],
            'folder':str(weights),'format':'edge0-bundle','context':context,'operatingMode':entry.get('operatingMode','echo'),
            'requestedContextTokens':NATIVE_TARGET if entry.get('operatingMode')=='native' else ECHO_TARGET,
            'runtimeBackend':'cuda','publisherEngine':ENGINE,'engineFamily':ENGINE}


def adapt_request(payload):
    if not isinstance(payload,dict):raise ValueError('Invalid Edge0 generation request')
    # These are OpenCore transport/admission controls, not publisher inference options.
    internal={'conversation_id','request_id','reasoning_budget_tokens','reasoning_min_tokens','reasoning_format','cache_prompt','n_keep','id_slot','return_tokens',
              'n_predict','t_max_predict_ms','samplers','typical_p','tfs_z','min_keep','dynatemp_range','dynatemp_exponent'}
    result={key:copy.deepcopy(value) for key,value in payload.items() if key not in internal and not key.startswith(('echo_','opencore_'))}
    effort=payload.get('opencore_reasoning_effort',payload.get('reasoning_effort'))
    if effort is not None:
        result['reasoning_effort']={'off':'none','ultra':'high','xhigh':'high','extra_high':'high'}.get(effort,effort)
    if 'repeat_penalty' in result:result['repetition_penalty']=result.pop('repeat_penalty')
    for message in result.get('messages',[]):
        if isinstance(message,dict) and message.get('role')=='tool':message.pop('name',None)
    for tool in result.get('tools',[]):
        if isinstance(tool,dict) and isinstance(tool.get('function'),dict):tool['function'].pop('strict',None)
    # The engine has no minimum-probability sampler; retaining its neutral default is equivalent.
    if result.get('min_p') in (None,0,0.0):result.pop('min_p',None)
    return result


def engine_command(args,port):
    strata=args.engine/'edge0-strata'
    return [str(args.engine/'edge0-infer.exe'),'serve','--model',str(args.model),'--device','cuda:0',
            '--host','127.0.0.1','--port',str(port),'--alias',args.model_id,'--context',str(args.context),
            '--api-key-file',str(args.keyfile),'--strata-dir',str(strata),'--strata-python',str(strata/'.venv/Scripts/python.exe'),
            '--strata-ready-timeout','240','--model-option','du=q8']


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


class Proxy(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,upstream,key,child=None,tokenizer=None):
        self.upstream,self.key,self.child,self.tokenizer=upstream,key,child,tokenizer
        self.opener=build_opener(ProxyHandler({}),NoRedirect())
        super().__init__(address,Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def send_json(self,status,value):
        raw=json.dumps(value).encode();self.send_response(status);self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(raw)

    def authorized(self):
        return hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+self.server.key)

    def do_GET(self):self.route()
    def do_POST(self):self.route()
    def route(self):
        if not self.authorized():return self.send_json(401,{'error':{'message':'The OpenCore worker key is required'}})
        allowed_get={'/health','/props','/status','/metrics','/v1/models'}
        allowed_post={'/v1/chat/completions','/tokenize','/detokenize'}
        if self.path not in (allowed_get if self.command=='GET' else allowed_post):return self.send_json(404,{'error':{'message':'Unsupported Edge0 endpoint'}})
        if self.server.child is not None and self.server.child.poll() is not None:
            return self.send_json(503,{'error':{'message':'The owned Edge0 engine stopped. Inspect the runtime log.'}})
        started=False
        try:
            payload=None
            if self.command=='POST':
                length=self.headers.get('Content-Length','')
                if not length.isdigit() or not 0<int(length)<=2*1024**2:raise ValueError('Invalid request size')
                payload=json.loads(self.rfile.read(int(length)))
                if not isinstance(payload,dict):raise ValueError('Supply a JSON object')
                if self.path=='/tokenize':
                    if self.server.tokenizer is None:raise ValueError('Edge0 tokenization is unavailable')
                    content=payload.get('content')
                    if not isinstance(content,str):raise ValueError('Tokenization requires text')
                    return self.send_json(200,{'tokens':self.server.tokenizer.encode(content,add_special_tokens=False).ids})
                if self.path=='/detokenize':
                    tokens=payload.get('tokens')
                    if self.server.tokenizer is None or not isinstance(tokens,list) or any(type(token) is not int or token<0 for token in tokens):raise ValueError('Invalid token list')
                    return self.send_json(200,{'content':self.server.tokenizer.decode(tokens,skip_special_tokens=False)})
                payload=adapt_request(payload)
            request=Request(self.server.upstream+self.path,data=json.dumps(payload).encode() if payload is not None else None,
                            headers={'Content-Type':'application/json','Authorization':'Bearer '+self.server.key},method=self.command)
            try:response=self.server.opener.open(request,timeout=300)
            except HTTPError as error:response=error
            with response:
                self.send_response(response.status)
                content_type=response.headers.get('Content-Type','application/json')
                self.send_header('Content-Type',content_type);self.send_header('Cache-Control','no-store')
                if response.headers.get('Content-Length'):self.send_header('Content-Length',response.headers['Content-Length'])
                self.end_headers()
                started=True
                streaming='text/event-stream' in content_type
                while True:
                    chunk=response.readline(2*1024**2) if streaming else response.read(65536)
                    if not chunk:break
                    self.wfile.write(chunk);self.wfile.flush()
        except (ValueError,TypeError) as error:
            if not started:self.send_json(400,{'error':{'message':str(error)[:1000]}})
        except (URLError,TimeoutError):
            if not started:self.send_json(503,{'error':{'message':'The local Edge0 engine is not ready or could not complete this request'}})
        except (BrokenPipeError,ConnectionResetError):pass


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--serve',action='store_true',required=True)
    for name in ('engine','model','keyfile','resources'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--model-id',required=True);parser.add_argument('--context',type=int,required=True);parser.add_argument('--port',type=int,required=True)
    args=parser.parse_args();key=args.keyfile.read_text(encoding='utf-8').strip()
    if len(key)<24:raise ValueError('Missing OpenCore worker key')
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));private_port=sock.getsockname()[1]
    from tokenizers import Tokenizer
    tokenizer=Tokenizer.from_file(str(args.model/'tokenizer/tokenizer.json'))
    child=subprocess.Popen(engine_command(args,private_port),stdin=subprocess.DEVNULL,stdout=sys.stderr,stderr=sys.stderr,
                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    job=None
    if os.name=='nt':
        sys.path.insert(0,str(args.resources/'runtime-setup'))
        from setup_manager import WindowsJob
        job=WindowsJob(child)
    server=Proxy(('127.0.0.1',args.port),'http://127.0.0.1:'+str(private_port),key,child,tokenizer)
    def watch():
        while child.poll() is None:time.sleep(.5)
        server.shutdown()
    threading.Thread(target=watch,daemon=True).start()
    try:server.serve_forever(poll_interval=.25)
    finally:
        server.server_close()
        if job:job.stop();job.close()
        elif child.poll() is None:child.terminate()


if __name__=='__main__':main()
