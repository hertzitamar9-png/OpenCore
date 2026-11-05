"""Pinned, isolated Windows inference engines for publisher-specific GGUF models."""
import hashlib
import ctypes
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
VULKAN_VARIANTS = {'lfm3-cpu-v1': 'lfm3-vulkan-v1', 'flint-cpu-v1': 'flint-vulkan-v1'}
CUDA_VARIANTS = {'lfm3-cpu-v1': {12: 'lfm3-cuda12-v1', 13: 'lfm3-cuda13-v1'},
                 'flint-cpu-v1': {12: 'flint-cuda12-v1', 13: 'flint-cuda13-v1'}}


def cuda_driver_version():
    """Return the NVIDIA CUDA driver API version only when a device initializes."""
    if os.name != 'nt':
        return 0
    try:
        driver = ctypes.WinDLL('nvcuda.dll')
        count, version = ctypes.c_int(), ctypes.c_int()
        if driver.cuInit(0) != 0 or driver.cuDeviceGetCount(ctypes.byref(count)) != 0 or count.value < 1:
            return 0
        return version.value if driver.cuDriverGetVersion(ctypes.byref(version)) == 0 else 0
    except (OSError, AttributeError, ValueError):
        return 0


def vulkan_gpu_available():
    """Probe the installed Vulkan driver; a loader or software device alone is insufficient."""
    if os.name != 'nt':
        return False
    instance = ctypes.c_void_p()
    try:
        library = ctypes.WinDLL('vulkan-1.dll')
        class InstanceInfo(ctypes.Structure):
            _fields_ = [('sType', ctypes.c_uint32), ('pNext', ctypes.c_void_p), ('flags', ctypes.c_uint32),
                        ('pApplicationInfo', ctypes.c_void_p), ('enabledLayerCount', ctypes.c_uint32),
                        ('ppEnabledLayerNames', ctypes.c_void_p), ('enabledExtensionCount', ctypes.c_uint32),
                        ('ppEnabledExtensionNames', ctypes.c_void_p)]
        library.vkCreateInstance.argtypes = [ctypes.POINTER(InstanceInfo), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        library.vkCreateInstance.restype = ctypes.c_int32
        library.vkEnumeratePhysicalDevices.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_void_p)]
        library.vkEnumeratePhysicalDevices.restype = ctypes.c_int32
        library.vkGetPhysicalDeviceProperties.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        library.vkDestroyInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        info = InstanceInfo(); info.sType = 1
        if library.vkCreateInstance(ctypes.byref(info), None, ctypes.byref(instance)) != 0:
            return False
        count = ctypes.c_uint32()
        if library.vkEnumeratePhysicalDevices(instance, ctypes.byref(count), None) != 0 or not 0 < count.value <= 64:
            return False
        devices = (ctypes.c_void_p * count.value)()
        if library.vkEnumeratePhysicalDevices(instance, ctypes.byref(count), devices) != 0:
            return False
        for device in devices:
            properties = (ctypes.c_uint64 * 512)()
            library.vkGetPhysicalDeviceProperties(device, ctypes.byref(properties))
            # VkPhysicalDeviceProperties begins with four uint32 values and deviceType.
            device_type = ctypes.cast(properties, ctypes.POINTER(ctypes.c_uint32))[4]
            if device_type in (1, 2, 3):  # integrated, discrete, virtual GPU
                return True
        return False
    except (OSError, AttributeError, ValueError):
        return False
    finally:
        if instance.value:
            library.vkDestroyInstance(instance, None)


def engine_variants(engine_id):
    return (engine_id, *((VULKAN_VARIANTS[engine_id],) if engine_id in VULKAN_VARIANTS else ()),
            *CUDA_VARIANTS.get(engine_id, {}).values())


def select_engine(engine_id, preference='auto'):
    if preference not in ('auto', 'cuda', 'vulkan', 'cpu'):
        raise ValueError('Choose Automatic, CUDA, Vulkan or CPU')
    if preference == 'cpu':
        return engine_id
    if preference in ('auto', 'cuda') and engine_id in CUDA_VARIANTS:
        driver = cuda_driver_version()
        # CUDA minor-version compatibility can run a 12.4/13.4 build on the
        # corresponding 12.x/13.x driver family; startup still verifies it.
        version = 13 if driver >= 13000 else 12 if driver >= 12000 else 0
        if version:
            return CUDA_VARIANTS[engine_id][version]
        if preference == 'cuda':
            raise ValueError('CUDA requires a compatible NVIDIA GPU and driver. Choose Automatic, Vulkan or CPU instead.')
    elif preference == 'cuda':
        raise ValueError('This model requires a publisher-specific CPU runtime; CUDA is not available for it yet.')
    if preference in ('auto', 'vulkan') and engine_id in VULKAN_VARIANTS:
        if vulkan_gpu_available():
            return VULKAN_VARIANTS[engine_id]
        if preference == 'vulkan':
            raise ValueError('No compatible Vulkan GPU was detected. Choose Automatic or CPU instead.')
    elif preference == 'vulkan':
        raise ValueError('This model requires a publisher-specific CPU runtime; Vulkan is not available for it yet.')
    return engine_id


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
    if engine_id not in ('flint-cpu-v1', 'raon-cpu-v1', 'edgerock-cpu-v1', 'lfm3-cpu-v1', 'velociq8-cpu-v1', 'prism-cpu-v1', 'lfm3-vulkan-v1', 'flint-vulkan-v1',
                         'lfm3-cuda12-v1', 'lfm3-cuda13-v1', 'flint-cuda12-v1', 'flint-cuda13-v1', 'cuda12-libs-v1', 'cuda13-libs-v1'):
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
        required = pin.get('requiredFiles', ['llama-server.exe'])
        if not files or any(not any(item['path'] == name for item in files) for name in required):
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
        return folder(root, engine_id) / definition(engine_id).get('requiredFiles', ['llama-server.exe'])[0]
    target, pin = folder(root, engine_id), definition(engine_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    with runtime_lock(target.with_suffix('.lock')):
        if cached(root, engine_id, cancel):
            return folder(root, engine_id) / definition(engine_id).get('requiredFiles', ['llama-server.exe'])[0]
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
        required = pin.get('requiredFiles', ['llama-server.exe'])
        if any(not (staging / name).is_file() for name in required):
            raise ValueError('The runtime archive is missing a required executable or library')
        (staging / 'engine-receipt.json').write_text(json.dumps({
            'schema': 1, 'id': engine_id, 'archiveSha256': pin['sha256'],
            'source': pin['source'], 'revision': pin['revision'], 'files': files}, indent=2))
        cancel()
        if target.exists():
            backup = target.with_name(target.name + '.previous-' + uuid.uuid4().hex)
            target.rename(backup)
        staging.rename(target)
        return target / required[0]
    finally:
        archive.unlink(missing_ok=True)
        if staging.exists() and staging.resolve().parent == target.parent.resolve():
            shutil.rmtree(staging)
