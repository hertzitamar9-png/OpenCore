"""Persistent, UI-independent model installation admission.

Only the resolver's allowlisted model commands are durable. A worker owns its
pause/cancel events until it has actually exited; changing views cannot start or
stop it. Interrupted work is requeued on service restart, in its original order.
"""
import copy
import json
from pathlib import Path
import secrets
import threading
import time

from hf_download import Paused, atomic_json


ACTIVE = {'starting', 'downloading', 'preparing'}
TERMINAL = {'ready', 'failed', 'cancelled'}


class DownloadQueue:
    def __init__(self, path, resolve, execute, *, discard=None, available=lambda: True):
        self.path, self.resolve, self.execute = Path(path), resolve, execute
        self.discard = discard
        self.available = available
        self.condition = threading.Condition(threading.RLock())
        self.items, self.workers = [], {}
        self.mode, self.paused, self.closed = 'sequential', False, False
        self._load()
        self.scheduler = threading.Thread(target=self._schedule, name='OpenCore-download-queue', daemon=True)
        self.scheduler.start()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return
        if not isinstance(data, dict) or data.get('schema') != 1 or data.get('mode') not in ('sequential', 'parallel') or not isinstance(data.get('items'), list):
            raise ValueError('The saved download queue is invalid')
        self.mode, self.paused = data['mode'], data.get('paused') is True
        ids = set()
        for saved in data['items']:
            if not isinstance(saved, dict) or not isinstance(saved.get('id'), str) or saved['id'] in ids:
                raise ValueError('The saved download queue has invalid item identities')
            ids.add(saved['id'])
            try:
                resolved = self.resolve(saved)
                item = {**saved, **{k: resolved[k] for k in ('key',) if k in resolved}}
            except (ValueError, KeyError, TypeError):
                item = {**saved, 'phase': 'failed', 'error': 'This saved model is no longer in the verified catalog'}
            if item.get('phase') in ACTIVE:
                item.update(phase='queued', detail='Resuming interrupted installation', error=None)
            item['bytesPerSecond'], item['etaSeconds'] = 0, None
            self.items.append(item)

    def _persist(self):
        atomic_json(self.path, {'schema': 1, 'mode': self.mode, 'paused': self.paused, 'items': self.items})

    def snapshot(self):
        with self.condition:
            result = []
            for position, item in enumerate(self.items, 1):
                result.append({key: copy.deepcopy(item.get(key)) for key in (
                    'id', 'modelId', 'label', 'phase', 'downloadedBytes', 'totalBytes',
                    'bytesPerSecond', 'etaSeconds', 'error', 'detail', 'stage')})
                result[-1]['position'] = position
            return {'schema': 1, 'mode': self.mode, 'paused': self.paused,
                    'activeIds': list(self.workers), 'items': result}

    def action(self, body):
        if not isinstance(body, dict):
            raise ValueError('A download queue action is required')
        action = body.get('action')
        if action == 'snapshot':
            return self.snapshot()
        resolved = None
        if action == 'enqueue':
            values = body.get('items')
            if not isinstance(values, list) or not values:
                raise ValueError('Choose at least one model to download')
            # Catalog resolution can acquire the bridge lock. Do it before the
            # queue condition so catalog/status and queue requests cannot invert
            # their locks. Admission and persistence remain atomic below.
            resolved = [self.resolve(value) for value in values]
        with self.condition:
            if self.closed:
                raise ValueError('The download service is shutting down')
            if action == 'enqueue':
                # Resolve the whole batch before admitting any item. The caller
                # cannot persist an arbitrary command, path, URL or recipe.
                for value in resolved:
                    existing = next((x for x in self.items if x.get('key') == value['key'] and x['phase'] not in TERMINAL), None)
                    if existing:
                        continue
                    self.items.append({**value, 'id': secrets.token_hex(16), 'phase': 'queued',
                                       'downloadedBytes': 0, 'totalBytes': value.get('totalBytes', 0),
                                       'bytesPerSecond': 0, 'etaSeconds': None, 'error': None,
                                       'stage': 'queued', 'detail': 'Waiting in download queue'})
            elif action == 'mode':
                if body.get('mode') not in ('sequential', 'parallel'):
                    raise ValueError('Choose sequential or parallel downloads')
                self.mode = body['mode']
            elif action in ('pause', 'resume', 'remove'):
                identity = body.get('id')
                if action == 'remove' and not identity:
                    raise ValueError('Choose the queue item to remove')
                selected = [x for x in self.items if not identity or x['id'] == identity]
                if identity and not selected:
                    raise ValueError('This download queue item no longer exists')
                if identity is None:
                    self.paused = action == 'pause'
                for item in selected:
                    worker = self.workers.get(item['id'])
                    if action in ('pause', 'remove') and worker:
                        worker['pause'].set()
                        if action == 'remove': worker['cancel'].set()
                        item['detail'] = 'Stopping this download' if action == 'remove' else 'Pausing this download'
                    elif action == 'pause' and item['phase'] == 'queued' and identity:
                        item.update(phase='paused', stage='paused', bytesPerSecond=0, etaSeconds=None)
                    elif action == 'remove':
                        # Cleanup is deferred outside the condition so another
                        # worker's progress/cancellation cannot be blocked by IO.
                        item.update(phase='cancelled', stage='cancelled', error=None, bytesPerSecond=0, etaSeconds=None)
                    elif action == 'resume' and worker and identity is None:
                        worker['resume'] = True
                    elif action == 'resume' and item['phase'] in (('paused', 'failed', 'cancelled') if identity else ('paused',)):
                        if worker:
                            raise ValueError('This download is still stopping; resume when it has stopped')
                        item.update(phase='queued', stage='queued', error=None, bytesPerSecond=0, etaSeconds=None)
            else:
                raise ValueError('Unsupported download queue action')
            self._persist()
            self.condition.notify_all()
            return self.snapshot()

    def _schedule(self):
        with self.condition:
            while not self.closed:
                candidates = [x for x in self.items if x['phase'] == 'queued' and x['id'] not in self.workers]
                slots = len(candidates) if self.mode == 'parallel' else max(0, 1 - len(self.workers))
                if not self.paused and candidates and slots and self.available():
                    for item in candidates[:slots]:
                        events = {'pause': threading.Event(), 'cancel': threading.Event()}
                        thread = threading.Thread(target=self._work, args=(item, events), name='OpenCore-model-' + item['id'][:8], daemon=True)
                        events['thread'] = thread
                        self.workers[item['id']] = events
                        item.update(phase='starting', stage='starting', detail='Starting verified installation')
                        try:
                            thread.start()
                        except RuntimeError:
                            self.workers.pop(item['id'], None)
                            item.update(phase='paused', stage='resource-limit', detail='The operating system could not start another worker. Resume this item after another download finishes.', error=None)
                        self._persist()
                # This bounded backend wait also observes legacy/native runtime
                # resource release. WebView timers never drive scheduling.
                self.condition.wait(timeout=.5)

    def _work(self, item, events):
        last = [time.monotonic(), item.get('downloadedBytes', 0), 0.0]
        def report(value):
            with self.condition:
                stage = str(value.get('stage', item.get('stage', 'preparing')))
                previous_stage = item.get('stage')
                item['stage'] = stage
                item['phase'] = 'downloading' if stage == 'downloading' else 'preparing'
                item['detail'] = str(value.get('detail') or value.get('filename') or stage.replace('-', ' '))[:2048]
                for source, target in (('bytesDone', 'downloadedBytes'), ('downloadedBytes', 'downloadedBytes'), ('bytesTotal', 'totalBytes'), ('totalBytes', 'totalBytes')):
                    if source in value: item[target] = max(0, int(value[source] or 0))
                now, done = time.monotonic(), item['downloadedBytes']
                elapsed = now - last[0]
                if stage == 'downloading' and previous_stage == stage and elapsed >= .25:
                    item['bytesPerSecond'] = max(0, (done - last[1]) / elapsed)
                    last[0], last[1] = now, done
                elif stage != 'downloading' or previous_stage != stage:
                    item['bytesPerSecond'] = 0
                    last[0], last[1] = now, done
                speed = item['bytesPerSecond']
                item['etaSeconds'] = max(0, (item['totalBytes'] - done) / speed) if speed and item['totalBytes'] else None
                if now - last[2] >= 1:
                    self._persist()
                    last[2] = now
        phase, error = 'ready', None
        try:
            with self.condition:
                execution = {**copy.deepcopy(item), '_parallel': self.mode == 'parallel'}
            self.execute(execution, events['pause'], events['cancel'], report)
            if events['pause'].is_set(): phase = 'paused'
        except Paused:
            phase = 'paused'
        except Exception as failure:
            phase = 'paused' if events['pause'].is_set() else 'failed'
            error = str(failure)[:1000] if isinstance(failure, (ValueError, RuntimeError)) else 'Model installation failed; verified files and resumable partials were retained'
        finally:
            if events['cancel'].is_set():
                phase, error = 'cancelled', None
                if self.discard:
                    try: self.discard(item)
                    except (OSError, ValueError, RuntimeError):
                        item['detail'] = 'Cancelled. Unproven or shared partial files were kept.'
            with self.condition:
                # Closing means an application restart, rather than an explicit
                # user pause. Retain this position for automatic continuation.
                if (self.closed or events.get('resume')) and phase == 'paused' and not events['cancel'].is_set(): phase = 'queued'
                item.update(phase=phase, error=error, bytesPerSecond=0, etaSeconds=None)
                self.workers.pop(item['id'], None)
                self._persist()
                self.condition.notify_all()

    def close(self):
        with self.condition:
            if self.closed: return
            self.closed = True
            workers = list(self.workers.values())
            for worker in workers: worker['pause'].set()
            self._persist()
            self.condition.notify_all()
        if self.scheduler is not threading.current_thread(): self.scheduler.join(timeout=2)
        deadline = time.monotonic() + 3
        for worker in workers:
            if worker['thread'] is not threading.current_thread(): worker['thread'].join(timeout=max(0, deadline - time.monotonic()))
