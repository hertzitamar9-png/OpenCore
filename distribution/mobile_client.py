"""Publish the reviewed signed Android client with each PC release."""
import hashlib
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent


def source_hashes(root):
    files = sorted(path for path in (root / 'android-remote').rglob('*') if path.is_file()
                   and '__pycache__' not in path.parts and path.suffix in ('.java', '.py', '.xml', '.md'))
    files.append(root / 'src-tauri/icons/icon.png')
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')
            if path.suffix != '.png' else path.read_bytes()).hexdigest() for path in files}


def prepare(folder):
    pinned = HERE / 'mobile'
    receipt = json.loads((pinned / 'android-client.json').read_text(encoding='utf-8'))
    apk = pinned / 'OpenCore.Remote.apk'
    if (source_hashes(HERE.parent) != receipt['sources'] or apk.stat().st_size != receipt['bytes'] or
            hashlib.sha256(apk.read_bytes()).hexdigest() != receipt['sha256']):
        raise ValueError('Rebuild and verify the signed Android client after changing its sources')
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    result = []
    for source in (apk, pinned / 'android-client.json'):
        target = folder / source.name
        if source.resolve() != target.resolve(): shutil.copyfile(source, target)
        result.append(target)
    return result
