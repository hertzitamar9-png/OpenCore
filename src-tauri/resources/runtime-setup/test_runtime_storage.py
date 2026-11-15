import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

import runtime_storage as storage

LATER = time.time() + 10 * 3600


def site(environment):
    folder = Path(environment) / ('Lib/site-packages' if os.name == 'nt' else 'lib/python3.12/site-packages')
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def python(environment):
    return Path(environment) / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def make_env(root, name, size=1000, speech=False):
    environment = Path(root) / ('speech' if speech else 'runtime-setup/environments') / name
    environment.mkdir(parents=True)
    (environment / 'pyvenv.cfg').write_text('home = x\n')
    packages = site(environment)
    (packages / 'torch.bin').write_bytes(b'x' * size)
    python(environment).parent.mkdir(parents=True, exist_ok=True)
    python(environment).write_bytes(b'exe')
    return environment


def share(borrower, lender, extended=False):
    path = str(site(lender))
    if extended and os.name == 'nt':
        path = '\\\\?\\' + path
    marker = site(borrower) / 'opencore-shared-torch.pth'
    marker.write_text('# shared\n' + path + '\n')
    return marker


def receipt(root, target, owner=None, **fields):
    folder = Path(root) / 'runtime-setup/receipts'
    folder.mkdir(parents=True, exist_ok=True)
    value = {'targetId': target, **fields}
    if owner is not None:
        value.setdefault('python', str(python(owner)))
    path = folder / f'{target}.json'
    path.write_text(json.dumps(value))
    return path


def by_id(rows):
    return {row['id']: row for row in rows}


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_statuses_follow_installed_models_features_and_demand(self):
        make_env(self.root, 'qwen-image-21-comfy-v1')
        make_env(self.root, 'moss-comfy-convrot-v1')
        make_env(self.root, 'memory-semantic-v1')
        make_env(self.root, 'mystery-env')
        make_env(self.root, 'tts-speech-v1')
        make_env(self.root, 'whisper-venv', speech=True)
        rows = by_id(storage.inventory(self.root, installed={'qwen-image-21-uc', 'whisper-large-v3'},
                                       demand={'tts-speech-v1': {'hf-voice'}}, now=LATER))
        self.assertEqual(rows['qwen-image-21-comfy-v1']['status'], 'in-use')
        self.assertEqual(rows['qwen-image-21-comfy-v1']['installedUsers'], ['qwen-image-21-uc'])
        self.assertEqual(rows['moss-comfy-convrot-v1']['status'], 'unused')
        self.assertEqual(rows['memory-semantic-v1']['status'], 'feature')
        self.assertEqual(rows['mystery-env']['status'], 'unknown')
        self.assertEqual(rows['tts-speech-v1']['status'], 'in-use')
        self.assertEqual(rows['tts-speech-v1']['label'], 'Speech generation runtime')
        self.assertEqual(rows['speech:whisper-venv']['status'], 'in-use')
        self.assertGreater(rows['moss-comfy-convrot-v1']['bytes'], 1000)
        self.assertFalse(rows['moss-comfy-convrot-v1']['recentlyChanged'])

    def test_engine_builds_are_listed_and_unused_without_demand(self):
        for relative in ('runtime-setup/engines/lfm3-cuda12-v1', 'runtime-setup/engines/cuda12-libs-v1',
                         'runtime-setup/engines/lfm3-vulkan-v1.previous-0123abcd',
                         'runtime-setup/runtimes/iris-sdcpp-v1', 'runtime-setup/runtimes/iris-sdcpp-cuda12-v1'):
            folder = self.root / relative
            folder.mkdir(parents=True)
            (folder / 'engine.bin').write_bytes(b'x' * 100)
        rows = by_id(storage.inventory(self.root, demand={'engine:lfm3-cuda12-v1': {'hf-text'},
                                                          'engine:cuda12-libs-v1': {'hf-text'},
                                                          'engine:iris-sdcpp-cuda12-v1': {'hf-image'}},
                                       installed={'hf-text', 'hf-image'}, now=LATER))
        self.assertEqual(rows['engine:lfm3-cuda12-v1']['status'], 'in-use')
        self.assertEqual(rows['engine:lfm3-cuda12-v1']['label'], 'Lfm3 text engine (CUDA 12)')
        self.assertEqual(rows['engine:cuda12-libs-v1']['label'], 'CUDA 12 libraries')
        self.assertEqual(rows['engine:iris-sdcpp-cuda12-v1']['status'], 'in-use')
        self.assertEqual(rows['engine:iris-sdcpp-v1']['label'], 'Image engine (Vulkan)')
        self.assertEqual(rows['engine:iris-sdcpp-v1']['status'], 'unused')
        old = rows['engine:lfm3-vulkan-v1.previous-0123abcd']
        self.assertEqual(old['status'], 'unused')
        self.assertTrue(old['label'].endswith(' - old copy'))

    def test_engine_labels(self):
        self.assertEqual(storage.engine_label('lfm3-cpu-v1'), 'Lfm3 text engine (CPU)')
        self.assertEqual(storage.engine_label('iris-sdcpp-rocm-v1'), 'Image engine (ROCm)')
        self.assertEqual(storage.engine_label('cuda13-libs-v1'), 'CUDA 13 libraries')

    def test_receipts_attribute_runtimes_without_recipe_models(self):
        environment = make_env(self.root, 'managed-transformers-text-cuda-v1')
        receipt(self.root, 'hf-abc', environment)
        rows = by_id(storage.inventory(self.root, now=LATER))
        self.assertEqual(rows['managed-transformers-text-cuda-v1']['status'], 'unused')
        self.assertIn('hf-abc', rows['managed-transformers-text-cuda-v1']['users'])
        rows = by_id(storage.inventory(self.root, installed={'hf-abc'}, now=LATER))
        self.assertEqual(rows['managed-transformers-text-cuda-v1']['status'], 'in-use')

    def test_receipt_environment_field_names_the_runtime(self):
        make_env(self.root, 'tts-speech-v1')
        receipt(self.root, 'hub-tts-runtime', environment='tts-speech-v1')
        rows = by_id(storage.inventory(self.root, now=LATER))
        self.assertEqual(rows['tts-speech-v1']['status'], 'unused')
        self.assertTrue(rows['tts-speech-v1']['_receipts'])

    def test_lender_of_a_needed_runtime_is_shared_not_unused(self):
        lender = make_env(self.root, 'phonon-venv', speech=True)
        borrower = make_env(self.root, 'diffusers-images-v1')
        share(borrower, lender, extended=True)
        rows = by_id(storage.inventory(self.root, demand={'diffusers-images-v1': {'hf-image'}}, now=LATER))
        self.assertEqual(rows['speech:phonon-venv']['status'], 'shared')
        self.assertEqual(rows['speech:phonon-venv']['sharedWith'], ['diffusers-images-v1'])
        self.assertEqual(rows['diffusers-images-v1']['borrowsFrom'], ['speech:phonon-venv'])
        rows = by_id(storage.inventory(self.root, now=LATER))
        self.assertEqual(rows['speech:phonon-venv']['status'], 'unused')

    def test_lender_of_a_feature_runtime_is_kept(self):
        lender = make_env(self.root, 'moss-comfy-convrot-v1')
        share(make_env(self.root, 'memory-semantic-v1'), lender)
        rows = by_id(storage.inventory(self.root, now=LATER))
        self.assertEqual(rows['moss-comfy-convrot-v1']['status'], 'shared')

    def test_marker_pointing_at_itself_or_missing_folder(self):
        environment = make_env(self.root, 'qwen-image-21-comfy-v1')
        share(environment, environment)
        rows = by_id(storage.inventory(self.root, now=LATER))
        self.assertEqual(rows['qwen-image-21-comfy-v1']['borrowsFrom'], [])
        self.assertFalse(rows['qwen-image-21-comfy-v1']['missingShared'])
        (site(environment) / 'opencore-shared-torch.pth').write_text(str(self.root / 'gone' / 'site-packages'))
        rows = by_id(storage.inventory(self.root, now=LATER))
        self.assertTrue(rows['qwen-image-21-comfy-v1']['missingShared'])

    def test_public_rows_hide_internal_fields_and_total_sizes(self):
        make_env(self.root, 'moss-comfy-convrot-v1', size=5000)
        make_env(self.root, 'mystery-env', size=100)
        value = storage.public(storage.inventory(self.root, now=LATER))
        self.assertEqual(value['schema'], 1)
        self.assertFalse(any(key.startswith('_') for row in value['runtimes'] for key in row))
        self.assertGreater(value['unusedBytes'], 5000)
        self.assertGreater(value['totalBytes'], value['unusedBytes'])
        json.dumps(value)

    def test_linked_folders_are_neither_listed_nor_counted(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'big.bin').write_bytes(b'x' * 50000)
        environment = make_env(self.root, 'moss-comfy-convrot-v1', size=10)
        environments = self.root / 'runtime-setup/environments'
        if not link_folder(environment / 'linked', outside) or not link_folder(environments / 'linked-env', outside):
            self.skipTest('Folder links are not available here')
        rows = by_id(storage.inventory(self.root, now=LATER))
        self.assertNotIn('linked-env', rows)
        self.assertLess(rows['moss-comfy-convrot-v1']['bytes'], 50000)
        storage.remove(self.root, 'moss-comfy-convrot-v1')
        self.assertTrue((outside / 'big.bin').is_file())

    def isolated_home(self):
        home = self.root / 'home'
        home.mkdir(exist_ok=True)
        environment = {key: value for key, value in os.environ.items() if key != 'OPENCORE_WHISPER_MODEL'}
        environment.update(USERPROFILE=str(home), HOME=str(home))
        return home, mock.patch.dict(os.environ, environment, clear=True)

    def test_installed_native_models_reads_install_receipts(self):
        _home, isolated = self.isolated_home()
        with isolated:
            folder = self.root / 'models/receipts'
            folder.mkdir(parents=True)
            (folder / 'model-dory-4b.json').write_text('{}')
            (folder / 'other.json').write_text('{}')
            self.assertEqual(storage.installed_native_models(self.root), {'dory-4b'})
            self.assertEqual(storage.installed_native_models(self.root / 'missing'), set())

    def test_speech_models_on_disk_keep_their_runtime(self):
        home, isolated = self.isolated_home()
        with isolated:
            whisper = make_env(self.root, 'whisper-venv', 10, speech=True)
            phonon = make_env(self.root, 'phonon-venv', 10, speech=True)
            share(whisper, phonon, extended=False)
            rows = by_id(storage.inventory(self.root, storage.installed_native_models(self.root)))
            self.assertEqual(rows['speech:whisper-venv']['status'], 'unused')
            weights = self.root / 'speech/phonon-2/model.fermion'
            weights.parent.mkdir(parents=True)
            weights.write_bytes(b'w')
            self.assertEqual(storage.installed_native_models(self.root), {'phonon-2'})
            checkpoint = home / 'OpenCore-Model-Test/asr/whisper-large-v3-turbo'
            checkpoint.mkdir(parents=True)
            (checkpoint / 'config.json').write_text('{}')
            installed = storage.installed_native_models(self.root)
            self.assertEqual(installed, {'phonon-2', 'whisper-large-v3-turbo'})
            rows = by_id(storage.inventory(self.root, installed))
            self.assertEqual(rows['speech:whisper-venv']['status'], 'in-use')
            self.assertEqual(rows['speech:phonon-venv']['status'], 'in-use')
            self.assertEqual(storage.prune(self.root, installed, grace=0)['removed'], [])


def link_folder(link, target):
    try:
        if os.name == 'nt':
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
        else:
            os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, ImportError, AttributeError):
        return False


class RemovalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_remove_deletes_folder_and_receipts_and_detaches_borrowers(self):
        lender = make_env(self.root, 'phonon-venv', speech=True, size=4000)
        speech_receipt = self.root / 'speech/phonon-runtime.json'
        speech_receipt.write_text('{}')
        borrower = make_env(self.root, 'diffusers-images-v1')
        marker = share(borrower, lender, extended=True)
        borrower_receipt = receipt(self.root, 'hub-image-runtime', borrower)
        result = storage.remove(self.root, 'speech:phonon-venv')
        self.assertFalse(lender.exists())
        self.assertFalse(speech_receipt.exists())
        self.assertFalse(marker.exists())
        self.assertFalse(borrower_receipt.exists())
        self.assertTrue(borrower.exists())
        self.assertEqual(result['invalidated'], ['diffusers-images-v1'])
        self.assertGreater(result['bytes'], 4000)
        self.assertEqual(list(storage.trash_folder(self.root).iterdir()), [])

    def test_remove_unknown_runtime_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'no longer installed'):
            storage.remove(self.root, 'nothing-here')

    def test_running_setup_blocks_removal_and_pruning(self):
        make_env(self.root, 'moss-comfy-convrot-v1')
        jobs = self.root / 'runtime-setup/jobs.json'
        updated = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        jobs.write_text(json.dumps([{'id': 'job', 'targetId': 'kromoss', 'status': 'running', 'updatedAt': updated}]))
        with self.assertRaises(storage.InUse):
            storage.remove(self.root, 'moss-comfy-convrot-v1')
        result = storage.prune(self.root, grace=0)
        self.assertIn('kromoss', result['skipped'])
        self.assertTrue((self.root / 'runtime-setup/environments/moss-comfy-convrot-v1').exists())
        stale = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() - 3 * 3600))
        jobs.write_text(json.dumps([{'id': 'job', 'targetId': 'kromoss', 'status': 'running', 'updatedAt': stale}]))
        storage.remove(self.root, 'moss-comfy-convrot-v1')
        self.assertFalse((self.root / 'runtime-setup/environments/moss-comfy-convrot-v1').exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows refuses to move folders with open files')
    def test_runtime_with_open_files_is_left_intact(self):
        environment = make_env(self.root, 'moss-comfy-convrot-v1')
        kept_receipt = receipt(self.root, 'kromoss', environment)
        with open(site(environment) / 'torch.bin', 'rb'):
            with self.assertRaisesRegex(storage.InUse, 'another process'):
                storage.remove(self.root, 'moss-comfy-convrot-v1')
        self.assertTrue((site(environment) / 'torch.bin').exists())
        self.assertTrue(kept_receipt.exists())


class PruneTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_prune_removes_only_unused_runtimes(self):
        make_env(self.root, 'qwen-image-21-comfy-v1')
        unused = make_env(self.root, 'moss-comfy-convrot-v1', size=3000)
        lender = make_env(self.root, 'phonon-venv', speech=True)
        share(make_env(self.root, 'diffusers-images-v1'), lender)
        make_env(self.root, 'memory-semantic-v1')
        make_env(self.root, 'mystery-env')
        result = storage.prune(self.root, installed={'qwen-image-21-uc'},
                               demand={'diffusers-images-v1': {'hf-image'}}, now=LATER)
        self.assertEqual([item['id'] for item in result['removed']], ['moss-comfy-convrot-v1'])
        self.assertGreater(result['freedBytes'], 3000)
        self.assertFalse(unused.exists())
        left = {row['id'] for row in storage.inventory(self.root, now=LATER)}
        self.assertEqual(left, {'qwen-image-21-comfy-v1', 'speech:phonon-venv', 'diffusers-images-v1',
                                'memory-semantic-v1', 'mystery-env'})

    def test_prune_waits_for_recently_changed_runtimes(self):
        environment = make_env(self.root, 'moss-comfy-convrot-v1')
        result = storage.prune(self.root)
        self.assertEqual(result['removed'], [])
        self.assertEqual(result['kept'], [{'id': 'moss-comfy-convrot-v1', 'reason': 'recently-changed'}])
        self.assertTrue(environment.exists())
        storage.prune(self.root, grace=0)
        self.assertFalse(environment.exists())

    def test_prune_repairs_runtimes_whose_shared_packages_vanished(self):
        environment = make_env(self.root, 'managed-transformers-text-v1')
        (site(environment) / 'opencore-shared-torch.pth').write_text(str(self.root / 'deleted-env/site-packages'))
        stale = receipt(self.root, 'hf-axiomiclabs-gpt-x3-150m', environment)
        result = storage.prune(self.root, installed={'hf-axiomiclabs-gpt-x3-150m'}, now=LATER)
        self.assertEqual(result['repaired'], ['managed-transformers-text-v1'])
        self.assertFalse(stale.exists())
        self.assertTrue(environment.exists())

    def test_prune_can_target_one_models_runtimes_and_defer_deletion(self):
        moss = make_env(self.root, 'moss-comfy-convrot-v1')
        qwen = make_env(self.root, 'qwen-image-21-comfy-v1')
        rows = storage.inventory(self.root, now=LATER)
        only = storage.runtimes_used_by(rows, 'moss-image-sft-int8-convrot')
        self.assertEqual(only, {'moss-comfy-convrot-v1'})
        result = storage.prune(self.root, now=LATER, only=only, purge=False)
        self.assertEqual([item['id'] for item in result['removed']], ['moss-comfy-convrot-v1'])
        self.assertFalse(moss.exists())
        self.assertTrue(qwen.exists())
        self.assertEqual(len(list(storage.trash_folder(self.root).iterdir())), 1)
        self.assertEqual(storage.purge_trash(self.root), 0)
        self.assertEqual(list(storage.trash_folder(self.root).iterdir()), [])

    def test_prune_finishes_earlier_trash(self):
        leftover = storage.trash_folder(self.root) / 'old-runtime-1'
        leftover.mkdir(parents=True)
        (leftover / 'file').write_text('x')
        storage.prune(self.root)
        self.assertFalse(leftover.exists())


class FakeRunner:
    """Reports the PyTorch build each candidate environment holds."""

    def __init__(self, builds):
        self.builds, self.asked = builds, []

    def run(self, command, timeout=None):
        environment = Path(command[0]).parent.parent
        self.asked.append(environment.name)
        if environment.name not in self.builds:
            raise RuntimeError('torch is not installed')
        value = {'version': self.builds[environment.name], 'site': str(site(environment)), 'python': str(command[0]), 'bits': 64}
        return type('Result', (), {'stdout': json.dumps(value) + '\n'})()


class TorchLenderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self):
        self.temporary.cleanup()

    def test_any_managed_environment_can_lend_and_preferred_lenders_come_first(self):
        import managed_model_sources as sources
        for name in ('edgerock-portable-v1', 'moss-comfy-convrot-v1', 'computer-hobbylm-v1'):
            make_env(self.root, name)
        make_env(self.root, 'phonon-venv', speech=True)
        (self.root / 'runtime-setup/environments/not-a-venv').mkdir()
        names = [path.parent.parent.name for path in sources.torch_lender_candidates(self.root, 'computer-hobbylm-v1')]
        self.assertEqual(names, ['moss-comfy-convrot-v1', 'phonon-venv', 'edgerock-portable-v1'])

    def test_cpu_runtime_borrows_an_existing_cpu_build_but_never_itself(self):
        import managed_model_sources as sources
        make_env(self.root, 'moss-comfy-convrot-v1')
        make_env(self.root, 'edgerock-portable-v1')
        make_env(self.root, 'computer-hobbylm-v1')
        recipe = {'id': 'computer-hobbylm-v1', 'shareManagedTorch': True, 'torchPackages': {'torch': '2.11.0'},
                  'torchIndex': 'https://download.pytorch.org/whl/cpu'}
        runner = FakeRunner({'moss-comfy-convrot-v1': '2.11.0+cu126', 'edgerock-portable-v1': '2.11.0+cpu',
                             'computer-hobbylm-v1': '2.11.0+cpu'})
        shared = sources.shared_torch_source(self.root, recipe, runner)
        self.assertEqual(Path(shared['python']).parent.parent.name, 'edgerock-portable-v1')
        self.assertNotIn('computer-hobbylm-v1', runner.asked)
        runner = FakeRunner({'computer-hobbylm-v1': '2.11.0+cpu'})
        self.assertIsNone(sources.shared_torch_source(self.root, recipe, runner))


if __name__ == '__main__':
    unittest.main()
