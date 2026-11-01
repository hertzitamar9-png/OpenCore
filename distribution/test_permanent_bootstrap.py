"""The same setup executable must serve unrelated future app versions without a compiler."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import permanent_bootstrap as bootstrap


class PermanentBootstrapTests(unittest.TestCase):
    def test_different_app_versions_use_identical_setup_bytes(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            root = Path(temporary)
            for index, version in enumerate(('0.3.243', '8.20.999')):
                output = root / str(index)
                bootstrap.prepare(output, version)
                report = json.loads((output / 'bootstrap-verification.json').read_text())
                self.assertTrue(report['permanent'])
                self.assertEqual(report['verifiedForAppVersion'], version)
                self.assertEqual(report['bootstrapVersion'], bootstrap.load_pin()['bootstrapVersion'])
                self.assertFalse((output/'OpenCore Setup.exe.sig').exists(), 'a stale signature must never be reused')
            self.assertEqual((root/'0'/'OpenCore Setup.exe').read_bytes(), (root/'1'/'OpenCore Setup.exe').read_bytes())

    def test_modified_permanent_file_is_rejected_before_creating_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / 'source', root / 'output'
            source.mkdir()
            shutil.copy2(bootstrap.SOURCE / 'OpenCore Setup.exe', source / 'OpenCore Setup.exe')
            setup = source / 'OpenCore Setup.exe'
            value = bytearray(setup.read_bytes()); value[-1] ^= 1; setup.write_bytes(value)
            with self.assertRaisesRegex(ValueError, 'permanent OpenCore Setup'):
                bootstrap.prepare(output, '0.3.159', source=source)
            self.assertFalse(output.exists())

    def test_canonical_file_cannot_be_overwritten_as_build_output(self):
        with self.assertRaisesRegex(ValueError, 'source cannot'):
            bootstrap.prepare(bootstrap.SOURCE, '0.3.159')


if __name__ == '__main__':
    unittest.main()
