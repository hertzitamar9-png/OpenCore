"""CPU contract tests for the pinned-transformer Fusion bridge trainer."""

from __future__ import annotations

from pathlib import Path
import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

torch = pytest.importorskip("torch")
from torch import nn

RESOURCE_ROOT = Path(__file__).resolve().parents[2] / "src-tauri" / "resources"
sys.path.insert(0, str(RESOURCE_ROOT))

from fusion import training  # noqa: E402
from fusion.alignment import ExactSurfaceAlignment  # noqa: E402
from fusion.coupled import CoupledFusion  # noqa: E402


class CharacterTokenizer:
    eos_token_id = 0
    all_special_ids = [0]

    def __init__(self, offset: int):
        self.offset = offset

    def apply_chat_template(self, messages, *, tokenize, add_generation_prompt):
        assert tokenize is False
        prefix = "".join(f"<{item['role']}>{item['content']}\n" for item in messages)
        return prefix + ("<assistant>" if add_generation_prompt else "")

    def encode(self, text: str, *, add_special_tokens: bool = False):
        assert add_special_tokens is False
        return [ord(char) + self.offset for char in text]

    def decode(self, ids, *, skip_special_tokens, clean_up_tokenization_spaces):
        assert clean_up_tokenization_spaces is False
        return "".join(
            "" if skip_special_tokens and int(token) == self.eos_token_id
            else chr(int(token) - self.offset)
            for token in ids if int(token) != self.eos_token_id
        )


class TinyCausalLM(nn.Module):
    def __init__(self, vocab_size: int = 260, hidden_size: int = 8):
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, hidden_size)
        self.lm_head = nn.Linear(hidden_size, vocab_size, bias=False)

    def get_input_embeddings(self):
        return self.embedding

    def get_output_embeddings(self):
        return self.lm_head

    def forward(self, *, inputs_embeds, use_cache=False, **_kwargs):
        hidden = torch.tanh(inputs_embeds)
        return SimpleNamespace(
            hidden_states=(hidden,),
            logits=self.lm_head(hidden),
            past_key_values=("tiny-cache",) if use_cache else None,
        )


def tiny_fusion():
    qwen = CharacterTokenizer(offset=1)
    k2 = CharacterTokenizer(offset=129)
    characters = list(range(128))
    alignment = ExactSurfaceAlignment(
        [character + 1 for character in characters],
        [character + 129 for character in characters],
        260,
        260,
    )
    model = CoupledFusion(
        TinyCausalLM(),
        TinyCausalLM(),
        alignment,
        nanbeige_hidden=8,
        k2_hidden=8,
        rank=4,
    )
    return model, qwen, k2


def test_transformer_teacher_forcing_trains_only_the_coupling_bridge():
    train_example = getattr(training, "teacher_forced_hf", None)
    assert callable(train_example), "HF coupled teacher-forcing path is missing"

    model, qwen, k2 = tiny_fusion()
    report = train_example(
        model,
        qwen,
        k2,
        {"messages": [{"role": "user", "content": "say hi"}], "answer": "ok"},
        max_tokens=8,
    )

    assert report.complete is True
    assert report.tokens == 3  # two answer tokens plus EOS
    assert report.loss.ndim == 0 and torch.isfinite(report.loss)
    report.loss.backward()
    bridge_parameters = list(model.bridge_parameters())
    assert bridge_parameters
    assert all(parameter.grad is not None for parameter in bridge_parameters)
    assert all(torch.isfinite(parameter.grad).all() for parameter in bridge_parameters)
    assert all(parameter.grad is None for base in (model.nanbeige, model.k2) for parameter in base.parameters())


def test_transformer_teacher_forcing_rejects_a_truncated_complete_answer():
    train_example = getattr(training, "teacher_forced_hf", None)
    assert callable(train_example), "HF coupled teacher-forcing path is missing"

    model, qwen, k2 = tiny_fusion()
    with pytest.raises(ValueError, match="complete target"):
        train_example(
            model,
            qwen,
            k2,
            {"messages": [{"role": "user", "content": "say hi"}], "answer": "long"},
            max_tokens=2,
        )


def test_bridge_training_updates_only_bridge_and_reports_held_out_loss():
    train = getattr(training, "train_hf_bridge", None)
    assert callable(train), "HF Fusion bridge optimizer loop is missing"

    model, qwen, k2 = tiny_fusion()
    before = [parameter.detach().clone() for parameter in model.bridge_parameters()]
    base_before = [parameter.detach().clone() for tower in (model.nanbeige, model.k2)
                   for parameter in tower.parameters()]
    examples = [
        {"id": "train-a", "messages": [{"role": "user", "content": "a"}], "answer": "bc"},
        {"id": "train-b", "messages": [{"role": "user", "content": "d"}], "answer": "ef"},
    ]
    held_out = [
        {"id": "validation-a", "messages": [{"role": "user", "content": "g"}], "answer": "hi"},
    ]
    report = train(
        model, qwen, k2, examples, held_out,
        epochs=2, max_tokens=8, learning_rate=1e-2, seed=73,
    )

    assert report["epochs"] == 2
    assert len(report["history"]) == 2
    assert report["history"][-1]["train_examples"] == 2
    assert report["history"][-1]["validation_examples"] == 1
    assert all(torch.isfinite(torch.tensor(row["train_loss"])) for row in report["history"])
    assert all(torch.isfinite(torch.tensor(row["validation_loss"])) for row in report["history"])
    assert any(not torch.equal(old, new) for old, new in zip(before, model.bridge_parameters()))
    assert all(torch.equal(old, new) for old, new in zip(
        base_before, (p for tower in (model.nanbeige, model.k2) for p in tower.parameters())
    ))


def test_bridge_training_rejects_benchmark_rows_and_prompt_leakage():
    train = getattr(training, "train_hf_bridge", None)
    assert callable(train), "HF Fusion bridge optimizer loop is missing"
    model, qwen, k2 = tiny_fusion()
    train_rows = [
        {"id": "train-a", "messages": [{"role": "user", "content": "a"}], "answer": "bc"},
    ]
    validation = [
        {"id": "validation-a", "messages": [{"role": "user", "content": "g"}], "answer": "hi"},
    ]
    with pytest.raises(ValueError, match="benchmark IDs"):
        train(
            model, qwen, k2,
            [{**train_rows[0], "id": "HumanEval/0"}], validation,
            epochs=1, max_tokens=8,
        )
    with pytest.raises(ValueError, match="prompt leakage"):
        train(
            model, qwen, k2,
            train_rows,
            [{**validation[0], "messages": train_rows[0]["messages"]}],
            epochs=1, max_tokens=8,
        )


def test_codeforces_split_loader_binds_files_and_extracts_assistant_targets(tmp_path):
    load = getattr(training, "read_hf_split_pair", None)
    assert callable(load), "Audited CodeForces split adapter is missing"
    records = {
        "train": {
            "source": "open-r1/codeforces-cots",
            "source_config": "solutions_py_decontaminated",
            "source_problem_id": "100/A",
            "fingerprint": "train-fingerprint",
            "license": "CC-BY-4.0",
            "messages": [{"role": "user", "content": "solve train"},
                         {"role": "assistant", "content": "print(1)"}],
        },
        "validation": {
            "source": "open-r1/codeforces-cots",
            "source_config": "solutions_py_decontaminated",
            "source_problem_id": "101/A",
            "fingerprint": "validation-fingerprint",
            "license": "CC-BY-4.0",
            "messages": [{"role": "user", "content": "solve validation"},
                         {"role": "assistant", "content": "print(2)"}],
        },
    }
    paths = {}
    hashes = {}
    for split, row in records.items():
        path = tmp_path / f"{split}.jsonl"
        data = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
        path.write_bytes(data)
        paths[split] = path
        hashes[split] = hashlib.sha256(data).hexdigest()

    corpus = load(
        paths["train"], paths["validation"],
        expected_train_sha256=hashes["train"],
        expected_validation_sha256=hashes["validation"],
        expected_source="open-r1/codeforces-cots",
        expected_source_config="solutions_py_decontaminated",
        expected_license="CC-BY-4.0",
    )

    assert len(corpus.train) == 1 and len(corpus.validation) == 1
    assert corpus.train[0]["messages"] == [{"role": "user", "content": "solve train"}]
    assert corpus.train[0]["answer"] == "print(1)"
    assert corpus.train[0]["id"] == "100/A:train-fingerprint"
    with pytest.raises(ValueError, match="SHA-256"):
        load(
            paths["train"], paths["validation"],
            expected_train_sha256="0" * 64,
            expected_validation_sha256=hashes["validation"],
            expected_source="open-r1/codeforces-cots",
            expected_source_config="solutions_py_decontaminated",
            expected_license="CC-BY-4.0",
        )
