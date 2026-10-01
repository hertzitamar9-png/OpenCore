"""Trusted reward parent. This file runs ONLY inside the isolated container.

Each generated function runs in a fresh restricted child. Expected answers stay
in this parent, outside the child's namespace. Stdout is not a reward channel.
"""
import json
import subprocess
import sys

CHILD=r'''
import ast,json,sys,resource
resource.setrlimit(resource.RLIMIT_CPU,(1,1))
resource.setrlimit(resource.RLIMIT_AS,(256<<20,256<<20))
resource.setrlimit(resource.RLIMIT_FSIZE,(0,0))
value=json.load(sys.stdin)
tree=ast.parse(value['source'])
safe={name:getattr(__builtins__,name) for name in (
    'abs','all','any','bool','dict','enumerate','filter','float','int','isinstance',
    'len','list','map','max','min','next','iter','pow','divmod','chr','ord','bin','oct','hex',
    'bytes','bytearray','format','repr','range','reversed','round','set','slice','sorted',
    'str','sum','tuple','zip','ValueError','TypeError','Exception')}
scope={'__builtins__':safe}
exec(compile(tree,'<sample>','exec'),scope)
answer=scope[value['name']](*value['args'])
sys.stdout.write(json.dumps(answer,ensure_ascii=False,allow_nan=False))
'''


def equal(actual,expected):
    # bool is an int subclass, but a boolean is not the requested integer API.
    if type(actual) is not type(expected): return False
    if isinstance(expected,list):
        return len(actual)==len(expected) and all(equal(a,b) for a,b in zip(actual,expected))
    if isinstance(expected,dict):
        return actual.keys()==expected.keys() and all(equal(actual[k],v) for k,v in expected.items())
    return actual==expected

if __name__=='__main__':
    payload=json.load(sys.stdin);outcomes=[];failures=[]
    for case in payload['cases']:
        child_input={key:payload[key] for key in ('source','name')}
        child_input['args']=case['args']
        try:
            result=subprocess.run([sys.executable,'-I','-c',CHILD],input=json.dumps(child_input),
                                  text=True,capture_output=True,timeout=2)
            answer=json.loads(result.stdout) if result.returncode==0 and len(result.stdout)<=32768 else None
            passed=result.returncode==0 and equal(answer,case['expected'])
            failures.append(None if passed else (result.stderr[-600:] or 'wrong answer'))
        except (subprocess.TimeoutExpired,ValueError):
            passed=False;failures.append('timeout or malformed result')
        outcomes.append(passed)
    print(json.dumps({'outcomes':outcomes,'failures':failures}))
