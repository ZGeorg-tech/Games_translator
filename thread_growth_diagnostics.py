"""Opt-in OS/Python thread snapshots for Live Paddle diagnostics."""

from __future__ import annotations

import json
import os
import threading
import session_logging
from datetime import datetime
from pathlib import Path
from typing import Any


_APP_DIR = Path(__file__).resolve().parent
_ENV_NAME = "GAME_TRANSLATOR_THREAD_DIAGNOSTICS"
_LOCK = threading.RLock()
_LOG_PATH: Path | None = None
_PREVIOUS_OS_THREAD_IDS: set[int] | None = None
_PREVIOUS_NATIVE_ONLY_IDS: set[int] | None = None


def diagnostics_enabled() -> bool:
    return session_logging.raw_enabled() and str(os.environ.get(_ENV_NAME, "") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def diagnostic_log_path() -> str:
    return str(_LOG_PATH) if _LOG_PATH is not None else ""


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _safe_bool_call(obj: Any, method_name: str) -> bool | None:
    if obj is None:
        return None
    try:
        return bool(getattr(obj, method_name)())
    except Exception:
        return None


def _python_threads() -> tuple[list[dict[str, Any]], set[int]]:
    rows = []
    native_ids: set[int] = set()
    for item in threading.enumerate():
        native_id = getattr(item, "native_id", None)
        if native_id is not None:
            native_ids.add(_safe_int(native_id))
        rows.append(
            {
                "name": str(getattr(item, "name", "") or ""),
                "ident": getattr(item, "ident", None),
                "native_id": native_id,
                "daemon": bool(getattr(item, "daemon", False)),
                "alive": bool(item.is_alive()),
            }
        )
    rows.sort(key=lambda row: (str(row.get("name") or ""), _safe_int(row.get("native_id"))))
    return rows, native_ids


def _live_worker_snapshot(worker: Any) -> dict[str, Any]:
    if worker is None:
        return {
            "present": False,
            "worker_identity": 0,
            "backend_identity": 0,
            "backend_context_identity": 0,
            "backend_initialization_count": 0,
            "ocr_request_count": 0,
        }
    return {
        "present": True,
        "worker_identity": _safe_int(getattr(worker, "worker_identity", id(worker))),
        "worker_thread_ident": _safe_int(getattr(worker, "worker_thread_ident", 0)),
        "worker_thread_native_id": _safe_int(getattr(worker, "worker_thread_native_id", 0)),
        "qthread_running": _safe_bool_call(worker, "isRunning"),
        "backend_name": str(getattr(worker, "backend_name", "") or ""),
        "source_lang": str(getattr(worker, "source_lang", "") or ""),
        "backend_identity": _safe_int(getattr(worker, "backend_identity", 0)),
        "backend_context_identity": _safe_int(
            getattr(worker, "backend_context_identity", 0)
        ),
        "backend_initialization_count": _safe_int(
            getattr(worker, "backend_initialization_count", 0)
        ),
        "ocr_request_count": _safe_int(getattr(worker, "_request_seq", 0)),
        "active_request_count": _safe_int(getattr(worker, "_active_request_count", 0)),
        "max_active_request_count": _safe_int(
            getattr(worker, "_max_active_request_count", 0)
        ),
    }


def _dynamic_worker_snapshot(worker: Any) -> dict[str, Any]:
    if worker is None:
        return {"present": False, "worker_identity": 0, "context_identity": 0}
    active_request_id = 0
    try:
        active_request_id = _safe_int(worker.active_request_id())
    except Exception:
        active_request_id = _safe_int(getattr(worker, "request_id", 0))
    return {
        "present": True,
        "worker_identity": _safe_int(id(worker)),
        "context_identity": _safe_int(getattr(worker, "backend_context_identity", 0)),
        "qthread_running": _safe_bool_call(worker, "isRunning"),
        "active_request_id": active_request_id,
        "busy": _safe_bool_call(worker, "is_busy"),
    }


def _session_snapshot(engine: Any, session_data: Any, roi: Any) -> dict[str, Any]:
    if not isinstance(session_data, dict):
        session_data = getattr(engine, "session_data", {}) if engine is not None else {}
    if not isinstance(session_data, dict):
        session_data = {}
    translation = session_data.get("translation", {}) or {}
    ocr = session_data.get("ocr", {}) or {}
    blocks = session_data.get("language_blocks", {}) or {}
    monitor_zone = session_data.get("monitor_zone", {}) or {}
    if isinstance(roi, dict):
        monitor_zone = {**monitor_zone, **roi}
    language_block = (
        blocks.get("active")
        or session_data.get("active_language_block")
        or session_data.get("language_mode")
        or ""
    )
    return {
        "language_block": str(language_block or ""),
        "target_language": str(translation.get("target_lang", "") or ""),
        "ocr_backend": str(ocr.get("engine", "") or ""),
        "roi_width": _safe_int(monitor_zone.get("width", 0)),
        "roi_height": _safe_int(monitor_zone.get("height", 0)),
        "overlay_running": bool(getattr(engine, "is_running", False)) if engine else False,
        "overlay_paused": bool(getattr(engine, "user_interaction_paused", False))
        if engine
        else False,
        "ocr_backend_busy": bool(getattr(engine, "_ocr_backend_busy", False))
        if engine
        else False,
        "runtime_generation": _safe_int(getattr(engine, "_runtime_generation", 0))
        if engine
        else 0,
    }


def _csanmt_processes(process: Any) -> list[dict[str, Any]]:
    rows = []
    try:
        children = process.children(recursive=True)
    except Exception:
        return rows
    for child in children:
        try:
            command = " ".join(child.cmdline())
            if "csanmt_worker.py" not in command.lower():
                continue
            rows.append(
                {
                    "pid": _safe_int(child.pid),
                    "name": str(child.name() or ""),
                    "role": "csanmt_worker_separate_process",
                }
            )
        except Exception:
            continue
    return rows


def capture_thread_snapshot(
    checkpoint: str,
    *,
    engine: Any = None,
    live_worker: Any = None,
    dynamic_worker: Any = None,
    session_data: Any = None,
    roi: Any = None,
    extra: Any = None,
) -> dict[str, Any] | None:
    """Write one fail-safe snapshot when explicitly enabled."""
    if not diagnostics_enabled():
        return None

    global _LOG_PATH, _PREVIOUS_OS_THREAD_IDS, _PREVIOUS_NATIVE_ONLY_IDS
    try:
        import psutil

        with _LOCK:
            process = psutil.Process(os.getpid())
            os_threads = process.threads()
            os_thread_ids = {_safe_int(item.id) for item in os_threads}
            python_threads, python_native_ids = _python_threads()
            native_only_ids = os_thread_ids - python_native_ids
            if _PREVIOUS_OS_THREAD_IDS is None:
                new_ids: list[int] = []
                gone_ids: list[int] = []
            else:
                new_ids = sorted(os_thread_ids - _PREVIOUS_OS_THREAD_IDS)
                gone_ids = sorted(_PREVIOUS_OS_THREAD_IDS - os_thread_ids)
            if _PREVIOUS_NATIVE_ONLY_IDS is None:
                new_native_only_ids: list[int] = []
                gone_native_only_ids: list[int] = []
            else:
                new_native_only_ids = sorted(native_only_ids - _PREVIOUS_NATIVE_ONLY_IDS)
                gone_native_only_ids = sorted(_PREVIOUS_NATIVE_ONLY_IDS - native_only_ids)
            _PREVIOUS_OS_THREAD_IDS = set(os_thread_ids)
            _PREVIOUS_NATIVE_ONLY_IDS = set(native_only_ids)

            row = {
                "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "checkpoint": str(checkpoint or "UNKNOWN"),
                "process_pid": os.getpid(),
                "os_thread_count": len(os_thread_ids),
                "os_thread_ids": sorted(os_thread_ids),
                "python_thread_count": len(python_threads),
                "python_threads": python_threads,
                "new_os_thread_ids_since_previous": new_ids,
                "gone_os_thread_ids_since_previous": gone_ids,
                "python_native_ids": sorted(python_native_ids),
                "native_only_thread_ids": sorted(native_only_ids),
                "native_only_thread_count": len(native_only_ids),
                "new_native_only_thread_ids_since_previous": new_native_only_ids,
                "gone_native_only_thread_ids_since_previous": gone_native_only_ids,
                "live_paddle": _live_worker_snapshot(live_worker),
                "dynamic": _dynamic_worker_snapshot(dynamic_worker),
                "current": _session_snapshot(engine, session_data, roi),
                "csanmt_separate_processes": _csanmt_processes(process),
                "extra": extra if isinstance(extra, dict) else {},
            }
            if _LOG_PATH is None:
                from data_paths import data_path
                output_dir = data_path("diagnostics")
                output_dir.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                _LOG_PATH = output_dir / f"thread_growth_{stamp}_{os.getpid()}.jsonl"
            with session_logging.open_log(_LOG_PATH, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            return row
    except Exception:
        return None
