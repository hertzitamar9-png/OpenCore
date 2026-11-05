import sys
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'distribution'))
from mobile_ui import FILES,prepare
from remote_access import RemoteAccess


class MobileUiTests(unittest.TestCase):
    def test_revision_bound_bundle_includes_new_chunks_and_the_web_bridge(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'source';target=root/'runtime-setup/mobile-ui'
            (source/'assets').mkdir(parents=True)
            (source/'index.html').write_text('<html><head></head></html>')
            (source/'assets/app-newhash.js').write_text('window.feature="one"')
            first=prepare(target,source)
            self.assertEqual(first['shellApi'],1)
            self.assertEqual(first['modes'],['pc','phone'])
            self.assertIn('assets/app-newhash.js',first['files'])
            self.assertIn('phone/mobile-shim.js',first['files'])
            self.assertIn('phone/mobile.css',first['files'])
            remote=RemoteAccess(root,root,surface=object())
            try:
                raw=remote.ui_manifest();revision=hashlib.sha256(raw).hexdigest()
                self.assertEqual(remote.ui_revision(),revision)
                self.assertEqual(json.loads(remote.ui_manifest(revision)),first)
                self.assertEqual(remote.mobile_asset('assets/app-newhash.js',revision)[0],b'window.feature="one"')
                (source/'assets/app-newhash.js').write_text('window.feature="two"')
                prepare(target,source)
                with self.assertRaises(ValueError):remote.mobile_asset('assets/app-newhash.js',revision)
                with self.assertRaises(ValueError):remote.ui_manifest(revision)
                self.assertNotEqual(remote.ui_revision(),revision)
            finally:remote.shutdown()

    def test_only_the_verified_built_interface_is_served(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'source';target=root/'runtime-setup/mobile-ui'
            for name in FILES:
                if name=='opencore-logo.png':continue
                path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture '+name,encoding='utf-8')
            prepare(target,source)
            remote=RemoteAccess(root,root,surface=object())
            try:
                data,mime=remote.mobile_asset('index.html')
                self.assertEqual(data,b'fixture index.html');self.assertEqual(mime,'text/html; charset=utf-8')
                for name in ('../secret','assets/../../secret','manifest.json','C:\\secret'):
                    with self.subTest(name=name),self.assertRaises(ValueError):remote.mobile_asset(name)
                (target/'index.html').write_text('modified',encoding='utf-8')
                with self.assertRaises(ValueError):remote.mobile_asset('index.html')
            finally:remote.shutdown()


if __name__=='__main__':unittest.main()
