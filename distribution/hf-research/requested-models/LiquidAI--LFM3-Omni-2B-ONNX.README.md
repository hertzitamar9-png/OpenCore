---
library_name: onnxruntime
license: other
license_name: lfm1.0
license_link: LICENSE
pipeline_tag: any-to-any
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
- da
- el
- fi
- he
- nl
- sv
- tr
base_model: LiquidAI/LFM3-Omni-2B
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
    <a href="https://playground.liquid.ai/talk" style="display: inline-flex; align-items: center; gap: 6px;"><img src="https://huggingface.co/LiquidAI/LFM3-1.7B/resolve/main/assets/icons/try.svg" width="16" height="16" alt="" aria-hidden="true" style="display: block; flex: 0 0 16px; width: 16px; height: 16px; margin: 0;" /><strong>Try</strong></a>
    <a href="https://docs.liquid.ai/lfm/getting-started/welcome" style="display: inline-flex; align-items: center; gap: 6px;"><img src="https://huggingface.co/LiquidAI/LFM3-1.7B/resolve/main/assets/icons/docs.svg" width="16" height="16" alt="" aria-hidden="true" style="display: block; flex: 0 0 16px; width: 16px; height: 16px; margin: 0;" /><strong>Docs</strong></a>
    <a href="https://www.liquid.ai/blog/lfm3" style="display: inline-flex; align-items: center; gap: 6px;"><img src="https://huggingface.co/LiquidAI/LFM3-1.7B/resolve/main/assets/icons/blog.svg" width="16" height="16" alt="" aria-hidden="true" style="display: block; flex: 0 0 16px; width: 16px; height: 16px; margin: 0;" /><strong>Blog</strong></a>
  </div>
</div>

# LFM3-Omni-2B-ONNX

ONNX version of [LFM3-Omni-2B](https://huggingface.co/LiquidAI/LFM3-Omni-2B) for cross-platform inference with ONNX Runtime.

Find architecture and usage details in the [original model card](https://huggingface.co/LiquidAI/LFM3-Omni-2B).

## Files
<hr style="margin: -0.7em 0 1.2em 0; border: 0; border-top: 1px solid rgba(127, 127, 127, 0.3);" />

Each precision pairs a decoder with a vision encoder, an audio encoder and a codec decoder. Every precision also runs the float32 [`depth_decoder`](https://huggingface.co/LiquidAI/LFM3-Omni-2B-ONNX/blob/main/onnx/depth_decoder.onnx), which the sizes include.

<div role="region" aria-label="Files" tabindex="0" style="width: 100%; max-width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch;">
<table style="width: 100%; display: table; border: 0; border-radius: 0; border-collapse: collapse;">
<thead style="border: 0;">
<tr style="border: 0;">
<th scope="col" style="text-align: left; padding: 0.55em 0.75em; border: 0; border-bottom: 1px solid rgba(127, 127, 127, 0.55);">Precision</th>
<th scope="col" style="text-align: left; padding: 0.55em 0.75em; border: 0; border-bottom: 1px solid rgba(127, 127, 127, 0.55);">Decoder</th>
<th scope="col" style="text-align: left; padding: 0.55em 0.75em; border: 0; border-bottom: 1px solid rgba(127, 127, 127, 0.55);">Vision encoder</th>
<th scope="col" style="text-align: left; padding: 0.55em 0.75em; border: 0; border-bottom: 1px solid rgba(127, 127, 127, 0.55);">Audio encoder</th>
<th scope="col" style="text-align: left; padding: 0.55em 0.75em; border: 0; border-bottom: 1px solid rgba(127, 127, 127, 0.55);">Codec decoder</th>
<th scope="col" style="text-align: left; padding: 0.55em 0.75em; border: 0; border-bottom: 1px solid rgba(127, 127, 127, 0.55);">Size</th>
<th scope="col" style="text-align: left; padding: 0.55em 0.75em; border: 0; border-bottom: 1px solid rgba(127, 127, 127, 0.55);">Device</th>
</tr>
</thead>
<tbody style="border: 0;">
<tr style="border: 0;">
<td style="border: 0; padding: 0.55em 0.75em;"><code>q4</code></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-Omni-2B-ONNX/blob/main/onnx/decoder_q4.onnx"><code>decoder_q4</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-Omni-2B-ONNX/blob/main/onnx/vision_encoder_q8.onnx"><code>vision_encoder_q8</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-Omni-2B-ONNX/blob/main/onnx/audio_encoder.onnx"><code>audio_encoder</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-Omni-2B-ONNX/blob/main/onnx/codec_decoder.onnx"><code>codec_decoder</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;">2.91 GB</td>
<td style="border: 0; padding: 0.55em 0.75em;">CPU</td>
</tr>
<tr style="border: 0;">
<td style="border: 0; padding: 0.55em 0.75em;"><code>q8</code></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-Omni-2B-ONNX/blob/main/onnx/decoder_q8.onnx"><code>decoder_q8</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-Omni-2B-ONNX/blob/main/onnx/vision_encoder_fp16.onnx"><code>vision_encoder_fp16</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-Omni-2B-ONNX/blob/main/onnx/audio_encoder.onnx"><code>audio_encoder</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-Omni-2B-ONNX/blob/main/onnx/codec_decoder.onnx"><code>codec_decoder</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;">3.73 GB</td>
<td style="border: 0; padding: 0.55em 0.75em;">CPU</td>
</tr>
<tr style="border: 0;">
<td style="border: 0; padding: 0.55em 0.75em;"><code>q4f16</code></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-Omni-2B-ONNX/blob/main/onnx/decoder_q4f16.onnx"><code>decoder_q4f16</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-Omni-2B-ONNX/blob/main/onnx/vision_encoder_fp16.onnx"><code>vision_encoder_fp16</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-Omni-2B-ONNX/blob/main/onnx/audio_encoder_fp16.onnx"><code>audio_encoder_fp16</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-Omni-2B-ONNX/blob/main/onnx/codec_decoder_fp16.onnx"><code>codec_decoder_fp16</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;">2.47 GB</td>
<td style="border: 0; padding: 0.55em 0.75em;">GPU</td>
</tr>
<tr style="border: 0;">
<td style="border: 0; padding: 0.55em 0.75em;"><code>q8f16</code></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-Omni-2B-ONNX/blob/main/onnx/decoder_q8f16.onnx"><code>decoder_q8f16</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-Omni-2B-ONNX/blob/main/onnx/vision_encoder_fp16.onnx"><code>vision_encoder_fp16</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-Omni-2B-ONNX/blob/main/onnx/audio_encoder_fp16.onnx"><code>audio_encoder_fp16</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-Omni-2B-ONNX/blob/main/onnx/codec_decoder_fp16.onnx"><code>codec_decoder_fp16</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;">3.12 GB</td>
<td style="border: 0; padding: 0.55em 0.75em;">GPU</td>
</tr>
<tr style="border: 0;">
<td style="border: 0; padding: 0.55em 0.75em;"><code>fp16</code></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-Omni-2B-ONNX/blob/main/onnx/decoder_fp16.onnx"><code>decoder_fp16</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-Omni-2B-ONNX/blob/main/onnx/vision_encoder_fp16.onnx"><code>vision_encoder_fp16</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-Omni-2B-ONNX/blob/main/onnx/audio_encoder_fp16.onnx"><code>audio_encoder_fp16</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-Omni-2B-ONNX/blob/main/onnx/codec_decoder_fp16.onnx"><code>codec_decoder_fp16</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;">4.68 GB</td>
<td style="border: 0; padding: 0.55em 0.75em;">GPU</td>
</tr>
<tr style="border: 0;">
<td style="border: 0; padding: 0.55em 0.75em;"><code>fp32</code></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-Omni-2B-ONNX/blob/main/onnx/decoder.onnx"><code>decoder</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-Omni-2B-ONNX/blob/main/onnx/vision_encoder.onnx"><code>vision_encoder</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-Omni-2B-ONNX/blob/main/onnx/audio_encoder.onnx"><code>audio_encoder</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;"><a href="https://huggingface.co/LiquidAI/LFM3-Omni-2B-ONNX/blob/main/onnx/codec_decoder.onnx"><code>codec_decoder</code></a></td>
<td style="border: 0; padding: 0.55em 0.75em;">9.03 GB</td>
<td style="border: 0; padding: 0.55em 0.75em;">Reference</td>
</tr>
</tbody>
</table>
</div>

The fp16, q4f16 and q8f16 decoders run with fp16 activations, the others with fp32. q4 and q4f16 keep the embedding, the LM head and 27 of the 153 layer projections in int8 and the rest in int4. Every decoder keeps the speech-frame codebook table in float.

## Use with ONNX Runtime
<hr style="margin: -0.7em 0 1.2em 0; border: 0; border-top: 1px solid rgba(127, 127, 127, 0.3);" />

The vision encoder maps each image, or each temporal patch of a video, to projected features, and the audio encoder maps the log-mel features of 16 kHz audio to audio features. The decoder takes them at the `<|image|>`, `<|video|>` and `<|audio|>` placeholders of `input_ids` (`image_features`, `video_features`, `audio_features`), and the codes of generated speech frames at the `<|audio_out|>` placeholders (`audio_out_codes`). Each step also takes `attention_mask`, `position_ids`, `logits_to_keep` and the convolution and attention caches (`past_conv.N`, `past_key_values.N.key` and `.value`). It returns `logits`, the final-norm `hidden_states` and the updated caches (`present_conv.N`, `present.N.key` and `.value`). The fp16, q4f16 and q8f16 decoders keep their caches in fp16.

For speech, the depth decoder predicts the 12 codes of each speech frame from the decoder's hidden state in 3 MaskGIT passes, and the codec decoder maps the codes to STFT features that an inverse STFT turns into 24 kHz audio. For preprocessing and the inverse STFT, use the values in `processor_config.json` and `audio_tokenizer/config.json`.

On GPU, use fp16, q4f16 or q8f16. The CUDA execution provider has no fp32 GroupQueryAttention kernel, so the other graphs run attention on the CPU.

In the browser, use `import * as ort from 'onnxruntime-web/webgpu'`: the default `onnxruntime-web` import cannot run these graphs. On WebGPU, pair `decoder_q4f16` with the float32 side graphs of the q4 bundle (`vision_encoder_q8`, `audio_encoder`, `codec_decoder`), and prefill in chunks of at most 1,024 tokens.

Recommended sampling: greedy decoding for text, and `audio_temperature=0.4, audio_repetition_penalty=1.2, audio_repetition_window=64` for speech or interleaved output. More options in the [ONNX guide](https://docs.liquid.ai/deployment/on-device/onnx).
