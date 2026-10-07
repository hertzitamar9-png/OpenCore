"""Contract checks for the publisher's compact engine, without installing it."""
import importlib
import importlib.util
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch


class OriginalRuntime(unittest.TestCase):
    def adapter(self):
        self.assertIsNotNone(importlib.util.find_spec('phonon_original'),
                             'Original must have a real runtime adapter, not a renamed dense precision')
        return importlib.import_module('phonon_original')

    def test_original_requires_packed_encoder_and_native_decoder(self):
        adapter = self.adapter()
        good = {'packed': {'modules': 264, 'onedot': True}, 'c_encoder': True, 'c_tdt_loop': True}
        adapter.require_compact_engine(good)
        for bad in [{}, {**good, 'packed': {'fallback': 'fp32 tier-1'}},
                    {**good, 'c_encoder': False}, {**good, 'c_tdt_loop': False}]:
            with self.subTest(bad=bad), self.assertRaisesRegex(RuntimeError, 'compact'):
                adapter.require_compact_engine(bad)

    def test_original_forces_publisher_compact_path(self):
        adapter = self.adapter()
        env = adapter.original_environment({'PATH': 'keep', 'FERMION_P2_CPU': 'fp32',
                                            'PHONON2_CPU_ONEDOT': '0', 'FERMION_P2_CPU_GRAPH': '1'})
        self.assertEqual(env['PATH'], 'keep')
        self.assertEqual(env['FERMION_P2_CPU'], 'onedot')
        self.assertEqual(env['PHONON2_CPU_ONEDOT'], '1')
        self.assertEqual(env['FERMION_P2_CPU_GRAPH'], '0')
        self.assertEqual(env['FERMION_P2_PLANE_CACHE'], '0')

    def test_config_is_verified_and_only_expected_archive_entry_is_extracted(self):
        adapter = self.adapter()
        class Decoder:
            class ZstdDecompressor:
                def stream_reader(self, source):
                    return source
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with tarfile.open(root / 'phonon-2.bps.tar.zst', 'w') as archive:
                for name, payload in [('../../escaped.txt', b'ignore'), ('config.json', b'config')]:
                    member = tarfile.TarInfo(name); member.size = len(payload)
                    archive.addfile(member, io.BytesIO(payload))
            with patch.object(adapter, 'ARCHIVE_SHA', adapter.digest(root / 'phonon-2.bps.tar.zst')), \
                 patch.object(adapter, 'CONFIG_SHA', hashlib.sha256(b'config').hexdigest()), \
                 patch.object(adapter, 'CONFIG_BYTES', 6):
                self.assertEqual(adapter.extract_original_config(root, Decoder).read_bytes(), b'config')
                (root / 'phonon-2.bps.tar.zst').unlink()
                adapter.extract_original_config(root, Decoder)
                self.assertEqual(sorted(p.name for p in root.iterdir()), ['config.json'])

    def test_corrupt_original_config_keeps_existing_file(self):
        adapter = self.adapter()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'config.json').write_bytes(b'existing')
            (root / 'phonon-2.bps.tar.zst').write_bytes(b'corrupt')
            with self.assertRaisesRegex(RuntimeError, 'missing or corrupted'):
                adapter.extract_original_config(root, None)
            self.assertEqual((root / 'config.json').read_bytes(), b'existing')

    def test_original_adapter_is_in_the_packaged_resource_manifest(self):
        config = json.loads((Path(__file__).parents[2] / 'tauri.conf.json').read_text(encoding='utf-8'))
        self.assertEqual(config['bundle']['resources'].get('resources/speech/phonon_original.py'),
                         'speech/phonon_original.py')


if __name__ == '__main__':
    unittest.main()
