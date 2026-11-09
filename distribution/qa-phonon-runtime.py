"""Opt-in real installation and microphone checks with separately supplied weights."""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / '../src-tauri/resources/runtime-setup/hf-extension'))
from hf_bridge import BridgeState, BridgeServer, RuntimeUnavailable
from hf_speech import supports
from phonon_runtime import stage


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--weights', required=True, type=Path)
    parser.add_argument('--ui', type=Path)
    args = parser.parse_args()
    catalog = json.loads((HERE / '../src-tauri/resources/runtime-setup/hf-extension/publisher-models.json').read_text())['entries']
    entries = [entry for entry in catalog if supports(entry)]
    assert len(entries) == 5
    results = []
    with tempfile.TemporaryDirectory(prefix='opencore-phonon25-', dir=args.weights) as temporary:
        root = Path(temporary)
        resources = root / 'resources'
        stage(resources)
        state = BridgeState(root / 'home', resources, runtime=RuntimeUnavailable())
        try:
            state.entries = {entry['id']: entry for entry in entries}
            state.persist_catalog()
            for entry in entries:
                for pin in entry['files']:
                    source = (args.weights / pin['filename'] if pin['role'] == 'model'
                              else HERE / '../src-tauri/resources/speech/PHONON25-NOTICE.txt')
                    state.downloader.register_shared(pin, source)
            audio = base64.b64encode((resources / 'speech/parakeet/verification.wav').read_bytes()).decode()
            for entry in entries:
                started = time.monotonic()
                state.progress = {'id': entry['id'], 'status': 'running', 'kind': 'download'}
                state._work(entry, 'download', threading.Event(), threading.Event())
                assert state.progress['status'] == 'ready', state.progress
                installed = state.entry(entry['id'], installed=True)
                assert installed['runtimeReady']
                state.speech.select(entry['id'])
                session = 'test-' + entry['speechQuantization']
                state.speech.start(session)
                speech_started = time.monotonic()
                result = state.speech.transcribe(session, audio)
                assert 'quick brown fox' in result['text'].lower(), result
                state.speech.cancel(session)
                assert state.speech.status()['workerReady']
                report = {'quantization': entry['quantization'], 'ready': True,
                          'transcriptionSeconds': round(time.monotonic() - speech_started, 3),
                          'installationAndTestSeconds': round(time.monotonic() - started, 3),
                          'modelBytes': installed['runtimePreparation']['modelBytes']}
                results.append(report)
                print(json.dumps(report), flush=True)
            if args.ui:
                packed = next(entry for entry in entries if entry['speechQuantization'] == 'packed')
                state.speech.select(packed['id'])
                secret = secrets.token_hex(32)
                server = BridgeServer(('127.0.0.1', 0), state, secret)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    env = dict(os.environ, OPENCORE_UI_OUTPUT=str(args.ui.resolve()),
                               OPENCORE_SPEECH_QA_URL='http://127.0.0.1:' + str(server.server_port),
                               OPENCORE_SPEECH_QA_SECRET=secret)
                    subprocess.run(['node', str(HERE / 'qa-phonon-microphone.cjs')], env=env, check=True)
                finally:
                    server.shutdown()
                    server.server_close()
        finally:
            state.close()
    (args.weights / 'runtime-qa-results.json').write_text(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
