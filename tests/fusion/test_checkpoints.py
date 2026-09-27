"""Bounded CPU training recovery, separate from model quality qualification."""
import json
from pathlib import Path
import sys

import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src-tauri/resources'))


def test_incomplete_checkpoint_cannot_be_used_for_inference(tmp_path):
    from fusion import adapter
    from test_training import trained, bridge
    model, optimizer, binding, evidence = trained(tmp_path)
    path = tmp_path / 'checkpoint'
    adapter.save_adapter(path, model, binding, evidence, optimizer=optimizer, checkpoint=True)
    unmodified = bridge()
    before = adapter.tensor_fingerprint(unmodified)
    with pytest.raises(ValueError, match='checkpoint|completed'):
        adapter.load_adapter(path, unmodified, binding)
    assert adapter.tensor_fingerprint(unmodified) == before
    with pytest.raises(ValueError, match='checkpoint|completed'):
        adapter.inspect_adapter(path, binding)


def test_checkpoint_resume_restores_exact_weights_and_optimizer(tmp_path):
    from fusion import adapter
    from test_training import trained, bridge
    model, optimizer, binding, evidence = trained(tmp_path)
    path = tmp_path / 'checkpoint'
    adapter.save_adapter(path, model, binding, evidence, optimizer=optimizer, checkpoint=True)
    restored = bridge()
    resumed = torch.optim.AdamW(restored.bridge_parameters(), lr=0.001)
    receipt = adapter.load_adapter(path, restored, binding, optimizer=resumed)
    assert receipt['checkpoint'] is True
    assert adapter.tensor_fingerprint(restored) == adapter.tensor_fingerprint(model)
    for old, new in zip(optimizer.state.values(), resumed.state.values()):
        for key in old:
            torch.testing.assert_close(old[key], new[key], rtol=0, atol=0)


def test_checkpoint_without_optimizer_fails_before_writing(tmp_path):
    from fusion import adapter
    from test_training import trained
    model, _, binding, evidence = trained(tmp_path)
    path = tmp_path / 'checkpoint'
    with pytest.raises(ValueError, match='optimizer'):
        adapter.save_adapter(path, model, binding, evidence, checkpoint=True)
    assert not path.exists()


def test_stale_checkpoint_validation_cannot_be_promoted_to_a_completed_adapter(tmp_path):
    from fusion import adapter
    from test_training import trained
    model, optimizer, binding, evidence = trained(tmp_path)
    evidence = {**evidence, 'validation_step': 0, 'validation_is_current': False}
    final = tmp_path / 'final'
    with pytest.raises(ValueError, match='validation'):
        adapter.save_adapter(final, model, binding, evidence, optimizer=optimizer)
    assert not final.exists()
    adapter.save_adapter(tmp_path / 'checkpoint', model, binding, evidence, optimizer=optimizer, checkpoint=True)


def schedule_module():
    try:
        from fusion import checkpoints
    except ImportError:
        pytest.fail('Bounded training checkpoints and exact sample scheduling are missing')
    return checkpoints


def drain(schedule):
    items = []
    while schedule.current() is not None:
        item = schedule.current()
        items.append(item)
        schedule.advance(item[1])
    return items


def test_resume_keeps_the_exact_remaining_order_across_epochs():
    checkpoints = schedule_module()
    schedule = checkpoints.TrainingSchedule(['a', 'b', 'c', 'd'], epochs=3, seed=7)
    for _ in range(3):
        schedule.advance(schedule.current()[1])
    state = json.loads(json.dumps(schedule.snapshot()))
    expected = drain(schedule)
    restored = checkpoints.TrainingSchedule(['a', 'b', 'c', 'd'], epochs=3, seed=7, state=state)
    assert drain(restored) == expected
    assert restored.current() is None


@pytest.mark.parametrize('change', ['epochs', 'seed', 'ids'])
def test_resume_rejects_a_changed_schedule(change):
    checkpoints = schedule_module()
    state = checkpoints.TrainingSchedule(['a', 'b'], epochs=2, seed=7).snapshot()
    ids, epochs, seed = ['a', 'b'], 2, 7
    if change == 'epochs': epochs = 3
    if change == 'seed': seed = 8
    if change == 'ids': ids = ['a', 'c']
    with pytest.raises(ValueError, match='schedule'):
        checkpoints.TrainingSchedule(ids, epochs=epochs, seed=seed, state=state)


def test_store_keeps_two_recoverable_states_and_leaves_other_files(tmp_path):
    checkpoints = schedule_module()
    from test_training import trained, TinyFrozenNative, rows
    from fusion.training import teacher_forced
    model, optimizer, binding, evidence = trained(tmp_path)
    unrelated = tmp_path / 'other-model.safetensors'
    unrelated.write_bytes(b'keep this')
    store = checkpoints.CheckpointStore(tmp_path / 'final-adapter')
    saved = []
    for step in range(1, 4):
        if step > 1:
            optimizer.zero_grad(set_to_none=True)
            teacher_forced(TinyFrozenNative(), model, rows()[0], max_tokens=8).loss.backward()
            optimizer.step()
        evidence = {**evidence, 'steps': step, 'tokens': step * 2}
        saved.append(store.save(model, binding, evidence, optimizer))
    assert not saved[0].exists()
    assert saved[1].is_dir() and saved[2].is_dir()
    assert checkpoints.resolve_resume(store.root) == saved[2]
    assert unrelated.read_bytes() == b'keep this'
    assert len(list(store.root.glob('step-*'))) == 2


def test_resume_pointer_cannot_escape_its_checkpoint_store(tmp_path):
    checkpoints = schedule_module()
    root = tmp_path / 'checkpoints'
    root.mkdir()
    (root / 'latest.json').write_text(json.dumps({'schema': 1,
        'relative_directory': '../outside', 'receipt_sha256': 'a' * 64}), encoding='utf-8')
    with pytest.raises(ValueError, match='checkpoint'):
        checkpoints.resolve_resume(root)


def test_failed_new_checkpoint_preserves_the_previous_resume_pointer(tmp_path, monkeypatch):
    checkpoints = schedule_module()
    from fusion import adapter
    from test_training import trained
    model, optimizer, binding, evidence = trained(tmp_path)
    store = checkpoints.CheckpointStore(tmp_path / 'final')
    previous = store.save(model, binding, evidence, optimizer)
    def fail(*args, **kwargs):
        raise OSError('Simulated incomplete checkpoint write')
    monkeypatch.setattr(adapter, 'save_adapter', fail)
    with pytest.raises(OSError, match='incomplete'):
        store.save(model, binding, {**evidence, 'steps': 2}, optimizer)
    assert checkpoints.resolve_resume(store.root) == previous
    assert previous.is_dir()
