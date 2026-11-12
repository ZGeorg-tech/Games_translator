"""Opt-in bounded monotonic OCR/UI telemetry, without pipeline ownership."""
from collections import deque
from contextlib import contextmanager
import json
import os
import threading
import time
from data_paths import data_path
import session_logging

ENABLED = session_logging.raw_enabled() and os.environ.get("GAME_TRANSLATOR_RESPONSIVENESS_DIAGNOSTICS", "") == "1"
_events, _gaps, _handlers, _resources = (deque(maxlen=n) for n in (1024, 4096, 4096, 1024))
_local = threading.local()
_lock = threading.Lock()
_stop = threading.Event()
_emitted = {}
_api_active = {}


def event(name, **fields):
    if ENABLED:
        row = {"event": name, "monotonic_ns": time.perf_counter_ns(), "pid": os.getpid(),
               "native_thread_id": threading.get_native_id(), **getattr(_local, "context", {}), **fields}
        with _lock:
            task = fields.get("task_id", row.get("task_id"))
            if name == "result_signal_emit":
                _emitted[task] = row["monotonic_ns"]
                if len(_emitted) > 64:
                    _emitted.pop(next(iter(_emitted)))
            elif name == "result_signal_receive" and task in _emitted:
                row["signal_wait_ms"] = (row["monotonic_ns"] - _emitted.pop(task)) / 1e6
            _events.append(row)


@contextmanager
def task_scope(task_id, backend, lang, **fields):
    if not ENABLED:
        yield
        return
    old = getattr(_local, "context", {})
    _local.context = {"task_id": task_id, "backend": backend, "lang": lang, **fields}
    event("task_started")
    try:
        yield
    finally:
        event("task_completed")
        _local.context = old


@contextmanager
def phase(name, **fields):
    if not ENABLED:
        yield
        return
    begin = time.perf_counter_ns()
    backend = fields.get("backend", getattr(_local, "context", {}).get("backend", ""))
    if name == "paddle_api":
        with _lock:
            _api_active[backend] = _api_active.get(backend, 0) + 1
            fields["api_active_at_start"] = _api_active[backend]
    try:
        yield
    finally:
        event("phase", phase=name, elapsed_ms=(time.perf_counter_ns() - begin) / 1e6, **fields)
        if name == "paddle_api":
            with _lock:
                _api_active[backend] -= 1


def summary(values):
    values = sorted(values)
    return {"count": len(values), **{key: values[min(len(values)-1, int((len(values)-1)*p))] if values else None
            for key, p in (("p50", .5), ("p95", .95), ("p99", .99), ("max", 1))}}


def snapshot():
    with _lock:
        return {"pid": os.getpid(), "monotonic_ns": time.perf_counter_ns(), "bounded": True,
                "ui_heartbeat_gap_ms": summary(list(_gaps)), "ui_handler_ms": summary(list(_handlers)),
                "events": list(_events), "resources": list(_resources)}


def flush():
    if not ENABLED or not session_logging.raw_enabled():
        return
    try:
        path = data_path("diagnostics", f"responsiveness_{os.getpid()}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(snapshot(), ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    except (OSError, ValueError, TypeError):
        pass


def application_class():
    from PyQt6.QtWidgets import QApplication
    if not ENABLED:
        return QApplication
    class DiagnosticApplication(QApplication):
        def notify(self, obj, event_object):
            begin = time.perf_counter_ns()
            try:
                return super().notify(obj, event_object)
            finally:
                with _lock:
                    _handlers.append((time.perf_counter_ns() - begin) / 1e6)
    return DiagnosticApplication


def install_ui_probe(app):
    if not ENABLED or getattr(app, "_responsiveness_probe", None) is not None:
        return
    from PyQt6.QtCore import QTimer
    import psutil
    process = psutil.Process()
    previous, last_flush = time.perf_counter_ns(), time.perf_counter()
    timer = QTimer(app)
    def tick():
        nonlocal previous, last_flush
        now = time.perf_counter_ns()
        with _lock:
            _gaps.append((now - previous) / 1e6)
        previous = now
        if time.perf_counter() - last_flush >= 5:
            last_flush = time.perf_counter()
            flush()
    timer.timeout.connect(tick)
    timer.start(50)
    app._responsiveness_probe = timer
    def sample():
        while not _stop.wait(.25):
            try:
                row = {"monotonic_ns": time.perf_counter_ns(), "cpu_seconds": sum(process.cpu_times()[:2]),
                       "rss_bytes": process.memory_info().rss, "native_threads": process.num_threads()}
                with _lock:
                    _resources.append(row)
            except psutil.Error:
                return
    sampler = threading.Thread(target=sample, name="responsiveness-sampler", daemon=True)
    sampler.start()
    def finish():
        _stop.set()
        sampler.join(.5)
        flush()
    app.aboutToQuit.connect(finish)
