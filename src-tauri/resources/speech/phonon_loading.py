"""Assign the publisher's decoded state without a second dense weight allocation.

The installed reference decoder remains authoritative. FP32 preserves its values;
BF16 explicitly rounds those values to the selected runtime precision.
"""
from contextlib import contextmanager
from threading import RLock

_EXPANSION_LOCK = RLock()


def optimized_five_value(blob, shape, *, trits, original, raw=None):
    """Replace finite FP16 multiplication by {-1,0,+1} with exact bit operations.

    Keep the publisher's trit decoder, byte layout, assertions and raw receipt.
    Nonfinite learned levels use its original multiply to preserve NaN behavior.
    """
    import numpy as np
    o, i = shape
    rb = (i + 4) // 5
    codes = trits(blob[:o * rb], o, i)
    nzmask = codes != 1
    nnz = int(nzmask.sum())
    rbytes = (nnz + 7) // 8
    off = o * rb
    bits = np.unpackbits(np.frombuffer(blob[off:off + rbytes], dtype=np.uint8),
                         bitorder='little')[:nnz].astype(bool)
    off += rbytes
    lo = np.frombuffer(blob[off:off + 2 * o], dtype=np.float16)
    hi = np.frombuffer(blob[off + 2 * o:off + 4 * o], dtype=np.float16)
    assert off + 4 * o == len(blob), (off + 4 * o, len(blob))
    if not (np.isfinite(lo).all() and np.isfinite(hi).all()):
        return original(blob, shape, raw)
    is_hi = np.zeros((o, i), dtype=bool)
    is_hi[nzmask] = bits
    result = np.where(is_hi, hi.view(np.uint16)[:, None], lo.view(np.uint16)[:, None])
    # Multiplication by -1 toggles the FP16 sign; +1 preserves every bit. For
    # +0 * a finite level, retain the level's sign even when the result is zero.
    np.bitwise_xor(result, (codes == 0) * np.uint16(0x8000), out=result)
    np.bitwise_and(result, np.where(nzmask, np.uint16(0xffff), np.uint16(0x8000)), out=result)
    if raw is not None:
        raw.update(sign=codes.astype(np.int8) - 1, is_hi=is_hi, lo=lo.copy(), hi=hi.copy())
    return result.view(np.float16)


@contextmanager
def accelerated_five_value_expansion(reader=None):
    """Use the exact fast decoder only during the unchanged publisher read."""
    if reader is None:
        import fermion_container as reader
    with _EXPANSION_LOCK:
        original = reader._five_value
        def accelerated(blob, shape, raw=None):
            return optimized_five_value(blob, shape, trits=reader._trits, original=original, raw=raw)
        reader._five_value = accelerated
        try:
            yield
        finally:
            reader._five_value = original


def materialize_model(config, state, dtype):
    import torch
    from transformers import ParakeetForTDT
    from transformers.models.parakeet.modeling_parakeet import ParakeetEncoderRelPositionalEncoding

    if dtype not in (torch.float32, torch.bfloat16):
        raise ValueError('Phonon-2 runtime precision must be BF16 or FP32.')
    with torch.device('meta'):
        model = ParakeetForTDT(config)
    # Keep integer BatchNorm counters intact. Replace each floating state in place
    # so BF16 conversion does not retain a second complete floating state dict.
    for name, value in state.items():
        if value.is_floating_point():
            state[name] = value.to(dtype=dtype)
    model.load_state_dict(state, strict=True, assign=True)
    # This derived, nonpersistent buffer is absent from the checkpoint. Rebuild
    # it with Transformers' own initializer before leaving the meta device.
    for module in model.modules():
        if isinstance(module, ParakeetEncoderRelPositionalEncoding) and module.inv_freq.is_meta:
            with torch.device('cpu'):
                module.inv_freq = module.compute_default_relative_positional_parameters(module.config).to(dtype)
    pending = [name for name, value in list(model.named_parameters()) + list(model.named_buffers())
               if value.is_meta]
    if pending:
        raise RuntimeError(f'Phonon-2 runtime has unmaterialized tensors: {pending[:5]}')
    model.eval()
    return model


def load_model(container, base_dir, dtype, progress):
    from reference_transformers import container_state_dict
    from transformers import AutoProcessor, GenerationConfig, ParakeetTDTConfig

    progress('building-model')
    config = ParakeetTDTConfig.from_pretrained(base_dir, local_files_only=True)
    progress('expanding-weights')
    with accelerated_five_value_expansion():
        state, index = container_state_dict(container)
    progress('applying-weights')
    model = materialize_model(config, state, dtype)
    receipt = {'params': sum(p.numel() for p in model.parameters()),
               'container_records': len(index), 'state_dict_keys': len(state)}
    del state, index
    progress('preparing-processor')
    model.generation_config = GenerationConfig.from_pretrained(base_dir, local_files_only=True)
    processor = AutoProcessor.from_pretrained(base_dir, local_files_only=True)
    return model, processor, receipt
