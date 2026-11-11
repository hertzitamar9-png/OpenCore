"""Persistent local speech synthesis worker for OpenCore's managed TTS runtime.

One JSON request per line on stdin, one JSON reply per line on the original
stdout. Library output is redirected to stderr, which the manager logs. The
worker holds at most one model; the manager restarts it to switch models or to
interrupt a generation. It never downloads: every path it receives was verified
by the manager, and HF_HUB_OFFLINE is set by its parent.
"""
import json
import math
import os
from pathlib import Path
import random
import sys
import time
import traceback

PROTOCOL = 1
FAMILIES = ('kitten-tts-2-v1', 'voxcpm2-v1', 'breeze-tts-2-v1')


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
        else:
            raise ValueError('Unsupported speech family')

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
