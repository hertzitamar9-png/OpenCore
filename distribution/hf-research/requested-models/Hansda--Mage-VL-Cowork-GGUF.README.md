---
license: apache-2.0
license_link: https://huggingface.co/Hansda/Mage-VL-Cowork-GGUF/blob/main/LICENSE
language:
- en
pipeline_tag: image-text-to-text
base_model: microsoft/Mage-VL-Cowork
base_model_relation: quantized
library_name: llama.cpp
tags:
- gguf
- multimodal
- vision-language-model
- computer-use
- mage-vl-cowork
---

# Mage-VL-Cowork-GGUF

GGUF quantizations of [Microsoft Mage-VL-Cowork](https://huggingface.co/microsoft/Mage-VL-Cowork), with **Q8_0 and F16 vision encoder/projector options**. Microsoft is the original model developer. This repository contains a third-party conversion under the original Apache 2.0 license.

**All requested variants are available.**

> **Runtime requirement:** Mage-VL-Cowork uses the custom `llava_onevision2` / Mage-ViT architecture. Image inference requires the experimental **Mage-VL-enabled llama.cpp fork** below. It is not the standard Qwen3-VL architecture. Stock llama.cpp, Ollama and LM Studio have not been validated for this projector. The underlying support [PR #26796](https://github.com/ggml-org/llama.cpp/pull/26796) is unmerged.

## Model overview

- Source: `microsoft/Mage-VL-Cowork`, revision `6fbe7f91da477c1c73790dbb1f08d001b8626be1`.
- Text backbone: Qwen3-4B-Instruct-2507; 36 layers, 32 query heads, 8 KV heads.
- Vision: original Mage-ViT encoder, patch size 16, spatial merge size 2, original normalization and positional encoding.
- Context: use the source model's active **32,768-token** window. The configuration advertises 262,144 positions; that longer window has not been validated here.
- Trained computer-use contract: `computer_use` and `bash`, with reasoning, `Action:` summaries and `<tool_call>` output. Use the supplied original system prompt.
- The source card's OSWorld and other benchmark scores describe the original checkpoint. Those benchmarks have not been rerun for these quantizations.

## Quantization

<!-- GGUF_FILES:START -->
| File | Download size | Status |
| :--- | ---: | :--- |
| [Mage-VL-Cowork-Q2_K.gguf](./Mage-VL-Cowork-Q2_K.gguf) | 1.80 GB | Available |
| [Mage-VL-Cowork-Q3_K_M.gguf](./Mage-VL-Cowork-Q3_K_M.gguf) | 2.24 GB | Available |
| [Mage-VL-Cowork-Q4_K_M.gguf](./Mage-VL-Cowork-Q4_K_M.gguf) | 2.72 GB | Available |
| [Mage-VL-Cowork-Q6_K.gguf](./Mage-VL-Cowork-Q6_K.gguf) | 3.63 GB | Available |
| [Mage-VL-Cowork-Q8_0.gguf](./Mage-VL-Cowork-Q8_0.gguf) | 4.69 GB | Available |
| [mmproj-Mage-VL-Cowork-Q8_0.gguf](./mmproj-Mage-VL-Cowork-Q8_0.gguf) | 0.35 GB | Shared vision encoder + projector |
| [mmproj-Mage-VL-Cowork-F16.gguf](./mmproj-Mage-VL-Cowork-F16.gguf) | 0.66 GB | Shared vision encoder + projector |
<!-- GGUF_FILES:END -->

Use exactly one decoder GGUF with exactly one vision file: `mmproj-Mage-VL-Cowork-Q8_0.gguf` or [`mmproj-Mage-VL-Cowork-F16.gguf`](./mmproj-Mage-VL-Cowork-F16.gguf). Each vision file contains the full vision encoder and merger/projector; do not load both together.

The F16 option is exported directly from the original Microsoft visual weights, not upcast from Q8_0. It has 99 F16 tensors and 199 F32 tensors (one-dimensional parameters and the rotation constant).

Q2_K and Q3_K_M trade more quality and tool-call reliability for size. Quantization labels describe mixed tensor formats; normalization and other sensitive tensors can remain floating point. Q8_0 is the supported standard 8-bit format; there is no Q8_K_M format in this quantizer.

The default GGUF chat template is the **unchanged original Mage-VL-Cowork multimodal template**. It is also provided as `chat_template.mage-original.jinja` and the named GGUF alias `mage_original`. The official Qwen3-4B-Instruct-2507 template is retained as the optional `qwen_instruct` alternative and `chat_template.qwen-instruct.jinja`. Use the original screenshot/action system prompt in `MAGE_HYBRID_SYSTEM_PROMPT.txt`. Model-card organization follows Qwen GGUF conventions while preserving Mage's trained prompt format.

## Quickstart

### Build the compatible runtime

```bash
git clone --branch locateanything-magevl-grounding https://github.com/ch686/llama.cpp-locateanything-magevl.git llama.cpp-mage
cd llama.cpp-mage
git checkout e07bdc8c8ef311561ab806e48792afb5d30b8054
cmake -B build -DCMAKE_BUILD_TYPE=Release -DLLAMA_BUILD_UI=OFF
cmake --build build --target llama-mtmd-cli llama-server -j
```

For NVIDIA acceleration, add `-DGGML_CUDA=ON` to the configure command with a compatible CUDA toolkit installed.

### Download a decoder and the shared vision projector

```bash
hf download Hansda/Mage-VL-Cowork-GGUF \
  --include Mage-VL-Cowork-Q4_K_M.gguf mmproj-Mage-VL-Cowork-Q8_0.gguf MAGE_HYBRID_SYSTEM_PROMPT.txt \
  --local-dir models/Mage-VL-Cowork
```

For F16 vision, download `mmproj-Mage-VL-Cowork-F16.gguf` instead of the Q8_0 vision file and change the `--mmproj` path below. The decoder and original chat/system prompts stay the same.

### Image inference

```bash
./build/bin/llama-mtmd-cli \
  -m models/Mage-VL-Cowork/Mage-VL-Cowork-Q4_K_M.gguf \
  --mmproj models/Mage-VL-Cowork/mmproj-Mage-VL-Cowork-Q8_0.gguf \
  --image screenshot.png \
  --jinja -c 32768 -n 4096 --temp 0.7 --top-p 0.8 \
  -sys "$(cat models/Mage-VL-Cowork/MAGE_HYBRID_SYSTEM_PROMPT.txt)" \
  -p "Click the Start button."
```

The output is an action proposal. An application or harness must execute the tools and return the resulting screenshot or terminal output. Follow the original model card's tool definitions, coordinate convention and working-memory rules.

## Compatibility

Conversion and inference checks are performed locally. The F16 vision option was checked against the original FP32 vision implementation on matched square, landscape and portrait inputs, and paired with the existing Q4_K_M decoder for single-image inference using the pinned experimental runtime. These are numerical/load checks, not task-accuracy or benchmark results. File hashes and byte sizes are in `model-manifest.json`. Task-accuracy results are not published in this repository.

The original model's benchmark scores have not been reproduced for these quantizations. Multi-frame video attention and codec-native streaming are **not validated** in this experimental runtime. Use the original Transformers/SGLang checkpoint for those workflows.

The original `config.json`, image preprocessing and generation settings are included with an `original-` prefix for provenance. They are not a Transformers loader configuration for these GGUF files. The source model's `trust_remote_code=True` requirement applies to its original Python implementation; these GGUF files use llama.cpp instead.

## Reproduce the conversion

`convert_mage.py` exports the original Qwen3 text tensors and the Mage-ViT vision tensors without changing the downloaded checkpoint. Run it with `--component text` or `--component vision`, `--source PATH`, `--llama-dir PATH`, and `--output FILE`. For text, pass `--chat-template chat_template.jinja --qwen-chat-template chat_template.qwen-instruct.jinja` to preserve this release's original default and optional Qwen template, then quantize that F16 decoder with `llama-quantize`. Vision is exported directly to Q8_0 with `gguf-py` (`--vision-outtype q8_0`, the default), retaining the rotation constant and normalization tensors in F32 and the patch convolution in F16. For the direct F16 vision export, use `--component vision --vision-outtype f16`; multi-dimensional learned tensors are F16, while one-dimensional parameters and the rotation constant remain F32. The standard `llama-quantize` tool does not accept the `clip` projector architecture.

Exact runtime, source and template revisions are recorded in `runtime-info.json`. Model-card organization follows the [official Qwen GGUF card](https://huggingface.co/Qwen/Qwen3-4B-GGUF); model-specific behavior follows [Microsoft's original card](https://huggingface.co/microsoft/Mage-VL-Cowork).
