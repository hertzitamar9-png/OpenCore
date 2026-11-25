"""CPU-only admission tests: no inference starts until the desktop releases the chat model."""
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'src-tauri/resources/runtime-setup/hf-extension'))
from pipeline_admission import PipelineAdmission


class PipelineAdmissionTests(unittest.TestCase):
    def test_native_job_needs_no_chat_handoff(self):
        gate=PipelineAdmission();update=Mock()
        gate.wait({'id':'native','request':{}},threading.Event(),update)
        update.assert_not_called();self.assertEqual(gate.list(),[])

    def test_echo_job_waits_for_explicit_desktop_release_and_cleans_up(self):
        gate=PipelineAdmission();cancel=threading.Event();changed=threading.Event();finished=threading.Event()
        job={'id':'echo','category':'3d','request':{'operatingMode':'echo'}}
        def update(job,**values):job.update(values);changed.set()
        thread=threading.Thread(target=lambda:(gate.wait(job,cancel,update),finished.set()));thread.start()
        self.assertTrue(changed.wait(1));self.assertFalse(finished.is_set())
        self.assertTrue(job['requiresTurnHandoff']);self.assertEqual(gate.list(),[{'jobId':'echo','category':'3d'}])
        gate.admit('echo');thread.join(2)
        self.assertTrue(finished.is_set());self.assertFalse(job['requiresTurnHandoff']);self.assertEqual(gate.list(),[])
        with self.assertRaises(ValueError):gate.admit('echo')

    def test_cancel_and_missing_desktop_timeout_do_not_leave_pending_admissions(self):
        for cancelled,timeout in ((True,10),(False,.001)):
            gate=PipelineAdmission();cancel=threading.Event()
            if cancelled:cancel.set()
            with self.assertRaises(RuntimeError):gate.wait({'id':'test','operatingMode':'echo'},cancel,Mock(),timeout=timeout)
            self.assertEqual(gate.list(),[])


if __name__=='__main__':unittest.main()
