"""Exercise installed-version decisions in the real small Windows setup."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class BootstrapInstallationTests(unittest.TestCase):
    def test_setup_distinguishes_new_install_current_update_and_unknown_version(self):
        exe=Path(os.environ.get('OPENCORE_BOOTSTRAP_EXE') or Path(__file__).parent/'bootstrap/permanent/OpenCore Setup.exe')
        with tempfile.TemporaryDirectory(prefix='opencore setup test ') as directory:
            root=Path(directory)
            def decision(tag='app-v0.3.247'):
                return subprocess.run([str(exe),'--check-installed',str(root),tag],capture_output=True,timeout=10).returncode
            def asset(expected):
                return subprocess.run([str(exe),'--check-asset',str(root),'app-v0.3.247',expected],capture_output=True,timeout=10).returncode
            self.assertEqual(decision(),20,'No app should select a fresh install')
            self.assertEqual(asset('OpenCore.Package.exe'),0,'A fresh install uses the complete package')
            (root/'OpenCore.exe').write_bytes(b'fixture')
            self.assertEqual(decision(),11,'An installed app with unknown version must not be redownloaded')
            info=root/'build-info.json'
            info.write_text(json.dumps({'build':'0.3.247'}),encoding='utf-8')
            self.assertEqual(decision(),0,'The current version needs no download')
            self.assertEqual(asset(''),0,'A current installation must not choose a download')
            info.write_text(json.dumps({'build':'0.3.246'}),encoding='utf-8')
            self.assertEqual(decision(),10,'An older install needs an update')
            self.assertEqual(asset('OpenCore.Update.exe'),0,'An update uses the incremental updater')
            info.write_text(json.dumps({'build':'0.3.248'}),encoding='utf-8')
            self.assertEqual(decision(),0,'A newer install must not be downgraded')
            info.write_text('{invalid',encoding='utf-8')
            self.assertEqual(decision(),11,'Corrupt metadata must not trigger a duplicate install')
            self.assertEqual(decision('app-v0.3.247-preview'),2,'Only a stable release is accepted')


if __name__=='__main__':unittest.main()
