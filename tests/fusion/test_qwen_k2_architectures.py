"""Tiny text-only contract test for the pinned OpenCore Fusion architectures.

This does not load pretrained weights, train the bridge, or qualify vision,
quality, memory use, speed, long context, or the production runtime.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("transformers")

from transformers import AutoConfig, AutoModelForCausalLM  # noqa: E402

RESOURCE_ROOT = Path(__file__).resolve().parents[2] / "src-tauri" / "resources"
MANIFEST_PATH = RESOURCE_ROOT / "fusion" / "opencore_fusion_sources.json"
ARCHITECTURE_MANIFEST_PATH = RESOURCE_ROOT / "fusion" / "checkpoints.sha256.json"
sys.path.insert(0, str(RESOURCE_ROOT))


def _source_path(env_name: str, cache_name: str, revision: str) -> Path:
    configured = os.environ.get(env_name)
    if configured:
        return Path(configured)
    return (
        Path.home() / ".cache" / "huggingface" / "hub"
        / f"models--{cache_name}" / "snapshots" / revision
    )


def _assert_sha256(path: Path, expected: str) -> None:
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected


def test_pinned_qwen_text_and_k2_architectures_produce_one_joint_step():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    qwen_manifest = manifest["checkpoints"]["qwen"]
    k2_manifest = manifest["checkpoints"]["k2"]
    qwen = _source_path(
        "OPENCORE_FUSION_QWEN_SOURCE", "Qwen--Qwen3.5-9B",
        qwen_manifest["revision"],
    )
    k2 = _source_path(
        "OPENCORE_FUSION_K2_SOURCE", "IFM--K2-Horizon-3.7B",
        k2_manifest["revision"],
    )
    if not qwen.is_dir() or not k2.is_dir():
        pytest.skip("Pinned Qwen and K2 architecture sources are not cached")

    _assert_sha256(
        qwen / "config.json", qwen_manifest["source_config"]["sha256"],
    )
    _assert_sha256(
        k2 / "config.json", k2_manifest["source_config"]["sha256"],
    )
    architecture_manifest = json.loads(
        ARCHITECTURE_MANIFEST_PATH.read_text(encoding="utf-8"),
    )
    _assert_sha256(
        k2 / "modeling_k2_horizon.py",
        architecture_manifest["models"]["k2-horizon-3.7b"]
        ["files"]["modeling_k2_horizon.py"]["sha256"],
    )

    qwen_config = AutoConfig.from_pretrained(
        qwen, local_files_only=True,
    ).text_config
    for key, value in {
        "vocab_size": 32,
        "hidden_size": 64,
        "intermediate_size": 128,
        "num_hidden_layers": 4,
        "num_attention_heads": 4,
        "num_key_value_heads": 2,
        "head_dim": 16,
        "max_position_embeddings": 64,
        "layer_types": qwen_config.layer_types[:4],
        "linear_num_key_heads": 4,
        "linear_num_value_heads": 4,
        "linear_key_head_dim": 16,
        "linear_value_head_dim": 16,
        "mtp_num_hidden_layers": 0,
    }.items():
        setattr(qwen_config, key, value)
    qwen_model = AutoModelForCausalLM.from_config(qwen_config)

    k2_config = AutoConfig.from_pretrained(
        k2, trust_remote_code=True, local_files_only=True,
    )
    for key, value in {
        "vocab_size": 32,
        "hidden_size": 64,
        "intermediate_size": 128,
        "num_hidden_layers": 2,
        "num_attention_heads": 4,
        "num_key_value_heads": 2,
        "head_dim": 16,
        "max_position_embeddings": 64,
        "mlp_only_layers": [0, 1],
        "num_experts": 0,
        "num_experts_per_tok": 0,
        "moe_intermediate_size": 0,
        "rope_head_dim": 16,
    }.items():
        setattr(k2_config, key, value)
    k2_model = AutoModelForCausalLM.from_config(
        k2_config, trust_remote_code=True,
    )

    from fusion.alignment import ExactSurfaceAlignment
    from fusion.coupled import CoupledFusion

    model = CoupledFusion(
        qwen_model,
        k2_model,
        ExactSurfaceAlignment([1, 2], [1, 2], 32, 32),
        nanbeige_hidden=64,
        k2_hidden=64,
        rank=16,
    )
    with torch.inference_mode():
        cached_prefix = model.step(
            torch.tensor([[1, 2]]),
            torch.tensor([[1, 2]]),
            use_cache=True,
        )
        full_result = model.step(
            torch.tensor([[1, 2, 3]]),
            torch.tensor([[1, 2, 3]]),
            feedback=cached_prefix.feedback,
        )
        cached_result = model.step(
            torch.tensor([[3]]),
            torch.tensor([[3]]),
            feedback=cached_prefix.feedback,
            first_cache=cached_prefix.first_cache,
            second_cache=cached_prefix.second_cache,
            use_cache=True,
        )

    assert torch.isfinite(cached_result.logits).all()
    assert torch.allclose(cached_result.logits, full_result.logits, atol=2e-4, rtol=2e-4)
    assert cached_result.logits.shape == (1, 32)
    assert cached_result.first_cache is not None and cached_result.second_cache is not None
    assert all(not parameter.requires_grad for parameter in qwen_model.parameters())
    assert all(not parameter.requires_grad for parameter in k2_model.parameters())
