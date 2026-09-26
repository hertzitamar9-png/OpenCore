"""Lower-bound resource accounting for pinned Nanbeige + K2 checkpoints."""

# Nanbeige shard sizes from its pinned HF revision; K2 is from its local manifest.
NANBEIGE_BF16_CHECKPOINT_BYTES = 8_339_624_720
K2_BF16_CHECKPOINT_BYTES = 10_116_547_824
BF16_CHECKPOINT_BYTES = NANBEIGE_BF16_CHECKPOINT_BYTES + K2_BF16_CHECKPOINT_BYTES

# Nanbeige: 22 physical layers, 2 recurrent passes, 8 KV heads, 128 dimensions.
# K2: 36 layers, 8 KV heads, 128 dimensions. Both use BF16 K and V tensors.
NANBEIGE_PHYSICAL_LAYERS = 22
NANBEIGE_NUM_LOOPS = 2
NANBEIGE_KV_HEADS = 8
NANBEIGE_HEAD_DIM = 128
K2_ATTENTION_LAYERS = 36
K2_KV_HEADS = 8
K2_HEAD_DIM = 128


def estimate_bf16_kv_bytes(tokens: int) -> int:
    """Estimate KV payload, including Nanbeige's separate loop cache slots."""
    if tokens < 0:
        raise ValueError("tokens must not be negative")
    nanbeige = (
        NANBEIGE_PHYSICAL_LAYERS * NANBEIGE_NUM_LOOPS
        * NANBEIGE_KV_HEADS * NANBEIGE_HEAD_DIM
    )
    k2 = K2_ATTENTION_LAYERS * K2_KV_HEADS * K2_HEAD_DIM
    return tokens * (nanbeige + k2) * 2 * 2  # key + value, BF16 bytes


def require_device_budget(
    available_bytes: int,
    resident_weight_bytes: int,
    kv_bytes: int,
    reserve_bytes: int,
) -> int:
    """Reject a load plan that cannot fit its known resident payloads."""
    values = (available_bytes, resident_weight_bytes, kv_bytes, reserve_bytes)
    if any(value < 0 for value in values):
        raise ValueError("budget values must not be negative")
    needed = resident_weight_bytes + kv_bytes + reserve_bytes
    if needed > available_bytes:
        raise ValueError(
            f"Known resident payload {needed:,} bytes exceeds device budget "
            f"{available_bytes:,} bytes; quantize or page weights/KV first"
        )
    return available_bytes - needed
