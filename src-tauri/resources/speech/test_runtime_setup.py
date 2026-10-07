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
            with patch.object(runtime, 'probe', return_value=None), \
                 patch.object(runtime.shutil, 'disk_usage', return_value=SimpleNamespace(free=free)), \
                 patch.object(runtime.subprocess, 'run') as run:
                result = runtime.prepare(root, 'whisper-venv', {'test-package': '1.0'})
                self.assertEqual(result, (target, None))
                self.assertEqual(run.call_count, 2)

    def test_cuda_runtime_fits_with_less_than_100_gb_free(self):
        self.prepare_without_downloads(88_000_000_000)

    def test_cuda_runtime_still_rejects_insufficient_space(self):
        with self.assertRaisesRegex(RuntimeError, 'disk space'):
            self.prepare_without_downloads(1_000_000_000)


if __name__ == '__main__':
    unittest.main()
