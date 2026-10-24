"""Build the small Windows downloader with a pinned, embedded signature verifier."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request
import zipfile

HERE = Path(__file__).resolve().parent


def build(output, version, compiler=None):
    if not re.fullmatch(r'\d+\.\d+\.\d+', version) or any(int(part) > 65535 for part in version.split('.')):
        raise ValueError('Invalid Windows bootstrap version')
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    pin = json.loads((HERE / 'bootstrap/minisign-pin.json').read_text())
    tools = output / 'bootstrap-build'
    tools.mkdir(exist_ok=True)
    archive = tools / 'minisign.zip'
    if not archive.exists():
        with urllib.request.urlopen(pin['url'], timeout=60) as response:
            archive.write_bytes(response.read(2_000_000))
    if hashlib.sha256(archive.read_bytes()).hexdigest() != pin['sha256']:
        raise ValueError('The pinned signature-verifier archive changed')
    with zipfile.ZipFile(archive) as package:
        verifier = package.read(pin['member'])
    if hashlib.sha256(verifier).hexdigest() != pin['exeSha256']:
        raise ValueError('The embedded signature verifier changed')
    verifier_path = tools / 'minisign.exe'
    verifier_path.write_bytes(verifier)
    notices = tools / 'ThirdPartyNotices.txt'
    notices.write_text('\n\n'.join(name + '\n' + (HERE / 'bootstrap/licenses' / (name + '.txt')).read_text()
                                  for name in ('minisign', 'libsodium')), encoding='utf-8')
    config = json.loads((HERE.parent / 'src-tauri/tauri.conf.json').read_text())
    key = base64.b64decode(config['plugins']['updater']['pubkey']).decode().splitlines()[1]
    source = (HERE / 'bootstrap/OpenCoreSetup.cs').read_text(encoding='utf-8')
    for token, value in [('VERSION', version), ('PUBLIC_KEY', key), ('VERIFIER_HASH', pin['exeSha256'])]:
        marker = '@@' + token + '@@'
        if source.count(marker) != 1:
            raise ValueError('Unexpected bootstrap template: ' + marker)
        source = source.replace(marker, value)
    generated = tools / 'OpenCoreSetup.cs'
    generated.write_text(source, encoding='utf-8-sig')
    compiler = compiler or Path(os.environ['SystemRoot']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    target = output / 'OpenCore Setup.exe'
    subprocess.run([str(compiler), '/nologo', '/target:winexe', '/platform:x86', '/optimize+', '/debug-',
                    '/out:' + str(target), '/win32icon:' + str(HERE.parent / 'src-tauri/icons/icon.ico'),
                    '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
                    '/reference:System.Web.Extensions.dll',
                    '/resource:' + str(verifier_path) + ',OpenCore.Minisign.exe',
                    '/resource:' + str(HERE.parent / 'src-tauri/icons/icon.png') + ',OpenCore.Logo.png',
                    '/resource:' + str(notices) + ',OpenCore.Notices.txt',
                    str(generated)], check=True)
    if target.stat().st_size > 2_000_000:
        raise ValueError('OpenCore Setup must not exceed two megabytes')
    result = {'passed': True, 'bytes': target.stat().st_size, 'maximumBytes': 2_000_000,
              'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'embeddedVerifierPinned': True,
              'packageSignatureRequired': True, 'version': version}
    (output / 'bootstrap-verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    build(args.output, args.version)
