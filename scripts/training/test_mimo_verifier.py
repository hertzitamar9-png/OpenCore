import subprocess
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch, MagicMock
from mimo_verifier import docker, TaskContainer, DOCKER


class ContainerTransport(unittest.TestCase):
    def test_linux_patch_bytes_are_not_newline_translated_by_windows(self):
        payload = 'diff --git a/src/file b/src/file\n+שלום\n'
        with patch('mimo_verifier.subprocess.run', return_value=subprocess.CompletedProcess([], 0, b'OK\n', b'')) as run:
            result = docker('exec', '-i', 'owned-test', 'git', 'apply', '--', input=payload)
        self.assertEqual(run.call_args.kwargs['input'], payload.encode('utf-8'))
        self.assertNotIn('text', run.call_args.kwargs)
        self.assertEqual(result.stdout, 'OK\n')

    def test_wsl_transport_bypasses_the_host_login_shell(self):
        self.assertIn('--exec', DOCKER)

    def test_task_keeps_wsl_alive_between_model_turns_and_releases_only_its_lease(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary)
            (folder/'mimo-task.json').write_text(json.dumps({'extra_info': {'instance_json':
                json.dumps({'instance_id':'format-code-task-001457'})}}))
            (folder/'mimo-verifier.patch').write_text('patch\n')
            events=[]
            lease=MagicMock();lease.poll.return_value=None
            lease.stdin.close.side_effect=lambda: events.append('lease closed')
            def launch(*args,**kwargs):
                events.append('lease started');return lease
            def command(*args,**kwargs):
                events.append(args[0]);return subprocess.CompletedProcess([],0,'','')
            with patch('mimo_verifier.subprocess.Popen',side_effect=launch), patch('mimo_verifier.docker',side_effect=command):
                with TaskContainer(folder):
                    self.assertEqual(events[0],'lease started')
                    self.assertNotIn('lease closed',events)
                self.assertLess(events.index('stop'),events.index('lease closed'))
                lease.wait.assert_called_once()

    def test_dead_container_is_an_infrastructure_failure_not_an_actor_result(self):
        container=TaskContainer.__new__(TaskContainer);container.name='owned-test'
        error='Error response from daemon: container owned-test is not running'
        with patch('mimo_verifier.docker',return_value=subprocess.CompletedProcess([],1,'',error)):
            with self.assertRaisesRegex(RuntimeError,'not running'):
                container.command('cat src/action.js')


if __name__ == '__main__':
    unittest.main()
