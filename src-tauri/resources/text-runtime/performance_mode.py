"""Managed inference optimizations. Always execute the complete model."""
import os


def configure(model, enabled):
    import torch
    enabled=bool(enabled)
    # Extra CPU threads help CPU inference but add synchronization overhead to
    # GPU decoding. Keep the measured GPU setting while optimizing its kernels.
    cpu_inference=getattr(getattr(model,'device',None),'type','cpu')!='cuda'
    torch.set_num_threads(max(1,min(6,(os.cpu_count() or 4)//2)) if enabled and cpu_inference else 2)
    # TF32 accelerates FP32 operations on supported NVIDIA devices. Restore
    # full precision when reconfiguring a worker back to its normal setting.
    torch.set_float32_matmul_precision('high' if enabled else 'highest')
    torch.backends.cuda.matmul.allow_tf32=enabled
    kind=getattr(model.config,'model_type','')
    attention='native'
    model.config.opencore_fast_mode=enabled
    if kind=='spark2_5':
        # Eager attention's temporary matrices can spill a 4B checkpoint into
        # Windows shared GPU memory with an ordinary long agent prompt.
        # Equivalent fused attention is part of the standard Spark runtime.
        model.config.opencore_sdpa=True
        attention='sdpa'
    elif enabled and kind not in ('gptx3','edgerock') and getattr(model,'_supports_sdpa',False):
        setter=getattr(model,'set_attn_implementation',None)
        if callable(setter):
            try:
                setter('sdpa')
                attention='sdpa'
            except ValueError:
                # Some composite configurations support only their original
                # attention implementation. Keep their full native forward.
                attention='native'
    value={'enabled':enabled,'fullModel':True,'technique':'optimized-runtime',
           'attention':attention,'cpuThreads':torch.get_num_threads()}
    budget=getattr(model,'_opencore_memory_budget',None)
    if budget is not None:value['gpuMemoryLimitBytes']=budget
    model._opencore_acceleration=value
    return value
