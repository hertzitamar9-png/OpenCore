"""Lightweight front-end tests; real kernel/accuracy qualification runs in CI."""
import importlib
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

try:
    import numpy as np
except ImportError:
    np = None


@unittest.skipUnless(np is not None, 'Requires the speech runtime NumPy')
class MinimalFrontend(unittest.TestCase):
    def frontend(self):
        return importlib.import_module('phonon_minimal')

    def test_silence_is_finite_and_input_is_not_modified(self):
        module = self.frontend()
        audio = np.zeros(16000, np.float32)
        result = module.log_mel(audio, np.ones((128, 257), np.float32))
        self.assertEqual(result.shape, (101, 128))
        self.assertTrue(np.isfinite(result).all())
        self.assertTrue((audio == 0).all())

    def test_short_audio_is_valid_and_empty_or_nonfinite_audio_is_rejected(self):
        module = self.frontend()
        filters = np.ones((128, 257), np.float32)
        self.assertTrue(np.isfinite(module.log_mel(np.ones(20, np.float32), filters)).all())
        for audio in (np.array([], np.float32), np.array([np.nan]), np.array([np.inf])):
            with self.subTest(audio=audio), self.assertRaises(ValueError):
                module.log_mel(audio, filters)

    def test_windows_legacy_packed_loader_preserves_planes_without_dense_weights(self):
        import json
        module = self.frontend()
        # Trit codes [0, 1, 2, 0]: negative low, zero, positive high, negative high.
        # Nonzero magnitude bits are little-endian [0, 1, 1].
        blob = bytes([21, 6]) + np.array([0.5, 1.5], dtype='<f2').tobytes()
        header = json.dumps({'format': 'fermion-five-value-parakeet-v1', 'index': [
            {'k': 'five_value', 'n': 'encoder.test', 'shape': [1, 4], 'b': len(blob)},
            {'k': 'fp16', 'n': 'encoder.bias', 'shape': [2], 'b': 4},
        ]}).encode()
        captured = []

        def matrix(lib, record, planes):
            captured.append(planes)
            return SimpleNamespace(h=1, pa=planes[2])

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'model.fermion'
            path.write_bytes(len(header).to_bytes(8, 'little') + header + blob +
                             np.array([0.25, -0.75], dtype='<f2').tobytes())
            tensors, raw, packed, count = module.read_packed_container(path, SimpleNamespace(),
                                                                     SimpleNamespace(PackedMatrix=matrix), SimpleNamespace())
        self.assertEqual(count, 1)
        self.assertEqual(list(packed), ['encoder.test'])
        self.assertNotIn('encoder.test.weight', tensors)
        self.assertEqual(raw, {})
        rows, cols, pa, pb, lo, hi = captured[0]
        self.assertEqual((rows, cols), (1, 4))
        np.testing.assert_array_equal(pa, [[36]])
        np.testing.assert_array_equal(pb, [[37]])
        np.testing.assert_array_equal(lo, [0.5])
        np.testing.assert_array_equal(hi, [1.5])
        np.testing.assert_array_equal(tensors['encoder.bias'], [0.25, -0.75])

    @unittest.skipUnless(os.environ.get('OPENCORE_PHONON_NUMERICS') == '1', 'Publisher parity runs in Windows CI')
    def test_features_match_publisher_torch_frontend(self):
        import torch
        from fermion._speech.engine_phonon2_cpu import Phonon2CpuSpeechModel, mel_filters, WIN
        module = self.frontend()
        publisher = Phonon2CpuSpeechModel(None, path=Path('.'), profile='five-value', backend='cpu', decode={}, load_seconds=0)
        publisher._window = torch.hann_window(WIN, periodic=False)
        publisher._melf = torch.from_numpy(mel_filters())
        rng = np.random.default_rng(716)
        for count in (160, 16000, 47991):
            with self.subTest(count=count):
                audio = (rng.normal(size=count) * 0.05).astype(np.float32)
                expected, _mask = publisher._log_mel(audio)
                actual = module.log_mel(audio, mel_filters())
                # At only two frames, one float32 log/FFT rounding unit is
                # amplified by almost-zero variance. Keep a separate bounded
                # 0.02% tolerance for this 10 ms edge case; normal recordings
                # retain the tighter tolerance and exact transcript gates.
                tolerance = 2e-4 if count == 160 else 5e-5
                np.testing.assert_allclose(actual, expected[0].numpy(), atol=tolerance, rtol=tolerance)


if __name__ == '__main__':
    unittest.main()
