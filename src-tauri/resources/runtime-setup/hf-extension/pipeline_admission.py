"""Hand agent jobs back to the desktop before allocating GPU model weights."""
import threading
import time


class PipelineAdmission:
    def __init__(self):
        self.lock, self.pending = threading.RLock(), {}

    def list(self):
        with self.lock:
            return [{'jobId': identity, 'category': value['category']} for identity,value in self.pending.items() if not value['event'].is_set()]

    def admit(self, identity):
        with self.lock:
            value = self.pending.get(identity)
            if value is None: raise ValueError('This pipeline job is no longer waiting for model release')
            value['event'].set()
        return {'jobId': identity, 'admitted': True}

    def wait(self, job, cancelled, update, timeout=300):
        if job.get('operatingMode', job.get('request', {}).get('operatingMode', 'native')) != 'echo': return
        event = threading.Event()
        with self.lock: self.pending[job['id']] = {'event': event, 'category': job.get('category', 'pipeline')}
        update(job, stage='Waiting for the current chat to finish and release model memory', requiresTurnHandoff=True)
        deadline = time.monotonic() + timeout
        try:
            while not event.wait(.2):
                if cancelled.is_set(): raise RuntimeError('Pipeline job cancelled before model loading')
                if time.monotonic() >= deadline:
                    raise RuntimeError('The desktop could not release the text model for this job. Finish the active chat, keep OpenCore open and retry.')
            if cancelled.is_set(): raise RuntimeError('Pipeline job cancelled before model loading')
            update(job, stage='Text model released; preparing generation', requiresTurnHandoff=False)
        finally:
            with self.lock: self.pending.pop(job['id'], None)
