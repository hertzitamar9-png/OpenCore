"""Managed local text-to-speech for Kitten TTS 2, VoxCPM2, Breeze-TTS-2, ZONOS3-Mini and dots.tts.edit.

The manager owns one isolated Python environment installed from a hashed lock,
the pinned third-party components those models need at run time, and one
persistent worker process (tts_worker.py) that keeps the selected model loaded.
Every model file is checked against its install receipt before it is loaded,
and the worker runs with the Hub offline, so synthesis never downloads code or
weights that OpenCore did not verify.
"""
import base64
import binascii
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from urllib.request import Request, urlopen
import uuid
import wave
import zipfile

from hf_download import Paused, atomic_json, managed_path
from hf_runtime import checked_model_files, _inside

ENVIRONMENT = 'tts-speech-v1'
WORKER_VERSION = 1
ACTIVE = {'queued', 'preparing', 'running'}
IDLE_SECONDS = 300
MAX_REFERENCE_BYTES = 12 * 1024 * 1024
MIN_DURATION_S, MIN_RMS = 0.5, 0.003
ENV_FREE_BYTES = 15 * 1024 ** 3
PYTORCH_INDEX = 'https://download.pytorch.org/whl/cu128'
USER_AGENT = 'OpenCore speech runtime setup'

COMPONENTS = {
    's3genMeanflow': {
        'label': 'Chatterbox Turbo S3Gen vocoder', 'kind': 'file',
        'repository': 'ResembleAI/chatterbox-turbo', 'revision': '749d1c1a46eb10492095d68fbcf55691ccf137cd',
        'filename': 's3gen_meanflow.safetensors', 'bytes': 1064875036,
        'sha256': 'd65cb687a2ed581ee6cc297e919ffefa63386944f42364ae13b78a594945514f', 'license': 'MIT',
    },
    'breezeSource': {
        'label': 'Breeze-TTS inference source', 'kind': 'source',
        'repository': 'breezeblue-ai/breeze-tts', 'revision': 'daa6cb2efe5471f09369229f322d20bb956cb3c3',
        'bytes': 219923, 'sha256': '5c559c6af0d04845c5174f99e970137320664b78cabef68060b42da493266ca4',
        'members': ('breeze_infer/', 'models/', 'LICENSE'), 'marker': 'breeze_infer/runtime.py',
        'license': 'Breeze-TTS license (non-commercial)',
    },
    'zonos3Source': {
        'label': 'ZONOS3 inference source', 'kind': 'source',
        'repository': 'Zyphra/ZONOS3-Mini', 'revision': 'f9863b31d0af6f4390bba183a0c9231601b70d61',
        'bytes': 41479827, 'sha256': '4922202528fd9b7a5100118761888ff3ad548fb9e4680326f4510fcbf9a4b1b9',
        'members': ('model/src/zonos3/', 'assets/steering/', 'LICENSE', 'NOTICE'),
        'marker': 'model/src/zonos3/reference/synth.py', 'license': 'MIT',
    },
    'dotsSource': {
        'label': 'dots.tts inference source', 'kind': 'source',
        'repository': 'studio-dots-ai/dots.tts', 'revision': '4cf7d6b13f42066f97784e7d7cf2a9ed5f956997',
        'bytes': 802981, 'sha256': '43e1960ea48b37fb0c5396e331a26b9517fff5052801645faadbd8497e58ba1e',
        'members': ('src/dots_tts/', 'LICENSE'), 'marker': 'src/dots_tts/edit_runtime.py', 'license': 'Apache-2.0',
    },
}

ZONOS3_LANGUAGES = [('en_us', 'English (US)'), ('en_gb', 'English (UK)'), ('fr_fr', 'French'), ('de', 'German'),
                    ('es', 'Spanish'), ('it', 'Italian'), ('pt_br', 'Portuguese (Brazil)'), ('ja', 'Japanese'),
                    ('cmn', 'Mandarin Chinese'), ('ko', 'Korean')]
ZONOS3_STYLES = ['Neutral', 'Angry', 'Happy', 'Sad', 'Calm', 'Scared', 'Confused', 'British newsreader',
                 'Elderly Scottish narrator', 'Young Australian', 'Gravelly old man', 'Soft-spoken Irish woman',
                 'Indian English caller', 'Chatty podcaster', 'Late-night radio']
DOTS_EMOTIONS = ['happy', 'angry', 'sad', 'afraid', 'disgusted', 'melancholic', 'surprised', 'calm']
# dots.tts.edit treats a request that contains one of its edit tags as an edit of the reference recording.
DOTS_EDIT_TAG = re.compile(r'<\s*/?\s*(?:sub|ins|del|emo|pitch|rate|pause|enhance|bg|spk_transfer)\b', re.IGNORECASE)

KITTEN_EMOTIONS = ['angry', 'contemplative', 'excited', 'joyful', 'mundane', 'nervous', 'sad', 'stern', 'surprised', 'tender']
KITTEN_EVENTS = ['gasp', 'giggle', 'growl', 'gulp', 'laugh', 'pause', 'scoff', 'sigh', 'sob', 'um']

FAMILIES = {
    'kitten-tts-2-v1': {
        'label': 'Kitten TTS 2', 'repository': 'KittenML/kitten-tts-2',
        'revision': 'f2eee74f4467855ad58fe16f1fb7b7df473263fd', 'components': ('s3genMeanflow',),
        'maxText': 5000, 'reference': 'transcript-required', 'instruction': None, 'voices': True,
        'presets': ['stable', 'expressive'], 'cfg': None, 'steps': None, 'sampling': True, 'normalize': True,
        'tags': [
            {'group': 'Emotion', 'insert': '[' + name + '] ', 'label': name} for name in KITTEN_EMOTIONS
        ] + [
            {'group': 'Vocal event', 'insert': '<' + name + '> ', 'label': name} for name in KITTEN_EVENTS
        ] + [{'group': 'Emphasis', 'insert': '(((word)))', 'label': 'emphasis', 'wrap': ['(((', ')))']}],
        'notes': 'Emotion tags apply to the sentence that follows them. Turn off text normalization for non-English text.',
        'hardware': {'gpuGiB': 3, 'bf16': False, 'cpu': True,
                     'summary': ('any computer, but slowly on the CPU (about 15 seconds of work per second of speech on a fast '
                                 'desktop CPU); an NVIDIA GPU with 3 GB of free memory is much faster')},
        'verification': {'text': 'OpenCore speech check. The local voice is ready.', 'settings': {'seed': 7}},
    },
    'voxcpm2-v1': {
        'label': 'VoxCPM2', 'repository': 'openbmb/VoxCPM2',
        'revision': '7936080b41163874f38610f46f438e2c0757f9fd', 'components': (),
        'maxText': 2000, 'reference': 'transcript-optional',
        'instruction': {'label': 'Voice design or style',
                        'placeholder': 'A warm, calm female narrator speaking slowly'},
        'voices': False, 'presets': None, 'cfg': {'min': 1.0, 'max': 5.0, 'default': 2.0, 'step': 0.1},
        'steps': {'min': 4, 'max': 30, 'default': 10}, 'sampling': False, 'normalize': False,
        'tags': [],
        'notes': 'Describe a voice to design one, or add a reference recording to clone it. Adding the transcript enables the highest-fidelity cloning.',
        'hardware': {'gpuGiB': 7, 'bf16': True, 'cpu': True,
                     'summary': ('an NVIDIA RTX 30-series or newer GPU with 7 GB of free memory; other computers use the CPU, '
                                 'which takes about 25 seconds of work per second of speech on a fast desktop CPU')},
        'verification': {'text': 'OpenCore speech check. The local voice is ready.',
                         'settings': {'seed': 7, 'cfg': 2.0, 'steps': 10}},
    },
    'breeze-tts-2-v1': {
        'label': 'Breeze-TTS-2', 'repository': 'BreezeBlue/Breeze-TTS-2',
        'revision': '3e28c5151381a722f1d8661b4118c298caa77aa4', 'components': ('breezeSource',),
        'maxText': 800, 'reference': 'transcript-required',
        'instruction': {'label': 'Voice direction', 'placeholder': 'Speak cheerfully, with a bright and fast delivery'},
        'voices': False, 'presets': None, 'cfg': {'min': 1.0, 'max': 6.0, 'default': 1.0, 'step': 0.1,
                                                  'hint': 'Around 4 works well with voice direction'},
        'steps': None, 'sampling': False, 'normalize': False,
        'tags': [{'group': 'Vocal event', 'insert': value + ' ', 'label': value} for value in
                 ('(laugh)', '(cough)', '(clears throat)', '(sigh)', '[笑]', '[咳嗽]', '[清嗓子]', '[叹气]')],
        'notes': 'Breeze-TTS-2 is licensed for non-commercial use. It is tuned for Taiwanese Mandarin and English.',
        'hardware': {'gpuGiB': 10, 'bf16': True, 'cpu': False,
                     'summary': ('an NVIDIA RTX 30-series or newer GPU with 10 GB of free memory (a 12 GB card or larger); '
                                 'it cannot run on the CPU')},
        'verification': {'text': 'OpenCore speech check. The local voice is ready.', 'settings': {'seed': 7}},
    },
    'zonos3-mini-v1': {
        'label': 'ZONOS3-Mini', 'repository': 'Zyphra/ZONOS3-Mini',
        'revision': '24f39592cf5521d675d96db41fc379af3ad369be', 'components': ('zonos3Source',),
        'maxText': 1000, 'reference': 'audio-only', 'instruction': None, 'voices': True,
        'voiceIndex': 'web/voices/voices.json', 'presets': ZONOS3_STYLES, 'cfg': None, 'steps': None,
        'sampling': True, 'normalize': True,
        'languages': [{'id': code, 'label': label} for code, label in ZONOS3_LANGUAGES],
        'tags': [],
        'notes': ('Pick a voice, or add a recording to clone any voice from the sound alone. Moods and the style presets '
                  'steer the delivery and accent. Speaks English, French, German, Spanish, Italian, Portuguese, Japanese, '
                  'Mandarin and Korean; choose the language when it differs from the voice.'),
        'hardware': {'gpuGiB': 6, 'bf16': True, 'cpu': True,
                     'summary': ('an NVIDIA RTX 30-series or newer GPU with 6 GB of free memory; other computers use the '
                                 'CPU, which is much slower')},
        'verification': {'text': 'OpenCore speech check. The local voice is ready.', 'settings': {'seed': 7}},
    },
    'dots-tts-edit-v1': {
        'label': 'dots.tts.edit', 'repository': 'dots-studio/dots.tts.edit',
        'revision': 'bef1e97b4d942c62843b92c30a68961d1902b6ec', 'components': ('dotsSource',),
        'maxText': 500, 'reference': 'transcript-optional', 'instruction': None, 'voices': False, 'presets': None,
        'cfg': {'min': 1.0, 'max': 3.0, 'default': 1.2, 'step': 0.1}, 'steps': {'min': 4, 'max': 32, 'default': 10},
        'sampling': False, 'normalize': False, 'editTags': True,
        'languages': [{'id': 'en', 'label': 'English'}, {'id': 'zh', 'label': 'Mandarin Chinese'}],
        'tags': [
            {'group': 'Edit the recording', 'label': 'replace words', 'insert': '<sub targ="new words">old words</sub>',
             'wrap': ['<sub targ="new words">', '</sub>']},
            {'group': 'Edit the recording', 'label': 'insert words', 'insert': '<ins>new words</ins>'},
            {'group': 'Edit the recording', 'label': 'delete words', 'insert': '<del>words</del>', 'wrap': ['<del>', '</del>']},
            {'group': 'Edit the recording', 'label': 'longer pause', 'insert': '<pause act="ins" level="2"/>'},
            {'group': 'Edit the recording', 'label': 'shorter pause', 'insert': '<pause act="red" level="2"/>'},
        ] + [
            {'group': 'Emotion (edit)', 'label': name, 'insert': '<emo type="' + name + '" level="2">words</emo>',
             'wrap': ['<emo type="' + name + '" level="2">', '</emo>']} for name in DOTS_EMOTIONS
        ] + [
            {'group': 'Emotion (edit)', 'label': 'describe it', 'insert': '<emo desc="quiet, warm delivery">words</emo>',
             'wrap': ['<emo desc="quiet, warm delivery">', '</emo>']},
            {'group': 'Pitch and speed (edit)', 'label': 'higher', 'insert': '<pitch, semitones=5>words</pitch>',
             'wrap': ['<pitch, semitones=5>', '</pitch>']},
            {'group': 'Pitch and speed (edit)', 'label': 'lower', 'insert': '<pitch, semitones=-5>words</pitch>',
             'wrap': ['<pitch, semitones=-5>', '</pitch>']},
            {'group': 'Pitch and speed (edit)', 'label': 'slower', 'insert': '<rate, factor=0.75>words</rate>',
             'wrap': ['<rate, factor=0.75>', '</rate>']},
            {'group': 'Pitch and speed (edit)', 'label': 'faster', 'insert': '<rate, factor=1.3>words</rate>',
             'wrap': ['<rate, factor=1.3>', '</rate>']},
        ],
        'notes': ('Add a recording to clone its voice (the transcript is optional), or leave it out for a new voice. To edit '
                  'the recording instead, type its exact words and wrap the part to change in an edit tag: replace, insert '
                  'or delete words, change the emotion, pitch, speed or a pause. English and Mandarin. Only clone or edit '
                  'voices with the speaker\'s consent, and disclose that the speech is AI-generated.'),
        'hardware': {'gpuGiB': 8, 'bf16': True, 'cpu': True,
                     'summary': ('an NVIDIA RTX 30-series or newer GPU with 8 GB of free memory; other computers use the '
                                 'CPU, which is much slower')},
        'verification': {'text': 'OpenCore speech check. The local voice is ready.', 'settings': {'seed': 7}},
    },
}

SETTINGS = {'voice', 'preset', 'instruction', 'referenceAudio', 'referenceText', 'cfg', 'steps', 'seed',
            'temperature', 'topP', 'topK', 'minP', 'normalize', 'maxNewTokens', 'language'}


def supports(entry):
    family = FAMILIES.get(entry.get('ttsRuntime'))
    return bool(family and entry.get('repository') == family['repository'] and entry.get('revision') == family['revision'])


def capabilities(entry):
    family = FAMILIES[entry['ttsRuntime']]
    return {key: copy.deepcopy(family.get(key)) for key in
            ('label', 'maxText', 'reference', 'instruction', 'voices', 'presets', 'cfg', 'steps', 'sampling', 'normalize', 'tags', 'notes',
             'hardware', 'languages', 'editTags')}


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


def hardware_fit(runtime, gpu):
    """Estimate before installation whether this computer can run a model; the worker makes the final check."""
    family = FAMILIES[runtime]
    hardware, label = family['hardware'], family['label']
    if not gpu:
        reason = 'no NVIDIA GPU was found'
    else:
        name, capability = gpu['name'], tuple(gpu['capability'])
        if capability < (7, 0):
            reason = 'the ' + name + ' is too old for the bundled PyTorch build'
        elif hardware['bf16'] and capability < (8, 0):
            reason = 'the ' + name + ' has no native BF16 support'
        elif gpu['totalGiB'] < hardware['gpuGiB'] + 1:
            reason = ('the ' + name + ' has ' + format(gpu['totalGiB'], '.0f') + ' GB of memory; '
                      + label + ' needs a card with about ' + format(hardware['gpuGiB'] + 2, 'g') + ' GB')
        else:
            return {'device': 'cuda', 'gpu': name, 'reason': None}
    if hardware['cpu']:
        return {'device': 'cpu', 'gpu': gpu and gpu['name'], 'reason': reason + ', so it will run on the CPU, which is much slower'}
    if ' needs a card ' not in reason:
        reason += '. ' + label + ' needs ' + hardware['summary']
    return {'device': None, 'gpu': gpu and gpu['name'], 'reason': reason}


def decode_reference(value):
    """Decode a browser-made reference clip: mono 16-bit PCM WAV, 8-48 kHz, 1-60 seconds."""
    if not isinstance(value, str) or len(value) > (MAX_REFERENCE_BYTES + 2) // 3 * 4:
        raise ValueError('The reference recording must be under 12 MB')
    try:
        data = base64.b64decode(value, validate=True)
        with wave.open(io.BytesIO(data), 'rb') as audio:
            rate, frames = audio.getframerate(), audio.getnframes()
            if audio.getcomptype() != 'NONE' or audio.getsampwidth() != 2 or audio.getnchannels() != 1 or not 8000 <= rate <= 48000:
                raise ValueError('Use a mono 16-bit PCM WAV reference recording')
            if not rate <= frames <= rate * 60:
                raise ValueError('Use a reference recording from 1 to 60 seconds long')
            if len(audio.readframes(frames)) != frames * 2:
                raise ValueError('The reference recording is incomplete')
    except (binascii.Error, wave.Error, EOFError):
        raise ValueError('The reference recording must be a valid PCM WAV file') from None
    return data


def _number(settings, name, low, high, integer=False):
    value = settings.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int if integer else (int, float)) or not low <= value <= high:
        raise ValueError(f'{name} must be {"an integer" if integer else "a number"} from {low} to {high}')
    return value


def validate_request(entry, request):
    if not isinstance(request, dict):
        raise ValueError('Invalid speech request')
    family = FAMILIES[entry['ttsRuntime']]
    text = request.get('text')
    if not isinstance(text, str) or not text.strip() or len(text) > family['maxText']:
        raise ValueError(f'Enter the words to speak, up to {family["maxText"]} characters')
    if any(ord(character) < 32 and character not in '\n\t' for character in text):
        raise ValueError('The text contains unsupported control characters')
    settings = request.get('settings') or {}
    if not isinstance(settings, dict) or set(settings) - SETTINGS:
        raise ValueError('This speech request contains unsupported controls')
    clean = {'seed': _number(settings, 'seed', 0, 2 ** 31 - 1, True)}
    if clean['seed'] is None:
        clean['seed'] = 1234
    reference = settings.get('referenceAudio')
    transcript = settings.get('referenceText')
    if transcript is not None and (not isinstance(transcript, str) or len(transcript) > 2000):
        raise ValueError('The reference transcript must be up to 2000 characters')
    transcript = (transcript or '').strip()
    if reference:
        decode_reference(reference)
        if family['reference'] == 'transcript-required' and not transcript:
            raise ValueError(f'{family["label"]} needs the exact words spoken in the reference recording')
        if family['reference'] == 'audio-only' and transcript:
            raise ValueError(f'{family["label"]} clones from the recording alone; leave the transcript empty')
        clean['referenceText'] = transcript
    elif transcript:
        raise ValueError('Add a reference recording for this transcript')
    if family.get('editTags') and DOTS_EDIT_TAG.search(text) and not reference:
        raise ValueError('Add the recording to edit; edit tags change a reference recording')
    language = settings.get('language')
    if language:
        if not family.get('languages') or language not in {item['id'] for item in family['languages']}:
            raise ValueError(f'Choose one of the languages {family["label"]} speaks')
        clean['language'] = language
    instruction = settings.get('instruction')
    if instruction:
        if not family['instruction']:
            raise ValueError(f'{family["label"]} does not take a voice instruction; use the expression tags instead')
        if not isinstance(instruction, str) or len(instruction) > 500:
            raise ValueError('The voice instruction must be up to 500 characters')
        clean['instruction'] = instruction.strip()
    voice = settings.get('voice')
    if voice:
        if not family['voices'] or not isinstance(voice, str) or len(voice) > 100:
            raise ValueError('Choose one of this model\'s voices')
        if not reference:
            clean['voice'] = voice
    preset = settings.get('preset')
    if preset:
        if not family['presets'] or preset not in family['presets']:
            raise ValueError('Choose a supported delivery preset')
        clean['preset'] = preset
    for name, spec in (('cfg', family['cfg']), ('steps', family['steps'])):
        if settings.get(name) is not None:
            if not spec:
                raise ValueError(f'{family["label"]} does not use {name}')
            clean[name] = _number(settings, name, spec['min'], spec['max'], name == 'steps')
    for name, low, high, integer in (('temperature', 0.05, 2.0, False), ('topP', 0.05, 1.0, False),
                                     ('topK', 1, 1000, True), ('minP', 0.0, 1.0, False), ('maxNewTokens', 50, 4000, True)):
        if settings.get(name) is not None:
            if not family['sampling']:
                raise ValueError(f'{family["label"]} does not use {name}')
            clean[name] = _number(settings, name, low, high, integer)
    if 'normalize' in settings:
        if type(settings['normalize']) is not bool or (not family['normalize'] and settings['normalize']):
            raise ValueError('Text normalization must be on or off')
        if family['normalize']:
            clean['normalize'] = settings['normalize']
    return {'modelId': request.get('modelId'), 'text': text.strip(), 'settings': clean, 'referenceAudio': reference or None}


def _hash(path, cancel=None):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        while block := source.read(4 * 1024 * 1024):
            if cancel is not None and cancel.is_set():
                raise Paused('Speech setup paused')
            digest.update(block)
    return digest.hexdigest()


def _download(url, target, size, sha256, cancel, report=None, label=''):
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.download')
    digest, received, last = hashlib.sha256(), 0, 0.0
    try:
        with urlopen(Request(url, headers={'User-Agent': USER_AGENT}), timeout=60) as response, temporary.open('wb') as stream:
            while chunk := response.read(1024 * 1024):
                if cancel is not None and cancel.is_set():
                    raise Paused('Speech setup paused')
                received += len(chunk)
                if received > size:
                    raise ValueError(label + ' download exceeded its verified size')
                digest.update(chunk)
                stream.write(chunk)
                if report and time.monotonic() - last > 1:
                    last = time.monotonic()
                    report(f'Downloading {label} · {received * 100 // size}%')
        if received != size or digest.hexdigest() != sha256:
            raise ValueError(label + ' failed its publisher checksum')
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


class TtsManager:
    def __init__(self, root, resources):
        self.root, self.resources = Path(root).resolve(), Path(resources).resolve()
        self.directory = self.root / 'studio/hub-tts-jobs'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock, self.wake = threading.RLock(), threading.Condition()
        self.setup_lock = threading.Lock()
        self.jobs, self.pending = [], []
        self.child, self.child_family, self.child_model, self.replies = None, None, None, None
        self.child_info, self.current, self.idle_since = {}, None, time.monotonic()
        self.verified_files, self.gpu = {}, False
        self.closed, self.setup_cancel = False, threading.Event()
        try:
            saved = json.loads((self.directory / 'jobs.json').read_text(encoding='utf-8'))
            if saved.get('schema') == 1 and isinstance(saved.get('jobs'), list):
                self.jobs = saved['jobs']
                for job in self.jobs:
                    if job.get('status') in ACTIVE:
                        job.update(status='interrupted', stage='Interrupted when OpenCore closed')
        except (OSError, ValueError, TypeError, AttributeError):
            self.jobs = []
        self.thread = threading.Thread(target=self._loop, name='opencore-tts', daemon=True)
        self.thread.start()

    # ── environment ─────────────────────────────────────────────────────────
    @property
    def environment(self):
        return self.root / 'runtime-setup/environments' / ENVIRONMENT

    def python(self):
        return self.environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')

    def _lock_file(self):
        return self.resources / 'runtime-setup/hf-extension/tts-speech-lock.txt'

    def _receipt_path(self):
        return self.root / 'runtime-setup/receipts/hub-tts-runtime.json'

    def lock_identity(self):
        return hashlib.sha256(self._lock_file().read_text(encoding='utf-8').replace('\r\n', '\n').encode()).hexdigest()

    def worker_identity(self):
        source = self.resources / 'runtime-setup/hf-extension/tts_worker.py'
        return hashlib.sha256(source.read_text(encoding='utf-8').replace('\r\n', '\n').encode()).hexdigest()

    def dependencies_ready(self):
        try:
            receipt = json.loads(self._receipt_path().read_text(encoding='utf-8'))
            return bool(receipt.get('environment') == ENVIRONMENT and receipt.get('dependenciesVerified') is True
                        and receipt.get('lockSha256') == self.lock_identity() and self.python().is_file()
                        and Path(receipt.get('python', '')).resolve() == self.python().resolve())
        except (OSError, ValueError, TypeError, AttributeError):
            return False

    def _base_python(self):
        candidates = [self.root / 'runtime-setup/python-3.12.10/python.exe', Path(sys.executable)]
        for candidate in candidates:
            if not candidate.is_file():
                continue
            try:
                result = subprocess.run([str(candidate), '-I', '-c', 'import sys,struct;print(sys.version_info[0],sys.version_info[1],struct.calcsize("P"))'],
                                        capture_output=True, text=True, timeout=30,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                if result.returncode == 0 and result.stdout.split() == ['3', '12', '8']:
                    return candidate
            except (OSError, subprocess.SubprocessError):
                continue
        raise RuntimeError('The speech runtime needs OpenCore\'s managed 64-bit Python 3.12. Repair OpenCore\'s runtime setup and try again')

    def _run_setup(self, command, report, stage, timeout=5400):
        log = self.directory / 'runtime-setup.log'
        report(stage)
        with log.open('ab') as stream:
            stream.write(('\n== ' + stage + ' ==\n').encode())
            stream.flush()
            child = subprocess.Popen([str(value) for value in command], stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                                     env=dict(os.environ, PYTHONIOENCODING='utf-8', PIP_DISABLE_PIP_VERSION_CHECK='1', PIP_NO_INPUT='1'),
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            started = time.monotonic()
            try:
                while child.poll() is None:
                    if self.setup_cancel.is_set():
                        raise Paused('Speech setup paused')
                    if time.monotonic() - started > timeout:
                        raise RuntimeError(stage + ' timed out')
                    time.sleep(0.5)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=30)
        if child.returncode:
            with log.open('rb') as source:
                source.seek(max(0, log.stat().st_size - 2500))
                detail = source.read().decode('utf-8', errors='replace').strip()
            raise RuntimeError(stage + ' failed. ' + detail[-1500:])

    def ensure_environment(self, report):
        with self.setup_lock:
            if self.dependencies_ready():
                return
            free = shutil.disk_usage(self.root).free
            if free < ENV_FREE_BYTES:
                raise RuntimeError('The speech runtime needs about 15 GB of free disk space to install')
            base = self._base_python()
            self._stop_worker()
            self._receipt_path().unlink(missing_ok=True)
            if self.environment.exists():
                shutil.rmtree(self.environment)
            lock = self._lock_file()
            self._run_setup([base, '-I', '-m', 'venv', self.environment], report, 'Creating the isolated speech environment')
            python = self.python()
            build_tools = self.directory / 'build-tools-lock.txt'
            build_tools.write_text(''.join(line + '\n' for line in lock.read_text(encoding='utf-8').splitlines()
                                           if line.lower().startswith('setuptools==')), encoding='utf-8')
            common = [python, '-I', '-m', 'pip', 'install', '--progress-bar', 'off', '--require-hashes', '--no-deps',
                      '--index-url', 'https://pypi.org/simple', '--extra-index-url', PYTORCH_INDEX]
            self._run_setup(common + ['-r', build_tools], report, 'Installing verified build tools')
            self._run_setup(common + ['--no-build-isolation', '-r', lock], report,
                            'Downloading and installing the speech runtime (PyTorch CUDA, about 4 GB)')
            probe = ('import json,importlib.util,torch,torchaudio,transformers,soundfile,librosa,kittenml,voxcpm,qwen_tts,'
                     'langcodes,loguru,torchdiffeq,zyphra_textnorm;'
                     'assert importlib.util.find_spec("dac"),"descript-audio-codec is missing";'
                     'print(json.dumps({"torch":torch.__version__,"cuda":torch.cuda.is_available(),'
                     '"gpu":torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}))')
            result = subprocess.run([str(python), '-I', '-c', probe], capture_output=True, text=True, timeout=600,
                                    env=dict(os.environ, PYTHONIOENCODING='utf-8', HF_HUB_OFFLINE='1'),
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise RuntimeError('The speech runtime failed its import check. ' + result.stderr[-1200:])
            hardware = json.loads(result.stdout.strip().splitlines()[-1])
            atomic_json(self._receipt_path(), {'schema': 1, 'environment': ENVIRONMENT, 'dependenciesVerified': True,
                                               'lockSha256': self.lock_identity(), 'python': str(python),
                                               'hardware': hardware, 'verifiedAt': time.time()})
            build_tools.unlink(missing_ok=True)

    # ── pinned components ───────────────────────────────────────────────────
    def component_path(self, name):
        spec = COMPONENTS[name]
        folder = managed_path(self.root, 'models', 'components', 'tts', spec['repository'].replace('/', '--'), spec['revision'])
        return folder / spec['filename'] if spec['kind'] == 'file' else folder / 'source'

    def component_ready(self, name, verify=False):
        spec, path = COMPONENTS[name], self.component_path(name)
        if spec['kind'] == 'file':
            if not path.is_file() or path.stat().st_size != spec['bytes']:
                return False
            if not verify:
                return True
            marker = (path.stat().st_size, path.stat().st_mtime_ns)
            if self.verified_files.get(str(path)) == marker:
                return True
            if _hash(path, self.setup_cancel) != spec['sha256']:
                return False
            self.verified_files[str(path)] = marker
            return True
        try:
            receipt = json.loads((path.parent / 'source-receipt.json').read_text(encoding='utf-8'))
            return receipt.get('sha256') == spec['sha256'] and (path / spec['marker']).is_file()
        except (OSError, ValueError, AttributeError):
            return False

    def ensure_component(self, name, report):
        spec, path = COMPONENTS[name], self.component_path(name)
        if self.component_ready(name, verify=True):
            return path
        if spec['kind'] == 'file':
            url = f'https://huggingface.co/{spec["repository"]}/resolve/{spec["revision"]}/{spec["filename"]}'
            _download(url, path, spec['bytes'], spec['sha256'], self.setup_cancel, report, spec['label'])
        else:
            archive = path.parent / 'source.zip'
            url = f'https://codeload.github.com/{spec["repository"]}/zip/{spec["revision"]}'
            _download(url, archive, spec['bytes'], spec['sha256'], self.setup_cancel, report, spec['label'])
            staging = path.parent / 'source.partial'
            if staging.exists():
                shutil.rmtree(staging)
            prefix = spec['repository'].split('/')[1] + '-' + spec['revision'] + '/'
            with zipfile.ZipFile(archive) as bundle:
                for member in bundle.infolist():
                    name_in_zip = member.filename.replace('\\', '/')
                    if not name_in_zip.startswith(prefix):
                        raise ValueError('The ' + spec['label'] + ' archive has an unexpected layout')
                    relative = name_in_zip[len(prefix):]
                    if not relative or not any(relative == item or relative.startswith(item) for item in spec['members']):
                        continue
                    parts = relative.split('/')
                    if any(part in ('', '.', '..') or ':' in part for part in parts[:-1]) or relative.startswith('/'):
                        raise ValueError('The ' + spec['label'] + ' archive contains an unsafe path')
                    destination = _inside(staging / relative, staging) if parts[-1] else None
                    if member.is_dir() or destination is None:
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(member) as source, destination.open('wb') as output:
                        shutil.copyfileobj(source, output)
            if path.exists():
                shutil.rmtree(path)
            os.replace(staging, path)
            archive.unlink(missing_ok=True)
            atomic_json(path.parent / 'source-receipt.json', {'repository': spec['repository'], 'revision': spec['revision'],
                                                              'sha256': spec['sha256'], 'extractedAt': time.time()})
        if not self.component_ready(name, verify=True):
            raise RuntimeError(spec['label'] + ' did not pass verification after download')
        return path

    # ── readiness ───────────────────────────────────────────────────────────
    def ready(self, entry, proof):
        try:
            family = FAMILIES[entry['ttsRuntime']]
            return bool(supports(entry) and entry.get('status') == 'installed' and isinstance(proof, dict)
                        and proof.get('inferenceVerified') is True and proof.get('receiptRevision') == entry['revision']
                        and proof.get('ttsRuntime') == entry['ttsRuntime'] and proof.get('workerVersion') == WORKER_VERSION
                        and proof.get('workerSha256') == self.worker_identity() and proof.get('lockSha256') == self.lock_identity()
                        and self.dependencies_ready() and all(self.component_ready(name) for name in family['components']))
        except (OSError, ValueError, TypeError, KeyError):
            return False

    def fit(self, entry):
        if self.gpu is False:
            self.gpu = nvidia_gpu()
        return hardware_fit(entry['ttsRuntime'], self.gpu) if supports(entry) else None

    def model_directory(self, entry):
        folder = _inside(entry['localDirectory'], self.root / 'models/hub')
        return _inside(folder / entry['subdirectory'], folder) if entry.get('subdirectory') else folder

    def voices(self, entry):
        if not supports(entry) or not FAMILIES[entry['ttsRuntime']]['voices'] or entry.get('status') != 'installed':
            return []
        index = FAMILIES[entry['ttsRuntime']].get('voiceIndex') or 'voices/voices.json'
        try:
            data = json.loads((self.model_directory(entry) / index).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return []
        voices = []
        if isinstance(data, list):
            from tts_worker import zonos3_voice
            for item in data:
                voice = zonos3_voice(item) if isinstance(item, dict) else None
                if voice and len(voice['id']) <= 100 and all(voice['id'] != known['id'] for known in voices):
                    voices.append(voice)
            return voices[:400]
        for name, spec in sorted(data.items()) if isinstance(data, dict) else []:
            if not isinstance(name, str) or len(name) > 100:
                continue
            details = {key: value for key, value in (spec or {}).items()
                       if isinstance(value, (str, int, float)) and key not in ('reference', 'artifacts') and len(str(value)) <= 300}
            voices.append({'id': name, **details})
        return voices[:400]

    def status(self, entries=()):
        with self.lock:
            busy = self.current['id'] if self.current else None
            return {'environment': ENVIRONMENT, 'dependenciesReady': self.dependencies_ready(),
                    'loadedModelId': self.child_model if self.child and self.child.poll() is None else None,
                    'device': self.child_info.get('device'), 'gpu': self.child_info.get('gpu'),
                    'deviceNote': self.child_info.get('deviceNote'),
                    'busyJobId': busy, 'queued': len(self.pending),
                    'models': {entry['id']: {'capabilities': capabilities(entry), 'voices': self.voices(entry),
                                             'components': [{**{k: COMPONENTS[name][k] for k in ('label', 'license', 'repository', 'revision')},
                                                             'ready': self.component_ready(name)} for name in FAMILIES[entry['ttsRuntime']]['components']]}
                               for entry in entries if supports(entry)}}

    # ── jobs ────────────────────────────────────────────────────────────────
    def _save(self):
        atomic_json(self.directory / 'jobs.json', {'schema': 1, 'jobs': self.jobs[-200:]})

    def _update(self, job, **value):
        with self.lock:
            job.update(value, updatedAt=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
            self._save()

    def list(self, model_id=None):
        with self.lock:
            rows = [job for job in self.jobs if not job.get('internalVerification')
                    and (model_id is None or job['request'].get('modelId') == model_id)]
            return copy.deepcopy(list(reversed(rows[-100:])))

    def job(self, identity):
        with self.lock:
            job = next((job for job in self.jobs if job['id'] == identity), None)
            return copy.deepcopy(job)

    def submit(self, entry, request, internal=False):
        if not supports(entry) or entry.get('status') != 'installed':
            raise ValueError('Download this speech model first')
        clean = validate_request(entry, request)
        if clean['modelId'] != entry['id']:
            raise ValueError('The speech request selects a different model')
        voices = {voice['id'] for voice in self.voices(entry)}
        if clean['settings'].get('voice') and voices and clean['settings']['voice'] not in voices:
            raise ValueError('Choose one of this model\'s voices')
        identity = 'hub-tts-' + uuid.uuid4().hex
        folder = managed_path(self.directory, identity)
        folder.mkdir()
        if clean.pop('referenceAudio'):
            reference = folder / 'reference.wav'
            reference.write_bytes(decode_reference(request['settings']['referenceAudio']))
            clean['settings']['referencePath'] = str(reference)
        job = {'id': identity, 'source': 'hub-tts', 'category': 'tts', 'request': clean, 'status': 'queued',
               'stage': 'Queued', 'createdAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'outputs': [],
               'error': None, 'result': None, 'internalVerification': internal, 'ttsRuntime': entry['ttsRuntime']}
        with self.lock:
            self.jobs.append(job)
            self._save()
        with self.wake:
            self.pending.append((job, copy.deepcopy(entry), threading.Event()))
            self.wake.notify_all()
        return copy.deepcopy(job)

    def cancel(self, identity):
        with self.wake:
            waiting = next((item for item in self.pending if item[0]['id'] == identity), None)
            if waiting:
                self.pending.remove(waiting)
                self._update(waiting[0], status='cancelled', stage='Cancelled')
                waiting[2].set()
                return {'cancelled': True}
        with self.lock:
            if not self.current or self.current['id'] != identity:
                raise ValueError('This speech job is not active')
            self.current['cancelRequested'] = True
        self.setup_cancel.set()
        self._stop_worker()
        return {'cancelled': True}

    def preview(self, identity):
        with self.lock:
            job = next((job for job in self.jobs if job['id'] == identity), None)
        if not job or job.get('status') != 'completed' or not job.get('outputs'):
            raise ValueError('Choose a finished speech job')
        file = _inside(job['outputs'][0], self.directory / identity)
        if file.suffix.lower() != '.wav' or not 0 < file.stat().st_size <= 96 * 1024 ** 2:
            raise ValueError('Invalid speech output')
        return {'mime': 'audio/wav', 'fileName': 'opencore-speech-' + identity[-8:] + '.wav',
                'dataUrl': 'data:audio/wav;base64,' + base64.b64encode(file.read_bytes()).decode()}

    def delete(self, identity):
        with self.lock:
            job = next((job for job in self.jobs if job['id'] == identity), None)
            if not job or job.get('status') in ACTIVE:
                raise ValueError('Choose a finished speech job to delete')
            self.jobs.remove(job)
            self._save()
        shutil.rmtree(managed_path(self.directory, identity), ignore_errors=True)
        return {'deleted': True}

    # ── worker process ──────────────────────────────────────────────────────
    def _stop_worker(self):
        with self.lock:
            child, self.child, self.child_family, self.child_model, self.child_info = self.child, None, None, None, {}
        if child and child.poll() is None:
            try:
                child.stdin.write(b'{"op":"exit"}\n')
                child.stdin.flush()
                child.wait(timeout=5)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
            if child.poll() is None:
                from hf_specialist_ocr import OCRManager
                OCRManager._terminate(child)
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()

    def _start_worker(self):
        log = self.directory / 'worker.log'
        if log.is_file() and log.stat().st_size > 8 * 1024 ** 2:
            log.replace(self.directory / 'worker.previous.log')
        cache = self.root / 'runtime-setup/cache/tts-speech-v1'
        cache.mkdir(parents=True, exist_ok=True)
        environment = dict(os.environ, PYTHONIOENCODING='utf-8', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                           HF_HUB_DISABLE_TELEMETRY='1', HF_HOME=str(cache / 'hf'), TORCH_HOME=str(cache / 'torch'),
                           NUMBA_CACHE_DIR=str(cache / 'numba'), TOKENIZERS_PARALLELISM='false')
        stream = log.open('ab')
        try:
            # -X utf8: model code reads its JSON with the default codec, which is not UTF-8 on many Windows locales.
            child = subprocess.Popen([str(self.python()), '-I', '-X', 'utf8',
                                      str(self.resources / 'runtime-setup/hf-extension/tts_worker.py')],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stream, env=environment, cwd=str(cache),
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0))
        finally:
            stream.close()
        replies = queue.Queue()

        def read():
            for line in child.stdout:
                try:
                    replies.put(json.loads(line.decode('utf-8')))
                except ValueError:
                    continue
            replies.put(None)
        threading.Thread(target=read, daemon=True).start()
        with self.lock:
            self.child, self.replies = child, replies
        started = self._reply(child, replies, 180, 'Starting the speech worker')
        if started.get('event') != 'started':
            raise RuntimeError('The speech worker did not start')
        return child

    def _reply(self, child, replies, timeout, stage):
        deadline = time.monotonic() + timeout
        while True:
            try:
                value = replies.get(timeout=0.5)
            except queue.Empty:
                with self.lock:
                    cancelled = bool(self.current and self.current.get('cancelRequested'))
                if cancelled:
                    raise RuntimeError('Speech generation cancelled')
                if time.monotonic() > deadline:
                    self._stop_worker()
                    raise RuntimeError(stage + ' timed out')
                continue
            if value is None:
                with self.lock:
                    cancelled = bool(self.current and self.current.get('cancelRequested'))
                if cancelled:
                    raise RuntimeError('Speech generation cancelled')
                raise RuntimeError(stage + ' failed: the speech worker exited. ' + self._log_tail())
            if not value.get('ok'):
                raise RuntimeError(str(value.get('error') or stage + ' failed')[:1500])
            return value

    def _log_tail(self):
        try:
            log = self.directory / 'worker.log'
            with log.open('rb') as source:
                source.seek(max(0, log.stat().st_size - 1500))
                return source.read().decode('utf-8', errors='replace').strip()[-1000:]
        except OSError:
            return ''

    def _send(self, message, timeout, stage):
        child, replies = self.child, self.replies
        if not child or child.poll() is not None:
            raise RuntimeError('The speech worker is not running')
        child.stdin.write((json.dumps(message, separators=(',', ':')) + '\n').encode('utf-8'))
        child.stdin.flush()
        return self._reply(child, replies, timeout, stage)

    def _load(self, entry, job):
        family = entry['ttsRuntime']
        if self.child and self.child.poll() is None and self.child_model == entry['id']:
            return
        self._stop_worker()
        self._update(job, status='preparing', stage='Checking the speech runtime')
        report = lambda stage: self._update(job, stage=stage)
        self.ensure_environment(report)
        components = {}
        for name in FAMILIES[family]['components']:
            components[name] = str(self.ensure_component(name, report))
        self._update(job, stage='Verifying the installed model files')
        directory = self.model_directory(entry)
        base = _inside(entry['localDirectory'], self.root / 'models/hub')
        marker = tuple(sorted((pin['filename'], (base / pin['filename']).stat().st_size,
                               (base / pin['filename']).stat().st_mtime_ns) for pin in entry['files']))
        if self.verified_files.get(entry['id']) != marker:
            checked_model_files(entry, self.root, self.setup_cancel)
            self.verified_files[entry['id']] = marker
        self._update(job, stage='Starting the speech worker')
        self._start_worker()
        self._update(job, stage='Loading ' + FAMILIES[family]['label'] + ' into memory')
        hardware = dict(FAMILIES[family]['hardware'], label=FAMILIES[family]['label'])
        info = self._send({'op': 'load', 'family': family, 'modelDir': str(directory), 'components': components,
                           'hardware': hardware}, 1800,
                          'Loading the speech model')
        with self.lock:
            self.child_family, self.child_model = family, entry['id']
            self.child_info = {key: info.get(key) for key in ('device', 'gpu', 'deviceNote', 'sampleRate', 'loadMs')}

    def _execute(self, job, entry):
        with self.lock:
            self.current = job
        self.setup_cancel.clear()
        try:
            self._load(entry, job)
            self._update(job, status='running', stage='Generating speech')
            output = managed_path(self.directory, job['id'], 'speech.wav')
            result = self._send({'op': 'synthesize', 'id': job['id'], 'text': job['request']['text'],
                                 'settings': job['request']['settings'], 'output': str(output)},
                                # The CPU fallback can need tens of seconds per second of speech.
                                1800 if self.child_info.get('device') == 'cuda' else max(1800, 8 * len(job['request']['text'])),
                                'Generating speech')
            path = _inside(result['path'], self.directory / job['id'])
            if path != output or not path.is_file():
                raise RuntimeError('The speech worker returned no audio file')
            if result.get('finite') is not True or result.get('durationS', 0) < MIN_DURATION_S or result.get('rms', 0) < MIN_RMS:
                raise RuntimeError('The generated audio was silent or too short; try different text or settings')
            summary = {key: result.get(key) for key in ('sampleRate', 'durationS', 'rms', 'peak', 'elapsedMs')}
            self._update(job, status='completed', stage='Speech ready', outputs=[str(path)], result=summary)
        except Exception as error:
            cancelled = bool(job.get('cancelRequested')) or isinstance(error, Paused)
            self._update(job, status='cancelled' if cancelled else 'failed',
                         stage='Cancelled' if cancelled else 'Failed', error=None if cancelled else str(error)[:2000])
        finally:
            job.pop('cancelRequested', None)
            with self.lock:
                self.current = None
                self.idle_since = time.monotonic()
                self._save()

    def _loop(self):
        while True:
            idle = False
            with self.wake:
                while not self.pending and not self.closed:
                    self.wake.wait(timeout=15)
                    if not self.pending and self.child and time.monotonic() - self.idle_since > IDLE_SECONDS:
                        idle = True
                        break
                if self.closed:
                    return
                item = None if idle else self.pending.pop(0)
            if item is None:
                # Unload on this thread so a job that arrives now waits for the
                # old worker to exit instead of racing a background stop.
                self._stop_worker()
                continue
            job, entry, done = item
            try:
                self._execute(job, entry)
            finally:
                done.set()

    # ── verification and lifecycle ──────────────────────────────────────────
    def prepare(self, entry, report, pause):
        family = FAMILIES[entry['ttsRuntime']]
        report({'stage': 'verifying-inference', 'detail': 'Installing the speech runtime and generating a verification clip'})
        job = self.submit(entry, {'modelId': entry['id'], 'text': family['verification']['text'],
                                  'settings': dict(family['verification']['settings'])}, internal=True)
        last = None
        while True:
            current = self.job(job['id'])
            if current['status'] not in ACTIVE:
                break
            if pause.is_set():
                try:
                    self.cancel(job['id'])
                except ValueError:
                    pass
                raise Paused('Speech preparation paused')
            if current['stage'] != last:
                last = current['stage']
                report({'stage': 'verifying-inference', 'detail': last})
            time.sleep(0.5)
        if current['status'] != 'completed':
            raise RuntimeError(current.get('error') or 'Speech verification did not finish')
        result = current['result']
        return {'runtimeReady': True, 'inferenceVerified': True, 'receiptRevision': entry['revision'],
                'ttsRuntime': entry['ttsRuntime'], 'workerVersion': WORKER_VERSION, 'workerSha256': self.worker_identity(),
                'lockSha256': self.lock_identity(), 'verifiedAt': time.time(), 'sampleRate': result['sampleRate'],
                'durationS': result['durationS'], 'rms': result['rms'], 'device': self.child_info.get('device'),
                'gpu': self.child_info.get('gpu'), 'verificationJobId': job['id']}

    def busy(self, model_id=None):
        with self.wake:
            items = [item[0] for item in self.pending] + ([self.current] if self.current else [])
            return any(model_id is None or item['request'].get('modelId') == model_id for item in items)

    def pause(self):
        with self.wake:
            for job, _, done in list(self.pending):
                self._update(job, status='cancelled', stage='Cancelled')
                done.set()
            self.pending.clear()
        with self.lock:
            if self.current:
                self.current['cancelRequested'] = True
        self.setup_cancel.set()
        self._stop_worker()

    def release(self, model_id):
        if self.busy(model_id):
            raise ValueError('Wait for speech generation to finish before removing this model')
        if self.child_model == model_id:
            self._stop_worker()
        self.verified_files.pop(model_id, None)

    def close(self):
        with self.wake:
            self.closed = True
            self.wake.notify_all()
        self.pause()
