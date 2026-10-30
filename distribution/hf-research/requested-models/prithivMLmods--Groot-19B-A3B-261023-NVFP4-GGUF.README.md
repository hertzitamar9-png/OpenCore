---
library_name: transformers
license: apache-2.0
language:
- en
base_model:
- GrootTeam/Groot-19B-A3B-261023
pipeline_tag: image-text-to-text
tags:
- text-generation-inference
- mtp
- moe
- llama-cpp
- nvfp4
- nvidia
- llama-cli
- speculative-decoding
- nvidia-blackwell
- rtx-pro-6000
- SM120
- GB202
- B200
- B300
- FP4
- blackwell
- qwen3_5
- qwen3_5_moe
- Qwen3.5
- Qwen3.6
- Qwen3.8
- Groot
- GrootTeam
- reasoning
- A3B
- 19B
---

# **Groot-19B-A3B-261023-NVFP4-GGUF**

> **Groot-19B-A3B-261023** is a compact, open-weight (Apache-2.0), natively multimodal Mixture-of-Experts (MoE) reasoning model built for complex real-world tasks, agentic coding, and native tool use. Derived from Qwen3.6-35B-A3B and post-trained on fewer than 0.1 billion open-source tokens, it comprises roughly 19B total parameters (plus a 0.4B Multi-Token Prediction layer) with only ~3B activated per token across 40 hybrid layers (10 full-attention and 30 linear-attention) and 128 routed experts. Despite using 45.7% fewer parameters than Qwen3.6-35B-A3B and 26.9% fewer than Gemma-4-26B-A4B, it outperforms both on 12 of 16 benchmarks (averaging 41.44 vs. 38.08 and 30.05) while consuming 23.1% and 65.6% fewer output tokens, respectively. The model operates in thinking mode by default—with configurable non-thinking and preserved-thinking modes—and supports a native 262,144-token (256K) context window extensible to roughly 1M tokens via YaRN. Released in BF16, FP8, NVFP4, and GGUF (Q4_K_M) weights for deployment on vLLM, SGLang, KTransformers, and Hugging Face Transformers, it is engineered for accessible hardware, running a 128K context on a 16GB RTX 4060 Ti and the full 256K context on 24GB consumer GPUs (RTX 3090/4090), an RTX 5090, or 128GB unified-memory systems.

> [!NOTE]
> MTP — Speculative Decoding: Yes

> [!NOTE]
Multi-Token Prediction (MTP) GGUF is a specialized GGUF model file format extension that integrates speculative decoding directly into the model weights to significantly accelerate local inference.  Unlike traditional speculative decoding which requires a separate, smaller "draft" model, MTP GGUF files include additional output heads within the main model architecture that predict multiple future tokens in a single forward pass.

## Model Files

| File Name | Quant Type | File Size | File Link | Description |
|-----------|------------|-----------|-----------|-------------|
| Groot-19B-A3B-261023-NVFP4.gguf | NVFP4 | 13.9 GB | [Link](https://huggingface.co/prithivMLmods/Groot-19B-A3B-261023-NVFP4-GGUF/blob/main/Groot-19B-A3B-261023-NVFP4.gguf) | NVFP4 quantization. Highly optimized for **NVIDIA Blackwell** hardware accelerators **(e.g., RTX Pro 6000, RTX 50-series, B200, B300)**, offering native hardware-accelerated FP4 Tensor Core speeds and a strong balance of size and performance for this 19B parameter MoE model. |
| Groot-19B-A3B-261023-NVFP4.mmproj.gguf | mmproj | 903 MB | [Link](https://huggingface.co/prithivMLmods/Groot-19B-A3B-261023-NVFP4-GGUF/blob/main/Groot-19B-A3B-261023-NVFP4.mmproj.gguf) | Multimodal projection file. Used for vision/language models. |

## NVFP4 Test Run

Tested `Groot-19B-A3B-261023-NVFP4.gguf` with vision support on an NVIDIA RTX PRO 6000 Blackwell Server Edition GPU with `96 GB` VRAM, using `llama-cli` with full GPU offloading and MTP speculative decoding.

### Performance Metrics

| Metric | Result |
|---|---:|
| Average Prompt Processing Speed | 161.82 tokens/s |
| Average Generation Speed | 278.05 tokens/s |
| Average Wall Time per Run | 9.24 s |
| Total Wall Time | 46.2 s |
| Total Generated Tokens | 7,267 |
| Context Length | 16,384 tokens |
| KV Cache (K) | `q8_0` |
| GPU | NVIDIA RTX PRO 6000 Blackwell Server Edition |
| GPU VRAM Capacity | 96 GB |
| Peak GPU VRAM Usage | 13,857 MiB (13.53 GiB) |
| Peak GPU Utilization | 83% |
| GPU Layers Offloaded | 99 |
| Random Seed | 42 |
| Speculative Decoding | MTP (`draft-mtp`, `draft_n_max=2`) |
| Test Runs | 5 |
| Minimum Generation Speed | 265.62 tokens/s |
| Maximum Generation Speed | 286.67 tokens/s |

### Test Shots

| Shot | Task | Prompt Processing (tokens/s) | Generation (tokens/s) | Wall Time |
|---|---|---:|---:|---:|
| 1 | C: Insert Node at End of Singly Linked List | 85.75 | 281.87 | 11.678 s |
| 2 | Jogger and Train Passing Time | 184.65 | 265.62 | 11.469 s |
| 3 | Two Pipes Filling a Tank | 168.66 | 276.52 | 8.332 s |
| 4 | Students in Examination Rooms | 227.82 | 286.67 | 8.086 s |
| 5 | Father and Son Ages | 142.22 | 279.56 | 6.625 s |

### Run Inference

```bash
# Download the model
huggingface-cli download prithivMLmods/Groot-19B-A3B-261023-NVFP4-GGUF \
    Groot-19B-A3B-261023-NVFP4.gguf \
    --local-dir /path/to/models/

# Run inference with MTP speculative decoding
llama-cli \
    -m /path/to/models/Groot-19B-A3B-261023-NVFP4.gguf \
    --spec-type draft-mtp \
    --spec-draft-n-max 2 \
    -ngl 99 \
    -c 16384 \
    --cache-type-k q8_0 \
    --seed 42 \
    -n 8192 \
    -st \
    -p "Write a program in C to insert a new node at the end of a Singly Linked List."
```

*Performance results reflect this specific hardware and inference configuration. Actual throughput may vary depending on the prompt, output length, model, software build, and runtime settings.*

## llama.cpp

LLM inference in C/C++ — https://github.com/ggml-org/llama.cpp

---

## Snapshots (5)

![download (1)](https://cdn-uploads.huggingface.co/production/uploads/65bb837dbfb878f46c77de4c/1wzh_2brkDh-3Co5vINOO.png)
![download (2)](https://cdn-uploads.huggingface.co/production/uploads/65bb837dbfb878f46c77de4c/2hSYShh8c5NdyychYfYgC.png)
![download (3)](https://cdn-uploads.huggingface.co/production/uploads/65bb837dbfb878f46c77de4c/UQ1ULjwpVwgqR4yEo3dMr.png)
![download (4)](https://cdn-uploads.huggingface.co/production/uploads/65bb837dbfb878f46c77de4c/ps1VnCYl9M99ly3qbgOcS.png)
![download (5)](https://cdn-uploads.huggingface.co/production/uploads/65bb837dbfb878f46c77de4c/y32JZPh-M8SWQ9TkNHQxv.png)
![download](https://cdn-uploads.huggingface.co/production/uploads/65bb837dbfb878f46c77de4c/cnXxvCPtKNRHt6Ch58G6T.png)

---

Quantized using llama.cpp by [prithivMLmods](https://huggingface.co/prithivMLmods)