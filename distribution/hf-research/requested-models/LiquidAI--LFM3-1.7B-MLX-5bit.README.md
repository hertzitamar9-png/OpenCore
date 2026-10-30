---
library_name: mlx
license: other
license_name: lfm1.0
license_link: LICENSE
pipeline_tag: text-generation
tags:
- liquid
- lfm3
- mlx
language:
- en
- ar
- zh
- fr
- de
- it
- ja
- ko
- pt
- es
- vi
- th
- id
- hi
- ru
- pl
base_model: LiquidAI/LFM3-1.7B
base_model_relation: quantized
---

<div align="center">
  <img
    src="https://huggingface.co/LiquidAI/LFM3-1.7B/resolve/main/assets/liquid-ai-banner.webp"
    alt="Liquid AI"
    style="width: 100%; max-width: 100%; height: auto; display: block; margin: 0;"
  />
  <div align="center" style="display: flex; justify-content: center; align-items: center; gap: 1.25em; margin: 8px 0 1.7rem; padding: 0; line-height: 1.3;">
    <a href="https://console.liquid.ai/dashboard" style="display: inline-flex; align-items: center; gap: 6px;"><img src="https://huggingface.co/LiquidAI/LFM3-1.7B/resolve/main/assets/icons/api.svg" width="16" height="16" alt="" aria-hidden="true" style="display: block; flex: 0 0 16px; width: 16px; height: 16px; margin: 0;" /><strong>API</strong></a>
    <a href="https://playground.liquid.ai/" style="display: inline-flex; align-items: center; gap: 6px;"><img src="https://huggingface.co/LiquidAI/LFM3-1.7B/resolve/main/assets/icons/try.svg" width="16" height="16" alt="" aria-hidden="true" style="display: block; flex: 0 0 16px; width: 16px; height: 16px; margin: 0;" /><strong>Try</strong></a>
    <a href="https://docs.liquid.ai/lfm/getting-started/welcome" style="display: inline-flex; align-items: center; gap: 6px;"><img src="https://huggingface.co/LiquidAI/LFM3-1.7B/resolve/main/assets/icons/docs.svg" width="16" height="16" alt="" aria-hidden="true" style="display: block; flex: 0 0 16px; width: 16px; height: 16px; margin: 0;" /><strong>Docs</strong></a>
    <a href="https://www.liquid.ai/blog/lfm3" style="display: inline-flex; align-items: center; gap: 6px;"><img src="https://huggingface.co/LiquidAI/LFM3-1.7B/resolve/main/assets/icons/blog.svg" width="16" height="16" alt="" aria-hidden="true" style="display: block; flex: 0 0 16px; width: 16px; height: 16px; margin: 0;" /><strong>Blog</strong></a>
  </div>
</div>

# LFM3-1.7B-MLX-5bit

5-bit MLX version of [LFM3-1.7B](https://huggingface.co/LiquidAI/LFM3-1.7B) for Apple Silicon. The weights take 1.19 GB.

Weights use 5-bit affine quantization with groups of 64.

Find architecture and usage details in the [original model card](https://huggingface.co/LiquidAI/LFM3-1.7B).

## Use with mlx-lm
<hr style="margin: -0.7em 0 1.2em 0; border: 0; border-top: 1px solid rgba(127, 127, 127, 0.3);" />

Support for LFM3 in [mlx-lm](https://github.com/ml-explore/mlx-lm) is in progress. We will update this card when an mlx-lm release supports it.

```bash
mlx_lm.generate --model LiquidAI/LFM3-1.7B-MLX-5bit \
  --prompt "What is the capital of Australia?" \
  --temp 0.1 --top-k 50 --max-tokens 16384
```

The command line has no repetition penalty flag. In Python, pass `make_logits_processors(repetition_penalty=1.1)` to `generate`. For math and science, use `--temp 1.0 --top-p 0.95 --top-k 20` instead. More options in the [MLX guide](https://docs.liquid.ai/deployment/on-device/mlx).
