"""Small read-only live samples; no model scans, process launches or persisted chat data."""
import ctypes
import json
import os
from pathlib import Path
import threading
import time
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener


class GpuSample:
    def __init__(self):
        self.library = None
        if os.name != 'nt': return
        # An absolute system path avoids DLL discovery through a model or cwd.
        for path in (Path(os.environ.get('SystemRoot', r'C:\Windows'))/'System32/nvml.dll',
                     Path(os.environ.get('ProgramFiles', r'C:\Program Files'))/'NVIDIA Corporation/NVSMI/nvml.dll'):
            try:
                dll = ctypes.CDLL(str(path))
                if dll.nvmlInit_v2() == 0: self.library = dll; break
            except (OSError, AttributeError): pass

    def __call__(self):
        dll = self.library
        if dll is None: return {}
        class Memory(ctypes.Structure):
            _fields_ = [('total', ctypes.c_ulonglong), ('free', ctypes.c_ulonglong), ('used', ctypes.c_ulonglong)]
        class Usage(ctypes.Structure):
            _fields_ = [('gpu', ctypes.c_uint), ('memory', ctypes.c_uint)]
        device, memory, usage = ctypes.c_void_p(), Memory(), Usage()
        try:
            if dll.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(device)) != 0: return {}
            sample = {}
            if dll.nvmlDeviceGetMemoryInfo(device, ctypes.byref(memory)) == 0:
                sample.update(vramUsedMib=memory.used/1048576, vramTotalMib=memory.total/1048576)
            if dll.nvmlDeviceGetUtilizationRates(device, ctypes.byref(usage)) == 0:
                sample['gpuUtilization'] = usage.gpu
            return sample
        except (OSError, AttributeError): return {}


class LiveTelemetry:
    def __init__(self, sampler=None, clock=time.monotonic):
        self.sample = sampler if sampler is not None else GpuSample()
        self.clock = clock
        self.lock = threading.Lock()
        self.hardware_at, self.hardware = -100.0, {}
        self.previous = {}

    def read(self, runtime, conversation, read_context=None):
        now = self.clock()
        with self.lock:
            if now-self.hardware_at >= .75:
                self.hardware = self.sample()
                self.hardware_at = now
            result = {'telemetry': dict(self.hardware), 'sampledAt': now}
        if runtime.get('status') != 'running' or not conversation:
            result['telemetry'].update(tokensPerSecond=0, completionTokens=0)
            return result
        if not isinstance(conversation, str) or len(conversation) > 500:
            raise ValueError('A valid conversation is required')
        try:
            if read_context is not None:
                context = read_context()
            else:
                port = runtime['echoPort']
                if type(port) is not int or not 1 <= port <= 65535: raise ValueError('Invalid ECHO port')
                request = Request('http://127.0.0.1:%d/echo/context?conversation=%s' % (port, quote(conversation, safe='')))
                # Never send model authorization or chat text to any remote host.
                with build_opener(ProxyHandler({})).open(request, timeout=.8) as response:
                    raw = response.read(256*1024+1)
                if len(raw) > 256*1024: raise ValueError('Context sample too large')
                context = json.loads(raw)
            if not isinstance(context, dict): raise ValueError('Invalid context sample')
            result['context'] = context
            count = context.get('modelOutputTokens')
            key = (runtime.get('profile'), runtime.get('echoPort'), conversation, context.get('modelTaskId'))
            if type(count) is int and count >= 0:
                with self.lock:
                    previous = self.previous.get(key)
                    speed = max(0, count-previous[1])/(now-previous[0]) if previous and count>=previous[1] and now>previous[0] else 0
                    self.previous = {key: (now, count)}
                result['telemetry'].update(completionTokens=count, tokensPerSecond=speed if context.get('modelSessionActive') else 0)
        except (OSError, ValueError, KeyError, RuntimeError):
            result['contextUnavailable'] = True
        return result
