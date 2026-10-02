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


def mismatch_diagnostic(actual,expected):
    def describe(value):
        encoded=json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)
        if len(encoded)>230: encoded=encoded[:210]+'... [truncated]'
        return type(value).__name__+' '+encoded
    return 'wrong answer: expected '+describe(expected)+', received '+describe(actual)


def grade_result(returncode,stdout,stderr,expected):
    if returncode!=0:
        return False,stderr[-600:] or 'child failed with exit code '+str(returncode)
    if len(stdout)>32768:
        return False,'oversized result'
    try: answer=json.loads(stdout)
    except ValueError: return False,'malformed result'
    passed=equal(answer,expected)
    return passed,None if passed else mismatch_diagnostic(answer,expected)

if __name__=='__main__':
    payload=json.load(sys.stdin);outcomes=[];failures=[]
    for case in payload['cases']:
        child_input={key:payload[key] for key in ('source','name')}
        child_input['args']=case['args']
        try:
            result=subprocess.run([sys.executable,'-I','-c',CHILD],input=json.dumps(child_input),
                                  text=True,capture_output=True,timeout=2)
            passed,reason=grade_result(result.returncode,result.stdout,result.stderr,case['expected'])
            failures.append(reason)
        except (subprocess.TimeoutExpired,ValueError):
            passed=False;failures.append('timeout or malformed result')
        outcomes.append(passed)
    print(json.dumps({'outcomes':outcomes,'failures':failures}))
