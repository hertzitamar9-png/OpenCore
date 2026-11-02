"""Synthetic files and answer fixtures exercise export policy, never model quality."""
import hashlib
import json
from pathlib import Path
import threading

import pytest

from autoloop_vendor.engine import Engine, Settings, paired_improvement_p
from autoloop_vendor.benchmarks import builtin_questions, split_questions
from autoloop_vendor.gguf import GGUFModel, inspect_model
from autoloop_vendor.errors import Cancelled
from conftest import make_gguf


class Answers:
    calls = []
    holdout_errors = 6
    candidate_losses = 0
    def __init__(self, executable, model, log_dir, **options):
        self.model = Path(model)
        self.changed = GGUFModel(model).block_count > 4
        dev, hold = split_questions(builtin_questions())
        self.base_errors = {q.prompt for q in dev[:3] + hold[:self.holdout_errors]}
        self.losses = {q.prompt for q in hold[-self.candidate_losses:]} if self.candidate_losses else set()
        self.questions = {q.prompt: q for q in builtin_questions()}
        self.runtime_proof = {'kind': 'synthetic-answer-fixture', 'authenticated': False}
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def ask(self, prompt, max_tokens=2048):
        q = self.questions[prompt.split('\n\nReply')[0]]
        self.calls.append((str(self.model), q.prompt))
        if (not self.changed and q.prompt in self.base_errors) or self.changed and q.prompt in self.losses:
            return 'INCORRECT'
        return q.answer


@pytest.fixture
def setup(tiny, tmp_path):
    runtime = tmp_path/'owned-llama-server.exe'
    runtime.write_bytes(b'fixture executable is never launched')
    Answers.calls = []
    return Settings(str(tiny), str(tmp_path/'result'), str(runtime), max_variants=3)


def run(settings, server=Answers, **kwargs):
    return Engine(settings, server_factory=server, **kwargs).run()


def test_accepted_export_has_full_original_artifact_runtime_and_benchmark_seals(setup):
    before = Path(setup.model).read_bytes()
    report = run(setup)
    assert report['status'] == 'accepted'
    artifact = report['acceptedArtifact']
    assert hashlib.sha256(Path(artifact['path']).read_bytes()).hexdigest() == artifact['sha256']
    assert report['source']['before']['sha256'] == report['source']['after']['sha256'] == hashlib.sha256(before).hexdigest()
    assert Path(setup.model).read_bytes() == before
    assert artifact['inferenceVerified'] is False, 'Synthetic answers cannot claim actual model inference'
    config = json.loads(Path(artifact['configPath']).read_text())
    assert config['sourceSha256'] == report['source']['before']['sha256']
    assert config['runtimeSha256'] == report['runtime']['before']['sha256']
    assert config['benchmarkSha256'] == report['benchmark']['sha256']
    assert config['weightSharing'] is False and config['training'] is False
    assert report['benchmark']['questionCount'] == 48
    assert not list(Path(setup.output_dir).glob('.autoloop-work-*'))


@pytest.mark.parametrize('wins,p', [(0, 1.0), (1, .5), (4, .0625), (5, .03125), (6, .015625)])
def test_one_sided_exact_paired_gate_has_hand_derived_boundaries(wins, p):
    assert paired_improvement_p(wins, 0) == p


def test_one_sided_gate_counts_regressions():
    assert paired_improvement_p(6, 1) == .0625
    assert paired_improvement_p(7, 1) == .03515625


@pytest.mark.parametrize('holdout_errors,expected', [(0, 'rejected'), (1, 'rejected'), (4, 'rejected'), (5, 'accepted')])
def test_small_confirmation_gains_are_rejected_not_called_improvements(setup, holdout_errors, expected):
    class Limited(Answers): pass
    Limited.holdout_errors = holdout_errors
    report = run(setup, Limited)
    assert report['status'] == expected
    assert bool(report['acceptedArtifact']) == (expected == 'accepted')
    if expected == 'rejected': assert not list(Path(setup.output_dir).glob('*.gguf'))


def test_holdout_regression_is_not_accepted_even_when_significance_passes(setup):
    class Regressing(Answers): pass
    Regressing.holdout_errors = 10
    Regressing.candidate_losses = 1
    report = run(setup, Regressing)
    assert report['status'] == 'rejected'
    assert report['confirmation']['comparison']['losses'] == 1


def test_no_gain_retains_original_and_no_candidate_weights(setup):
    class NoGain(Answers):
        def ask(self, prompt, max_tokens=2048):
            self.changed = False
            return super().ask(prompt, max_tokens)
    report = run(setup, NoGain)
    assert report['status'] == 'rejected'
    assert report['confirmation'] is None
    assert not list(Path(setup.output_dir).glob('*.gguf'))


def test_only_development_winner_ever_sees_confirmation_questions(setup):
    report = run(setup)
    hold = {q.prompt for q in split_questions(builtin_questions())[1]}
    candidate_calls = [(path, prompt) for path, prompt in Answers.calls if Path(path) != Path(setup.model) and prompt in hold]
    assert len(candidate_calls) == 17
    assert len({path for path, _ in candidate_calls}) == 1
    assert report['confirmation']['evaluatedCandidates'] == 1


def test_default_proposals_are_plain_repetition_and_never_damping(setup):
    report = run(setup)
    assert all(not record['experimentalDamping'] for record in report['variants'])


def test_preexisting_candidate_names_are_never_deleted(setup):
    output = Path(setup.output_dir);output.mkdir()
    protected = output/'_candidate_1.gguf';protected.write_bytes(b'preexisting user weights')
    run(setup)
    assert protected.read_bytes() == b'preexisting user weights'


def test_original_named_like_a_candidate_in_output_folder_is_never_deleted(setup):
    source = Path(setup.output_dir);source.mkdir()
    original = make_gguf(source/'_candidate_1.gguf')
    setup.model = str(original)
    before = original.read_bytes()
    run(setup)
    assert original.read_bytes() == before


def test_existing_report_collision_preserves_all_existing_files(setup):
    output = Path(setup.output_dir);output.mkdir()
    report = output/'autoloop-report.json';report.write_text('keep this receipt')
    with pytest.raises(FileExistsError): run(setup)
    assert report.read_text() == 'keep this receipt'


def test_source_change_during_search_invalidates_export_and_does_not_repair_source(setup):
    changed = False
    def progress(event):
        nonlocal changed
        if event['event'] == 'candidate-evaluated' and not changed:
            with Path(setup.model).open('ab') as file: file.write(b'external change')
            changed = True
    report = run(setup, progress=progress)
    assert report['status'] == 'failed' and report['acceptedArtifact'] is None
    assert Path(setup.model).read_bytes().endswith(b'external change')
    assert not list(Path(setup.output_dir).glob('*.gguf'))


def test_cancel_between_questions_removes_only_owned_weights(setup):
    cancel = threading.Event()
    def progress(event):
        if event['event'] == 'question' and event.get('candidate') != 'baseline': cancel.set()
    report = run(setup, progress=progress, cancel=cancel)
    assert report['status'] == 'cancelled'
    assert report['source']['before']['sha256'] == report['source']['after']['sha256']
    assert not list(Path(setup.output_dir).glob('*.gguf'))
    assert not list(Path(setup.output_dir).glob('.autoloop-work-*'))


@pytest.mark.parametrize('architecture', ['qwen35', 'qwen35moe', 'mamba', 'deepseek2'])
def test_unknown_and_hybrid_architectures_are_inventoried_but_not_modified(tmp_path, architecture):
    path = make_gguf(tmp_path/'unsupported.gguf', architecture=architecture)
    result = inspect_model(path)
    assert result['architecture'] == architecture and result['blockCount'] == 4
    assert result['quantTypes'] == [0] and result['supported'] is False and result['reason']


def test_multimodal_modification_requires_manager_compatibility_proof(setup):
    setup.multimodal = True
    report = run(setup)
    assert report['status'] == 'failed'
    assert 'vision' in report['error'].lower() or 'multimodal' in report['error'].lower()
    assert Answers.calls == []


def test_unsupported_structural_architecture_can_run_explicit_text_baseline_only(setup):
    make_gguf(Path(setup.model), architecture='qwen35')
    setup.baseline_only = True
    report = run(setup)
    assert report['status'] == 'baseline_complete'
    assert report['acceptedArtifact'] is None and report['variants'] == []


def test_runtime_binary_change_invalidates_export(setup):
    changed = False
    def progress(event):
        nonlocal changed
        if event['event'] == 'candidate-evaluated' and not changed:
            Path(setup.llama_server).write_bytes(b'changed externally');changed = True
    report = run(setup, progress=progress)
    assert report['status'] == 'failed' and report['acceptedArtifact'] is None


def test_pinned_runtime_hash_mismatch_never_launches_a_runner(setup):
    setup.runtime_sha256 = '0'*64
    report = run(setup)
    assert report['status'] == 'failed' and Answers.calls == []


def test_cancelled_baseline_keeps_every_completed_raw_answer_and_score(setup):
    cancel=threading.Event()
    def progress(event):
        if event['event']=='question':cancel.set()
    report=run(setup,progress=progress,cancel=cancel)
    assert report['status']=='cancelled'
    raw=report['rawResponses']
    rows=[json.loads(line) for line in Path(raw['path']).read_text().splitlines()]
    assert len(rows)==raw['count']==1
    assert rows[0]['candidate']=='baseline' and rows[0]['response'] and type(rows[0]['correct']) is bool
    assert hashlib.sha256(Path(raw['path']).read_bytes()).hexdigest()==raw['sha256']


def test_recognized_vision_metadata_requires_compatibility_even_without_a_caller_flag(setup):
    make_gguf(Path(setup.model),extra_metadata=[('llama.vision.image_size',4,448)])
    assert inspect_model(setup.model)['requiresVisionCompatibility'] is True
    report=run(setup)
    assert report['status']=='failed' and Answers.calls==[]


def test_moe_metadata_does_not_masquerade_as_a_dense_supported_layout(tmp_path):
    path=make_gguf(tmp_path/'moe.gguf',extra_metadata=[('llama.expert_count',4,8)])
    result=inspect_model(path)
    assert result['supported'] is False and 'expert' in result['reason'].lower()
