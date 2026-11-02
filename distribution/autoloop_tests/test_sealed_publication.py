import hashlib
import json
from pathlib import Path

import pytest

from autoloop_vendor.gguf import GGUFModel,inspect_model
from autoloop_vendor.engine import Settings
from conftest import make_gguf
from test_engine_safety import Answers,run
import threading


@pytest.mark.parametrize('context',[1024,2048,8192,262144])
def test_inspection_reads_published_context_and_duplication_preserves_it(tmp_path,context):
    path=make_gguf(tmp_path/'source.gguf',context=context)
    assert inspect_model(path)['contextTokens']==context
    output=GGUFModel(path).build(tmp_path/'copy.gguf',[0,1,1,2,3])
    assert inspect_model(output)['contextTokens']==context


def test_unspecified_context_is_unknown_instead_of_a_runtime_claim(tiny):
    assert inspect_model(tiny)['contextTokens'] is None


def test_failed_final_copy_progress_callback_cannot_leave_an_untracked_published_weight(tiny,tmp_path):
    path=tmp_path/'candidate.gguf'
    def progress(message):
        if not message.startswith('Writing tensors:'):raise RuntimeError('The owner cancelled progress delivery')
    with pytest.raises(RuntimeError):GGUFModel(tiny).build(path,[0,1,1,2,3],progress)
    assert not path.exists()
    assert not list(tmp_path.glob('.autoloop_*.gguf.tmp'))


def test_benchmark_seal_matches_the_actual_saved_manifest_bytes(tiny,tmp_path):
    runtime=tmp_path/'runner.exe';runtime.write_bytes(b'not executed')
    settings=Settings(str(tiny),str(tmp_path/'result'),str(runtime))
    report=run(settings)
    assert hashlib.sha256(Path(report['benchmark']['manifestPath']).read_bytes()).hexdigest()==report['benchmark']['sha256']


def test_modified_frozen_benchmark_manifest_invalidates_acceptance(tiny,tmp_path):
    runtime=tmp_path/'runner.exe';runtime.write_bytes(b'not executed')
    settings=Settings(str(tiny),str(tmp_path/'result'),str(runtime))
    changed=False
    def progress(event):
        nonlocal changed
        if event['event']=='candidate-evaluated' and not changed:
            path=Path(settings.output_dir)/'benchmark-manifest.json'
            data=json.loads(path.read_text());data['holdout'][0]['answer']='tampered expected answer'
            path.write_text(json.dumps(data));changed=True
    report=run(settings,progress=progress)
    assert report['status']=='failed' and report['acceptedArtifact'] is None
    assert not list(Path(settings.output_dir).glob('*.gguf'))


def test_cancel_racing_final_publication_removes_the_owned_export_only(tiny,tmp_path,monkeypatch):
    from autoloop_vendor import engine
    runtime=tmp_path/'runner.exe';runtime.write_bytes(b'not executed')
    settings=Settings(str(tiny),str(tmp_path/'result'),str(runtime))
    cancel=threading.Event();publish=engine.publish_no_replace
    def cancelling_publish(source,destination):
        publish(source,destination);cancel.set()
    monkeypatch.setattr(engine,'publish_no_replace',cancelling_publish)
    report=run(settings,cancel=cancel)
    assert report['status']=='cancelled' and report['acceptedArtifact'] is None
    assert tiny.is_file() and not list(Path(settings.output_dir).glob('*.gguf'))
