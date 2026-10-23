"""User-bound Windows DPAPI token storage. No global Hugging Face state is read."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import secrets

from hf_download import managed_path


def _dpapi(data, decrypt=False):
    if os.name != "nt":
        raise RuntimeError("Secure Hugging Face token storage requires Windows DPAPI")
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    def blob(value):
        buffer = ctypes.create_string_buffer(value)
        return Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer
    source, source_buffer = blob(data)
    entropy, entropy_buffer = blob(b"OpenCore.Managed.HuggingFace.Token.v1")
    output = Blob()
    crypt, kernel = ctypes.WinDLL("crypt32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [ctypes.c_void_p], ctypes.c_void_p
    if decrypt:
        function = crypt.CryptUnprotectData
        function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        success = function(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(output))
    else:
        function = crypt.CryptProtectData
        function.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        success = function(ctypes.byref(source), "OpenCore Hugging Face access", ctypes.byref(entropy), None, None, 1, ctypes.byref(output))
    if not success:
        raise RuntimeError("Windows could not protect or unlock the managed Hugging Face token")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


class TokenStore:
    def __init__(self, root):
        self.path = managed_path(Path(root).resolve(), "secrets", "hugging-face-token.dpapi")

    def get(self):
        if not self.path.exists():
            return None
        if self.path.stat().st_size > 16384:
            return None
        try:
            return _dpapi(self.path.read_bytes(), True).decode("utf-8")
        except (OSError,RuntimeError,UnicodeError):
            return None

    def status(self):
        return {"configured": bool(self.get())}

    def save(self, token, client):
        client.validate_token(token)
        encrypted = _dpapi(token.encode("utf-8"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + "." + secrets.token_hex(6) + ".tmp")
        try:
            with temporary.open("xb") as output:
                output.write(encrypted)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        return {"configured": True}

    def clear(self):
        self.path.unlink(missing_ok=True)
        return {"configured": False}
