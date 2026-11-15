"""OpenCore YuE2 Studio server.

Written by OpenCore (Apache-2.0) for the YuE2 inference package published by
multimodal-art-projection/YuE. OpenCore's installer places this file at
<YuE folder>/studio/server.py. OpenCore's music_host.py loads it, wraps the
job hooks below and serves the Music Studio page.

The YuE2 model weights are licensed separately under CC BY-NC 4.0.
"""
# opencore-yue2-studio-server
from __future__ import annotations

import argparse
import dataclasses
import gc
import json
import os
import re
import secrets
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

SERVER_MARKER = "opencore-yue2-studio-server"
SERVER_VERSION = 1
ROOT = Path(os.environ.get("OPENCORE_MUSIC_HOME") or Path(__file__).resolve().parents[1])
OUTPUT = ROOT / "studio-output"
APP_PREFS = OUTPUT / "_app.json"
MODEL_NAME, VAE_NAME = "YuE2-3B", "YuE2-Vae"
SAMPLING_FIELDS = {"temperature": float, "top_p": float, "top_k": int, "repetition_penalty": float,
                   "penalty_window": int, "min_tokens": int, "max_tokens": int}
SERVED_SUFFIXES = {".flac": "audio/flac", ".wav": "audio/wav", ".json": "application/json", ".abc": "text/plain; charset=utf-8",
                   ".txt": "text/plain; charset=utf-8"}
BODY_LIMIT = 1024 * 1024
HTTPD = None


class StudioHTTPServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR would let a second server share the port silently.
    allow_reuse_address = False
    daemon_threads = True
_PIPE, _PIPE_KEY = None, None
_PIPE_LOCK = threading.RLock()
_HARDWARE = None


class Job:
    """Minimal job state; OpenCore's music host replaces it with ManagedJob."""

    def __init__(self):
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self._state = {"status": "idle", "worker_active": False}

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self._state))

    def begin(self, **values):
        with self.lock:
            if self._state.get("worker_active"):
                raise RuntimeError("The previous worker is still releasing resources")
            self.cancel.clear()
            self._state = dict(values, status="running", worker_active=True)

    def update(self, **values):
        with self.lock:
            self._state.update(values)
            if self.cancel.is_set():
                self._state.update(status="cancelled", stage="Cancelled", error=None)

    def tick(self):
        with self.lock:
            if not self.cancel.is_set():
                self._state["done"] = self._state.get("done", 0) + 1

    def request_cancel(self, expected_run=None):
        with self.lock:
            if self._state.get("worker_active"):
                self.cancel.set()
                self._state.update(status="cancelled", stage="Cancelled", error=None)
            return self.snapshot()

    def finish(self, cleanup_error=None):
        with self.lock:
            self._state.update(worker_active=False, worker_finished=True)


JOB = Job()


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def models_dir():
    configured = os.environ.get("YUE2_MODELS")
    return Path(configured) if configured else ROOT / "models"


def weights_present():
    base = models_dir()
    return all((base / name / "weights_manifest.json").is_file() for name in (MODEL_NAME, VAE_NAME))


def hardware():
    """Pick the device YuE2 can actually use here; CUDA requires BF16 support."""
    global _HARDWARE
    if _HARDWARE is not None:
        return _HARDWARE
    import torch
    value = {"device": "cpu", "gpu": None, "vram_gib": None, "note": None}
    try:
        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            value.update(gpu=props.name, vram_gib=round(props.total_memory / 2**30, 1))
            if torch.cuda.is_bf16_supported():
                value["device"] = "cuda"
            else:
                value["note"] = "This NVIDIA GPU has no BF16 support, so YuE2 runs on the CPU."
        elif hasattr(torch, "xpu") and torch.xpu.is_available():
            props = torch.xpu.get_device_properties(0)
            value.update(device="xpu", gpu=props.name, vram_gib=round(props.total_memory / 2**30, 1))
    except Exception as error:  # A broken driver must not stop the studio page.
        value["note"] = f"GPU detection failed: {error}"
    _HARDWARE = value
    return value


def defaults():
    from yue2.protocol import GenerationConfig
    config = GenerationConfig()
    return {"abc": dataclasses.asdict(config.abc), "semantic": dataclasses.asdict(config.semantic),
            "ode_steps": config.ode_steps}


def app_prefs():
    return read_json(APP_PREFS) or {}


def info():
    found = hardware()
    return {"server": SERVER_MARKER, "server_version": SERVER_VERSION, "defaults": defaults(),
            "model_loaded": _PIPE is not None, "output": str(OUTPUT), "models": str(models_dir()),
            "weights_present": weights_present(), "tokens_per_second": app_prefs().get("tokens_per_second"),
            "device": found["device"], "gpu": found["gpu"], "vram_gib": found["vram_gib"], "hardware_note": found["note"]}


def memory_settings(memory):
    memory = memory if isinstance(memory, dict) else {}
    frames = memory.get("vae_core_frames")
    frames = int(frames) if isinstance(frames, (int, float)) and not isinstance(frames, bool) and frames >= 64 else None
    return {"quantization": "fp8" if memory.get("quantization") == "fp8" else "none",
            "offload_ar": memory.get("offload_ar") is True, "vae_core_frames": frames,
            "spill_to_ram": memory.get("spill_to_ram") is True}


def unload_model():
    global _PIPE, _PIPE_KEY
    with _PIPE_LOCK:
        pipe, _PIPE, _PIPE_KEY = _PIPE, None, None
        if pipe is not None:
            try:
                pipe.close()
            finally:
                del pipe
                gc.collect()
                if "torch" in sys.modules:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                    elif hasattr(torch, "xpu") and torch.xpu.is_available():
                        torch.xpu.empty_cache()


def get_pipe(memory):
    global _PIPE, _PIPE_KEY
    settings = memory_settings(memory)
    key = json.dumps(settings, sort_keys=True)
    with _PIPE_LOCK:
        if _PIPE is not None and _PIPE_KEY == key:
            return _PIPE
        unload_model()
        base = models_dir()
        if not weights_present():
            raise FileNotFoundError("The YuE2 model is not downloaded yet. Download it from Models in OpenCore, then try again.")
        from yue2.pipeline import YuE2Pipeline
        found = hardware()
        device = found["device"]
        vram = found["vram_gib"] or 0
        budget = max(vram, 4) if device in ("cuda", "xpu") else 24
        JOB.update(stage="Verifying and loading the YuE2 model", done=0, total=0, unit="")
        _PIPE = YuE2Pipeline(base / MODEL_NAME, base / VAE_NAME, device=device, memory_budget_gib=budget,
                             backend="torch" if device == "cuda" else "torch-eager",
                             vae_core_frames=settings["vae_core_frames"], quantization=settings["quantization"],
                             offload_ar=settings["offload_ar"],
                             low_vram=device != "cpu" and (vram < 20 or settings["spill_to_ram"]), progress=False)
        _PIPE_KEY = key
        return _PIPE


def load_model(memory):
    get_pipe(memory)
    JOB.update(status="done", stage="Model loaded")


def _number(value, kind, name, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    if kind is int:
        if float(value) != int(value):
            raise ValueError(f"{name} must be a whole number")
        value = int(value)
    else:
        value = float(value)
    if (low is not None and value < low) or (high is not None and value > high):
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def _sampling(value, name):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return {key: _number(value[key], kind, f"{name}.{key}") for key, kind in SAMPLING_FIELDS.items() if value.get(key) is not None}


def validate(data):
    if not isinstance(data, dict):
        raise ValueError("The request must be a JSON object")
    mode = data.get("mode", "song")
    if mode not in ("song", "plan"):
        raise ValueError("mode must be song or plan")
    style, lyrics = data.get("style"), data.get("lyrics")
    if not isinstance(style, str) or not style.strip():
        raise ValueError("Describe the style before generating")
    if not isinstance(lyrics, str) or not lyrics.strip():
        raise ValueError("Add lyrics, or choose Instrumental")
    cot = data.get("cot", "full")
    if cot not in ("full", "melody", "off"):
        raise ValueError("cot must be full, melody or off")
    abc = data.get("abc") or None
    if abc is not None and (not isinstance(abc, str) or cot == "off" or mode != "song"):
        raise ValueError("A provided score needs song mode with full or melody planning")
    seed = data.get("seed", 831001)
    seed = secrets.randbelow(2**31) if data.get("random_seed") is True or seed is None else _number(seed, int, "seed", 0, 2**62)
    cfg = data.get("cfg_scale")
    return {"mode": mode, "title": str(data.get("title") or "").strip()[:120], "style": style, "lyrics": lyrics, "cot": cot,
            "instrumental": data.get("instrumental") is True, "abc": abc, "seed": seed,
            "random_seed": data.get("random_seed") is True,
            "takes": _number(data.get("takes", 1), int, "takes", 1, 8),
            "cfg_scale": None if cfg is None else _number(cfg, float, "cfg_scale", 0, 20),
            "ode_steps": None if data.get("ode_steps") is None else _number(data["ode_steps"], int, "ode_steps", 1, 256),
            "abc_sampling": _sampling(data.get("abc_sampling"), "abc_sampling"),
            "semantic_sampling": _sampling(data.get("semantic_sampling"), "semantic_sampling"),
            "memory": memory_settings(data.get("memory")), "ui": data.get("ui") if isinstance(data.get("ui"), dict) else {}}


def new_run(title):
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower()[:40] or "song"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for index in range(1000):
        run = f"{stamp}-{slug}" + (f"-{index + 1}" if index else "")
        directory = OUTPUT / run
        try:
            directory.mkdir(parents=True)
            return run, directory
        except FileExistsError:
            continue
    raise RuntimeError("Could not create a generation folder")


def run_job(request, directory):
    manifest_path = directory / "studio.json"
    manifest = read_json(manifest_path) or {}
    try:
        _run_takes(request, directory, manifest, manifest_path)
    except BaseException as error:
        manifest.update(status="cancelled" if JOB.cancel.is_set() else "error")
        if not JOB.cancel.is_set():
            manifest["error"] = f"{type(error).__name__}: {error}"
        write_json(manifest_path, manifest)
        raise


def _run_takes(request, directory, manifest, manifest_path):
    from yue2.protocol import GenerationConfig
    JOB.update(stage="Loading the YuE2 model")
    pipe = get_pipe(request["memory"])
    base = GenerationConfig()
    pipe.generation_config = dataclasses.replace(base, ode_steps=request["ode_steps"] or base.ode_steps)
    limits = {"abc": request["abc_sampling"].get("max_tokens", base.abc.max_tokens),
              "semantic": request["semantic_sampling"].get("max_tokens", base.semantic.max_tokens)}
    results = []
    for index in range(request["takes"]):
        if JOB.cancel.is_set():
            raise InterruptedError("Cancelled")
        seed = (request["seed"] + index) % 2**62
        phase_seen = {"value": None}

        def on_token(phase, _token):
            phase = "abc" if str(phase).startswith("abc") else "semantic"
            if phase != phase_seen["value"]:
                phase_seen["value"] = phase
                JOB.update(stage="Planning the score" if phase == "abc" else "Generating the song",
                           done=0, total=limits[phase], unit="tokens")
            JOB.tick()

        def on_progress(stage, done, total):
            JOB.update(stage="Rendering audio" if stage == "nar" else "Decoding audio", done=done, total=total, unit="steps")

        JOB.update(take=index + 1, stage="Starting take", done=0, total=0)
        options = dict(abc_sampling=request["abc_sampling"] or None, cancelled=JOB.cancel.is_set, on_token=on_token,
                       cot=request["cot"], seed=seed, id=f"take{index + 1}")
        take_dir = directory / f"take{index + 1}-{seed}"
        if request["mode"] == "plan":
            plan = pipe.plan(request["style"], request["lyrics"], **options)
            if JOB.cancel.is_set():
                raise InterruptedError("Cancelled")
            plan.save(take_dir)
            entry = {"dir": take_dir.name, "seed": seed, "abc": plan.abc, "truncated": {"abc": bool(plan.truncated)}}
        else:
            result = pipe(request["style"], request["lyrics"], semantic_sampling=request["semantic_sampling"] or None,
                          on_progress=on_progress, abc=request["abc"], cfg_scale=request["cfg_scale"], **options)
            if JOB.cancel.is_set():
                raise InterruptedError("Cancelled")
            JOB.update(stage="Saving audio", done=0, total=0, unit="")
            result.save_artifacts(take_dir)
            seconds = len(result.audio) / result.sample_rate
            entry = {"dir": take_dir.name, "seed": seed, "seconds": round(seconds, 2), "abc": result.abc,
                     "truncated": {key: bool(value) for key, value in result.truncated.items()}}
            if seconds > 1:
                prefs = app_prefs()
                prefs["tokens_per_second"] = round(len(result.semantic.tokens) / seconds, 3)
                write_json(APP_PREFS, prefs)
        results.append(entry)
        manifest.setdefault("takes", []).append(entry)
        write_json(manifest_path, manifest)
        JOB.update(results=list(results))
    manifest.update(status="done")
    write_json(manifest_path, manifest)
    JOB.update(status="done", stage="Finished", results=results)


def _standalone(worker):
    """Without OpenCore's host, record errors and release the job ourselves."""
    if not isinstance(JOB, Job):
        return worker

    def run(*args):
        try:
            worker(*args)
        except Exception as error:
            if not JOB.cancel.is_set():
                JOB.update(status="error", stage="Generation failed", error=f"{type(error).__name__}: {error}")
        finally:
            try:
                if args and isinstance(args[-1], Path):
                    unload_model()
            finally:
                JOB.finish()
    return run


def history():
    runs = []
    if not OUTPUT.is_dir():
        return runs
    for directory in OUTPUT.iterdir():
        if directory.name.startswith(("_", ".")) or directory.is_symlink() or not directory.is_dir():
            continue
        manifest = read_json(directory / "studio.json")
        if isinstance(manifest, dict) and manifest.get("run") == directory.name:
            runs.append(manifest)
    runs.sort(key=lambda run: str(run.get("created", "")), reverse=True)
    return runs[:200]


def _run_dir(run):
    if not isinstance(run, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,179}", run) or ".." in run:
        raise ValueError("Unknown generation")
    directory = OUTPUT / run
    if directory.is_symlink() or not (directory / "studio.json").is_file():
        raise ValueError("Unknown generation")
    return directory


def _downloads():
    configured = os.environ.get("YUE2_DOWNLOADS")
    return Path(configured) if configured else Path.home() / "Downloads"


class Handler(BaseHTTPRequestHandler):
    server_version = "OpenCoreYuE2Studio/1"

    def log_message(self, format, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path):
        path = Path(path)
        if not path.is_file():
            return self.send_json({"error": "File not found"}, 404)
        kind = "text/html; charset=utf-8" if path.suffix.lower() == ".html" else SERVED_SUFFIXES.get(path.suffix.lower(), "application/octet-stream")
        size = path.stat().st_size
        start, end = 0, size - 1
        ranged = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
        if ranged and size:
            first, last = ranged.groups()
            if first:
                start, end = int(first), min(int(last), size - 1) if last else size - 1
            elif last:
                start = max(0, size - int(last))
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
        self.send_response(206 if ranged and size else 200)
        self.send_header("Content-Type", kind)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1 if size else 0))
        if ranged and size:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as stream:
            stream.seek(start)
            remaining = end - start + 1 if size else 0
            while remaining > 0:
                block = stream.read(min(remaining, 256 * 1024))
                if not block:
                    break
                self.wfile.write(block)
                remaining -= len(block)

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 <= length <= BODY_LIMIT:
            raise ValueError("The request is too large")
        data = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("The request must be a JSON object")
        return data

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/info":
                return self.send_json(info())
            if path == "/api/status":
                return self.send_json(JOB.snapshot())
            if path == "/api/history":
                return self.send_json(history())
            if path.startswith("/files/"):
                parts = [unquote(part) for part in path[len("/files/"):].split("/")]
                if len(parts) != 3 or any(part in ("", ".", "..") or "\\" in part or ":" in part for part in parts):
                    return self.send_json({"error": "File not found"}, 404)
                directory = _run_dir(parts[0])
                target = directory / parts[1] / parts[2]
                if target.suffix.lower() not in SERVED_SUFFIXES or target.is_symlink() or target.parent.is_symlink():
                    return self.send_json({"error": "File not found"}, 404)
                return self.send_file(target)
            return self.send_json({"error": "Not found"}, 404)
        except ValueError as error:
            return self.send_json({"error": str(error)}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            data = self.read_body()
        except (ValueError, TypeError) as error:
            return self.send_json({"error": str(error)}, 400)
        try:
            if path == "/api/generate":
                request = validate(data)
                with JOB.lock:
                    if JOB.snapshot().get("worker_active"):
                        return self.send_json({"error": "Music Studio is already generating."}, 409)
                    run, directory = new_run(request["title"] or request["style"][:40])
                    write_json(directory / "studio.json", {"run": run, "mode": request["mode"], "status": "running",
                               "created": time.strftime("%Y-%m-%d %H:%M:%S"), "settings": request, "takes": []})
                    JOB.begin(run=run, mode=request["mode"], stage="Starting", done=0, total=0, unit="tokens",
                              take=0, takes=request["takes"], started=time.time(), results=[], error=None)
                    threading.Thread(target=_standalone(run_job), args=(request, directory), daemon=True, name="yue2-generate").start()
                return self.send_json({"run": run})
            if path == "/api/model/load":
                memory = memory_settings(data.get("memory"))
                with JOB.lock:
                    JOB.begin(run=None, mode="load", stage="Loading the YuE2 model", done=0, total=0, started=time.time(), results=[])
                    threading.Thread(target=_standalone(load_model), args=(memory,), daemon=True, name="yue2-load").start()
                return self.send_json({"ok": True})
            if path == "/api/model/unload":
                if JOB.snapshot().get("worker_active"):
                    return self.send_json({"error": "Music Studio is still working."}, 409)
                unload_model()
                return self.send_json({"ok": True})
            if path == "/api/cancel":
                return self.send_json(dict(ok=True, **JOB.request_cancel(data.get("expectedRun"))))
            if path == "/api/download":
                directory = _run_dir(data.get("run"))
                take = data.get("take")
                if not isinstance(take, str) or not re.fullmatch(r"take\d+-\d+", take):
                    raise ValueError("Unknown take")
                source = directory / take / "audio.flac"
                if not source.is_file() or source.is_symlink():
                    raise ValueError("This take has no audio file")
                title = (read_json(directory / "studio.json") or {}).get("settings", {}).get("title") or directory.name
                name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", f"{title} - {take.split('-')[0]}").strip(" .") or "song"
                folder = _downloads()
                folder.mkdir(parents=True, exist_ok=True)
                target = folder / f"{name}.flac"
                counter = 2
                while target.exists():
                    target, counter = folder / f"{name} ({counter}).flac", counter + 1
                shutil.copyfile(source, target)
                return self.send_json({"ok": True, "path": str(target)})
            if path == "/api/open":
                directory = _run_dir(data.get("run"))
                if os.name == "nt":
                    os.startfile(directory)  # noqa: S606 - a folder inside this studio's output
                return self.send_json({"ok": True})
            if path == "/api/shutdown":
                JOB.request_cancel()
                self.send_json({"ok": True})
                threading.Thread(target=HTTPD.shutdown, daemon=True).start()
                return None
            return self.send_json({"error": "Not found"}, 404)
        except (ValueError, TypeError) as error:
            return self.send_json({"error": str(error)}, 400)
        except RuntimeError as error:
            return self.send_json({"error": str(error)}, 409)


def main(argv=None):
    global HTTPD
    parser = argparse.ArgumentParser(description="OpenCore YuE2 Studio server")
    parser.add_argument("--no-browser", action="store_true", help="accepted for compatibility; no browser is opened")
    parser.add_argument("--port", type=int, default=7860)
    options = parser.parse_args(argv)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    HTTPD = StudioHTTPServer(("127.0.0.1", options.port), Handler)
    print(f"YuE2 Studio listening on http://127.0.0.1:{options.port}", flush=True)
    try:
        HTTPD.serve_forever()
    finally:
        try:
            unload_model()
        finally:
            HTTPD.server_close()


if __name__ == "__main__":
    main()
