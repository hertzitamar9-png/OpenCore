---
language:
- en
license: other
base_model:
- IQuestLab/IQuest-Coder-V1-7B-Thinking
tags:
- gguf
- llama.cpp
- text-generation
- iquestcoder
---

# IQuest-Coder-V1-7B-Thinking-GGUF

GGUF quant collection for [IQuestLab/IQuest-Coder-V1-7B-Thinking](https://huggingface.co/IQuestLab/IQuest-Coder-V1-7B-Thinking).

## Included quants

- `IQuest-Coder-V1-7B-Thinking-Q4_K_M.gguf`
- `IQuest-Coder-V1-7B-Thinking-Q6_K.gguf`
- `IQuest-Coder-V1-7B-Thinking-Q8_0.gguf`

## Checksums

- `4a7f9a129a27b5bedcf2946453375c51cf8d3ed09b76868cfe524832d0ac8738`  `IQuest-Coder-V1-7B-Thinking-Q4_K_M.gguf`
- `8caab18bb8526afe5180f6bcb99ce903d92ae0da173d5443d019374ad6215ab0`  `IQuest-Coder-V1-7B-Thinking-Q6_K.gguf`
- `c9a16029ef9d8b7ac3d2ad26691456cedb45eafcce8f5d0c738438cc51f4dd13`  `IQuest-Coder-V1-7B-Thinking-Q8_0.gguf`

## Provenance

- Source model: https://huggingface.co/IQuestLab/IQuest-Coder-V1-7B-Thinking
- Converted + quantized with llama.cpp (`convert_hf_to_gguf.py` + `llama-quantize`).
- Metadata hygiene scan passed for local/personal identifiers before upload.

## License

This repo redistributes quantized weights from `IQuestLab/IQuest-Coder-V1-7B-Thinking` and includes the upstream `LICENSE` file verbatim.
Please follow upstream license terms, including the IQuest commercial UI attribution requirement.
