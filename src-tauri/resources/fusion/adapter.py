"""Safe, hash-bound native coupling adapters; base checkpoint weights stay separate."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import shutil

from safetensors.torch import load_file, save_file
import torch

from .q6_identity import CHECKPOINTS, SOURCE_COMMIT, canonical, file_digest
from .training import GRADIENT_MODE


def _tensor_map_fingerprint(tensors):
    digest = hashlib.sha256()
    for name in sorted(tensors):
        value = tensors[name].detach().cpu().contiguous()
        digest.update(canonical([name, str(value.dtype), list(value.shape)]))
        digest.update(value.reshape(-1).view(torch.uint8).numpy().tobytes())
    return digest.hexdigest()


def tensor_fingerprint(bridge):
    return _tensor_map_fingerprint(bridge.state_dict())


def make_binding(bridge, checkpoints, native_identity):
    if checkpoints != CHECKPOINTS:
        raise ValueError('TwinCore checkpoint identity must match both complete Q6 files')
    if native_identity.get('source_commit') != SOURCE_COMMIT or not native_identity.get('libraries'):
        raise ValueError('TwinCore native runtime identity is missing')
    alignment = _tensor_map_fingerprint({
        'nanbeige_ids': bridge.alignment.nanbeige_ids, 'k2_ids': bridge.alignment.k2_ids,
    })
    return {'schema': 1, 'checkpoints': json.loads(canonical(checkpoints)),
            'native': json.loads(canonical(native_identity)), 'alignment_sha256': alignment,
            'coupling_sources': {name: file_digest(Path(__file__).with_name(name)) for name in
                ('bridge.py', 'alignment.py', 'native.py', 'native_library.py', 'q6_identity.py',
                 'canonical.py', 'chat_template.py', 'training.py', 'adapter.py', 'qualification.py')},
            'geometry': {'nanbeige_hidden': bridge.nanbeige_hidden, 'k2_hidden': bridge.k2_hidden,
                         'nanbeige_vocab': bridge.alignment.nanbeige_vocab_size,
                         'k2_vocab': bridge.alignment.k2_vocab_size, 'rank': bridge.rank}}


def _validate_training(training, final_fingerprint):
    validation = training.get('validation', {})
    if (not isinstance(training.get('steps'), int) or training['steps'] < 1
            or not isinstance(training.get('tokens'), int) or training['tokens'] < 1
            or training.get('gradient_mode') != GRADIENT_MODE
            or len(training.get('corpus_sha256', '')) != 64
            or len(training.get('initial_bridge_sha256', '')) != 64
            or not isinstance(validation.get('tokens'), int) or validation['tokens'] < 1
            or any(not isinstance(validation.get(key), (float, int)) or not math.isfinite(validation[key])
                   or validation[key] < 0 for key in ('loss', 'baseline_loss'))):
        raise ValueError('Adapter requires completed training and held-out validation evidence')
    if training['initial_bridge_sha256'] == final_fingerprint:
        raise ValueError('An initialized or untrained adapter cannot be saved as trained')


def _validate_binding(bridge, binding):
    if binding.get('schema') != 1 or binding.get('checkpoints') != CHECKPOINTS:
        raise ValueError('TwinCore checkpoint identity mismatch')
    derived = make_binding(bridge, binding['checkpoints'], binding['native'])
    if canonical(derived) != canonical(binding):
        raise ValueError('TwinCore bridge geometry or alignment identity mismatch')


def _pack_optimizer(optimizer):
    if type(optimizer) is not torch.optim.AdamW:
        raise ValueError('Safe adapter resume supports the recorded AdamW optimizer only')
    state = optimizer.state_dict()
    tensors, entries = {}, {}
    for identifier, values in state['state'].items():
        entries[str(identifier)] = {}
        for name, value in values.items():
            if not isinstance(value, torch.Tensor):
                raise ValueError('Safe adapter resume supports tensor optimizer state only')
            key = f'{identifier}.{name}'
            tensors[key] = value.detach().cpu().contiguous().clone()
            entries[str(identifier)][name] = key
    return tensors, {'algorithm': 'AdamW', 'entries': entries, 'param_groups': state['param_groups']}


def save_adapter(destination: Path, bridge, binding, training, *, optimizer=None):
    _validate_binding(bridge, binding)
    fingerprint = tensor_fingerprint(bridge)
    _validate_training(training, fingerprint)
    tensors = {name: value.detach().cpu().contiguous().clone() for name, value in bridge.state_dict().items()}
    if any(value.is_floating_point() and not torch.isfinite(value).all() for value in tensors.values()):
        raise ValueError('Cannot save nonfinite adapter tensors')
    optimizer_tensors, optimizer_structure = {}, None
    if optimizer is not None:
        optimizer_tensors, optimizer_structure = _pack_optimizer(optimizer)
        # Validate exactly the state that a later resume would consume, before
        # making a directory or writing even the bridge file.
        _restore_optimizer({'optimizer': optimizer_structure, 'training': training},
                           optimizer_tensors, optimizer)
    metadata_bytes = canonical({'binding': binding, 'training': training, 'optimizer': optimizer_structure})
    destination = Path(destination)
    parent = destination.parent
    if not parent.is_dir():
        raise ValueError('Adapter output parent must already exist')
    needed = (sum(value.numel() * value.element_size() for value in [*tensors.values(), *optimizer_tensors.values()])
              + len(metadata_bytes) + 1_048_576)  # bounded tensor headers and receipt overhead
    if shutil.disk_usage(parent).free < 200_000_000_000 + needed:
        raise ValueError('Saving this adapter would violate the 200 GB free-space reserve')
    destination.mkdir(exist_ok=False)
    save_file(tensors, str(destination / 'bridge.safetensors'))
    receipt = {'schema': 1, 'binding': binding, 'training': training, 'tensor_fingerprint': fingerprint,
               'files': {'bridge.safetensors': file_digest(destination / 'bridge.safetensors')},
               'scope': 'Frozen native Q6 adapter training; validation loss alone is not coding quality'}
    if optimizer is not None:
        save_file(optimizer_tensors, str(destination / 'optimizer.safetensors'))
        receipt['optimizer'] = optimizer_structure
        receipt['files']['optimizer.safetensors'] = file_digest(destination / 'optimizer.safetensors')
    receipt['receipt_sha256'] = hashlib.sha256(canonical(receipt)).hexdigest()
    temporary = destination / 'receipt.json.tmp'
    temporary.write_bytes(canonical(receipt) + b'\n')
    temporary.replace(destination / 'receipt.json')
    return receipt


def _restore_optimizer(receipt, tensors, optimizer):
    structure = receipt.get('optimizer')
    if (not isinstance(structure, dict) or structure.get('algorithm') != 'AdamW'
            or type(optimizer) is not torch.optim.AdamW):
        raise ValueError('Adapter optimizer resume algorithm mismatch')
    incoming, current = structure['param_groups'], optimizer.param_groups
    if len(incoming) != len(current) or any(len(a['params']) != len(b['params']) for a, b in zip(incoming, current)):
        raise ValueError('Optimizer resume geometry mismatch')
    states, used = {}, set()
    parameters = {identifier: parameter for group, actual in zip(incoming, current)
                  for identifier, parameter in zip(group['params'], actual['params'])}
    identifiers = [identifier for group in incoming for identifier in group['params']]
    if (len(parameters) != len(identifiers) or any(type(identifier) is not int or identifier < 0 for identifier in identifiers)
            or set(structure['entries']) != {str(identifier) for identifier in identifiers}):
        raise ValueError('Optimizer resume parameter identity mismatch')
    required = {}
    for group, actual in zip(incoming, current):
        if (set(group) != set(actual) or not isinstance(group.get('lr'), (int, float))
                or not math.isfinite(group['lr']) or group['lr'] <= 0
                or not isinstance(group.get('eps'), (int, float)) or not math.isfinite(group['eps']) or group['eps'] <= 0
                or not isinstance(group.get('weight_decay'), (int, float))
                or not math.isfinite(group['weight_decay']) or group['weight_decay'] < 0
                or len(group.get('betas', [])) != 2
                or any(not isinstance(beta, (int, float)) or not math.isfinite(beta) or not 0 <= beta < 1 for beta in group['betas'])):
            raise ValueError('Invalid optimizer resume configuration')
        names = {'step', 'exp_avg', 'exp_avg_sq'} | ({'max_exp_avg_sq'} if group['amsgrad'] else set())
        required.update({identifier: names for identifier in group['params']})
    for identifier, values in structure['entries'].items():
        if int(identifier) not in parameters:
            raise ValueError('Optimizer resume parameter identity mismatch')
        states[int(identifier)] = {}
        if set(values) != required[int(identifier)]:
            raise ValueError('Optimizer resume is missing required moment tensors')
        for name, key in values.items():
            value = tensors[key]
            if name not in ('step', 'exp_avg', 'exp_avg_sq', 'max_exp_avg_sq') or not torch.isfinite(value).all():
                raise ValueError('Invalid optimizer resume state')
            if name == 'step':
                if value.numel() != 1 or value.item() != receipt['training']['steps']:
                    raise ValueError('Invalid optimizer resume step')
            elif value.shape != parameters[int(identifier)].shape or value.dtype != parameters[int(identifier)].dtype:
                raise ValueError('Optimizer resume tensor geometry mismatch')
            if key in used:
                raise ValueError('Optimizer resume aliases multiple moment tensors')
            used.add(key)
            states[int(identifier)][name] = value
    if used != set(tensors):
        raise ValueError('Optimizer resume contains unbound tensors')
    return {'state': states, 'param_groups': incoming}


def load_adapter(destination: Path, bridge, expected_binding, *, optimizer=None):
    destination = Path(destination)
    receipt = json.loads((destination / 'receipt.json').read_text(encoding='utf-8'))
    digest = receipt.pop('receipt_sha256', None)
    if receipt.get('schema') != 1 or hashlib.sha256(canonical(receipt)).hexdigest() != digest:
        raise ValueError('Adapter receipt integrity mismatch')
    receipt['receipt_sha256'] = digest
    _validate_binding(bridge, expected_binding)
    if canonical(receipt.get('binding')) != canonical(expected_binding):
        raise ValueError('Adapter runtime/checkpoint/alignment identity mismatch')
    _validate_training(receipt['training'], receipt['tensor_fingerprint'])
    for name, expected in receipt['files'].items():
        if name not in ('bridge.safetensors', 'optimizer.safetensors') or file_digest(destination / name) != expected:
            raise ValueError('Adapter tensor integrity mismatch')
    if 'bridge.safetensors' not in receipt['files']:
        raise ValueError('Adapter weight integrity binding is missing')
    weights = load_file(str(destination / 'bridge.safetensors'), device='cpu')
    existing = bridge.state_dict()
    if set(weights) != set(existing) or any(weights[name].shape != value.shape or weights[name].dtype != value.dtype
                                           for name, value in existing.items()):
        raise ValueError('Adapter tensor geometry identity mismatch')
    if _tensor_map_fingerprint(weights) != receipt['tensor_fingerprint'] or any(
            value.is_floating_point() and not torch.isfinite(value).all() for value in weights.values()):
        raise ValueError('Adapter tensor content integrity mismatch')
    for name in ('alignment.nanbeige_ids', 'alignment.k2_ids'):
        if not torch.equal(weights[name], existing[name].cpu()):
            raise ValueError('Adapter alignment tensor identity mismatch')
    optimizer_state = None
    if optimizer is not None:
        if 'optimizer.safetensors' not in receipt['files']:
            raise ValueError('Optimizer resume integrity binding is missing')
        optimizer_state = _restore_optimizer(receipt, load_file(str(destination / 'optimizer.safetensors')), optimizer)
    # Apply only after every receipt, tensor and resume check has succeeded.
    bridge.load_state_dict(weights, strict=True)
    if optimizer_state is not None:
        optimizer.load_state_dict(optimizer_state)
    return receipt
