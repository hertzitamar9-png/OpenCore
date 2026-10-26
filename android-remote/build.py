"""Build the small native Android client with the official Android SDK tools."""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sdk', type=Path, default=os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT'))
    parser.add_argument('--output', type=Path, default=ROOT.parent / 'distribution/.output/android/OpenCore.Remote.apk')
    parser.add_argument('--keys', type=Path, default=Path.home() / '.opencore/distribution-keys')
    args = parser.parse_args()
    sdk = args.sdk
    if not sdk or not sdk.is_dir(): raise ValueError('Pass the installed Android SDK directory with --sdk')
    tools = sorted((sdk / 'build-tools').iterdir(), key=lambda path: tuple(int(p) for p in path.name.split('.') if p.isdigit()))[-1]
    platform = sorted((sdk / 'platforms').iterdir(), key=lambda path: int(path.name.split('-')[-1]))[-1] / 'android.jar'
    java = Path(os.environ.get('JAVA_HOME') or Path(shutil.which('java')).parent.parent)
    build = ROOT.parent / 'distribution/.output/android/build'
    if build.exists():
        # No recursive deletion: use a new, uniquely named build directory.
        build = build.with_name('build-' + secrets.token_hex(5))
    for name in ('classes', 'dex', 'generated', 'res/drawable'): (build / name).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT.parent / 'src-tauri/icons/icon.png', build / 'res/drawable/opencore.png')
    environment = dict(os.environ, JAVA_HOME=str(java))
    def run(command):
        result = subprocess.run([str(value) for value in command], capture_output=True, text=True, env=environment,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RuntimeError(str(command[0]) + ': ' + (result.stderr + result.stdout)[-12000:])
    run([tools/'aapt2.exe', 'compile', '--dir', build/'res', '-o', build/'resources.zip'])
    run([tools/'aapt2.exe', 'link', '-o', build/'resources.apk', '-I', platform, '--manifest', ROOT/'AndroidManifest.xml',
         '--java', build/'generated', build/'resources.zip'])
    run([java/'bin/javac.exe', '-encoding', 'UTF-8', '--release', '8', '-classpath', platform,
         '-d', build/'classes', *ROOT.glob('src/**/*.java'), *build.glob('generated/**/*.java')])
    with zipfile.ZipFile(build/'classes.jar', 'w', zipfile.ZIP_DEFLATED) as archive:
        for file in (build/'classes').rglob('*.class'): archive.write(file, file.relative_to(build/'classes').as_posix())
    run([java/'bin/java.exe', '-cp', tools/'lib/d8.jar', 'com.android.tools.r8.D8', '--min-api', '26', '--lib', platform,
         '--output', build/'dex', build/'classes.jar'])
    shutil.copyfile(build/'resources.apk', build/'unsigned.apk')
    with zipfile.ZipFile(build/'unsigned.apk', 'a', zipfile.ZIP_DEFLATED) as archive:
        for file in (build/'dex').glob('*.dex'): archive.write(file, file.name)
    run([tools/'zipalign.exe', '-f', '4', build/'unsigned.apk', build/'aligned.apk'])
    args.keys.mkdir(parents=True, exist_ok=True)
    keystore, protected = args.keys/'opencore-remote.jks', args.keys/'opencore-remote-password.dpapi'
    sys.path.insert(0, str(ROOT.parent/'src-tauri/resources/runtime-setup/hf-extension'))
    from hf_security import _dpapi
    if keystore.exists() and not protected.exists(): raise ValueError('The signing key password is missing; preserve the existing key')
    if protected.exists(): password = _dpapi(protected.read_bytes(), True).decode()
    else:
        password = secrets.token_urlsafe(48)
        protected.write_bytes(_dpapi(password.encode()))
    environment['OPENCORE_APK_STORE_PASSWORD'] = password
    if not keystore.exists():
        run([java/'bin/keytool.exe', '-genkeypair', '-keystore', keystore, '-storepass:env', 'OPENCORE_APK_STORE_PASSWORD',
             '-keypass:env', 'OPENCORE_APK_STORE_PASSWORD', '-alias', 'opencore-remote', '-keyalg', 'RSA', '-keysize', '3072',
             '-validity', '10000', '-dname', 'CN=OpenCore Remote', '-noprompt'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    run([java/'bin/java.exe', '-jar', tools/'lib/apksigner.jar', 'sign', '--ks', keystore,
         '--ks-key-alias', 'opencore-remote', '--ks-pass', 'env:OPENCORE_APK_STORE_PASSWORD', '--key-pass', 'env:OPENCORE_APK_STORE_PASSWORD',
         '--out', args.output, build/'aligned.apk'])
    run([java/'bin/java.exe', '-jar', tools/'lib/apksigner.jar', 'verify', '--verbose', args.output])
    import hashlib
    print(json.dumps({'apk': str(args.output.resolve()), 'bytes': args.output.stat().st_size,
                      'sha256': hashlib.sha256(args.output.read_bytes()).hexdigest()}))


if __name__ == '__main__': main()
