---
license: apache-2.0
language:
- en
- zh
base_model: sensenova/SenseNova-Loop-9B-Preview
base_model_relation: quantized
pipeline_tag: text-generation
tags:
- gguf
- sensenova
- sensenova-loop
- llama.cpp
- quantized
---

# SenseNova Loop 9B Preview — GGUF

Text-only GGUF quants of [sensenova/SenseNova-Loop-9B-Preview](https://huggingface.co/sensenova/SenseNova-Loop-9B-Preview).
Weights, training and the model itself are SenseNova / OpenSenseNova's work. This repository only
converts and quantizes.

**Stock llama.cpp cannot load these.** The architecture is `sensenova_loop`, implemented in the
runtime fork https://github.com/GuideboardLabs/llama.cpp-sensenova-loop (branch `sensenova-loop`,
two commits, patches included). Vision and MTP are not part of the export; text chat only.

## Files

| file | quant | size |
| --- | --- | ---: |
| `SenseNova-Loop-9B-Preview-Q4_0.gguf` | Q4_0 | 5,325,947,040 B |
| `SenseNova-Loop-9B-Preview-Q4_K_M.gguf` | Q4_K_M | 5,628,723,360 B |
| `SenseNova-Loop-9B-Preview-Q3_K_M.gguf` | Q3_K_M | 4,642,406,560 B |

Convolution kernels, norms and the mHC router weights stay FP32; everything else is quantized.

The looped block is stored **once**. The runtime aliases the second visit of those layers onto the
first, which is numerically identical and 36 percent smaller than storing the block twice.

## Run

```bash
GGML_VK_DISABLE_HOST_VISIBLE_VIDMEM=1 ./build/bin/llama-server \
    -m SenseNova-Loop-9B-Preview-Q4_0.gguf -ngl 99 -c 4096 -fa on --port 8080
```

The environment variable matters on small-BAR cards: without it the recurrent state buffers land
in host-visible memory. It only takes effect once the model fits in VRAM.

## Measured

RX 5700 XT (NAVI10), 4096 context, full offload, temperature 0:

| quant | decode | prompt |
| --- | ---: | ---: |
| Q4_0 | 31.4 t/s | 29.6 t/s |
| Q4_K_M | 30.5 t/s | 30.5 t/s |
| Q3_K_M | 29.9 t/s | 19.4 t/s |

Q3_K_M is slower than Q4_0 despite a smaller file: RDNA1 has no integer dot product, so the
K-quant decode costs more than the bytes it saves.

## Accuracy

Greedy decoding matches the reference `transformers` implementation token for token on the
development prompt (`The capital of France is`, temperature 0), and the custom routing ops agree
with their CPU implementation to 1.2e-7. No KL or perplexity sweep has been run on these quants.

Context was exercised to 16K tokens here; the upstream model advertises 262K, which we have not
validated.
