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
import wave

ROOT = Path(__file__).resolve().parents[1]
SPEECH = ROOT / 'src-tauri/resources/speech'
sys.path.insert(0, str(SPEECH))
from prepare_phonon_runtime import extract_container
from phonon_original import extract_original_config, require_compact_engine


def word_errors(expected, actual):
    previous = list(range(len(actual) + 1))
    for i, word in enumerate(expected, 1):
        current = [i]
        for j, other in enumerate(actual, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j-1] + (word != other)))
        previous = current
    return previous[-1]


def require_transcript_checks(metrics):
    publisher, minimal, repeat = (metrics[label]['texts'] for label in ('publisher', 'minimal', 'minimalRepeat'))
    assert minimal == repeat, 'The minimal runtime must produce repeatable raw transcripts'
    assert publisher[:2] == minimal[:2], 'Normal and quiet recordings must match the publisher exactly'
    # This fixture repeats the product name ten times. The two float32 audio
    # frontends can choose its joined or split spelling. Permit only that named
    # orthographic alias, retaining all raw text and requiring the candidate's
    # exact spoken sentence, punctuation and repetition count. A publisher
    # recognition error must not become the candidate's ground truth.
    canonical = lambda text: re.sub(r'\bOpen core\b', 'Opencore', text)
    expected = ' '.join(['Opencore can recognize the sentence. Both precision options should work correctly.'] * 10)
    candidate, reference = canonical(minimal[2]), canonical(publisher[2])
    assert candidate == expected, metrics
    return {'normalAndQuiet': 'exact', 'minimalRepeat': 'exact',
            'longAudio': 'candidate matches exact fixture except Open core/Opencore orthography',
            'rawLongAudioEqual': publisher[2] == minimal[2],
            'canonicalLongAudioEqual': reference == candidate,
            'publisherLongFixtureWordErrors': word_errors(expected.split(), reference.split()),
            'minimalLongFixtureWordErrors': word_errors(expected.split(), candidate.split())}


def run_worker(directory, fixtures, reference=False):
    requests = [{'action': 'wake'}, *[{'action': 'transcribe', 'audio': str(fixture)} for fixture in fixtures], {'action': 'shutdown'}]
    command = [sys.executable, '-B', str(SPEECH / 'phonon_worker.py')]
    if reference:
        # CI-only baseline in a separate process. The shipped worker has no heavy fallback.
        source = ("import sys,runpy,types; sys.path.insert(0,sys.argv.pop(1)); reference=types.ModuleType('phonon_minimal'); "
                  "reference.load=lambda directory,progress:__import__('fermion._speech.engine_phonon2_cpu',fromlist=['load']).load(directory,profile='five-value',backend='phonon2-five-value',quiet=True); "
                  "sys.modules['phonon_minimal']=reference; "
                  "sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name='__main__')")
        command = [sys.executable, '-B', '-c', source, str(SPEECH), str(SPEECH / 'phonon_worker.py')]
    started = time.monotonic()
    result = subprocess.run([*command, '--model', str(directory), '--precision', 'original', '--idle-mode', 'cold'],
                            input=''.join(json.dumps(r) + '\n' for r in requests), capture_output=True,
                            text=True, encoding='utf-8', timeout=240,
                            env={**os.environ, 'HF_HUB_OFFLINE': '1', 'PYTHONIOENCODING': 'utf-8'})
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    return {'exitCode': result.returncode, 'seconds': round(time.monotonic() - started, 3),
            'events': events, 'stderr': result.stderr}


def validate(receipt, minimal):
    events = receipt['events']
    assert receipt['exitCode'] == 0, receipt
    assert not any('error' in event for event in events), events
    ready = next(event for event in events if event.get('ready'))
    assert ready['runtimePrecision'] == 'original' and ready['device'] == 'cpu', ready
    assert ready['runtimeResidentBytes'] > 0, 'Original must report measured RAM'
    require_compact_engine(ready['publisherRuntime'])
    if minimal:
        assert ready['publisherRuntime']['frontend'] == 'numpy', ready
        assert ready['publisherRuntime']['torchImported'] is False, ready
    transcriptions = [event for event in events if 'text' in event]
    assert len(transcriptions) == 3, transcriptions
    transcription = transcriptions[0]
    expected = 'Open core can recognize the sentence Both precision options should work correctly'.lower().split()
    errors = word_errors(expected, re.findall(r'[a-z]+', transcription['text'].lower()))
    assert errors <= 3, f'Original smoke transcript differs by {errors} words: {transcription}'
    assert all(item['gpuModelBytes'] == item['torchGpuBytes'] == 0 for item in transcriptions), transcriptions
    return {'startupMs': ready['coldStartMs'], 'startupRamBytes': ready['runtimeResidentBytes'],
            'decodeSeconds': [item['decodeSeconds'] for item in transcriptions], 'fixtureWordErrors': errors,
            'texts': [item['text'] for item in transcriptions]}


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
        # Both processes start with the same prepared source files. Config
        # extraction is installation work, not a fair repeated-start comparison.
        extract_original_config(directory, zstandard)
        import numpy as np
        with wave.open(str(fixture), 'rb') as recording:
            params, pcm = recording.getparams(), recording.readframes(recording.getnframes())
        assert params.sampwidth == 2, 'Qualification fixture must be PCM16'
        quiet, long = directory / 'quiet.wav', directory / 'long.wav'
        with wave.open(str(quiet), 'wb') as recording:
            recording.setparams(params)
            recording.writeframes((np.frombuffer(pcm, dtype='<i2') * 0.2).astype('<i2').tobytes())
        with wave.open(str(long), 'wb') as recording:
            recording.setparams(params)
            recording.writeframes(pcm * 10)
        assert params.nframes * 10 / params.framerate > 35, 'Long-audio case must exercise segmentation'
        fixtures = [fixture, quiet, long]
        receipt = {'checkpointSha256': artifact['sha256'], 'runs': {}}
        metrics = {}
        for label in ('publisher', 'minimal', 'minimalRepeat'):
            receipt['runs'][label] = run_worker(directory, fixtures, reference=label == 'publisher')
            output.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
            metrics[label] = validate(receipt['runs'][label], minimal=label != 'publisher')
            print(json.dumps({'run': label, **metrics[label]}), flush=True)
        receipt['transcriptChecks'] = require_transcript_checks(metrics)
        assert metrics['minimal']['startupRamBytes'] < metrics['publisher']['startupRamBytes'], metrics
        assert metrics['minimal']['startupMs'] < metrics['publisher']['startupMs'], metrics
        assert not (directory.parent / 'runtime-cache/phonon-2/expanded-fp32.pt').exists()
        receipt['metrics'] = metrics
        receipt['status'] = 'passed'
        output.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
        print(json.dumps({'status': 'passed', 'metrics': metrics}))


if __name__ == '__main__':
    main()
