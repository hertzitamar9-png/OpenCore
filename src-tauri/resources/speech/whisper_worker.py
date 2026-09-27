"""Local dictation worker; park weights in RAM or exit between recordings."""
import argparse
import json
import os
from pathlib import Path
import sys
import time


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def main():
    started = time.perf_counter()
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True)
    parser.add_argument('--idle-mode', choices=('auto', 'ram', 'cold'), default='auto')
    parser.add_argument('--awake', action='store_true')
    args = parser.parse_args()
    # CUDA wheels keep their DLLs outside PATH on Windows.
    handles = []
    if os.name == 'nt':
        for entry in sys.path:
            for name in ('cublas', 'cudnn', 'cuda_nvrtc'):
                folder = Path(entry) / 'nvidia' / name / 'bin'
                if folder.is_dir():
                    handles.append(os.add_dll_directory(str(folder)))
                    os.environ['PATH'] = str(folder) + os.pathsep + os.environ.get('PATH', '')
    from faster_whisper import WhisperModel
    model = WhisperModel(args.model, device='cuda', compute_type='float16',
                         local_files_only=True, num_workers=1, cpu_threads=2)
    cold_ms = round((time.perf_counter() - started) * 1000)
    mode = args.idle_mode if args.idle_mode != 'auto' else ('cold' if cold_ms <= 1000 else 'ram')
    wake_ms = None
    if not args.awake:
        model.model.unload_model(to_cpu=mode == 'ram')
        wake_started = time.perf_counter()
        model.model.load_model()
        wake_ms = round((time.perf_counter() - wake_started) * 1000)
        model.model.unload_model(to_cpu=mode == 'ram')
    emit({'ready': True, 'sleeping': not args.awake, 'idleMode': mode,
          'coldStartMs': cold_ms, 'wakeMs': wake_ms})
    for line in sys.stdin:
        request = json.loads(line)
        action = request.get('action', 'transcribe')
        if action == 'wake':
            tick = time.perf_counter()
            model.model.load_model()
            emit({'awake': True, 'wakeMs': round((time.perf_counter() - tick) * 1000)})
        elif action == 'sleep':
            model.model.unload_model(to_cpu=mode == 'ram')
            emit({'sleeping': True})
        elif action == 'shutdown':
            break
        elif action == 'transcribe':
            try:
                model.model.load_model()
                segments, info = model.transcribe(request['audio'], beam_size=1,
                    task='transcribe', vad_filter=True, condition_on_previous_text=False)
                text = ''.join(segment.text for segment in segments).strip()
                result = {'text': text, 'language': info.language}
            except Exception as error:
                result = {'error': str(error)}
            finally:
                model.model.unload_model(to_cpu=mode == 'ram')
            emit(dict(result, sleeping=True))
            if mode == 'cold':
                break
        else:
            raise ValueError('Unknown speech operation')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        emit({'error': str(error)})
        sys.exit(1)
