---
base_model: GrootTeam/Groot-19B-A3B-261023
language:
- en
library_name: transformers
license: apache-2.0
license_link: LICENSE
mradermacher:
  readme_rev: 1
quantized_by: mradermacher
---
## About

<!-- ### quantize_version: 2 -->
<!-- ### output_tensor_quantised: 1 -->
<!-- ### convert_type: hf -->
<!-- ### vocab_type:  -->
<!-- ### tags:  -->
<!-- ### quants:  x-f16 Q4_K_S Q2_K Q8_0 Q6_K Q3_K_M Q3_K_S Q3_K_L Q4_K_M Q5_K_S Q5_K_M IQ4_XS -->
<!-- ### quants_skip:  -->
<!-- ### skip_mmproj:  -->
static quants of https://huggingface.co/GrootTeam/Groot-19B-A3B-261023

<!-- provided-files -->

***For a convenient overview and download list, visit our [model page for this model](https://hf.tst.eu/model#Groot-19B-A3B-261023-GGUF).***

weighted/imatrix quants are available at https://huggingface.co/mradermacher/Groot-19B-A3B-261023-i1-GGUF
## Usage

If you are unsure how to use GGUF files, refer to one of [TheBloke's
READMEs](https://huggingface.co/TheBloke/KafkaLM-70B-German-V0.1-GGUF) for
more details, including on how to concatenate multi-part files.

## Provided Quants

(sorted by size, not necessarily quality. IQ-quants are often preferable over similar sized non-IQ quants)

| Link | Type | Size/GB | Notes |
|:-----|:-----|--------:|:------|
| [GGUF](https://huggingface.co/mradermacher/Groot-19B-A3B-261023-GGUF/resolve/main/Groot-19B-A3B-261023.mmproj-Q8_0.gguf) | mmproj-Q8_0 | 0.7 | multi-modal supplement |
| [GGUF](https://huggingface.co/mradermacher/Groot-19B-A3B-261023-GGUF/resolve/main/Groot-19B-A3B-261023.mmproj-f16.gguf) | mmproj-f16 | 1.0 | multi-modal supplement |
| [GGUF](https://huggingface.co/mradermacher/Groot-19B-A3B-261023-GGUF/resolve/main/Groot-19B-A3B-261023.Q2_K.gguf) | Q2_K | 7.3 |  |
| [GGUF](https://huggingface.co/mradermacher/Groot-19B-A3B-261023-GGUF/resolve/main/Groot-19B-A3B-261023.Q3_K_S.gguf) | Q3_K_S | 8.5 |  |
| [GGUF](https://huggingface.co/mradermacher/Groot-19B-A3B-261023-GGUF/resolve/main/Groot-19B-A3B-261023.Q3_K_M.gguf) | Q3_K_M | 9.4 | lower quality |
| [GGUF](https://huggingface.co/mradermacher/Groot-19B-A3B-261023-GGUF/resolve/main/Groot-19B-A3B-261023.Q3_K_L.gguf) | Q3_K_L | 10.1 |  |
| [GGUF](https://huggingface.co/mradermacher/Groot-19B-A3B-261023-GGUF/resolve/main/Groot-19B-A3B-261023.IQ4_XS.gguf) | IQ4_XS | 10.6 |  |
| [GGUF](https://huggingface.co/mradermacher/Groot-19B-A3B-261023-GGUF/resolve/main/Groot-19B-A3B-261023.Q4_K_S.gguf) | Q4_K_S | 11.1 | fast, recommended |
| [GGUF](https://huggingface.co/mradermacher/Groot-19B-A3B-261023-GGUF/resolve/main/Groot-19B-A3B-261023.Q4_K_M.gguf) | Q4_K_M | 11.8 | fast, recommended |
| [GGUF](https://huggingface.co/mradermacher/Groot-19B-A3B-261023-GGUF/resolve/main/Groot-19B-A3B-261023.Q5_K_S.gguf) | Q5_K_S | 13.3 |  |
| [GGUF](https://huggingface.co/mradermacher/Groot-19B-A3B-261023-GGUF/resolve/main/Groot-19B-A3B-261023.Q5_K_M.gguf) | Q5_K_M | 13.7 |  |
| [GGUF](https://huggingface.co/mradermacher/Groot-19B-A3B-261023-GGUF/resolve/main/Groot-19B-A3B-261023.Q6_K.gguf) | Q6_K | 15.7 | very good quality |
| [GGUF](https://huggingface.co/mradermacher/Groot-19B-A3B-261023-GGUF/resolve/main/Groot-19B-A3B-261023.Q8_0.gguf) | Q8_0 | 20.3 | fast, best quality |

Here is a handy graph by ikawrakow comparing some lower-quality quant
types (lower is better):

![image.png](https://www.nethype.de/huggingface_embed/quantpplgraph.png)

And here are Artefact2's thoughts on the matter:
https://gist.github.com/Artefact2/b5f810600771265fc1e39442288e8ec9

## FAQ / Model Request

See https://huggingface.co/mradermacher/model_requests for some answers to
questions you might have and/or if you want some other model quantized.

## Thanks

I thank my company, [nethype GmbH](https://www.nethype.de/), for letting
me use its servers and providing upgrades to my workstation to enable
this work in my free time.

<!-- end -->
