"""Run the real helper with pinned AOSP JSON and no network or Android device."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import urllib.request
from test_phone_startup import JSON_URL, JSON_SHA256

ROOT = Path(__file__).resolve().parents[1]


class PhoneTemplateSupportTests(unittest.TestCase):
    def test_real_java_template_verification_and_cache(self):
        java = Path(os.environ['JAVA_HOME']) / 'bin'
        sdk = Path(os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT') or Path.home() / '.opencore/android-sdk')
        platforms = sorted((sdk / 'platforms').glob('android-*/android.jar'))
        self.assertTrue(platforms, 'Pinned Android SDK is required for this helper regression')
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            cached = ROOT.parent/'distribution/.output/android-json-aosp.jar'
            if cached.is_file():
                dependency = cached.read_bytes()
            else:
                with urllib.request.urlopen(JSON_URL, timeout=20) as response:
                    dependency = response.read(128*1024)
            self.assertEqual(hashlib.sha256(dependency).hexdigest(), JSON_SHA256)
            jar = output/'android-json.jar'
            jar.write_bytes(dependency)
            stub = output / 'NetworkForbidden.java'
            stub.write_text('package ai.opencore.remote; class PhoneHub {'
                            'static javax.net.ssl.HttpsURLConnection open(String u,String t) throws java.io.IOException {throw new java.io.IOException("Network forbidden in tests");}'
                            'static String downloadUrl(org.json.JSONObject o){throw new AssertionError("Network forbidden");}}'
                            'class PhoneCredentials {static String token(android.content.Context c){throw new AssertionError("Credentials forbidden in tests");}}', encoding='utf-8')
            source = ROOT / 'src/ai/opencore/remote'
            classpath = os.pathsep.join(map(str, (jar, platforms[-1])))
            compiled = subprocess.run([str(java / 'javac.exe'), '--release', '8', '-encoding', 'UTF-8',
                                       '-classpath', classpath, '-d', str(output), str(stub),
                                       str(source / 'ExecutionPolicy.java'), str(source / 'PhoneTemplateSupport.java'),
                                       str(ROOT / 'tests/PhoneTemplateSupportTest.java')], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(java / 'java.exe'), '-cp', str(output) + os.pathsep + classpath,
                                     'ai.opencore.remote.PhoneTemplateSupportTest'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('ownership checks passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
