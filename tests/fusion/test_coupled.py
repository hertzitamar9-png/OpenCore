from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path
import hashlib
import json
import sys

import pytest

torch = pytest.importorskip("torch")
from torch import nn

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src-tauri" / "resources"))

from fusion.alignment import ExactSurfaceAlignment  # noqa: E402
from fusion.budget import BF16_CHECKPOINT_BYTES, estimate_bf16_kv_bytes, require_device_budget  # noqa: E402
from fusion.coupled import CoupledFusion  # noqa: E402
from fusion.heads import ChunkedOutputHead  # noqa: E402
from fusion.manifest import verify_checkpoint  # noqa: E402


class ToyTokenizer:
    def __init__(self, vocab: dict[str, int], special_ids=()):
        self.vocab = vocab
        self.all_special_ids = list(special_ids)

    def get_vocab(self):
        return dict(self.vocab)

    def decode(self, ids, **_kwargs):
        reverse = {value: key for key, value in self.vocab.items()}
        return "".join(reverse[value] for value in ids)

    def encode(self, text, **_kwargs):
        pieces = sorted(self.vocab, key=len, reverse=True)
        result = []
        while text:
            piece = next((part for part in pieces if text.startswith(part)), None)
            if piece is None:
                raise ValueError(f"Unencodable text: {text!r}")
            result.append(self.vocab[piece])
            text = text[len(piece):]
        return result


class ToyCausalLM(nn.Module):
    def __init__(self, vocab: int, hidden: int):
        super().__init__()
        self.embedding = nn.Embedding(vocab, hidden)
        self.head = nn.Linear(hidden, vocab, bias=False)
        self.calls = 0
        self.last_inputs = None
        self.input_lengths = []

    def get_input_embeddings(self):
        return self.embedding

    def get_output_embeddings(self):
        return self.head

    def forward(
        self, *, inputs_embeds, output_hidden_states, use_cache, logits_to_keep,
        past_key_values=None,
    ):
        assert output_hidden_states is True and logits_to_keep == 1
        self.calls += 1
        self.last_inputs = inputs_embeds
        self.input_lengths.append(inputs_embeds.shape[1])
        # Prefix sum makes the output depend on all supplied token embeddings.
        previous = (
            torch.zeros_like(inputs_embeds[:, 0, :])
            if past_key_values is None else past_key_values[0]
        )
        hidden = (inputs_embeds + previous[:, None, :]).cumsum(dim=1)
        next_cache = (hidden[:, -1, :],) if use_cache else None
        return SimpleNamespace(
            hidden_states=(hidden,), logits=self.head(hidden[:, -1:]),
            past_key_values=next_cache,
        )


def make_model():
    qtok = ToyTokenizer({"a": 0, "b": 1, "x": 2, "<eos>": 3}, special_ids=(3,))
    ktok = ToyTokenizer({"a": 4, "b": 5, "ab": 6, "<eos>": 7}, special_ids=(7,))
    alignment = ExactSurfaceAlignment.from_tokenizers(qtok, ktok, 4, 8)
    nanbeige = ToyCausalLM(4, 6)
    k2 = ToyCausalLM(8, 5)
    with torch.no_grad():
        nanbeige.embedding.weight.fill_(0.1)
        nanbeige.head.weight.zero_()
        nanbeige.head.weight[1].fill_(2.0)
        k2.embedding.weight.fill_(0.1)
        k2.head.weight.zero_()
    fusion = CoupledFusion(nanbeige, k2, alignment, nanbeige_hidden=6, k2_hidden=5, rank=4)
    return fusion, nanbeige, k2, qtok, ktok


def test_12gb_card_rejects_bf16_checkpoints_and_joint_kv_is_finite():
    assert BF16_CHECKPOINT_BYTES == 18_456_172_544
    assert estimate_bf16_kv_bytes(32_768) == 10_737_418_240
    assert estimate_bf16_kv_bytes(1_000_000) == 327_680_000_000
    with pytest.raises(ValueError, match="exceeds device budget"):
        require_device_budget(12_282 * 1_048_576, BF16_CHECKPOINT_BYTES, 0, 0)


def test_alignment_only_projects_equal_non_special_pieces():
    q = ToyTokenizer({"a": 0, "b": 1, "x": 2, "<eos>": 3}, special_ids=(3,))
    k = ToyTokenizer({"a": 4, "b": 5, "ab": 6, "<eos>": 7}, special_ids=(7,))
    alignment = ExactSurfaceAlignment.from_tokenizers(q, k, 4, 8)
    assert alignment.size == 2
    scores = torch.zeros((1, 8))
    scores[0, 4] = 4.0
    evidence = alignment.project(scores)
    assert evidence.shape == (1, 4)
    assert evidence[0, 0] > evidence[0, 1]
    assert evidence[0, 2] == 0 and evidence[0, 3] == 0


def test_alignment_rejects_incomplete_byte_piece_and_keeps_unicode():
    q = ToyTokenizer({"שלום": 0, "\ufffd": 1})
    k = ToyTokenizer({"שלום": 3, "\ufffd": 4})
    alignment = ExactSurfaceAlignment.from_tokenizers(q, k, 2, 5)
    assert alignment.size == 1
    assert alignment.nanbeige_ids.tolist() == [0]


def test_chunked_cpu_output_head_matches_dense_linear():
    torch.manual_seed(19)
    weight = torch.randn(11, 7, dtype=torch.bfloat16)
    bias = torch.randn(11, dtype=torch.bfloat16)
    hidden = torch.randn(2, 3, 7, dtype=torch.bfloat16)
    head = ChunkedOutputHead(weight, bias, torch.device("cpu"), chunk_rows=4)
    actual = head(hidden)
    expected = torch.nn.functional.linear(hidden, weight, bias)
    assert actual.device.type == "cpu"
    assert torch.equal(actual, expected)


def test_checkpoint_verification_rejects_changed_source_before_load(tmp_path):
    revision = "0123456789abcdef0123456789abcdef01234567"
    root = tmp_path / f"pinned-model-{revision}"
    root.mkdir()
    (root / "config.json").write_text('{"model_type":"test"}', encoding="utf-8")
    (root / "model-00001.safetensors").write_bytes(b"shard")
    (root / "model.safetensors.index.json").write_text(
        json.dumps({"weight_map": {"layer": "model-00001.safetensors"}}), encoding="utf-8"
    )
    files = {}
    for name in ("config.json", "model-00001.safetensors", "model.safetensors.index.json"):
        raw = (root / name).read_bytes()
        files[name] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    manifest = {"schema": 1, "models": {"test": {
        "repo_id": "test/model", "revision": revision, "folder": root.name, "files": files,
    }}}
    assert verify_checkpoint(tmp_path, manifest, "test") == root
    (root / "config.json").write_text('{"model_type":"hack"}', encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_checkpoint(tmp_path, manifest, "test")


def test_checkpoint_verification_accepts_pinned_git_blob_for_custom_model_code(tmp_path):
    revision = "89abcdef0123456789abcdef0123456789abcdef"
    root = tmp_path / f"nanbeige-{revision}"
    root.mkdir()
    config = b'{"model_type":"test"}'
    shard = b"weights"
    (root / "config.json").write_bytes(config)
    (root / "model-00001.safetensors").write_bytes(shard)
    (root / "model.safetensors.index.json").write_text(
        json.dumps({"weight_map": {"layer": "model-00001.safetensors"}}), encoding="utf-8"
    )
    files = {
        "config.json": {
            "bytes": len(config),
            "git_blob_sha1": hashlib.sha1(f"blob {len(config)}\0".encode() + config).hexdigest(),
        },
        "model-00001.safetensors": {
            "bytes": len(shard), "sha256": hashlib.sha256(shard).hexdigest(),
        },
        "model.safetensors.index.json": {
            "bytes": (root / "model.safetensors.index.json").stat().st_size,
            "sha256": hashlib.sha256((root / "model.safetensors.index.json").read_bytes()).hexdigest(),
        },
    }
    manifest = {"schema": 1, "models": {"nanbeige": {
        "repo_id": "Nanbeige/Nanbeige4.2-3B", "revision": revision,
        "folder": root.name, "files": files,
    }}}
    assert verify_checkpoint(tmp_path, manifest, "nanbeige") == root


def test_twincore_manifest_pins_nanbeige_and_k2_not_qwen():
    manifest_path = Path(__file__).resolve().parents[2] / "src-tauri" / "resources" / "fusion" / "checkpoints.sha256.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert set(manifest["models"]) == {"nanbeige4.2-3b", "k2-horizon-3.7b"}
    nanbeige = manifest["models"]["nanbeige4.2-3b"]
    assert nanbeige["repo_id"] == "Nanbeige/Nanbeige4.2-3B"
    assert nanbeige["revision"] == "b82e54bd609793562a75cbf9337970a93369eab5"
    assert sum(
        info["bytes"] for name, info in nanbeige["files"].items()
        if name.endswith(".safetensors")
    ) == 8_339_624_720


def test_both_native_heads_change_the_one_output_distribution():
    fusion, nanbeige, k2, *_ = make_model()
    qids = torch.tensor([[0, 1]])
    kids = torch.tensor([[6]])
    baseline = fusion.step(qids, kids).logits.detach().clone()
    with torch.no_grad():
        nanbeige.head.weight[0].add_(2.0)
    q_changed = fusion.step(qids, kids).logits.detach().clone()
    assert not torch.allclose(baseline, q_changed)
    with torch.no_grad():
        k2.head.weight[4].add_(8.0)
    k_changed = fusion.step(qids, kids).logits.detach().clone()
    assert not torch.allclose(q_changed, k_changed)
    assert nanbeige.calls == k2.calls == 3


def test_feedback_enters_both_full_decoders_on_the_next_step():
    fusion, nanbeige, k2, *_ = make_model()
    first = fusion.step(torch.tensor([[0]]), torch.tensor([[4]]))
    assert first.feedback.nanbeige.abs().sum() > 0
    assert first.feedback.k2.abs().sum() > 0
    fusion.step(torch.tensor([[0, 1]]), torch.tensor([[6]]), feedback=first.feedback)
    q_raw = nanbeige.embedding(torch.tensor([[0, 1]]))
    k_raw = k2.embedding(torch.tensor([[6]]))
    assert not torch.allclose(nanbeige.last_inputs, q_raw)
    assert not torch.allclose(k2.last_inputs, k_raw)


def test_bridge_trains_while_both_checkpoint_towers_stay_frozen():
    fusion, nanbeige, k2, *_ = make_model()
    output = fusion.step(torch.tensor([[0, 1]]), torch.tensor([[6]]))
    output.logits.square().mean().backward()
    assert all(not p.requires_grad for p in nanbeige.parameters())
    assert all(not p.requires_grad for p in k2.parameters())
    assert any(p.grad is not None and p.grad.abs().sum() > 0 for p in fusion.bridge_parameters())


def test_one_stream_retokenizes_k2_after_each_nanbeige_token():
    fusion, nanbeige, k2, qtok, ktok = make_model()
    emitted = list(fusion.stream_text("a", qtok, ktok, max_new_tokens=2))
    assert emitted == ["b", "b"]
    assert nanbeige.calls == k2.calls == 2
    assert nanbeige.input_lengths == [1, 1]
    assert k2.input_lengths == [1, 1]  # "ab" becomes one K2 token.
    assert fusion.last_text == "a" + "".join(emitted)
    assert fusion.last_k2_ids == ktok.encode(fusion.last_text)


def test_native_chat_prefixes_share_only_the_generated_suffix():
    fusion, nanbeige, k2, qtok, ktok = make_model()
    pieces = list(fusion.stream_text(
        "a", qtok, ktok, k2_prompt="ab", max_new_tokens=2
    ))
    assert pieces == ["b", "b"]
    assert fusion.last_generated_text == "bb"
    assert fusion.last_text == "abb"
    assert nanbeige.input_lengths == [1, 1]
    assert k2.input_lengths == [1, 1]
    assert fusion.last_k2_ids == ktok.encode("abbb")


def test_zero_output_budget_does_not_prefill_either_tower():
    fusion, nanbeige, k2, qtok, ktok = make_model()
    assert list(fusion.stream_text("a", qtok, ktok, max_new_tokens=0)) == []
    assert nanbeige.calls == k2.calls == 0


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA is not available")
def test_cpu_embedding_can_feed_gpu_decoder_and_joint_head():
    class ToyBase(nn.Module):
        def __init__(self, hidden):
            super().__init__()
            self.projection = nn.Linear(hidden, hidden, bias=False, device="cuda")

        def forward(self, *, inputs_embeds, use_cache):
            assert use_cache is False
            return SimpleNamespace(last_hidden_state=self.projection(inputs_embeds))

    class ToyOffloadedLM(nn.Module):
        def __init__(self, vocab, hidden, offload_embedding):
            super().__init__()
            self.embedding = nn.Embedding(vocab, hidden, device="cpu" if offload_embedding else "cuda")
            self.model = ToyBase(hidden)
            self.head = nn.Linear(hidden, vocab, bias=False, device="cuda")

        def get_input_embeddings(self):
            return self.embedding

        def get_output_embeddings(self):
            return self.head

    nanbeige = ToyOffloadedLM(4, 6, offload_embedding=True)
    k2 = ToyOffloadedLM(8, 5, offload_embedding=False)
    alignment = ExactSurfaceAlignment([0], [4], 4, 8)
    fusion = CoupledFusion(nanbeige, k2, alignment, nanbeige_hidden=6, k2_hidden=5, rank=4)
    with torch.inference_mode():
        output = fusion.step(torch.tensor([[0, 1]]), torch.tensor([[4, 5]], device="cuda"))
    assert output.logits.shape == (1, 4)
    assert output.logits.device.type == "cuda"
