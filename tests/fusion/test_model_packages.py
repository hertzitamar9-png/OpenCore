"""Runtime source packages must be rebuildable without publishing on import."""
import importlib.util
from pathlib import Path
import sys
import types


APP = Path(__file__).resolve().parents[2]


def publisher(monkeypatch):
    offline = types.ModuleType('huggingface_hub')

    def forbidden(*args, **kwargs):
        raise AssertionError('Importing source inventory must not initialize a remote publisher')

    offline.HfApi = forbidden
    offline.CommitOperationAdd = forbidden
    monkeypatch.setitem(sys.modules, 'huggingface_hub', offline)
    spec = importlib.util.spec_from_file_location('model_package_publisher', APP / 'scripts/publish-model-packages.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_inventory_can_be_imported_without_starting_a_remote_client(monkeypatch):
    assert callable(publisher(monkeypatch).collect_runtime_sources)


def test_source_package_keeps_native_headers_and_excludes_generated_payloads(monkeypatch, tmp_path):
    files = {
        'native/twincore.cpp': '#include "head_projection.h"\n',
        'native/head_projection.h': '#pragma once\n',
        'native/CMakeLists.txt': 'project(twincore)\n',
        'requirements-native.txt': 'safetensors==0.8.0\n',
        'adapter.py': '# adapter source\n',
        'native/build/Release/build-info.json': '{}',
        'runtime/llama.dll': 'not package source',
        '__pycache__/adapter.py': 'not source',
        'bridge.safetensors': 'not source',
        'private-conversation.jsonl': 'not source',
    }
    for name, value in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value.encode('utf-8'))
    contents = publisher(monkeypatch).collect_runtime_sources(tmp_path)
    assert contents['native/head_projection.h'] == b'#pragma once\n'
    assert contents['native/CMakeLists.txt'] == b'project(twincore)\n'
    assert contents['native/twincore.cpp'] == b'#include "head_projection.h"\n'
    assert set(contents) == {
        'native/twincore.cpp', 'native/head_projection.h', 'native/CMakeLists.txt',
        'requirements-native.txt', 'adapter.py',
    }
    assert list(contents) == sorted(contents)


def test_actual_twincore_package_contains_its_shared_native_capture_header(monkeypatch):
    contents = publisher(monkeypatch).collect_runtime_sources(APP / 'src-tauri/resources/fusion')
    assert b'#include "head_projection.h"' in contents['native/twincore.cpp']
    assert contents['native/head_projection.h']
    assert contents['native/CMakeLists.txt']
    assert contents['requirements-native.txt']
