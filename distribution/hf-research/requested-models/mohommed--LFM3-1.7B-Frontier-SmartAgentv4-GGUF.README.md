---
library_name: transformers
base_model: LiquidAI/LFM3-1.7B
pipeline_tag: text-generation
license: apache-2.0
tags:
- lfm3
- liquid-ai
- sdt
- smart-deep-training
- simpo
- uncensored
- 512k-context
- gguf
- imatrix
---

# 🧠 mohommed/LFM3-1.7B-Frontier-SmartAgentv4 (SDT-v2 • Assistant-Masked • ShortConv-Unfrozen • SimPO • 512K)

**`LFM3-1.7B-Frontier-SmartAgentv4`** is trained via **SDT-v2 (Smart Deep Training v2)** across Dual-T4 GPUs:
1. **100% Assistant-Turn Loss Masking + Focal Surprise Gate $(1 - p_t)^{1.5}$ + Pivotal Token Boost ($1.85\times$).**
2. **Unfrozen Native `shortconv.conv` 1D Convolutions & QK-Norms alongside Rank-Stabilized LoRA (`rsLoRA r=64, alpha=128`).**
3. **Reference-Free SimPO Contrastive Margin Pass (Winner $y^+$ vs. Trap/Slop Loser $y^-$).**
4. **Wise-FT Task-Vector Anchor Blending (`0.88 / 0.12`) + Post-Merge Triple-Subspace SVD Uncensoring.**
5. **512K Fast ABF-RoPE + `llama-imatrix` Calibrated GGUFs:** Hosted in [mohommed/LFM3-1.7B-Frontier-SmartAgentv4-GGUF](https://huggingface.co/mohommed/LFM3-1.7B-Frontier-SmartAgentv4-GGUF).