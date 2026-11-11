"""Persistent local speech synthesis worker for OpenCore's managed TTS runtime.

One JSON request per line on stdin, one JSON reply per line on the original
stdout. Library output is redirected to stderr, which the manager logs. The
worker holds at most one model; the manager restarts it to switch models or to
interrupt a generation. It never downloads: every path it receives was verified
by the manager, and HF_HUB_OFFLINE is set by its parent.
"""
import base64
import json
import math
import os
from pathlib import Path
import random
import re
import sys
import time
import traceback
import types

PROTOCOL = 1
QWEN3_TTS = ('qwen3-tts-0.6b-base-v1', 'qwen3-tts-1.7b-base-v1', 'qwen3-tts-0.6b-customvoice-v1',
             'qwen3-tts-1.7b-customvoice-v1', 'qwen3-tts-1.7b-voicedesign-v1')
FAMILIES = ('kitten-tts-2-v1', 'voxcpm2-v1', 'breeze-tts-2-v1', 'zonos3-mini-v1', 'dots-tts-edit-v1') + QWEN3_TTS
ZONOS3_REPOSITORY = 'Zyphra/ZONOS3-Mini'
ZONOS3_BUNDLE = 'assets/steering/studio_bundle_mect60k_x10.json'
ZONOS3_PRESET_NAMES = {'BBC newsreader': 'British newsreader'}
DOTS_EDIT_TAG = re.compile(r'<\s*/?\s*(?:sub|ins|del|emo|pitch|rate|pause|enhance|bg|spk_transfer)\b', re.IGNORECASE)
ZONOS3_LANGUAGE_CODES = {'fr': 'fr_fr', 'de': 'de', 'es': 'es', 'it': 'it', 'pt': 'pt_br', 'ko': 'ko', 'zh': 'cmn', 'ja': 'ja'}
# Speakers recorded in a language ZONOS3 has no text front end for; they read one of its own languages.
ZONOS3_SPEAKER_LANGUAGES = {'hi': 'Hindi', 'te': 'Telugu'}
ZONOS3_STYLE_WORDS = {'announcervoice': 'announcer', 'asrm': 'ASMR'}


def zonos3_voice(item):
    """A ZONOS3 release voice ('Cillian en Irish M 1') as its name, language code, gender and style."""
    parts = str(item.get('label') or '').split()
    if len(parts) < 4 or parts[-2] not in ('M', 'F'):
        return None
    code, style = parts[1], [ZONOS3_STYLE_WORDS.get(word.lower(), word) for word in parts[2:-2]]
    if code == 'en':
        language = 'en_gb' if 'British' in style else 'en_us'
    elif code in ZONOS3_LANGUAGE_CODES:
        language = ZONOS3_LANGUAGE_CODES[code]
    else:
        language = 'en_us'
        style.insert(0, ZONOS3_SPEAKER_LANGUAGES.get(code, code) + ' speaker')
    return {'id': parts[0], 'language': language, 'gender': 'female' if parts[-2] == 'F' else 'male', 'style': ' '.join(style)}


def _seed(value):
    import numpy as np
    import torch
    random.seed(value)
    np.random.seed(value % 2**32)
    torch.manual_seed(value)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(value)


def _arch_supported(torch, major, minor):
    """True when the installed PyTorch build has kernels this GPU can run."""
    for arch in torch.cuda.get_arch_list():
        kind, _, digits = arch.partition('_')
        if not digits.isdigit() or len(digits) < 2:
            continue
        built = (int(digits[:-1]), int(digits[-1]))
        if kind == 'sm' and built[0] == major and built[1] <= minor:
            return True
        if kind == 'compute' and built <= (major, minor):
            return True
    return False


def choose_device(hardware):
    """Use the GPU only when this model can really run there; otherwise fall back or explain why."""
    import torch
    hardware = hardware or {}
    label = hardware.get('label') or 'This model'
    need = float(hardware.get('gpuGiB') or 0)
    if not torch.cuda.is_available():
        reason = 'No NVIDIA GPU with a working CUDA driver was found'
    else:
        name = torch.cuda.get_device_name(0)
        major, minor = torch.cuda.get_device_capability(0)
        free, _ = torch.cuda.mem_get_info(0)
        if not _arch_supported(torch, major, minor):
            reason = 'The ' + name + ' is not supported by the bundled PyTorch build'
        elif hardware.get('bf16') and not torch.cuda.is_bf16_supported(including_emulation=False):
            reason = 'The ' + name + ' has no native BF16 support, which ' + label + ' needs on a GPU'
        elif free < need * 1024 ** 3:
            reason = ('The ' + name + ' has ' + format(free / 1024 ** 3, '.1f') + ' GB of free memory and ' + label
                      + ' needs about ' + format(need, 'g') + ' GB; unload other models or close other GPU apps to use it')
        else:
            return 'cuda', None
    if hardware.get('cpu', True):
        return 'cpu', reason + '. Running on the CPU instead, which is much slower.'
    raise RuntimeError(label + ' needs ' + (hardware.get('summary') or 'a supported NVIDIA GPU') + '. ' + reason + '.')


def _add_path(path):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _module(name, **attributes):
    module = types.ModuleType(name)
    module.__dict__.update(attributes)
    sys.modules[name] = module
    return module


def _force_weights_only():
    """Load every checkpoint in this worker with PyTorch's restricted unpickler, even where upstream code asks for the
    unrestricted one. Tensors, numbers and plain containers load the same; pickled code can never run."""
    import torch
    if getattr(torch.load, 'opencore_weights_only', False):
        return
    original = torch.load

    def load(*args, **kwargs):
        kwargs['weights_only'] = True
        return original(*args, **kwargs)
    load.opencore_weights_only = True
    torch.load = load


def _install_dac_layers():
    """ZONOS3 uses only the layer and quantizer modules of descript-audio-codec. Import them directly, so the package's
    training stack (audiotools and its many dependencies) is never needed."""
    import importlib.util
    import torch
    spec = importlib.util.find_spec('dac')
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError('descript-audio-codec is missing from the speech environment; repair the speech runtime')
    root = Path(next(iter(spec.submodule_search_locations)))
    _module('dac', __path__=[str(root)])
    _module('dac.nn', __path__=[str(root / 'nn')])
    _module('dac.model', __path__=[])
    _module('dac.model.base', CodecMixin=type('CodecMixin', (), {}))
    _module('audiotools', __path__=[])
    _module('audiotools.ml', BaseModel=type('BaseModel', (torch.nn.Module,), {'INTERN': [], 'EXTERN': []}))


def _install_dots_text_tools():
    """dots.tts imports the WeTextProcessing normalizers and the lingua language detector at module level. OpenCore always
    passes normalize_text=False and either no language tag or an explicit one, so neither runs; these stand-ins keep the
    imports working and fail clearly if a code path ever reaches them."""
    def unavailable(*_, **__):
        raise RuntimeError('Automatic text normalization and language detection are not part of the OpenCore speech '
                           'runtime; choose the language instead')
    normalizer = type('Normalizer', (), {'__init__': unavailable})
    _module('tn', __path__=[])
    for language in ('chinese', 'english'):
        _module('tn.' + language, __path__=[])
        _module('tn.' + language + '.normalizer', Normalizer=normalizer)
    _module('lingua', Language=type('Language', (), {'all': staticmethod(unavailable)}),
            LanguageDetectorBuilder=type('LanguageDetectorBuilder', (), {'from_languages': staticmethod(unavailable)}))


def _zonos3_offline(model_dir):
    """Map the release's hf://Zyphra/ZONOS3-Mini/... locations to the verified local snapshot and refuse any other."""
    from zonos3.utils import release

    def local(path):
        if path is None or not release.is_hf(str(path)):
            return path if path is None else os.path.expanduser(str(path))
        repository, inside = release.split(str(path))
        if repository != ZONOS3_REPOSITORY:
            raise RuntimeError('OpenCore runs ZONOS3 offline; ' + str(path) + ' is not a managed file')
        return str(model_dir / inside) if inside else str(model_dir)
    release.resolve_file = release.resolve_dir_or_local = local


def _zonos3_assets(base):
    class Assets(base):
        def decoder(self, codec, device, decoder=None):
            # The manager verified generator.pt at install time; upstream re-hashes all 8 GB of it on every load.
            from zonos3.tokenizer import matched_codec
            key = (codec, matched_codec.decoder_id(codec, decoder), str(device))
            if key not in self._decoders:
                self._decoders[key] = matched_codec.load_model(codec, device, decoder=key[1])
            return self._decoders[key]
    return Assets


class Engine:
    def __init__(self, family, model_dir, components, hardware=None):
        self.family, self.model_dir, self.components = family, Path(model_dir), components
        self.device, self.device_note = choose_device(hardware)
        self.voices, self.presets = [], []
        if family == 'kitten-tts-2-v1':
            import kittenml.kittentts2.vocoder as vocoder
            weights = components['s3genMeanflow']

            def offline(repo_id=None, filename=None, **_):
                if repo_id == vocoder.CHATTERBOX_REPO and filename == vocoder.S3GEN_WEIGHTS:
                    return weights
                raise RuntimeError('OpenCore runs Kitten TTS 2 offline; ' + str(repo_id) + ' is not a managed component')
            vocoder.hf_hub_download = offline
            from kittenml import KittenTTS
            self.model = KittenTTS(str(self.model_dir), device=self.device)
            self.sample_rate = int(self.model.sample_rate)
            self.voices = list(self.model.available_voices)
            self.presets = list(self.model.decode_presets) or ['stable']
        elif family == 'voxcpm2-v1':
            from voxcpm import VoxCPM
            self.model = VoxCPM(voxcpm_model_path=str(self.model_dir), zipenhancer_model_path=None,
                                enable_denoiser=False, optimize=False, device=self.device)
            self.sample_rate = int(self.model.tts_model.sample_rate)
        elif family == 'breeze-tts-2-v1':
            source = components['breezeSource']
            if source not in sys.path:
                sys.path.insert(0, source)
            from breeze_infer.runtime import load_runtime, update_generation_config_for_breeze
            from models.fast_streaming import FastBreezeStreamingRuntime, FastStreamingConfig
            tokenizer, model, codec = load_runtime(self.model_dir, device='cuda:0' if self.device == 'cuda' else 'cpu',
                                                   attn_implementation='eager')
            update_generation_config_for_breeze(model)
            config = FastStreamingConfig(max_new_tokens=1500, max_seq_len=2048, repetition_penalty=1.1)
            self.tokenizer, self.model, self.codec = tokenizer, model, codec
            self.runtime = FastBreezeStreamingRuntime(model, codec, config, tokenizer=tokenizer)
            self.sample_rate = int(self.runtime.sample_rate)
        elif family == 'zonos3-mini-v1':
            self._load_zonos3(Path(components['zonos3Source']))
        elif family == 'dots-tts-edit-v1':
            import torch
            _add_path(Path(components['dotsSource']) / 'src')
            _install_dots_text_tools()
            _force_weights_only()
            threads, available = torch.get_num_threads(), torch.cuda.is_available
            if self.device == 'cpu':
                # dots.tts picks CUDA whenever it is visible; keep it on the CPU when OpenCore chose the CPU (too little
                # GPU memory, an unsupported GPU), and keep every CPU core it would otherwise drop to one thread.
                torch.cuda.is_available = lambda: False
            try:
                from dots_tts.edit_runtime import DotsTtsEditRuntime
                self.model = DotsTtsEditRuntime.from_pretrained(
                    str(self.model_dir), precision='bfloat16' if self.device == 'cuda' else 'float32')
            finally:
                torch.cuda.is_available = available
                torch.set_num_threads(threads)
            self.sample_rate = int(self.model.sample_rate)
        elif family in QWEN3_TTS:
            self._load_qwen3()
        else:
            raise ValueError('Unsupported speech family')

    def _load_qwen3(self):
        import torch
        from qwen_tts import Qwen3TTSModel
        # Loaded from the verified local folder only; qwen-tts downloads the speech tokenizer when given a repository id.
        self.model = Qwen3TTSModel.from_pretrained(
            str(self.model_dir), device_map='cuda:0' if self.device == 'cuda' else 'cpu',
            dtype=torch.bfloat16 if self.device == 'cuda' else torch.float32, local_files_only=True)
        self.qwen3_kind = str(self.model.model.tts_model_type)
        expected = 'base' if 'base' in self.family else 'voice_design' if 'voicedesign' in self.family else 'custom_voice'
        if self.qwen3_kind != expected:
            raise ValueError('The Qwen3 TTS checkpoint is a ' + self.qwen3_kind + ' model, not ' + expected)
        self.voices = sorted(str(name) for name in (self.model.get_supported_speakers() or []))
        self.sample_rate = int(self.model.model.speech_tokenizer.get_output_sample_rate())

    def _qwen3(self, text, settings, reference, reference_text, instruction):
        import soundfile
        options = {'language': settings.get('language') or 'auto',
                   'max_new_tokens': int(settings.get('maxNewTokens', 2048))}
        for name, key in (('temperature', 'temperature'), ('topP', 'top_p'), ('topK', 'top_k')):
            if settings.get(name) is not None:
                options[key] = settings[name]
        if self.qwen3_kind == 'base':
            if not reference:
                raise ValueError('Add a reference recording of the voice to clone')
            audio, rate = soundfile.read(reference, dtype='float32', always_2d=False)
            # With the transcript the model continues the recording (closest match); without it only the voice
            # timbre (speaker embedding) is used.
            wavs, rate = self.model.generate_voice_clone(text=text, ref_audio=(audio, int(rate)), ref_text=reference_text,
                                                         x_vector_only_mode=not reference_text, **options)
        elif self.qwen3_kind == 'voice_design':
            wavs, rate = self.model.generate_voice_design(text=text, instruct=instruction or '', **options)
        else:
            voice = settings.get('voice') or 'Ryan'
            if voice.lower() not in {name.lower() for name in self.voices}:
                raise ValueError('Choose one of the Qwen3 TTS voices')
            wavs, rate = self.model.generate_custom_voice(text=text, speaker=voice, instruct=instruction, **options)
        self.sample_rate = int(rate)
        return wavs[0]

    def _load_zonos3(self, source):
        import torch
        _add_path(source / 'model' / 'src')
        _install_dac_layers()
        _force_weights_only()
        _zonos3_offline(self.model_dir)
        from zonos3.reference.lm import Zonos3LM
        from zonos3.reference.synth import ReferenceAssets, synthesize
        import zonos3.tts.audio as zonos3_audio

        def wav_only(data):
            # The manager only passes validated mono 16-bit PCM WAV, so no ffmpeg program is needed.
            if data[:4] != b'RIFF' or data[8:12] != b'WAVE':
                raise ValueError('Use a mono 16-bit PCM WAV reference recording')
            return data

        zonos3_audio._transcode_audio_bytes_to_wav = wav_only
        self.zonos3_synthesize = synthesize
        dtype = torch.bfloat16 if self.device == 'cuda' else torch.float32
        self.model = Zonos3LM.load(str(self.model_dir / 'lm' / 'zonos3_mini'), device=self.device, dtype=dtype)
        self.assets = _zonos3_assets(ReferenceAssets).load(steer_bundle=str(source / ZONOS3_BUNDLE))
        self.model = self.assets.grow(self.model)
        self.assets.decoder(self.model.config.audio_codec, torch.device(self.device))
        ui = json.loads((source / ZONOS3_BUNDLE).read_text(encoding='utf-8'))['ui']
        self.controls = {item['id']: item['map'] for item in ui['controls']}
        self.controls.update({'accent:' + item['id']: item['map'] for item in ui['accents']})
        self.styles = {'Neutral': {}}
        self.styles.update({item['label']: {item['control']: item['dose']} for item in ui['moods']})
        self.styles.update({ZONOS3_PRESET_NAMES.get(name, name): values for name, values in ui['presets'].items()})
        self.presets = list(self.styles)
        folder = (self.model_dir / 'web' / 'voices').resolve()
        self.voice_files = {}
        for item in json.loads((folder / 'voices.json').read_text(encoding='utf-8')):
            voice = zonos3_voice(item)
            path = (folder / str(item.get('embedding') or '')).resolve()
            if voice and voice['id'] not in self.voice_files and folder in path.parents and path.suffix == '.npy' \
                    and path.is_file():
                self.voice_files[voice['id']] = (path, voice['language'])
        self.voices = list(self.voice_files)
        self.sample_rate = 44100

    def _zonos3_controls(self, values):
        """Studio slider values ({'accent:british': 1.5, 'authority': 3}) as steering-direction doses."""
        doses = {}
        for control, value in values.items():
            mapping, value = self.controls.get(control), float(value)
            if mapping is None or not value:
                continue
            side = mapping if control.startswith('accent:') else mapping.get('+' if value > 0 else '-') or {}
            for direction, weight in side.items():
                doses[direction] = doses.get(direction, 0.0) + float(weight) * abs(value)
        return {direction: dose for direction, dose in doses.items() if dose}

    def _zonos3(self, text, settings, reference, seed):
        import torch
        body = {'text': text, 'seed': seed, 'speaker_mode': 'embedding', 'stream': False, 'stream_decode': 'whole',
                'text_normalization': settings.get('normalize', True) is not False}
        language = settings.get('language') or None
        if reference:
            body.update(speaker_audio_base64=base64.b64encode(Path(reference).read_bytes()).decode('ascii'),
                        speaker_audio_name='reference.wav')
        else:
            voice = settings.get('voice') or (self.voices[0] if self.voices else None)
            if voice not in self.voice_files:
                raise ValueError('Choose one of the ZONOS3 voices or add a reference recording')
            path, voice_language = self.voice_files[voice]
            body.update(speaker_embedding_base64=base64.b64encode(path.read_bytes()).decode('ascii'),
                        speaker_embedding_name=path.name)
            language = language or voice_language
        body['language'] = language or 'en_us'
        controls = self._zonos3_controls(self.styles.get(settings.get('preset') or 'Neutral') or {})
        if controls:
            body['controls'] = controls
        for name, key in (('temperature', 'temperature'), ('topP', 'top_p'), ('topK', 'topk'), ('minP', 'min_p')):
            if settings.get(name) is not None:
                body[key] = settings[name]
        if settings.get('maxNewTokens') is not None:
            body['max_tokens'] = int(settings['maxNewTokens'])
        with torch.inference_mode():
            return self.zonos3_synthesize(self.model, body, assets=self.assets, device=self.device).pcm

    def _dots(self, text, settings, reference, reference_text):
        options = {'num_steps': int(settings.get('steps', 10)), 'guidance_scale': float(settings.get('cfg', 1.2))}
        if DOTS_EDIT_TAG.search(text):
            if not reference:
                raise ValueError('Add the recording to edit; edit tags change a reference recording')
            result = self.model.generate_edit(source_audio_path=reference, instruction=text, **options)
        else:
            result = self.model.generate(text=text, prompt_audio_path=reference, prompt_text=reference_text,
                                         language=settings.get('language') or None, normalize_text=False, **options)
        audio = result['audio']
        return audio.detach().float().cpu().numpy() if hasattr(audio, 'detach') else audio

    def synthesize(self, request):
        import numpy as np
        settings = request.get('settings') or {}
        text = request['text']
        reference = settings.get('referencePath')
        reference_text = (settings.get('referenceText') or '').strip() or None
        instruction = (settings.get('instruction') or '').strip() or None
        seed = int(settings.get('seed', 1234))
        _seed(seed)
        import torch
        if self.device == 'cuda':
            torch.cuda.reset_peak_memory_stats()
        if self.family == 'kitten-tts-2-v1':
            options = {'preset': settings.get('preset') or None, 'normalize': settings.get('normalize', True) is not False,
                       'max_new_tokens': int(settings.get('maxNewTokens', 1000)), 'advanced': {'seed': seed}}
            for name, key in (('temperature', 'temperature'), ('topP', 'top_p'), ('topK', 'top_k'), ('minP', 'min_p')):
                if settings.get(name) is not None:
                    options[key] = settings[name]
            if reference:
                options.update(reference=reference, reference_text=reference_text)
            else:
                options['voice'] = settings.get('voice') or None
            audio = self.model.generate(text, **options)
        elif self.family == 'voxcpm2-v1':
            options = {'cfg_value': float(settings.get('cfg', 2.0)), 'inference_timesteps': int(settings.get('steps', 10)),
                       'normalize': False, 'denoise': False, 'retry_badcase': True}
            if reference:
                options['reference_wav_path'] = reference
                if reference_text:
                    options.update(prompt_wav_path=reference, prompt_text=reference_text)
            audio = self.model.generate(text=('(' + instruction + ')' if instruction else '') + text, **options)
        elif self.family == 'zonos3-mini-v1':
            audio = self._zonos3(text, settings, reference, seed)
        elif self.family == 'dots-tts-edit-v1':
            audio = self._dots(text, settings, reference, reference_text)
        elif self.family in QWEN3_TTS:
            with torch.inference_mode():
                audio = self._qwen3(text, settings, reference, reference_text, instruction)
        else:
            from breeze_infer.runtime import set_all_seeds
            from breeze_infer.templates import get_template, prepare_inputs, select_template_name
            identity = request.get('id') or 'opencore'
            row = {'id': identity, 'text': text, 'speaker': 'S0'}
            if instruction:
                row['instruction'] = instruction
            if reference:
                row.update(ref_audio_path=reference, ref_text=reference_text)
            set_all_seeds(seed)
            inputs = prepare_inputs(self.tokenizer, self.codec, self.model, [row], get_template(select_template_name(row)),
                                    guidance_scale=float(settings.get('cfg', 1.0)), guidance_scale_ref=None,
                                    guidance_scale_ins=None)
            chunks = [np.asarray(chunk.audio, dtype=np.float32).reshape(-1)
                      for chunk in self.runtime.iter_audio_chunks(inputs, request_id=identity, seed=seed)]
            audio = np.concatenate(chunks) if chunks else np.zeros(0, np.float32)
        result = self._write(np.asarray(audio, dtype=np.float32).reshape(-1), Path(request['output']))
        if self.device == 'cuda':
            result['peakGpuMiB'] = int(torch.cuda.max_memory_reserved() // 1024 ** 2)
        return result

    def _write(self, audio, output):
        import numpy as np
        import soundfile
        finite = bool(audio.size and np.isfinite(audio).all())
        if not finite:
            raise ValueError('The model returned no finite audio samples')
        peak = float(np.abs(audio).max())
        if peak > 1:
            audio = audio / peak
        rms = float(math.sqrt(float(np.mean(np.square(audio, dtype=np.float64)))))
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix('.partial.wav')
        soundfile.write(str(temporary), audio, self.sample_rate, subtype='PCM_16', format='WAV')
        os.replace(temporary, output)
        return {'path': str(output), 'sampleRate': self.sample_rate, 'durationS': round(audio.size / self.sample_rate, 3),
                'rms': round(rms, 6), 'peak': round(min(peak, 1.0), 6), 'finite': finite}


def main():
    protocol = os.fdopen(os.dup(1), 'w', encoding='utf-8', buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    engine = None
    reply = lambda value: (protocol.write(json.dumps(value, separators=(',', ':')) + '\n'), protocol.flush())
    reply({'ok': True, 'event': 'started', 'protocol': PROTOCOL, 'pid': os.getpid()})
    for raw in sys.stdin.buffer:
        try:
            message = json.loads(raw.decode('utf-8'))
            operation = message.get('op')
            started = time.monotonic()
            if operation == 'load':
                if message.get('family') not in FAMILIES:
                    raise ValueError('Unsupported speech family')
                engine = None
                engine = Engine(message['family'], message['modelDir'], message.get('components') or {}, message.get('hardware'))
                import torch
                reply({'ok': True, 'op': 'load', 'family': engine.family, 'sampleRate': engine.sample_rate,
                       'voices': engine.voices, 'presets': engine.presets, 'device': engine.device,
                       'deviceNote': engine.device_note,
                       'gpu': torch.cuda.get_device_name(0) if engine.device == 'cuda' else None,
                       'loadMs': int((time.monotonic() - started) * 1000)})
            elif operation == 'synthesize':
                if engine is None:
                    raise ValueError('Load a speech model first')
                result = engine.synthesize(message)
                reply({'ok': True, 'op': 'synthesize', 'id': message.get('id'), **result,
                       'elapsedMs': int((time.monotonic() - started) * 1000)})
            elif operation == 'exit':
                reply({'ok': True, 'op': 'exit'})
                return
            else:
                raise ValueError('Unknown worker operation')
        except Exception as error:
            traceback.print_exc()
            reply({'ok': False, 'error': (type(error).__name__ + ': ' + str(error))[:1500]})


if __name__ == '__main__':
    main()
