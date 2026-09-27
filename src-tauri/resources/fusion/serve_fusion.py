"""Local, text-only HTTP server for the experimental full-weight TwinCore model.

Both complete Q6 checkpoints are loaded once. A trained adapter and a bound
full-model resource qualification are required; there is no NF4 fallback.
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import select
import socket
import threading
import time
import uuid


DEFAULT_CONTEXT_TOKENS = 1024
MAX_BODY_BYTES = 2_000_000


from .native_engine import TwinCoreEngine
from .tool_protocol import OutputStream, prepare_messages, selected_message, validate_tools


class FusionHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, engine):
        super().__init__(address, handler)
        self.engine = engine
        self.generation_lock = threading.Lock()
        self.active_guard = threading.Lock()
        self.active_cancel = None

    def cancel_active(self, expected=None):
        with self.active_guard:
            event = self.active_cancel
            if event is None or expected is not None and expected is not event:
                return False
            event.set()
            if hasattr(self.engine, 'cancel'):
                self.engine.cancel()
            return True

    def server_close(self):
        self.cancel_active()
        super().server_close()
        if hasattr(self.engine, 'close'):
            self.engine.close()


class FusionHandler(BaseHTTPRequestHandler):
    server_version = "OpenCoreTwinCore/0.1"

    def log_message(self, format, *args):
        print("[twincore] " + format % args, flush=True)

    def _json(self, status: int, value: dict):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str):
        self._json(status, {"error": {"message": message, "type": "invalid_request_error"}})

    def _read_json(self) -> dict:
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Invalid Content-Length") from error
        if not 0 < size <= MAX_BODY_BYTES:
            raise ValueError("JSON body is empty or exceeds 2 MB")
        value = json.loads(self.rfile.read(size))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def do_GET(self):
        engine = self.server.engine
        if self.path == "/health":
            ready = not getattr(engine, 'closed', False)
            self._json(200 if ready else 503, {"status": "ok" if ready else "failed", "ready": ready, "model": engine.model_id})
        elif self.path == "/props":
            self._json(200, {"n_ctx": engine.context_tokens,
                             "model": engine.model_id, "experimental": True,
                             "configuration": getattr(engine, 'configuration', None)})
        elif self.path in ("/v1/models", "/models"):
            self._json(200, {"object": "list", "data": [{"id": engine.model_id,
                          "object": "model", "owned_by": "opencore"}]})
        else:
            self._error(404, "Unknown endpoint")

    def do_POST(self):
        owns_generation, monitor, finished = False, None, threading.Event()
        try:
            payload = self._read_json()
            if self.path == '/cancel':
                self._json(200, {'cancel_requested': self.server.cancel_active()})
                return
            if self.path == "/tokenize":
                content = payload.get("content")
                if not isinstance(content, str):
                    raise ValueError("content must be text")
                self._json(200, {"tokens": self.server.engine.tokenize(content)})
                return
            if self.path not in ("/v1/chat/completions", "/chat/completions"):
                self._error(404, "Unknown endpoint")
                return
            messages = payload.get("messages")
            if not isinstance(messages, list) or not messages:
                raise ValueError("messages must be a nonempty list")
            tools = payload.get("tools") or []
            if any(not isinstance(message, dict) for message in messages):
                raise ValueError('Chat messages must be objects')
            prepare_messages(messages, tools)
            max_tokens = payload.get("max_tokens", 128)
            if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
                raise ValueError("max_tokens must be a positive integer")
            if payload.get('temperature', 0) != 0:
                raise ValueError('This experimental TwinCore decoder currently supports temperature=0 only')
            if not self.server.generation_lock.acquire(blocking=False):
                self._error(409, "TwinCore is generating another response")
                return
            owns_generation = True
            event = threading.Event()
            with self.server.active_guard:
                self.server.active_cancel = event
                if hasattr(self.server.engine, 'begin_request'):
                    self.server.engine.begin_request(event)
            prepared, prompt_tokens = self.server.engine.prepare(messages, tools, max_tokens)
            monitor = threading.Thread(target=self._monitor_disconnect, args=(event, finished), daemon=True)
            monitor.start()
            if payload.get("stream"):
                self._stream(prepared, prompt_tokens, max_tokens, tools)
            else:
                self._complete(prepared, prompt_tokens, max_tokens, tools)
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self._error(400, str(error))
        except OSError:
            if owns_generation:
                self.server.cancel_active()
        except Exception as error:
            self._error(500, f'TwinCore request failed: {type(error).__name__}: {error}')
        finally:
            finished.set()
            if monitor:
                monitor.join(timeout=1)
            if owns_generation:
                with self.server.active_guard:
                    self.server.active_cancel = None
                self.server.generation_lock.release()

    def _monitor_disconnect(self, event, finished):
        while not finished.is_set():
            try:
                ready, _, _ = select.select([self.connection], [], [], 0.2)
                if ready and not self.connection.recv(1, socket.MSG_PEEK):
                    self.server.cancel_active(expected=event)
                    return
            except OSError:
                if not finished.is_set():
                    self.server.cancel_active(expected=event)
                return

    def _chunks(self, prepared, prompt_tokens, max_tokens, tools):
        generation_id = "chatcmpl-twincore-" + uuid.uuid4().hex
        created = int(time.time())
        start = time.perf_counter()
        count = 0
        decoder = OutputStream()
        finish = None
        def chunk(delta):
            return {'id': generation_id, 'object': 'chat.completion.chunk', 'created': created,
                    'model': self.server.engine.model_id,
                    'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]}
        stream = self.server.engine.generate(prepared, max_tokens)
        try:
            for event in stream:
                if isinstance(event, str):
                    # Legacy HTTP-only test engines expose one string per token.
                    count += 1
                    piece = event
                else:
                    if event.completion_tokens < count or event.completion_tokens > max_tokens:
                        raise RuntimeError('Invalid native completion token count')
                    count, piece, finish = event.completion_tokens, event.text, event.finish_reason
                for delta in decoder.feed(piece):
                    yield chunk(delta)
            for delta in decoder.feed('', final=True):
                yield chunk(delta)
            message = selected_message(decoder, tools)
            if message.get('tool_calls'):
                yield chunk({'tool_calls': [dict(call, index=index) for index, call in enumerate(message['tool_calls'])]})
                finish = 'tool_calls'
            elapsed = max(time.perf_counter() - start, 0.001)
            yield {"id": generation_id, "object": "chat.completion.chunk",
                   "created": created, "model": self.server.engine.model_id,
                   "choices": [{"index": 0, "delta": {},
                                "finish_reason": finish or ("length" if count >= max_tokens else "stop")}],
                   "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": count,
                             "total_tokens": prompt_tokens + count},
                   "timings": {"predicted_per_second": round(count / elapsed, 3)}, '_message': message}
        finally:
            if hasattr(stream, 'close'):
                stream.close()

    def _stream(self, prepared, prompt_tokens, max_tokens, tools):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        chunks = self._chunks(prepared, prompt_tokens, max_tokens, tools)
        try:
            for chunk in chunks:
                chunk.pop('_message', None)
                self.wfile.write(b"data: " + json.dumps(chunk, ensure_ascii=False).encode() + b"\n\n")
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except Exception as error:
            self.server.cancel_active()
            try:
                self.wfile.write(b"data: " + json.dumps(
                    {"error": {"message": f"{type(error).__name__}: {error}"}}
                ).encode() + b"\n\n")
                self.wfile.flush()
            except OSError:
                pass
        finally:
            chunks.close()

    def _complete(self, prepared, prompt_tokens, max_tokens, tools):
        try:
            chunks = list(self._chunks(prepared, prompt_tokens, max_tokens, tools))
        except Exception as error:
            self._error(500, f"TwinCore generation failed: {type(error).__name__}: {error}")
            return
        final = chunks[-1]
        self._json(200, {"id": final["id"], "object": "chat.completion",
                         "created": final["created"], "model": final["model"],
                         "choices": [{"index": 0,
                                      "message": final['_message'],
                                      "finish_reason": final["choices"][0]["finish_reason"]}],
                         "usage": final["usage"], "timings": final["timings"]})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--nanbeige', type=Path, required=True)
    parser.add_argument('--k2', type=Path, required=True)
    parser.add_argument('--adapter', type=Path, required=True)
    parser.add_argument('--qualification', type=Path, required=True)
    parser.add_argument('--dll', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--rank', type=int, default=256)
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--recompute', action='store_true')
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8850)
    parser.add_argument("--context-tokens", type=int, default=DEFAULT_CONTEXT_TOKENS)
    args = parser.parse_args()
    if args.host != "127.0.0.1":
        parser.error("TwinCore only binds to 127.0.0.1")
    engine = TwinCoreEngine(args.nanbeige, args.k2, args.adapter, args.qualification,
        args.dll, args.runtime, context_tokens=args.context_tokens, rank=args.rank,
        seed=args.seed, recompute=args.recompute)
    server = FusionHttpServer((args.host, args.port), FusionHandler, engine)
    print(f"[twincore] Serving http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
