---
library_name: transformers
license: apache-2.0
base_model: Qwen/Qwen3-14B
language:
- en
tags:
- dna
- genomics
- biology
- variant-effect-prediction
- conversational
pipeline_tag: text-generation
---

![](figures/carbon-x-banner.png)

<p align="center">
  <a href="https://huggingface.co/spaces/HuggingFaceBio/Carbon-X-Release"><b>Blog</b></a> |
  <a href="https://huggingface.co/HuggingFaceBio"><b>Hugging Face Bio</b></a> |
  <a href="https://github.com/huggingface/carbon/tree/main"><b>Code</b></a>
</p>

# Carbon-X

An open 14B language model for DNA understanding and generation, from the **Carbon** family.

## Table of Contents

1. [Model Summary](#model-summary)
2. [How to use](#how-to-use)
3. [Evaluation](#evaluation)
4. [Training](#training)
5. [Using Carbon-X as a tool for an agent](#using-carbon-x-as-a-tool-for-an-agent)
6. [Limitations](#limitations)
7. [License](#license)
8. [Acknowledgements](#acknowledgements)

## Model Summary

<p align="center">
    <img src="figures/score-vs-cost.png" alt="Average DNA benchmark score versus cost per problem relative to Carbon-X" width="800">
</p>

<p align="center"><em>Each comparison model is shown at the reasoning effort with its highest average score. Costs use standard API pricing; for Carbon-X, measured throughput per H200-hour at $5/hour.</em></p>

**Carbon-X** is a 14.8B-parameter decoder-only language model trained jointly on DNA sequences and natural-language text. You give it raw DNA inside an ordinary chat conversation and it answers in English: it predicts whether mutations are benign or pathogenic, identifies regions that control gene activity, classifies sequences by organism group, predicts the next bases of a sequence, and explains its reasoning. Responses can contain both DNA and natural language.

DNA and text share a single token stream in a single decoder. There is no separate DNA encoder. A hybrid tokenizer keeps a BPE vocabulary for text and encodes DNA as non-overlapping 6-mers, so one DNA token covers 6 base pairs.

Across five DNA benchmarks, which cover variant-effect prediction, sequence understanding and next-base prediction, Carbon-X averages **66.7%** with thinking disabled. That is 27 points above Qwen3-14B, the model it starts from, and ahead of every other open model we evaluated. It is close to GPT-6.1 Sol (67.5%) at about 1/1,800 of the inference cost per problem, and it leads every model we tested on coding-variant effects and next-base prediction ([Evaluation](#evaluation)). Carbon-X answers directly, without extended reasoning, so each problem costs few output tokens. We estimate that analysing the 2.4 million variants of uncertain significance in ClinVar would cost about $300 with Carbon-X, compared with $32,000 to $4 million with the frontier models we evaluated. The weights fit on a single 80 GB GPU in bfloat16, so researchers can keep sensitive or unpublished sequences on their own infrastructure.

### Key features

- **14.8B parameters**: 40 layers, hidden size 5120, GQA (40 query heads, 8 KV heads), SwiGLU, RMSNorm with QK-norm.
- **Hybrid tokenizer**: the Qwen3 BPE vocabulary for text plus 5,515 DNA tokens. These are 4,096 6-mers, 1,364 tail tokens for sequences whose length is not a multiple of 6, and the `<dna>`, `</dna>` and `<oov>` markers. DNA in a prompt is detected and tagged automatically.
- **Chat model**: post-trained and evaluated with thinking disabled.
- **Context window**: 40,960 tokens.
- **Training**: 302B tokens of mid-training on DNA, biology text, English and instruction data, then supervised fine-tuning and reinforcement learning with verifiable rewards on DNA tasks.
- **Efficient inference**: serves with vLLM on a single GPU.

## How to use

Carbon-X uses a standard causal LM architecture. The DNA tokenizer is custom code, so pass `trust_remote_code=True` when you load the tokenizer. Carbon-X requires Transformers v5 or later.

```bash
pip install -U transformers
```

```python
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

model_id = "HuggingFaceBio/Carbon-X"

tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(model_id, dtype=torch.bfloat16, device_map="cuda")

messages = [
    {
        "role": "user",
        "content": (
            "Reference: ATGGTGCATCTGACTCCTGAGGAGAAGTCTGCCGTTACTGCCCTGTGGGGCAAGG\n"
            "Variant:   ATGGTGCATCTGACTCCTGTGGAGAAGTCTGCCGTTACTGCCCTGTGGGGCAAGG\n\n"
            "Is this variant likely to be benign or pathogenic? Explain your reasoning."
        ),
    }
]
inputs = tokenizer.apply_chat_template(
    messages,
    add_generation_prompt=True,
    enable_thinking=False,
    return_tensors="pt",
    return_dict=True,
).to(model.device)

outputs = model.generate(**inputs, max_new_tokens=1024)
print(tokenizer.decode(outputs[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True))
```

Carbon-X was post-trained and evaluated with `enable_thinking=False`, and we recommend keeping it that way. `generate()` uses the sampling defaults from `generation_config.json` (temperature 0.6, top-p 0.95, top-k 20), which are also the settings we used for every reported result.

### Serving with vLLM

```bash
pip install -U vllm
vllm serve HuggingFaceBio/Carbon-X --trust-remote-code
```

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="EMPTY")
response = client.chat.completions.create(
    model="HuggingFaceBio/Carbon-X",
    messages=[{"role": "user", "content": "What does this sequence encode?\n\nATGGTGCATCTGACTCCTGAGGAGAAGTCTGCCGTT"}],
    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
)
print(response.choices[0].message.content)
```

vLLM also applies the sampling defaults from `generation_config.json` unless you override them in the request.

### Tokenizer: working with DNA inputs

The tokenizer switches between BPE for text and 6-mers for DNA. You can paste sequences directly into a message:

```python
>>> ids = tokenizer("Describe this sequence: ACGTACGATTCA")["input_ids"]
>>> tokenizer.convert_ids_to_tokens(ids)
['Describe', 'Ġthis', 'Ġsequence', ':', 'Ġ', '<dna>', 'ACGTAC', 'GATTCA', '</dna>']
```

- **DNA is tagged automatically.** Runs of at least 6 A/C/G/T bases are wrapped in `<dna>…</dna>` and tokenized as 6-mers. Runs of at least 12 bases with up to 20% IUPAC ambiguity codes (`N`, `R`, `Y`, …) are tagged too. Lowercase bases are uppercased, and sequences displayed in space-separated blocks are joined. Text you have already wrapped in `<dna>…</dna>` is always tokenized as DNA. To turn off detection for a call, pass `auto_dna_tags=False` to the tokenizer or to `apply_chat_template()`.
- **Only A, C, G and T form 6-mers.** A 6-mer that contains any other character, such as `N`, becomes a single `<oov>` token. To keep full resolution, filter to canonical bases first.
- **Any length works.** When a sequence's length is not a multiple of 6, its last 1–5 bases are encoded with dedicated tail tokens, and decoding returns the original sequence exactly.
- **DNA in responses is tagged.** The model writes DNA inside `<dna>…</dna>`. Decoding with `skip_special_tokens=True` removes the tags.
- **First load needs Hub access.** On first load, the tokenizer downloads the pinned BPE vocabulary it builds on.

### Prompting tips

- Put the sequence and the question in the same user message.
- Include any context you already have, such as the gene, strand, coordinates or annotations. Without it, the model may guess at a sequence's identity or function.
- For variant questions, give the reference and alternate sequences, or a reference sequence with the position and the base change.

## Evaluation

We evaluate Carbon-X on five benchmarks that test whether a model can use DNA knowledge to predict mutation effects, identify biological features and recover DNA bases. Carbon-X results use thinking disabled, the default sampling settings and one sample per problem. Each comparison model is shown at the reasoning effort with its highest average score. The best score on each benchmark is in bold.

| Task | Benchmark | Carbon-X<br><sub>No reasoning</sub> | GPT-6.1 Sol<br><sub>Max</sub> | Opus 5.5<br><sub>Max</sub> | Gemini 4 Argon<br><sub>Low</sub> | GLM-5.3<br><sub>Low</sub> | Kimi K3<br><sub>Low</sub> | DeepSeek V4.1 Flash<br><sub>Low</sub> |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Coding variant effects | BioReason Coding | **74.9** | 71.0 | 70.3 | 6.5 | 52.1 | 53.9 | 51.4 |
| Complex variant effects | BioReason Non-SNV | 85.1 | 91.7 | **94.8** | 90.3 | 60.1 | 65.3 | 55.9 |
| DNA understanding | GenomeQA | 63.6 | **67.2** | 62.2 | 63.1 | 44.6 | 52.0 | 49.7 |
| DNA prediction | NextBase | **39.4** | 32.9 | 1.1 | 31.2 | 9.7 | 30.6 | 27.2 |
| Single-base variant effects | FrontierVEP | 70.4 | 74.9 | **76.2** | 58.0 | 32.7 | 50.1 | 51.3 |
| **Average** | | 66.7 | **67.5** | 60.9 | 49.8 | 39.8 | 50.4 | 47.1 |

BioReason and GenomeQA come from the literature; we built FrontierVEP and NextBase.

- **BioReason Coding** (1,018 variants, balanced accuracy): given original and mutated sequences from protein-coding regions, predict whether the mutation is benign or pathogenic. The gene name is removed from the prompt, so the model must work from the sequence alone.
- **BioReason Non-SNV** (873 variants, balanced accuracy): predict whether insertions, deletions and other changes that are not single-nucleotide variants are benign or pathogenic.
- **GenomeQA** (2,600 questions, accuracy averaged over six tasks): multiple-choice questions about sequence features: enhancer vs. promoter, splice sites, organism group, histone marks, transcription-factor binding sites and motifs.
- **NextBase** (600 sequences, per-base accuracy): predict the next DNA bases of a sequence. The score is the fraction of bases predicted correctly.
- **FrontierVEP** (1,000 variants, accuracy): given a long reference sequence (up to 8,192 bases) and a single-nucleotide variant, predict whether the variant is benign or pathogenic (500 benign, 500 pathogenic).

<details>
<summary>GenomeQA per-task accuracy</summary>

| Task | Accuracy |
|---|---:|
| Enhancer vs. promoter | 70.0 |
| Splice sites | 29.8 |
| Organism group (eukaryote classification) | 93.2 |
| Histone marks | 34.6 |
| Transcription-factor binding sites | 64.0 |
| Motifs | 90.0 |

</details>

Carbon-X also remains a capable assistant. On IFEval it reaches 79.3% prompt-level strict accuracy, or 84.0% averaged over the four IFEval metrics.

All training data was decontaminated against these benchmarks with three checks: whole-span DNA overlap, text n-gram overlap and sequence homology search.

## Training

### Model

- **Architecture:** decoder-only Transformer with 40 layers, hidden size 5120, FFN size 17408, 40 attention heads with GQA (8 KV heads), head dimension 128, SwiGLU, RMSNorm with QK-norm and untied embeddings.
- **Positional embedding:** RoPE with base θ = 1 × 10^6. The context window is 40,960 tokens.
- **Vocabulary:** 157,184 tokens: 151,669 BPE tokens and 5,515 DNA tokens, including padding to a multiple of 128.
- **Precision:** bfloat16.

### Vocabulary extension

Carbon-X starts from [Qwen3-14B](https://huggingface.co/Qwen/Qwen3-14B). We add the DNA tokens to its vocabulary with randomly initialised embeddings. We then train for about 10B tokens with the transformer body frozen, so that only the new DNA embeddings learn, before unfreezing the whole model.

### Mid-training

The full model is then trained for **302B tokens** with a sequence length of 8,192 and a global batch size of 512 sequences (≈ 4.2M tokens per step, 72,000 steps). The learning rate follows a cosine schedule from 5e-5 to 5e-6 after 2,000 warmup steps. In our ablations, learning rate was the dominant knob at this stage. A low learning rate preserves English but leaves DNA at chance, and a high one learns DNA but forgets English. The data mixture is:

| Component | Share |
|---|---:|
| **Raw DNA** | **55%** |
| Eukaryotic genomes: other vertebrates 16.2%, mammals 10.3%, invertebrates 7.6%, plants 3.1%, fungi 1.1%, protozoa 0.2% | 38.5% |
| mRNA (OpenGenome2) | 8.8% |
| Prokaryotic genomes (OpenGenome2) | 5.5% |
| mRNA with splice sites and promoters (OpenGenome2) | 2.2% |
| **English** | **20%** |
| FineWeb-Edu | 8% |
| DCLM | 8% |
| FinePDFs-Edu | 4% |
| **Biology text** | **15%** |
| Documents with long DNA sequences (FinePDFs, PubMed, bioRxiv, FineWeb, Biostars) | 7% |
| Other biology documents from the same sources | 7% |
| Synthetic descriptions of annotated RefSeq sequences | 1% |
| **Instruction data** | **10%** |
| Instruction following and chat | 10% |

### Supervised fine-tuning

The mid-trained model is fine-tuned for 3 epochs on a mixture of 20 dataset subsets. The learning rate follows a cosine schedule from 1e-5 to 1e-6, with a global batch size of 32, examples of up to 9,984 tokens and the loss computed on assistant tokens only. The mixture covers:

- Variant-effect reasoning over several views of the same variant (about 30% of the mixture)
- Sequence classification questions: enhancers vs. promoters, organism groups, transcription-factor motifs and binding sites
- Basic DNA operations, plus procedurally generated DNA skills such as counting, searching and editing sequences (about 10%)
- Sequence recovery and next-base prediction
- Biology questions from community Q&A forums
- Instruction-following, format-following and general chat data, which keep the model a good assistant

### Reinforcement learning

The fine-tuned model is then trained with asynchronous GRPO and verifiable rewards in two rounds, with thinking disabled:

1. One epoch over a blend of 98,433 prompts, selected from the fine-tuned model's own pass rates, for 150 steps.
2. A second round from that checkpoint, on 58,249 prompts re-selected from its rollouts. The released weights come after about 2.2 passes over the blend.

Both rounds use 16 samples per prompt, completions of up to 4,096 tokens, a learning rate of 1e-6, asymmetric clipping (ε = 0.2 / 0.28) and a maximum gradient norm of 0.2. Rewards are task-specific, code-checked verifiers. They cover variant-effect prediction (with a penalty for ungrounded rationales), sequence-feature classification, basic DNA operations, DNA skills, locating positions within sequences, biology multiple-choice questions and instruction following.

### Software

- **Post-training:** [TRL](https://github.com/huggingface/trl) for supervised fine-tuning and asynchronous GRPO, with [vLLM](https://github.com/vllm-project/vllm) for generation.

## Using Carbon-X as a tool for an agent

A general-purpose assistant can call Carbon-X when a question involves DNA. Serve Carbon-X with vLLM
([Serving with vLLM](#serving-with-vllm)) and wrap it in a function that takes a question and named DNA sequences:

```python
from openai import OpenAI

carbon = OpenAI(base_url="http://localhost:8000/v1", api_key="EMPTY")


def ask_carbon(question: str, sequences: dict[str, str], context: str = "") -> str:
    """Ask Carbon-X a question about one or more named DNA sequences."""
    parts = [context, question] if context else [question]
    parts += [f"{name}: <dna>{seq}</dna>" for name, seq in sequences.items()]
    response = carbon.chat.completions.create(
        model="HuggingFaceBio/Carbon-X",
        messages=[{"role": "user", "content": "\n\n".join(parts)}],
        max_tokens=4096,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    return response.choices[0].message.content
```

The snippet above is a core call. We provide the options below as examples, each one is self-contained, so pick the one that fits your agent:

- **Any agent with tool calling:** [`examples/agent_tool.py`](examples/agent_tool.py) describes `ask_carbon` as an
  OpenAI-style tool and runs a minimal agent loop. The agent refers to sequences by name and your code looks up the DNA,
  because language models often miscopy long sequences.
- **Inspect:** [`examples/inspect_agent.py`](examples/inspect_agent.py) wraps Carbon-X in an `@tool` for an
  [Inspect](https://inspect.aisi.org.uk/) `react()` agent, with each sample's DNA kept in metadata.
- **Coding agents (Claude Code, Codex CLI):** [`skills/carbon-dna/`](skills/carbon-dna/) is a ready-made skill. Its
  script needs only the Python standard library and has two commands: `ask` (a question about named DNA files) and
  `compare` (rank equal-length variants by sequence likelihood). You need a GPU with about 80 GB of memory and vLLM
  (`pip install -U vllm`); the skill itself needs no extra packages or API keys. Start Carbon-X, install the skill and
  point it at the server:

  ```bash
  vllm serve HuggingFaceBio/Carbon-X --trust-remote-code              # one 80 GB GPU
  hf download HuggingFaceBio/Carbon-X --include "skills/*" --local-dir carbon-x
  cp -r carbon-x/skills/carbon-dna ~/.claude/skills/                  # Codex CLI: ~/.codex/skills/
  export CARBON_BASE_URL=http://localhost:8000/v1                     # or the address of your vLLM server
  ```

  Carbon-X runs with vLLM or Transformers. llama.cpp and other GGUF runtimes are not supported: the DNA tokenizer is
  custom Python code that they cannot load, so DNA would be tokenized incorrectly.

### Tips for agent use

- **Pass DNA by reference.** Keep sequences in files or a lookup table and let the agent name them.
- **Ask for Carbon-X explicitly.** Agents often answer DNA questions on their own instead of calling a tool. If you want Carbon-X's view, say so in the system prompt.
- **Leave exact operations to code.** Counting, coordinates and edits are more reliable in Python than in either model. For variants, compute the position and the reference and alternate bases in code and pass them as `context`.
- **Cap generation length.** For next-base and continuation questions, set `max_tokens` to what you need and a client timeout, since long DNA generations are slow (see [Limitations](#limitations)).

## Limitations

Carbon-X is a starting point for research on connecting DNA and language. It has **not been clinically validated**, and it should not be used to make medical decisions.

- ⚠️ **Genetic data is highly sensitive.** Depending on how you use this model (local download, inference API or endpoints, third-party inference providers, Spaces demos or others), input and output data may be processed or handled differently by different providers or Space owners. Make sure you understand and agree with how your data is handled before using the model.
- **Exact sequence operations.** The model can miscount bases, confuse coordinates or make incorrect edits, particularly when it locates exact positions in long sequences. For tasks that need exact operations, pair it with tools that perform them.
- **Missing context and hallucinations.** The model may invent a sequence's identity or function when the prompt does not give enough information. Include known annotations and context where available, and verify its biological claims.
- **Uneven task coverage.** Splice-site and histone-mark questions remain weak (29.8% and 34.6% on GenomeQA). Carbon-X is also less reliable on short DNA exercises and textbook biology than on its core variant-effect and sequence-recovery tasks.
- **Long sequence generation.** Quality degrades when the model is asked to generate long DNA sequences. Carbon-X reads and explains existing sequences. It was not trained to design new ones.
- **Long inputs.** Mid-training used 8,192-token sequences (up to ≈ 49 kbp of DNA), and post-training examples were shorter than 16k tokens. The model accepts up to 40,960 tokens, but we have not evaluated inputs much longer than those seen in training.
- **Thinking mode.** The chat template supports `enable_thinking=True`, but Carbon-X was post-trained and evaluated with thinking disabled.

## License

Apache 2.0.

## Acknowledgements

Carbon-X is a joint collaboration between the research teams at [Hugging Face](https://huggingface.co), [Zhongguancun Academy](https://www.bza.edu.cn/en/), and the [Telethon Institute of Genetics and Medicine](https://www.tigem.it/)/[University of Naples “Federico II”](https://www.dicmapi.unina.it/).

<p align="center">
  <img src="figures/partner-logos.png" alt="Hugging Face, Zhongguancun Academy, Telethon Institute of Genetics and Medicine and University of Naples Federico II" width="800">
</p>
