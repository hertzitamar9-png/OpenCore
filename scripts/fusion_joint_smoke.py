"""Bounded, one-step smoke test of the pinned Nanbeige + K2 TwinCore towers.

This is not an adapter-quality test. It uses the untrained bridge and leaves
the desktop app unchanged. Run only while the GPU is free.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
from pathlib import Path
import sys
import time
import traceback

import psutil
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig


RESOURCES = Path(__file__).resolve().parents[1] / "src-tauri" / "resources"
sys.path.insert(0, str(RESOURCES))
from fusion import CoupledFusion, ExactSurfaceAlignment, offload_output_head, verify_checkpoint  # noqa: E402


def sample_memory() -> dict[str, float]:
    return {
        "cuda_allocated_mib": round(torch.cuda.memory_allocated() / 2**20, 1),
        "cuda_reserved_mib": round(torch.cuda.memory_reserved() / 2**20, 1),
        "process_rss_gib": round(psutil.Process().memory_info().rss / 2**30, 2),
        "system_available_gib": round(psutil.virtual_memory().available / 2**30, 2),
    }


def run(source_root: Path, manifest_path: Path, output_path: Path) -> dict:
    if not torch.cuda.is_available():
        raise RuntimeError("A CUDA GPU is required for this bounded smoke test")
    result: dict = {
        "task": "full_checkpoint_joint_twincore_text_step",
        "quantization": "bitsandbytes NF4 double quantization",
        "gpu_fraction_limit": 0.88,
        "adapter": "untrained reference bridge; no quality claim",
    }
    start = time.perf_counter()
    try:
        free_bytes, total_bytes = torch.cuda.mem_get_info()
        result["gpu_free_before_mib"] = round(free_bytes / 2**20, 1)
        result["gpu_total_mib"] = round(total_bytes / 2**20, 1)
        if free_bytes < 10_000 * 2**20:
            raise RuntimeError("GPU has under 10,000 MiB free; another workload is active")
        torch.cuda.set_per_process_memory_fraction(0.88)
        torch.cuda.reset_peak_memory_stats()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        nanbeige_path = verify_checkpoint(source_root, manifest, "nanbeige4.2-3b")
        k_path = verify_checkpoint(source_root, manifest, "k2-horizon-3.7b")
        result["verified_source_seconds"] = round(time.perf_counter() - start, 3)
        print("Both complete checkpoints verified", flush=True)

        quantization = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            llm_int8_enable_fp32_cpu_offload=True,
        )
        nanbeige = AutoModelForCausalLM.from_pretrained(
            nanbeige_path, local_files_only=True, trust_remote_code=True,
            quantization_config=quantization, device_map={"": 0},
            low_cpu_mem_usage=True,
        )
        result["nanbeige_loaded_seconds"] = round(time.perf_counter() - start, 3)
        result["nanbeige_memory"] = sample_memory()
        nanbeige.get_input_embeddings().to("cpu")
        result["nanbeige_embedding_offloaded"] = True
        result["nanbeige_output_head_released_bytes"] = offload_output_head(nanbeige)
        result["nanbeige_output_head_device"] = str(nanbeige.get_output_embeddings().weight.device)
        gc.collect()
        torch.cuda.empty_cache()
        result["nanbeige_after_offload"] = sample_memory()
        print("Nanbeige loaded; embedding offloaded", result["nanbeige_after_offload"], flush=True)

        k2 = AutoModelForCausalLM.from_pretrained(
            k_path, local_files_only=True, trust_remote_code=True,
            quantization_config=quantization, device_map={"": 0}, low_cpu_mem_usage=True,
        )
        k2.get_input_embeddings().to("cpu")
        result["k2_output_head_released_bytes"] = offload_output_head(k2)
        result["k2_output_head_device"] = str(k2.get_output_embeddings().weight.device)
        torch.cuda.empty_cache()
        result["k2_loaded_seconds"] = round(time.perf_counter() - start, 3)
        result["joint_memory_before_step"] = sample_memory()
        print("Both full towers loaded", result["joint_memory_before_step"], flush=True)

        nanbeige_tokenizer = AutoTokenizer.from_pretrained(
            nanbeige_path, local_files_only=True, trust_remote_code=True
        )
        k_tokenizer = AutoTokenizer.from_pretrained(
            k_path, local_files_only=True, trust_remote_code=True
        )
        alignment = ExactSurfaceAlignment.from_tokenizers(
            nanbeige_tokenizer, k_tokenizer,
            nanbeige.get_output_embeddings().weight.shape[0],
            k2.get_output_embeddings().weight.shape[0],
        )
        result["aligned_token_pieces"] = alignment.size
        result["nanbeige_output_head_cpu_chunk_rows"] = nanbeige.get_output_embeddings().chunk_rows
        result["k2_output_head_cpu_chunk_rows"] = k2.get_output_embeddings().chunk_rows
        fusion = CoupledFusion(
            nanbeige, k2, alignment,
            nanbeige_hidden=int(nanbeige.config.hidden_size),
            k2_hidden=int(k2.config.hidden_size), rank=64,
        )
        prompt = "def add(a, b):\n    return"
        nanbeige_ids = torch.tensor(
            [nanbeige_tokenizer.encode(prompt, add_special_tokens=False)], dtype=torch.long,
            device=nanbeige.get_input_embeddings().weight.device,
        )
        k_ids = torch.tensor(
            [k_tokenizer.encode(prompt, add_special_tokens=False)], dtype=torch.long,
            device=k2.get_input_embeddings().weight.device,
        )
        step_start = time.perf_counter()
        with torch.inference_mode():
            output = fusion.step(nanbeige_ids, k_ids)
        torch.cuda.synchronize()
        result["step_seconds"] = round(time.perf_counter() - step_start, 3)
        result["nanbeige_native_logits_shape"] = list(output.nanbeige_native_logits.shape)
        result["k2_native_logits_shape"] = list(output.k2_native_logits.shape)
        result["single_stream_logits_shape"] = list(output.logits.shape)
        result["scores_finite"] = bool(torch.isfinite(output.logits).all())
        generation_start = time.perf_counter()
        pieces = list(fusion.stream_text(
            prompt, nanbeige_tokenizer, k_tokenizer, max_new_tokens=3
        ))
        torch.cuda.synchronize()
        result["generated_text"] = "".join(pieces)
        result["generated_tokens"] = len(pieces)
        result["generation_seconds"] = round(time.perf_counter() - generation_start, 3)
        result["generation_tokens_per_second"] = round(
            len(pieces) / result["generation_seconds"], 3
        ) if result["generation_seconds"] else 0.0
        result["final_nanbeige_token_count"] = len(nanbeige_tokenizer.encode(fusion.last_text, add_special_tokens=False))
        result["final_k2_token_count"] = len(fusion.last_k2_ids)
        result["peak_allocated_mib"] = round(torch.cuda.max_memory_allocated() / 2**20, 1)
        result["peak_reserved_mib"] = round(torch.cuda.max_memory_reserved() / 2**20, 1)
        result["status"] = "ok" if result["scores_finite"] else "nonfinite_scores"
    except Exception as error:
        result["status"] = "error"
        result["error"] = f"{type(error).__name__}: {str(error)[:1000]}"
        result["traceback"] = traceback.format_exc(limit=8)
        print(result["error"] + "\n" + result["traceback"], flush=True)
    finally:
        result["elapsed_seconds"] = round(time.perf_counter() - start, 3)
        result["memory_at_exit"] = sample_memory()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    completed = run(args.source_root, args.manifest, args.output)
    print(json.dumps(completed), flush=True)
    raise SystemExit(0 if completed["status"] == "ok" else 1)
