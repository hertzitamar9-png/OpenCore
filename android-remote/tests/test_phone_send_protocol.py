"""Strict production phone IPC + real host JNI; no browser command fixtures."""
from pathlib import Path
import hashlib
import os
import subprocess
import tempfile
import unittest
import urllib.request

from test_phone_startup import JSON_URL, JSON_SHA256, platform_rank

ROOT = Path(__file__).resolve().parents[1]
MODEL_SHA256 = '2e8040ceae7815abe0dcb3540b9995eaa1fa0d2ca9e797d0a635ae4433c68c2d'


class PhoneSendProtocolTests(unittest.TestCase):
    def test_actual_send_files_side_chat_and_download_lifecycle(self):
        output_root = ROOT.parent/'distribution/.output'
        model = Path(os.environ.get('OPENCORE_PHONE_TEST_MODEL') or output_root/'android-runtime/SmolLM2-test.gguf')
        native = Path(os.environ.get('OPENCORE_PHONE_TEST_NATIVE') or output_root/'android-runtime/windows')
        sdk = Path(os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT') or Path.home()/'.opencore/android-sdk')
        platforms = [path for path in (sdk/'platforms').glob('android-*/android.jar') if platform_rank(path)[0] >= 0]
        java_home = os.environ.get('JAVA_HOME')
        if not java_home or not platforms or not model.is_file() or not (native/'opencore_phone.dll').is_file():
            self.skipTest('Requires the previously verified host JNI/model fixture, JDK, and Android SDK; downloads no model')
        self.assertEqual(hashlib.sha256(model.read_bytes()).hexdigest(), MODEL_SHA256, 'Use the pinned real neutral test model')
        java = Path(java_home)/'bin'
        with tempfile.TemporaryDirectory(prefix='opencore-phone-protocol-') as directory:
            output = Path(directory)
            cache = output_root/'android-json-aosp.jar'
            if cache.is_file():
                dependency = cache.read_bytes()
            else:
                with urllib.request.urlopen(JSON_URL, timeout=20) as response:
                    dependency = response.read(128*1024)
            self.assertEqual(hashlib.sha256(dependency).hexdigest(), JSON_SHA256)
            jar = output/'android-json.jar'; jar.write_bytes(dependency)
            resource = output/'R.java'; resource.write_text('package ai.opencore.remote; final class R {static final class drawable {static final int opencore=1;}}')
            classes = output/'classes'; classes.mkdir()
            state = output/'state'; state.mkdir()
            classpath = os.pathsep.join(map(str, (jar, max(platforms, key=platform_rank), ROOT/'vendor/zxing-core-3.5.3.jar', *(ROOT/'vendor/litertlm').glob('*.jar'))))
            result = subprocess.run([str(java/'javac.exe'), '--release', '8', '-encoding', 'UTF-8', '-classpath', classpath,
                                     '-d', str(classes), *map(str, (ROOT/'src').rglob('*.java')),
                                     str(ROOT/'tests/PhoneSendProtocolTest.java'), str(resource)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(java/'java.exe'), '-Djava.library.path='+str(native), '-cp', str(classes)+os.pathsep+classpath,
                                     'ai.opencore.remote.PhoneSendProtocolTest', str(model), str(state)], capture_output=True, text=True, timeout=60)
            log = output_root/'android-real-send-protocol.log'; log.write_text(result.stdout+result.stderr, encoding='utf-8')
            self.assertEqual(result.returncode, 0, (result.stdout+result.stderr)[-5000:])
            for marker in ('ACTUAL_SEND_HI', 'ACTUAL_SIDE_CHAT', 'ACTUAL_WORKSPACE', 'ACTUAL_DOWNLOAD'):
                self.assertIn(marker, result.stdout)


if __name__ == '__main__':
    unittest.main()
