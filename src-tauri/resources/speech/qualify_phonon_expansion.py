"""Stream and compare every installed five-value record without loading a model.

Usage: python qualify_phonon_expansion.py --model PATH --out receipt.json
Requires NumPy only. It never writes the checkpoint, publisher code, or settings.
"""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import time

from phonon_loading import optimized_five_value


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def qualify(directory):
    import numpy as np
    directory = Path(directory).resolve()
    checkpoint = directory / 'model.fermion'
    publisher = directory / 'fermion_container.py'
    watched = [checkpoint, publisher, directory / 'reference_transformers.py']
    before = {path.name: digest(path) for path in watched}
    spec = importlib.util.spec_from_file_location('phonon_publisher_expansion_fixture', publisher)
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    original, trits, parser = reader._five_value, reader._trits, reader.read_container
    records = []
    started = time.perf_counter()
    with checkpoint.open('rb') as source:
        header_size = int.from_bytes(source.read(8), 'little')
        assert 0 < header_size < checkpoint.stat().st_size
        header = json.loads(source.read(header_size))
        assert header['format'] == reader.FORMAT == 'fermion-five-value-parakeet-v1'
        index = header['index']
        kinds = Counter(entry['k'] for entry in index)
        assert kinds['five_value'] > 0
        assert len({entry['n'] for entry in index}) == len(index)
        assert 8 + header_size + sum(entry['b'] for entry in index) == checkpoint.stat().st_size
        for position, entry in enumerate(index):
            blob = source.read(entry['b'])
            assert len(blob) == entry['b'], entry['n']
            if entry['k'] != 'five_value':
                continue
            shape = tuple(entry['shape'])
            assert len(shape) == 2 and all(size > 0 for size in shape), entry['n']
            def fast():
                return optimized_five_value(blob, shape, trits=trits, original=original)
            # Alternate timing order so the accelerated decoder does not always
            # benefit from running after the original on the same record.
            if position % 2:
                begin = time.perf_counter(); actual = fast(); accelerated_s = time.perf_counter() - begin
                begin = time.perf_counter(); expected = original(blob, shape); publisher_s = time.perf_counter() - begin
            else:
                begin = time.perf_counter(); expected = original(blob, shape); publisher_s = time.perf_counter() - begin
                begin = time.perf_counter(); actual = fast(); accelerated_s = time.perf_counter() - begin
            assert expected.dtype == actual.dtype == np.float16, entry['n']
            assert expected.shape == actual.shape == shape, entry['n']
            assert np.array_equal(expected.view(np.uint16), actual.view(np.uint16)), entry['n']
            records.append({'name': entry['n'], 'shape': list(shape), 'values': int(actual.size),
                'publisherMs': publisher_s * 1000, 'acceleratedMs': accelerated_s * 1000,
                'bitExact': True,
                'nonfiniteLevels': bool(not np.isfinite(np.frombuffer(blob[-4 * shape[0]:], dtype=np.float16)).all())})
            del actual, expected
            if len(records) % 32 == 0:
                print(f'Compared {len(records)}/{kinds["five_value"]} five-value records', flush=True)
        assert source.read(1) == b'', 'trailing bytes'
    assert len(records) == kinds['five_value']
    assert sum(record['values'] for record in records) == sum(
        int(np.prod(entry['shape'])) for entry in index if entry['k'] == 'five_value')
    assert reader._five_value is original and reader._trits is trits and reader.read_container is parser
    after = {path.name: digest(path) for path in watched}
    assert before == after, 'source file changed during qualification'
    publisher_ms = sum(record['publisherMs'] for record in records)
    accelerated_ms = sum(record['acceleratedMs'] for record in records)
    return {'format': header['format'], 'headerBytes': header_size, 'checkpointBytes': checkpoint.stat().st_size,
        'recordKinds': dict(kinds), 'fiveValueRecords': len(records),
        'fiveValueElements': sum(record['values'] for record in records),
        'bitExact': True, 'sourceFilesUnchanged': True, 'sha256': before,
        'publisherExpansionMs': publisher_ms, 'acceleratedExpansionMs': accelerated_ms,
        'expansionSpeedup': publisher_ms / accelerated_ms, 'elapsedSeconds': time.perf_counter() - started,
        'numpyVersion': np.__version__, 'records': records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    receipt = qualify(args.model)
    destination = Path(args.out).resolve()
    model = Path(args.model).resolve()
    assert destination != model and model not in destination.parents, 'receipt must be outside installed model'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in receipt.items() if key != 'records'}, indent=2), flush=True)


if __name__ == '__main__':
    main()
