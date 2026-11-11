---
license: apache-2.0
pipeline_tag: text-to-speech
library_name: zonos3
language:
  - en
  - fr
  - es
  - de
  - it
  - pt
  - nl
  - pl
  - ru
  - sv
  - tr
  - uk
  - cs
  - da
  - fi
  - el
  - hu
  - "no"
  - ro
  - sk
  - bg
  - zh
  - ja
  - ko
  - ar
  - he
  - vi
  - id
  - th
  - tl
  - ms
  - ka
  - hi
  - bn
  - ta
  - te
  - mr
  - gu
  - kn
  - ml
  - pa
  - or
  - ur
tags:
  - text-to-speech
  - tts
  - speech-synthesis
  - voice-cloning
  - multilingual
  - gguf
  - zonos
  - zonos3
  - zyphra
---

# ZONOS3-Mini

<p align="center">
  <img src="./assets/zonos3_mini_hero.webp" alt="ZONOS3-Mini title card" width="750" />
</p>

<div align="center">
  <a href="https://discord.gg/gTW9JwST8q" target="_blank">
    <img src="https://img.shields.io/badge/Join%20Our%20Discord-7289DA?style=for-the-badge&logo=discord&logoColor=white" alt="Discord">
  </a>
</div>

---

ZONOS3-Mini is Zyphra’s compact, open-source text-to-speech model, delivering natural reliable speech and high-fidelity voice
cloning with just 100 million active parameters. It runs in real time on phones and laptops, including directly in the browser.

Clone a voice from an audio sample, or design a character by shaping age, accent and vocal texture. Steer emotion, pace and
delivery down to individual words for games, narration and voice assistants.

Run ZONOS3-Mini locally with our browser and native runtimes, or access it through [Zyphra Cloud](https://cloud.zyphra.com/models/zyphra/ZONOS3-Mini). Model weights are released
under Apache 2.0.

**Model details, samples and evaluations are in the [blog post](https://www.zyphra.com/our-work/zonos3-mini).**
**A hosted version is available at [Zyphra Cloud](https://cloud.zyphra.com/models/zyphra/ZONOS3-Mini).**

## Languages

ZONOS3-Mini supports 43 languages.

| Region | Languages |
|---|---|
| Western and Southern Europe | English, French, Spanish, German, Italian, Portuguese, Dutch, Greek |
| Northern Europe | Swedish, Danish, Norwegian, Finnish |
| Central and Eastern Europe | Polish, Czech, Slovak, Hungarian, Romanian, Bulgarian, Russian, Ukrainian |
| Middle East and Caucasus | Turkish, Arabic, Hebrew, Georgian |
| East Asia | Mandarin Chinese, Japanese, Korean |
| Southeast Asia | Vietnamese, Indonesian, Malay, Thai, Tagalog |
| South Asia | Hindi, Bengali, Tamil, Telugu, Marathi, Gujarati, Kannada, Malayalam, Punjabi, Odia, Urdu |

## Quick start

### In your browser

ZONOS3-Mini runs entirely on your own GPU through WebGPU: no install and no server. The first visit downloads the model, and
the browser keeps it cached for later visits. You need a browser with WebGPU support.

- [Zonos Voice Studio](https://zyphra.com/zonos3-mini/localplayground#cast): the full studio.
- [Lite](https://zyphra.com/zonos3-mini/localplayground/lite): quick voice cloning. Pick, upload or record a voice and
  speak with it.

### With SGLang

[SGLang-Omni](https://github.com/sgl-project/sglang-omni) serves ZONOS3-Mini on NVIDIA GPUs behind the OpenAI-compatible
`/v1/audio/speech` endpoint, with streaming and voice cloning from a reference clip (no transcript needed). Install SGLang-Omni
by following its [installation guide](https://github.com/sgl-project/sglang-omni/blob/main/docs/get_started/installation.md),
then start a server:

```bash
sgl-omni serve --model-path Zyphra/ZONOS3-Mini --port 8000
```

Once `curl http://localhost:8000/health` reports `"healthy"`, generate speech with the default voice from another terminal:

```bash
curl -X POST http://localhost:8000/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"input": "Hello from ZONOS3-Mini!"}' \
  --output output.wav
```

Add `"references": [{"audio_path": "<reference clip>"}]` to clone a voice, or `"stream": true, "response_format": "pcm"` to
stream 44.1 kHz audio as it is generated. The [ZONOS3 cookbook](https://github.com/sgl-project/sglang-omni/blob/main/docs/cookbook/zonos3.md)
covers voice cloning, streaming and the sampling options.

### Native runtimes

The [ZONOS3-Mini GitHub repository](https://github.com/Zyphra/ZONOS3-Mini) holds Zyphra's own runtimes, which share one HTTP
API and serve the Zonos Voice Studio: a CUDA server for NVIDIA GPUs, an MLX server for Apple Silicon, a C++/ggml server and CLI
(CPU, CUDA, Metal or Vulkan), and the in-browser WebGPU build.
They load the GGUF files in [`gguf/`](https://huggingface.co/Zyphra/ZONOS3-Mini/tree/main/gguf) and the WebGPU shards in
[`web/`](https://huggingface.co/Zyphra/ZONOS3-Mini/tree/main/web); the 27 default voices, grouped by language, are in
[`gguf/voices/`](https://huggingface.co/Zyphra/ZONOS3-Mini/blob/main/gguf/voices/README.md).

## Architecture (rough outline)

Text is NeMo-normalized (English, Mandarin, Japanese, Korean, German, Spanish, French, Italian, Portuguese; other languages
pass through as written) and encoded with an 8,192-entry SuperBPE tokenizer. The language model autoregressively generates 15 Hz
dual-codec frames (10 codebooks of 4,096 entries each: one semantic codebook distilled from the Wav2Vec2-BERT teacher plus nine
acoustic codebooks), conditioned on the text, a speaker prompt and three bucketed acoustic controls. A causal multi-resolution
iSTFT head renders the frames to 44.1 kHz audio as they are produced.

![ZONOS3-Mini inference pipeline](assets/zonos3_mini_arloop_animated.gif)

*Inference pipeline. Speaker conditioning (raw reference codes or a speaker-embedding row), control rows and SuperBPE text enter
the hybrid GDN/attention MoE backbone, which predicts all ten codebooks of the next frame under the delay pattern (four drawn);
as soon as a frame's last codebook is generated, the codec's causal iSTFT head renders that frame to audio.*

| | ZONOS3-Mini |
|---|---|
| Backbone | 11 layers, model dim 1,024; 800M parameters total, about 100M active per token (latent MoE: 64 experts, top-2 routing, from layer 1) |
| Sequence mixer | pattern `gdn3_global1`: 9 Gated DeltaNet (GDN) layers + 2 global attention layers (16 heads x 32 dims, 4 KV heads), attention residuals (`full_attnres`); context 32,768 positions |
| Text | SuperBPE, 8,192 entries (+4 specials), NeMo text normalization at inference |
| Audio tokens | 15 Hz, 44.1 kHz dual-encoding codec, 10 x 4,096 codebooks (1 semantic + 9 acoustic); hop 2,940 samples; up to 10 minutes of audio per generation |
| Speaker prompt | raw reference clip (encoded by the codec into a token prefix, up to 60 s, with speaker CFG) **or** a single 192-d MECT-B2 speaker embedding (`ownmect_s2`) |
| Controls | loudness (LUFS, 10 buckets), bandwidth f_max (Hz, 10 buckets), **speaking rate (UTF-8 bytes per second, 30 buckets)**; each row has an "unknown" index; bucket edges in `params.json` (`condition_edges`) |
| Decoder | causal multi-resolution iSTFT head: `full_mr` (default) or `keep2_plain` (pruned, about 3x faster decode) |
| Precision | bf16 weights (`model.pth`); decoder heads run in fp32 |

![ZAC 1 audio codec](assets/arch_zac1_dark.png)

*ZAC 1 (Zyphra Audio Codec 1).*

## Evaluations

Full results and the ZTTS2 capability index are in the [blog post](https://www.zyphra.com/our-work/zonos3-mini#evaluation).

Quality against speed on one H100 80GB. Quality combines macro speaker similarity and macro WER over the 55 language subsets of
[ZTTS2-Eval](https://zyphra.github.io/ZTTS2-Eval/) (benchmark version 2: FLEURS-R read speech in 35 languages and VoxBlink2 in-the-wild recordings in 20 languages) as
½ z(similarity) − ½ z(WER) across the systems shown; ZONOS3-Mini is measured with the speaker-embedding prompt. Left: median
time to first audio with 8 concurrent requests (80 ms for ZONOS3-Mini). Right: peak throughput at each model's best concurrency
(457 seconds of audio per second for ZONOS3-Mini).

![ZONOS3-Mini quality versus time to first audio and throughput](assets/eval_pareto.png)

## Citation
If you find this model useful in an academic context, please cite the release post:
```
@misc{zyphra2026zonos3mini,
  title        = {{ZONOS3-Mini Technical Report}},
  author       = {Gabriel Clark and Sofian Mejjoute and George Close and Beren Millidge},
  year         = {2026},
  month        = nov,
  howpublished = {\url{https://www.zyphra.com/our-work/zonos3-mini}},
}
```
