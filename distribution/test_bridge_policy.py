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

    def test_registered_plugin_uses_only_the_user_scope_uninstall_and_owned_marketplace(self):
        with tempfile.TemporaryDirectory() as root:
            directory=Path(root)/'.claude';(directory/'plugins').mkdir(parents=True)
            registry=directory/'plugins/installed_plugins.json'
            registry.write_text(json.dumps({'version':2,'plugins':{PLUGIN:[{'scope':'user'}],'other@publisher':[{'scope':'user'}]}}))
            (directory/'plugins/known_marketplaces.json').write_text(json.dumps({'opencore-local':{'source':{'source':'directory','path':'owned-market'}}}))
            cli=Path(root)/'resources'/RELATIVE/'opencore-agent.exe';cli.parent.mkdir(parents=True);cli.write_bytes(b'MZ-test-cli')
            calls=[]
            def run(command,**options):
                args=command[1:];calls.append(args)
                self.assertEqual(options['env']['CLAUDE_CONFIG_DIR'],str(directory))
                value=json.loads(registry.read_bytes())
                if args[:2]==['plugin','uninstall']:
                    self.assertEqual(args,['plugin','uninstall',PLUGIN,'--scope','user'])
                    value['plugins'].pop(PLUGIN);registry.write_text(json.dumps(value))
                elif args[:3]==['plugin','marketplace','remove']:
                    self.assertEqual(args,['plugin','marketplace','remove','opencore-local','--scope','user'])
                output=[{'id':key,'scope':'user'} for key in value['plugins']]
                return type('Response',(),{'returncode':0,'stdout':json.dumps(output),'stderr':''})()
            with patch('subprocess.run',side_effect=run):
                self.assertTrue(retire_automatic_plugin(root,Path(root)/'resources'))
            self.assertEqual(json.loads(registry.read_bytes())['plugins'],{'other@publisher':[{'scope':'user'}]})
            self.assertIn(['plugin','marketplace','remove','opencore-local','--scope','user'],calls)

    def test_private_sdk_uses_the_separate_agent_path(self):
        runner=(Path(__file__).resolve().parents[1]/'src-tauri/resources/claude/runner.mjs').read_text(encoding='utf-8')
        self.assertIn('pathToClaudeCodeExecutable: privateAgentExecutable()',runner)
