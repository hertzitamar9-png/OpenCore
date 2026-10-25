"""Reuse the exact signed setup file for every app release; never rebuild it."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
SOURCE = HERE / 'bootstrap/permanent'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load_pin():
    return json.loads((SOURCE / 'pin.json').read_text(encoding='utf-8'))


def validate_files(folder, pin):
    setup = folder / 'OpenCore Setup.exe'
    signature = folder / 'OpenCore Setup.exe.sig'
    if (not setup.is_file() or not signature.is_file()
            or setup.stat().st_size != pin['bytes'] or not 0 < pin['bytes'] <= 2_000_000
            or digest(setup) != pin['sha256'] or digest(signature) != pin['signatureSha256']):
        raise ValueError('The permanent OpenCore Setup file or its signature changed')
    return setup


def prepare(output, app_version, *, source=SOURCE):
    if not re.fullmatch(r'\d+\.\d+\.\d+', app_version):
        raise ValueError('Invalid app release version')
    output, source = output.resolve(), source.resolve()
    if output == SOURCE.resolve():
        raise ValueError('The permanent setup source cannot be used as build output')
    pin = load_pin()
    setup = validate_files(source, pin)
    subprocess.run(['node', str(HERE / 'verify-signature.cjs'), str(setup), str(setup) + '.sig',
                    str(HERE.parent / 'src-tauri/tauri.conf.json')], check=True)
    output.mkdir(parents=True, exist_ok=True)
    for name in ('OpenCore Setup.exe', 'OpenCore Setup.exe.sig'):
        shutil.copy2(source / name, output / name)
    validate_files(output, pin)
    result = dict(passed=True, permanent=True, bootstrapVersion=pin['bootstrapVersion'],
                  verifiedForAppVersion=app_version, bytes=pin['bytes'], maximumBytes=2_000_000,
                  sha256=pin['sha256'], signatureSha256=pin['signatureSha256'],
                  embeddedVerifierPinned=True, packageSignatureRequired=True)
    (output / 'bootstrap-verification.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))
    return output / 'OpenCore Setup.exe'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--app-version', required=True)
    args = parser.parse_args()
    prepare(args.output, args.app_version)
