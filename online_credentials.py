"""Windows-user DPAPI secrets. Only encrypted blobs enter app-owned storage."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import threading
import uuid

from data_paths import data_path


class CredentialError(RuntimeError):
    def __init__(self):
        super().__init__("credential_unavailable")


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(value: bytes, decrypt=False) -> bytes:
    if os.name != "nt":
        raise CredentialError()
    buffer = ctypes.create_string_buffer(value)
    source = _Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = _Blob()
    api = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        fn = api.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        ok = fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result))
    else:
        fn = api.CryptProtectData
        fn.argtypes = [ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        ok = fn(ctypes.byref(source), "Games_translator online", None, None, None, 1, ctypes.byref(result))
    if not ok:
        raise CredentialError()
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


class CredentialStore:
    def __init__(self, root: Path | None = None):
        self.root = root if root is not None else data_path("settings", "online-secrets")
        self._lock = threading.RLock()

    def _path(self, connection: str) -> Path:
        return self.root / (hashlib.sha256(connection.encode()).hexdigest() + ".dpapi")

    def revision(self, connection: str) -> int:
        try:
            return self._path(connection).stat().st_mtime_ns
        except OSError:
            return 0

    def status(self, connection: str) -> str:
        """Presence/decryption only, never proof of remote authorization."""
        with self._lock:
            try:
                if not self._path(connection).exists():
                    return "missing"
                return "saved" if self.get(connection) else "unavailable"
            except (OSError, CredentialError):
                return "unavailable"

    def get(self, connection: str) -> str:
        with self._lock:
            try:
                return _crypt(self._path(connection).read_bytes(), decrypt=True).decode("utf-8")
            except (OSError, UnicodeError, CredentialError):
                raise CredentialError() from None

    def put(self, connection: str, secret: str) -> None:
        if not secret.strip() or len(secret) > 4096 or any(c in secret for c in "\r\n\0"):
            raise CredentialError()
        encrypted = _crypt(secret.strip().encode("utf-8"))
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self._path(connection)
            temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                temporary.write_bytes(encrypted)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)

    def delete(self, connection: str) -> None:
        with self._lock:
            self._path(connection).unlink(missing_ok=True)


CREDENTIALS = CredentialStore()
