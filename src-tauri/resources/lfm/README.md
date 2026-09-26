# DualCore and FusionCore

Both families use **two complete copies of the same pinned DavidAU LFM checkpoint**.
The supplied repository has no BF16 checkpoint. The authorized fallback is Q8_0 MAX,
not Q4 or a dequantized file labelled as original BF16.

| Profile | Execution | Active inference | Memory |
| --- | --- | --- | --- |
| DualCore KV | Independent drafts and blind cross-reviews; one selected response | 131,072 tokens | Two native F16 KV/state buffers |
| DualCore ECHO | Independent drafts and reviews, exact ECHO archive and retrieval | 32,768 tokens | Recompute each brain's prefix for every token; no retained cross-token cache, transient attention/state buffers remain |
| FusionCore KV | One token loop, both full towers, fused scores and bidirectional hidden feedback | 131,072 tokens | Two native F16 KV/state buffers |
| FusionCore ECHO | Same coupled loop, recompute both prefixes for every new token; ECHO archive retrieval | 8,192 tokens | No retained cross-token cache, but transient F16 attention/state buffers remain allocated |

The model contains 5,394,397,184 parameters across two towers. One 3,120,573,088-byte
GGUF is stored on disk; two weight sets are loaded for inference. Variant installations
share the download. They do not download weights on application startup.

FusionCore's 0.02 RMS-normalized hidden-feedback gate is **untuned**, with an identity
alignment between identical hidden spaces. It is an experimental coupled model, not a
new trained dense checkpoint. No general quality improvement, HumanEval score,
LiveBench score, simultaneous trillion-token attention, or infinite output is claimed.
Draft review scores are self-reports, not calibrated correctness probabilities.

The pinned runtime uses a compatible ChatML template for LFM. The checkpoint's custom
template conflicts with structured grammar in this runtime. Weights are unchanged.
Reasoning streams separately from answer text; registered tool calls use validated
structured envelopes. The application's approval and argument-validation gates still
own execution.

`native/fusioncore.cpp` links the exact llama.cpp ABI declared in `native/build-info.json`.
Rebuild its DLL with `native/CMakeLists.txt` and the pinned DuoCore runtime source/build.
The DLL exposes normalized hidden states through the graph observation callback while
keeping the prediction head enabled. Embedding mode alone suppresses LFM logits.

The weights declare **LFM Open License v1.0** in GGUF metadata. Keep
`LICENSE.weights.txt` and upstream notices with redistributions. The model card's
Apache label does not replace that underlying weight license. Native runtime code
inherits the repository's code license; llama.cpp and its bundled dependencies retain
their own notices.
