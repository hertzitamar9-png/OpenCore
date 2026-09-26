"""Local, text-only HTTP server for the experimental full-weight TwinCore model.

The two pinned checkpoints are loaded once. Each generated token is decoded
from one coupled forward pass through both native decoder towers. This is a
reference runtime with an untrained bridge, not a coding-quality release.
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time
import uuid


MODEL_ID = "opencore-twincore-experimental"
DEFAULT_CONTEXT_TOKENS = 1024
MAX_BODY_BYTES = 2_000_000


class TwinCoreEngine:
    model_id = MODEL_ID

    def __init__(self, source_root: Path, manifest_path: Path,
                 context_tokens: int = DEFAULT_CONTEXT_TOKENS) -> None:
        if not 1 <= context_tokens <= 8192:
            raise ValueError("Experimental TwinCore context must be 1–8192 tokens")
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        from .alignment import ExactSurfaceAlignment
        from .coupled import CoupledFusion
        from .heads import offload_output_head
        from .manifest import verify_checkpoint

        if not torch.cuda.is_available():
            raise RuntimeError("TwinCore needs a CUDA GPU")
        free_bytes, _ = torch.cuda.mem_get_info()
        if free_bytes < 10_000 * 2**20:
            raise RuntimeError("TwinCore needs at least 10,000 MiB free VRAM before loading")
        torch.cuda.set_per_process_memory_fraction(0.88)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        nanbeige_path = verify_checkpoint(source_root, manifest, "nanbeige4.2-3b")
        k_path = verify_checkpoint(source_root, manifest, "k2-horizon-3.7b")
        print("[twincore] Both full source checkpoints verified", flush=True)

        quantization = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16,
            llm_int8_enable_fp32_cpu_offload=True,
        )
        nanbeige = AutoModelForCausalLM.from_pretrained(
            nanbeige_path, local_files_only=True, trust_remote_code=True,
            quantization_config=quantization, device_map={"": 0},
            low_cpu_mem_usage=True,
        )
        nanbeige.get_input_embeddings().to("cpu")
        nanbeige_head_bytes = offload_output_head(nanbeige)
        torch.cuda.empty_cache()
        print(f"[twincore] Nanbeige input and output embeddings on CPU; output head released "
              f"{nanbeige_head_bytes / 2**20:.0f} MiB VRAM", flush=True)
        k2 = AutoModelForCausalLM.from_pretrained(
            k_path, local_files_only=True, trust_remote_code=True,
            quantization_config=quantization, device_map={"": 0}, low_cpu_mem_usage=True,
        )
        k2.get_input_embeddings().to("cpu")
        k2_head_bytes = offload_output_head(k2)
        torch.cuda.empty_cache()
        print(f"[twincore] K2 input and output embeddings on CPU; output head released "
              f"{k2_head_bytes / 2**20:.0f} MiB VRAM", flush=True)
        self.nanbeige_tokenizer = AutoTokenizer.from_pretrained(
            nanbeige_path, local_files_only=True, trust_remote_code=True,
        )
        self.k_tokenizer = AutoTokenizer.from_pretrained(
            k_path, local_files_only=True, trust_remote_code=True,
        )
        alignment = ExactSurfaceAlignment.from_tokenizers(
            self.nanbeige_tokenizer, self.k_tokenizer,
            nanbeige.get_output_embeddings().weight.shape[0],
            k2.get_output_embeddings().weight.shape[0],
        )
        self.model = CoupledFusion(
            nanbeige, k2, alignment,
            nanbeige_hidden=int(nanbeige.config.hidden_size),
            k2_hidden=int(k2.config.hidden_size), rank=64,
        ).eval()
        self.context_tokens = context_tokens
        print(f"[twincore] Both full towers ready; {alignment.size} aligned pieces; "
              f"{round(torch.cuda.memory_allocated()/2**20)} MiB allocated", flush=True)

    def tokenize(self, text: str) -> list[int]:
        return self.nanbeige_tokenizer.encode(text, add_special_tokens=False)

    def prepare(self, messages: list[dict], tools: list[dict], max_new_tokens: int):
        # Both models see the same conversation through their own chat syntax.
        kwargs = {"tokenize": False, "add_generation_prompt": True}
        if tools:
            kwargs["tools"] = tools
        nanbeige_prompt = self.nanbeige_tokenizer.apply_chat_template(messages, **kwargs)
        k_prompt = self.k_tokenizer.apply_chat_template(messages, **kwargs)
        nanbeige_tokens = len(
            self.nanbeige_tokenizer.encode(nanbeige_prompt, add_special_tokens=False)
        )
        k_tokens = len(self.k_tokenizer.encode(k_prompt, add_special_tokens=False))
        prompt_tokens = max(nanbeige_tokens, k_tokens)
        if prompt_tokens + max_new_tokens > self.context_tokens:
            raise ValueError(
                f"TwinCore active context exceeded: {prompt_tokens} prompt + "
                f"{max_new_tokens} response > {self.context_tokens} tokens. "
                "ECHO archive history is separate from this experimental live window."
            )
        return ((nanbeige_prompt, k_prompt), prompt_tokens)

    def generate(self, prepared, max_new_tokens: int):
        nanbeige_prompt, k_prompt = prepared
        yield from self.model.stream_text(
            nanbeige_prompt, self.nanbeige_tokenizer, self.k_tokenizer,
            k2_prompt=k_prompt, max_new_tokens=max_new_tokens,
        )


class FusionHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, engine):
        super().__init__(address, handler)
        self.engine = engine
        self.generation_lock = threading.Lock()


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
            self._json(200, {"status": "ok", "model": engine.model_id})
        elif self.path == "/props":
            self._json(200, {"n_ctx": engine.context_tokens,
                             "model": engine.model_id, "experimental": True})
        elif self.path in ("/v1/models", "/models"):
            self._json(200, {"object": "list", "data": [{"id": engine.model_id,
                          "object": "model", "owned_by": "opencore"}]})
        else:
            self._error(404, "Unknown endpoint")

    def do_POST(self):
        try:
            payload = self._read_json()
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
            if any(not isinstance(m, dict) or m.get("role") not in
                   ("system", "developer", "user", "assistant", "tool") or
                   not isinstance(m.get("content"), str) for m in messages):
                raise ValueError("Experimental Fusion currently accepts text-only messages")
            tools = payload.get("tools") or []
            if not isinstance(tools, list):
                raise ValueError("tools must be a list")
            max_tokens = payload.get("max_tokens", 128)
            if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
                raise ValueError("max_tokens must be a positive integer")
            prepared, prompt_tokens = self.server.engine.prepare(messages, tools, max_tokens)
            if not self.server.generation_lock.acquire(blocking=False):
                self._error(409, "TwinCore is generating another response")
                return
            if payload.get("stream"):
                self._stream(prepared, prompt_tokens, max_tokens)
            else:
                self._complete(prepared, prompt_tokens, max_tokens)
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self._error(400, str(error))

    def _chunks(self, prepared, prompt_tokens, max_tokens):
        generation_id = "chatcmpl-twincore-" + uuid.uuid4().hex
        created = int(time.time())
        start = time.perf_counter()
        count = 0
        try:
            for piece in self.server.engine.generate(prepared, max_tokens):
                count += 1
                yield {"id": generation_id, "object": "chat.completion.chunk",
                       "created": created, "model": self.server.engine.model_id,
                       "choices": [{"index": 0, "delta": {"content": piece},
                                    "finish_reason": None}]}
            elapsed = max(time.perf_counter() - start, 0.001)
            yield {"id": generation_id, "object": "chat.completion.chunk",
                   "created": created, "model": self.server.engine.model_id,
                   "choices": [{"index": 0, "delta": {},
                                "finish_reason": "length" if count >= max_tokens else "stop"}],
                   "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": count,
                             "total_tokens": prompt_tokens + count},
                   "timings": {"predicted_per_second": round(count / elapsed, 3)}}
        finally:
            self.server.generation_lock.release()

    def _stream(self, prepared, prompt_tokens, max_tokens):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        chunks = self._chunks(prepared, prompt_tokens, max_tokens)
        try:
            for chunk in chunks:
                self.wfile.write(b"data: " + json.dumps(chunk, ensure_ascii=False).encode() + b"\n\n")
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (OSError, RuntimeError, ValueError) as error:
            try:
                self.wfile.write(b"data: " + json.dumps(
                    {"error": {"message": f"{type(error).__name__}: {error}"}}
                ).encode() + b"\n\n")
                self.wfile.flush()
            except OSError:
                pass
        finally:
            chunks.close()

    def _complete(self, prepared, prompt_tokens, max_tokens):
        try:
            chunks = list(self._chunks(prepared, prompt_tokens, max_tokens))
        except Exception as error:
            self._error(500, f"TwinCore generation failed: {type(error).__name__}: {error}")
            return
        final = chunks[-1]
        content = "".join(chunk["choices"][0]["delta"].get("content", "") for chunk in chunks[:-1])
        self._json(200, {"id": final["id"], "object": "chat.completion",
                         "created": final["created"], "model": final["model"],
                         "choices": [{"index": 0,
                                      "message": {"role": "assistant", "content": content},
                                      "finish_reason": final["choices"][0]["finish_reason"]}],
                         "usage": final["usage"], "timings": final["timings"]})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8850)
    parser.add_argument("--context-tokens", type=int, default=DEFAULT_CONTEXT_TOKENS)
    args = parser.parse_args()
    if args.host != "127.0.0.1":
        parser.error("TwinCore only binds to 127.0.0.1")
    engine = TwinCoreEngine(args.source_root, args.manifest, args.context_tokens)
    server = FusionHttpServer((args.host, args.port), FusionHandler, engine)
    print(f"[twincore] Serving http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
