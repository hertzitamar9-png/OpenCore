"""The Fusion HTTP surface must stream actual deltas and report honest limits."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


RESOURCES = Path(__file__).resolve().parents[2] / "src-tauri" / "resources"
sys.path.insert(0, str(RESOURCES))
from fusion.serve_fusion import FusionHttpServer, FusionHandler  # noqa: E402


class FakeEngine:
    model_id = "opencore-twincore-experimental"
    context_tokens = 24

    def __init__(self):
        self.requests = []

    def tokenize(self, text):
        return list(range(len(text.split())))

    def prepare(self, messages, tools, max_new_tokens):
        self.requests.append((messages, tools, max_new_tokens))
        prompt_tokens = sum(len(message["content"].split()) for message in messages)
        if prompt_tokens + max_new_tokens > self.context_tokens:
            raise ValueError("Fusion active context exceeded")
        return (messages, prompt_tokens)

    def generate(self, prepared, max_new_tokens):
        yield from ["Hello", " world"][:max_new_tokens]


@pytest.fixture
def server():
    engine = FakeEngine()
    http = FusionHttpServer(("127.0.0.1", 0), FusionHandler, engine)
    thread = Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}", engine
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=2)


def post(url, path, payload):
    return urlopen(Request(url + path, json.dumps(payload).encode(),
                           {"Content-Type": "application/json"}), timeout=3)


def test_health_models_props_and_tokenizer(server):
    url, _ = server
    with urlopen(url + "/health") as response:
        assert json.load(response)["status"] == "ok"
    with urlopen(url + "/props") as response:
        assert json.load(response)["n_ctx"] == 24
    with urlopen(url + "/v1/models") as response:
        assert json.load(response)["data"][0]["id"] == "opencore-twincore-experimental"
    with post(url, "/tokenize", {"content": "one two three"}) as response:
        assert len(json.load(response)["tokens"]) == 3


def test_stream_yields_each_delta_before_done(server):
    url, engine = server
    payload = {"messages": [{"role": "user", "content": "Say hello"}],
               "stream": True, "max_tokens": 2}
    with post(url, "/v1/chat/completions", payload) as response:
        assert response.headers["Content-Type"].startswith("text/event-stream")
        events = [line.removeprefix("data: ").strip() for line in response.read().decode().splitlines()
                  if line.startswith("data: ")]
    chunks = [json.loads(event) for event in events[:-1]]
    assert events[-1] == "[DONE]"
    assert [chunk["choices"][0]["delta"].get("content") for chunk in chunks[:2]] == ["Hello", " world"]
    assert chunks[-1]["choices"][0]["finish_reason"] == "length"
    assert chunks[-1]["usage"]["completion_tokens"] == 2
    assert engine.requests[0][0][0]["content"] == "Say hello"


def test_nonstream_completion_has_usage(server):
    url, _ = server
    with post(url, "/v1/chat/completions", {"messages": [{"role": "user", "content": "Hello"}],
                                                "max_tokens": 2}) as response:
        result = json.load(response)
    assert result["choices"][0]["message"]["content"] == "Hello world"
    assert result["usage"] == {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}


@pytest.mark.parametrize("payload", [
    {"messages": []},
    {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": "x"}]}]},
    {"messages": [{"role": "user", "content": "hello"}], "max_tokens": 25},
])
def test_invalid_requests_fail_before_stream_headers(server, payload):
    url, _ = server
    payload["stream"] = True
    with pytest.raises(HTTPError) as exc:
        post(url, "/v1/chat/completions", payload)
    assert exc.value.code == 400
    assert json.load(exc.value)["error"]["message"]


def test_generation_error_releases_lock_and_does_not_return_partial_success(server):
    url, engine = server
    original = engine.generate

    def broken(_prepared, _max_new_tokens):
        yield "partial"
        raise RuntimeError("model failed")

    engine.generate = broken
    payload = {"messages": [{"role": "user", "content": "test"}], "max_tokens": 2}
    with pytest.raises(HTTPError) as exc:
        post(url, "/v1/chat/completions", payload)
    assert exc.value.code == 500
    assert "model failed" in json.load(exc.value)["error"]["message"]
    engine.generate = original
    with post(url, "/v1/chat/completions", payload) as response:
        assert json.load(response)["choices"][0]["message"]["content"] == "Hello world"
