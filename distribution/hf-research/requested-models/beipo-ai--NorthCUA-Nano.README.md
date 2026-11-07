---
license: apache-2.0
library_name: transformers
pipeline_tag: image-text-to-text
tags:
- northcua
- computer-use
- multimodal
- qwen3_5
- safetensors
---

<div align="center">
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://huggingface.co/beipo-ai/NorthCUA-Nano/resolve/main/northcua-logo-dark.png">
  <img src="northcua-logo.png" width="440" alt="NorthCUA — official mountain logo and wordmark">
</picture>

<h1 align="center">NorthCUA-Nano</h1>

<p align="center"><strong>9B total parameters · 9B active · Dense</strong></p>

**Computer-use agents trained through experience at scale**

<a href="https://beipo.top/blog/northcua/"><img src="https://img.shields.io/badge/Project-Homepage-004DBE?style=flat-square" alt="Project Homepage"></a>
<a href="https://github.com/china-qijizhifeng/NorthCUA-Harness"><img src="https://img.shields.io/badge/GitHub-Harness-222222?style=flat-square&logo=github" alt="GitHub Harness"></a>
<a href="https://huggingface.co/beipo-ai/NorthCUA-Pro"><img src="https://img.shields.io/badge/Hugging_Face-Models-FFCC4D?style=flat-square&logo=huggingface" alt="Hugging Face Models"></a>
<a href="https://huggingface.co/beipo-ai/NorthCUA-Nano/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-00877D?style=flat-square" alt="Apache 2.0 License"></a>

[Model family](#model-family) · [Performance](#performance) · [Training & report](#training-and-report) · [Quickstart](#quickstart) · [Citation](#citation)

</div>

---

> **Explore the NorthCUA model family**
>
> This is **NorthCUA-Nano**. Looking for another model size? Explore the other two members of our family:
>
> **[NorthCUA-Mini](https://huggingface.co/beipo-ai/NorthCUA-Mini)** — 35B total · 3B active · MoE
>
> **[NorthCUA-Pro](https://huggingface.co/beipo-ai/NorthCUA-Pro)** — 397B total · 17B active · MoE

## Meet NorthCUA-Nano

**NorthCUA-Nano is a 9B-parameter computer-use model with 9B active parameters**, built on Qwen3.5. As the family's compact dense model, it takes user requests, screenshots, and interaction history and generates reasoning, responses, and tool calls for a desktop or browser runtime.

> **Required for computer use:** Run NorthCUA with our official [NorthCUA Harness](https://github.com/china-qijizhifeng/NorthCUA-Harness). The model and Harness are designed to be used together for prompts, tool calls, observations, and action execution.
>
> **Strict coordinate contract — `normalized_1000`:** All GUI action coordinates use the **0–1000 normalized coordinate space**, not raw pixel coordinates. Keep this convention throughout the model–Harness interaction; the Harness converts coordinates to pixels at execution time.

NorthCUA connects **sustained interaction**, **verified experience**, and **staged learning** to carry tasks across changing software states—from understanding an instruction to producing and checking a result.

- **See and act.** Visual grounding and multimodal observations support desktop applications and web workflows.
- **Learn from complete work.** Long trajectories teach dependent steps, intermediate artifacts, and outcome verification.
- **Build on open weights.** The Nano, Mini, and Pro family spans **9B / 35B / 397B** total parameters, released under **Apache-2.0**.

## Model family

| Model | Total parameters | Active parameters | Architecture |
|:--|--:|--:|:--|
| [**NorthCUA-Nano**](https://huggingface.co/beipo-ai/NorthCUA-Nano) | **9B** | 9B | Dense |
| [NorthCUA-Mini](https://huggingface.co/beipo-ai/NorthCUA-Mini) | **35B** | 3B | Mixture of experts |
| [NorthCUA-Pro](https://huggingface.co/beipo-ai/NorthCUA-Pro) | **397B** | 17B | Mixture of experts |

<sub>Nominal parameter counts follow the technical report. MoE active parameters describe computation per token, not the memory needed to load all weights.</sub>

## Performance

<img src="northcua-report-results.png" width="100%" alt="Report RL success rates: Nano 78.90 OSWorld and 67.16 WebArena; Mini 85.30 and 70.43; Pro 88.64 and 73.89.">

**Results reported in the NorthCUA technical report.** Grounding uses SFT checkpoints; task completion uses subsequent joint desktop–web RL checkpoints. These research results have not been tied to an exact checkpoint975 export and are not new measurements of the files in this repository.

| Benchmark | Stage / metric | Nano · 9B | Mini · 35B | Pro · 397B |
|:--|:--|--:|--:|--:|
| ScreenSpot-Pro | SFT · accuracy (%) | 72.87 | 92.41 | **93.30** |
| OSWorld-G | SFT · accuracy (%) | 82.98 | 87.94 | **90.96** |
| OSWorld-Verified | RL · success (%) | 78.90 | 85.30 | **88.64** |
| WebArena-Verified | RL · success (%) | 67.16 | 70.43 | **73.89** |

<details>
<summary><b>Evaluation settings, SFT → RL comparisons, and additional results</b></summary>

**Sources:** Table 6 and Tables 12–13 of the 121-page report. ScreenSpot-Pro uses 1,581 examples, one prediction per screenshot, and no iterative zoom/crop search. OSWorld-G uses 564 refined instructions and the **best of three complete runs**, not a mean or itemwise pass@3 (temperature 0.7, top-p 0.95, top-k 40, 1,024 output tokens, normalized 0–1000 coordinates).

| Model | OSWorld SFT | OSWorld RL | WebArena SFT | WebArena RL |
|:--|--:|--:|--:|--:|
| Nano | 76.05 | 78.90 | 64.16 | 67.16 |
| Mini | 83.29 | 85.30 | 67.98 | 70.43 |
| Pro | 85.87 | 88.64 | 69.58 | 73.89 |

OSWorld-Verified uses 361 tasks, excluding eight Google Drive tasks requiring separate account configuration. WebArena-Verified uses 812 tasks and official outcome checks. Both have a **100-step task limit**. Retained-attempt selection and other protocol details are in §6.1 and Appendix I; compare results with those conditions in view.

The report also lists **66.80% mean partial credit** for Pro after RL on OSWorld 2.0 (108 tasks, 500-step limit). This is not a full-completion rate; Nano and Mini results are unavailable in that table. The detailed WebTestBench table marks NorthCUA results as pending, so no score is asserted here.

</details>

## Training and report

<p align="center"><img src="northcua-experience-loop.png" width="760" alt="Figure 2 from the NorthCUA technical report: Harness, Experience Flywheel and Staged Learning form the experience loop."></p>

**The NorthCUA experience loop** — original Figure 2 from the technical report.

1. **Harness:** a common runtime sustains desktop and web interaction, collects trajectories, and retains workspaces for outcome inspection. Visual context, compaction, and task memory support long workflows.
2. **Experience Flywheel:** intents, environments, and verifiers become executable tasks. Assessed trajectories provide training experience; the report also proposes skill-guided and checkpoint-assisted collection for hard tasks.
3. **Staged Learning:** broad GUI mid-training, verified long-horizon SFT, and joint desktop–web GRPO turn complementary experience into an improved policy.

<details>
<summary><b>Training scale and figure notes</b></summary>

| Stage described in the report | Experience and scale |
|:--|:--|
| Broad GUI mid-training | More than 100,000 trajectories and 4 million interaction steps; approximately 40,000 grounding examples |
| Verified long-horizon SFT | Approximately 3,000 verified trajectories and 310,000 GUI steps |
| Joint desktop–web RL | A prepared pool of approximately 1,500 desktop tasks and 500 web tasks |

These are rounded corpus/prepared-pool summaries from Table 3 and §5.1, not per-model consumption counts. The research pipeline contains multiple checkpoints and stages.

**Figure 2 notes:** GUI states, cursor cues, and rewards are schematic. Trajectory rows omit intermediate actions; GRPO retains valid failures alongside successes. The hard-task panel denotes the proposed skill-guided and checkpoint-assisted collection in §4.3. Reusing experience during training does not mean the serving model updates its weights while handling a request.

</details>

## Quickstart

### Required runtime and coordinate convention

**Use these weights together with the official [NorthCUA Harness](https://github.com/china-qijizhifeng/NorthCUA-Harness)** for computer-use tasks. Follow its prompts, tool definitions, observation processing, and action execution rather than substituting an unrelated agent interface.

**Coordinates are strictly normalized to 1000 on both axes:** `x` and `y` each lie in **[0, 1000]**, relative to the corresponding screenshot. For example, `(500, 500)` denotes the screenshot's center. This contract applies to every coordinate-bearing GUI action, including click targets and drag endpoints. Preserve `normalized_1000` in model outputs and tool arguments, and let the official Harness perform the pixel conversion at the execution boundary. Do not pass these values directly as screen-pixel coordinates or mix coordinate systems between steps.

### Download the weights

```bash
pip install -U huggingface_hub
hf download beipo-ai/NorthCUA-Nano --local-dir ./NorthCUA-Nano
cd NorthCUA-Nano
shasum -a 256 -c checksums.sha256
```

Pin a commit with `--revision` for reproducible use. The repository contains full Safetensors weights, configuration, tokenizer, image processor, and native chat template. The official [NorthCUA Harness](https://github.com/china-qijizhifeng/NorthCUA-Harness) supplies the computer-use interaction loop and action execution and is required for the intended agent workflow.

<details>
<summary><b>Python: load the model and generate from a screenshot</b></summary>

Use PyTorch, Accelerate, Pillow, and a Transformers release that supports `Qwen3_5ForConditionalGeneration`. The export configuration records Transformers 5.20.2. The following is an illustrative loading and generation example for a machine with sufficient aggregate accelerator memory; it is not a benchmarked serving configuration.

```python
import torch
from transformers import AutoProcessor, Qwen3_5ForConditionalGeneration

model_id = "beipo-ai/NorthCUA-Nano"
processor = AutoProcessor.from_pretrained(model_id)
model = Qwen3_5ForConditionalGeneration.from_pretrained(
    model_id, dtype=torch.bfloat16, device_map="auto"
)

messages = [{
    "role": "user",
    "content": [
        {"type": "image", "url": "/absolute/path/to/screenshot.png"},
        {"type": "text", "text": "Describe the visible interface and how to open its settings."},
    ],
}]
inputs = processor.apply_chat_template(
    messages,
    tokenize=True,
    add_generation_prompt=True,
    return_dict=True,
    return_tensors="pt",
).to(model.device)
with torch.inference_mode():
    output = model.generate(**inputs, max_new_tokens=512, do_sample=False)
new_tokens = output[0][inputs["input_ids"].shape[-1]:]
print(processor.decode(new_tokens, skip_special_tokens=True))
```

This standalone example demonstrates image-text generation only. For computer-use tasks, use the official [NorthCUA Harness](https://github.com/china-qijizhifeng/NorthCUA-Harness), which supplies the interaction loop, tool execution, and updated observations. Preserve native tool-call/result pairing and the strict `normalized_1000` coordinate contract; pixel conversion belongs at the Harness execution boundary.

</details>

<details>
<summary><b>Release specifications and serving integration</b></summary>

| This release | Value |
|---|---|
| Model | NorthCUA-Nano |
| Architecture family | Qwen3.5-9B |
| Transformers class | `Qwen3_5ForConditionalGeneration` |
| Language-model layers | 32 |
| Format | Full Safetensors weights; predominantly BF16 |
| Weight shards | 4 |
| Model files on disk | Approximately 18.84 GB, before runtime caches |
| Configured context length | 262,144 tokens |
| Release | r13 post-training checkpoint 975 |
| License | Apache-2.0 |

The repository includes configuration, tokenizer, image processor, and native chat template. Use the official NorthCUA Harness for desktop or browser interaction, tool handling, and action execution. The configured context length is not a measured guarantee of task quality at that length. These exports do not include multi-token-prediction (MTP) weights.

The export records Transformers **5.20.2**. Choose an engine that supports the architecture, vision processing, native chat template, and reasoning/tool-call format. The [upstream Qwen3.5 guide](https://huggingface.co/Qwen/Qwen3.5-35B-A3B#quickstart) is an architecture reference, not serving validation for these fine-tuned weights. Check text, image, multi-image, tool use, and streaming in your deployment. Allow additional memory for activations and KV caches; active-parameter counts are not weight-memory estimates.

`checksums.sha256` covers its listed files. Documentation and image assets may evolve independently of the model weights.

</details>

## Responsible use and limitations

NorthCUA is intended for research and development of computer-use agents. Verify consequential actions and final artifacts, isolate the execution environment, and protect credentials from observed content.

<details>
<summary><b>Read the model and evaluation limitations</b></summary>

NorthCUA supports research and development of computer-use agents, including interface understanding, application workflows, and browser-based tasks. Its behavior depends on the supplied observations, prompts, tools, and execution environment.

- Generated actions and claimed outcomes can be incorrect. Validate important state changes and final artifacts independently.
- Long workflows and cross-site coordination remain challenging. Preserve intermediate work and provide explicit recovery and stopping behavior.
- Screenshots, documents, and pages can contain misleading instructions. Keep user intent and tool permissions separate from observed content, and protect credentials in the runtime.
- The report documents a grounding example with a training-seen control in a changed surrounding GUI state (§6.2, Figure 26). The reported scores should not be interpreted as proof that every evaluated control was unseen during training.
- The research report also explores NorthCUA-Ultrafast. These repositories do not establish that its separate direct-action-selection mechanism or latency results apply to the released weights.

The release's file-size, SHA-256, tensor/index, tokenizer, and template checks establish artifact consistency. They do not establish end-to-end task accuracy or universal inference-engine compatibility.

</details>

## License

The model weights and repository documentation are released under [Apache-2.0](https://huggingface.co/beipo-ai/NorthCUA-Nano/blob/main/LICENSE). See [NOTICE](https://huggingface.co/beipo-ai/NorthCUA-Nano/blob/main/NOTICE) for attribution. We thank the Qwen team and the open-source research, training, and inference communities. NorthCUA is an independent model release.

## Citation

If you use NorthCUA in your research, please cite our [technical report (arXiv:2611.02102v1)](https://arxiv.org/pdf/2611.02102v1):

```bibtex
@misc{zhang2026northcua,
  title = {NorthCUA Technical Report: Turbocharging Computer-Use Agents through Experience at Scale},
  author = {Chaoyun Zhang and Bingxuan Li and Pinxue Guo and Yangfan Gao and Zikun Cai and Tianci Xie and Hui Li and Yuqi Huang and Yifei Cao and Bo Mao and Tian Tang and Qichang Zheng and Tongtong Liu and Xiaoxing Wang and Jinxiu Liu and Lizhi Lin and Zhou Shao and Maosen Zhou and Miao Zheng and Shuo Zhang and Yangji He and Kepeng Lei and Ning Zhao and Wei Shen and Zhenjiang Jin and Xinyue Zhang and Peng Sun and Zhenhua Han and Rui Zheng and Hang Yan},
  year = {2026},
  eprint = {2611.02102},
  archivePrefix = {arXiv},
  primaryClass = {cs.LG},
  url = {https://arxiv.org/pdf/2611.02102v1}
}
```

<p align="center"><a href="https://beipo.top/blog/northcua/">Project homepage</a> · <a href="https://github.com/china-qijizhifeng/NorthCUA-Harness">GitHub</a> · <a href="https://huggingface.co/beipo-ai/NorthCUA-Nano/discussions">Community</a></p>
