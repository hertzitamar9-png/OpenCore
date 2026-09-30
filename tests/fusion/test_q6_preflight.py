"""Resource refusals must precede native loading; no GPU inference in these tests."""
from pathlib import Path
import subprocess
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src-tauri/resources'))


def module():
    try:
        from fusion import q6_preflight
    except ImportError:
        pytest.fail('The full Q6 resource and checkpoint preflight is missing')
    return q6_preflight


def test_busy_gpu_is_rejected_before_checkpoint_loading():
    with pytest.raises(ValueError, match='device budget'):
        module().require_q6_resources(context=1024, free_gpu_bytes=5_000_000_000,
                                      free_disk_bytes=245_000_000_000, environment={})


def test_disabling_cuda_cannot_silently_turn_full_q6_training_into_cpu_inference():
    with pytest.raises(ValueError, match='CUDA is disabled'):
        module().require_q6_resources(context=1024, free_gpu_bytes=13_000_000_000,
                                      free_disk_bytes=245_000_000_000,
                                      environment={'CUDA_VISIBLE_DEVICES': '-1'})


def test_disk_reserve_is_kept_even_when_the_gpu_has_room():
    with pytest.raises(ValueError, match='100 GB'):
        module().require_q6_resources(context=1024, free_gpu_bytes=13_000_000_000,
                                      free_disk_bytes=99_999_999_999, environment={})


def test_twelve_gb_budget_still_accounts_for_both_full_weights_and_both_caches():
    # Exact hand-derived Q6 bytes plus 1K KV and a 1GiB scratch reserve.
    result = module().require_q6_resources(context=1024, free_gpu_bytes=12_000_000_000,
                                          free_disk_bytes=245_000_000_000, environment={})
    assert result['required_gpu_bytes'] == 9_166_292_512
    assert result['gpu_margin_bytes'] == 2_833_707_488


def test_wrong_quantization_or_checkpoint_is_rejected_by_content(tmp_path):
    path = tmp_path / 'wrong.gguf'
    path.write_bytes(b'not the complete Q6 file')
    with pytest.raises(ValueError, match='checkpoint identity'):
        module().verify_q6_checkpoint(path, 'nanbeige')


def test_budget_preflight_does_not_import_tensor_frameworks():
    # A resource refusal must work without initializing Torch or importing CUDA.
    resources = str(Path(__file__).resolve().parents[2] / 'src-tauri/resources')
    result = subprocess.run([sys.executable, '-c',
        "import sys; sys.path.insert(0, sys.argv[1]); from fusion import q6_preflight; "
        "assert not {'torch', 'numpy', 'transformers'} & sys.modules.keys()", resources],
        text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
