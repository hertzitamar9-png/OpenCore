"""Keep large full-vocabulary heads in RAM and multiply them in CUDA chunks."""

from __future__ import annotations

import torch
from torch import nn
from torch.nn import functional as F


class ChunkedOutputHead(nn.Module):
    """Exact dense head multiplication with bounded, temporary CUDA weights."""

    def __init__(self, weight: torch.Tensor, bias: torch.Tensor | None,
                 compute_device: torch.device, chunk_rows: int = 16_384) -> None:
        super().__init__()
        if weight.ndim != 2 or weight.device.type != "cpu":
            raise ValueError("Chunked output weights must be a CPU [vocab, hidden] matrix")
        if chunk_rows < 1:
            raise ValueError("chunk_rows must be positive")
        self.register_buffer("weight", weight.contiguous(), persistent=False)
        self.register_buffer("bias", None if bias is None else bias.contiguous(), persistent=False)
        self.compute_device = compute_device
        self.chunk_rows = chunk_rows

    def forward(self, hidden: torch.Tensor) -> torch.Tensor:
        if hidden.shape[-1] != self.weight.shape[-1]:
            raise ValueError("Hidden width does not match the output head")
        input_gpu = hidden.to(device=self.compute_device, dtype=self.weight.dtype)
        outputs = []
        for start in range(0, self.weight.shape[0], self.chunk_rows):
            stop = min(start + self.chunk_rows, self.weight.shape[0])
            weight = self.weight[start:stop].to(self.compute_device)
            bias = None if self.bias is None else self.bias[start:stop].to(self.compute_device)
            outputs.append(F.linear(input_gpu, weight, bias).to("cpu"))
            del weight, bias
        return torch.cat(outputs, dim=-1)


def offload_output_head(model: nn.Module, *, chunk_rows: int = 16_384) -> int:
    """Dequantize one vocab head to BF16 RAM; return released CUDA bytes."""
    head = model.get_output_embeddings()
    old_weight = head.weight
    if old_weight.device.type == "cpu":
        return 0
    if old_weight.device.type != "cuda":
        raise RuntimeError(f"Output head is on unsupported device {old_weight.device}")

    quant_state = getattr(old_weight, "quant_state", None)
    if quant_state is not None:
        from bitsandbytes.functional import dequantize_4bit

        dense = dequantize_4bit(old_weight.data, quant_state=quant_state)
    else:
        dense = old_weight.detach()
    dense_cpu = dense.to(device="cpu", dtype=torch.bfloat16).contiguous()
    old_bias = getattr(head, "bias", None)
    bias_cpu = None if old_bias is None else old_bias.detach().to(device="cpu", dtype=torch.bfloat16).contiguous()
    released = old_weight.numel() * old_weight.element_size()
    replacement = ChunkedOutputHead(
        dense_cpu, bias_cpu, torch.device("cuda", torch.cuda.current_device()), chunk_rows,
    )
    model.set_output_embeddings(replacement)
    del old_weight, dense, dense_cpu, old_bias, bias_cpu, head
    torch.cuda.empty_cache()
    return released
