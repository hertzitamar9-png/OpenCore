"""Publisher Phonon-2 runtime for the original five-value checkpoint.

The 164 MB figure is the checkpoint download, not a promise about process RAM.
This uses the publisher's native packed Windows CPU kernels; dense fallbacks
are refused so choosing Original never silently selects our FP32 runtime.
"""
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time

from prepare_phonon_runtime import ARCHIVE_SHA, CONTAINER_BYTES, CONTAINER_SHA, digest

PUBLISHER_VERSION = '0.2.10'
PUBLISHER_WHEEL = ('https://files.pythonhosted.org/packages/87/ae/2bf70a140ea6ce9a124fc5c2332f7c079a1410ec5ecfb94a87c923853a02/'
                   'fermion_research-0.2.10-py3-none-any.whl'
                   '#sha256=112c3b99851962782c7306f5343a5e764a2e985d91d19c4ce3ddbabf4ca6bc69')
CONFIG_BYTES = 277493
CONFIG_SHA = 'd0daad3b2a182893844f4abdc11e4f5b7083f7d42c8ad7e8203f71559785a31b'


def original_environment(environment):
    env = dict(environment)
    env.update(FERMION_P2_CPU='onedot', PHONON2_CPU_ONEDOT='1',
               FERMION_P2_CPU_GRAPH='0', FERMION_P2_CPU_ENC='c',
               FERMION_P2_CPU_TDT='c', FERMION_P2_DIRECT='1',
               FERMION_P2_PLANE_CACHE='0', CUDA_VISIBLE_DEVICES='-1')
    return env


def require_compact_engine(description):
    packed = description.get('packed') or {}
    if (not packed.get('modules') or not packed.get('onedot')
            or not description.get('c_encoder') or not description.get('c_tdt_loop')
            or packed.get('fallback')):
        raise RuntimeError('Phonon-2 original compact runtime could not load its native kernels. '
                           'BF16 and FP32 remain available. ' + json.dumps(description, default=str))


def ensure_publisher_runtime(progress):
    try:
        installed = importlib.metadata.version('fermion-research')
    except importlib.metadata.PackageNotFoundError:
        installed = None
    if installed != PUBLISHER_VERSION:
        progress('preparing-original-runtime')
        # The wheel supplies the publisher's prebuilt platform kernels. All its
        # dependencies are already owned by the isolated Phonon environment.
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check',
                        '--no-deps', '--no-cache-dir', PUBLISHER_WHEEL],
                       check=True, timeout=120, stdout=sys.stderr, stderr=sys.stderr,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def extract_original_config(directory, zstandard):
    """Read only the hash-pinned config from the already installed archive."""
    directory = Path(directory)
    target = directory / 'config.json'
    if target.is_file() and target.stat().st_size == CONFIG_BYTES and digest(target) == CONFIG_SHA:
        return target
    archive = directory / 'phonon-2.bps.tar.zst'
    if not archive.is_file() or digest(archive) != ARCHIVE_SHA:
        raise RuntimeError('Phonon-2 original archive is missing or corrupted. Reinstall its checkpoint.')
    temporary = directory / '.original-config.partial'
    found = False
    try:
        with archive.open('rb') as source, zstandard.ZstdDecompressor().stream_reader(source) as decoded:
            with tarfile.open(fileobj=decoded, mode='r|') as contents:
                for member in contents:
                    if member.name not in ('config.json', 'model_phonon2_c4c_int6/config.json'):
                        continue
                    if found or not member.isfile() or member.size != CONFIG_BYTES:
                        raise RuntimeError('Invalid Phonon-2 original configuration entry.')
                    found = True
                    with contents.extractfile(member) as entry, temporary.open('wb') as output:
                        shutil.copyfileobj(entry, output, length=1024 * 1024)
        if not found or temporary.stat().st_size != CONFIG_BYTES or digest(temporary) != CONFIG_SHA:
            raise RuntimeError('Phonon-2 original configuration checksum mismatch.')
        temporary.replace(target)
        return target
    finally:
        temporary.unlink(missing_ok=True)


def run_worker(args, started, emit):
    from whisper_worker import load_audio, require_ram
    try:
        import av
        import numpy as np
        import psutil
        import zstandard
        directory = Path(args.model).resolve()
        emit({'progress': 'verifying-checkpoint'})
        container = directory / 'model.fermion'
        if not container.is_file() or container.stat().st_size != CONTAINER_BYTES or digest(container) != CONTAINER_SHA:
            raise RuntimeError('Phonon-2 container is missing or corrupted. Reinstall the speech model.')
        require_ram(psutil, 2 * 1024**3, 'Phonon-2 original runtime')
        extract_original_config(directory, zstandard)
        ensure_publisher_runtime(lambda stage: emit({'progress': stage}))
        os.environ.update(original_environment(os.environ))
        emit({'progress': 'loading-packed-weights'})
        from phonon_minimal import load
        model = load(directory, lambda stage: emit({'progress': stage}))
        description = model.describe()
        require_compact_engine(description)
        emit({'ready': True, 'modelId': 'phonon-2', 'language': 'en', 'device': 'cpu',
              'runtimePrecision': 'original', 'weightDtype': 'publisher-five-value',
              'coldStartMs': round((time.monotonic() - started) * 1000),
              'wakeMs': 0 if args.awake else None,
              'runtimeResidentBytes': psutil.Process().memory_info().rss,
              'runtimeDescription': description['runtime'], 'publisherRuntime': description})
        for line in sys.stdin:
            try:
                request = json.loads(line)
                action = request.get('action')
                if action == 'shutdown':
                    break
                if action == 'wake':
                    emit({'awake': True, 'wakeMs': 0, 'device': 'cpu'})
                elif action == 'transcribe':
                    audio = load_audio(request['audio'], av, np)
                    text, decode_seconds, duration = model.transcribe_array(audio)
                    emit({'text': text, 'language': 'en', 'device': 'cpu', 'standbyDevice': 'cpu',
                          'gpuModelBytes': 0, 'torchGpuBytes': 0, 'decodeSeconds': decode_seconds,
                          'audioSeconds': duration, 'runtimeResidentBytes': psutil.Process().memory_info().rss})
                else:
                    emit({'error': f'Unknown speech action: {action}'})
            except Exception as error:
                emit({'error': str(error)})
        return 0
    except (Exception, SystemExit) as error:
        emit({'error': str(error)})
        return 1
