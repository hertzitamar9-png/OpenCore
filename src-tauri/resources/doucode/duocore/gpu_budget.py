from __future__ import annotations

import ctypes
import math
import os
from pathlib import Path
import subprocess

from .spec import DuoCoreConfig


def available_physical_memory_bytes() -> int:
    """Read currently available physical RAM without adding a Python dependency."""
    if os.name == "nt":
        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError("GlobalMemoryStatusEx failed")
        return int(status.ullAvailPhys)
    if hasattr(os, "sysconf"):
        return int(os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
    raise OSError("No physical-memory query is available on this platform")


def required_host_ram_mib(
    config: DuoCoreConfig,
    k2_bytes: int,
    nanbeige_bytes: int,
    k2_gpu_layers: int = 0,
    nanbeige_gpu_layers: int = 0,
) -> int:
    """Estimate host-resident weights plus Q4 KV/runtime memory and a 5 GiB reserve."""
    k2_gpu_fraction = min(max(k2_gpu_layers, 0), config.k2.num_hidden_layers) / config.k2.num_hidden_layers
    nb_gpu_fraction = min(max(nanbeige_gpu_layers, 0), config.nanbeige.num_hidden_layers) / config.nanbeige.num_hidden_layers
    host_model_bytes = k2_bytes * (1 - k2_gpu_fraction) + nanbeige_bytes * (1 - nb_gpu_fraction)
    q4_kv_bytes = config.k2.q4_kv_bytes(config.live_window_tokens) + config.nanbeige.q4_kv_bytes(
        config.live_window_tokens
    )
    # GPU-resident layers do not consume host RAM after upload. Keep slack for
    # CPU GGUF mappings, Q4 block scales, graph buffers, and worker overhead.
    estimated_working_bytes = math.ceil(host_model_bytes * 1.25 + q4_kv_bytes * 1.5 + 1024**3)
    desktop_reserve_bytes = 5 * 1024**3
    return math.ceil((estimated_working_bytes + desktop_reserve_bytes) / (1024**2))


def host_ram_startup_budget(
    config: DuoCoreConfig,
    release: Path,
    k2_gpu_layers: int = 0,
    nanbeige_gpu_layers: int = 0,
) -> tuple[int, int]:
    """Return estimated RAM needed and currently available RAM, in MiB."""
    k2_path = release / "backbones" / "k2" / config.k2.gguf_file
    nanbeige_path = release / "backbones" / "nanbeige" / config.nanbeige.gguf_file
    required_mib = required_host_ram_mib(
        config,
        k2_path.stat().st_size,
        nanbeige_path.stat().st_size,
        k2_gpu_layers,
        nanbeige_gpu_layers,
    )
    try:
        available_mib = available_physical_memory_bytes() // (1024**2)
    except (OSError, ValueError) as error:
        raise RuntimeError(f"Cannot safely start DuoCore: could not read available system RAM ({error})") from error
    return required_mib, int(available_mib)


def gpu_startup_budget(
    config: DuoCoreConfig,
    release: Path,
    k2_layers: int | None,
    nb_layers: int | None,
) -> tuple[int, int, int, int]:
    """Fit automatic layer offload to free VRAM and estimate the startup budget."""
    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        free_mib = int(completed.stdout.splitlines()[0].strip())
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as error:
        raise RuntimeError("Cannot safely start DuoCore GPU workers: nvidia-smi did not report free VRAM") from error

    k2_path = release / "backbones" / "k2" / config.k2.gguf_file
    nb_path = release / "backbones" / "nanbeige" / config.nanbeige.gguf_file
    k2_max = config.k2.num_hidden_layers
    nb_max = config.nanbeige.num_hidden_layers
    k2_bytes = k2_path.stat().st_size
    nb_bytes = nb_path.stat().st_size
    # The GGUF file-size estimate omits substantial CUDA graph/context buffers.
    # A real paired RTX 4070 load used about 2.1 GiB beyond estimated GPU layers.
    # Reserve 3.5 GiB for runtime/decode plus 1.5 GiB for the desktop and variation.
    runtime_mib = 3584
    safety_mib = 1536
    k2_values = range(k2_max + 1) if k2_layers is None else [min(max(0, k2_layers), k2_max)]
    nb_values = range(nb_max + 1) if nb_layers is None else [min(max(0, nb_layers), nb_max)]
    best: tuple[tuple[float, float], int, int, int] | None = None
    for k2_count in k2_values:
        for nb_count in nb_values:
            k2_ratio = k2_count / k2_max
            nb_ratio = nb_count / nb_max
            model_weights_mib = int((k2_bytes * k2_ratio + nb_bytes * nb_ratio) / (1024 * 1024))
            runtime_reserve = runtime_mib if k2_count or nb_count else 0
            required_mib = model_weights_mib + runtime_reserve + safety_mib
            # Keep both experts comparably GPU-resident; assigning almost all
            # layers to one head makes the nominally parallel pair serial in practice.
            score = (min(k2_ratio, nb_ratio), k2_ratio + nb_ratio, -abs(k2_ratio - nb_ratio))
            if required_mib <= free_mib and (best is None or score > best[0]):
                best = (score, required_mib, k2_count, nb_count)
    if best is not None:
        return best[1], free_mib, best[2], best[3]

    # Keep an explicit request intact so the caller can report its shortfall.
    k2_count = min(max(0, k2_layers or 0), k2_max)
    nb_count = min(max(0, nb_layers or 0), nb_max)
    model_weights_mib = int(
        (k2_bytes * (k2_count / k2_max) + nb_bytes * (nb_count / nb_max)) / (1024 * 1024)
    )
    required_mib = model_weights_mib + (runtime_mib if k2_count or nb_count else 0) + safety_mib
    return required_mib, free_mib, k2_count, nb_count
