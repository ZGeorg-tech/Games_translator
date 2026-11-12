from __future__ import annotations

import atexit
import json
import os
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


APP_DIR = Path(__file__).resolve().parent
from data_paths import data_path
import session_logging
LOG_DIR = data_path("logs", "translation", "pipeline")
LOG_MAX_BYTES = 2 * 1024 * 1024
QUEUE_MAX_ITEMS = 128
SETTLE_DELAY_SEC = 0.075


def _startup_trace(event: str, **details: Any) -> None:
    if not session_logging.raw_enabled():
        return
    path = str(os.environ.get("GAME_TRANSLATOR_STARTUP_TRACE", "") or "").strip()
    if not path:
        return
    try:
        thread = threading.current_thread()
        try:
            started_perf = float(os.environ.get("GAME_TRANSLATOR_STARTUP_T0_PERF", "0") or 0)
        except Exception:
            started_perf = 0.0
        elapsed_ms = int((time.perf_counter() - started_perf) * 1000) if started_perf > 0 else -1
        suffix = " ".join(f"{key}={' '.join(str(value).split())[:500]}" for key, value in details.items())
        line = (
            f"{time.strftime('%Y-%m-%d %H:%M:%S')} marker={event} "
            f"elapsed_ms={elapsed_ms} pid={os.getpid()} "
            f"thread_name={thread.name} thread_id={threading.get_ident()}"
            + (f" {suffix}" if suffix else "")
            + "\n"
        )
        with session_logging.open_log(path, "a", encoding="utf-8") as stream:
            stream.write(line)
            stream.flush()
    except Exception:
        pass


class OrderedPersistenceQueue:
    def __init__(self) -> None:
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=QUEUE_MAX_ITEMS)
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._accepting = True
        self._sentinel = object()
        self._log_path = LOG_DIR / f"persistence_{os.getpid()}.jsonl"

    def _log(self, event: str, **details: Any) -> None:
        session_logging.diagnostic_event(event, **details)
        if not session_logging.raw_enabled():
            return
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            if not session_logging.context() and self._log_path.exists() and self._log_path.stat().st_size >= LOG_MAX_BYTES:
                return
            row = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": str(event),
                **details,
            }
            with session_logging.open_log(self._log_path, "a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _ensure_thread_locked(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        _startup_trace("PERSISTENCE_WORKER_CREATE_BEGIN")
        try:
            self._thread = threading.Thread(
                target=self._worker,
                name="translation-persistence",
                daemon=True,
            )
            self._thread.start()
            _startup_trace("PERSISTENCE_WORKER_CREATE_END")
        except Exception as exc:
            _startup_trace("PERSISTENCE_WORKER_CREATE_ERROR", error=f"{type(exc).__name__}: {exc}")
            raise

    def submit(
        self,
        correlation_id: str,
        job: Callable[[], dict[str, Any] | None],
        on_complete: Callable[[dict[str, Any]], None] | None = None,
    ) -> bool:
        item = {
            "correlation_id": str(correlation_id or ""),
            "submitted_perf": time.perf_counter(),
            "job": job,
            "on_complete": on_complete,
        }
        with self._lock:
            if not self._accepting:
                self._log("persistence_rejected", correlation_id=item["correlation_id"], reason="shutdown")
                return False
            self._ensure_thread_locked()
            try:
                self._queue.put_nowait(item)
            except queue.Full:
                self._log(
                    "persistence_rejected",
                    correlation_id=item["correlation_id"],
                    reason="queue_full",
                    queue_max=QUEUE_MAX_ITEMS,
                )
                return False
        return True

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is self._sentinel:
                    return
                correlation_id = str(item.get("correlation_id") or "")
                time.sleep(SETTLE_DELAY_SEC)
                queue_wait_ms = (time.perf_counter() - float(item["submitted_perf"])) * 1000.0
                started = time.perf_counter()
                try:
                    details = dict(item["job"]() or {})
                    details.update(
                        {
                            "correlation_id": correlation_id,
                            "persistence_queue_wait_ms": round(queue_wait_ms, 3),
                            "persistence_total_ms": round((time.perf_counter() - started) * 1000.0, 3),
                            "persistence_error": "",
                        }
                    )
                    self._log("persistence_finished", **details)
                except Exception as exc:
                    details = {
                        "correlation_id": correlation_id,
                        "persistence_queue_wait_ms": round(queue_wait_ms, 3),
                        "persistence_total_ms": round((time.perf_counter() - started) * 1000.0, 3),
                        "persistence_error": f"{type(exc).__name__}: {exc}",
                    }
                    self._log("persistence_error", **details)
                callback = item.get("on_complete")
                if callable(callback):
                    try:
                        callback(details)
                    except Exception as exc:
                        self._log(
                            "persistence_callback_error",
                            correlation_id=correlation_id,
                            error=f"{type(exc).__name__}: {exc}",
                        )
            finally:
                self._queue.task_done()

    def flush(self, timeout_sec: float = 8.0) -> dict[str, Any]:
        started = time.perf_counter()
        deadline = time.monotonic() + max(0.0, float(timeout_sec))
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.02)
        pending = int(self._queue.unfinished_tasks)
        result = {
            "flushed": pending == 0,
            "pending": pending,
            "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "log_path": str(self._log_path),
        }
        self._log("persistence_flush", **result)
        return result

    def shutdown(self, timeout_sec: float = 8.0) -> dict[str, Any]:
        with self._lock:
            self._accepting = False
        result = self.flush(timeout_sec=timeout_sec)
        thread = self._thread
        if thread is not None and thread.is_alive():
            try:
                self._queue.put_nowait(self._sentinel)
                thread.join(timeout=1.5)
            except queue.Full:
                pass
        result["thread_alive"] = bool(thread is not None and thread.is_alive())
        return result

    @property
    def log_path(self) -> str:
        return str(self._log_path)


_QUEUE = OrderedPersistenceQueue()
atexit.register(_QUEUE.shutdown)


def submit(
    correlation_id: str,
    job: Callable[[], dict[str, Any] | None],
    on_complete: Callable[[dict[str, Any]], None] | None = None,
) -> bool:
    return _QUEUE.submit(correlation_id, job, on_complete)


def flush(timeout_sec: float = 8.0) -> dict[str, Any]:
    return _QUEUE.flush(timeout_sec)


def shutdown(timeout_sec: float = 8.0) -> dict[str, Any]:
    return _QUEUE.shutdown(timeout_sec)


def log_path() -> str:
    return _QUEUE.log_path
