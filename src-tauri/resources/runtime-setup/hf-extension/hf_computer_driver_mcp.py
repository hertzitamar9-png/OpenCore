"""Permission adapter for upstream Cua MCP. The agent loop stays in Codex."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import ntpath
import os
from pathlib import Path
import queue
import sqlite3
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hf_computer_driver import CONFIG, layout, verify_runtime

TOOLS = frozenset(('list_apps', 'list_windows', 'get_window_state', 'verify_state', 'click',
    'double_click', 'right_click', 'drag', 'type_text', 'press_key', 'hotkey', 'set_value', 'scroll', 'get_guide'))
INPUTS = TOOLS - {'list_apps', 'list_windows', 'get_window_state', 'verify_state', 'get_guide'}
LIMIT = 32 * 1024 * 1024


def read_policy(database, root=None):
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True, timeout=.25)) as db:
        row = db.execute("SELECT value FROM settings WHERE key='computer_access_v1'").fetchone()
    policy = json.loads(row[0]) if row else {'enabled': False, 'apps': [], 'revision': 0}
    if not isinstance(policy, dict) or type(policy.get('enabled')) is not bool or not isinstance(policy.get('apps'), list) or len(policy['apps']) > 256:
        raise ValueError('Computer permissions are invalid; access remains blocked')
    apps, denied = {}, set()
    for app in policy['apps']:
        path = app.get('path') if isinstance(app, dict) else None
        if not isinstance(path, str) or not ntpath.isabs(path) or '\x00' in path or len(path) > 32768 or app.get('access') not in ('allow', 'deny'):
            raise ValueError('Computer application permissions are invalid; access remains blocked')
        key = ntpath.normcase(ntpath.normpath(path)).removeprefix('\\\\?\\')
        if app['access'] == 'deny':
            denied.add(key)
        else:
            apps[key] = path
    connected = True
    if root is not None:
        connection = layout(root).parent / 'connection.json'
        value = json.loads(connection.read_text(encoding='utf-8')) if connection.is_file() and connection.stat().st_size <= 1024 else None
        connected = isinstance(value, dict) and value.get('enabled') is True
    return {'enabled': policy['enabled'] and connected, 'revision': policy.get('revision', 0),
            'apps': [path for key, path in sorted(apps.items()) if key not in denied]}


def capability_manifest(policy):
    return {'version': 3, 'expires_after': '24h', 'idle_timeout': '30m',
            'allow': {'tools': sorted(TOOLS)}, 'resources': {'desktop': {'display': True}, 'apps': [
                # Upstream's Windows-path bundle identity checks the OS-attested
                # executable and normalizes drive case and extended path prefixes.
                {'bundle_id': path, 'windows': 'all', 'launch': False, 'terminate': 'deny'}
                for path in policy['apps'] if policy['enabled']]}}


def checked_arguments(name, arguments):
    if name not in TOOLS or not isinstance(arguments, dict):
        raise ValueError('This Cua tool is outside OpenCore computer permissions')
    arguments = dict(arguments)
    if arguments.get('delivery_mode', 'background') != 'background':
        raise ValueError('OpenCore Cua uses background control. Foreground input is not enabled.')
    if name in INPUTS:
        # Display observation admits the app/window inventory, not global input.
        # Require the window form so the upstream adapter attests its executable
        # against resources.apps; no tool may turn this into display-wide input.
        if (type(arguments.get('pid')) is not int or arguments['pid'] <= 0
                or type(arguments.get('window_id')) is not int or arguments['window_id'] <= 0
                or 'target' in arguments or arguments.get('scope', 'window') != 'window'):
            raise ValueError('Input requires pid and window_id from fresh window state; desktop targets are not enabled.')
        arguments['delivery_mode'] = 'background'
    return arguments


class DriverClient:
    def __init__(self, executable, manifest):
        self.waiters, self.lock, self.counter = {}, threading.RLock(), 0
        env = dict(os.environ, CUA_DRIVER_PERMISSION_MODE='bounded', CUA_DRIVER_EMBEDDED='1',
                   CUA_DRIVER_CAPABILITY_MANIFEST_FILE=str(manifest), CUA_DRIVER_CAPABILITY_MANIFEST_APPROVED='1', DO_NOT_TRACK='1')
        self.process = subprocess.Popen([str(executable), 'mcp', '--direct', '--embedded'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.job = None
        if os.name == 'nt':
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from setup_manager import WindowsJob
            self.job = WindowsJob(self.process)
        threading.Thread(target=self._read, daemon=True).start()
        self.request('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
            'clientInfo': {'name': 'OpenCore', 'version': '1'}})
        self.notify('notifications/initialized', {})

    def _read(self):
        try:
            while line := self.process.stdout.readline(LIMIT + 1):
                if len(line) > LIMIT:
                    raise ValueError('Cua Driver response exceeded the supported size')
                value = json.loads(line)
                with self.lock:
                    waiter = self.waiters.get(value.get('id'))
                if waiter:
                    waiter.put(value)
        except (ValueError, OSError):
            pass
        finally:
            with self.lock:
                for waiter in self.waiters.values():
                    waiter.put({'error': {'message': 'Cua Driver stopped. Inspect fresh state before retrying an action.'}})

    def notify(self, method, params):
        with self.lock:
            self.process.stdin.write((json.dumps({'jsonrpc': '2.0', 'method': method, 'params': params}) + '\n').encode())
            self.process.stdin.flush()

    def request(self, method, params):
        with self.lock:
            if self.process.poll() is not None:
                raise ValueError('Cua Driver is no longer running; inspect fresh state before retrying')
            self.counter += 1
            number, waiter = self.counter, queue.Queue()
            self.waiters[number] = waiter
            self.process.stdin.write((json.dumps({'jsonrpc': '2.0', 'id': number, 'method': method, 'params': params}) + '\n').encode())
            self.process.stdin.flush()
        try:
            value = waiter.get(timeout=35)
            if 'error' in value:
                raise ValueError(str(value['error'].get('message', 'Cua Driver request failed')))
            return value.get('result', {})
        except queue.Empty:
            self.stop()
            raise ValueError('Cua Driver timed out. The action outcome is unknown; inspect fresh state before retrying.') from None
        finally:
            with self.lock:
                self.waiters.pop(number, None)

    def stop(self):
        if self.job:
            self.job.stop()
            self.job.close()
        elif self.process.poll() is None:
            self.process.kill()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()


class ComputerMcp:
    def __init__(self, root, database):
        self.root, self.database = Path(root), Path(database)
        self.executable = verify_runtime(layout(root), hashes=True)
        self.client, self.policy, self.manifest = None, None, None
        self.generation = 0
        self.guard = threading.RLock()
        self.serial = threading.Lock()
        self.closed = threading.Event()
        self.watcher = threading.Thread(target=self._watch, daemon=True)
        self.watcher.start()

    def _watch(self):
        while not self.closed.wait(.5):
            try:
                current = read_policy(self.database, self.root)
            except (ValueError, OSError, sqlite3.Error):
                current = None
            with self.guard:
                if self.client and current != self.policy:
                    self.generation += 1
                    self._stop_locked()

    def _stop_locked(self):
        if self.client:
            self.client.stop()
        self.client = None
        if self.manifest:
            self.manifest.unlink(missing_ok=True)
            self.manifest = None

    def cancel(self):
        with self.guard:
            self.generation += 1
            self._stop_locked()

    def close(self):
        self.closed.set()
        self.cancel()
        if self.watcher is not threading.current_thread():
            self.watcher.join(timeout=1)

    def _driver(self, policy):
        with self.guard:
            if self.closed.is_set():
                raise ValueError('The computer-use connection has closed')
            if policy != read_policy(self.database, self.root):
                raise ValueError('Computer permissions changed. Inspect fresh state before continuing.')
            if self.client and self.policy == policy and self.client.process.poll() is None:
                return self.client
            self._stop_locked()
            directory = layout(self.root).parent / 'sessions'
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', dir=directory, delete=False, encoding='utf-8') as output:
                json.dump(capability_manifest(policy), output)
                self.manifest = Path(output.name)
            self.policy = policy
            self.client = DriverClient(self.executable, self.manifest)
            return self.client

    def dispatch(self, method, params, generation=None):
        if method == 'initialize':
            return {'protocolVersion': '2025-06-18', 'capabilities': {'tools': {}},
                    'serverInfo': {'name': 'OpenCore-Cua-Driver', 'version': CONFIG['version']},
                    'instructions': 'Cua Driver is connected for native desktop GUI tasks. Use its app/window discovery, fresh window state and input tools; terminal work stays with the harness terminal tools. App access follows OpenCore Settings > Computer use. Only background input is enabled. Verify the result from fresh state after every action.'}
        if method == 'ping':
            return {}
        with self.serial:
            if self.closed.is_set():
                raise ValueError('The computer-use connection has closed')
            if generation is not None and generation != self.generation:
                raise ValueError('This computer-use request was cancelled; inspect fresh state before continuing.')
            policy = read_policy(self.database, self.root)
            if method == 'tools/call':
                if not policy['enabled']:
                    raise ValueError('Computer use is paused. Enable it in OpenCore Settings > Computer use.')
                params = {'name': params.get('name'), 'arguments': checked_arguments(params.get('name'), params.get('arguments', {}))}
            elif method != 'tools/list':
                raise ValueError('Unsupported MCP method')
            driver = self._driver(policy)
            result = driver.request(method, params)
            if method == 'tools/list':
                result['tools'] = [tool for tool in result.get('tools', []) if tool['name'] in TOOLS]
                for tool in result['tools']:
                    if tool['name'] in INPUTS:
                        schema = tool['inputSchema']
                        schema['required'] = sorted(set(schema.get('required', [])) | {'pid', 'window_id'})
                        schema.get('properties', {}).pop('target', None)
                        if 'scope' in schema.get('properties', {}):
                            schema['properties']['scope'] = {'type': 'string', 'enum': ['window'], 'default': 'window'}
                    delivery = tool.get('inputSchema', {}).get('properties', {}).get('delivery_mode')
                    if delivery:
                        delivery.update(enum=['background'], default='background',
                            description='OpenCore permits background input only; it keeps the current app in front.')
            return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--database', type=Path, required=True)
    args = parser.parse_args()
    service = ComputerMcp(args.root, args.database)
    output_lock = threading.Lock()
    executor = ThreadPoolExecutor(max_workers=1)

    def respond(message, generation):
        try:
            result = service.dispatch(message['method'], message.get('params') or {}, generation)
            response = {'jsonrpc': '2.0', 'id': message['id'], 'result': result}
        except Exception as error:
            response = {'jsonrpc': '2.0', 'id': message['id'], 'error': {'code': -32603, 'message': str(error)}}
        with output_lock:
            sys.stdout.write(json.dumps(response, ensure_ascii=True, allow_nan=False) + '\n')
            sys.stdout.flush()

    try:
        while line := sys.stdin.buffer.readline(2 * 1024 * 1024 + 1):
            if len(line) > 2 * 1024 * 1024:
                break
            message = json.loads(line)
            if message.get('method') == 'notifications/cancelled':
                service.cancel()
            elif 'id' in message:
                executor.submit(respond, message, service.generation)
    finally:
        service.close()
        executor.shutdown(wait=True, cancel_futures=True)


if __name__ == '__main__':
    main()
