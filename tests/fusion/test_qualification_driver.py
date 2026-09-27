"""Durable qualification evidence survives an uncatchable native failure."""
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
import torch

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP / 'src-tauri/resources'))


class NativeAbort(BaseException):
    """Simulate a native exit that Python's Exception handler cannot catch."""


@pytest.mark.parametrize('phase', ['loading_full_q6', 'forward_backward', 'releasing_models'])
def test_native_abort_leaves_last_phase_without_a_success_receipt(tmp_path, monkeypatch, phase):
    from fusion import q6_pair, q6_preflight, training
    spec = spec_from_file_location('qualification_driver', APP / 'scripts/fusion/qualify_native.py')
    driver = module_from_spec(spec)
    spec.loader.exec_module(driver)
    output = tmp_path / 'qualification.json'
    monkeypatch.setattr(sys, 'argv', ['qualify_native.py', '--nanbeige', 'n.gguf',
                                     '--k2', 'k.gguf', '--output', str(output)])
    monkeypatch.setattr(q6_preflight, 'gpu_snapshot', lambda: {
        'uuid': 'GPU-fixture', 'free_bytes': 12_000_000_000, 'used_bytes': 0})
    monkeypatch.setattr(q6_preflight, 'require_q6_resources', lambda **_: {'required_gpu_bytes': 1})
    parameter = torch.nn.Parameter(torch.tensor([1.0]))
    native = SimpleNamespace(api=SimpleNamespace(loaded_libraries={}), closed=False)

    class Pair:
        binding = {'fixture': True}
        placement = [{'head_on_gpu': 1, 'physical_matrix_layers': 1, 'gpu_matrix_layers': 1}] * 2
        bridge = SimpleNamespace(bridge_parameters=lambda: [parameter])

        def __enter__(self):
            return self

        def __exit__(self, *_):
            if phase == 'releasing_models':
                raise NativeAbort('release failed')
            native.closed = True

    pair = Pair()
    pair.native = native

    def open_pair(*_, **__):
        if phase == 'loading_full_q6':
            raise NativeAbort('load failed')
        return pair

    def teacher_forced(*_, **__):
        if phase == 'forward_backward':
            raise NativeAbort('decode failed')
        return SimpleNamespace(loss=parameter.sum(), tokens=1, complete=True)

    monkeypatch.setattr(q6_pair, 'open_pair', open_pair)
    monkeypatch.setattr(training, 'teacher_forced', teacher_forced)
    with pytest.raises(NativeAbort):
        driver.main()
    report = json.loads(output.read_text(encoding='utf-8'))
    assert report['phase'] == phase
    assert report['status'] == 'probe_in_progress'
    assert report['gpu_qualified'] is False
    assert report['models_released'] is False
    assert report['models_loaded'] is (phase != 'loading_full_q6')


def test_qualification_rejects_missing_model_release_evidence():
    from fusion.qualification import execution_configuration, validate_qualification
    configuration = execution_configuration(context=1024, rank=256, seed=7, recompute=False)
    report = {'schema': 2, 'status': 'full_q6_resource_probe_passed',
              'gpu_qualified': True, 'models_loaded': True, 'configuration': configuration,
              'gpu_before': {'uuid': 'GPU-fixture'},
              'probe': {'finite_bridge_gradients': True, 'complete_target': True, 'tokens': 1},
              'placement': [{'head_on_gpu': 1, 'physical_matrix_layers': 1, 'gpu_matrix_layers': 1}] * 2}
    with pytest.raises(ValueError, match='release'):
        validate_qualification(report, configuration, 'GPU-fixture')
