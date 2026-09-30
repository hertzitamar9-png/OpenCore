"""Offline English Phonon-2 reference inference with bounded audio chunks.

Expands the checkpoint's actual five learned levels to FP32; it does not load
the teacher's weight file or substitute another ASR model. CUDA is temporary.
"""
import argparse
import gc
import json
from pathlib import Path
import sys
import time

from whisper_worker import emit, gpu_free, load_audio, require_ram
from prepare_phonon_runtime import CONTAINER_SHA, CONTAINER_BYTES, digest

MIN_GPU_FREE = 3 * 1024**3
MIN_RAM_FREE = 8 * 1024**3


def audio_blocks(audio, np, sample_rate=16000, seconds=20):
    """Bound activation memory; prefer quiet boundaries near each chunk end."""
    limit = sample_rate * seconds
    offset = 0
    while offset < len(audio):
        end = min(len(audio), offset + limit)
        if end < len(audio):
            start = max(offset + limit // 2, end - 3 * sample_rate)
            hop = sample_rate // 50
            energy = [float(np.mean(audio[p:p + hop] ** 2)) for p in range(start, end, hop)]
            end = start + int(np.argmin(energy)) * hop + hop
        yield audio[offset:end]
        offset = end


def transcribe(model, processor, audio, np, torch, device):
    texts = []
    with torch.inference_mode():
        for block in audio_blocks(audio, np):
            inputs = processor([block], sampling_rate=16000, return_tensors='pt', padding=True)
            features = inputs['input_features'].to(device=device, dtype=torch.float32)
            mask = inputs.get('attention_mask')
            if mask is not None:
                mask = mask.to(device)
            result = model.generate(input_features=features, attention_mask=mask)
            sequences = getattr(result, 'sequences', result)
            text = processor.batch_decode(sequences, skip_special_tokens=True)[0].strip()
            if text:
                texts.append(text)
    return ' '.join(texts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True)
    parser.add_argument('--idle-mode', choices=('cold','ram'), required=True)
    parser.add_argument('--awake', action='store_true')
    args = parser.parse_args()
    directory = Path(args.model).resolve()
    try:
        import av
        import numpy as np
        import psutil
        import torch
        torch.set_num_threads(2)
        torch.set_num_interop_threads(1)
        container = directory / 'model.fermion'
        if not container.is_file() or container.stat().st_size != CONTAINER_BYTES or digest(container) != CONTAINER_SHA:
            raise RuntimeError('Phonon-2 container is missing or corrupted. Reinstall the speech model.')
        require_ram(psutil, MIN_RAM_FREE, 'Phonon-2 reference checkpoint loading')
        started = time.monotonic()
        sys.path.insert(0, str(directory))
        from reference_transformers import load_model
        model, processor, receipt = load_model(str(container), str(directory / 'processor'), dtype=torch.float32)
        gc.collect()
        device = 'cpu'

        def activate():
            nonlocal device
            begin = time.monotonic()
            if torch.cuda.is_available() and gpu_free(torch) >= MIN_GPU_FREE:
                try:
                    model.to('cuda:0')
                    device = 'cuda:0'
                except torch.cuda.OutOfMemoryError:
                    model.to('cpu')
                    torch.cuda.empty_cache()
                    device = 'cpu'
            return round((time.monotonic() - begin) * 1000)

        def sleep():
            nonlocal device
            model.to('cpu')
            device = 'cpu'
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            gc.collect()

        wake_ms = activate() if args.awake else None
        emit({'ready':True,'modelId':'phonon-2','language':'en','device':device,
            'coldStartMs':round((time.monotonic()-started)*1000),'wakeMs':wake_ms,
            'params':receipt['params'],'weightDtype':'float32'})
        for line in sys.stdin:
            try:
                request = json.loads(line)
                action = request.get('action')
                if action == 'shutdown':
                    break
                if action == 'wake':
                    elapsed = activate()
                    emit({'awake':True,'wakeMs':elapsed,'device':device})
                elif action == 'transcribe':
                    audio = load_audio(request['audio'], av, np)
                    try:
                        text = transcribe(model,processor,audio,np,torch,device)
                    except torch.cuda.OutOfMemoryError:
                        sleep()
                        text = transcribe(model,processor,audio,np,torch,device)
                    used_device = device
                    sleep()
                    emit({'text':text,'language':'en','device':used_device,'standbyDevice':device,
                        'gpuModelBytes':sum(t.numel()*t.element_size() for t in list(model.parameters())+list(model.buffers()) if t.is_cuda),
                        'torchGpuBytes':torch.cuda.memory_allocated() if torch.cuda.is_available() else 0})
                else:
                    emit({'error':f'Unknown speech action: {action}'})
            except Exception as error:
                if args.idle_mode == 'ram':
                    sleep()
                emit({'error':str(error)})
        sleep()
        return 0
    except Exception as error:
        emit({'error':str(error)})
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
