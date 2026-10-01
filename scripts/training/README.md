# Local ECHO training

`train_echo_pilot.py` runs the existing bounded UltraData supervised pilot and
admits a MiMo completion only after its immutable verifier passes. That path is
reward-filtered supervised training, not a policy-gradient RL algorithm.

`train_echo_rl.py` runs a separate online coding RL pilot on the approved
UltraData candidate. It samples four temperature-one completions per authored
task, verifies fixed tests in an offline, read-only Docker container, computes
group-relative advantages, and backpropagates a clipped policy objective. Failed
solutions participate through their rewards rather than becoming SFT targets.
Constant-reward groups are skipped rather than inventing a learning signal.

The pilot records its exact protocol, prompts, token IDs, behavior probabilities,
reward provenance, optimizer updates and resumable state in its output folder.
Prompt prefill and tokenwise expert stages are reproduced during scoring. Native
parity and cached-generation/scoring parity are required before weight updates.

Only six composition arrays (90,000 parameters) are trainable. Export preserves
the original Q6_K/F32 backbone and BF16 expert tensors; the original and UltraData
candidate remain immutable. The new GGUF is separate and is never automatically
promoted. Held-out loss, native coding tasks and native speed qualify the result;
they do not establish general improvement or replace independent benchmarks.

The default pilot has eight training tasks, four independent held-out tasks,
one epoch, a 192-token prompt limit, a 128-token completion limit and a 45-minute
active training budget. It does not train on HumanEval, GSM8K or SWE-bench test
data. It requires the existing local CUDA training environment and pinned local
grader image; it does not download models or run paid cloud jobs.

Use the local `lfm-bf16-py311` Python with `--research-root`, `--folder` and
`--source`. A subsequent invocation requires `--resume` and the same protocol.
The OS lock rejects concurrent trainers, saved completed groups are restored,
and incomplete-group responses are reused only when their task, prompt and
policy identity match. Do not delete saved artifacts to restart a run.
