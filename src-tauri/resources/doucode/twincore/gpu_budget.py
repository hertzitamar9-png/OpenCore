from __future__ import annotations

from pathlib import Path
import subprocess

from .spec import TwinCoreConfig


def gpu_startup_budget(
    config: TwinCoreConfig,
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
        raise RuntimeError("Cannot safely start doUcode GPU workers: nvidia-smi did not report free VRAM") from error

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
