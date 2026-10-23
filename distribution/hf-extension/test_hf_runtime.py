import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hf_runtime import RuntimeManager, checked_model_files, conversation_key, runtime_plan


class RuntimeSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.resources = self.root / 'resources'
        self.folder = self.root / 'models/hub/hf-fixture'
        self.folder.mkdir(parents=True)
        self.data = b'GGUF-test-fixture'
        self.path = self.folder / 'model-Q4_K_M.gguf'
        self.path.write_bytes(self.data)
        pin = {'filename': self.path.name, 'bytes': len(self.data), 'role': 'model',
               'sha256': hashlib.sha256(self.data).hexdigest(), 'localPath': str(self.path),
               'actualSha256': hashlib.sha256(self.data).hexdigest()}
        self.entry = {'id': 'hf-fixture', 'repository': 'test/model', 'revision': 'a' * 40,
                      'format': 'gguf', 'status': 'installed', 'files': [pin],
                      'localDirectory': str(self.folder), 'contextTokens': 262144,
                      'runtime': {'adapter': 'gguf'}}
        self.receipt = self.folder / 'install-receipt.json'
        self.receipt.write_text(json.dumps({'schema': 1, 'id': self.entry['id'],
            'repository': self.entry['repository'], 'revision': self.entry['revision'], 'files': [pin]}))
        self.entry['receiptPath'] = str(self.receipt)
        (self.root / 'runtime').mkdir()
        (self.root / 'runtime/llama-server.exe').write_bytes(b'owned-engine')
        (self.resources / 'echo').mkdir(parents=True)
        (self.resources / 'echo/echo_server.py').write_text('# installed ECHO')

    def test_files_require_matching_installed_receipt_and_content(self):
        self.assertEqual(checked_model_files(self.entry, self.root), [self.path])
        self.path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed|size|checksum'):
            checked_model_files(self.entry, self.root)

    def test_relocated_or_traversing_files_are_rejected(self):
        self.entry['localDirectory'] = str(self.root.parent)
        with self.assertRaisesRegex(ValueError, 'managed|outside'):
            checked_model_files(self.entry, self.root)

    def test_receipt_for_another_revision_cannot_prove_readiness(self):
        self.entry['revision'] = 'b' * 40
        with self.assertRaisesRegex(ValueError, 'receipt|revision'):
            checked_model_files(self.entry, self.root)

    def test_gguf_plan_bounds_context_threads_and_uses_installed_engine(self):
        plan = runtime_plan(self.entry, self.root, self.resources)
        self.assertEqual(Path(plan['command'][0]), self.root / 'runtime/llama-server.exe')
        self.assertLessEqual(plan['context'], 32768)
        self.assertIn('--fit', plan['command'])
        self.assertIn('--no-webui', plan['command'])
        self.assertNotIn('--trust-remote-code', plan['command'])

    def test_unsupported_format_is_downloadable_but_not_executable(self):
        self.entry['format'] = 'mlx'
        with self.assertRaisesRegex(ValueError, 'runtime|format|adapter'):
            runtime_plan(self.entry, self.root, self.resources)

    def test_fresh_install_uses_the_bundled_engine_without_another_model_download(self):
        (self.root/'runtime/llama-server.exe').unlink()
        bundled=self.resources/'opencore/runtime/llama-server.exe'
        bundled.parent.mkdir(parents=True)
        bundled.write_bytes(b'owned-engine')
        plan=runtime_plan(self.entry,self.root,self.resources)
        self.assertEqual(Path(plan['command'][0]),bundled)

    def test_explicit_nvfp4_checkpoint_does_not_fall_back_to_plain_transformers(self):
        self.entry.update(format='safetensors', quantization='NVFP4')
        with self.assertRaisesRegex(ValueError, 'NVFP4|hardware|runtime'):
            runtime_plan(self.entry, self.root, self.resources)

    def test_echo_conversations_have_separate_stable_identity(self):
        self.assertEqual(conversation_key('chat-1'), conversation_key('chat-1'))
        self.assertNotEqual(conversation_key('chat-1'), conversation_key('chat-2'))
        with self.assertRaises(ValueError):
            conversation_key('')
        with self.assertRaises(ValueError):
            conversation_key('../outside')

    def test_context_admission_prevents_cross_chat_replacement(self):
        runtime = RuntimeManager(self.root, self.resources)
        runtime.set_context('hf-fixture', 'chat-1', 'request-1')
        with self.assertRaisesRegex(RuntimeError, 'active|finish|conversation'):
            runtime.set_context('hf-fixture', 'chat-2', 'request-2')
        runtime.release_context('request-1')
        runtime.set_context('hf-fixture', 'chat-2', 'request-2')
        self.assertEqual(runtime._context['conversationId'], 'chat-2')
        runtime.stop()

    def test_models_does_not_call_unverified_download_runtime_ready(self):
        runtime = RuntimeManager(self.root, self.resources)
        models = runtime.models([self.entry])
        self.assertEqual(models['data'], [])
        runtime.stop()

    def test_stop_releases_only_managed_worker_processes(self):
        runtime = RuntimeManager(self.root, self.resources)
        class Child:
            def __init__(self): self.stopped = False
            def poll(self): return None
            def terminate(self): self.stopped = True
            def wait(self, timeout=None): return 0
        child = Child()
        runtime._children.append(child)
        runtime.stop()
        self.assertTrue(child.stopped)
        self.assertIsNone(runtime._context)


if __name__ == '__main__':
    unittest.main()
