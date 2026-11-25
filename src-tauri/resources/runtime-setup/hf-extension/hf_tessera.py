"""Prepare the publisher's Tessera sampler and CLIP encoder for local CPU image jobs."""
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
from zipfile import BadZipFile, ZipFile

from hf_catalog import stable_id
from hf_download import Downloader, HubClient, atomic_json, managed_path
from hf_runtime import _python

REPOSITORY = 'thejagstudio/tessera-small'
REVISION = 'e019edd7b29367821c05e6261bc40294ce6f8d27'
WEIGHTS = 'tessera-small-fp16.pt'
WEIGHTS_SHA256 = '772a289de232950bc411bbfeaa4d60c320450b04cf5b7d8280c9c0b36880fec3'
MODEL_ID = stable_id(REPOSITORY, 'pytorch:' + WEIGHTS)
RUNTIME = 'tessera-small-v1'
ENVIRONMENT = 'tessera-small-cpu-v1'
EXTENSION = Path(__file__).resolve().parent
ASSETS = json.loads((EXTENSION / 'tessera-assets.json').read_bytes())
SOURCE = ASSETS['sourceFiles']
ENCODER = ASSETS['encoder']


def supports(entry):
    return (entry.get('id') == MODEL_ID and entry.get('repository') == REPOSITORY
            and entry.get('revision') == REVISION and entry.get('imageRuntime') == RUNTIME
            and any(pin.get('filename') == WEIGHTS and pin.get('sha256') == WEIGHTS_SHA256
                    and pin.get('role') == 'model' for pin in entry.get('files', ())))


def environment_python(root):
    return managed_path(root, 'runtime-setup', 'environments', ENVIRONMENT, 'Scripts', 'python.exe')


def source_path(root):
    return managed_path(root, 'models', 'hub', MODEL_ID, 'components', 'tessera.zip')


class EncoderDownloader(Downloader):
    """Keep the encoder in this model's install so its normal uninstall includes all weights."""
    def directory(self, entry):
        if entry != ENCODER:
            raise ValueError('Unexpected Tessera encoder package')
        return managed_path(self.root, 'models', 'hub', MODEL_ID, 'components', 'clip')


def source_valid(path):
    try:
        # The archive is imported directly. Reject extra modules or duplicate members.
        if path.stat().st_size > 200_000:
            return False
        with ZipFile(path) as archive:
            if sorted(archive.namelist()) != sorted(item['filename'] for item in SOURCE):
                return False
            return all(archive.getinfo(pin['filename']).file_size == pin['bytes']
                       and hashlib.sha256(archive.read(pin['filename'])).hexdigest() == pin['sha256']
                       for pin in SOURCE)
    except (OSError, ValueError, KeyError, BadZipFile):
        return False


def prepare_source(root, client, cancelled):
    target = source_path(root)
    if source_valid(target):
        return target
    base = '/spaces/' + ASSETS['sourceRepository'] + '/resolve/' + ASSETS['sourceRevision'] + '/'
    buffer = io.BytesIO()
    with ZipFile(buffer, 'w') as archive:
        for pin in SOURCE:
            if cancelled.is_set():
                raise RuntimeError('Tessera preparation cancelled')
            with client.open(base + pin['filename']) as response:
                data = response.read(pin['bytes'] + 1)
            if len(data) != pin['bytes'] or hashlib.sha256(data).hexdigest() != pin['sha256']:
                raise ValueError('Tessera source checksum mismatch for ' + pin['filename'])
            archive.writestr(pin['filename'], data)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
        partial = Path(stream.name)
        stream.write(buffer.getvalue())
    try:
        if not source_valid(partial):
            raise ValueError('The Tessera source archive failed verification')
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    return target


def identity():
    digest = hashlib.sha256()
    for name in ('hf_tessera.py', 'tessera_worker.py', 'tessera-assets.json', 'tessera-cpu-lock.txt'):
        digest.update((EXTENSION / name).read_text(encoding='utf-8').encode())
    return digest.hexdigest()


def environment_ready(root):
    try:
        receipt = json.loads(managed_path(root, 'runtime-setup', 'receipts', ENVIRONMENT + '.json').read_bytes())
        return (receipt.get('lockSha256') == lock_hash() and environment_python(root).is_file()
                and Path(receipt['python']).resolve() == environment_python(root).resolve())
    except (OSError, KeyError, ValueError, TypeError):
        return False


def lock_hash():
    return hashlib.sha256((EXTENSION / 'tessera-cpu-lock.txt').read_text(encoding='utf-8').encode()).hexdigest()


def ensure_environment(manager, job):
    root = manager.root
    python = environment_python(root)
    if environment_ready(root):
        return python
    manager._update(job, stage='Preparing the Tessera CPU runtime')
    if not python.is_file():
        manager._spawn([_python(root), '-I', '-m', 'venv', python.parent.parent], job, timeout=180)
    manager._spawn([python, '-I', '-m', 'pip', '--isolated', 'install', '--disable-pip-version-check',
                    '--only-binary=:all:', '--require-hashes', '--no-deps', '--index-url', 'https://pypi.org/simple',
                    '-r', EXTENSION / 'tessera-cpu-lock.txt'], job)
    manager._spawn([python, '-I', '-c', 'import torch, numpy, PIL; from transformers import CLIPTextModel, AutoTokenizer; '
                    'assert torch.version.cuda is None'], job, timeout=90)
    atomic_json(managed_path(root, 'runtime-setup', 'receipts', ENVIRONMENT + '.json'),
                {'python': str(python), 'lockSha256': lock_hash(), 'device': 'cpu'})
    return python


def ready(root, entry, proof):
    if not (supports(entry) and entry.get('status') == 'installed' and isinstance(proof, dict)
            and proof.get('inferenceVerified') is True and proof.get('receiptRevision') == REVISION
            and proof.get('workerSha256') == identity() and environment_ready(root)):
        return False
    folder = managed_path(root, 'models', 'hub', MODEL_ID, 'components', 'clip')
    try:
        return source_valid(source_path(root)) and all(
            managed_path(folder, pin['filename']).stat().st_size == pin['bytes'] for pin in ENCODER['files'])
    except OSError:
        return False


def run(manager, entry, request, job):
    python = ensure_environment(manager, job)
    client = HubClient()
    manager._update(job, stage='Verifying the Tessera sampler and CLIP text encoder')
    source = prepare_source(manager.root, client, manager.cancelled)
    downloader = EncoderDownloader(manager.root, client)
    def progress(value):
        filename = value.get('filename')
        stage = 'Preparing CLIP text encoder' + (' · ' + filename if filename else '')
        if stage != job.get('stage'):
            manager._update(job, stage=stage)
    encoder = downloader.install(ENCODER, manager.cancelled, progress)
    before_inference = getattr(manager, 'before_inference', None)
    if before_inference:
        before_inference(job, manager.cancelled, manager._update)
    output = managed_path(manager.directory, job['id'])
    output.mkdir()
    payload = {'request': request, 'checkpoint': str(managed_path(entry['localDirectory'], WEIGHTS)),
               'encoder': encoder['localDirectory'], 'source': str(source)}
    request_file = managed_path(manager.directory, job['id'] + '.json')
    atomic_json(request_file, payload)
    manager._update(job, status='running', stage='Drawing the pixel-art sprite on CPU')
    manager._spawn([python, '-I', EXTENSION / 'tessera_worker.py', request_file, output], job, output=output)
    result = json.loads((output / 'result.json').read_bytes())
    count = request['settings'].get('numImages', 1)
    paths = [managed_path(output, f'sprite-{i + 1}.png') for i in range(count)]
    if (result.get('modelId') != MODEL_ID or result.get('count') != count
            or any(not file.is_file() or file.stat().st_size < 64 for file in paths)):
        raise RuntimeError('Tessera did not return the requested sprites')
    manager._update(job, status='completed', stage='Generation complete', outputs=list(map(str, paths)))
    return {'runtimeReady': True, 'inferenceVerified': True, 'receiptRevision': REVISION,
            'workerSha256': identity(), 'verifiedAt': time.time(), 'device': 'cpu', 'recipe': ENVIRONMENT}
