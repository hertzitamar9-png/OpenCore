"""Bundle public model identities, never user state or model weights, for offline browsing."""
import hashlib
import re
from urllib.parse import urlparse


def build(native,publisher):
    families={};by_file={};by_repo={}
    def chat_template(row,weight):
        declaration=row.get('chatTemplate')
        if declaration is None:return None
        if not isinstance(declaration,dict):raise ValueError('Invalid external chat template declaration')
        repo=declaration.get('repo');revision=declaration.get('revision');name=declaration.get('filename','');size=declaration.get('bytes');sha=declaration.get('sha256','');blob=declaration.get('gitBlobSha1')
        pins=[pin for pin in row.get('files',[]) if pin.get('filename')==name]
        if (repo!=weight['repo'] or revision!=weight['revision'] or type(size) is not int or not 0<size<1048576
                or not re.fullmatch('[a-f0-9]{64}',sha) or not name or name.startswith('/') or '\\' in name
                or any(part in ('','.','..') for part in name.split('/')) or len(pins)!=1):
            raise ValueError('The external chat template does not belong to this model')
        pin=pins[0]
        if (pin.get('role')!='support' or pin.get('pinned') is not True or pin.get('bytes')!=size
                or pin.get('repository')!=repo or pin.get('revision')!=revision or pin.get('gitBlobSha1')!=blob
                or pin.get('sha256') and pin['sha256']!=sha
                or not pin.get('sha256') and not (isinstance(blob,str) and re.fullmatch('[a-f0-9]{40}',blob))):
            raise ValueError('The external chat template has no matching support pin')
        return dict(repo=repo,revision=revision,filename=name,bytes=size,sha256=sha,gitBlobSha1=blob,
                    modelFilename=weight['filename'],modelSha256=weight['sha256'])
    def source(row):
        try:
            url=urlparse(row.get('sourceUrl',''));parts=url.path.strip('/').split('/')
            return '/'.join(parts[:2]) if url.hostname=='huggingface.co' and len(parts)>=2 else ''
        except (ValueError,AttributeError):return ''
    def text_capable(row):
        return bool(row.get('supportsTextGeneration') or row.get('outputsText') or 'text' in row.get('outputModalities',[]) or row.get('category') in ('text','reasoning','brain','omni','ocr'))
    def file(row,package=None):
        repo=row.get('repository') or row.get('repo','');revision=row.get('revision','');filename=row.get('filename','');sha=row.get('sha256','');size=row.get('bytes',0)
        litert=filename.lower().endswith('.litertlm')
        if not filename.lower().endswith('.gguf') and not litert:return None
        if litert and (not package or package.get('format')!='litertlm' or repo!='litert-community/LFM3-1.7B-LiteRT'):return None
        if not re.fullmatch(r'[a-fA-F0-9]{40,64}',revision) or not re.fullmatch(r'[a-fA-F0-9]{64}',sha) or not isinstance(size,int) or size<=0:return None
        if not re.fullmatch(r'[\w.-]+/[\w.-]+',repo) or any(part in ('','.','..') for part in filename.split('/')) or '\\' in filename:return None
        identity=hashlib.sha256((repo+'\n'+revision+'\n'+filename).encode()).hexdigest()
        value=dict(id=identity,repo=repo,revision=revision,filename=filename,sha256=sha,bytes=size)
        if litert:
            cap=package.get('supportedContextTokens')
            if cap not in (2048,4096) or not filename.endswith('_ekv'+str(cap)+'.litertlm'):raise ValueError('LiteRT context differs from its published cache size')
            value.update(format='litertlm',engine='litertlm',contextTokens=cap,architecture='lfm3',supportsTextGeneration=True)
        return value
    def family(key,label,repo,category,capable):
        if key not in families:families[key]=dict(id=key,label=label,repo=repo,category=category,textCapable=capable,files={})
        families[key]['textCapable']|=capable
        return families[key]
    for row in publisher.get('entries',[]):
        repo=row.get('originalRepository') or row.get('repository','')
        key=row.get('familyId') or 'hub-'+hashlib.sha256(repo.lower().encode()).hexdigest()[:20]
        current=family(key,row.get('familyLabel') or repo,repo,row.get('category','model'),text_capable(row));by_repo[repo.lower()]=key
        for raw in row.get('files',[]):
            value=file(raw,row)
            if value:
                if raw.get('role')=='model':
                    template=chat_template(row,value)
                    if template:value['chatTemplate']=template
                current['files'][value['id']]=value;by_file[value['id']]=key
    artifacts={row['id']:row for row in native.get('artifacts',[])}
    for row in native.get('models',[]):
        files=[value for artifact in row.get('artifacts',[]) if artifact in artifacts for value in [file(artifacts[artifact])] if value]
        repo=source(row);matched=next((by_file[value['id']] for value in files if value['id'] in by_file),None)
        key=matched or by_repo.get(repo.lower()) or 'native-'+hashlib.sha256((repo or row.get('variantOf') or row['id']).lower().encode()).hexdigest()[:20]
        label=re.sub(r'\s*·\s*(?:ECHO|Native).*$', '',row.get('label',row['id']),flags=re.I)
        current=family(key,label,repo,row.get('category','model'),text_capable(row))
        standalone=[value for value in files if not re.search(r'(?i)(?:mmproj|projector|lora|adapter|-\d{5}-of-\d{5}\.gguf$)',value['filename'])]
        for value in files:
            previous=current['files'].get(value['id'],{})
            if previous.get('chatTemplate'):value['chatTemplate']=previous['chatTemplate']
            profiles={item['id']:item for item in previous.get('profiles',[])}
            # Profile names must retain their desktop identity. A standalone
            # phone file is still downloaded once when Native/ECHO share it.
            if text_capable(row) and len(standalone)==1 and value['id']==standalone[0]['id']:
                profiles[row['id']]=dict(id=row['id'],label=row.get('label',row['id']),memoryMode=row.get('memoryMode','native'),
                                        contextTokens=row.get('contextTokens',2048),precision=row.get('precision','GGUF'),
                                        modeFamilyKey=row.get('familyId') or row.get('variantOf') or row['id'])
            if profiles:value['profiles']=list(profiles.values())
            current['files'][value['id']]=value
    result=[]
    for value in families.values():
        value['files']=sorted(value['files'].values(),key=lambda item:item['filename'].lower());result.append(value)
    return dict(schema=1,families=sorted(result,key=lambda item:item['label'].lower()))
