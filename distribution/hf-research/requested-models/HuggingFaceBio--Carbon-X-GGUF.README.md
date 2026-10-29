---
library_name: gguf
license: apache-2.0
base_model: HuggingFaceBio/Carbon-X
base_model_relation: quantized
language:
- en
- dna
tags:
- dna
- genomics
- biology
- variant-effect-prediction
- llama.cpp
- gguf
- conversational
pipeline_tag: text-generation
---

![](figures/carbon-x-banner.png)

<p align="center">
  <a href="https://huggingface.co/spaces/HuggingFaceBio/Carbon-X-Release"><b>Blog</b></a> |
  <a href="https://huggingface.co/HuggingFaceBio"><b>Hugging Face Bio</b></a> |
  <a href="https://github.com/huggingface/carbon/tree/main"><b>Code</b></a>
</p>

# Carbon-X GGUF

GGUF quantizations of [Carbon-X](https://huggingface.co/HuggingFaceBio/Carbon-X), an open 14B language model for DNA
understanding and generation from the **Carbon** family, for local inference with
[llama.cpp](https://github.com/ggml-org/llama.cpp).

Carbon-X reads raw DNA inside an ordinary chat conversation and answers in English: it predicts whether mutations are
benign or pathogenic, identifies regions that control gene activity, classifies sequences by organism group, predicts
the next bases of a sequence and explains its reasoning. A hybrid tokenizer keeps a BPE vocabulary for text and encodes
DNA as non-overlapping 6-mers. See the [Carbon-X model card](https://huggingface.co/HuggingFaceBio/Carbon-X) for
details on the model and its limitations.

> [!IMPORTANT]
> These files need llama.cpp [b11742](https://github.com/ggml-org/llama.cpp/releases/tag/b11742) or newer, the first
> build with support for the Carbon `HybridDNATokenizer` ([#30825](https://github.com/ggml-org/llama.cpp/pull/30825)).
> Older builds, including the v0.7.0 release, load them but tokenize DNA wrongly.

## Table of Contents

1. [Available files](#available-files)
2. [How to use](#how-to-use)
3. [Evaluation](#evaluation)
4. [License](#license)
5. [Acknowledgements](#acknowledgements)

## Available files

| Quant | File | Size |
| --- | --- | ---: |
| BF16 | `carbon-x-bf16.gguf` | 29.7 GB |
| Q8_0 (recommended) | `carbon-x-q8_0.gguf` | 15.8 GB |
| Q6_K | `carbon-x-q6_k.gguf` | 12.6 GB |
| Q5_K_M | `carbon-x-q5_k_m.gguf` | 11.1 GB |
| Q4_K_M | `carbon-x-q4_k_m.gguf` | 9.6 GB |

**Q8_0 is the recommended file**: half the size of BF16, with the same benchmark scores. Use Q4_K_M when memory is
tight. In every K-quant the token embedding and output tensors, which hold the DNA tokens, stay at Q8_0.

## How to use

Click **Use this model > llama.cpp** on this page for a ready-made snippet. The easiest way to run it is the
[llama.cpp desktop app](https://llama.app) for macOS and Windows, which lists every model in your Hugging Face cache.
Alternatively, install llama.cpp from [llama.app](https://llama.app) or download a prebuilt binary from the
[releases page](https://github.com/ggml-org/llama.cpp/releases), then run:

```bash
llama serve -hf HuggingFaceBio/Carbon-X-GGUF:Q8_0   # opens a chat web UI and an OpenAI-compatible API
llama cli -hf HuggingFaceBio/Carbon-X-GGUF:Q8_0     # chat in the terminal
```

No other flags are needed: the GGUF files carry the chat template and Carbon-X's default sampling settings. Carbon-X
was post-trained and evaluated with thinking disabled. To match that, add
`--chat-template-kwargs '{"enable_thinking": false}'`.

### Working with DNA inputs

The tokenizer switches between BPE for text and 6-mers for DNA. Paste sequences directly into a message:

- **DNA is tagged automatically.** Runs of at least 6 A/C/G/T bases (either case) are wrapped in `<dna>…</dna>` and
  tokenized as 6-mers. Runs of at least 12 bases with up to 20% IUPAC ambiguity codes (`N`, `R`, `Y`, …) are tagged
  too, and sequences displayed in space-separated blocks are joined. Text you have already wrapped in `<dna>…</dna>`
  is always tokenized as DNA.
- **Only A, C, G and T form 6-mers.** A 6-mer that contains any other character, such as `N`, becomes a single `<oov>`
  token. To keep full resolution, filter to canonical bases first.
- **Any length works.** When a sequence's length is not a multiple of 6, its last 1–5 bases are encoded with dedicated
  tail tokens, and decoding returns the original sequence exactly.
- **DNA in responses is tagged.** The model writes DNA inside `<dna>…</dna>`, and llama.cpp keeps the tags in the
  returned text. Strip them if you need the bare sequence.

The llama.cpp tokenizer matches the Python `HybridDNATokenizer` exactly: identical token ids, detokenized text and
chat-template rendering on 1,000 benchmark prompts (1.13M tokens) and 36 DNA edge cases.

### Prompting tips

- Put the sequence and the question in the same user message.
- Include any context you already have, such as the gene, strand, coordinates or annotations. Without it, the model may
  guess at a sequence's identity or function.
- For variant questions, give the reference and alternate sequences, or a reference sequence with the position and the
  base change.

## Evaluation

We ran two of the DNA benchmarks from the [Carbon-X model card](https://huggingface.co/HuggingFaceBio/Carbon-X#evaluation)
on each GGUF file with the llama.cpp server, with thinking disabled, Carbon-X's default sampling settings and one sample
per problem. The Carbon-X column is the original model served with vLLM.

| Benchmark | Problems | Metric | Carbon-X | BF16 | Q8_0 | Q6_K | Q5_K_M | Q4_K_M |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| FrontierVEP | 1,000 | Accuracy | 70.4 | 69.6 | 69.1 | 69.0 | 70.4 | 67.8 |
| NextBase | 600 | Per-base accuracy | 39.4 | 40.1 | 39.7 | 39.5 | 39.6 | 38.6 |

- **FrontierVEP**: given a long reference sequence and a single-nucleotide variant, predict whether the variant is
  benign or pathogenic (500 benign, 500 pathogenic).
- **NextBase**: predict the next DNA bases of a sequence. The score is the fraction of bases predicted correctly.

Scores are single runs with sampling: the standard error is about 1.5 points on FrontierVEP and 1 point on NextBase.
BF16 through Q5_K_M are within about one standard error of Carbon-X. Q4_K_M is the lowest on both benchmarks.

## License

Apache 2.0.

## Acknowledgements

Carbon-X is a joint collaboration between the research teams at [Hugging Face](https://huggingface.co),
[Zhongguancun Academy](https://www.bza.edu.cn/en/), and the
[Telethon Institute of Genetics and Medicine](https://www.tigem.it/)/[University of Naples “Federico II”](https://www.dicmapi.unina.it/).

<p align="center">
  <img src="figures/partner-logos.png" alt="Hugging Face, Zhongguancun Academy, Telethon Institute of Genetics and Medicine and University of Naples Federico II" width="800">
</p>
