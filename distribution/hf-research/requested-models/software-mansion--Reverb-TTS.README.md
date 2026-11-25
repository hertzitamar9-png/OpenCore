---
license: cc-by-nc-4.0
language:
- en
- de
- fr
- es
- it
- pl
- pt
base_model: k2-fsa/OmniVoice
base_model_relation: finetune
pipeline_tag: text-to-speech
library_name: reverb-tts
tags:
- text-to-speech
- voice-cloning
- zero-shot
- executorch
- on-device
---

<img class="dark:hidden" src="assets/header.png" alt="Reverb-TTS: zero-shot voice cloning with open weights in 7 languages. 297M parameters; 232.3x faster than real time on an H200 and 164.9x on an RTX 5090 at batch 32, 21.6x on an M4 Pro and 20.6x on an iPhone 17; 0.96% word error rate on MiniMax. Software Mansion AI Labs.">
<img class="hidden dark:block" src="assets/header_dark.png" alt="Reverb-TTS: zero-shot voice cloning with open weights in 7 languages. 297M parameters; 232.3x faster than real time on an H200 and 164.9x on an RTX 5090 at batch 32, 21.6x on an M4 Pro and 20.6x on an iPhone 17; 0.96% word error rate on MiniMax. Software Mansion AI Labs.">

# Reverb-TTS

<p align="center">
  <a href="https://github.com/software-mansion-labs/Reverb-TTS">GitHub</a> ·
  <a href="https://huggingface.co/spaces/software-mansion/Reverb-TTS-runntime">Demo</a> ·
  <a href="#usage">Quick start</a> ·
  <a href="#quality-size-and-speed">Benchmarks</a>
</p>

**Reverb** is a **text-to-speech** model designed for high-quality **voice-cloning**. Give it a few seconds of someone speaking and their
transcript, and it speaks any text in that voice, with the speaker's timbre, prosody, and emotion. It's built on one of
the leading open-weight TTS models.

- **Clones a voice from 3 to 20 seconds** of clean speech, with no fine-tuning.
- **Seven languages:** English, German, French, Spanish, Italian, Polish, and Portuguese, and a voice can speak any of
  them.
- **The lowest word error rate** on the Open TTS Leaderboard's MiniMax test set (0.96%).
- **Speed:** 232.3× real time on an NVIDIA H200 and 164.9× on an RTX 5090 at batch 32, over 20× on an Apple M4 Pro and
  iPhone 17 at batch 1.
- **On-device formats:** int8 and int4 weights, and ExecuTorch programs for Apple GPUs (MLX), Android GPUs (Vulkan), and
  Qualcomm NPUs (QNN).

## What is Reverb?

Reverb starts from [OmniVoice](https://github.com/k2-fsa/OmniVoice), a 0.6B masked-diffusion TTS model that fills in
a grid of audio-codec tokens over several passes, conditioned on the text and on a reference speaker's audio. We made
Reverb from it in three stages:

1. **Pruning with distillation.** 4 of the 28 transformer layers are removed, the feed-forward layers shrink from 3072
   to 2048, KV groups from 8 to 6, and the text vocabulary from 151,676 to 62,035 tokens. The pruned model learns to match OmniVoice's
   predictions.
2. **Supervised retraining** in Reverb's seven languages, on cleaned and selected subsets of open speech datasets such
   as [Emilia](https://huggingface.co/datasets/amphion/Emilia-Dataset) and variants of YODAS2, such as
   [yodas2-sidon](https://huggingface.co/datasets/sarulab-speech/yodas2_sidon).
3. **RLHF-style post-training:** several rounds of iterative DPO with an automatic judge fitted on human preference
   rankings.

The result has 297M parameters, less than half of OmniVoice's 613M. Additional performance improvements let it
generate speech 10× to 20× faster than the original model.

<img class="dark:hidden" src="assets/training_scheme.png" alt="How Reverb is made: OmniVoice is pruned and distilled, retrained on seven languages, then refined with iterative DPO against a critic fitted on human preference rankings.">
<img class="hidden dark:block" src="assets/training_scheme_dark.png" alt="How Reverb is made: OmniVoice is pruned and distilled, retrained on seven languages, then refined with iterative DPO against a critic fitted on human preference rankings.">

## Quality, size and speed

We compare Reverb with well-known open-weight models on the
[Open TTS Leaderboard](https://huggingface.co/spaces/hf-audio/open_tts_leaderboard)'s MiniMax test set, in seven
languages: 100 sentences per language, one take each, every model cloning a reference recording. WER (Qwen3-ASR) and
speaker similarity (SIM-o, WavLM) are averaged over the languages.

| Model | Parameters | WER ↓ | SIM-o ↑ |
|:---|---:|---:|---:|
| **Reverb** | **319M** | **1.03%** | 0.817 |
| [OmniVoice](https://huggingface.co/k2-fsa/OmniVoice) | 634M | 1.13% | **0.833** |
| [VoxCPM2](https://huggingface.co/openbmb/VoxCPM2) | 2,336M | 1.20% | 0.812 |
| [Fish S2 Pro](https://huggingface.co/fishaudio/s2-pro) | 4.95B | 1.35% | 0.761 |
| [Audio8-TTS-Realistic](https://huggingface.co/Edge0/Audio8-TTS-Realistic-0.6b) | 732M | 1.46% | 0.764 |
| [MOSS-TTS](https://huggingface.co/OpenMOSS-Team/MOSS-TTS) | 8.49B | 1.84% | 0.774 |
| [Chatterbox](https://huggingface.co/ResembleAI/chatterbox) | 0.80B | 2.76% | 0.715 |
| [Higgs TTS 3](https://huggingface.co/bosonai/higgs-tts-3-4b) | 4.65B | 6.59% | 0.754 |

Generation speed, in × real time (seconds of audio per second of compute; higher is better), with each model's default
settings. The H200 row is the Open TTS Leaderboard's run over all seven MiniMax languages; the other rows are ours, on
MiniMax English sentences, timed from the text and an already-encoded reference voice.

| Hardware | Reverb | OmniVoice | Audio8 | Qwen3-TTS 0.6B | Qwen3-TTS 1.7B | VoxCPM2 | Fish S2 Pro |
|:---|---:|---:|---:|---:|---:|---:|---:|
| NVIDIA H200, batch 32 | **232.3×** | 45.5× | 11.2× | – | – | 1.9× | 0.40×² |
| NVIDIA RTX 5090, batch 32 | **164.9×** | 13.2× | 4.2× | 3.3× | 4.0× | 3.0×² | 0.18×² |
| Apple M4 Pro, batch 1 | **12.3×³** | 0.84× | 0.76× | 0.51× | 0.52× | 0.69× | – |
| iPhone 17, batch 1 | **20.6×** | 0.91× | 1.03× | 1.14× | 0.74× | – | – |

RTX 5090 and M4 Pro: PyTorch in bf16 on the GPU, to the waveform with codec decoding. iPhone 17: ExecuTorch MLX
programs in fp16 on the GPU, to the codec codes, without the decoder. H200: the Open TTS Leaderboard's default voice,
MiniMax test set, 7 languages.<br>
² No batched generation: sentences run one at a time.<br>
³ Reverb's ExecuTorch MLX program (fp16) with the Core ML codec decoder, as it ships: 21.6×.

<img class="dark:hidden" src="assets/frontier_size_speed.png" alt="Speed against size on an RTX 5090, batch 32: every other model is bigger and slower than Reverb-TTS">
<img class="hidden dark:block" src="assets/frontier_size_speed_dark.png" alt="Speed against size on an RTX 5090, batch 32: every other model is bigger and slower than Reverb-TTS">

## Try it

[The demo Space](https://huggingface.co/spaces/software-mansion/Reverb-TTS-runntime) lets you clone a voice from your own recording or try one of the preset voices. It runs Reverb
on your GPU in the browser through ruNNtime, so it needs a browser with WebGPU, such as Chrome or Edge.

## Usage

```bash
pip install reverb-tts
```

### Clone a voice

```python
import soundfile as sf
from reverb_tts import TTS

tts = TTS.from_pretrained("software-mansion/Reverb-TTS")
voice = tts.voice("reference.wav", "The exact transcript of the reference recording.", language="en")
audio = tts.generate("Hello! This is my cloned voice.", voice, language="en")
sf.write("output.wav", audio, tts.sample_rate)
```

The reference works best as 3 to 10 seconds of clean speech from one speaker, with a transcript that matches it word
for word. References over 15 seconds make worse takes.

### Use a preset voice

```python
tts.preset_voices()  # ['de_female_standard', 'de_male_standard', ..., 'pt_male_standard']
voice = tts.preset_voice("en_male_narrator_1")
audio = tts.generate("Hello! This is a preset voice speaking.", voice, language="en")
```

Each name starts with the voice's language: `de`, `en`, `es`, `fr`, `it`, `pl` or `pt`. A voice can speak any of the
seven languages, though it sounds most natural in its own.

### Speak another language

`language` in `generate` is the language of the text, and it can differ from the voice's:

```python
audio = tts.generate("Dzień dobry, jak się masz?", voice, language="pl")
```

### On your Mac with Native

Run it on your Mac with Nativ, from Neywa Labs. You can read more in a [blogpost here](https://runnativ.ai/blog/reverb-on-nativ).

### In the browser with ruNNtime

[ruNNtime](https://docs.swmansion.com/runntime/zoo/text-to-speech/) runs Reverb on the GPU in the browser through
WebGPU. Install it with `npm install runntime` and follow its text-to-speech guide.

### Text preprocessing

Reverb reads text in spoken form: it was trained on text with numbers, dates, money, units, symbols, and abbreviations
written out as words, so normalizing the text is a required step before generating.

- **With the `reverb-tts` package**, it's done for you: `generate` and `voice` run
  [Verbalis](https://github.com/software-mansion-labs/Verbalis) on the text and the transcript by default.
- **With the ExecuTorch programs or ruNNtime**, the text reaches the model exactly as you give it. Normalize it yourself
  first; we strongly recommend Verbalis, with C++, TypeScript, and Python APIs for all seven languages.

Long texts are split at sentence ends automatically. For seeds, speed, batches, and sampler settings, see the [usage
guide](https://github.com/software-mansion-labs/Reverb-TTS/blob/main/docs/usage.md).

## Model variants

### safetensors

PyTorch weights for the `reverb-tts` package, in five variants:

| Variant | Weights | Activations | Size |
|:---|:---|:---|---:|
| `fp32` | fp32 | full precision | 1.19 GB |
| `fp16` | fp16 | full precision | 594 MB |
| `int8` (default) | int8, one scale per output channel | full precision | 307 MB |
| `int8a8` | int8, one scale per output channel | int8, one scale per token | 307 MB |
| `int4` | int4, one scale per group of 32 inputs | full precision | 191 MB |

Select one with `TTS.from_pretrained(..., variant="int4")`. The weights are in `model/safetensors/<variant>/`.
On an internal test set, `int8` showed no quality loss against `fp32`, and `int4` none that we could measure. The
benchmarks above use full precision.

### ExecuTorch

Programs for on-device use are in `model/executorch/`, with the audio codec kept outside the program:

| Backend | Hardware | Precisions |
|:---|:---|:---|
| MLX | Apple GPUs | fp16, int8, int4 |
| Vulkan | Android GPUs | int4 |
| QNN | Qualcomm NPUs, one program per chip: Snapdragon 8 Gen 1 (SM8450), 8 Gen 2 (SM8550), 8 Gen 3 (SM8650), 8 Elite (SM8750), 8 Elite Gen 5 (SM8850) | int4 |

The programs take text exactly as given: normalize it first, ideally with Verbalis's C++ API (see [Text
preprocessing](#text-preprocessing)).

`voices/` holds the 16 preset voices, covering all seven languages, as ready-encoded codec codes.

## Limitations and responsible use

- Reverb copies the voice in the reference, including its recording conditions: noise, room echo, and microphone
  coloring carry over into the output.
- It has no style or emotion control apart from the reference: to get an angry take, use an angry reference.
- It speaks only its seven languages. Text in other languages or scripts gives unpredictable output.
- **Clone only voices you have the right to use.** Get the speaker's consent, don't impersonate real people, and don't
  use generated speech to deceive, defraud, or harass. Tell your listeners when they hear a synthetic voice.

## License and acknowledgments

- **Code:** Apache 2.0; see [LICENSE](https://github.com/software-mansion-labs/Reverb-TTS/blob/main/LICENSE).
- **Model weights:** [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/), for non-commercial use with
  attribution.

Reverb's weights were initialized from [OmniVoice](https://github.com/k2-fsa/OmniVoice) by the k2-fsa team, and its
target-length rule and prompt format come from OmniVoice's code. We thank its authors for releasing the model and code
openly. Reverb uses the [Higgs Audio v2 tokenizer](https://huggingface.co/eustlb/higgs-audio-v2-tokenizer) as its audio
codec, and [Verbalis](https://github.com/software-mansion-labs/Verbalis) prepares its text.

## Community Discord

[Join the Software Mansion Community Discord](https://discord.swmansion.com) to chat about Reverb or other Software
Mansion libraries.

## Reverb is created by Software Mansion AI Labs

<a href="https://ai.swmansion.com/?utm_source=reverb&utm_medium=huggingface">
<img class="dark:hidden" src="assets/swm_ai_labs.svg" alt="Software Mansion AI Labs" width="122" height="80" align="left">
<img class="hidden dark:block" src="assets/swm_ai_labs_dark.svg" alt="Software Mansion AI Labs" width="122" height="80" align="left">
</a>

We're an end-to-end AI development lab focused on local and hybrid AI. We build and fine-tune models, deploy them
anywhere and turn them into agents that solve real business problems.
[Hire us](https://ai.swmansion.com/?utm_source=reverb&utm_medium=huggingface#contact).
