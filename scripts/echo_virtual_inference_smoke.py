"""Short, real-model ECHO recall check. Never downloads or changes weights."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src-tauri/resources/echo"))
from echo_server import ArchiveSet
from evoagent.echo_context import LiveTranscript
from evoagent.echo_memory import EchoArchive


def request(url, payload=None, timeout=15):
    body = json.dumps(payload).encode() if payload is not None else None
    with urllib.request.urlopen(urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}), timeout=timeout) as response:
        return json.load(response)


def ready(url, process, deadline=90):
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if process.poll() is not None:
            raise RuntimeError(f"Owned helper exited with {process.returncode}")
        try:
            return request(url)
        except Exception:
            time.sleep(.25)
    raise TimeoutError(url)


def gpu_usage():
    try:
        result = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        return int(result.stdout.strip().splitlines()[0])
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def owned_process_ram(pid):
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [(name, ctypes.c_size_t) for name in
            ("peak_working", "working", "peak_paged", "paged", "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile")]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    info = ctypes.WinDLL("psapi").GetProcessMemoryInfo
    info.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
    handle = kernel.OpenProcess(0x0400 | 0x0010, False, pid)
    if not handle:
        return None
    try:
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        return counters.working if info(handle, ctypes.byref(counters), counters.cb) else None
    finally:
        kernel.CloseHandle(handle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.server.is_file() or not args.model.is_file():
        parser.error("Existing server and model files are required")
    model_hash = hashlib.sha256()
    with args.model.open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
            model_hash.update(block)
    processes = []
    with tempfile.TemporaryDirectory(prefix="opencore-echo-smoke-") as folder:
        path = Path(folder)
        conversation = "verified-old-memory"
        archives = ArchiveSet(path / "memory", 0)
        archive = archives.get(conversation)
        live = LiveTranscript(archive, conversation)
        count = lambda text: len(text.encode())
        live.start_turn("We are working on unrelated menus.", count)
        live.append({"role": "assistant", "content": "The menu work is saved."}, count)
        live.open = False
        live.save()
        cold_path = archive.path.with_name(archive.path.stem + "-cold.db")
        cold = EchoArchive(cold_path)
        key = "blue-garnet-742"
        cold.append(f"User architecture decision: PhysicsController launch key is {key}. Keep this exact key.", conversation, 1)
        for i in range(40):
            cold.append(f"Unrelated menu note {i}: labels and colors only.", conversation, i + 2)
        cold.close()
        archives.close()
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            with (path / "backend.log").open("wb") as backend_log, (path / "echo.log").open("wb") as echo_log:
                command = [str(args.server), "-m", str(args.model), "--host", "127.0.0.1", "--port", "18711", "-ngl", "99", "-c", "8192", "-t", "4", "--no-kv-offload"]
                environment = dict(os.environ)
                if args.model.name == "OpenCore-Code-Single-File.gguf":
                    # Existing app ECHO profile flags, without altering weights.
                    for name, value in {"OPENCORE_BF16_RESIDENT_POOL":"1", "OPENCORE_BF16_EXPERT_GGUF":str(args.model),
                        "OPENCORE_BACKEND_DIR":str(args.server.parent), "OPENCORE_ACTIVE_EXPERTS":"5", "OPENCORE_WORKFLOW_STAGES":"18",
                        "OPENCORE_Q8_STAGE_EXPERTS":"10000", "OPENCORE_STAGE_FILE":str(args.model.parent / "opencore-stage.txt"),
                        "OPENCORE_FUSED_PRIVATE_SHARED":"1", "OPENCORE_CARRIER_GRAPH_INPUTS":"1"}.items():
                        environment[name] = value
                    command.extend(["--flash-attn", "on", "--cache-type-k", "q4_0", "--cache-type-v", "q4_0", "--reasoning", "off"])
                backend = subprocess.Popen(command, env=environment, stdout=backend_log, stderr=subprocess.STDOUT, creationflags=flags)
                processes.append(backend)
                ready("http://127.0.0.1:18711/health", backend)
                props = request("http://127.0.0.1:18711/props")
                echo = subprocess.Popen([sys.executable, str(ROOT / "src-tauri/resources/echo/echo_server.py"), "--upstream", "http://127.0.0.1:18711", "--port", "18713", "--archive", str(path / "memory"), "--no-console", "--reasoning", "off"], stdout=echo_log, stderr=subprocess.STDOUT, creationflags=flags)
                processes.append(echo)
                ready("http://127.0.0.1:18713/echo/stats", echo)
                cases = []
                previous_words = 0
                for history_words in (10000, 32768, 100000, 1000000):
                    cold = EchoArchive(cold_path)
                    cold.append("unrelated menu color label " * ((history_words - previous_words) // 4), conversation)
                    previous_words = history_words
                    cold.close()
                    started = time.perf_counter()
                    result = request("http://127.0.0.1:18713/v1/chat/completions", {
                        "model": "opencore", "conversation_id": conversation,
                        "messages": [{"role": "user", "content": f"Recall our earlier PhysicsController decision. Return just the exact launch key we chose. Verification round {history_words}."}],
                        "max_tokens": 256, "echo_max_total_tokens": 256, "echo_max_calls": 2,
                        "reasoning_effort": "off", "temperature": 0,
                    }, timeout=180)
                    elapsed = time.perf_counter() - started
                    answer = result["choices"][0]["message"]["content"]
                    context = request("http://127.0.0.1:18713/echo/context?conversation=" + conversation)
                    case = {"synthetic_history_words": history_words, "answer": answer, "exact_fact_in_answer": key in answer,
                            "elapsed_seconds": round(elapsed, 3), "usage": result.get("usage"), "timings": result.get("timings"),
                            "total_gpu_used_mib": gpu_usage(), "echo_process_ram_bytes": owned_process_ram(echo.pid),
                            "archive_disk_bytes": request("http://127.0.0.1:18713/echo/stats")["total_bytes"], "context": context}
                    cases.append(case)
                    print(json.dumps({key: case[key] for key in ("synthetic_history_words", "exact_fact_in_answer", "elapsed_seconds", "total_gpu_used_mib")}), flush=True)
                    if key not in answer or not context.get("echoActivePages"):
                        raise AssertionError(f"Cold ECHO memory did not inform generation: {answer}")
                report = {"model_file": str(args.model), "model_sha256": model_hash.hexdigest(), "model_bytes": args.model.stat().st_size,
                          "backend_props": props, "answer": answer, "exact_fact_in_answer": key in answer,
                          "elapsed_seconds": round(elapsed, 3), "usage": result.get("usage"), "timings": result.get("timings"),
                          "command": command, "cases": cases, "context": context,
                          "scope": "real local generation; old fact only in cold archive; original app weight precision unchanged; GPU values are total device usage, not isolated allocator measurements"}
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
                if key not in answer or not context.get("echoActivePages"):
                    raise AssertionError(f"Cold ECHO memory did not inform generation: {answer}")
                print(json.dumps({"exact_fact_in_answer": True, "answer": answer, "elapsed_seconds": report["elapsed_seconds"], "active_pages": context["echoActivePages"]}))
        except Exception:
            for name in ("backend", "echo"):
                log = path / f"{name}.log"
                if log.exists():
                    print(log.read_text(errors="replace")[-6000:], file=sys.stderr)
            raise
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=20)


if __name__ == "__main__":
    main()
