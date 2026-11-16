---
license: apache-2.0
base_model: openbmb/MiniCPM-V-4.7-1B
datasets: [nvidia/ProCUA-SFT]
language: [en]
tags: [computer-use, gui-agent, agent, osworld, vision-language, gguf, llama.cpp, lora]
pipeline_tag: image-text-to-text
---

<!-- Released 2026-11-15. Repo: banyohavlusu23/minicpm-v-4.7-1b-computer-use -->

# MiniCPM-V-4.7-1B-Computer-Use

A 1B computer-use agent. **Unofficial: not affiliated with, endorsed or sponsored by OpenBMB.** It is a derivative of
their model, described as such below.

MiniCPM-V-4.7-1B-Computer-Use is a LoRA fine-tune of [MiniCPM-V-4.7-1B](https://huggingface.co/openbmb/MiniCPM-V-4.7-1B) that operates a
desktop from screenshots: it looks at the screen, writes one short `Action: ...` line and calls one `computer_use` tool
(click, type, key, scroll, drag, wait, terminate), one action per step. It was trained on one laptop GPU (RTX 4060
Laptop, 8 GB) from step rows built from [nvidia/ProCUA-SFT](https://huggingface.co/datasets/nvidia/ProCUA-SFT).

## Results

OSWorld tasks, run locally in OSWorld's Docker VM with our harness: 1 VM, **15 steps per task**, one screenshot per
step, thinking on, one run per number. `held-out-99` = 99 OSWorld tasks whose starting setups were removed from the
training data. `full 360` = all 360 tasks of our OSWorld subset.

| Model | held-out-99 | full 360 | seconds/task |
|---|---|---|---|
| Qwen3.5-4B (untrained base, Q4_K_M, T 0.6) | 12/99 | – | 224.0 |
| MiniCPM-V-4.7-1B (untrained base, Q8_0, T 0.7) | 0/99 (2 failed in setup) | – | 164.7 |
| **MiniCPM-V-4.7-1B-Computer-Use** (this model, T 0.7) | **5/99** (1 failed in setup) | 18/360 (4 failed in setup) | 168.8 / 176.3 |

Sources, so the numbers can be checked: `reports/mc-v3_heldout99_summary.json` (n 99, success 0.0505,
errors 1, temperature 0.7, max_tokens 4096, prompt action-line), `reports/base-minicpm_heldout99_summary.json`
(n 99, success 0.0, errors 2, temperature 0.7), `reports/mc-v3_full360_summary.json` (n 360, success 0.05,
errors 4, median 176.3 s/task), and our own run log for the Qwen baseline (success 0.1212, temperature 0.6; that log
is not shipped, the other three are in `reports/`).

Caveats, plainly:

- **A four-times-larger untrained model scores higher.** Qwen3.5-4B, with no fine-tuning at all, gets 12/99 against
  this model's 5/99 on the same tasks. Fine-tuning MiniCPM-V-4.7-1B did not close that gap.
- **One run per number.** Run-to-run noise is about ±2 tasks: two runs of this model on a 60-task dev set (T 0.7 and
  T 0.2) solved 5 and 4 tasks and shared only 1 solved task. Treat 5/99 as "5, give or take 2", not as a precise score.
- **Not comparable to the OSWorld leaderboard.** Published OSWorld numbers use 369 tasks and 50–100 steps per task; we
  use 15 steps and one screenshot per step, so our absolute numbers are lower by construction.
- **`held-out-99` is the clean number.** Every training trajectory starting from those task setups was removed. In the
  full 360, 260 tasks share a starting setup with training data, so a full-360 score is inflated for this model and
  should not be compared with published numbers. (It happened to come out at the same rate here, 18/360 = 5.0% against
  5/99 = 5.1%, which is within the noise below — it buys this model no credit.)
- **Failed task setups are counted as unsolved, never dropped.** In held-out-99, 1 task failed in task setup on both
  tries with this model and 2 with the untrained base; in the full 360, 4 tasks failed in setup and remained failed
  after the one allowed retry. Each of them is in the denominator and contributes a 0.
- **Known weakness: the model often stops too early or does not finish a task.** It calls `terminate` before the goal
  is reached, and it rarely finishes multi-step tasks in office applications.

## How to run

llama.cpp, the exact files in this folder (`llama-server` from llama.cpp build b12032 or newer):

```bash
llama-server -m MiniCPM-V-4.7-1B-base-Q8_0.gguf \
  --mmproj mmproj-MiniCPM-V-4.7-1B-base-F16.gguf \
  --lora mc-v3-lora-f16.gguf \
  --chat-template-file chat_template.jinja \
  --jinja --parallel 1 --no-webui --load-mode none --cache-ram 512 -ngl 99 -c 8192 --port 8091
```

- `--chat-template-file chat_template.jinja` is **required**: the GGUF's built-in template has thinking turned off,
  and this model was trained and evaluated with thinking on. (Upstream ships a `thinking/` GGUF subfolder that restores
  the same template; we ship the original `chat_template.jinja` instead.)
- `--lora` loads the adapter on top of the base weights; no merged file is needed.
- `--load-mode none` and `--cache-ram 512` keep host RAM down on the 8 GB GPU machine this was tested on.
- Sampling (client side, and what every number above used): **temperature 0.7, top-p 0.95, top-k 100**, thinking on,
  max 4096 new tokens.

Then, in dry-run mode (prints actions, does not touch the mouse or keyboard):

```bash
pip install requests pillow pyautogui
python examples/run_agent.py "Open the Downloads folder"
python examples/run_agent.py "Open the Downloads folder" --execute   # really clicks: use a VM
```

The prompt, tool schema, 0–999 coordinate grid and history format in `examples/run_agent.py` are the same constants the
model was trained and scored with (`computer_use/osworld/protocol.py`, prompt variant `action-line`).

## Training

- LoRA, rank 16, alpha 32, dropout 0.05, on the language model's attention and MLP projections; vision tower and
  projector frozen. lr 5e-5, 1 epoch, max sequence length 4096 tokens (longer rows dropped, not truncated),
  batch 1 × gradient accumulation 16, bf16, warm start from the previous adapter (`adapter-mc-v2`).
- 13,000 training rows: 4,967 "keep going" rows taken from the last steps of successful ProCUA-SFT runs, all 1,314
  `terminate` rows of the earlier step set, and 6,719 replay rows, so the model does not forget the rest.
- Data: [nvidia/ProCUA-SFT](https://huggingface.co/datasets/nvidia/ProCUA-SFT) (CC BY 4.0), shards
  `procua_sft_00000.tar.zst` and `procua_sft_00001.tar.zst`, one action per row. Trajectories that start from a
  held-out task setup, goals that near-duplicate an OSWorld instruction, runs with no usable steps, and stuck runs were
  dropped by `computer_use/train/make_steps.py` before sampling. Exact counts: `computer_use/release/DATA_SOURCES.md`.

## Files

| File | What it is |
|---|---|
| `MiniCPM-V-4.7-1B-base-Q8_0.gguf` | base weights, Q8_0 GGUF, converted from `openbmb/MiniCPM-V-4.7-1B@ad9bf379` |
| `mmproj-MiniCPM-V-4.7-1B-base-F16.gguf` | vision projector for the base, F16 |
| `mc-v3-lora-f16.gguf` | the LoRA adapter, GGUF F16 (SHA-256 in `SHA256SUMS.txt`) |
| `chat_template.jinja` | the chat template with thinking enabled (required, see above) |
| `adapter-mc-v3/` | the same adapter in PEFT safetensors format (`adapter_config.json`, `adapter_model.safetensors`) |
| `SHA256SUMS.txt` | checksums for every file here |

## License and attribution

- **This adapter and card:** Apache-2.0.
- **Base model:** `openbmb/MiniCPM-V-4.7-1B` (revision `ad9bf379`), Apache-2.0 as declared in its model card. Its
  duties (ship the licence text, keep notices, state changes) are in `NOTICE`. Changes made here: LoRA fine-tune, then
  conversion of the base weights to Q8_0 GGUF and of the adapter to GGUF F16.
- **Training data:** ProCUA-SFT by NVIDIA, **CC BY 4.0**. Attribution as its card asks for:

  > Jung, Jaehun, et al. "ProCUA-SFT Technical Report." arXiv:2606.17321, 2026.
  > https://arxiv.org/abs/2606.17321 — dataset: https://huggingface.co/datasets/nvidia/ProCUA-SFT

  ```bibtex
  @misc{jung2026procuasfttechnicalreport,
    title={ProCUA-SFT Technical Report},
    author={Jaehun Jung and Ximing Lu and Brandon Cui and Muhammad Khalifa and Shaokun Zhang and Hao Zhang and Jin Xu and Amala Sanjay Deshmukh and Karan Sapra and Andrew Tao and Yejin Choi and Jan Kautz and Mingjie Liu and Yi Dong},
    year={2026},
    eprint={2606.17321},
    archivePrefix={arXiv},
    primaryClass={cs.LG},
    url={https://arxiv.org/abs/2606.17321},
  }
  ```

- **Evaluation:** OSWorld (`xlang-ai/OSWorld`, commit `8903ebeb`), Apache-2.0. Used for evaluation only; never trained
  on.
