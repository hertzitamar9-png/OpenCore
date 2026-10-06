"""Assign the publisher's decoded state without a second dense weight allocation.

The installed reference decoder remains authoritative. FP32 preserves its values;
BF16 explicitly rounds those values to the selected runtime precision.
"""


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
