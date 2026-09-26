from __future__ import annotations
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time
import uuid
import urllib.request

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / 'doucode'))
from duocore.runtime import launch_llama_server, stop_llama_server, wait_healthy
from duocore.spec import BackboneSpec
from dual import DualCoreEngine
from fusion import FusionCoreModel
from protocol import OutputStream, with_tools


PROFILES = {
    'dualcore-kv': ('DualCore KV', 'dual', False, 131072),
    'dualcore-echo': ('DualCore ECHO', 'dual', True, 32768),
    'fusioncore-kv': ('FusionCore KV', 'fusion', False, 131072),
    'fusioncore-echo': ('FusionCore ECHO', 'fusion', True, 8192),
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print('[lfm] ' + fmt % args, file=sys.stderr, flush=True)

    def send_json(self, code, value):
        body = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_event(self, value):
        self.wfile.write(b'data: ' + json.dumps(value, ensure_ascii=False).encode('utf-8') + b'\n\n')
        self.wfile.flush()

    def do_GET(self):
        state = self.server.state
        if self.path == '/health':
            self.send_json(200, {'status': 'ok', 'ready': True, 'model': state['name'], 'precision': 'Q8_0 MAX'})
        elif self.path in ('/v1/models', '/models'):
            self.send_json(200, {'object': 'list', 'data': [{'id': state['name'], 'object': 'model'}]})
        elif self.path == '/props':
            self.send_json(200, {'model': state['name'], 'n_ctx': state['context'],
                'default_generation_settings': {'n_ctx': state['context']}, 'lfm': state['evidence']})
        else:
            self.send_json(404, {'error': {'message': 'not found'}})

    def do_POST(self):
        streaming = False
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length < 1 or length > 16*1024*1024:
                raise ValueError('Request body must be between 1 byte and 16 MiB')
            payload = json.loads(self.rfile.read(length))
            state = self.server.state
            if self.path in ('/tokenize', '/detokenize') and state['kind'] == 'dual' and not state['engine'].native:
                request = urllib.request.Request(state['engine'].brains[0].base_url + self.path,
                    json.dumps(payload).encode(), {'Content-Type': 'application/json'})
                with urllib.request.urlopen(request, timeout=60) as response:
                    self.send_json(200, json.load(response))
                return
            native = state['engine'] if state['kind'] == 'fusion' else state['engine'].native
            if self.path == '/tokenize' and native:
                with self.server.lock:
                    tokens = native.tokenize(payload.get('content') or '')
                self.send_json(200, {'tokens': tokens})
                return
            if self.path == '/detokenize' and native:
                with self.server.lock:
                    content = native.detokenize(payload.get('tokens') or [])
                self.send_json(200, {'content': content})
                return
            if self.path not in ('/v1/chat/completions', '/chat/completions'):
                self.send_json(404, {'error': {'message': 'not found'}})
                return
            messages = payload.get('messages')
            if not isinstance(messages, list) or not messages:
                raise ValueError('messages must be a nonempty array')
            if any(not isinstance(message, dict) or not isinstance(message.get('content', ''), str) for message in messages):
                raise ValueError('LFM is text-only. Use the vision-capable OpenCore profile for images.')
            tools = payload.get('tools') or None
            streaming = payload.get('stream') is True
            generation = 'lfm-' + uuid.uuid4().hex
            if streaming:
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Cache-Control', 'no-cache')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.close_connection = True
            def chunk(delta, finish=None, usage=None):
                value = {'id': generation, 'object': 'chat.completion.chunk', 'model': state['name'],
                         'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
                if usage is not None:
                    value['usage'] = usage
                self.send_event(value)
            with self.server.lock:
                started = time.perf_counter()
                requested = payload.get('max_completion_tokens', payload.get('max_tokens'))
                limit = state['context'] if requested is None or int(requested) <= 0 else min(int(requested), state['context'])
                if state['kind'] == 'fusion':
                    engine = state['engine']
                    prompt = engine.start(with_tools(messages, tools))
                    limit = min(limit, state['context'] - prompt)
                    if limit <= 0:
                        raise ValueError('Prompt leaves no output space in the active context')
                    decoder = OutputStream()
                    for part in engine.stream(limit):
                        for delta in decoder.feed(part):
                            if streaming:
                                chunk(delta)
                    for delta in decoder.feed('', final=True):
                        if streaming:
                            chunk(delta)
                    message = decoder.message(tools)
                    if streaming and message.get('tool_calls'):
                        chunk({'tool_calls': [dict(call, index=index) for index, call in enumerate(message['tool_calls'])]})
                    usage = {'prompt_tokens': prompt, 'completion_tokens': engine.completion_tokens,
                             'total_tokens': prompt + engine.completion_tokens}
                    evidence = state['evidence']
                    finish = 'tool_calls' if message.get('tool_calls') else engine.finish_reason
                else:
                    preview = (lambda delta: self.send_event({'echo_preview': {'generation': generation,
                               'phase': 'drafting', 'delta': delta}})) if streaming else None
                    message, usage, evidence = state['engine'].complete(messages, tools, limit, preview)
                    finish = 'tool_calls' if message.get('tool_calls') else evidence.get('finish_reason', 'stop')
                    if streaming:
                        delta = dict(message)
                        if delta.get('tool_calls'):
                            delta['tool_calls'] = [dict(call, index=index) for index, call in enumerate(delta['tool_calls'])]
                        chunk(delta)
                seconds = time.perf_counter() - started
            if streaming:
                chunk({}, finish, usage)
                self.wfile.write(b'data: [DONE]\n\n'); self.wfile.flush()
            else:
                self.send_json(200, {'id': generation, 'object': 'chat.completion', 'model': state['name'],
                    'choices': [{'index': 0, 'message': message, 'finish_reason': finish}], 'usage': usage,
                    'lfm': {**evidence, 'seconds': seconds}})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            value = {'error': {'message': f'{type(error).__name__}: {error}'}}
            if streaming:
                self.send_event(value)
            else:
                self.send_json(400 if isinstance(error, ValueError) else 500, value)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', choices=PROFILES, required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8850)
    parser.add_argument('--context', type=int)
    args = parser.parse_args()
    spec = json.loads((ROOT / 'checkpoint.json').read_text(encoding='utf-8'))
    if not args.checkpoint.is_file():
        raise FileNotFoundError('Install LFM from the Models tab before starting this profile')
    if args.checkpoint.stat().st_size != spec['bytes']:
        raise ValueError('Checkpoint size differs from the pinned Q8 MAX file')
    print('[lfm] Verifying pinned Q8 checkpoint', flush=True)
    with args.checkpoint.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != spec['sha256']:
            raise ValueError('Checkpoint SHA256 differs from the pinned file')
    name, kind, echo, context = PROFILES[args.profile]
    context = args.context or context
    if context < 512 or context > spec['published_context_tokens']:
        raise ValueError('Active context must be between 512 and the published 131072-token limit')
    children = []
    engine = None
    try:
        if kind == 'fusion':
            engine = FusionCoreModel(args.checkpoint, args.runtime, context, recompute=echo)
        else:
            native = FusionCoreModel(args.checkpoint, args.runtime, context, recompute=True) if echo else None
            engine = DualCoreEngine(args.checkpoint, args.port, native=native)
            for brain in [] if native else engine.brains:
                child = launch_llama_server(args.runtime / 'llama-server.exe', brain,
                                           context=context, gpu_layers=99, embeddings=False, gpu_kv=True,
                                           jinja=True, chat_template='chatml')
                children.append(child)
                wait_healthy(brain, child)
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
        server.lock = threading.Lock()
        server.state = {'name': name, 'kind': kind, 'context': context, 'engine': engine,
            'evidence': {'profile': args.profile, 'checkpoint': spec, 'complete_towers': 2,
                'parameters': engine.parameters,
                'execution_mode': 'single_coupled_token_stream' if kind == 'fusion' else 'cooperating_candidate_brains',
                'cache_mode': 'recompute_each_token' if echo else 'KV',
                'memory_note': 'ECHO archive retrieval has finite active inference; recomputation still needs transient attention buffers.',
                'quality': 'experimental; no benchmark improvement claimed'}}
        print(f'[lfm] {name} ready on http://127.0.0.1:{args.port}', flush=True)
        server.serve_forever()
    finally:
        if engine is not None:
            engine.close()
        for child in children:
            stop_llama_server(child)


if __name__ == '__main__':
    main()
