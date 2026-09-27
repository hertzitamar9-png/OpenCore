"""Verify installed official scorer sources against their wheel RECORD hashes."""
import base64
import importlib.metadata
from pathlib import PurePosixPath

from benchmark_capture import file_hash

PACKAGES = {'inspect-evals': ('0.22.0', 'inspect_evals'), 'livebench': ('0.0.4', 'livebench')}


def verify_package_sources(package):
    if package not in PACKAGES:
        raise ValueError('Unknown qualified source package')
    version, prefix = PACKAGES[package]
    distribution = importlib.metadata.distribution(package)
    if distribution.version != version:
        raise ValueError(f'Official source package version differs: {package}')
    files = []
    for entry in distribution.files or []:
        name = str(entry).replace('\\', '/')
        if not name.startswith(prefix + '/') or not name.endswith('.py'):
            continue
        if '..' in PurePosixPath(name).parts:
            raise ValueError('Source path escapes the official package')
        recorded = entry.hash
        if recorded is None or recorded.mode != 'sha256':
            raise ValueError(f'Official source lacks a wheel SHA-256 record: {name}')
        expected = base64.urlsafe_b64decode(recorded.value + '=' * (-len(recorded.value) % 4)).hex()
        source = distribution.locate_file(entry)
        actual = file_hash(source)
        if actual != expected:
            raise ValueError(f'Official scorer source changed after installation: {name}')
        files.append({'path': name, 'bytes': source.stat().st_size, 'sha256': actual})
    if not files:
        raise ValueError(f'No hash-bound official Python sources: {package}')
    return {'package': package, 'version': version, 'files': sorted(files, key=lambda item: item['path'])}


def qualified_sources(benchmark):
    return [verify_package_sources(package) for package in
            (('inspect-evals', 'livebench') if benchmark == 'livebench' else ('inspect-evals',))]


def validate_recorded_sources(records):
    if not isinstance(records, list) or not records:
        raise ValueError('Recorded official source identities are missing')
    packages = [record['package'] for record in records]
    if len(set(packages)) != len(packages):
        raise ValueError('Duplicate official source package identity')
    for record in records:
        if verify_package_sources(record['package']) != record:
            raise ValueError('Official scorer source files differ from recorded provenance')
