import subprocess
import unittest
from unittest.mock import patch
from mimo_verifier import docker


class ContainerTransport(unittest.TestCase):
    def test_linux_patch_bytes_are_not_newline_translated_by_windows(self):
        payload = 'diff --git a/src/file b/src/file\n+שלום\n'
        with patch('mimo_verifier.subprocess.run', return_value=subprocess.CompletedProcess([], 0, b'OK\n', b'')) as run:
            result = docker('exec', '-i', 'owned-test', 'git', 'apply', '--', input=payload)
        self.assertEqual(run.call_args.kwargs['input'], payload.encode('utf-8'))
        self.assertNotIn('text', run.call_args.kwargs)
        self.assertEqual(result.stdout, 'OK\n')


if __name__ == '__main__':
    unittest.main()
