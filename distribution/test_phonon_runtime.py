import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from phonon_runtime import stage, SOURCE_REVISION


class PhononRuntimePackageTests(unittest.TestCase):
    def test_bundled_runtime_extracts_only_the_verified_binaries_and_support_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            stage(Path(temporary))
            folder = Path(temporary) / 'speech/parakeet'
            manifest = json.loads((folder / 'engine.json').read_text())
            self.assertEqual(manifest['sourceRevision'], SOURCE_REVISION)
            self.assertEqual(manifest['build'], 'gnu-simd-v1')
            self.assertEqual(manifest['f16Quantizer'], 1)
            for name, digest in manifest['files'].items():
                path = folder / name
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest, name)
            self.assertEqual(len(list(folder.glob('*.exe'))), 2)
            self.assertTrue((folder / 'GGML-LICENSE.txt').is_file())
            self.assertTrue((folder / 'PHONON25-NOTICE.txt').is_file())


if __name__ == '__main__':
    unittest.main()
