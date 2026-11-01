import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('bridge_startup_qa',Path(__file__).with_name('qa-native-bridge-startup.py'))
qa=importlib.util.module_from_spec(spec);spec.loader.exec_module(qa)


class StartupQaTests(unittest.TestCase):
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
