"""The Fusion HTTP surface must stream actual deltas and report honest limits."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from threading import Event, Thread
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


def test_props_discloses_the_identity_of_the_loaded_runtime(server):
    url, engine = server
    engine.evidence = {'binding': {'schema': 1}, 'adapter_receipt_sha256': 'a' * 64,
                       'qualification_sha256': 'b' * 64}
    with urlopen(url + '/props') as response:
        result = json.load(response)
    assert result['runtime_identity'] == engine.evidence


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


def test_first_text_delta_arrives_before_generation_finishes(server):
    url, engine = server
    release = Event()

    def held(_prepared, _max):
        yield 'first'
        assert release.wait(2), 'Test did not release the second decoding step'
        yield ' second'

    engine.generate = held
    try:
        with post(url, '/v1/chat/completions', {'messages': [{'role': 'user', 'content': 'hello'}],
                                                'max_tokens': 2, 'stream': True}) as response:
            line = response.readline().decode()
            assert json.loads(line.removeprefix('data: '))['choices'][0]['delta']['content'] == 'first'
            assert not release.is_set()
            release.set()
            assert '[DONE]' in response.read().decode()
    finally:
        release.set()


def test_native_unicode_token_usage_counts_tokens_and_eos_instead_of_text_chunks(server):
    from fusion.generation import GenerationEvent
    url, engine = server
    engine.generate = lambda *_: iter([GenerationEvent('', 1), GenerationEvent('א', 2), GenerationEvent('', 3, 'stop')])
    with post(url, '/v1/chat/completions', {'messages': [{'role': 'user', 'content': 'hello'}], 'max_tokens': 4}) as response:
        result = json.load(response)
    assert result['choices'][0]['message']['content'] == 'א'
    assert result['usage']['completion_tokens'] == 3
    assert result['choices'][0]['finish_reason'] == 'stop'


TOOLS = [{'type': 'function', 'function': {'name': 'dev', 'parameters': {
    'type': 'object', 'properties': {'action': {'type': 'string', 'enum': ['read', 'patch']}},
    'required': ['action'], 'additionalProperties': False}}}]


def test_model_tool_call_is_structured_and_registered(server):
    url, engine = server
    engine.generate = lambda *_: iter(['<tool_call>{"name":"dev","arguments":{"action":"read"}}</tool_call>'])
    with post(url, '/v1/chat/completions', {'messages': [{'role': 'user', 'content': 'Inspect'}],
                                         'max_tokens': 8, 'tools': TOOLS}) as response:
        result = json.load(response)
    message = result['choices'][0]['message']
    call = message['tool_calls'][0]
    assert result['choices'][0]['finish_reason'] == 'tool_calls'
    assert call['id'].startswith('call_twincore_') and call['function']['name'] == 'dev'
    assert json.loads(call['function']['arguments']) == {'action': 'read'}
    assert message['content'] == ''


@pytest.mark.parametrize('arguments', ['{}', '{"action":"erase"}', '{"action":"read","unexpected":1}'])
def test_invalid_tool_arguments_fail_without_selected_action_or_partial_success(server, arguments):
    url, engine = server
    engine.generate = lambda *_: iter(['<tool_call>{"name":"dev","arguments":' + arguments + '}</tool_call>'])
    with pytest.raises(HTTPError) as failed:
        post(url, '/v1/chat/completions', {'messages': [{'role': 'user', 'content': 'Inspect'}],
                                         'max_tokens': 8, 'tools': TOOLS})
    assert failed.value.code == 500
    assert 'tool' in json.load(failed.value)['error']['message'].lower()


def test_explicit_cancellation_is_accepted_while_generation_is_blocked(server):
    from fusion.generation import GenerationCancelled
    url, engine = server
    stopped = Event()
    engine.cancel = stopped.set

    def held(*_):
        yield 'first'
        assert stopped.wait(2), 'The server did not deliver cancellation'
        raise GenerationCancelled('TwinCore generation cancelled')

    engine.generate = held
    try:
        with post(url, '/v1/chat/completions', {'messages': [{'role': 'user', 'content': 'hello'}],
                                                'max_tokens': 2, 'stream': True}) as response:
            assert 'first' in response.readline().decode()
            with post(url, '/cancel', {}) as cancellation:
                assert json.load(cancellation)['cancel_requested'] is True
            remaining = response.read().decode()
            assert 'cancelled' in remaining and '[DONE]' not in remaining
    finally:
        stopped.set()
