"""Capture and operate only the owning OpenCore window for a paired phone."""
import ctypes
from ctypes import wintypes as w
import math
import os
import struct
import threading
import time
import zlib


def png(width, height, bgra):
    rgb = bytearray(width * height * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
    scan = b''.join(b'\0' + rgb[row * width * 3:(row + 1) * width * 3] for row in range(height))
    def chunk(kind, value):
        return struct.pack('!I', len(value)) + kind + value + struct.pack('!I', zlib.crc32(kind + value) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(scan, 2)) + chunk(b'IEND', b'')


class WindowSurface:
    def __init__(self, owner_pid):
        if os.name != 'nt' or type(owner_pid) is not int or owner_pid < 1:
            raise ValueError('A running OpenCore desktop process is required')
        self.pid, self.lock = owner_pid, threading.RLock()
        self.held, self.mouse_held, self.last_live = set(), False, time.monotonic()
        self.user, self.gdi = ctypes.WinDLL('user32', use_last_error=True), ctypes.WinDLL('gdi32', use_last_error=True)
        try:
            self.user.SetProcessDpiAwarenessContext.argtypes = [w.HANDLE]
            self.user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except AttributeError: pass
        declarations = {
            'GetWindowThreadProcessId': ([w.HWND, ctypes.POINTER(w.DWORD)], w.DWORD),
            'GetWindowTextW': ([w.HWND, w.LPWSTR, ctypes.c_int], ctypes.c_int),
            'IsWindowVisible': ([w.HWND], w.BOOL), 'IsIconic': ([w.HWND], w.BOOL),
            'GetClientRect': ([w.HWND, ctypes.POINTER(w.RECT)], w.BOOL),
            'GetWindowDC': ([w.HWND], w.HDC), 'ReleaseDC': ([w.HWND, w.HDC], ctypes.c_int),
            'PrintWindow': ([w.HWND, w.HDC, w.UINT], w.BOOL),
            'SetForegroundWindow': ([w.HWND], w.BOOL), 'GetForegroundWindow': ([], w.HWND),
            'ShowWindow': ([w.HWND, ctypes.c_int], w.BOOL),
            'ClientToScreen': ([w.HWND, ctypes.POINTER(w.POINT)], w.BOOL),
            'SetCursorPos': ([ctypes.c_int, ctypes.c_int], w.BOOL),
            'mouse_event': ([w.DWORD, w.DWORD, w.DWORD, w.DWORD, ctypes.c_size_t], None),
            'keybd_event': ([w.BYTE, w.BYTE, w.DWORD, ctypes.c_size_t], None),
        }
        for name, (args, result) in declarations.items():
            function = getattr(self.user, name); function.argtypes, function.restype = args, result
        for name, args, result in (
            ('CreateCompatibleDC', [w.HDC], w.HDC), ('CreateCompatibleBitmap', [w.HDC, ctypes.c_int, ctypes.c_int], w.HBITMAP),
            ('SelectObject', [w.HDC, w.HANDLE], w.HANDLE), ('DeleteObject', [w.HANDLE], w.BOOL),
            ('DeleteDC', [w.HDC], w.BOOL), ('GetDIBits', [w.HDC, w.HBITMAP, w.UINT, w.UINT, ctypes.c_void_p, ctypes.c_void_p, w.UINT], ctypes.c_int)):
            function = getattr(self.gdi, name); function.argtypes, function.restype = args, result
        def release_idle():
            while True:
                time.sleep(1)
                if time.monotonic() - self.last_live > 3: self.release()
        threading.Thread(target=release_idle, daemon=True).start()

    def release(self):
        with self.lock:
            for key in self.held: self.user.keybd_event(key, 0, 2, 0)
            self.held.clear()
            if self.mouse_held: self.user.mouse_event(4, 0, 0, 0, 0)
            self.mouse_held = False

    def window(self):
        found = []
        callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        def visit(hwnd, _):
            pid, title = w.DWORD(), ctypes.create_unicode_buffer(512)
            self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            self.user.GetWindowTextW(hwnd, title, len(title))
            if pid.value == self.pid and title.value == 'OpenCore' and self.user.IsWindowVisible(hwnd):
                rect = w.RECT(); self.user.GetClientRect(hwnd, ctypes.byref(rect))
                found.append(((rect.right - rect.left) * (rect.bottom - rect.top), hwnd))
            return True
        self.user.EnumWindows.argtypes = [callback_type, w.LPARAM]
        self.user.EnumWindows(callback_type(visit), 0)
        if not found: raise ValueError('OpenCore must have an open desktop window')
        return max(found)[1]

    def frame(self):
        with self.lock:
            self.last_live = time.monotonic()
            hwnd = self.window()
            if self.user.IsIconic(hwnd): raise ValueError('OpenCore is minimized. Tap Wake PC app to restore it.')
            rect = w.RECT(); self.user.GetClientRect(hwnd, ctypes.byref(rect))
            width, height = rect.right, rect.bottom
            if not (1 <= width <= 4096 and 1 <= height <= 3072): raise ValueError('OpenCore window size is unsupported')
            source = self.user.GetWindowDC(hwnd)
            target = self.gdi.CreateCompatibleDC(source)
            bitmap = self.gdi.CreateCompatibleBitmap(source, width, height)
            previous = self.gdi.SelectObject(target, bitmap)
            try:
                if not self.user.PrintWindow(hwnd, target, 3): raise ValueError('Windows could not capture the OpenCore window')
                self.gdi.SelectObject(target, previous)
                # Negative height requests top-down BGRA. PW_CLIENTONLY excludes the title bar.
                header = struct.pack('<IiiHHIIiiII', 40, width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0) + bytes(16)
                info, pixels = ctypes.create_string_buffer(header), ctypes.create_string_buffer(width * height * 4)
                if self.gdi.GetDIBits(source, bitmap, 0, height, pixels, info, 0) != height:
                    raise ValueError('Windows returned an incomplete frame')
                return png(width, height, pixels.raw), width, height
            finally:
                self.gdi.SelectObject(target, previous)
                self.gdi.DeleteObject(bitmap); self.gdi.DeleteDC(target); self.user.ReleaseDC(hwnd, source)

    def input(self, body):
        kind = body.get('kind')
        if kind not in ('tap', 'down', 'up', 'move', 'scroll', 'text', 'key', 'wake', 'release'):
            raise ValueError('Unsupported remote input')
        if kind in ('tap', 'down', 'up', 'move', 'scroll'):
            if any(type(body.get(k)) not in (int, float) or not math.isfinite(body[k]) or not 0 <= body[k] <= 1 for k in ('x', 'y')):
                raise ValueError('Coordinates must be inside the OpenCore window')
        if kind == 'text' and (not isinstance(body.get('text'), str) or len(body['text']) > 16000 or '\0' in body['text']):
            raise ValueError('Remote text must be at most 16000 characters')
        keys = {'Enter': 13, 'Backspace': 8, 'Escape': 27, 'Tab': 9, 'ArrowLeft': 37, 'ArrowUp': 38,
                'ArrowRight': 39, 'ArrowDown': 40, 'Space': 32, **{chr(v): v for v in range(65, 91)}}
        if kind == 'key' and body.get('key') not in keys: raise ValueError('Unsupported remote key')
        if kind == 'key' and body.get('action', 'tap') not in ('tap', 'down', 'up'): raise ValueError('Invalid key action')
        if kind == 'scroll' and (type(body.get('delta')) is not int or abs(body['delta']) > 1200): raise ValueError('Invalid scroll amount')
        with self.lock:
            hwnd = self.window()
            self.last_live = time.monotonic()
            if kind == 'release': self.release(); return
            if kind == 'wake': self.user.ShowWindow(hwnd, 9); return
            if self.user.IsIconic(hwnd): raise ValueError('Restore OpenCore before using remote controls')
            self.user.SetForegroundWindow(hwnd)
            if self.user.GetForegroundWindow() != hwnd: raise ValueError('Windows did not allow OpenCore to receive input')
            if kind in ('tap', 'down', 'up', 'move', 'scroll'):
                rect = w.RECT(); self.user.GetClientRect(hwnd, ctypes.byref(rect))
                point = w.POINT(round(body['x'] * max(0, rect.right - 1)), round(body['y'] * max(0, rect.bottom - 1)))
                self.user.ClientToScreen(hwnd, ctypes.byref(point)); self.user.SetCursorPos(point.x, point.y)
                if self.user.GetForegroundWindow() != hwnd: return
                for flag in ([2, 4] if kind == 'tap' else [2] if kind == 'down' else [4] if kind == 'up' else [0x800] if kind == 'scroll' else []):
                    self.user.mouse_event(flag, 0, 0, body.get('delta', 0) & 0xffffffff, 0)
                    if flag == 2: self.mouse_held = True
                    elif flag == 4: self.mouse_held = False
            else:
                class Mouse(ctypes.Structure):
                    _fields_ = [('dx', w.LONG), ('dy', w.LONG), ('data', w.DWORD), ('flags', w.DWORD), ('time', w.DWORD), ('extra', ctypes.c_size_t)]
                class Keyboard(ctypes.Structure):
                    _fields_ = [('key', w.WORD), ('scan', w.WORD), ('flags', w.DWORD), ('time', w.DWORD), ('extra', ctypes.c_size_t)]
                class Union(ctypes.Union): _fields_ = [('keyboard', Keyboard), ('mouse', Mouse)]
                class Input(ctypes.Structure): _fields_ = [('type', w.DWORD), ('value', Union)]
                self.user.SendInput.argtypes = [w.UINT, ctypes.POINTER(Input), ctypes.c_int]
                values = [int.from_bytes(body['text'].encode('utf-16-le')[i:i+2], 'little') for i in range(0, len(body['text'].encode('utf-16-le')), 2)] if kind == 'text' else [keys[body['key']]]
                for value in values:
                    if self.user.GetForegroundWindow() != hwnd: break
                    for release in ((False,) if kind == 'key' and body.get('action') == 'down' else (True,) if kind == 'key' and body.get('action') == 'up' else (False, True)):
                        key = Keyboard(0 if kind == 'text' else value, value if kind == 'text' else 0,
                                       (4 if kind == 'text' else 0) | (2 if release else 0), 0, 0)
                        item = Input(1, Union(keyboard=key))
                        if self.user.SendInput(1, ctypes.byref(item), ctypes.sizeof(Input)) != 1:
                            raise ValueError('Windows refused remote keyboard input')
                        if kind == 'key':
                            if release: self.held.discard(value)
                            else: self.held.add(value)
