"""Publish the APK verified for this release, preserving the installed Android key."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import zipfile

from phone_ui_release import validate as validate_phone_ui

HERE = Path(__file__).resolve().parent
PACKAGE = 'ai.opencore.remote'
SIGNER_SHA256 = '12fba5fd7e17b8bfa6e6b83f00de36e09b4a8f3fa00908d2b447469274a44e5d'
SHARED_UI_PREFIX = 'android-remote/shared-ui/'


def source_digest(path, data):
    if Path(path).suffix not in ('.png', '.jar', '.so'):
        data = data.replace(b'\r\n', b'\n')
    return hashlib.sha256(data).hexdigest()


def source_hashes(root):
    files = sorted(path for path in (root / 'android-remote').rglob('*') if path.is_file()
                   and '__pycache__' not in path.parts and path.suffix in ('.java', '.py', '.xml', '.md', '.jar', '.txt', '.js', '.css', '.html', '.cpp', '.so', '.json'))
    files += [root / name for name in ('src-tauri/icons/icon.png', 'public/opencore-logo.png',
              'src-tauri/resources/model-catalog.json',
              'src-tauri/resources/runtime-setup/hf-extension/publisher-models.json',
              'distribution/mobile_ui.py', 'distribution/mobile_client.py', 'distribution/android_release.py')
              if (root / name).is_file()]
    return {path.relative_to(root).as_posix(): source_digest(path, path.read_bytes()) for path in files}


def validate(pinned, root=None, environment=None):
    root = Path(root or HERE.parent)
    pinned = Path(pinned)
    environment = os.environ if environment is None else environment
    try:
        receipt = json.loads((pinned / 'android-client.json').read_text(encoding='utf-8'))
        apk = pinned / 'OpenCore.Remote.apk'
        if (receipt.get('schema') != 1 or receipt.get('package') != PACKAGE or
                receipt.get('signatureVerified') is not True or receipt.get('signerCertificateSha256') != SIGNER_SHA256):
            raise ValueError('The Android update must preserve the original package and signing identity')
        if (not 0 < receipt.get('versionCode', 0) <= 2_100_000_000 or
                not 0 < receipt.get('bytes', 0) <= 256 * 1024 * 1024 or
                apk.stat().st_size != receipt['bytes'] or hashlib.sha256(apk.read_bytes()).hexdigest() != receipt['sha256']):
            raise ValueError('Rebuild and verify the Android client: its APK checksum or size changed')
        if receipt.get('buildOrigin') == 'github-actions':
            if (not receipt.get('commit') or environment.get('GITHUB_SHA') and receipt['commit'] != environment['GITHUB_SHA'] or
                    environment.get('OPENCORE_RELEASE_VERSION') and receipt['version'] != environment['OPENCORE_RELEASE_VERSION']):
                raise ValueError('The CI Android APK belongs to a different commit or release')
        elif str(environment.get('GITHUB_ACTIONS', '')).lower() == 'true':
            raise ValueError('GitHub publication requires an Android APK built by this workflow')
        with zipfile.ZipFile(apk) as packaged:
            if json.loads(packaged.read('assets/phone/build-sources.json')) != receipt['sources']:
                raise ValueError('The signed APK was not built from these reviewed Android sources')
            current = source_hashes(root)
            if receipt.get('buildOrigin') == 'github-actions':
                # The separate publish job checks out committed UI files. Its
                # APK contains the final candidate-dist generated in Windows.
                # Compare code/catalog inputs to the checkout and generated UI
                # inputs to the actual bytes in that same signed APK.
                current = {name: digest for name, digest in current.items() if not name.startswith(SHARED_UI_PREFIX)}
                for name in receipt['sources']:
                    if name.startswith(SHARED_UI_PREFIX):
                        relative = name[len(SHARED_UI_PREFIX):]
                        current[name] = source_digest(name, packaged.read('assets/phone/shared-ui/' + relative))
                manifest = json.loads(packaged.read('assets/phone/shared-ui/manifest.json'))
                for relative, record in manifest['files'].items():
                    data = packaged.read('assets/phone/shared-ui/' + relative)
                    if len(data) != record['bytes'] or hashlib.sha256(data).hexdigest() != record['sha256']:
                        raise ValueError('The generated Android shared interface changed')
                verification = json.loads((pinned / 'phone-runtime-verification.json').read_text(encoding='utf-8'))
                if (verification.get('passed') is not True or verification.get('apkSha256') != receipt['sha256'] or
                        verification.get('commit') != receipt['commit'] or verification.get('versionCode') != receipt['versionCode'] or
                        verification.get('physicalPhoneVerified') is not False):
                    raise ValueError('The generated Android APK requires matching runtime packaging verification')
            if current != receipt['sources']:
                raise ValueError('Rebuild and verify the signed Android client after changing its sources')
            # Every CI release also publishes its interface, signed by this APK's key, for live phone updates.
            if receipt.get('buildOrigin') == 'github-actions' and 'phoneUi' not in receipt:
                raise ValueError('A CI Android release must publish its signed phone interface')
            if 'phoneUi' in receipt:
                bundled = hashlib.sha256(packaged.read('assets/phone/shared-ui/manifest.json')).hexdigest()
                validate_phone_ui(pinned, receipt['phoneUi'], 'app-v' + receipt['version'], bundled, SIGNER_SHA256)
        return receipt
    except (OSError, KeyError, TypeError, zipfile.BadZipFile) as error:
        raise ValueError('The verified Android release is missing or incomplete; rebuild this release') from error


def prepare(folder):
    selected = os.environ.get('OPENCORE_ANDROID_RELEASE_DIR')
    if str(os.environ.get('GITHUB_ACTIONS', '')).lower() == 'true' and not selected:
        raise ValueError('GitHub publication requires OPENCORE_ANDROID_RELEASE_DIR; refusing a stale prebuilt APK')
    pinned = Path(selected) if selected else HERE / 'mobile'
    receipt = validate(pinned)
    apk = pinned / 'OpenCore.Remote.apk'
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    result = []
    sources = [apk, pinned / 'android-client.json']
    if receipt.get('buildOrigin') == 'github-actions':
        sources.append(pinned / 'phone-runtime-verification.json')
    if 'phoneUi' in receipt:
        sources += [pinned / name for name in receipt['phoneUi']['files']]
    for source in sources:
        target = folder / source.name
        if source.resolve() != target.resolve(): shutil.copyfile(source, target)
        result.append(target)
    return result
