---
license: apache-2.0
language:
- en
- es
- pt
- de
- fr
- zh
- ja
- ru
- ko
pipeline_tag: audio-to-audio
tags:
- speech
- full-duplex
- speech-to-speech
- haru
---

# Haru-4B

[Code](https://github.com/higurashi-ai/Haru) ·
[Technical blog](https://blog.higurashi.ai/melte/haru4b/) ·
[Technical blog (Japanese)](https://blog.higurashi.ai/melte/haru4b-ja/)

Haru-4B is a low-latency and multilingual full-duplex continuous spoken dialogue foundation
model: it listens and speaks at the same time, the way people do on a phone call. It is a
pretrained base model, released for research.

- **Native full-duplex at 7.5 Hz.** Every 133 ms the model takes one frame of the user's audio
  and produces one frame of its own audio and text. User audio, model audio and model text are
  summed into a single position of the language model, so listening and speaking never wait
  for each other.
- **Continuous audio, one sampling step.** Audio is a 64-dimensional latent per frame. A small
  MLP head with MeanFlow sampling produces it in one step from the language model's hidden
  state; there is no separate audio transformer and there are no discrete audio tokens.
- **Character and voice control.** A text card sets the character, a short reference clip sets
  the voice.
- **Multilingual.** English, Spanish, Portuguese, German, French, Chinese, Japanese, Russian
  and Korean.

<p align="center">
  <img src="assets/architecture.svg" width="720" alt="Haru-4B architecture: in every 133 ms frame, the user's speech latent, the model's previous text token and its previous speech latent are summed into one position of the language model, whose output predicts the next text token and the next speech latent.">
</p>

## Usage

The code is at <https://github.com/higurashi-ai/Haru>: a PyTorch implementation with a
web demo, an MLX implementation with a native macOS app, and a plugin for serving with
vLLM-Omni.

With an NVIDIA GPU (about 12 GB of memory):

```bash
git clone https://github.com/higurashi-ai/Haru && cd Haru
pip install -e ./haru
python -m haru.server --hf-repo higurashi-ai/Haru-4B
```

Open <http://localhost:8998>, pick a character, give the model a voice, and talk. For low
latency, we recommend wired headphones and a wired microphone. Bluetooth devices add 100 ms or
more.

**Bring your own voice.** Haru ships without voice clips. Upload or record a clip of the voice
the model should speak with: one speaker, 3 seconds to 3 minutes, clean speech. Only use voices
you have the right to use.

```python
from haru import DuplexSession, HaruModel
from haru.audio import load_audio, prepare_reference

model = HaruModel.from_pretrained("higurashi-ai/Haru-4B")
voice = prepare_reference(load_audio("voice.wav"))
session = DuplexSession(model, system_prompt=card, reference=voice)

frame = session.start()                  # the model's first 133 ms: frame.audio, frame.text
while not frame.done:
    frame = session.step(user_audio)     # 3200 samples at 24 kHz in, 3200 out
```

The system prompt is a character card with a fixed layout; the repository's README shows how
to build one.

## Files

| File | Contents |
| --- | --- |
| `model.safetensors` | All weights (bfloat16): `lm.*` language model, `model_audio_proj.*` / `user_audio_proj.*` audio input projections, `head.*` MeanFlow head, `codec.*` audio codec |
| `config.json` | Model configuration |
| `tokenizer.json` | Text tokenizer |

## Architecture

| Part | Details |
| --- | --- |
| Language model | Qwen3.5-4B text backbone, 32 layers, hidden size 2560 |
| Audio codec | VibeVoice acoustic tokenizer, causal, 24 kHz, 3200 samples per latent |
| Audio head | 4-layer MLP, MeanFlow, 1 sampling step, guidance scale 1 to 2 (default 1.5) |
| Frame rate | 7.5 Hz |

## Limitations

Haru-4B is a pretrained base model, released for research. It holds a natural conversation,
but it is not a reliable source of facts and does not follow instructions the way an assistant
does. It can imitate the voice of a reference clip; only use voices you have the right to use.

## License

The weights are released under the Apache License 2.0.
