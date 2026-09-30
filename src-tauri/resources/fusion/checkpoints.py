"""Exact training sample order and two bounded, resume-only adapter states."""
import hashlib
import json
from pathlib import Path
import random
import re
import shutil
import stat

from .q6_identity import canonical


def _tuple_tree(value):
    return tuple(_tuple_tree(item) for item in value) if isinstance(value, (tuple, list)) else value


class TrainingSchedule:
    def __init__(self, ids, *, epochs, seed, state=None):
        if (not ids or len(set(ids)) != len(ids) or any(not isinstance(item, str) or not item for item in ids)
                or type(epochs) is not int or epochs < 1 or type(seed) is not int):
            raise ValueError('Invalid training schedule')
        self.ids, self.epochs, self.seed = list(ids), epochs, seed
        self.ids_digest = hashlib.sha256(canonical(self.ids)).hexdigest()
        self.rng = random.Random(seed)
        self.epoch, self.index, self.order = 0, 0, list(ids)
        if state is None:
            self.rng.shuffle(self.order)
        else:
            if (state.get('schema') != 1 or state.get('epochs') != epochs or state.get('seed') != seed
                    or state.get('ids_sha256') != self.ids_digest):
                raise ValueError('Resume training schedule identity changed')
            epoch, index, order = state.get('epoch'), state.get('next_index'), state.get('order')
            if (type(epoch) is not int or not 0 <= epoch <= epochs or type(index) is not int
                    or not isinstance(order, list)
                    or (epoch == epochs and (index != 0 or order))
                    or (epoch < epochs and (not 0 <= index <= len(ids)
                        or len(order) != len(ids) or set(order) != set(ids)))):
                raise ValueError('Invalid resume training schedule cursor')
            try:
                self.rng.setstate(_tuple_tree(state['random_state']))
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError('Invalid resume training schedule RNG state') from error
            self.epoch, self.index, self.order = epoch, index, list(order)

    def current(self):
        if self.epoch >= self.epochs:
            return None
        if self.index == len(self.order):
            self.epoch, self.index = self.epoch + 1, 0
            if self.epoch == self.epochs:
                self.order = []
                return None
            self.order = list(self.ids)
            self.rng.shuffle(self.order)
        return self.epoch, self.order[self.index]

    def advance(self, identifier):
        current = self.current()
        if current is None or current[1] != identifier:
            raise ValueError('Training schedule advance did not match the completed example')
        self.index += 1

    def snapshot(self):
        return {'schema': 1, 'epochs': self.epochs, 'seed': self.seed, 'ids_sha256': self.ids_digest,
                'epoch': self.epoch, 'next_index': self.index, 'order': list(self.order),
                'random_state': self.rng.getstate()}


def _owned_directory(root, path):
    root, path = Path(root), Path(path)
    if root.resolve() != root.absolute() or root.is_symlink():
        raise ValueError('Training checkpoint root was redirected')
    root_metadata = root.stat(follow_symlinks=False)
    reparse_flag = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
    if getattr(root_metadata, 'st_file_attributes', 0) & reparse_flag:
        raise ValueError('Training checkpoint root is a reparse point')
    if (not re.fullmatch(r'step-[0-9]{12}', path.name) or path.resolve().parent != root.resolve()
            or path.is_symlink()):
        raise ValueError('Unsafe training checkpoint directory')
    metadata = path.stat(follow_symlinks=False)
    if getattr(metadata, 'st_file_attributes', 0) & reparse_flag:
        raise ValueError('Training checkpoint directory is a reparse point')
    return path.resolve()


class CheckpointStore:
    def __init__(self, output):
        output = Path(output).resolve()
        if not output.parent.is_dir() or output.exists():
            raise ValueError('Use a fresh final output under an existing parent')
        self.root = output.with_name(output.name + '.checkpoints')
        if self.root.exists():
            raise ValueError('Use a fresh output name; earlier training checkpoints were preserved')
        self.owned = []

    def save(self, bridge, binding, training, optimizer):
        from .adapter import save_adapter
        if not self.root.exists():
            if shutil.disk_usage(self.root.parent).free < 100_000_000_000 + 1_048_576:
                raise ValueError('Checkpoint metadata would violate the 100 GB reserve')
            self.root.mkdir(exist_ok=False)
        path = self.root / f"step-{training['steps']:012d}"
        receipt = save_adapter(path, bridge, binding, training, optimizer=optimizer, checkpoint=True)
        _owned_directory(self.root, path)
        pointer = {'schema': 1, 'relative_directory': path.name, 'receipt_sha256': receipt['receipt_sha256']}
        temporary = self.root / 'latest.json.tmp'
        temporary.write_bytes(canonical(pointer) + b'\n')
        temporary.replace(self.root / 'latest.json')
        self.owned.append(path)
        while len(self.owned) > 2:
            obsolete = self.owned.pop(0)
            # Only paths created by this store are eligible. Resolve and verify
            # the exact target immediately before one native Python deletion.
            shutil.rmtree(_owned_directory(self.root, obsolete))
        return path


def resolve_resume(path):
    path = Path(path).resolve()
    if (path / 'receipt.json').is_file():
        return path
    pointer = json.loads((path / 'latest.json').read_text(encoding='utf-8'))
    if pointer.get('schema') != 1 or not re.fullmatch(r'step-[0-9]{12}', pointer.get('relative_directory', '')):
        raise ValueError('Invalid training checkpoint pointer')
    target = _owned_directory(path, path / pointer['relative_directory'])
    receipt = json.loads((target / 'receipt.json').read_text(encoding='utf-8'))
    if receipt.get('checkpoint') is not True or receipt.get('receipt_sha256') != pointer.get('receipt_sha256'):
        raise ValueError('Training checkpoint pointer integrity mismatch')
    return target
