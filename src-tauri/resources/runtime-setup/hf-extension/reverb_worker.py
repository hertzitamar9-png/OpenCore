"""Reverb TTS through the managed speech job protocol, using CPU only."""
import json
import os
from pathlib import Path
import sys
import time
import traceback

# Isolated Python does not add the worker's directory to its import path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hf_reverb as reverb
from tts_worker import Engine, PROTOCOL


class ReverbEngine:
    def __init__(self, family, model_dir, components):
        import torch
        from reverb_tts import TTS

        if torch.version.cuda is not None:
            raise RuntimeError('Reverb needs OpenCore’s isolated CPU worker')
        self.family = family
        variant = reverb.VARIANTS[family]
        model = Path(model_dir)
        codec = Path(components['reverbCodec']).parent
        weights = model / 'model' / 'safetensors' / variant
        required = [weights / 'model.safetensors', weights / 'config.json',
                    model / 'model/tokenizer.json', codec / 'model.safetensors', codec / 'config.json']
        required += [model / 'voices' / (voice + '.json') for voice in reverb.VOICE_IDS]
        if not all(path.is_file() for path in required):
            raise RuntimeError('Reverb is missing installed model, voice or codec files. Repair its installation and retry.')
        self.tts = TTS.from_pretrained(model, variant=variant, device='cpu', dtype=torch.float32,
                                       codec=str(codec), revision=None)
        if set(self.tts.preset_voices()) != set(reverb.VOICE_IDS):
            raise RuntimeError('The installed Reverb voices do not match the reviewed package')
        self.sample_rate = int(self.tts.sample_rate)

    def synthesize(self, request):
        import numpy as np

        settings = request['settings']
        voice_name = settings.get('voice') or reverb.DEFAULT_VOICE
        language = settings.get('language') or voice_name.split('_', 1)[0]
        if settings.get('referencePath'):
            voice = self.tts.voice(settings['referencePath'], settings['referenceText'],
                                   language=language, prepare=True)
        else:
            voice = self.tts.preset_voice(voice_name)
        audio = self.tts.generate(request['text'], voice, language=language, prepare=True,
                                  seed=settings['seed'], steps=settings.get('steps', 8))
        return Engine._write(self, np.asarray(audio, dtype=np.float32).reshape(-1), Path(request['output']))


def main():
    protocol = os.fdopen(os.dup(1), 'w', encoding='utf-8', buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    def reply(value):
        print(json.dumps(value, ensure_ascii=True, separators=(',', ':')), file=protocol, flush=True)

    engine = None
    reply({'ok': True, 'event': 'started', 'protocol': PROTOCOL, 'pid': os.getpid()})
    for raw in sys.stdin.buffer:
        try:
            message = json.loads(raw)
            operation = message['op']
            started = time.monotonic()
            if operation == 'load':
                engine = None
                engine = ReverbEngine(message['family'], message['modelDir'], message['components'])
                reply({'ok': True, 'op': 'load', 'family': engine.family, 'sampleRate': engine.sample_rate,
                       'voices': list(reverb.VOICE_IDS), 'presets': [], 'device': 'cpu', 'gpu': None,
                       'deviceNote': 'Reverb uses the isolated CPU runtime.',
                       'loadMs': round((time.monotonic() - started) * 1000)})
            elif operation == 'synthesize':
                if engine is None:
                    raise ValueError('Load Reverb before generating speech')
                result = engine.synthesize(message)
                reply({'ok': True, 'op': 'synthesize', 'id': message['id'], **result,
                       'elapsedMs': round((time.monotonic() - started) * 1000)})
            elif operation == 'exit':
                reply({'ok': True, 'op': 'exit'})
                return
            else:
                raise ValueError('Unknown Reverb worker operation')
        except Exception as error:
            traceback.print_exc()
            reply({'ok': False, 'error': (type(error).__name__ + ': ' + str(error))[:1500]})


if __name__ == '__main__':
    main()
