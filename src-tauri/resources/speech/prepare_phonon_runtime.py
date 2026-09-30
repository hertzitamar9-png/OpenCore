"""Isolated Phonon reference runtime; reuse installed CUDA libraries, not weights."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

TRANSFORMERS = "5.18.0"
ZSTANDARD = "0.25.0"
ARCHIVE_SHA = "98125795b6dda72f5c6eee9ba33d19815df65dcb18b50a357bf9f73c9935309e"
CONTAINER_SHA = "4b6bfa3a12cc3c4e0a54f2ab3ec4ca7a842b09e5c7ecfc8e7ca0ac6cc8c11468"
CONTAINER_BYTES = 177438361


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def extract_container(model_dir, zstandard):
    target = model_dir / 'model.fermion'
    if target.is_file() and target.stat().st_size == CONTAINER_BYTES and digest(target) == CONTAINER_SHA:
        return
    archive = model_dir / 'phonon-2.bps.tar.zst'
    if digest(archive) != ARCHIVE_SHA:
        raise RuntimeError('Phonon-2 archive checksum mismatch. Reinstall its pinned checkpoint.')
    temporary = target.with_suffix('.fermion.partial')
    found = False
    try:
        with archive.open('rb') as source, zstandard.ZstdDecompressor().stream_reader(source) as decoded:
            with tarfile.open(fileobj=decoded, mode='r|') as contents:
                for member in contents:
                    if member.name not in ('model.fermion', 'model_phonon2_c4c_int6/model.fermion'):
                        continue
                    if found or not member.isfile() or member.size != CONTAINER_BYTES:
                        raise RuntimeError('Invalid Phonon-2 container entry.')
                    found = True
                    with contents.extractfile(member) as entry, temporary.open('wb') as output:
                        shutil.copyfileobj(entry, output, length=1024 * 1024)
        if not found or temporary.stat().st_size != CONTAINER_BYTES or digest(temporary) != CONTAINER_SHA:
            raise RuntimeError('Phonon-2 container checksum mismatch.')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--prepare-model', action='store_true')
    args = parser.parse_args()
    root = Path(args.root).resolve()
    python = root / 'phonon-venv/Scripts/python.exe'
    if args.prepare_model:
        # Use the isolated runtime which owns the decompressor dependency.
        subprocess.run([str(python), '-c',
            'import sys; sys.path.insert(0,sys.argv[1]); from prepare_phonon_runtime import extract_container; '
            'from pathlib import Path; import zstandard; extract_container(Path(sys.argv[2]),zstandard)',
            str(Path(__file__).parent), str(root / 'phonon-2')], check=True)
        return
    from runtime_setup import prepare
    python, shared = prepare(root,'phonon-venv',{'transformers':TRANSFORMERS,'zstandard':ZSTANDARD,'av':'13.1.0','psutil':'6.1.1','librosa':'0.11.0'})
    check = subprocess.run([str(python), '-c',
        'import torch,av,numpy,psutil,zstandard,transformers,librosa; '
        'from transformers import ParakeetForTDT,ParakeetTDTConfig,AutoProcessor,ParakeetFeatureExtractor; '
        'ParakeetFeatureExtractor(); '
        f'assert transformers.__version__=="{TRANSFORMERS}"; assert torch.version.cuda'],
        capture_output=True, text=True)
    if check.returncode:
        raise RuntimeError(check.stderr.strip() or 'Phonon reference runtime verification failed.')
    (root / 'phonon-runtime.json').write_text(json.dumps({'schema':2,'transformers':TRANSFORMERS,'zstandard':ZSTANDARD,'librosa':'0.11.0',
        'sharedCudaRuntime':shared,'backend':'transformers-reference','weightDtype':'float32'}), encoding='utf-8')
    print('Phonon-2 reference speech runtime ready.', flush=True)


if __name__ == '__main__':
    main()
