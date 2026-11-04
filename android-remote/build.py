"""Build the small native Android client with the official Android SDK tools."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parent
PACKAGE = 'ai.opencore.remote'
SIGNER_SHA256 = '12fba5fd7e17b8bfa6e6b83f00de36e09b4a8f3fa00908d2b447469274a44e5d'
ANDROID_VERSION_CODE_LIMIT = 2_100_000_000


def ci_version_code(run_number):
    """Reserve codes above the previous manual builds for this workflow's run number."""
    if isinstance(run_number, bool) or not re.fullmatch(r'[1-9][0-9]*', str(run_number)):
        raise ValueError('GitHub Actions run number must be a positive integer')
    value = 100_000 + int(run_number)
    if value > ANDROID_VERSION_CODE_LIMIT:
        raise ValueError('GitHub Actions run number exceeds the Android versionCode limit')
    return value


def signing_inputs(keystore, password_env, environment):
    """An explicit/CI signing route can only use an existing key, never create one."""
    ci = any(str(environment.get(name, '')).lower() in ('true', '1') for name in ('CI', 'GITHUB_ACTIONS'))
    if keystore is None:
        if ci or password_env:
            raise ValueError('An existing Android signing keystore is required in CI')
        return None
    keystore = Path(keystore)
    if not keystore.is_file():
        raise ValueError('The existing Android signing keystore is missing; no replacement key will be created')
    if not password_env or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', password_env) or not environment.get(password_env):
        raise ValueError('The existing Android signing key password environment variable is missing')
    return keystore, password_env


def manifest_for_build(source, destination, version_code=None, version_name=None):
    """Override only release version fields in a build-local copy of the manifest."""
    tree = ET.parse(source)
    manifest = tree.getroot()
    if manifest.get('package') != PACKAGE:
        raise ValueError('The Android update package must remain ' + PACKAGE)
    namespace = '{http://schemas.android.com/apk/res/android}'
    if version_code is not None:
        if isinstance(version_code, bool) or not re.fullmatch(r'[1-9][0-9]*', str(version_code)) or int(version_code) > ANDROID_VERSION_CODE_LIMIT:
            raise ValueError('Android versionCode must be a positive supported integer')
        manifest.set(namespace + 'versionCode', str(version_code))
    if version_name is not None:
        if not re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,3}', version_name):
            raise ValueError('Android versionName must be a numeric release version')
        manifest.set(namespace + 'versionName', version_name)
    ET.register_namespace('android', namespace[1:-1])
    tree.write(destination, encoding='utf-8', xml_declaration=True)
    return destination


def sdk_inputs(sdk):
    sdk = Path(sdk) if sdk else None
    if not sdk or not sdk.is_dir():
        raise ValueError('Pass the installed Android SDK directory with --sdk')
    candidates = [path for path in (sdk / 'build-tools').iterdir() if re.fullmatch(r'\d+\.\d+\.\d+', path.name)]
    if not candidates:
        raise ValueError('Install released Android SDK build tools')
    requested = os.environ.get('OPENCORE_ANDROID_BUILD_TOOLS')
    if requested:
        if not re.fullmatch(r'\d+\.\d+\.\d+', requested):
            raise ValueError('Invalid Android build tools version')
        tools = next((path for path in candidates if path.name == requested), None)
        if tools is None:
            raise ValueError('The requested Android build tools are not installed: ' + requested)
    else:
        tools = max(candidates, key=lambda path: tuple(map(int, path.name.split('.'))))
    platforms = [(tuple(int(value or 0) for value in match.groups()), path) for path in (sdk / 'platforms').iterdir()
                 if (match := re.fullmatch(r'android-(\d+)(?:-ext(\d+))?', path.name))]
    if not platforms:
        raise ValueError('Install a released Android SDK platform')
    return tools, max(platforms, key=lambda row: row[0])[1] / 'android.jar'


def java_home():
    executable = shutil.which('java')
    if not os.environ.get('JAVA_HOME') and not executable:
        raise ValueError('JDK 21 is required for the bundled LiteRT SDK')
    return Path(os.environ.get('JAVA_HOME') or Path(executable).parent.parent)


def run_tool(command, environment):
    result = subprocess.run([str(value) for value in command], capture_output=True, text=True, env=environment,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError(str(command[0]) + ': ' + (result.stderr + result.stdout)[-12000:])
    return result.stdout


def verify_signer_output(certificates):
    # Current apksigner versions may label a signer by SDK range rather than
    # by number. Every reported certificate must still be the original key.
    signers = re.findall(r'^Signer\s+(?:#\d+|\([^\r\n]*\))\s+certificate SHA-256 digest:\s*([0-9a-fA-F]{64})\s*$', certificates, re.MULTILINE)
    count = re.search(r'^Number of signers:\s*(\d+)\s*$', certificates, re.MULTILINE)
    if not signers or {digest.lower() for digest in signers} != {SIGNER_SHA256} or count and int(count.group(1)) != 1:
        details = [line.strip() for line in certificates.splitlines() if 'Signer ' in line or 'signers:' in line]
        raise ValueError('The APK signing identity differs from the original installed Android app: ' + '; '.join(details[:8]))
    return SIGNER_SHA256


def verify_apk(apk, tools, java, environment):
    """Use Android's actual signature verifier and binary manifest parser."""
    certificates = run_tool([java / 'bin/java.exe', '-jar', tools / 'lib/apksigner.jar', 'verify',
                             '--verbose', '--print-certs', apk], environment)
    signer = verify_signer_output(certificates)
    badging = run_tool([tools / 'aapt2.exe', 'dump', 'badging', apk], environment)
    package = re.search(r"^package: name='([^']+)' versionCode='([0-9]+)' versionName='([^']+)'", badging, re.MULTILINE)
    if not package or package[1] != PACKAGE:
        raise ValueError('The built APK changed the installed Android package identity')
    return {'package': package[1], 'versionCode': int(package[2]), 'version': package[3],
            'signatureVerified': True, 'signerCertificateSha256': signer}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sdk', type=Path, default=os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT'))
    parser.add_argument('--output', type=Path, default=ROOT.parent / 'distribution/.output/android/OpenCore.Remote.apk')
    parser.add_argument('--keys', type=Path, default=Path.home() / '.opencore/distribution-keys')
    parser.add_argument('--keystore', type=Path, help='Use this existing signing key; never create a replacement')
    parser.add_argument('--store-password-env', help='Name of the environment variable containing its password')
    parser.add_argument('--version-code', type=int)
    parser.add_argument('--version-name')
    args = parser.parse_args()
    signing = signing_inputs(args.keystore, args.store_password_env, os.environ)
    tools, platform = sdk_inputs(args.sdk)
    java = java_home()
    environment = dict(os.environ, JAVA_HOME=str(java))
    java_version = subprocess.run([str(java/'bin/javac.exe'), '-version'], capture_output=True, text=True, env=environment)
    match = re.search(r'javac (\d+)', java_version.stdout + java_version.stderr)
    if java_version.returncode or not match or int(match[1]) < 21:
        raise ValueError('JDK 21 or newer is required for the pinned LiteRT SDK')
    build = ROOT.parent / 'distribution/.output/android/build'
    zxing = ROOT / 'vendor/zxing-core-3.5.3.jar'
    from litert_sdk import inputs as litert_inputs, resources as litert_resources
    litert_vendor, litert_manifest, litert_jars = litert_inputs()
    if not zxing.is_file() or hashlib.sha256(zxing.read_bytes()).hexdigest() != '8d8064c1636fdaef7189dd9055c7d59950a8940a12f2293956446ec3c109fd82':
        raise ValueError('The pinned QR scanner dependency is missing or changed')
    if build.exists():
        # No recursive deletion: use a new, uniquely named build directory.
        build = build.with_name('build-' + secrets.token_hex(5))
    for name in ('classes', 'dex', 'generated', 'res/drawable'): (build / name).mkdir(parents=True, exist_ok=True)
    manifest = manifest_for_build(ROOT/'AndroidManifest.xml', build/'AndroidManifest.xml', args.version_code, args.version_name)
    if (ROOT/'res').is_dir(): shutil.copytree(ROOT/'res', build/'res', dirs_exist_ok=True)
    shutil.copyfile(ROOT.parent / 'src-tauri/icons/icon.png', build / 'res/drawable/opencore.png')
    shutil.copytree(ROOT/'assets', build/'assets/phone')
    shared=ROOT/'shared-ui'
    shared_manifest=json.loads((shared/'manifest.json').read_text(encoding='utf-8'))
    for relative,record in shared_manifest['files'].items():
        source=shared/relative
        if not source.is_file() or source.stat().st_size!=record['bytes'] or hashlib.sha256(source.read_bytes()).hexdigest()!=record['sha256']:
            raise ValueError('The bundled desktop UI is missing or changed; regenerate it: '+relative)
    shutil.copytree(shared,build/'assets/phone/shared-ui')
    shutil.copyfile(ROOT.parent/'public/opencore-logo.png',build/'assets/phone/opencore-logo.png')
    from phone_catalog import build as build_phone_catalog
    resources=ROOT.parent/'src-tauri/resources'
    phone_catalog=build_phone_catalog(json.loads((resources/'model-catalog.json').read_text(encoding='utf-8')),
                                     json.loads((resources/'runtime-setup/hf-extension/publisher-models.json').read_text(encoding='utf-8')))
    (build/'assets/phone/catalog.json').write_text(json.dumps(phone_catalog,separators=(',',':'))+'\n',encoding='utf-8')
    sys.path.insert(0,str(ROOT.parent/'distribution'))
    from mobile_client import source_hashes
    (build/'assets/phone/build-sources.json').write_text(json.dumps(source_hashes(ROOT.parent),sort_keys=True)+'\n',encoding='utf-8')
    runtime=json.loads((ROOT/'native/runtime.json').read_text(encoding='utf-8'))
    native_receipt=json.loads((ROOT/'native/binaries.json').read_text(encoding='utf-8'))
    if native_receipt['revision'] != runtime['revision']:raise ValueError('Native Android runtime source pin changed; rebuild it')
    for relative,sha in native_receipt['sources'].items():
        if hashlib.sha256((ROOT/'native'/relative).read_bytes().replace(b'\r\n',b'\n')).hexdigest()!=sha:raise ValueError('Android runtime code changed; rebuild it: '+relative)
    for relative,record in native_receipt['files'].items():
        native=ROOT/'native'/relative
        if not native.is_file() or native.stat().st_size!=record['bytes'] or hashlib.sha256(native.read_bytes()).hexdigest()!=record['sha256']:
            raise ValueError('Missing or unverified bundled Android inference library: '+relative)
    (build/'assets/phone/runtime.json').write_text(json.dumps(runtime,indent=2)+'\n',encoding='utf-8')
    shutil.copyfile(ROOT/'vendor/LLAMA-LICENSE.txt',build/'assets/phone/LLAMA-LICENSE.txt')
    for name in ('LICENSE', 'THIRD_PARTY_NOTICE.txt', 'manifest.json'):
        folder=build/'assets/phone/litertlm';folder.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(litert_vendor/name,folder/name)
    def run(command):
        return run_tool(command, environment)
    run([tools/'aapt2.exe', 'compile', '--dir', build/'res', '-o', build/'resources.zip'])
    run([tools/'aapt2.exe', 'link', '-o', build/'resources.apk', '-I', platform, '--manifest', manifest, '-A', build/'assets',
         '--java', build/'generated', build/'resources.zip'])
    run([java/'bin/javac.exe', '-encoding', 'UTF-8', '--release', '8', '-classpath', os.pathsep.join(map(str,[platform,zxing,*litert_jars])),
         '-d', build/'classes', *ROOT.glob('src/**/*.java'), *build.glob('generated/**/*.java')])
    with zipfile.ZipFile(build/'classes.jar', 'w', zipfile.ZIP_DEFLATED) as archive:
        for file in (build/'classes').rglob('*.class'): archive.write(file, file.relative_to(build/'classes').as_posix())
    run([java/'bin/java.exe', '-cp', tools/'lib/d8.jar', 'com.android.tools.r8.D8', '--min-api', '26', '--lib', platform,
         '--output', build/'dex', build/'classes.jar', zxing, *litert_jars])
    shutil.copyfile(build/'resources.apk', build/'unsigned.apk')
    with zipfile.ZipFile(build/'unsigned.apk', 'a', zipfile.ZIP_DEFLATED) as archive:
        for file in (build/'dex').glob('*.dex'): archive.write(file, file.name)
        for relative in native_receipt['files']:archive.write(ROOT/'native'/relative,'lib/'+relative)
        archive.write(litert_vendor/'liblitertlm_jni.so','lib/arm64-v8a/liblitertlm_jni.so')
        litert_resources(archive,litert_jars)
    run([tools/'zipalign.exe', '-f', '4', build/'unsigned.apk', build/'aligned.apk'])
    if signing:
        keystore, password_env = signing
    else:
        # Preserve the original Windows/DPAPI route for local builds. CI cannot
        # enter this branch, even if an empty keys directory happens to exist.
        args.keys.mkdir(parents=True, exist_ok=True)
        keystore, protected = args.keys/'opencore-remote.jks', args.keys/'opencore-remote-password.dpapi'
        sys.path.insert(0, str(ROOT.parent/'src-tauri/resources/runtime-setup/hf-extension'))
        from hf_security import _dpapi
        if keystore.exists() and not protected.exists(): raise ValueError('The signing key password is missing; preserve the existing key')
        if protected.exists(): password = _dpapi(protected.read_bytes(), True).decode()
        else:
            password = secrets.token_urlsafe(48)
            protected.write_bytes(_dpapi(password.encode()))
        password_env = 'OPENCORE_APK_STORE_PASSWORD'
        environment[password_env] = password
        if not keystore.exists():
            run([java/'bin/keytool.exe', '-genkeypair', '-keystore', keystore, '-storepass:env', password_env,
                 '-keypass:env', password_env, '-alias', 'opencore-remote', '-keyalg', 'RSA', '-keysize', '3072',
                 '-validity', '10000', '-dname', 'CN=OpenCore Remote', '-noprompt'])
    certificate = build/'signing-certificate.der'
    run([java/'bin/keytool.exe', '-exportcert', '-keystore', keystore, '-storepass:env', password_env,
         '-alias', 'opencore-remote', '-file', certificate])
    if hashlib.sha256(certificate.read_bytes()).hexdigest() != SIGNER_SHA256:
        raise ValueError('Preserve the original Android signing key; this key cannot update installed OpenCore apps')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    run([java/'bin/java.exe', '-jar', tools/'lib/apksigner.jar', 'sign', '--ks', keystore,
         '--ks-key-alias', 'opencore-remote', '--ks-pass', 'env:'+password_env, '--key-pass', 'env:'+password_env,
         '--out', args.output, build/'aligned.apk'])
    identity = verify_apk(args.output, tools, java, environment)
    if args.version_code is not None and identity['versionCode'] != args.version_code or args.version_name is not None and identity['version'] != args.version_name:
        raise ValueError('The signed APK does not have the requested release version')
    print(json.dumps({'apk': str(args.output.resolve()), 'bytes': args.output.stat().st_size,
                      'sha256': hashlib.sha256(args.output.read_bytes()).hexdigest(), **identity}))


if __name__ == '__main__': main()
