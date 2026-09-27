# TwinCore Q6 runtime and adapter training

This continues the user's approved full-weight coupled design and later
correction to **Nanbeige4.2-3B + K2-Horizon-3.7B**, with Q6 inference. The user
has authorized implementation, testing and pushing completed changes to main.

## Required behavior

One TwinCore model object owns both complete checkpoints, embeddings, decoder
towers and prediction heads. Every prediction step uses both towers. Trainable
bidirectional low-rank projections exchange hidden-state feedback. Both native
heads contribute to one Nanbeige-space score vector through the existing exact
surface alignment. There are no separate answer candidates or a judge.

The complete Q6_K GGUFs already used by DuoCore are retained unchanged:

- K2: 4,161,403,264 bytes,
  `2180f3ca4eb4906a109b364a98740778fd8dcd9969e270b0d34036d18ee33232`.
- Nanbeige: 3,595,603,104 bytes,
  `93f884a2d8d6cafc5406df84be64f197a407889904b18db7c6e82fd35f2b0170`.

Native loading uses the shipped MBZUAI-IFM llama.cpp ABI at
`42adf019f76013dac873b5b43950d54d5ab27216`, which supports both architectures.
It must not substitute NF4, a different checkpoint, one tower, or a candidate
selection service. BF16 conversion cannot restore precision discarded by Q6.

## Native boundary

The native backend exposes full decoder steps, final normalized hidden states,
native logits, tokenization and native head linear projection in both directions.
It owns and releases both models and all contexts on failure or cancellation.
Python owns the trainable bridge and one canonical generated suffix. Native
tokenizers process each model's own chat prefix and that same suffix.

Prefixes may retokenize after appended text. In KV mode, retain only the common
raw-token prefix, invalidate the previously biased last position, and recompute
the changed suffix with the new feedback applied to its last input embedding.
In recompute mode, clear decoder memory and run the current prefix each step.
Neither mode supplies infinite simultaneous attention. Do not raise advertised
limits or conflate an ECHO archive with native attention.

Native head operations must not retain a second dense copy of either complete
checkpoint. Head transpose operations for training may materialize bounded
chunks, which are freed after each operation. Adapter computations use CPU RAM
so a second CUDA framework does not reserve GPU memory for these small tensors.

## Training and evidence

Freeze both base weight sets. Train only the bridge and gates on a hash-bound
training corpus separate from HumanEval and LiveBench. Native decoder features
are detached at step boundaries; head projections expose their exact linear
gradient. This is training with truncated feedback gradients, not end-to-end
fine-tuning of native decoder weights. Record that distinction in artifacts.

Save only adapter tensors, geometry, exact source/checkpoint/alignment hashes,
training configuration and validation evidence. A trained adapter must match
both full checkpoints and all bridge tensor shapes when loaded. Never label
an initialized or merely updated adapter as a coding-quality improvement.

## Activation gates

- CPU tests prove bidirectional projection, native API error handling, frozen
  base weights, bridge gradients, checkpoint binding and suffix invalidation.
- Real Q6 GPU tests prove both full towers run, callbacks observe both native
  states, one response streams, stopping releases owned resources, and peak
  memory fits the user's RTX 4070. An ABI test alone proves none of these.
- Adapter training and held-out validation finish with versioned artifacts.
- Full official HumanEval, then the pinned full public LiveBench, retain raw
  answers and per-task scores; comparisons label compute and generation budgets.
- Only then wire TwinCore profiles into Models and the normal app harness,
  memory, installation, uninstallation and updater paths.

Keep at least 200,000,000,000 bytes free. Reuse verified existing weights and
vendor sources. Preserve active LFM evaluation and the other chat's speech work.
