"""Synthetic regression fixtures for architecture-specific, training-free motif repetition.

These files verify metadata and tensor directories, not real model quality.
"""
import hashlib
import struct
from pathlib import Path

import pytest
from autoloop_vendor.gguf import GGUFModel, GGUFError, _write_string, _write_value, _align


def make_structured(path: Path, architecture: str, *, mtp: bool = False):
    if architecture == 'qwen35':
        n = 13 if mtp else 12
    elif architecture == 'dory':
        n = 24
    else:
        raise ValueError(architecture)
    meta = [
        ('general.architecture', 8, architecture),
        (architecture + '.block_count', 4, n),
        (architecture + '.context_length', 4, 32768),
        ('general.alignment', 4, 32),
    ]
    if architecture == 'qwen35':
        meta.extend([
            ('qwen35.attention.recurrent_layers', 9, (7, [i % 4 < 3 if i < 12 else False for i in range(n)])),
            ('qwen35.nextn_predict_layers', 4, 1 if mtp else 0),
        ])
    else:
        meta.extend([
            ('dory.input_layer_count', 4, 8),
            ('dory.recurrent_layer_count', 4, 8),
            ('dory.output_layer_count', 4, 8),
            ('dory.layer_pattern', 8, 'S-S-S-*-S-S-S-*-S-S-S-*-'),
            ('dory.recurrent_kv_cache_mode', 8, 'per_loop'),
        ])
    names = ['token_embd.weight']
    for i in range(n):
        if architecture == 'qwen35':
            suffixes = (['ssm_a', 'ssm_conv1d.weight', 'ssm_out.weight', 'attn_qkv.weight', 'ffn_down.weight']
                        if i % 4 < 3 else
                        ['attn_q.weight', 'attn_k.weight', 'attn_v.weight', 'attn_output.weight', 'ffn_down.weight'])
            if mtp and i == n - 1:
                suffixes += ['nextn.eh_proj.weight']
        else:
            suffixes = ['attn_q.weight', 'attn_output.weight'] if i % 2 == 0 else ['ffn_down.weight', 'ffn_up.weight']
        names.extend(f'blk.{i}.{suffix}' for suffix in suffixes)
    names.append('output.weight')
    with path.open('wb') as file:
        file.write(b'GGUF' + struct.pack('<IQQ', 3, len(names), len(meta)))
        for key, type_id, value in meta:
            file.write(_write_string(key) + struct.pack('<I', type_id) + _write_value(type_id, value))
        for idx, name in enumerate(names):
            file.write(_write_string(name) + struct.pack('<IQIQ', 1, 8, 0, idx * 32))
        file.write(b'\0' * (_align(file.tell(), 32) - file.tell()))
        for idx in range(len(names)):
            file.write(struct.pack('<8f', *([float(idx + 1)] * 8)))
    return path


@pytest.mark.parametrize('architecture,mtp', [('qwen35', False), ('qwen35', True), ('dory', False)])
def test_complete_pattern_repetition_roundtrip(tmp_path, architecture, mtp):
    source = make_structured(tmp_path / 'source.gguf', architecture, mtp=mtp)
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    model = GGUFModel(source)
    assert model.validate_for_surgery()
    label, mapping, damping = model.propose_variants(3)[0]
    assert 'whole-pattern' in label and not damping
    result = model.build(tmp_path / 'candidate.gguf', mapping, damped=damping)
    changed = GGUFModel(result)
    assert changed.validate_for_surgery()
    assert changed.block_count == len(mapping)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
    if architecture == 'dory':
        assert changed.metadata_value('dory.recurrent_layer_count') == 16
        assert len(changed.metadata_value('dory.layer_pattern').strip('-').split('-')) == 16
    else:
        assert changed.metadata_value('qwen35.nextn_predict_layers') == int(mtp)
        flags = changed.metadata_value('qwen35.attention.recurrent_layers')[1]
        assert flags == [i % 4 < 3 if i < len(mapping) - int(mtp) else False for i in range(len(mapping))]
        if mtp:
            assert any(t.name.startswith(f'blk.{len(mapping)-1}.nextn.') for t in changed.tensors)


@pytest.mark.parametrize('architecture', ['qwen35', 'dory'])
def test_partial_motif_and_damping_are_not_allowed(tmp_path, architecture):
    model = GGUFModel(make_structured(tmp_path / 'source.gguf', architecture))
    valid = model.propose_variants(1)[0][1]
    with pytest.raises(GGUFError, match='complete|pattern'):
        model.build(tmp_path / 'bad.gguf', valid[:-1])
    with pytest.raises(GGUFError, match='Damping'):
        model.build(tmp_path / 'damped.gguf', valid, damped=True)
    assert not (tmp_path / 'bad.gguf').exists()
    assert not (tmp_path / 'damped.gguf').exists()


@pytest.mark.parametrize('architecture', ['qwen35', 'dory'])
def test_invalid_recurrent_layout_not_promoted(tmp_path, architecture):
    source = make_structured(tmp_path / 'source.gguf', architecture)
    model = GGUFModel(source)
    # Shift a single block rather than a complete four/eight-block motif.
    invalid = [item for index in range(model.block_count) for item in ([index, index] if index == model.block_count // 2 else [index])]
    with pytest.raises(GGUFError, match='complete|pattern'):
        model.build(tmp_path / 'invalid.gguf', invalid)
