---
license: apache-2.0
base_model: Qwen/Qwen3.5-4B
base_model_relation: finetune
datasets:
- ccore/ThreeJS-3D-Scenes
tags:
- qwen3_5
- code
- threejs
- 3d
- preview
---

# Qwen3.5-4B-3D-Coder

### Early preview · General coding with a focus on 3D

**An experiment exploring the feasibility of small language models for general-purpose coding and high-quality 3D scene generation.**

This is an early preview of a 4B model I am still training, with a particular focus on creating interactive 3D scenes with Three.js. Initial hands-on results have been promising for coding in general, and especially for generating 3D scenes, so I decided to share the progress while development continues.

This is an **independent fine-tune** by [CCORE](https://huggingface.co/ccore), based on [Qwen/Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B). **It is not an official Qwen release.**

## Technical report — now available

The **technical report for this training experiment is now available**.

[**Read the technical report (PDF)**](https://huggingface.co/ccore/Qwen3.5-4B-3D-Coder/blob/main/Qwen3.5-4B-3D-Coder_Technical_Report.pdf) · [**Download the PDF**](https://huggingface.co/ccore/Qwen3.5-4B-3D-Coder/resolve/main/Qwen3.5-4B-3D-Coder_Technical_Report.pdf?download=true)

## Full FP16 weights — now available

The **original FP16 weights** are now available in this repository in **Safetensors format**, alongside the Q4_K_M and Q8_0 GGUF versions.

The release includes all **four weight shards**, the model configuration, tokenizer, generation configuration, chat template, and image/video processor settings. The FP16 weights were verified locally for **loading and text generation with Transformers 5.20.2**.

The FP16 generation defaults use **temperature 0.6**, **top-k 20**, **top-p 0.95**, and **repetition penalty 1.0**, with caching enabled.

[**Browse the FP16 files and GGUF versions**](https://huggingface.co/ccore/Qwen3.5-4B-3D-Coder/tree/main)

## Training dataset — now available

The dataset used to train this model is now public: [**CCORE Three.js 3D Scenes**](https://huggingface.co/datasets/ccore/ThreeJS-3D-Scenes).

It contains **4,146 scene examples** (**3,933 training / 213 validation**), including prompts, full thinking traces, complete HTML/JavaScript responses, and complexity levels. Rendered reference images are available in the optional `previews` configuration. The dataset is released under the **MIT License**.

## October 27 checkpoint update

Both **Q4_K_M** and **Q8_0** now contain the updated October 27 checkpoint. If you downloaded an earlier version, please download the files again to get this update.

In my latest tests, **one-shot success has improved substantially**, along with **overall scene quality**. Some scenes can still require another attempt or a correction.

**Generation parameters remain unchanged** for both quantizations. Use the settings listed below. The earlier fix for the layer-related issue reported by some users is also included.

The comparison below shows the Rome scene across the **October 25, 26, and 27** previews.

[![Rome scene progression across the October 25, 26, and 27 previews](images/roma3.png)](https://huggingface.co/ccore/Qwen3.5-4B-3D-Coder/blob/main/images/roma3.png)

## Release update — October 29, 2026

The **full FP16 weights**, [**training dataset**](https://huggingface.co/datasets/ccore/ThreeJS-3D-Scenes), and [**technical report (PDF)**](https://huggingface.co/ccore/Qwen3.5-4B-3D-Coder/blob/main/Qwen3.5-4B-3D-Coder_Technical_Report.pdf) are now available.

The **full set of quantized versions** is also planned.

## Early examples

Selected screenshots of rendered Three.js scenes: stylized architecture, gardens, voxel environments, furniture, workshop interiors, and mechanical objects, built through HTML and JavaScript.

Click any image to view the original screenshot at full size.

<table>
<tbody>
<tr>
<td width="50%" align="center"><a href="https://huggingface.co/ccore/Qwen3.5-4B-3D-Coder/blob/main/images/sakura-temple-garden.png"><img src="images/gallery/sakura-temple-garden.svg" alt="Sakura Temple Garden" width="1280" /></a><br /><strong>Sakura Temple Garden</strong></td>
<td width="50%" align="center"><a href="https://huggingface.co/ccore/Qwen3.5-4B-3D-Coder/blob/main/images/sunset-village.png"><img src="images/gallery/sunset-village.svg" alt="Sunset Village" width="1280" /></a><br /><strong>Sunset Village</strong></td>
</tr>
<tr>
<td width="50%" align="center"><a href="https://huggingface.co/ccore/Qwen3.5-4B-3D-Coder/blob/main/images/dragonrider-aerie.png"><img src="images/gallery/dragonrider-aerie.svg" alt="Dragonrider Aerie" width="1280" /></a><br /><strong>Dragonrider Aerie</strong></td>
<td width="50%" align="center"><a href="https://huggingface.co/ccore/Qwen3.5-4B-3D-Coder/blob/main/images/voxel-launch-site.png"><img src="images/gallery/voxel-launch-site.svg" alt="Voxel launch site" width="1280" /></a><br /><strong>Voxel launch site</strong></td>
</tr>
<tr>
<td width="50%" align="center"><a href="https://huggingface.co/ccore/Qwen3.5-4B-3D-Coder/blob/main/images/bookbinding-bench.png"><img src="images/gallery/bookbinding-bench.svg" alt="The Bookbinding Bench" width="1280" /></a><br /><strong>The Bookbinding Bench</strong></td>
<td width="50%" align="center"><a href="https://huggingface.co/ccore/Qwen3.5-4B-3D-Coder/blob/main/images/lga-3647-socket.png"><img src="images/gallery/lga-3647-socket.svg" alt="LGA 3647 Socket" width="1280" /></a><br /><strong>LGA 3647 Socket</strong></td>
</tr>
<tr>
<td width="50%" align="center"><a href="https://huggingface.co/ccore/Qwen3.5-4B-3D-Coder/blob/main/images/pelican-bike.png"><img src="images/gallery/pelican-bike.svg" alt="Pelican Bike No. 07" width="1280" /></a><br /><strong>Pelican Bike No. 07</strong></td>
<td width="50%" align="center"><a href="https://huggingface.co/ccore/Qwen3.5-4B-3D-Coder/blob/main/images/secretary-desk.png"><img src="images/gallery/secretary-desk.svg" alt="Bow-Front Secretary Desk" width="1280" /></a><br /><strong>Bow-Front Secretary Desk</strong></td>
</tr>
<tr>
<td width="50%" align="center"><a href="https://huggingface.co/ccore/Qwen3.5-4B-3D-Coder/blob/main/images/korin-garden.png"><img src="images/gallery/korin-garden.svg" alt="Kōrin Garden" width="1280" /></a><br /><strong>Kōrin Garden</strong></td>
<td width="50%" align="center"><a href="https://huggingface.co/ccore/Qwen3.5-4B-3D-Coder/blob/main/images/sakura-bonsai.png"><img src="images/gallery/sakura-bonsai.svg" alt="Sakura bonsai" width="1280" /></a><br /><strong>Sakura bonsai</strong></td>
</tr>
</tbody>
</table>

## Generation settings

Use the following settings for both the **Q4_K_M** and **Q8** versions:

| Parameter | Value |
| --- | --- |
| Temperature | 0.6–0.8 |
| Top P | 0.90–0.95 |
| Repeat penalty | 1.0 |
| Top K | 20–40 |

### LM Studio: disable MTP

> **Important:** If you're running this model in LM Studio, **disable multi-token prediction (MTP)** for the model to work properly. LM Studio enables this option by default.

## System prompt for Three.js scenes

```text
Use only the Three.js core ES module from
https://cdnjs.cloudflare.com/ajax/libs/three.js/0.160.0/three.module.min.js.
Do not import addons or legacy examples/js paths;
implement any camera controls directly inside the HTML.
```

## Example prompts

Use the system prompt above, then try one of these user prompts.

### Elephant rehabilitation habitat

```text
Please create one self-contained HTML file with Three.js from cdnjs showing
an elephant rehabilitation habitat.

A broad sheltered yard connects water, shade, and feeding areas through
accessible paths. Use the elephant's scale and movement clearance to guide
the layout around the structures.

Render it as a deliberate 3D voxel model built from visible cubic blocks,
with clear depth, readable silhouettes, and solid spatial relationships.
```

### Dragonrider aerie on a sea stack

```text
Build a self-contained HTML scene with Three.js from cdnjs featuring a
dragonrider aerie on a sea stack, with perches, a hatchery, and a windlass.

The aerie occupies a flat-topped sea stack, reached by a rope windlass from
the shore. Four projecting stone perches ring the central hatchery, a
vaulted cave with sand nests and a smoke hole. A timber landing deck holds
a hoist crane and harness racks, while switchback stairs connect the
perches to a water-level boathouse.

Render it as a deliberate 3D voxel model built from visible cubic blocks,
with clear depth, readable silhouettes, and solid spatial relationships.
```

### Canal lock and weir complex

```text
Please create one self-contained HTML file with Three.js from cdnjs showing
a canal lock and weir complex with a central island.
```

### Animated air traffic control tower

```text
Make a standalone HTML scene with Three.js from cdnjs depicting an animated
air traffic control tower with rotating radar and light signals.

A tapered control tower rises from a rectangular base to a cantilevered
glass cab with angled glazing panels and an external catwalk. A primary
surveillance radar on a roof pedestal turns continuously, and a rotating
beacon sits above the cab.
```

## Known issues

The bugs observed so far have generally involved duplicate variable names, rather than major structural problems.
