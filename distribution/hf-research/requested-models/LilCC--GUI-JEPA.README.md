---
language:
  - en
license: mit
base_model:
  - timm/vit_large_patch16_224.augreg_in21k_ft_in1k
  - sentence-transformers/all-MiniLM-L12-v2
  - sentence-transformers/all-MiniLM-L6-v2
tags:
  - gui-agent
  - mobile-gui
  - jepa
  - pytorch
  - androidcontrol
  - arxiv:2610.36877
---

<div align="center">

# GUI-JEPA

### Candidate-Conditioned JEPA Latents as a Fast Action Policy for Mobile GUI Agents

[![Paper](https://img.shields.io/badge/arXiv-2610.36877-b31b1b)](https://arxiv.org/abs/2610.36877)
[![Code](https://img.shields.io/badge/GitHub-GUI--JEPA-181717?logo=github)](https://github.com/bcwang-sjtu/GUI-JEPA)
[![License](https://img.shields.io/badge/License-MIT-16a34a)](LICENSE)

**Bingchao Wang · Yongcheng Jing · Shunyu Liu · Chengyu Wang · Jun Huang · Jie Yang · Wei Liu · Dacheng Tao**

<img src="assets/teaser.png" width="560" alt="GUI-JEPA accuracy and inference speed">

</div>

GUI-JEPA selects mobile GUI actions by scoring candidate-conditioned JEPA latents in a single batched pass. The complete system has approximately **0.50B parameters**, including its frozen CLIP text encoder, and runs **2.8–4.0× faster** than a same-protocol Qwen2.5-VL-7B reference served with vLLM on one NVIDIA H20.

This repository contains the **final seed-3 checkpoint**, serving both AC-Low and AC-High with one policy. The included encoder, policy, span ranker and click reranker reproduce **84.06 / 60.99** IDD step accuracy with the released evaluation code.

## Model components

| Component | Location | Contents |
|:--|:--|:--|
| JEPA visual encoder | `encoder/encoder_1.pt` | Frozen ViT-L/16 after GUI screenshot pretraining |
| Action policy | `policy/heads.pt` | Jointly trained residual predictor, structural head and text-matcher head |
| Policy text matcher | `policy/matcher_lm/` | Fine-tuned MiniLM-L12 and tokenizer |
| Goal-source span ranker | `span_ranker/` | Fine-tuned MiniLM-L6, tokenizer and scoring head for `input_text` |
| Type-gated click reranker | `click_reranker/` | Fine-tuned MiniLM-L12, tokenizer, scoring head and metadata |
| Application vocabulary | `metadata/app_vocab.json` | Ordered app vocabulary used by the `open_app` argument heuristic |

The model definitions, loaders and evaluation scripts match [code commit `c03c565`](https://github.com/bcwang-sjtu/GUI-JEPA/tree/c03c565065a8b31f9d4ce1be6c29104c8869799f).

The inference settings are recorded in [`gui_jepa_config.json`](gui_jepa_config.json). File sizes and SHA-256 checksums are recorded in [`manifest.json`](manifest.json) and [`SHA256SUMS`](SHA256SUMS).

The frozen [`openai/clip-vit-base-patch32`](https://huggingface.co/openai/clip-vit-base-patch32) text encoder is loaded from its upstream repository. The evaluation and preprocessing scripts also initialize the MiniLM modules from their upstream repositories before replacing the trained weights with these checkpoints. An internet connection or an existing local Hugging Face cache is needed on the first run.

## Method

<p align="center">
<img src="assets/overview.png" width="100%" alt="GUI-JEPA model architecture">
</p>

The screenshot is resized directly to 224 × 224, normalized with ImageNet mean and standard deviation, and encoded into a 14 × 14 grid of 1,024-dimensional patch tokens. A residual predictor conditions this grid on every executable candidate action and its region of interest. A structural head and MiniLM text matcher score the candidates. The span ranker fills typed text from the goal; the click reranker refines click selection.

Use the preprocessing in the code repository to preserve the evaluation protocol. The default classification transform associated with the upstream timm backbone uses different normalization and cropping.

## Download and evaluate

Install the [code repository](https://github.com/bcwang-sjtu/GUI-JEPA) and its dependencies. Download the complete model bundle:

```bash
pip install -U huggingface_hub
export HF_REPO_ID=LilCC/GUI-JEPA
hf download "$HF_REPO_ID" --local-dir checkpoints/GUI-JEPA
export CKPT="$PWD/checkpoints/GUI-JEPA"
```

Prepare AndroidControl and its feature cache using the steps in the code README. For feature extraction, use the downloaded visual encoder:

```bash
export AC=/path/to/android_control
export CACHE=/path/to/ac_cache
SHARDS="$AC/android_control-*-of-*"

python scripts/preprocess/dump_texts.py --shards "$SHARDS" --splits "$AC/splits.json" --out-dir "$CACHE"
python scripts/preprocess/dump_candidate_texts.py --shards "$SHARDS" --out "$CACHE/cand_texts.json"
python scripts/preprocess/build_cache.py --shards "$SHARDS" --splits-dir "$AC" \
    --encoder-ckpt "$CKPT/encoder/encoder_1.pt" --out "$CACHE"

python scripts/train_policy.py --cache "$CACHE" --eval-only \
    --ckpt-dir "$CKPT/policy" --span-ranker "$CKPT/span_ranker" \
    --click-reranker "$CKPT/click_reranker" --app-vocab "$CKPT/metadata/app_vocab.json" \
    --span-max-words 16 --span-cap 800 --rerank-beta 1 --rerank-gate 1 \
    --seed 1 --subsplit IDD --out outputs/eval_IDD
```

These commands run from the root of the code repository. Evaluation requires the AndroidControl cache and matching instruction and candidate-text files. `hf download` preserves the directory structure expected by the scripts. Full-system loading uses the `gui_jepa` modules and these scripts; the top-level bundle is a custom architecture rather than a single Transformers `AutoModel`.

The policy predictor is already stored in `policy/heads.pt`; evaluation does not require the separate dynamics-pretraining initializer. Use `--subsplit full` for the full test set. The model uses one-step prediction with ground-truth action history under the paper's offline evaluation protocol.

## Results

| Evaluation | Episodes | Steps | AC-Low | AC-High |
|:--|--:|--:|--:|--:|
| IDD, released seed-3 checkpoint | 716 | 3,858 | **84.06** | **60.99** |
| Full test, seed 3 (reported in the paper) | 1,543 | 8,444 | **78.64** | **53.75** |

Accuracy is step accuracy in percent under the exact-match judge. AC-Low includes the step instruction; AC-High uses the goal. The IDD values above were reproduced with the released code on 2026-10-29. Detailed metrics are in [`evaluation/idd.json`](evaluation/idd.json). These are single-checkpoint results; the paper also reports three-seed averages.

## Training

The encoder was initialized from an ImageNet-pretrained ViT-L/16 and pretrained on 189K unlabelled GUI-Odyssey screenshots and VideoCUA frames. It remains frozen during policy training. The policy was trained on the AndroidControl training set for six epochs with seed 3, an AdamW learning rate of `1e-4` for scoring heads and `2e-5` for MiniLM and the predictor, weight decay `0.01`, and non-click loss weight `1.5`.

The final predictor uses three Transformer blocks and residual candidate conditioning. The span ranker takes spans from the goal with a maximum of 16 words and a cap of 800 candidates. The click reranker uses 11 auxiliary geometry/flag features, fusion weight `1.0` and confidence gate `1.0`.

## Intended use and scope

The weights support research on candidate-based mobile GUI policies, AndroidControl evaluation and model latency. Inputs include a screenshot, accessibility-tree candidates, a task goal and action history. The benchmark results measure offline step selection; they do not establish closed-loop task completion. English goal text and the AndroidControl action space are the evaluated setting.

## License and upstream models

GUI-JEPA contributions are released under the [MIT License](LICENSE). The visual-backbone initialization and MiniLM derivatives retain their upstream Apache-2.0 notices and conditions; see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) and [`licenses/Apache-2.0.txt`](licenses/Apache-2.0.txt). The weights in this repository were adapted through the GUI-JEPA training procedures described above. External models and datasets retain their own licenses.

## Citation

```bibtex
@article{wang2026guijepa,
  title   = {{GUI-JEPA}: Candidate-Conditioned {JEPA} Latents as a Fast Action Policy for Mobile {GUI} Agents},
  author  = {Wang, Bingchao and Jing, Yongcheng and Liu, Shunyu and Wang, Chengyu and Huang, Jun and Yang, Jie and Liu, Wei and Tao, Dacheng},
  journal = {arXiv preprint arXiv:2610.36877},
  year    = {2026}
}
```
