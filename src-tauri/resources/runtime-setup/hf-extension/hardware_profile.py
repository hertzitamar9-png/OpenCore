"""One hardware profile that every OpenCore runtime uses to choose what to install.

Runtimes used to guess separately (speech and text to speech always took CUDA
PyTorch, the image runtime always took Vulkan). This module reads the graphics
hardware once, without loading PyTorch or starting a GPU context, and answers
one question for every runtime: which single variant should a default install
use on this PC?

* NVIDIA GPU with a working CUDA driver -> ``cuda``
* AMD or Intel GPU (or NVIDIA without a CUDA driver) with Vulkan -> ``vulkan``
  (ROCm stays an install-on-request option: driver support varies by card)
* anything else -> ``cpu``

The other variants are never installed automatically. ``alternatives`` lists
them with a plain explanation so the interface can offer them as a choice.
Choices the person makes are kept in ``runtime-setup/runtime-variants.json``.
"""
import json
import os
from pathlib import Path
import threading
import time

SCHEMA = 1
CACHE_SECONDS = 600
VARIANTS = ('cuda', 'rocm', 'vulkan', 'cpu')
LABELS = {'cuda': 'CUDA (NVIDIA)', 'rocm': 'ROCm (AMD)', 'vulkan': 'Vulkan GPU', 'cpu': 'CPU'}
VENDORS = {'10de': 'nvidia', '1002': 'amd', '1022': 'amd', '8086': 'intel', '1414': 'software'}
CHOICES_FILE = 'runtime-setup/runtime-variants.json'

_lock = threading.Lock()
_cached = None
_cached_at = 0.0


def _adapters():
    """Display adapters from the Windows registry: vendor, name and dedicated memory."""
    if os.name != 'nt':
        return []
    try:
        import winreg
    except ImportError:
        return []
    found = []
    key_path = r'SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}'
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path)
    except OSError:
        return []
    with root:
        for index in range(64):
            try:
                name = winreg.EnumKey(root, index)
            except OSError:
                break
            if not name.isdigit():
                continue
            try:
                with winreg.OpenKey(root, name) as key:
                    def value(field):
                        try:
                            return winreg.QueryValueEx(key, field)[0]
                        except OSError:
                            return None
                    device = str(value('MatchingDeviceId') or '').lower()
                    label = str(value('DriverDesc') or '').strip()
                    memory = value('HardwareInformation.qwMemorySize') or value('HardwareInformation.MemorySize')
                    if isinstance(memory, bytes):
                        memory = int.from_bytes(memory[:8], 'little')
                    vendor = 'other'
                    marker = device.find('ven_')
                    if marker >= 0:
                        vendor = VENDORS.get(device[marker + 4:marker + 8], 'other')
                    if vendor == 'software' or not label:
                        continue
                    found.append({'vendor': vendor, 'name': label,
                                  'vramBytes': int(memory) if isinstance(memory, int) and memory > 0 else None})
            except OSError:
                continue
    return found


def nvidia_gpu():
    """Read the first NVIDIA GPU through the driver's NVML library, without loading PyTorch."""
    if os.name != 'nt':
        return None
    import ctypes

    class Memory(ctypes.Structure):
        _fields_ = [('total', ctypes.c_ulonglong), ('free', ctypes.c_ulonglong), ('used', ctypes.c_ulonglong)]
    # Absolute system paths keep DLL discovery away from model folders and the working directory.
    for path in (Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/nvml.dll',
                 Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'NVIDIA Corporation/NVSMI/nvml.dll'):
        try:
            library = ctypes.CDLL(str(path))
            if library.nvmlInit_v2() != 0:
                continue
        except (OSError, AttributeError):
            continue
        try:
            device, memory, name = ctypes.c_void_p(), Memory(), ctypes.create_string_buffer(96)
            major, minor = ctypes.c_int(), ctypes.c_int()
            if (library.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(device))
                    or library.nvmlDeviceGetName(device, name, 96)
                    or library.nvmlDeviceGetMemoryInfo(device, ctypes.byref(memory))
                    or library.nvmlDeviceGetCudaComputeCapability(device, ctypes.byref(major), ctypes.byref(minor))):
                return None
            return {'name': name.value.decode('utf-8', 'replace'), 'totalGiB': round(memory.total / 1024 ** 3, 2),
                    'capability': [major.value, minor.value]}
        except (OSError, AttributeError):
            return None
        finally:
            try:
                library.nvmlShutdown()
            except (OSError, AttributeError):
                pass
    return None


def _cuda_driver():
    try:
        from managed_engines import cuda_driver_version
        return cuda_driver_version()
    except Exception:
        return 0


def _vulkan():
    try:
        from managed_engines import vulkan_gpu_available
        return vulkan_gpu_available()
    except Exception:
        return False


def detect():
    """Read the hardware now. Prefer ``profile()``, which caches the answer."""
    adapters = _adapters()
    nvidia = nvidia_gpu()
    driver = _cuda_driver()
    vulkan = bool(_vulkan())
    if nvidia and not any(item['vendor'] == 'nvidia' for item in adapters):
        adapters.insert(0, {'vendor': 'nvidia', 'name': nvidia['name'], 'vramBytes': None})
    for item in adapters:
        if item['vendor'] == 'nvidia' and nvidia and item['name'] == nvidia['name']:
            # NVML is exact; the registry value is capped at 4 GB on older drivers.
            item['vramBytes'] = int(nvidia['totalGiB'] * 1024 ** 3)
    order = {'nvidia': 0, 'amd': 1, 'intel': 2, 'other': 3}
    gpus = sorted(adapters, key=lambda item: (order.get(item['vendor'], 9), -(item['vramBytes'] or 0)))
    primary = gpus[0] if gpus else None
    return build({'gpus': gpus, 'cudaDriver': driver, 'vulkan': vulkan,
                  'computeCapability': list(nvidia['capability']) if nvidia else None,
                  'nvidiaVramBytes': int(nvidia['totalGiB'] * 1024 ** 3) if nvidia else None,
                  'primary': primary})


def build(values):
    """Complete a profile from raw facts. Tests build profiles with this."""
    gpus = list(values.get('gpus') or [])
    primary = values.get('primary') or (gpus[0] if gpus else None)
    driver = int(values.get('cudaDriver') or 0)
    capability = values.get('computeCapability')
    cuda = bool(driver >= 12000 and any(item.get('vendor') == 'nvidia' for item in gpus)
                and (capability is None or tuple(capability) >= (7, 0)))
    vulkan = bool(values.get('vulkan'))
    vendor = primary.get('vendor') if primary else None
    vram = values.get('nvidiaVramBytes') if cuda else (primary or {}).get('vramBytes')
    best = 'cuda' if cuda else 'vulkan' if vulkan else 'cpu'
    return {'schema': SCHEMA, 'gpus': gpus, 'vendor': vendor, 'gpuName': primary.get('name') if primary else None,
            'vramBytes': vram, 'cudaDriver': driver, 'cuda': cuda, 'computeCapability': capability,
            'vulkan': vulkan, 'amd': any(item.get('vendor') == 'amd' for item in gpus), 'best': best,
            'detectedAt': values.get('detectedAt', time.time())}


def profile(refresh=False):
    """The cached hardware profile; probing takes a few hundred milliseconds."""
    global _cached, _cached_at
    with _lock:
        if refresh or _cached is None or time.monotonic() - _cached_at > CACHE_SECONDS:
            _cached, _cached_at = detect(), time.monotonic()
        return dict(_cached)


def compatible(variant, hardware):
    """Can this variant run on this PC at all?"""
    if variant == 'cpu':
        return True
    if variant == 'cuda':
        return bool(hardware.get('cuda'))
    if variant == 'vulkan':
        return bool(hardware.get('vulkan'))
    if variant == 'rocm':
        return bool(hardware.get('amd'))
    return False


def default_variant(choices, hardware=None):
    """The one variant a default install uses, from the variants a runtime offers."""
    hardware = profile() if hardware is None else hardware
    choices = [item for item in choices if item in VARIANTS]
    if not choices:
        raise ValueError('This runtime has no installable variant')
    order = ['cuda'] if hardware.get('cuda') else []
    if hardware.get('vulkan'):
        order.append('vulkan')
    order.append('cpu')
    for variant in order:
        if variant in choices:
            return variant
    # A runtime without a CPU build: its first GPU build is the only option.
    return next((item for item in choices if compatible(item, hardware)), choices[0])


def _why(variant, default, hardware):
    name = hardware.get('gpuName') or 'this PC'
    if variant == default:
        return 'Recommended for ' + name + '.'
    if variant == 'cuda':
        return ('Fastest on NVIDIA GPUs.' if compatible('cuda', hardware)
                else 'Needs an NVIDIA GPU with a CUDA 12 or newer driver; none was found.')
    if variant == 'vulkan':
        return ('Works on most NVIDIA, AMD and Intel GPUs. Usually slower than CUDA on NVIDIA.' if compatible('vulkan', hardware)
                else 'Needs a GPU with a Vulkan driver; none was found.')
    if variant == 'rocm':
        return ('AMD\'s own GPU runtime. Only recent Radeon cards are supported; try it if Vulkan is slow.'
                if compatible('rocm', hardware) else 'Needs a supported AMD Radeon GPU; none was found.')
    return 'Runs on any PC without a GPU. Much slower, but always works.'


def alternatives(choices, hardware=None, installed=()):
    """Every variant a runtime offers, with whether it is recommended, installed and usable here."""
    hardware = profile() if hardware is None else hardware
    default = default_variant(choices, hardware)
    installed = set(installed)
    return [{'variant': variant, 'label': LABELS.get(variant, variant), 'recommended': variant == default,
             'installed': variant in installed, 'compatible': compatible(variant, hardware),
             'reason': _why(variant, default, hardware)}
            for variant in choices if variant in VARIANTS]


def fallback_offer(failed, choices, hardware=None):
    """After ``failed`` could not start, the variant to *offer* (never install) instead."""
    hardware = profile() if hardware is None else hardware
    for variant in ('vulkan', 'cpu') if failed == 'cuda' else ('cpu',):
        if variant in choices and variant != failed and compatible(variant, hardware):
            return {'variant': variant, 'label': LABELS[variant], 'reason': _why(variant, None, hardware)}
    return None


def public(hardware=None):
    """Profile fields that are safe and useful for the interface."""
    hardware = profile() if hardware is None else hardware
    return {key: hardware.get(key) for key in ('schema', 'vendor', 'gpuName', 'vramBytes', 'cuda', 'vulkan', 'amd', 'best')} | {
        'gpus': [{'vendor': item.get('vendor'), 'name': item.get('name'), 'vramBytes': item.get('vramBytes')}
                 for item in hardware.get('gpus') or []]}


# ── Variants the person chose ───────────────────────────────────────────────

def _choices_path(root):
    return Path(root) / CHOICES_FILE


def load_choices(root):
    try:
        value = json.loads(_choices_path(root).read_text(encoding='utf-8'))
        rows = value.get('variants') if isinstance(value, dict) and value.get('schema') == SCHEMA else None
        return {key: item for key, item in (rows or {}).items()
                if isinstance(key, str) and item in VARIANTS}
    except (OSError, ValueError, AttributeError):
        return {}


def save_choice(root, runtime_id, variant):
    if variant is not None and variant not in VARIANTS:
        raise ValueError('Unknown runtime variant')
    path = _choices_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = load_choices(root)
    if variant is None:
        rows.pop(runtime_id, None)
    else:
        rows[runtime_id] = variant
    temporary = path.with_suffix('.json.partial')
    temporary.write_text(json.dumps({'schema': SCHEMA, 'variants': rows}, indent=2), encoding='utf-8')
    temporary.replace(path)


def chosen_variant(root, runtime_id, choices, hardware=None):
    """The person's choice for this runtime when it is still offered, else the default."""
    saved = load_choices(root).get(runtime_id)
    return saved if saved in choices else default_variant(choices, hardware)
