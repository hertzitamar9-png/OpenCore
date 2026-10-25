"""Verify real published EdgeRock quants through the Windows engine and ECHO."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import shutil
import sys
import threading
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'src-tauri/resources/runtime-setup/hf-extension'))
from hf_catalog import build_plan
from hf_download import Downloader, HubClient
from hf_runtime import RuntimeManager
import managed_engines as engines

parser = argparse.ArgumentParser()
parser.add_argument('--root', type=Path, default=HERE / '.output/edgerock-quant-verification')
parser.add_argument('--resources', type=Path, default=HERE.parent / 'src-tauri/resources')
parser.add_argument('--runtime-archive', type=Path, required=True)
parser.add_argument('--quants', nargs='+', default=['Q3_K_M', 'Q4_0', 'Q4_K_S', 'Q4_K_M', 'Q5_K_M'])
parser.add_argument('--discard-weights', action='store_true')
parser.add_argument('--output', type=Path, default=HERE / '.output/edgerock-quant-verification.json')
args = parser.parse_args()
root = args.root.resolve()
if args.discard_weights and root.exists():
    raise ValueError('Discard mode requires a new isolated verification directory')
root.mkdir(parents=True, exist_ok=True)
archive = args.runtime_archive.resolve(strict=True)
pin = engines.definition('edgerock-cpu-v1')
if engines.digest(archive) != pin['sha256']:
    raise ValueError('The runtime archive is not the pinned release candidate')
target = engines.folder(root, 'edgerock-cpu-v1')
target.parent.mkdir(parents=True, exist_ok=True)
with engines.runtime_lock(target.with_suffix('.lock')):
    engines._install(target, dict(pin, url=archive.as_uri()), 'edgerock-cpu-v1', lambda **v: None, lambda: None)
client = HubClient()
metadata = client.json('/api/models/hkust-aaron/EdgeRock-4B-GGUF/revision/a5e045c646b70303156394d52c2ca1e415ec1544?blobs=true')
plan = build_plan(metadata)
checks = []
for quant in args.quants:
    entry = next(value for value in plan['variants'] if value['quantization'] == quant)
    entry['contextTokens'] = 8192
    if shutil.disk_usage(root).free < entry['bytes'] + 2 * 1024 ** 3:
        raise RuntimeError('Not enough disk space for the isolated quantization check')
    started, last = time.monotonic(), [0]
    def progress(value):
        if time.monotonic() - last[0] > 30:
            last[0] = time.monotonic()
            print(json.dumps({'quantization': quant, **value}), flush=True)
    receipt = Downloader(root, client).install(entry, threading.Event(), progress)
    entry.update(localDirectory=receipt['localDirectory'], receiptPath=receipt['receiptPath'], status='installed')
    runtime = RuntimeManager(root, args.resources)
    runtime.on_progress = lambda **value: print(json.dumps({'quantization': quant, **value}), flush=True)
    try:
        proof = runtime.prepare(entry)
        body = {'model': entry['id'], 'messages': [{'role': 'user', 'content': 'Calculate 17 * 23. Reply with the number only.'}],
                'opencore_reasoning_effort': 'off', 'max_tokens': 32, 'temperature': 0, 'stream': False}
        response, _ = runtime.chat(entry, body)
        reply = json.loads(response)['choices'][0]['message']
        content = str(reply.get('content') or '')
        thoughts = re.findall(r'<think>(.*?)</think>', content, flags=re.I | re.S)
        if '391' not in content or any(value.strip() for value in thoughts) or reply.get('reasoning_content') or reply.get('reasoning'):
            raise RuntimeError('The real checkpoint did not return the expected arithmetic response with reasoning Off')
        chunks, kind = runtime.chat(entry, dict(body, stream=True,
            messages=[{'role': 'user', 'content': 'Reply with the words: streaming confirmed.'}]))
        text, reasoning = '', ''
        for line in chunks:
            if not line.startswith(b'data:') or line[5:].strip() == b'[DONE]':
                continue
            event = json.loads(line[5:].strip())
            if event.get('error'):
                raise RuntimeError(str(event['error']))
            for choice in event.get('choices', []):
                delta = choice.get('delta') or {}
                text += delta.get('content') or ''
                reasoning += delta.get('reasoning_content') or delta.get('reasoning') or ''
        if kind != 'text/event-stream' or 'confirmed' not in text.lower() or reasoning or any(
                value.strip() for value in re.findall(r'<think>(.*?)</think>', text, flags=re.I | re.S)):
            raise RuntimeError('The actual streaming ECHO check failed')
        checks.append({'quantization': quant, 'passed': True, 'realCheckpoint': True,
            'checkpointSha256': entry['files'][0]['sha256'], 'checkpointBytes': entry['bytes'],
            'reply': reply, 'streamReply': text, 'engineArchiveSha256': proof['engineArchiveSha256'],
            'echoMode': proof['echoMode'], 'contextTokens': proof['contextTokens'], 'cpuThreads': 2,
            'elapsedSeconds': time.monotonic() - started})
        print(json.dumps(checks[-1]), flush=True)
    finally:
        runtime.stop()
    if args.discard_weights:
        managed = (root / 'models/hub').resolve()
        for value in receipt['files']:
            path = Path(value['localPath']).resolve()
            if not path.is_relative_to(managed) or not path.name.lower().endswith('.gguf'):
                raise ValueError('Refusing to discard a file outside the isolated GGUF test directory')
            path.unlink()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'passed': len(checks) == len(args.quants), 'platform': 'Windows x64 CPU',
        'repository': plan['repository'], 'revision': plan['revision'],
        'runtimeSourceSha256': hashlib.sha256(Path(sys.modules['hf_runtime'].__file__).read_text(encoding='utf-8').encode()).hexdigest(),
        'checks': checks}, indent=2) + '\n', encoding='utf-8')
