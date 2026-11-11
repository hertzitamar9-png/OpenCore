---
license: other
language:
  - en
  - zh
pipeline_tag: text-to-speech
tags:
  - audio
  - speech-editing
  - text-to-speech
  - safetensors
---

# dots.tts.edit V1.1

**dots.tts.edit V1.1** is a continuous autoregressive model for precise,
instruction-controlled speech editing and zero-shot text-to-speech synthesis.
It supports text replacement, insertion and deletion, emotion and prosody
control, pauses, enhancement, and background-audio operations while preserving
the speaker and the acoustic context outside edited regions.

- [Code](https://github.com/studio-dots-ai/dots.tts/tree/main)
- [Paper](https://arxiv.org/abs/2608.02673)
- [Interactive Playground](https://dots-studio-dots-tts-edit.hf.space)
- [Data Pipeline](https://github.com/studio-dots-ai/dots.tts.edit-data-pipeline)
- [Demo Page](https://huggingface.co/spaces/dots-studio/dots.tts.edit-demo)

V1.1 adds free-form style editing and improves prosody and emotion editing.

## Model details

The model supports English and Mandarin speech editing and zero-shot speech
synthesis. It produces 48 kHz audio. The core weights use BF16; the speaker
encoder and vocoder retain FP32 weights.

Supported editing operations include:

- Text replacement, insertion, and deletion
- Localized emotion control and free-form style descriptions
- Pitch and speaking-rate control
- Pause insertion and reduction
- Speech enhancement and background-audio operations
- Compositions of multiple edit operations

## Installation

Install the latest `main` branch of
[dots.tts](https://github.com/studio-dots-ai/dots.tts) and its recommended
dependencies:

```bash
git clone --branch main --single-branch https://github.com/studio-dots-ai/dots.tts.git
cd dots.tts
python -m pip install -e . -c constraints/recommended.txt
```

## Speech editing CLI

Source audio, a tagged instruction, and the output path are required. Source
and target transcripts are optional; when omitted, both are derived from the
instruction.

```bash
dots.tts.edit \
  --model-name-or-path dots-studio/dots.tts.edit \
  --source-audio /path/to/source.wav \
  --instruction 'Hello <sub targ="small">brave</sub> world.' \
  --output edited.wav
```

Use `--source-text` and `--target-text` to override the derived transcripts.
Source-speaker guidance defaults to `auto`: it is disabled when the instruction
contains at least one operation and every operation is `emo`, `bg`, or
`enhance`, and enabled for text, pitch, rate, pause, speaker-transfer, or mixed
edits. Pass bare `--use-xvector` (or
`--use-xvector on`) to force it on, and `--use-xvector off` to force it off.
Speaker guidance uses a default scale of `1.5`; override it with
`--speaker-scale` when needed.

## Python API

```python
import soundfile as sf

from dots_tts.edit_runtime import DotsTtsEditRuntime

runtime = DotsTtsEditRuntime.from_pretrained(
    "dots-studio/dots.tts.edit",
    precision="bfloat16",
)
result = runtime.generate_edit(
    source_audio_path="/path/to/source.wav",
    instruction='Hello <sub targ="small">brave</sub> world.',
    # source_text and target_text are optional overrides.
    # use_xvector defaults to "auto"; pass True or False to override it.
)
sf.write(
    "edited.wav",
    result["audio"].float().cpu().squeeze().numpy(),
    result["sample_rate"],
)
```

Supported structural tags include `<del>`, `<ins>`,
`<sub targ="replacement">`, `<emo>`, `<pitch>`, `<rate>`, `<enhance>`,
`<bg>`, `<pause/>`, and `<spk_transfer/>`. Malformed instructions and
instructions that derive an empty source or target transcript are rejected.

Free-form style descriptions use the `desc` attribute, for example:

```xml
She said, <emo desc="quiet, warm delivery with an audible smile">it is good to see you again.</emo>
```

`DotsTtsEditRuntime` also provides `generate` and `generate_stream` for
zero-shot TTS with the same checkpoint. See the
[code repository](https://github.com/studio-dots-ai/dots.tts) for examples.

## Local Edit Playground

Build the committed frontend source with Node.js 20+ and launch the local app:

```bash
cd apps/edit_playground/frontend
npm ci
npm run build
cd ../../..
python apps/edit_playground/app.py \
  --model-name-or-path dots-studio/dots.tts.edit \
  --optimize
```

## Limitations and responsible use

Generated or edited speech may contain pronunciation, timing,
speaker-similarity, or instruction-following errors. Do not use the model to
impersonate people, mislead listeners, violate consent, or create unlawful
content. Users are responsible for complying with applicable laws.

Please obtain explicit consent from the relevant speakers for the intended
use of their voices in speech synthesis or editing.

Do not present generated or edited audio as an authentic, unmodified
recording, or use localized edits to misrepresent a speaker's original
meaning or intent.

When publicly sharing audio that could reasonably be mistaken for an
authentic, unmodified recording, clearly disclose that it has been
AI-generated or edited.

To report suspected impersonation, fraud, or other misuse involving this
model, please contact the project team through the model repository's
[Community discussions](https://huggingface.co/dots-studio/dots.tts.edit/discussions).

## License

See the repository license metadata and the
[`dots.tts`](https://github.com/studio-dots-ai/dots.tts) project for the terms
that apply to the weights and code.
