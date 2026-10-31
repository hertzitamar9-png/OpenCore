import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from bridge_policy import prepare, RELATIVE
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src-tauri/resources/runtime-setup/hf-extension'))
from hf_bridge_policy import retire_automatic_plugin, PLUGIN


class BridgePolicyTests(unittest.TestCase):
    def test_fresh_package_cannot_invoke_legacy_plugin_installer_and_keeps_private_agent(self):
        with tempfile.TemporaryDirectory() as root:
            directory=Path(root)/RELATIVE;directory.mkdir(parents=True)
            original=b'MZ-private-agent-fixture';(directory/'claude.exe').write_bytes(original)
            result=prepare(root)
            self.assertTrue(result['automaticPluginInstallerDisabled'])
            self.assertEqual((directory/'opencore-agent.exe').read_bytes(),original)
            self.assertFalse((directory/'claude.exe').read_bytes().startswith(b'MZ'))
            self.assertEqual(result['privateAgentSha256'],hashlib.sha256(original).hexdigest())

    def test_fresh_profile_has_no_external_writes_or_plugin_installation(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertFalse(retire_automatic_plugin(root))
            self.assertEqual(list(Path(root).iterdir()),[])

    def test_update_removes_only_the_legacy_setting_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            directory=Path(root)/'.claude';directory.mkdir()
            path=directory/'settings.json'
            initial={'enabledPlugins':{PLUGIN:True,'other@publisher':True},'env':{'EXAMPLE':'keep'},'permissions':{'allow':['Read']}}
            raw=json.dumps(initial).encode();path.write_bytes(raw)
            self.assertTrue(retire_automatic_plugin(root))
            initial['enabledPlugins'].pop(PLUGIN)
            self.assertEqual(json.loads(path.read_bytes()),initial)
            self.assertEqual((directory/'settings.json.before-opencore-removal.bak').read_bytes(),raw)
            self.assertFalse(retire_automatic_plugin(root))

    def test_disabled_legacy_plugin_is_removed_too_without_starting_a_cli(self):
        with tempfile.TemporaryDirectory() as root:
            directory=Path(root)/'.claude';directory.mkdir()
            path=directory/'settings.json'
            path.write_text(json.dumps({'enabledPlugins':{PLUGIN:False,'other@publisher':True}}))
            with patch('subprocess.run',side_effect=AssertionError('No registered plugin to uninstall')):
                self.assertTrue(retire_automatic_plugin(root))
            self.assertEqual(json.loads(path.read_bytes()),{'enabledPlugins':{'other@publisher':True}})

    def test_registered_plugin_is_retired_without_starting_any_client(self):
        with tempfile.TemporaryDirectory() as root:
            directory=Path(root)/'.claude';(directory/'plugins').mkdir(parents=True)
            registry=directory/'plugins/installed_plugins.json'
            registry.write_text(json.dumps({'version':2,'plugins':{PLUGIN:[{'scope':'user'}],'other@publisher':[{'scope':'user'}]}}))
            marketplace=directory/'plugins/known_marketplaces.json'
            marketplace.write_text(json.dumps({'opencore-local':{'source':{'source':'directory','path':'owned-market'}},'other':{'source':'keep'}}))
            with patch('subprocess.run',side_effect=AssertionError('No external client should be started')):
                self.assertTrue(retire_automatic_plugin(root,Path(root)/'resources'))
            self.assertEqual(json.loads(registry.read_bytes())['plugins'],{'other@publisher':[{'scope':'user'}]})
            self.assertEqual(json.loads(marketplace.read_bytes()),{'other':{'source':'keep'}})
            self.assertFalse(retire_automatic_plugin(root,Path(root)/'resources'))

    def test_shared_marketplace_and_unrelated_plugins_are_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            directory=Path(root)/'.claude';(directory/'plugins').mkdir(parents=True)
            registry=directory/'plugins/installed_plugins.json'
            registry.write_text(json.dumps({'plugins':{PLUGIN:[{'scope':'user'},{'scope':'project','projectPath':'keep'}],'other@opencore-local':[{'scope':'user'}]}}))
            marketplace=directory/'plugins/known_marketplaces.json';marketplace.write_text(json.dumps({'opencore-local':{'source':'keep'}}))
            with patch('subprocess.run',side_effect=AssertionError('No external client should be started')):
                self.assertTrue(retire_automatic_plugin(root,Path(root)/'resources'))
            self.assertNotIn(PLUGIN,json.loads(registry.read_bytes())['plugins'])
            self.assertIn('other@opencore-local',json.loads(registry.read_bytes())['plugins'])
            self.assertIn('opencore-local',json.loads(marketplace.read_bytes()))

    def test_shared_marketplace_is_kept_when_another_plugin_is_enabled(self):
        with tempfile.TemporaryDirectory() as root:
            directory=Path(root)/'.claude';(directory/'plugins').mkdir(parents=True)
            settings=directory/'settings.json'
            settings.write_text(json.dumps({'enabledPlugins':{PLUGIN:True,'other@opencore-local':True},'extraKnownMarketplaces':{'opencore-local':{'source':'keep'}}}))
            marketplace=directory/'plugins/known_marketplaces.json';marketplace.write_text(json.dumps({'opencore-local':{'source':'keep'}}))
            self.assertTrue(retire_automatic_plugin(root))
            self.assertIn('other@opencore-local',json.loads(settings.read_bytes())['enabledPlugins'])
            self.assertIn('opencore-local',json.loads(settings.read_bytes())['extraKnownMarketplaces'])
            self.assertIn('opencore-local',json.loads(marketplace.read_bytes()))

    def test_private_sdk_uses_the_separate_agent_path(self):
        runner=(Path(__file__).resolve().parents[1]/'src-tauri/resources/claude/runner.mjs').read_text(encoding='utf-8')
        self.assertIn('pathToClaudeCodeExecutable: privateAgentExecutable()',runner)
        self.assertIn('settingSources: []',runner)
        self.assertIn('strictMcpConfig: true',runner)
        self.assertIn("if (!existsSync(path)) throw new Error",runner)
