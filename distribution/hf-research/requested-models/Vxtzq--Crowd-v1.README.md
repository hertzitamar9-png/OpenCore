---
license: mit
language:
- ia
pipeline_tag: text-generation
datasets:
- openbmb/Ultra-FineWeb-L3
- HuggingFaceFW/fineweb
- Vxtzq/CrowdGPT
---
# Crowd-v1

**CrowdGPT's first community-distributed language model architecture.**

Trained on **10B tokens** as of today


* **Model:** Crowd-v1
* **Organization:** CrowdGPT
* **Parameters:** ~1B
* **Architecture:** Transformer
* **License:** MIT
* **Status:** Training in progress
* **Repository:** https://github.com/Vxtzq/CrowdGPT
* **CrowdGPT:** https://crowdgpt.net

## Overview

Crowd-v1 is the first official model architecture released for **CrowdGPT**, a community-driven distributed AI project.

Unlike a conventional pretrained model release, **Crowd-v1 is distributed with partially trained weights**. The purpose of this release is to provide a common model definition and weight format that CrowdGPT clients can download and collectively train.

The model is designed to be consumed by the CrowdGPT distributed training infrastructure, where individual participants contribute compute toward training a shared model.

> **Important:** Crowd-v1 undergoing pretraining. Its initial weights contain partial useful language knowledge and the model should not be expected to generate very coherent text.

## Architecture

Crowd-v1 contains approximately **1 billion parameters**.

| Component               |                     Configuration |
| ----------------------- | --------------------------------: |
| Architecture            |                       Transformer |
| Total parameters        |                     1,007,787,392 |
| Vocabulary size         |                           151,669 |
| Hidden dimension        |                             1,536 |
| Layers                  |                                24 |
| Attention heads         |                                16 |
| KV heads                |                                 4 |
| Head dimension          |                                96 |
| MLP hidden dimension    |                             2,560 |
| Maximum sequence length |                              2048 |
| Attention               |           Grouped-Query Attention |
| Position encoding       | Rotary Position Embeddings (RoPE) |
| MLP                     |                            SwiGLU |
| Weight tying            |                               Yes |
| Attention               |                  Causal / chunked |
| Engrams                 |                   350m cpu params |
| Initialization          |     Normal distribution, σ = 0.02 |

## Inference code
Please note that this inference code will be deprecated in future version, this version is for testing only:
```python
from transformers import pipeline

pipe = pipeline(
    "text-generation",
    model="Vxtzq/Crowd-v1",
    trust_remote_code=True,
)

print(pipe("The future of AI is", max_new_tokens=100))
```

### Key features

**Grouped-Query Attention (GQA)**
Crowd-v1 uses 16 query heads but only 4 key/value heads, reducing KV-cache and attention-related memory requirements.

**Rotary Position Embeddings (RoPE)**
Rotary embeddings are applied to query and key representations.

**SwiGLU**
The feed-forward network uses a SwiGLU-style gated MLP with a hidden dimension of 2,560.

**Weight tying**
The token embedding matrix and language-model head share weights, reducing the total parameter count.

**Causal attention**
Each token can only attend to preceding tokens, making the architecture suitable for autoregressive language modeling.

## Initialization

The released weights are generated deterministically using seed `42`.

Non-normalization parameters are initialized from:

`N(0, 0.02)`

LayerNorm weights are initialized to `1.0` and LayerNorm biases to `0.0`.

This means that different copies of the initial model can be verified against the published SHA-256 checksums.

## Weight formats

The repository contains flat binary weight files designed specifically for the CrowdGPT client.

### BF16

`weights_bf16.bin`

Approximately **1.4 GB**.

`engrams_bf16.bin`

Approximately **0.7 GB**.

This is the recommended file for normal CrowdGPT client usage.

### FP32

`weights_fp32.bin`

Approximately **2.6 GB**.

`engrams_fp32.bin`

Approximately **1.4 GB**.

The FP32 weights are provided for environments or experiments that require full-precision initialization (or don't support bf16).

The binary weights are stored as a single flattened parameter array. The tensor ordering is identical to the parameter ordering used by the CrowdGPT `SotaGPT` implementation, allowing the weights to be loaded directly without converting them into a framework-specific checkpoint format.

## Configuration

The model configuration is provided in `config.json`.

```json
{
  "architecture": "SotaGPT",
  "vocabSize": 151,669,
  "dim": 1536,
  "nLayers": 24,
  "nHeads": 16,
  "nKvHeads": 4,
  "headDim": 96,
  "maxSeqLen": 2048,
  "mlpHidden": 2560,
  "weightTying": true
}
```

## Intended use

Crowd-v1 is primarily intended for:

* Distributed training through CrowdGPT
* Research into community-driven AI training
* Experiments with distributed language-model training
* Reproducible model initialization
* Development of CrowdGPT-compatible clients
* Educational experimentation with transformer architectures

## Tokenizer

The current release uses a **Qwen2.5-compatible vocabulary of 151,669 tokens**.

The tokenizer is an architectural dependency: changing the tokenizer changes the vocabulary size and therefore the dimensions of the tied embedding / language-model head.

Future CrowdGPT releases may use a custom tokenizer with a different vocabulary.

If the tokenizer is changed, `vocabSize` in `config.json` must be updated accordingly and the model must be reinitialized with the new vocabulary dimensions.

## Reproducibility

Crowd-v1 is generated from a fixed architecture and initialization seed.

The model-generation implementation is available in the CrowdGPT repository:

https://github.com/Vxtzq/CrowdGPT

The repository also contains the CrowdGPT client implementation responsible for consuming the flat weight format.

## Verification

Each distributed weight file is accompanied by its SHA-256 checksum in `config.json`.

This allows clients and researchers to verify that downloaded weights have not been corrupted or modified.

## License

Crowd-v1 is released under the **MIT License**.

See the repository for the complete license text.

## Citation

If you use Crowd-v1 or CrowdGPT in your research or project, please reference the CrowdGPT project:

```bibtex
@misc{crowdgpt,
  title        = {CrowdGPT},
  author       = {Vxtzq},
  year         = {2026},
  url          = {https://github.com/Vxtzq/CrowdGPT}
}
```

## About CrowdGPT

**CrowdGPT is a community-driven approach to training AI.**

Instead of concentrating all training compute in a single data center, CrowdGPT is designed around participants contributing their own compute to a shared training process.

The goal is simple:

**Community compute → Shared training → Open model**

Learn more at https://crowdgpt.net