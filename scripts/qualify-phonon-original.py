"""Real Windows Original checkpoint smoke test. Run in CI, never installs packages."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SPEECH = ROOT / 'src-tauri/resources/speech'
sys.path.insert(0, str(SPEECH))
from prepare_phonon_runtime import extract_container
from phonon_original import require_compact_engine


def word_errors(expected, actual):
    previous = list(range(len(actual) + 1))
    for i, word in enumerate(expected, 1):
        current = [i]
        for j, other in enumerate(actual, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j-1] + (word != other)))
        previous = current
    return previous[-1]


def main():
    import zstandard
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='artifacts/phonon-original-qualification.json')
    args = parser.parse_args()
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    catalog = json.loads((ROOT / 'src-tauri/resources/model-catalog.json').read_text(encoding='utf-8'))
    artifact = next(a for a in catalog['artifacts'] if a['id'] == 'phonon-2-phonon-2-bps-tar-zst')
    fixture = ROOT / 'tests/fixtures/phonon-english.wav'
    with tempfile.TemporaryDirectory(prefix='phonon-original-') as temporary:
        directory = Path(temporary)
        archive = directory / artifact['filename']
        url = f"https://huggingface.co/{artifact['repo']}/resolve/{artifact['revision']}/{artifact['filename']}"
        digest = hashlib.sha256()
        with urllib.request.urlopen(url, timeout=180) as source, archive.open('wb') as target:
            while block := source.read(1024 * 1024):
                target.write(block); digest.update(block)
        assert archive.stat().st_size == artifact['bytes'], 'Original archive size differs from its pin'
        assert digest.hexdigest() == artifact['sha256'], 'Original archive hash differs from its pin'
        extract_container(directory, zstandard)
        requests = [{'action': 'wake'}, {'action': 'transcribe', 'audio': str(fixture)}, {'action': 'shutdown'}]
        started = time.monotonic()
        result = subprocess.run([sys.executable, '-B', str(SPEECH / 'phonon_worker.py'),
                                 '--model', str(directory), '--precision', 'original', '--idle-mode', 'cold'],
                                input=''.join(json.dumps(r) + '\n' for r in requests), capture_output=True,
                                text=True, encoding='utf-8', timeout=240,
                                env={**os.environ, 'HF_HUB_OFFLINE': '1', 'PYTHONIOENCODING': 'utf-8'})
        events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        receipt = {'exitCode': result.returncode, 'seconds': round(time.monotonic() - started, 3),
                   'checkpointSha256': artifact['sha256'], 'events': events, 'stderr': result.stderr}
        output.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
        assert result.returncode == 0, f'Original worker failed: {events} {result.stderr}'
        assert not any('error' in event for event in events), events
        ready = next(event for event in events if event.get('ready'))
        assert ready['runtimePrecision'] == 'original' and ready['device'] == 'cpu', ready
        assert ready['runtimeResidentBytes'] > 0, 'Original must report measured RAM'
        require_compact_engine(ready['publisherRuntime'])
        transcription = next(event for event in events if 'text' in event)
        expected = 'Open core can recognize the sentence Both precision options should work correctly'.lower().split()
        actual = re.findall(r'[a-z]+', transcription['text'].lower())
        errors = word_errors(expected, actual)
        assert errors <= 3, f'Original smoke transcript differs by {errors} words: {actual}'
        assert transcription['gpuModelBytes'] == transcription['torchGpuBytes'] == 0, transcription
        assert not (directory.parent / 'runtime-cache/phonon-2/expanded-fp32.pt').exists()
        print(json.dumps({'status': 'passed', 'runtime': ready['runtimeDescription'],
                          'startupMs': ready['coldStartMs'], 'startupRamBytes': ready['runtimeResidentBytes'],
                          'decodeSeconds': transcription['decodeSeconds'], 'fixtureWordErrors': errors}))


if __name__ == '__main__':
    main()
