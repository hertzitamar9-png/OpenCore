---
license: apache-2.0
base_model: mPLUG/GUI-Owl-1.5-2B-Instruct
base_model_relation: adapter
library_name: peft
language:
- en
tags:
- gui-agent
- android
- lora
- fixed-slot
- safetensors
---
# GUI-Swift-Mobile-2B

![GUI-Swift-Mobile-2B — One forward. One mobile action.](assets/hero.svg)

<p align="center"><strong>Single-Pass Mobile GUI Action Prediction</strong><br />
<a href="#results">Results</a> · <a href="#real-validation-examples">Examples</a> · <a href="#quick-start">Quick start</a> · <a href="README_zh.md">简体中文</a></p>

**GUI-Swift-Mobile-2B** turns a screenshot, the current step instruction and executed-action history into one structured Android action through **one native causal forward over 105 masked value slots**. Built on [GUI-Owl-1.5-2B-Instruct](https://huggingface.co/mPLUG/GUI-Owl-1.5-2B-Instruct), it continues the [GUI-Swift-4B](https://huggingface.co/dongjintao/GUI-Swift-4B) family with an Android-focused protocol.

**Release:** AndroidControl SFT · two epochs · step4658
**Download:** unchanged PEFT LoRA adapter · 433.68M adapter parameters · 1.73 GB
**Protocol:** 105 slots · 256 coordinate bins per axis · 16K context

![Screenshot to fixed-slot action](assets/workflow.svg)

The base model is downloaded separately. Use the included native inference runner: ordinary autoregressive `generate()` does not implement this trained protocol. Single-pass describes the mechanism; no measured speedup or live task-success advantage is claimed.

## Results

![Held-out results and text limitation](assets/results.svg)

| Saved evaluation | Result | Scope |
|---|---:|---|
| ScreenSpot-v2 · full mobile subset | **69.66%** · 349 / 501 | Predicted tap falls inside the original target box |
| AndroidControl test · action type | **95.89%** · 8,077 / 8,423 | Action type agrees with reference |
| AndroidControl test · tolerance action match | **72.01%** · 6,065 / 8,423 | Local full-action metric described below |
| AndroidControl test · strict full action | **28.94%** · 2,438 / 8,423 | Canonical action and all parameters exactly equal |
| AndroidControl test · text exact match | **19.62%** · 124 / 632 | Exact `type_text` action and text |
| AndroidControl test · invalid decoded outputs | **0 / 8,423** | Decoder validity only |

ScreenSpot-v2 includes all **501** mobile rows (Android, iOS and shop source labels); Android-only source rows score **156/211 (73.93%)**. Text-widget grounding is **233/290 (80.34%)**, icon grounding **116/211 (54.98%)**. We reuse the inclusive point-in-box expression from pinned SeeClick `0ef37ac4d7aaf37ba7b990e3e3c3ca77e1fb8f93`, require a decoded tap, and count other actions as misses. Original labels are retained, including [reported source-label issues](https://github.com/OS-Copilot/OS-Atlas/issues/65).

AndroidControl uses all **8,423 admitted held-out test steps**, low-level step instructions, and reference executed-action history with the same whole-turn window used in training. Train/validation/test trajectory IDs are disjoint. This is **offline teacher-forced next-action prediction**, not rollouts or an official AndroidControl leaderboard result. Source conversion excludes unsupported or invalid source rows; these counts describe our admitted split, not every raw dataset row.

Tolerance match requires the same action and field set, each coordinate axis within **50/1000**, text normalized Levenshtein similarity **≥0.9**, and other parameters exact. The tolerance is a project reporting convention chosen earlier during development, not an established task-success metric. Strict equality is reported alongside it. [Machine-readable results](evaluation.json) include action-level breakdowns and hashes.

The 690-step development validation scored action **669/690 (96.96%)**, strict action **209/690 (30.29%)**, text exact **43/98 (43.88%)**, and loss **4.064669**. Text exact match is substantially lower on the held-out test split; text spelling, omission/repetition, icons, and some scroll directions remain limitations. No AndroidWorld score or end-to-end phone success rate is claimed. Base-model pretraining overlap with evaluation data is unknown.

## Real validation examples

Four actual saved step4658 predictions, selected after evaluation. The current input screenshot is shown; prior observations were also supplied where noted. The gallery includes a text failure and is not a performance estimate or proof of an executed task. Images and outputs are unchanged, with source IDs and hashes.

<table><tr><td width="50%" valign="top"><h3>Find the search icon</h3>
<a href="examples/validation/01-tap.png"><img src="examples/validation/01-tap.png" width="230" alt="Find the search icon: original input screenshot" /></a>
<p><strong>Instruction</strong><br />Click on the search icon at the top right corner</p>
<p><strong>Saved prediction</strong><br /><code>{&quot;action&quot;: &quot;tap&quot;, &quot;coordinate&quot;: [935, 88]}</code></p>
<p><strong>Reference</strong><br /><code>{&quot;action&quot;: &quot;tap&quot;, &quot;coordinate&quot;: [931, 87]}</code></p>
<details><summary>Evidence</summary><p>android-control:3911:0 · 0 previous observations</p><a href="examples/validation/01-tap.json">JSON + SHA256</a></details></td><td width="50%" valign="top"><h3>Enter a search term</h3>
<a href="examples/validation/02-text.png"><img src="examples/validation/02-text.png" width="230" alt="Enter a search term: original input screenshot" /></a>
<p><strong>Instruction</strong><br />type the text running shoes</p>
<p><strong>Saved prediction</strong><br /><code>{&quot;action&quot;: &quot;type_text&quot;, &quot;text&quot;: &quot;running shoes&quot;}</code></p>
<p><strong>Reference</strong><br /><code>{&quot;action&quot;: &quot;type_text&quot;, &quot;text&quot;: &quot;running shoes&quot;}</code></p>
<details><summary>Evidence</summary><p>android-control:10026:5 · 5 previous observations</p><a href="examples/validation/02-text.json">JSON + SHA256</a></details></td></tr><tr><td width="50%" valign="top"><h3>Browse product details</h3>
<a href="examples/validation/03-scroll.png"><img src="examples/validation/03-scroll.png" width="230" alt="Browse product details: original input screenshot" /></a>
<p><strong>Instruction</strong><br />Swipe up for details</p>
<p><strong>Saved prediction</strong><br /><code>{&quot;action&quot;: &quot;scroll_down&quot;}</code></p>
<p><strong>Reference</strong><br /><code>{&quot;action&quot;: &quot;scroll_down&quot;}</code></p>
<details><summary>Evidence</summary><p>android-control:3851:1 · 1 previous observations</p><a href="examples/validation/03-scroll.json">JSON + SHA256</a></details></td><td width="50%" valign="top"><h3>Where text prediction fails</h3>
<a href="examples/validation/04-text-failure.png"><img src="examples/validation/04-text-failure.png" width="230" alt="Where text prediction fails: original input screenshot" /></a>
<p><strong>Instruction</strong><br />Enter The Nightingale in the search textbox</p>
<p><strong>Saved prediction</strong><br /><code>{&quot;action&quot;: &quot;type_text&quot;, &quot;text&quot;: &quot;Theinging&quot;}</code></p>
<p><strong>Reference</strong><br /><code>{&quot;action&quot;: &quot;type_text&quot;, &quot;text&quot;: &quot;The Nightingale&quot;}</code></p>
<details><summary>Evidence</summary><p>android-control:14486:2 · 2 previous observations</p><a href="examples/validation/04-text-failure.json">JSON + SHA256</a></details></td></tr></table>

## Quick start

Verified with Python 3.10, CUDA and a 24 GB RTX 4090. Download this repository, then use an isolated environment and the official CUDA-enabled Torch wheel appropriate for your machine:

```bash
hf download dongjintao/GUI-Swift-Mobile-2B --local-dir GUI-Swift-Mobile-2B
cd GUI-Swift-Mobile-2B
pip install -r requirements.txt
python prepare_dependencies.py --destination /path/on/data-disk/gui-swift-mobile-deps
python infer.py --base-model /path/on/data-disk/gui-swift-mobile-deps/GUI-Owl-1.5-2B-Instruct \
  --bundle /path/on/data-disk/gui-swift-mobile-deps/runtime-bundle \
  --request request.json
```

`request.json` uses screenshot paths relative to that file:

```json
{"turns":[{"instruction":"Tap Settings.","image":"screen.png","receipt":null}]}
```

For the next observation, append a turn with the current instruction, screenshot and **actually executed** previous action/outcome:

```json
{"instruction":"Open Wi-Fi settings.","image":"screen2.png","receipt":{"executed_action":{"action":"tap","coordinate":[500,300]},"outcome":"unverified"}}
```

Receipt and decoded coordinates are normalized to **0–1000**. The runner returns an action; it does not execute it or verify task success. Earlier assistant MASK tables remain as input scaffolds. Oldest complete turns are removed only when needed to fit 16,384 tokens. This runner recomputes the retained conversation; it does not implement persistent vLLM KV caching.

The pinned GUI-Decisions dependency is fetched separately because its selected snapshot contains no LICENSE file; it is **not redistributed** here. You can supply an existing checkout with `--gui-decisions` and an existing base with `--base-model`. See [THIRD_PARTY.md](THIRD_PARTY.md).

## Protocol and training

Slots: **1 action + 4 coordinate values + 100 text positions**. Coordinates use 256 bins per axis. Text supports up to 99 native payload tokens followed by EOS; inactive fields are ignored. Action codes A–P cover tap, long_press, swipe, scroll_up/down/left/right, type_text, open_app, back, home, enter, menu, wait, done and failed. Protocol support does not imply that every action has training coverage.

AndroidControl: **74,525** admitted training targets, **690** validation steps, **8,423** test steps. Two logical epochs, **4,658** updates, effective batch **32** with smaller epoch tails, two RTX 4090s. The official LLaMA-Factory loop is reused through a local fixed-slot adapter. This is SFT, not RL.

Official PEFT LoRA: **rank256 / alpha16 / dropout0.05 / learning rate1e-5**, adapting 301 linear modules across vision, projections, language and output head. Embeddings, normalization and convolutional patch projection are excluded. Frozen tied input/output weights stay shared; the output head has a separate LoRA delta. Do not blindly merge into tied embeddings.

The Desktop131 objective is `L_action + 2 * mean(active parameter-field losses)` (action loss alone for parameter-free actions), averaged over samples. Coordinate soft targets use sigma0.005 and full-vocabulary normalization. Base precision is BF16; adapters remain FP32. Image bounds: 65,536–1,048,576 pixels per observation. The adapter JSON omits optional `task_type:null`, retaining PEFT's default generic wrapper.

## Attribution and reproducibility

GUI-Owl is the base model, not the author of this independent derivative. **GUI-Decisions primarily inspired our 256-bin localization representation/readout. Single-forward complete-action prediction was already part of our project.** Concrete reuse of its coordinate labels, targets and native slot reader is credited separately in [THIRD_PARTY.md](THIRD_PARTY.md).

Base revision: `528ceaec795bbfbe6103bd79e03db849feadfb24`. Adapter SHA256: `7312466ddb1f9a1137de4a2e3b0bd311a3f7662b64331b0cb2dd5ac33636aa0c`.

[Release validation](release-validation.json) compares the portable runner against frozen inference; [provenance](provenance.json), [configuration](gui_swift_mobile_config.json) and [checksums](SHA256SUMS.json) pin the package. We publish four curated validation screenshots, not the raw corpus, private conversations, optimizer states or credentials.
