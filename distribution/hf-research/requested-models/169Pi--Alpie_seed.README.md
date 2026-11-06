---
license: apache-2.0
base_model: Qwen/Qwen3.5-9B
library_name: transformers
pipeline_tag: image-text-to-text
language:
- en
- hi
- bn
- mr
- gu
- te
- ml
- pa
- kn
- or
- ta
tags:
- alpie
- 169pi
- qwen3.5
- vision-language
- multimodal
- reasoning
- coding
- video-understanding
- multilingual
- indic
- long-context
- gguf
- 9b
- apache-2.0
---

<div align="center">

<h1>🌱 Alpie Seed 9B</h1>

<p><b>A 9B open-weight vision-language model for reasoning, coding, and multimodal understanding.</b></p>

<p>
  <a href="https://huggingface.co/169Pi/Alpie_Seed_GGUF"><img src="https://img.shields.io/badge/📦_GGUF-2%2F3%2F4--bit-8B5CF6?style=flat&labelColor=1E1B4B" alt="GGUF"></a>
  <a href="https://github.com/169Pi"><img src="https://img.shields.io/badge/💻_GitHub-169Pi-2563EB?style=flat&labelColor=0F172A" alt="GitHub"></a>
</p>

<p>
  <a href="https://x.com/169Pi_ai"><img src="https://img.shields.io/badge/𝕏_X-@169Pi__ai-0F172A?style=flat&labelColor=000000" alt="X"></a>
  <a href="https://www.linkedin.com/company/169pi"><img src="https://img.shields.io/badge/💼_LinkedIn-169Pi-0284C7?style=flat&labelColor=082F49" alt="LinkedIn"></a>
  <a href="https://www.youtube.com/@169pi"><img src="https://img.shields.io/badge/▶️_YouTube-@169pi-EF4444?style=flat&labelColor=450A0A" alt="YouTube"></a>
  <a href="https://www.instagram.com/169pi.ai/"><img src="https://img.shields.io/badge/📸_Instagram-@169pi.ai-D946EF?style=flat&labelColor=4A044E" alt="Instagram"></a>
</p>

A short demo of Alpie Seed running fully offline on a mobile device, tested on [PocketPal](https://github.com/a-ghorbani/pocketpal-ai). It shows the quantized model generating responses on-device with no cloud connection.

<video controls playsinline preload="metadata" width="354" height="199" style="width:354px; height:199px; max-width:100%; object-fit:contain; background:#000;" src="https://huggingface.co/169Pi/Alpie_seed/resolve/main/alpie_offline_mobile_demo_fast.mp4"></video>

[▶️ Watch the demo video](https://huggingface.co/169Pi/Alpie_seed/resolve/main/alpie_offline_mobile_demo_fast.mp4)

<div style="max-width: 720px; margin: 8px auto; padding: 7px 9px; background-color: #FAF5FF; border: 1px solid #5825B3; border-radius: 8px; box-shadow: 0 4px 12px rgba(88, 37, 179, 0.15); text-align: center;">

<p style="font-size: 11px; margin: 2px 0;"><b>📄 Technical Report</b></p>

<p style="font-size: 9px; line-height: 1.3; margin: 2px 0;">The <b>Alpie Seed Technical Report</b> documents the model in detail: lineage and architecture, the SFT and GRPO post-training pipeline, reward design, quantization and inference profiling, offline results on real devices (desktop GPU, Apple Silicon, mobile, and CPU-only), benchmark results across reasoning, coding, multimodal, and Indic-language tasks, safety evaluation, and limitations.</p>

<a href="https://huggingface.co/169Pi/Alpie_seed/resolve/main/Alpie%20Seed%20Final%20Technical%20Report.pdf"><img height="16" src="https://img.shields.io/badge/📄_Read_the_Technical_Report-PDF-5825B3?style=flat&labelColor=1E1B4B" alt="Read the Technical Report (PDF)"></a>

</div>

<div style="max-width: 720px; margin: 16px auto; padding: 14px 18px; background-color: #FAF5FF; border: 2px solid #5825B3; border-radius: 14px; box-shadow: 0 4px 12px rgba(88, 37, 179, 0.15); text-align: center;">

<p><b>Get Alpie Seed</b></p>

<a href="https://ollama.com/169pi/alpie-seed"><img src="https://img.shields.io/badge/🦙_Run_on_Ollama-169pi%2Falpie--seed-EC4899?style=for-the-badge&labelColor=500724" alt="Run on Ollama"></a>
<a href="https://www.kaggle.com/models/alpie/alpie-seed-9b"><img src="https://img.shields.io/badge/Kaggle-alpie--seed--9b-20BEFF?style=for-the-badge&logo=kaggle&logoColor=white&labelColor=0F172A" alt="Alpie Seed 9B on Kaggle"></a>

</div>

</div>

---

**Alpie Seed 9B** is a multimodal model developed by **169Pi AI**, built on the Qwen3.5-9B architecture and post-trained to strengthen reasoning, instruction following, coding, and visual understanding. It brings together text, image, and video understanding with a 262K context window and multiple quantized formats for deployment across different hardware.

## Alpie Seed Highlights

- **Unified vision-language model**: text, image, and video input on the Qwen3.5-9B architecture.
- **Reasoning and coding**: post-trained with supervised fine-tuning and GRPO; supports thinking and non-thinking modes.
- **262K native context** for long documents and extended conversations.
- **Flexible deployment**: FP16 weights plus 2-bit, 3-bit, and 4-bit GGUF builds.
- **Open license**: Apache 2.0.

## Available On

| Platform | Link |
|---|---|
| Hugging Face (GGUF) | [169Pi/Alpie_Seed_GGUF](https://huggingface.co/169Pi/Alpie_Seed_GGUF) |
| Ollama | [ollama.com/169pi/alpie-seed](https://ollama.com/169pi/alpie-seed) |
| Kaggle | [kaggle.com/models/alpie/alpie-seed-9b](https://www.kaggle.com/models/alpie/alpie-seed-9b) |

### Ollama

Run Alpie Seed directly from the Ollama library:

```bash
ollama run 169pi/alpie-seed
```

Available tags:

| Name | Size | Context | Input |
|---|---:|---:|---|
| `alpie-seed:latest` | 5.6 GB | 256K | Text |
| `alpie-seed:q2` | 3.8 GB | 256K | Text |
| `alpie-seed:q3` | 4.6 GB | 256K | Text |
| `alpie-seed:vision` | 6.6 GB | 256K | Text, Image |

To run a specific tag:

```bash
ollama run 169pi/alpie-seed:q2
ollama run 169pi/alpie-seed:vision
```

## Model Overview

- Type: Causal Language Model with Vision Encoder
- Training Stage: Post-training (SFT + GRPO) on Qwen3.5-9B
- Language Model
  - Number of Parameters: 9B
  - Hidden Dimension: 4096
  - Token Embedding: 248320 (Padded)
  - Number of Layers: 32
  - Hidden Layout: 8 × (3 × (Gated DeltaNet → FFN) → 1 × (Gated Attention → FFN))
  - Gated DeltaNet:
    - Number of Linear Attention Heads: 32 for V and 16 for QK
    - Head Dimension: 128
  - Gated Attention:
    - Number of Attention Heads: 16 for Q and 4 for KV
    - Head Dimension: 256
    - Rotary Position Embedding Dimension: 64
  - Feed Forward Network:
    - Intermediate Dimension: 12288
  - LM Output: 248320 (Padded)
  - MTP: trained with multi-steps
- Vision Encoder: 27-layer Vision Transformer
- Context Length: 262,144 tokens

The architecture is retained from Qwen3.5-9B; Alpie Seed's adaptation is carried out through post-training rather than a structural redesign.

## Training

Alpie Seed uses a two-stage post-training pipeline.

1. **Supervised fine-tuning (SFT):** a DORA adapter is trained to improve instruction following, response quality, and task performance.
2. **Group Relative Policy Optimization (GRPO):** a trainable DoRA adapter is optimized while the SFT adapter is kept as a frozen reference policy. The GRPO stage uses a 4-bit NF4 base with double quantization and BF16 computation, and the trained adapter is merged into the base model at its original precision for release.


## Benchmark Results

> **Evaluation Settings Note:** All benchmarks were evaluated with extended thinking enabled (`enable_thinking: true`), a maximum generation length of `max_tokens: 65536`, and `temperature: 0.0`.
>
> Alpie Seed scores are from the FP16 model and do not apply to the 2-bit, 3-bit, or 4-bit variants. **Bold** indicates the best score in each row. Comparison-model scores are taken from published model cards and reports (including the [Qwen3.5-9B model card](https://huggingface.co/Qwen/Qwen3.5-9B)).
### Language

<div style="background-color: #FAF5FF; border: 1.5px solid #C4B5FD; border-radius: 14px; padding: 12px; margin-bottom: 24px; box-shadow: 0 4px 12px rgba(88, 37, 179, 0.05); overflow-x: auto;">
  <table style="width: 100%; border-collapse: separate; border-spacing: 0; text-align: center; font-size: 13.5px; font-family: inherit;">
    <thead>
      <tr style="color: #1E1B4B; font-weight: 700; background-color: #EDE9FE;">
        <th style="text-align: left; padding: 10px 14px; border-top-left-radius: 8px; border-bottom: 1.5px solid #C4B5FD;">Benchmark</th>
        <th style="padding: 10px 8px; border-bottom: 1.5px solid #C4B5FD;">Qwen3.5<br><span style="font-size: 12px; font-weight: normal; color: #4338CA;">9B</span></th>
        <th style="padding: 10px 8px; border-bottom: 1.5px solid #C4B5FD;">Gemma 4<br><span style="font-size: 12px; font-weight: normal; color: #4338CA;">12B</span></th>
        <th style="padding: 10px 8px; border-bottom: 1.5px solid #C4B5FD;">Ministral 3<br><span style="font-size: 12px; font-weight: normal; color: #4338CA;">8B</span></th>
        <th style="padding: 10px 8px; border-bottom: 1.5px solid #C4B5FD;">GPT-OSS<br><span style="font-size: 12px; font-weight: normal; color: #4338CA;">20B</span></th>
        <th style="padding: 10px 8px; border-bottom: 1.5px solid #C4B5FD;">Sarvam<br><span style="font-size: 12px; font-weight: normal; color: #4338CA;">30B</span></th>
        <th style="padding: 10px 8px; border-bottom: 1.5px solid #C4B5FD;">Nemotron 3.5<br><span style="font-size: 12px; font-weight: normal; color: #4338CA;">Lightning</span></th>
        <th style="padding: 10px 10px; background-color: #5825B3; color: #FFFFFF; border-top-right-radius: 8px; font-weight: 800; border-bottom: 1.5px solid #431A9E;">Alpie Seed<br><span style="font-size: 12px; color: #DDD6FE;">9B</span></th>
      </tr>
    </thead>
    <tbody>
      <!-- Section: Knowledge & STEM -->
      <tr style="background-color: #E0E7FF; color: #3730A3; font-weight: 800; text-align: left;">
        <td colspan="8" style="padding: 8px 14px; font-size: 13px; letter-spacing: 0.3px;"> Knowledge &amp; STEM</td>
      </tr>
      <tr style="border-bottom: 1px solid #DDD6FE; background-color: #FFFFFF;">
        <td style="text-align: left; padding: 9px 14px; font-weight: 600; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">MMLU</td>
        <td style="padding: 9px 8px; color: #64748B; border-bottom: 1px solid #EDE9FE;">--</td>
        <td style="padding: 9px 8px; color: #64748B; border-bottom: 1px solid #EDE9FE;">--</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">76.1</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">85.3</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">85.1</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">78.63</td>
        <td style="padding: 9px 8px; background-color: #F3E8FF; color: #581C87; font-weight: 800; border-bottom: 1px solid #DDD6FE; border-left: 1.5px solid #C4B5FD; border-right: 1.5px solid #C4B5FD;">87.30</td>
      </tr>
      <tr style="border-bottom: 1px solid #DDD6FE; background-color: #FAFAFE;">
        <td style="text-align: left; padding: 9px 14px; font-weight: 600; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">MMLU-Pro</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">82.5</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">77.2</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">75.00</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">77.2</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">80.0</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">81.94</td>
        <td style="padding: 9px 8px; background-color: #F3E8FF; color: #581C87; font-weight: 800; border-bottom: 1px solid #DDD6FE; border-left: 1.5px solid #C4B5FD; border-right: 1.5px solid #C4B5FD;">82.54</td>
      </tr>
      <tr style="border-bottom: 1px solid #DDD6FE; background-color: #FFFFFF;">
        <td style="text-align: left; padding: 9px 14px; font-weight: 600; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">GPQA Diamond</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">81.7</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">78.8</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">38.6</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">71.5</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">66.5</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">75.44</td>
        <td style="padding: 9px 8px; background-color: #F3E8FF; color: #581C87; font-weight: 800; border-bottom: 1px solid #DDD6FE; border-left: 1.5px solid #C4B5FD; border-right: 1.5px solid #C4B5FD;">84.32</td>
      </tr>
      <!-- Section: Mathematics -->
      <tr style="background-color: #E0E7FF; color: #3730A3; font-weight: 800; text-align: left;">
        <td colspan="8" style="padding: 8px 14px; font-size: 13px; letter-spacing: 0.3px;"> Mathematics</td>
      </tr>
      <tr style="border-bottom: 1px solid #DDD6FE; background-color: #FFFFFF;">
        <td style="text-align: left; padding: 9px 14px; font-weight: 600; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">AIME 2026</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">69.4</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">77.5</td>
        <td style="padding: 9px 8px; color: #64748B; border-bottom: 1px solid #EDE9FE;">--</td>
        <td style="padding: 9px 8px; color: #64748B; border-bottom: 1px solid #EDE9FE;">--</td>
        <td style="padding: 9px 8px; color: #64748B; border-bottom: 1px solid #EDE9FE;">--</td>
        <td style="padding: 9px 8px; color: #64748B; border-bottom: 1px solid #EDE9FE;">--</td>
        <td style="padding: 9px 8px; background-color: #F3E8FF; color: #581C87; font-weight: 800; border-bottom: 1px solid #DDD6FE; border-left: 1.5px solid #C4B5FD; border-right: 1.5px solid #C4B5FD;">83.33</td>
      </tr>
      <!-- Section: Coding & Software Engineering -->
      <tr style="background-color: #E0E7FF; color: #3730A3; font-weight: 800; text-align: left;">
        <td colspan="8" style="padding: 8px 14px; font-size: 13px; letter-spacing: 0.3px;"> Coding &amp; Software Engineering</td>
      </tr>
      <tr style="border-bottom: 1px solid #DDD6FE; background-color: #FFFFFF;">
        <td style="text-align: left; padding: 9px 14px; font-weight: 600; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">LiveCodeBench v6</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">65.6</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">72.0</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">61.6</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">61.0</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">70.0</td>
        <td style="padding: 9px 8px; color: #64748B; border-bottom: 1px solid #EDE9FE;">--</td>
        <td style="padding: 9px 8px; background-color: #F3E8FF; color: #581C87; font-weight: 800; border-bottom: 1px solid #DDD6FE; border-left: 1.5px solid #C4B5FD; border-right: 1.5px solid #C4B5FD;">72.99</td>
      </tr>
      <tr style="background-color: #FAFAFE;">
        <td style="text-align: left; padding: 9px 14px; font-weight: 600; color: #1E1B4B; border-bottom-left-radius: 8px;">SWE-Bench Verified</td>
        <td style="padding: 9px 8px; color: #1E1B4B;">60.0</td>
        <td style="padding: 9px 8px; color: #1E1B4B;">52.0</td>
        <td style="padding: 9px 8px; color: #64748B;">--</td>
        <td style="padding: 9px 8px; color: #1E1B4B;">60.7</td>
        <td style="padding: 9px 8px; color: #1E1B4B;">34.0</td>
        <td style="padding: 9px 8px; color: #1E1B4B;">51.56</td>
        <td style="padding: 9px 8px; background-color: #F3E8FF; color: #581C87; font-weight: 800; border-bottom-right-radius: 8px; border-left: 1.5px solid #C4B5FD; border-right: 1.5px solid #C4B5FD;">61.20</td>
      </tr>
    </tbody>
  </table>
</div>

### Vision Language

<div style="background-color: #FAF5FF; border: 1.5px solid #C4B5FD; border-radius: 14px; padding: 12px; margin-bottom: 24px; box-shadow: 0 4px 12px rgba(88, 37, 179, 0.05); overflow-x: auto;">
  <table style="width: 100%; border-collapse: separate; border-spacing: 0; text-align: center; font-size: 13.5px; font-family: inherit;">
    <thead>
      <tr style="color: #1E1B4B; font-weight: 700; background-color: #EDE9FE;">
        <th style="text-align: left; padding: 10px 14px; border-top-left-radius: 8px; border-bottom: 1.5px solid #C4B5FD;">Benchmark</th>
        <th style="padding: 10px 8px; border-bottom: 1.5px solid #C4B5FD;">GPT-5-Nano<br><span style="font-size: 12px; font-weight: normal; color: #4338CA;">2025-08-07</span></th>
        <th style="padding: 10px 8px; border-bottom: 1.5px solid #C4B5FD;">Gemini-2.5<br><span style="font-size: 12px; font-weight: normal; color: #4338CA;">Flash-Lite</span></th>
        <th style="padding: 10px 8px; border-bottom: 1.5px solid #C4B5FD;">Qwen3-VL<br><span style="font-size: 12px; font-weight: normal; color: #4338CA;">30B-A3B</span></th>
        <th style="padding: 10px 8px; border-bottom: 1.5px solid #C4B5FD;">Qwen3.5<br><span style="font-size: 12px; font-weight: normal; color: #4338CA;">9B</span></th>
        <th style="padding: 10px 8px; border-bottom: 1.5px solid #C4B5FD;">Gemma 4<br><span style="font-size: 12px; font-weight: normal; color: #4338CA;">12B</span></th>
        <th style="padding: 10px 10px; background-color: #5825B3; color: #FFFFFF; border-top-right-radius: 8px; font-weight: 800; border-bottom: 1.5px solid #431A9E;">Alpie Seed<br><span style="font-size: 12px; color: #DDD6FE;">9B</span></th>
      </tr>
    </thead>
    <tbody>
      <!-- Section: STEM and Puzzle -->
      <tr style="background-color: #E0E7FF; color: #3730A3; font-weight: 800; text-align: left;">
        <td colspan="7" style="padding: 8px 14px; font-size: 13px; letter-spacing: 0.3px;"> STEM and Puzzle</td>
      </tr>
      <tr style="border-bottom: 1px solid #DDD6FE; background-color: #FFFFFF;">
        <td style="text-align: left; padding: 9px 14px; font-weight: 600; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">MMMU-Pro</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">57.2</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">59.7</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">63.0</td>
        <td style="padding: 9px 8px; color: #1E1B4B; font-weight: 800; border-bottom: 1px solid #EDE9FE;">70.1</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">69.1</td>
        <td style="padding: 9px 8px; background-color: #F3E8FF; color: #581C87; font-weight: 800; border-bottom: 1px solid #DDD6FE; border-left: 1.5px solid #C4B5FD; border-right: 1.5px solid #C4B5FD;">68.70</td>
      </tr>
      <tr style="border-bottom: 1px solid #DDD6FE; background-color: #FAFAFE;">
        <td style="text-align: left; padding: 9px 14px; font-weight: 600; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">MathVision</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">62.2</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">52.1</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">65.7</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">78.9</td>
        <td style="padding: 9px 8px; color: #1E1B4B; font-weight: 800; border-bottom: 1px solid #EDE9FE;">79.7</td>
        <td style="padding: 9px 8px; background-color: #F3E8FF; color: #581C87; font-weight: 800; border-bottom: 1px solid #DDD6FE; border-left: 1.5px solid #C4B5FD; border-right: 1.5px solid #C4B5FD;">76.76</td>
      </tr>
      <!-- Section: General VQA -->
      <tr style="background-color: #E0E7FF; color: #3730A3; font-weight: 800; text-align: left;">
        <td colspan="7" style="padding: 8px 14px; font-size: 13px; letter-spacing: 0.3px;"> General VQA</td>
      </tr>
      <tr style="border-bottom: 1px solid #DDD6FE; background-color: #FFFFFF;">
        <td style="text-align: left; padding: 9px 14px; font-weight: 600; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">RealWorldQA</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">71.8</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">72.2</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">77.4</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">80.3</td>
        <td style="padding: 9px 8px; color: #64748B; border-bottom: 1px solid #EDE9FE;">--</td>
        <td style="padding: 9px 8px; background-color: #F3E8FF; color: #581C87; font-weight: 800; border-bottom: 1px solid #DDD6FE; border-left: 1.5px solid #C4B5FD; border-right: 1.5px solid #C4B5FD;">80.39</td>
      </tr>
      <tr style="border-bottom: 1px solid #DDD6FE; background-color: #FAFAFE;">
        <td style="text-align: left; padding: 9px 14px; font-weight: 600; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">MMStar</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">68.6</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">69.1</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">75.5</td>
        <td style="padding: 9px 8px; color: #1E1B4B; font-weight: 800; border-bottom: 1px solid #EDE9FE;">79.7</td>
        <td style="padding: 9px 8px; color: #64748B; border-bottom: 1px solid #EDE9FE;">--</td>
        <td style="padding: 9px 8px; background-color: #F3E8FF; color: #581C87; font-weight: 800; border-bottom: 1px solid #DDD6FE; border-left: 1.5px solid #C4B5FD; border-right: 1.5px solid #C4B5FD;">78.87</td>
      </tr>
      <tr style="border-bottom: 1px solid #DDD6FE; background-color: #FFFFFF;">
        <td style="text-align: left; padding: 9px 14px; font-weight: 600; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">HallusionBench</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">58.4</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">64.5</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">66.0</td>
        <td style="padding: 9px 8px; color: #1E1B4B; border-bottom: 1px solid #EDE9FE;">69.3</td>
        <td style="padding: 9px 8px; color: #64748B; border-bottom: 1px solid #EDE9FE;">--</td>
        <td style="padding: 9px 8px; background-color: #F3E8FF; color: #581C87; font-weight: 800; border-bottom: 1px solid #DDD6FE; border-left: 1.5px solid #C4B5FD; border-right: 1.5px solid #C4B5FD;">79.88</td>
      </tr>
      <!-- Section: Text Recognition and Document Understanding -->
      <tr style="background-color: #E0E7FF; color: #3730A3; font-weight: 800; text-align: left;">
        <td colspan="7" style="padding: 8px 14px; font-size: 13px; letter-spacing: 0.3px;"> Text Recognition &amp; Document Understanding</td>
      </tr>
      <tr style="background-color: #FAFAFE;">
        <td style="text-align: left; padding: 9px 14px; font-weight: 600; color: #1E1B4B; border-bottom-left-radius: 8px;">OCRBench</td>
        <td style="padding: 9px 8px; color: #1E1B4B;">75.3</td>
        <td style="padding: 9px 8px; color: #1E1B4B;">82.5</td>
        <td style="padding: 9px 8px; color: #1E1B4B;">83.9</td>
        <td style="padding: 9px 8px; color: #1E1B4B;">89.2</td>
        <td style="padding: 9px 8px; color: #64748B;">--</td>
        <td style="padding: 9px 8px; background-color: #F3E8FF; color: #581C87; font-weight: 800; border-bottom-right-radius: 8px; border-left: 1.5px solid #C4B5FD; border-right: 1.5px solid #C4B5FD;">90.30</td>
      </tr>
    </tbody>
  </table>
</div>

     
    
- SWE-Bench Verified: resolved rate using Mini-SWE-agent, with retries disabled.
- Comparison-model scores were not re-run by 169Pi, and harnesses, prompts, and benchmark versions differ, so cross-model comparisons are indicative rather than strictly like-for-like. Scores vary with dataset versions, prompts, evaluation harnesses, and inference settings.
- Empty cells (--) indicate scores not yet available or not applicable.
- Sources: [Ministral 3 8B model card](https://www.modelscope.cn/models/mistralai/Ministral-3-8B-Instruct-2512-BF16), [NVIDIA Nemotron 3.5 Lightning evaluation](https://docs.nvidia.com/nemotron/nightly/nemotron/lightning35/evaluate.html), [Qwen3.5-9B model card](https://huggingface.co/Qwen/Qwen3.5-9B), [Gemma 4 model card](https://huggingface.co/google/gemma-4-12B), [GPT-OSS evaluation](https://deploymentsafety.openai.com/gpt-oss), and [Sarvam-30B model card](https://huggingface.co/sarvamai/sarvam-30b).

## Quickstart

### Install dependencies

Alpie Seed support requires a recent Transformers release (Transformers 5 or later).

```bash
pip install -U "transformers>=5.2.0" torch torchvision pillow accelerate
```

### Serve with vLLM (OpenAI-compatible API)

Start the standard Alpie Seed 9B server:

```bash
vllm serve 169Pi/Alpie_seed \
  --port 8000 \
  --max-model-len 262144 \
  --reasoning-parser qwen3
```

To enable automatic tool-call parsing, use this command instead:

```bash
vllm serve 169Pi/Alpie_seed \
  --port 8000 \
  --max-model-len 262144 \
  --reasoning-parser qwen3 \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder
```

The API is available at `http://localhost:8000/v1`. The tool-calling options enable vLLM to parse model-generated tool calls; your application is still responsible for executing the requested tools and returning their results.

### Run with Transformers

The following examples load the full vision-language checkpoint using `AutoModelForImageTextToText` and `AutoProcessor`. They retain the model's multimodal architecture. The `enable_thinking` argument is passed to the chat template to select the response mode.

#### 1. Thinking mode (`enable_thinking=True`)

```python
import torch
from transformers import AutoModelForImageTextToText, AutoProcessor

MODEL_ID = "169Pi/Alpie_seed"

processor = AutoProcessor.from_pretrained(MODEL_ID)
model = AutoModelForImageTextToText.from_pretrained(
    MODEL_ID,
    torch_dtype="auto",
    device_map="auto",
)
model.eval()

messages = [
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "Explain why the sky appears blue."}
        ],
    }
]

inputs = processor.apply_chat_template(
    messages,
    tokenize=True,
    add_generation_prompt=True,
    enable_thinking=True,
    return_dict=True,
    return_tensors="pt",
).to(model.device)

with torch.inference_mode():
    output_ids = model.generate(
        **inputs,
        max_new_tokens=4096,
        do_sample=True,
        temperature=0.7,
        top_p=0.8,
    )

new_tokens = output_ids[0, inputs["input_ids"].shape[1]:]
response = processor.decode(new_tokens, skip_special_tokens=True)
print(response)
```

#### 2. Non-thinking mode (`enable_thinking=False`)

```python
import torch
from transformers import AutoModelForImageTextToText, AutoProcessor

MODEL_ID = "169Pi/Alpie_seed"

processor = AutoProcessor.from_pretrained(MODEL_ID)
model = AutoModelForImageTextToText.from_pretrained(
    MODEL_ID,
    torch_dtype="auto",
    device_map="auto",
)
model.eval()

messages = [
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "Explain why the sky appears blue in two sentences."}
        ],
    }
]

inputs = processor.apply_chat_template(
    messages,
    tokenize=True,
    add_generation_prompt=True,
    enable_thinking=False,
    return_dict=True,
    return_tensors="pt",
).to(model.device)

with torch.inference_mode():
    output_ids = model.generate(
        **inputs,
        max_new_tokens=512,
        do_sample=True,
        temperature=0.7,
        top_p=0.8,
    )

new_tokens = output_ids[0, inputs["input_ids"].shape[1]:]
response = processor.decode(new_tokens, skip_special_tokens=True)
print(response)
```

#### 3. Tool calling with Transformers

Transformers can format the available tool definitions into the prompt. The model generates a tool-call request; your application must parse the request, execute the corresponding function, append the tool result, and ask the model to continue. This example demonstrates generating and inspecting a tool call; it does not execute external tools automatically.

```python
import json
import torch
from transformers import AutoModelForImageTextToText, AutoProcessor

MODEL_ID = "169Pi/Alpie_seed"

processor = AutoProcessor.from_pretrained(MODEL_ID)
model = AutoModelForImageTextToText.from_pretrained(
    MODEL_ID,
    torch_dtype="auto",
    device_map="auto",
)
model.eval()

tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "The city to check.",
                    }
                },
                "required": ["city"],
            },
        },
    }
]

messages = [
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "What is the weather like in Patna?"}
        ],
    }
]

inputs = processor.apply_chat_template(
    messages,
    tools=tools,
    tokenize=True,
    add_generation_prompt=True,
    enable_thinking=True,
    return_dict=True,
    return_tensors="pt",
).to(model.device)

with torch.inference_mode():
    output_ids = model.generate(
        **inputs,
        max_new_tokens=1024,
        do_sample=True,
        temperature=0.7,
        top_p=0.8,
    )

new_tokens = output_ids[0, inputs["input_ids"].shape[1]:]
generated_text = processor.decode(new_tokens, skip_special_tokens=False)
print("Model output:")
print(generated_text)

# Tool execution is application-side. Inspect the generated tool-call
# structure, validate its arguments, and dispatch only to tools you trust.
```

For image or video inputs, provide the corresponding image/video content items in the user message and use the same `AutoProcessor` + `AutoModelForImageTextToText` loading pattern. See the upstream Qwen3.5 documentation for multimodal message examples and framework-specific details.

## Citation

If you use Alpie Seed 9B in your work, please cite:

```bibtex
@misc{alpie_seed_9b_2026,
  title        = {Alpie Seed 9B},
  author       = {{169Pi AI}},
  year         = {2026},
  howpublished = {\url{https://huggingface.co/169Pi/Alpie_seed}}
}
```