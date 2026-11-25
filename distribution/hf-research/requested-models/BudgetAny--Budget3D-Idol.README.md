---
license: apache-2.0
base_model: speridlabs/idol
base_model_relation: finetune
pipeline_tag: image-to-3d
library_name: idol
tags:
- budgetany
- budget3d
- adaptive-tokenization
- gaussian-splatting
- 3d
- image-to-3d
---

# Budget3D-Idol

🌐 [Project page](https://budgetany.github.io/budget3d/) &nbsp;|&nbsp; 💻 [Code](https://github.com/BudgetAny/Budget3D) &nbsp;|&nbsp; 🎮 [Demo](https://huggingface.co/spaces/hugging-apps/budget3d-idol) &nbsp;|&nbsp; 🖥️ [WebUI](https://github.com/RetroUpgrade-YouTube/budget3d-webui) &nbsp;|&nbsp; 📄 Tech report (coming soon) · [BudgetPix paper](https://arxiv.org/abs/2610.12307) &nbsp;|&nbsp; 🧩 [BudgetAny](https://huggingface.co/BudgetAny)

**One checkpoint, any token budget.** Budget3D-Idol is a fine-tune of
[speridlabs/idol](https://huggingface.co/speridlabs/idol), a single-image to 3D Gaussian splat generator. At
inference you choose how many of Idol's 8,192 latent tokens the diffusion transformer runs on: the regions where the
latent varies least, empty space first, are merged into coarser tokens, and sampling gets faster and lighter. The same
weights serve every budget; Idol's image encoders and Gaussian decoders are used unchanged. The 5-step model is
[BudgetAny/Budget3D-Idol-Turbo](https://huggingface.co/BudgetAny/Budget3D-Idol-Turbo).

> **Unofficial, open-source implementation.** This model is part of BudgetAny, a community reimplementation and
> extension of the compute-adaptive tokenization idea of
> [*BudgetPix: Compute-Adaptive Tokenization for Pixel-Space Image Diffusion*](https://arxiv.org/abs/2610.12307) (Kara et al., 2026). It is not
> an official release of that paper, and BudgetAny is not a company or a lab. The paper covers pixel-space image
> diffusion only; applying the idea to image-to-3D is an extension made here, and the numbers on this card are
> measurements of this project, not results from the paper. Thanks to the BudgetPix authors for the method.

## Files

| file | base model | dense tokens | parameters | size | SHA-256 |
|---|---|---|---|---|---|
| `budget3d_idol.safetensors` | Idol (50 sampling steps) | 8,192 | 378 M | 1.51 GB | `8d87669ab02823d6…` |

The file holds the fine-tuned denoiser (Idol's DiT and the Budget3D modules) as fp32 EMA weights. Also in the
repository: `perf/` (the complete speed and memory reports) and `assets/` (the clips below; `assets/fast/`: the same
objects sampled with optimised inference). Idol's image encoders and Gaussian decoders are downloaded from
[speridlabs/idol](https://huggingface.co/speridlabs/idol) by the code.

## Usage

```bash
git clone https://github.com/BudgetAny/Budget3D && cd Budget3D    # setup: see README.md
hf download BudgetAny/Budget3D-Idol budget3d_idol.safetensors config.json --local-dir weights
python scripts/sample.py --ckpt weights/budget3d_idol.safetensors --image photo.jpg --budget 0.5 --out photo.ply
```

`--budget` is the fraction of the 8,192 tokens (0.0625 to 1); `--fast` switches on the optimised inference.

For a graphical interface, try the online [demo](https://huggingface.co/spaces/hugging-apps/budget3d-idol) on Hugging Face Spaces (Budget3D-Idol, nothing to install)
or run the community [WebUI](https://github.com/RetroUpgrade-YouTube/budget3d-webui) on your own GPU.

## Speed and memory

One NVIDIA H100 80GB HBM3, 50 Euler steps with classifier-free guidance 3, batches of 8 objects, optimised inference (`--fast`): bf16 transformer blocks, TF32 elsewhere, cuDNN fused attention, torch.compile (blocks + adapters). Times are per object, averaged over the 72 objects in full batches after each GPU's warm-up batch; the speed-up is the sampling time at 100 % divided by the sampling time at the budget, for the same batch.

| budget | tokens | sampling / object | speed-up | peak sampling memory | end to end / object |
|---:|---:|---:|---:|---:|---:|
| 100 % | 8,192 | 5.67 s | 1.00× | 9.9 GB | 11.80 s |
| 90 % | 7,373 | 5.15 s | 1.10× | 9.6 GB | 10.85 s |
| 80 % | 6,554 | 4.59 s | 1.24× | 9.2 GB | 10.34 s |
| 70 % | 5,734 | 4.10 s | 1.39× | 8.9 GB | 9.64 s |
| 60 % | 4,915 | 3.60 s | 1.58× | 8.5 GB | 9.33 s |
| 50 % | 4,096 | 3.18 s | 1.79× | 8.2 GB | 8.96 s |
| 40 % | 3,277 | 2.66 s | 2.14× | 7.8 GB | 8.62 s |

Peak memory includes about 4.5 GB of resident weights and image encoders.
End to end adds decoding the Gaussians (2.53 s) and rendering a 72-frame orbit (3.60 s), which do not depend on the budget. The optimised settings speed up the stock Idol in the same way; the budget speed-ups come on top of
them. This run covers budgets from 100 % to 40 %; the complete reports are in `perf/`.

<details>
<summary>The same in full precision (fp32), down to 10 % of the tokens</summary>

| budget | tokens | sampling / object | speed-up | peak sampling memory | end to end / object |
|---:|---:|---:|---:|---:|---:|
| 100 % | 8,192 | 63.44 s | 1.00× | 14.8 GB | 67.60 s |
| 90 % | 7,373 | 56.25 s | 1.13× | 14.1 GB | 60.33 s |
| 80 % | 6,554 | 49.58 s | 1.28× | 13.4 GB | 53.70 s |
| 70 % | 5,734 | 43.28 s | 1.47× | 12.8 GB | 47.37 s |
| 60 % | 4,915 | 37.48 s | 1.69× | 12.1 GB | 41.57 s |
| 50 % | 4,096 | 32.20 s | 1.97× | 11.4 GB | 36.25 s |
| 40 % | 3,277 | 27.15 s | 2.34× | 10.7 GB | 31.22 s |
| 30 % | 2,458 | 22.50 s | 2.82× | 10.1 GB | 26.66 s |
| 20 % | 1,638 | 18.28 s | 3.47× | 9.4 GB | 22.53 s |
| 10 % | 819 | 14.48 s | 4.38× | 8.7 GB | 18.82 s |

Averaged over the 160 objects in full batches after each GPU's warm-up batch.

</details>

## Examples

Each clip shows the input image and the same object generated at 100 % down to 40 % of the tokens, from the same seed
and camera orbit, with the sampling speed-up and peak memory of each budget (full precision). The objects were picked
by hand from the Idol repository's example images.

<video src="https://huggingface.co/BudgetAny/Budget3D-Idol/resolve/main/assets/fig-knight.mp4" width="100%" autoplay loop muted playsinline controls></video>

<video src="https://huggingface.co/BudgetAny/Budget3D-Idol/resolve/main/assets/food-avocado.mp4" width="100%" autoplay loop muted playsinline controls></video>

<video src="https://huggingface.co/BudgetAny/Budget3D-Idol/resolve/main/assets/fig-axolotl.mp4" width="100%" autoplay loop muted playsinline controls></video>

<video src="https://huggingface.co/BudgetAny/Budget3D-Idol/resolve/main/assets/fig-robot-cute.mp4" width="100%" autoplay loop muted playsinline controls></video>

<video src="https://huggingface.co/BudgetAny/Budget3D-Idol/resolve/main/assets/fur-chesterfield.mp4" width="100%" autoplay loop muted playsinline controls></video>

<video src="https://huggingface.co/BudgetAny/Budget3D-Idol/resolve/main/assets/obj-power-drill.mp4" width="100%" autoplay loop muted playsinline controls></video>

## Citation

Tech report coming soon. Until then, please cite the BudgetPix paper:

```bibtex
@article{kara2026budgetpix,
  title={BudgetPix: Compute-Adaptive Tokenization for Pixel-Space Image Diffusion},
  author={Ozgur Kara and Yujia Chen and Daniel Watson and David Forsyth and James Matthew Rehg and Wen-Sheng Chu and Du Tran},
  journal={arXiv preprint arXiv:2610.12307},
  year={2026}
}
```

Built on [Idol](https://huggingface.co/speridlabs/idol) by Sperid Labs, [DINOv3](https://github.com/facebookresearch/dinov3), the [FLUX.2](https://huggingface.co/black-forest-labs) VAE and [Dome-Objaverse](https://huggingface.co/datasets/zeyuanyin/Dome-Objaverse) renders of [Objaverse](https://objaverse.allenai.org/).

We acknowledge the compute support of the Health Care Engineering Systems Center within the Coordinated Science Lab at UIUC.

## License

Apache-2.0. The weights are a fine-tune of [speridlabs/idol](https://huggingface.co/speridlabs/idol), which is
released under Apache-2.0. Running the model also needs DINOv3 ViT-H+/16 and the FLUX.2 VAE, which are not part of
this repository and have their own licenses.
