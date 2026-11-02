import json
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]/'src-tauri/resources/runtime-setup/hf-extension'))
from hf_connectors import ConnectorProfiles, without_codex_profile


class ConnectorConsentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)/'home'
        self.home.mkdir()
        self.root = Path(self.temporary.name)/'OpenCore'
        self.manager = ConnectorProfiles(self.root, home=self.home, environ={})

    def test_fresh_install_keeps_every_connector_off_even_when_client_profiles_exist(self):
        for relative, content in {
            '.claude/settings.json': '{"theme":"dark"}',
            '.claude/projects/source/chat.jsonl': 'original claude history',
            '.codex/config.toml': '[profiles.opencore]\nmodel_provider="opencore"\n',
            '.codex/auth.json': '{"token":"keep"}',
            '.codex/sessions/source.jsonl': 'original codex history',
        }.items():
            path = self.home/relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
        before = {path.relative_to(self.home): path.read_bytes() for path in self.home.rglob('*') if path.is_file()}
        self.assertEqual(self.manager.status()['disconnected'],
                         {'claude-code': True, 'codex': True, 'hermes': True, 'opencode': True})
        self.assertFalse(self.root.exists(), 'Reading connector state must not create profiles or consent')
        self.assertEqual({path.relative_to(self.home): path.read_bytes() for path in self.home.rglob('*') if path.is_file()}, before)

    def test_explicit_profile_enable_survives_restart_for_only_the_selected_client(self):
        self.manager.reconnect('codex')
        restarted = ConnectorProfiles(self.root, home=self.home, environ={})
        self.assertEqual(restarted.status()['disconnected'],
                         {'claude-code': True, 'hermes': True, 'opencode': True})
        self.assertEqual(restarted.status()['historyEnabled'], {})
        self.assertEqual(list(self.home.iterdir()), [], 'Consent does not create an external profile')

    def test_legacy_disconnect_file_or_generated_profile_is_not_enable_consent(self):
        self.manager.path.parent.mkdir(parents=True)
        self.manager.path.write_text('{"disconnected":{"hermes":true}}', encoding='utf-8')
        self.assertEqual(self.manager.status()['disconnected'],
                         {'claude-code': True, 'codex': True, 'hermes': True, 'opencode': True})

    def test_identifiable_explicit_enable_state_is_retained(self):
        self.manager.path.parent.mkdir(parents=True)
        self.manager.path.write_text('{"enabled":{"codex":true,"hermes":"true"},"historyEnabled":{"claude-code":true},"disconnected":{}}', encoding='utf-8')
        self.assertEqual(self.manager.status()['disconnected'],
                         {'claude-code': True, 'hermes': True, 'opencode': True})
        self.assertEqual(self.manager.status()['historyEnabled'], {'claude-code': True})

    def test_explicit_history_import_does_not_enable_or_create_a_client_profile(self):
        self.manager.enable_history('claude-code')
        restarted = ConnectorProfiles(self.root, home=self.home, environ={})
        self.assertEqual(restarted.status()['disconnected'],
                         {'claude-code': True, 'codex': True, 'hermes': True, 'opencode': True})
        self.assertTrue(restarted.history_enabled('claude-code'))
        self.assertFalse(restarted.history_enabled('codex'))
        self.assertEqual(list(self.home.iterdir()), [])

    def test_disconnect_revokes_the_selected_history_and_profile_permissions(self):
        self.manager.reconnect('codex')
        self.manager.enable_history('codex')
        self.manager.enable_history('claude-code')
        self.manager.disconnect('codex')
        self.assertTrue(self.manager.status()['disconnected']['codex'])
        self.assertFalse(self.manager.history_enabled('codex'))
        self.assertTrue(self.manager.history_enabled('claude-code'))


class ConnectorDisconnectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)/'home'; self.home.mkdir()
        self.manager = ConnectorProfiles(Path(self.temporary.name)/'OpenCore', home=self.home,
                                         environ={'LOCALAPPDATA': str(self.home/'AppData/Local')})

    def file(self, name, text):
        path = self.home/name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8'); return path

    def test_codex_disconnect_retires_only_owned_tables_and_persists_across_restart(self):
        raw = ('model="cloud-model"\n[profiles.work]\nmodel="other"\n'
               '[model_providers.opencore]\nbase_url="http://127.0.0.1:8812/v1"\nexperimental_bearer_token="opencore-local"\n'
               '[profiles.opencore]\nmodel="opencore"\nmodel_provider="opencore"\n'
               '[projects."C:/project"]\ntrust_level="trusted"\n')
        path = self.file('.codex/config.toml', raw)
        auth = self.file('.codex/auth.json', '{"token":"keep"}')
        chat = self.file('.codex/sessions/chat.jsonl', 'original chat')
        self.manager.disconnect('codex')
        saved = tomllib.loads(path.read_text())
        self.assertEqual(saved['model'], 'cloud-model')
        self.assertEqual(saved['profiles'], {'work': {'model': 'other'}})
        self.assertEqual(saved['projects']['C:/project']['trust_level'], 'trusted')
        self.assertNotIn('opencore', saved.get('model_providers', {}))
        self.assertEqual(auth.read_text(), '{"token":"keep"}'); self.assertEqual(chat.read_text(), 'original chat')
        self.assertEqual(next(path.parent.glob('*.bak')).read_text(), raw)
        restarted = ConnectorProfiles(self.manager.root, home=self.home, environ={})
        self.assertTrue(restarted.status()['disconnected']['codex'])
        self.assertNotIn('codex', restarted.reconnect('codex')['disconnected'])

    def test_table_like_text_in_multiline_toml_is_preserved(self):
        text = 'note = """\n[profiles.opencore]\nnot a profile\n"""\n[profiles.work]\nmodel="safe"\n'
        self.assertEqual(without_codex_profile(text), text)

    def test_unowned_or_inline_codex_profile_is_rejected_without_modification(self):
        for text in ('[model_providers.opencore]\nbase_url="https://other.example"\n',
                     'profiles={opencore={model_provider="opencore"}}\n'):
            path = self.file('.codex/config.toml', text)
            with self.assertRaises(ValueError): self.manager.disconnect('codex')
            self.assertEqual(path.read_text(), text)
            self.assertFalse(self.manager.path.exists(), 'A failed disconnect must not persist consent')

    def test_claude_disconnect_preserves_account_settings_and_imported_source_files(self):
        path = self.file('.claude/opencore-settings.json', json.dumps({'env': {'ANTHROPIC_BASE_URL': 'http://127.0.0.1:8812',
                         'ANTHROPIC_AUTH_TOKEN': 'opencore-local', 'CUSTOM': 'keep'}, 'permissions': {'allow': ['Read']}}))
        defaults = self.file('.claude/settings.json', '{"theme":"dark"}')
        transcript = self.file('.claude/projects/test/conversation.jsonl', 'source')
        self.manager.disconnect('claude-code')
        self.assertEqual(json.loads(path.read_text())['env'], {'CUSTOM': 'keep'})
        self.assertEqual(defaults.read_text(), '{"theme":"dark"}')
        self.assertEqual(transcript.read_text(), 'source')

    def test_opencode_jsonc_removes_only_local_provider_and_local_model_selection(self):
        path = self.file('.config/opencode/opencode.jsonc', '''// user config
        {"provider":{"cloud":{"apiKey":"keep"},"opencore":{"options":{"baseURL":"http://127.0.0.1:8812/v1"}}},
         "model":"opencore/opencore","small_model":"cloud/small","enabled_providers":["cloud","opencore"],
         "description":"literal ,} and https://example.com/a/*x*/",}''')
        self.manager.disconnect('opencode')
        value = json.loads(path.read_text())
        self.assertEqual(value['provider'], {'cloud': {'apiKey': 'keep'}})
        self.assertNotIn('model', value)
        self.assertEqual(value['small_model'], 'cloud/small')
        self.assertEqual(value['enabled_providers'], ['cloud'])
        self.assertEqual(value['description'], 'literal ,} and https://example.com/a/*x*/')

    def test_hermes_retires_only_opt_in_config_and_keeps_profile_data(self):
        path = self.file('AppData/Local/hermes/profiles/opencore/config.yaml',
                         'model:\n  base_url: http://127.0.0.1:8812/v1\n  default: opencore\n')
        source = self.file('AppData/Local/hermes/config.yaml', 'model: cloud\n')
        chats = self.file('AppData/Local/hermes/profiles/opencore/state.db', 'keep')
        self.manager.disconnect('hermes')
        self.assertFalse(path.exists()); self.assertEqual(len(list(path.parent.glob('*.bak'))), 1)
        self.assertEqual(source.read_text(), 'model: cloud\n'); self.assertEqual(chats.read_text(), 'keep')

    def test_failed_state_save_rolls_back_the_profile_and_keeps_backup(self):
        path = self.file('.claude/opencore-settings.json', '{"env":{"ANTHROPIC_BASE_URL":"http://127.0.0.1:8812"}}')
        raw = path.read_bytes()
        with patch('hf_connectors.atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.manager.disconnect('claude-code')
        self.assertEqual(path.read_bytes(), raw)
        self.assertFalse(self.manager.path.exists(), 'A failed state save must not persist consent')

    def test_unknown_ids_and_profile_links_are_rejected(self):
        with self.assertRaises(ValueError): self.manager.disconnect('../../anywhere')
        target = self.file('elsewhere.json', '{}')
        path = self.home/'.claude/opencore-settings.json'; path.parent.mkdir(exist_ok=True)
        try: path.symlink_to(target)
        except OSError: return
        with self.assertRaises(ValueError): self.manager.disconnect('claude-code')
        self.assertEqual(target.read_text(), '{}')


if __name__ == '__main__': unittest.main()
