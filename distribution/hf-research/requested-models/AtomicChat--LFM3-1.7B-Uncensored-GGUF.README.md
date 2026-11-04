---
base_model: LiquidAI/LFM3-1.7B
license: other
license_name: lfm-open-license-v1.0
license_link: https://huggingface.co/LiquidAI/LFM3-1.7B/blob/main/LICENSE
language:
- en
- zh
- de
pipeline_tag: text-generation
library_name: gguf
tags:
- gguf
- quantized
- lfm3
---

# LFM3-1.7B Uncensored — GGUF (by AtomicChat)

<div style="display: flex; gap: 8px; align-items: center; margin-top: 10px; margin-bottom: 10px;">
  <a href="https://atomic.chat/?utm_source=huggingface&utm_medium=referral&utm_campaign=hf_lfm3_1_7b_uncensored&utm_content=btn_atomic"><img src="https://huggingface.co/AtomicChat/Qwen3.8-27B-GGUF/resolve/main/btn_atomic.png" width="162" alt="Atomic Chat"></a>
  <a href="https://discord.gg/8wGSsvmg4V"><img src="https://huggingface.co/AtomicChat/Qwen3.8-27B-GGUF/resolve/main/btn_discord.png" width="119" alt="Discord"></a>
  <a href="https://github.com/AtomicBot-ai/Atomic-Chat"><img src="https://huggingface.co/AtomicChat/Qwen3.8-27B-GGUF/resolve/main/btn_github.png" width="115" alt="GitHub"></a>
  <a href="https://x.com/atomic_chat_hq"><img src="https://huggingface.co/AtomicChat/Qwen3.8-27B-GGUF/resolve/main/btn_x.png" width="108" alt="X"></a>
</div>
<ul style="margin: 0 0 12px 0;">
  <li>Built by the team behind <a href="https://atomic.chat/?utm_source=huggingface&utm_medium=referral&utm_campaign=hf_lfm3_1_7b_uncensored&utm_content=bullet_app">Atomic Chat</a>, the app that runs open-weight models locally and offline.</li>
</ul>
<hr style="margin: 0 0 16px 0;">

Safety-ablated GGUF of [LiquidAI/LFM3-1.7B](https://huggingface.co/LiquidAI/LFM3-1.7B). A projected rank-1 LoRA (direction = mean harmful − mean harmless activations, with the harmless component projected out) is baked into the weights at W*=2.0, then AD-quantized.

## What changed (measured)

| metric | base | uncensored |
|---|---|---|
| refusals on harmful prompts | 61.7% | **0.0%** |
| false refusals (XSTest) | 5.2% | 0.0% |
| MMLU (2000 q) | 41.0% | 41.0% (McNemar p=1.0 — no drop) |
| needle (long context) | 3/3 | 3/3 |
| ΔKLD vs base (neutral) | — | +0.019 |

A random edit of the same size dropped MMLU 1.3pp and had a larger ΔKLD (0.028), so the targeted direction is both more effective and less damaging. Thinking-loop and empty-output checks pass at W*=2.0.

## Rungs

| Rung | GB on disk | bpw |
|---|---|---|
| AD-Q6_K | 1.6 | 7.31 |
| AD-Q4_K_M | 1.1 | 5.20 |

## Run (llama.cpp)

Needs a build with LFM3 support (merged in llama.cpp #31252; produced at llama.cpp `19cce69`).

```bash
llama-cli -hf AtomicChat/LFM3-1.7B-Uncensored-GGUF:AD-Q4_K_M -p "Hello"
```

## Base model & license

Base model: [LiquidAI/LFM3-1.7B](https://huggingface.co/LiquidAI/LFM3-1.7B). License: LFM Open License v1.0 (see `LICENSE`). This removes safety refusals; use responsibly and in line with the base license.

Quants by AtomicChat.
