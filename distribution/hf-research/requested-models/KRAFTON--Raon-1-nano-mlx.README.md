---
library_name: mlx
license: apache-2.0
pipeline_tag: text-generation
base_model: KRAFTON/Raon-1-nano
base_model_relation: quantized
tags:
  - raon
  - mlx
---

# Raon-1 nano (MLX)

> [!NOTE]
> This repository contains the MLX weights of [Raon-1 nano](https://huggingface.co/KRAFTON/Raon-1-nano) for Apple silicon.
>
> The weights are quantized to 4 bits with GPTQ (group size 64). See the MLX section below for a BF16 export.

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

Raon-1 nano is a **3B dense language model** developed by [**KRAFTON AI**](https://krafton.ai/en/). It is designed to follow instructions, maintain conversational context, and call the right tools in both Korean and English. As part of the Raon model family, it is licensed under **Apache 2.0**.

> [!TIP]
> Want to explore Raon without setting anything up?<br>
> Visit the [Raon playground](https://raon-console.krafton.ai/playground/guest).

## Highlights

- **Compact dense model:** 3.2B parameters, including a 1.6B per-layer embedding table.
- **Hybrid attention:** 18 Raon Delta Attention layers and 6 gated MLA layers.
- **Raon-mHC and per-layer embeddings:** Four residual streams and a learned 256-dimensional
  per-layer input, with a shared input embedding and LM head.
- **Transformers support:** Load directly with `trust_remote_code=True` using the included
  PyTorch implementation.
- **Long context:** A 262,144-token context window.
- **Korean and English:** Supports Korean and English.
- **On-device deployment:** Available as GGUF for llama.cpp (Apple silicon, NVIDIA, AMD, CPU),
  a 4-bit MLX model for Apple silicon, and a LiteRT-LM bundle.

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
| Total Parameters | 3.2B (including the 1.6B per-layer embedding table) |
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
| Context Length | 262,144 tokens |

---

## Benchmark Results

<p align="center">
  <img src="https://huggingface.co/KRAFTON/Raon-1-nano/resolve/main/assets/raon-1-nano-benchmark.png" alt="Raon-1 nano benchmark results" width="100%">
</p>

---

## Sampling parameters

Recommended settings:

| Task | temperature | presence_penalty |
|---|---|---|
| General (chat, knowledge Q&A, instruction following, etc.) | 0.1 | 1.5 |
| Agentic / code | 1.0 | |

Thinking is enabled by default in the chat template. `generation_config.json` defines the EOS and
padding token IDs; it does not set sampling parameters. The output token limit (`max_new_tokens`,
`max_tokens`) includes both thinking and the final answer; increase it for longer responses.

## Quickstart

Raon-1 nano supports inference with Transformers, vLLM, llama.cpp, MLX, and LiteRT-LM.
The examples below show how to run the model locally or launch an OpenAI-compatible server
with thinking mode enabled (the LiteRT-LM bundle answers without thinking). The vLLM and llama.cpp examples use the recommended general
sampling settings above; the Transformers example uses greedy decoding for a simple first run.

The model repositories are not gated, so no access request is needed.

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
    --hf-overrides '{"max_position_embeddings": 262144}' \
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

To answer without thinking, use `--reasoning off` and
`'{"enable_thinking": false}'`.

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

#### MTP speculative decoding

The repository also ships the Raon-1 nano MTP block (`mtp/`, 8-bit) with a small Python module,
`raon_mtp_mlx.py`, for greedy self-speculative decoding: the MTP block drafts up to `drafts` tokens
and one forward of the 4-bit model checks them. `raon_mlx_model.py` is unchanged, and
`mlx_lm generate` / `mlx_lm server` do not use MTP.

```python
import sys
from huggingface_hub import snapshot_download
from transformers import AutoTokenizer

path = snapshot_download("KRAFTON/Raon-1-nano-mlx")
sys.path.insert(0, path)
import raon_mlx_model
import raon_mtp_mlx

model = raon_mlx_model.load(path)
mtp = raon_mtp_mlx.load_mtp(model, path)
tokenizer = AutoTokenizer.from_pretrained(path)
prompt = tokenizer.apply_chat_template(
    [{"role": "user", "content": "Tell me about KRAFTON AI."}],
    tokenize=False,
    add_generation_prompt=True,
    enable_thinking=True,
)
ids = tokenizer.encode(prompt, add_special_tokens=False)
out = raon_mtp_mlx.generate(model, mtp, ids, max_tokens=4096, drafts=2,
                            eos=(259202, 259200))  # generation_config.json eos_token_id
print(tokenizer.decode(out))
```

`drafts=0` decodes without MTP.

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

The Apache License does not extend to Krafton’s proprietary training datasets, training methodologies, undistributed software, hosted API, playground, or other proprietary materials not included in this repository.

### Hosted API and Playground Have Separate License Terms

Krafton also makes Raon available through its hosted API and playground service (“RAON Service”). The Raon Service is not licensed under Apache 2.0.  Access to and use of the Raon Service is governed by separate Hosted Access Terms and Conditions that are provided before access to the service is granted.  The Hosted Access Terms and Conditions govern, among other things, permitted service use, suspension, termination, and Krafton’s right to charge fees in the future.
