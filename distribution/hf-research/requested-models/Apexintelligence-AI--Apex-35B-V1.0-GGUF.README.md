---
license: apache-2.0
license_link: https://huggingface.co/Apexintelligence-AI/Apex-35B-V1.0/blob/main/LICENSE
base_model: Apexintelligence-AI/Apex-35B-V1.0
base_model_relation: quantized
pipeline_tag: text-generation
tags:
- gguf
- llama.cpp
- unsloth
- mixture-of-experts
---

<div align="center">
  <img src="https://huggingface.co/Apexintelligence-AI/Apex-35B-V1.0/resolve/main/assets/apex-intelligence-logo.png" width="200" height="200" alt="Apex Intelligence">
</div>

# Apex-35B-V1.0-GGUF

This repository contains GGUF builds of [Apex-35B-V1.0](https://huggingface.co/Apexintelligence-AI/Apex-35B-V1.0) for [llama.cpp](https://github.com/ggml-org/llama.cpp), [Unsloth](https://unsloth.ai), and other GGUF runtimes. For the model introduction, evaluation results, and serving with SGLang, vLLM, TokenSpeed, KTransformers, or Transformers, see the [Apex-35B-V1.0 model card](https://huggingface.co/Apexintelligence-AI/Apex-35B-V1.0).

## Model Overview

- Developer: Apex Intelligence; built on [Qwen3.6-35B-A3B](https://huggingface.co/Qwen/Qwen3.6-35B-A3B)
- Number of parameters: 35B in total and 3B activated
- Architecture: sparse mixture-of-experts, 40 layers, 3 Gated DeltaNet + 1 Gated Attention per 4 layers, 256 routed experts + 1 shared expert, 8 routed experts activated per token
- Context length: 262,144 tokens, shared by input and output
- Thinking: dynamic; the model decides on each turn whether to think and for how long
- MTP: 1 MTP layer, included in every quantization (for BF16, in the second of the two split files)
- Chat template: embedded in every quantization (for BF16, in the first split file); the generation prompt ends with `<think>`

## Files

| Quantization | File | Size | Notes |
| :--- | :--- | ---: | :--- |
| BF16 | `Apex-35B-V1.0-BF16-00001-of-00002.gguf`<br>`Apex-35B-V1.0-BF16-00002-of-00002.gguf` | 71.1 GB | 2 split files; pass the first file to llama.cpp |
| Q8_0 | `Apex-35B-V1.0-Q8_0.gguf` | 37.8 GB |  |
| Q6_K | `Apex-35B-V1.0-Q6_K.gguf` | 29.2 GB |  |
| Q5_K_M | `Apex-35B-V1.0-Q5_K_M.gguf` | 25.3 GB |  |
| Q4_K_M | `Apex-35B-V1.0-Q4_K_M.gguf` | 21.7 GB |  |

## Quickstart

### llama.cpp

Build [llama.cpp](https://github.com/ggml-org/llama.cpp) from a recent `master` (NVIDIA GPU shown; see its [build guide](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md) for other platforms):

```bash
git clone https://github.com/ggml-org/llama.cpp
cmake llama.cpp -B llama.cpp/build -DGGML_CUDA=ON
cmake --build llama.cpp/build --config Release -j --target llama-server llama-cli
```

Download one file, for example Q4_K_M:

```bash
hf download Apexintelligence-AI/Apex-35B-V1.0-GGUF Apex-35B-V1.0-Q4_K_M.gguf --local-dir .
```

Start an OpenAI-compatible server at `http://localhost:8000/v1`:

```bash
./llama.cpp/build/bin/llama-server -m Apex-35B-V1.0-Q4_K_M.gguf --jinja -ngl 99 -c 32768 --port 8000 --temp 1.0 --top-p 0.95 --top-k 20 --min-p 0.0
```

- `--jinja` is required: it applies the chat template embedded in the file.
- The server returns the thinking in `reasoning_content` and converts tool calls into standard `tool_calls`.
- llama-server keeps the thinking of earlier turns by default. Add `--no-reasoning-preserve` to keep only the thinking after the latest user message.
- `-c` sets the context for the prompt and the output together (up to 262,144). With `-c 32768`, a long thinking can be cut off before the 65,536-token budget of the main card's examples; raise `-c` if you have the memory.

### Unsloth

Install [Unsloth](https://unsloth.ai) and serve a downloaded file:

```bash
curl -fsSL https://unsloth.ai/install.sh | UNSLOTH_SKIP_AUTOSTART=1 sh
source ~/.profile   # or open a new shell, so that `unsloth` is on PATH
unsloth run --model Apex-35B-V1.0-Q4_K_M.gguf --parallel 1 --temperature 1.0 --top-p 0.95 --top-k 20 --min-p 0.0 --host 127.0.0.1 --port 8001
```

- `UNSLOTH_SKIP_AUTOSTART=1` stops the installer from starting Unsloth Studio in the foreground. The installer took 2 to 25 minutes in our tests. On its first start, `unsloth run` creates a local Unsloth Studio admin account (user name `unsloth`); its initial password is in `~/.unsloth/studio/auth/.bootstrap_password`. Use the API key that Unsloth prints at startup as the bearer token, for example `export OPENAI_BASE_URL="http://127.0.0.1:8001/v1"` and `export OPENAI_API_KEY="<key printed by unsloth run>"`.
- Unsloth enables its own server-side tools (for example web search and code execution) by default. To send only your own messages and tools, add `"enable_tools": false` to each request (with the OpenAI SDK: `extra_body={"enable_tools": False}`).
- Unsloth manages the served model name; do not pass `--alias`.
- `unsloth run` keeps only the thinking after the latest user message by default. To keep the thinking of earlier turns, pass `extra_body={"chat_template_kwargs": {"preserve_thinking": True}}` as in the main card's examples.

### Usage

Set `model` in the requests to the model ID that the server lists at `/v1/models` (`unsloth run` uses the file name, for example `Apex-35B-V1.0-Q4_K_M`). Otherwise use the same requests as for the BF16 model: see [Using Apex-35B-V1.0 via the Chat Completions API](https://huggingface.co/Apexintelligence-AI/Apex-35B-V1.0#using-apex-35b-v10-via-the-chat-completions-api). We recommend `temperature=1.0`, `top_p=0.95`, `top_k=20`, and an output budget large enough for the thinking. For multi-turn conversations and tool calls, append the complete assistant message, including `reasoning_content` and `tool_calls`, to `messages`.

## License

Apex-35B-V1.0, including these GGUF files, is licensed under the [Apache License 2.0](https://huggingface.co/Apexintelligence-AI/Apex-35B-V1.0/blob/main/LICENSE).

## Contact

If you have any questions, please reach out at [contact@apexin.ai](mailto:contact@apexin.ai).
