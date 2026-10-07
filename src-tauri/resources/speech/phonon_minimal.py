"""Torch-free Phonon Original adapter using the pinned publisher's packed kernels.

The NumPy audio frontend and encoder binding are adapted from fermion-research
0.2.10 (Fermion Research, Apache-2.0):
https://github.com/fermionresearch/phonon
The unchanged publisher wheel supplies and hash-checks the native binaries,
container reader, packed matrices, decoder and long-audio segmentation.
"""
import ctypes
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import numpy as np


def log_mel(audio, filters):
    """Parakeet's 16 kHz frontend: 512 FFT, 400 Hann, 160 hop, 128 Slaney mel."""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim != 1 or not len(audio) or not np.isfinite(audio).all():
        raise ValueError('Speech audio must be a nonempty, finite mono waveform.')
    emphasized = np.concatenate((audio[:1], audio[1:] - np.float32(0.97) * audio[:-1]))
    frames = np.lib.stride_tricks.sliding_window_view(np.pad(emphasized, (256, 256)), 512)[::160]
    window = np.pad(np.hanning(400).astype(np.float32), (56, 56))
    spectrum = np.fft.rfft(frames * window, axis=-1).astype(np.complex64)
    # Keep the publisher's sqrt/square order, including float32 rounding.
    power = np.sqrt(spectrum.real ** 2 + spectrum.imag ** 2) ** 2
    mel = np.log(np.asarray(filters, dtype=np.float32) @ power.T + np.float32(2.0 ** -24)).T
    centered = mel - mel.mean(axis=0, keepdims=True)
    variance = (centered ** 2).sum(axis=0) / max(1, len(mel) - 1)
    return np.ascontiguousarray(centered / (np.sqrt(variance) + np.float32(1e-5)), dtype=np.float32)


def numpy_encoder(pk, lib, tensors, packed, cfg):
    """The publisher's CEncoder.from_tensors binding without importing Torch."""
    if not hasattr(lib, 'phonon2_enc_abi_version') or lib.phonon2_enc_abi_version() != 1:
        raise RuntimeError('Phonon Original requires the publisher C encoder ABI 1.')
    encoder = pk.CEncoder.__new__(pk.CEncoder)
    encoder.lib, encoder._keep = lib, []
    width, layers = cfg['hidden_size'], cfg['num_hidden_layers']
    encoder.d = width

    def pointer(value):
        array = np.ascontiguousarray(np.asarray(value).reshape(-1), dtype=np.float32)
        encoder._keep.append(array)
        return array.ctypes.data

    encoder.h = lib.phonon2_enc_create(layers, width, cfg['intermediate_size'], cfg['num_attention_heads'],
                                      cfg['conv_kernel_size'], cfg['num_mel_bins'], cfg['subsampling_conv_channels'])
    if not encoder.h:
        raise RuntimeError('Phonon Original C encoder allocation failed.')
    inv_freq = np.float32(1) / np.power(np.float32(10000), np.arange(0, width, 2, dtype=np.float32) / width)
    names = ('layers.0.weight', 'layers.0.bias', 'layers.2.weight', 'layers.2.bias', 'layers.3.weight', 'layers.3.bias',
             'layers.5.weight', 'layers.5.bias', 'layers.6.weight', 'layers.6.bias', 'linear.weight', 'linear.bias')
    if lib.phonon2_enc_set_subsampling(encoder.h, *[pointer(tensors['encoder.subsampling.' + name]) for name in names], pointer(inv_freq)) != 0:
        raise RuntimeError('Phonon Original C subsampling initialization failed.')
    for layer in range(layers):
        prefix = f'encoder.layers.{layer}.'
        matrices = (ctypes.c_void_p * len(pk.CEncoder._MATS))(*[packed[prefix + name].h for name in pk.CEncoder._MATS])
        vectors = (ctypes.c_void_p * len(pk.CEncoder._VECS))(*[pointer(tensors[prefix + name]) for name in pk.CEncoder._VECS])
        if lib.phonon2_enc_set_layer(encoder.h, layer, matrices, vectors) != 0:
            raise RuntimeError(f'Phonon Original C encoder layer {layer} initialization failed.')
    encoder._keep.clear()  # C owns its copied vectors; packed handles stay on the speech object.
    return encoder


def load(directory, progress=lambda _stage: None):
    import psutil
    from fermion._speech import engine_phonon2_cpu as engine
    from fermion._speech import _engine_phonon2_cpu as pk
    from fermion._speech import _cpu_features

    class MinimalSpeech(engine.Phonon2CpuSpeechModel):
        def _decode_single(self, audio, repetition_penalty):
            features = log_mel(audio, self._melf)
            encoded = self._cenc.forward(features)
            projected = np.ascontiguousarray(encoded @ self._proj_w.T + self._proj_b, dtype=np.float32)
            bias = self._hotword_automaton(self._vocab) if self.hotwords else None
            ids, frames, durations = self._ctdt.decode_timed(projected, bias=bias)
            return self._finish(ids, frames, durations, len(encoded))

        def describe(self):
            return {**super().describe(), 'frontend': 'numpy', 'torchImported': 'torch' in sys.modules,
                    'note': 'Packed publisher CPU kernels with NumPy audio frontend. No dense fallback or CUDA runtime.'}

    started = time.monotonic()
    directory = Path(directory)
    physical = psutil.cpu_count(logical=False) or os.cpu_count() or 1
    threads = max(1, min(16, physical, os.cpu_count() or 1))
    lib = pk.load_library()
    forced = pk.forced_kernel()
    if forced:
        lib.phonon2_cpu_set_kernel(int(forced))
    lib.phonon2_cpu_set_threads(threads)
    if not hasattr(lib, 'phonon2_cpu_create_onedot_wire'):
        raise RuntimeError('Phonon Original needs the pinned direct packed loader. Choose BF16 or FP32 on unsupported CPUs.')
    tensors, raw, packed, count = engine._read_container(directory / engine.CONTAINER, packed_lib=lib, wire=True, as_numpy=True)
    expected = {f'encoder.layers.{layer}.{name}' for layer in range(engine.HF_CONFIG['encoder_config']['num_hidden_layers']) for name in pk.CEncoder._MATS}
    if not expected.issubset(packed) or any(getattr(matrix, 'pa', None) is not None for matrix in packed.values()):
        raise RuntimeError('Phonon Original could not load all direct packed matrices; refusing a dense fallback.')
    for matrix in packed.values():
        matrix.release_planes()
    progress('building-speech-frontend')
    encoder = numpy_encoder(pk, lib, tensors, packed, engine.HF_CONFIG['encoder_config'])
    cfg = SimpleNamespace(durations=engine.HF_CONFIG['durations'], blank_token_id=engine.HF_CONFIG['blank_token_id'],
                          vocab_size=engine.HF_CONFIG['vocab_size'], max_symbols_per_step=engine.HF_CONFIG['max_symbols_per_step'])
    biases = {name: value for name, value in tensors.items() if name.startswith(('decoder.', 'joint.')) and 'bias' in name}
    decoder = pk.CTdtDecoder(SimpleNamespace(config=cfg), raw, biases, cfg.vocab_size, threads)
    if not decoder.can_bias:
        raise RuntimeError('Phonon Original requires the pinned native TDT decoder ABI 3.')
    kernel = int(lib.phonon2_cpu_kernel_in_use())
    speech = MinimalSpeech(None, path=directory, profile='five-value', backend='phonon2-five-value', load_seconds=0,
                           decode={'five_value_modules': count, 'threads': threads, 'model_impl': 'C kernels + NumPy',
                                   'packed': {'modules': len(packed), 'kernel_id': kernel, 'kernel': _cpu_features.KERNEL_NAMES.get(kernel, '?'),
                                              'tier': pk._plan()['tier'], 'onedot': True, 'plane_cache': 'direct',
                                              'library': pk.default_library().name}})
    speech._cenc, speech._ctdt, speech._packed = encoder, decoder, packed
    speech._proj_w, speech._proj_b = tensors['encoder_projector.weight'], tensors['encoder_projector.bias']
    config = json.loads((directory / engine.CONFIG).read_text())
    speech._vocab = list(config.get('vocabulary') or config['joint']['vocabulary'])
    speech._melf = engine.mel_filters()
    tensors.clear()
    raw.clear()
    engine._trim_heap()
    speech.load_seconds = time.monotonic() - started
    if 'torch' in sys.modules:
        raise RuntimeError('Phonon Original unexpectedly imported Torch; refusing the heavy runtime path.')
    return speech
