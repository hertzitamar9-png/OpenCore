"""Immutable, offline container rewards; never execute generated code on host."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import uuid
from mimo_verifier import docker,WSL

IMAGE='sha256:ae9b065afe8207a4bb2974504d64085dca23459ebfd4fa30ed8ff9364c209ac6'


def extract_function(source,name):
    if len(source)>12000: raise ValueError('Oversized generated function')
    tree=ast.parse(source)
    if len(tree.body)!=1 or not isinstance(tree.body[0],ast.FunctionDef) or tree.body[0].name!=name:
        raise ValueError('Expected exactly the requested function')
    function=tree.body[0]
    if function.decorator_list or function.returns or any(arg.annotation for arg in ast.walk(function) if isinstance(arg,ast.arg)):
        raise ValueError('Executable decorators/annotations are not allowed')
    for node in ast.walk(tree):
        if isinstance(node,(ast.Import,ast.ImportFrom,ast.Global,ast.Nonlocal,ast.ClassDef)):
            raise ValueError('Only a self-contained function is allowed')
        # Ordinary locals such as `_`, `_count` and `_seen` are legal Python.
        # Restrict interpreter namespaces and private attribute introspection,
        # rather than misgrading those variables as sandbox escapes.
        if ((isinstance(node,ast.Name) and node.id.startswith('__')) or
                (isinstance(node,ast.Attribute) and node.attr.startswith('_'))):
            raise ValueError('Interpreter internals are not allowed')
    return source


def verifier_payload(task,source):
    return {'source':extract_function(source,task['name']),'name':task['name'],'cases':task['cases']}


def validate_report(report,count):
    outcomes=report.get('outcomes')
    if not isinstance(outcomes,list) or len(outcomes)!=count or any(type(x) is not bool for x in outcomes):
        raise RuntimeError('Verifier did not return all immutable case outcomes')
    return {'reward':sum(outcomes)/count,'all_passed':all(outcomes),'outcomes':outcomes,
            'failures':report.get('failures',[])}


class CodingVerifier:
    def __init__(self,folder):
        self.folder=Path(folder);self.lease=None
        self.worker=Path(__file__).with_name('rl_verify_worker.py').read_text(encoding='utf-8')
        self.worker_sha=hashlib.sha256(self.worker.encode()).hexdigest()
        self.verifier_sha=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

    def __enter__(self):
        self.lease=subprocess.Popen(WSL+['/bin/cat'],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        # Exact locally installed image; no registry pull, network or host mount.
        docker('image','inspect',IMAGE)
        return self

    def verify(self,task,source,label,truncated=False):
        base={'label':label,'task_id':task['id'],'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
              'worker_sha256':self.worker_sha,'verifier_sha256':self.verifier_sha,'image':IMAGE,'truncated':truncated}
        try: payload=verifier_payload(task,source)
        except (SyntaxError,ValueError) as error:
            base.update(reward=0.,all_passed=False,outcomes=[False]*len(task['cases']),reason=str(error))
        else:
            if truncated:
                base.update(reward=0.,all_passed=False,outcomes=[False]*len(task['cases']),reason='token cap; incomplete rollout')
            else:
                name='opencore-rl-'+uuid.uuid4().hex
                result=docker('run','--name',name,'--label','opencore.training=coding-rl-v1',
                    '--pull','never','--network','none','--read-only','--user','65534:65534',
                    '--cpus','1','--memory','384m','--pids-limit','32','--cap-drop','ALL',
                    '--security-opt','no-new-privileges','--tmpfs','/tmp:rw,noexec,nosuid,size=16m',
                    '--entrypoint','python', '-i',IMAGE,'-I','-c',self.worker,
                    input=json.dumps(payload,ensure_ascii=False),timeout=30,check=False)
                # Container already exits. Preserve it as evidence; no prune.
                if result.returncode: raise RuntimeError('Isolated verifier failed: '+result.stderr[-1500:])
                try: report=json.loads(result.stdout)
                except ValueError as error: raise RuntimeError('Invalid trusted verifier response') from error
                base.update(validate_report(report,len(task['cases'])),container=name)
        target=self.folder/(label+'-reward.json')
        target.write_text(json.dumps(base,indent=2,ensure_ascii=False),encoding='utf-8')
        return base

    def __exit__(self,*args):
        if self.lease:
            self.lease.stdin.close()
            try: self.lease.wait(timeout=5)
            except subprocess.TimeoutExpired: self.lease.terminate();self.lease.wait(timeout=5)
