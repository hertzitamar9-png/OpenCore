"""Pinned, offline Reverb speech packages and their isolated CPU runtime."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from hf_download import atomic_json, managed_path

REPOSITORY = 'software-mansion/Reverb-TTS'
REVISION = '9c0da9ddfd47619375ce8e5aeede4681fe32d24d'
ENVIRONMENT = 'reverb-tts-cpu-v1'
EXTENSION = 'runtime-setup/hf-extension'
VARIANTS = {
    'reverb-tts-int8-v1': 'int8',
    'reverb-tts-int4-v1': 'int4',
    'reverb-tts-fp16-v1': 'fp16',
    'reverb-tts-fp32-v1': 'fp32',
}
DEFAULT_VOICE = 'en_male_narrator_1'
LANGUAGES = {
    'en': 'English', 'de': 'German', 'fr': 'French', 'es': 'Spanish',
    'it': 'Italian', 'pl': 'Polish', 'pt': 'Portuguese',
}
VOICE_IDS = (
    DEFAULT_VOICE, 'en_male_narrator_2', 'en_female_narrator', 'en_female_whisper', 'en_male_angry',
    'de_female_standard', 'de_male_standard', 'es_male_standard_1', 'es_male_standard_2',
    'fr_female_standard', 'fr_male_standard', 'it_male_expressive', 'it_male_standard',
    'pl_male_standard', 'pt_female_standard', 'pt_male_standard',
)
CODEC_REPOSITORY = 'eustlb/higgs-audio-v2-tokenizer'
CODEC_REVISION = '0e3a9177c2991016b8e5e17d23aa474ee665bef2'
COMPONENTS = {
    name: {
        'label': label, 'kind': 'file', 'repository': CODEC_REPOSITORY, 'revision': CODEC_REVISION,
        'filename': filename, 'bytes': length, 'sha256': digest, 'license': 'See codec repository',
    }
    for name, label, filename, length, digest in (
        ('reverbCodec', 'Higgs Audio v2 codec weights', 'model.safetensors', 805665628,
         'fe7c5e8785e0a05833e1bfc3e002ec7f55af21e306b2e7154a448c1f54ccfb0d'),
        ('reverbCodecConfig', 'Higgs Audio v2 codec configuration', 'config.json', 2531,
         'eefb20806f7104e77c9a5277c9df0f9bb8826b08eb1d4e8ab2b9829b6ef9fac1'),
        ('reverbCodecProcessor', 'Higgs Audio v2 codec processor configuration', 'preprocessor_config.json', 206,
         'ae61eea88558608ee2fa86d2aec9fce8d99a5ff75d09cb7651ccce21ae1d9084'),
    )
}


def family(model_runtime):
    if model_runtime not in VARIANTS:
        raise ValueError('Choose a supported Reverb package')
    voices = []
    for name in VOICE_IDS:
        language, gender, style = name.split('_', 2)
        voices.append({'id': name, 'language': language, 'gender': gender, 'style': style.replace('_', ' ')})
    return {
        'repository': REPOSITORY, 'revision': REVISION, 'label': 'Reverb TTS', 'environment': ENVIRONMENT,
        'components': tuple(COMPONENTS), 'maxText': 1200, 'reference': 'transcript-required',
        'referenceRequired': False, 'instruction': False, 'voices': True, 'voiceList': voices,
        'presets': None, 'cfg': None, 'steps': {'min': 1, 'max': 24, 'default': 8}, 'sampling': False,
        'normalize': False, 'tags': [], 'editTags': False,
        'notes': ('Use a preset voice or add 3–20 seconds of clean speech and its exact transcript. '
                  'Text is normalized automatically in the selected language. '
                  'The smallest downloads expand to FP32 in RAM. The shared codec is installed once.'),
        'languages': [{'id': identity, 'label': label} for identity, label in LANGUAGES.items()],
        'hardware': {'cpu': True, 'cpuOnly': True, 'gpuGiB': 0, 'bf16': False, 'summary': 'Windows x64 CPU'},
        'verification': {'text': 'OpenCore is ready to read aloud.', 'settings': {
            'voice': DEFAULT_VOICE, 'language': 'en', 'seed': 7,
        }},
    }


def python_path(root):
    return managed_path(root, 'runtime-setup', 'environments', ENVIRONMENT, 'Scripts', 'python.exe')


def worker_path(resources):
    return Path(resources) / EXTENSION / 'reverb_worker.py'


def _lock_path(resources):
    return Path(resources) / EXTENSION / 'reverb-cpu-lock.txt'


def _receipt(root):
    return managed_path(root, 'runtime-setup', 'receipts', ENVIRONMENT + '.json')


def lock_identity(resources):
    return hashlib.sha256(_lock_path(resources).read_text(encoding='utf-8').encode()).hexdigest()


def worker_identity(resources):
    digest = hashlib.sha256()
    for name in ('hf_reverb.py', 'reverb_worker.py', 'tts_worker.py'):
        digest.update((Path(resources) / EXTENSION / name).read_text(encoding='utf-8').encode())
    return digest.hexdigest()


def dependencies_ready(root, resources):
    try:
        receipt = json.loads(_receipt(root).read_bytes())
        python = python_path(root)
        return (python.is_file() and receipt['environment'] == ENVIRONMENT and receipt['dependenciesVerified'] is True
                and receipt['lockSha256'] == lock_identity(resources)
                and Path(receipt['python']).resolve() == python.resolve() and receipt['device'] == 'cpu')
    except (OSError, ValueError, TypeError, KeyError):
        return False


def ensure_environment(manager, report):
    """Prepare the CPU worker on the first explicit Reverb install or repair."""
    with manager.setup_lock:
        root, resources = manager.root, manager.resources
        if dependencies_ready(root, resources):
            return
        if os.name != 'nt':
            raise RuntimeError('The pinned Reverb runtime is for Windows x64')
        if shutil.disk_usage(root).free < 2 * 1024**3:
            raise RuntimeError('Free at least 2 GB for the Reverb CPU runtime and retry')
        python = python_path(root)
        environment = python.parent.parent
        if not python.is_file():
            manager._run_setup([manager._base_python(), '-I', '-m', 'venv', environment], report,
                               'Creating the isolated Reverb speech runtime')
        prefix = 'import pathlib,sys; assert sys.prefix != sys.base_prefix; assert pathlib.Path(sys.prefix).resolve() == pathlib.Path(sys.argv[1]).resolve()'
        manager._run_setup([python, '-I', '-c', prefix, environment], report, 'Checking the Reverb environment', timeout=30)
        manager._run_setup([python, '-I', '-m', 'ensurepip', '--upgrade'], report, 'Preparing the Reverb package installer', timeout=120)
        manager._run_setup([python, '-I', '-m', 'pip', '--isolated', 'install', '--require-hashes', '--no-deps',
                            '--only-binary=:all:', '--no-cache-dir', '--force-reinstall', '--disable-pip-version-check',
                            '--index-url', 'https://pypi.org/simple', '-r', _lock_path(resources)],
                           report, 'Installing the pinned Reverb CPU dependencies')
        report('Checking Reverb, the audio codec and the text normalizer')
        check = subprocess.run(
            [str(python), '-I', '-X', 'utf8', '-c',
             'import torch,torchaudio,verbalis; from reverb_tts import TTS; '
             'from transformers import HiggsAudioV2TokenizerModel; '
             'assert torch.version.cuda is None and torch.__version__=="2.11.0+cpu"; '
             'assert torchaudio.__version__=="2.11.0+cpu"; '
             'assert verbalis.Verbalizer("en").process("12")'],
            capture_output=True, encoding='utf-8', errors='replace', timeout=120,
            env=dict(os.environ, CUDA_VISIBLE_DEVICES='-1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1'),
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if check.returncode:
            raise RuntimeError('The Reverb CPU worker failed its dependency check. ' + check.stderr[-1000:])
        atomic_json(_receipt(root), {
            'schema': 1, 'environment': ENVIRONMENT, 'python': str(python), 'device': 'cpu',
            'dependenciesVerified': True, 'lockSha256': lock_identity(resources),
        })
