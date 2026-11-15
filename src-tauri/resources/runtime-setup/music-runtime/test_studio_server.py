"""OpenCore studio server under OpenCore's music host, with a fake yue2 package (no torch, no weights)."""
import http.client
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import textwrap
import threading
import time
import unittest

HERE = Path(__file__).resolve().parent
STUDIO = HERE.parents[1] / 'studio'

FAKE_YUE2 = {
    '__init__.py': '__version__ = "0.1.6"\n',
    'storage.py': 'def sha256_file(path):\n    return "x"\n',
    'protocol.py': textwrap.dedent('''
        from dataclasses import dataclass, field
        @dataclass(frozen=True)
        class Sampling:
            temperature: float = 1.0
            top_p: float = 0.95
            top_k: int = 100
            repetition_penalty: float = 1.2
            penalty_window: int = 50
            min_tokens: int = 200
            max_tokens: int = 9000
        @dataclass(frozen=True)
        class GenerationConfig:
            abc: Sampling = field(default_factory=lambda: Sampling(.7, .9, 30, 1.005, 100, 32, 4096))
            semantic: Sampling = field(default_factory=Sampling)
            ode_steps: int = 32
    '''),
    'pipeline.py': textwrap.dedent('''
        import threading
        from pathlib import Path
        CREATED, CLOSED = [], []
        GATE = threading.Event(); GATE.set()
        def sha256_file(path):
            return "x"
        class Semantic:
            tokens = list(range(250))
        class Result:
            sample_rate = 48000
            abc = "X:1"
            truncated = {"abc": False, "semantic": False}
            semantic = Semantic()
            audio = [0.0] * 480000
            def save_artifacts(self, directory):
                Path(directory).mkdir(parents=True, exist_ok=True)
                (Path(directory) / "audio.flac").write_bytes(b"fLaC fake audio")
        class Plan:
            abc = "X:1\\nK:C\\nCDEF|"
            truncated = False
            def save(self, directory):
                Path(directory).mkdir(parents=True, exist_ok=True)
                (Path(directory) / "abc.txt").write_text(self.abc)
        class YuE2Pipeline:
            def __init__(self, model_dir, vae_dir, **options):
                CREATED.append(options)
            def close(self):
                CLOSED.append(True)
            def plan(self, style, lyrics, *, cancelled, on_token, **kwargs):
                on_token("abc", 1)
                return Plan()
            def __call__(self, style, lyrics, *, cancelled, on_token, on_progress, **kwargs):
                for _ in range(3):
                    on_token("abc", 1)
                for _ in range(5):
                    on_token("semantic", 1)
                GATE.wait(10)
                if cancelled():
                    raise InterruptedError("Cancelled")
                on_progress("nar", 1, 2)
                on_progress("vae", 1, 1)
                return Result()
    '''),
}


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class StudioServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix='opencore-studio-test-')
        root = Path(cls.folder.name) / 'YuE'
        package = root / 'src' / 'yue2'
        package.mkdir(parents=True)
        for name, source in FAKE_YUE2.items():
            (package / name).write_text(source, encoding='utf-8')
        models = Path(cls.folder.name) / 'models'
        for name in ('YuE2-3B', 'YuE2-Vae'):
            (models / name).mkdir(parents=True)
            (models / name / 'weights_manifest.json').write_text('{}')
        cls.downloads = Path(cls.folder.name) / 'Downloads'
        os.environ.update(OPENCORE_MUSIC_HOME=str(root), YUE2_MODELS=str(models), YUE2_DOWNLOADS=str(cls.downloads))
        sys.path[:0] = [str(root / 'src'), str(STUDIO)]
        import music_host
        spec = importlib.util.spec_from_file_location('opencore_test_server', HERE / 'studio_server.py')
        server = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(server)
        server.hardware = lambda: {'device': 'cpu', 'gpu': None, 'vram_gib': None, 'note': None}
        music_host.attach(server)
        cls.server, cls.root, cls.port = server, root, free_port()
        import yue2.pipeline
        cls.fake = yue2.pipeline
        cls.thread = threading.Thread(target=server.main, args=(['--no-browser', '--port', str(cls.port)],), daemon=True)
        cls.thread.start()
        deadline = time.monotonic() + 10
        while server.HTTPD is None and time.monotonic() < deadline:
            time.sleep(.02)

    @classmethod
    def tearDownClass(cls):
        cls.request('POST', '/api/shutdown', {})
        cls.thread.join(10)
        cls.folder.cleanup()

    @classmethod
    def request(cls, method, path, body=None):
        connection = http.client.HTTPConnection('127.0.0.1', cls.port, timeout=10)
        connection.request(method, path, None if body is None else json.dumps(body), {'Content-Type': 'application/json'})
        response = connection.getresponse()
        data = response.read()
        connection.close()
        try:
            return response.status, json.loads(data)
        except ValueError:
            return response.status, data

    def wait_idle(self):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            _, status = self.request('GET', '/api/status')
            if not status.get('worker_active'):
                return status
            time.sleep(.05)
        self.fail('worker did not finish')

    def song(self, **values):
        return dict({'mode': 'song', 'title': 'Test Song', 'style': 'pop', 'lyrics': '[Verse]\nla la', 'cot': 'full',
                     'seed': 7, 'random_seed': False, 'takes': 2, 'ode_steps': 8,
                     'semantic_sampling': {'max_tokens': 600, 'min_tokens': 10}, 'memory': {'quantization': 'none'}}, **values)

    def test_info_satisfies_native_identity_check(self):
        status, info = self.request('GET', '/api/info')
        self.assertEqual(status, 200)
        self.assertEqual(Path(info['output']), self.root / 'studio-output')
        self.assertIsInstance(info['model_loaded'], bool)
        self.assertEqual(info['defaults']['semantic']['max_tokens'], 9000)
        self.assertEqual(info['defaults']['ode_steps'], 32)
        self.assertEqual(info['opencore_host_version'], 1)
        status, page = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn(b'<html', page[:2000].lower())

    def test_song_takes_are_saved_listed_served_and_downloadable(self):
        status, started = self.request('POST', '/api/generate', self.song())
        self.assertEqual(status, 200, started)
        run = started['run']
        final = self.wait_idle()
        self.assertEqual(final['status'], 'done', final)
        self.assertEqual(final['run'], run)
        self.assertEqual([take['dir'] for take in final['results']], ['take1-7', 'take2-8'])
        self.assertTrue(final['worker_finished'])
        _, runs = self.request('GET', '/api/history')
        match = next(item for item in runs if item['run'] == run)
        self.assertEqual(match['status'], 'done')
        self.assertEqual(match['takes'][0]['seconds'], 10.0)
        status, audio = self.request('GET', f'/files/{run}/take1-7/audio.flac')
        self.assertEqual((status, audio), (200, b'fLaC fake audio'))
        self.assertEqual(self.request('GET', f'/files/{run}/..%2F..%2Fsecret/audio.flac')[0], 404)
        status, saved = self.request('POST', '/api/download', {'run': run, 'take': 'take1-7'})
        self.assertEqual(status, 200, saved)
        self.assertTrue(Path(saved['path']).is_file())
        _, info = self.request('GET', '/api/info')
        self.assertEqual(info['tokens_per_second'], 25.0)
        self.assertFalse(info['model_loaded'])  # the host unloads after every song

    def test_cancel_marks_run_cancelled_and_releases_model(self):
        self.fake.GATE.clear()
        status, started = self.request('POST', '/api/generate', self.song(takes=1))
        self.assertEqual(status, 200)
        deadline = time.monotonic() + 5
        while self.request('GET', '/api/status')[1].get('stage') != 'Generating the song' and time.monotonic() < deadline:
            time.sleep(.02)
        status, snapshot = self.request('POST', '/api/cancel', {'expectedRun': started['run']})
        self.assertEqual(snapshot['status'], 'cancelled')
        self.fake.GATE.set()
        final = self.wait_idle()
        self.assertEqual(final['status'], 'cancelled')
        manifest = json.loads((self.root / 'studio-output' / started['run'] / 'studio.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['status'], 'cancelled')

    def test_plan_mode_returns_score(self):
        status, started = self.request('POST', '/api/generate', self.song(mode='plan', takes=1))
        self.assertEqual(status, 200)
        final = self.wait_idle()
        self.assertEqual(final['status'], 'done', final)
        self.assertIn('CDEF', final['results'][0]['abc'])

    def test_invalid_requests_are_rejected_before_any_work(self):
        self.assertEqual(self.request('POST', '/api/generate', self.song(style=''))[0], 400)
        self.assertEqual(self.request('POST', '/api/generate', self.song(takes=99))[0], 400)
        self.assertEqual(self.request('POST', '/api/generate', self.song(cot='weird'))[0], 400)


if __name__ == '__main__':
    unittest.main()
