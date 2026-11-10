---
language:
- en
license: apache-2.0
base_model: mPLUG/GUI-Owl-1.5-4B-Instruct
base_model_relation: adapter
tags:
- gui-agent
- computer-use
- vision-language
- single-pass
- fixed-slot
- custom-inference
---

![GUI-Swift-4B — One forward. A complete GUI action.](assets/hero.svg)

<p align="center"><strong>Single-Pass GUI Action Prediction</strong><br />
<a href="#real-validation-examples">Examples</a> · <a href="#results-at-a-glance">Results</a> · <a href="#quick-start">Quick start</a> · <a href="README_zh.md">简体中文</a></p>

**GUI-Swift-4B** turns a screenshot, an instruction and action history into one
desktop GUI action through **a single native causal forward over 131 fixed value slots**.
It is a lightweight LoRA release built on [GUI-Owl-1.5-4B-Instruct](https://huggingface.co/mPLUG/GUI-Owl-1.5-4B-Instruct),
with the original vocabulary head retained and no separate autoregressive text writer.

**Release:** Desktop131 SFT · step3368 &nbsp; / &nbsp; **Adapter:** 33.03M parameters · 132 MB

![How GUI-Swift reads the screen and predicts an action](assets/workflow.svg)

This is a **desktop research checkpoint**. “Single-pass” describes the inference
mechanism; a matched speed or task-success advantage has not been established.

## Real validation examples

These are **actual saved predictions** from this checkpoint's 1,967-row AgentNet
development validation set. The gallery is selected after evaluation and includes
a text failure. Screenshots show the current input frame; action history was also
part of the model input. These are **next-action examples, not completed-task demonstrations**.

<table>
<tr><td width="50%" valign="top"><h3>GIMP · Find the Desktop folder</h3>
<p><strong>ACTION CORRECT · OFFSET (2, 0) / 1000</strong></p>
<a href="examples/validation/01-gimp.png"><img src="examples/validation/01-gimp.png" alt="GIMP · Find the Desktop folder: Input screenshot · click for full size" width="100%" /></a>
<p><small>Input screenshot · click for full size</small></p>
<p><strong>Model prediction</strong><br /><code>{&quot;action&quot;: &quot;left_click&quot;, &quot;coordinate&quot;: [155, 243]}</code></p>
<p><strong>Reference action</strong><br /><code>{&quot;action&quot;: &quot;left_click&quot;, &quot;coordinate&quot;: [153, 243]}</code></p>
<details><summary>Original task instruction & record</summary>
<p>Please use GIMP to open the &quot;MMQ.png&quot; image on your desktop, add the black text &quot;sky&quot; at the top of the image with a font size of 100 and make it bold.</p>
<p>Historical action records: 4 · Strict action match: false</p>
<p><a href="examples/validation/01-gimp.json">Saved prediction & provenance (JSON)</a></p>
</details></td><td width="50%" valign="top"><h3>Writer · Enter a search term</h3>
<p><strong>EXACT ACTION MATCH</strong></p>
<a href="examples/validation/02-writer.png"><img src="examples/validation/02-writer.png" alt="Writer · Enter a search term: Input screenshot · click for full size" width="100%" /></a>
<p><small>Input screenshot · click for full size</small></p>
<p><strong>Model prediction</strong><br /><code>{&quot;action&quot;: &quot;type&quot;, &quot;text&quot;: &quot;CAGR&quot;}</code></p>
<p><strong>Reference action</strong><br /><code>{&quot;action&quot;: &quot;type&quot;, &quot;text&quot;: &quot;CAGR&quot;}</code></p>
<details><summary>Original task instruction & record</summary>
<p>Help me open the desktop document &quot;144.odt&quot;, change the overall font size of the document to 16pt and the font color to green, use the find and replace function to replace &quot;CAGR&quot; with &quot;WWAA&quot;, and then save it as &quot;yrrte7&quot; on the desktop after completion.</p>
<p>Historical action records: 8 · Strict action match: true</p>
<p><a href="examples/validation/02-writer.json">Saved prediction & provenance (JSON)</a></p>
</details></td></tr>
<tr><td width="50%" valign="top"><h3>Impress · Insert a slide</h3>
<p><strong>EXACT ACTION MATCH</strong></p>
<a href="examples/validation/03-impress.png"><img src="examples/validation/03-impress.png" alt="Impress · Insert a slide: Input screenshot · click for full size" width="100%" /></a>
<p><small>Input screenshot · click for full size</small></p>
<p><strong>Model prediction</strong><br /><code>{&quot;action&quot;: &quot;key&quot;, &quot;keys&quot;: [&quot;ctrl&quot;, &quot;m&quot;]}</code></p>
<p><strong>Reference action</strong><br /><code>{&quot;action&quot;: &quot;key&quot;, &quot;keys&quot;: [&quot;ctrl&quot;, &quot;m&quot;]}</code></p>
<details><summary>Original task instruction & record</summary>
<p>In Impress,add two new slides.Enter“Ancient History”and“Modern Art”in the title areas of slides 1 and 2 respectively.Enter the table of contents“・Egyptian・Greek・Roman”in the content area of slide 3.After completion,change the font to“Likhan”uniformly.</p>
<p>Historical action records: 1 · Strict action match: true</p>
<p><a href="examples/validation/03-impress.json">Saved prediction & provenance (JSON)</a></p>
</details></td><td width="50%" valign="top"><h3>Calc · Rename a worksheet</h3>
<p><strong>EXACT ACTION MATCH</strong></p>
<a href="examples/validation/04-calc.png"><img src="examples/validation/04-calc.png" alt="Calc · Rename a worksheet: Input screenshot · click for full size" width="100%" /></a>
<p><small>Input screenshot · click for full size</small></p>
<p><strong>Model prediction</strong><br /><code>{&quot;action&quot;: &quot;type&quot;, &quot;text&quot;: &quot;7&quot;}</code></p>
<p><strong>Reference action</strong><br /><code>{&quot;action&quot;: &quot;type&quot;, &quot;text&quot;: &quot;7&quot;}</code></p>
<details><summary>Original task instruction & record</summary>
<p>Create three worksheets named &quot;5, 6, 7&quot; and change the color labels of worksheet 567 to &quot;red, yellow, blue&quot;, then hide them.</p>
<p>Historical action records: 12 · Strict action match: true</p>
<p><a href="examples/validation/04-calc.json">Saved prediction & provenance (JSON)</a></p>
</details></td></tr>
</table>
<h3>A limitation · Repeated text</h3>
<p><strong>ACTION CORRECT · TEXT MISMATCH</strong></p>
<a href="examples/validation/05-text-failure.png"><img src="examples/validation/05-text-failure.png" alt="A limitation · Repeated text: Input screenshot · click for full size" width="100%" /></a>
<p><small>Input screenshot · click for full size</small></p>
<p><strong>Model prediction</strong><br /><code>{&quot;action&quot;: &quot;type&quot;, &quot;text&quot;: &quot;・ Egyptianian GreekGreek Roman Roman&quot;}</code></p>
<p><strong>Reference action</strong><br /><code>{&quot;action&quot;: &quot;type&quot;, &quot;text&quot;: &quot;・Egyptian・Greek・Roman&quot;}</code></p>
<details><summary>Original task instruction & record</summary>
<p>In Impress,add two new slides.Enter“Ancient History”and“Modern Art”in the title areas of slides 1 and 2 respectively.Enter the table of contents“・Egyptian・Greek・Roman”in the content area of slide 3.After completion,change the font to“Likhan”uniformly.</p>
<p>Historical action records: 13 · Strict action match: false</p>
<p><a href="examples/validation/05-text-failure.json">Saved prediction & provenance (JSON)</a></p>
</details>

<details>
<summary><strong>Inspect the GIMP click locations</strong></summary>

<img src="assets/gimp-click.svg" alt="Predicted and reference click positions on the unchanged GIMP input" width="100%" />

The model predicts `(155, 243)` and the reference is `(153, 243)`, on a 0–1000
coordinate scale. The action type agrees and the coordinate offset is `(2, 0)`;
the strict full-action match is **false**. The circles/cross are presentation
overlays, not model outputs or evidence of executing the click.

</details>

Source: [AgentNet / OpenCUA](https://huggingface.co/datasets/xlangai/AgentNet).
[Example manifest](examples/validation/manifest.json) ·
[Original screenshot license](examples/validation/AGENTNET-LICENSE.txt).
The raw validation corpus is not bundled.

## Results at a glance

| Saved evaluation | Result | What it measures |
|---|---:|---|
| OSWorld-derived selected suite | **16.29%** · 58 / 356 | Environment-verified full task success among scored tasks |
| Development validation · action type | **75.60%** · 1,487 / 1,967 | Whether the action type agrees |
| Development validation · position @50/1000 | **49.64%** · 548 / 1,104 | Action type agrees and both x/y errors are <= 50 on a 0–1000 scale |
| Development validation · text | **30.08%** · 120 / 399 | Exact text action match on reference `type` rows |

The position threshold was changed from 20 to 50 after inspecting the results,
then recounted on the same saved predictions. It allows 5% of each screen dimension
and measures the primary point, not widget hit rate or complete action correctness.
The original @20 result is **41.30%** (456/1,104); strict full-action matching is
**24.05%** (473/1,967). [Metric definitions and original results](docs/technical-details.md#coordinate-tolerance-reporting-update).

The OSWorld-derived run selected 361 of 369 tasks: 356 scored, 5 unscored;
mean reward among scored tasks was **0.1712077**. This is not a complete official
leaderboard score. Validation was repeatedly used during development, and this
checkpoint was chosen after observing results. No new model inference or environment
run was performed; the five examples above do not estimate average performance.

**Next direction — reinforcement learning:** use this SFT checkpoint as a starting
point and train with environment-verifier feedback to improve end-to-end task
completion. The 16.29% is the current measured SFT result; improvement through RL
is a future objective and has not been established for this release.

[Original saved metrics](evaluation.json) · [Position recount](docs/coordinate-tolerance.json) · [Training and evaluation details](docs/technical-details.md#training)

## Quick start

Use Python 3.10+ and an NVIDIA GPU. Download the adapter repository, then prepare
the pinned backbone and GUI-Decisions dependency on a disk with enough space.

```bash
hf download dongjintao/GUI-Swift-4B --local-dir GUI-Swift-4B
cd GUI-Swift-4B
python -m pip install -r requirements.txt
python prepare_dependencies.py --destination /path/on/data-disk/gui-swift-deps
python infer.py \
  --base-model /path/on/data-disk/gui-swift-deps/GUI-Owl-1.5-4B-Instruct \
  --gui-decisions /path/on/data-disk/gui-swift-deps/GUI-Decisions \
  --request examples/request.json
```

The base download is approximately **8.9 GB**. Matching existing local dependencies
can be used instead. The script predicts an action; it does not operate your desktop.
The bundled runnable request is a synthetic smoke example, separate from the saved
validation gallery. Gallery JSON files document historical results and are not
complete replay requests because historical images/actions are omitted.

<details>
<summary><strong>Request format and loading notes</strong></summary>

<pre><code>{"instruction": "Click the Save button.",
 "image": "screen.png", "history": []}</code></pre>

<p>Image paths are relative to the request file. History records contain <code>image</code> and
<code>actions</code> (actually executed canonical actions); the original runtime retains all
supplied actions and the last two historical screenshots. Do not put the reference
next action into the input. Use the custom <code>infer.py</code> entry point: a generic
<code>generate()</code> call or attaching PEFT to the entire VLM does not reproduce the protocol.
The frozen loader attaches LoRA to the <strong>language-model submodule</strong>.</p>

</details>

## Design & credits

We thank **[GUI-Decisions](https://github.com/infinitylogesh/GUI-Decisions)** for its
**256-bin coordinate representation and readout**, our principal inspiration from
that work. Single-forward complete-action prediction was already being explored
in this project. The runtime also directly reuses the pinned upstream `slotread.train.read`
and label/numerical utilities; design history and implementation reuse are credited separately.

We also thank **GUI-Owl / Mobile-Agent** for the backbone, **OpenCUA / AgentNet** for
the validation examples, and **PEFT / Transformers** for adapter and model support.
The external GUI-Decisions snapshot has no license file and is not bundled or
relicensed here. [Third-party sources and terms](THIRD_PARTY.md).

## Research notes

- **Current strengths and gaps:** the saved cases show simple click, key and text
  prediction. Text repetition, dragging, misclicks and premature stopping remain
  limitations. No verified mobile compatibility or general latency advantage is claimed.
- **Scope:** SFT, desktop-only, 131 slots. This release is not Unified133 or the
  later RL model. Terra-assisted trajectories are not standalone model results.
- **Reproducibility:** 504 exported adapter tensors matched the checkpoint exactly.
  Separate-process checks matched inputs, logits and decoded actions on two saved
  inputs. One RTX 4090 used about 9.1 GiB peak allocated memory in that bounded check;
  this is not a maximum-context memory bound.

[Full protocol and training recipe](docs/technical-details.md) ·
[Release verification](release-validation.json) · [Provenance](provenance.json) ·
[File checksums](SHA256SUMS.json)
