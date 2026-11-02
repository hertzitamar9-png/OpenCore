---
license: apache-2.0
tags:
- computer-use
- gui-grounding
- reinforcement-learning
- grpo
- qwen
- playwright
- browser-agent
pipeline_tag: text-generation
---

# JEV-Computer-Use: System-One Decision Agent for Computer & Browser Use

**JEV-Computer-Use** is a fast System-One decision model designed for high-speed Computer Use and Browser Navigation. Unlike traditional vision-language agents that output slow, verbose text reasoning before each mouse click, JEV uses a **single-readout candidate slot formulation**, outputting calibrated discrete action probabilities in a single forward pass.

## 🚀 Key Features
- **Ultra-fast Execution**: Single-token action readout ($A, B, C...$) enables sub-second decision latency.
- **Online Interactive GRPO**: Built-in Group Relative Policy Optimization training with real headless Chromium (Playwright).
- **VLM Verifier**: Automated semantic verification using Qwen Vision-Language judge.
- **Calibrated Policy**: Trained with combined Cross-Entropy, Brier calibration loss, and analytical closed-form KL regularization.

## 📦 Training & Inference Code
The full training, data preprocessing, and Online GRPO environment code is available at:
👉 [https://github.com/pidsana42-lgtm/JEV-Computer-Use](https://github.com/pidsana42-lgtm/JEV-Computer-Use)

## 🛠️ Usage Quickstart

```bash
git clone https://github.com/pidsana42-lgtm/JEV-Computer-Use.git
cd JEV-Computer-Use
./start.sh
```
