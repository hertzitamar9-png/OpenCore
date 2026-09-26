"""Tiny CPU contract test for the pinned Nanbeige + K2 decoder classes."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

torch = pytest.importorskip("torch")
transformers = pytest.importorskip("transformers")

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src-tauri" / "resources"))
from fusion.alignment import ExactSurfaceAlignment  # noqa: E402
from fusion.coupled import CoupledFusion  # noqa: E402


def test_pinned_nanbeige_and_k2_classes_supply_one_joint_step_on_cpu():
    from transformers import AutoConfig, AutoModelForCausalLM

    source = Path(os.environ.get(
        "OPENCORE_FUSION_SOURCE",
        r"C:\Users\hertz\Documents\OpenCoreFusion\hf-source",
    ))
    manifest_path = Path(__file__).resolve().parents[2] / "src-tauri" / "resources" / "fusion" / "checkpoints.sha256.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    nanbeige_info = manifest["models"]["nanbeige4.2-3b"]
    k2_info = manifest["models"]["k2-horizon-3.7b"]
    nanbeige_dir = source / nanbeige_info["folder"]
    k2_dir = source / k2_info["folder"]
    if not nanbeige_dir.is_dir() or not k2_dir.is_dir():
        pytest.skip("Pinned Nanbeige or K2 custom model source is not installed")

    expected_k2 = manifest["models"]["k2-horizon-3.7b"]["files"]["modeling_k2_horizon.py"]["sha256"]
    assert hashlib.sha256((k2_dir / "modeling_k2_horizon.py").read_bytes()).hexdigest() == expected_k2

    nanbeige_config = AutoConfig.from_pretrained(
        nanbeige_dir, trust_remote_code=True, local_files_only=True,
    )
    for key, value in {
        "vocab_size": 32, "hidden_size": 64, "intermediate_size": 128,
        "num_hidden_layers": 2, "num_attention_heads": 4,
        "num_key_value_heads": 2, "head_dim": 16,
        "max_position_embeddings": 64, "num_loops": 1,
    }.items():
        setattr(nanbeige_config, key, value)
    nanbeige = AutoModelForCausalLM.from_config(
        nanbeige_config, trust_remote_code=True,
    )

    k_config = AutoConfig.from_pretrained(k2_dir, trust_remote_code=True, local_files_only=True)
    for key, value in {
        "vocab_size": 32, "hidden_size": 64, "intermediate_size": 128,
        "num_hidden_layers": 2, "num_attention_heads": 4, "num_key_value_heads": 2,
        "head_dim": 16, "max_position_embeddings": 64, "mlp_only_layers": [0, 1],
        "num_experts": 0, "num_experts_per_tok": 0, "moe_intermediate_size": 0,
        "rope_head_dim": 16,
    }.items():
        setattr(k_config, key, value)
    k2 = AutoModelForCausalLM.from_config(k_config, trust_remote_code=True)

    alignment = ExactSurfaceAlignment([1], [4], 32, 32)
    model = CoupledFusion(
        nanbeige, k2, alignment, nanbeige_hidden=64, k2_hidden=64, rank=16,
    )
    with torch.inference_mode():
        result = model.step(torch.tensor([[1, 2]]), torch.tensor([[4, 5]]))

    assert result.logits.shape == (1, 32)
    assert result.nanbeige_native_logits.shape == (1, 32)
    assert result.k2_native_logits.shape == (1, 32)
    assert torch.isfinite(result.logits).all()
