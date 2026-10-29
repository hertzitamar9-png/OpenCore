# Qwen Image loading and chat routing repair

The Qwen NVFP4 package previously required all components to coexist on the CPU.
Its safetensors loader also copied tensors while retaining a mapping of the same
checkpoint, causing a much larger memory peak than the compressed weight size.

Qwen now encodes the prompt in a dedicated process, saves only the conditioning
tensors, exits that process, and then starts image sampling. Safetensors are read
directly into owned buffers without retaining a file mapping or a second copy.
Dynamic disk loading is disabled for this path. The per-phase CPU loading budget
remains separate from GPU sampling memory; insufficient memory still fails
explicitly. Reference conditioning and the downloaded LoRA survive the phase
boundary. The sampler uses the Qwen 2.1 64-channel latent format.

Windows can report an owned TCP listener with state `NONE`. Readiness accepts an
owned, unconnected stream socket in that state and then verifies its HTTP API;
UDP sockets and established connections do not qualify.

Selecting an image model and immediately pressing Send could previously race the
React commit and send the prompt to the text agent. Selection and routing now
share a synchronous reference. Missing image handlers retain the draft and show
a retry notice instead of falling through to text. Status from an older failed
attempt is not redisplayed as the current attempt.

## Verification

- Actual installed Qwen Image 2.1 Uncensored NVFP4 weights and downloaded Qwen
  LoRA: a new 512 x 512 cup image completed all 40 steps in 109.1 seconds including
  both process startups and loading. The saved PNG was opened and visually
  inspected. No model download or synthetic image fallback was used.
- Regression coverage includes immediate selection and Send, missing handlers,
  stale errors, isolated workflow dependencies, CPU staging budgets, owned
  checkpoint tensor lifetime, malformed checkpoint ranges, conditioning values,
  reference latent sizes, and Windows listener ownership.
- The packaged composer is exercised with both Send and Enter, reference images,
  LoRA selection, sampling progress, elapsed duration, and mobile sizing.

These results qualify this package and adapter. They do not establish support
for every Hugging Face architecture or arbitrary LoRA.
