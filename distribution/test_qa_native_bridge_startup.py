import importlib.util
from contextlib import closing
import os
from pathlib import Path
import subprocess
import sqlite3
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('bridge_startup_qa',Path(__file__).with_name('qa-native-bridge-startup.py'))
qa=importlib.util.module_from_spec(spec);spec.loader.exec_module(qa)


class StartupQaTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='nt','Windows known folders')
    def test_isolated_profile_resolves_windows_known_folders_before_native_startup(self):
        probe='''import ctypes,json,uuid
from ctypes import wintypes
result={}
for name,identity in [('roaming','3EB685DB-65F9-4CF6-A03A-E3EF65729F3D'),('local','F1B32785-6FBA-4FCF-9D55-7B8E7F157091')]:
    folder=(ctypes.c_byte*16).from_buffer_copy(uuid.UUID(identity).bytes_le)
    value=ctypes.c_wchar_p();shell=ctypes.WinDLL('shell32');shell.SHGetKnownFolderPath.restype=ctypes.c_long
    status=shell.SHGetKnownFolderPath(ctypes.byref(folder),0,None,ctypes.byref(value))
    result[name]={'status':status,'path':value.value}
    if value:ctypes.OleDLL('ole32').CoTaskMemFree(value)
print(json.dumps(result))'''
        import json,sys
        with tempfile.TemporaryDirectory(prefix='opencore-known-folders-qa-') as directory:
            home=Path(directory)/'home';home.mkdir()
            # This is the actual Windows failure, reproduced without launching
            # OpenCore or touching the current user's application state.
            missing=subprocess.run([sys.executable,'-I','-c',probe],env=dict(os.environ,USERPROFILE=str(home)),capture_output=True,text=True,check=True,timeout=10)
            self.assertNotEqual(json.loads(missing.stdout)['roaming']['status'],0)
            environment=qa.isolated_windows_environment(home)
            result=subprocess.run([sys.executable,'-I','-c',probe],env=environment,capture_output=True,text=True,check=True,timeout=10)
            paths=json.loads(result.stdout)
            for key in ('roaming','local'):self.assertEqual(paths[key]['status'],0,paths)
            self.assertEqual(Path(paths['roaming']['path']),Path(environment['APPDATA']))
            self.assertEqual(Path(paths['local']['path']),Path(environment['LOCALAPPDATA']))

    def test_fresh_startup_check_rejects_imported_external_history(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory)/'app.sqlite3'
            with closing(sqlite3.connect(database)) as connection:
                connection.executescript('CREATE TABLE conversations(id TEXT,client TEXT); CREATE TABLE timeline(content TEXT); CREATE TABLE operations(kind TEXT);')
                connection.execute("INSERT INTO conversations VALUES ('native','OpenCore')")
                connection.commit()
            qa.assert_no_automatic_history(database)
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("INSERT INTO conversations VALUES ('codex:fixture','Codex')")
                connection.commit()
            with self.assertRaisesRegex(RuntimeError, 'imported external history'):
                qa.assert_no_automatic_history(database)

    def test_fresh_startup_check_rejects_sync_even_before_the_first_imported_row(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory)/'app.sqlite3'
            with closing(sqlite3.connect(database)) as connection:
                connection.executescript('CREATE TABLE conversations(id TEXT,client TEXT); CREATE TABLE timeline(content TEXT); CREATE TABLE operations(kind TEXT);')
                connection.execute("INSERT INTO operations VALUES ('history_sync')")
                connection.commit()
            with self.assertRaisesRegex(RuntimeError, 'history sync'):
                qa.assert_no_automatic_history(database)

    def test_external_fixture_is_real_source_history_and_new_profile_writes_are_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)/'home'
            before = qa.seed_external_sources(home)
            self.assertIn('.claude/projects/consent-fixture/claude-consent.jsonl', before)
            self.assertIn('.codex/sessions/2026/10/01/codex-consent.jsonl', before)
            self.assertIn('.claude/plugins/installed_plugins.json', before)
            self.assertIn(b'OpenCore startup consent fixture', before['.claude/projects/consent-fixture/claude-consent.jsonl'])
            self.assertIn(b'"type": "response_item"', before['.codex/sessions/2026/10/01/codex-consent.jsonl'])
            qa.assert_sources_preserved(home, before)
            (home/'.claude/opencore-settings.json').write_text('{}')
            with self.assertRaisesRegex(RuntimeError, 'changed external client files'):
                qa.assert_sources_preserved(home, before)

    def test_launcher_exit_zero_is_followed_to_the_live_relaunched_app(self):
        process=type('Process',(),{'pid':101,'poll':lambda self:0})()
        self.assertEqual(qa.wait_for_relaunched_app(process,lambda:{202},lambda:'[setup] Tauri setup completed',timeout=1),{202})

    def test_a_successful_launcher_alone_does_not_count_as_app_startup(self):
        process=type('Process',(),{'pid':101,'poll':lambda self:0})()
        with self.assertRaisesRegex(RuntimeError,'did not report completed startup'):
            qa.wait_for_relaunched_app(process,lambda:set(),lambda:'[setup] Tauri setup completed',timeout=.01)

    def test_a_failed_launcher_is_reported(self):
        process=type('Process',(),{'pid':101,'poll':lambda self:7})()
        with self.assertRaisesRegex(RuntimeError,'launcher failed: 7'):
            qa.wait_for_relaunched_app(process,lambda:set(),lambda:'',timeout=1)

    @unittest.skipUnless(os.name=='nt','Windows process enumeration')
    def test_process_check_follows_and_stops_only_the_relaunched_fixture(self):
        compiler=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        if not compiler.is_file():self.skipTest('The .NET Framework compiler is unavailable')
        with tempfile.TemporaryDirectory(prefix='opencore-process-qa-') as directory:
            root=Path(directory);source=root/'fixture.cs';exe=root/'OpenCoreQaFixture.exe'
            source.write_text('using System.Diagnostics; using System.Reflection; class Fixture { static void Main(string[] args) { if(args.Length==0) { Process.Start(new ProcessStartInfo { FileName=Assembly.GetExecutingAssembly().Location, Arguments="child", UseShellExecute=false, CreateNoWindow=true }); return; } System.Threading.Thread.Sleep(30000); } }',encoding='utf-8')
            subprocess.run([str(compiler),'/nologo','/target:exe','/out:'+str(exe),str(source)],
                           check=True,capture_output=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
            process=subprocess.Popen([str(exe)],creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                self.assertEqual(process.wait(timeout=10),0)
                children=qa.wait_for_relaunched_app(process,lambda:qa._windows_processes(exe),
                    lambda:'[setup] Tauri setup completed',timeout=10)
                self.assertTrue(children)
                self.assertTrue(children.issubset(qa._windows_processes(exe,terminate=True)))
            finally:
                qa._windows_processes(exe,terminate=True)
                if process.poll() is None:process.terminate();process.wait(timeout=10)


if __name__=='__main__':unittest.main()
