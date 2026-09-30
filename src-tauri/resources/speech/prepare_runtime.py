"""Prepare OpenCore's local Transformers + CUDA speech runtime only.

This script installs libraries, never model weights. Whisper checkpoints remain
offline and are installed separately by OpenCore's pinned model catalog.
"""
import argparse
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys

TORCH = "2.5.1"
TORCH_CUDA = "12.4"
PACKAGES = {
    "numpy": "1.26.4",
    "transformers": "4.48.3",
    "accelerate": "1.2.1",
    "av": "13.1.0",
    "psutil": "6.1.1",
}


def check(python, root):
    result = subprocess.run(
        [str(python), "-c", "import json, torch; import av, numpy, psutil, transformers; "
         "print(json.dumps({'torch': torch.__version__, 'cuda': torch.version.cuda}))"],
        capture_output=True, text=True,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Speech runtime imports failed.")
    info = json.loads(result.stdout.strip().splitlines()[-1])
    if not info["torch"].startswith(TORCH) or info["cuda"] != TORCH_CUDA:
        raise RuntimeError(f"PyTorch must be {TORCH} with CUDA {TORCH_CUDA}; found {info}.")
    for package, version in PACKAGES.items():
        if importlib.metadata.version(package) != version:
            raise RuntimeError(f"{package} requires version {version}.")
    (root / "runtime.json").write_text(json.dumps({"torch": TORCH, "cuda": TORCH_CUDA, **PACKAGES}), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    python = root / "venv/Scripts/python.exe"
    if args.check:
        check(python, root)
        return
    root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < 102_000_000_000:
        raise RuntimeError("Speech setup must preserve 100 GB of free disk space.")
    if not python.is_file():
        subprocess.run([sys.executable, "-m", "venv", str(root / "venv")], check=True)
    # Install CUDA-enabled PyTorch from its official wheel index, then the
    # smaller speech packages from PyPI. Model weights are never downloaded here.
    torch_check = subprocess.run(
        [str(python), "-c", "import torch; assert torch.__version__.startswith('2.5.1') and torch.version.cuda == '12.4'"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if torch_check.returncode:
        subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir",
                        "--index-url", "https://download.pytorch.org/whl/cu124", f"torch=={TORCH}"], check=True)
    packages_check = subprocess.run(
        [str(python), "-c", "import importlib.metadata as m; " +
         "; ".join(f"assert m.version('{name}') == '{version}'" for name, version in PACKAGES.items())],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if packages_check.returncode:
        subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir",
                        *[f"{name}=={version}" for name, version in PACKAGES.items()]], check=True)
    check(python, root)
    print("Whisper Large V3 speech runtime is ready.", flush=True)


if __name__ == "__main__":
    main()
