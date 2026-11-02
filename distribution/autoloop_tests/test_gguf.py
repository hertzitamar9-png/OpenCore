import hashlib
from pathlib import Path
import pytest
from autoloop_vendor.gguf import GGUFModel,GGUFError,_parse_value,variants
from conftest import make_gguf


def hash_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def get_array(m,key):
    entry=next(x for x in m.metadata if x.key==key)
    with m.path.open('rb') as f:
        f.seek(entry.offset)
        return _parse_value(f,entry.type_id,True)


def tensor_bytes(m,name):
    t=next(x for x in m.tensors if x.name==name)
    with m.path.open('rb') as f:
        f.seek(m.data_start+t.offset)
        return f.read(t.size)


def test_surgery_round_trip(tiny,tmp_path):
    source=GGUFModel(tiny)
    source.validate_for_surgery()
    original_hash=hash_file(tiny)
    new_path=source.build(tmp_path/'new.gguf',[0,1,1,2,3])
    converted=GGUFModel(new_path)
    converted.validate_for_surgery()
    assert converted.block_count==5
    assert converted.architecture=='llama'
    assert len(converted.tensors)==len(source.tensors)+4
    assert get_array(converted,'llama.attention.head_count_kv')==(4,[1,2,2,3,4])
    assert get_array(converted,'tokenizer.ggml.tokens')==(8,['a','b','c','d'])
    assert tensor_bytes(converted,'blk.2.attn_q.weight')==tensor_bytes(source,'blk.1.attn_q.weight')
    assert tensor_bytes(converted,'blk.3.attn_q.weight')==tensor_bytes(source,'blk.2.attn_q.weight')
    assert tensor_bytes(converted,'token_embd.weight')==tensor_bytes(source,'token_embd.weight')
    assert hash_file(tiny)==original_hash


def test_prevents_source_overwrite(tiny):
    m=GGUFModel(tiny)
    with pytest.raises(GGUFError,match='overwrite'):
        m.build(tiny,[0,1,1,2,3])


def test_existing_output_refused(tiny,tmp_path):
    m=GGUFModel(tiny)
    output=tmp_path/'new.gguf'
    output.write_bytes(b'existing')
    with pytest.raises(GGUFError,match='already exists'):
        m.build(output,[0,1,1,2,3])
    assert output.read_bytes()==b'existing'


def test_rejects_heterogeneous(tmp_path):
    path=make_gguf(tmp_path/'uneven.gguf',heterogeneous=True)
    with pytest.raises(GGUFError,match='Heterogeneous'):
        GGUFModel(path).validate_for_surgery()


def test_rejects_unsupported_architecture(tmp_path):
    path=make_gguf(tmp_path/'unknown.gguf',architecture='mamba')
    with pytest.raises(GGUFError,match='allowlist'):
        GGUFModel(path).validate_for_surgery()


def test_variants_mapping():
    for name,mapping,damped in variants(8,8):
        assert len(mapping)>8
        assert [a for i,a in enumerate(mapping) if i==0 or a!=mapping[i-1]]==list(range(8))


def test_damped_residual_projection(tiny,tmp_path):
    import math,struct
    source=GGUFModel(tiny)
    output=source.build(tmp_path/'damped.gguf',[0,1,1,2,3],damped=True)
    changed=GGUFModel(output)
    # First and repeated copies of both terminal projections scale by 1/2.
    for suffix in ('attn_output.weight','ffn_down.weight'):
        a=tensor_bytes(source,f'blk.1.{suffix}')
        for index in (1,2):
            b=tensor_bytes(changed,f'blk.{index}.{suffix}')
            orig=struct.unpack('<8f',a)
            new=struct.unpack('<8f',b)
            for x,y in zip(orig,new):
                if math.isfinite(x):
                    assert math.isclose(y,x/2,rel_tol=1e-5,abs_tol=1e-25)
    assert tensor_bytes(changed,'blk.3.ffn_down.weight')==tensor_bytes(source,'blk.2.ffn_down.weight')
