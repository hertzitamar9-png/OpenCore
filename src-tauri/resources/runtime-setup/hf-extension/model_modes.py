"""Shared operating-mode policy for catalog generation and managed execution."""
import copy
import json
from pathlib import Path

NATIVE_TARGET = 1048576
ECHO_TARGET = 32768

def positive(value):
    return value if type(value) is int and 0 < value <= 16 * NATIVE_TARGET else None

def published_context_tokens(info):
    config=info.get('config') or {}
    for data in (config.get('text_config') or {}, config, info.get('gguf') or {}, info.get('tokenizer_config') or {}):
        for key in ('max_position_embeddings','max_seq_len','max_sequence_length','maxSeqLen','seq_length','n_positions','context_length','model_max_length'):
            value=positive(data.get(key))
            if value:return value
    return None

def supports_text_generation(model, info=None):
    info=info or {}
    if model.get('supportsTextChat') is False or model.get('computerRuntime')=='gui-jepa-v1':return False
    category=model.get('category','text')
    if category in ('text','brain'):return True
    if category in ('speech','tts','voice-cloning','music','embedding','classification','image','video','3d','2d-animation','3d-animation','policy'):return False
    if (info.get('pipeline_tag') or model.get('task')) in ('text-generation','text2text-generation','image-text-to-text'):return True
    return category in ('omni','computer-use','ocr') and 'text' in model.get('inputModalities',[]) and 'text' in model.get('outputModalities',[])

def descriptors(model, info=None):
    info=info or {};published=published_context_tokens(info) or positive(model.get('publishedContextTokens'))
    # Existing supported context is a conservative fallback, not evidence for 1M.
    available=published or positive(model.get('contextTokens'))
    runtime_cap=positive(model.get('supportedContextTokens'))
    if runtime_cap:available=min(available,runtime_cap) if available else runtime_cap
    runtime=model.get('runtime') or {}
    supported=bool(runtime.get('adapter') or model.get('backend')=='gguf' or model.get('preparedRuntime')) and model.get('runtimeReady') is not False
    if 'runtime' in model:supported=bool(runtime.get('adapter'))
    choices=[]
    for mode,target in (('native',NATIVE_TARGET),('echo',ECHO_TARGET)):
        known=[value for value in (available,positive(model.get('echoWorkingWindowTokens')) if mode=='echo' else None) if value]
        choices.append({'id':mode,'targetContextTokens':target,'contextTokens':min(target,*known) if known else None,'supported':supported})
    return published,choices

def apply_operating_modes(models, metadata=lambda _:{}):
    for model in models:
        info=metadata(model) or {}
        enabled=supports_text_generation(model,info)
        model['supportsTextGeneration']=enabled
        model['operatingModes']=[]
        if enabled:
            model['publishedContextTokens'],model['operatingModes']=descriptors(model,info)
    return models

def mode_context(entry, mode=None):
    mode=mode or entry.get('operatingMode','echo')
    if mode not in ('echo','native'):raise ValueError('Choose Native or ECHO')
    for choice in entry.get('operatingModes') or []:
        if choice['id']==mode and positive(choice.get('contextTokens')):return choice['contextTokens']
    target=ECHO_TARGET if mode=='echo' else NATIVE_TARGET
    supported=positive(entry.get('publishedContextTokens')) or positive(entry.get('contextTokens')) or ECHO_TARGET
    return min(target,supported,positive(entry.get('supportedContextTokens')) or supported)

def snapshot_metadata(root):
    root=Path(root);cache={}
    for directory in (root,root/'requested-models',root/'builtin-repos',root/'bundled-quants',root/'catalog-observations'):
        for file in directory.glob('*.api.json'):
            try:
                value=json.loads(file.read_text(encoding='utf-8'));key=value.get('id')
                if key:cache[key.lower()]=value
            except (OSError,ValueError):pass
    def find(model):
        repo=model.get('originalRepository') or model.get('repository')
        if not repo:
            source=model.get('sourceUrl','')
            if 'huggingface.co/' in source:repo='/'.join(source.split('huggingface.co/',1)[1].split('/')[:2])
        return cache.get(str(repo).lower(),{})
    return find

def native_model_bindings(catalog):
    models=[copy.deepcopy(model) for model in catalog['models'] if model.get('supportsTextGeneration')]
    used={key for model in models for key in model.get('artifacts',[])}
    return {'schema':1,'models':models,'artifacts':[copy.deepcopy(a) for a in catalog['artifacts'] if a['id'] in used]}

def native_mode_aliases(catalog):
    """The existing generic GGUF loader reads modes from its pinned catalog.

    Aliases are execution profiles only: identical artifact IDs and paths, no
    second weight package. The frontend collapses these to one quant choice.
    """
    models=catalog['models'];by_id={m['id']:m for m in models};aliases=[]
    def family(model):
        seen=set()
        while model.get('variantOf') in by_id and model['id'] not in seen:
            seen.add(model['id']);model=by_id[model['variantOf']]
        return model['id']
    packages={}
    physical={}
    for model in models:
        if model.get('supportsTextGeneration') and model.get('artifacts'):
            physical.setdefault((tuple(sorted(model['artifacts'])),model.get('precision')),[]).append(model)
    for variants in physical.values():
        if len({m.get('memoryMode','native') for m in variants})<2:continue
        preferred=next((m for m in variants if m.get('memoryMode')=='echo'),variants[0]);parent=family(preferred)
        for model in variants:
            if family(model)!=parent:model['variantOf']=parent
    for model in models:
        if model.get('supportsTextGeneration') and model.get('artifacts'):
            key=(family(model),tuple(model.get('artifacts',[])),model.get('precision'))
            packages.setdefault(key,[]).append(model)
    for (parent,_,_),variants in packages.items():
        base=variants[0];profiles={}
        for model in variants:profiles.setdefault(model.get('memoryMode','native'),model['id'])
        if base.get('backend')=='gguf':
            # Earlier catalog rows used different safety caps for the same
            # checkpoint. Both modes now derive their limits from one policy.
            published=next((positive(m.get('publishedContextTokens')) for m in variants if positive(m.get('publishedContextTokens'))),None)
            available=published or max((positive(m.get('contextTokens')) or 0 for m in variants),default=0)
            caps=[positive(m.get('supportedContextTokens')) for m in variants if positive(m.get('supportedContextTokens'))]
            if caps:available=min(available,*caps) if available else min(caps)
            modes=copy.deepcopy(base['operatingModes'])
            for choice in modes:
                limits=[available] if available else []
                if choice['id']=='echo':limits.extend(positive(m.get('echoWorkingWindowTokens')) for m in variants if positive(m.get('echoWorkingWindowTokens')))
                choice['contextTokens']=min(choice['targetContextTokens'],*limits) if limits else None
            for model in variants:
                model['operatingModes']=copy.deepcopy(modes)
                if published:model['publishedContextTokens']=published
        for mode in ('native','echo'):
            if mode not in profiles:
                if base.get('backend')!='gguf' or base.get('preparedRuntime') or base.get('runtimeReady') is False:continue
                alias=copy.deepcopy(base);identity=base['id']+'-mode-'+mode
                if identity in by_id:raise ValueError('Operating profile ID collision')
                alias.update(id=identity,variantOf=parent,memoryMode=mode,modeAliasOf=base['id'],label=base['label'].replace(' · ECHO','')+(' · ECHO' if mode=='echo' else ' · Native'))
                aliases.append(alias);by_id[identity]=alias;profiles[mode]=identity
        for model in variants:
            if len(profiles)==2:
                model['modeProfiles']=dict(profiles)
                if model.get('backend')=='gguf':model['contextTokens']=mode_context(model,model.get('memoryMode','native'))
        for alias in aliases:
            if alias.get('modeAliasOf')==base['id']:
                alias['modeProfiles']=dict(profiles);alias['contextTokens']=mode_context(alias,alias['memoryMode'])
    models.extend(aliases)
    return len(aliases)

def native_catalog(catalog):
    result=copy.deepcopy(catalog)
    for model in result['models']:
        for key in ('operatingModes','publishedContextTokens','supportsTextGeneration','modeProfiles','modeAliasOf'):
            model.pop(key,None)
    return result
