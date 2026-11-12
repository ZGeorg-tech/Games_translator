"""Ownership/cleanup for DataPaths process temp namespaces, not a path registry.

An OS-held lease is the activity authority, not PID age. Native children retain
their parent's namespace by PID + OS creation identity; uncertain spawns retain
it conservatively. No legacy/unmarked namespace is adopted for deletion.
"""
from __future__ import annotations

import atexit
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time
import uuid

MARKER = ".m30-owner.json"
LEASE = ".m30-lease"
NAMESPACE = re.compile(r"[0-9]+-[a-f0-9]{12}\Z")


def process_identity(pid):
    """None = definitely absent, 'unknown' = cannot prove identity/death."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    handle = kernel.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return None if ctypes.get_last_error() == 87 else "unknown"
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            return "unknown"
        if code.value != 259:
            return None
        values = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(v) for v in values)):
            return "unknown"
        return (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime
    finally:
        kernel.CloseHandle(handle)


def linked(path):
    return bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400) or path.is_symlink()


def native_children(parents):
    """Also guard third-party spawns (e.g. pytesseract) not made by our wrapper."""
    class Entry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                    ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
                    ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                    ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
                    ("flags", wintypes.DWORD), ("exe", wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = kernel.Process32FirstW.argtypes
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        return True
    try:
        entry = Entry()
        entry.size = ctypes.sizeof(entry)
        if not kernel.Process32FirstW(snapshot, ctypes.byref(entry)):
            return True
        while True:
            if entry.parent in parents:
                identity = process_identity(entry.pid)
                birth = parents[entry.parent]
                if identity == "unknown" or (identity is not None and
                        (not isinstance(birth, int) or identity >= birth)):
                    return True
            if not kernel.Process32NextW(snapshot, ctypes.byref(entry)):
                if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                    return True
                break
        return False
    finally:
        kernel.CloseHandle(snapshot)


def safe_root(root):
    # Reject reparse points even if they resolve back inside DATA_ROOT.
    for part in (root, *root.parents):
        if part.exists() and linked(part):
            raise ValueError(f"Reparse temp ancestor: {part}")


def lock_file(path, create=False):
    import msvcrt
    stream = path.open("a+b" if create else "r+b")
    try:
        if create and path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        return stream
    except BaseException:
        stream.close()
        raise


def unlock(stream):
    import msvcrt
    try:
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        stream.close()


def inspect_payload(root):
    """Preflight the entire tree before removal; never follow links/owners."""
    count, directories, size = 0, 1, 0
    def scan_error(error):
        raise error
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=scan_error):
        for name in dirs + files:
            path = Path(directory) / name
            if linked(path):
                raise ValueError(f"reparse: {path}")
            if name == MARKER and path.parent != root:
                raise ValueError(f"nested_owner: {path}")
            if name.lower() in {"profiles", "settings", "history", "backups", "sessions"} or name.lower().endswith((".sqlite", ".db", ".sqlite-wal", ".sqlite-shm")):
                raise ValueError(f"possible_user_data: {path}")
        directories += len(dirs)
        for name in files:
            count += 1
            size += (Path(directory) / name).stat().st_size
    return count, directories, size


def cleanup_namespace(temp_root, root):
    result = {"path": str(root), "status": "retained"}
    lease = None
    try:
        safe_root(temp_root)
        if root.parent != temp_root or not NAMESPACE.fullmatch(root.name) or linked(root):
            raise ValueError("not_direct_owned_namespace")
        if not (root / MARKER).is_file():
            result["reason"] = "legacy_unowned"
            return result
        if linked(root / MARKER) or linked(root / LEASE):
            raise ValueError("linked_ownership_metadata")
        try:
            lease = lock_file(root / LEASE)
        except OSError:
            result["reason"] = "active_or_unavailable_lease"
            return result
        record = json.loads((root / MARKER).read_text(encoding="utf-8"))
        if (not isinstance(record, dict) or record.get("schema") != 1 or
                record.get("namespace") != root.name or
                not re.fullmatch(r"[a-f0-9]{32}", record.get("token", "")) or
                type(record.get("pid")) is not int or record["pid"] <= 0 or
                str(record["pid"]) != root.name.split("-", 1)[0] or
                type(record.get("created")) is not int or
                not isinstance(record.get("children"), list) or
                not isinstance(record.get("pending_spawns"), list)):
            raise ValueError("invalid_ownership_record")
        if record.get("pending_spawns"):
            result["reason"] = "unresolved_spawn"
            return result
        for child in record["children"]:
            if (not isinstance(child, dict) or type(child.get("pid")) is not int or
                    child["pid"] <= 0 or "created" not in child):
                raise ValueError("invalid_child_record")
            current = process_identity(child["pid"])
            if current == "unknown" or child["created"] == "unknown" or (current is not None and current == child["created"]):
                result["reason"] = "live_or_unverifiable_child"
                return result
        parents = {record["pid"]: record["created"]}
        parents.update({c["pid"]: c["created"] for c in record["children"]})
        if native_children(parents):
            result["reason"] = "live_or_unverifiable_native_descendant"
            return result
        files, directories, size = inspect_payload(root)
        # Preserve ownership evidence on sharing violations/partial payload cleanup.
        for child in root.iterdir():
            if child.name not in {MARKER, LEASE}:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        # No process can join this namespace; children never own the parent lease.
        unlock(lease)
        lease = None
        try:
            (root / MARKER).unlink()
            (root / LEASE).unlink()
            root.rmdir()
        except OSError:
            # The final empty-directory removal can also fail (e.g. antivirus).
            # Restore evidence rather than leaving an unowned, uncollectable root.
            if root.is_dir() and not linked(root):
                (root / MARKER).write_text(json.dumps(record), encoding="utf-8")
                with (root / LEASE).open("ab") as stream:
                    if stream.tell() == 0:
                        stream.write(b"0")
            raise
        result.update(status="deleted", files=files, directories=directories, bytes=size)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result.update(reason=type(exc).__name__, detail=str(exc))
    finally:
        if lease is not None:
            try:
                unlock(lease)
            except OSError as exc:
                result.update(status="retained", reason="lease_release_failed", detail=str(exc))
    return result


def cleanup_stale(temp_root):
    start, cpu = time.perf_counter(), time.process_time()
    rows, legacy = [], 0
    try:
        safe_root(temp_root)
        if temp_root.is_dir():
            # Only inspect direct managed children, never recurse through legacy data.
            for root in temp_root.iterdir():
                if not linked(root) and root.is_dir() and (root / MARKER).is_file():
                    rows.append(cleanup_namespace(temp_root, root))
                else:
                    legacy += 1
    except (OSError, ValueError) as exc:
        rows.append({"path": str(temp_root), "status": "retained", "reason": str(exc)})
    report = {"rows": rows, "legacy_unowned_count": legacy,
              "elapsed_ms": (time.perf_counter() - start) * 1000,
              "cpu_ms": (time.process_time() - cpu) * 1000,
              "freed_bytes": sum(r.get("bytes", 0) for r in rows if r["status"] == "deleted")}
    return report


def diagnostic(report, phase):
    # Do not create a new log session, nor place cleanup diagnostics inside temp.
    logger = sys.modules.get("session_logging")
    if logger is not None:
        try:
            rows = report.get("rows", [])
            logger.event("TEMP_CLEANUP", cleanup_phase=phase,
                         deleted_count=sum(r["status"] == "deleted" for r in rows),
                         retained_count=sum(r["status"] == "retained" for r in rows),
                         freed_bytes=sum(r.get("bytes", 0) for r in rows if r["status"] == "deleted"),
                         elapsed_ms=report.get("elapsed_ms", 0), result=report)
        except Exception:
            pass


class TempOwner:
    def __init__(self, temp_root, namespace):
        self.temp_root, self.root = temp_root, temp_root / namespace
        self._lock = threading.RLock()
        self.closed = False
        safe_root(temp_root)
        self.startup_cleanup = cleanup_stale(temp_root)
        self.root.mkdir(parents=True, exist_ok=False)
        self.lease = lock_file(self.root / LEASE, create=True)
        self.record = {"schema": 1, "namespace": namespace, "token": uuid.uuid4().hex,
                       "pid": os.getpid(), "created": process_identity(os.getpid()),
                       "children": [], "pending_spawns": []}
        self._save()
        atexit.register(self.close)
        diagnostic(self.startup_cleanup, "startup")

    def _save(self):
        staging = self.root / (MARKER + ".tmp")
        staging.write_text(json.dumps(self.record), encoding="utf-8")
        os.replace(staging, self.root / MARKER)

    def begin_spawn(self):
        with self._lock:
            if self.closed:
                raise RuntimeError("Temp lifecycle already closed")
            token = uuid.uuid4().hex
            self.record["pending_spawns"].append(token)
            self._save()
            return token

    def finish_spawn(self, token, pid=None):
        with self._lock:
            if token not in self.record["pending_spawns"]:
                return
            if pid:
                self.record["children"].append({"pid": int(pid), "created": process_identity(pid)})
            self.record["pending_spawns"].remove(token)
            self._save()

    def close(self):
        with self._lock:
            if self.closed:
                return getattr(self, "shutdown_cleanup", {})
            self.closed = True
            try:
                unlock(self.lease)
            except OSError as exc:
                self.shutdown_cleanup = {"rows": [{"path": str(self.root), "status": "retained",
                    "reason": "lease_release_failed", "detail": str(exc)}]}
                diagnostic(self.shutdown_cleanup, "shutdown")
                return self.shutdown_cleanup
            start, cpu = time.perf_counter(), time.process_time()
            row = cleanup_namespace(self.temp_root, self.root)
            self.shutdown_cleanup = {"rows": [row], "elapsed_ms": (time.perf_counter()-start)*1000,
                                     "cpu_ms": (time.process_time()-cpu)*1000}
            diagnostic(self.startup_cleanup, "startup")
            diagnostic(self.shutdown_cleanup, "shutdown")
            return self.shutdown_cleanup
