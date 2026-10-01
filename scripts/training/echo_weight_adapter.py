"""Trainable view of the SHIPPED ECHO GGUF, not a substitute dense model.

Frozen Q6 backbone values are rematerialized in RAM only for autograd. Six small
expert composition arrays have FP32 optimizer masters; exported values remain
BF16. Private/shared expert factors, architecture, MTP and backbone stay intact.
The resident five-lane FFN mirrors the pinned native qwen35.cpp implementation.
Forward parity against that native backend is a mandatory training gate.
"""
from pathlib import Path
import json
import sys
import numpy as np
import torch
from torch import nn
import torch.nn.functional as F

MODEL_SHA='261ef6c572bf9916f9ea5097bc156da0ee0ef6d631d52cf59dbcf293f416b7ae'
COUNTS=(250,250,500,500,416,417,666,667,583,584,1083,1084,916,917,333,334,250,250)

def stage_block(position, layers=32, active=5, window=2048):
    offset=first=0
    for i,count in enumerate(COUNTS):
        length=max((count+layers*active-1)//(layers*active),window*count//10000)
        if i==len(COUNTS)-1 or position<first+length: return offset,count
        first+=length;offset+=count
    raise AssertionError('Unreachable stage')

def stage_segments(position_start, length, decode_start=None):
    """Mirror native prompt batching, followed by tokenwise workflow routing.

    Legacy supervised prefill keeps one stage for its entire batch. RL scoring
    sets decode_start to the original prompt length so teacher forcing uses the
    same routes as cached autoregressive generation, including stage changes.
    """
    segments=[]
    for local in range(length):
        position=position_start+local
        stage=stage_block(position_start if decode_start is None or position<decode_start else position)
        if segments and segments[-1][2:]==stage:
            start,_,offset,count=segments[-1];segments[-1]=(start,local+1,offset,count)
        else: segments.append((local,local+1,*stage))
    return segments

def route_ids(positions, layer, offset, count, active=5, layers=32):
    steps=positions*layers+layer
    return torch.stack([offset+slot+active*(steps% (1+(count-1-slot)//active)) for slot in range(active)],dim=-1)

class Projection(nn.Module):
    def __init__(self, values):
        super().__init__()
        for key,value in values.items():
            if key in ('seed_scale','shared_coeff'): self.register_parameter(key,nn.Parameter(value.float()))
            else: self.register_buffer(key,value)

    def prepare(self,offset,count,device):
        signature=(offset,count,str(device))
        if getattr(self,'_signature',None)==signature: return
        # Frozen factors remain in RAM; only the stage needed by this batch
        # goes to the GPU. Do not allocate a second complete expert pool there.
        self._factors={key:getattr(self,key)[offset:offset+count].to(device) for key in ('expert_a','expert_b')}
        self._factors.update({key:getattr(self,key).to(device) for key in ('shared_a','shared_b')})
        self._offset=offset;self._signature=signature

    def forward(self,x,seed,ids):
        # x:[tokens,input], ids:[tokens]. No dense expert substitute.
        local=ids-self._offset
        private=torch.einsum('toi,ti->to',self._factors['expert_a'][local],x)
        private=torch.einsum('toi,ti->to',self._factors['expert_b'][local],private)
        shared=torch.zeros_like(seed)
        for basis in range(self.shared_a.shape[0]):
            value=F.linear(F.linear(x,self._factors['shared_a'][basis]),self._factors['shared_b'][basis])
            shared=shared+value*self.shared_coeff[ids,basis,None].to(x.dtype)
        return seed*self.seed_scale[ids,None].to(x.dtype)+(private+shared)*.001

class ResidentPool(nn.Module):
    def __init__(self, values):
        super().__init__()
        for prefix in ('gate','up','down'):
            setattr(self,prefix,Projection({key.split('.',1)[1]:value for key,value in values.items() if key.startswith(prefix+'.')}))
        self.position_start=0
        self.decode_start=None

class ResidentMLP(nn.Module):
    def __init__(self,seed,pool,layer):
        super().__init__();self.seed=seed
        # Pool is registered once on the top-level model. Every layer uses it.
        object.__setattr__(self,'pool',pool);self.layer=layer

    def forward(self,hidden):
        if hidden.shape[0]!=1: raise ValueError('Pilot uses independent batch-size-one examples')
        x=hidden[0];pool=self.pool
        return torch.cat([self.segment(x[start:end],pool.position_start+start,offset,count)
            for start,end,offset,count in stage_segments(pool.position_start,x.shape[0],pool.decode_start)],dim=0).unsqueeze(0)

    def segment(self,x,position_start,offset,count):
        pool=self.pool
        positions=torch.arange(x.shape[0],device=x.device)+position_start
        for prefix in ('gate','up','down'): getattr(pool,prefix).prepare(offset,count,x.device)
        ids=route_ids(positions,self.layer,offset,count)
        seed_up=self.seed.up_proj(x);seed_gate=self.seed.gate_proj(x)
        result=torch.zeros(x.shape[0],x.shape[1],device=x.device,dtype=x.dtype)
        rows=torch.arange(seed_up.shape[-1],device=x.device)
        for lane in range(5):
            expert=ids[:,lane]
            mask=(rows%5==(offset+lane)%5).to(x.dtype)
            up=pool.up(x,seed_up,expert)*mask
            gate=pool.gate(x,seed_gate,expert)*mask
            activated=up*F.silu(gate)
            result=result+pool.down(activated,self.seed.down_proj(activated),expert)
        return result

def gguf_reader(path,gguf_python):
    sys.path.insert(0,str(gguf_python))
    from gguf import GGUFReader
    class Reader(GGUFReader):
        def _push_field(self,field,skip_sum=False):
            # The shipped single-file artifact has duplicate GGUF structural
            # fields from its two constituent streams. Other duplicates fail.
            if field.name in self.fields and field.name.startswith('GGUF.'):
                return 0 if skip_sum else sum(int(p.nbytes) for p in field.parts)
            return super()._push_field(field,skip_sum)
    return Reader(str(path))

BLOCK_MAP={'attn_norm.weight':'input_layernorm.weight','post_attention_norm.weight':'post_attention_layernorm.weight',
 'ffn_gate.weight':'mlp.gate_proj.weight','ffn_up.weight':'mlp.up_proj.weight','ffn_down.weight':'mlp.down_proj.weight',
 'attn_qkv.weight':'linear_attn.in_proj_qkv.weight','attn_gate.weight':'linear_attn.in_proj_z.weight',
 'attn_q.weight':'self_attn.q_proj.weight','attn_k.weight':'self_attn.k_proj.weight','attn_v.weight':'self_attn.v_proj.weight',
 'attn_output.weight':'self_attn.o_proj.weight','attn_q_norm.weight':'self_attn.q_norm.weight','attn_k_norm.weight':'self_attn.k_norm.weight',
 'ssm_a':'linear_attn.A_log','ssm_conv1d.weight':'linear_attn.conv1d.weight','ssm_dt.bias':'linear_attn.dt_bias',
 'ssm_norm.weight':'linear_attn.norm.weight','ssm_out.weight':'linear_attn.out_proj.weight',
 'ssm_alpha.weight':'linear_attn.in_proj_a.weight','ssm_beta.weight':'linear_attn.in_proj_b.weight'}

def untile(value,dim,k,r,h):
    shape=list(value.shape)
    return value.reshape(shape[:dim]+[r,k,h]+shape[dim+1:]).transpose(dim,dim+1).contiguous().reshape(shape)

def inverse(name,value,cfg):
    if name.endswith('.A_log'): value=torch.log(-value)
    elif name.endswith('norm.weight') and not name.endswith('linear_attn.norm.weight'): value=value-1
    if '.linear_attn.' in name:
        k=cfg['linear_num_key_heads'];r=cfg['linear_num_value_heads']//k;h=cfg['linear_value_head_dim']
        qk=2*k*cfg['linear_key_head_dim']
        if name.endswith(('in_proj_qkv.weight','conv1d.weight')):
            value=torch.cat([value[:qk],untile(value[qk:],0,k,r,h)])
        elif name.endswith('in_proj_z.weight'): value=untile(value,0,k,r,h)
        elif name.endswith(('in_proj_a.weight','in_proj_b.weight','A_log','dt_bias')): value=untile(value,0,k,r,1)
        elif name.endswith('out_proj.weight'): value=untile(value,1,k,r,h)
    return value

def load_training_view(path,seed_dir,gguf_python,device='cpu'):
    from transformers import Qwen3_5TextConfig,Qwen3_5ForCausalLM
    reader=gguf_reader(path,gguf_python)
    from gguf.quants import dequantize
    cfg=json.loads((seed_dir/'config.json').read_text())
    config=Qwen3_5TextConfig(**{key:value for key,value in cfg.items() if key!='architectures'})
    with torch.device('meta'): model=Qwen3_5ForCausalLM(config)
    model.config.use_cache=False
    expected={name:tuple(value.shape) for name,value in model.state_dict().items()}
    # Nonpersistent positional buffers must be built on CPU, not left meta.
    from transformers.models.qwen3_5.modeling_qwen3_5 import Qwen3_5TextRotaryEmbedding
    model.model.rotary_emb=Qwen3_5TextRotaryEmbedding(config)
    loaded=set();pool_values={}
    for tensor in reader.tensors:
        if tensor.name.startswith('opencore.'):
            key=tensor.name.removeprefix('opencore.')
            if key.split('.')[0] in ('gate','up','down'):
                if tensor.tensor_type.name!='BF16': raise ValueError('Expert precision changed')
                pool_values[key]=torch.from_numpy(np.array(tensor.data,copy=True)).view(torch.bfloat16).reshape(tuple(reversed(tensor.shape.tolist())))
            continue
        if tensor.name.startswith('blk.'):
            _,layer,part=tensor.name.split('.',2);name=f'model.layers.{layer}.{BLOCK_MAP[part]}'
        else: name={'token_embd.weight':'model.embed_tokens.weight','output_norm.weight':'model.norm.weight','output.weight':'lm_head.weight'}[tensor.name]
        value=torch.from_numpy(np.array(dequantize(tensor.data,tensor.tensor_type),copy=True)).float().reshape(expected[name])
        value=inverse(name,value,cfg)
        if not torch.isfinite(value).all(): raise ValueError('Nonfinite source tensor')
        module_path,parameter=name.rsplit('.',1)
        module=model.get_submodule(module_path)
        setattr(module,parameter,nn.Parameter(value.to(torch.bfloat16 if value.ndim>1 else torch.float32),requires_grad=False))
        loaded.add(name)
    if cfg['tie_word_embeddings']:
        model.lm_head.weight=model.model.embed_tokens.weight;loaded.add('lm_head.weight')
    if set(expected)!=loaded: raise ValueError(f'Backbone coverage mismatch: {set(expected)-loaded}')
    model.to(device)
    pool=ResidentPool(pool_values)
    for prefix in ('gate','up','down'):
        projection=getattr(pool,prefix)
        for key in ('seed_scale','shared_coeff'):
            setattr(projection,key,nn.Parameter(getattr(projection,key).to(device)))
    model.add_module('opencore_expert_pool',pool)
    for layer,block in enumerate(model.model.layers): block.mlp=ResidentMLP(block.mlp,pool,layer)
    return model,reader
