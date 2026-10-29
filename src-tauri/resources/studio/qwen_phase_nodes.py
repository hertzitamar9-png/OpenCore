"""Job-local prompt tensors between isolated encoder and sampler processes.

No model weights or executable objects are serialized. The path is supplied by
the parent worker, never by a graph or a remote request.
"""
import os
import json
import math
import struct
from pathlib import Path
import torch


def load_owned_safetensors(path,device=None):
    """Read each tensor into its final owned buffer, without a file mapping.

    safe_open().get_tensor().clone() retains the mapping while all clones are
    assembled, nearly doubling the checkpoint's CPU loading peak on Windows.
    A bytearray retained by Torch's storage needs no second copy.
    """
    types={'F64':torch.float64,'F32':torch.float32,'F16':torch.float16,'BF16':torch.bfloat16,
           'I64':torch.int64,'I32':torch.int32,'I16':torch.int16,'I8':torch.int8,'U8':torch.uint8,'BOOL':torch.bool,
           'F8_E4M3':torch.float8_e4m3fn,'F8_E5M2':torch.float8_e5m2,'C64':torch.complex64,
           'U64':torch.uint64,'U32':torch.uint32,'U16':torch.uint16}
    device=torch.device(device or 'cpu');path=Path(path);size=path.stat().st_size
    with path.open('rb',buffering=0) as stream:
        prefix=stream.read(8)
        if len(prefix)!=8:raise ValueError('Incomplete safetensors header')
        length=struct.unpack('<Q',prefix)[0]
        if not 2<=length<=min(100_000_000,size-8):raise ValueError('Invalid safetensors header length')
        header=json.loads(stream.read(length));start=8+length
        if not isinstance(header,dict):raise ValueError('Invalid safetensors header')
        metadata=header.pop('__metadata__',None)
        if metadata is not None and (not isinstance(metadata,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in metadata.items())):
            raise ValueError('Invalid safetensors metadata')
        tensors=[]
        for name,info in header.items():
            if not isinstance(info,dict) or info.get('dtype') not in types:raise ValueError('Unsupported safetensors dtype')
            shape=info.get('shape');offsets=info.get('data_offsets');dtype=types[info['dtype']]
            if not isinstance(shape,list) or len(shape)>32 or any(type(n) is not int or n<0 for n in shape):raise ValueError('Invalid safetensors shape')
            if not isinstance(offsets,list) or len(offsets)!=2 or any(type(n) is not int for n in offsets):raise ValueError('Invalid safetensors offsets')
            lo,hi=offsets;item_size=torch.empty((),dtype=dtype).element_size()
            if not 0<=lo<=hi<=size-start or hi-lo!=math.prod(shape)*item_size:raise ValueError('Invalid safetensors tensor range')
            tensors.append((lo,hi,name,shape,dtype))
        tensors.sort(key=lambda value:(value[0],value[1]));end=0;result={}
        for lo,hi,name,shape,dtype in tensors:
            if lo!=end:raise ValueError('Overlapping or non-contiguous safetensors data')
            end=hi;buffer=bytearray(hi-lo);view=memoryview(buffer);read=0
            while read<len(buffer):
                count=stream.readinto(view[read:read+64*1024*1024])
                if not count:raise ValueError('Truncated safetensors tensor')
                read+=count
            tensor=torch.frombuffer(buffer,dtype=dtype).reshape(shape) if buffer else torch.empty(shape,dtype=dtype)
            result[name]=tensor if device.type=='cpu' else tensor.to(device)
        if end!=size-start:raise ValueError('Unindexed safetensors data')
    return result,metadata


def install_owned_checkpoint_loader():
    try:
        import comfy.utils
        import comfy.storage
    except ImportError:return
    original=comfy.utils.load_torch_file
    def load(ckpt,safe_load=False,device=None,return_metadata=False):
        if comfy.utils.DISABLE_MMAP and str(ckpt).lower().endswith(('.safetensors','.sft')):
            values,metadata=load_owned_safetensors(ckpt,device)
            comfy.storage.annotate_state_dict(values,ckpt)
            return (values,metadata) if return_metadata else values
        return original(ckpt,safe_load=safe_load,device=device,return_metadata=return_metadata)
    comfy.utils.load_torch_file=load


def conditioning_file():
    root=Path(os.environ['OPENCORE_IMAGE_PHASE_DIR']).resolve(strict=True)
    return root/'prompt-conditioning.pt'


def cpu_tensors(value):
    if isinstance(value,torch.Tensor):return value.detach().to(device='cpu',copy=True)
    if isinstance(value,dict):return {key:cpu_tensors(item) for key,item in value.items()}
    if isinstance(value,(list,tuple)):return type(value)(cpu_tensors(item) for item in value)
    if value is None or isinstance(value,(str,int,float,bool)):return value
    raise ValueError('Unsupported prompt conditioning value: '+type(value).__name__)


class SaveConditioning:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'positive':('CONDITIONING',),'negative':('CONDITIONING',),'latent':('LATENT',)}}
    RETURN_TYPES=()
    FUNCTION='save'
    OUTPUT_NODE=True
    CATEGORY='OpenCore/internal'

    def save(self,positive,negative,latent):
        path=conditioning_file();temporary=path.with_suffix('.tmp')
        torch.save(cpu_tensors({'positive':positive,'negative':negative,'latent':latent}),temporary)
        if temporary.stat().st_size>256_000_000:
            temporary.unlink();raise ValueError('Prompt conditioning exceeds the job limit')
        temporary.replace(path)
        return {'ui':{'conditioning':['ready']},'result':()}


class LoadConditioning:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'width':('INT',{'default':1024,'min':128,'max':2048,'step':16}),
                            'height':('INT',{'default':1024,'min':128,'max':2048,'step':16}),
                            'reference':('BOOLEAN',{'default':False})}}
    RETURN_TYPES=('CONDITIONING','CONDITIONING','LATENT')
    FUNCTION='load'
    CATEGORY='OpenCore/internal'

    def load(self,width,height,reference):
        path=conditioning_file()
        if path.stat().st_size>256_000_000:raise ValueError('Prompt conditioning exceeds the job limit')
        value=torch.load(path,map_location='cpu',weights_only=True)
        if not isinstance(value,dict) or set(value)!={'positive','negative','latent'}:raise ValueError('Invalid prompt conditioning')
        latent=value['latent'] if reference else {'samples':torch.zeros([1,64,height//16,width//16])}
        return value['positive'],value['negative'],latent


NODE_CLASS_MAPPINGS={'OpenCoreSaveConditioning':SaveConditioning,'OpenCoreLoadConditioning':LoadConditioning}
install_owned_checkpoint_loader()
