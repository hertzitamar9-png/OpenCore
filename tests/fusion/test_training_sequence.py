"""Sequence gates use tiny receipts; these tests do not load or qualify a model."""
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from test_training_driver import driver_fixture


APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP / 'src-tauri/resources'))


@pytest.fixture
def sequence(tmp_path, monkeypatch):
    spec = spec_from_file_location('training_sequence', APP / 'scripts/fusion/run_training.py')
    driver = module_from_spec(spec)
    spec.loader.exec_module(driver)
    from fusion.q6_identity import file_digest
    from fusion.qualification import execution_configuration
    for name in ('n.gguf', 'k.gguf', 'twincore.dll', 'corpus.jsonl'):
        (tmp_path / name).write_bytes(b'tiny fixture, never full weights')
    args = SimpleNamespace(nanbeige=tmp_path / 'n.gguf', k2=tmp_path / 'k.gguf',
        dll=tmp_path / 'twincore.dll', runtime=tmp_path, corpus=tmp_path / 'corpus.jsonl',
        corpus_sha256=file_digest(tmp_path / 'corpus.jsonl'), output=tmp_path / 'run',
        context=1024, rank=4, seed=7, recompute=False, epochs=1, max_target_tokens=512,
        checkpoint_every=8, lr=0.0002, resume=None, prepare_only=False)
    configuration = execution_configuration(context=1024, rank=4, seed=7, recompute=False)
    proof = {'schema': 2, 'status': 'full_q6_resource_probe_passed',
        'gpu_qualified': True, 'models_loaded': True, 'models_released': True,
        'configuration': configuration,
        'binding': {'checkpoints': driver.CHECKPOINTS, 'fixture_only': True},
        'driver_sha256': file_digest(APP / 'scripts/fusion/qualify_native.py'),
        'gpu_before': {'uuid': 'GPU-fixture'},
        'probe': {'finite_bridge_gradients': True, 'complete_target': True, 'tokens': 2},
        'placement': [{'head_on_gpu': 1, 'physical_matrix_layers': 1, 'gpu_matrix_layers': 1}] * 2}
    calls = []
    monkeypatch.setattr(driver.shutil, 'disk_usage', lambda _: SimpleNamespace(free=250_000_000_000))

    def run(command, log):
        calls.append((command, log))
        log.write_text('fixture stage\n', encoding='utf-8')
        if Path(command[1]).name == 'qualify_native.py':
            output = Path(command[command.index('--output') + 1])
            output.write_text(json.dumps(proof), encoding='utf-8')
        return 0

    resources = lambda *_: {'gpu': {'uuid': 'GPU-fixture'}, 'budget': {'fixture_only': True}}
    return driver, args, proof, calls, run, resources


def test_resource_refusal_never_starts_a_child(sequence):
    driver, args, _, calls, run, _ = sequence
    def unavailable(*_):
        raise ValueError('Not enough free GPU memory')
    with pytest.raises(ValueError, match='free GPU'):
        driver.execute(args, run_stage=run, check_resources=unavailable)
    report = json.loads((args.output / 'sequence.json').read_text())
    assert calls == [] and report['status'] == 'failed'
    assert report['stage'] == 'qualification_resource_check'
    assert report['training_completed'] is False


def test_successful_exit_with_preflight_only_receipt_cannot_start_training(sequence):
    driver, args, proof, calls, run, resources = sequence
    proof.update(status='preflight_only', gpu_qualified=False, models_loaded=False)
    with pytest.raises(ValueError, match='actual complete GPU probe'):
        driver.execute(args, run_stage=run, check_resources=resources)
    assert len(calls) == 1
    report = json.loads((args.output / 'sequence.json').read_text())
    assert report['status'] == 'failed' and report['gpu_qualified'] is False


def test_gpu_identity_change_between_stages_refuses_training(sequence):
    driver, args, _, calls, run, _ = sequence
    snapshots = iter([{'gpu': {'uuid': 'GPU-fixture'}}, {'gpu': {'uuid': 'GPU-other'}}])
    with pytest.raises(ValueError, match='GPU identity changed'):
        driver.execute(args, run_stage=run, check_resources=lambda *_: next(snapshots))
    assert len(calls) == 1


def test_different_checkpoint_receipt_cannot_start_training(sequence):
    driver, args, proof, calls, run, resources = sequence
    proof['binding'] = {'checkpoints': {'different': True}}
    with pytest.raises(ValueError, match='checkpoint identities'):
        driver.execute(args, run_stage=run, check_resources=resources)
    assert len(calls) == 1


def test_failed_training_preserves_qualification_and_resume_location(sequence):
    driver, args, _, calls, run, resources = sequence
    def fail_training(command, log):
        if Path(command[1]).name == 'train_native.py':
            calls.append((command, log))
            store = args.output / 'adapter.checkpoints'
            store.mkdir()
            (store / 'latest.json').write_text('{"preserved":true}')
            log.write_text('training failure')
            return 9
        return run(command, log)
    with pytest.raises(RuntimeError, match='exit code 9'):
        driver.execute(args, run_stage=fail_training, check_resources=resources)
    report = json.loads((args.output / 'sequence.json').read_text())
    assert (args.output / 'qualification.json').is_file()
    assert (args.output / 'adapter.checkpoints/latest.json').is_file()
    assert report['gpu_qualified'] is True and report['training_completed'] is False
    assert report['resume_store'] == str(args.output / 'adapter.checkpoints')


def test_prepare_only_never_checks_gpu_or_launches_a_stage(sequence):
    driver, args, _, calls, _, _ = sequence
    args.prepare_only = True
    def unexpected(*_):
        pytest.fail('A prepared sequence must not touch the GPU or run a child')
    report = driver.execute(args, run_stage=unexpected, check_resources=unexpected)
    assert calls == [] and report['status'] == 'prepared'
    assert report['gpu_qualified'] is False and report['training_completed'] is False
    assert report['model_quality_measured'] is False and report['app_activated'] is False


def test_completed_adapter_does_not_start_benchmarks_or_activate_app(sequence):
    driver, args, proof, calls, run, resources = sequence
    receipt = {'training': {'steps': 512, 'tokens': 1000, 'validation': {'loss': 1.2}}}
    inspected = []
    def inspect(path, qualification, supplied):
        inspected.append(path)
        assert qualification == proof and supplied is args
        return receipt
    report = driver.execute(args, run_stage=run, check_resources=resources, inspect_trained=inspect)
    assert len(calls) == 2 and inspected == [args.output / 'adapter']
    assert report['status'] == 'adapter_validated_unbenchmarked'
    assert report['training_completed'] is True
    assert report['model_quality_measured'] is False and report['app_activated'] is False
    training_command = calls[1][0]
    assert training_command[training_command.index('--qualification') + 1] == str(args.output / 'qualification.json')
    assert training_command[training_command.index('--corpus-sha256') + 1] == args.corpus_sha256


def test_sequence_inspector_accepts_actual_completed_cpu_adapter(driver_fixture):
    run, tmp_path, _ = driver_fixture
    output = run('adapter')
    spec = spec_from_file_location('training_sequence_inspector', APP / 'scripts/fusion/run_training.py')
    driver = module_from_spec(spec)
    spec.loader.exec_module(driver)
    from fusion.q6_identity import file_digest
    proof = json.loads((tmp_path / 'qualification.json').read_text())
    args = SimpleNamespace(output=tmp_path, context=1024, rank=4, seed=7, recompute=False,
        max_target_tokens=8, epochs=1, corpus_sha256=file_digest(tmp_path / 'corpus.jsonl'))
    receipt = driver.inspect_completed_adapter(output, proof, args)
    assert receipt['training']['steps'] == 1 and receipt['checkpoint'] is False
    args.epochs = 2
    with pytest.raises(ValueError, match='requested training schedule'):
        driver.inspect_completed_adapter(output, proof, args)


def test_real_owned_child_streams_unicode_into_its_log(sequence, capsys):
    driver, args, _, _, _, _ = sequence
    log = args.output.parent / 'console.log'
    code = driver.run_child([sys.executable, '-u', '-c', "print('שלום')"], log)
    assert code == 0 and log.read_text(encoding='utf-8') == 'שלום\n'
    assert 'שלום' in capsys.readouterr().out


def test_output_failure_terminates_only_the_owned_child(sequence, monkeypatch):
    driver, args, _, _, _, _ = sequence
    actual_popen, owned = driver.subprocess.Popen, []
    def launch(*positional, **named):
        child = actual_popen(*positional, **named)
        owned.append(child)
        return child
    def interrupted(*_, **__):
        raise RuntimeError('Simulated output interruption')
    monkeypatch.setattr(driver.subprocess, 'Popen', launch)
    monkeypatch.setattr(driver, 'print', interrupted, raising=False)
    with pytest.raises(RuntimeError, match='output interruption'):
        driver.run_child([sys.executable, '-u', '-c',
            "import time; print('started', flush=True); time.sleep(60)"], args.output.parent / 'interrupted.log')
    assert len(owned) == 1 and owned[0].poll() is not None
    assert (args.output.parent / 'interrupted.log').read_text().strip() == 'started'
