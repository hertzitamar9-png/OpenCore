"""Shared, offline ECHO coverage and published-context policy for catalog builds."""
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


def metadata_reader(research):
    files={}
    for folder in (research,research/'bundled-quants',research/'requested-models'):
        for path in folder.glob('*.api.json'):
            files[path.name.casefold()]=path
    cache={}
    def read(repository):
        key=(repository or '').casefold()
        if key not in cache:
            path=files.get(key.replace('/','--')+'.api.json')
            cache[key]=json.loads(path.read_text(encoding='utf-8')) if path else {}
        return cache[key]
    return read


def native_echo_coverage(catalog,research,context_tokens):
    artifacts={item['id']:item for item in catalog['artifacts']}
    def text(model):return model.get('category') in ('text','brain')
    def identity(model):
        return tuple(sorted(artifacts[name]['sha256'] for name in model.get('weightArtifacts',model.get('artifacts',[])) if name in artifacts))
    groups={}
    for model in catalog['models']:
        if text(model) and model.get('selectable') and identity(model):
            groups.setdefault(identity(model),[]).append(model)
    changed=[]
    for models in groups.values():
        if not any(model.get('memoryMode')=='echo' for model in models):
            for model in models:
                model['memoryMode']='echo'
                model['label']=re.sub(r'\s*·\s*Native\s*$','',model['label'])
                if not re.search(r'\bECHO\b',model['label']):model['label']+=' · ECHO'
                changed.append(model['id'])
    read=metadata_reader(Path(research))
    limits_file=Path(research)/'echo-context-limits.json'
    limits=json.loads(limits_file.read_text(encoding='utf-8')).get('families',{}) if limits_file.is_file() else {}
    for model in catalog['models']:
        if not text(model):continue
        if not model.get('selectable'):model['memoryMode']='echo'
        if model.get('memoryMode')!='echo':continue
        existing=int(model.get('contextTokens') or 32768)
        target=min(existing,32768)
        verified=limits.get(model.get('variantOf') or model['id'])
        if verified:target=min(32768,int(verified['publishedContextTokens']))
        if model.get('backend') in ('gguf','managed-text'):
            source=urlsplit(model.get('sourceUrl') or '').path.strip('/').split('/')
            repository='/'.join(source[:2]) if len(source)>=2 else ''
            selected=[artifacts[name]['repo'] for name in model.get('weightArtifacts',model.get('artifacts',[])) if name in artifacts]
            for candidate in [repository,*selected]:
                metadata=read(candidate)
                if metadata:
                    target=context_tokens(metadata,fallback=target)
                    break
            model['contextTokens']=target
            if target!=existing:
                for field in ('note','description'):
                    if isinstance(model.get(field),str):model[field]=re.sub(r'\b(?:8|16)K context\b',str(target//1024)+'K context',model[field])
        model['echoWorkingWindowTokens']=target
    return {'eligibleWeightPackages':len(groups),'promotedModelIds':changed,
            'missingEchoWeightPackages':sum(not any(m.get('memoryMode')=='echo' for m in models) for models in groups.values())}


def publisher_echo_policy(entries,research,context_tokens):
    read=metadata_reader(Path(research))
    for entry in entries:
        category=entry.get('category') or entry.get('libraryCategory') or ('text' if entry.get('format') in ('gguf','safetensors') else '')
        if category!='text':continue
        source=read(entry.get('originalRepository')) or read(entry.get('repository'))
        entry.update(category='text',memoryMode='echo',contextTokens=context_tokens(source),echoWorkingWindowTokens=context_tokens(source))
    return entries
