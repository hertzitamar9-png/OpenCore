from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PhoneRuntimeTests(unittest.TestCase):
    def test_policy_and_metadata(self):
        java = Path(os.environ['JAVA_HOME']) / 'bin'
        sources = ROOT / 'src/ai/opencore/remote'
        with tempfile.TemporaryDirectory(prefix='opencore-phone-tests-') as output:
            result = subprocess.run([str(java/'javac.exe'), '--release', '8', '-encoding', 'UTF-8', '-d', output,
                                     str(sources/'ExecutionPolicy.java'), str(sources/'GgufMetadata.java'), str(sources/'PhoneOperationGate.java'),
                                     str(ROOT/'tests/PhoneRuntimeTest.java')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(java/'java.exe'), '-cp', output, 'ai.opencore.remote.PhoneRuntimeTest'],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('passed', result.stdout)


if __name__ == '__main__': unittest.main()
