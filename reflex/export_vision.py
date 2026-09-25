"""Merge a Reflex Vision LoRA adapter and write the two GGUF files OpenCore loads.

usage: export_vision.py BASE_DIR ADAPTER_DIR OUT_DIR [--llama-cpp DIR]

Writes OUT_DIR/merged (safetensors, without the duplicated lm_head), then
OUT_DIR/reflex-vision-0.8b-q8_0.gguf and OUT_DIR/reflex-vision-0.8b-mmproj-f16.gguf.
The adapter also changes the vision encoder, so both files are rebuilt.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import torch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("base")
    parser.add_argument("adapter")
    parser.add_argument("out")
    parser.add_argument("--llama-cpp", default=str(Path.home() / ".unsloth" / "llama.cpp"))
    args = parser.parse_args()
    from peft import PeftModel
    from safetensors.torch import load_file, save_file
    from transformers import Qwen3_5ForConditionalGeneration

    out = Path(args.out)
    merged = out / "merged"
    merged.mkdir(parents=True, exist_ok=True)
    model = Qwen3_5ForConditionalGeneration.from_pretrained(args.base, dtype=torch.bfloat16, device_map="cpu",
                                                            local_files_only=True)
    model = PeftModel.from_pretrained(model, args.adapter).merge_and_unload()
    model.save_pretrained(merged, safe_serialization=True, max_shard_size="8GB")
    for name in ("tokenizer.json", "tokenizer_config.json", "vocab.json", "merges.txt", "chat_template.jinja",
                 "preprocessor_config.json", "video_preprocessor_config.json"):
        if (Path(args.base) / name).is_file():
            shutil.copy2(Path(args.base) / name, merged / name)
    # Tied embeddings: keep one copy so the GGUF holds 0.85B parameters, not 1.1B.
    for shard in merged.glob("*.safetensors"):
        tensors = load_file(shard)
        if "lm_head.weight" in tensors:
            del tensors["lm_head.weight"]
            save_file(tensors, shard, metadata={"format": "pt"})
    index = merged / "model.safetensors.index.json"
    if index.exists() and len(list(merged.glob("*.safetensors"))) == 1:
        index.unlink()

    env = {**os.environ, "PYTHONPATH": str(Path(args.llama_cpp) / "gguf-py"), "PYTHONIOENCODING": "utf-8"}
    convert = [sys.executable, str(Path(args.llama_cpp) / "convert_hf_to_gguf.py"), str(merged)]
    bf16 = out / "reflex-vision-0.8b-bf16.gguf"
    subprocess.run(convert + ["--no-mtp", "--outtype", "bf16", "--outfile", str(bf16)], env=env, check=True)
    subprocess.run(convert + ["--mmproj", "--outtype", "f16", "--outfile", str(out / "reflex-vision-0.8b-mmproj-f16.gguf")],
                   env=env, check=True)
    quantize = Path(args.llama_cpp) / "build" / "bin" / "Release" / "llama-quantize.exe"
    subprocess.run([str(quantize), str(bf16), str(out / "reflex-vision-0.8b-q8_0.gguf"), "Q8_0"], check=True)
    print("wrote", out / "reflex-vision-0.8b-q8_0.gguf", out / "reflex-vision-0.8b-mmproj-f16.gguf")


if __name__ == "__main__":
    main()
