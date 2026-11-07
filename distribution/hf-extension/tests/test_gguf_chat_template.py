"""Only an immutable, locally verified support pin may override a GGUF template."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / 'src-tauri/resources/runtime-setup/hf-extension'))
from hf_runtime import runtime_plan


class GgufChatTemplateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / 'models/hub/fixture'
        self.folder.mkdir(parents=True)
        self.template = b'{{ bos_token }}{% for m in messages %}{{ m.content }}{% endfor %}'
        self.path = self.folder / 'chat_template'
        self.path.write_bytes(self.template)
        blob = hashlib.sha1(b'blob ' + str(len(self.template)).encode() + b'\0' + self.template).hexdigest()
        pin = {'repository': 'publisher/model', 'revision': 'a' * 40, 'filename': 'chat_template',
               'bytes': len(self.template), 'gitBlobSha1': blob, 'sha256': None, 'pinned': True, 'role': 'support'}
        self.entry = {'id': 'fixture', 'repository': 'publisher/model', 'revision': 'a' * 40,
                      'format': 'gguf', 'modelType': 'lfm3', 'contextTokens': 8192,
                      'localDirectory': str(self.folder), 'runtime': {'adapter': 'gguf'},
                      'files': [{'filename': 'model.gguf', 'role': 'model'}, pin],
                      'chatTemplate': {'repo': 'publisher/model', 'revision': 'a' * 40, 'filename': 'chat_template',
                                       'bytes': len(self.template), 'sha256': hashlib.sha256(self.template).hexdigest(),
                                       'gitBlobSha1': blob}}

    def plan(self, entry=None):
        return runtime_plan(entry or self.entry, self.root, self.root / 'resources', prefer_gpu=False)

    def test_pinned_template_is_selected_by_exact_managed_file(self):
        command = self.plan()['command']
        self.assertIn('--chat-template-file', command)
        self.assertEqual(command[command.index('--chat-template-file') + 1], str(self.path.resolve()))
        self.assertIn('--jinja', command)

    def test_ordinary_model_has_no_override(self):
        entry = copy.deepcopy(self.entry)
        entry.pop('chatTemplate')
        self.assertNotIn('--chat-template-file', self.plan(entry)['command'])

    def test_template_identity_cannot_be_taken_from_another_model_revision_or_support_file(self):
        for field, value in [('repo', 'attacker/model'), ('revision', 'b' * 40), ('filename', 'other_template'),
                             ('sha256', '0' * 64), ('bytes', len(self.template) + 1), ('gitBlobSha1', '0' * 40)]:
            with self.subTest(field=field):
                entry = copy.deepcopy(self.entry)
                entry['chatTemplate'][field] = value
                with self.assertRaisesRegex(ValueError, 'template'):
                    self.plan(entry)

    def test_missing_corrupt_unpinned_or_wrong_role_template_is_rejected(self):
        for mutation in ('missing', 'corrupt', 'unpinned', 'role'):
            with self.subTest(mutation=mutation):
                entry = copy.deepcopy(self.entry)
                self.path.write_bytes(self.template)
                if mutation == 'missing':
                    self.path.unlink()
                elif mutation == 'corrupt':
                    self.path.write_bytes(b'x' * len(self.template))
                elif mutation == 'unpinned':
                    entry['files'][1]['pinned'] = False
                else:
                    entry['files'][1]['role'] = 'model'
                with self.assertRaisesRegex(ValueError, 'template'):
                    self.plan(entry)

    def test_template_paths_are_never_interpreted_as_commands_or_allowed_to_escape(self):
        for filename in ('../chat_template', '/tmp/chat_template', 'C:\\chat_template', 'a\\chat_template'):
            with self.subTest(filename=filename):
                entry = copy.deepcopy(self.entry)
                entry['chatTemplate']['filename'] = filename
                entry['files'][1]['filename'] = filename
                with self.assertRaises(ValueError):
                    self.plan(entry)

    def test_empty_overlarge_binary_or_invalid_utf8_template_is_rejected(self):
        for value in (b'', b'x' * 1048576, b'\x00template', b'\xff\xfe'):
            with self.subTest(length=len(value)):
                entry = copy.deepcopy(self.entry)
                self.path.write_bytes(value)
                blob = hashlib.sha1(b'blob ' + str(len(value)).encode() + b'\0' + value).hexdigest()
                entry['files'][1].update(bytes=len(value), gitBlobSha1=blob)
                entry['chatTemplate'].update(bytes=len(value), sha256=hashlib.sha256(value).hexdigest(), gitBlobSha1=blob)
                with self.assertRaisesRegex(ValueError, 'template'):
                    self.plan(entry)


if __name__ == '__main__':
    unittest.main()
