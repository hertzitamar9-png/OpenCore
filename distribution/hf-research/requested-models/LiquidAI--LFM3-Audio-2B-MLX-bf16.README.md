---
library_name: mlx
license: other
license_name: lfm1.0
license_link: LICENSE
pipeline_tag: any-to-any
tags:
- liquid
- lfm3
- mlx
- audio
- speech
language:
- en
- ar
- da
- de
- el
- fi
- fr
- he
- it
- ja
- ko
- nl
- pl
- pt
- es
- sv
- tr
- zh
base_model: LiquidAI/LFM3-Audio-2B
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

# LFM3-Audio-2B-MLX-bf16

BF16 MLX version of [LFM3-Audio-2B](https://huggingface.co/LiquidAI/LFM3-Audio-2B) for Apple Silicon. The weights take 3.96 GB, plus 0.15 GB for the audio codec in `audio_tokenizer/`.

Weights are stored in BF16, as in the original checkpoint. The audio codec is the original one.

Find architecture and usage details in the [original model card](https://huggingface.co/LiquidAI/LFM3-Audio-2B).

## Use with MLX-VLM
<hr style="margin: -0.7em 0 1.2em 0; border: 0; border-top: 1px solid rgba(127, 127, 127, 0.3);" />

Support for LFM3-Audio in [MLX-VLM](https://github.com/Blaizzy/mlx-vlm) is in progress. We will update this card when an MLX-VLM release supports it.

```bash
# Speech recognition
python -m mlx_vlm.generate --model LiquidAI/LFM3-Audio-2B-MLX-bf16 \
  --system "Perform ASR in English." --prompt "" --audio <AUDIO PATH> \
  --temperature 0 --max-tokens 2048

# Text to speech
python -m mlx_vlm.generate --model LiquidAI/LFM3-Audio-2B-MLX-bf16 \
  --system "Perform TTS in American English using a female voice." \
  --prompt "Hello there!" --output-modality audio --output tts.wav

# Speech-to-speech chat
python -m mlx_vlm.generate --model LiquidAI/LFM3-Audio-2B-MLX-bf16 \
  --system "Respond with interleaved text and audio." --prompt "" \
  --audio <AUDIO PATH> --output-modality audio --max-tokens 1024 --output answer.wav
```

Speech output samples with `audio_temperature=1.0`, `audio_top_k=128` and `audio_repetition_penalty=1.1`, the defaults in `generation_config.json`. The original model card lists the voices, languages and other system prompts. More options in the [MLX guide](https://docs.liquid.ai/deployment/on-device/mlx).
