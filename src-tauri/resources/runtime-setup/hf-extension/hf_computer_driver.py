"""Install the pinned upstream Cua Driver inside OpenCore, without global setup."""
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import threading
import time
from urllib.request import urlopen
import zipfile
from hf_download import atomic_json

CONFIG = json.loads(Path(__file__).with_name('cua-driver.json').read_text(encoding='utf-8'))
SERVER_ID = 'opencore-cua-driver'


def layout(root):
    return Path(root).resolve() / 'tools' / 'cua-driver' / CONFIG['version']


def verify_runtime(directory, *, hashes=False):
    directory = Path(directory)
    for name, pin in CONFIG['files'].items():
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != pin['bytes']:
            raise ValueError('Cua Driver is incomplete. Use Install or repair in Computer use settings.')
        if hashes:
            with path.open('rb') as source:
                digest = hashlib.file_digest(source, 'sha256').hexdigest()
            if digest != pin['sha256']:
                raise ValueError('Cua Driver verification failed. Repair it in Computer use settings.')
    return directory / 'cua-driver.exe'


class ComputerDriverManager:
    def __init__(self, root, resources):
        self.root, self.resources = Path(root).resolve(), Path(resources).resolve()
        self.directory = layout(root)
        self.connection_path = self.directory.parent / 'connection.json'
        self.lock = threading.RLock()
        self.cancelled = threading.Event()
        self.worker = None
        self.progress = {'status': 'idle', 'downloadedBytes': 0, 'totalBytes': CONFIG['archiveBytes']}

    def status(self):
        with self.lock:
            try:
                verify_runtime(self.directory)
                installed = True
            except (OSError, ValueError):
                installed = False
            return {**self.progress, 'installed': installed, 'version': CONFIG['version'],
                    'supported': os.name == 'nt' and platform.machine().lower() in ('amd64', 'x86_64'),
                    'sourceUrl': CONFIG['sourceRepository'], 'serverId': SERVER_ID}

    def start(self):
        if not self.status()['supported']:
            raise ValueError('This reviewed Cua Driver package requires 64-bit Windows.')
        with self.lock:
            if self.worker and self.worker.is_alive():
                return self.status()
            self.disconnect()
            self.cancelled.clear()
            self.progress = {'status': 'downloading', 'stage': 'Downloading Cua Driver',
                             'downloadedBytes': 0, 'totalBytes': CONFIG['archiveBytes']}
            self.worker = threading.Thread(target=self._install, daemon=True, name='opencore-cua-install')
            self.worker.start()
            return self.status()

    def cancel(self):
        self.cancelled.set()
        return self.status()

    def disconnect(self):
        atomic_json(self.connection_path, {'enabled': False})
        return {'disconnected': True}

    def _install(self):
        try:
            parent = self.directory.parent
            parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='install-', dir=parent) as temporary:
                temporary = Path(temporary)
                archive = temporary / 'driver.zip'
                digest, downloaded, started = hashlib.sha256(), 0, time.monotonic()
                with urlopen(CONFIG['archiveUrl'], timeout=30) as response, archive.open('wb') as output:
                    while chunk := response.read(256 * 1024):
                        if self.cancelled.is_set():
                            raise InterruptedError('Installation cancelled')
                        downloaded += len(chunk)
                        if downloaded > CONFIG['archiveBytes']:
                            raise ValueError('Cua Driver archive exceeded the reviewed size')
                        digest.update(chunk)
                        output.write(chunk)
                        with self.lock:
                            self.progress.update(downloadedBytes=downloaded,
                                bytesPerSecond=downloaded / max(.01, time.monotonic() - started))
                if downloaded != CONFIG['archiveBytes'] or digest.hexdigest() != CONFIG['archiveSha256']:
                    raise ValueError('Cua Driver download did not match the pinned publisher release')
                with self.lock:
                    self.progress.update(status='preparing', stage='Verifying and installing Cua Driver')
                with zipfile.ZipFile(archive) as bundle:
                    names = bundle.namelist()
                    if len(names) != len(CONFIG['files']) or set(names) != set(CONFIG['files']):
                        raise ValueError('Cua Driver archive contained unexpected files')
                    for name, pin in CONFIG['files'].items():
                        info = bundle.getinfo(name)
                        if info.file_size != pin['bytes']:
                            raise ValueError('Cua Driver file size did not match its pin')
                        data = bundle.read(name)
                        if hashlib.sha256(data).hexdigest() != pin['sha256']:
                            raise ValueError('Cua Driver file did not match its pin')
                        (temporary / name).write_bytes(data)
                if self.cancelled.is_set():
                    raise InterruptedError('Installation cancelled')
                self.directory.mkdir(parents=True, exist_ok=True)
                for name in CONFIG['files']:
                    target = self.directory / name
                    if target.is_symlink():
                        raise ValueError('Cua Driver installation contains an unexpected link')
                    os.replace(temporary / name, target)
                verify_runtime(self.directory, hashes=True)
            with self.lock:
                self.progress.update(status='installed', stage='Cua Driver installed')
        except Exception as error:
            with self.lock:
                self.progress.update(status='cancelled' if isinstance(error, InterruptedError) else 'failed', error=str(error))

    def connection(self):
        if not self.status()['supported']:
            raise ValueError('Cua Driver requires the Windows PC execution device.')
        verify_runtime(self.directory, hashes=True)
        script = Path(__file__).with_name('hf_computer_driver_mcp.py').resolve()
        appdata = os.environ.get('APPDATA')
        if not appdata:
            raise ValueError('The OpenCore workspace could not be located')
        database = Path(appdata).resolve() / 'ai.opencore.control-center' / 'control-center.sqlite3'
        if not database.is_file():
            raise ValueError('OpenCore must finish opening its local workspace before connecting Cua Driver')
        atomic_json(self.connection_path, {'enabled': True})
        return {'id': SERVER_ID, 'name': 'cua_driver', 'enabled': True,
                'command': sys.executable, 'args': ['-I', str(script), '--root', str(self.root), '--database', str(database)],
                'env': {}, 'url': None, 'bearerTokenEnvVar': None, 'startupTimeoutSec': 45, 'toolTimeoutSec': 40}
