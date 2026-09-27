import base64
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import source_integrity as integrity


class PackageFile(str):
    pass


class SourceIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.path = root / 'inspect_evals/livebench/scorer.py'
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b'original official scoring implementation')
        self.entry = PackageFile('inspect_evals/livebench/scorer.py')
        self.entry.hash = SimpleNamespace(mode='sha256', value=base64.urlsafe_b64encode(
            hashlib.sha256(self.path.read_bytes()).digest()).decode().rstrip('='))
        self.distribution = SimpleNamespace(version='0.22.0', files=[self.entry],
                                            locate_file=lambda entry: root / str(entry))
        self.mock = patch.object(integrity.importlib.metadata, 'distribution', return_value=self.distribution)
        self.mock.start()

    def tearDown(self):
        self.mock.stop()
        self.temporary.cleanup()

    def test_unchanged_scorer_files_are_recorded(self):
        result = integrity.verify_package_sources('inspect-evals')
        self.assertEqual(result['files'][0]['sha256'], hashlib.sha256(self.path.read_bytes()).hexdigest())
        integrity.validate_recorded_sources([result])

    def test_changed_scorer_is_rejected_without_any_change_to_task_module(self):
        self.path.write_bytes(b'changed score semantics')
        with self.assertRaisesRegex(ValueError, 'changed after installation'):
            integrity.verify_package_sources('inspect-evals')

    def test_missing_wheel_hash_cannot_qualify_as_official_source(self):
        self.entry.hash = None
        with self.assertRaisesRegex(ValueError, 'wheel SHA-256'):
            integrity.verify_package_sources('inspect-evals')

    def test_changed_recorded_source_identity_is_rejected(self):
        recorded = integrity.verify_package_sources('inspect-evals')
        recorded['files'][0]['sha256'] = 'different recorded source'
        with self.assertRaisesRegex(ValueError, 'recorded provenance'):
            integrity.validate_recorded_sources([recorded])

    def test_empty_or_duplicate_source_manifests_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'missing'):
            integrity.validate_recorded_sources([])
        record = integrity.verify_package_sources('inspect-evals')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            integrity.validate_recorded_sources([record, record])

    def test_unknown_or_changed_package_version_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unknown'):
            integrity.verify_package_sources('unknown')
        self.distribution.version = 'changed'
        with self.assertRaisesRegex(ValueError, 'version'):
            integrity.verify_package_sources('inspect-evals')


if __name__ == '__main__':
    unittest.main()
