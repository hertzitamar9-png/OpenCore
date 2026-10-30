---
license: other
license_name: lfm1.0
license_link: LICENSE
base_model: LiquidAI/LFM3-1.7B-Base
base_model_relation: quantized
pipeline_tag: text-generation
inference: false
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
tags:
- gguf
- velociq8
- q8a64
- cpu
- 8-bit
- quantized
- arm64
- aarch64
- x86_64
- liquid
- lfm3
- text-generation
- base-model
---

<!-- velociq8:common:header -->
<p align="center">
  <a href="https://huggingface.co/VelociQ8"><img src="https://huggingface.co/spaces/VelociQ8/README/resolve/main/logo.png" alt="VelociQ8" width="96"></a>
</p>

<p align="center">
  <b>Q8A64 model file for VelociQ8</b><br>
  Fast CPU inference for GGUF models on Arm and x86<br>
  Embeddings, dense and MoE LLMs<br>
  <a href="https://github.com/dhaern/VelociQ8">Runtime</a> ·
  <a href="https://github.com/dhaern/VelociQ8/releases">Releases</a> ·
  <a href="https://github.com/dhaern/VelociQ8/blob/main/docs/usage.md">Usage</a> ·
  <a href="https://huggingface.co/VelociQ8">All models</a>
</p>
<!-- /velociq8:common:header -->

# LFM3-1.7B-Base · Q8A64

This repository holds [LFM3-1.7B-Base](https://huggingface.co/LiquidAI/LFM3-1.7B-Base) converted to Q8A64, the 8-bit weight format of VelociQ8, to run on the CPU. It is the pre-trained model behind LFM3-1.7B: it continues the text you give it and has no chat tuning. Liquid AI recommends it only as a starting point for heavy fine-tuning; for chat, tool calling and agents, use [LFM3-1.7B](https://huggingface.co/VelociQ8/LFM3-1.7B-Q8A64-GGUF).

<table>
<tbody>
<tr><th align="left" scope="row">Base model</th><td><a href="https://huggingface.co/LiquidAI/LFM3-1.7B-Base">LiquidAI/LFM3-1.7B-Base</a>, by Liquid AI</td></tr>
<tr><th align="left" scope="row">Type</th><td>Pre-trained base model that continues text; not a chat model</td></tr>
<tr><th align="left" scope="row">Size</th><td>1.73B parameters</td></tr>
<tr><th align="left" scope="row">Context</th><td>256K tokens in the original model</td></tr>
<tr><th align="left" scope="row">Weights license</th><td><a href="https://huggingface.co/LiquidAI/LFM3-1.7B-Base/blob/main/LICENSE">LFM Open License v1.0</a>, commercial use only below 10 million US dollars of annual revenue</td></tr>
<tr><th align="left" scope="row">Measurements</th><td>Speed and quality measured, see <a href="#speed-and-quality">Speed and quality</a></td></tr>
</tbody>
</table>

**Commercial use is limited by revenue.** The original model is released under the LFM Open License v1.0, which licenses commercial use only below 10 million US dollars of annual revenue (section 5), and this converted file has the same limit. The VelociQ8 runtime is free for commercial use, but that does not change the license of these weights.

The original repository has no GGUF file. This file comes from its safetensors, converted to a BF16 GGUF with llama.cpp b11963 and then to Q8A64.

**LFM3-1.7B-Base needs VelociQ8 1.5 or later.** That is the first version that runs this model; the version in the next paragraph is the general minimum for Q8A64 files.

<!-- velociq8:common:runtime -->
**This file loads only in VelociQ8 1.1 or later.** Other GGUF tools, including standard llama.cpp, cannot load Q8A64 files. The automatic snippets that Hugging Face shows under **Use this model** (for llama.cpp, Ollama and similar tools) do not work with these files.

## Get the runtime

VelociQ8 is free-to-use proprietary software built on llama.cpp. It ships the usual tools (`llama-server`, `llama-cli`, `llama-embedding`, `llama-bench` and `llama-quantize`) with the same options and the same HTTP API, including the OpenAI-compatible endpoints. Download the archive for your machine from the table (GitHub release) or from the [`runtime`](https://huggingface.co/VelociQ8/LFM3-1.7B-Base-Q8A64-GGUF/tree/main/runtime) folder of this repository, which has the same files. Check it against `SHA256SUMS` from the same place.

| Archive | For |
|---|---|
| [`velociq8-1.5-linux-arm64.tar.gz`](https://github.com/dhaern/VelociQ8/releases/download/v1.5/velociq8-1.5-linux-arm64.tar.gz) | Arm servers with dotprod: Ampere Altra, AWS Graviton2 and later, Neoverse N1 and later |
| [`velociq8-1.5-linux-arm64-multi.tar.gz`](https://github.com/dhaern/VelociQ8/releases/download/v1.5/velociq8-1.5-linux-arm64-multi.tar.gz) | Any 64-bit Arm CPU; it picks the best kernels at startup |
| [`velociq8-1.5-linux-x64.tar.gz`](https://github.com/dhaern/VelociQ8/releases/download/v1.5/velociq8-1.5-linux-x64.tar.gz) | x86-64 with AVX2 and FMA (Intel Haswell, AMD Zen and later) |
| [`velociq8-1.5-windows-x64.zip`](https://github.com/dhaern/VelociQ8/releases/download/v1.5/velociq8-1.5-windows-x64.zip) | Windows on the same x86-64 CPUs (tested on Windows 11) |
| [`velociq8-1.5-ollama-0.40.13-linux-arm64.tar.gz`](https://github.com/dhaern/VelociQ8/releases/download/v1.5/velociq8-1.5-ollama-0.40.13-linux-arm64.tar.gz) | Portable Ollama 0.40.13 for the CPU, Linux on Arm |
| [`velociq8-1.5-ollama-0.40.13-linux-x64.tar.gz`](https://github.com/dhaern/VelociQ8/releases/download/v1.5/velociq8-1.5-ollama-0.40.13-linux-x64.tar.gz) | Portable Ollama 0.40.13, Linux on x86-64: CPU, or the GPU through Vulkan |
| [`velociq8-1.5-ollama-0.40.13-windows-x64.zip`](https://github.com/dhaern/VelociQ8/releases/download/v1.5/velociq8-1.5-ollama-0.40.13-windows-x64.zip) | Portable Ollama 0.40.13, Windows on x86-64: CPU, or the GPU through Vulkan |

The Ollama archives are a portable Ollama with VelociQ8 inside, and do not touch an installed Ollama. The x86-64 ones also use an NVIDIA, AMD or Intel GPU through Vulkan when its driver is installed; see [Ollama](https://github.com/dhaern/VelociQ8/blob/main/docs/usage.md#ollama). The Linux archives need glibc 2.28 or newer. There is also a Docker image for Linux on Arm and x86-64, [`ghcr.io/dhaern/velociq8`](https://github.com/dhaern/VelociQ8/pkgs/container/velociq8) (see [Docker](https://github.com/dhaern/VelociQ8/blob/main/docs/usage.md#docker)). The other archives and the Docker image run on the CPU. VelociQ8 tools cannot download models: download the file from this repository first and pass it with `-m`.

### NVIDIA GPUs (CUDA)

For NVIDIA GPUs there are also portable Ollama archives with CUDA. They are larger (about 1 GB), so they are only on GitHub: [`velociq8-1.5-ollama-0.40.13-windows-x64-cuda.zip`](https://github.com/dhaern/VelociQ8/releases/download/v1.5/velociq8-1.5-ollama-0.40.13-windows-x64-cuda.zip) and [`velociq8-1.5-ollama-0.40.13-linux-x64-cuda.tar.gz`](https://github.com/dhaern/VelociQ8/releases/download/v1.5/velociq8-1.5-ollama-0.40.13-linux-x64-cuda.tar.gz). They need an NVIDIA GPU of the Turing generation or newer (GeForce RTX 20 series onwards) with a current driver.

Questions about this file go to the [Community](https://huggingface.co/VelociQ8/LFM3-1.7B-Base-Q8A64-GGUF/discussions) tab; problems with the runtime, to [GitHub issues](https://github.com/dhaern/VelociQ8/issues).
<!-- /velociq8:common:runtime -->

## Files

| File | Contents | Size |
|---|---|---|
| [`LFM3-1.7B-Base-Q8A64.gguf`](https://huggingface.co/VelociQ8/LFM3-1.7B-Base-Q8A64-GGUF/resolve/main/LFM3-1.7B-Base-Q8A64.gguf) | Q8A64 weights | 1709.20 MiB |
| [`LICENSE`](https://huggingface.co/VelociQ8/LFM3-1.7B-Base-Q8A64-GGUF/blob/main/LICENSE) | License text | |

SHA256 of the weights file, in the format `sha256sum -c` reads:

```text
af8930ca697a664bc69eafbb8861d3546d9be9012102c56467fbdd0432be086a  LFM3-1.7B-Base-Q8A64.gguf
```

## Quick start

Unpack the archive that matches your CPU (Linux Arm64 shown) and download the file:

```sh
tar xf velociq8-1.5-linux-arm64.tar.gz && cd velociq8-1.5-linux-arm64
REPO=VelociQ8/LFM3-1.7B-Base-Q8A64-GGUF
FILE=LFM3-1.7B-Base-Q8A64.gguf
curl -L -O https://huggingface.co/$REPO/resolve/main/$FILE
```

You can also download it from the **Files** tab or with `hf download VelociQ8/LFM3-1.7B-Base-Q8A64-GGUF LFM3-1.7B-Base-Q8A64.gguf --local-dir .`. On Windows use `llama-server.exe` from the zip; the options are the same.

LFM3-1.7B-Base is a base model: it continues the text you give it and does not follow chat turns. Start a server and ask for a completion through the OpenAI-compatible completions endpoint. Use `-t` for the number of physical cores you want to give the model and `-c` for the context you need (8192 tokens here). Without `-c`, `llama-server` sets up the full context of the model (see the *Context* row of the table above), which needs more memory than most uses do:

```sh
./llama-server -m LFM3-1.7B-Base-Q8A64.gguf -t 4 -c 8192 --port 8080
```

```sh
curl -s http://127.0.0.1:8080/v1/completions \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "The nematode C. elegans is", "max_tokens": 128, "temperature": 0}'
```

The continuation is in `choices[0].text`. `llama-server` also has a native `/completion` endpoint.

### Ollama

The portable Ollama 0.40.13 archives from the table above run this file too (Linux shown; the [usage guide](https://github.com/dhaern/VelociQ8/blob/main/docs/usage.md#ollama) covers Windows, the GPU and what to do if another Ollama is already running). Start the server from the unpacked folder with `./bin/ollama serve` and, in a second terminal, import the file with a Modelfile:

```sh
cat > Modelfile <<'EOF'
FROM ./LFM3-1.7B-Base-Q8A64.gguf
EOF
./bin/ollama create lfm3-1.7b-base-q8a64 -f Modelfile
```

Ask for a completion through `/api/generate` with `"raw": true`, so Ollama sends the text as it is and does not wrap it in a chat template:

```sh
curl -s http://127.0.0.1:11434/api/generate -d '{"model": "lfm3-1.7b-base-q8a64", "prompt": "The nematode C. elegans is", "raw": true, "stream": false, "options": {"num_predict": 128, "temperature": 0}}'
```

## Prompt

There is no chat format to follow: send plain text and the model continues it. The original repository includes a chat template, but its card presents the model as a starting point for fine-tuning, not as a chat model.

## Speed and quality

### Speed

Tokens per second, higher is better. VelociQ8 1.5 against llama.cpp b11963 on the Arm machine (Oracle Cloud A1, 4 threads), `llama-bench` with 3 repetitions per run and flash attention on, two interleaved rounds; median of the six repetitions. VelociQ8 loaded this Q8A64 file and llama.cpp a Q8_0 file made from the same BF16 GGUF with llama.cpp b11963, since Liquid AI publishes no GGUF of this model.

| Test | VelociQ8 | llama.cpp, Q8_0 |
|---|---|---|
| Prompt processing, 512 tokens | 168.3 | 103.1 |
| Text generation, 128 tokens | 18.5 | 18.5 |

Prompt processing is 1.63 times faster. Text generation is level with llama.cpp with Q8_0.

### Quality against full precision

KL divergence against the original model, lower is better. "Same top token" is how often the most likely next token matches the original model's. The reference is the original BF16 model converted to F32, which loses nothing, run by llama.cpp b11963. `llama-perplexity --kl-divergence` on wikitext-2, 8 chunks of 512 tokens, 4 threads, on the Arm machine. VelociQ8 1.5 ran this file; llama.cpp b11963 ran a Q8_0 file made from the same BF16 GGUF with llama.cpp b11963, since Liquid AI publishes no GGUF of this model.

| Build | KL divergence | Same top token |
|---|---|---|
| VelociQ8 | 0.000471 | 99.07 % |
| llama.cpp, Q8_0 | 0.000546 | 98.92 % |

VelociQ8 has 14 % less divergence than Q8_0. The standard errors are 0.000013 and 0.000014; the same-top-token rates differ by 0.15 points, under their standard error of 0.21 to 0.23 points.

Full tables, other builds and the method: [benchmarks.md](https://github.com/dhaern/VelociQ8/blob/main/docs/benchmarks.md) and [quality.md](https://github.com/dhaern/VelociQ8/blob/main/docs/quality.md).

## About the base model

**Source:** the original card of [LiquidAI/LFM3-1.7B-Base](https://huggingface.co/LiquidAI/LFM3-1.7B-Base), by Liquid AI ([release blog post](https://www.liquid.ai/blog/lfm3)). What follows is copied or condensed from it and is Liquid AI's statement, not VelociQ8's; it leaves out Liquid AI's memory claims. Read on 2026-10-29. If anything here differs from the original, the original is right. The original card has no benchmark tables.

### Description

LFM3-1.7B-Base is the pre-trained checkpoint behind [LFM3-1.7B](https://huggingface.co/LiquidAI/LFM3-1.7B), a text model for local agents and specialized applications based on Liquid AI's tri-hybrid architecture. It was pre-trained on 22T tokens; the last 3.5T tokens are curated mid-training data with agentic traces and tool calls, and they extend the context window to 256K tokens. It is meant as a starting point for continued pre-training and your own post-training.

### Architecture and size

| | LFM3-1.7B-Base |
|---|---|
| Checkpoint | Pre-trained, text-only base model |
| Parameters | 1.73B |
| Architecture | 24 layers: 13 gated short convolutions, 5 sliding-window attention, 6 global attention |
| Context length | 256K tokens (input + output) |
| Vocabulary | 128,000 tokens |

It uses the same architecture and modeling code as LFM3-1.7B.

### Languages

English, Arabic, Chinese, French, German, Italian, Japanese, Korean, Portuguese, Spanish, Vietnamese, Thai, Indonesian, Hindi, Russian and Polish.

### Intended use

The original card recommends it only for tasks that need heavy fine-tuning, such as language-specific or domain-specific assistants, training on proprietary data, or experimenting with new post-training approaches. For chat, tool calling and agents it points to LFM3-1.7B.

### Limitations and risks

The original card does not say beyond the recommendation above. It has no section on limitations or risks.

### Prompt format

The example of the original card is plain text with no template: the prompt `The nematode C. elegans is`, generated with `max_new_tokens=128` and `do_sample=False`. It gives no sampling settings.

Mid-training included chat data, with and without thinking traces, formatted with the same chat template as LFM3-1.7B. With `apply_chat_template`, reasoning is on by default, so a reply starts with a thinking trace; `enable_thinking=False` prefills an empty one. The original card still presents the model as a base for fine-tuning, not as a chat model.

### License

The original card gives the license as the [LFM Open License v1.0](https://huggingface.co/LiquidAI/LFM3-1.7B-Base/blob/main/LICENSE) (`license_name: lfm1.0`). Its text is in the `LICENSE` file of this repository, copied unchanged from the original repository.

**Commercial use is limited by revenue.** Section 5 of the license licenses commercial use only below 10 million US dollars of annual revenue, and section 1 defines a legal entity as the acting entity together with the entities that control it, are controlled by it or are under common control with it. Section 5(c) lifts the limit for a qualified non-profit organization that uses the model for non-commercial or research purposes. The same limit applies to this converted file.

### Citation

```bibtex
@article{liquidAI2026LFM3,
  author  = {Liquid AI},
  title   = {Introducing LFM3: Every Modality for Specialized Intelligence},
  journal = {Liquid AI Blog},
  year    = {2026},
  note    = {www.liquid.ai/blog/lfm3},
}
```

<!-- velociq8:common:license -->
## Licenses

Two licenses apply, and they do not mix:

- **Runtime: VelociQ8 Software License Agreement.** It covers the VelociQ8 software only, that is, the archives in the [releases](https://github.com/dhaern/VelociQ8/releases). The runtime is free to use for personal or commercial work.
- **Weights: the license of the original model.** The weights in this repository are a converted copy of the model described in *About the base model*. They stay under the original license, stated below. The VelociQ8 license agreement does not cover them and does not limit the rights that license gives you.

VelociQ8 is not affiliated with the developers of the original model, and this conversion is not endorsed by them.
<!-- /velociq8:common:license -->

### License of this model

This repository contains a converted copy of [LiquidAI/LFM3-1.7B-Base](https://huggingface.co/LiquidAI/LFM3-1.7B-Base). The weights were converted to Q8A64 and stored in a GGUF file. The model was not retrained or fine-tuned.

License: [LFM Open License v1.0](https://huggingface.co/LiquidAI/LFM3-1.7B-Base/blob/main/LICENSE). The full text is in the `LICENSE` file of this repository, copied unchanged from the repository of the original model.

**Commercial use is limited by revenue.** Section 5 of the license licenses commercial use only below 10 million US dollars of annual revenue, and section 1 defines a legal entity as the acting entity together with the entities that control it, are controlled by it or are under common control with it. Section 5(c) lifts the limit for a qualified non-profit organization that uses the model for non-commercial or research purposes. Read the license for the exact terms. The same limit applies to this file.

**Redistribution.** Section 4 asks anyone who passes the model on to give the recipient a copy of the license and to mark modified files. This repository has the license in `LICENSE`, and its weights are a modified version of the original files, converted to Q8A64.

<!-- velociq8:common:footer -->
<sub>Arm is a registered trademark of Arm Limited (or its subsidiaries) in the US and/or elsewhere. Other product and hardware names belong to their respective owners and are used only to say what VelociQ8 is compared against or measured on.</sub>
<!-- /velociq8:common:footer -->
