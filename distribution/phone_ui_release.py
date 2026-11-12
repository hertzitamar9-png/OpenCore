"""Publish each release's phone interface so installed Android apps update live.

The installed Android app is a permanent shell. Every release carries the same
desktop interface with its phone layer as three assets. The app checks the
signature against its own signing certificate, then installs only the
hash-listed web files, exactly like a PC update but without a new APK.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

HERE = Path(__file__).resolve().parent
MANIFEST, SIGNATURE, ARCHIVE = 'OpenCore.Phone.UI.json', 'OpenCore.Phone.UI.sig', 'OpenCore.Phone.UI.zip'
NAMES = (MANIFEST, SIGNATURE, ARCHIVE)
FIXED_TIME = (2020, 1, 1, 0, 0, 0)


def file_record(path):
    data = Path(path).read_bytes()
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def build(shared_ui, output, tag, sign):
    """Write the three release assets; ``sign(tag, revision)`` returns the signature JSON."""
    shared_ui, output = Path(shared_ui), Path(output)
    raw = (shared_ui / 'manifest.json').read_bytes()
    manifest = json.loads(raw)
    revision = hashlib.sha256(raw).hexdigest()
    for name in NAMES:
        (output / name).unlink(missing_ok=True)
    archive = output / (ARCHIVE + '.tmp')
    with zipfile.ZipFile(archive, 'w') as bundle:
        for relative, record in sorted(manifest['files'].items()):
            data = (shared_ui / relative).read_bytes()
            if len(data) != record['bytes'] or hashlib.sha256(data).hexdigest() != record['sha256']:
                raise ValueError('The phone interface changed after its manifest was written: ' + relative)
            info = zipfile.ZipInfo(relative, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, data, compresslevel=9)
    signature = json.loads(sign(tag, revision))
    if signature.get('tag') != tag or signature.get('revision') != revision:
        raise ValueError('The phone interface signature belongs to another release')
    archive.replace(output / ARCHIVE)
    (output / MANIFEST).write_bytes(raw)
    (output / SIGNATURE).write_text(json.dumps(signature, separators=(',', ':')) + '\n', encoding='utf-8')
    return {'tag': tag, 'revision': revision, 'certificateSha256': signature.get('certificateSha256'),
            'files': {name: file_record(output / name) for name in NAMES}}


def java_signer(java, keystore, password_env, environment, alias='opencore-remote'):
    """Sign with the Android key; the password stays in the environment, never argv."""
    def sign(tag, revision):
        result = subprocess.run([str(java), str(HERE / 'SignPhoneUi.java'), str(keystore), alias, password_env, tag, revision],
                                env=environment, capture_output=True, text=True, encoding='utf-8', errors='replace')
        if result.returncode:
            raise RuntimeError('Could not sign the phone interface: ' + (result.stdout + result.stderr)[-4000:])
        return result.stdout.strip()
    return sign


def validate(folder, record, tag, revision, signer_sha256):
    """Fail closed unless the three assets are exactly what this Android build signed."""
    folder = Path(folder)
    if (not isinstance(record, dict) or record.get('tag') != tag or record.get('revision') != revision
            or record.get('certificateSha256') != signer_sha256 or set(record.get('files') or {}) != set(NAMES)):
        raise ValueError('The release phone interface does not match this signed Android build')
    for name in NAMES:
        if file_record(folder / name) != record['files'][name]:
            raise ValueError('The release phone interface changed after signing: ' + name)
    raw = (folder / MANIFEST).read_bytes()
    if hashlib.sha256(raw).hexdigest() != revision:
        raise ValueError('The release phone interface manifest is not the signed revision')
    signature = json.loads((folder / SIGNATURE).read_text(encoding='utf-8'))
    if signature.get('tag') != tag or signature.get('revision') != revision or signature.get('certificateSha256') != signer_sha256:
        raise ValueError('The release phone interface signature belongs to another build')
    with zipfile.ZipFile(folder / ARCHIVE) as bundle:
        for relative, entry in json.loads(raw)['files'].items():
            data = bundle.read(relative)
            if len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
                raise ValueError('The release phone interface archive failed verification: ' + relative)
    return [folder / name for name in NAMES]
