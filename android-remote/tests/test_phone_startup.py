"""Run the production snapshot serializer against AOSP's real Android org.json."""
from pathlib import Path
import hashlib
import os
import re
import subprocess
import tempfile
import unittest
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
# This is Android's libcore JSON implementation, not JSON-java. Its numeric
# rejection and missing optDouble default reproduce the physical-phone failure.
JSON_URL = ('https://repo.maven.apache.org/maven2/com/vaadin/external/google/'
            'android-json/0.0.20131108.vaadin1/android-json-0.0.20131108.vaadin1.jar')
JSON_SHA256 = 'dfb7bae2f404cfe0b72b4d23944698cb716b7665171812a0a4d0f5926c0fac79'


def platform_rank(path):
    match = re.fullmatch(r'android-(\d+)(?:-ext(\d+))?', path.parent.name)
    return (int(match[1]), int(match[2] or 0)) if match else (-1, 0)


class PhoneStartupTests(unittest.TestCase):
    def test_numeric_sdk_platform_ranking_including_extensions(self):
        jars = [Path(name)/'android.jar' for name in ('android-34', 'android-35-ext10', 'android-36', 'android-preview')]
        self.assertEqual(max(jars, key=platform_rank), Path('android-36/android.jar'))
        self.assertEqual(platform_rank(Path('android-35-ext10/android.jar')), (35, 10))
        self.assertEqual(platform_rank(Path('android-preview/android.jar')), (-1, 0))

    def test_real_android_json_startup_snapshots(self):
        java_home = os.environ.get('JAVA_HOME')
        sdk = Path(os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT')
                   or Path.home()/'.opencore/android-sdk')
        platforms = [path for path in (sdk/'platforms').glob('android-*/android.jar') if platform_rank(path)[0] >= 0]
        if not java_home or not platforms:
            self.skipTest('An installed JDK and Android SDK are required for the native bridge regression')
        platform = max(platforms, key=platform_rank)
        java = Path(java_home)/'bin'
        with tempfile.TemporaryDirectory(prefix='opencore-startup-json-') as directory:
            output = Path(directory)
            jar = output/'android-json.jar'
            cached = ROOT.parent/'distribution/.output/android-json-aosp.jar'
            if cached.is_file():
                dependency = cached.read_bytes()
            else:
                with urllib.request.urlopen(JSON_URL, timeout=20) as response:
                    dependency = response.read(128*1024)
            self.assertEqual(hashlib.sha256(dependency).hexdigest(), JSON_SHA256,
                             'The pinned real Android org.json dependency changed')
            jar.write_bytes(dependency)
            # Android resources are not exercised; this compiler-only ID replaces
            # the aapt-generated R class so the regression needs no APK build.
            resource = output/'R.java'
            resource.write_text('package ai.opencore.remote; final class R {'
                                'static final class drawable {static final int opencore=1;}}', encoding='utf-8')
            classpath = os.pathsep.join(map(str, (jar, platform, ROOT/'vendor/zxing-core-3.5.3.jar')))
            sources = list((ROOT/'src').rglob('*.java'))
            tests = [ROOT/'tests/PhoneStartupSnapshotTest.java', ROOT/'tests/PhoneGenerationSettingsTest.java', ROOT/'tests/PhoneProfileSelectionTest.java']
            compiled = subprocess.run([str(java/'javac.exe'), '--release', '8', '-encoding', 'UTF-8',
                                       '-classpath', classpath, '-d', str(output),
                                       *map(str, sources), *map(str, tests), str(resource)],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            for test in ('PhoneStartupSnapshotTest', 'PhoneGenerationSettingsTest', 'PhoneProfileSelectionTest'):
                result = subprocess.run([str(java/'java.exe'), '-cp', str(output)+os.pathsep+classpath,
                                         'ai.opencore.remote.'+test], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                if test == 'PhoneStartupSnapshotTest':
                    for marker in ('FRESH_NO_MODEL_SNAPSHOT', 'EXISTING_DATA_STARTUP', 'REPORTED_NATIVE_METRICS'):
                        self.assertIn(marker, result.stdout)


if __name__ == '__main__':
    unittest.main()
