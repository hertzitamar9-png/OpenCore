"""Frozen full Q6 decoders and differentiable native heads; no model fallbacks."""
from __future__ import annotations

import ctypes as C
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import threading

import numpy as np
import torch
from torch import nn

from .q6_identity import SOURCE_COMMIT
from .native_library import (CPU_LIBRARIES, load_library, mapped_libraries,
                             prepare_gpu_libraries, validate_loaded_libraries)


class NativeCancelled(RuntimeError):
    """Cooperative cancellation, distinct from a damaged model or failed tower."""


def _digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def verify_build(dll: Path, runtime: Path):
    manifest = json.loads(dll.with_name('build-info.json').read_text(encoding='utf-8'))
    if manifest.get('source_commit') != SOURCE_COMMIT or manifest.get('abi') != 1:
        raise RuntimeError('Native TwinCore source or ABI does not match the pinned runtime')
    records = manifest.get('libraries', [])
    identities = {(record.get('scope'), record.get('path')) for record in records}
    required = {('native', 'twincore.dll')} | {('runtime', name) for name in
        ('llama.dll', 'ggml.dll', 'ggml-base.dll', 'ggml-cpu.dll')}
    if len(identities) != len(records) or not required.issubset(identities):
        raise RuntimeError('Native TwinCore receipt is missing unique required library identities')
    for record in records:
        if record['scope'] not in ('native', 'runtime'):
            raise RuntimeError('Invalid native library manifest scope')
        base = dll.parent if record['scope'] == 'native' else runtime
        name = record['path']
        if Path(name).name != name:
            raise RuntimeError('Invalid native library manifest path')
        path = base / name
        if path.stat().st_size != record['bytes'] or _digest(path) != record['sha256']:
            raise RuntimeError(f'Native TwinCore library identity mismatch: {name}')
    return manifest


def _float_ptr(array):
    return array.ctypes.data_as(C.POINTER(C.c_float))


def _ids(values):
    result = np.asarray(values)
    if (result.ndim != 1 or not len(result) or result.dtype.kind not in 'iu'
            or np.any(result < 0) or np.any(result > np.iinfo(np.int32).max)):
        raise ValueError('Native prefixes must be nonempty integer token arrays')
    return np.ascontiguousarray(result, dtype=np.int32)


class NativeAPI:
    def __init__(self, dll: Path, runtime: Path):
        self.runtime = Path(runtime).resolve()
        self.identity = verify_build(Path(dll).resolve(), Path(runtime).resolve())
        validate_loaded_libraries(self.identity['libraries'], mapped_libraries(CPU_LIBRARIES))
        self.dll = load_library(dll, runtime)
        self.loaded_libraries = validate_loaded_libraries(self.identity['libraries'],
            mapped_libraries(CPU_LIBRARIES), required=CPU_LIBRARIES)
        p, f, i = C.c_void_p, C.POINTER(C.c_float), C.POINTER(C.c_int)
        signatures = {
            'tc_error': ([], C.c_char_p), 'tc_abi': ([], C.c_int),
            'tc_create': ([C.c_char_p, C.c_char_p, C.c_int, C.c_int, C.c_int], p),
            'tc_destroy': ([p], None), 'tc_geometry': ([p, C.c_int, C.c_int], C.c_int),
            'tc_parameters': ([p, C.c_int], C.c_uint64), 'tc_placement': ([p, C.c_int, C.c_int], C.c_int),
            'tc_step': ([p, i, C.c_int, i, C.c_int, f, C.c_int, f, C.c_int], C.c_int),
            'tc_read': ([p, C.c_int, f, C.c_int, f, C.c_int], C.c_int),
            'tc_project': ([p, C.c_int, f, C.c_int, f, C.c_int, C.c_int], C.c_int),
            'tc_clear': ([p], C.c_int), 'tc_cancel': ([p], C.c_int),
            'tc_tokenize': ([p, C.c_int, C.c_char_p, C.c_int, C.c_int, i, C.c_int], C.c_int),
            'tc_piece': ([p, C.c_int, C.c_int, p, C.c_int], C.c_int),
            'tc_token_flags': ([p, C.c_int, C.c_int], C.c_int),
            'tc_format': ([p, C.c_int, C.POINTER(C.c_char_p), C.POINTER(C.c_char_p), C.c_int, p, C.c_int], C.c_int),
            'tc_chat_data': ([p, C.c_int, C.c_int, p, C.c_int], C.c_int),
        }
        for name, (arguments, returns) in signatures.items():
            function = getattr(self.dll, name)
            function.argtypes, function.restype = arguments, returns
        if self.dll.tc_abi() != 1:
            raise RuntimeError('Unsupported native TwinCore ABI')

    def _checked(self, value):
        if value == -2:
            raise NativeCancelled((self.dll.tc_error() or b'Native TwinCore cancelled').decode('utf-8'))
        if value is None or value < 0:
            raise RuntimeError((self.dll.tc_error() or b'Native TwinCore failure').decode('utf-8'))
        return value

    def create(self, nanbeige, k2, context, gpu_layers, recompute):
        paths = [str(path).encode('utf-8') for path in (nanbeige, k2)]
        if any(b'\0' in path for path in paths):
            raise ValueError('Checkpoint paths cannot contain NUL bytes')
        if gpu_layers > 0:
            self.loaded_libraries.update(prepare_gpu_libraries(self.runtime, self.identity))
        handle = self.dll.tc_create(*paths, context, gpu_layers, int(recompute))
        if not handle:
            self._checked(None)
        return handle

    def destroy(self, handle):
        self.dll.tc_destroy(handle)

    def geometry(self, handle, brain):
        return {name: self._checked(self.dll.tc_geometry(handle, brain, field))
                for field, name in enumerate(('hidden', 'vocab', 'capacity'))}

    def statistics(self, handle, brain):
        parameters = self.dll.tc_parameters(handle, brain)
        if not parameters:
            self._checked(None)
        return {'parameters': parameters, **{
            name: self._checked(self.dll.tc_placement(handle, brain, field))
            for field, name in enumerate(('physical_matrix_layers', 'gpu_matrix_layers', 'head_on_gpu', 'logical_decoder_layers'))}}

    def step(self, handle, n_ids, k_ids, n_feedback, k_feedback):
        n, k = _ids(n_ids), _ids(k_ids)
        n_bias = np.ascontiguousarray([] if n_feedback is None else n_feedback, dtype=np.float32).reshape(-1)
        k_bias = np.ascontiguousarray([] if k_feedback is None else k_feedback, dtype=np.float32).reshape(-1)
        self._checked(self.dll.tc_step(handle, n.ctypes.data_as(C.POINTER(C.c_int)), len(n),
            k.ctypes.data_as(C.POINTER(C.c_int)), len(k), _float_ptr(n_bias), len(n_bias),
            _float_ptr(k_bias), len(k_bias)))

    def read(self, handle, brain, geometry):
        hidden, logits = np.empty(geometry['hidden'], np.float32), np.empty(geometry['vocab'], np.float32)
        self._checked(self.dll.tc_read(handle, brain, _float_ptr(hidden), len(hidden), _float_ptr(logits), len(logits)))
        return torch.from_numpy(hidden).unsqueeze(0), torch.from_numpy(logits).unsqueeze(0)

    def project(self, handle, brain, values, geometry, transpose):
        array = np.ascontiguousarray(values, dtype=np.float32)
        width = geometry['vocab'] if transpose else geometry['hidden']
        if array.ndim != 2 or array.shape[1] != width or not len(array) or not np.isfinite(array).all():
            raise ValueError('Native head requires finite batched vectors with the correct width')
        output = np.empty((len(array), geometry['hidden'] if transpose else geometry['vocab']), np.float32)
        for row, result in zip(array, output):
            self._checked(self.dll.tc_project(handle, brain, _float_ptr(row), len(row),
                                              _float_ptr(result), len(result), int(transpose)))
        return output

    def clear(self, handle):
        self._checked(self.dll.tc_clear(handle))

    def cancel(self, handle):
        self._checked(self.dll.tc_cancel(handle))

    def tokenize(self, handle, brain, text, special=True):
        source = text.encode('utf-8')
        count = self._checked(self.dll.tc_tokenize(handle, brain, source, len(source), int(special), None, 0))
        output = np.empty(count, np.int32)
        written = self._checked(self.dll.tc_tokenize(handle, brain, source, len(source), int(special),
                                                    output.ctypes.data_as(C.POINTER(C.c_int)), count))
        if written != count:
            raise RuntimeError('Native tokenizer changed its required capacity')
        return output

    def piece(self, handle, brain, token):
        count = self._checked(self.dll.tc_piece(handle, brain, token, None, 0))
        output = C.create_string_buffer(count + 1)
        written = self._checked(self.dll.tc_piece(handle, brain, token, output, count))
        if written != count:
            raise RuntimeError('Native token piece changed its required capacity')
        return output.raw[:count]

    def token_flags(self, handle, brain, token):
        return self._checked(self.dll.tc_token_flags(handle, brain, token))

    def format(self, handle, brain, messages):
        from .chat_template import render_chat_template
        values = []
        for field in range(3):
            count = self._checked(self.dll.tc_chat_data(handle, brain, field, None, 0))
            output = C.create_string_buffer(count + 1)
            written = self._checked(self.dll.tc_chat_data(handle, brain, field, output, count))
            if written != count:
                raise RuntimeError('Native chat metadata changed its required capacity')
            values.append(output.raw[:count].decode('utf-8'))
        # Complete-answer supervision and this experimental single stream use
        # both checkpoints' explicit no-thinking template option. This avoids
        # labeling one tower's implicit thinking prefix as visible answer text.
        return render_chat_template(values[0], messages, bos_token=values[1], eos_token=values[2])


@dataclass
class NativeFeatures:
    nanbeige_hidden: torch.Tensor
    k2_hidden: torch.Tensor
    nanbeige_logits: torch.Tensor
    k2_logits: torch.Tensor


class NativeTwinCore:
    def __init__(self, api, nanbeige, k2, *, context=1024, gpu_layers=99, recompute=False):
        self.api, self.handle, self.lock = api, None, threading.RLock()
        self._cancel_lock = threading.RLock()
        self._surfaces = None
        self.handle = api.create(nanbeige, k2, context, gpu_layers, recompute)
        try:
            self.geometry = [api.geometry(self.handle, brain) for brain in (0, 1)]
        except Exception:
            self.close()
            raise

    @property
    def closed(self):
        return self.handle is None

    def close(self):
        with self.lock, self._cancel_lock:
            handle, self.handle = self.handle, None
            self._surfaces = None
            if handle is not None:
                self.api.destroy(handle)

    def cancel(self):
        # A decoder call owns self.lock. This separate short lock protects the
        # handle from destruction while allowing an atomic cancellation request.
        with self._cancel_lock:
            if self.handle is not None:
                self.api.cancel(self.handle)

    def _call(self, name, *arguments):
        with self.lock:
            if self.closed:
                raise RuntimeError('Native TwinCore is closed')
            try:
                return getattr(self.api, name)(self.handle, *arguments)
            except NativeCancelled:
                try:
                    self.api.clear(self.handle)
                except Exception:
                    self.close()
                    raise
                raise
            except Exception:
                # A partially successful step cannot become a one-tower model.
                self.close()
                raise

    def step(self, n_ids, k_ids, n_feedback=None, k_feedback=None):
        with self.lock:
            self._call('step', n_ids, k_ids, n_feedback, k_feedback)
            n_hidden, n_logits = self._call('read', 0, self.geometry[0])
            k_hidden, k_logits = self._call('read', 1, self.geometry[1])
            return NativeFeatures(n_hidden, k_hidden, n_logits, k_logits)

    def project(self, brain, values, transpose=False):
        return self._call('project', brain, values, self.geometry[brain], transpose)

    def clear(self):
        self._call('clear')

    def statistics(self, brain):
        return self._call('statistics', brain)

    def surfaces(self):
        from .canonical import inspect_surfaces
        with self.lock:
            if self.closed:
                raise RuntimeError('Native TwinCore is closed')
            if self._surfaces is None:
                self._surfaces = inspect_surfaces(self)
            return self._surfaces

    def tokenize(self, brain, text, special=True):
        return self._call('tokenize', brain, text, special)

    def piece(self, brain, token):
        return self._call('piece', brain, token)

    def token_flags(self, brain, token):
        return self._call('token_flags', brain, token)

    def format(self, brain, messages):
        return self._call('format', brain, messages)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def __del__(self):
        if getattr(self, 'handle', None) is not None:
            self.close()


class _HeadProjection(torch.autograd.Function):
    @staticmethod
    def forward(ctx, vectors, owner, brain):
        if vectors.device.type != 'cpu' or vectors.ndim != 2:
            raise ValueError('The Q6 native bridge uses batched CPU vectors')
        ctx.owner, ctx.brain, ctx.dtype = owner, brain, vectors.dtype
        result = owner.project(brain, vectors.detach().float().numpy())
        return torch.from_numpy(result)

    @staticmethod
    def backward(ctx, gradient):
        result = ctx.owner.project(ctx.brain, gradient.detach().float().contiguous().numpy(), True)
        return torch.from_numpy(result).to(ctx.dtype), None, None


class NativeHead(nn.Module):
    """The native linear head has no copied parameters; its input gradient is exact."""
    def __init__(self, owner, brain):
        super().__init__()
        self.owner, self.brain = owner, brain

    def forward(self, vectors):
        return _HeadProjection.apply(vectors, self.owner, self.brain)
