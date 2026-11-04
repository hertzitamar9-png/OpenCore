"""Build this workflow's Android APK with the original signing identity.

Signing material is supplied only to this process through GitHub secrets, is
written to a temporary private file, and is never included in release output.
There is no key-generation fallback in this CI entry point.
"""
import argparse
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import zipfile

from mobile_client import source_hashes, validate
from mobile_ui import prepare as prepare_mobile_ui

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / 'android-remote'))
from build import ci_version_code, java_home, sdk_inputs, verify_apk
from litert_sdk import inputs as litert_inputs
from phone_catalog import build as build_phone_catalog

PASSWORD_ENV = 'OPENCORE_ANDROID_STORE_PASSWORD'
KEYSTORE_ENV = 'OPENCORE_ANDROID_KEYSTORE_BASE64'


def signing_secret_inputs(environment):
    encoded, password = environment.get(KEYSTORE_ENV), environment.get(PASSWORD_ENV)
    if not encoded or not password:
        raise ValueError('Both OPENCORE_ANDROID_KEYSTORE_BASE64 and OPENCORE_ANDROID_STORE_PASSWORD are required; no replacement Android key will be created')
    if len(encoded) > 2 * 1024 * 1024 or '\x00' in password:
        raise ValueError('Invalid Android signing secret input')
    try:
        keystore = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError('OPENCORE_ANDROID_KEYSTORE_BASE64 must encode the existing Android keystore') from None
    if not keystore:
        raise ValueError('The existing Android signing keystore is empty')
    return keystore


def verify_android_elf(data):
    if len(data) < 64 or data[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', data, 18)[0] != 183:
        raise ValueError('The bundled Android inference library must be an ARM64 ELF')
    offset = struct.unpack_from('<Q', data, 32)[0]
    size, count = struct.unpack_from('<HH', data, 54)
    if size < 56 or offset + size * count > len(data):
        raise ValueError('Invalid Android ELF program headers')
    loads = [struct.unpack_from('<IIQQQQQQ', data, offset + i * size) for i in range(count)
             if struct.unpack_from('<I', data, offset + i * size)[0] == 1]
    if not loads or not all(row[-1] >= 16384 and row[2] % 16384 == row[3] % 16384 for row in loads):
        raise ValueError('The bundled Android inference library does not support 16 KB pages')


def verify_packaged_inputs(apk, root, sources):
    """Check generated UI/catalog and both actual shipped native engines."""
    phone = root / 'android-remote'
    runtime = json.loads((phone / 'native/runtime.json').read_text(encoding='utf-8'))
    native = json.loads((phone / 'native/binaries.json').read_text(encoding='utf-8'))
    vendor, litert, _ = litert_inputs()
    catalog = build_phone_catalog(
        json.loads((root / 'src-tauri/resources/model-catalog.json').read_text(encoding='utf-8')),
        json.loads((root / 'src-tauri/resources/runtime-setup/hf-extension/publisher-models.json').read_text(encoding='utf-8')))
    with zipfile.ZipFile(apk) as archive:
        if json.loads(archive.read('assets/phone/build-sources.json')) != sources:
            raise ValueError('The built APK embeds a different source map')
        shared_data = archive.read('assets/phone/shared-ui/manifest.json')
        shared = json.loads(shared_data)
        if shared != json.loads((phone / 'shared-ui/manifest.json').read_text(encoding='utf-8')):
            raise ValueError('The built APK does not contain the current generated shared interface')
        for relative, record in shared['files'].items():
            data = archive.read('assets/phone/shared-ui/' + relative)
            if (data != (phone / 'shared-ui' / relative).read_bytes() or len(data) != record['bytes'] or
                    hashlib.sha256(data).hexdigest() != record['sha256']):
                raise ValueError('The built APK shared interface failed hash verification: ' + relative)
        catalog_data = archive.read('assets/phone/catalog.json')
        if json.loads(catalog_data) != catalog:
            raise ValueError('The built APK catalog does not match the current phone model inputs')
        if native['revision'] != runtime['revision'] or json.loads(archive.read('assets/phone/runtime.json')) != runtime:
            raise ValueError('The built APK has a stale native runtime pin')
        for relative, record in native['files'].items():
            data = archive.read('lib/' + relative)
            if len(data) != record['bytes'] or hashlib.sha256(data).hexdigest() != record['sha256']:
                raise ValueError('The packaged Android native library failed hash verification')
            verify_android_elf(data)
        data = archive.read('lib/arm64-v8a/liblitertlm_jni.so')
        record = litert['files']['liblitertlm_jni.so']
        if len(data) != record['bytes'] or hashlib.sha256(data).hexdigest() != record['sha256']:
            raise ValueError('The packaged official LiteRT library failed hash verification')
        verify_android_elf(data)
        if archive.read('assets/phone/litertlm/manifest.json') != (vendor / 'manifest.json').read_bytes():
            raise ValueError('The packaged official LiteRT input manifest changed')
    return {'nativeRuntimeRevision': runtime['revision'],
            'sharedUiManifestSha256': hashlib.sha256(shared_data).hexdigest(),
            'catalogSha256': hashlib.sha256(catalog_data).hexdigest()}


def build_release(args, environment):
    key_bytes = signing_secret_inputs(environment)
    version_code = ci_version_code(args.run_number)
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,3}', args.version or '') or not re.fullmatch(r'[0-9a-f]{40}', args.commit or ''):
        raise ValueError('A CI Android build requires the exact release version and full source commit')
    prepare_mobile_ui(ROOT / 'android-remote/shared-ui', source=args.source)
    sources = source_hashes(ROOT)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for name in ('android-client.json', 'phone-runtime-verification.json'):
        (output / name).unlink(missing_ok=True)
    apk = output / 'OpenCore.Remote.apk'
    # Children need the password variable, but not a second copy of the base64
    # key. No password is passed in argv, diagnostic output, or JSON receipts.
    child_environment = dict(environment)
    child_environment.pop(KEYSTORE_ENV, None)
    with tempfile.TemporaryDirectory(prefix='opencore-android-signing-') as directory:
        keystore = Path(directory) / 'original.jks'
        descriptor = os.open(keystore, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(key_bytes)
        del key_bytes
        result = subprocess.run([sys.executable, str(ROOT / 'android-remote/build.py'), '--sdk', str(args.sdk),
            '--output', str(apk), '--keystore', str(keystore), '--store-password-env', PASSWORD_ENV,
            '--version-code', str(version_code), '--version-name', args.version],
            env=child_environment, capture_output=True, text=True, encoding='utf-8', errors='replace')
        if result.returncode:
            raise RuntimeError('Android release build failed: ' + (result.stdout + result.stderr)[-16000:])
    tools, _ = sdk_inputs(args.sdk)
    identity = verify_apk(apk, tools, java_home(), child_environment)
    if identity['versionCode'] != version_code or identity['version'] != args.version:
        raise ValueError('The verified Android APK has a different release version')
    packaged = verify_packaged_inputs(apk, ROOT, sources)
    digest = hashlib.sha256(apk.read_bytes()).hexdigest()
    receipt = {'schema': 1, **identity, 'bytes': apk.stat().st_size, 'sha256': digest,
               'sources': sources, 'buildOrigin': 'github-actions', 'commit': args.commit,
               'githubRunNumber': int(args.run_number), 'releaseTag': 'app-v' + args.version,
               'sharedUiManifestSha256': packaged['sharedUiManifestSha256'], 'catalogSha256': packaged['catalogSha256']}
    verification = {'schema': 1, 'passed': True, 'verificationKind': 'signed-APK-packaging',
        'androidVersion': args.version, 'versionCode': version_code, 'commit': args.commit,
        'apkSha256': digest, **packaged,
        'androidArm64BinaryVerified': True, 'android16KbPagesVerified': True,
        'apkSigningIdentityPreserved': True, 'packagedSourcesMatchReviewedSources': True,
        'sharedDesktopInterface': True, 'generatedCatalogMatchesInputs': True,
        'physicalPhoneVerified': False,
        'limitations': ['Build and packaging checks do not establish physical Android gameplay or inference performance',
                        'Android requires the user to approve installation of each app update']}
    for name, value in (('android-client.json', receipt), ('phone-runtime-verification.json', verification)):
        (output / name).write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    validate(output, root=ROOT, environment=environment)
    return {'apk': str(apk), 'version': args.version, 'versionCode': version_code, 'sha256': digest, 'verified': True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-signing', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--source', type=Path, default=HERE / 'candidate-dist')
    parser.add_argument('--sdk', type=Path, default=os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT'))
    parser.add_argument('--run-number', default=os.environ.get('GITHUB_RUN_NUMBER'))
    parser.add_argument('--version', default=os.environ.get('OPENCORE_RELEASE_VERSION'))
    parser.add_argument('--commit', default=os.environ.get('GITHUB_SHA'))
    args = parser.parse_args()
    if args.check_signing:
        signing_secret_inputs(os.environ)
        print(json.dumps({'signingInputsPresent': True}))
        return
    if not args.output or not args.sdk:
        parser.error('--output and an installed Android SDK are required')
    print(json.dumps(build_release(args, os.environ)))


if __name__ == '__main__':
    main()
