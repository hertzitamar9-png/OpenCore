---
license: apache-2.0
library_name: llama.cpp
pipeline_tag: image-text-to-text
tags:
- ternary
- 2-bit
- gguf
- llama-cpp
- cuda
- metal
- on-device
- hybrid-attention
- vision
- prismml
- bonsai
base_model:
- Qwen/Qwen3.5-9B
---

<p align="center">
  <a href="https://prismml.com"><b>Prism ML Website</b></a> &nbsp;|&nbsp;
  <a href="https://github.com/PrismML-Eng/Bonsai-demo"><b>Demo &amp; Examples</b></a> &nbsp;|&nbsp;
  <a href="https://discord.gg/prismml"><b>Discord</b></a>
</p>

# Bonsai 2 Ternary 9B: GGUF

A 9B-class vision-language model with ternary transformer weights, for llama.cpp (CUDA, Metal, CPU).

> **~2.42 GB** language model (PQ2_0), down from ~17.9 GB at FP16

## Highlights

- **Ternary language weights** across embeddings, attention projections, MLP projections and the LM head, stored with a group-wise FP16 scale.
- **Three GGUF packings** of the same weights, consumed directly by custom kernels and never expanded back to FP16: **PTQ1_0** (dense trits, 2.00 GB), **PQ2_0** (2-bit slots, 2.42 GB) and **Q2_0** (2.56 GB).
- **Vision input** through a separate mmproj pack (Q8_0, or BF16 for reference precision), loaded only when an image arrives.
- **262K-token context**, inherited from the Qwen3.5-9B hybrid-attention backbone (24 linear-attention and 8 full-attention layers).

## Model Overview

| Item | Specification |
| :--- | :--- |
| Base model | Derived from Qwen3.5-9B (architecture unchanged) |
| Parameters | ~8.95B language model (32 blocks, hidden 4096, separate embedding and LM head) + ~0.46B vision tower (27 blocks) |
| Architecture | Hybrid attention (3 linear-attention : 1 full-attention), SwiGLU MLP, RoPE, RMSNorm |
| Context length | 262,144 tokens (inherited from the base model) |
| Weight format | Ternary {−1, 0, +1} with FP16 group-wise scales, packed as PTQ1_0, PQ2_0 or Q2_0 |
| Weight basis | Blockwise Hadamard rotation folded into the stored weights; the runtime applies the matching transform to activations |
| Higher-precision tensors | Normalization weights and the linear-attention state path (`ssm_alpha`, `ssm_beta`) stay F32 |
| Vision tower | Optional mmproj pack: Q8_0 (0.62 GB, recommended) or BF16 (0.92 GB) |
| Backends | llama.cpp (CUDA, Metal, CPU) via the PrismML fork |
| License | Apache 2.0 |

## Files

| File | Contents | Size | Bits/weight (language model) |
| :--- | :--- | ---: | ---: |
| `Ternary-Bonsai-2-9B-PTQ1_0.gguf` | language model, dense trits | 2.00 GB | ~1.78 |
| `Ternary-Bonsai-2-9B-PQ2_0.gguf` | language model, 2-bit slots | 2.42 GB | ~2.16 |
| `Ternary-Bonsai-2-9B-Q2_0.gguf` | language model, Q2_0 | 2.56 GB | ~2.28 |
| `Ternary-Bonsai-2-9B-mmproj-Q8_0.gguf` | vision tower | 0.62 GB | |
| `Ternary-Bonsai-2-9B-mmproj-BF16.gguf` | vision tower (reference precision) | 0.92 GB | |
| `Ternary-Bonsai-2-9B-PQ2_0-MTP-Q8_0.gguf` | PQ2_0 model with its MTP draft head built in | 2.68 GB | |
| `Ternary-Bonsai-2-9B-DFlash2-Q8_0.gguf` | DFlash2 block drafter (optional) | 1.57 GB | |

Bits/weight is file size over the ~8.95B language-model parameters, so it includes the F32 tensors and metadata. The three language-model packs hold the same ternary weights; they differ only in storage layout and kernel speed. The MTP file is the PQ2_0 model plus one extra draft layer, so you load it instead of the plain PQ2_0, not alongside it.

## Quickstart

> **[PrismML-Eng/Bonsai-demo](https://github.com/PrismML-Eng/Bonsai-demo) is the source of truth for running these models.** It has the tested setup for every backend and pins a known-good binary release.

### Use the PrismML llama.cpp build

The ternary kernels live in the [PrismML-Eng/llama.cpp](https://github.com/PrismML-Eng/llama.cpp) fork (branch `prism`). It runs every file here, including MTP and DFlash2. Upstream llama.cpp runs only `Q2_0` (recent `master`, which includes the Hadamard activation runtime); it rejects `PQ2_0` and `PTQ1_0` as unknown types.

```bash
# prebuilt: pick the archive for your platform
# https://github.com/PrismML-Eng/llama.cpp/releases/latest
tar -xzf llama-<tag>-bin-<platform>.tar.gz -C bin --strip-components=1

# or build it
git clone https://github.com/PrismML-Eng/llama.cpp && cd llama.cpp
cmake -B build -DGGML_CUDA=ON && cmake --build build -j   # drop -DGGML_CUDA=ON on macOS; Metal is the default
```

### Text

```bash
./bin/llama-cli -m Ternary-Bonsai-2-9B-PQ2_0.gguf -ngl 99 -fa on \
  -p "Explain in three sentences why the sky is blue."
```

### Image + text

```bash
./bin/llama-mtmd-cli -m Ternary-Bonsai-2-9B-PQ2_0.gguf \
  --mmproj Ternary-Bonsai-2-9B-mmproj-Q8_0.gguf -ngl 99 \
  --image photo.jpg -p "Describe this image."
```

**Vision projector:** use the Q8_0 mmproj. With the current fork build, the BF16 mmproj can crash llama-server when several image requests run at once; Q8_0 is not affected and quality is nearly the same.

**Small text in images:** for small or dense text, raising the image token budget with `--image-min-tokens 1024` may help.

### OpenAI-compatible server

```bash
./bin/llama-server -m Ternary-Bonsai-2-9B-PQ2_0.gguf \
  --mmproj Ternary-Bonsai-2-9B-mmproj-Q8_0.gguf -ngl 99 -fa on -c 32768 --port 8080
```

```bash
curl http://localhost:8080/v1/chat/completions -H "Content-Type: application/json" -d '{
  "messages": [{"role": "user", "content": [
    {"type": "text", "text": "What is in this image?"},
    {"type": "image_url", "image_url": {"url": "https://upload.wikimedia.org/wikipedia/commons/4/47/PNG_transparency_demonstration_1.png"}}
  ]}],
  "chat_template_kwargs": {"enable_thinking": false}
}'
```

## Best Practices

### Generation Parameters

We recommend these sampling settings. They are the same for every packing and for MLX:

> - Thinking mode (default): `temperature=1.0`, `top_p=0.95`, `top_k=20`, `min_p=0.0`, `presence_penalty=1.0`, `repetition_penalty=1.0`
> - Non-thinking mode: `temperature=0.7`, `top_p=0.80`, `top_k=20`, `min_p=0.0`, `presence_penalty=1.5`, `repetition_penalty=1.0`

Apply the penalties over the last 64 tokens (llama.cpp's default window). Presence penalty has no GGUF metadata key, so pass it on the command line or per request.

```bash
# thinking on
./bin/llama-server -m Ternary-Bonsai-2-9B-PQ2_0.gguf -ngl 99 -fa on \
  --temp 1.0 --top-p 0.95 --top-k 20 --min-p 0 --presence-penalty 1.0 --repeat-penalty 1.0 --repeat-last-n 64

# thinking off: start the server with these values, and send
# "chat_template_kwargs": {"enable_thinking": false} with each request
./bin/llama-server -m Ternary-Bonsai-2-9B-PQ2_0.gguf -ngl 99 -fa on \
  --temp 0.7 --top-p 0.8 --top-k 20 --min-p 0 --presence-penalty 1.5 --repeat-penalty 1.0 --repeat-last-n 64
```

Avoid greedy decoding (`temperature=0`) with thinking on; it can get stuck reasoning on some prompts.

### Output Length

Give thinking mode room to finish. Even simple prompts can use 3,000+ thinking tokens, so allow at least 8,192 output tokens with thinking on. For short answers, turn thinking off.

### System Prompt

You can use a simple system prompt such as:

```
You are a helpful assistant
```

### MLX

On Apple Silicon you can also use the MLX build of the same weights, [Ternary-Bonsai-2-9B-mlx-2bit](https://huggingface.co/prism-ml/Ternary-Bonsai-2-9B-mlx-2bit). It loads natively in mlx-vlm 0.7.8 and newer (the released mlx-lm cannot load it yet). Pass the values above explicitly: mlx-vlm's Python `generate()` is greedy by default. Its model card has the commands.

### Choosing a Packing

- **PQ2_0**: the default; cheaper to unpack, and the pack measured on Apple Silicon.
- **PTQ1_0**: the smallest file; use it where memory is tightest.
- **Q2_0**: an alternative layout, and the one format upstream llama.cpp can also run.

## Speculative Decoding

Two optional drafters ship with the model. Both need the PrismML fork.

**Recommended: MTP.** `Ternary-Bonsai-2-9B-PQ2_0-MTP-Q8_0.gguf` is the PQ2_0 model with a multi-token-prediction head built in. Draft depth 2 works best:

```bash
./bin/llama-server -m Ternary-Bonsai-2-9B-PQ2_0-MTP-Q8_0.gguf -ngl 99 -fa on \
  --spec-type draft-mtp --spec-draft-n-max 2 --spec-draft-n-min 0 --spec-draft-p-min 0
```

**KV cache with MTP on CUDA:** quantized KV (`--cache-type-k q8_0 --cache-type-v q8_0`) works with MTP from release [`prism-b10776-d00e252`](https://github.com/PrismML-Eng/llama.cpp/releases/tag/prism-b10776-d00e252) onward. On older CUDA builds, keep the default f16 KV cache (or quantize only K) when MTP is on. Metal is not affected.

**Optional: DFlash2.** A block drafter for `Ternary-Bonsai-2-9B-PQ2_0.gguf`. It helps on copy-heavy work (code edits, rewrites) and is slower than plain decoding on general chat, so prefer MTP by default:

```bash
./bin/llama-server -m Ternary-Bonsai-2-9B-PQ2_0.gguf -md Ternary-Bonsai-2-9B-DFlash2-Q8_0.gguf \
  -ngl 99 -ngld 99 --spec-type draft-dflash
```

On CUDA, speculative outputs can differ slightly from plain decoding where two tokens are nearly tied, so exact-match comparisons against non-speculative runs will show small differences.

## License

Apache 2.0, following the base model.
