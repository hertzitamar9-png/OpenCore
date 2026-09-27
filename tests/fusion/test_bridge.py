"""CPU checks for the bridge shared by full Torch and native Q6 towers."""
from pathlib import Path
import sys

import pytest
import torch
from torch import nn

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src-tauri/resources'))
from fusion.alignment import ExactSurfaceAlignment


def fixture():
    try:
        from fusion.bridge import CouplingBridge
    except ModuleNotFoundError:
        pytest.fail('The reusable coupling bridge is not implemented', pytrace=False)
    torch.manual_seed(17)
    bridge = CouplingBridge(ExactSurfaceAlignment([0, 1], [4, 5], 4, 8),
                            nanbeige_hidden=6, k2_hidden=5, rank=4)
    n_head = nn.Linear(6, 4, bias=False).requires_grad_(False)
    k_head = nn.Linear(5, 8, bias=False).requires_grad_(False)
    n_hidden, k_hidden = torch.randn(1, 6), torch.randn(1, 5)
    return bridge, n_head, k_head, n_hidden, k_hidden


def test_both_native_projections_and_logits_contribute_to_one_distribution():
    bridge, n_head, k_head, n_hidden, k_hidden = fixture()
    n_logits, k_logits = n_head(n_hidden), k_head(k_hidden)
    output = bridge(n_hidden, k_hidden, n_logits, k_logits, n_head, k_head)
    assert output.logits.shape == (1, 4)
    assert output.feedback.nanbeige.shape == (1, 6)
    assert output.feedback.k2.shape == (1, 5)
    assert output.feedback.nanbeige.abs().sum() > 0
    assert output.feedback.k2.abs().sum() > 0
    changed = n_logits.clone()
    changed[0, 2] += 2
    assert not torch.allclose(output.logits, bridge(n_hidden, k_hidden, changed, k_logits, n_head, k_head).logits)
    changed_k = k_logits.clone()
    changed_k[0, 4] += 3
    assert not torch.allclose(output.logits, bridge(n_hidden, k_hidden, n_logits, changed_k, n_head, k_head).logits)


def test_adapter_receives_gradients_through_frozen_head_projections():
    bridge, n_head, k_head, n_hidden, k_hidden = fixture()
    output = bridge(n_hidden, k_hidden, n_head(n_hidden), k_head(k_hidden), n_head, k_head)
    torch.nn.functional.cross_entropy(output.logits, torch.tensor([2])).backward()
    parameters = list(bridge.bridge_parameters())
    assert len(parameters) == 7
    assert all(p.grad is not None and torch.isfinite(p.grad).all() for p in parameters)
    assert all(p.grad.abs().sum() > 0 for p in parameters)
    assert all(p.grad is None and not p.requires_grad for p in [*n_head.parameters(), *k_head.parameters()])
    assert n_hidden.grad is None and k_hidden.grad is None


def test_native_projection_failure_does_not_fall_back_to_a_single_brain():
    bridge, n_head, k_head, n_hidden, k_hidden = fixture()
    def broken(_):
        raise RuntimeError('Native K2 head failed')
    with pytest.raises(RuntimeError, match='Native K2 head failed'):
        bridge(n_hidden, k_hidden, n_head(n_hidden), k_head(k_hidden), n_head, broken)


def test_native_geometry_mismatch_is_rejected():
    bridge, n_head, k_head, n_hidden, k_hidden = fixture()
    with pytest.raises(ValueError, match='hidden'):
        bridge(n_hidden[:, :3], k_hidden, n_head(n_hidden), k_head(k_hidden), n_head, k_head)
    with pytest.raises(ValueError, match='vocabulary'):
        bridge(n_hidden, k_hidden, torch.zeros(1, 9), k_head(k_hidden), n_head, k_head)


def test_reference_fusion_uses_the_same_bridge_parameter_names_and_math():
    from test_coupled import make_model
    fusion, nanbeige, k2, *_ = make_model()
    bridge, *_ = fixture()
    bridge.load_state_dict({k: v for k, v in fusion.state_dict().items()
                            if not k.startswith(('nanbeige.', 'k2.'))})
    n_ids, k_ids = torch.tensor([[0, 1]]), torch.tensor([[6]])
    n_hidden, n_logits = fusion._tower(nanbeige, n_ids, None)
    k_hidden, k_logits = fusion._tower(k2, k_ids, None)
    expected = fusion.step(n_ids, k_ids)
    actual = bridge(n_hidden, k_hidden, n_logits, k_logits,
                    nanbeige.get_output_embeddings(), k2.get_output_embeddings())
    torch.testing.assert_close(actual.logits, expected.logits)
    torch.testing.assert_close(actual.feedback.nanbeige, expected.feedback.nanbeige)
    torch.testing.assert_close(actual.feedback.k2, expected.feedback.k2)
