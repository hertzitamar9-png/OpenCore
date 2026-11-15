"""Music runtime installer behavior with a fake runner: no downloads or packages."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hf_music_runtime as music


def smi(stdout, code=0):
    return lambda args, timeout=15: subprocess.CompletedProcess(args, code, stdout, '')


class FakeRunner:
    def __init__(self, root, calls, reporter, verify_code=0, events=None):
        self.root, self.calls, self.reporter, self.verify_code = root, calls, reporter, verify_code
        self.events = events if events is not None else []

    def run(self, args, *, env=None, timeout=1800, check=True, allow_cancel=True, input_text=None):
        args = [str(value) for value in args]
        self.calls.append(args)
        environment = self.root / '.venv'
        if '-m' in args and 'venv' in args:
            music.venv_python(self.root).parent.mkdir(parents=True, exist_ok=True)
            music.venv_python(self.root).write_text('')
            return subprocess.CompletedProcess(args, 0, '', '')
        if '-c' in args and 'prefix' in args[args.index('-c') + 1]:
            value = {'prefix': str(environment), 'base': 'C:/Python312', 'version': [3, 12]}
            return subprocess.CompletedProcess(args, 0, json.dumps(value), '')
        if 'pip' in args and 'install' in args:
            self.reporter('diagnostic', detail='Progress 50000000 of 100000000')
            return subprocess.CompletedProcess(args, 0, '', '')
        if '-c' in args and 'import torch' in args[args.index('-c') + 1]:
            self.events.append(('verify', (self.root / 'studio' / 'server.py').exists()))
            if self.verify_code:
                return subprocess.CompletedProcess(args, 1, 'AssertionError: PyTorch cannot see the NVIDIA GPU', '')
            return subprocess.CompletedProcess(args, 0, json.dumps({'torch': '2.10.0+cpu', 'yue2': '0.1.6', 'device': 'cpu'}), '')
        return subprocess.CompletedProcess(args, 0, 'pip 25.0', '')


class MusicRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='opencore-music-test-')
        self.root = Path(self.folder.name) / 'YuE'
        self.files = {'LICENSE': b'license text', 'src/yue2/__init__.py': b'__version__ = "0.1.6"\n'}
        pins = {name: {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()} for name, data in self.files.items()}
        config = json.loads(json.dumps(music.CONFIG))
        config['source']['files'] = pins
        patcher = mock.patch.object(music, 'CONFIG', config)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.folder.cleanup)
        self.calls, self.events = [], []

    def manager(self, *, fetch=None, verify_code=0, plan=None):
        plan = plan or music._plan('cpu', None, 'test')
        return music.MusicRuntimeManager(
            root_resolver=lambda: self.root, detect=lambda: plan, python='C:/Python312/python.exe',
            runner_factory=lambda reporter: FakeRunner(self.root, self.calls, reporter, verify_code, self.events),
            fetch=fetch or (lambda url, limit: self.files[url.rsplit('/', 1)[-1] if url.endswith('LICENSE') else 'src/yue2/__init__.py']))

    def wait(self, manager):
        deadline = time.monotonic() + 20
        while manager.status()['busy'] and time.monotonic() < deadline:
            time.sleep(.02)
        return manager.status()

    def test_root_matches_native_resolution(self):
        with mock.patch.dict(os.environ, {'OPENCORE_MUSIC_HOME': 'D:/Music/YuE'}):
            self.assertEqual(music.music_root(), Path('D:/Music/YuE'))
        with mock.patch.dict(os.environ, {'USERPROFILE': 'C:/Users/someone'}, clear=False):
            os.environ.pop('OPENCORE_MUSIC_HOME', None)
            self.assertEqual(music.music_root(), Path('C:/Users/someone/YuE'))

    def test_hardware_picks_only_the_torch_build_this_pc_can_use(self):
        with mock.patch.object(music.shutil, 'which', return_value='nvidia-smi'):
            self.assertEqual(music.detect_hardware(smi('NVIDIA GeForce RTX 5060, 8151, 576.02, 12.0'))['variant'], 'cu128')
            self.assertEqual(music.detect_hardware(smi('NVIDIA GeForce RTX 3090, 24576, 551.86, 8.6'))['variant'], 'cu126')
            old = music.detect_hardware(smi('NVIDIA GeForce RTX 2080, 8192, 576.02, 7.5'))
            self.assertEqual(old['variant'], 'cpu')
            self.assertIn('BF16', old['note'])
            self.assertEqual(music.detect_hardware(smi('NVIDIA GeForce RTX 3060, 12288, 472.12, 8.6'))['variant'], 'cpu')
        with mock.patch.object(music.shutil, 'which', return_value=None), mock.patch.object(music.os, 'name', 'nt'):
            self.assertEqual(music.detect_hardware(smi('Intel(R) Arc(TM) A770 Graphics\n'))['variant'], 'xpu')
            self.assertEqual(music.detect_hardware(smi('Intel(R) Arc(TM) Graphics\n'))['variant'], 'cpu')
            amd = music.detect_hardware(smi('AMD Radeon RX 7900 XTX\n'))
            self.assertEqual(amd['variant'], 'cpu')
            self.assertIn('not supported', amd['note'])

    @unittest.skipUnless(os.name == 'nt', 'Windows installer')
    def test_install_verifies_code_and_writes_server_last(self):
        manager = self.manager()
        manager.start()
        status = self.wait(manager)
        self.assertEqual(status['status'], 'installed', status.get('error'))
        self.assertTrue(status['installed'])
        self.assertEqual(status['server'], 'opencore')
        self.assertEqual(self.events, [('verify', False)])
        self.assertEqual((self.root / 'LICENSE').read_bytes(), self.files['LICENSE'])
        receipt = json.loads((self.root / 'studio' / music.RECEIPT_NAME).read_text(encoding='utf-8'))
        self.assertEqual(receipt['variant'], 'cpu')
        pip = [call for call in self.calls if 'pip' in call and 'install' in call]
        self.assertIn('torch==2.10.0+cpu', pip[0])
        self.assertIn('https://download.pytorch.org/whl/cpu', pip[0])
        self.assertTrue(all('--only-binary=:all:' in call for call in pip))
        self.assertIn('transformers==4.57.6', pip[1])

    @unittest.skipUnless(os.name == 'nt', 'Windows installer')
    def test_checksum_mismatch_fails_closed(self):
        manager = self.manager(fetch=lambda url, limit: b'tampered!!!!')
        manager.start()
        status = self.wait(manager)
        self.assertEqual(status['status'], 'failed')
        self.assertIn('checksum', status['error'])
        self.assertFalse((self.root / 'studio' / 'server.py').exists())
        self.assertFalse((self.root / 'LICENSE').exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows installer')
    def test_failed_gpu_check_never_reports_installed(self):
        manager = self.manager(verify_code=1, plan=music._plan('cu128', {'name': 'GPU'}, None))
        manager.start()
        status = self.wait(manager)
        self.assertEqual(status['status'], 'failed')
        self.assertIn('graphics driver', status['error'])
        self.assertFalse(status['installed'])

    @unittest.skipUnless(os.name == 'nt', 'Windows installer')
    def test_cancel_stops_before_server_is_written(self):
        def fetch(url, limit):
            manager.cancel()
            return self.files['LICENSE']
        manager = self.manager(fetch=fetch)
        manager.start()
        status = self.wait(manager)
        self.assertEqual(status['status'], 'cancelled')
        self.assertFalse((self.root / 'studio' / 'server.py').exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows installer')
    def test_refuses_foreign_server_and_low_disk(self):
        (self.root / 'studio').mkdir(parents=True)
        (self.root / 'studio' / 'server.py').write_text('# someone else\n')
        with self.assertRaisesRegex(ValueError, 'will not replace'):
            self.manager().start()
        (self.root / 'studio' / 'server.py').unlink()
        with mock.patch.object(music, 'free_bytes', return_value=1024):
            with self.assertRaisesRegex(ValueError, 'GB free'):
                self.manager().start()
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
