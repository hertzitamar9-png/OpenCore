---
license: apache-2.0
base_model: microsoft/Mage-VL-Cowork
base_model_relation: quantized
library_name: mlx
pipeline_tag: image-text-to-text
tags:
- mlx
- mlx-vlm
- computer-use
- vision-language
- apple-silicon
---

# Mage-VL-Cowork MLX 4-bit

A 4-bit MLX build of [microsoft/Mage-VL-Cowork](https://huggingface.co/microsoft/Mage-VL-Cowork), Microsoft's 4B computer-use model (Mage-ViT vision encoder + Qwen3-4B language model), so you can run it on an Apple Silicon Mac.

**This is a community MLX conversion, not an official Microsoft release.** All credit for the model goes to Microsoft. License stays Apache-2.0 (see `LICENSE`, copied from the original repo).

## How it was made

1. The original checkpoint uses a different weight key layout than mlx-vlm expects, so the weight keys were remapped to the `mage_vl` layout. Only key names and a few config fields changed; the tensors were copied unchanged.
2. Converted with `mlx_vlm.convert` (mlx 0.32.4, mlx-vlm 0.7.11):
   - language model: 4-bit affine quantization, group size 64
   - vision tower: kept in BF16
   - about 3.14 GB on disk, 5.30 bits per weight on average

The remap script is in the benchmark dataset (`raw/scripts/remap.py`) if you want to redo it yourself.

## How it loads

`config.json` has `model_type: mage_vl`, which is a built-in model in mlx-vlm 0.7.11 (`mlx_vlm/models/mage_vl`). No custom modeling code or `trust_remote_code` is needed. It was tested with stock mlx-vlm 0.7.11 on a 64GB M5 Pro Mac mini; older mlx-vlm versions without `mage_vl` won't load it.

```bash
pip install -U mlx-vlm

python -m mlx_vlm.generate \
  --model pratikgx/Mage-VL-Cowork-MLX-4bit \
  --image screenshot.png \
  --prompt "Turn on Dark Mode." \
  --max-tokens 512
```

For agent use, pass Mage's official system prompt and first-step message format from the [original model card](https://huggingface.co/microsoft/Mage-VL-Cowork) (`--system` works for the system prompt). The exact prompt used in the benchmarks is in the dataset at `raw/scripts/system_prompt.txt`.

## Speed on a 64GB M5 Pro Mac mini

Measured with mlx-vlm 0.7.11, greedy, 256 generated tokens, median of 3 runs after 1 warmup. A background inference app was running on the Mac during the benchmarks, so an idle machine may be a bit faster.

| Context | Prefill tok/s | Decode tok/s | Time to first token | Peak memory |
|---|---|---|---|---|
| 512 | 2,709 | 95.6 | 0.19s | 3.87 GB |
| 4K | 2,341 | 79.5 | 1.76s | 4.57 GB |
| 16K | 1,728 | 50.2 | 9.50s | 6.27 GB |
| 32K | 1,237 | 34.5 | 26.5s | 8.80 GB |

**One full agent turn** (screenshot in, tool call out): **2.36s** at 1280x800 and **3.10s** at 1920x1080.

## Caveats

- **Speed only.** No quality suite was run, and no OSWorld run.
- In a tiny 3-task demo, this 4-bit build missed a Dark Mode toggle click by 42px, while the BF16 MLX build got it right. That's a very small sample and not an OSWorld score, so don't read too much into it.
- Community port: if something looks off compared to Microsoft's PyTorch code, it's most likely the conversion, not the model.

Full results, raw logs, scripts and the demo traces: https://huggingface.co/datasets/pratikgx/mage-vl-cowork-m5-pro-mac-mini-bench

<!-- github-field-notes:start -->
## Write-up on GitHub

How I ran this, what broke, and the scripts: [Microsoft's Mage-VL-Cowork 4B on my Mac mini](https://github.com/pratikgx/local-ai-field-notes/blob/main/notes/2026-10-30-mage-vl-cowork.md). It's part of [Local AI field notes](https://github.com/pratikgx/local-ai-field-notes), where I log every model I run on my Mac mini.
<!-- github-field-notes:end -->
