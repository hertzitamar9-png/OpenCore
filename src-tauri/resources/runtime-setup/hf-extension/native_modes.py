"""Select a pinned native execution profile without copying checkpoint files."""
import hashlib
import json
from pathlib import Path
import re
import threading
from hf_download import managed_path, atomic_json

_lock=threading.RLock()
_verified={}
_id=re.compile(r'^[A-Za-z0-9_-]{1,200}$')

def regular(root, relative):
    path=managed_path(root,*relative.split('/'))
    current=Path(root)
    for piece in relative.split('/'):
        if piece in ('','.','..') or '\\' in piece or ':' in piece:raise ValueError('Invalid native model binding path')
        current=current/piece
        if current.exists() or current.is_symlink():
            info=current.lstat()
            if current.is_symlink() or getattr(info,'st_file_attributes',0)&0x400:
                raise ValueError('Linked native model paths cannot be admitted')
    return path

def activate(root, resources, model_id, mode):
    if not isinstance(model_id,str) or not _id.fullmatch(model_id) or mode not in ('native','echo'):
        raise ValueError('Choose a valid native model and operating mode')
    root=Path(root).resolve();binding=Path(resources)/'runtime-setup/hf-extension/native-model-bindings.json'
    if binding.stat().st_size>16*1024*1024:raise ValueError('Native model bindings are too large')
    data=json.loads(binding.read_text(encoding='utf-8'));models={m['id']:m for m in data['models']}
    model=models.get(model_id)
    if not model or model.get('backend') not in (None,'gguf'):
        raise ValueError('This checkpoint needs its managed runtime before selecting this operating mode')
    profile=model.get('modeProfiles',{}).get(mode)
    target=models.get(profile)
    if not target or target.get('artifacts')!=model.get('artifacts'):
        raise ValueError('This model has no verified matching execution profile')
    artifacts={a['id']:a for a in data['artifacts']}
    siblings=[m for m in models.values() if m.get('modeProfiles')==model.get('modeProfiles') and m.get('artifacts')==model['artifacts']]
    with _lock:
        receipts=[]
        for sibling in siblings:
            path=regular(root,'models/receipts/model-'+sibling['id']+'.json')
            if path.exists():
                if path.stat().st_size>65536:raise ValueError('Native model receipt is invalid')
                value=json.loads(path.read_text(encoding='utf-8'))
                if value!=model['artifacts']:raise ValueError('Native model receipt does not match this checkpoint')
                receipts.append(path)
        if not receipts:raise ValueError('Install and verify this quantization before choosing its operating mode')
        for artifact_id in model['artifacts']:
            artifact=artifacts[artifact_id];path=regular(root,artifact['path']);info=path.stat()
            if not path.is_file() or info.st_size!=artifact['bytes']:raise ValueError('A selected model file is missing or changed')
            signature=(str(path),info.st_size,info.st_mtime_ns,artifact['sha256'])
            if signature not in _verified:
                with path.open('rb') as stream:observed=hashlib.file_digest(stream,'sha256').hexdigest()
                if observed not in [artifact['sha256'],*artifact.get('compatibleLocalSha256',[])]:
                    raise ValueError('A selected native model file failed its pinned checksum')
                _verified[signature]=True
        selected=regular(root,'models/receipts/model-'+profile+'.json')
        # Only one receipt for this physical quant is active. Uninstall therefore
        # sees one package rather than retaining weights for a hidden mode alias.
        if selected not in receipts:
            atomic_json(selected,model['artifacts'])
        for path in receipts:
            if path!=selected:path.unlink()
        return {'profileId':profile,'operatingMode':mode,'contextTokens':target['contextTokens'],'weightsReused':True}
