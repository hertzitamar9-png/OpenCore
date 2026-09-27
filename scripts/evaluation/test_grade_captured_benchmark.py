"""Catch replay misalignment and fabricated answers before scoring real captures."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from datetime import date

from inspect_ai import Task
from inspect_ai.dataset import MemoryDataset, Sample
import grade_captured_benchmark as grading

try:
    from grade_captured_benchmark import validate_official_inputs
except ModuleNotFoundError:
    validate_official_inputs = None


class ReplayAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.task = Task(dataset=MemoryDataset([Sample(id="known", input="actual official input", target="not for generation")]))
        self.inputs = {"rows": [{"id": "known", "prompt": "actual official input",
                                "prompt_sha256": hashlib.sha256(b"actual official input").hexdigest()}]}

    def validate(self, data):
        self.assertTrue(callable(validate_official_inputs), "Official replay alignment is not implemented")
        return validate_official_inputs(self.task, data)

    def test_correct_alignment_is_accepted(self):
        self.assertIsNone(self.validate(self.inputs))

    def test_same_id_with_modified_prompt_is_rejected(self):
        self.inputs["rows"][0]["prompt"] = "a different prompt"
        self.inputs["rows"][0]["prompt_sha256"] = hashlib.sha256(b"a different prompt").hexdigest()
        with self.assertRaisesRegex(ValueError, "official prompt"):
            self.validate(self.inputs)

    def test_missing_official_id_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "sample identities"):
            self.validate({"rows": []})


class PinnedTaskTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.source = root / 'official_task.py'
        self.source.write_bytes(b'pinned official scorer source')
        self.provenance = root / 'humaneval-provenance.json'
        self.task = Task(dataset=MemoryDataset([Sample(id='known', input='official input',
                              metadata={'test': 'unchanged tests'}, target='canonical target')]))
        digest = lambda value: hashlib.sha256(value.encode()).hexdigest()
        manifest = {'sample_count': 1, 'task_source_sha256': hashlib.sha256(self.source.read_bytes()).hexdigest(),
                    'rows': [{'id': 'known', 'prompt_sha256': digest('official input'),
                              'tests_sha256': digest('unchanged tests'), 'target_sha256': digest('canonical target')}]}
        self.provenance.write_text(json.dumps(manifest))
        self.inputs = {'dataset_manifest_sha256': hashlib.sha256(self.provenance.read_bytes()).hexdigest()}

    def tearDown(self):
        self.temporary.cleanup()

    def validate(self):
        self.assertTrue(callable(getattr(grading, 'validate_pinned_task', None)), 'Pinned scorer validation is missing')
        return grading.validate_pinned_task(self.task, self.inputs, self.provenance, self.source)

    def test_matching_pinned_tests_and_source_are_accepted(self):
        self.assertTrue(self.validate()['all_recorded_hashes_match'])

    def test_same_prompt_with_changed_tests_is_rejected(self):
        self.task.dataset[0].metadata['test'] = 'different tests'
        with self.assertRaisesRegex(ValueError, 'test hash'):
            self.validate()

    def test_modified_scorer_source_is_rejected(self):
        self.source.write_bytes(b'changed scorer')
        with self.assertRaisesRegex(ValueError, 'scorer source'):
            self.validate()

    def test_modified_target_is_rejected(self):
        self.task.dataset[0].target = 'changed target'
        with self.assertRaisesRegex(ValueError, 'target hash'):
            self.validate()

    def test_changed_grading_metadata_is_rejected_even_with_unchanged_prompt_and_target(self):
        manifest = json.loads(self.provenance.read_text())
        manifest['rows'][0]['metadata_sha256'] = hashlib.sha256(
            json.dumps(self.task.dataset[0].metadata, sort_keys=True, default=str).encode()).hexdigest()
        self.provenance.write_text(json.dumps(manifest))
        self.inputs['dataset_manifest_sha256'] = hashlib.sha256(self.provenance.read_bytes()).hexdigest()
        self.task.dataset[0].metadata['coding'] = {'private_test_cases': 'changed tests'}
        with self.assertRaisesRegex(ValueError, 'metadata hash'):
            self.validate()


class LiveBenchReleaseTests(unittest.TestCase):
    def release(self, inputs, provenance):
        function = getattr(grading, 'livebench_release', None)
        self.assertTrue(callable(function), 'LiveBench release selection is not bound to provenance')
        return function(inputs, provenance)

    def test_newer_public_release_is_selected_from_matching_recorded_metadata(self):
        self.assertEqual(self.release({'release_date': '2024-11-25'}, {'release_date': '2024-11-25'}),
                         date(2024, 11, 25))

    def test_old_capture_without_release_field_preserves_its_provenance_release(self):
        self.assertEqual(self.release({}, {'release_date': '2024-07-26'}), date(2024, 7, 26))

    def test_conflicting_release_metadata_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'release'):
            self.release({'release_date': '2024-11-25'}, {'release_date': '2024-07-26'})

    def test_missing_or_unqualified_release_cannot_silently_use_a_different_dataset(self):
        for provenance in ({}, {'release_date': '2026-01-08'}, {'release_date': 'not-a-date'}):
            with self.subTest(provenance=provenance), self.assertRaisesRegex(ValueError, 'release'):
                self.release({}, provenance)


if __name__ == "__main__":
    unittest.main()
