"""Numerical DLL and qualification checks; these tests load no model or GPU."""
import hashlib
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src-tauri/resources'))


def modules():
    try:
        from fusion import native_library, qualification
    except ImportError:
        pytest.fail('Mapped native numerical libraries and resource qualifications must be verified')
    return native_library, qualification


def test_loaded_numerical_dll_cannot_be_substituted_by_another_cuda_framework(tmp_path):
    library, _ = modules()
    correct = tmp_path / 'cublas64_13.dll'
    correct.write_bytes(b'pinned CUDA library')
    wrong = tmp_path / 'torch' / correct.name
    wrong.parent.mkdir()
    wrong.write_bytes(b'another version of the same library')
    record = {'scope': 'runtime', 'path': correct.name, 'bytes': correct.stat().st_size,
              'sha256': hashlib.sha256(correct.read_bytes()).hexdigest()}
    with pytest.raises(RuntimeError, match='loaded.*identity'):
        library.validate_loaded_libraries([record], {correct.name: str(wrong)}, required=[correct.name])
    measured = library.validate_loaded_libraries([record], {correct.name: str(correct)}, required=[correct.name])
    assert measured[correct.name]['sha256'] == record['sha256']


def test_a_required_native_dependency_cannot_be_missing_from_mapped_modules(tmp_path):
    library, _ = modules()
    with pytest.raises(RuntimeError, match='not loaded'):
        library.validate_loaded_libraries([], {}, required=['ggml-cuda.dll'])


def resource_receipt(configuration):
    return {'schema': 2, 'status': 'full_q6_resource_probe_passed', 'gpu_qualified': True,
            'models_loaded': True, 'models_released': True, 'configuration': configuration,
            'gpu_before': {'uuid': 'GPU-qualified'},
            'probe': {'finite_bridge_gradients': True, 'complete_target': True, 'tokens': 12},
            'placement': [{'physical_matrix_layers': 22, 'gpu_matrix_layers': 22, 'head_on_gpu': 1},
                          {'physical_matrix_layers': 36, 'gpu_matrix_layers': 36, 'head_on_gpu': 1}]}


@pytest.mark.parametrize('change', ['context', 'memory', 'gpu', 'incomplete', 'cpu-head'])
def test_full_q6_training_refuses_a_qualification_for_different_execution(change):
    _, qualification = modules()
    requested = qualification.execution_configuration(context=1024, rank=256, seed=7, recompute=False)
    report = resource_receipt(dict(requested))
    gpu = 'GPU-qualified'
    if change == 'context':
        report['configuration']['context'] = 512
    elif change == 'memory':
        report['configuration']['memory_mode'] = 'recompute'
    elif change == 'gpu':
        gpu = 'GPU-another'
    elif change == 'incomplete':
        report['probe']['complete_target'] = False
    else:
        report['placement'][1]['head_on_gpu'] = 0
    with pytest.raises(ValueError, match='qualification'):
        qualification.validate_qualification(report, requested, gpu)


def test_real_full_q6_qualification_matches_execution_and_bound_model_identity():
    _, qualification = modules()
    configuration = qualification.execution_configuration(context=1024, rank=256, seed=7, recompute=True)
    report = resource_receipt(configuration)
    report['binding'] = {'model': 'pinned full pair'}
    qualification.validate_qualification(report, configuration, 'GPU-qualified', binding=report['binding'])
    with pytest.raises(ValueError, match='identity'):
        qualification.validate_qualification(report, configuration, 'GPU-qualified', binding={'model': 'drift'})
