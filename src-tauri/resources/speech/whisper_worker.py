"""One recording per process. Exiting releases the CUDA context and allocations."""
import argparse
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True)
    args = parser.parse_args()
    # CUDA wheels keep their DLLs outside PATH on Windows.
    handles = []
    if os.name == 'nt':
        for entry in sys.path:
            for name in ('cublas', 'cudnn'):
                folder = Path(entry) / 'nvidia' / name / 'bin'
                if folder.is_dir():
                    handles.append(os.add_dll_directory(str(folder)))
                    os.environ['PATH'] = str(folder) + os.pathsep + os.environ.get('PATH', '')
    from faster_whisper import WhisperModel
    model = WhisperModel(args.model, device='cuda', compute_type='float16',
                         local_files_only=True, num_workers=1)
    print(json.dumps({'ready': True}), flush=True)
    request = json.loads(sys.stdin.readline())
    segments, info = model.transcribe(request['audio'], beam_size=1, task='transcribe',
                                     vad_filter=True, condition_on_previous_text=False)
    text = ''.join(segment.text for segment in segments).strip()
    print(json.dumps({'text': text, 'language': info.language}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'error': str(error)}), flush=True)
        sys.exit(1)
