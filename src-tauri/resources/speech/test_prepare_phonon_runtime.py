import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import prepare_phonon_runtime as runtime
import runtime_setup


class IdentityDecompressor:
    def stream_reader(self, source):
        return source


class Decoder:
    ZstdDecompressor = IdentityDecompressor


class ContainerPreparation(unittest.TestCase):
    def test_unchanged_verified_environment_is_reused_but_changed_dependencies_are_not(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python = root / 'phonon-venv/Scripts/python.exe'
            python.parent.mkdir(parents=True)
            python.write_bytes(b'python')
            receipt = {'schema': runtime_setup.RUNTIME_SCHEMA, 'executionContract': runtime_setup.EXECUTION_CONTRACT,
                       'dependenciesVerified': True, 'packages': runtime.PACKAGES,
                       'hardware': {'cudaBuild': '12.4', 'cudaAvailable': True, 'kernelVerified': True},
                       'environmentSignature': 'verified-signature', 'python': str(python.resolve())}
            import json
            (root / 'phonon-runtime.json').write_text(json.dumps(receipt))
            with patch.object(runtime_setup, 'environment_signature', return_value='verified-signature'):
                self.assertEqual(runtime_setup.cached_verification(root, 'phonon', runtime.PACKAGES), receipt)
            with patch.object(runtime_setup, 'environment_signature', return_value='changed-signature'):
                self.assertIsNone(runtime_setup.cached_verification(root, 'phonon', runtime.PACKAGES))
            python.unlink()
            with patch.object(runtime_setup, 'environment_signature', return_value='verified-signature'):
                self.assertIsNone(runtime_setup.cached_verification(root, 'phonon', runtime.PACKAGES))

    def test_extracts_only_verified_container_and_reuses_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with tarfile.open(root / 'phonon-2.bps.tar.zst', 'w') as archive:
                for name, payload in [('../../escaped.txt', b'ignore'), ('model_phonon2_c4c_int6/model.fermion', b'checkpoint')]:
                    info = tarfile.TarInfo(name); info.size = len(payload)
                    archive.addfile(info, io.BytesIO(payload))
            with patch.object(runtime,'ARCHIVE_SHA',runtime.digest(root / 'phonon-2.bps.tar.zst')), \
                 patch.object(runtime,'CONTAINER_SHA',hashlib.sha256(b'checkpoint').hexdigest()), \
                 patch.object(runtime,'CONTAINER_BYTES',10):
                runtime.extract_container(root,Decoder)
                self.assertEqual((root / 'model.fermion').read_bytes(),b'checkpoint')
                self.assertEqual(sorted(p.name for p in root.iterdir()),['model.fermion','phonon-2.bps.tar.zst'])
                (root / 'phonon-2.bps.tar.zst').unlink()
                runtime.extract_container(root,Decoder)
                self.assertFalse((root / 'model.fermion.partial').exists())

    def test_corrupt_download_does_not_replace_existing_container(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'model.fermion').write_bytes(b'old')
            (root / 'phonon-2.bps.tar.zst').write_bytes(b'corrupt')
            with self.assertRaisesRegex(RuntimeError,'archive checksum'):
                runtime.extract_container(root,Decoder)
            self.assertEqual((root / 'model.fermion').read_bytes(),b'old')


if __name__ == '__main__':
    unittest.main()
