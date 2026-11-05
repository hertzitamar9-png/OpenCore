"""Exercise the real updater's immutable release selection and bounded disk stream."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import urllib.request

from test_phone_startup import JSON_SHA256, JSON_URL, platform_rank

ROOT = Path(__file__).resolve().parents[1]


class AppUpdatesTests(unittest.TestCase):
    def test_production_release_selection_streaming_and_tamper_rejection(self):
        java_home = os.environ.get('JAVA_HOME')
        sdk = Path(os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT') or Path.home() / '.opencore/android-sdk')
        platforms = [path for path in (sdk / 'platforms').glob('android-*/android.jar') if platform_rank(path)[0] >= 0]
        if not java_home or not platforms:
            self.skipTest('Requires the installed JDK and Android SDK')
        with tempfile.TemporaryDirectory(prefix='opencore-app-update-') as directory:
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
            java = Path(java_home) / 'bin'
            classpath = os.pathsep.join(map(str, (jar, max(platforms, key=platform_rank))))
            compiled = subprocess.run([str(java / 'javac.exe'), '--release', '8', '-encoding', 'UTF-8',
                '-classpath', classpath, '-d', str(output),
                str(ROOT / 'src/ai/opencore/remote/AppUpdates.java'), str(ROOT / 'tests/AppUpdatesTest.java')],
                capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(java / 'java.exe'), '-cp', str(output) + os.pathsep + classpath,
                'ai.opencore.remote.AppUpdatesTest', str(output)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PINNED_RELEASE_AND_ORIGINAL_CERT_VERIFIED', result.stdout)
            self.assertIn('AUTOMATIC_APK_PATHS_DISABLED', result.stdout)
            self.assertIn('STREAMED_APK_OVER_16_MB_AND_TAMPER_REJECTION_VERIFIED', result.stdout)


if __name__ == '__main__':
    unittest.main()
