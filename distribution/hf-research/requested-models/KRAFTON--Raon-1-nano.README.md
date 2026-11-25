---
library_name: transformers
license: apache-2.0
pipeline_tag: text-generation
tags:
  - raon
---

# Raon-1 nano

<div align="center">
  <img class="block dark:hidden" src="https://huggingface.co/KRAFTON/Raon-1-nano/resolve/main/assets/raon-1-nano-logo.png" alt="Raon-1 nano" width="400">
  <img class="hidden dark:block" src="https://huggingface.co/KRAFTON/Raon-1-nano/resolve/main/assets/raon-1-nano-logo-dark.png" alt="Raon-1 nano" width="400">
</div>

<p align="center">
  <a href="https://krafton.ai/en/"><img src="https://img.shields.io/badge/Homepage-KRAFTON%20AI-blue?style=flat&logo=google-chrome&logoColor=white" alt="Homepage"></a>
  <a href="https://huggingface.co/KRAFTON"><img src="https://img.shields.io/badge/%F0%9F%A4%97%20Hugging%20Face-KRAFTON-yellow?style=flat" alt="Hugging Face"></a>
  <a href="https://www.apache.org/licenses/LICENSE-2.0"><img src="https://img.shields.io/badge/License-Apache%202.0-lightgrey?style=flat" alt="License"></a>
  <br>
  <a href="https://x.com/Krafton_AI"><img src="https://img.shields.io/badge/X-KRAFTON%20AI-white?style=flat&logo=x&logoColor=black" alt="X"></a>
  <a href="https://discord.gg/sDG7mmZPnc"><img src="https://img.shields.io/badge/Discord-Community-5865F2?style=flat&logo=discord&logoColor=white" alt="Discord Community"></a>
</p>

Raon-1 nano is a dense language model with 1.5B effective parameters (3.1B total, including a 1.5B per-layer embedding table), developed by [KRAFTON AI](https://krafton.ai/en/). It is designed to **follow instructions, maintain conversational context, and call the right tools in both Korean and English**. As part of the Raon open model family, it is released under the **Apache 2.0 license**.

## Highlights

- **Faithful, natural conversation**: Leads the comparison models on the instruction-following, context-following, and Korean conversation benchmarks shown below.
- **Korean and English**: Trained on Korean and English, with solid English capabilities alongside its Korean strengths.
- **Tool calling**: Tool calling within conversations, for everyday tasks such as office work and mobile control.
- **On-device deployment**: Available in GGUF, MLX, and LiteRT-LM formats for local inference, including on Apple silicon.
- **Long context**: Natively supports up to 262,144 tokens of context.

<p align="center">
  <img src="https://huggingface.co/KRAFTON/Raon-1-nano/resolve/main/assets/raon-1-nano-main-plot.png" alt="Raon-1 nano vs. open models of similar size" width="100%">
</p>

## Model Overview

Raon-1 nano uses a hybrid dense architecture, consisting of 18 linear attention layers and
6 full attention layers.

- **Raon Delta Attention (RDA):** Gated delta-rule linear attention with separate erase and write
  gates and a fixed-size recurrent state.
- **Raon-mHC:** Raon-mHC reduces the computational cost of DeepSeek's manifold-constrained
  hyper-connections (mHC), which speeds up training.
- **Per-layer embedding:** Each layer has its own small 256-dimensional embedding for every token.
  After its MLP, the layer uses it to adjust its output before passing it on.
- **Hybrid layout:** 18 of the 24 layers are Raon Delta Attention layers with a fixed-size state.
  Only the 6 MLA layers keep a KV cache, about 7 KiB per token in BF16.
- **Tied embeddings:** The LM head shares the input embedding.

| Field | Value |
|---|---|
| Architecture | Dense |
| Total Parameters | 3.1B (including the 1.5B per-layer embedding table) |
| Layers | 24 |
| Hidden Size | 1,536 |
| Attention | Hybrid (RDA 3 : Gated MLA 1) |
| Position Encoding | NoPE (no positional encoding) |
| Number of Attention Heads | 16 for RDA; 32 for Gated MLA |
| Attention Head Dimension | 128 for RDA; 192 QK / 128 V, for Gated MLA |
| Query / KV LoRA Rank | 512 / 512, for Gated MLA |
| FFN Intermediate Size | 6,144 |
| Activation Function | SwiGLU |
| Residual Connection | Raon-mHC w/ 4 streams |
| Per-Layer Embedding | 256 per layer |
| Vocabulary Size | 259,840 |
| LM Head | Tied |
| MTP | 1 layer, 55M |
| Context Length | 262,144 tokens |

The checkpoint on HuggingFace contains 3.2B parameters: 3.1B for the main model and 55M for the Multi-Token Prediction (MTP) module.

---

## Training

- **MoE-to-Dense:** Raon-1 nano was derived from Raon-1 mini preview (30B-A4B) through [MoE-to-Dense](https://arxiv.org/abs/2605.28207) pruning and knowledge distillation.
- **Continued pre-training:** 3T tokens.
- **Post-training:** supervised fine-tuning (SFT) and on-policy distillation (OPD) from Raon-1 mini preview, followed by reinforcement learning (RL).

---

## Benchmark Results

* Red indicates that Raon-1 nano is the best among the compared models; bold indicates the best model when another model leads.

<div style="font-family:'Pretendard','Noto Sans KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;max-width:1180px;margin:0 auto;padding:0;">
<div>
<table style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;border-collapse:collapse;width:100%;table-layout:fixed;font-size:14px;">
<colgroup><col style="width:12%"><col style="width:22%"><col style="width:13.2%"><col style="width:13.2%"><col style="width:13.2%"><col style="width:13.2%"><col style="width:13.2%"></colgroup>
<tr><th style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;background:#000;color:#ffffff;text-align:left;padding:12px 14px;font-weight:700;vertical-align:middle;">Category</th><th style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;background:#000;color:#ffffff;text-align:left;padding:12px 14px;font-weight:700;vertical-align:middle;">Benchmark</th>
<th style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;color:#fff;font-weight:600;font-size:13px;padding:10px 6px;text-align:center;vertical-align:middle;background:#e0362f;">Raon-1 nano<br>(E1.5B)</th>
<th style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;color:#fff;font-weight:600;font-size:13px;padding:10px 6px;text-align:center;vertical-align:middle;background:#2a2a2a;">Qwen3.5<br>2B</th>
<th style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;color:#fff;font-weight:600;font-size:13px;padding:10px 6px;text-align:center;vertical-align:middle;background:#2a2a2a;">Gemma 4<br>E2B</th>
<th style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;color:#fff;font-weight:600;font-size:13px;padding:10px 6px;text-align:center;vertical-align:middle;background:#2a2a2a;">MiniCPM5<br>2B</th>
<th style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;color:#fff;font-weight:600;font-size:13px;padding:10px 6px;text-align:center;vertical-align:middle;background:#2a2a2a;">LFM3<br>1.7B</th>
</tr>
<tr>
<td colspan="2" style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 14px;font-weight:700;text-align:left;vertical-align:middle;color:#1a1a1a;background:#ffffff;border-bottom:2px solid #9a9a9a;">Total params<br>(effective)</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;text-align:center;color:#1a1a1a;font-size:15px;background:#fef0ef;border-bottom:2px solid #9a9a9a;">3.1B<br>(E1.5B)</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;border-bottom:2px solid #9a9a9a;">2.3B</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;border-bottom:2px solid #9a9a9a;">5.1B<br>(E2.3B)</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;border-bottom:2px solid #9a9a9a;">2.5B</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;border-bottom:2px solid #9a9a9a;">1.7B</td>
</tr>
<tr>
<td rowspan="4" style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 14px;font-weight:700;font-size:15px;text-align:left;vertical-align:middle;white-space:normal;background:#ffffff;color:#1a1a1a;border-bottom:1px solid #e6e6e6;">Knowledge</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 14px;border-bottom:1px solid #e6e6e6;color:#1a1a1a;font-weight:500;text-align:left;white-space:normal;background:#ffffff;"><span style="font-size:15px;font-weight:400;">MMLU-Redux</span></td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#fef0ef;">81.1</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;">81.5</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;">78.9</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;font-weight:800;background:#ffffff;">84.0</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;">81.1</td>
</tr>
<tr>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 14px;border-bottom:1px solid #e6e6e6;color:#1a1a1a;font-weight:500;text-align:left;white-space:normal;background:#f7f7f7;"><span style="font-size:15px;font-weight:400;">KMMLU-Redux (ko)</span></td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;color:#e32e5e;font-weight:800;background:#fde4e2;">50.9</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#f7f7f7;">46.1</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#f7f7f7;">44.9</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#f7f7f7;">43.0</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#f7f7f7;">45.2</td>
</tr>
<tr>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 14px;border-bottom:1px solid #e6e6e6;color:#1a1a1a;font-weight:500;text-align:left;white-space:normal;background:#ffffff;"><span style="font-size:15px;font-weight:400;">HAE-RAE 1.1 (ko)</span></td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#fef0ef;">44.2</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;">36.2</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;font-weight:800;background:#ffffff;">45.2</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;">36.1</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;">34.3</td>
</tr>
<tr>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 14px;border-bottom:1px solid #e6e6e6;color:#1a1a1a;font-weight:500;text-align:left;white-space:normal;background:#f7f7f7;"><span style="font-size:15px;font-weight:400;">CLIcK (ko)</span></td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;color:#e32e5e;font-weight:800;background:#fde4e2;">65.2</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#f7f7f7;">54.8</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#f7f7f7;">62.2</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#f7f7f7;">54.6</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#f7f7f7;">54.1</td>
</tr>
<tr>
<td rowspan="2" style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 14px;font-weight:700;font-size:15px;text-align:left;vertical-align:middle;white-space:normal;background:#ffffff;color:#1a1a1a;border-bottom:1px solid #e6e6e6;">Instruction Following</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 14px;border-bottom:1px solid #e6e6e6;color:#1a1a1a;font-weight:500;text-align:left;white-space:normal;background:#ffffff;"><span style="font-size:15px;font-weight:400;">IFBench</span></td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;color:#e32e5e;font-weight:800;background:#fde4e2;">75.9</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;">45.7</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;">39.4</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;">63.8</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;">73.7</td>
</tr>
<tr>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 14px;border-bottom:1px solid #e6e6e6;color:#1a1a1a;font-weight:500;text-align:left;white-space:normal;background:#f7f7f7;"><span style="font-size:15px;font-weight:400;">MultiChallenge</span></td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;color:#e32e5e;font-weight:800;background:#fde4e2;">40.5</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#f7f7f7;">37.0</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#f7f7f7;">32.3</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#f7f7f7;">28.1</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#f7f7f7;">39.0</td>
</tr>
<tr>
<td rowspan="3" style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 14px;font-weight:700;font-size:15px;text-align:left;vertical-align:middle;white-space:normal;background:#ffffff;color:#1a1a1a;border-bottom:1px solid #e6e6e6;">Context<br>Following</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 14px;border-bottom:1px solid #e6e6e6;color:#1a1a1a;font-weight:500;text-align:left;white-space:normal;background:#ffffff;"><span style="font-size:15px;font-weight:400;">FACTS Grounding</span></td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;color:#e32e5e;font-weight:800;background:#fde4e2;">44.4</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;">13.8</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;">40.5</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;">28.4</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;">7.6</td>
</tr>
<tr>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 14px;border-bottom:1px solid #e6e6e6;color:#1a1a1a;font-weight:500;text-align:left;white-space:normal;background:#f7f7f7;"><span style="font-size:15px;font-weight:400;">PersonaMem v2<sup>1</sup></span></td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;color:#e32e5e;font-weight:800;background:#fde4e2;">38.8</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#f7f7f7;">29.2</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#f7f7f7;">32.7</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#f7f7f7;">29.3</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#f7f7f7;">28.8</td>
</tr>
<tr>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 14px;border-bottom:1px solid #e6e6e6;color:#1a1a1a;font-weight:500;text-align:left;white-space:normal;background:#ffffff;"><span style="font-size:15px;font-weight:400;">PrefEval<sup>2</sup></span></td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;color:#e32e5e;font-weight:800;background:#fde4e2;">44.0</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;">26.1</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;">23.3</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;">24.4</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;">19.6</td>
</tr>
<tr>
<td rowspan="5" style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 14px;font-weight:700;font-size:15px;text-align:left;vertical-align:middle;white-space:normal;background:#ffffff;color:#1a1a1a;border-bottom:1px solid #e6e6e6;">Tool Use</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 14px;border-bottom:1px solid #e6e6e6;color:#1a1a1a;font-weight:500;text-align:left;white-space:normal;background:#f7f7f7;"><span style="font-size:15px;font-weight:400;">Office Work Bench (ko, internal)<sup>3</sup></span><br><span style="font-size:12px;color:#8a8a8a;font-weight:400;">Workplace</span></td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;color:#e32e5e;font-weight:800;background:#fde4e2;">49.2</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#f7f7f7;">15.7</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#f7f7f7;">19.8</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#f7f7f7;">33.9</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#f7f7f7;">39.8</td>
</tr>
<tr>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 14px;border-bottom:1px solid #e6e6e6;color:#1a1a1a;font-weight:500;text-align:left;white-space:normal;background:#ffffff;"><span style="font-size:15px;font-weight:400;">BFCL v4</span><br><span style="font-size:12px;color:#8a8a8a;font-weight:400;">General function calling</span></td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#fef0ef;">56.3</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;">41.2</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;">37.9</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;">61.0</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;font-weight:800;background:#ffffff;">67.2</td>
</tr>
<tr>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 14px;border-bottom:1px solid #e6e6e6;color:#1a1a1a;font-weight:500;text-align:left;white-space:normal;background:#f7f7f7;"><span style="font-size:15px;font-weight:400;">AndroidControl<sup>4</sup></span><br><span style="font-size:12px;color:#8a8a8a;font-weight:400;">Mobile</span></td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;color:#e32e5e;font-weight:800;background:#fde4e2;">36.0</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#f7f7f7;">26.9</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#f7f7f7;">35.0</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#f7f7f7;">31.7</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#f7f7f7;">17.5</td>
</tr>
<tr>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 14px;border-bottom:1px solid #e6e6e6;color:#1a1a1a;font-weight:500;text-align:left;white-space:normal;background:#ffffff;"><span style="font-size:15px;font-weight:400;">NL2SH-ALFA</span><br><span style="font-size:12px;color:#8a8a8a;font-weight:400;">Terminal</span></td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#fef0ef;">58.0</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;">43.9</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;font-weight:800;background:#ffffff;">62.6</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;">61.4</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;">57.3</td>
</tr>
<tr>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 14px;border-bottom:1px solid #e6e6e6;color:#1a1a1a;font-weight:500;text-align:left;white-space:normal;background:#f7f7f7;"><span style="font-size:15px;font-weight:400;">BrowseComp Plus</span><br><span style="font-size:12px;color:#8a8a8a;font-weight:400;">Search</span></td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#fef0ef;">34.5</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#f7f7f7;">25.7</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#f7f7f7;">17.2</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;font-weight:800;background:#f7f7f7;">37.2</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#f7f7f7;">33.6</td>
</tr>
<tr>
<td rowspan="1" style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 14px;font-weight:700;font-size:15px;text-align:left;vertical-align:middle;white-space:normal;background:#ffffff;color:#1a1a1a;border-bottom:1px solid #e6e6e6;">Korean Conversation</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 14px;border-bottom:1px solid #e6e6e6;color:#1a1a1a;font-weight:500;text-align:left;white-space:normal;background:#ffffff;"><span style="font-size:15px;font-weight:400;">Chatbot Korean (ko, internal)<sup>5</sup></span></td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;color:#e32e5e;font-weight:800;background:#fde4e2;">91.0</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;">42.2</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;">87.6</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;">41.6</td>
<td style="font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;padding:12px 6px;border-bottom:1px solid #e6e6e6;text-align:center;color:#1a1a1a;font-size:15px;background:#ffffff;">58.3</td>
</tr>
</table>
</div>
</div>
<p style="margin:0;font-size:10px;opacity:0.7">
<sup>1</sup> PersonaMem v2: 32k context setting.<br/><sup>2</sup> PrefEval: generation task, average of 10 / 70 / 150 conversation turns.<br/><sup>3</sup> Office Work Bench: an internal benchmark measuring diverse office tasks performed in Slack, including scheduling meetings and searching documents or threads. Not publicly released.<br/><sup>4</sup> AndroidControl: average of high-level (HL) and low-level (LL) instruction settings.<br/><sup>5</sup> Chatbot Korean: an internal benchmark measuring Korean colloquial ability in chatbot conversations across diverse situations. Not publicly released.<br/>Raon-1 nano sampling: temperature 0.7 for Knowledge, temperature 1.0 for Tool Use, temperature 0.1 with presence penalty 1.5 for the others. Baselines use the sampling parameters recommended in each model card.</p>

---

## Sampling parameters

> [!Important]
> Raon-1 nano is designed for thinking mode; disabling it is not recommended.

Recommended settings:

| Task | temperature | presence_penalty |
|---|---|---|
| General (chat, knowledge Q&A, instruction following, etc.) | 0.1 | 1.5 |
| Agentic / code | 1.0 | 0.0 |

Thinking is enabled by default in the chat template. `generation_config.json` defines the EOS and
padding token IDs; it does not set sampling parameters. The output token limit (`max_new_tokens`,
`max_tokens`) includes both thinking and the final answer; increase it for longer responses.

## Quickstart

Raon-1 nano supports inference with Transformers, vLLM, llama.cpp, MLX, and LiteRT-LM.
The examples below show how to run the model locally or launch an OpenAI-compatible server
with thinking mode enabled (the LiteRT-LM bundle answers without thinking). The vLLM and llama.cpp examples use the recommended general
sampling settings above; the Transformers example uses greedy decoding for a simple first run.

<details open>
<summary><strong>Transformers</strong></summary>

Use Transformers to load Raon-1 nano and generate responses directly in Python.
The Transformers example uses Python 3.12, PyTorch 2.13.0 with CUDA 13.0,
Transformers 5.16.1, and Accelerate 1.15.0.

Create an environment and install a PyTorch build suitable for your hardware, then install:

```bash
uv venv --python 3.12 --seed
source .venv/bin/activate

# Install the appropriate PyTorch build in this environment first.
pip install \
    "transformers==5.16.1" \
    "accelerate==1.15.0"
```

Then run the model:

```python
from transformers import AutoModelForCausalLM, AutoTokenizer

model_id = "KRAFTON/Raon-1-nano"

tokenizer = AutoTokenizer.from_pretrained(
    model_id,
    trust_remote_code=True,
)
model = AutoModelForCausalLM.from_pretrained(
    model_id,
    dtype="auto",
    device_map="auto",
    trust_remote_code=True,
)

messages = [
    {"role": "user", "content": "Tell me about KRAFTON AI."}
]
inputs = tokenizer.apply_chat_template(
    messages,
    add_generation_prompt=True,
    tokenize=True,
    return_dict=True,
    return_tensors="pt",
    enable_thinking=True,
    truncate_history_thinking=False,
).to(model.device)

outputs = model.generate(
    **inputs,
    max_new_tokens=4096,
    do_sample=False,
)

output_ids = outputs[0, inputs["input_ids"].shape[-1]:]
print(tokenizer.decode(output_ids, skip_special_tokens=False).strip())
```

The example prints both thinking and the final answer, retaining the `</think>` separator.

The implementation uses plain PyTorch and requires no vLLM installation. RDA processes
prompt tokens sequentially, so long-prompt prefill can be slow.

</details>

<details>
<summary><strong>vLLM</strong></summary>

Use vLLM to serve Raon-1 nano through an OpenAI-compatible API.
The example below targets one NVIDIA B300 GPU with BF16 weights.

Install from the `raon-1-nano` branch of [KRAFTON's vLLM](https://github.com/krafton-ai/vllm/tree/raon-1-nano):

```bash
uv venv --python 3.12
source .venv/bin/activate

VLLM_USE_PRECOMPILED=1 uv pip install \
    "vllm @ git+https://github.com/krafton-ai/vllm.git@raon-1-nano"
```

Start the server:

```bash
vllm serve KRAFTON/Raon-1-nano \
    --host 127.0.0.1 \
    --port 8000 \
    --served-model-name Raon-1-nano \
    --trust-remote-code \
    --dtype bfloat16 \
    --tensor-parallel-size 1 \
    --pipeline-parallel-size 1 \
    --max-model-len 262144 \
    --gpu-memory-utilization 0.85 \
    --reasoning-parser raon_1 \
    --enable-auto-tool-choice \
    --tool-call-parser raon_1 \
    --default-chat-template-kwargs '{
      "enable_thinking": true,
      "truncate_history_thinking": false
    }'
```

Send a request from another terminal:

```bash
curl http://localhost:8000/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{
      "model": "Raon-1-nano",
      "messages": [
        {"role": "user", "content": "Tell me about KRAFTON AI."}
      ],
      "max_tokens": 4096,
      "temperature": 0.1,
      "presence_penalty": 1.5,
      "chat_template_kwargs": {
        "enable_thinking": true,
        "truncate_history_thinking": false
      }
    }'
```

`max_tokens` includes both thinking and the final answer.

</details>

<details>
<summary><strong>llama.cpp</strong></summary>

Run the GGUF model on Apple silicon (Metal), NVIDIA GPUs (CUDA),
AMD GPUs (Vulkan), or CPU.

Model: [KRAFTON/Raon-1-nano-gguf](https://huggingface.co/KRAFTON/Raon-1-nano-gguf)

Build the Raon-1 nano branch of [KRAFTON's llama.cpp](https://github.com/krafton-ai/llama.cpp/tree/raon-1-nano-preview):
On Windows, see the [build guide](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md).

```bash
git clone -b raon-1-nano-preview \
    https://github.com/krafton-ai/llama.cpp
cd llama.cpp

# Apple silicon: no flag needed (Metal is on by default).
# NVIDIA: add -DGGML_CUDA=ON. AMD: add -DGGML_VULKAN=ON.
cmake -B build
cmake --build build --config Release -j --target llama-cli llama-server
```

On Windows, run these in the Developer PowerShell for Visual Studio;
the binaries are in `build\bin\Release\` (for example
`.\build\bin\Release\llama-cli.exe`).

Start a terminal chat. The model downloads automatically.

```bash
./build/bin/llama-cli \
    -hf KRAFTON/Raon-1-nano-gguf:Q4_K_M \
    --ctx-size 65536 \
    --reasoning on \
    --chat-template-kwargs '{"enable_thinking": true}'
```

Or start an OpenAI-compatible server:

```bash
./build/bin/llama-server \
    -hf KRAFTON/Raon-1-nano-gguf:Q4_K_M \
    --host 127.0.0.1 \
    --port 8000 \
    --alias Raon-1-nano \
    --ctx-size 65536 \
    --parallel 1 \
    --reasoning on \
    --chat-template-kwargs '{"enable_thinking": true}'
```

Send a request from another terminal:

```bash
curl http://localhost:8000/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{
      "model": "Raon-1-nano",
      "messages": [
        {"role": "user", "content": "Tell me about KRAFTON AI."}
      ],
      "temperature": 0.1,
      "presence_penalty": 1.5
    }'
```

The thinking text is returned in `reasoning_content`,
the answer in `content`.

</details>

<details>
<summary><strong>MLX</strong></summary>

Run the 4-bit MLX model (GPTQ) on Apple silicon.

Model: [KRAFTON/Raon-1-nano-mlx](https://huggingface.co/KRAFTON/Raon-1-nano-mlx)

The model code ships in the MLX repository (`raon_mlx_model.py`),
so pass `trust_remote_code=True`. It includes our fused Metal kernels
for decode and prefill; they are enabled only with `mlx==0.32.4`
(other versions run a slower path).

Install the dependencies with Python 3.11+:

```bash
python3 -m venv .venv
source .venv/bin/activate

python -m pip install \
    "mlx==0.32.4" \
    "mlx-lm==0.32.2" \
    "transformers==5.20.2"
```

Run in Python:

```python
from mlx_lm import load, generate

model, tokenizer = load(
    "KRAFTON/Raon-1-nano-mlx",
    trust_remote_code=True,
)

messages = [
    {"role": "user", "content": "Tell me about KRAFTON AI."}
]
prompt = tokenizer.apply_chat_template(
    messages,
    tokenize=False,
    add_generation_prompt=True,
    enable_thinking=True,
)

response = generate(
    model,
    tokenizer,
    prompt=prompt,
    max_tokens=4096,
)
print(response)
```

Or start an OpenAI-compatible server:

```bash
python -m mlx_lm server \
    --model KRAFTON/Raon-1-nano-mlx \
    --trust-remote-code \
    --host 127.0.0.1 \
    --port 8000 \
    --chat-template-args '{"enable_thinking": true}'
```

Send a request from another terminal:

```bash
curl http://localhost:8000/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{
      "model": "KRAFTON/Raon-1-nano-mlx",
      "messages": [
        {"role": "user", "content": "Tell me about KRAFTON AI."}
      ]
    }'
```

The thinking text is returned in `reasoning`, the answer in `content`.

#### BF16 (no quantization)

The model above is 4-bit. To run without quantization, export a BF16 MLX model
from [KRAFTON/Raon-1-nano](https://huggingface.co/KRAFTON/Raon-1-nano)
with the model code in the MLX repository (about 6.3 GB; decode is slower than 4-bit):

```python
import json, shutil, sys
from pathlib import Path

import mlx.core as mx
from mlx.utils import tree_flatten
from huggingface_hub import snapshot_download

code = Path(snapshot_download("KRAFTON/Raon-1-nano-mlx", ignore_patterns=["model.safetensors"]))
original = Path(snapshot_download("KRAFTON/Raon-1-nano"))
out = Path("Raon-1-nano-mlx-bf16")

sys.path.insert(0, str(code))
import raon_mlx_model

model = raon_mlx_model.load_model(original, dtype=mx.bfloat16)
out.mkdir()
mx.save_safetensors(str(out / "model.safetensors"), dict(tree_flatten(model.parameters())))
for f in code.iterdir():
    if f.is_file() and f.name not in ("model.safetensors", "config.json", "README.md"):
        shutil.copy(f, out / f.name)
config = json.loads((code / "config.json").read_text())
config.pop("quantization")
(out / "config.json").write_text(json.dumps(config, indent=2))
```

Then use the commands above with the local folder in place of the repository id,
for example `load("Raon-1-nano-mlx-bf16", trust_remote_code=True)` or
`python -m mlx_lm server --model Raon-1-nano-mlx-bf16 --trust-remote-code ...`.

</details>

<details>
<summary><strong>LiteRT-LM</strong></summary>

Run the LiteRT-LM bundle (4-bit weights, fp16 activations) with the
official `litert-lm` package; no runtime changes are needed.
Checked on Apple silicon (GPU).

Model: [KRAFTON/Raon-1-nano-litert-lm](https://huggingface.co/KRAFTON/Raon-1-nano-litert-lm)

This bundle has a 4,096-token context and answers without thinking.

Install the dependencies with Python 3.12:

```bash
python3.12 -m venv .venv
source .venv/bin/activate

python -m pip install "litert-lm==0.18.1"
```

Import the model once from the LiteRT-LM repository, then chat:

```bash
litert-lm import \
    --from-huggingface-repo KRAFTON/Raon-1-nano-litert-lm \
    model.litertlm \
    raon-1-nano

litert-lm run raon-1-nano \
    --backend gpu \
    --max-num-tokens 4096 \
    --prompt "Tell me about KRAFTON AI."
```

The first run compiles the GPU program (about 30-60 seconds) and
caches it next to the model; later runs start quickly.

Or start an OpenAI-compatible server. Save this as `raon-litert.json`:

```json
{
  "models": {
    "raon-1-nano": {
      "backend": "gpu",
      "max_num_tokens": 4096
    }
  }
}
```

```bash
litert-lm serve \
    --config raon-litert.json \
    --host 127.0.0.1 \
    --port 8000
```

Send a request from another terminal:

```bash
curl http://localhost:8000/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{
      "model": "raon-1-nano",
      "messages": [
        {"role": "user", "content": "Tell me about KRAFTON AI."}
      ]
    }'
```

</details>

## License

### Hugging Face model card / README notice License - Apache 2.0

The Raon model, including its model weights, configuration files, and other model components made available for download through this repository, is licensed under the Apache License, Version 2.0.  Subject to that license, you may download, use, modify, and redistribute the model and its weights, including for commercial purposes.

Krafton and its licensors retain ownership of their respective intellectual property.  The Apache License grants the rights stated in that license and does not transfer ownership.  Rights in third-party materials remain subject to their applicable terms.

The Apache License does not extend to Krafton’s proprietary training datasets, training methodologies, undistributed software, or other proprietary materials not included in this repository.
