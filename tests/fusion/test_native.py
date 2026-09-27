"""Native Q6 boundary tests; no model checkpoints or CUDA are loaded here."""
from __future__ import annotations

import ctypes as C
from functools import lru_cache
import json
import os
from pathlib import Path
import sys

import numpy as np
import pytest
import torch

RESOURCES = Path(__file__).resolve().parents[2] / 'src-tauri/resources'
sys.path.insert(0, str(RESOURCES))


def native_module():
    try:
        from fusion import native
    except ImportError:
        pytest.fail('The full native TwinCore boundary is not implemented')
    return native


@lru_cache(maxsize=1)
def probe():
    native = native_module()
    path = RESOURCES / 'fusion/native/build/Release/twincore-probe.dll'
    assert path.is_file(), 'Build the shared production head projection probe first'
    runtime = RESOURCES / 'doucode/runtime'
    dll = native.load_library(path, runtime)
    dll.tp_create.argtypes = [C.c_int, C.c_int, C.c_int, C.c_int]
    dll.tp_create.restype = C.c_void_p
    dll.tp_destroy.argtypes = [C.c_void_p]
    dll.tp_project.argtypes = [C.c_void_p, C.POINTER(C.c_float), C.POINTER(C.c_float), C.c_int]
    dll.tp_project.restype = C.c_int
    dll.tp_weights.argtypes = [C.c_void_p, C.POINTER(C.c_float)]
    dll.tp_weights.restype = C.c_int
    dll.tp_retain.argtypes = [C.POINTER(C.c_int), C.c_int, C.POINTER(C.c_int), C.c_int, C.c_int]
    dll.tp_retain.restype = C.c_int
    dll.tp_error.restype = C.c_char_p
    return dll


def ptr(array):
    return array.ctypes.data_as(C.POINTER(C.c_float))


class ProjectionProbe:
    def __init__(self, dll, quantized, *, mapped=False, gpu=False):
        self.dll, self.width, self.vocab = dll, 256, 137
        # A 17-row chunk deliberately crosses seven full chunks and a tail.
        create = dll.tp_create
        if mapped:
            create = dll.tp_create_mapped
            create.argtypes = [C.c_int, C.c_int, C.c_int, C.c_int]
            create.restype = C.c_void_p
        elif gpu:
            create = dll.tp_create_gpu
            create.argtypes = [C.c_int, C.c_int, C.c_int, C.c_int]
            create.restype = C.c_void_p
        self.handle = create(self.width, self.vocab, int(quantized), 17)
        assert self.handle, dll.tp_error().decode()

    def project(self, brain, values, transpose=False):
        assert brain == 0
        source = np.ascontiguousarray(values, dtype=np.float32)
        outputs = []
        for row in source:
            output = np.empty(self.width if transpose else self.vocab, np.float32)
            result = self.dll.tp_project(self.handle, ptr(row), ptr(output), int(transpose))
            assert result == 0, self.dll.tp_error().decode()
            outputs.append(output)
        return np.stack(outputs)

    def weights(self):
        result = np.empty((self.vocab, self.width), np.float32)
        assert self.dll.tp_weights(self.handle, ptr(result)) == 0
        return result

    def close(self):
        self.dll.tp_destroy(self.handle)
        self.handle = None


def test_cpu_mapped_head_has_no_device_handle_and_is_never_reported_as_gpu():
    # Real GGUF CPU mmap buffers have a null device. This caught a fatal native
    # assertion when the full Nanbeige checkpoint was first loaded on CPU.
    model = ProjectionProbe(probe(), True, mapped=True)
    try:
        model.dll.tp_buffer_device.argtypes = [C.c_void_p]
        model.dll.tp_buffer_device.restype = C.c_void_p
        model.dll.tp_on_gpu.argtypes = [C.c_void_p]
        model.dll.tp_on_gpu.restype = C.c_int
        assert model.dll.tp_buffer_device(model.handle) is None
        assert model.dll.tp_on_gpu(model.handle) == 0
        x = np.linspace(-0.1, 0.1, 256, dtype=np.float32).reshape(1, 256)
        np.testing.assert_allclose(model.project(0, x), x @ model.weights().T,
                                   rtol=2e-4, atol=2e-4)
    finally:
        model.close()


@pytest.mark.parametrize('quantized', [False, True])
def test_shared_native_head_forward_and_transpose_match_the_actual_weights(quantized):
    # Catches wrong head orientation, wrong chunk offsets and lost tail rows.
    model = ProjectionProbe(probe(), quantized)
    try:
        weight = model.weights()
        x = np.linspace(-0.1, 0.15, 512, dtype=np.float32).reshape(2, 256)
        dy = np.linspace(0.07, -0.03, 274, dtype=np.float32).reshape(2, 137)
        np.testing.assert_allclose(model.project(0, x), x @ weight.T, rtol=2e-4, atol=2e-4)
        np.testing.assert_allclose(model.project(0, dy, True), dy @ weight, rtol=2e-4, atol=2e-4)
    finally:
        model.close()


def test_native_q6_head_backward_trains_inputs_without_dense_head_copies():
    # Catches omitted transpose gradient or a detached native head operation.
    native = native_module()
    model = ProjectionProbe(probe(), True)
    try:
        x = torch.linspace(-0.1, 0.1, 512).reshape(2, 256).requires_grad_()
        head = native.NativeHead(model, 0)
        output = head(x)
        gradient = torch.linspace(-0.02, 0.05, 274).reshape(2, 137)
        output.backward(gradient)
        expected = gradient.numpy() @ model.weights()
        np.testing.assert_allclose(x.grad.numpy(), expected, rtol=2e-4, atol=2e-4)
        assert list(head.parameters()) == []
    finally:
        model.close()


def capture_hidden(model, rows, width=256):
    model.dll.tp_capture_last_hidden.argtypes = [C.c_void_p, C.c_int, C.c_int, C.POINTER(C.c_float)]
    model.dll.tp_capture_last_hidden.restype = C.c_int
    output = np.full(model.width, 123.0, np.float32)
    status = model.dll.tp_capture_last_hidden(model.handle, rows, width, ptr(output))
    return status, output


def test_prefill_microbatch_without_requested_outputs_does_not_read_an_empty_tensor():
    # The real 1K tool prompt exposed result_norm[3072, 0] on the first
    # microbatch. Only the later microbatch contains a requested output row.
    model = ProjectionProbe(probe(), False)
    try:
        status, output = capture_hidden(model, 0)
        assert status == 0
        np.testing.assert_array_equal(output, np.full(256, 123.0, np.float32))
        status, output = capture_hidden(model, model.vocab)
        assert status == 1
        np.testing.assert_array_equal(output, model.weights()[-1])
    finally:
        model.close()


@pytest.mark.parametrize('failure', ['width', 'type'])
def test_empty_output_microbatch_still_rejects_incompatible_hidden_geometry(failure):
    model = ProjectionProbe(probe(), failure == 'type')
    try:
        status, _ = capture_hidden(model, 0, width=255 if failure == 'width' else 256)
        assert status == -1
        assert 'hidden-state geometry' in model.dll.tp_error().decode()
    finally:
        model.close()


@pytest.mark.skipif(os.environ.get('OPENCORE_TEST_NATIVE_CUDA') != '1',
                    reason='Explicit opt-in: small native CUDA head math, not full model qualification')
@pytest.mark.parametrize('quantized', [False, True])
def test_native_cuda_head_chunk_and_transpose_match_exact_frozen_rows(quantized):
    # Concrete remaining risk: CUDA get_rows/view/tail math was only exercised
    # on CPU. This uses 137 x 256 fixture weights, never either real checkpoint.
    from fusion.native_library import prepare_gpu_libraries
    from fusion.q6_preflight import gpu_snapshot
    native = native_module()
    assert gpu_snapshot()['free_bytes'] >= 256 * 1024 * 1024
    identity = native.verify_build(RESOURCES / 'fusion/native/build/Release/twincore.dll',
                                   RESOURCES / 'doucode/runtime')
    prepare_gpu_libraries(RESOURCES / 'doucode/runtime', identity)
    model = ProjectionProbe(probe(), quantized, gpu=True)
    try:
        model.dll.tp_on_gpu.argtypes = [C.c_void_p]
        model.dll.tp_on_gpu.restype = C.c_int
        assert model.dll.tp_on_gpu(model.handle) == 1
        weight = model.weights()
        x = np.linspace(-0.1, 0.15, 512, dtype=np.float32).reshape(2, 256)
        dy = np.linspace(0.07, -0.03, 274, dtype=np.float32).reshape(2, 137)
        np.testing.assert_allclose(model.project(0, x), x @ weight.T, rtol=2e-4, atol=2e-4)
        np.testing.assert_allclose(model.project(0, dy, True), dy @ weight, rtol=2e-4, atol=2e-4)
    finally:
        model.close()


@pytest.mark.parametrize('old,new,recompute,want', [
    ([1, 2, 3], [1, 2, 3, 4], False, 2),
    ([1, 2, 3], [1, 8, 9], False, 1),
    ([1, 2, 3], [1, 2, 3], False, 2),
    ([1, 2, 3], [1, 2], False, 1),
    ([1, 2, 3], [1, 2, 3, 4], True, 0),
    ([], [1], False, 0),
])
def test_prefix_reuse_discards_changed_tokens_and_previous_feedback(old, new, recompute, want):
    # Catches stale biased last tokens being retained after retokenization.
    a, b = (C.c_int * len(old))(*old), (C.c_int * len(new))(*new)
    assert probe().tp_retain(a, len(old), b, len(new), int(recompute)) == want


def test_native_missing_checkpoint_fails_before_allocating_a_model():
    native = native_module()
    api = native.NativeAPI(RESOURCES / 'fusion/native/build/Release/twincore.dll',
                           RESOURCES / 'doucode/runtime')
    with pytest.raises(RuntimeError, match='checkpoint'):
        native.NativeTwinCore(api, 'missing-nanbeige.gguf', 'missing-k2.gguf', gpu_layers=0)


def test_second_tower_step_failure_closes_both_owned_towers():
    # A double is necessary here: a full failing GPU tower cannot share the live
    # benchmark's memory. The contract under test is the Python owner lifecycle.
    native = native_module()

    class FailingAPI:
        def __init__(self):
            self.destroyed = []

        def create(self, *_args):
            return 7

        def geometry(self, handle, brain):
            assert handle == 7 and brain in (0, 1)
            return {'hidden': 4 + brain, 'vocab': 8 + brain, 'capacity': 64}

        def step(self, *_args):
            raise RuntimeError('K2 decoder failed')

        def destroy(self, handle):
            self.destroyed.append(handle)

    api = FailingAPI()
    model = native.NativeTwinCore(api, 'nan', 'k2')
    with pytest.raises(RuntimeError, match='K2 decoder failed'):
        model.step([1], [2])
    assert model.closed
    model.close()
    assert api.destroyed == [7]
    with pytest.raises(RuntimeError, match='closed'):
        model.step([1], [2])


def test_empty_native_library_receipt_cannot_bypass_binary_verification(tmp_path):
    # Catches manifests whose pinned commit disguises missing binary bindings.
    native = native_module()
    dll = tmp_path / 'twincore.dll'
    dll.write_bytes(b'wrong binary')
    (tmp_path / 'build-info.json').write_text(json.dumps({
        'source_commit': native.SOURCE_COMMIT, 'abi': 1, 'libraries': [],
    }))
    with pytest.raises(RuntimeError, match='library identities'):
        native.verify_build(dll, tmp_path)


def test_native_cancellation_is_distinct_from_model_failure_and_clears_both_contexts():
    native = native_module()

    class CancelAPI:
        def __init__(self):
            self.cancellations, self.clears, self.destroyed = [], [], []

        def create(self, *_):
            return 7

        def geometry(self, *_):
            return {'hidden': 4, 'vocab': 8, 'capacity': 64}

        def cancel(self, handle):
            self.cancellations.append(handle)

        def clear(self, handle):
            self.clears.append(handle)

        def step(self, *_):
            raise native.NativeCancelled('Native TwinCore cancelled')

        def destroy(self, handle):
            self.destroyed.append(handle)

    api = CancelAPI()
    model = native.NativeTwinCore(api, 'nan', 'k2')
    model.cancel()
    with pytest.raises(native.NativeCancelled):
        model.step([1], [2])
    assert api.cancellations == [7] and api.clears == [7] and not model.closed
    model.close()
    model.cancel()
    assert api.destroyed == [7] and api.cancellations == [7]
