"""One native coupled stream, including raw byte pieces and cancellation."""
from pathlib import Path
import sys
import threading

import numpy as np
import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src-tauri/resources'))
from fusion.alignment import ExactSurfaceAlignment
from fusion.bridge import CouplingBridge
from fusion.native import NativeFeatures


def generation():
    try:
        from fusion import generation
    except ImportError:
        pytest.fail('The native one-stream decoder is missing')
    return generation


class ByteNative:
    def __init__(self, *, fail_second=False, k_capacity=32):
        self.geometry = [{'hidden': 6, 'vocab': 5, 'capacity': 32},
                         {'hidden': 5, 'vocab': 9, 'capacity': k_capacity}]
        self.closed, self.clears, self.calls, self.heads = False, 0, [], []
        self.fail_second = fail_second

    def format(self, brain, messages):
        return ''.join(row['content'] for row in messages)

    def tokenize(self, brain, text, special=True):
        return np.array([{0xd7: 0, 0x90: 1, 0x61: 2}[byte] + 4 * brain for byte in text.encode()], np.int32)

    def piece(self, brain, token):
        return [b'\xd7', b'\x90', b'a', b'<eos>', b'<control>'][token - 4 * brain]

    def token_flags(self, brain, token):
        local = token - 4 * brain
        return 3 if local == 3 else 2 if local not in (0, 1, 2) else 0

    def clear(self):
        self.clears += 1

    def close(self):
        self.closed = True

    def step(self, n, k, n_feedback=None, k_feedback=None):
        self.calls.append((n.tolist(), k.tolist(), n_feedback, k_feedback))
        if self.fail_second and len(self.calls) == 2:
            raise RuntimeError('K2 native decoder failed')
        chosen = [0, 1, 3][min(len(self.calls) - 1, 2)]
        n_logits = torch.full((1, 5), -50.0)
        k_logits = torch.full((1, 9), -50.0)
        n_logits[0, chosen], k_logits[0, chosen + 4] = 50.0, 50.0
        return NativeFeatures(torch.ones(1, 6), torch.ones(1, 5), n_logits, k_logits)

    def project(self, brain, values, transpose=False):
        self.heads.append(brain)
        return np.zeros((len(values), self.geometry[brain]['hidden' if transpose else 'vocab']), np.float32)


def decoder(**kwargs):
    native = ByteNative(**kwargs)
    torch.manual_seed(7)
    bridge = CouplingBridge(ExactSurfaceAlignment([0, 1, 2], [4, 5, 6], 5, 9),
                            nanbeige_hidden=6, k2_hidden=5, rank=4)
    return native, generation().SingleStreamDecoder(native, bridge)


def test_unicode_is_streamed_before_eos_with_true_token_usage_and_both_feedbacks():
    native, model = decoder()
    prepared, count = model.prepare([{'role': 'user', 'content': 'a'}], max_tokens=4)
    assert count == 1
    stream = model.generate(prepared, max_tokens=4)
    first, second = next(stream), next(stream)
    assert (first.text, first.completion_tokens) == ('', 1)
    assert (second.text, second.completion_tokens) == ('א', 2)
    final = list(stream)[-1]
    assert final.finish_reason == 'stop' and final.completion_tokens == 3
    assert [(row[0], row[1]) for row in native.calls] == [([2], [6]), ([2, 0], [6, 4]), ([2, 0, 1], [6, 4, 5])]
    assert all(row[2] is not None and row[3] is not None for row in native.calls[1:])
    assert native.heads == [0, 1, 0, 1, 0, 1]
    assert native.clears == 2 and not native.closed


def test_length_finish_keeps_native_token_count_distinct_from_text_deltas():
    native, model = decoder()
    prepared, _ = model.prepare([{'role': 'user', 'content': 'a'}], max_tokens=2)
    events = list(model.generate(prepared, max_tokens=2))
    assert ''.join(event.text for event in events) == 'א'
    assert events[-1].completion_tokens == 2 and events[-1].finish_reason == 'length'
    assert len(native.calls) == 2


def test_already_formatted_native_prompt_does_not_receive_a_second_bos():
    native, model = decoder()
    original = native.tokenize

    def with_automatic_bos(brain, text, special=True):
        ids = original(brain, text, special)
        return np.concatenate(([2 + 4 * brain], ids)) if special else ids

    native.tokenize = with_automatic_bos
    prepared, count = model.prepare([{'role': 'user', 'content': 'a'}], max_tokens=4)
    assert count == 1
    list(model.generate(prepared, max_tokens=4))
    assert native.calls[0][:2] == ([2], [6])


def test_cancellation_clears_both_contexts_and_cannot_be_reported_as_success():
    native, model = decoder()
    cancel = threading.Event()
    prepared, _ = model.prepare([{'role': 'user', 'content': 'a'}], max_tokens=4)
    stream = model.generate(prepared, max_tokens=4, cancel=cancel)
    next(stream)
    cancel.set()
    with pytest.raises(generation().GenerationCancelled):
        next(stream)
    assert len(native.calls) == 1 and native.clears == 2 and not native.closed


def test_closed_stream_releases_both_decoding_contexts():
    native, model = decoder()
    prepared, _ = model.prepare([{'role': 'user', 'content': 'a'}], max_tokens=4)
    stream = model.generate(prepared, max_tokens=4)
    next(stream)
    stream.close()
    assert native.clears == 2


def test_second_tower_failure_releases_the_entire_pair_instead_of_a_single_brain_fallback():
    native, model = decoder(fail_second=True)
    prepared, _ = model.prepare([{'role': 'user', 'content': 'a'}], max_tokens=4)
    stream = model.generate(prepared, max_tokens=4)
    next(stream)
    with pytest.raises(RuntimeError, match='K2 native'):
        next(stream)
    assert native.closed


def test_smaller_second_vocabulary_capacity_is_checked_before_starting_generation():
    native, model = decoder(k_capacity=3)
    with pytest.raises(ValueError, match='context'):
        model.prepare([{'role': 'user', 'content': 'a'}], max_tokens=4)
    assert native.calls == []
