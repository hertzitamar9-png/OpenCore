"""Exercise the real bootstrap release admission without launching an installer."""
import os
from pathlib import Path
import subprocess
import unittest


class BootstrapBridgePolicy(unittest.TestCase):
    def test_setup_rejects_old_bridge_enabled_releases_and_accepts_current_stable(self):
        exe=Path(os.environ.get('OPENCORE_BOOTSTRAP_EXE') or Path(__file__).parent/'bootstrap/permanent/OpenCore Setup.exe')
        def admitted(tag):
            return subprocess.run([str(exe),'--check-release',tag],capture_output=True,timeout=10).returncode==0
        self.assertFalse(admitted('app-v0.3.242'))
        self.assertTrue(admitted('app-v0.3.243'))
        self.assertTrue(admitted('app-v1.0.0'))
        self.assertFalse(admitted('app-v0.3.243-preview'))
        self.assertFalse(admitted('untrusted'))


if __name__=='__main__':unittest.main()
