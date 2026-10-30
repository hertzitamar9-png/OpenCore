---
library_name: onnxruntime
license: other
license_name: lfm1.0
license_link: LICENSE
pipeline_tag: image-text-to-text
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
base_model: LiquidAI/LFM3-VL-2B
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

# LFM3-VL-2B-ONNX

ONNX version of [LFM3-VL-2B](https://huggingface.co/LiquidAI/LFM3-VL-2B) for cross-platform inference with ONNX Runtime.

Find architecture and usage details in the [original model card](https://huggingface.co/LiquidAI/LFM3-VL-2B).

## Files
<hr style="margin: -0.7em 0 1.2em 0; border: 0; border-top: 1px solid rgba(127, 127, 127, 0.3);" />

Each precision pairs a decoder with a vision encoder.

<table style="width: 100%; display: table; border: 0; border-radius: 0; border-collapse: collapse;">
<thead style="border: 0;">
<tr style="border: 0;">
<th scope="col" style="text-align: left; padding: 0.55em 0.75em; border: 0; border-bottom: 1px solid rgba(127, 127, 127, 0.55);">Precision</th>
<th scope="col" style="text-align: left; padding: 0.55em 0.75em; border: 0; border-bottom: 1px solid rgba(127, 127, 127, 0.55);">Decoder</th>
<th scope="col" style="text-align: left; padding: 0.55em 0.75em; border: 0; border-bottom: 1px solid rgba(127, 127, 127, 0.55);">Vision encoder</th>
<th scope="col" style="text-align: left; padding: 0.55em 0.75em; border: 0; border-bottom: 1px solid rgba(127, 127, 127, 0.55);">Size</th>
<th scope="col" style="text-align: left; padding: 0.55em 0.75em; border: 0; border-bottom: 1px solid rgba(127, 127, 127, 0.55);">Device</th>
</tr>
</thead>
<tbody style="border: 0;">
<tr style="border: 0;">
<td style="border: 0; padding: 0.55em 0.75em;"><code>q4</code></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-VL-2B-ONNX/blob/main/onnx/decoder_q4.onnx"><code>decoder_q4</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-VL-2B-ONNX/blob/main/onnx/vision_encoder_q8.onnx"><code>vision_encoder_q8</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;">1.69 GB</td>
<td style="border: 0; padding: 0.55em 0.75em;">CPU</td>
</tr>
<tr style="border: 0;">
<td style="border: 0; padding: 0.55em 0.75em;"><code>q8</code></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-VL-2B-ONNX/blob/main/onnx/decoder_q8.onnx"><code>decoder_q8</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-VL-2B-ONNX/blob/main/onnx/vision_encoder_fp16.onnx"><code>vision_encoder_fp16</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;">2.51 GB</td>
<td style="border: 0; padding: 0.55em 0.75em;">CPU</td>
</tr>
<tr style="border: 0;">
<td style="border: 0; padding: 0.55em 0.75em;"><code>q4f16</code></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-VL-2B-ONNX/blob/main/onnx/decoder_q4f16.onnx"><code>decoder_q4f16</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-VL-2B-ONNX/blob/main/onnx/vision_encoder_fp16.onnx"><code>vision_encoder_fp16</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;">1.69 GB</td>
<td style="border: 0; padding: 0.55em 0.75em;">GPU</td>
</tr>
<tr style="border: 0;">
<td style="border: 0; padding: 0.55em 0.75em;"><code>q8f16</code></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-VL-2B-ONNX/blob/main/onnx/decoder_q8f16.onnx"><code>decoder_q8f16</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-VL-2B-ONNX/blob/main/onnx/vision_encoder_fp16.onnx"><code>vision_encoder_fp16</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;">2.34 GB</td>
<td style="border: 0; padding: 0.55em 0.75em;">GPU</td>
</tr>
<tr style="border: 0;">
<td style="border: 0; padding: 0.55em 0.75em;"><code>fp16</code></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-VL-2B-ONNX/blob/main/onnx/decoder_fp16.onnx"><code>decoder_fp16</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-VL-2B-ONNX/blob/main/onnx/vision_encoder_fp16.onnx"><code>vision_encoder_fp16</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;">3.90 GB</td>
<td style="border: 0; padding: 0.55em 0.75em;">GPU</td>
</tr>
<tr style="border: 0;">
<td style="border: 0; padding: 0.55em 0.75em;"><code>fp32</code></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-VL-2B-ONNX/blob/main/onnx/decoder.onnx"><code>decoder</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-VL-2B-ONNX/blob/main/onnx/vision_encoder.onnx"><code>vision_encoder</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;">7.80 GB</td>
<td style="border: 0; padding: 0.55em 0.75em;">Reference</td>
</tr>
</tbody>
</table>

## Use with ONNX Runtime
<hr style="margin: -0.7em 0 1.2em 0; border: 0; border-top: 1px solid rgba(127, 127, 127, 0.3);" />

The vision encoder maps each image, or each temporal patch of a video, to projected features. The decoder takes these features at the `<|image|>` and `<|video|>` placeholders of `input_ids`. For image and video preprocessing, use the values in `processor_config.json`.

In the browser, use `import * as ort from 'onnxruntime-web/webgpu'`: the default `onnxruntime-web` import cannot run these graphs. On WebGPU, pair `decoder_q4f16` with the float32 side graph of the q4 bundle (`vision_encoder_q8`), and prefill in chunks of at most 1,024 tokens.

Recommended sampling: `temperature=0.1, top_p=1.0, top_k=50, min_p=0.0, repetition_penalty=1.1`. More options in the [ONNX guide](https://docs.liquid.ai/deployment/on-device/onnx).
