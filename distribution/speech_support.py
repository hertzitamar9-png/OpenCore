"""Tell the app, for every built-in speech model, whether OpenCore can run it.

The native catalogue lists text-to-speech, voice-cloning and speech-recognition
checkpoints, and most of them have no engine in OpenCore. Rather than leaving a
card stuck on "Setup needed", each one gets one of three states. They are worked
out here when the publisher catalogue is built, shipped in publisher-models.json
under ``speechSupport`` and served by the bridge at GET /speech/support:

managed      A bundled package runs this model. ``use`` lists the package entry
             ids to install; they are the cards that replace this one.
native       The app's own dictation engine already runs this model.
unavailable  OpenCore can't run it. ``reason`` says why in plain words, and
             ``use`` lists models that do run (package entry ids or native ids)
             when there is a close match.

A speech model with no state fails the build, so a new catalogue entry can't fall
back to the dead card unnoticed.
"""
import re

SPEECH_CATEGORIES = ('tts', 'voice-cloning', 'speech')
DICTATION = ('phonon-2', 'whisper-large-v3-turbo')

# Runtime names in ``use`` resolve to the bundled package entry ids of that runtime.
VOXCPM2 = 'runtime:voxcpm2-v1'

_CHATTERBOX = ("Chatterbox needs Resemble AI's chatterbox engine, which pins older library versions that "
               "conflict with OpenCore's speech engine, so the two can't be installed together.")
_F5 = ('F5-TTS and E2-TTS are licensed for non-commercial use only (CC BY-NC 4.0) and need the f5-tts engine, '
       "which OpenCore doesn't include.")
_XTTS = ("XTTS v2 needs the Coqui TTS engine, whose libraries conflict with OpenCore's speech engine, and the "
         "Coqui Public Model License doesn't allow commercial use.")
_COSYVOICE = ("CosyVoice needs Alibaba's CosyVoice code, which isn't published as an installable package, so "
              "OpenCore can't run it.")
_MOSS = ("MOSS runs custom code from its model repository, which OpenCore never executes, and there is no "
         'installable engine for it.')
_WHISPER = ('OpenCore dictates with the faster-whisper builds of Whisper large-v3 and large-v3 turbo; this '
            'smaller Whisper checkpoint has no tested package. Use Whisper large-v3 turbo or Phonon-2 for dictation.')
_QWEN3_ASR = ("Qwen3 ASR needs the qwen-asr engine, which requires different library versions than OpenCore's "
              "speech engine, so the two can't be installed together yet. Use Phonon-2 or Whisper large-v3 turbo "
              'for dictation.')
_VIBEVOICE = ("VibeVoice needs Microsoft's VibeVoice code, which isn't part of the libraries OpenCore's speech "
              'engine installs, so it has no tested engine yet.')
_PYANNOTE = ('This finds who spoke when (speaker diarization); it does not transcribe speech. pyannote only '
             'releases it to people who accept its access agreement on Hugging Face, and it needs the '
             "pyannote.audio engine, which OpenCore doesn't include.")

# Why a model family can't run, keyed by the native id its variants point at.
REASONS = {
    'tts-csm-1b': ('Sesame only releases CSM 1B to people who accept its access agreement on Hugging Face, and '
                   'OpenCore has no tested engine for it.', ()),
    'tts-orpheus-3b-0-1-ft': ('Canopy Labs only releases Orpheus to people who accept its access agreement on '
                              "Hugging Face, and it needs the SNAC audio decoder, which OpenCore's speech engine "
                              "doesn't include.", ()),
    'tts-xtts-v2': (_XTTS, ()),
    'voice-cloning-xtts-v2': (_XTTS, ()),
    'voice-cloning-openaudio-s1-mini': ('Fish Audio only releases OpenAudio S1 Mini to people who accept its access '
                                        'agreement, it is licensed for non-commercial use only, and it needs Fish '
                                        "Audio's own engine, which OpenCore doesn't include.", ()),
    'voice-cloning-fish-speech-1-5': ("Fish Speech 1.5 is licensed for non-commercial use only and needs Fish Audio's "
                                      "own engine, which OpenCore doesn't include.", ()),
    'bark-small': ('Bark is not packaged for OpenCore yet: it has no tested download package or speech worker, so '
                   "it can't be installed.", ()),
    'speecht5-tts': ('SpeechT5 is not packaged for OpenCore yet: it also needs a separate HiFi-GAN vocoder and '
                     "speaker voice files, and none of these has a tested package.", ()),
    'whisper-tiny': (_WHISPER, DICTATION),
    'whisper-base': (_WHISPER, DICTATION),
    'whisper-small': (_WHISPER, DICTATION),
    'whisper-medium': (_WHISPER, DICTATION),
    'speech-qwen3-asr-0-6b': (_QWEN3_ASR, DICTATION),
    'speech-qwen3-asr-1-7b': (_QWEN3_ASR, DICTATION),
    'tts-vibevoice-1-5b': (_VIBEVOICE, ()),
    'tts-vibevoice-realtime-0-5b': (_VIBEVOICE, ()),
    'tts-f5-tts': (_F5, ()),
    'voice-cloning-f5-tts': (_F5, ()),
    'tts-e2-tts': (_F5, ()),
    'voice-cloning-e2-tts': (_F5, ()),
    'tts-kokoro-82m': ("Kokoro needs the kokoro engine and the eSpeak NG pronunciation program, which OpenCore "
                       "doesn't install.", ()),
    'tts-chatterbox': (_CHATTERBOX, ()),
    'voice-cloning-chatterbox': (_CHATTERBOX, ()),
    'tts-chatterbox-turbo': (_CHATTERBOX, ()),
    'voice-cloning-chatterbox-turbo': (_CHATTERBOX, ()),
    'voice-cloning-voxcpm-0-5b': ('VoxCPM 0.5B is not packaged for OpenCore. VoxCPM2, its successor from the same '
                                  'team, is, and it also clones voices.', (VOXCPM2,)),
    'voice-cloning-cosyvoice2-0-5b': (_COSYVOICE, ()),
    'voice-cloning-cosyvoice-300m': (_COSYVOICE, ()),
    'moss-tts-local15': (_MOSS, ()),
    'moss-voicegenerator': (_MOSS, ()),
    'hf-fishaudio-drama-3': ('drama-3 has 36 billion parameters, far more than a consumer graphics card can hold, '
                             "it needs Fish Audio's own engine, and its research license doesn't allow commercial "
                             'use.', ()),
    'hf-fishaudio-s2-pro': ("S2 Pro needs Fish Audio's own engine, which OpenCore doesn't include, and its research "
                            "license doesn't allow commercial use.", ()),
    'hf-pyannote-speaker-diarization-3-1': (_PYANNOTE, ()),
    'hf-pyannote-speaker-diarization-community-1': (_PYANNOTE, ()),
    'hf-cactus-compute-whistle': ("Whistle needs Cactus Compute's own on-device engine, which OpenCore doesn't "
                                  'include.', DICTATION),
    'hf-canberkkkkkk-ema-lightning': ('EMA Lightning is published without an installable engine, so OpenCore has '
                                      'no tested way to run it.', ()),
    'hf-qnguyen3-quan-transcribe-1-0': ('QUAN-Transcribe is only released to people who accept its access agreement '
                                        "on Hugging Face, and it needs its own engine, which OpenCore doesn't "
                                        'include.', ()),
    'hf-k2-fsa-omnivoice': ("OmniVoice needs k2-fsa's omnivoice engine, which is not packaged for OpenCore yet.", ()),
    'hf-openai-whisper-large-v3': ('This is the original PyTorch release of Whisper large-v3. OpenCore runs the '
                                   'same model as Whisper large-v3 in dictation.', ('whisper-large-v3',)),
    'hf-nvidia-personaplex-7b-v1': ('PersonaPlex is a speech-to-speech conversation model, not dictation. NVIDIA only '
                                    'releases it to people who accept its license, and it needs the Moshi engine, '
                                    "which OpenCore doesn't include.", ()),
    'hf-neuphonic-neudecide': ("NeuDecide chooses tool calls from speech; it doesn't transcribe or speak. Neuphonic "
                               'only releases it to people who accept its access agreement on Hugging Face.', ()),
    'hf-moondream-parakeet-redux': ("Parakeet Redux stores its weights in a ternary format that needs moondream's "
                                    "own engine, which OpenCore doesn't include. Phonon-2, also built on Parakeet, "
                                    'runs in OpenCore.', ('phonon-2',)),
    'hf-mistralai-voxtral-mini-4b-realtime-arabic': ("Voxtral Realtime needs the vLLM serving engine, which OpenCore "
                                                     "doesn't include.", ()),
    'hf-audio-cpp-audio-cpp-gguf': ("This is a collection of GGUF files for the audio.cpp engine, which OpenCore "
                                    "doesn't include.", ()),
    'hf-nvidia-nemotron-3-5-asr-streaming-0-6b': ("Nemotron ASR needs NVIDIA's NeMo engine, which OpenCore doesn't "
                                                  'include.', DICTATION),
    'hf-sahilmahendrakar-paradee-8m-v1-0': ("Paradee is published as ONNX files for its own player, which OpenCore "
                                            "doesn't include.", ()),
    'hf-cloud0day3-antalia-mini': ("Antalia Mini needs its own antalia-mini engine, which OpenCore doesn't "
                                   'include.', ()),
}


def _repository(url):
    match = re.match(r'https://huggingface\.co/([^/]+/[^/]+)', url or '')
    return match.group(1).lower() if match else None


def _variant_reason(model, name):
    path, precision = (model.get('runtimeModelPath') or '').lower(), model.get('precision') or 'converted'
    kind = 'ONNX' if path.endswith('.onnx') or 'onnx' in precision.lower() else 'GGUF' if path.endswith('.gguf') or not path else None
    files = precision if kind is None or kind.lower() in precision.lower() else precision + ' ' + kind
    engine = {'ONNX': 'an ONNX speech engine', 'GGUF': 'a GGUF speech engine'}.get(kind, 'a different speech engine')
    return ("OpenCore already runs {}, so use that instead. These {} files need {} that OpenCore doesn't "
            'include.').format(name, files, engine)


def speech_support(catalog, entries):
    """Map every native speech model id to its state; raise for a model with none."""
    packages = [entry for entry in entries if entry.get('ttsRuntime') and entry.get('installable')]
    by_runtime, by_repository, by_native = {}, {}, {}
    for entry in packages:
        by_runtime.setdefault(entry['ttsRuntime'], []).append(entry['id'])
        by_repository.setdefault(entry['repository'].lower(), []).append(entry['id'])
        for native in entry.get('nativeModelIds', ()):
            by_native.setdefault(native, []).append(entry)
    labels = {entry['id']: entry.get('familyLabel') or entry.get('label') for entry in packages}
    models = {model['id']: model for model in catalog['models']}

    def resolve(ids):
        result = []
        for identity in ids:
            if identity.startswith('runtime:'):
                found = by_runtime.get(identity.split(':', 1)[1])
                if not found:
                    raise ValueError('A speech alternative names a runtime with no bundled package: ' + identity)
                result.extend(found)
            elif identity in models:
                result.append(identity)
            else:
                raise ValueError('A speech alternative names an unknown model: ' + identity)
        return result

    def managed(model):
        # A package claims the card only at the precision it installs, as the hub card merge does.
        claimed = [entry['id'] for entry in by_native.get(model['id'], ()) if entry.get('quantization') == model.get('precision')]
        if claimed:
            return claimed
        if not model.get('variantOf') and not model.get('runtimeReady'):
            return by_repository.get(_repository(model.get('sourceUrl')), [])
        return []

    support = {}
    for model in catalog['models']:
        if model.get('category') not in SPEECH_CATEGORIES:
            continue
        identity, root_id = model['id'], model.get('variantOf') or model['id']
        root = models.get(root_id, model)
        if model.get('runtimeReady'):
            support[identity] = {'state': 'native', 'use': [identity]}
        elif managed(model):
            support[identity] = {'state': 'managed', 'use': managed(model)}
        elif root is not model and (managed(root) or root.get('runtimeReady')):
            use = managed(root) or [root_id]
            name = labels.get(use[0]) if managed(root) else root.get('label')
            support[identity] = {'state': 'unavailable', 'use': use, 'reason': _variant_reason(model, name)}
        elif root_id in REASONS:
            reason, use = REASONS[root_id]
            support[identity] = {'state': 'unavailable', 'use': resolve(use), 'reason': reason}
        else:
            raise ValueError('Give this built-in speech model a managed package or a reason it cannot run: ' + identity)
    return support
