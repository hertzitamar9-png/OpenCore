---
license: other
license_name: lfm1.0
license_link: LICENSE
base_model:
- LiquidAI/LFM3-1.7B
base_model_relation: quantized
pipeline_tag: text-generation
library_name: gguf
tags:
- lfm3
- slq
- mixed-precision
- gguf
- llama.cpp
- quantization
---

<div align="center">
  <img src="https://cdn-uploads.huggingface.co/production/uploads/64b93e6bd6c468ac7536607e/mj6xac74jHGLqymiovObc.png" alt="The Kaitchup — AI on a Budget" width="100%" />
  <p><a href="https://kaitchup.substack.com/subscribe"><strong>Subscribe to my newsletter</strong></a> · <a href="https://buymeacoffee.com/kaitchup"><strong>Support</strong></a></p>
</div>

# LFM3-1.7B-SLQ-GGUF

This is [LiquidAI/LFM3-1.7B](https://huggingface.co/LiquidAI/LFM3-1.7B), compressed with a **customized version of SLQ (Statistically-Lossless Quantization)** and assembled as GGUF. The search combines existing quantized donor weights and chooses a source separately for 154 complete matrices, including the shared embeddings and output head.

The search used many donors, but could only match the size of AtomicChat's AD-Q6_K with a slightly better EAR@10: **97.7383% versus 97.5171%**, an improvement of **0.2212 percentage points**. Both GGUF files are approximately **1.588 GB**.

One model is available:

| Model | GGUF file | Active text weights (GB) | Complete file (GB) | Measured EAR@10 ↑ |
|---|---|---:|---:|---:|
| EAR97.7 | [LFM3-1.7B-SLQ-EAR97.7-Q6_K.gguf](LFM3-1.7B-SLQ-EAR97.7-Q6_K.gguf) | 1.579 | 1.588 | 97.738262% |

- **Measured EAR@10:** 97.738262%, evaluated using the BF16 reference's top 10 tokens.
- **Sizes:** decimal GB (1 GB = 1,000,000,000 bytes). Active text weights include fixed text tensors and exclude metadata and alignment.
- **Embeddings / output head:** shared Q8_0 weights.
- The file contains 287 text tensors. No MTP tensors, vision tensors or multimodal projector are included.
- **Made by:** [The Kaitchup](https://kaitchup.substack.com/).
- **License:** [LFM Open License v1.0](LICENSE).

The filename suffix **Q6_K** is a Hub compatibility and dominant-precision-family label for this **custom SLQ allocation**.

Method: [SLQ paper](https://arxiv.org/abs/2605.02404) · [SLQ repository](https://github.com/IST-DASLab/SLQ).

## Candidate sources

The search considered **Q4_K_M, Q5_K_M, Q6_K, Q8_0, QAD-Q4_0, and BF16** from [LiquidAI/LFM3-1.7B-GGUF](https://huggingface.co/LiquidAI/LFM3-1.7B-GGUF), plus **AD-IQ3_S, AD-IQ4_XS, AD-Q4_K_M, AD-Q5_K_M, AD-Q6_K, and Q8_0** from [AtomicChat/LFM3-1.7B-GGUF](https://huggingface.co/AtomicChat/LFM3-1.7B-GGUF). Identical tensor payload alternatives were canonicalized.

**99% of the work was already done** thanks to the GGUFs released by [LiquidAI](https://huggingface.co/LiquidAI/LFM3-1.7B-GGUF) and [AtomicChat](https://huggingface.co/AtomicChat/LFM3-1.7B-GGUF), and the [SLQ implementation by ISTA](https://github.com/IST-DASLab/SLQ).

Credit goes to Liquid AI for the base model and official GGUF donors, AtomicChat for its GGUF donors, and Michael Helcig, Eldar Kurtic and Dan Alistarh for SLQ. The base and donor repositories declare LFM Open License v1.0 licensing; the full license is included in [LICENSE](LICENSE). Original copyright and attribution rights remain with their authors.


## Precision by layer

### EAR97.7

![EAR97.7: actual tensor formats and stored bits per weight](assets/precision-ear97.7.svg)

[SVG](assets/precision-ear97.7.svg)

## EAR comparison

**EAR means Expected Acceptance Rate.** It measures the overlap between the original BF16 model's next-token probabilities and those of the compared model. For EAR@10, both distributions are separately renormalized over the BF16 reference's 10 most likely tokens. An EAR@10 of 0.99 means 99% average probability overlap on that measured support; it does not mean 99% benchmark accuracy or identical generated answers.


![EAR@10 versus complete GGUF file size: 512 sequences of length 2,048](assets/ear-vs-size.svg)

[Zoomable SVG](assets/ear-vs-size.svg)

All points use the same 512 × 2,048-token corpus and BF16 reference. Donor measurements used four parallel sequences; the SLQ checkpoint used eight.

SLQ's “statistically lossless” objective concerns approximate fidelity, not identical numerical outputs. This release achieves the measured EAR@10 shown above. The same calibration corpus guided model selection, so these values are calibration fidelity rather than an independent held-out benchmark. Statistical equivalence on downstream tasks has not been established for this release.

## Instructions: llama.cpp

Use a llama.cpp build with LFM3 architecture support. Native text scoring used an NVIDIA GeForce RTX 3060 with llama.cpp commit `2973c945bf32759976d858a3a35ae0e026b4507d`. The exported GGUF's tensor formats, allocation and exact file size were checked against the measured model.

Install the Hugging Face CLI, then download one file:

```bash
pip install -U huggingface_hub
hf download kaitchup/LFM3-1.7B-SLQ-GGUF \
  LFM3-1.7B-SLQ-EAR97.7-Q6_K.gguf --local-dir .
```

Start an OpenAI-compatible text server with the downloaded model:

```bash
llama-server \
  --model LFM3-1.7B-SLQ-EAR97.7-Q6_K.gguf \
  --n-gpu-layers 99 \
  --ctx-size 8192 \
  --port 8080
```

Send a chat request:

```bash
curl http://localhost:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "messages": [{"role": "user", "content": "Is Earth smaller than Venus?"}],
    "max_tokens": 256
  }'
```


## Support

If this model is useful, [support the work](https://buymeacoffee.com/kaitchup) or [subscribe to The Kaitchup](https://kaitchup.substack.com/subscribe).
