"""In-memory, device-scoped command and event exchange for a paired mobile UI.

The desktop host executes commands using OpenCore's own Tauri process.  This
broker never executes commands or writes conversation contents to disk.
"""
from collections import deque
import json
import re
import secrets
import threading
import time
from pathlib import Path
from hf_download import atomic_json

ID = re.compile(r'^[A-Za-z0-9_-]{1,100}$')
COMMAND = re.compile(r'^[a-z][a-z0-9_]{0,99}$')
PREFERENCE_KEY = re.compile(r'^opencore\.[A-Za-z0-9._:-]{1,180}$')
PLUGINS = frozenset({'plugin:event|listen', 'plugin:event|unlisten', 'plugin:event|emit'})
METHODS = frozenset({'GET', 'POST', 'PUT', 'DELETE'})

def presentation_preference(key):
    return key.startswith(('opencore.ui.','opencore.workspace.','opencore.browser.','opencore.floating.')) or key=='opencore.model-profile'


class MobileBridge:
    def __init__(self, preference_file=None):
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.waiting = deque()
        self.pending = {}
        self.mailboxes = {}
        self.subscriptions = {}
        self.sessions = {}
        self.last_phone_activity = {}
        self.preferences = {}
        self.preference_file=Path(preference_file) if preference_file else None
        self.device_preferences={}
        if self.preference_file:
            try:
                saved=json.loads(self.preference_file.read_text(encoding='utf-8'))
                self.device_preferences={device:{key:value for key,value in self._preferences(values).items() if presentation_preference(key)} for device,values in saved.items() if ID.fullmatch(device)}
            except (OSError,ValueError,TypeError,AttributeError):pass

    def _save_preferences(self):
        if self.preference_file:atomic_json(self.preference_file,self.device_preferences)

    def _phone_values(self,device):
        return {**self.preferences,**self.device_preferences.get(device,{})}

    @staticmethod
    def _preferences(values):
        if not isinstance(values, dict) or len(values) > 512:
            raise ValueError('Invalid OpenCore preference snapshot')
        result = {}
        for key, value in values.items():
            if not isinstance(key, str) or not PREFERENCE_KEY.fullmatch(key):
                raise ValueError('Only OpenCore app preferences can be synced')
            if not isinstance(value, str) or len(value) > 32768:
                raise ValueError('OpenCore preference value is too large')
            result[key] = value
        if len(json.dumps(result, ensure_ascii=True).encode()) > 256 * 1024:
            raise ValueError('OpenCore preference snapshot is too large')
        return result

    def publish_preferences(self, values):
        values = {key:value for key,value in self._preferences(values).items() if not presentation_preference(key)}
        with self.lock:
            if values != self.preferences:
                self.preferences = values
                for device in self.sessions:
                    self._push(device, {'type': 'preferences', 'values': self._phone_values(device)})
            return {'accepted': True}

    def phone_preferences(self, device, session):
        with self.lock:
            if not self.sessions.get(device) or session != self.sessions.get(device):
                raise ValueError('The phone session expired. Reconnect to the PC.')
            return {'values': self._phone_values(device)}

    def open_session(self, device):
        with self.lock:
            self.forget(device,drop_preferences=False)
            self.sessions[device] = secrets.token_urlsafe(18)
            self.last_phone_activity[device] = time.monotonic()
            self.changed.notify_all()
            return {'session': self.sessions[device]}

    def enqueue(self, device, value):
        if not isinstance(value, dict) or not ID.fullmatch(str(value.get('id', ''))):
            raise ValueError('Invalid phone request ID')
        if not self.sessions.get(device) or value.get('session') != self.sessions.get(device):
            raise ValueError('The phone session expired. Reconnect to the PC.')
        identity, kind = value['id'], value.get('kind')
        if kind == 'invoke':
            command, args = value.get('command'), value.get('args', {})
            if not isinstance(command, str) or not (COMMAND.fullmatch(command) or command in PLUGINS):
                raise ValueError('This desktop command is unavailable on the phone')
            if not isinstance(args, dict): raise ValueError('Invalid desktop command arguments')
        elif kind == 'hub':
            path, method = value.get('path'), value.get('method')
            if not isinstance(path, str) or not re.fullmatch(r'/[a-z0-9_/-]{1,160}', path):
                raise ValueError('Invalid local service path')
            if method not in METHODS or not (path.startswith('/api/') or path.startswith('/v1/')):
                raise ValueError('This local service operation is unavailable')
            if path.startswith('/api/mobile/') or path.startswith('/api/remote/') and path not in ('/api/remote/status','/api/remote/name','/api/remote/revoke','/api/remote/stop'):
                raise ValueError('Phone connection management is available on the PC')
        elif kind == 'preference':
            action, key, preference = value.get('action'), value.get('key'), value.get('value')
            if action not in ('set', 'remove') or not isinstance(key, str) or not PREFERENCE_KEY.fullmatch(key):
                raise ValueError('Invalid OpenCore preference update')
            if action == 'set' and (not isinstance(preference, str) or len(preference) > 32768):
                raise ValueError('OpenCore preference value is too large')
        else:
            raise ValueError('Invalid phone request')
        if len(json.dumps(value, ensure_ascii=True).encode()) > 48 * 1024 * 1024:
            raise ValueError('Phone request is too large')
        key = (device, identity)
        with self.lock:
            if value.get('session') != self.sessions.get(device): raise ValueError('The phone session expired. Reconnect to the PC.')
            self.last_phone_activity[device] = time.monotonic()
            if kind=='preference' and presentation_preference(value['key']):
                preferences=self.device_preferences.setdefault(device,{})
                if value['action']=='set':preferences[value['key']]=value['value']
                else:preferences.pop(value['key'],None)
                self._save_preferences()
                self._push(device,{'type':'result','id':identity,'ok':True,'value':None})
                return {'accepted':True}
            if key in self.pending: raise ValueError('Phone request ID is already in use')
            if sum(owner == device for owner, _ in self.pending) >= 128 or len(self.pending) >= 512:
                raise ValueError('Too many phone requests are pending')
            record = {**value, 'device': device}
            self.pending[key] = {'created': time.monotonic(), 'value': record}
            self.waiting.append(key)
            self.changed.notify_all()
            if kind == 'invoke' and value['command'] == 'plugin:event|listen':
                handler = value['args'].get('handler')
                if type(handler) is not int or handler < 0 or handler > 2147483647:
                    self.waiting.pop(); del self.pending[key]
                    raise ValueError('Invalid phone event listener')
                self.subscriptions.setdefault(device, set()).add(handler)
            elif kind == 'invoke' and value['command'] == 'plugin:event|unlisten':
                handler = value['args'].get('eventId')
                if type(handler) is not int or handler < 0 or handler > 2147483647:
                    self.waiting.pop(); del self.pending[key]
                    raise ValueError('Invalid phone event listener')
                self.subscriptions.setdefault(device, set()).discard(handler)
            return {'accepted': True}

    def take(self, devices, wait=0):
        with self.lock:
            allowed = devices() if callable(devices) else devices
            deadline = time.monotonic() + min(max(wait, 0), 20)
            while wait and not any(key in self.pending and key[0] in allowed for key in self.waiting):
                remaining = deadline - time.monotonic()
                if remaining <= 0: break
                self.changed.wait(remaining)
                allowed = devices() if callable(devices) else devices
            now = time.monotonic()
            for key, item in list(self.pending.items()):
                if now - item['created'] > 3600:
                    del self.pending[key]
                    self._push(key[0], {'type': 'result', 'id': key[1], 'ok': False, 'error': 'The PC request timed out.'})
            tasks = []
            while self.waiting and len(tasks) < 32:
                key = self.waiting.popleft()
                if key in self.pending and key[0] in allowed:
                    tasks.append(self.pending[key]['value'])
            active = bool(tasks) or any(device in allowed and now - seen < 30 for device,seen in self.last_phone_activity.items())
            return {'active': active, 'longPoll': bool(wait), 'sessions': {device:self.sessions[device] for device in allowed if device in self.sessions}, 'tasks': tasks}

    def _push(self, device, item):
        mailbox = self.mailboxes.setdefault(device, deque())
        if len(mailbox) >= 512: mailbox.popleft()
        mailbox.append(item)

    def result(self, value, devices):
        if not isinstance(value, dict): raise ValueError('Invalid PC response')
        device, identity, phase = value.get('device'), value.get('id'), value.get('phase', 'complete')
        with self.lock:
            key = (device, identity)
            if device not in devices or key not in self.pending or value.get('session') != self.sessions.get(device): raise ValueError('Phone request is no longer pending')
            if phase not in ('complete', 'start', 'chunk', 'end'): raise ValueError('Invalid PC response phase')
            packet = {key: val for key, val in value.items() if key not in ('device', 'phase', 'session')}
            packet['type'] = 'result' if phase == 'complete' else 'stream-' + phase
            if len(json.dumps(packet, ensure_ascii=True).encode()) > 48 * 1024 * 1024:
                raise ValueError('PC response is too large')
            self._push(device, packet)
            if phase in ('complete', 'end'): del self.pending[key]
            return {'accepted': True}

    def event(self, value, devices):
        if not isinstance(value, dict): raise ValueError('Invalid PC event')
        device, handler = value.get('device'), value.get('handler')
        with self.lock:
            if device not in devices or value.get('session') != self.sessions.get(device) or handler not in self.subscriptions.get(device, set()):
                raise ValueError('Phone event listener is unavailable')
            self._push(device, {'type': 'event', 'handler': handler, 'payload': value.get('payload')})
            return {'accepted': True}

    def updates(self, device, session):
        with self.lock:
            if not self.sessions.get(device) or session != self.sessions.get(device): raise ValueError('The phone session expired. Reconnect to the PC.')
            self.last_phone_activity[device] = time.monotonic()
            mailbox = self.mailboxes.setdefault(device, deque())
            items = []
            while mailbox and len(items) < 64: items.append(mailbox.popleft())
            return {'items': items}

    def forget(self, device, drop_preferences=True):
        with self.lock:
            if drop_preferences and device in self.device_preferences:
                self.device_preferences.pop(device,None);self._save_preferences()
            self.mailboxes.pop(device, None)
            self.subscriptions.pop(device, None)
            self.sessions.pop(device, None)
            self.last_phone_activity.pop(device, None)
            self.waiting = deque(key for key in self.waiting if key[0] != device)
            for key in list(self.pending):
                if key[0] == device: del self.pending[key]

    def close(self):
        with self.lock:
            self.waiting.clear(); self.pending.clear(); self.mailboxes.clear(); self.subscriptions.clear(); self.sessions.clear(); self.last_phone_activity.clear(); self.preferences.clear()
