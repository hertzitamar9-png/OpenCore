"""Cua reuses the upstream runtime while retaining OpenCore's owner permissions."""
import io
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'src-tauri/resources/runtime-setup/hf-extension'))
import hf_computer_driver as installer
import hf_computer_driver_mcp as mcp


class FakeDriver:
    def __init__(self, executable, manifest):
        self.manifest = json.loads(Path(manifest).read_text())
        self.stopped = threading.Event()
        self.process = self
        self.calls = []
    def poll(self):
        return 1 if self.stopped.is_set() else None
    def stop(self):
        self.stopped.set()
    def request(self, method, params):
        self.calls.append((method, params))
        if method == 'tools/list':
            return {'tools': [{'name': name, 'inputSchema': {'properties': {'delivery_mode': {'enum': ['background', 'foreground']}}}}
                              for name in ('click', 'list_apps', 'run_script', 'kill_app', 'clipboard_read')]}
        return {'content': [{'type': 'text', 'text': 'Upstream result'}, {'type': 'image', 'mimeType': 'image/png', 'data': 'image-bytes'}]}


class ComputerDriverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'state.sqlite3'
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            db.commit()
        self.policy = {'enabled': True, 'apps': [{'path': r'C:\Apps\Notes.exe', 'name': 'Notes', 'access': 'allow'}], 'revision': 1}
        self.save_policy()
        self.connection = installer.layout(self.root).parent / 'connection.json'
        self.connection.parent.mkdir(parents=True, exist_ok=True)
        self.connection.write_text('{"enabled":true}')

    def save_policy(self):
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('computer_access_v1', json.dumps(self.policy)))
            db.commit()

    def test_app_denial_wins_and_missing_connection_is_disabled(self):
        self.policy['apps'].append({'path': r'c:\APPS\notes.EXE', 'name': 'Notes', 'access': 'deny'})
        self.save_policy()
        self.assertEqual(mcp.read_policy(self.db, self.root)['apps'], [])
        self.connection.unlink()
        self.assertFalse(mcp.read_policy(self.db, self.root)['enabled'])

    def test_manifest_allows_inventory_but_scopes_window_control_and_excludes_files(self):
        manifest = mcp.capability_manifest(mcp.read_policy(self.db, self.root))
        self.assertEqual(manifest['resources'], {'desktop': {'display': True}, 'apps': [{'bundle_id': r'C:\Apps\Notes.exe', 'windows': 'all', 'launch': False, 'terminate': 'deny'}]})
        self.assertNotIn('run_script', manifest['allow']['tools'])
        self.assertNotIn('get_desktop_state', manifest['allow']['tools'])
        self.assertNotIn('clipboard_read', manifest['allow']['tools'])
        self.policy['enabled'] = False
        self.save_policy()
        self.assertEqual(mcp.capability_manifest(mcp.read_policy(self.db, self.root))['resources']['apps'], [])

    def test_no_foreground_or_script_escape_and_argument_input_is_preserved(self):
        for name, args in [('run_script', {}), ('kill_app', {}), ('click', {'delivery_mode': 'foreground'}),
                           ('click', {}), ('click', {'pid': 1, 'window_id': 2, 'scope': 'desktop'}),
                           ('click', {'pid': 1, 'window_id': 2, 'target': {'kind': 'display'}})]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                mcp.checked_arguments(name, args)
        arguments = {'element_token': 'snapshot:4', 'pid': 1, 'window_id': 2}
        self.assertEqual(mcp.checked_arguments('click', arguments), {**arguments, 'delivery_mode': 'background'})
        self.assertNotIn('delivery_mode', arguments)

    @patch.object(installer, 'verify_runtime')
    def test_bad_download_cannot_replace_existing_runtime(self, verify):
        manager = installer.ComputerDriverManager(self.root, self.root)
        manager.directory.mkdir(parents=True, exist_ok=True)
        old = manager.directory / 'cua-driver.exe'
        old.write_bytes(b'existing runtime')
        with patch.object(installer, 'urlopen', return_value=io.BytesIO(b'corrupt download')):
            manager._install()
        self.assertEqual(manager.status()['status'], 'failed')
        self.assertEqual(old.read_bytes(), b'existing runtime')

    @patch.object(mcp, 'verify_runtime', return_value=Path('driver.exe'))
    @patch.object(mcp, 'DriverClient', FakeDriver)
    def test_upstream_results_are_retained_and_revocation_stops_the_owned_driver(self, verify):
        service = mcp.ComputerMcp(self.root, self.db)
        self.addCleanup(service.close)
        tools = service.dispatch('tools/list', {})['tools']
        self.assertEqual([tool['name'] for tool in tools], ['click', 'list_apps'])
        self.assertEqual(tools[0]['inputSchema']['properties']['delivery_mode']['enum'], ['background'])
        result = service.dispatch('tools/call', {'name': 'list_apps', 'arguments': {}})
        self.assertEqual(result['content'][1]['data'], 'image-bytes')
        child = service.client
        self.policy.update(enabled=False, revision=2)
        self.save_policy()
        self.assertTrue(child.stopped.wait(2), 'Revocation must stop a pending native process without another model call')
        with self.assertRaisesRegex(ValueError, 'paused'):
            service.dispatch('tools/call', {'name': 'click', 'arguments': {}})

    @patch.object(mcp, 'verify_runtime', return_value=Path('driver.exe'))
    @patch.object(mcp, 'DriverClient', FakeDriver)
    def test_disconnect_revokes_existing_sessions_and_closed_session_cannot_restart(self, verify):
        service = mcp.ComputerMcp(self.root, self.db)
        self.addCleanup(service.close)
        service.dispatch('tools/list', {})
        child = service.client
        installer.ComputerDriverManager(self.root, self.root).disconnect()
        self.assertTrue(child.stopped.wait(2))
        service.close()
        with self.assertRaisesRegex(ValueError, 'closed'):
            service.dispatch('tools/list', {})

    @patch.object(mcp, 'verify_runtime', return_value=Path('driver.exe'))
    @patch.object(mcp, 'DriverClient', FakeDriver)
    def test_cancellation_does_not_replay_old_queued_actions(self, verify):
        service = mcp.ComputerMcp(self.root, self.db)
        self.addCleanup(service.close)
        generation = service.generation
        service.cancel()
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            service.dispatch('tools/call', {'name': 'click', 'arguments': {'pid': 1, 'window_id': 2}}, generation)
        self.assertIsNone(service.client)


if __name__ == '__main__':
    unittest.main()
