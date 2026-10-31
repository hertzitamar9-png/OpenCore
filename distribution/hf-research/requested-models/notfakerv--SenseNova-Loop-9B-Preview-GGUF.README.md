---
license: apache-2.0
language:
- en
- zh
base_model: sensenova/SenseNova-Loop-9B-Preview
base_model_relation: quantized
pipeline_tag: text-generation
tags:
- gguf
- sensenova
- sensenova-loop
- quantized
- llama-cpp
- lm-studio
- custom-runtime
---

# SenseNova Loop 9B Preview — Q8_0 GGUF

An **unofficial, text-only GGUF conversion** of [SenseNova's SenseNova-Loop-9B-Preview](https://huggingface.co/sensenova/SenseNova-Loop-9B-Preview), packaged by [notfakerv](https://huggingface.co/notfakerv). Original model development, training, and weights are credited to **SenseNova / OpenSenseNova**. This repository does not claim authorship of the base model or endorsement by SenseNova or LM Studio.

**Requires the custom SenseNova Loop runtime implementation included as source patches in this repository.** The model uses the actual `sensenova_loop` architecture. A runtime without that implementation may fail with `unknown model architecture: 'sensenova_loop'`. This is not a claim of support in stock LM Studio or upstream llama.cpp.

## Download

| File | Quantization | Size |
| --- | --- | --- |
| [SenseNova-Loop-9B-Preview-Q8_0.gguf](https://huggingface.co/notfakerv/SenseNova-Loop-9B-Preview-GGUF/resolve/main/SenseNova-Loop-9B-Preview-Q8_0.gguf?download=true) | Q8_0, with FP32 convolution, normalization, and mHC routing tensors | 15,069,056,512 bytes / 14.03 GiB |

```bash
hf download notfakerv/SenseNova-Loop-9B-Preview-GGUF \
  --local-dir SenseNova-Loop-9B-Preview-GGUF
cd SenseNova-Loop-9B-Preview-GGUF
sha256sum -c SHA256SUMS
```

## What changed

- Converted the text decoder to GGUF through a BF16 intermediate, then quantized it to Q8_0. No fine-tuning was performed.
- Added native execution of the two-stream mHC routing and repeated decoder passes to a local llama.cpp fork.
- Expanded 32 physical decoder layers into 56 virtual layer entries, duplicating the repeated block's stored weights so each visit has independent attention and recurrent state. This increases the file size; it does not introduce newly trained parameters.
- Preserved FP32 routing, normalization, and convolution tensors.
- Excluded the vision encoder/projector and MTP components. **This export supports text chat only. Image input, MTP, and diffusion acceleration are not implemented or validated.**

The upstream model's advertised context is 262,144 tokens; this conversion was tested at **8,192 tokens**. Longer contexts and other platforms have not been validated here.

## Runtime and LM Studio

See [runtime/README.md](runtime/README.md) for the pinned source, patch order, build commands, and LM Studio integration details. Patches were checked for clean application to the specified base.

The local validation used Linux x86_64, two NVIDIA RTX 3090 GPUs, LM Studio 0.4.26+4, and a custom runtime named `llama.cpp-linux-x86_64-nvidia-cuda12-avx2@2.66.101`, derived from LM Studio runtime 2.66.0. **2.66.101 is a local custom version, not an official downloadable LM Studio release.** No LM Studio vendor binaries are distributed here.

Once the custom runtime is installed and selected, put the GGUF under your LM Studio model directory and load `sensenova-loop-9b-preview@q8_0`. The tested configuration used full GPU offload, 8,192 context tokens, and one parallel prediction. Disable automatic runtime replacement if it would switch away from the custom runtime. Use the embedded chat template; the smoke tests used thinking disabled.

## Validation and limits

BF16 and Q8_0 native logits were compared with the official Transformers implementation on three short prompts: multiplication, a capital-city question, and Python code completion. All three matched the official top-1 next token. Q8_0 KL divergences were approximately `0.00000325`, `0.0000918`, and `0.00401`; raw summary metrics are in [validation/](validation/).

LM Studio generated `91` for `13 × 7`, produced a correct Python square function, and recalled a project codename from conversation history. These are smoke tests, **not a general quality benchmark or proof of numerical equivalence**. No upstream benchmark scores are claimed for this conversion.

## Attribution and license

- **SenseNova / OpenSenseNova:** original model, weights, loop architecture, mHC implementation, and documentation. [Model repository](https://huggingface.co/sensenova/SenseNova-Loop-9B-Preview), [official code](https://github.com/OpenSenseNova/SenseNova-Loop). Model source revision: `27abea779b1e6634b136805b6519371739d551f7`.
- **The ggml authors and llama.cpp contributors:** GGUF tooling and the native inference foundation, under the MIT license. [llama.cpp](https://github.com/ggml-org/llama.cpp), [retained MIT license](runtime/LICENSE.llama.cpp).
- **Maphist0 and contributors to llama.cpp PR #31306:** the pre-existing Clover patch on which the tested runtime was built. [Original PR](https://github.com/ggml-org/llama.cpp/pull/31306). It is included as a runtime build prerequisite; the model weights in this repository are SenseNova Loop.
- **notfakerv, with OpenAI Codex assistance:** this GGUF conversion, quantization, native SenseNova Loop adaptation, packaging, and local validation.

The model and new SenseNova Loop adaptation are provided under [Apache License 2.0](LICENSE). Existing llama.cpp code and the prerequisite Clover patch retain their MIT licensing. See [NOTICE](NOTICE) and [provenance.json](provenance.json) for modification and source details.

Upstream citation:

```bibtex
@misc{sensenova2026loop,
  title        = {{SenseNova-Loop-9B-Preview}},
  author       = {{SenseNova}},
  year         = {2026},
  howpublished = {Hugging Face model repository},
  url          = {https://huggingface.co/sensenova/SenseNova-Loop-9B-Preview}
}
```
