import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from hf_bridge import BridgeState, Conflict

OLD = time.time() - 2 * 3600


def environment(root, name):
    folder = Path(root) / 'runtime-setup/environments' / name
    packages = folder / ('Lib/site-packages' if os.name == 'nt' else 'lib/python3.12/site-packages')
    packages.mkdir(parents=True)
    (folder / 'pyvenv.cfg').write_text('home = x\n')
    (packages / 'torch.bin').write_bytes(b'x' * 2048)
    python = folder / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_bytes(b'exe')
    for path in (folder, folder / 'pyvenv.cfg', packages):
        os.utime(path, (OLD, OLD))
    return folder, python


class RuntimeCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state = BridgeState(self.root, self.root)

    def tearDown(self):
        self.state.close()
        self.temporary.cleanup()

    def test_uninstall_removes_the_runtime_only_that_model_used(self):
        identity = 'hf-' + 'c' * 24
        self.state.entries[identity] = {'id': identity, 'name': 'Example text', 'repository': 'owner/Model', 'revision': 'd' * 40, 'files': []}
        folder = self.root / 'models/hub' / identity
        folder.mkdir(parents=True)
        (folder / 'model.safetensors').write_bytes(b'weights')
        used, python = environment(self.root, 'managed-transformers-text-cuda-v1')
        other, _ = environment(self.root, 'moss-comfy-convrot-v1')
        receipts = self.root / 'runtime-setup/receipts'
        receipts.mkdir(parents=True)
        (receipts / (identity + '.json')).write_text(json.dumps({'targetId': identity, 'python': str(python)}))
        listed = {row['id']: row for row in self.state.runtime_inventory()['runtimes']}
        self.assertEqual(set(listed), {'managed-transformers-text-cuda-v1', 'moss-comfy-convrot-v1'})
        self.assertEqual(self.state.runtime_inventory()['modelNames'], {identity: 'Example text'})
        plan = self.state.removal_plan(identity)
        self.state.uninstall(identity, plan['confirmationToken'])
        self.state.runtime_cleanup.join(timeout=10)
        self.assertFalse(used.exists())
        self.assertFalse((receipts / (identity + '.json')).exists())
        self.assertTrue(other.exists(), 'a runtime the uninstalled model never used is left for Settings')

    def test_settings_can_remove_any_runtime_and_prune_unused_ones(self):
        moss, _ = environment(self.root, 'moss-comfy-convrot-v1')
        qwen, _ = environment(self.root, 'qwen-image-21-comfy-v1')
        environment(self.root, 'memory-semantic-v1')
        with self.assertRaisesRegex(ValueError, 'no longer installed'):
            self.state.remove_runtime('missing-runtime')
        result = self.state.remove_runtime('qwen-image-21-comfy-v1')
        self.assertEqual(result['id'], 'qwen-image-21-comfy-v1')
        self.assertFalse(qwen.exists())
        result = self.state.prune_runtimes()
        self.assertEqual([item['id'] for item in result['removed']], ['moss-comfy-convrot-v1'])
        self.assertFalse(moss.exists())
        self.state.runtime_cleanup.join(timeout=10)
        left = self.state.runtime_inventory()
        self.assertEqual([row['id'] for row in left['runtimes']], ['memory-semantic-v1'])
        self.assertEqual(left['pendingBytes'], 0)

    def test_busy_bridge_refuses_removal_and_skips_pruning(self):
        moss, _ = environment(self.root, 'moss-comfy-convrot-v1')
        self.state.progress = {'status': 'running', 'id': 'hf-' + 'e' * 24}
        with self.assertRaises(Conflict):
            self.state.remove_runtime('moss-comfy-convrot-v1')
        self.assertIn('installation', self.state.prune_runtimes()['skipped'])
        self.assertTrue(moss.exists())
        self.state.progress = None


if __name__ == '__main__':
    unittest.main()
