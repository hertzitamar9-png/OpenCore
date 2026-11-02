import sys
sys.path.insert(0,str(__import__("pathlib").Path(__file__).resolve().parents[2]/"src-tauri/resources/runtime-setup/hf-extension"))
from pathlib import Path
import struct
import pytest
from autoloop_vendor.gguf import _write_string,_write_value,_align


def make_gguf(path:Path,block_count=4,architecture='llama',heterogeneous=False,context=None,extra_metadata=()):
    metadata=[
      ('general.architecture',8,architecture),
      (f'{architecture}.block_count',4,block_count),
      ('general.alignment',4,32),
      (f'{architecture}.attention.head_count_kv',9,(4,list(range(1,block_count+1)))),
      ('tokenizer.ggml.tokens',9,(8,['a','b','c','d'])),
      ('general.name',8,'Synthetic Tiny'),
    ]
    if context is not None:metadata.append((f'{architecture}.context_length',4,context))
    metadata.extend(extra_metadata)
    tensors=[]
    names=['token_embd.weight']
    for i in range(block_count):
        for suffix in ['attn_q.weight','attn_output.weight','attn_norm.weight','ffn_down.weight']:
            if heterogeneous and i==1 and suffix=='attn_q.weight':
                continue
            names.append(f'blk.{i}.{suffix}')
    names.append('output.weight')
    for idx,name in enumerate(names):
        tensor_data=bytes([(idx * 7 + j)%256 for j in range(32)])
        tensors.append((name,(8,),0,tensor_data))
    with path.open('wb') as f:
        f.write(b'GGUF'+struct.pack('<IQQ',3,len(tensors),len(metadata)))
        for key,t,value in metadata:
            f.write(_write_string(key))
            f.write(struct.pack('<I',t))
            f.write(_write_value(t,value))
        for i,(name,shape,typ,data) in enumerate(tensors):
            f.write(_write_string(name))
            f.write(struct.pack('<I',len(shape)))
            for d in shape:f.write(struct.pack('<Q',d))
            f.write(struct.pack('<IQ',typ,i*32))
        f.write(b'\0'*(_align(f.tell(),32)-f.tell()))
        for name,shape,typ,data in tensors:
            f.write(data)
    return path

@pytest.fixture
def tiny(tmp_path):
    return make_gguf(tmp_path/'source.gguf')
