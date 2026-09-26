from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from twincore.gpu_budget import gpu_startup_budget  # noqa: E402
from twincore.runtime import TwinCoreEngine, launch_llama_server, wait_healthy  # noqa: E402
from twincore.spec import TwinCoreConfig, default_twincore_config  # noqa: E402


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
    parser = argparse.ArgumentParser(description="TwinCore K2 + Nanbeige consensus model server")
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--config", type=Path, help="Consensus config file; defaults to twincore-config.json inside the release package")
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
    config = default_twincore_config() if not config_path.is_file() else TwinCoreConfig.load(config_path)
    engine = TwinCoreEngine(config, args.release)
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
                f"Not enough free VRAM to start both doUcode backbones safely: "
                f"need about {required_mib} MiB; {free_mib} MiB is free. "
                "Close the other GPU model or use fewer GPU layers."
            )
        args.k2_gpu_layers, args.nanbeige_gpu_layers = k2_layers, nb_layers
        print(
            f"[twincore] VRAM fit selected K2 {k2_layers}/{config.k2.num_hidden_layers} and "
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
            raise RuntimeError("Both TwinCore backbone servers must be healthy before serving")
    except Exception:
        for child in children:
            child.terminate()
            child.wait(timeout=10)
        raise

    server = ThreadingHTTPServer((config.host, config.port), TwinCoreHandler)
    server.engine = engine
    server.config = config
    print(f"TwinCore listening on http://{config.host}:{config.port}", flush=True)
    print(f"K2: {engine.k2.base_url} | Nanbeige: {engine.nanbeige.base_url}", flush=True)
    try:
        server.serve_forever()
    finally:
        for child in children:
            child.terminate()
    return 0


class TwinCoreHandler(BaseHTTPRequestHandler):
    """OpenAI-compatible HTTP surface for TwinCore and its context telemetry."""

    server_version = "TwinCoreConsensus/0.1"

    def _json(self, status: int, value: dict):
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt, *args):
        sys.stderr.write("[twincore] " + (fmt % args) + "\n")

    def do_GET(self):
        engine = self.server.engine
        config = self.server.config
        if self.path == "/health":
            self._json(200, {
                "status": "ok",
                "k2": engine.k2.healthy(),
                "nanbeige": engine.nanbeige.healthy(),
                "judge": getattr(engine.judge, "status", {"enabled": False}),
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
            result = engine.chat_completion(payload)
            self._json(200, result)
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            print("[twincore] ERROR " + message, file=sys.stderr, flush=True)
            self._json(500, {"error": {"message": message}})


if __name__ == "__main__":
    raise SystemExit(main())
