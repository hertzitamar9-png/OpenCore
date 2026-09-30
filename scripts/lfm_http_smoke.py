"""Manual GPU integration checks for the four optional LFM profiles, not a benchmark."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote
import urllib.request

APP = Path(__file__).resolve().parents[1]
ECHO_SERVER = APP / 'src-tauri/resources/echo/echo_server.py'
PROFILES = {'fusioncore-kv': 131072, 'fusioncore-echo': 131072,
            'dualcore-kv': 131072, 'dualcore-echo': 131072}
TOOLS = [{'type': 'function', 'function': {'name': 'sum_numbers',
    'description': 'Add two integers.', 'parameters': {'type': 'object',
    'properties': {'a': {'type': 'integer'}, 'b': {'type': 'integer'}},
    'required': ['a', 'b'], 'additionalProperties': False}}}]
MIN_USER_ANSWER_TPS = 20
MIN_USER_ANSWER_TOKENS = 50
MAX_ECHO_SPEED_CALLS = 4


def load_speed_prompt(path, prompt_id):
    inputs = json.loads(Path(path).read_text(encoding='utf-8'))
    for row in inputs.get('rows', []):
        if row.get('id') == prompt_id and isinstance(row.get('prompt'), str):
            return row['prompt']
    raise ValueError(f'Speed prompt {prompt_id} is missing from {path}')


def build_speed_payload(prompt, profile, conversation_id, max_tokens, thinking_budget_tokens=None):
    payload = {'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': max_tokens, 'temperature': 0, 'stream': True,
        'stream_options': {'include_usage': True}}
    if profile.endswith('-echo'):
        payload['conversation_id'] = conversation_id
        payload['echo_max_calls'] = MAX_ECHO_SPEED_CALLS
    if thinking_budget_tokens is not None:
        if not isinstance(thinking_budget_tokens, int) or thinking_budget_tokens < 1:
            raise ValueError('Thinking budget must be a positive integer')
        payload['thinking_budget_tokens'] = thinking_budget_tokens
    return payload


def build_runtime_command(profile, checkpoint, port, thinking_budget_tokens=None):
    command = [sys.executable, '-u', str(APP / 'src-tauri/resources/lfm/serve_lfm.py'),
               '--profile', profile, '--checkpoint', str(checkpoint),
               '--runtime', str(APP / 'src-tauri/resources/doucode/runtime'), '--port', str(port)]
    if thinking_budget_tokens is not None:
        if isinstance(thinking_budget_tokens, bool) or not isinstance(thinking_budget_tokens, int) or thinking_budget_tokens < 0:
            raise ValueError('Thinking budget must be a non-negative integer')
        command += ['--reasoning-budget-tokens', str(thinking_budget_tokens)]
    return command


def free_loopback_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def validate_echo_readiness(status, expected_context):
    if status.get('contextMode') != 'persistent_echo':
        raise RuntimeError('Readiness endpoint did not identify the persistent ECHO proxy')
    if status.get('windowTokens') != expected_context:
        raise RuntimeError('ECHO proxy context differs from the selected profile')


def delta_text(value):
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ('text', 'content', 'reasoning_content'):
            if isinstance(value.get(key), str):
                return value[key]
        if any(key in value for key in ('text', 'content', 'reasoning_content')):
            return ''.join(delta_text(value.get(key))
                           for key in ('reasoning_content', 'content', 'text')
                           if key in value)
    if isinstance(value, list):
        return ''.join(delta_text(part) for part in value)
    excerpt = repr(value)[:160]
    raise TypeError(f'Unsupported streamed content shape: {type(value).__name__} {excerpt}')


def extract_stream_text(events):
    return ''.join(delta_text(choice.get('delta', {}).get('content'))
                   for event in events for choice in event.get('choices', []))


def speed_probe_failure(probe):
    failures = []
    minimum = int(probe.get('minimum_streamed_draft_tokens_per_second', MIN_USER_ANSWER_TPS))
    if probe.get('streamed_draft_tokens', 0) < MIN_USER_ANSWER_TOKENS:
        failures.append(f"draft was shorter than {MIN_USER_ANSWER_TOKENS} tokens")
    if probe.get('streamed_draft_tokens_per_second', 0) < minimum:
        failures.append(f"streamed draft rate was below {minimum} tokens/s")
    if probe.get('selected_answer_tokens', 0) < MIN_USER_ANSWER_TOKENS:
        failures.append(f"selected answer was shorter than {MIN_USER_ANSWER_TOKENS} tokens")
    if probe.get('aggregate_model_tokens_per_second', 0) < minimum:
        failures.append(f"aggregate model decode rate was below {minimum} tokens/s")
    answer_minimum = int(probe.get('minimum_selected_answer_wall_tokens_per_second', minimum))
    if probe.get('selected_answer_wall_tokens_per_second', 0) < answer_minimum:
        failures.append(f"user-visible answer rate was below {answer_minimum} tokens/s")
    if probe.get('selected_finish_reason') != 'stop':
        failures.append(f"selected answer did not stop cleanly (finish_reason={probe.get('selected_finish_reason')!r})")
    return '; '.join(failures)


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
    parser.add_argument('--decoder-only', action='store_true',
                        help='Qualify the selected profile backend directly, without the optional ECHO HTTP proxy.')
    parser.add_argument('--speed-only', action='store_true',
                        help='Skip structured-tool and exact-answer assertions; retain profile, ECHO, and sustained-speed checks.')
    parser.add_argument('--speed-prompt-file', type=Path,
                        help='Use a representative task prompt from a benchmark inputs JSON file.')
    parser.add_argument('--speed-prompt-id', default='HumanEval/32',
                        help='Prompt ID to read from --speed-prompt-file (default: HumanEval/32).')
    parser.add_argument('--speed-max-tokens', type=int, default=512,
                        help='Completion budget for the speed probe (default: 512, including hidden reasoning).')
    parser.add_argument('--thinking-budget-tokens', type=int,
                        help='Optional bounded reasoning budget for each speed-probe request.')
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
        echo_child = None
        echo_archive = None
        started = time.perf_counter()
        with log_path.open('w', encoding='utf-8') as log:
            child = subprocess.Popen(build_runtime_command(
                profile, args.checkpoint, 8870, args.thinking_budget_tokens),
                stdout=log, stderr=log, env=env)
            try:
                base = 'http://127.0.0.1:8870'
                client_base = base
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
                if profile.endswith('-echo') and not args.decoder_only:
                    echo_archive = tempfile.mkdtemp(prefix=f'opencore-{profile}-echo-')
                    echo_port = free_loopback_port()
                    echo_child = subprocess.Popen([sys.executable, '-u', str(ECHO_SERVER),
                        '--upstream', base, '--port', str(echo_port), '--max-continuations', '0',
                        '--offload-every', '100', '--warm-cache-budget-mb', '128',
                        '--archive', echo_archive, '--context-size', str(PROFILES[profile]),
                        '--no-console'], stdout=log, stderr=log, env=env, cwd=APP)
                    client_base = f'http://127.0.0.1:{echo_port}'
                    ready_conversation = f'qualification-{profile}-ready'
                    proxy_deadline = time.monotonic() + 180
                    while True:
                        if echo_child.poll() is not None:
                            raise RuntimeError(f'ECHO proxy exited; inspect {log_path}')
                        try:
                            with request(client_base, f'/echo/context?conversation={quote(ready_conversation)}') as response:
                                echo_status = json.load(response)
                            validate_echo_readiness(echo_status, PROFILES[profile])
                            record['echo_readiness'] = echo_status
                            break
                        except OSError:
                            if time.monotonic() >= proxy_deadline:
                                raise TimeoutError(f'ECHO proxy startup timed out; inspect {log_path}')
                            time.sleep(0.5)
                    record['echo_proxy'] = True
                elif profile.endswith('-echo'):
                    record['echo_proxy'] = False
                    record['qualification_route'] = 'direct profile decoder; ECHO proxy excluded from isolated speed measurement'
                with request(client_base, '/props') as response:
                    record['props'] = json.load(response)
                assert record['props']['n_ctx'] == PROFILES[profile]
                assert record['props']['lfm']['complete_towers'] == 2
                assert record['props']['lfm']['cache_mode'] == 'incremental_F16_KV'
                if not args.speed_only:
                    payload = {'messages': [{'role': 'user', 'content':
                        'Use sum_numbers to add 19 and 23. Do not answer directly.'}],
                        'tools': TOOLS, 'max_tokens': 256}
                    if profile.endswith('-echo'):
                        payload['conversation_id'] = f'qualification-{profile}-tool'
                    with request(client_base, '/v1/chat/completions', payload) as response:
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
                if profile.endswith('-echo'):
                    payload['conversation_id'] = f'qualification-{profile}-answer'
                with request(client_base, '/v1/chat/completions', payload) as response:
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
                if not args.speed_only:
                    assert content.strip() == '42', repr(content)
                assert any(e.get('choices', [{}])[0].get('finish_reason') == 'stop' for e in events)
                record['stream'] = {'answer': content, 'event_count': len(events),
                    'first_delta_seconds': first_delta, 'seconds': time.perf_counter() - stream_started,
                    'usage': next(e['usage'] for e in reversed(events) if 'usage' in e)}
                speed_prompt = load_speed_prompt(args.speed_prompt_file, args.speed_prompt_id) if args.speed_prompt_file else (
                    'Write the integers from 1 through 80 in order on one line, separated by spaces. '
                    'Output only the numbers.'
                )
                speed_payload = build_speed_payload(
                    speed_prompt, profile, f'qualification-{profile}-speed', args.speed_max_tokens,
                    args.thinking_budget_tokens)
                speed_events, speed_timeline = [], []
                speed_started = time.perf_counter()
                with request(client_base, '/v1/chat/completions', speed_payload) as response:
                    for line in response:
                        if not line.startswith(b'data: '):
                            continue
                        data = line[6:].strip()
                        if data == b'[DONE]':
                            break
                        event = json.loads(data)
                        if 'error' in event:
                            raise RuntimeError(event['error'])
                        speed_events.append(event)
                        speed_timeline.append((time.perf_counter() - speed_started, event))
                speed_seconds = time.perf_counter() - speed_started
                speed_text = extract_stream_text(speed_events)
                selected_finish_reason = next((choice.get('finish_reason')
                    for event in reversed(speed_events) for choice in event.get('choices', [])
                    if choice.get('finish_reason') is not None), None)
                speed_usage = next(e['usage'] for e in reversed(speed_events) if 'usage' in e)
                with request(client_base, '/tokenize', {'content': speed_text}) as response:
                    answer_tokens = len(json.load(response)['tokens'])
                preview_timeline = [(at, event['echo_preview']['delta']) for at, event in speed_timeline
                                    if isinstance(event.get('echo_preview'), dict)
                                    and event['echo_preview'].get('delta')]
                draft_timeline = preview_timeline or [
                    (at, event['choices'][0]['delta']['content']) for at, event in speed_timeline
                    if event.get('choices') and event['choices'][0].get('delta', {}).get('content')]
                draft_parts = [delta_text(text) for _, text in draft_timeline]
                stream_source = 'ECHO draft' if preview_timeline else 'model deltas'
                if len(draft_timeline) < 2:
                    reasoning_timeline = [
                        (at, event['choices'][0]['delta']['reasoning_content']) for at, event in speed_timeline
                        if event.get('choices') and event['choices'][0].get('delta', {}).get('reasoning_content')]
                    if len(reasoning_timeline) > 1:
                        draft_timeline = reasoning_timeline
                        draft_parts = [delta_text(text) for _, text in draft_timeline]
                        stream_source = 'model reasoning deltas'
                draft_times = [at for at, _ in draft_timeline]
                draft_text = ''.join(draft_parts)
                with request(client_base, '/tokenize', {'content': draft_text}) as response:
                    draft_tokens = len(json.load(response)['tokens'])
                draft_seconds = max(0.0, draft_times[-1] - draft_times[0]) if len(draft_times) > 1 else 0.0
                draft_tps = draft_tokens / draft_seconds if draft_seconds else 0.0
                answer_wall_tps = answer_tokens / speed_seconds if speed_seconds else 0.0
                model_tokens = int(speed_usage.get('completion_tokens', 0))
                record['speed_probe'] = {
                    'prompt_id': args.speed_prompt_id if args.speed_prompt_file else None,
                    'prompt': speed_prompt,
                    'stream_source': stream_source,
                    'streamed_draft_tokens': draft_tokens,
                    'streamed_draft_seconds': round(draft_seconds, 3),
                    'streamed_draft_tokens_per_second': round(draft_tps, 2),
                    'selected_answer_tokens': answer_tokens,
                    'selected_answer_wall_tokens_per_second': round(answer_wall_tps, 2),
                    'selected_finish_reason': selected_finish_reason,
                    'selected_answer_preview': speed_text[:240],
                    'all_brain_tokens': model_tokens,
                    'elapsed_seconds': round(speed_seconds, 3),
                    'aggregate_model_tokens_per_second': round(model_tokens / speed_seconds, 2)
                        if speed_seconds else 0.0,
                    'minimum_streamed_draft_tokens_per_second': MIN_USER_ANSWER_TPS,
                    'minimum_aggregate_model_tokens_per_second': MIN_USER_ANSWER_TPS,
                    'minimum_user_answer_tokens': MIN_USER_ANSWER_TOKENS,
                    'draft_matches_selected_answer': draft_text == speed_text,
                }
                failure = speed_probe_failure(record['speed_probe'])
                assert not failure, failure
                record['qualification_kind'] = 'speed_only' if args.speed_only else 'functional_and_speed'
                record['passed'] = True
            except Exception as error:
                record['error'] = f'{type(error).__name__}: {error}'
            finally:
                if echo_child is not None and echo_child.poll() is None:
                    if os.name == 'nt':
                        subprocess.run(['taskkill', '/PID', str(echo_child.pid), '/T', '/F'], capture_output=True, check=False)
                    else:
                        echo_child.terminate()
                    echo_child.wait(timeout=30)
                if child.poll() is None:
                    if os.name == 'nt':
                        subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'], capture_output=True, check=False)
                    else:
                        child.terminate()
                    child.wait(timeout=30)
                if echo_archive is not None:
                    if record['passed']:
                        shutil.rmtree(echo_archive, ignore_errors=True)
                    else:
                        record['debug_archive_directory'] = echo_archive
                report['profiles'].append(record)
                args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
                print(json.dumps({'profile': profile, 'passed': record['passed'],
                    'load_seconds': record.get('load_seconds'), 'stream': record.get('stream'),
                    'speed_probe': record.get('speed_probe'),
                    'error': record.get('error')}), flush=True)
    return 0 if all(record['passed'] for record in report['profiles']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
