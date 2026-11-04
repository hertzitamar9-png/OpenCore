"""Exercise production durable scheduling with real Android JSON, without JNI or downloads."""
from pathlib import Path
import hashlib
import os
import subprocess
import tempfile
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
JSON_URL = ('https://repo.maven.apache.org/maven2/com/vaadin/external/google/'
            'android-json/0.0.20131108.vaadin1/android-json-0.0.20131108.vaadin1.jar')
JSON_SHA256 = 'dfb7bae2f404cfe0b72b4d23944698cb716b7665171812a0a4d0f5926c0fac79'


class PhoneDownloadQueueTests(unittest.TestCase):
    def test_real_durable_scheduler(self):
        java_home = os.environ.get('JAVA_HOME')
        if not java_home:
            self.skipTest('An installed JDK is required for phone queue regression tests')
        java = Path(java_home) / 'bin'
        with tempfile.TemporaryDirectory(prefix='opencore-download-queue-tests-') as directory:
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
            files = [ROOT / 'src/ai/opencore/remote' / name for name in
                     ('ExecutionPolicy.java', 'PhoneDownloadQueue.java')]
            compiled = subprocess.run([str(java / 'javac.exe'), '--release', '8', '-encoding', 'UTF-8',
                                       '-classpath', str(jar), '-d', str(output), *map(str, files),
                                       str(ROOT / 'tests/PhoneDownloadQueueTest.java')],
                                      capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(java / 'java.exe'), '-cp', str(output) + os.pathsep + str(jar),
                                     'ai.opencore.remote.PhoneDownloadQueueTest'],
                                    capture_output=True, text=True, encoding='utf-8', timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for marker in ('PHONE_QUEUE_FIFO_AND_DEDUP', 'PHONE_QUEUE_PARALLEL_ISOLATION_AND_MODE_SWITCH',
                           'PHONE_QUEUE_INTERRUPTION_RESTORE_AND_PAUSE', 'PHONE_QUEUE_CANCEL_RESTORE_AND_IDENTITY_GUARDS',
                           'PHONE_QUEUE_UNBOUNDED_APPEND'):
                self.assertIn(marker, result.stdout)


if __name__ == '__main__':
    unittest.main()
