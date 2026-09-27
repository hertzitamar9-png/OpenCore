"""A GPU resource probe qualifies only its measured execution configuration."""
from .q6_identity import canonical


def execution_configuration(*, context, rank, seed, recompute):
    if not isinstance(context, int) or isinstance(context, bool) or not 32 <= context <= 8192:
        raise ValueError('TwinCore context must remain within the experimental 32–8192 token bounds')
    if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
        raise ValueError('Bridge rank must be a positive integer')
    return {'context': context, 'rank': rank, 'seed': seed,
            'memory_mode': 'recompute' if recompute else 'kv',
            'native_threads': 2, 'bridge_cpu_threads': 2, 'gpu_layers_requested': 99}


def validate_qualification(report, configuration, gpu_uuid, *, binding=None):
    if (report.get('schema') != 2 or report.get('status') != 'full_q6_resource_probe_passed'
            or report.get('gpu_qualified') is not True or report.get('models_loaded') is not True):
        raise ValueError('Full Q6 qualification requires an actual complete GPU probe')
    if report.get('models_released') is not True:
        raise ValueError('Full Q6 qualification requires confirmed native model release')
    if canonical(report.get('configuration')) != canonical(configuration):
        raise ValueError('Full Q6 qualification execution configuration changed')
    if report.get('gpu_before', {}).get('uuid') != gpu_uuid:
        raise ValueError('Full Q6 qualification GPU identity changed')
    probe = report.get('probe', {})
    placement = report.get('placement', [])
    if (probe.get('finite_bridge_gradients') is not True or probe.get('complete_target') is not True
            or not isinstance(probe.get('tokens'), int) or probe['tokens'] < 1
            or len(placement) != 2 or any(not row.get('head_on_gpu')
                or row.get('physical_matrix_layers', 0) < 1
                or row.get('physical_matrix_layers') != row.get('gpu_matrix_layers') for row in placement)):
        raise ValueError('Full Q6 qualification did not measure both complete GPU towers and gradients')
    if binding is not None and canonical(report.get('binding')) != canonical(binding):
        raise ValueError('Full Q6 qualification model/runtime identity changed')
