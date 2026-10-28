"""Run the Android-independent native client checks with the installed JDK."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NativeClientTests(unittest.TestCase):
    def test_session_revision_and_image_cache(self):
        java_home = Path(os.environ['JAVA_HOME'])
        sources = ROOT / 'src/ai/opencore/remote'
        with tempfile.TemporaryDirectory(prefix='opencore-android-tests-') as output:
            files = [sources / name for name in ('RemoteSession.java', 'ImageMemoryCache.java', 'ConnectionMessages.java')]
            compile_result = subprocess.run([str(java_home / 'bin/javac.exe'), '--release', '8', '-encoding', 'UTF-8', '-d', output,
                                             *map(str, files), str(ROOT / 'tests/RemoteClientTest.java')], capture_output=True, text=True)
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            result = subprocess.run([str(java_home / 'bin/java.exe'), '-cp', output, 'ai.opencore.remote.RemoteClientTest'],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
