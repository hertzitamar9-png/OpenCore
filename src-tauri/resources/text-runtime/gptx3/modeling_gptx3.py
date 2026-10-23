"""GPT-X3 Transformers implementation with hybrid recurrent/KV caching.

Parameter names match TrainWork. FLA is optional; the PyTorch GDN2 path
implements the same recurrence on CPU, Windows, and CUDA.
"""

import importlib

import torch
from torch import nn
from torch.nn import functional as F
from transformers import PreTrainedModel
from transformers.generation import GenerationMixin
from transformers.modeling_outputs import CausalLMOutputWithPast

from .configuration_gptx3 import GPTX3Config


class GPTX3Cache:
    def __init__(self, num_layers):
        self.layers = [{} for _ in range(num_layers)]
        self.seen_tokens = 0

    def get_seq_length(self, layer_idx=0):
        return self.seen_tokens

    def reorder_cache(self, beam_idx):
        for layer in self.layers:
            for name, value in layer.items():
                if isinstance(value, torch.Tensor):
                    layer[name] = value.index_select(0, beam_idx.to(value.device))


class _FLALayerCache:
    """Minimal single-layer interface used by FLA's GDN2 forward."""
    def __init__(self, state):
        self.state = state

    def __len__(self):
        return int(self.state is not None)

    def __getitem__(self, index):
        return self.state

    def update(self, layer_idx=0, offset=0, **kwargs):
        self.state = kwargs
        return self.state


class RMSNorm(nn.Module):
    def __init__(self, dim, eps=1e-6, offset=False):
        super().__init__()
        self.eps, self.offset = eps, offset
        self.weight = nn.Parameter(torch.zeros(dim) if offset else torch.ones(dim))

    def forward(self, x):
        y = x.float() * torch.rsqrt(x.float().square().mean(-1, keepdim=True) + self.eps)
        if self.offset:
            return (y * (1.0 + self.weight.float())).to(x.dtype)
        return y.to(x.dtype) * self.weight


def rotary(x, cos, sin):
    dim = cos.shape[-1] * 2
    a, b = x[..., :dim].float().reshape(*x.shape[:-1], -1, 2).unbind(-1)
    rotated = torch.stack((a * cos - b * sin, a * sin + b * cos), -1).flatten(-2)
    return torch.cat((rotated.to(x.dtype), x[..., dim:]), -1)


class GPTX3Attention(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.config = config
        h, d, kv = config.num_attention_heads, config.head_dim, config.num_key_value_heads
        self.q_gate_proj = nn.Linear(config.hidden_size, 2 * h * d, bias=False)
        self.k_proj = nn.Linear(config.hidden_size, kv * d, bias=False)
        self.v_proj = nn.Linear(config.hidden_size, kv * d, bias=False)
        self.o_proj = nn.Linear(h * d, config.hidden_size, bias=False)
        self.q_norm = RMSNorm(d, config.rms_norm_eps, offset=True)
        self.k_norm = RMSNorm(d, config.rms_norm_eps, offset=True)

    def forward(self, x, cos, sin, attention_mask, state):
        batch, length, _ = x.shape
        c = self.config
        q, gate = self.q_gate_proj(x).view(batch, length, c.num_attention_heads, 2 * c.head_dim).chunk(2, -1)
        q = self.q_norm(q.transpose(1, 2))
        k = self.k_norm(self.k_proj(x).view(batch, length, c.num_key_value_heads, c.head_dim).transpose(1, 2))
        v = self.v_proj(x).view(batch, length, c.num_key_value_heads, c.head_dim).transpose(1, 2)
        q, k = rotary(q, cos, sin), rotary(k, cos, sin)
        past_length = 0
        if state is not None and "k" in state:
            past_length = state["k"].shape[2]
            k, v = torch.cat((state["k"], k), 2), torch.cat((state["v"], v), 2)
        if state is not None:
            state.update(k=k, v=v)
        key_pos = torch.arange(k.shape[2], device=x.device)
        query_pos = torch.arange(length, device=x.device) + past_length
        mask = (key_pos[None, :] <= query_pos[:, None])[None, None]
        mask = mask & attention_mask[:, None, None, :].bool()
        if past_length == 0 and bool(attention_mask.all()):
            y = F.scaled_dot_product_attention(q, k, v, is_causal=True, enable_gqa=True)
        else:
            y = F.scaled_dot_product_attention(q, k, v, attn_mask=mask, enable_gqa=True)
        y = y * torch.sigmoid(gate.transpose(1, 2))
        return self.o_proj(y.transpose(1, 2).contiguous().view(batch, length, -1))


class GPTX3GDN2Kernel(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.config = config
        hidden, dim = config.hidden_size, config.gdn_head_dim
        heads = hidden // dim
        self.q_proj = nn.Linear(hidden, hidden, bias=False)
        self.k_proj = nn.Linear(hidden, hidden, bias=False)
        self.v_proj = nn.Linear(hidden, hidden, bias=False)
        self.q_conv1d = nn.Conv1d(hidden, hidden, config.conv_size, groups=hidden, bias=False)
        self.k_conv1d = nn.Conv1d(hidden, hidden, config.conv_size, groups=hidden, bias=False)
        self.v_conv1d = nn.Conv1d(hidden, hidden, config.conv_size, groups=hidden, bias=False)
        self.f_proj = nn.Sequential(nn.Linear(hidden, dim, bias=False), nn.Linear(dim, hidden, bias=False))
        self.b_proj = nn.Linear(hidden, hidden, bias=False)
        self.w_proj = nn.Linear(hidden, hidden, bias=False)
        self.A_log = nn.Parameter(torch.zeros(heads))
        self.dt_bias = nn.Parameter(torch.zeros(hidden))
        self.g_proj = nn.Sequential(nn.Linear(hidden, dim, bias=False), nn.Linear(dim, hidden, bias=True))
        self.o_norm = RMSNorm(dim, config.gdn_norm_eps)
        self.o_proj = nn.Linear(hidden, hidden, bias=False)

    def _forward_fla(self, x, state, layer_class):
        c = self.config
        layer = getattr(self, "_fla_adapter", None)
        if layer is None:
            # The adapter is deliberately unregistered: all its weights alias
            # the canonical checkpoint parameters; it owns no extra weights.
            with torch.device("meta"):
                layer = layer_class(hidden_size=c.hidden_size, head_dim=c.gdn_head_dim,
                                    num_heads=c.hidden_size // c.gdn_head_dim,
                                    num_v_heads=c.hidden_size // c.gdn_head_dim,
                                    mode="chunk", use_short_conv=True,
                                    conv_size=c.conv_size, norm_eps=c.gdn_norm_eps, layer_idx=0)
            object.__setattr__(self, "_fla_adapter", layer)
        layer.train(self.training)
        for name in ("q_proj", "k_proj", "v_proj", "f_proj", "b_proj", "w_proj", "g_proj", "o_proj"):
            setattr(layer, name, getattr(self, name))
        layer.A_log, layer.dt_bias = self.A_log, self.dt_bias
        layer.o_norm.weight = self.o_norm.weight
        for name in ("q_conv1d", "k_conv1d", "v_conv1d"):
            getattr(layer, name).weight = getattr(self, name).weight
        fla_cache = None
        if state is not None:
            previous = None
            if "recurrent" in state:
                previous = {"recurrent_state": state["recurrent"],
                            "conv_state": tuple(F.pad(state[name], (1, 0)) for name in ("conv_q", "conv_k", "conv_v"))}
            fla_cache = _FLALayerCache(previous)
        result, _, _ = layer(x, past_key_values=fla_cache, use_cache=state is not None)
        if state is not None:
            state["recurrent"] = fla_cache.state["recurrent_state"]
            for name, value in zip(("conv_q", "conv_k", "conv_v"), fla_cache.state["conv_state"]):
                state[name] = value[..., -(c.conv_size - 1):].contiguous()
        return result

    def _conv(self, x, conv, valid, state, name, all_valid):
        batch, length, channels = x.shape
        size = self.config.conv_size
        history = x.new_zeros(batch, channels, size - 1)
        if state is not None and name in state:
            history = state[name]
        if all_valid:
            combined = torch.cat((history, x.transpose(1, 2)), -1)
            y = F.silu(F.conv1d(combined, conv.weight, groups=channels)).transpose(1, 2)
            history = combined[..., -(size - 1):]
        else:
            outputs = []
            for t in range(length):
                window = torch.cat((history, x[:, t, :, None]), -1)
                outputs.append(F.silu((window * conv.weight[:, 0][None]).sum(-1)))
                history = torch.where(valid[:, t, None, None], window[..., 1:], history)
            y = torch.stack(outputs, 1)
        if state is not None:
            state[name] = history
        return y

    def forward(self, x, valid, state):
        c = self.config
        batch, length, _ = x.shape
        dim, heads = c.gdn_head_dim, c.hidden_size // c.gdn_head_dim
        all_valid = bool(valid.all())
        if c.use_fla and x.is_cuda and all_valid:
            try:
                layer_class = importlib.import_module("fla.layers.gdn2").GatedDeltaNet2
            except ImportError:
                layer_class = None
            if layer_class is not None:
                return self._forward_fla(x, state, layer_class)
        q, k, v = [self._conv(proj(x), conv, valid, state, name, all_valid).view(batch, length, heads, dim)
                   for proj, conv, name in [(self.q_proj, self.q_conv1d, "conv_q"),
                                            (self.k_proj, self.k_conv1d, "conv_k"),
                                            (self.v_proj, self.v_conv1d, "conv_v")]]
        g = F.softplus(self.f_proj(x).float() + self.dt_bias.float()).view(batch, length, heads, dim)
        g = -self.A_log.float().exp()[None, None, :, None] * g
        b, w = self.b_proj(x).sigmoid().view_as(q), self.w_proj(x).sigmoid().view_as(v)
        initial = state.get("recurrent") if state is not None else None
        # FLA's L2 normalization uses rsqrt(sum(x*x) + 1e-6).
        q = q.float() * torch.rsqrt(q.float().square().sum(-1, keepdim=True) + 1e-6)
        k = k.float() * torch.rsqrt(k.float().square().sum(-1, keepdim=True) + 1e-6)
        q = q * dim**-0.5
        recurrent = x.new_zeros(batch, heads, dim, dim, dtype=torch.float32) if initial is None else initial
        outputs = []
        for t in range(length):
            decayed = recurrent * g[:, t].exp().unsqueeze(-1)
            erase = ((b[:, t].float() * k[:, t]).unsqueeze(-1) * decayed).sum(-2)
            update = w[:, t].float() * v[:, t].float() - erase
            new_state = decayed + k[:, t].unsqueeze(-1) * update.unsqueeze(-2)
            recurrent = torch.where(valid[:, t, None, None, None], new_state, recurrent)
            outputs.append((q[:, t].unsqueeze(-1) * recurrent).sum(-2))
        o = torch.stack(outputs, 1).to(x.dtype)
        if state is not None:
            state["recurrent"] = recurrent
        gate = self.g_proj(x).view(batch, length, heads, dim)
        normalized = o.float() * torch.rsqrt(o.float().square().mean(-1, keepdim=True) + c.gdn_norm_eps)
        y = (normalized * self.o_norm.weight.float() * torch.sigmoid(gate.float())).to(o.dtype)
        return self.o_proj(y.flatten(-2))


class GPTX3GDN2(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.kernel = GPTX3GDN2Kernel(config)

    def forward(self, x, valid, state):
        return self.kernel(x, valid, state)


class GPTX3MLP(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.w_gate = nn.Linear(config.hidden_size, config.intermediate_size, bias=False)
        self.w_up = nn.Linear(config.hidden_size, config.intermediate_size, bias=False)
        self.w_down = nn.Linear(config.intermediate_size, config.hidden_size, bias=False)

    def forward(self, x):
        return self.w_down(F.silu(self.w_gate(x)) * self.w_up(x))


class GPTX3Block(nn.Module):
    def __init__(self, config, index):
        super().__init__()
        self.ln_1 = RMSNorm(config.hidden_size, config.rms_norm_eps)
        self.ln_2 = RMSNorm(config.hidden_size, config.rms_norm_eps)
        self.full_attention = config.is_full_attention_layer(index)
        self.attn = GPTX3Attention(config) if self.full_attention else GPTX3GDN2(config)
        self.mlp = GPTX3MLP(config)

    def forward(self, x, cos, sin, mask, state):
        h = self.ln_1(x)
        x = x + (self.attn(h, cos, sin, mask, state) if self.full_attention else self.attn(h, mask[:, -x.shape[1]:], state))
        return x + self.mlp(self.ln_2(x))


class GPTX3ForCausalLM(PreTrainedModel, GenerationMixin):
    config_class = GPTX3Config
    base_model_prefix = "transformer"
    main_input_name = "input_ids"
    _tied_weights_keys = {"lm_head.weight": "transformer.wte.weight"}
    _no_split_modules = ["GPTX3Block"]

    @classmethod
    def _supports_default_dynamic_cache(cls):
        return False

    def __init__(self, config):
        super().__init__(config)
        self.transformer = nn.ModuleDict({
            "wte": nn.Embedding(config.vocab_size, config.hidden_size),
            "h": nn.ModuleList([GPTX3Block(config, i) for i in range(config.num_hidden_layers)]),
            "ln_f": RMSNorm(config.hidden_size, config.rms_norm_eps),
        })
        self.lm_head = nn.Linear(config.hidden_size, config.vocab_size, bias=False)
        self.register_buffer("rope_cos", None, persistent=False)
        self.register_buffer("rope_sin", None, persistent=False)
        self.post_init()

    def _init_weights(self, module):
        if isinstance(module, (nn.Linear, nn.Embedding, nn.Conv1d)):
            nn.init.normal_(module.weight, std=0.02)
            if getattr(module, "bias", None) is not None:
                nn.init.zeros_(module.bias)

    def get_input_embeddings(self):
        return self.transformer["wte"]

    def set_input_embeddings(self, value):
        self.transformer["wte"] = value

    def get_output_embeddings(self):
        return self.lm_head

    def set_output_embeddings(self, value):
        self.lm_head = value

    def prepare_inputs_for_generation(self, input_ids, past_key_values=None, attention_mask=None, use_cache=True, **kwargs):
        if past_key_values is not None:
            input_ids = input_ids[:, past_key_values.get_seq_length():]
        return {"input_ids": input_ids, "attention_mask": attention_mask,
                "past_key_values": past_key_values, "use_cache": use_cache,
                "logits_to_keep": 1}

    def forward(self, input_ids=None, attention_mask=None, labels=None,
                past_key_values=None, use_cache=None, inputs_embeds=None,
                position_ids=None, return_dict=None, output_hidden_states=False,
                output_attentions=False, logits_to_keep=0, **kwargs):
        if output_attentions:
            raise ValueError("Attention weights are not exposed by the hybrid GDN2/GQA model")
        if (input_ids is None) == (inputs_embeds is None):
            raise ValueError("Provide exactly one of input_ids or inputs_embeds")
        use_cache = self.config.use_cache if use_cache is None else use_cache
        return_dict = self.config.return_dict if return_dict is None else return_dict
        if past_key_values is not None and not use_cache:
            raise ValueError("past_key_values requires use_cache=True")
        if past_key_values is not None and not isinstance(past_key_values, GPTX3Cache):
            raise TypeError("GPT-X3 requires its hybrid GPTX3Cache")
        cache = past_key_values
        if use_cache and cache is None:
            cache = GPTX3Cache(self.config.num_hidden_layers)
        x = self.transformer["wte"](input_ids) if inputs_embeds is None else inputs_embeds
        batch, length, _ = x.shape
        past = cache.get_seq_length() if cache is not None else 0
        total = past + length
        if not length or total > self.config.max_position_embeddings:
            raise ValueError(f"Sequence length {total} exceeds context or is empty")
        if attention_mask is None:
            attention_mask = torch.ones(batch, total, device=x.device, dtype=torch.bool)
        else:
            attention_mask = attention_mask.to(device=x.device, dtype=torch.bool)
        if attention_mask.shape != (batch, total):
            raise ValueError("attention_mask must cover the full sequence, including cached tokens")
        if position_ids is None:
            position_ids = (attention_mask.long().cumsum(-1) - 1).clamp_min(0)[:, -length:]
        if self.rope_cos is None or self.rope_cos.device != x.device or self.rope_cos.dtype != torch.float32:
            # TrainWork precomputes RoPE on the CPU before moving to CUDA.
            # Retain that arithmetic: tiny cos/sin differences can be amplified
            # by the trained low-rank gates in FP32 FLA chunk kernels.
            freq = 1.0 / (self.config.rope_theta ** (torch.arange(0, self.config.rotary_dim, 2, device="cpu", dtype=torch.float32) / self.config.rotary_dim))
            angles = torch.outer(torch.arange(self.config.max_position_embeddings, device="cpu", dtype=torch.float32), freq)
            self.rope_cos, self.rope_sin = angles.cos().to(x.device), angles.sin().to(x.device)
        cos, sin = self.rope_cos[position_ids][:, None], self.rope_sin[position_ids][:, None]
        hidden = () if output_hidden_states else None
        for i, block in enumerate(self.transformer["h"]):
            # Explicit experimental runtime graph change. Keep attention blocks,
            # first/last blocks and cache indexing; never change saved weights.
            if getattr(self.config,"opencore_fast_mode",False) and i%4==2 and i<len(self.transformer["h"])-1 and not block.full_attention:
                continue
            if output_hidden_states:
                hidden += (x,)
            x = block(x, cos, sin, attention_mask, cache.layers[i] if cache is not None else None)
        x = self.transformer["ln_f"](x)
        if output_hidden_states:
            hidden += (x,)
        if cache is not None:
            cache.seen_tokens = total
        selected = x if labels is not None or logits_to_keep == 0 else x[:, -logits_to_keep:]
        logits = self.lm_head(selected)
        loss = None
        if labels is not None:
            shifted_labels = labels[:, 1:].to(logits.device).clone()
            shifted_labels.masked_fill_(~attention_mask[:, -length:][:, 1:], -100)
            loss = F.cross_entropy(logits[:, :-1].float().reshape(-1, self.config.vocab_size), shifted_labels.reshape(-1))
        output = CausalLMOutputWithPast(loss=loss, logits=logits, past_key_values=cache, hidden_states=hidden)
        return output if return_dict else output.to_tuple()
