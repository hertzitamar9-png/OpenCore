"""MiMo task runs in a pinned container, never executes dataset code on host."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import uuid

IMAGE='xiaomimimo/mimo-v2.6-rl-oss@sha256:3942cb66755b5427a79db3e9623630995a92eb2c592159b01d5bc717c947059b'
DOCKER=['wsl.exe','-d','Ubuntu-24.04','-u','root','--','/usr/bin/docker']

def docker(*args,input=None,timeout=60,check=True):
    # Text stdin on Windows rewrites LF to CRLF, corrupting Linux git patches.
    result=subprocess.run(DOCKER+list(args),input=input.encode('utf-8') if input is not None else None,capture_output=True,timeout=timeout,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    result.stdout=result.stdout.decode('utf-8',errors='replace')
    result.stderr=result.stderr.decode('utf-8',errors='replace')
    if check and result.returncode: raise RuntimeError((result.stderr or result.stdout)[-3000:])
    return result

class TaskContainer:
    def __init__(self,folder):
        self.folder=Path(folder)
        self.task=json.loads((self.folder/'mimo-task.json').read_text(encoding='utf-8'))
        self.instance=json.loads(self.task['extra_info']['instance_json'])
        if self.instance['instance_id']!='format-code-task-001457': raise ValueError('Image identity does not match task')
        self.name='opencore-mimo-'+uuid.uuid4().hex
        self.patch=(self.folder/'mimo-verifier.patch').read_text(encoding='utf-8')

    def __enter__(self):
        docker('create','--name',self.name,'--label','opencore.training=bounded-mimo-pilot','--network','none','--cpus','2','--memory','2g',
            '--pids-limit','256','--cap-drop','ALL','--security-opt','no-new-privileges','--workdir','/testbed','--entrypoint','/bin/bash',IMAGE,'-lc','sleep infinity')
        try:
            docker('start',self.name)
            docker('exec','-i',self.name,'git','apply','--',input=self.patch)
        except BaseException:
            docker('stop','--time','2',self.name,check=False);raise
        return self

    def command(self,command,timeout=30):
        if not isinstance(command,str) or len(command)>12000: raise ValueError('Invalid container command')
        result=docker('exec',self.name,'bash','-lc',command,timeout=min(timeout,60),check=False)
        return {'exit_code':result.returncode,'output':(result.stdout+'\n'+result.stderr)[-8000:]}

    def verify(self,label):
        # Actor may inspect tests but cannot change the verifier to get reward.
        # Immutable verification happens in a separate container below. This
        # container produces only the proposed source patch.
        source=docker('exec',self.name,'git','diff','--','src/action.js','action.yml','README.md').stdout
        verifier_name=self.name+'-verify'
        docker('create','--name',verifier_name,'--network','none','--cpus','2','--memory','2g','--pids-limit','256',
            '--cap-drop','ALL','--security-opt','no-new-privileges','--workdir','/testbed','--entrypoint','/bin/bash',IMAGE,'-lc','sleep infinity')
        try:
            docker('start',verifier_name)
            if source: docker('exec','-i',verifier_name,'git','apply','--',input=source)
            docker('exec','-i',verifier_name,'git','apply','--',input=self.patch)
            result=docker('exec',verifier_name,'bash','/testbed/mimo_test_command.sh',timeout=300,check=False)
            # Exit 0 from the supplied test command is the task reward, with
            # the captured immutable source patch and test output as evidence.
            output=result.stdout+'\n'+result.stderr
            tests=re.search(r'Tests:\s+(\d+) passed,\s+(\d+) total',output)
            passed=result.returncode==0 and tests is not None and int(tests[1])==int(tests[2])>=26
            record={'label':label,'image':IMAGE,'instance':self.instance['instance_id'],'exit_code':result.returncode,
                'passed':passed,'source_patch':source,'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
                'verifier_sha256':hashlib.sha256(self.patch.encode()).hexdigest(),'output':output[-24000:]}
            (self.folder/f'mimo-{label}.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
            return record
        finally: docker('stop','--time','2',verifier_name,check=False)

    def __exit__(self,*args):
        # Keep stopped containers as evidence. No prune or storage cleanup.
        docker('stop','--time','2',self.name,check=False)

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--folder',type=Path,required=True);args=parser.parse_args()
    with TaskContainer(args.folder) as task:
        result=task.verify('baseline')
        print(json.dumps({key:result[key] for key in ('instance','exit_code','passed')},indent=2),flush=True)
