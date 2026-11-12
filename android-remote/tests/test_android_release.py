"""Regression checks for same-package, same-key Android releases from CI."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'distribution'))
import mobile_client
import phone_ui_release

spec = importlib.util.spec_from_file_location('opencore_android_build', ROOT / 'android-remote/build.py')
android_build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(android_build)


class AndroidReleaseBuildTests(unittest.TestCase):
    def test_release_uses_the_explicitly_pinned_android_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            sdk = Path(temporary)
            for version in ('36.0.0', '37.0.0'):
                (sdk / 'build-tools' / version).mkdir(parents=True)
            (sdk / 'platforms/android-36').mkdir(parents=True)
            with patch.dict(os.environ, {'OPENCORE_ANDROID_BUILD_TOOLS': '36.0.0'}):
                self.assertEqual(android_build.sdk_inputs(sdk)[0].name, '36.0.0')
            with patch.dict(os.environ, {'OPENCORE_ANDROID_BUILD_TOOLS': '99.0.0'}):
                with self.assertRaisesRegex(ValueError, 'not installed'):
                    android_build.sdk_inputs(sdk)

    def test_verified_certificate_formats_and_untrusted_signers(self):
        digest = android_build.SIGNER_SHA256
        for signer in ('Signer #1', 'Signer (minSdkVersion=33, maxSdkVersion=2147483647)'):
            self.assertEqual(android_build.verify_signer_output(signer + ' certificate SHA-256 digest: ' + digest + '\n'), digest)
        with self.assertRaisesRegex(ValueError, 'signing identity'):
            android_build.verify_signer_output('Signer #1 certificate SHA-256 digest: ' + '0' * 64 + '\n')
        with self.assertRaisesRegex(ValueError, 'signing identity'):
            android_build.verify_signer_output('Number of signers: 2\nSigner #1 certificate SHA-256 digest: ' + digest + '\n')

    def test_ci_version_codes_increase_above_all_manually_released_builds(self):
        mapping = getattr(android_build, 'ci_version_code', None)
        self.assertTrue(callable(mapping), 'CI must assign Android version codes from its run number')
        self.assertEqual(mapping('294'), 100294)
        self.assertGreater(mapping('1'), 15)
        self.assertEqual(mapping('295'), mapping('294') + 1)
        for invalid in ('', '0', '-1', '1.5', 'true', True, None, '2100000000'):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                mapping(invalid)

    def test_ci_missing_signing_input_cannot_fall_back_to_a_new_local_key(self):
        validate = getattr(android_build, 'signing_inputs', None)
        self.assertTrue(callable(validate), 'Signing must validate the CI route before compiling')
        with self.assertRaisesRegex(ValueError, 'existing.*keystore|keystore.*required'):
            validate(None, None, {'GITHUB_ACTIONS': 'true'})
        with tempfile.TemporaryDirectory() as temporary:
            existing = Path(temporary) / 'original.jks'
            existing.write_bytes(b'original-key-fixture')
            before = existing.read_bytes()
            with self.assertRaisesRegex(ValueError, 'password'):
                validate(existing, 'OPENCORE_ANDROID_STORE_PASSWORD', {'GITHUB_ACTIONS': 'true'})
            self.assertEqual(existing.read_bytes(), before)
            self.assertEqual(set(Path(temporary).iterdir()), {existing})
            self.assertEqual(validate(existing, 'PASS', {'GITHUB_ACTIONS': 'true', 'PASS': 'fixture'}),
                             (existing, 'PASS'))

    def test_build_version_override_preserves_committed_manifest_and_package(self):
        override = getattr(android_build, 'manifest_for_build', None)
        self.assertTrue(callable(override), 'Build overrides must use a generated manifest')
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'source.xml'
            destination = Path(temporary) / 'generated.xml'
            original = (ROOT / 'android-remote/AndroidManifest.xml').read_bytes()
            source.write_bytes(original)
            result = override(source, destination, 100294, '0.3.294')
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(result, destination)
            manifest = ET.parse(destination).getroot()
            namespace = '{http://schemas.android.com/apk/res/android}'
            self.assertEqual(manifest.attrib['package'], 'ai.opencore.remote')
            self.assertEqual(manifest.attrib[namespace + 'versionCode'], '100294')
            self.assertEqual(manifest.attrib[namespace + 'versionName'], '0.3.294')
            self.assertEqual(len(manifest.findall('application/provider')), 1)
            with self.assertRaises(ValueError):
                override(source, destination, 0, '0.3.294')

    def test_workflow_builds_the_apk_after_frontend_generation_before_publication(self):
        workflow = (ROOT / '.github/workflows/build.yml').read_text(encoding='utf-8')
        self.assertIn('python distribution/android_release.py', workflow)
        build_position = workflow.index('python distribution/android_release.py --output')
        self.assertLess(workflow.index('python distribution/prepare.py'), build_position)
        upload = workflow.index('      - name: Retain the verified signed installer for independent publication retries')
        self.assertLess(build_position, upload)
        self.assertLess(upload, workflow.index('  publish:'))
        self.assertIn('actions/upload-artifact@v4', workflow[upload:workflow.index('  publish:')])
        self.assertIn('name: opencore-signed-release-', workflow[upload:workflow.index('  publish:')])
        self.assertIn('OPENCORE_ANDROID_KEYSTORE_BASE64: ${{ secrets.OPENCORE_ANDROID_KEYSTORE_BASE64 }}', workflow)
        self.assertIn('OPENCORE_ANDROID_STORE_PASSWORD: ${{ secrets.OPENCORE_ANDROID_STORE_PASSWORD }}', workflow)
        self.assertIn('OPENCORE_ANDROID_RELEASE_DIR:', workflow[workflow.index('  publish:'):])


class AndroidReleasePublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / 'release'
        self.output.mkdir()
        self.distribution = self.root / 'distribution'
        self.distribution.mkdir()
        (self.root / 'android-remote/shared-ui').mkdir(parents=True)
        (self.root / 'android-remote/src').mkdir()
        (self.root / 'android-remote/src/example.java').write_text('class Example {}\n', encoding='utf-8')
        (self.root / 'src-tauri/icons').mkdir(parents=True)
        (self.root / 'src-tauri/icons/icon.png').write_bytes(b'fixture-icon')
        self.sources = mobile_client.source_hashes(self.root)
        self.apk = self.output / 'OpenCore.Remote.apk'

    def write_apk(self, embedded_sources=None, extra=None):
        with zipfile.ZipFile(self.apk, 'w') as archive:
            archive.writestr('assets/phone/build-sources.json', json.dumps(
                self.sources if embedded_sources is None else embedded_sources))
            for name, data in (extra or {}).items():
                archive.writestr(name, data)
        self.receipt = {
            'schema': 1, 'package': 'ai.opencore.remote', 'versionCode': 100294,
            'version': '0.3.294', 'bytes': self.apk.stat().st_size,
            'sha256': hashlib.sha256(self.apk.read_bytes()).hexdigest(),
            'signatureVerified': True,
            'signerCertificateSha256': '12fba5fd7e17b8bfa6e6b83f00de36e09b4a8f3fa00908d2b447469274a44e5d',
            'sources': self.sources,
        }
        (self.output / 'android-client.json').write_text(json.dumps(self.receipt), encoding='utf-8')
        return self.receipt

    def prepare(self, **environment):
        with patch.object(mobile_client, 'HERE', self.distribution), patch.dict(
                os.environ, {'OPENCORE_ANDROID_RELEASE_DIR': str(self.output), **environment}, clear=True):
            return mobile_client.prepare(self.root / 'publish')

    def test_publication_uses_the_current_ci_apk_instead_of_a_committed_prebuilt(self):
        self.write_apk()
        published = self.prepare()
        self.assertEqual({path.name for path in published}, {'OpenCore.Remote.apk', 'android-client.json'})
        self.assertEqual((self.root / 'publish/OpenCore.Remote.apk').read_bytes(), self.apk.read_bytes())

    def test_changed_apk_and_embedded_source_map_fail_closed(self):
        self.write_apk()
        with self.apk.open('ab') as stream:
            stream.write(b'tampered')
        with self.assertRaisesRegex(ValueError, 'Rebuild|checksum|changed'):
            self.prepare()
        self.write_apk({'android-remote/src/example.java': 'wrong-source'})
        with self.assertRaisesRegex(ValueError, 'sources|source'):
            self.prepare()

    def test_a_different_signer_receipt_cannot_be_published_as_an_update(self):
        receipt = self.write_apk()
        receipt['signerCertificateSha256'] = '0' * 64
        (self.output / 'android-client.json').write_text(json.dumps(receipt), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'signing|signer|identity'):
            self.prepare()

    def test_github_receipt_must_belong_to_the_published_commit_and_release(self):
        receipt = self.write_apk()
        receipt.update(buildOrigin='github-actions', commit='a' * 40)
        (self.output / 'android-client.json').write_text(json.dumps(receipt), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'commit|release'):
            self.prepare(GITHUB_SHA='b' * 40, OPENCORE_RELEASE_VERSION='0.3.294')

    def test_ci_publication_carries_the_signed_phone_interface_for_live_updates(self):
        interface = self.root / 'interface'
        (interface / 'phone').mkdir(parents=True)
        files = {'index.html': b'<html></html>', 'phone/mobile-shim.js': b'// shim', 'phone/mobile.css': b'body{}'}
        for name, data in files.items():
            (interface / name).write_bytes(data)
        manifest = json.dumps({'schema': 1, 'shellApi': 1, 'modes': ['pc', 'phone'], 'files': {
            name: {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'mime': 'text/plain'} for name, data in files.items()}}).encode()
        (interface / 'manifest.json').write_bytes(manifest)
        commit = 'a' * 40

        def ci_release(phone_ui):
            receipt = self.write_apk(extra={'assets/phone/shared-ui/manifest.json': manifest,
                                            **{'assets/phone/shared-ui/' + name: data for name, data in files.items()}})
            receipt.update(buildOrigin='github-actions', commit=commit, **({'phoneUi': phone_ui} if phone_ui else {}))
            (self.output / 'android-client.json').write_text(json.dumps(receipt), encoding='utf-8')
            (self.output / 'phone-runtime-verification.json').write_text(json.dumps({
                'passed': True, 'apkSha256': receipt['sha256'], 'commit': commit, 'versionCode': receipt['versionCode'],
                'physicalPhoneVerified': False}), encoding='utf-8')
            return self.prepare(GITHUB_SHA=commit, OPENCORE_RELEASE_VERSION='0.3.294')

        with self.assertRaisesRegex(ValueError, 'phone interface'):
            ci_release(None)

        def sign(tag, revision):
            return json.dumps({'schema': 1, 'tag': tag, 'revision': revision, 'algorithm': 'SHA256withRSA',
                               'certificateSha256': mobile_client.SIGNER_SHA256, 'signature': 'c2lnbmF0dXJl'})
        record = phone_ui_release.build(interface, self.output, 'app-v0.3.294', sign)
        self.assertEqual(record['revision'], hashlib.sha256(manifest).hexdigest())
        published = ci_release(record)
        self.assertEqual({path.name for path in published}, {'OpenCore.Remote.apk', 'android-client.json',
                                                             'phone-runtime-verification.json', *phone_ui_release.NAMES})
        self.assertFalse(any(path.suffix == '.part' or path.name.endswith('.tmp') for path in self.output.iterdir()))
        with zipfile.ZipFile(self.output / phone_ui_release.ARCHIVE) as archive:
            self.assertEqual({name: archive.read(name) for name in archive.namelist()}, files)

        archive = (self.output / phone_ui_release.ARCHIVE).read_bytes()
        (self.output / phone_ui_release.ARCHIVE).write_bytes(archive[:-1] + bytes([archive[-1] ^ 1]))
        with self.assertRaisesRegex(ValueError, 'phone interface'):
            ci_release(record)
        (self.output / phone_ui_release.ARCHIVE).write_bytes(archive)
        with self.assertRaisesRegex(ValueError, 'phone interface'):
            ci_release({**record, 'tag': 'app-v0.3.293'})
        with self.assertRaisesRegex(ValueError, 'phone interface'):
            ci_release({**record, 'certificateSha256': '0' * 64})

    def test_phone_interface_signing_keeps_the_password_out_of_argv(self):
        calls = []

        def run(command, **options):
            calls.append((command, options))
            return subprocess.CompletedProcess(command, 0, '{}', '')
        with patch.object(phone_ui_release.subprocess, 'run', run):
            phone_ui_release.java_signer('java', 'original.jks', 'OPENCORE_ANDROID_STORE_PASSWORD',
                                         {'OPENCORE_ANDROID_STORE_PASSWORD': 'fixture-secret'})('app-v1.2.3', 'a' * 64)
        command, options = calls[0]
        self.assertNotIn('fixture-secret', ' '.join(map(str, command)))
        self.assertIn('OPENCORE_ANDROID_STORE_PASSWORD', command)
        self.assertEqual(options['env']['OPENCORE_ANDROID_STORE_PASSWORD'], 'fixture-secret')


if __name__ == '__main__':
    unittest.main()
