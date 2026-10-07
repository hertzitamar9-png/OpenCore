"""Lightweight front-end tests; real kernel/accuracy qualification runs in CI."""
import importlib
import os
from pathlib import Path
import sys
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
                np.testing.assert_allclose(actual, expected[0].numpy(), atol=5e-5, rtol=5e-5)


if __name__ == '__main__':
    unittest.main()
