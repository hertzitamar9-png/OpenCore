import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'distribution'))
from mobile_ui import FILES,prepare
from remote_access import RemoteAccess


class MobileUiTests(unittest.TestCase):
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
