# AutoLoop in Learning Studio

Open **Learning Studio → AutoLoop · No training**. Choose an installed GGUF or another local checkpoint, then press **Optimize and verify**. Tests run in a separate CPU process, with two threads by default. Progress shows the current model, question split, counts and elapsed time. Stop cancels only that experiment. Nothing runs automatically at startup.

AutoLoop physically repeats selected transformer blocks in a separate GGUF. It does not train weights, download a replacement checkpoint, or implement the full training-free looped-transformer research method. Experimental residual damping starts off. Repeated blocks increase checkpoint size and can slow inference.

The original checkpoint is never overwritten or removed. Up to eight predefined candidates can be tested within the configured time and disk budgets. Development questions select one candidate. Only that candidate receives the independent confirmation questions. Default acceptance needs at least two net development wins, at least sixteen confirmation questions, a strict confirmation improvement, zero regressions and an exact one-sided paired-binomial probability of at most 0.05.

The bundled 48 questions cover elementary reasoning and exact answers. Passing them demonstrates a gain on that set, not generally better coding, vision, reasoning or intelligence. A custom JSON benchmark may contain 48–10,000 distinct objects with string `prompt`, string `answer`, and `kind` set to `exact` or `number`. Use fresh, representative questions and correct answers; repeatedly inspecting and tuning against the same confirmation set weakens its value.

## Results

An accepted candidate must have unchanged source, runtime, benchmark and configuration identities. OpenCore then verifies actual generation through its normal managed chat runtime. Only after those checks does **Use verified model in chat** appear. It registers a separate local model; its Native/ECHO options use the real checkpoint context cap. The original remains selectable.

Rejected, cancelled and failed experiments do not retain candidate weights. They retain exact scores, raw answers and provenance in the local run directory. A normal runtime-verification failure preserves the completed quality report and its failure reason. Reports are visible in Learning Studio; they are not sent to a cloud service.

## Compatibility

The adapter currently admits single-file, little-endian GGUF v3 checkpoints for homogeneous `llama`, `qwen2`, `qwen3`, `mistral`, `gemma2`, `phi3` and `gemma3` decoder blocks. It checks tensor shapes and layouts rather than trusting the filename. OpenCore chat also requires a declared context of at least 8,192 tokens. Unsupported hybrid/MoE architectures, split GGUFs and vision models without verified companion compatibility stay unchanged. GGUF alone does not establish that layer repetition is safe.

The integrated core comes from the user-provided AutoLoop v0.2 archive. Its exact source hashes and changes are recorded in `autoloop_vendor/provenance.json`. The supplied archive had no standalone license file; the integration retains its notices and original README without inventing one.
