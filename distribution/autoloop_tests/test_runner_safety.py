"""Owned process doubles and loopback request doubles; no model executable runs."""
import json
from pathlib import Path
import threading
import time

import pytest

from autoloop_vendor.runner import LocalServer,RunnerError,find_server
from autoloop_vendor.errors import Cancelled


class Process:
    pid=4141
    def __init__(self):self.returncode=None;self.terminations=0;self.kills=0;self.stopped=threading.Event()
    def poll(self):return self.returncode
    def terminate(self):self.terminations+=1;self.returncode=0;self.stopped.set()
    def kill(self):self.kills+=1;self.returncode=0;self.stopped.set()
    def wait(self,timeout=None):return self.returncode


@pytest.fixture
def runtime(tmp_path,monkeypatch):
    executable=tmp_path/'owned-llama-server.exe';executable.write_bytes(b'synthetic executable, never run')
    model=tmp_path/'model.gguf';model.write_bytes(b'synthetic model, never loaded')
    process=Process();calls=[]
    def launch(args,**options):calls.append((args,options));return process
    monkeypatch.setattr('autoloop_vendor.runner.subprocess.Popen',launch)
    def request(self,route,data=None,timeout=2):
        if route=='/health':return{'status':'ok'}
        if route=='/v1/models':return{'data':[{'id':self.model_id}]}
        return{'choices':[{'message':{'content':'42'}}]}
    monkeypatch.setattr(LocalServer,'_request',request)
    return executable,model,process,calls,tmp_path/'logs'


def test_explicit_owned_runtime_is_required_and_path_lookup_is_not_used(tmp_path):
    for value in (None,'llama-server.exe',str(tmp_path/'missing.exe')):
        with pytest.raises(RunnerError,match='absolute pinned'):find_server(value)


def test_child_is_loopback_authenticated_cpu_only_and_not_started_through_a_shell(runtime,monkeypatch):
    executable,model,process,calls,logs=runtime
    monkeypatch.setenv('HF_TOKEN','fixture-credential-must-not-reach-child')
    monkeypatch.setenv('OPENAI_API_KEY','fixture-credential-must-not-reach-child')
    with LocalServer(str(executable),model,logs) as server:
        args,options=calls[0]
        assert args[args.index('--host')+1]=='127.0.0.1'
        assert args[args.index('--gpu-layers')+1]=='0'
        assert args[args.index('--threads')+1]=='2'
        assert '--api-key-file' in args and server._key not in ' '.join(args)
        assert options['shell'] is False
        assert 'HF_TOKEN' not in options['env'] and 'OPENAI_API_KEY' not in options['env']
        assert server.ask('6 * 7')=='42'
        key=server.key_path;assert key.is_file()
        assert server.runtime_proof['authenticated'] is True
    assert process.terminations==1 and not key.exists()


def test_cancel_before_start_launches_nothing(runtime):
    executable,model,process,calls,logs=runtime
    cancel=threading.Event();cancel.set()
    with pytest.raises(Cancelled):LocalServer(str(executable),model,logs,cancel=cancel).__enter__()
    assert calls==[] and process.terminations==0


def test_cancellation_interrupts_a_question_by_terminating_only_the_owned_child(runtime,monkeypatch):
    executable,model,process,calls,logs=runtime
    cancel=threading.Event();entered=threading.Event();errors=[]
    original=LocalServer._request
    def request(self,route,data=None,timeout=2):
        if data is not None:
            entered.set();process.stopped.wait(2);raise OSError('connection closed')
        return original(self,route,data,timeout)
    monkeypatch.setattr(LocalServer,'_request',request)
    with LocalServer(str(executable),model,logs,cancel=cancel) as server:
        def question():
            try:server.ask('a bounded fixture question')
            except Exception as error:errors.append(error)
        thread=threading.Thread(target=question);thread.start();assert entered.wait(1)
        cancel.set();thread.join(2)
        assert not thread.is_alive() and isinstance(errors[0],Cancelled)
    assert process.terminations==1 and len(calls)==1


def test_startup_cancellation_does_not_leave_a_child_or_key(runtime,monkeypatch):
    executable,model,process,calls,logs=runtime
    cancel=threading.Event()
    def request(self,*args,**kwargs):cancel.set();raise OSError('not ready')
    monkeypatch.setattr(LocalServer,'_request',request)
    server=LocalServer(str(executable),model,logs,cancel=cancel)
    with pytest.raises(Cancelled):server.__enter__()
    assert process.terminations==1 and not server.key_path.exists()


def test_wrong_advertised_model_is_not_accepted_as_the_owned_checkpoint(runtime,monkeypatch):
    executable,model,process,calls,logs=runtime
    monkeypatch.setattr(LocalServer,'_request',lambda self,route,**kwargs:{'status':'ok'} if route=='/health' else {'data':[{'id':'unrelated-already-running-model'}]})
    with pytest.raises(RunnerError,match='timed out'):
        with LocalServer(str(executable),model,logs,startup_timeout=.1):pass
    assert process.terminations==1


def test_diagnostic_tail_and_exception_redact_the_private_api_key(runtime,monkeypatch):
    executable,model,process,calls,logs=runtime
    with LocalServer(str(executable),model,logs) as server:
        secret=server._key
        server.log_file.write((b'x'*10000)+secret.encode());server.log_file.flush()
        assert len(server.diagnostic_tail())<=4096 and secret not in server.diagnostic_tail()
        def failure(*args,**kwargs):raise ValueError('request rejected '+secret)
        monkeypatch.setattr(server,'_request',failure)
        with pytest.raises(RunnerError) as caught:server.ask('fixture')
        assert secret not in str(caught.value) and '[redacted]' in str(caught.value)


def test_http_requests_authenticate_and_ignore_host_proxy_settings(runtime,monkeypatch):
    executable,model,process,calls,logs=runtime
    captured={}
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,limit):return b'{"status":"ok"}'
    class Opener:
        def open(self,request,timeout):captured.update(request=request,timeout=timeout);return Response()
    def opener(handler):captured['proxies']=handler.proxies;return Opener()
    from autoloop_vendor import runner
    monkeypatch.setattr(runner.urllib.request,'build_opener',opener)
    server=LocalServer(str(executable),model,logs);server.port=12345
    result=REAL_REQUEST(server,'/health',timeout=.5)
    assert result=={'status':'ok'} and captured['proxies']=={}
    assert captured['request'].get_header('Authorization')=='Bearer '+server._key
    assert captured['request'].full_url=='http://127.0.0.1:12345/health'


def test_key_file_creation_failure_closes_the_owned_log_without_starting_a_process(runtime,monkeypatch):
    executable,model,process,calls,logs=runtime
    from autoloop_vendor import runner
    create=runner.tempfile.NamedTemporaryFile
    def temporary(*args,**kwargs):
        if kwargs.get('prefix')=='.llama-key-':raise OSError('No space for private key file')
        return create(*args,**kwargs)
    monkeypatch.setattr(runner.tempfile,'NamedTemporaryFile',temporary)
    server=LocalServer(str(executable),model,logs)
    with pytest.raises(RunnerError,match='private key file'):server.__enter__()
    assert server.log_file.closed and calls==[]


def test_diagnostic_boundary_does_not_reveal_a_truncated_private_key(runtime):
    executable,model,process,calls,logs=runtime
    with LocalServer(str(executable),model,logs) as server:
        server.log_file.write(server._key.encode()+b'x'*4080);server.log_file.flush()
        assert server._key[-10:] not in server.diagnostic_tail()


REAL_REQUEST=LocalServer._request
