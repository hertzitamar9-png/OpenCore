"""Bundle public model identities, never user state or model weights, for offline browsing."""
import hashlib
import re
from urllib.parse import urlparse


def build(native,publisher):
    families={};by_file={};by_repo={}
    def source(row):
        try:
            url=urlparse(row.get('sourceUrl',''));parts=url.path.strip('/').split('/')
            return '/'.join(parts[:2]) if url.hostname=='huggingface.co' and len(parts)>=2 else ''
        except (ValueError,AttributeError):return ''
    def text_capable(row):
        return bool(row.get('supportsTextGeneration') or row.get('outputsText') or 'text' in row.get('outputModalities',[]) or row.get('category') in ('text','reasoning','brain','omni','ocr'))
    def file(row):
        repo=row.get('repository') or row.get('repo','');revision=row.get('revision','');filename=row.get('filename','');sha=row.get('sha256','');size=row.get('bytes',0)
        if not filename.lower().endswith('.gguf') or not re.fullmatch(r'[a-fA-F0-9]{40,64}',revision) or not re.fullmatch(r'[a-fA-F0-9]{64}',sha) or not isinstance(size,int) or size<=0:return None
        if not re.fullmatch(r'[\w.-]+/[\w.-]+',repo) or any(part in ('','.','..') for part in filename.split('/')) or '\\' in filename:return None
        identity=hashlib.sha256((repo+'\n'+revision+'\n'+filename).encode()).hexdigest()
        return dict(id=identity,repo=repo,revision=revision,filename=filename,sha256=sha,bytes=size)
    def family(key,label,repo,category,capable):
        if key not in families:families[key]=dict(id=key,label=label,repo=repo,category=category,textCapable=capable,files={})
        families[key]['textCapable']|=capable
        return families[key]
    for row in publisher.get('entries',[]):
        repo=row.get('originalRepository') or row.get('repository','')
        key=row.get('familyId') or 'hub-'+hashlib.sha256(repo.lower().encode()).hexdigest()[:20]
        current=family(key,row.get('familyLabel') or repo,repo,row.get('category','model'),text_capable(row));by_repo[repo.lower()]=key
        for raw in row.get('files',[]):
            value=file(raw)
            if value:current['files'][value['id']]=value;by_file[value['id']]=key
    artifacts={row['id']:row for row in native.get('artifacts',[])}
    for row in native.get('models',[]):
        files=[value for artifact in row.get('artifacts',[]) if artifact in artifacts for value in [file(artifacts[artifact])] if value]
        repo=source(row);matched=next((by_file[value['id']] for value in files if value['id'] in by_file),None)
        key=matched or by_repo.get(repo.lower()) or 'native-'+hashlib.sha256((repo or row.get('variantOf') or row['id']).lower().encode()).hexdigest()[:20]
        label=re.sub(r'\s*·\s*(?:ECHO|Native).*$', '',row.get('label',row['id']),flags=re.I)
        current=family(key,label,repo,row.get('category','model'),text_capable(row))
        for value in files:current['files'][value['id']]=value
    result=[]
    for value in families.values():
        value['files']=sorted(value['files'].values(),key=lambda item:item['filename'].lower());result.append(value)
    return dict(schema=1,families=sorted(result,key=lambda item:item['label'].lower()))
