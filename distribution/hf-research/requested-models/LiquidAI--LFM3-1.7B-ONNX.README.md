---
library_name: onnxruntime
license: other
license_name: lfm1.0
license_link: LICENSE
pipeline_tag: text-generation
tags:
- liquid
- lfm3
- onnx
language:
- en
- ar
- zh
- fr
- de
- it
- ja
- ko
- pt
- es
- vi
- th
- id
- hi
- ru
- pl
base_model: LiquidAI/LFM3-1.7B
base_model_relation: quantized
---

<div align="center">
  <img
    src="https://huggingface.co/LiquidAI/LFM3-1.7B/resolve/main/assets/liquid-ai-banner.webp"
    alt="Liquid AI"
    style="width: 100%; max-width: 100%; height: auto; display: block; margin: 0;"
  />
  <div align="center" style="display: flex; justify-content: center; align-items: center; gap: 1.25em; margin: 8px 0 1.7rem; padding: 0; line-height: 1.3;">
    <a href="https://console.liquid.ai/dashboard" style="display: inline-flex; align-items: center; gap: 6px;"><img src="https://huggingface.co/LiquidAI/LFM3-1.7B/resolve/main/assets/icons/api.svg" width="16" height="16" alt="" aria-hidden="true" style="display: block; flex: 0 0 16px; width: 16px; height: 16px; margin: 0;" /><strong>API</strong></a>
    <a href="https://playground.liquid.ai/" style="display: inline-flex; align-items: center; gap: 6px;"><img src="https://huggingface.co/LiquidAI/LFM3-1.7B/resolve/main/assets/icons/try.svg" width="16" height="16" alt="" aria-hidden="true" style="display: block; flex: 0 0 16px; width: 16px; height: 16px; margin: 0;" /><strong>Try</strong></a>
    <a href="https://docs.liquid.ai/lfm/getting-started/welcome" style="display: inline-flex; align-items: center; gap: 6px;"><img src="https://huggingface.co/LiquidAI/LFM3-1.7B/resolve/main/assets/icons/docs.svg" width="16" height="16" alt="" aria-hidden="true" style="display: block; flex: 0 0 16px; width: 16px; height: 16px; margin: 0;" /><strong>Docs</strong></a>
    <a href="https://www.liquid.ai/blog/lfm3" style="display: inline-flex; align-items: center; gap: 6px;"><img src="https://huggingface.co/LiquidAI/LFM3-1.7B/resolve/main/assets/icons/blog.svg" width="16" height="16" alt="" aria-hidden="true" style="display: block; flex: 0 0 16px; width: 16px; height: 16px; margin: 0;" /><strong>Blog</strong></a>
  </div>
</div>

# LFM3-1.7B-ONNX

ONNX version of [LFM3-1.7B](https://huggingface.co/LiquidAI/LFM3-1.7B) for cross-platform inference with ONNX Runtime.

Find architecture and usage details in the [original model card](https://huggingface.co/LiquidAI/LFM3-1.7B).

## Files
<hr style="margin: -0.7em 0 1.2em 0; border: 0; border-top: 1px solid rgba(127, 127, 127, 0.3);" />

<table style="width: 100%; display: table; border: 0; border-radius: 0; border-collapse: collapse;">
<thead style="border: 0;">
<tr style="border: 0;">
<th scope="col" style="text-align: left; padding: 0.55em 0.75em; border: 0; border-bottom: 1px solid rgba(127, 127, 127, 0.55);">Precision</th>
<th scope="col" style="text-align: left; padding: 0.55em 0.75em; border: 0; border-bottom: 1px solid rgba(127, 127, 127, 0.55);">File</th>
<th scope="col" style="text-align: left; padding: 0.55em 0.75em; border: 0; border-bottom: 1px solid rgba(127, 127, 127, 0.55);">Size</th>
<th scope="col" style="text-align: left; padding: 0.55em 0.75em; border: 0; border-bottom: 1px solid rgba(127, 127, 127, 0.55);">Device</th>
</tr>
</thead>
<tbody style="border: 0;">
<tr style="border: 0;">
<td style="border: 0; padding: 0.55em 0.75em;"><code>q4</code></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-1.7B-ONNX/blob/main/onnx/model_q4.onnx"><code>model_q4</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;">1.63 GB</td>
<td style="border: 0; padding: 0.55em 0.75em;">CPU</td>
</tr>
<tr style="border: 0;">
<td style="border: 0; padding: 0.55em 0.75em;"><code>q4f32</code></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-1.7B-ONNX/blob/main/onnx/model_q4f32.onnx"><code>model_q4f32</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;">2.24 GB</td>
<td style="border: 0; padding: 0.55em 0.75em;">CPU</td>
</tr>
<tr style="border: 0;">
<td style="border: 0; padding: 0.55em 0.75em;"><code>q8</code></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-1.7B-ONNX/blob/main/onnx/model_q8.onnx"><code>model_q8</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;">2.27 GB</td>
<td style="border: 0; padding: 0.55em 0.75em;">CPU</td>
</tr>
<tr style="border: 0;">
<td style="border: 0; padding: 0.55em 0.75em;"><code>q4f16</code></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-1.7B-ONNX/blob/main/onnx/model_q4f16.onnx"><code>model_q4f16</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;">1.38 GB</td>
<td style="border: 0; padding: 0.55em 0.75em;">GPU</td>
</tr>
<tr style="border: 0;">
<td style="border: 0; padding: 0.55em 0.75em;"><code>q8f16</code></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-1.7B-ONNX/blob/main/onnx/model_q8f16.onnx"><code>model_q8f16</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;">2.03 GB</td>
<td style="border: 0; padding: 0.55em 0.75em;">GPU</td>
</tr>
<tr style="border: 0;">
<td style="border: 0; padding: 0.55em 0.75em;"><code>fp16</code></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-1.7B-ONNX/blob/main/onnx/model_fp16.onnx"><code>model_fp16</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;">3.59 GB</td>
<td style="border: 0; padding: 0.55em 0.75em;">GPU</td>
</tr>
<tr style="border: 0;">
<td style="border: 0; padding: 0.55em 0.75em;"><code>fp32</code></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-1.7B-ONNX/blob/main/onnx/model.onnx"><code>model</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;">7.18 GB</td>
<td style="border: 0; padding: 0.55em 0.75em;">Reference</td>
</tr>
</tbody>
</table>

fp16, q4f16 and q8f16 run with fp16 activations, the others with fp32. q4 and q4f16 keep the embedding, the LM head and 27 of the 153 layer projections in int8 and the rest in int4, while q4f32 quantizes all 153 projections to int4 and keeps the embedding and LM head in fp32.

## Use with ONNX Runtime
<hr style="margin: -0.7em 0 1.2em 0; border: 0; border-top: 1px solid rgba(127, 127, 127, 0.3);" />

The graphs run on ONNX Runtime. Each step takes `input_ids`, `attention_mask`, `position_ids` and `logits_to_keep`, plus the convolution and attention caches (`past_conv.N`, `past_key_values.N.key` and `.value`). It returns `logits` and the updated caches (`present_conv.N`, `present.N.key` and `.value`). The fp16, q4f16 and q8f16 graphs keep their caches in fp16.

On GPU, use fp16, q4f16 or q8f16. The CUDA execution provider has no fp32 GroupQueryAttention kernel, so the other graphs run attention on the CPU.

In the browser, use `import * as ort from 'onnxruntime-web/webgpu'`: the default `onnxruntime-web` import cannot run these graphs. On WebGPU, use `model_q4f16`, and prefill in chunks of at most 256 tokens.

Recommended sampling: `temperature=0.1, top_k=50, repetition_penalty=1.1`. For math and science, use `temperature=1.0, top_p=0.95, top_k=20` instead. More options in the [ONNX guide](https://docs.liquid.ai/deployment/on-device/onnx).
