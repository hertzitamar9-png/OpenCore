from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import runtime_setup as runtime
import os


class RuntimeDiskSpace(unittest.TestCase):
    def test_detects_available_managed_environments_without_requiring_a_named_training_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed = root / 'training-envs/available-runtime/Scripts/python.exe'
            installed.parent.mkdir(parents=True)
            installed.write_bytes(b'fixture')
            with patch.dict(os.environ, {'LOCALAPPDATA': str(root)}):
                candidates = runtime.python_candidates(root / 'speech')
            self.assertIn(str(installed), candidates)

    def prepare_without_downloads(self, free):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'whisper-venv/Scripts/python.exe'
            target.parent.mkdir(parents=True)
            target.write_bytes(b'placeholder')
            with patch.object(runtime, 'probe', side_effect=[None, {'version':'2.5.1+cu124','cuda':'12.4'}]), \
                 patch.object(runtime, 'python_candidates', return_value=[]), \
                 patch.object(runtime, 'probe_packages', side_effect=[['test-package'], []]), \
                 patch.object(runtime.shutil, 'disk_usage', return_value=SimpleNamespace(free=free)), \
                 patch.object(runtime.subprocess, 'run') as run:
                result = runtime.prepare(root, 'whisper-venv', {'test-package': '1.0'})
                self.assertEqual(result, (target, None))
                self.assertEqual(run.call_count, 2)

    def test_cuda_runtime_fits_with_less_than_100_gb_free(self):
        self.prepare_without_downloads(88_000_000_000)

    def test_usable_existing_environment_never_reinstalls_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'whisper-venv/Scripts/python.exe'
            target.parent.mkdir(parents=True); target.write_bytes(b'placeholder')
            with patch.object(runtime, 'probe', return_value={'version':'2.5.1+cu124','cuda':'12.4'}), \
                 patch.object(runtime, 'probe_packages', return_value=[]), \
                 patch.object(runtime.subprocess, 'run') as run:
                self.assertEqual(runtime.prepare(root, 'whisper-venv', {'test-package':'1.0'}), (target, None))
                run.assert_not_called()

    def test_cuda_runtime_still_rejects_insufficient_space(self):
        with self.assertRaisesRegex(RuntimeError, 'disk space'):
            self.prepare_without_downloads(1_000_000_000)



class RuntimeVariants(unittest.TestCase):
    def test_variant_follows_the_shared_hardware_profile(self):
        profile = runtime._hardware_profile()
        nvidia = profile.build({'gpus': [{'vendor': 'nvidia', 'name': 'RTX'}], 'cudaDriver': 12080,
                                'computeCapability': [8, 9], 'vulkan': True})
        cpu_only = profile.build({'gpus': []})
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=False):
            os.environ.pop('OPENCORE_SPEECH_VARIANT', None)
            with patch.object(profile, 'profile', return_value=nvidia):
                self.assertEqual(runtime.selected_variant(directory, 'speech-whisper-v1'), 'cuda')
                self.assertTrue(runtime.cuda_ready())
            with patch.object(profile, 'profile', return_value=cpu_only):
                self.assertEqual(runtime.selected_variant(directory, 'speech-whisper-v1'), 'cpu')
                self.assertFalse(runtime.cuda_ready())
                profile.save_choice(directory, 'speech-whisper-v1', 'cuda')
                self.assertEqual(runtime.selected_variant(directory, 'speech-whisper-v1'), 'cuda')
            os.environ['OPENCORE_SPEECH_VARIANT'] = 'cpu'
            self.assertEqual(runtime.selected_variant(directory, 'speech-whisper-v1'), 'cpu')


if __name__ == '__main__':
    unittest.main()
