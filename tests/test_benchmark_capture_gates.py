"""Regression tests for benchmark route and throughput qualification gates."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "evaluation" / "benchmark_capture.py"
SPEC = importlib.util.spec_from_file_location("benchmark_capture_under_test", SCRIPT)
assert SPEC and SPEC.loader
benchmark_capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark_capture)


@pytest.mark.parametrize(
    "profile",
    ["echo", "native1m", "unsloth-echo", "doucode", "dualcore-echo", "fusioncore-echo"],
)
def test_echo_profiles_require_persistent_echo_route(monkeypatch, profile):
    calls = []

    def request(url, *_args, **_kwargs):
        calls.append(url)
        if url.endswith("/echo/stats"):
            return {"history_mode": "persistent_echo", "archive_capacity": 1000}
        raise AssertionError(f"unexpected request: {url}")

    monkeypatch.setattr(benchmark_capture, "request", request)
    result = benchmark_capture.verify_echo_route("http://127.0.0.1:8813", {"profile": profile})

    assert result == {
        "status": "passed",
        "profile": profile,
        "history_mode": "persistent_echo",
        "archive_capacity": 1000,
    }
    assert calls == ["http://127.0.0.1:8813/echo/stats"]


def test_echo_profile_rejects_direct_backend_without_echo_stats(monkeypatch):
    def request(url, *_args, **_kwargs):
        raise RuntimeError(f"404 at {url}")

    monkeypatch.setattr(benchmark_capture, "request", request)

    with pytest.raises(RuntimeError, match="must use the live ECHO proxy"):
        benchmark_capture.verify_echo_route(
            "http://127.0.0.1:8850", {"profile": "dualcore-echo"},
        )


def test_capture_refuses_to_send_any_benchmark_rows_to_direct_echo_backend(monkeypatch, tmp_path):
    import hashlib
    import json

    artifact = tmp_path / "checkpoint.gguf"
    artifact.write_bytes(b"pinned fixture bytes")
    identity = {
        "model": "DualCore ECHO",
        "evidence_kind": "real_model",
        "profile": "dualcore-echo",
        "artifacts": [{
            "path": str(artifact),
            "bytes": artifact.stat().st_size,
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        }],
    }
    identity_path = tmp_path / "identity.json"
    identity_path.write_text(json.dumps(identity), encoding="utf-8")
    inputs_path = tmp_path / "inputs.json"
    prompt = "one row"
    inputs_path.write_text(json.dumps({
        "schema": 1,
        "benchmark": "livebench",
        "rows": [{
            "id": "sample-1",
            "prompt": prompt,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        }],
    }), encoding="utf-8")
    output = tmp_path / "capture"
    requested_urls = []

    def request(url, *_args, **_kwargs):
        requested_urls.append(url)
        if url.endswith("/health") or url.endswith("/props"):
            return {}
        if url.endswith("/echo/stats"):
            raise RuntimeError("direct backend returned 404")
        raise AssertionError(f"benchmark request must not be sent: {url}")

    monkeypatch.setattr(benchmark_capture, "request", request)

    with pytest.raises(RuntimeError, match="must use the live ECHO proxy"):
        benchmark_capture.capture(
            "http://127.0.0.1:8850", "DualCore ECHO", inputs_path,
            identity_path, output, 256,
        )

    assert requested_urls == [
        "http://127.0.0.1:8850/health",
        "http://127.0.0.1:8850/props",
        "http://127.0.0.1:8850/echo/stats",
    ]
    assert not (output / "capture-manifest.json").exists()


def test_non_echo_profile_does_not_require_echo_proxy(monkeypatch):
    def unexpected(*_args, **_kwargs):
        raise AssertionError("KV profiles must not probe the ECHO endpoint")

    monkeypatch.setattr(benchmark_capture, "request", unexpected)
    assert benchmark_capture.verify_echo_route(
        "http://127.0.0.1:8850", {"profile": "dualcore-kv"},
    ) is None


def test_speed_qualification_rejects_under_20_visible_tokens_per_second(monkeypatch, tmp_path):
    responses = iter([
        {
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "x" * 80},
                         "finish_reason": "stop"}],
        },
        {"tokens": list(range(80))},
    ])
    monkeypatch.setattr(benchmark_capture, "request", lambda *_args, **_kwargs: next(responses))
    times = iter([0.0, 5.0])
    monkeypatch.setattr(benchmark_capture.time, "perf_counter", lambda: next(times))

    with pytest.raises(RuntimeError, match="failed the 20 tokens/s"):
        benchmark_capture.qualify_speed(
            "http://127.0.0.1:8813", "test-model", "identity-hash", tmp_path,
        )

    import json
    saved = json.loads((tmp_path / "speed-qualification.json").read_text(encoding="utf-8"))
    assert saved["status"] == "rejected"
    assert saved["visible_tokens_per_second"] == 16.0


def test_speed_qualification_passes_at_threshold(monkeypatch, tmp_path):
    responses = iter([
        {
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "x" * 50},
                         "finish_reason": "stop"}],
        },
        {"tokens": list(range(50))},
    ])
    monkeypatch.setattr(benchmark_capture, "request", lambda *_args, **_kwargs: next(responses))
    times = iter([0.0, 2.5])
    monkeypatch.setattr(benchmark_capture.time, "perf_counter", lambda: next(times))

    result = benchmark_capture.qualify_speed(
        "http://127.0.0.1:8813", "test-model", "identity-hash", tmp_path,
    )

    assert result["status"] == "passed"
    assert result["visible_tokens_per_second"] == 20.0
