"""Artifact startup gates; tiny CPU fixtures, never model/GPU qualification."""
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src-tauri/resources'))


def trained_fixture(tmp_path):
    from test_training import bridge, binding_for, TinyFrozenNative, rows
    from fusion.adapter import save_adapter, tensor_fingerprint
    from fusion.training import teacher_forced, GRADIENT_MODE
    from fusion.qualification import execution_configuration
    import torch
    model = bridge()
    initial = tensor_fingerprint(model)
    loss = teacher_forced(TinyFrozenNative(), model, rows()[0], max_tokens=8).loss
    loss.backward()
    torch.optim.AdamW(model.bridge_parameters(), lr=0.01).step()
    binding = binding_for(model)
    artifact = tmp_path / 'adapter'
    receipt = save_adapter(artifact, model, binding, {'steps': 1, 'tokens': 2,
        'initial_bridge_sha256': initial, 'corpus_sha256': 'a' * 64,
        'gradient_mode': GRADIENT_MODE,
        'validation': {'tokens': 2, 'loss': 1.0, 'baseline_loss': 2.0}})
    configuration = execution_configuration(context=1024, rank=4, seed=7, recompute=False)
    report = {'schema': 2, 'status': 'full_q6_resource_probe_passed',
        'gpu_qualified': True, 'models_loaded': True, 'models_released': True,
        'configuration': configuration, 'binding': binding, 'gpu_before': {'uuid': 'GPU-fixture'},
        'probe': {'finite_bridge_gradients': True, 'complete_target': True, 'tokens': 2},
        'placement': [{'physical_matrix_layers': 1, 'gpu_matrix_layers': 1, 'head_on_gpu': 1}] * 2}
    qualification = tmp_path / 'qualification.json'
    qualification.write_text(json.dumps(report), encoding='utf-8')
    return artifact, receipt, qualification, report


def rewrite_receipt(path, receipt):
    from fusion.q6_identity import canonical
    receipt.pop('receipt_sha256', None)
    receipt['receipt_sha256'] = hashlib.sha256(canonical(receipt)).hexdigest()
    path.write_bytes(canonical(receipt) + b'\n')


@pytest.mark.parametrize('failure', ['corrupt-receipt', 'corrupt-tensor', 'initialized', 'stale-source', 'wrong-checkpoint', 'wrong-native'])
def test_bad_adapter_is_rejected_before_either_full_model_is_allocated(tmp_path, monkeypatch, failure):
    from fusion import native, native_engine, q6_pair
    artifact, receipt, qualification, report = trained_fixture(tmp_path)
    current_native = json.loads(json.dumps(report['binding']['native']))
    if failure == 'corrupt-receipt':
        (artifact / 'receipt.json').write_text('{}', encoding='utf-8')
    elif failure == 'corrupt-tensor':
        (artifact / 'bridge.safetensors').write_bytes(b'corrupt')
    else:
        if failure == 'initialized':
            receipt['training']['initial_bridge_sha256'] = receipt['tensor_fingerprint']
        elif failure == 'stale-source':
            receipt['binding']['coupling_sources']['bridge.py'] = '0' * 64
        elif failure == 'wrong-checkpoint':
            receipt['binding']['checkpoints']['nanbeige']['sha256'] = '0' * 64
        else:
            receipt['binding']['native']['libraries'][0]['sha256'] = '0' * 64
        report['binding'] = receipt['binding']
        rewrite_receipt(artifact / 'receipt.json', receipt)
        qualification.write_text(json.dumps(report), encoding='utf-8')
    monkeypatch.setattr(native_engine, 'preflight', lambda *_: {'gpu': {'uuid': 'GPU-fixture'}})
    monkeypatch.setattr(native, 'verify_build', lambda *_: current_native)

    def forbidden_load(*_, **__):
        pytest.fail('GPU model allocation was reached before invalid adapter rejection')

    monkeypatch.setattr(q6_pair, 'open_pair', forbidden_load)
    with pytest.raises(ValueError, match='integrity|untrained|identity|checkpoint'):
        native_engine.TwinCoreEngine('n.gguf', 'k.gguf', artifact, qualification,
                                    'native.dll', 'runtime', rank=4)


def test_matching_trained_adapter_is_loaded_on_cpu_before_model_allocation(tmp_path, monkeypatch):
    from fusion import adapter, native, native_engine, q6_pair
    from test_training import bridge
    artifact, receipt, qualification, report = trained_fixture(tmp_path)
    monkeypatch.setattr(native_engine, 'preflight', lambda *_: {'gpu': {'uuid': 'GPU-fixture'}})
    monkeypatch.setattr(native, 'verify_build', lambda *_: report['binding']['native'])
    inspected = []
    inspect_adapter = getattr(adapter, 'inspect_adapter', None)
    assert inspect_adapter is not None, 'Small trained tensors must be inspected before GPU allocation'

    def inspect(*args, **kwargs):
        result = inspect_adapter(*args, **kwargs)
        inspected.append(result)
        return result

    monkeypatch.setattr(adapter, 'inspect_adapter', inspect)
    pair = SimpleNamespace(native=SimpleNamespace(closed=False), bridge=bridge(),
                           binding=report['binding'], configuration=report['configuration'],
                           resource_plan={'gpu': {'uuid': 'GPU-fixture'}})
    def open_pair(*_, **__):
        assert len(inspected) == 1
        assert adapter.tensor_fingerprint(inspected[0][0]) == receipt['tensor_fingerprint']
        return pair

    monkeypatch.setattr(q6_pair, 'open_pair', open_pair)
    engine = native_engine.TwinCoreEngine('n.gguf', 'k.gguf', artifact, qualification,
                                         'native.dll', 'runtime', rank=4)
    assert engine.pair.bridge is inspected[0][0]
    assert adapter.tensor_fingerprint(engine.pair.bridge) == receipt['tensor_fingerprint']
