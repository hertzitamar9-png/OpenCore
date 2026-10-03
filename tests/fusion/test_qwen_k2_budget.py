"""Qwen3.5-9B + K2 Horizon budgets are preflighted without loading weights."""
import sys
import hashlib
import json
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src-tauri/resources'))


def budget():
    try:
        from fusion import qwen_k2_budget
    except ImportError:
        pytest.fail('The model-specific Qwen+K2 budget estimator is missing')
    return qwen_k2_budget


def test_q6_inference_artifacts_are_revision_size_and_hash_pinned():
    model = budget()
    assert model.QWEN_Q6['revision'] == '182be2fd6c7bc44887d88a91cb03ff009cc9f549'
    assert model.QWEN_Q6['filename'] == 'Qwen_Qwen3.5-9B-Q6_K.gguf'
    assert model.QWEN_Q6['bytes'] == 7_958_818_848
    assert model.QWEN_Q6['sha256'] == '073a9275e65d9c8cd2819cf5f77b99fbaa6e87ba591da6bbaa86ec073a64bfef'
    assert model.K2_Q6['revision'] == '1751cb49e31823f4401fc1d195db8a2c61a79783'
    assert model.K2_Q6['filename'] == 'K2-Horizon-4B-Q6_K.gguf'
    assert model.K2_Q6['bytes'] == 4_161_403_264
    assert model.K2_Q6['sha256'] == '2180f3ca4eb4906a109b364a98740778fd8dcd9969e270b0d34036d18ee33232'


def test_mixed_qwen_attention_and_k2_full_attention_kv_lower_bound():
    model = budget()
    assert model.estimate_attention_kv_bytes(0) == 0
    assert model.estimate_attention_kv_bytes(1) == 180_224
    assert model.estimate_attention_kv_bytes(1_000_000) == 180_224_000_000


def test_fusion_source_record_pins_unqualified_q6_estimate():
    resources = Path(__file__).resolve().parents[2] / 'src-tauri/resources/fusion'
    source = json.loads((resources / 'opencore_fusion_sources.json').read_text())
    source_models = source['checkpoints']
    model = budget()
    assert model.MANIFEST['attention_layout']['qwen']['source_revision'] == source_models['qwen']['revision']
    assert model.MANIFEST['attention_layout']['k2']['source_revision'] == source_models['k2']['revision']
    candidate = source['candidate_inference_profile']
    manifest = resources / candidate['manifest']
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == candidate['manifest_sha256']
    assert candidate['combined_weight_bytes'] == model.Q6_WEIGHT_BYTES
    assert candidate['throughput_qualified'] is False
    assert candidate['device_fit_qualified'] is False
    assert candidate['attention_kv_bytes_lower_bound']['native_shared_context_262144_tokens'] == 47_244_640_256


def test_full_q6_towers_are_rejected_on_12_gib_even_at_zero_context():
    model = budget()
    with pytest.raises(ValueError, match='resident weights'):
        model.require_gpu_budget(12 * 1024**3, context_tokens=0)


def test_cpu_offload_budget_is_reported_without_claiming_speed():
    model = budget()
    result = model.require_gpu_budget(
        12 * 1024**3,
        context_tokens=8_192,
        resident_weight_bytes=6 * 1024**3,
    )
    assert result['attention_kv_bytes_lower_bound'] == 1_476_395_008
    assert result['nonresident_weight_bytes'] == model.Q6_WEIGHT_BYTES - 6 * 1024**3
    assert result['known_payloads_fit'] is True
    assert result['qualified_for_device'] is False
    assert result['throughput_qualified'] is False


@pytest.mark.parametrize('tokens', [-1, True, 1.5, '1024'])
def test_invalid_token_counts_are_rejected(tokens):
    with pytest.raises(ValueError, match='token count'):
        budget().estimate_attention_kv_bytes(tokens)
