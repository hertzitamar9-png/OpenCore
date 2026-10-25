"""Generate with an actual downloaded publisher checkpoint and its ECHO endpoint."""
import argparse
import json
from pathlib import Path
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'src-tauri/resources/runtime-setup/hf-extension'))
from hf_runtime import RuntimeManager

parser = argparse.ArgumentParser()
parser.add_argument('model', choices=['flint-nvfp4-gguf', 'flint-iq2-xxs', 'raon-1-mini-iq4-xs', 'tess-iq2-xxs'])
args = parser.parse_args()
root = HERE / '.output/publisher-live'
entry = json.loads((root / (args.model + '-entry.json')).read_text())
resources = HERE / '.output/published-145-check'
runtime = RuntimeManager(root, resources)
runtime.on_progress = lambda **values: print(json.dumps({'model': args.model, **values}), flush=True)
start = time.monotonic()
try:
    proof = runtime.prepare(entry)
    conversation = 'qa-publisher-' + uuid.uuid4().hex
    runtime.set_context(entry['id'], conversation, 'qa-' + uuid.uuid4().hex)
    data, content_type = runtime.chat(entry, {
        'model': entry['id'], 'messages': [{'role': 'user', 'content': 'Reply with one short greeting. The verification word is sapphire.'}],
        'max_tokens': 64, 'temperature': 0, 'stream': False, 'opencore_reasoning_effort': 'off',
        'chat_template_kwargs': {'enable_thinking': False},
    })
    value = json.loads(data)
    message = value['choices'][0]['message']
    if not (message.get('content') or message.get('reasoning_content')):
        raise RuntimeError('ECHO did not return generated output from the actual checkpoint')
    if message.get('reasoning_content') or message.get('reasoning'):
        raise RuntimeError('Reasoning Off returned reasoning text')
    streaming, stream_type = runtime.chat(entry, {
        'model': entry['id'], 'messages': [{'role': 'user', 'content': 'Reply with the exact words: Stream verified.'}],
        'max_tokens': 32, 'temperature': 0, 'stream': True, 'opencore_reasoning_effort': 'off',
    })
    stream_text, stream_reasoning = '', ''
    for line in streaming:
        if not line.startswith(b'data:'):
            continue
        raw = line[5:].strip()
        if raw == b'[DONE]':
            continue
        event = json.loads(raw)
        if event.get('error'):
            raise RuntimeError('Streaming ECHO returned an error: ' + str(event['error']))
        for choice in event.get('choices', []):
            delta = choice.get('delta') or {}
            stream_text += delta.get('content') or ''
            stream_reasoning += delta.get('reasoning_content') or delta.get('reasoning') or ''
    if not stream_text.strip() or stream_reasoning:
        raise RuntimeError('Streaming ECHO did not return content with reasoning Off')
    result = {'passed': True, 'model': args.model, 'repository': entry['repository'], 'revision': entry['revision'],
              'format': entry['format'], 'quantization': entry['quantization'], 'realCheckpoint': True,
              'cpuThreads': 2, 'echoConversation': conversation, 'echoResponse': message,
              'usage': value.get('usage'), 'runtimeProof': proof, 'reasoningOffVerified': True,
              'streamingVerified': stream_type == 'text/event-stream', 'streamResponse': stream_text,
              'elapsedSeconds': time.monotonic() - start}
    (root / (args.model + '-generation.json')).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2), flush=True)
finally:
    runtime.stop()
