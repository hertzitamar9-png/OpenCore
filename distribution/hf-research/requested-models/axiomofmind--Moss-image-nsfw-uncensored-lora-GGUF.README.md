---
license: apache-2.0
base_model: axiomofmind/Moss-Image-SFT-GGUF
base_model_relation: adapter
pipeline_tag: text-to-image
instance_prompt: "moss_xvstyle"
tags:
- moss-image
- gguf
- lora
- stable-diffusion.cpp
- text-to-image
- image-generation
- nsfw
- adult-content
- adapter
---

# MOSS-Image NSFW Uncensored LoRA GGUF

Separate F16 GGUF conversions of the adult-photography LoRAs from [axiomofmind/Moss-image-nsfw-uncensored-lora](https://huggingface.co/axiomofmind/Moss-image-nsfw-uncensored-lora). They are designed to be applied at inference time to [axiomofmind/Moss-Image-SFT-GGUF](https://huggingface.co/axiomofmind/Moss-Image-SFT-GGUF), keeping the adapter strength adjustable without creating a merged model for every base quantization or scale.

This repository contains the **LoRA adapter only**. It does not contain the base diffusion transformer, Qwen3.5 text encoder, tokenizer, or VAE.

> **Adult-content notice:** The source adapter is intended for lawful creative generation involving clearly adult fictional subjects and can produce explicit sexual imagery. It is for adults only in jurisdictions where such content is lawful. Do not use it for minors or age-ambiguous subjects, abuse, exploitation, coercion, non-consensual imagery, or sexualized depictions of identifiable real people without explicit consent.

## File

| File | Size | Tensors | SHA-256 |
| --- | ---: | ---: | --- |
| `MOSS-Image-NSFW-Uncensored-LoRA-F16.gguf` | 70.9 MiB | 504 F16 LoRA tensors | `2C3E044A3331A94416ED91610CCD5135DF0F79F0CB3D5DB5983DE0FB5DFA5603` |
| `MOSS-Image-NSFW-Uncensored-LoRA-Alternative-1750-F16.gguf` | 70.9 MiB | 504 F16 LoRA tensors | `6366D060B9653C5B72C0694238A09B01787F3CA610E7F27AF051A5AED8FFF7D8` |

Both adapters use rank 16 and alpha 16. Their trigger phrase is `moss_xvstyle`; place it at the beginning of the prompt.

- `MOSS-Image-NSFW-Uncensored-LoRA-Alternative-1750-F16.gguf` corresponds to the source repository's current root/default adapter: the 1,750-step checkpoint from the focused 130-image run. Try scale 0.7, 0.8, or 0.9; 0.8 is a useful starting point. Guidance settings other than CFG 3 were not tested for this GGUF conversion.
- `MOSS-Image-NSFW-Uncensored-LoRA-F16.gguf` preserves the original 2,000-step checkpoint trained on 221 images, which is now archived under `original-checkpoint-2000/` in the source repository. Its recommended scale is 0.7.

## Required components

- A compatible base transformer from [Moss-Image-SFT-GGUF](https://huggingface.co/axiomofmind/Moss-Image-SFT-GGUF). Q8_0 was validated; Q4_K_S uses the same MOSS architecture and LoRA targets.
- A MOSS-enabled [stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp) build.
- A Qwen3.5-9B text encoder, the official MOSS `tokenizer.json`, and the official MOSS / FLUX.2 VAE. See the base GGUF repository for links and setup notes.

## Usage

Put the base GGUF and this LoRA GGUF in accessible locations. The `<lora:...>` tag selects the adapter file and scale; `moss_xvstyle` is the learned trigger sent to the text encoder.

```powershell
sd-cli.exe `
  --diffusion-model MOSS-Image-SFT-Q8_0.gguf `
  --llm text_encoder/model.safetensors.index.json `
  --vae vae/diffusion_pytorch_model.safetensors `
  --tokenizer tokenizer/tokenizer.json `
  --lora-model-dir . `
  --lora-apply-mode at_runtime `
  --cfg-scale 3 `
  --steps 30 `
  --sampling-method euler `
  --scheduler discrete `
  --flow-shift 5 `
  --seed 42 `
  --rng cpu `
  --sampler-rng cpu `
  -W 1024 -H 1024 `
  -p "moss_xvstyle, a full-body photograph of an adult woman in elegant black lingerie standing beside a tall window, soft side light, confident neutral expression, refined boudoir editorial composition. <lora:MOSS-Image-NSFW-Uncensored-LoRA-F16:0.7>" `
  -o moss-adult-gguf.png
```

Quantized transformers automatically select runtime LoRA application. `--lora-apply-mode at_runtime` is explicit here because it is the validated path. Ready-to-edit PowerShell and Bash wrappers are provided under `examples/`.

To use the current default focused checkpoint, replace the LoRA tag with:

```text
<lora:MOSS-Image-NSFW-Uncensored-LoRA-Alternative-1750-F16:0.8>
```

The example wrappers retain the historical selectors `primary` and `alternative`: `alternative` selects the current default focused checkpoint, while `primary` selects the archived original checkpoint.

## Controlled validation

The adapter was tested with `MOSS-Image-SFT-Q8_0.gguf` using seven adult-only explicit prompts drawn from earlier xvstyle evaluations. Rows 2, 4, and 5 come from `xvstyle-last-five-prompt-plan.json`; row 1 comes from the SFT rerun plan; and rows 3, 6, and 7 come from the step-2000 comparison plan. Every comparison row used the same description in both columns, along with the same base model, text encoder, VAE, 1024×1024 resolution, 30 steps, CFG 3, Euler/discrete sampling, flow shift 5, and seed 42.

- The stock prompt omitted both the trigger phrase and LoRA tag.
- The primary adapter prompt added `moss_xvstyle` and `<lora:MOSS-Image-NSFW-Uncensored-LoRA-F16:0.7>`.
- The alternative adapter prompt added `moss_xvstyle` and `<lora:MOSS-Image-NSFW-Uncensored-LoRA-Alternative-1750-F16:0.8>`.
- stable-diffusion.cpp reported all `504 / 504` LoRA tensors applied in every adapter run.

| Prompt | Stock SFT Q8_0 | SFT Q8_0 + Adult LoRA GGUF (primary, 0.7) | SFT Q8_0 + Adult LoRA GGUF (alt checkpoint, 0.8) |
| --- | --- | --- | --- |
| Crouching with smiley-face socks | ![Stock MOSS SFT Q8 crouching adult explicit photograph](images/01-crouching-smiley-socks-stock-seed42.png) | ![MOSS SFT Q8 plus primary adult LoRA crouching adult explicit photograph](images/01-crouching-smiley-socks-lora-seed42.png) | ![MOSS SFT Q8 plus alternative adult LoRA crouching adult explicit photograph](images/01-crouching-smiley-socks-alt-lora-s0p8-seed42.png) |
| Rear view over wooden surface | ![Stock MOSS SFT Q8 rear-view adult explicit photograph](images/02-rear-wooden-surface-stock-seed42.png) | ![MOSS SFT Q8 plus primary adult LoRA rear-view adult explicit photograph](images/02-rear-wooden-surface-lora-seed42.png) | ![MOSS SFT Q8 plus alternative adult LoRA rear-view adult explicit photograph](images/02-rear-wooden-surface-alt-lora-s0p8-seed42.png) |
| Kneeling beside a bedroom mirror | ![Stock MOSS SFT Q8 kneeling mirror adult explicit photograph](images/03-kneeling-mirror-stock-seed42.png) | ![MOSS SFT Q8 plus primary adult LoRA kneeling mirror adult explicit photograph](images/03-kneeling-mirror-lora-seed42.png) | ![MOSS SFT Q8 plus alternative adult LoRA kneeling mirror adult explicit photograph](images/03-kneeling-mirror-alt-lora-s0p8-seed42.png) |
| Dark lingerie on blue fabric | ![Stock MOSS SFT Q8 blue-fabric adult explicit photograph](images/04-blue-fabric-stock-seed42.png) | ![MOSS SFT Q8 plus primary adult LoRA blue-fabric adult explicit photograph](images/04-blue-fabric-lora-seed42.png) | ![MOSS SFT Q8 plus alternative adult LoRA blue-fabric adult explicit photograph](images/04-blue-fabric-alt-lora-s0p8-seed42.png) |
| Close-up POV in black halter top | ![Stock MOSS SFT Q8 POV adult explicit photograph](images/05-pov-halter-stock-seed42.png) | ![MOSS SFT Q8 plus primary adult LoRA POV adult explicit photograph](images/05-pov-halter-lora-seed42.png) | ![MOSS SFT Q8 plus alternative adult LoRA POV adult explicit photograph](images/05-pov-halter-alt-lora-s0p8-seed42.png) |
| White thong, rear view | ![Stock MOSS SFT Q8 white-thong rear-view adult explicit photograph](images/06-white-thong-rear-stock-seed42.png) | ![MOSS SFT Q8 plus primary adult LoRA white-thong rear-view adult explicit photograph](images/06-white-thong-rear-lora-seed42.png) | ![MOSS SFT Q8 plus alternative adult LoRA white-thong rear-view adult explicit photograph](images/06-white-thong-rear-alt-lora-s0p8-seed42.png) |
| Bathroom mirror selfie | ![Stock MOSS SFT Q8 mirror-selfie adult explicit photograph](images/07-mirror-selfie-stock-seed42.png) | ![MOSS SFT Q8 plus primary adult LoRA mirror-selfie adult explicit photograph](images/07-mirror-selfie-lora-seed42.png) | ![MOSS SFT Q8 plus alternative adult LoRA mirror-selfie adult explicit photograph](images/07-mirror-selfie-alt-lora-s0p8-seed42.png) |

Prompts are shown without the `moss_xvstyle` prefix and the checkpoint-specific LoRA tag used only in the adapter columns:

1. `a nude adult woman with fair skin and shoulder-length light brown hair is photographed in a medium close-up from a slightly low angle. She is crouching with her legs spread wide, exposing her vulva and labia, while her left hand rests on a wooden surface behind her. Her right arm is raised above her head. She has a belly button piercing with a silver stud and is wearing white ankle socks with yellow smiley faces. Her facial expression is neutral, and she gazes directly at the camera. The setting is an indoor room with a white wall, a dark television screen, and a white lamp in the background. The lighting is soft and even, highlighting her smooth skin and large breasts. The image is a photograph.`
2. `A medium close-up photograph from behind and slightly to the side of an adult woman with long blonde hair featuring darker roots, wearing a red strap top with a silver buckle visible on her upper back. She is bent forward over a wooden surface, her pale skin and large buttocks prominently displayed. Her vulva and labia are clearly visible between her thighs as she leans forward, with her right hand partially visible near her lower back. The setting is an indoor room with a beige couch and light-colored wall in the background, illuminated by soft, even indoor lighting.`
3. `A full-body photograph shows an adult woman with a curvaceous body and long brown hair styled in a braid, kneeling on a wooden floor with her back to the camera and her head resting on her arms. Her skin tone is light tan, and she is completely nude, with her large buttocks prominently displayed and her vulva visible between her thighs. She wears a gold anklet and a green floral hair accessory. Her gaze is directed downward, and her expression is neutral. The setting is a bedroom with a large mirror reflecting her pose, a white upholstered chair, and light-colored curtains. The lighting is soft and indoor, and the camera angle is a medium shot from behind.`
4. `A close-up photograph of an adult woman with long light brown hair lying on her side on a patterned blue and white fabric. She has fair skin and a curvy body type, wearing dark lingerie. Her right arm is bent with her hand resting behind her head. The camera focuses on her lower back and buttocks, showing her exposed vulva with visible labia and clitoral hood as her legs are slightly parted. Her gaze is directed away from the camera, and her expression is neutral. The background features a large green leafy plant and a softly lit indoor setting with neutral-colored walls.`
5. `an adult woman with long blonde hair styled in loose waves and fair skin is positioned in a close-up POV shot, lying on her back with her legs spread wide to expose her vulva and labia. She wears a black halter-style top that leaves her breasts bare and has a tattoo on her lower abdomen. Her right hand rests between her thighs with fingers near her clitoris, and she wears a ring on her finger. Her mouth is slightly open and she gazes directly at the camera with an aroused expression. The setting appears to be a modern indoor room with light-colored walls, a metal railing, and wooden cabinetry in the background, illuminated by soft overhead lighting. The image is a photograph.`
6. `a close-up photograph of an adult woman with fair skin and long blonde hair, viewed from behind as she bends forward over a gray surface. Her body is positioned with her buttocks prominently displayed, revealing her vulva and labia in a natural, unposed state. She wears a white thong that is pulled down to expose her intimate area. The lighting is soft and natural, highlighting the smooth texture of her skin. The background is blurred, suggesting an indoor setting with neutral tones.`
7. `A close-up photograph shows an adult woman with light brown wavy hair lying face down on a light-colored surface, holding a dark blue smartphone with a triple-lens camera system to take a mirror selfie. Her pale skin is visible on her rounded buttocks and thighs, and she is completely nude, with her vulva and labia clearly exposed between her spread legs. The setting appears to be a bathroom with a plain white wall and a partial view of kitchen appliances in the background. The lighting is soft and even, and the camera angle is from behind and slightly above, focusing on her lower back and genital area.`

The previews validate technical compatibility and adapter influence against prompts used in the prior xvstyle evaluations; they do not attempt to represent the full content range of the source model.

The focused alternative GGUF was tested against all seven controlled prompts at scale 0.8 with the same `MOSS-Image-SFT-Q8_0.gguf`, 1024×1024, 30-step, CFG 3, Euler/discrete, flow-shift 5, and seed 42 settings. stable-diffusion.cpp reported `504 / 504` LoRA tensors applied for every image.

## Conversion

The original 2,000-step checkpoint was converted from `original-checkpoint-2000/pytorch_lora_weights.safetensors` using stable-diffusion.cpp commit `b13a482`:

```powershell
sd-cli.exe -M convert `
  -m original-checkpoint-2000/pytorch_lora_weights.safetensors `
  -o MOSS-Image-NSFW-Uncensored-LoRA-F16.gguf `
  --type f16 `
  --convert-name `
  -t 16
```

The current default focused checkpoint was converted from the source repository root with the same settings:

```powershell
sd-cli.exe -M convert `
  -m pytorch_lora_weights.safetensors `
  -o MOSS-Image-NSFW-Uncensored-LoRA-Alternative-1750-F16.gguf `
  --type f16 `
  --convert-name `
  -t 16
```

The source adapters were trained against MOSS-Image-Base. Their transformer targets are compatible with MOSS-Image-SFT, which is the preferred inference base. See `conversion.json` for machine-readable conversion and validation metadata.

## Intended use, restrictions, and limitations

This adapter is intended for lawful creative generation involving clearly adult fictional subjects. Users are responsible for complying with applicable law, platform rules, privacy rights, and consent requirements.

Do not use this adapter for:

- Any depiction of minors or age-ambiguous people in sexual contexts.
- Sexual abuse, exploitation, coercion, trafficking, or non-consensual imagery.
- Sexualized deepfakes or depictions of identifiable real people without explicit consent.
- Harassment, blackmail, doxxing, or attempts to evade legal or platform safeguards.

The model can produce anatomical errors, malformed hands, inconsistent reflections, implausible poses, or outputs that diverge from the prompt. Review outputs before use or distribution.

## License

The adapter and this conversion are provided under Apache License 2.0, matching the source release and base model. This is an independent community conversion and is not affiliated with or endorsed by OpenMOSS.
