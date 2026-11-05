"""Real Java store + pinned TLS transport + production PC routes; no APK install.

This host evidence deliberately does not claim physical Android/WebView proof.
Run rendered follow-up with: node android-remote/tests/live-ui-render.cjs
"""
import hashlib
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request

from test_phone_startup import JSON_SHA256, JSON_URL, platform_rank

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'src-tauri/resources/runtime-setup/hf-extension'))
sys.path.insert(0, str(ROOT/'distribution'))
from remote_access import RemoteAccess, RemoteHandler
from mobile_ui import prepare
from tls_identity import create_identity


class LiveUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sdk = Path(os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT') or Path.home()/'.opencore/android-sdk')
        platforms = list((sdk/'platforms').glob('android-*/android.jar'))
        if not platforms or not os.environ.get('JAVA_HOME'):
            raise unittest.SkipTest('Requires the installed Android SDK and JDK')
        cls.temporary = tempfile.TemporaryDirectory(prefix='opencore-live-ui-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.output = Path(cls.temporary.name)
        cached = ROOT/'distribution/.output/android-json-aosp.jar'
        if cached.is_file():
            data = cached.read_bytes()
        else:
            with urllib.request.urlopen(JSON_URL, timeout=20) as response:
                data = response.read(128*1024)
        if hashlib.sha256(data).hexdigest() != JSON_SHA256:
            raise AssertionError('Real Android JSON dependency changed')
        jar = cls.output/'android-json.jar'
        jar.write_bytes(data)
        cls.java = Path(os.environ['JAVA_HOME'])/'bin'
        cls.classpath = os.pathsep.join(map(str, (cls.output, jar, max(platforms, key=platform_rank))))
        source = ROOT/'android-remote/src/ai/opencore/remote'
        result = subprocess.run([str(cls.java/'javac.exe'), '--release', '8', '-encoding', 'UTF-8', '-cp', cls.classpath, '-d', str(cls.output),
                                 *map(str, [source/'LiveUiStore.java', source/'Connection.java', ROOT/'android-remote/tests/LiveUiStoreTest.java', ROOT/'android-remote/tests/LiveUiProbe.java'])], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr)

    def java_run(self, name, *args):
        return subprocess.run([str(self.java/'java.exe'), '-cp', self.classpath, 'ai.opencore.remote.'+name, *map(str, args)], capture_output=True, text=True, timeout=40)

    def test_atomic_store_failure_recovery_and_both_modes(self):
        result = self.java_run('LiveUiStoreTest', self.output/'store-tests')
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual(result.stdout.count('ATOMIC_SWAP_ROLLBACK_OFFLINE_CRASH_TAMPER_MODE_AND_NATIVE_REJECTION_PASSED'), 2)

    def test_backend_ui_changes_with_identical_client_and_no_apk_traffic(self):
        evidence = ROOT/'distribution/.output/live-ui/evidence'
        evidence.mkdir(parents=True, exist_ok=True)
        resources = self.output/'backend'
        target = resources/'runtime-setup/mobile-ui'
        source = self.output/'frontend'
        source.mkdir()
        # An explicitly built frontend can exercise a newer UI on the same
        # client. Otherwise use the real committed Android desktop bundle.
        desktop = Path(os.environ.get('OPENCORE_LIVE_UI_DIST') or ROOT/'android-remote/shared-ui')
        for path in desktop.rglob('*'):
            if path.is_file() and path.name != 'manifest.json':
                to = source/path.relative_to(desktop)
                to.parent.mkdir(parents=True, exist_ok=True)
                to.write_bytes(path.read_bytes())
        original = (source/'index.html').read_text(encoding='utf-8')
        certificate, key, pin = create_identity()  # Temporary test key, never the user's identity.
        cert_file, key_file = self.output/'test-cert.pem', self.output/'test-key.pem'
        cert_file.write_bytes(certificate)
        key_file.write_bytes(key)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert_file, key_file)
        token = 'fixture-only-bearer-token'
        remote = RemoteAccess(resources, resources, surface=object())
        remote.devices['fixture'] = {'id':'fixture', 'name':'Test phone', 'hash':hashlib.sha256(token.encode()).hexdigest()}
        requests = []
        class Handler(RemoteHandler):
            def handle_api(self):
                requests.append({'method':self.command, 'path':self.path})
                return super().handle_api()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.state = remote
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        endpoint = 'https://127.0.0.1:'+str(server.server_port)
        client_before = hashlib.sha256((self.output/'ai/opencore/remote/LiveUiStore.class').read_bytes()).hexdigest()
        apk = ROOT/'distribution/mobile/OpenCore.Remote.apk'
        apk_before = hashlib.sha256(apk.read_bytes()).hexdigest()
        results = []
        def publish(version):
            feature = source/'assets/live-ui-feature.js'
            feature.write_text("const button=document.createElement('button');button.id='live-ui-feature';button.textContent='Backend feature "+version+"';button.onclick=()=>button.textContent='Activated "+version+"';document.body.append(button);", encoding='utf-8')
            (source/'index.html').write_text(original.replace('<head>', '<head><meta name="live-ui-test-revision" content="'+version+'"><script type="module" src="/assets/live-ui-feature.js"></script>'), encoding='utf-8')
            prepare(target, source)
        try:
            for mode in ('pc', 'phone'):
                store = evidence/mode
                if store.exists():
                    # Preserve prior test evidence; use a distinct store per run.
                    store = evidence/(mode+'-'+os.urandom(4).hex())
                sentinel = self.output/(mode+'-private-chats-and-weights.txt')
                sentinel.write_text('private chat and model fixture; never upload')
                before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
                versions = []
                for version in ('one', 'two'):
                    publish(version)
                    probe = self.java_run('LiveUiProbe', endpoint, pin, token, mode, store, 'healthy')
                    self.assertEqual(probe.returncode, 0, probe.stdout+probe.stderr)
                    versions.append(json.loads(probe.stdout))
                self.assertNotEqual(versions[0]['revision'], versions[1]['revision'])
                self.assertNotEqual(versions[0]['indexSha256'], versions[1]['indexSha256'])
                # Reject another revision that boots incorrectly; last good is two.
                publish('broken-startup')
                rollback = self.java_run('LiveUiProbe', endpoint, pin, token, mode, store, 'rollback')
                self.assertEqual(rollback.returncode, 0, rollback.stderr)
                self.assertEqual(json.loads(rollback.stdout)['revision'], versions[1]['revision'])
                count = len(requests)
                offline = self.java_run('LiveUiProbe', 'https://127.0.0.1:1', pin, token, mode, store, 'offline')
                self.assertEqual(offline.returncode, 0, offline.stderr)
                self.assertEqual(json.loads(offline.stdout)['revision'], versions[1]['revision'])
                self.assertEqual(len(requests), count, 'Offline reopening must make zero network requests')
                self.assertEqual(hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)
                denied = self.java_run('LiveUiProbe', endpoint, pin, 'unpaired-invalid-token', mode, self.output/(mode+'-unpaired'), 'healthy')
                self.assertNotEqual(denied.returncode, 0, 'An unpaired client cannot obtain UI assets')
                wrong_pin = self.java_run('LiveUiProbe', endpoint, '0'*64, token, mode, self.output/(mode+'-wrong-pin'), 'healthy')
                self.assertNotEqual(wrong_pin.returncode, 0, 'A different TLS identity cannot serve executable UI')
                results.append({'mode':mode, 'versions':versions, 'offlineLastKnownGood':True, 'rollback':True, 'privateDataUnchanged':True})
        finally:
            server.shutdown()
            server.server_close()
            remote.mobile.close()
        self.assertEqual(hashlib.sha256(apk.read_bytes()).hexdigest(), apk_before)
        self.assertEqual(hashlib.sha256((self.output/'ai/opencore/remote/LiveUiStore.class').read_bytes()).hexdigest(), client_before)
        self.assertTrue(all(item['path'] in ('/info', '/uimanifest', '/ui') for item in requests), requests)
        report = {'scope':'JVM production store/transport + real HTTPS PC routes; not an installed Android test', 'physicalAndroidVerified':False, 'frontendInput':str(desktop),
                  'clientClassSha256Before':client_before, 'clientClassSha256After':client_before, 'repositoryApkSha256Before':apk_before, 'repositoryApkSha256After':apk_before,
                  'apkRequests':0, 'installerInvocationsInThisHarness':0, 'modes':results, 'requests':requests}
        (evidence/'results.json').write_text(json.dumps(report, indent=2)+'\n')

    def test_ordinary_paths_do_not_reach_the_installer(self):
        for name in ('MobileActivity.java', 'PhoneActivity.java', 'LiveUi.java', 'LiveUiStore.java'):
            source = (ROOT/'android-remote/src/ai/opencore/remote'/name).read_text()
            self.assertNotIn('AppUpdates.check(', source)
            self.assertNotIn('PackageInstaller', source)
            self.assertNotIn('.apk', source)
        updater = (ROOT/'android-remote/src/ai/opencore/remote/AppUpdates.java').read_text()
        background = updater.split('static void checkBackground(', 1)[1].split('static void error(', 1)[0]
        self.assertNotIn('candidate(', background)
        self.assertNotIn('submitInstall(', background)
        self.assertIn('jobs.cancel(JOB_ID)', updater)


if __name__ == '__main__':
    unittest.main()
