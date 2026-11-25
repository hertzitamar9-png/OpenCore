---
license: apache-2.0
base_model: Qwen/Qwen3-VL-2B-Instruct
library_name: transformers
pipeline_tag: image-text-to-text
language:
  - en
tags:
  - computer-use
  - gui-agent
  - gui-grounding
  - system-1
  - qwen3-vl
---

# deskpaw-CUA-2B

A **fully fine-tuned** 2B vision-language model for computer-use GUI interaction.

It is a **System-1** model: given a screenshot and a natural-language instruction, it
returns the target click location in a **single forward pass** — no reasoning chain, no
test-time search, no iterative refinement.

## Model details

| | |
|---|---|
| Base model | Qwen3-VL-2B-Instruct (2.13B) |
| Parameters | 2.13B, every weight updated (full fine-tune, not an adapter) |
| Precision | bf16 |
| Max input | screenshots up to ~5.9M pixels |
| Released checkpoint | step 20,000 |

## Evaluation

Benchmark: **ScreenSpot-v2** official test split (1,272 samples), single-step, top-1
point must fall inside the ground-truth bounding box.

| Split | Samples | Accuracy |
|---|---|---|
| Overall | 1,272 | **87.97%** |
| desktop | 306 | 88.56% |
| mobile | 449 | 90.87% |
| web | 517 | 85.11% |
| text elements | 718 | 95.0% |
| icon elements | 554 | 78.9% |

**Best validated score of this training run: 87.97%** on ScreenSpot-v2 (1,272 samples).

> Scope note: this figure was obtained on an intermediate checkpoint of the same run
> (step 14,000). The released weights (step 20,000) were **not** re-evaluated before
> publication. No claim of state-of-the-art is made — this is a single benchmark,
> measured with a single protocol.

## Training status — please read before use

Training was **stopped early**. The released checkpoint is step 20,000 of a planned
31,848-step schedule, i.e. **about 63% of one epoch (~63% of the training data seen
once)**. The remaining schedule was not executed. The model has therefore *not* been
trained over its full data budget.

## Usage

Custom prediction head — the modeling code is **included in this repository**. Load with
`trust_remote_code=True`:

```python
import torch
from transformers import AutoProcessor, AutoModelForImageTextToText

repo = "llstar/deskpaw-CUA-2B"
processor = AutoProcessor.from_pretrained(repo)
model = AutoModelForImageTextToText.from_pretrained(
    repo, trust_remote_code=True, torch_dtype=torch.bfloat16
).eval().cuda()

messages = [
    {"role": "system", "content": [{"type": "text", "text":
        "You are a GUI agent. Given a screenshot and an instruction, locate the screen "
        "element and output a click position."}]},
    {"role": "user", "content": [{"type": "image", "image": "screenshot.png"},
                                 {"type": "text", "text": "Close this window"}]},
]
inputs = processor.apply_chat_template(messages, add_generation_prompt=True,
                                       tokenize=True, return_dict=True,
                                       return_tensors="pt").to(model.device)
out = model(**inputs)
```

Note: plain `AutoModelForCausalLM` / `Qwen3VLForConditionalGeneration` will **not** work.
`trust_remote_code=True` is required; review the included `.py` files before use.

## Limitations

- **Not safety-evaluated.** No red-teaming, no prompt-injection testing, no destructive
  action testing. Do not deploy against a live desktop without a sandbox and human
  supervision.
- Trained on ~63% of its planned schedule (see above).
- Evaluated on one benchmark only; no claim of state-of-the-art.
- Instruction following is strongest in English.
- Fails more often on **icon-only targets** (78.9%) than on text targets (95.0%).

## License and attribution

- Released under **Apache-2.0**, inherited from the base model.
- Base model: [Qwen3-VL-2B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct) (Apache-2.0).
- The custom modeling code included here is derived from a publicly released open-source
  GUI grounding implementation.
- This is a fine-tune; the base model's licence obligations (attribution, change notice)
  apply to this release.

## Intended use

Research on GUI agents and screen-element grounding. Not intended for unattended
operation of real systems, and not evaluated for commercial deployment.
