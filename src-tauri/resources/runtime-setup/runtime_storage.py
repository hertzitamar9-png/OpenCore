"""Disk inventory, removal and cleanup of app-managed model runtimes.

A runtime is an isolated Python environment that OpenCore prepares the first
time an installed model needs it. Runtimes are large (mostly PyTorch), so this
module reports which installed models still use each one, removes a runtime
when the person asks, and prunes runtimes that no installed model needs any
more. It never installs or downloads anything: a removed runtime is prepared
again automatically the next time a model needs it.

Environments may share PyTorch read-only through a ``.pth`` file that points
at another environment's site-packages. A runtime that lends packages to a
runtime still in use is kept by pruning; removing it explicitly invalidates
the borrowers' receipts so they are prepared again on their next use.
"""
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import threading
import time

MANIFEST = json.loads(Path(__file__).with_name('recipes.json').read_text(encoding='utf-8'))
REPARSE_POINT = 0x400
# Pruning never removes a runtime changed this recently: it may be mid-setup.
GRACE_SECONDS = 3600
# Cleanup after an uninstall or a request from the person uses a short window
# that only protects a runtime another model is being prepared into.
REQUESTED_GRACE_SECONDS = 300
# Native setup jobs older than this are treated as interrupted, not active.
STALE_JOB_SECONDS = 2 * 3600
ACTIVE_JOB = {'queued', 'running', 'cancelling', 'preparing', 'downloading', 'verifying'}
SAFE_NAME = re.compile(r'[A-Za-z0-9_.-]{1,80}')
# Runtimes that back an app feature rather than a removable model. They are
# listed and can be removed by the person, but pruning never removes them.
FEATURE_KINDS = {'memory', 'android', 'virtualbox', 'virtualbox-guest'}
# Generic runtimes prepared for any model of one type (no per-model receipt).
GENERIC_LABELS = {'tts-speech-v1': 'Speech generation runtime'}
# Engine builds (llama.cpp, stable-diffusion.cpp and their CUDA libraries) live
# here. Each is one hardware variant; only builds an installed model uses are kept.
ENGINE_FOLDERS = ('runtime-setup/engines', 'runtime-setup/runtimes')
OLD_COPY = re.compile(r'\.(previous|staging)-[0-9a-f]{8,32}$')
VARIANT_WORDS = {'cuda12': 'CUDA 12', 'cuda13': 'CUDA 13', 'vulkan': 'Vulkan', 'rocm': 'ROCm', 'cpu': 'CPU'}


def engine_label(name):
    """A readable name for an engine folder, such as 'Lfm3 text engine (CUDA 12)'."""
    old = bool(OLD_COPY.search(name))
    base = re.sub(r'-v\d+$', '', OLD_COPY.sub('', name))
    match = re.fullmatch(r'cuda(\d+)-libs', base)
    if match:
        label = 'CUDA ' + match[1] + ' libraries'
    else:
        parts = base.split('-')
        variant = next((VARIANT_WORDS[part] for part in parts if part in VARIANT_WORDS), None)
        family = [part for part in parts if part not in VARIANT_WORDS]
        if family[:2] == ['iris', 'sdcpp']:
            label = 'Image engine'
        else:
            label = (' '.join(family) or base).capitalize() + ' text engine'
        # The original Iris folder holds the Vulkan build.
        variant = variant or ('Vulkan' if family[:2] == ['iris', 'sdcpp'] else None)
        if variant:
            label += ' (' + variant + ')'
    return label + (' - old copy' if old else '')


class InUse(RuntimeError):
    """The runtime is open in another process or a setup job is running."""


def plain(value):
    """Return a comparable path without the Windows extended-length prefix."""
    text = os.fspath(value)
    if os.name == 'nt':
        if text[:8].lower() == '\\\\?\\unc\\':
            text = '\\\\' + text[8:]
        elif text.startswith('\\\\?\\'):
            text = text[4:]
    return Path(text)


def _long(text):
    """Expand Windows 8.3 short names (such as RUNNER~1) so equal paths compare equal."""
    if os.name != 'nt' or '~' not in text:
        return text
    try:
        import ctypes
        head, tail = text, ''
        while '~' in head:
            size = ctypes.windll.kernel32.GetLongPathNameW(head, None, 0)
            if size:
                buffer = ctypes.create_unicode_buffer(size)
                if ctypes.windll.kernel32.GetLongPathNameW(head, buffer, size):
                    return os.path.join(buffer.value, tail) if tail else buffer.value
            parent, name = os.path.split(head)
            if parent == head:
                break
            head, tail = parent, os.path.join(name, tail) if tail else name
    except (AttributeError, OSError, ValueError):
        pass
    return text


def _key(value):
    return os.path.normcase(_long(os.path.abspath(plain(value))))


def inside(path, folder):
    path, folder = _key(path), _key(folder).rstrip('\\/')
    return path == folder or path.startswith(folder + os.sep)


def _linked(entry):
    """True for symlinks and Windows junctions, which are never followed."""
    try:
        if entry.is_symlink() or (hasattr(entry, 'is_junction') and entry.is_junction()):
            return True
        return bool(getattr(entry.stat(follow_symlinks=False), 'st_file_attributes', 0) & REPARSE_POINT)
    except OSError:
        return True


def _linked_path(path):
    try:
        if path.is_symlink() or (hasattr(os.path, 'isjunction') and os.path.isjunction(path)):
            return True
        return bool(getattr(path.lstat(), 'st_file_attributes', 0) & REPARSE_POINT)
    except OSError:
        return False


def disk_bytes(path):
    """Bytes stored under ``path`` without following links or junctions."""
    total, pending = 0, [Path(path)]
    while pending:
        try:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    if _linked(entry):
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(entry.path)
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def site_packages(environment):
    environment = Path(environment)
    if os.name == 'nt':
        return [environment / 'Lib' / 'site-packages']
    return sorted(environment.glob('lib/python*/site-packages'))


def _markers(environment):
    """Path entries added by ``.pth`` files: [(pth file, [paths])]."""
    found = []
    for site in site_packages(environment):
        try:
            files = sorted(site.glob('*.pth'))
        except OSError:
            continue
        for marker in files:
            try:
                lines = marker.read_text(encoding='utf-8', errors='replace').splitlines()
            except OSError:
                continue
            paths = [line.strip() for line in lines
                     if line.strip() and not line.lstrip().startswith(('#', 'import ', 'import\t'))
                     and os.path.isabs(plain(line.strip()))]
            if paths:
                found.append((marker, paths))
    return found


def _modified(path):
    newest = 0
    for candidate in [Path(path), Path(path) / 'pyvenv.cfg', *site_packages(path)]:
        try:
            newest = max(newest, candidate.stat().st_mtime)
        except OSError:
            continue
    return newest


def _recipe_runtimes():
    """Map a runtime id to its recipe and the model ids that recipe serves."""
    result = {}
    for recipe in MANIFEST['recipes']:
        if recipe['kind'] == 'speech':
            name = 'speech:' + ('phonon-venv' if 'phonon-2' in recipe['modelIds'] else 'whisper-venv')
        else:
            name = recipe['id']
        current = result.setdefault(name, {'recipe': recipe, 'models': set()})
        current['models'].update(recipe['modelIds'])
    return result


def discover(root):
    """App-managed Python runtimes under ``root``. Linked folders are skipped."""
    root = Path(root)
    found = []
    environments = root / 'runtime-setup' / 'environments'
    if environments.is_dir():
        for child in sorted(environments.iterdir()):
            if child.is_dir() and not _linked_path(child) and SAFE_NAME.fullmatch(child.name):
                found.append({'id': child.name, 'kind': 'environment', 'path': child})
    speech = root / 'speech'
    if speech.is_dir():
        for child in sorted(speech.glob('*-venv')):
            if child.is_dir() and not _linked_path(child) and SAFE_NAME.fullmatch(child.name):
                found.append({'id': 'speech:' + child.name, 'kind': 'speech', 'path': child})
    for relative in ENGINE_FOLDERS:
        engines = root / relative
        if engines.is_dir():
            for child in sorted(engines.iterdir()):
                if child.is_dir() and not _linked_path(child) and SAFE_NAME.fullmatch(child.name):
                    found.append({'id': 'engine:' + child.name, 'kind': 'engine', 'path': child})
    return found


def _receipts(root):
    folder = Path(root) / 'runtime-setup' / 'receipts'
    result = []
    if folder.is_dir():
        for path in sorted(folder.glob('*.json')):
            try:
                value = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if isinstance(value, dict):
                result.append((path, value))
    return result


def _label(runtime, recipe):
    if recipe and recipe.get('label'):
        return recipe['label']
    if runtime['id'] in GENERIC_LABELS:
        return GENERIC_LABELS[runtime['id']]
    if runtime.get('kind') == 'engine':
        return engine_label(runtime['id'].split(':', 1)[-1])
    name = runtime['id'].split(':', 1)[-1]
    return re.sub(r'[-_]+', ' ', re.sub(r'-v\d+$|-venv$', '', name)).strip().capitalize() or name


def active_setup(root, now=None):
    """Return the target of a running native setup job, if any."""
    now = time.time() if now is None else now
    try:
        jobs = json.loads((Path(root) / 'runtime-setup' / 'jobs.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    for job in jobs if isinstance(jobs, list) else []:
        if not isinstance(job, dict) or job.get('status') not in ACTIVE_JOB:
            continue
        try:
            from datetime import datetime
            updated = datetime.fromisoformat(str(job.get('updatedAt')).replace('Z', '+00:00')).timestamp()
        except (TypeError, ValueError):
            updated = now
        if now - updated < STALE_JOB_SECONDS:
            return job.get('targetId') or 'a model'
    return None


def inventory(root, installed=(), demand=None, sizes=True, now=None):
    """Describe every managed runtime and whether an installed model needs it.

    ``installed`` holds the ids of installed models. ``demand`` maps a runtime
    id to installed model ids that need it but have no per-model receipt (for
    example every Hub image model uses one shared image runtime).
    """
    root = Path(root)
    now = time.time() if now is None else now
    installed = set(installed or ())
    demand = {key: set(value) for key, value in (demand or {}).items()}
    runtimes = discover(root)
    recipes = _recipe_runtimes()
    by_id = {runtime['id']: runtime for runtime in runtimes}

    def owner(path):
        if not path:
            return None
        return next((runtime for runtime in runtimes if inside(path, runtime['path'])), None)

    for runtime in runtimes:
        known = recipes.get(runtime['id'])
        runtime.update(recipe=known['recipe'] if known else None, users=set(known['models']) if known else set(),
                       receipts=[], lendsTo=set(), borrowsFrom=set(), missingShared=False)
    for path, value in _receipts(root):
        environment = value.get('environment')
        runtime = by_id.get(environment) if isinstance(environment, str) else owner(value.get('python') or value.get('executable'))
        if runtime:
            runtime['users'].add(str(value.get('targetId') or path.stem))
            runtime['receipts'].append(path)
    for runtime in runtimes:
        if runtime['kind'] == 'speech':
            receipt = root / 'speech' / (runtime['path'].name[:-len('-venv')] + '-runtime.json')
            if receipt.is_file():
                runtime['receipts'].append(receipt)
        for _marker, paths in _markers(runtime['path']):
            for path in paths:
                lender = owner(path)
                if lender is runtime:
                    continue
                if lender:
                    runtime['borrowsFrom'].add(lender['id'])
                    lender['lendsTo'].add(runtime['id'])
                elif not os.path.isdir(plain(path)):
                    runtime['missingShared'] = True
    # Pruning keeps runtimes an installed model needs, runtimes behind an app
    # feature and runtimes it cannot attribute to any model, and every runtime
    # that lends packages to one of those.
    needed = set()
    for runtime in runtimes:
        runtime['installedUsers'] = sorted((runtime['users'] & installed) | demand.get(runtime['id'], set()))
        runtime['users'] |= demand.get(runtime['id'], set())
        recipe = runtime['recipe']
        runtime['feature'] = bool(recipe and recipe['kind'] in FEATURE_KINDS)
        # Engine builds are always attributable: one no installed model uses is unused.
        if runtime['installedUsers'] or runtime['feature'] or (not runtime['users'] and runtime['kind'] != 'engine'):
            needed.add(runtime['id'])
    pending = list(needed)
    while pending:
        for lender in by_id[pending.pop()]['borrowsFrom']:
            if lender not in needed:
                needed.add(lender)
                pending.append(lender)
    rows = []
    for runtime in runtimes:
        recipe = runtime['recipe']
        shared_with = sorted(item for item in runtime['lendsTo'] if item in needed)
        if runtime['installedUsers']:
            status = 'in-use'
        elif shared_with:
            status = 'shared'
        elif runtime['feature']:
            status = 'feature'
        elif runtime['users'] or runtime['kind'] == 'engine':
            status = 'unused'
        else:
            status = 'unknown'
        modified = _modified(runtime['path'])
        rows.append({
            'id': runtime['id'], 'kind': runtime['kind'], 'label': _label(runtime, recipe),
            'path': str(plain(runtime['path'])), 'status': status, 'feature': runtime['feature'],
            'users': sorted(runtime['users']), 'installedUsers': runtime['installedUsers'],
            'sharedWith': shared_with,
            'borrowsFrom': sorted(runtime['borrowsFrom']), 'missingShared': runtime['missingShared'],
            'recentlyChanged': now - modified < GRACE_SECONDS,
            'bytes': disk_bytes(runtime['path']) if sizes else None,
            '_receipts': [str(path) for path in runtime['receipts']],
            '_lendsTo': sorted(runtime['lendsTo']), '_path': runtime['path']})
    return rows


def public(rows):
    """Inventory rows without internal fields, safe to send to the interface."""
    clean = [{key: value for key, value in row.items() if not key.startswith('_')} for row in rows]
    sized = [row['bytes'] for row in clean if isinstance(row.get('bytes'), int)]
    unused = [row['bytes'] for row in clean if row['status'] == 'unused' and isinstance(row.get('bytes'), int)]
    return {'schema': 1, 'runtimes': clean, 'totalBytes': sum(sized), 'unusedBytes': sum(unused)}


def _make_writable(function, path, _error):
    os.chmod(path, stat.S_IWRITE)
    function(path)


def delete_tree(path):
    """Delete a folder without following links. Returns True when it is gone."""
    path = Path(path)
    if not os.path.lexists(path):
        return True
    if _linked_path(path):
        try:
            os.unlink(path) if not path.is_dir() or path.is_symlink() else os.rmdir(path)
        except OSError:
            return False
        return not os.path.lexists(path)
    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_make_writable)
        else:
            shutil.rmtree(path, onerror=_make_writable)
    except OSError:
        pass
    return not os.path.lexists(path)


def trash_folder(root):
    return Path(root) / 'runtime-setup' / 'trash'


_PURGE = threading.Lock()


def purge_trash(root):
    """Finish deleting runtimes moved aside earlier and return how many remain.

    Purges run one at a time. Folders still open in another process remain
    and are retried by the next purge.
    """
    with _PURGE:
        folder = trash_folder(root)
        try:
            children = list(folder.iterdir()) if folder.is_dir() else []
        except OSError:
            return 0
        return sum(1 for child in children if not delete_tree(child))


def _invalidate(paths):
    for path in paths:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            continue


def _detach_borrower(borrower, lender_path):
    """Drop the borrower's marker for a removed lender and its stale receipts."""
    _invalidate(borrower['_receipts'])
    for marker, paths in _markers(borrower['_path']):
        if all(inside(path, lender_path) for path in paths):
            try:
                marker.unlink()
            except OSError:
                continue


def remove(root, runtime_id, installed=(), demand=None, purge=True, rows=None):
    """Remove one runtime. It is prepared again when a model next needs it.

    The folder is first renamed into the trash, which fails while any process
    still has files open inside it, so a runtime in use is never half deleted.
    """
    root = Path(root)
    rows = inventory(root, installed, demand, sizes=False) if rows is None else rows
    row = next((item for item in rows if item['id'] == runtime_id), None)
    if row is None:
        raise ValueError('This runtime is no longer installed. Refresh the list.')
    running = active_setup(root)
    if running:
        raise InUse(f'Wait for the current setup of {running} to finish before removing a runtime.')
    source = row['_path']
    if not os.path.lexists(source):
        _invalidate(row['_receipts'])
        return {'id': row['id'], 'label': row['label'], 'bytes': 0, 'invalidated': []}
    size = disk_bytes(source)
    folder = trash_folder(root)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{row['id'].replace(':', '-')}-{time.time_ns()}"
    try:
        os.replace(source, target)
    except OSError as error:
        if getattr(error, 'winerror', None) == 17 or getattr(error, 'errno', None) == 18:
            target = None  # Another volume: delete in place below.
        else:
            raise InUse(f"{row['label']} is open in another process. Stop the model that uses it, then try again.") from error
    _invalidate(row['_receipts'])
    invalidated = []
    for borrower_id in row['_lendsTo']:
        borrower = next((item for item in rows if item['id'] == borrower_id), None)
        if borrower:
            _detach_borrower(borrower, source)
            invalidated.append(borrower_id)
    if target is None:
        if not delete_tree(source):
            raise InUse(f"Part of {row['label']} is still open in another process. Stop the model that uses it, then try again.")
    elif purge:
        delete_tree(target)
    return {'id': row['id'], 'label': row['label'], 'bytes': size, 'invalidated': invalidated}


def runtimes_used_by(rows, model_id):
    """Ids of runtimes that list ``model_id`` among their users."""
    return {row['id'] for row in rows if model_id in row['users']}


def prune(root, installed=(), demand=None, grace=GRACE_SECONDS, now=None, only=None, purge=True):
    """Remove every runtime that no installed model needs.

    Runtimes that lend packages to a needed runtime, back an app feature, have
    no known owner or changed within ``grace`` seconds are kept. ``only``
    limits removal to the given runtime ids. With ``purge`` false the removed
    folders wait in the trash for :func:`purge_trash`. Runtimes whose shared
    packages disappeared lose their receipts so they are repaired on their
    next use.
    """
    root = Path(root)
    now = time.time() if now is None else now
    if purge:
        purge_trash(root)
    result = {'schema': 1, 'removed': [], 'kept': [], 'repaired': [], 'freedBytes': 0}
    running = active_setup(root, now)
    if running:
        result['skipped'] = f'Setup of {running} is running'
        return result
    rows = inventory(root, installed, demand, sizes=False, now=now)
    for row in rows:
        if row['missingShared'] and row['_receipts']:
            _invalidate(row['_receipts'])
            result['repaired'].append(row['id'])
    for row in rows:
        if row['status'] != 'unused' or (only is not None and row['id'] not in only):
            continue
        if grace and now - _modified(row['_path']) < grace:
            result['kept'].append({'id': row['id'], 'reason': 'recently-changed'})
            continue
        try:
            removed = remove(root, row['id'], installed, demand, purge=False, rows=rows)
        except (InUse, OSError) as error:
            result['kept'].append({'id': row['id'], 'reason': 'in-use', 'detail': str(error)})
            continue
        result['removed'].append(removed)
        result['freedBytes'] += removed['bytes']
    if purge and result['removed']:
        purge_trash(root)
    return result


# Speech models can be installed without a model receipt: Phonon by its
# weights, Whisper from an existing checkpoint folder or one the user points
# OpenCore at. Any weights found mean the model keeps its speech runtime.
SPEECH_WEIGHTS = {
    'phonon-2': ('speech/phonon-2/model.fermion',),
    'whisper-large-v3-turbo': ('speech/large-v3-turbo/model.safetensors', 'speech/large-v3-turbo/model.safetensors.index.json',
                               'speech/large-v3-turbo/model.bin'),
    'whisper-large-v3': ('speech/large-v3/model.safetensors', 'speech/large-v3/model.safetensors.index.json', 'speech/large-v3/model.bin'),
}
WHISPER_MODELS = ('whisper-large-v3-turbo', 'whisper-large-v3')


def _external_whisper(model_id):
    candidates = []
    configured = os.environ.get('OPENCORE_WHISPER_MODEL')
    if configured:
        candidates.append(Path(configured))
    profile = os.environ.get('USERPROFILE') or os.environ.get('HOME')
    if profile:
        candidates.append(Path(profile) / 'OpenCore-Model-Test' / 'asr' / model_id)
    for folder in candidates:
        try:
            if folder.is_absolute() and (folder / 'config.json').is_file():
                return True
        except OSError:
            continue
    return False


def installed_native_models(root):
    """Ids of installed native catalog models.

    Model receipts are the normal record. Speech models are also counted when
    their weights are present, so their runtimes are never treated as unused
    while the model itself is still on disk.
    """
    root = Path(root)
    folder = root / 'models' / 'receipts'
    installed = set()
    if folder.is_dir():
        installed = {path.stem[len('model-'):] for path in folder.glob('model-*.json') if path.is_file()}
    for model_id, paths in SPEECH_WEIGHTS.items():
        if any((root / path).is_file() for path in paths):
            installed.add(model_id)
    installed.update(model_id for model_id in WHISPER_MODELS if _external_whisper(model_id))
    return installed
