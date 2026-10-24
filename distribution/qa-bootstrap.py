"""Exercise the small EXE against a real signed package without installing it."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--exe', type=Path, required=True)
parser.add_argument('--package', type=Path, required=True)
parser.add_argument('--signature', type=Path, required=True)
args = parser.parse_args()
exe, package, signature = [p.resolve(strict=True) for p in (args.exe, args.package, args.signature)]
sha = hashlib.sha256()
with package.open('rb') as source:
    for block in iter(lambda: source.read(1024 * 1024), b''):
        sha.update(block)
digest = sha.hexdigest()
def verify(sig, expected):
    return subprocess.run([str(exe), '--verify-only', str(package), str(sig), expected],
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=120).returncode
if verify(signature, digest) != 0:
    raise RuntimeError('The bootstrap did not accept the genuine signed package')
with tempfile.TemporaryDirectory(prefix='opencore-bootstrap-check-') as temporary:
    bad = Path(temporary) / 'invalid.sig'
    data = bytearray(base64.b64decode(signature.read_text().strip()))
    # Change a byte in the cryptographic signature, preserving framing and Base64 syntax.
    lines = bytes(data).decode('utf-8').splitlines()
    detached = bytearray(base64.b64decode(lines[1])); detached[-1] ^= 1
    lines[1] = base64.b64encode(detached).decode()
    bad.write_text(base64.b64encode(('\n'.join(lines) + '\n').encode()).decode())
    if verify(bad, digest) == 0:
        raise RuntimeError('The bootstrap accepted a damaged signature')
if verify(signature, '0' * 64) == 0:
    raise RuntimeError('The bootstrap accepted an incorrect checksum')
result_path = exe.parent / 'bootstrap-verification.json'
result = json.loads(result_path.read_text())
result.update(signatureVerificationPassed=True, tamperedSignatureRejected=True, tamperedHashRejected=True)
result_path.write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result))
