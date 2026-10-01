"""Pinned, resumable GSM8K main/test evaluation of the installed ECHO weights.

Independent eight-shot prompts; no tools or cross-question archive recall.
This measures arithmetic, not ECHO long-history retrieval. Never train on test.
"""
import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request

MODEL_HASH = '261ef6c572bf9916f9ea5097bc156da0ee0ef6d631d52cf59dbcf293f416b7ae'
DATA_REVISION = '740312add88f781978c0658806c59bc2815b9866'
DATA_HASHES = {'test': 'ee7b8da9e381df27b9e3f7758a159ab2bdaa4dbaa910546cbbc47e0cb44e4f59',
               'train': 'ea82612ea9582142387730c793eb67d3b12849002bc0b7fa6f8efafa7351419d'}

def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(8 << 20), b''):
            h.update(block)
    return h.hexdigest()

def numeric_answer(text):
    # Require an explicit final-answer marker; don't score a stray intermediate
    # number or a truncated chain of thought as a valid final response.
    found = re.findall(r'####\s*([-+]?\d[\d,]*(?:\.\d+)?)', text)
    if not found:
        return None
    try:
        return str(Decimal(found[-1].replace(',', '')).normalize())
    except InvalidOperation:
        return None

def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)

def load_captures(path, test):
    """Resume only hash-bound, internally consistent saved responses."""
    captured = {}
    if not path.exists():
        return captured
    with path.open(encoding='utf-8') as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                row = json.loads(line)
                index = row['index']
                if type(index) is not int or not 0 <= index < len(test) or index in captured:
                    raise ValueError('duplicate or invalid index')
                source = test[index]
                if row['question_sha256'] != hashlib.sha256(source['question'].encode()).hexdigest():
                    raise ValueError('question hash mismatch')
                gold, predicted = numeric_answer(source['answer']), numeric_answer(row['response'])
                correct = predicted is not None and predicted == gold
                if row['gold'] != gold or row['predicted'] != predicted or row['correct'] is not correct:
                    raise ValueError('saved score mismatch')
                captured[index] = row
            except (ValueError, KeyError, TypeError) as error:
                raise RuntimeError(f'Invalid capture at line {line_number}; original file preserved: {error}') from error
    return captured

def request(base, path, value=None, timeout=300):
    payload = json.dumps(value).encode() if value is not None else None
    req = urllib.request.Request(base + path, data=payload, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)

def load_data(out, split):
    import pyarrow.parquet as pq
    path = out / f'gsm8k-{split}.parquet'
    if not path.exists():
        urllib.request.urlretrieve(f'https://huggingface.co/datasets/openai/gsm8k/resolve/{DATA_REVISION}/main/{split}-00000-of-00001.parquet', path)
    if digest(path) != DATA_HASHES[split]:
        raise RuntimeError('Dataset hash mismatch')
    return pq.read_table(path).to_pylist()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, default=Path.home() / 'OpenCore')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8887)
    parser.add_argument('--max-tokens', type=int, default=1024)
    parser.add_argument('--limit', type=int, default=1319)
    args = parser.parse_args()
    out = args.output; out.mkdir(parents=True, exist_ok=True)
    model = args.home / 'OpenCore-Code-Single-File.gguf'
    server = args.home / 'runtime/llama-server.exe'
    stage = args.home / 'opencore-stage.txt'
    if digest(model) != MODEL_HASH:
        raise RuntimeError('Installed model is not the pinned main ECHO artifact')
    base = f'http://127.0.0.1:{args.port}'
    try:
        request(base, '/health', timeout=1)
    except Exception:
        pass
    else:
        raise RuntimeError('Evaluation port is already occupied; do not attach to an unknown run')
    test = load_data(out, 'test')[:args.limit]
    few_shot = load_data(out, 'train')[:8]
    protocol = {'model_sha256': MODEL_HASH, 'model_revision': '69cf90d90cf450df2a0a373d69d4146bbacaa84b',
                'dataset': 'openai/gsm8k', 'dataset_revision': DATA_REVISION, 'config': 'main', 'split': 'test',
                'samples': len(test), 'few_shot': 8, 'few_shot_selection': 'first 8 train rows',
                'max_tokens': args.max_tokens, 'temperature': 0, 'seed': 42, 'fresh_context_per_question': True,
                'archive_recall': False, 'tools': False, 'backend_sha256': digest(server),
                'stage_sha256': digest(stage), 'stage': stage.read_text().strip(), 'kv_precision': 'q4_0',
                'weight_precision': 'original shipped APEX; unchanged'}
    manifest = out / 'protocol.json'
    if manifest.exists() and json.loads(manifest.read_text()) != protocol:
        raise RuntimeError('Cannot resume responses from a different model or evaluation protocol')
    atomic_json(manifest, protocol)
    capture = out / 'responses.jsonl'
    captured = load_captures(capture, test)
    env = dict(os.environ, OPENCORE_BF16_RESIDENT_POOL='1', OPENCORE_BF16_EXPERT_GGUF=str(model),
               OPENCORE_BACKEND_DIR=str(server.parent), OPENCORE_ACTIVE_EXPERTS='5', OPENCORE_WORKFLOW_STAGES='18',
               OPENCORE_Q8_STAGE_EXPERTS='10000', OPENCORE_STAGE_FILE=str(stage), OPENCORE_FUSED_PRIVATE_SHARED='1',
               OPENCORE_CARRIER_GRAPH_INPUTS='1')
    command = [str(server), '-m', str(model), '--host', '127.0.0.1', '--port', str(args.port),
               '-ngl', '99', '-c', '16384', '-b', '512', '-ub', '512', '-np', '1', '-t', '1',
               '--flash-attn', 'on', '--cache-type-k', 'q4_0', '--cache-type-v', 'q4_0', '--no-kv-offload',
               '-sm', 'none', '-mg', '0', '--reasoning', 'off', '--ctx-checkpoints', '64', '--checkpoint-min-step', '256']
    log = (out / 'server.log').open('ab')
    child = subprocess.Popen(command, cwd=args.home, env=env, stdout=log, stderr=log,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    atomic_json(out / 'process.json', {'runner_pid': os.getpid(), 'server_pid': child.pid, 'started': time.time()})
    try:
        deadline = time.monotonic() + 180
        while True:
            if child.poll() is not None: raise RuntimeError(f'Backend exited {child.returncode}; inspect server.log')
            try:
                request(base, '/health', timeout=2); break
            except Exception:
                if time.monotonic() > deadline: raise RuntimeError('Backend startup timed out')
                time.sleep(1)
        warm = request(base, '/v1/chat/completions', {'messages': [{'role': 'user', 'content': 'Write a numbered list of twenty short arithmetic facts.'}], 'max_tokens': 128, 'temperature': 0, 'seed': 42})
        speed = (warm.get('timings') or {}).get('predicted_per_second')
        atomic_json(out / 'speed-gate.json', {'tokens_per_second': speed, 'minimum': 20, 'response': warm})
        if speed is None or speed < 20:
            raise RuntimeError(f'20 tokens/s speed gate failed: {speed}; full benchmark not started')
        print(f'Speed gate passed: {speed:.1f} tokens/s', flush=True)
        examples = '\n\n'.join(f"Question: {row['question']}\nAnswer: {row['answer']}" for row in few_shot)
        with capture.open('a', encoding='utf-8', buffering=1) as stream:
            for index, row in enumerate(test):
                if index in captured: continue
                prompt = f"Solve the problem step by step. Finish with #### followed by the numeric answer.\n\n{examples}\n\nQuestion: {row['question']}\nAnswer:"
                started = time.monotonic()
                response = request(base, '/v1/chat/completions', {'messages': [{'role': 'user', 'content': prompt}], 'max_tokens': args.max_tokens, 'temperature': 0, 'seed': 42, 'cache_prompt': False})
                choice = response['choices'][0]; text = choice['message'].get('content') or ''
                record = {'index': index, 'question_sha256': hashlib.sha256(row['question'].encode()).hexdigest(),
                          'response': text, 'gold': numeric_answer(row['answer']), 'predicted': numeric_answer(text),
                          'finish_reason': choice.get('finish_reason'), 'seconds': time.monotonic()-started,
                          'timings': response.get('timings'), 'usage': response.get('usage')}
                record['correct'] = record['predicted'] is not None and record['predicted'] == record['gold']
                stream.write(json.dumps(record, ensure_ascii=False)+'\n'); stream.flush(); os.fsync(stream.fileno())
                captured[index] = record
                done = len(captured); correct = sum(item['correct'] for item in captured.values())
                atomic_json(out / 'progress.json', {'status': 'running', 'completed': done, 'required': len(test), 'correct_so_far': correct, 'updated': time.time()})
                print(f'{done}/{len(test)} captured; {correct} correct so far; {record["seconds"]:.1f}s', flush=True)
        atomic_json(out / 'score.json', {'status': 'complete', 'benchmark': 'GSM8K', 'correct': sum(item['correct'] for item in captured.values()),
                    'total': len(test), 'accuracy': sum(item['correct'] for item in captured.values()) / len(test),
                    'truncated': sum(item['finish_reason']=='length' for item in captured.values()), 'protocol': protocol})
    except BaseException as error:
        atomic_json(out / 'failure.json', {'error': str(error), 'type': type(error).__name__, 'captured': len(captured), 'time': time.time()})
        raise
    finally:
        child.terminate()
        try: child.wait(timeout=10)
        except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=10)
        log.close()

if __name__ == '__main__': main()
