import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import prepare_phonon_runtime as runtime


class IdentityDecompressor:
    def stream_reader(self, source):
        return source


class Decoder:
    ZstdDecompressor = IdentityDecompressor


class ContainerPreparation(unittest.TestCase):
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
