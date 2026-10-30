---
library_name: mlx
license: other
license_name: lfm1.0
license_link: LICENSE
pipeline_tag: any-to-any
tags:
- liquid
- lfm3
- lfm3-omni
- omni
- audio
- vision
- speech-to-speech
- mlx
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
    <a href="https://playground.liquid.ai/" style="display: inline-flex; align-items: center; gap: 6px;"><img src="https://huggingface.co/LiquidAI/LFM3-1.7B/resolve/main/assets/icons/try.svg" width="16" height="16" alt="" aria-hidden="true" style="display: block; flex: 0 0 16px; width: 16px; height: 16px; margin: 0;" /><strong>Try</strong></a>
    <a href="https://docs.liquid.ai/lfm/getting-started/welcome" style="display: inline-flex; align-items: center; gap: 6px;"><img src="https://huggingface.co/LiquidAI/LFM3-1.7B/resolve/main/assets/icons/docs.svg" width="16" height="16" alt="" aria-hidden="true" style="display: block; flex: 0 0 16px; width: 16px; height: 16px; margin: 0;" /><strong>Docs</strong></a>
    <a href="https://www.liquid.ai/blog/lfm3" style="display: inline-flex; align-items: center; gap: 6px;"><img src="https://huggingface.co/LiquidAI/LFM3-1.7B/resolve/main/assets/icons/blog.svg" width="16" height="16" alt="" aria-hidden="true" style="display: block; flex: 0 0 16px; width: 16px; height: 16px; margin: 0;" /><strong>Blog</strong></a>
  </div>
</div>

# LFM3-Omni-2B-MLX-4bit

4-bit MLX version of [LFM3-Omni-2B](https://huggingface.co/LiquidAI/LFM3-Omni-2B) for Apple Silicon. The weights take 1.96 GB, plus 0.15 GB for the audio codec in `audio_tokenizer/`.

The language backbone uses 4-bit affine quantization with groups of 64, except for the token embedding (also the output layer) and the value and feed-forward down projections in the layers that llama.cpp's Q4_K_M upgrades, which use 6 bits. The vision tower and projector, the audio encoder and projector, the depth decoder and the audio codec in `audio_tokenizer/` stay in BF16. It is the smallest variant and loses some accuracy, so [8-bit](https://huggingface.co/LiquidAI/LFM3-Omni-2B-MLX-8bit) is the recommended trade-off.

Find architecture and usage details in the [original model card](https://huggingface.co/LiquidAI/LFM3-Omni-2B).

## Use with MLX-VLM
<hr style="margin: -0.7em 0 1.2em 0; border: 0; border-top: 1px solid rgba(127, 127, 127, 0.3);" />

Support for LFM3-Omni in [MLX-VLM](https://github.com/Blaizzy/mlx-vlm) is in progress. We will update this card when an MLX-VLM release supports it.

```bash
# Speech recognition
python -m mlx_vlm.generate --model LiquidAI/LFM3-Omni-2B-MLX-4bit --temperature 0 \
  --system "Perform ASR in English." --prompt "" --audio speech.wav

# Text to speech (24 kHz WAV)
python -m mlx_vlm.generate --model LiquidAI/LFM3-Omni-2B-MLX-4bit \
  --system "Perform TTS in American English using a female voice." \
  --prompt "The quick brown fox jumps over the lazy dog." \
  --output-modality audio --output tts.wav

# Image question
python -m mlx_vlm.generate --model LiquidAI/LFM3-Omni-2B-MLX-4bit --temperature 0 \
  --system "Answer questions about the provided image. Base every answer only on what can be seen. Respond with text." \
  --image image.png --prompt "What does the sign say?"
```

Text answers use greedy decoding (`--temperature 0`). Speech output samples with `audio_temperature=0.4` and `audio_repetition_penalty=1.2` by default. The original model card lists the voices, languages and other system prompts. More options in the [MLX guide](https://docs.liquid.ai/deployment/on-device/mlx).
