"""Exercise download detection in the actual phone bridge permission gate."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import urllib.request

from test_phone_startup import JSON_SHA256, JSON_URL, platform_rank

ROOT = Path(__file__).resolve().parents[1]


class NotificationGateTests(unittest.TestCase):
    def test_only_new_model_downloads_trigger_the_explanation(self):
        home = os.environ.get('JAVA_HOME')
        sdk = Path(os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT') or Path.home() / '.opencore/android-sdk')
        platforms = [p for p in (sdk / 'platforms').glob('android-*/android.jar') if platform_rank(p)[0] >= 0]
        if not home or not platforms:
            self.skipTest('Requires the installed JDK and Android SDK')
        with tempfile.TemporaryDirectory(prefix='opencore-notification-gate-') as directory:
            output = Path(directory)
            cached = ROOT.parent / 'distribution/.output/android-json-aosp.jar'
            if cached.is_file():
                dependency = cached.read_bytes()
            else:
                with urllib.request.urlopen(JSON_URL, timeout=20) as response:
                    dependency = response.read(128 * 1024)
            self.assertEqual(hashlib.sha256(dependency).hexdigest(), JSON_SHA256)
            jar = output / 'android-json.jar'
            jar.write_bytes(dependency)
            java = Path(home) / 'bin'
            classpath = os.pathsep.join(map(str, (jar, max(platforms, key=platform_rank))))
            result = subprocess.run([str(java / 'javac.exe'), '--release', '8', '-encoding', 'UTF-8',
                '-classpath', classpath, '-d', str(output),
                str(ROOT / 'src/ai/opencore/remote/NotificationGate.java'), str(ROOT / 'tests/NotificationGateTest.java')],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(java / 'java.exe'), '-cp', str(output) + os.pathsep + classpath,
                'ai.opencore.remote.NotificationGateTest'], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('NOTIFICATION_DOWNLOAD_REQUESTS_VERIFIED', result.stdout)


if __name__ == '__main__':
    unittest.main()
