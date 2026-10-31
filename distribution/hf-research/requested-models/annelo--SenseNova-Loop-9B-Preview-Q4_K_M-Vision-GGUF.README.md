---
license: apache-2.0
base_model: sensenova/SenseNova-Loop-9B-Preview
language:
  - en
  - zh
pipeline_tag: image-text-to-text
tags:
  - gguf
  - llama.cpp
  - vision
  - sensenova-loop
---

# SenseNova Loop 9B Preview — Q4_K_M + F16 vision projector

Unofficial GGUF release. The original model and vision weights are by [SenseNova / OpenSenseNova](https://huggingface.co/sensenova/SenseNova-Loop-9B-Preview). The text GGUF is **requantized from [notfakerv's text-only Q8_0 GGUF](https://huggingface.co/notfakerv/SenseNova-Loop-9B-Preview-GGUF)**; the F16 vision projector is converted from the original SenseNova vision weights. This is not an official SenseNova, notfakerv, or llama.cpp release.

| File | Purpose |
| --- | --- |
| `SenseNova-Loop-9B-Preview-Q4_K_M.gguf` | Text decoder, Q4_K_M from the published Q8_0; mHC routing and recurrent conv tensors remain F32 |
| `mmproj-SenseNova-Loop-F16.gguf` | Qwen3.5 vision tower and merger, predominantly F16 with small F32 tensors |
| `runtime/01-clover-pr31306.patch` | Prerequisite Clover patch distributed by notfakerv |
| `runtime/02-sensenova-loop-native.patch` | SenseNova Loop text runtime and converter distributed by notfakerv |
| `runtime/03-sensenova-loop-vision.patch` | Minimal vision converter registration and image preprocessing metadata |

`SHA256SUMS` checks the two GGUF files. See `NOTICE` and `LICENSE` for provenance and license terms. The runtime requires **all three patches in order** on llama.cpp commit `ff0d396e3dda0f33e7ce5ad4267fce4f7690cfb1`; stock llama.cpp will not load this text model. The third patch only touches `conversion/__init__.py` and `conversion/qwen3vl.py`. It reuses the existing Qwen3VL mtmd graph and does not modify the Loop/mHC text graph.

## Run

Build the patched llama.cpp with CUDA (on the tested CUDA 13.3 host, set `-DCMAKE_CUDA_HOST_COMPILER=/usr/bin/g++-15 -DCMAKE_CUDA_ARCHITECTURES=86`):

```bash
git clone https://github.com/ggml-org/llama.cpp.git
cd llama.cpp
git checkout ff0d396e3dda0f33e7ce5ad4267fce4f7690cfb1
git apply /path/to/this-repo/runtime/01-clover-pr31306.patch
git apply /path/to/this-repo/runtime/02-sensenova-loop-native.patch
git apply /path/to/this-repo/runtime/03-sensenova-loop-vision.patch
cmake -S . -B build -DGGML_CUDA=ON -DCMAKE_CUDA_HOST_COMPILER=/usr/bin/g++-15 -DCMAKE_CUDA_ARCHITECTURES=86
cmake --build build --target llama-server llama-cli llama-quantize -j 12
```

Download both GGUFs from this repository, then start:

```bash
./build/bin/llama-server \
  -m /path/to/SenseNova-Loop-9B-Preview-Q4_K_M.gguf \
  --mmproj /path/to/mmproj-SenseNova-Loop-F16.gguf \
  -ngl 99 -c 8192 --parallel 1 --port 8080
```

The built-in web UI accepts images. The OpenAI-compatible `/v1/chat/completions` API accepts a user message containing `text` and `image_url` parts; for a local image, provide a `data:image/png;base64,...` URL. Text-only usage needs just `-m`, without `--mmproj`.

## Reproduce the GGUFs

Pinned text source: notfakerv GGUF revision `b54ee83643a4e8066290d3062e1a218139c4c007`. Requantization from Q8_0 requires `--allow-requantize` and can reduce quality relative to quantizing from BF16:

```bash
hf download notfakerv/SenseNova-Loop-9B-Preview-GGUF SenseNova-Loop-9B-Preview-Q8_0.gguf --revision b54ee83643a4e8066290d3062e1a218139c4c007 --local-dir source-q8
./build/bin/llama-quantize --allow-requantize source-q8/SenseNova-Loop-9B-Preview-Q8_0.gguf SenseNova-Loop-9B-Preview-Q4_K_M.gguf Q4_K_M 12
```

Pinned original model revision: `27abea779b1e6634b136805b6519371739d551f7`. All 333 vision tensors reside in shard 8. Download that shard and the configuration into a **new directory without `model.safetensors.index.json`**; the existing converter will index the available shard and discard its text tensors. The entire BF16 checkpoint is not needed and was not run:

```bash
hf download sensenova/SenseNova-Loop-9B-Preview config.json preprocessor_config.json processor_config.json tokenizer_config.json generation_config.json README.md model-00008-of-00008.safetensors --revision 27abea779b1e6634b136805b6519371739d551f7 --local-dir vision-source
python convert_hf_to_gguf.py vision-source --mmproj --outtype f16 --outfile mmproj-SenseNova-Loop-F16.gguf
```

## Validation and limits

Tested on an RTX 3060 12 GB at 8192 context (about 9.9 GB VRAM while serving): text-only `13 × 7 → 91`; image recognition `star → Star`; the same color question changed from `Red` to `Blue` when only the image was flipped; and counting three circles then adding two gave `5`. An image request through `/v1/chat/completions` returned 200 and the correct color. I also tried a video clip, and the model described it quite accurately. I haven't tested video support more broadly yet.

Image resizing metadata follows the original HF processor (16-pixel patches, 2×2 spatial merge, 65,536 minimum and 16,777,216 maximum pixels). Very large images can exceed an 8192-token context; reduce the image or set an appropriate image-token limit. Numeric comparison against the complete HF BF16 model was not performed. Multi-image, general video behavior, long-context quality, vision quantization, MTP, diffusion acceleration, and speculative decoding are not validated by this release.
