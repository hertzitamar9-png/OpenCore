"""Opt-in direct TLS phone connection. No relay, cloud account or outbound service."""
from collections import OrderedDict
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
import mimetypes
import re
from pathlib import Path
import secrets
import socket
import subprocess
import threading
import time
from urllib.parse import quote, unquote, urlencode, urlsplit

from hf_download import atomic_json, managed_path


def local_addresses():
    """Find this PC's private IPv4 addresses without contacting another host."""
    try:
        result = subprocess.run(['ipconfig.exe'], capture_output=True, text=True, errors='replace', timeout=4,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode: return []
    except (OSError, subprocess.TimeoutExpired): return []
    ranges = tuple(ipaddress.ip_network(value) for value in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','100.64.0.0/10'))
    found, interface = [], ''
    for line in result.stdout.splitlines():
        if line.strip() and not line[0].isspace() and line.rstrip().endswith(':'):
            interface = re.sub(r'^(?:(?:Wireless LAN|Ethernet|Tunnel) adapter)\s+', '', line.strip().rstrip(':'), flags=re.I)
            continue
        match = re.search(r'\bIPv4\b[^:]*:\s*(\d{1,3}(?:\.\d{1,3}){3})', line, re.I)
        if not match: continue
        try: address = ipaddress.ip_address(match.group(1))
        except ValueError: continue
        if any(address in network for network in ranges) and not any(item['address'] == str(address) for item in found):
            found.append({'address': str(address), 'interface': interface[:100] or 'Network adapter'})
    def priority(item):
        name = item['interface']
        if re.search(r'\bwi[ -]?fi\b|wireless', name, re.I): return 0
        if re.match(r'^ethernet(?:\s+\d+)?$', name, re.I): return 1
        if re.search(r'vethernet|virtual|hyper-v', name, re.I): return 3
        return 2
    found.sort(key=priority)
    return found[:16]


def pairing_qr(text):
    import qrcode
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    code.add_data(text)
    code.make(fit=True)
    return [''.join('1' if bit else '0' for bit in row) for row in code.get_matrix()]


class RemoteAccess:
    def __init__(self, root, resources, owner=None, *, surface=None, tls_factory=None):
        self.root, self.resources = Path(root).resolve(), Path(resources).resolve()
        self.owner, self.surface, self.tls_factory = owner, surface, tls_factory
        self.lock, self.server, self.thread = threading.RLock(), None, None
        self.pair_code, self.pair_until, self.fingerprint = None, 0, ''
        self.attempts, self.last_input, self.last_seen = {}, {}, {}
        self.addresses, self.addresses_checked = [], 0
        self.last_frame, self.last_frame_at = None, 0
        self.devices_path = managed_path(self.root, 'secrets', 'remote-device-identities.json')
        self.config_path = managed_path(self.root, 'runtime-setup', 'remote-access.json')
        configured = self.config()
        self.pc_name = configured.get('name') or os.environ.get('COMPUTERNAME') or socket.gethostname() or 'OpenCore PC'
        if not isinstance(self.pc_name, str) or len(self.pc_name) > 80 or any(ord(c) < 32 for c in self.pc_name):
            self.pc_name = 'OpenCore PC'
        try:
            value = json.loads(self.devices_path.read_text(encoding='utf-8'))
            self.devices = {item['id']: item for item in value if isinstance(item, dict) and
                            isinstance(item.get('hash'), str) and len(item['hash']) == 64} if isinstance(value, list) else {}
        except (OSError, ValueError, TypeError, KeyError): self.devices = {}
        # Only app-created outputs are exposed. Models, user-selected files,
        # credentials, databases and arbitrary absolute paths are never roots.
        self.media_roots = {name: self.root / name for name in ('artifacts', 'generated', 'studio/outputs')}
        appdata = os.environ.get('APPDATA')
        if appdata:
            app_root = Path(appdata).resolve() / 'ai.opencore.control-center'
            self.media_roots.update({'app-artifacts': app_root / 'artifacts', 'app-studio': app_root / 'studio'})
        self.output_extensions = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.mp4', '.webm', '.html', '.pdf', '.txt', '.glb', '.zip', '.wav', '.mp3', '.flac', '.ogg', '.gltf', '.obj', '.stl', '.csv', '.md'}

    def config(self):
        try:
            value = json.loads(self.config_path.read_text(encoding='utf-8'))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError): return {}

    def rename(self, value):
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 80 or any(ord(c) < 32 for c in value):
            raise ValueError('Choose a PC name from 1 to 80 characters')
        with self.lock:
            self.pc_name = value.strip()
            options = self.config()
            options['name'] = self.pc_name
            atomic_json(self.config_path, options)
            return self.status()

    def status(self):
        now = time.monotonic()
        if now - self.addresses_checked > 60:
            self.addresses = local_addresses()
            self.addresses_checked = now
        with self.lock:
            return {'enabled': self.server is not None, 'port': self.server.server_port if self.server else None, 'pcName': self.pc_name,
                    'fingerprint': self.fingerprint, 'pairingExpiresIn': max(0, int(self.pair_until - time.monotonic())),
                    'addresses': list(self.addresses),
                    'devices': [{'id': d['id'], 'name': d.get('name', 'Phone'),
                                 'connected': now-self.last_seen.get(d['id'], -100) < 10} for d in self.devices.values()],
                    'network': 'direct', 'relay': False}

    def start(self, options):
        with self.lock:
            if self.server: return self.status()
            saved = self.config()
            host = options.get('bind', saved.get('bind', '0.0.0.0'))
            try: ipaddress.ip_address(host)
            except (ValueError, TypeError): raise ValueError('Bind to a local IP address') from None
            port = options.get('port', saved.get('port', 0))
            if type(port) is not int or not (port == 0 or 1024 <= port <= 65535):
                raise ValueError('Choose a port between 1024 and 65535')
            if self.surface is None:
                if not self.owner: raise ValueError('A running OpenCore PC app is required')
                from pc_window import WindowSurface
                self.surface = WindowSurface(self.owner['pid'])
            if self.tls_factory: tls, fingerprint = self.tls_factory()
            else:
                from tls_identity import context
                tls, fingerprint = context(self.root)
            server = RemoteServer((host, port), self, tls)
            self.server, self.fingerprint = server, fingerprint
            self.thread = threading.Thread(target=server.serve_forever, daemon=True)
            self.thread.start()
            atomic_json(self.config_path, {'enabled': True, 'bind': host, 'port': server.server_port, 'name': self.pc_name})
            return self.status()

    def restore(self):
        try:
            options = json.loads(self.config_path.read_text(encoding='utf-8'))
            if options.get('enabled') is True: self.start(options)
        except (OSError, ValueError, RuntimeError):
            pass

    def pairing(self, address):
        with self.lock:
            if not self.server: raise ValueError('Enable remote access first')
            if not isinstance(address, str): raise ValueError('Enter the PC’s reachable IP address or VPN name')
            parsed = urlsplit(address if '://' in address else 'https://' + address)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
                raise ValueError('Use a direct HTTPS PC address without a path')
            if len(parsed.hostname) > 253 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-:' for c in parsed.hostname):
                raise ValueError('Invalid PC address')
            host = '[' + parsed.hostname + ']' if ':' in parsed.hostname else parsed.hostname
            endpoint = 'https://' + host + ':' + str(parsed.port or self.server.server_port)
            self.pair_code, self.pair_until = secrets.token_urlsafe(32), time.monotonic() + 120
            self.attempts.clear()
            text = 'opencore://pair?' + urlencode({'url': endpoint, 'pin': self.fingerprint, 'code': self.pair_code, 'name': self.pc_name})
            return {'pairingText': text, 'qrRows': pairing_qr(text), 'expiresIn': 120, 'fingerprint': self.fingerprint}

    def pair(self, code, name, address):
        with self.lock:
            now = time.monotonic()
            self.attempts = {key: value for key, value in self.attempts.items() if now - value[0] < 120}
            stamp, attempts = self.attempts.get(address, (now, 0))
            if len(self.attempts) > 128 or attempts >= 10: raise PermissionError('Pairing attempt limit reached')
            self.attempts[address] = (stamp, attempts + 1)
            if not isinstance(code, str) or not self.pair_code or now > self.pair_until or not hmac.compare_digest(code, self.pair_code):
                raise PermissionError('Pairing code is invalid or expired')
            if len(self.devices) >= 8: raise ValueError('Revoke an old phone before pairing another')
            if not isinstance(name, str) or not 1 <= len(name) <= 80 or any(ord(c) < 32 for c in name): raise ValueError('Invalid phone name')
            token, identity = secrets.token_urlsafe(48), secrets.token_hex(12)
            self.devices[identity] = {'id': identity, 'name': name, 'hash': hashlib.sha256(token.encode()).hexdigest()}
            atomic_json(self.devices_path, list(self.devices.values()))
            self.pair_code, self.pair_until = None, 0
            return {'deviceId': identity, 'token': token, 'name': self.pc_name}

    def authorize(self, authorization):
        if not isinstance(authorization, str) or not authorization.startswith('Bearer ') or len(authorization) > 256: return None
        digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
        with self.lock:
            identity = next((item['id'] for item in self.devices.values() if hmac.compare_digest(item['hash'], digest)), None)
            if identity: self.last_seen[identity] = time.monotonic()
            return identity

    def revoke(self, identity):
        with self.lock:
            if identity not in self.devices: raise ValueError('Phone is not paired')
            del self.devices[identity]
            self.last_seen.pop(identity, None)
            atomic_json(self.devices_path, list(self.devices.values()))
            return self.status()

    def shutdown(self):
        with self.lock:
            server, self.server = self.server, None
            self.pair_code, self.pair_until = None, 0
        if server: server.shutdown(); server.server_close()
        if self.surface and hasattr(self.surface, 'release'): self.surface.release()
        self.last_frame = None
        self.last_seen.clear()
        return self.status()

    def close(self):
        if self.config_path.exists():
            saved = self.config()
            saved.update(enabled=False, name=self.pc_name)
            atomic_json(self.config_path, saved)
        return self.shutdown()

    def media_path(self, root, relative):
        base = self.media_roots.get(root)
        if base is None or not isinstance(relative, str) or not relative or '\\' in relative or any(part in ('', '.', '..') for part in relative.split('/')):
            raise ValueError('Invalid generated media path')
        expected = base
        base = base.resolve()
        # A redirected output root is not an implicit share of another directory.
        if base != expected: raise ValueError('Generated media root is redirected')
        path = (base / relative).resolve()
        if not path.is_relative_to(base) or not path.is_file(): raise ValueError('Generated file is unavailable')
        if path.suffix.lower() not in self.output_extensions or path.name.startswith('.'):
            raise ValueError('This file is not a generated media output')
        return path

    def media(self):
        items = []
        for root, base in self.media_roots.items():
            if not base.exists() or base.resolve() != base: continue
            # Bounded scan, no following symlink directories.
            import os
            visited = 0
            for folder, directories, files in os.walk(base, followlinks=False):
                directories[:] = [d for d in directories if not (Path(folder) / d).is_symlink()]
                for name in files:
                    visited += 1
                    if visited > 5000: break
                    path = Path(folder) / name
                    if path.suffix.lower() not in self.output_extensions: continue
                    relative = path.relative_to(base).as_posix()
                    try: stat = self.media_path(root, relative).stat()
                    except (ValueError, OSError): continue
                    items.append({'root': root, 'path': relative, 'bytes': stat.st_size, 'modified': stat.st_mtime})
                if visited > 5000: break
        return {'files': sorted(items, key=lambda item: item['modified'], reverse=True)[:200]}

    def frame(self):
        with self.lock:
            now = time.monotonic()
            if self.last_frame is None or now - self.last_frame_at >= .12:
                self.last_frame = self.surface.frame()
                self.last_frame_at = now
            return self.last_frame


class RemoteServer(ThreadingHTTPServer):
    daemon_threads, allow_reuse_address, request_queue_size = True, True, 8
    def __init__(self, address, state, tls):
        self.state, self.tls, self.slots = state, tls, threading.BoundedSemaphore(8)
        super().__init__(address, RemoteHandler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        try: return self.tls.wrap_socket(connection, server_side=True), address
        except Exception: connection.close(); raise

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False): request.close(); return
        try: super().process_request(request, address)
        except Exception: self.slots.release(); raise

    def process_request_thread(self, request, address):
        try: super().process_request_thread(request, address)
        finally: self.slots.release()

    def handle_error(self, *_): pass


class RemoteHandler(BaseHTTPRequestHandler):
    server_version, sys_version = 'OpenCore', ''
    def log_message(self, *_): pass
    def send(self, status, value, content_type='application/json', headers=None):
        raw = json.dumps(value).encode() if content_type == 'application/json' else value
        self.send_response(status)
        for key, val in {'Content-Type': content_type, 'Content-Length': str(len(raw)), 'Cache-Control': 'no-store',
                         'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer', **(headers or {})}.items(): self.send_header(key, val)
        self.end_headers(); self.wfile.write(raw)

    def body(self):
        length = self.headers.get('Content-Length', '')
        if not length.isdigit() or not 0 < int(length) <= 128 * 1024 or self.headers.get('Transfer-Encoding'): raise ValueError('Invalid remote request size')
        value = json.loads(self.rfile.read(int(length)))
        if not isinstance(value, dict): raise ValueError('Expected a request object')
        return value

    def handle_api(self):
        if self.headers.get('Origin') or len(self.path) > 4096: raise PermissionError('Native paired clients only')
        state, parsed = self.server.state, urlsplit(self.path)
        if parsed.query or parsed.fragment: raise ValueError('Query parameters are not accepted')
        if self.command == 'POST' and parsed.path == '/pair':
            body = self.body(); return self.send(200, state.pair(body.get('code'), body.get('name', 'Android phone'), self.client_address[0]))
        device = state.authorize(self.headers.get('Authorization'))
        if not device: raise PermissionError('Pair this phone from the PC app')
        if self.command == 'GET' and parsed.path == '/info': return self.send(200, {'name': state.pc_name})
        if self.command == 'POST' and parsed.path == '/rename':
            state.rename(self.body().get('name')); return self.send(200, {'name': state.pc_name})
        if self.command == 'POST' and parsed.path == '/unpair':
            state.revoke(device); return self.send(200, {'removed': True})
        if self.command == 'GET' and parsed.path == '/frame':
            value, width, height = state.frame()
            return self.send(200, value, 'image/png', {'X-Frame-Width': str(width), 'X-Frame-Height': str(height)})
        if self.command == 'POST' and parsed.path == '/input':
            body = self.body()
            with state.lock:
                now = time.monotonic()
                if now - state.last_input.get(device, 0) < .008: raise ValueError('Input arrived too quickly')
                state.last_input[device] = now
            state.surface.input(body); return self.send(200, {'accepted': True})
        if self.command == 'GET' and parsed.path == '/media': return self.send(200, state.media())
        if self.command == 'POST' and parsed.path == '/file':
            body = self.body(); path = state.media_path(body.get('root'), body.get('path'))
            kind = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            with path.open('rb') as source:
                self.send_response(200)
                self.send_header('Content-Type', kind)
                self.send_header('Content-Length', str(os.fstat(source.fileno()).st_size))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(path.name))
                self.end_headers()
                for chunk in iter(lambda: source.read(65536), b''): self.wfile.write(chunk)
            return
        return self.send(404, {'error': 'Unknown remote endpoint'})

    def _handle(self):
        try: self.handle_api()
        except PermissionError as error: self.send(403, {'error': str(error)})
        except (ValueError, KeyError, OSError) as error:
            if isinstance(error, (BrokenPipeError, ConnectionError, socket.timeout)): return
            self.send(400, {'error': str(error) if isinstance(error, ValueError) else 'The generated file is unavailable'})
        except Exception: self.send(500, {'error': 'Remote request failed on the PC'})
    do_GET = do_POST = _handle
