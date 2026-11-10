---
base_model: Qwen/Qwen3-VL-4B-Instruct
library_name: transformers
pipeline_tag: image-text-to-text
tags:
- reinforcement-learning
- grpo
- visual-agent
- mobile-gui
---

# MobileGym-4B

This checkpoint is a reproduction of MobileGym-style GRPO training starting from [Qwen3-VL-4B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct). It is an independently trained reproduction, not an official MobileGym release.

## Training

The run used the MobileGym training split (160 tasks), three NVIDIA H200 GPUs, 96 parallel environments, batch size 12, group size 8, 10 GRPO updates, a learning rate of 1e-6, and an actor KL coefficient of 0.01. It used the repository training flow's loop threshold of 8 and its 10-step allowance for answer sheets. The actor KL coefficient was set explicitly to the paper's value; the repository launcher otherwise inherits an actor default of 0.001.

The paper specifies a 15-step answer-sheet allowance for benchmark evaluation, but does not establish whether that allowance also applied during training. The checkpoint follows the repository training code for this setting, so exact reproduction of the authors' training run cannot be established. The training driver was interrupted after saving step 10 and completing its built-in validation because it stalled while cancelling asynchronous tasks. The built-in validation encountered gateway read errors; the scores below come from separate evaluation runs.

## Evaluation

Four runs covered all 256 tasks in the test split. They used grounded mode, state-based judging, temperature 0.1, top-p 0.95, a maximum of 4096 output tokens, a loop threshold of 10, an action delay of 0.8 seconds, and the benchmark's 15-step answer-sheet allowance. SR is the fraction of tasks completed successfully; PR is mean task progress.

| Sampling seed | SR | PR |
| --- | ---: | ---: |
| 42 | 21.09% | 36.79% |
| 43 | 19.14% | 33.27% |
| 44 | 17.58% | 33.82% |
| 45 | 19.53% | 33.52% |
| Mean | **19.34%** | **34.35%** |

The SR population standard deviation across runs was 1.25 percentage points. Three completed episodes each produced a duplicate fallback error after their result had already been recorded, due to a progress-display write failure. The metrics use the recorded completed results; all four runs were audited for 256 distinct test tasks and unresolved errors.

## Loading

The directory contains a full Hugging Face Transformers checkpoint, tokenizer, processor, and chat template. A compatible vLLM installation can serve it with:

```bash
vllm serve ./MobileGym-4B --served-model-name mobilegym-4b --max-model-len 40960
```

These scores require the MobileGym environment, its agent prompts and action protocol, and its evaluation configuration; loading the checkpoint alone does not reproduce the benchmark.

## Source and licensing information

- Base model: [Qwen/Qwen3-VL-4B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct), whose model card declares Apache-2.0.
- Training and evaluation platform: [MobileGym paper](https://arxiv.org/abs/2605.26114), repository revision `5fbd3f34be0e9780ae4b1e9d5089963ddbd05d38`.
- The MobileGym repository's data license describes a CC BY-NC 4.0 license for its dataset compilation and separate conditions for third-party content. The license for this independently produced checkpoint has not been designated in this model card.
