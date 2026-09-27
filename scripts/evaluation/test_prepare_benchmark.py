"""Dataset preparation must reject drift before writing capture inputs."""
from datetime import date
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import prepare_benchmark as preparation


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / 'official.py'
        self.source.write_bytes(b'pinned task source')
        self.module = SimpleNamespace(__file__=str(self.source),
                                     HUMANEVAL_DATASET_REVISION=preparation.HUMANEVAL_REVISION)
        self.sample = SimpleNamespace(id='HumanEval/0', input='official prompt', target='canonical answer',
                                      metadata={'test': 'official tests'})

    def tearDown(self):
        self.temporary.cleanup()

    def verify(self):
        with patch.dict(preparation.TASK_HASHES, {'humaneval': hashlib.sha256(self.source.read_bytes()).hexdigest()}):
            preparation.verify_environment('humaneval', self.module)

    def test_changed_package_version_is_rejected(self):
        with patch.object(preparation.importlib.metadata, 'version', return_value='changed'):
            with self.assertRaisesRegex(ValueError, 'version'):
                self.verify()

    def test_changed_dataset_revision_is_rejected_even_with_matching_source_hash(self):
        self.module.HUMANEVAL_DATASET_REVISION = 'a different dataset'
        with self.assertRaisesRegex(ValueError, 'revision'):
            self.verify()

    def test_changed_scorer_source_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'source'):
            preparation.verify_environment('humaneval', self.module)

    def test_livebench_scorers_cannot_silently_use_a_different_git_revision(self):
        distribution = SimpleNamespace(read_text=lambda _: json.dumps({'vcs_info': {'commit_id': 'changed'}}))
        with patch.object(preparation.importlib.metadata, 'distribution', return_value=distribution):
            with self.assertRaisesRegex(ValueError, 'scorer revision'):
                preparation.verify_livebench_revision()

    def test_duplicate_ids_cannot_qualify_as_a_full_dataset(self):
        with self.assertRaisesRegex(ValueError, 'distinct'):
            preparation.validate_samples('humaneval', [self.sample] * 164)

    def test_missing_livebench_category_is_rejected_even_at_full_task_count(self):
        rows = [SimpleNamespace(id=str(i), metadata={'category': 'math'}) for i in range(1000)]
        with self.assertRaisesRegex(ValueError, 'categories'):
            preparation.validate_samples('livebench', rows)

    def test_preparation_preserves_prompt_and_records_test_target_and_metadata_hashes(self):
        task = SimpleNamespace(dataset=[self.sample])
        self.module.humaneval = lambda **_: task
        output = self.root / 'prepared'
        with patch.object(preparation.importlib, 'import_module', return_value=self.module), \
             patch.object(preparation, 'verify_environment', return_value=[]), patch.object(preparation, 'validate_samples'):
            preparation.prepare('humaneval', output)
        inputs = json.loads((output / 'humaneval-inputs.json').read_text())
        provenance = json.loads((output / 'humaneval-provenance.json').read_text())
        digest = lambda text: hashlib.sha256(text.encode()).hexdigest()
        self.assertEqual(inputs['rows'][0]['prompt'], 'official prompt')
        self.assertEqual(inputs['dataset_manifest_sha256'], preparation.file_hash(output / 'humaneval-provenance.json'))
        row = provenance['rows'][0]
        self.assertEqual(row['tests_sha256'], digest('official tests'))
        self.assertEqual(row['target_sha256'], digest('canonical answer'))
        self.assertEqual(row['metadata_sha256'], digest(json.dumps(self.sample.metadata, sort_keys=True, default=str)))
        self.assertFalse(provenance['model_quality_measured'])

    def test_existing_output_is_not_replaced_or_reloaded(self):
        output = self.root / 'existing'
        output.mkdir()
        original = output / 'humaneval-inputs.json'
        original.write_bytes(b'previous evidence')
        with patch.object(preparation.importlib, 'import_module') as module:
            with self.assertRaises(FileExistsError):
                preparation.prepare('humaneval', output)
            module.assert_not_called()
        self.assertEqual(original.read_bytes(), b'previous evidence')

    def test_unqualified_livebench_release_is_rejected_before_loading_data(self):
        with patch.object(preparation.importlib, 'import_module') as module:
            with self.assertRaisesRegex(ValueError, 'release'):
                preparation.prepare('livebench', self.root / 'new', date(2026, 1, 8))
            module.assert_not_called()


if __name__ == '__main__':
    unittest.main()
