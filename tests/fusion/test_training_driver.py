"""Actual CPU training control flow with a tiny decoder boundary, never GPU proof."""
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sys

import pytest

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP / 'src-tauri/resources'))


@pytest.fixture
def driver_fixture(tmp_path, monkeypatch):
    from fusion import q6_pair, q6_preflight
    from fusion.q6_identity import file_digest
    from fusion.qualification import execution_configuration
    from test_training import bridge, binding_for, corpus_file, TinyFrozenNative
    configuration = execution_configuration(context=1024, rank=4, seed=7, recompute=False)
    binding = binding_for(bridge())
    proof = {'schema': 2, 'status': 'full_q6_resource_probe_passed',
        'gpu_qualified': True, 'models_loaded': True, 'models_released': True,
        'configuration': configuration, 'binding': binding, 'gpu_before': {'uuid': 'GPU-fixture'},
        'probe': {'finite_bridge_gradients': True, 'complete_target': True, 'tokens': 2},
        'placement': [{'head_on_gpu': 1, 'physical_matrix_layers': 1, 'gpu_matrix_layers': 1}] * 2}
    qualification = tmp_path / 'qualification.json'
    qualification.write_text(json.dumps(proof), encoding='utf-8')
    corpus = corpus_file(tmp_path)
    monkeypatch.setattr(q6_preflight, 'preflight', lambda *_: {'gpu': {'uuid': 'GPU-fixture'}})
    pairs = []

    class Pair:
        def __init__(self):
            self.native, self.bridge = TinyFrozenNative(), bridge()
            self.binding, self.configuration = binding, configuration
            self.resource_plan = {'gpu': {'uuid': 'GPU-fixture'}}
            self.released = False

        def __enter__(self): return self
        def __exit__(self, *_): self.released = True

    def open_pair(*_, **__):
        pair = Pair()
        pairs.append(pair)
        return pair

    monkeypatch.setattr(q6_pair, 'open_pair', open_pair)
    spec = spec_from_file_location('training_driver', APP / 'scripts/fusion/train_native.py')
    driver = module_from_spec(spec)
    spec.loader.exec_module(driver)

    def run(name, resume=None):
        output = tmp_path / name
        arguments = ['train_native.py', '--nanbeige', 'n.gguf', '--k2', 'k.gguf',
            '--corpus', str(corpus), '--corpus-sha256', file_digest(corpus),
            '--qualification', str(qualification), '--output', str(output),
            '--rank', '4', '--max-target-tokens', '8', '--checkpoint-every', '1']
        if resume is not None: arguments += ['--resume', str(resume)]
        monkeypatch.setattr(sys, 'argv', arguments)
        driver.main()
        return output

    return run, tmp_path, pairs


def test_driver_saves_resume_only_state_then_completed_adapter(driver_fixture):
    from fusion.checkpoints import resolve_resume
    run, tmp_path, pairs = driver_fixture
    output = run('final')
    checkpoint = resolve_resume(tmp_path / 'final.checkpoints')
    saved = json.loads((checkpoint / 'receipt.json').read_text(encoding='utf-8'))
    complete = json.loads((output / 'receipt.json').read_text(encoding='utf-8'))
    assert saved['checkpoint'] is True and complete['checkpoint'] is False
    assert saved['training']['schedule']['next_index'] == 1
    assert complete['training']['steps'] == 1
    assert saved['tensor_fingerprint'] == complete['tensor_fingerprint']
    assert all(pair.released for pair in pairs)


def test_interrupted_final_validation_resumes_without_repeating_completed_examples(driver_fixture, monkeypatch):
    from fusion import training
    from fusion.checkpoints import resolve_resume
    run, tmp_path, pairs = driver_fixture
    evaluate, calls = training.evaluate, []
    def interrupted(*args, **kwargs):
        calls.append(1)
        if len(calls) == 3:
            raise RuntimeError('Simulated final validation interruption')
        return evaluate(*args, **kwargs)
    monkeypatch.setattr(training, 'evaluate', interrupted)
    with pytest.raises(RuntimeError, match='validation interruption'):
        run('interrupted')
    assert not (tmp_path / 'interrupted').exists()
    resume = resolve_resume(tmp_path / 'interrupted.checkpoints')
    saved = json.loads((resume / 'receipt.json').read_text(encoding='utf-8'))
    monkeypatch.setattr(training, 'evaluate', evaluate)
    output = run('resumed', resume=resume)
    complete = json.loads((output / 'receipt.json').read_text(encoding='utf-8'))
    assert complete['training']['steps'] == saved['training']['steps'] == 1
    assert complete['tensor_fingerprint'] == saved['tensor_fingerprint']
    assert complete['checkpoint'] is False
    assert all(pair.released for pair in pairs)
