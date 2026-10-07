"""Disposable dense CPU tensors derived from the unchanged installed checkpoint.

This saves repeated container expansion; it never downloads weights, changes
checkpoint precision, or recovers information absent from the container.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

SCHEMA = 1


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def source_identity(container, base_dir, config, dtype):
    import torch
    import transformers
    container, base_dir = Path(container).resolve(), Path(base_dir).resolve()
    sources = [container.parent / 'fermion_container.py', container.parent / 'reference_transformers.py',
               Path(__file__).resolve(), Path(__file__).with_name('phonon_loading.py').resolve()]
    # Processor files are small and include both generation and network config.
    sources.extend(path for path in sorted(base_dir.rglob('*')) if path.is_file() and path.suffix in ('.json', '.txt', '.model'))
    return {'schema': SCHEMA, 'checkpointSha256': digest(container), 'checkpointBytes': container.stat().st_size,
            'sources': {str(path.relative_to(base_dir)) if path.is_relative_to(base_dir) else path.name: digest(path) for path in sources},
            'configSha256': fingerprint(config.to_dict()), 'precision': str(dtype).removeprefix('torch.'),
            'torchVersion': str(torch.__version__), 'transformersVersion': str(transformers.__version__)}


def expected_tensors(config, dtype):
    import torch
    from transformers import ParakeetForTDT
    with torch.device('meta'):
        model = ParakeetForTDT(config)
    return {name: {'shape': list(value.shape), 'dtype': str(dtype if value.is_floating_point() else value.dtype).removeprefix('torch.')}
            for name, value in model.state_dict().items()}


def validate_tensors(state, spec):
    import torch
    if not isinstance(state, dict) or state.keys() != spec.keys():
        raise ValueError('Dense cache tensor keys do not match the selected model configuration')
    for name, wanted in spec.items():
        value = state[name]
        if not isinstance(value, torch.Tensor) or value.device.type != 'cpu' or value.layout != torch.strided \
                or list(value.shape) != wanted['shape'] or str(value.dtype).removeprefix('torch.') != wanted['dtype']:
            raise ValueError(f'Dense cache tensor shape, dtype, or device mismatch: {name}')


class DenseCache:
    def __init__(self, root, precision, identity, spec):
        if precision not in ('bf16', 'fp32'):
            raise ValueError('Dense cache precision must be bf16 or fp32')
        self.root, self.identity, self.spec = Path(root), identity, spec
        self.weights = self.root / f'expanded-{precision}.pt'
        self.manifest = self.root / f'expanded-{precision}.json'
        self.reason = 'No prepared dense runtime cache'

    def read(self):
        import torch
        try:
            if self.weights.is_symlink() or self.manifest.is_symlink():
                raise ValueError('Dense cache paths must be ordinary files')
            if not self.weights.is_file() or not self.manifest.is_file():
                return None
            if self.manifest.stat().st_size > 4 * 1024 * 1024:
                raise ValueError('Dense cache manifest is oversized')
            manifest = json.loads(self.manifest.read_text(encoding='utf-8'))
            if manifest.get('schema') != SCHEMA or manifest.get('identity') != self.identity or manifest.get('tensors') != self.spec:
                raise ValueError('Checkpoint, source, configuration or selected precision changed')
            if manifest.get('weightsBytes') != self.weights.stat().st_size or manifest.get('weightsSha256') != digest(self.weights):
                raise ValueError('Dense cache file checksum does not match')
            state = torch.load(self.weights, map_location='cpu', weights_only=True, mmap=True)
            validate_tensors(state, self.spec)
            self.reason = 'Verified dense CPU cache'
            return state, manifest.get('receipt', {})
        except Exception as error:
            self.reason = f'Cache ignored: {error}'
            return None

    def write(self, state, receipt):
        import torch
        validate_tensors(state, self.spec)
        self.root.mkdir(parents=True, exist_ok=True)
        if self.weights.is_symlink() or self.manifest.is_symlink():
            raise ValueError('Dense cache paths must be ordinary files')
        tensor_bytes = sum(value.numel() * value.element_size() for value in state.values())
        if shutil.disk_usage(self.root).free < tensor_bytes + 64 * 1024 * 1024:
            raise OSError('Insufficient disk space for the optional derived dense runtime cache')
        weights_tmp = self.root / f'.expanded-{uuid.uuid4().hex}.pt.tmp'
        manifest_tmp = self.root / f'.expanded-{uuid.uuid4().hex}.json.tmp'
        try:
            with weights_tmp.open('xb') as destination:
                torch.save(state, destination)
                destination.flush()
                os.fsync(destination.fileno())
            manifest = {'schema': SCHEMA, 'identity': self.identity, 'identitySha256': fingerprint(self.identity),
                        'tensors': self.spec, 'weightsBytes': weights_tmp.stat().st_size,
                        'weightsSha256': digest(weights_tmp), 'receipt': receipt}
            with manifest_tmp.open('x', encoding='utf-8') as destination:
                json.dump(manifest, destination, separators=(',', ':'), sort_keys=True)
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(weights_tmp, self.weights)
            # A crash between these replacements leaves a checksum mismatch and
            # safely triggers authoritative expansion on the next startup.
            os.replace(manifest_tmp, self.manifest)
        finally:
            weights_tmp.unlink(missing_ok=True)
            manifest_tmp.unlink(missing_ok=True)

    def details(self, hit):
        return {'hit': hit, 'bytes': self.weights.stat().st_size if self.weights.is_file() else 0,
                'path': str(self.root), 'identitySha256': fingerprint(self.identity), 'detail': self.reason,
                'derivedFromInstalledCheckpoint': True}
