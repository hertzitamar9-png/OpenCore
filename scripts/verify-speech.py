"""Manual CUDA smoke test using a known audio fixture, without microphone access."""
import json
import pathlib
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
HOME = pathlib.Path.home() / 'OpenCore' / 'speech'


def gpu():
    return subprocess.check_output(['nvidia-smi', '--query-gpu=memory.used',
                                    '--format=csv,noheader,nounits'], text=True).strip()


if __name__ == '__main__':
    before = gpu()
    start = time.monotonic()
    child = subprocess.Popen([str(HOME / 'venv/Scripts/python.exe'),
                              str(ROOT / 'src-tauri/resources/speech/whisper_worker.py'),
                              '--model', str(HOME / 'large-v3')],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, encoding='utf-8')
    try:
        ready = child.stdout.readline()
        print(ready, flush=True)
        assert json.loads(ready).get('ready'), ready
        load_seconds = time.monotonic() - start
        loaded = gpu()
        transcription_start = time.monotonic()
        stdout, stderr = child.communicate(json.dumps({'audio': str(ROOT / 'artifacts/speech-test.wav')}) + '\n', timeout=120)
        result = json.loads(stdout.strip().splitlines()[-1])
        assert child.returncode == 0, stderr + stdout
        assert 'running' in result['text'].lower() and 'walking' in result['text'].lower(), result
        receipt = dict(result, load_seconds=round(load_seconds, 3),
                       transcription_seconds=round(time.monotonic() - transcription_start, 3),
                       gpu_before_mib=before, gpu_loaded_mib=loaded, gpu_after_mib=gpu(),
                       worker_exited=child.poll() is not None, audio_source='Windows synthetic speech fixture')
        (ROOT / 'artifacts/speech-verification.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
        print(json.dumps(receipt, indent=2), flush=True)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()
