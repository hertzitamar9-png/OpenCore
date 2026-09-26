from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from duocore.gpu_budget import gpu_startup_budget, host_ram_startup_budget  # noqa: E402
from duocore.runtime import DuoCoreEngine, launch_llama_server, stop_llama_server, wait_healthy  # noqa: E402
from duocore.spec import DuoCoreConfig, default_duocore_config  # noqa: E402


def endpoints_healthy(ports: tuple[int, int]) -> tuple[bool, bool]:
    values = []
    for port in ports:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                values.append(response.status == 200)
        except (OSError, urllib.error.URLError):
            values.append(False)
    return values[0], values[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="DuoCore K2 + Nanbeige candidate-selection model server")
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--config", type=Path, help="DuoCore config file; defaults to twincore-config.json inside the release package")
    parser.add_argument(
        "--llama-server",
        type=Path,
        required=True,
    )
    parser.add_argument("--start-backbones", action="store_true")
    parser.add_argument("--k2-gpu-layers", type=lambda value: None if value.lower() == "auto" else int(value), default=None)
    parser.add_argument("--nanbeige-gpu-layers", type=lambda value: None if value.lower() == "auto" else int(value), default=None)
    args = parser.parse_args()

    config_path = args.config or (args.release / "twincore-config.json")
    config = default_duocore_config() if not config_path.is_file() else DuoCoreConfig.load(config_path)
    engine = DuoCoreEngine(config, args.release)
    children = []
    if args.start_backbones and not all(endpoints_healthy((config.k2.port, config.nanbeige.port))):
        required_mib, free_mib, k2_layers, nb_layers = gpu_startup_budget(
            config,
            args.release,
            args.k2_gpu_layers,
            args.nanbeige_gpu_layers,
        )
        if required_mib and free_mib < required_mib:
            raise RuntimeError(
                f"Not enough free VRAM to start both DuoCore backbones safely: "
                f"need about {required_mib} MiB; {free_mib} MiB is free. "
                "Close the other GPU model or use fewer GPU layers."
            )
        args.k2_gpu_layers, args.nanbeige_gpu_layers = k2_layers, nb_layers
        required_ram_mib, free_ram_mib = host_ram_startup_budget(
            config, args.release, k2_layers, nb_layers
        )
        if free_ram_mib < required_ram_mib:
            raise RuntimeError(
                f"Not enough free system RAM to start DuoCore with a {config.live_window_tokens:,}-token context: "
                f"estimated need {required_ram_mib:,} MiB after GPU layer placement, including a 5 GiB desktop reserve; "
                f"{free_ram_mib:,} MiB is currently free. Lower the context or close other applications."
            )
        print(
            f"[duocore] Host RAM preflight: estimated {required_ram_mib} MiB needed; "
            f"{free_ram_mib} MiB available after GPU placement; retaining a 5 GiB desktop reserve",
            flush=True,
        )
        print(
            f"[duocore] VRAM fit selected K2 {k2_layers}/{config.k2.num_hidden_layers} and "
            f"Nanbeige {nb_layers}/{config.nanbeige.num_hidden_layers} GPU layers "
            f"({required_mib} MiB budget; {free_mib} MiB free)",
            flush=True,
        )
    try:
        if args.start_backbones:
            for backbone, layers in ((engine.k2, args.k2_gpu_layers), (engine.nanbeige, args.nanbeige_gpu_layers)):
                if not backbone.model_path.is_file():
                    raise FileNotFoundError(backbone.model_path)
                if not backbone.healthy():
                    child = launch_llama_server(args.llama_server, backbone, context=config.live_window_tokens, gpu_layers=layers)
                    children.append(child)
                    wait_healthy(backbone, child)

        if not engine.k2.healthy() or not engine.nanbeige.healthy():
            raise RuntimeError("Both DuoCore backbone servers must be healthy before serving")
    except Exception:
        for child in children:
            stop_llama_server(child)
        raise

    server = ThreadingHTTPServer((config.host, config.port), DuoCoreHandler)
    server.engine = engine
    server.config = config
    print(f"DuoCore listening on http://{config.host}:{config.port}", flush=True)
    print(f"K2: {engine.k2.base_url} | Nanbeige: {engine.nanbeige.base_url}", flush=True)
    try:
        server.serve_forever()
    finally:
        for child in children:
            stop_llama_server(child)
    return 0


class DuoCoreHandler(BaseHTTPRequestHandler):
    """OpenAI-compatible HTTP surface for DuoCore and its context telemetry."""

    server_version = "DuoCoreSelection/0.2"

    def _json(self, status: int, value: dict):
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt, *args):
        sys.stderr.write("[duocore] " + (fmt % args) + "\n")

    def _write_sse(self, value: dict):
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.wfile.write(b"data: " + raw + b"\n\n")
        self.wfile.flush()

    def _stream_completion(self, engine, payload: dict):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        generation = uuid.uuid4().hex
        try:
            result = engine.chat_completion(
                payload,
                on_preview=lambda delta: self._write_sse({"echo_preview": {
                    "generation": generation,
                    "phase": "drafting",
                    "delta": delta,
                }}),
            )
            choice = result["choices"][0]
            chunk = {
                "id": result.get("id", f"duocore-{generation}"),
                "object": "chat.completion.chunk",
                "created": result.get("created"),
                "model": result.get("model"),
                "choices": [{
                    "index": 0,
                    "delta": choice.get("message") or {},
                    "finish_reason": choice.get("finish_reason") or "stop",
                }],
            }
            if result.get("usage") is not None:
                chunk["usage"] = result["usage"]
            self._write_sse(chunk)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except Exception as error:
            try:
                self._write_sse({"error": {"message": f"{type(error).__name__}: {error}"}})
            except OSError:
                pass

    def do_GET(self):
        engine = self.server.engine
        config = self.server.config
        if self.path == "/health":
            k2_healthy = engine.k2.healthy()
            nanbeige_healthy = engine.nanbeige.healthy()
            ready = k2_healthy and nanbeige_healthy
            self._json(200 if ready else 503, {
                "status": "ok" if ready else "unavailable",
                "ready": ready,
                "k2": k2_healthy,
                "nanbeige": nanbeige_healthy,
                "selection": {
                    "strategy": "pairwise_candidate_selection",
                    "models": ["K2", "Nanbeige"],
                    "third_party_judge": False,
                    "latent_bridge": False,
                },
            })
            return
        if self.path.startswith("/v1/models"):
            self._json(200, {"object": "list", "data": [{"id": config.model_id, "object": "model"}]})
            return
        if self.path == "/props":
            self._json(200, {
                "model": config.model_id,
                "n_ctx": config.live_window_tokens,
                "default_generation_settings": {"n_ctx": config.live_window_tokens},
            })
            return
        self._json(404, {"error": {"message": "not found"}})

    def do_POST(self):
        engine = self.server.engine
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw_body = self.rfile.read(length) or b"{}"
            payload = json.loads(raw_body)
            if self.path == "/tokenize":
                replies = []
                for backbone in (engine.k2, engine.nanbeige):
                    req = urllib.request.Request(
                        backbone.base_url + "/tokenize",
                        data=raw_body,
                        headers={"Content-Type": "application/json"},
                    )
                    with urllib.request.urlopen(req, timeout=60) as response:
                        replies.append(json.load(response))
                chosen = max(replies, key=lambda item: len(item.get("tokens") or []))
                self._json(200, chosen)
                return
            if self.path == "/detokenize":
                req = urllib.request.Request(
                    engine.k2.base_url + "/detokenize",
                    data=raw_body,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=60) as response:
                    self._json(200, json.load(response))
                return
            if self.path not in ("/v1/chat/completions", "/chat/completions"):
                self._json(404, {"error": {"message": "not found"}})
                return
            if payload.get("stream") is True:
                self._stream_completion(engine, payload)
                return
            result = engine.chat_completion(payload)
            self._json(200, result)
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            print("[duocore] ERROR " + message, file=sys.stderr, flush=True)
            self._json(500, {"error": {"message": message}})


if __name__ == "__main__":
    raise SystemExit(main())
