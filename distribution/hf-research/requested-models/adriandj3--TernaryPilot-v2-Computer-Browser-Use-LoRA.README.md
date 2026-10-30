---
license: apache-2.0
base_model: prism-ml/Ternary-Bonsai-2-9B-gguf
base_model_relation: adapter
library_name: gguf
pipeline_tag: image-text-to-text
language:
- en
datasets:
- vyokky/GUI-360
- xlangai/AgentNet
- osunlp/Multimodal-Mind2Web
tags:
- computer-use
- browser-use
- gui-agent
- web-agent
- gui-grounding
- screen-understanding
- visual-grounding
- agent
- lora
- gguf
- llama.cpp
- ternary
- 1.58-bit
- bonsai
- qwen3.5
- screenspot
- screenspot-pro
- gui-360
- windows
- macos
- local-ai
---

<div align="center">

# TernaryPilot v2 · computer & browser use for Ternary-Bonsai-2-9B

**A LoRA that teaches the 9B ternary Bonsai (2.7 GB) to click, act and read on Windows, macOS and the web.**
Screenshot in, a point or the next action out. Trained from scratch on 60,516 examples from GUI-360, AgentNet and Mind2Web.

[![latest](https://img.shields.io/badge/version-v2_latest-brightgreen)](#)
[![llama.cpp](https://img.shields.io/badge/llama.cpp-GGUF_F16_LoRA-2b6cb0)](#how-to-use-it)
[![ScreenSpot-v2 desktop+web](https://img.shields.io/badge/ScreenSpot--v2_desktop+web-37.0→71.5%25-0f766e)](#results-at-a-glance)
[![GUI-360 grounding](https://img.shields.io/badge/GUI--360_grounding-16.9→66.4%25-0f766e)](#results-at-a-glance)
[![GUI-360 actions](https://img.shields.io/badge/GUI--360_actions-0.5→42.2%25-0f766e)](#results-at-a-glance)
[![data](https://img.shields.io/badge/data-60.5k_examples_·_32k_screenshots-6b46c1)](#how-i-trained-it)
[![trained on](https://img.shields.io/badge/trained_on-RTX_PRO_6000_Blackwell-76b900)](#how-i-trained-it)
[![GitHub](https://img.shields.io/badge/GitHub-ArtyomITA-181717?logo=github)](https://github.com/ArtyomITA)

By **Adrian Ciprian Iancu** · [GitHub](https://github.com/ArtyomITA) · [Hugging Face](https://huggingface.co/adriandj3) · [LinkedIn](https://www.linkedin.com/in/adrian-ciprian-iancu-a2a477391)

</div>

> **Updated October 2026: this is the current version.** Not a standalone model: it is an adapter for
> [prism-ml/Ternary-Bonsai-2-9B-gguf](https://huggingface.co/prism-ml/Ternary-Bonsai-2-9B-gguf), loaded at scale 0 and
> switched on per request, so normal chat stays exactly the base model.

## Results at a glance

Every case is asked to the base model (adapter at scale 0) and to v2 on the same llama.cpp server. Greedy decoding,
images at 1024 tokens, `Ternary-Bonsai-2-9B-PQ2_0-MTP-Q8_0.gguf` with the Q8_0 vision projector.

<p align="center"><img src="https://huggingface.co/adriandj3/TernaryPilot-v2-Computer-Browser-Use-LoRA/resolve/main/assets/bench.png" alt="Base model and TernaryPilot v2 on four public GUI benchmarks" width="100%"></p>

| benchmark | cases | base model | TernaryPilot v2 | gain |
|---|---:|---:|---:|---:|
| ScreenSpot-v2 | 1,272 | 46.5% | **60.7%** | +14.2 |
| ScreenSpot-v2, desktop + web only | 771 | 37.0% | **71.5%** | +34.5 |
| ScreenSpot-Pro | 423 | 1.7% | **11.1%** | +9.4 |
| GUI-360 grounding | 2,000 | 16.9% | **66.4%** | +49.5 |
| GUI-360 actions | 1,192 | 0.5% | **42.2%** | +41.7 |

The machine had a fixed stop time, so some runs were cut before the end. ScreenSpot-Pro ran the first 423 of 1,581 cases in the benchmark's file order, which goes application by application, so later applications are missing (not a random sample); GUI-360 actions ran 1,192 of its 2,000 sampled cases, asked in random order, so they are a random subset.

## Pick a file

| file | for | size | what it is |
|---|---|---:|---|
| `TernaryPilot-v2-Ternary-Bonsai-2-9B-LoRA-F16.gguf` | llama.cpp (PrismML fork) `--lora` | 321 MB | rank 64, alpha 128, F16 |
| `eval/` | checking my numbers | small | summaries and every answer of the base model and v2 on each benchmark |
| `training/` | rebuilding it | small | data builder, trainer (Blackwell dense mode), benchmark runner |

## Results

**ScreenSpot-v2** by platform and target type:

| platform / type | cases | base | v2 |
|---|---:|---:|---:|
| mobile-text | 290 | **68.3%** | 52.8% |
| web-text | 234 | 40.6% | **82.9%** |
| mobile-icon | 211 | **51.7%** | 32.2% |
| web-icon | 203 | 30.5% | **66.5%** |
| desktop-text | 194 | 39.2% | **73.7%** |
| desktop-icon | 140 | 37.1% | **56.4%** |

**ScreenSpot-Pro** (professional apps on high-resolution screens) by application group:

| application group | cases | base | v2 |
|---|---:|---:|---:|
| Creative | 195 | 1.0% | **9.7%** |
| Dev | 80 | 1.2% | **7.5%** |
| Office | 64 | 1.6% | **14.1%** |
| Scientific | 50 | 6.0% | **26.0%** |
| CAD | 34 | **0.0%** | **0.0%** |

and by target type:

| target | cases | base | v2 |
|---|---:|---:|---:|
| text | 269 | 2.2% | **14.9%** |
| icon | 154 | 0.6% | **4.5%** |

**GUI-360 grounding** (click the control the agent's thought refers to) by app:

| app | cases | base | v2 |
|---|---:|---:|---:|
| word | 848 | 24.4% | **71.0%** |
| excel | 620 | 9.7% | **63.2%** |
| ppt | 532 | 13.2% | **62.8%** |

**GUI-360 actions** (task + history + screen: right action type and, for clicks and typing, the point inside the control) by action and by app:

| action | cases | base | v2 |
|---|---:|---:|---:|
| click | 927 | 0.0% | **43.4%** |
| type | 243 | 0.0% | **37.9%** |
| wheel_mouse_input | 20 | 30.0% | **45.0%** |
| drag | 2 | **0.0%** | **0.0%** |

| app | cases | base | v2 |
|---|---:|---:|---:|
| word | 491 | 0.2% | **44.2%** |
| excel | 371 | 0.3% | **38.8%** |
| ppt | 330 | 1.2% | **43.0%** |

## How to use it

You need the [PrismML fork of llama.cpp](https://github.com/PrismML-Eng/llama.cpp) (ternary kernels), the base model and
the vision projector from [prism-ml/Ternary-Bonsai-2-9B-gguf](https://huggingface.co/prism-ml/Ternary-Bonsai-2-9B-gguf),
and the adapter from this repo. I tested `Ternary-Bonsai-2-9B-PQ2_0-MTP-Q8_0.gguf` with
`Ternary-Bonsai-2-9B-mmproj-Q8_0.gguf`.

```bash
llama-server -m Ternary-Bonsai-2-9B-PQ2_0-MTP-Q8_0.gguf \
  --mmproj Ternary-Bonsai-2-9B-mmproj-Q8_0.gguf \
  --lora-scaled TernaryPilot-v2-Ternary-Bonsai-2-9B-LoRA-F16.gguf:0 \
  --image-max-tokens 1024 -ngl 999 -fa on --jinja -c 16384 --parallel 2
```

Turn it on for GUI requests only, with `"lora": [{"id": 0, "scale": 1}]` in the request body, `"temperature": 0` and
`"chat_template_kwargs": {"enable_thinking": false}`. The scale set by a request stays on its slot, so give GUI requests
their own slot (`"id_slot": 1`) or send `"scale": 0` on every chat request. On Windows `--lora-scaled` splits on `:`:
start the server from the adapter's folder and pass the file name only.

**Click** (image first, then this text, the target at the end):

```text
Localize an element on the GUI image according to the provided target and output a click position.
 * You must output a valid JSON following the format: {"x": int, "y": int}, with x and y normalized to 0-1000
 Your target is:
save the document
```

**Next action:**

```text
You are operating a Windows computer.
Task: Open Notepad and write a shopping list with milk and bread
Previous actions:
1. Click on the Start button
2. Type "notepad" in the search box
Look at the current screen and give the next action. Coordinates are integers normalized to 0-1000.
Available actions: click(x, y), double_click(x, y), right_click(x, y), move(x, y), drag(x, y), write(text), press(key), hotkey(keys), scroll(amount), wait(), done()
Reply as:
Thought: <short reasoning>
Action: <one action>
```

Use `You are operating a Mac computer.` or `You are operating a web browser.` as the first line where it fits.
**Screen reading:** `Describe the current screen: the application or website, its state, and the important visible text.`
Coordinates are on a 0-1000 grid over the image you send.

## How I trained it

- **Model:** Ternary-Bonsai-2-9B rebuilt exactly in PyTorch from the PQ2_0 GGUF (2-bit codes plus the Hadamard rotation of
  the PrismML format), weights frozen, LoRA on the attention, GatedDeltaNet and MLP projections of all 32 layers. Exported
  in the GGUF LoRA format the fork reads with `--lora`.
- **Run:** rank 64, alpha 128, AdamW lr 1e-4, warmup, constant, final decay over the last 15%, gradient accumulation 8,
  2.5 hours of training, about 21,000 examples seen once (35% of the dataset). Fresh start from random LoRA weights.
- **Speed on one RTX PRO 6000 Blackwell:** frozen ternary weights unpacked once to BF16 (the 1080 kit unpacks them at
  every use), vision encoded on the fly and shared by all examples of the same screenshot, gradient checkpointing only
  above 3,072 tokens. About 0.4 s per example, against 6 to 9 s with the same kit on a GTX 1080.
- **Data (60,516 examples on 32,001 screenshots, English prompts):**

| part | examples | source | license |
|---|---:|---|---|
| Office actions with history (Word, Excel, PowerPoint) | 15,000 | [GUI-360](https://huggingface.co/datasets/vyokky/GUI-360), action prediction joined to grounding by screenshot | MIT |
| Office clicks from the agent's own thought | 15,000 | GUI-360 grounding | MIT |
| Windows and macOS actions with history | 10,000 | [AgentNet](https://huggingface.co/datasets/xlangai/AgentNet), completed tasks with alignment score 8 or more | MIT |
| Windows and macOS clicks from the same steps | 4,921 | AgentNet | MIT |
| Screen reading | 2,426 | AgentNet observations | MIT |
| Web actions with history | 7,227 | [Multimodal-Mind2Web](https://huggingface.co/datasets/osunlp/Multimodal-Mind2Web) train, page cropped to 1280x800 around the target | OpenRAIL |
| Web clicks from the same steps | 5,942 | Multimodal-Mind2Web | OpenRAIL |

  Every training screenshot was compared by perceptual hash (16x16 dHash, Hamming distance 12 or less) with the 2,308
  ScreenSpot-v2 and ScreenSpot-Pro images, and near copies were dropped. GUI-360 test is a separate official split.

## How much to trust this

- Every number is a full public benchmark or a random sample of one, run on the same server for the base model and v2, greedy. A hit is a point inside the target box, with no tolerance.
- ScreenSpot-v2 and ScreenSpot-Pro screenshots were filtered out of the training data by perceptual hash; GUI-360 test is a separate official split of the same dataset as part of the training data, so it is in-domain for v2.
- One run per benchmark, so expect a point or two of noise on the smaller groups.

## Limitations

- **No safety layer.** It predicts actions, it does not judge them. Keep a human in the loop for anything that deletes,
  buys or sends.
- **No phone screens in the data.** On ScreenSpot-v2 mobile text targets it is below the base model; use it for desktop and web.
- **Short element names** ("the Save button") are its weakest kind of click target: most of its click data are agent thoughts and action descriptions, which is where the GUI-360 numbers come from.
- **English prompts.** Screens in other languages work but were not tuned for.
- **Professional apps are still hard** (ScreenSpot-Pro: high-resolution screens with tiny targets).
- **Speed:** with the adapter active, decoding is slower than the bare base model; measure it on your card.
- **Responsible use:** part of the data comes from Multimodal-Mind2Web under an OpenRAIL license; please use the adapter
  in line with its terms.

## License and credits

Adapter weights: Apache-2.0. Base model: [prism-ml/Ternary-Bonsai-2-9B-gguf](https://huggingface.co/prism-ml/Ternary-Bonsai-2-9B-gguf)
(Apache-2.0), from Qwen3.5-9B. Data: GUI-360 (MIT), AgentNet (MIT), Multimodal-Mind2Web (OpenRAIL). Benchmarks:
ScreenSpot-v2 (OS-Copilot), ScreenSpot-Pro, GUI-360 test. Not affiliated with PrismML or Qwen.

## Citation

```bibtex
@misc{iancu2026ternarypilot2,
  author       = {Iancu, Adrian Ciprian},
  title        = {TernaryPilot v2: a computer use and browser use LoRA for Ternary-Bonsai-2-9B},
  year         = {2026},
  publisher    = {Hugging Face},
  howpublished = {\url{https://huggingface.co/adriandj3/TernaryPilot-v2-Computer-Browser-Use-LoRA}}
}
```

Its sibling for decisions: [TernaryDecider v1](https://huggingface.co/adriandj3/TernaryDecider-v1-Decision-LoRA),
one-token calibrated choices for the same base model. If you try it, open a discussion here or find me on
[GitHub](https://github.com/ArtyomITA).

Keywords: computer use, browser use, GUI agent, web agent, GUI grounding, screen understanding, LoRA, GGUF, llama.cpp,
ternary, 1.58-bit, Bonsai, Qwen3.5, ScreenSpot, ScreenSpot-Pro, GUI-360, AgentNet, Mind2Web, Windows, macOS, local AI
