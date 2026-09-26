"""Manual GPU integration checks for the four optional LFM profiles, not a benchmark."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

APP = Path(__file__).resolve().parents[1]
PROFILES = {'fusioncore-kv': 131072, 'fusioncore-echo': 8192,
            'dualcore-kv': 131072, 'dualcore-echo': 32768}
TOOLS = [{'type': 'function', 'function': {'name': 'sum_numbers',
    'description': 'Add two integers.', 'parameters': {'type': 'object',
    'properties': {'a': {'type': 'integer'}, 'b': {'type': 'integer'}},
    'required': ['a', 'b'], 'additionalProperties': False}}}]


def request(base, path, payload=None):
    req = urllib.request.Request(base + path,
        None if payload is None else json.dumps(payload).encode(),
        {'Content-Type': 'application/json'})
    return urllib.request.urlopen(req, timeout=300)


def gpu():
    return subprocess.check_output(['nvidia-smi', '--query-gpu=memory.used,memory.total',
        '--format=csv,noheader,nounits'], text=True).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--profiles', nargs='+', choices=list(PROFILES), default=list(PROFILES))
    parser.add_argument('--output', type=Path, default=APP / 'tests/evidence/lfm-four-profiles-qualification-2026-09-26.json')
    args = parser.parse_args()
    report = {'scope': 'Real GPU HTTP integration; not HumanEval, LiveBench or a quality comparison',
        'native_build': json.loads((APP / 'src-tauri/resources/lfm/native/build-info.json').read_text()),
        'checkpoint': json.loads((APP / 'src-tauri/resources/lfm/checkpoint.json').read_text()), 'profiles': []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for profile in args.profiles:
        record = {'profile': profile, 'passed': False}
        log_path = Path(tempfile.gettempdir()) / f'opencore-{profile}-qualification.log'
        env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
        started = time.perf_counter()
        with log_path.open('w', encoding='utf-8') as log:
            child = subprocess.Popen([sys.executable, '-u', str(APP / 'src-tauri/resources/lfm/serve_lfm.py'),
                '--profile', profile, '--checkpoint', str(args.checkpoint),
                '--runtime', str(APP / 'src-tauri/resources/doucode/runtime'), '--port', '8870'],
                stdout=log, stderr=log, env=env)
            try:
                base = 'http://127.0.0.1:8870'
                deadline = time.monotonic() + 240
                while True:
                    if child.poll() is not None:
                        raise RuntimeError(f'Runtime exited; inspect {log_path}')
                    try:
                        with request(base, '/health') as response:
                            if json.load(response)['ready']:
                                break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError(f'Runtime startup timed out; inspect {log_path}')
                        time.sleep(0.5)
                record['load_seconds'] = time.perf_counter() - started
                record['gpu_mib_used_total'] = gpu()
                with request(base, '/props') as response:
                    record['props'] = json.load(response)
                assert record['props']['n_ctx'] == PROFILES[profile]
                assert record['props']['lfm']['complete_towers'] == 2
                assert record['props']['lfm']['cache_mode'] == ('recompute_each_token' if profile.endswith('echo') else 'KV')
                payload = {'messages': [{'role': 'user', 'content':
                    'Use sum_numbers to add 19 and 23. Do not answer directly.'}],
                    'tools': TOOLS, 'max_tokens': 256}
                with request(base, '/v1/chat/completions', payload) as response:
                    record['tool_completion'] = json.load(response)
                choice = record['tool_completion']['choices'][0]
                assert choice['finish_reason'] == 'tool_calls'
                call = choice['message']['tool_calls'][0]['function']
                assert call['name'] == 'sum_numbers'
                assert json.loads(call['arguments']) == {'a': 19, 'b': 23}
                payload = {'messages': [{'role': 'user', 'content':
                    'What is 19 plus 23? Reply with just the result.'}], 'max_tokens': 256, 'stream': True}
                events, first_delta = [], None
                stream_started = time.perf_counter()
                with request(base, '/v1/chat/completions', payload) as response:
                    for line in response:
                        if not line.startswith(b'data: '):
                            continue
                        data = line[6:].strip()
                        if data == b'[DONE]':
                            break
                        event = json.loads(data)
                        if 'error' in event:
                            raise RuntimeError(event['error'])
                        events.append(event)
                        delta = event.get('choices', [{}])[0].get('delta', {})
                        if first_delta is None and (delta.get('content') or delta.get('reasoning_content') or event.get('echo_preview')):
                            first_delta = time.perf_counter() - stream_started
                content = ''.join(e.get('choices', [{}])[0].get('delta', {}).get('content', '') for e in events)
                assert content.strip() == '42', repr(content)
                assert any(e.get('choices', [{}])[0].get('finish_reason') == 'stop' for e in events)
                record['stream'] = {'answer': content, 'event_count': len(events),
                    'first_delta_seconds': first_delta, 'seconds': time.perf_counter() - stream_started,
                    'usage': next(e['usage'] for e in reversed(events) if 'usage' in e)}
                record['passed'] = True
            except Exception as error:
                record['error'] = f'{type(error).__name__}: {error}'
            finally:
                if child.poll() is None:
                    if os.name == 'nt':
                        subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'], capture_output=True, check=False)
                    else:
                        child.terminate()
                    child.wait(timeout=30)
                report['profiles'].append(record)
                args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
                print(json.dumps({'profile': profile, 'passed': record['passed'],
                    'load_seconds': record.get('load_seconds'), 'stream': record.get('stream'),
                    'error': record.get('error')}), flush=True)
    return 0 if all(record['passed'] for record in report['profiles']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
