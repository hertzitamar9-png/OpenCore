"""Bounded, conversation-scoped sampler overrides; no prompts or model weights."""
import json,math,os,tempfile
from pathlib import Path

LIMITS={'temperature':(0,2,False),'top_p':(.01,1,False),'top_k':(0,1000,True),
        'repeat_penalty':(.5,2,False),'max_tokens':(16,32768,True),'seed':(0,4294967295,True)}

def normalize(value):
    if value is None:return {}
    if not isinstance(value,dict) or set(value)-set(LIMITS):raise ValueError('Unsupported generation parameter')
    result={}
    for key,number in value.items():
        low,high,integer=LIMITS[key]
        if type(number) not in (int,float) or not math.isfinite(number) or not low<=number<=high or integer and int(number)!=number:
            raise ValueError('Invalid generation parameter: '+key)
        result[key]=int(number) if integer else number
    return result

def load(path):
    path=Path(path)
    try:
        if path.stat().st_size>256*1024:return {}
        value=json.loads(path.read_text(encoding='utf-8'))
        return value.get('conversations',{}) if value.get('schema')==1 and isinstance(value.get('conversations'),dict) else {}
    except (OSError,ValueError,TypeError,AttributeError):return {}

def save(path,conversation,settings):
    if not isinstance(conversation,str) or not 1<=len(conversation)<=256 or any(ord(c)<32 for c in conversation):raise ValueError('A bounded conversation ID is required')
    parameters=normalize(settings);entries=load(path);entries.pop(conversation,None)
    if parameters:entries[conversation]=parameters
    entries=dict(list(entries.items())[-256:])
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent,suffix='.tmp',mode='w',encoding='utf-8',delete=False) as stream:
        json.dump({'schema':1,'conversations':entries},stream,separators=(',',':'));temporary=Path(stream.name)
    try:os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)
    return {'saved':True,'mode':'manual' if parameters else 'automatic','parameters':parameters}

def apply(path,conversation,payload):
    try:parameters=normalize(load(path).get(conversation))
    except ValueError:parameters={}
    if not parameters:return payload
    result={**payload,**parameters}
    if 'max_tokens' in parameters:
        result.pop('max_completion_tokens',None)
        result['echo_max_total_tokens']=parameters['max_tokens']
    return result
