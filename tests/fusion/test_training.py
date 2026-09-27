"""CPU training/receipt contracts, separate from real full-checkpoint quality."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src-tauri/resources'))
from fusion.alignment import ExactSurfaceAlignment
from fusion.bridge import CouplingBridge
from fusion.native import NativeFeatures


def training_modules():
    try:
        from fusion import adapter, training
    except ImportError:
        pytest.fail('Native TwinCore training and bound adapters are not implemented')
    return adapter, training


class TinyFrozenNative:
    """A deterministic CPU decoder boundary, not a deployed model."""
    def __init__(self):
        self.geometry = [{'hidden': 6, 'vocab': 4, 'capacity': 64},
                         {'hidden': 5, 'vocab': 8, 'capacity': 64}]
        generator = torch.Generator().manual_seed(39)
        self.weights = [torch.randn(4, 6, generator=generator), torch.randn(8, 5, generator=generator)]
        self.feedback_calls = []

    def clear(self):
        self.feedback_calls.clear()

    def format(self, brain, messages):
        return ''.join(message['content'] for message in messages)

    def tokenize(self, brain, text, special=True):
        return np.array([{'a': 0, 'b': 1, 'c': 2}[character] + 4 * brain for character in text], np.int32)

    def piece(self, brain, token):
        return ['a', 'b', 'c'][token - 4 * brain].encode()

    def token_flags(self, brain, token):
        return 0

    def step(self, n_ids, k_ids, n_feedback=None, k_feedback=None):
        self.feedback_calls.append((n_feedback, k_feedback))
        n = torch.arange(6).float().unsqueeze(0) * 0.15 + len(n_ids) * 0.03
        k = torch.arange(5).float().unsqueeze(0) * 0.12 + len(k_ids) * 0.02
        if n_feedback is not None:
            n = n + torch.as_tensor(n_feedback)
            k = k + torch.as_tensor(k_feedback)
        return NativeFeatures(n.detach(), k.detach(), n @ self.weights[0].T, k @ self.weights[1].T)

    def project(self, brain, values, transpose=False):
        weight = self.weights[brain].numpy()
        return np.asarray(values, dtype=np.float32) @ (weight if transpose else weight.T)


def bridge():
    torch.manual_seed(7)
    return CouplingBridge(ExactSurfaceAlignment([0, 1], [4, 5], 4, 8),
                          nanbeige_hidden=6, k2_hidden=5, rank=4)


def rows():
    return [
        {'id': 'train-1', 'split': 'train', 'messages': [{'role': 'user', 'content': 'a'}], 'answer': 'ab'},
        {'id': 'val-1', 'split': 'validation', 'messages': [{'role': 'user', 'content': 'c'}], 'answer': 'bb'},
    ]


def corpus_file(tmp_path, records=None):
    path = tmp_path / 'corpus.jsonl'
    path.write_text(''.join(json.dumps(row) + '\n' for row in (records or rows())), encoding='utf-8')
    return path


def binding_for(model):
    adapter, _ = training_modules()
    return adapter.make_binding(model, {
        'nanbeige': {'sha256': '93f884a2d8d6cafc5406df84be64f197a407889904b18db7c6e82fd35f2b0170', 'bytes': 3595603104},
        'k2': {'sha256': '2180f3ca4eb4906a109b364a98740778fd8dcd9969e270b0d34036d18ee33232', 'bytes': 4161403264},
    }, {'source_commit': '42adf019f76013dac873b5b43950d54d5ab27216',
         'libraries': [{'scope': 'native', 'path': 'twincore.dll', 'bytes': 100, 'sha256': '1' * 64}],
         'sources': [{'path': 'head_projection.h', 'bytes': 20, 'sha256': '2' * 64}]})


def test_teacher_forcing_uses_both_heads_and_truncated_feedback():
    adapter, training = training_modules()
    native, model = TinyFrozenNative(), bridge()
    before = adapter.tensor_fingerprint(model)
    report = training.teacher_forced(native, model, rows()[0], max_tokens=8)
    assert report.tokens == 2 and report.complete
    report.loss.backward()
    for parameter in model.bridge_parameters():
        assert parameter.grad is not None and torch.isfinite(parameter.grad).all()
        assert parameter.grad.abs().sum() > 0
    assert native.feedback_calls[0] == (None, None)
    assert native.feedback_calls[1][0] is not None and native.feedback_calls[1][1] is not None
    assert isinstance(native.feedback_calls[1][0], np.ndarray)
    assert all(weight.grad is None for weight in native.weights)
    assert adapter.tensor_fingerprint(model) == before


@pytest.mark.parametrize('leak', ['duplicate-id', 'same-prompt', 'benchmark-id'])
def test_corpus_rejects_train_validation_leaks_and_benchmark_ids(tmp_path, leak):
    _, training = training_modules()
    records = rows()
    if leak == 'duplicate-id':
        records[1]['id'] = records[0]['id']
    elif leak == 'same-prompt':
        records[1]['messages'] = records[0]['messages']
    else:
        records[0]['id'] = 'HumanEval/0'
    with pytest.raises(ValueError, match='duplicate|leak|benchmark'):
        training.read_corpus(corpus_file(tmp_path, records))


def test_corpus_hash_is_bound_to_exact_bytes(tmp_path):
    _, training = training_modules()
    path = corpus_file(tmp_path)
    corpus = training.read_corpus(path)
    assert corpus.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_text(path.read_text() + '\n')
    with pytest.raises(ValueError, match='identity'):
        training.read_corpus(path, expected_sha256=corpus.sha256)


def trained(tmp_path):
    adapter, training = training_modules()
    model, native = bridge(), TinyFrozenNative()
    initial = adapter.tensor_fingerprint(model)
    optimizer = torch.optim.AdamW(model.bridge_parameters(), lr=0.02)
    optimizer.zero_grad()
    training.teacher_forced(native, model, rows()[0], max_tokens=8).loss.backward()
    optimizer.step()
    corpus = training.read_corpus(corpus_file(tmp_path))
    evidence = {
        'steps': 1, 'tokens': 2, 'initial_bridge_sha256': initial, 'corpus_sha256': corpus.sha256,
        'gradient_mode': training.GRADIENT_MODE,
        'validation': {'tokens': 2, 'loss': 1.3, 'baseline_loss': 1.4},
    }
    return model, optimizer, binding_for(model), evidence


def test_adapter_resume_restores_weights_and_optimizer_exactly(tmp_path):
    adapter, _ = training_modules()
    model, optimizer, binding, evidence = trained(tmp_path)
    saved = tmp_path / 'adapter'
    adapter.save_adapter(saved, model, binding, evidence, optimizer=optimizer)
    restored = bridge()
    resumed_optimizer = torch.optim.AdamW(restored.bridge_parameters(), lr=0.001)
    receipt = adapter.load_adapter(saved, restored, binding, optimizer=resumed_optimizer)
    assert receipt['training']['corpus_sha256'] == evidence['corpus_sha256']
    assert adapter.tensor_fingerprint(restored) == adapter.tensor_fingerprint(model)
    assert resumed_optimizer.param_groups[0]['lr'] == optimizer.param_groups[0]['lr']
    for old, new in zip(optimizer.state.values(), resumed_optimizer.state.values()):
        for key in old:
            torch.testing.assert_close(old[key], new[key], rtol=0, atol=0)


def test_adamw_resume_cannot_be_applied_to_a_different_optimizer(tmp_path):
    adapter, _ = training_modules()
    model, optimizer, binding, evidence = trained(tmp_path)
    saved = tmp_path / 'adapter'
    adapter.save_adapter(saved, model, binding, evidence, optimizer=optimizer)
    live = bridge()
    before = adapter.tensor_fingerprint(live)
    with pytest.raises(ValueError, match='optimizer|Optimizer'):
        adapter.load_adapter(saved, live, binding, optimizer=torch.optim.SGD(live.bridge_parameters(), lr=0.01))
    assert adapter.tensor_fingerprint(live) == before


@pytest.mark.parametrize('invalid', ['different-algorithm', 'uninitialized-adamw'])
def test_invalid_optimizer_save_leaves_no_partial_adapter_files(tmp_path, invalid):
    adapter, _ = training_modules()
    model, _, binding, evidence = trained(tmp_path)
    destination = tmp_path / 'rejected-adapter'
    optimizer_type = torch.optim.SGD if invalid == 'different-algorithm' else torch.optim.AdamW
    optimizer = optimizer_type(model.bridge_parameters(), lr=0.01)
    with pytest.raises(ValueError, match='optimizer|Optimizer|AdamW'):
        adapter.save_adapter(destination, model, binding, evidence, optimizer=optimizer)
    assert not destination.exists()


def test_resume_rejects_missing_adam_moments_before_applying_any_weights(tmp_path):
    from safetensors.torch import load_file, save_file
    adapter, _ = training_modules()
    model, optimizer, binding, evidence = trained(tmp_path)
    saved = tmp_path / 'adapter'
    adapter.save_adapter(saved, model, binding, evidence, optimizer=optimizer)
    receipt = json.loads((saved / 'receipt.json').read_text())
    entries = next(iter(receipt['optimizer']['entries'].values()))
    missing = entries.pop('exp_avg')
    tensors = load_file(str(saved / 'optimizer.safetensors'))
    del tensors[missing]
    save_file(tensors, str(saved / 'optimizer.safetensors'))
    receipt['files']['optimizer.safetensors'] = adapter.file_digest(saved / 'optimizer.safetensors')
    receipt.pop('receipt_sha256')
    receipt['receipt_sha256'] = hashlib.sha256(adapter.canonical(receipt)).hexdigest()
    (saved / 'receipt.json').write_bytes(adapter.canonical(receipt))
    live = bridge()
    before = adapter.tensor_fingerprint(live)
    with pytest.raises(ValueError, match='optimizer|Optimizer'):
        adapter.load_adapter(saved, live, binding, optimizer=torch.optim.AdamW(live.bridge_parameters()))
    assert adapter.tensor_fingerprint(live) == before


@pytest.mark.parametrize('change', ['weights', 'checkpoint', 'alignment', 'training', 'optimizer'])
def test_adapter_rejects_drift_without_mutating_the_live_bridge(tmp_path, change):
    adapter, _ = training_modules()
    model, optimizer, binding, evidence = trained(tmp_path)
    saved = tmp_path / 'adapter'
    adapter.save_adapter(saved, model, binding, evidence, optimizer=optimizer)
    expected = json.loads(json.dumps(binding))
    if change in ('weights', 'optimizer'):
        name = 'bridge.safetensors' if change == 'weights' else 'optimizer.safetensors'
        path = saved / name
        path.write_bytes(path.read_bytes() + b'corrupt')
    elif change == 'checkpoint':
        expected['checkpoints']['k2']['sha256'] = '3' * 64
    elif change == 'alignment':
        expected['alignment_sha256'] = '4' * 64
    else:
        receipt_path = saved / 'receipt.json'
        receipt = json.loads(receipt_path.read_text())
        receipt['training']['steps'] = 0
        receipt_path.write_text(json.dumps(receipt))
    live = bridge()
    before = adapter.tensor_fingerprint(live)
    with pytest.raises(ValueError, match='identity|training|integrity|untrained'):
        adapter.load_adapter(saved, live, expected, optimizer=torch.optim.AdamW(live.bridge_parameters()))
    assert adapter.tensor_fingerprint(live) == before


def test_initialized_adapter_is_not_accepted_as_trained(tmp_path):
    adapter, training = training_modules()
    model = bridge()
    evidence = {'steps': 1, 'tokens': 2, 'initial_bridge_sha256': adapter.tensor_fingerprint(model),
                'corpus_sha256': '5' * 64, 'gradient_mode': training.GRADIENT_MODE,
                'validation': {'tokens': 2, 'loss': 1.4, 'baseline_loss': 1.4}}
    with pytest.raises(ValueError, match='untrained|initialized'):
        adapter.save_adapter(tmp_path / 'adapter', model, binding_for(model), evidence)


def test_unicode_byte_targets_reach_both_towers_without_replacement_text():
    _, training = training_modules()

    class ByteNative(TinyFrozenNative):
        def __init__(self):
            super().__init__()
            self.prefix_snapshots = []

        def tokenize(self, brain, text, special=True):
            return np.array([{0xd7: 0, 0x90: 1, 0x61: 2}[byte] + 4 * brain
                             for byte in text.encode('utf-8')], np.int32)

        def piece(self, brain, token):
            return [b'\xd7', b'\x90', b'a'][token - 4 * brain]

        def token_flags(self, brain, token):
            return 2 if token < 4 * brain or token - 4 * brain > 2 else 0

        def step(self, n_ids, k_ids, *bias):
            self.prefix_snapshots.append((n_ids.tolist(), k_ids.tolist()))
            return super().step(n_ids, k_ids, *bias)

    native, model = ByteNative(), bridge()
    example = {'messages': [{'role': 'user', 'content': 'a'}], 'answer': 'א'}
    report = training.teacher_forced(native, model, example, max_tokens=8)
    assert report.tokens == 2 and report.complete
    assert native.prefix_snapshots == [([2], [6]), ([2, 0], [6, 4])]


def test_native_alignment_keeps_equal_raw_byte_pieces_and_excludes_controls():
    try:
        from fusion.canonical import inspect_surfaces, native_alignment
    except ImportError:
        pytest.fail('Native vocabulary surfaces are not yet aligned')

    class SurfaceNative:
        geometry = [{'vocab': 4}, {'vocab': 8}]

        def token_flags(self, brain, token):
            return 3 if (brain == 0 and token == 3) or (brain == 1 and token < 4 or token == 7) else 0

        def piece(self, brain, token):
            return [b'\xd7', b'a', b'bc', b'<eos>'][token - 4 * brain]

    surfaces = inspect_surfaces(SurfaceNative())
    alignment = native_alignment(surfaces)
    assert alignment.nanbeige_ids.tolist() == [0, 1, 2]
    assert alignment.k2_ids.tolist() == [4, 5, 6]
    assert surfaces[0].byte_tokens == {0xd7: 0, 0x61: 1}


class DummySpaceNative(TinyFrozenNative):
    """SentencePiece-style dummy space, with exact ordinary piece boundaries."""
    def __init__(self):
        super().__init__()
        self.geometry[0]['vocab'] = 6
        self.weights[0] = torch.arange(36).float().reshape(6, 6) * 0.003

    def tokenize(self, brain, text, special=True):
        if brain:
            return super().tokenize(brain, text, special)
        return np.array([4] + [{'a': 0, 'b': 1, 'c': 2, '\n': 3, ' ': 4}[character]
                               for character in text], np.int32)

    def piece(self, brain, token):
        if brain:
            return super().piece(brain, token)
        return [b'a', b'b', b'c', b'\n', b' ', b''][token]


def test_sentencepiece_dummy_space_is_not_added_to_the_teacher_answer():
    _, training = training_modules()
    native = DummySpaceNative()
    model = CouplingBridge(ExactSurfaceAlignment([0, 1], [4, 5], 6, 8),
                           nanbeige_hidden=6, k2_hidden=5, rank=4)
    measured = training.teacher_forced(native, model, rows()[0], max_tokens=8)
    assert measured.complete and measured.tokens == 2
    measured.loss.backward()
    assert all(parameter.grad is not None and torch.isfinite(parameter.grad).all()
               for parameter in model.bridge_parameters())


def test_answer_encoding_preserves_real_leading_spaces_and_line_breaks():
    _, training = training_modules()
    answer = ' ab\nc'
    ids, pieces = training.encode_target(DummySpaceNative(), answer)
    assert ids.tolist() == [4, 0, 1, 3, 2]
    assert b''.join(pieces) == answer.encode('utf-8')


@pytest.mark.parametrize('failure', ['truncated-target', 'k2-capacity'])
def test_corpus_capacity_is_checked_before_any_decoder_or_optimizer_work(tmp_path, failure):
    _, training = training_modules()
    native = TinyFrozenNative()
    corpus = training.read_corpus(corpus_file(tmp_path))
    budget = 8
    if failure == 'truncated-target':
        budget = 1
    else:
        native.geometry[1]['capacity'] = 2
    with pytest.raises(ValueError, match='target|capacity'):
        training.inspect_corpus(native, corpus, max_tokens=budget)
    assert native.feedback_calls == []


def test_corpus_inspection_records_complete_targets_and_both_native_prefix_sizes(tmp_path):
    _, training = training_modules()
    native = TinyFrozenNative()
    corpus = training.read_corpus(corpus_file(tmp_path))
    result = training.inspect_corpus(native, corpus, max_tokens=8)
    assert result['corpus_sha256'] == corpus.sha256
    assert result['train_samples'] == 1 and result['validation_samples'] == 1
    assert result['target_tokens'] == 4 and result['maximum_target_tokens'] == 2
    assert result['maximum_native_prefix_tokens'] == [3, 3]
    assert [row['id'] for row in result['samples']] == ['train-1', 'val-1']
    assert native.feedback_calls == []
