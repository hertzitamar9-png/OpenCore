"""Private authenticated child runner. No discovery, shell, downloads or process reuse."""
from pathlib import Path
import json
import os
import secrets
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from .errors import Cancelled,checkpoint
from .identity import file_identity


class RunnerError(RuntimeError):
    pass


def find_server(path=None,model_path=None):
    if not isinstance(path,(str,Path)) or not str(path) or not Path(path).is_absolute() or not Path(path).is_file():
        raise RunnerError('The manager must supply an existing absolute pinned llama-server path; automatic external runtime discovery is disabled.')
    return str(Path(path).resolve())


def _port():
    with socket.socket() as server:
        server.bind(('127.0.0.1',0));return server.getsockname()[1]


class LocalServer:
    def __init__(self,executable,model,log_dir,threads=2,gpu_layers=0,context=4096,startup_timeout=240,
                 request_timeout=240,cancel=None,companion=None):
        self.executable=find_server(executable)
        self.model=str(Path(model).resolve());self.log_dir=Path(log_dir)
        self.threads=int(threads);self.gpu_layers=int(gpu_layers);self.context=int(context)
        self.startup_timeout=max(.1,min(float(startup_timeout),1800));self.request_timeout=max(.1,min(float(request_timeout),1800))
        self.cancel=cancel or threading.Event();self._done=threading.Event();self._stop_lock=threading.Lock()
        self.process=None;self.log_file=None;self.key_path=None;self.port=None;self.runtime_proof=None
        self._key=secrets.token_urlsafe(32);self.model_id='autoloop-'+secrets.token_hex(12)
        self.companion=str(Path(companion).resolve()) if companion else None
        self._monitor=None

    def redact(self,value):
        return str(value).replace(self._key,'[redacted]')[-4096:]

    def diagnostic_tail(self):
        if self.log_file is None:return ''
        try:
            self.log_file.flush()
            with Path(self.log_file.name).open('rb') as source:
                source.seek(max(0,source.seek(0,2)-4096-len(self._key)));return self.redact(source.read().decode('utf-8','replace'))
        except OSError:return ''

    def _stop_process(self):
        with self._stop_lock:
            process=self.process
            if process is not None and process.poll() is None:
                process.terminate()
                try:process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait(timeout=2)

    def _watch(self):
        while not self._done.wait(.05):
            if self.cancel.is_set():
                try:self._stop_process()
                except OSError:pass
                return

    def _request(self,route,data=None,timeout=2):
        checkpoint(self.cancel)
        request=urllib.request.Request('http://127.0.0.1:'+str(self.port)+route,
            data=None if data is None else json.dumps(data).encode('utf-8'),
            headers={'Content-Type':'application/json','Authorization':'Bearer '+self._key})
        # Ignore host proxy configuration: model traffic cannot leave loopback.
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request,timeout=timeout) as response:
            raw=response.read(8*1024*1024+1)
        checkpoint(self.cancel)
        if len(raw)>8*1024*1024:raise RunnerError('The local inference response exceeded its size limit')
        return json.loads(raw)

    def __enter__(self):
        checkpoint(self.cancel);self.port=_port();self.log_dir.mkdir(parents=True,exist_ok=True)
        try:
            self.log_file=tempfile.NamedTemporaryFile('w+b',prefix='llama-',suffix='.log',dir=self.log_dir,delete=False)
            with tempfile.NamedTemporaryFile('w',prefix='.llama-key-',dir=self.log_dir,delete=False,encoding='utf-8') as key:
                self.key_path=Path(key.name);key.write(self._key)
            os.chmod(self.key_path,0o600)
            args=[self.executable,'--model',self.model,'--alias',self.model_id,'--host','127.0.0.1','--port',str(self.port),
                  '--ctx-size',str(self.context),'--gpu-layers',str(self.gpu_layers),'--parallel','1','--no-webui','--api-key-file',str(self.key_path)]
            if self.threads>0:args.extend(['--threads',str(self.threads)])
            if self.companion:
                if not Path(self.companion).is_file():raise RunnerError('The manager-selected vision companion is unavailable')
                args.extend(['--mmproj',self.companion])
            allowed=('PATH','SystemRoot','WINDIR','TEMP','TMP','COMSPEC','PROCESSOR_ARCHITECTURE','NUMBER_OF_PROCESSORS','CUDA_PATH','CUDA_VISIBLE_DEVICES')
            environment={name:os.environ[name] for name in allowed if name in os.environ}
            environment['PATH']=str(Path(self.executable).parent)+os.pathsep+environment.get('PATH','')
            options={'stdin':subprocess.DEVNULL,'stdout':self.log_file,'stderr':subprocess.STDOUT,'shell':False,'env':environment,
                     'cwd':str(Path(self.executable).parent)}
            if os.name=='nt':options['creationflags']=subprocess.CREATE_NO_WINDOW
            checkpoint(self.cancel)
            self.process=subprocess.Popen(args,**options)
            self._monitor=threading.Thread(target=self._watch,daemon=True);self._monitor.start()
            deadline=time.monotonic()+self.startup_timeout
            while time.monotonic()<deadline:
                checkpoint(self.cancel)
                if self.process.poll() is not None:raise RunnerError('The owned llama-server exited: '+self.diagnostic_tail())
                try:
                    health=self._request('/health',timeout=.5)
                    models=self._request('/v1/models',timeout=.5)
                    if health.get('status')=='ok' and any(item.get('id')==self.model_id for item in models.get('data',[])):
                        self.runtime_proof={'kind':'local-child-server','authenticated':True,'processId':self.process.pid,
                            'executableSha256':file_identity(self.executable)['sha256'],'modelAlias':self.model_id}
                        return self
                except Cancelled:raise
                except (OSError,ValueError,urllib.error.HTTPError):pass
                self.cancel.wait(.1)
            raise RunnerError('The owned llama-server startup timed out: '+self.diagnostic_tail())
        except Exception as error:
            self.__exit__(None,None,None)
            if self.cancel.is_set():raise Cancelled('AutoLoop cancelled during runtime startup.') from None
            raise RunnerError(self.redact(error)) from None

    def ask(self,prompt,max_tokens=2048):
        checkpoint(self.cancel)
        try:
            answer=self._request('/v1/chat/completions',{'model':self.model_id,'messages':[{'role':'user','content':prompt}],
                'temperature':0,'top_p':1,'seed':1234,'stream':False,'max_tokens':max_tokens},timeout=self.request_timeout)
            content=answer['choices'][0]['message'].get('content')
            if not isinstance(content,str) or not content.strip():raise RunnerError('The model returned no final answer text')
            return content
        except Exception as error:
            if self.cancel.is_set():raise Cancelled('AutoLoop cancelled during inference.') from None
            raise RunnerError('Local inference failed: '+self.redact(error)) from None

    def __exit__(self,*args):
        self._done.set()
        try:self._stop_process()
        finally:
            if self._monitor is not None and self._monitor is not threading.current_thread():self._monitor.join(timeout=3)
            if self.key_path is not None:self.key_path.unlink(missing_ok=True)
            if self.log_file is not None:self.log_file.close()
        return False
