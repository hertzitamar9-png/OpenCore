---
base_model:
  - Kwaipilot/KAT-Coder-V2.5-Dev
  - Qwen/Qwen-AgentWorld-35B-A3B
  - empero-ai/Qwen3.8-35B-A3B-Distill
base_model_relation: merge
library_name: transformers
tags:
  - merge
  - moe
  - a3b
  - ties
  - dare
  - deltanet
  - linear-attention
  - agentic
  - swe-bench
  - code
  - reasoning
  - qwen
  - qwen3.5
  - qwen3.6
  - qwen3.8
  - causal-lm
license: apache-2.0
language:
  - en
  - zh
pipeline_tag: text-generation
model_type: qwen3_5_moe
extra_gated_heading: Access via Free Patreon Membership
extra_gated_prompt: "Requests submitted directly through this native form are not approved manually. To get instant access for 100% free, please become a free member of my Patreon https://www.patreon.com/pragmaticcs"
extra_gated_button_content: "Don't click this button is useless"
---

> [!TIP]
> ### Free Model Access 🔓
> This model is gated, but **100% free** to download for my free tier Patreon community members.
> 1. Open the **[Free Patreon Unlock Post](https://www.patreon.com/pragmaticcs/posts/exclusive-model-172232779?utm_medium=clipboard_copy&utm_source=copyLink&utm_campaign=postshare_creator&utm_content=join_link)**.
> 2. Join as a free member & submit your Hugging Face username.
> 3. Your HF account will be approved automatically for instant download!
>
> 👉 **[Become a free member of my Patreon to unlock access to this model](https://www.patreon.com/pragmaticcs/posts/exclusive-model-172232779?utm_medium=clipboard_copy&utm_source=copyLink&utm_campaign=postshare_creator&utm_content=join_link)**.
>
> **NOTE:** I am not approving the access requests sent via the official huggingface gated request form above. The **only** way to get access is by becoming a **free member** of my Patreon, where I plan to post more exclusive models in the near future.

![Qwen3.8-KAT-AgentWorld-35B-A3B-Coder](https://huggingface.co/pragmaticcs/banners/resolve/main/Qwen3.8-KAT-AgentWorld-35B-A3B-Coder.png)

<div align="center">

[![Patreon Exclusive](https://img.shields.io/badge/Patreon-Member%20Exclusive-FF424D?style=for-the-badge&logo=patreon&logoColor=white)](https://www.patreon.com/pragmaticcs/posts/exclusive-model-172232779?utm_medium=clipboard_copy&utm_source=copyLink&utm_campaign=postshare_creator&utm_content=join_link)
[![License](https://img.shields.io/badge/License-Apache%202.0-6E56CF?style=for-the-badge)](https://opensource.org/licenses/Apache-2.0)
[![Architecture](https://img.shields.io/badge/Architecture-Qwen%203.5%2035B%20A3B%20MoE-2D9CDB?style=for-the-badge)](#architectural-specifications)
[![Active Params](https://img.shields.io/badge/Active%20Compute-~3.3B%20per%20token-9B51E0?style=for-the-badge)](#architectural-specifications)
[![Context](https://img.shields.io/badge/Context-262k%20Native-F2994A?style=for-the-badge)](#architectural-specifications)

</div>

> [!NOTE]
> This repository provides quantized GGUF builds of [**pragmaticcs/Qwen3.8-KAT-AgentWorld-35B-A3B-Coder**](https://huggingface.co/pragmaticcs/Qwen3.8-KAT-AgentWorld-35B-A3B-Coder)

Most coding fine-tunes generate clean snippets in isolation but choke on real developer workflows: running bash commands, parsing runtime errors, and navigating large repositories. Generalist reasoning models have the opposite problem: solid high-level logic, but brittle git/diff formatting and inconsistent tool calls.

**Qwen3.8-KAT-AgentWorld-35B-A3B-Coder** solves this problem by merging three specialized models on the Qwen 3.5 35B A3B architecture into a **fast, practical coding workhorse**:

- **[Kwaipilot/KAT-Coder-V2.5-Dev](https://huggingface.co/Kwaipilot/KAT-Coder-V2.5-Dev) (Anchor):** SOTA open coding core (69.4% SWE-bench Verified), providing raw patch generation, refactoring, and code synthesis.
- **[Qwen/Qwen-AgentWorld-35B-A3B](https://huggingface.co/Qwen/Qwen-AgentWorld-35B-A3B) (Donor 1):** Trained on 10M+ trajectories across terminal, SWE, MCP, and OS tasks to keep tool use disciplined and prevent repetition loops when commands fail.
- **[empero-ai/Qwen3.8-35B-A3B-Distill](https://huggingface.co/empero-ai/Qwen3.8-35B-A3B-Distill) (Donor 2):** Frontier reasoning and algorithmic problem-solving to handle complex logic without prompt brittleness.

---

### Contents

- [Architectural Specifications](#architectural-specifications)
- [Composition & Triad Specialization](#composition--triad-specialization)
- [Merge Methodology & Mathematical Formulation (HSCM-DP)](#merge-methodology--mathematical-formulation-hscm-dp)
- [Layer-Stratified Component Policies](#layer-stratified-component-policies)
- [Agentic Chat Template & Multi-Tier CoT](#agentic-chat-template--multi-tier-cot)
- [Recommended Generation Parameters](#recommended-generation-parameters)
- [Citation & References](#citation--references)

---

## Architectural Specifications

| Parameter | Specification |
| :--- | :---: |
| **Total Parameters** | 34.7B (Text Backbone; Vision & MTP excised) |
| **Active Parameters per Token** | ~3.3B (8 Routed Experts + 1 Shared Expert) |
| **Architecture Class** | Hybrid Recurrent-Attention MoE Causal LM (`Qwen3_5MoeForCausalLM`) |
| **Hidden Dimension** (*d*<sub>model</sub>) | 2048 |
| **Intermediate Dimension** (*d*<sub>intermediate</sub>) | 512 per routed expert (131,072 collective) / 2048 shared expert |
| **Decoder Layers** | 40 |
| **Routed Experts** | 256 Total (Top-8 routing via softmax gate) |
| **Shared Experts** | 1 Unconditional Shared Highway Expert |
| **Attention Topology** | 10 Blocks × (3 Gated DeltaNet Recurrent Layers : 1 Gated Softmax Layer) |
| **Full Attention Layers** | Layers 3, 7, 11, 15, 19, 23, 27, 31, 35, 39 |
| **Linear Attention Configuration** | 16 Key Heads / 32 Value Heads (*d*<sub>k</sub> = *d*<sub>v</sub> = 128) |
| **Full Attention Configuration** | 16 Query Heads / 2 Key-Value Heads (GQA, *d*<sub>h</sub> = 256) |
| **Position Embedding** | YaRN RoPE (θ = 10⁷, Factor = 4.0, Original = 262,144) |
| **Context Window Length** | 262,144 tokens (256k) |
| **Native Precision** | `bfloat16` |
| **Vocabulary Size** | 248,320 (Synchronized for tool-calling and CoT tokens) |

---

## Composition & Triad Specialization

The architecture treats [**KAT-Coder-V2.5-Dev**](https://huggingface.co/Kwaipilot/KAT-Coder-V2.5-Dev) as the topological parameter anchor (*W*₀). Two complementary donor models inject non-overlapping cognitive subspaces governed by a continuous depth schedule over normalized layer index *u* = *l* / 39, where *l* ∈ [0, 39]:

| Model | Structural Role | Primary Capabilities | Depth Priority |
| :--- | :--- | :--- | :---: |
| [**Kwaipilot/KAT-Coder-V2.5-Dev**](https://huggingface.co/Kwaipilot/KAT-Coder-V2.5-Dev) | **Anchor Baseline (*W*₀)** | SOTA 69.4% SWE-bench verified patch generation, repo AST parsing, code diff synthesis, refactoring purity. | Global Foundation (50%–75%) |
| [**Qwen/Qwen-AgentWorld-35B-A3B**](https://huggingface.co/Qwen/Qwen-AgentWorld-35B-A3B) | **Environment Donor (Δ₁)** | Language world modeling, terminal execution anticipation, multi-turn tool schema integrity, error rollback heuristics. | Deep Output Layers (Ascending to 35%) |
| [**empero-ai/Qwen3.8-35B-A3B-Distill**](https://huggingface.co/empero-ai/Qwen3.8-35B-A3B-Distill) | **Reasoning Donor (Δ₂)** | Frontier CoT distillation, competitive algorithmic decomposition, math logic, multi-language semantic grounding. | Central Decoder Core (Peaking at 30%) |

---

## Merge Methodology & Mathematical Formulation (HSCM-DP)

Standard merging strategies (such as naive SLERP or uniform DARE-TIES) degrade complex MoE hybrid networks. In a 35B A3B backbone, uniform coordinate dropping corrupts the recurrent eigenvalues of linear attention kernels and induces routing collapse across the 256 expert gates.

The **HSCM-DP** (*Heterogeneous Subsystem Consensus Merging with DeltaNet Preservation*) engine solves these failure modes by dividing the parameter space into distinct mathematical manifolds.

### 1. Anchor-Relative Task Formulation

Task delta vectors are computed strictly relative to the verified coding anchor *W*₀:

$$
\Delta_1 = W_{\text{AgentWorld}} - W_0, \qquad \Delta_2 = W_{\text{Qwen3.8}} - W_0
$$

### 2. Continuous Depth Schedule & Partition of Unity

Donor mixing weights are continuously modulated across layer depth *l* ∈ [0, 39]:

$$
\beta(l) = 0.10 + 0.25 \left(\frac{l}{39}\right)^{1.5} \quad (\text{AgentWorld: Tool \& Terminal Ramp})
$$

$$
\gamma(l) = 0.15 + 0.15 \exp\left(-\frac{(l - 20)^2}{72}\right) \quad (\text{Qwen3.8: Central Reasoning Envelope})
$$

$$
\alpha(l) = \max\left(0.45, \, 1.0 - \beta(l) - \gamma(l)\right) \quad (\text{Anchor: Coding Core Stability})
$$

The scalar coefficients are normalized such that:

$$
\tilde{\alpha}(l) + \tilde{\beta}(l) + \tilde{\gamma}(l) = 1.0
$$

This schedule ensures the coding specialist (*W*₀) retains majority control throughout the stack, while Qwen3.8 provides deep reasoning in the middle layers, and AgentWorld directs terminal command emission at the final projection layers.

### 3. Gram-Schmidt Orthogonal Task Disentanglement

Fine-tuned models frequently share conversational alignment drift, creating collinear task vectors that cause destructive cancelation or over-amplification. The shared collinear component is projected out of Donor 2:

$$
\Delta_2^{\parallel} = \frac{\langle \Delta_1, \Delta_2 \rangle}{\|\Delta_1\|_F^2 + \epsilon} \Delta_1
$$

$$
\Delta_2^{\perp} = \Delta_2 - \Delta_2^{\parallel}
$$

By injecting only the strictly orthogonal delta Δ₂<sup>⊥</sup>, the model absorbs 100% of Qwen3.8's unique algorithmic reasoning without double-counting shared fine-tuning updates.

### 4. MoE Router Gate Frobenius Isometry (Entropy Calibration)

When high-dimensional routing gate matrices (*W*<sub>gate</sub> ∈ ℝ<sup>2048 × 256</sup>) are linearly blended, Euclidean norm shrinkage contracts the logit scale:

$$
\left\|\tilde{W}_{\text{gate}}\right\|_F < \tilde{\alpha} \|W_0\|_F + \tilde{\beta} \|W_1\|_F + \tilde{\gamma} \|W_2\|_F
$$

In a softmax routing gate, smaller logit magnitudes act as an artificial temperature spike:

$$
\lim_{\|W_{\text{gate}}\| \to 0} \operatorname{Softmax}\left(\frac{x W_{\text{gate}}}{\sqrt{d}}\right) = \left[\frac{1}{256}, \dots, \frac{1}{256}\right]
$$

This causes uniform expert firing and expert starvation. HSCM-DP enforces **Frobenius Norm Isometry** anchored on KAT-Coder:

$$
\tilde{W}_{\text{gate}} = 0.60 W_{0, \text{gate}} + 0.25 W_{1, \text{gate}} + 0.15 W_{2, \text{gate}}
$$

$$
W_{\text{gate}}^{\text{final}} = \tilde{W}_{\text{gate}} \cdot \left(\frac{\|W_{0, \text{gate}}\|_F}{\|\tilde{W}_{\text{gate}}\|_F + \epsilon}\right)
$$

This mathematically guarantees that the routing entropy, sharpness, and expert dispatch calibration of the base coding specialist are preserved.

### 5. Outlier Feature & Channel-Kurtosis Shielding

Large language models exhibit emergent outlier feature channels (*Z* > 3.0) along the input dimension that maintain code indentation, markdown block delimiters, and AST tree boundaries.

For projection matrices *W*₀ ∈ ℝ<sup>*d*<sub>out</sub> × *d*<sub>in</sub></sup>, column-wise *L*₂ norms are evaluated:

$$
Z_j = \frac{\|W_{0, (:, j)}\|_2 - \mu_c}{\sigma_c + \epsilon}
$$

Donor updates along channels with *Z*<sub>*j*</sub> > 3.0 are attenuated by a protection factor:

$$
\Omega_j = \frac{1}{1 + 0.5 \cdot (Z_j - 3.0)}
$$

$$
\Delta_{:, j}^{\text{protected}} = \Omega_j \cdot \Delta_{:, j}
$$

This prevents donor perturbation from degrading whitespace tracking and syntax formatting.

### 6. Row-Wise Continuous Directional Forget-Gating

When fine-tuning on conversational datasets, certain neuron rows rotate counter to core coding representations. Rather than applying a binary sign mask, HSCM-DP evaluates row-wise directional cosine alignment:

$$
\cos \theta_{i} = \frac{\langle W_{0, (i, :)}, \, \Delta_{(i, :)} \rangle}{\|W_{0, (i, :)}\|_2 \|\Delta_{(i, :)}\|_2 + \epsilon}
$$

Neurons exhibiting counter-directional drift (cos θ<sub>*i*</sub> < 0) are exponentially dampened:

$$
\Phi_i = \begin{cases} 1.0 & \text{if } \cos \theta_i \ge 0 \\ \exp(-2.0 \cdot |\cos \theta_i|) & \text{if } \cos \theta_i < 0 \end{cases}
$$

$$
\Delta_{(i, :)}^{\text{gated}} = \Phi_i \cdot \Delta_{(i, :)}
$$

This allows donors to enrich feature representations while preventing them from inverting established coding weights.

### 7. Truncated SVD Denoising of Dense Projections

In dense attention and shared expert projections, task vectors are projected into their principal low-rank singular subspace to remove high-frequency optimizer noise:

$$
\Delta = U \Sigma V^T = \sum_{r=1}^{\min(m, n)} \sigma_r u_r v_r^T
$$

The spectrum is truncated to retain 85% of the total Frobenius energy (*S*²):

$$
k^* = \arg\min_k \left( \frac{\sum_{r=1}^k \sigma_r^2}{\sum_{r=1}^{\min(m, n)} \sigma_r^2} \ge 0.85 \right), \quad k^* \le 128
$$

$$
\Delta^{\text{denoised}} = \sum_{r=1}^{k^*} \sigma_r u_r v_r^T
$$

### 8. Expert-Salience Dynamic Routing & Pareto Pruning

For the 256 routed experts (*d*<sub>intermediate</sub> = 512), standard SVD would reduce representational capacity. Instead, experts are merged using **Relative Drift Salience**:

$$
\mathcal{D}_k(e) = \frac{\|\Delta_{k, e}\|_F}{\|W_{0, e}\|_F + \epsilon}
$$

Dynamic blending coefficients for expert *e* are calculated as:

$$
w_{1, \text{sal}} = \beta(l) \sqrt{\mathcal{D}_1(e) + 10^{-4}}, \qquad w_{2, \text{sal}} = \gamma(l) \sqrt{\mathcal{D}_2(e) + 10^{-4}}
$$

Surviving weights are filtered via deterministic Pareto energy thresholding (*E*<sub>ratio</sub> = 0.90) and smoothed using continuous magnitude-weighted consensus damping:

$$
A_{ij} = \frac{|\Delta_{1, ij} + \Delta_{2, ij}|}{|\Delta_{1, ij}| + |\Delta_{2, ij}| + \epsilon}
$$

$$
\Delta_{\text{expert}} = \left(b_{\text{dyn}} \Delta_{1}^{\text{clean}} + g_{\text{dyn}} \Delta_{2}^{\text{clean}}\right) \odot A^{1.5}
$$

If AgentWorld extensively modified an expert (e.g., shell parsing), that expert receives a higher donor weight. If an expert was untouched, KAT-Coder maintains full control.

### 9. Contractive Spectral-Radius Clamping for DeltaNet Recurrence

In Gated DeltaNet layers (30 of the 40 layers), linear attention relies on the state-space recurrent update:

$$
S_t = S_{t-1}(I - \beta_t q_t k_t^T) + v_t k_t^T
$$

To prevent numerical divergence over long contexts, the recurrent transition operators (`linear_attn` kernel and decay projections) must remain **contractive** (spectral radius σ<sub>max</sub> ≤ 1.0).

Using power iteration, the dominant singular values of the anchor and merged matrices are computed:

$$
\sigma_0 = \sigma_{\max}(W_0), \qquad \sigma_{\text{raw}} = \sigma_{\max}(W_{\text{raw}})
$$

If the merged matrix exceeds the anchor's spectral bound, it is scaled down:

$$
W_{\text{final}} = \begin{cases} W_{\text{raw}} \cdot \left(\frac{\sigma_0}{\sigma_{\text{raw}} + \epsilon}\right) & \text{if } \sigma_{\text{raw}} > \sigma_0 \\ W_{\text{raw}} & \text{otherwise} \end{cases}
$$

This ensures numerical stability across the entire 262,144 token context window.

### 10. Geodesic Norm Conservation

Linear combinations in high dimensions contract the matrix norm. For all projections, the output matrix is rescaled to match the target energy norm:

$$
\mathcal{S}^* = \tilde{\alpha}(l) \|W_0\|_F + \tilde{\beta}(l) \|W_1\|_F + \tilde{\gamma}(l) \|W_2\|_F
$$

$$
W_{\text{final}} = W_{\text{raw}} \cdot \left(\frac{\mathcal{S}^*}{\|W_{\text{raw}}\|_F + \epsilon}\right)
$$

This prevents hidden-state variance decay across the 40 decoder layers.

---

## Layer-Stratified Component Policies

| Parameter Subsystem | Target Identifiers | Applied Policy | Density / Rank | Mathematical Invariants |
| :--- | :--- | :---: | :---: | :--- |
| **Embeddings & LM Head** | `embed_tokens`, `lm_head` | Geodesic Norm Blend | 1.0 | Convex projection; vocabulary dimension aligned to 248,320. |
| **MoE Router Gates** | `mlp.gate.weight` | Frobenius Norm Isometry | 1.0 (No DARE) | Logit scale matched to \|*W*₀\|<sub>*F*</sub>; preserves softmax routing entropy. |
| **Linear Attention (DeltaNet)** | `linear_attn.*` | DARE-Free Smooth Addition | 1.0 | Power-iteration spectral clamping guarantees σ<sub>max</sub> ≤ σ₀. |
| **Full Attention Projections** | `self_attn.q/k/v/o_proj` | GS-Orthogonal + SVD Denoise | Rank ≤ 128 | Kurtosis protection (*Z* > 3.0); 85% Frobenius energy retention. |
| **MoE Shared Expert** | `mlp.shared_expert.*` | GS-Orthogonal + SVD Denoise | Rank ≤ 128 | Provides common syntactic grounding across all tokens. |
| **256 Routed Experts** | `mlp.experts.*` | Salience Routing + Soft TIES | 90% Energy | Pareto pruning; consensus agreement damping (*A*<sup>1.5</sup>). |
| **Normalization Layers** | `*layernorm*`, `norm` | Convex Weighted Average | 1.0 | Preserves variance calibration across residual layers. |

---

## Agentic Chat Template & Multi-Tier CoT

This model uses the [Improved Chat Template for Qwen 3.x](https://huggingface.co/pragmaticcs/Improved-Chat-Template-for-Qwen-3.x) to support multi-tier Chain-of-Thought reasoning, dual-format agentic tool execution (JSON and XML), automatic error recovery, and strict token-waste elimination.

### Template Features

- **Multi-Tier `<thought>` Reasoning:** Supports explicit internal reasoning traces before tool calls and final code synthesis.
- **Dual Tool Calling Schemas:** Compatible with both JSON-RPC function calls and XML tool tags for terminal, editor, and search operations.
- **Circuit-Breaker Recovery:** Suppresses repetition loops when shell commands or linters return non-zero exit codes.

---

## Recommended Generation Parameters

For repository-level software engineering, bug localization, and automated tool use:

| Parameter | Recommended Value | Description |
| :--- | :---: | :--- |
| **Temperature** | `0.6` | Balances strict syntactic validity with algorithmic path exploration. |
| **Top-P** | `0.95` | Eliminates low-probability token tails while preserving alternative logic paths. |
| **Top-K** | `20` | Restricts token candidate pools to prevent architectural syntax drift. |
| **Min-P** | `0.0` (Off) | Disabled in favor of explicit Top-K / Top-P governance. |
| **Repetition Penalty** | `1.0` (Off) | Disabled to prevent syntax degradation in repetitive code patterns (indentation, braces). |
| **Presence Penalty** | `0.0` | Prevents naming mutations across long-context symbol resolution. |

---

## Citation & References

- [Kwaipilot/KAT-Coder-V2.5-Dev](https://huggingface.co/Kwaipilot/KAT-Coder-V2.5-Dev)
- [Qwen/Qwen-AgentWorld-35B-A3B](https://huggingface.co/Qwen/Qwen-AgentWorld-35B-A3B)
- [empero-ai/Qwen3.8-35B-A3B-Distill](https://huggingface.co/empero-ai/Qwen3.8-35B-A3B-Distill)
- [Improved Chat Template for Qwen 3.x](https://huggingface.co/pragmaticcs/Improved-Chat-Template-for-Qwen-3.x)

```bibtex
@inproceedings{yadav2023ties,
  title={Resolving Interference When Merging Models},
  author={Yadav, Prateek and Tam, Derek and Choshen, Leshem and Raffel, Colin and Bansal, Mohit},
  booktitle={Advances in Neural Information Processing Systems (NeurIPS)},
  volume={36},
  pages={7093--7115},
  year={2023}
}

@article{yu2024dare,
  title={Language Models are Super Mario: Absorbing Abilities from Homologous Models as a Free Lunch},
  author={Yu, Le and Yu, Bowman and Yu, Haiyang and Huang, Fei and Li, Yongbin},
  journal={arXiv preprint arXiv:2311.03099},
  year={2023}
}

@article{yang2024deltanet,
  title={Gated Delta Networks: Improving Mamba2 with Delta Rule for Linear Recurrent Attention},
  author={Yang, Songlin and Wang, Bailin and Shen, Yikang and Panda, Rameswar and Kim, Yoon},
  journal={arXiv preprint arXiv:2412.06464},
  year={2024}
}
```