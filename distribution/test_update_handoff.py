"""Exercise the updater's exact-path stop logic with disposable processes."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

import package


class UpdateHandoff(unittest.TestCase):
    def test_installer_only_stops_processes_during_an_explicit_update(self):
        script = package.installer_script(app=[], python=[], output=Path('out'), version='0.3.174',
            icon=Path('icon.ico'), branding=Path('branding'), webview=Path('webview.exe'))
        self.assertIn('stop-opencore-update.ps1', script)
        self.assertIn('$UpdateInstall == "1"', script)
        self.assertIn('-AppPath "$INSTDIR\\OpenCore.exe"', script)

    @unittest.skipUnless(os.name == 'nt', 'Windows process handoff')
    def test_restarted_app_is_stopped_but_another_installation_is_left_running(self):
        compiler = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        if not compiler.is_file(): self.skipTest('Windows C# compiler unavailable')
        helper = Path(__file__).with_name('stop-opencore-update.ps1').resolve()
        creation = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        with tempfile.TemporaryDirectory(prefix='opencore-handoff-') as temp:
            root = Path(temp)
            source = root/'fixture.cs'
            source.write_text('using System.Threading; class Program { static void Main() { Thread.Sleep(60000); } }')
            target, other = root/'target/OpenCore.exe', root/'other/OpenCore.exe'
            target.parent.mkdir(); other.parent.mkdir()
            subprocess.run([str(compiler), '/nologo', '/target:winexe', '/out:'+str(target), str(source)],
                           check=True, capture_output=True, creationflags=creation)
            shutil.copyfile(target, other)
            processes = []
            try:
                processes.extend([subprocess.Popen([str(target)], creationflags=creation),
                                  subprocess.Popen([str(other)], creationflags=creation)])
                handoff = subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                    '-File', str(helper), '-AppPath', str(target)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creation)
                time.sleep(1)
                restarted = subprocess.Popen([str(target)], creationflags=creation)
                processes.append(restarted)
                out, err = handoff.communicate(timeout=30)
                self.assertEqual(handoff.returncode, 0, (out+err).decode(errors='replace'))
                self.assertIsNotNone(processes[0].poll())
                self.assertIsNotNone(restarted.poll())
                self.assertIsNone(processes[1].poll(), 'The updater stopped a different installation')
            finally:
                for proc in processes:
                    if proc.poll() is None: proc.terminate(); proc.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
