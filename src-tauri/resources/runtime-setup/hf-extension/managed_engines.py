"""Pinned, isolated Windows inference engines for publisher-specific GGUF models."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import urllib.request
import uuid
import zipfile
from contextlib import contextmanager

HERE = Path(__file__).resolve().parent


@contextmanager
def runtime_lock(path):
    # OS locks disappear when the helper exits, including after a crash. Keep the
    # inode in place so a second helper cannot lock a replaced lock file.
    with Path(path).open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'\0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('Another model preparation owns this publisher runtime. Retry when it finishes.') from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def engine_for(entry):
    if entry.get('format') != 'gguf':
        return None
    model_type = str(entry.get('modelType') or '').lower()
    repository = str(entry.get('repository') or '').lower()
    if entry.get('managedEngine') in ('velociq8-cpu-v1', 'prism-cpu-v1'):
        return entry['managedEngine']
    if model_type == 'edgerock' or repository == 'hkust-aaron/edgerock-4b-gguf':
        return 'edgerock-cpu-v1'
    if (model_type in ('lfm3', 'lfm3_vl') or entry.get('managedEngine') == 'lfm3-cpu-v1'
            or repository in ('liquidai/lfm3-1.7b-gguf', 'liquidai/lfm3-vl-2b-gguf')):
        return 'lfm3-cpu-v1'
    if model_type in ('flint', 'spark2_5', 'sparkx2_5') or repository.startswith(('recursivesi/flint', 'xhtoken/spark-x2')):
        return 'flint-cpu-v1'
    if model_type in ('raon1_mini', 'raon', 'raon1') or repository.startswith('krafton/raon-1-mini-preview'):
        return 'raon-cpu-v1'
    if entry.get('managedEngine') == 'flint-cpu-v1':
        return 'flint-cpu-v1'
    return None


def definition(engine_id):
    data = json.loads((HERE / 'managed-engines.json').read_text(encoding='utf-8'))
    value = data['engines'][engine_id]
    if not re.fullmatch(r'[a-f0-9]{64}', value['sha256']):
        raise ValueError('The publisher runtime does not have a complete immutable pin')
    return value


def folder(root, engine_id):
    if engine_id not in ('flint-cpu-v1', 'raon-cpu-v1', 'edgerock-cpu-v1', 'lfm3-cpu-v1', 'velociq8-cpu-v1', 'prism-cpu-v1'):
        raise ValueError('Unknown managed publisher runtime')
    base = Path(root).resolve() / 'runtime-setup/engines'
    target = (base / engine_id).resolve()
    if not target.is_relative_to(base.resolve()):
        raise ValueError('Runtime folder leaves the managed directory')
    return target


def executable(root, engine_id):
    return folder(root, engine_id) / 'llama-server.exe'


def digest(path, cancel=lambda: None):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            cancel()
            value.update(block)
    return value.hexdigest()


def cached(root, engine_id, cancel=lambda: None):
    target, pin = folder(root, engine_id), definition(engine_id)
    try:
        receipt = json.loads((target / 'engine-receipt.json').read_text())
        if receipt.get('archiveSha256') != pin['sha256'] or receipt.get('id') != engine_id:
            return False
        files = receipt['files']
        if not files or not any(item['path'] == 'llama-server.exe' for item in files):
            return False
        for item in files:
            local = (target / item['path']).resolve()
            if not local.is_relative_to(target) or local.is_symlink() or local.stat().st_size != item['bytes']:
                return False
            if digest(local, cancel) != item['sha256']:
                return False
        return True
    except (FileNotFoundError, KeyError, OSError, ValueError):
        return False


def ensure(root, engine_id, report=lambda **values: None, cancel=lambda: None):
    if cached(root, engine_id, cancel):
        return executable(root, engine_id)
    target, pin = folder(root, engine_id), definition(engine_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    with runtime_lock(target.with_suffix('.lock')):
        if cached(root, engine_id, cancel):
            return executable(root, engine_id)
        return _install(target, pin, engine_id, report, cancel)


def _install(target, pin, engine_id, report, cancel):
    staging = target.with_name(target.name + '.staging-' + uuid.uuid4().hex)
    archive = target.with_suffix('.zip.partial')
    try:
        if shutil.disk_usage(target.parent).free < pin['bytes'] * 4 + 1024 ** 3:
            raise RuntimeError('Not enough free space to prepare the publisher runtime')
        report(stage='installing-runtime', detail='Downloading the verified ' + pin['label'] + ' runtime')
        # Restart the small runtime archive; large model weights keep their independent resumable downloads.
        request = urllib.request.Request(pin['url'], headers={'User-Agent': 'OpenCore-Managed-Runtime/1'})
        with urllib.request.urlopen(request, timeout=30) as response, archive.open('wb') as output:
            received, last = 0, 0.0
            while True:
                cancel()
                block = response.read(256 * 1024)
                if not block:
                    break
                received += len(block)
                if received > pin['bytes']:
                    raise ValueError('Publisher runtime exceeded its pinned size')
                output.write(block)
                if time.monotonic() - last > .5:
                    last = time.monotonic()
                    report(stage='installing-runtime', detail=f"Preparing {pin['label']}: {received / 1e6:.1f} / {pin['bytes'] / 1e6:.1f} MB")
        if received != pin['bytes'] or digest(archive, cancel) != pin['sha256']:
            raise ValueError('The publisher runtime failed checksum verification')
        staging.mkdir()
        files = []
        with zipfile.ZipFile(archive) as package:
            members = package.infolist()
            if len(members) > 1000 or sum(item.file_size for item in members) > 2 * 1024 ** 3:
                raise ValueError('Publisher runtime archive exceeds its extraction limit')
            seen = set()
            for item in members:
                cancel()
                name = item.orig_filename
                if '\\' in name or '\0' in name or name.startswith('/') or ':' in name or any(part in ('', '.', '..') for part in name.rstrip('/').split('/')):
                    raise ValueError('Publisher runtime archive contains an unsafe path')
                if (item.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError('Publisher runtime archive contains a symbolic link')
                prefix = pin.get('stripPrefix')
                if prefix:
                    if not re.fullmatch(r'[A-Za-z0-9_.-]+/', prefix) or '..' in prefix:
                        raise ValueError('Invalid publisher runtime layout prefix')
                    if name == prefix and item.is_dir(): continue
                    if not name.startswith(prefix):
                        raise ValueError('Publisher runtime archive leaves its reviewed layout prefix')
                    name = name[len(prefix):]
                local = (staging / name).resolve()
                if not local.is_relative_to(staging.resolve()) or name.casefold() in seen:
                    raise ValueError('Publisher runtime archive contains a colliding path')
                seen.add(name.casefold())
                if item.is_dir():
                    local.mkdir(parents=True, exist_ok=True)
                    continue
                local.parent.mkdir(parents=True, exist_ok=True)
                with package.open(item) as source, local.open('xb') as output:
                    while True:
                        cancel()
                        block = source.read(1024 * 1024)
                        if not block:
                            break
                        output.write(block)
                files.append({'path': name, 'bytes': local.stat().st_size, 'sha256': digest(local, cancel)})
        if not (staging / 'llama-server.exe').is_file():
            raise ValueError('Publisher runtime archive has no server executable')
        (staging / 'engine-receipt.json').write_text(json.dumps({
            'schema': 1, 'id': engine_id, 'archiveSha256': pin['sha256'],
            'source': pin['source'], 'revision': pin['revision'], 'files': files}, indent=2))
        cancel()
        if target.exists():
            backup = target.with_name(target.name + '.previous-' + uuid.uuid4().hex)
            target.rename(backup)
        staging.rename(target)
        return target / 'llama-server.exe'
    finally:
        archive.unlink(missing_ok=True)
        if staging.exists() and staging.resolve().parent == target.parent.resolve():
            shutil.rmtree(staging)
