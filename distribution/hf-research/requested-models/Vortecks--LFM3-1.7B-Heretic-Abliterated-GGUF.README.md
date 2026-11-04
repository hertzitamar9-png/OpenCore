---
license: other
license_name: lfm1.0
license_link: https://huggingface.co/LiquidAI/LFM3-1.7B/blob/main/LICENSE
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
library_name: gguf
pipeline_tag: text-generation
tags:
- liquid
- lfm3
- edge
- conversational
- heretic
- abliterated
- uncensored
- gguf
- llama.cpp
base_model:
- Vortecks/LFM3-1.7B-Heretic-Abliterated
---

<div align="center">
<h1>LFM3-1.7B</h1>
</div>

<p align="center">
<a href="https://www.liquid.ai" target="_blank">Liquid AI</a> |
<a href="https://huggingface.co/LiquidAI/LFM3-1.7B" target="_blank">Base Model</a>
</p>

## Introduction

**LFM3-1.7B** is a text model for local agents and specialized applications from **Liquid AI**, built on their new tri-hybrid architecture. It targets everyday assistant use, tool calling, structured outputs, long-context work, and edge deployment, with a 256K-token context window.

This build is a **heretic abliterated** variant, meaning the refusal directions in the model's activation space have been identified and ablated to remove built-in refusal behavior. As a result, this version is marketed as **uncensored** and will generally comply with a wider range of prompts than the base instruction-tuned release, without the usual safety-alignment guardrails.

All quantizations were produced with llama.cpp. No importance matrix was used — every file in this repository is a plain (static) quantization.

## Download a file (not the whole branch) from below:

| Filename | Quant type | File Size | Split | Description |
| -------- | ---------- | --------- | ----- | ----------- |
[LFM3-1.7B-Heretic-Abliterated-F32.gguf](https://huggingface.co/Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF/blob/main/LFM3-1.7B-Heretic-Abliterated-F32.gguf) | f32 | 6.92GB | false | Full 32-bit precision weights. Use only as source or for maximum quality.
[LFM3-1.7B-Heretic-Abliterated-F16.gguf](https://huggingface.co/Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF/blob/main/LFM3-1.7B-Heretic-Abliterated-F16.gguf) | f16 | 3.47GB | false | Full 16-bit weights. Use as the source GGUF.
[LFM3-1.7B-Heretic-Abliterated-BF16.gguf](https://huggingface.co/Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF/blob/main/LFM3-1.7B-Heretic-Abliterated-BF16.gguf) | bf16 | 3.47GB | false | Full bfloat16 weights.
[LFM3-1.7B-Heretic-Abliterated-Q8_0.gguf](https://huggingface.co/Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF/blob/main/LFM3-1.7B-Heretic-Abliterated-Q8_0.gguf) | Q8_0 | 1.85GB | false | Extremely high quality, generally unneeded but max available quant.
[LFM3-1.7B-Heretic-Abliterated-Q6_K.gguf](https://huggingface.co/Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF/blob/main/LFM3-1.7B-Heretic-Abliterated-Q6_K.gguf) | Q6_K | 1.43GB | false | Very high quality, near perfect, *recommended*.
[LFM3-1.7B-Heretic-Abliterated-Q5_1.gguf](https://huggingface.co/Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF/blob/main/LFM3-1.7B-Heretic-Abliterated-Q5_1.gguf) | Q5_1 | 1.32GB | false | Legacy format, slightly higher quality than Q5_0.
[LFM3-1.7B-Heretic-Abliterated-Q5_K_M.gguf](https://huggingface.co/Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF/blob/main/LFM3-1.7B-Heretic-Abliterated-Q5_K_M.gguf) | Q5_K_M | 1.26GB | false | High quality, *recommended*.
[LFM3-1.7B-Heretic-Abliterated-Q5_K_S.gguf](https://huggingface.co/Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF/blob/main/LFM3-1.7B-Heretic-Abliterated-Q5_K_S.gguf) | Q5_K_S | 1.23GB | false | High quality, *recommended*.
[LFM3-1.7B-Heretic-Abliterated-Q5_0.gguf](https://huggingface.co/Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF/blob/main/LFM3-1.7B-Heretic-Abliterated-Q5_0.gguf) | Q5_0 | 1.23GB | false | Legacy format, high quality.
[LFM3-1.7B-Heretic-Abliterated-Q4_1.gguf](https://huggingface.co/Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF/blob/main/LFM3-1.7B-Heretic-Abliterated-Q4_1.gguf) | Q4_1 | 1.14GB | false | Legacy format, similar performance to Q4_K_S but with improved tokens/watt on Apple silicon.
[LFM3-1.7B-Heretic-Abliterated-Q4_K_M.gguf](https://huggingface.co/Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF/blob/main/LFM3-1.7B-Heretic-Abliterated-Q4_K_M.gguf) | Q4_K_M | 1.10GB | false | Good quality, default size for most use cases, *recommended*.
[LFM3-1.7B-Heretic-Abliterated-IQ4_NL.gguf](https://huggingface.co/Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF/blob/main/LFM3-1.7B-Heretic-Abliterated-IQ4_NL.gguf) | IQ4_NL | 1.06GB | false | Similar to IQ4_XS, but slightly larger. Offers online repacking for ARM CPU inference.
[LFM3-1.7B-Heretic-Abliterated-Q4_K_S.gguf](https://huggingface.co/Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF/blob/main/LFM3-1.7B-Heretic-Abliterated-Q4_K_S.gguf) | Q4_K_S | 1.06GB | false | Slightly lower quality with more space savings, *recommended*.
[LFM3-1.7B-Heretic-Abliterated-Q4_0.gguf](https://huggingface.co/Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF/blob/main/LFM3-1.7B-Heretic-Abliterated-Q4_0.gguf) | Q4_0 | 1.05GB | false | Legacy format, offers online repacking for ARM and AVX CPU inference.
[LFM3-1.7B-Heretic-Abliterated-IQ4_XS.gguf](https://huggingface.co/Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF/blob/main/LFM3-1.7B-Heretic-Abliterated-IQ4_XS.gguf) | IQ4_XS | 1.01GB | false | Decent quality, smaller than Q4_K_S with similar performance, *recommended*.
[LFM3-1.7B-Heretic-Abliterated-Q3_K_L.gguf](https://huggingface.co/Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF/blob/main/LFM3-1.7B-Heretic-Abliterated-Q3_K_L.gguf) | Q3_K_L | 0.97GB | false | Lower quality but usable, good for low RAM availability.
[LFM3-1.7B-Heretic-Abliterated-Q3_K_M.gguf](https://huggingface.co/Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF/blob/main/LFM3-1.7B-Heretic-Abliterated-Q3_K_M.gguf) | Q3_K_M | 0.91GB | false | Low quality.
[LFM3-1.7B-Heretic-Abliterated-IQ3_M.gguf](https://huggingface.co/Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF/blob/main/LFM3-1.7B-Heretic-Abliterated-IQ3_M.gguf) | IQ3_M | 0.87GB | false | Medium-low quality, new method with decent performance comparable to Q3_K_M.
[LFM3-1.7B-Heretic-Abliterated-IQ3_S.gguf](https://huggingface.co/Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF/blob/main/LFM3-1.7B-Heretic-Abliterated-IQ3_S.gguf) | IQ3_S | 0.85GB | false | Lower quality, new method with decent performance, slightly better than Q3_K_S.
[LFM3-1.7B-Heretic-Abliterated-Q3_K_S.gguf](https://huggingface.co/Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF/blob/main/LFM3-1.7B-Heretic-Abliterated-Q3_K_S.gguf) | Q3_K_S | 0.85GB | false | Low quality, not recommended.
[LFM3-1.7B-Heretic-Abliterated-Q2_K.gguf](https://huggingface.co/Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF/blob/main/LFM3-1.7B-Heretic-Abliterated-Q2_K.gguf) | Q2_K | 0.75GB | false | Very low quality but surprisingly usable.
[LFM3-1.7B-Heretic-Abliterated-Q2_0.gguf](https://huggingface.co/Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF/blob/main/LFM3-1.7B-Heretic-Abliterated-Q2_0.gguf) | Q2_0 | 0.64GB | false | Legacy 2-bit format, very low quality.
[LFM3-1.7B-Heretic-Abliterated-TQ2_0.gguf](https://huggingface.co/Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF/blob/main/LFM3-1.7B-Heretic-Abliterated-TQ2_0.gguf) | TQ2_0 | 0.60GB | false | Ternary 2-bit quantization, experimental.
[LFM3-1.7B-Heretic-Abliterated-TQ1_0.gguf](https://huggingface.co/Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF/blob/main/LFM3-1.7B-Heretic-Abliterated-TQ1_0.gguf) | TQ1_0 | 0.53GB | false | Ternary 1-bit quantization, experimental.
[LFM3-1.7B-Heretic-Abliterated-Q1_0.gguf](https://huggingface.co/Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF/blob/main/LFM3-1.7B-Heretic-Abliterated-Q1_0.gguf) | Q1_0 | 0.43GB | false | Extremely low quality, 1.125 bpw, experimental.

## Which file should I choose?
- For most use cases, use **Q4_K_M** (good quality / size balance).
- For maximum quality, use **Q6_K** or **Q8_0** (only if you have the RAM).
- For low-memory devices, work down from **Q4_K_S** → **Q3_K_M** → **Q2_K**.
- The **IQ3_S** / **IQ3_M** files here are static quants (these two types don't require an importance matrix) for squeezing onto limited hardware.
- The low-bit IQ quants (IQ1_\* to IQ3_XXS) were generated with an importance matrix and live in the companion imatrix repo: [Vortecks/LFM3-1.7B-Heretic-Abliterated-i1-GGUF](https://huggingface.co/Vortecks/LFM3-1.7B-Heretic-Abliterated-i1-GGUF).

## Model Information

- **Type**: Causal Language Model
- **Architecture**: `Lfm3ForCausalLM` (tri-hybrid: 24 layers — 13 gated short convolutions, 5 sliding-window attention, 6 global attention)
- **Number of Parameters**: ~1.73B
- **Context Length**: 262,144

## Performance

| Metric | This model | Original model ([LiquidAI/LFM3-1.7B](https://huggingface.co/LiquidAI/LFM3-1.7B)) |
| :----- | :--------: | :---------------------------: |
| **KL divergence** | 0.0094 | 0 |
| **Refusals** | 3/100 | 92/100 |

<sup>Metrics from the heretic optimization run: refusals measured with heretic's refusal-keyword test on 100 adversarial prompts; KL divergence relative to the base model.</sup>

## Quickstart

GGUF quantizations of LFM3-1.7B-Heretic-Abliterated, for use with `llama.cpp`, `Ollama`, `LM Studio`, and other GGUF-compatible runtimes. The example commands below use the `Q8_0` quantization — swap in whichever quant level you've downloaded from the [Files and versions](https://huggingface.co/Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF/tree/main) tab.

> **Note:** llama.cpp needs a build with LFM3 support (recent llama.cpp master or an LFM3-enabled build).

### llama.cpp

```bash
# Build or install llama.cpp: https://github.com/ggml-org/llama.cpp
huggingface-cli download Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF LFM3-1.7B-Heretic-Abliterated-Q8_0.gguf --local-dir .
```

**CLI (interactive chat):**

```bash
llama-cli -m LFM3-1.7B-Heretic-Abliterated-Q8_0.gguf \
  -c 131072 \
  -cnv \
  --temp 0.1 \
  --top-k 50 \
  --repeat-penalty 1.1
```

**Server (OpenAI-compatible API):**

```bash
llama-server -m LFM3-1.7B-Heretic-Abliterated-Q8_0.gguf \
  -c 131072 \
  --port 8080
```

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "Who are you?"}],
    "max_tokens": 128,
    "temperature": 0.1
  }'
```

### Ollama

```bash
ollama run hf.co/Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF:Q8_0
```

### LM Studio

Search for `Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF` directly in the LM Studio model search, or download a `.gguf` file from the [Files and versions](https://huggingface.co/Vortecks/LFM3-1.7B-Heretic-Abliterated-GGUF/tree/main) tab and load it manually via **Load Model from File**.

Recommended sampling parameters:

| Mode | Recommended params |
| --- | --- |
| **General** (chat, tool use, agents, coding) | `temperature=0.1, top_k=50, repetition_penalty=1.1` |
| **Math & STEM** | `temperature=1.0, top_p=0.95, top_k=20` |

## Limitations and Responsible Use

LFM3-1.7B is a language model that generates content based on learned statistical patterns from training data. It may produce inaccurate, biased, or unsafe outputs, and generated content should be reviewed and verified before use in high-stakes settings. Users are responsible for evaluating outputs, applying appropriate safeguards, and complying with applicable laws, regulations, and platform policies. As an abliterated, uncensored build, this model has had its built-in refusal behavior removed and will generally comply with a wider range of prompts than its base counterpart, so downstream safety, moderation, and policy compliance must be implemented by the deployer rather than relying on the model itself.

## License

This repository is a derivative of [LiquidAI/LFM3-1.7B](https://huggingface.co/LiquidAI/LFM3-1.7B) and is distributed under the **LFM Open License v1.0**. See the [base model's LICENSE](https://huggingface.co/LiquidAI/LFM3-1.7B/blob/main/LICENSE) for the full terms.