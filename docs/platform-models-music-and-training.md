# Model library, Music Studio and ECHO training

## Application integration

- The library has 32 entries: 14 text, 3 speech, 4 computer use, 3 image,
  3 mesh generation, 4 mesh animation and 1 frame animation.
- New checkpoints use pinned revisions, file sizes and SHA-256 hashes.
- Swift, Dirk and DavidAU have optional GGUF chat paths. They have not been
  downloaded or load-qualified on the RTX 4070. Other new specialist entries
  display **Setup needed** until an upstream inference backend is installed.
- HY-Motion and HY-Motion Lite use the official `tencent/HY-Motion-1.0`
  repository. Neither is misrepresented as a working text-chat model.
- ModelsLab/3D-Animation-Diffusion is categorized as 2D image generation.
- Delete retains the existing per-model confirmation and shared-file checks.
- Downloads preserve the existing 100 GB free-space guard. Adding library
  entries does not download their weight files.

Music Studio starts and embeds the existing YuE2 service at localhost:7860.
It retains its output folder, song history and model files. GPU weights remain
unloaded until requested in the studio. The backend checks the service identity
before embedding it and shuts down a service it owns on normal app exit.
An existing WSL YuE engine is used if the installation's Windows Python is broken;
this does not install another music environment or overwrite YuE preferences.

Microphone sessions receive a frontend-generated identifier before GPU startup.
Navigation cancels pending startup/recording/transcription, releases browser
tracks and waits for the old worker to exit before another composer starts.
An old transcript cannot be inserted into the newly opened conversation.

## Evaluation

`scripts/evaluation/gsm8k_echo.py` evaluates the exact original main ECHO GGUF
on all 1,319 GSM8K main/test questions. It uses the first eight train examples,
temperature 0, seed 42, 1,024 output tokens and fresh context per question.
Cross-question ECHO recall and tools are disabled. This evaluates arithmetic,
not long-history memory. Captures and the final score are separate artifacts.
Resume validates the model/protocol, question hashes and each saved result.

## Local training pilot

The pilot is deliberately bounded; it is not full UltraData or MiMo RL training.
`prepare_echo_pilot.py` reads selected columns of one pinned UltraData row group
through HTTP ranges, producing 24 train and 8 held-out examples. MiMo is a task
environment dataset, not completed supervised answers. A model-generated repair
may enter training only after passing its immutable verifier in a fresh isolated
container with no network or host mounts. Failed rollouts are retained as failures.

`after_gsm8k.py` queues one training run behind the existing complete evaluation.
It never interrupts or restarts the evaluation. `train_echo_pilot.py` then:

1. Binds the source model and prepared data to their SHA-256 hashes.
2. Captures native next-token probabilities on five short prompts.
3. Runs one MiMo task with at most 16 model actions.
4. Rematerializes the original frozen quantized backbone for autograd in RAM.
5. Pages only the current frozen expert stage onto the GPU.
6. Requires at least four matching top-1 tokens and mean top-64 log-probability
   error no greater than 0.3 before starting any optimizer updates.
7. Fine-tunes only 90,000 expert composition coefficients for one epoch at
   learning rate 0.001, batch size one. Prompt tails are limited to 192 tokens
   and target prefixes to 64 tokens; this is not full-solution supervision.
8. Rejects a held-out loss regression greater than 0.02 nats.
9. Writes a separate candidate GGUF, changing only six BF16 coefficient arrays.
   All other tensor payloads and the original model must remain unchanged.
10. Requires native decoding at least 20 tokens/s before marking the candidate
    usable. It does not automatically promote the pilot into the application.

The pilot must not be called completed training on both datasets when the MiMo
rollout fails. A short-prefix loss improvement is not evidence of general coding
improvement. No GSM8K test examples are used for training.

Protocol, failures, rewards, progress, held-out losses, memory use and candidate
hashes are recorded locally. Raw dataset samples and model weights are excluded
from source control. Canonical ECHO history, ASR downloads and user data are kept.
