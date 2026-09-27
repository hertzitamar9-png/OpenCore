"""Evidence snapshots preserve rebuild sources while leaving model weights out."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import snapshot_model_source


class SourceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = []
        for name in ('native.cpp', 'head_projection.h', 'CMakeLists.txt', 'requirements-native.txt',
                     'twincore.dll', 'fusioncore.dll', 'ggml-cuda.dll'):
            path = self.root / name
            path.write_bytes(('fixture ' + name).encode())
            self.paths.append({'path': str(path), 'bytes': path.stat().st_size,
                               'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        self.identity = self.root / 'identity.json'
        self.identity.write_text(json.dumps({'runtime_files': self.paths}), encoding='utf-8')
        self.output = self.root / 'snapshot'

    def tearDown(self):
        self.temp.cleanup()

    def run_snapshot(self, free=300_000_000_000):
        with patch.object(sys, 'argv', ['snapshot_model_source.py', '--identity', str(self.identity),
                                      '--output', str(self.output)]), \
                patch('shutil.disk_usage', return_value=SimpleNamespace(free=free)):
            snapshot_model_source.main()

    def test_keeps_native_build_sources_and_wrappers_without_large_vendor_libraries(self):
        self.run_snapshot()
        receipt = json.loads((self.output / 'snapshot-manifest.json').read_text())
        names = {Path(row['path']).name for row in receipt['files']}
        self.assertEqual(names, {'native.cpp', 'head_projection.h', 'CMakeLists.txt', 'requirements-native.txt',
                                 'twincore.dll', 'fusioncore.dll'})
        for row in receipt['files']:
            self.assertEqual(hashlib.sha256((self.output / row['snapshot_file']).read_bytes()).hexdigest(), row['sha256'])

    def test_changed_source_fails_before_creating_a_snapshot(self):
        (self.root / 'native.cpp').write_bytes(b'changed source')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.run_snapshot()
        self.assertFalse(self.output.exists())

    def test_storage_reserve_refuses_snapshot_before_writing(self):
        with self.assertRaisesRegex(ValueError, '200 GB'):
            self.run_snapshot(free=199_999_999_999)
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
