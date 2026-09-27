"""Verify the numerical libraries actually mapped by the Windows process."""
from __future__ import annotations

import ctypes as C
import os
from pathlib import Path

from .q6_identity import file_digest

CPU_LIBRARIES = ('twincore.dll', 'llama.dll', 'ggml.dll', 'ggml-base.dll', 'ggml-cpu.dll')
CUDA_LIBRARIES = ('cublasLt64_13.dll', 'cublas64_13.dll', 'ggml-cuda.dll')
_directory_handles, _libraries = [], []


def mapped_libraries(names):
    if os.name != 'nt':
        raise RuntimeError('The pinned native TwinCore runtime requires Windows')
    kernel = C.WinDLL('kernel32', use_last_error=True)
    kernel.GetModuleHandleW.argtypes, kernel.GetModuleHandleW.restype = [C.c_wchar_p], C.c_void_p
    kernel.GetModuleFileNameW.argtypes = [C.c_void_p, C.c_wchar_p, C.c_uint]
    kernel.GetModuleFileNameW.restype = C.c_uint
    result = {}
    for name in names:
        handle = kernel.GetModuleHandleW(name)
        if handle:
            output = C.create_unicode_buffer(32768)
            if not kernel.GetModuleFileNameW(handle, output, len(output)):
                raise RuntimeError(f'Cannot read the loaded native library path: {name}')
            result[name.lower()] = output.value
    return result


def validate_loaded_libraries(records, loaded, *, required=()):
    expected = {row['path'].lower(): row for row in records}
    loaded = {name.lower(): path for name, path in loaded.items()}
    for name in required:
        if name.lower() not in loaded:
            raise RuntimeError(f'Required native numerical library is not loaded: {name}')
    measured = {}
    for name, location in loaded.items():
        row = expected.get(name)
        path = Path(location).resolve()
        if row is None or not path.is_file() or path.stat().st_size != row['bytes'] or file_digest(path) != row['sha256']:
            raise RuntimeError(f'Native loaded library identity mismatch: {name} at {path}')
        measured[name] = {'path': str(path), 'bytes': row['bytes'], 'sha256': row['sha256']}
    return measured


def load_library(path: Path, runtime: Path):
    path, runtime = Path(path).resolve(), Path(runtime).resolve()
    if not path.is_file():
        raise RuntimeError(f'Native TwinCore library is missing: {path}')
    if hasattr(os, 'add_dll_directory'):
        _directory_handles.extend((os.add_dll_directory(str(runtime)), os.add_dll_directory(str(path.parent))))
    library = C.CDLL(str(path))
    _libraries.append(library)
    return library


def prepare_gpu_libraries(runtime: Path, manifest):
    # Reject a same-named CUDA DLL already imported by another framework before
    # the native model allocates anything. Then load the exact recorded files.
    records = manifest['libraries']
    if not {name.lower() for name in CUDA_LIBRARIES}.issubset({row['path'].lower() for row in records}):
        raise RuntimeError('Native GPU numerical library identities are missing from the receipt')
    validate_loaded_libraries(records, mapped_libraries((*CPU_LIBRARIES, *CUDA_LIBRARIES)))
    for name in CUDA_LIBRARIES:
        load_library(Path(runtime) / name, Path(runtime))
    measured = validate_loaded_libraries(records, mapped_libraries(CUDA_LIBRARIES), required=CUDA_LIBRARIES)
    # This pinned build has GGML_BACKEND_DL=OFF: CUDA is a linked backend, not
    # a plugin exporting ggml_backend_init. Bind its actual registry pointer.
    registry = load_library(Path(runtime) / 'ggml.dll', Path(runtime))
    measured.update(validate_loaded_libraries(records, mapped_libraries(CPU_LIBRARIES), required=('ggml.dll',)))
    cuda = load_library(Path(runtime) / 'ggml-cuda.dll', Path(runtime))
    cuda.ggml_backend_cuda_reg.argtypes, cuda.ggml_backend_cuda_reg.restype = [], C.c_void_p
    expected = cuda.ggml_backend_cuda_reg()
    registry.ggml_backend_reg_by_name.argtypes, registry.ggml_backend_reg_by_name.restype = [C.c_char_p], C.c_void_p
    actual = registry.ggml_backend_reg_by_name(b'CUDA')
    if not expected or actual != expected:
        raise RuntimeError('The pinned CUDA numerical backend registry identity does not match')
    base = load_library(Path(runtime) / 'ggml-base.dll', Path(runtime))
    base.ggml_backend_reg_dev_count.argtypes, base.ggml_backend_reg_dev_count.restype = [C.c_void_p], C.c_size_t
    if base.ggml_backend_reg_dev_count(actual) < 1:
        raise RuntimeError('The pinned CUDA numerical backend has no visible GPU device')
    return measured
