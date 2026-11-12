"""Bounded asynchronous recorder for live OCR crop diagnostics."""

from __future__ import annotations

import hashlib
import io
import json
import os
import queue
import re
import statistics
import threading
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from data_paths import DataPaths
import session_logging
from typing import Any, Callable


ENV_ENABLED = "GAME_TRANSLATOR_LIVE_OCR_CROP_DEBUG"
ENV_MAX_TASKS = "GAME_TRANSLATOR_LIVE_OCR_CROP_MAX_TASKS"
ENV_MAX_BYTES = "GAME_TRANSLATOR_LIVE_OCR_CROP_MAX_BYTES"
ENV_QUEUE_SIZE = "GAME_TRANSLATOR_LIVE_OCR_CROP_QUEUE_SIZE"

DEFAULT_MAX_TASKS = 240
DEFAULT_MAX_BYTES = 512 * 1024 * 1024
DEFAULT_QUEUE_SIZE = 32
SCHEMA_VERSION = 1


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    try:
        scalar = value.item()
        if scalar is not value:
            return _json_safe(scalar)
    except Exception:
        pass
    try:
        return _json_safe(value.tolist())
    except Exception:
        pass
    if value.__class__.__name__ == "TextMaskInfo":
        return {
            "signature": str(getattr(value, "signature", "") or ""),
            "density": float(getattr(value, "density", 0.0) or 0.0),
            "width": int(getattr(value, "width", 0) or 0),
            "height": int(getattr(value, "height", 0) or 0),
        }
    return str(value)


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _utc_iso(value: float | None = None) -> str:
    timestamp = time.time() if value is None else float(value)
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="milliseconds")


def safe_windows_name(value: Any, fallback: str = "profile", limit: int = 80) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(value or "").strip())
    text = re.sub(r"\s+", " ", text).rstrip(" .")
    if not text:
        text = fallback
    return text[:limit].rstrip(" .") or fallback


def live_ocr_crop_debug_enabled(environ: dict[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    return session_logging.raw_enabled() and _truthy(source.get(ENV_ENABLED))


def create_live_ocr_crop_recorder(
    project_root: str | os.PathLike[str],
    manifest: dict[str, Any],
    *,
    event_callback: Callable[..., None] | None = None,
) -> "LiveOcrCropRecorder | None":
    """Create the recorder only when the explicit environment switch is enabled."""
    if not live_ocr_crop_debug_enabled():
        return None
    return LiveOcrCropRecorder(
        project_root,
        manifest,
        max_tasks=_bounded_int(os.environ.get(ENV_MAX_TASKS), DEFAULT_MAX_TASKS, 1, 10000),
        max_bytes=_bounded_int(
            os.environ.get(ENV_MAX_BYTES),
            DEFAULT_MAX_BYTES,
            1024,
            64 * 1024 * 1024 * 1024,
        ),
        queue_size=_bounded_int(os.environ.get(ENV_QUEUE_SIZE), DEFAULT_QUEUE_SIZE, 1, 1024),
        event_callback=event_callback,
    )


class LiveOcrCropRecorder:
    """Save copied live OCR inputs on one bounded background writer thread."""

    def __init__(
        self,
        project_root: str | os.PathLike[str],
        manifest: dict[str, Any],
        *,
        max_tasks: int = DEFAULT_MAX_TASKS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        event_callback: Callable[..., None] | None = None,
        output_base: str | os.PathLike[str] | None = None,
        writer_delay_sec: float = 0.0,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.max_tasks = max(1, int(max_tasks))
        self.max_bytes = max(1024, int(max_bytes))
        self.control_reserve_bytes = min(
            self.max_bytes // 2,
            max(64 * 1024, min(32 * 1024 * 1024, self.max_bytes // 10)),
        )
        self.image_budget_bytes = max(0, self.max_bytes - self.control_reserve_bytes)
        self.queue_size = max(1, int(queue_size))
        self._event_callback = event_callback
        self._writer_delay_sec = max(0.0, float(writer_delay_sec))
        self._lock = threading.RLock()
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=self.queue_size)
        self._accepting = True
        self._shutdown_requested = False
        self._limit_reason = ""
        self._limit_event_emitted = False
        self._accepted_records: dict[str, dict[str, Any]] = {}
        self._task_keys_by_id: dict[int, list[str]] = {}
        self._task_metadata: dict[str, dict[str, Any]] = {}
        self._dropped: list[dict[str, Any]] = []
        self._write_errors: list[dict[str, Any]] = []
        self._copy_ms: list[float] = []
        self._enqueue_ms: list[float] = []
        self._writer_wait_ms: list[float] = []
        self._max_queue_depth = 0
        self._saved_tasks = 0
        self._saved_image_bytes = 0
        self._updates_saved = 0
        self._updates_dropped = 0
        self._capture_error_seq = 0
        self._final_summary: dict[str, Any] | None = None

        profile_name = safe_windows_name(manifest.get("profile_name"), "profile")
        session_stamp = time.strftime("%Y%m%d_%H%M%S")
        self.session_id = f"{session_stamp}_{profile_name}"
        base = (
            Path(output_base).resolve()
            if output_base is not None
            else DataPaths(self.project_root).path("diagnostics", "ocr_debug", "live_crops")
        )
        candidate = base / self.session_id
        suffix = 2
        while candidate.exists():
            candidate = base / f"{self.session_id}_{suffix}"
            suffix += 1
        self.session_dir = candidate
        self.crops_dir = self.session_dir / "crops"
        self.manifest_path = self.session_dir / "session_manifest.json"
        self.tasks_path = self.session_dir / "tasks.jsonl"
        self.summary_path = self.session_dir / "final_summary.json"
        self._started_at = time.time()
        self._manifest = dict(manifest or {})
        self._manifest.update(
            {
                "schema_version": SCHEMA_VERSION,
                "session_id": self.session_id,
                "start_time": _utc_iso(self._started_at),
                "end_time": None,
                "debug_limits": {
                    "max_tasks": self.max_tasks,
                    "max_bytes": self.max_bytes,
                    "control_reserve_bytes": self.control_reserve_bytes,
                    "image_budget_bytes": self.image_budget_bytes,
                    "queue_size": self.queue_size,
                },
                "output_path": self._portable_path(self.session_dir),
                "image_format": "PNG",
            }
        )

        self._thread = threading.Thread(
            target=self._writer_loop,
            name="LiveOcrCropDebugWriter",
            daemon=True,
        )
        self._thread.start()
        self._queue.put_nowait(
            {
                "kind": "session_start",
                "enqueued_perf": time.perf_counter(),
            }
        )

    @property
    def writer_thread(self) -> threading.Thread:
        return self._thread

    @property
    def accepting(self) -> bool:
        with self._lock:
            return bool(self._accepting)

    def _portable_path(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.project_root)).replace("\\", "/")
        except ValueError:
            return str(path.resolve()).replace("\\", "/")

    def _emit(
        self,
        event: str,
        *,
        task_id: int | None = None,
        outcome: str = "",
        reason: str = "",
        extra: str = "",
    ) -> None:
        callback = self._event_callback
        if callback is None:
            return
        try:
            details = "; ".join(
                part
                for part in (
                    f"outcome={outcome}" if outcome else "",
                    f"session_path={self._portable_path(self.session_dir)}",
                    extra,
                )
                if part
            )
            callback(event, task_id=task_id, reason=reason, extra=details)
        except Exception:
            pass

    @staticmethod
    def _record_key(task_id: int, generation: int) -> str:
        return f"{int(generation)}:{int(task_id)}"

    def _resolve_key(self, task_id: int, generation: int | None = None) -> str | None:
        if generation is not None:
            exact = self._record_key(task_id, generation)
            if exact in self._accepted_records:
                return exact
        keys = self._task_keys_by_id.get(int(task_id), [])
        return keys[-1] if keys else None

    def _drop(self, task_id: int | None, reason: str, *, record_key: str = "") -> None:
        row = {
            "task_id": task_id,
            "record_key": record_key or None,
            "reason": str(reason),
            "timestamp": _utc_iso(),
        }
        with self._lock:
            self._dropped.append(row)
        self._emit(
            "LIVE_OCR_CROP_DEBUG_ITEM_DROPPED",
            task_id=task_id,
            reason=reason,
        )

    def _mark_limit(self, reason: str) -> None:
        emit = False
        with self._lock:
            self._accepting = False
            if not self._limit_reason:
                self._limit_reason = str(reason)
            if not self._limit_event_emitted:
                self._limit_event_emitted = True
                emit = True
        if emit:
            self._emit("LIVE_OCR_CROP_DEBUG_LIMIT_REACHED", reason=reason)

    def record_task(
        self,
        raw_image: Any,
        processed_image: Any,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Copy two live images and enqueue them without blocking the OCR thread."""
        if str((metadata or {}).get("pipeline_mode") or "") != "live":
            return {"accepted": False, "reason": "not_live_pipeline"}
        task_id = int((metadata or {}).get("task_id") or 0)
        generation = int((metadata or {}).get("runtime_generation") or 0)
        record_key = self._record_key(task_id, generation)

        with self._lock:
            if not self._accepting:
                reason = self._limit_reason or "recorder_not_accepting"
                self._drop(task_id, reason, record_key=record_key)
                return {"accepted": False, "reason": reason}
            if len(self._accepted_records) >= self.max_tasks:
                self._mark_limit("max_tasks")
                self._drop(task_id, "max_tasks", record_key=record_key)
                return {"accepted": False, "reason": "max_tasks"}
            if self._queue.full():
                self._drop(task_id, "queue_full", record_key=record_key)
                return {"accepted": False, "reason": "queue_full"}

        copy_started = time.perf_counter()
        try:
            raw_copy = raw_image.copy()
            processed_copy = processed_image.copy()
        except Exception as exc:
            reason = f"image_copy_failed:{type(exc).__name__}:{exc}"
            self._drop(task_id, reason, record_key=record_key)
            return {"accepted": False, "reason": reason}
        copy_ms = (time.perf_counter() - copy_started) * 1000.0

        task_metadata = _json_safe(self._task_defaults(metadata))
        # Validate before enqueueing so the writer cannot fail later on a
        # TextMaskInfo or backend-specific result object.
        json.dumps(task_metadata, ensure_ascii=False, sort_keys=True)
        task_metadata["record_key"] = record_key
        task_metadata["copy_ms"] = round(copy_ms, 3)
        task_metadata["enqueue_ms"] = None
        task_metadata["writer_wait_ms"] = None
        item = {
            "kind": "task",
            "record_key": record_key,
            "task_id": task_id,
            "raw_image": raw_copy,
            "processed_image": processed_copy,
            "metadata": task_metadata,
            "enqueued_perf": 0.0,
        }

        manifest_update_needed = False
        client_rect = task_metadata.get("game_client_rect")
        if isinstance(client_rect, dict) and client_rect.get("width") and client_rect.get("height"):
            with self._lock:
                if not self._manifest.get("game_client_size"):
                    self._manifest["game_client_size"] = {
                        "width": int(client_rect["width"]),
                        "height": int(client_rect["height"]),
                    }
                    manifest_update_needed = True

        enqueue_started = time.perf_counter()
        item["enqueued_perf"] = enqueue_started
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            enqueue_ms = (time.perf_counter() - enqueue_started) * 1000.0
            with self._lock:
                self._copy_ms.append(copy_ms)
                self._enqueue_ms.append(enqueue_ms)
            self._drop(task_id, "queue_full_after_copy", record_key=record_key)
            return {
                "accepted": False,
                "reason": "queue_full_after_copy",
                "copy_ms": round(copy_ms, 3),
                "enqueue_ms": round(enqueue_ms, 3),
            }
        enqueue_ms = (time.perf_counter() - enqueue_started) * 1000.0
        task_metadata["enqueue_ms"] = round(enqueue_ms, 3)
        if manifest_update_needed:
            try:
                self._queue.put_nowait(
                    {
                        "kind": "manifest_update",
                        "enqueued_perf": time.perf_counter(),
                    }
                )
            except queue.Full:
                pass
        with self._lock:
            self._accepted_records[record_key] = {
                "task_id": task_id,
                "generation": generation,
            }
            self._task_keys_by_id.setdefault(task_id, []).append(record_key)
            self._copy_ms.append(copy_ms)
            self._enqueue_ms.append(enqueue_ms)
            self._max_queue_depth = max(self._max_queue_depth, self._queue.qsize())
            accepted_count = len(self._accepted_records)
        if accepted_count >= self.max_tasks:
            self._mark_limit("max_tasks")
        return {
            "accepted": True,
            "record_key": record_key,
            "copy_ms": round(copy_ms, 3),
            "enqueue_ms": round(enqueue_ms, 3),
        }

    def record_capture_error(self, metadata: dict[str, Any]) -> bool:
        """Record a capture-stage failure without changing production task IDs."""
        with self._lock:
            if not self._accepting:
                return False
            self._capture_error_seq += 1
            sequence = self._capture_error_seq
        row = self._task_defaults(metadata)
        row.update(
            {
                "record_key": f"capture_error:{sequence}",
                "task_id": None,
                "outcome": "capture_error",
                "source_result": "capture_error",
            }
        )
        try:
            self._queue.put_nowait(
                {
                    "kind": "capture_error",
                    "record_key": row["record_key"],
                    "task_id": None,
                    "metadata": row,
                    "enqueued_perf": time.perf_counter(),
                }
            )
            with self._lock:
                self._max_queue_depth = max(self._max_queue_depth, self._queue.qsize())
            return True
        except queue.Full:
            self._drop(None, "queue_full_capture_error", record_key=row["record_key"])
            return False

    def update_task(
        self,
        task_id: int | None,
        *,
        generation: int | None = None,
        **fields: Any,
    ) -> bool:
        if task_id is None:
            return False
        with self._lock:
            record_key = self._resolve_key(int(task_id), generation)
        if not record_key:
            return False
        item = {
            "kind": "update",
            "record_key": record_key,
            "task_id": int(task_id),
            "fields": dict(fields),
            "enqueued_perf": time.perf_counter(),
        }
        try:
            self._queue.put_nowait(item)
            with self._lock:
                self._max_queue_depth = max(self._max_queue_depth, self._queue.qsize())
            return True
        except queue.Full:
            with self._lock:
                self._updates_dropped += 1
            self._drop(int(task_id), "queue_full_metadata_update", record_key=record_key)
            return False

    def record_ocr_result(
        self,
        task_id: int,
        *,
        generation: int,
        source_result: str,
        raw_text: str,
        normalized_text: str,
        ocr_started_at: float,
        ocr_finished_at: float,
        ocr_duration_ms: float,
        error: str | None = None,
    ) -> bool:
        outcome = "ocr_error" if error else ("nonempty" if normalized_text else "empty")
        return self.update_task(
            task_id,
            generation=generation,
            source_result=str(source_result or "ocr_backend"),
            raw_text=str(raw_text or ""),
            normalized_text=str(normalized_text or ""),
            ocr_started_at=_utc_iso(ocr_started_at),
            ocr_finished_at=_utc_iso(ocr_finished_at),
            ocr_duration_ms=round(float(ocr_duration_ms or 0.0), 3),
            total_task_duration_ms=round(
                max(0.0, ocr_finished_at - float(ocr_started_at or ocr_finished_at)) * 1000.0,
                3,
            ),
            outcome=outcome,
            error=error,
        )

    def note_runtime_event(
        self,
        event: str,
        *,
        task_id: int | None,
        generation: int | None,
        text: str,
        reason: str,
        extra: str,
    ) -> None:
        event_name = str(event or "")
        if task_id is None or event_name.startswith("LIVE_OCR_CROP_DEBUG_"):
            return
        fields: dict[str, Any] = {
            "last_runtime_event": event_name,
            "last_runtime_event_at": _utc_iso(),
        }
        stale_events = {
            "OCR_TASK_DROPPED_AFTER_NO_TEXT",
            "OCR_TASK_DROPPED_STALE_AFTER_OCR",
            "OCR_TASK_DROPPED_STALE_BEFORE_TRANSLATION",
            "OCR_TASK_DROPPED_STALE_AFTER_TRANSLATION",
            "OCR_TASK_DROPPED_STALE_BEFORE_UI",
            "OCR_TASK_DROPPED_STALE_AFTER_LLM_DIRECT",
            "TRANSLATION_RESULT_DROPPED_AFTER_NO_TEXT",
            "TRANSLATION_RESULT_DROPPED_VISUAL_STALE",
        }
        if event_name in stale_events:
            fields.update({"outcome": "stale", "stale_reason": str(reason or event_name)})
        elif event_name in {"OCR_POST_OCR_ERROR", "TRANSLATION_EMPTY"}:
            fields["error"] = str(reason or event_name)
        elif event_name == "OCR_TASK_ACCEPTED_FOR_TRANSLATION":
            fields["translation_started"] = True
        elif event_name == "OCR_TASK_APPLIED":
            fields["translation_applied"] = True
        elif event_name == "TRANSLATION_PIPELINE_PUBLISHED":
            fields["translation_published"] = True
        elif event_name in {"TRANSLATION_MEMORY_HIT", "LLM_DIRECT_MEMORY_HIT"}:
            fields["memory_hit"] = True
        elif event_name in {"TRANSLATION_MEMORY_MISS", "LLM_DIRECT_MEMORY_MISS"}:
            fields["memory_hit"] = False
        elif "CANCEL" in event_name and event_name.startswith(("OCR_", "TRANSLATION_")):
            fields.update({"outcome": "cancelled", "stale_reason": str(reason or event_name)})
        else:
            important_prefixes = (
                "OCR_GARBAGE",
                "OCR_SHORT_FRAGMENT",
                "OCR_REGRESSION",
                "OCR_DUPLICATE",
                "OCR_POST_CLEAR",
            )
            if event_name.startswith(important_prefixes):
                fields["classifier_runtime_event"] = {
                    "event": event_name,
                    "reason": str(reason or ""),
                    "details": str(extra or "")[:1000],
                    "text": str(text or "")[:1000],
                }
            else:
                return
        self.update_task(int(task_id), generation=generation, **fields)

    def note_classifier_decision(
        self,
        task_id: int | None,
        generation: int | None,
        classification: dict[str, Any],
        final_outcome: str,
        exact_reason_code: str | None,
    ) -> None:
        if task_id is None:
            return
        self.update_task(
            int(task_id),
            generation=generation,
            classifier_decision=dict(classification or {}),
            classifier_final_outcome=str(final_outcome or ""),
            classifier_reason=str(exact_reason_code or ""),
        )

    def note_gate_decision(
        self,
        task_id: int | None,
        generation: int | None,
        decision: dict[str, Any],
    ) -> None:
        if task_id is None:
            return
        self.update_task(
            int(task_id),
            generation=generation,
            gate_decision=dict(decision or {}),
        )

    def stop_accepting(self, reason: str = "shutdown") -> None:
        with self._lock:
            self._accepting = False
            if not self._limit_reason and reason not in {"shutdown", "session_stopping"}:
                self._limit_reason = str(reason)

    def shutdown(self, timeout_sec: float = 3.0) -> dict[str, Any]:
        """Stop accepting items and wait a bounded time for the writer queue."""
        started = time.perf_counter()
        self.stop_accepting("shutdown")
        with self._lock:
            if self._shutdown_requested:
                if self._final_summary is not None:
                    return dict(self._final_summary)
            self._shutdown_requested = True
        deadline = time.perf_counter() + max(0.05, float(timeout_sec))
        stop_item = {"kind": "stop", "enqueued_perf": time.perf_counter()}
        queued = False
        while time.perf_counter() < deadline and not queued:
            try:
                self._queue.put(stop_item, timeout=min(0.05, max(0.001, deadline - time.perf_counter())))
                queued = True
            except queue.Full:
                pass
        remaining = max(0.0, deadline - time.perf_counter())
        if queued:
            self._thread.join(remaining)
        incomplete = self._thread.is_alive()
        if incomplete:
            summary = self._build_summary(incomplete=True, reason="shutdown_timeout")
            try:
                self.session_dir.mkdir(parents=True, exist_ok=True)
                self._atomic_write_json(self.summary_path, summary)
            except Exception:
                pass
            with self._lock:
                self._final_summary = dict(summary)
        else:
            with self._lock:
                summary = dict(self._final_summary or self._build_summary(incomplete=False))
        summary["shutdown_wait_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
        summary["writer_thread_alive"] = self._thread.is_alive()
        summary["session_disk_bytes"] = self._directory_size(self.session_dir)
        summary["session_within_max_bytes"] = summary["session_disk_bytes"] <= self.max_bytes
        try:
            self._atomic_write_json(self.summary_path, summary)
        except Exception:
            pass
        with self._lock:
            self._final_summary = dict(summary)
        self._emit(
            "LIVE_OCR_CROP_DEBUG_SESSION_FINISHED",
            reason="shutdown_timeout" if incomplete else "normal_shutdown",
            extra=f"saved={summary.get('saved_tasks', 0)}; dropped={summary.get('dropped_count', 0)}; incomplete={incomplete}",
        )
        return summary

    def _writer_loop(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if self._writer_delay_sec:
                    time.sleep(self._writer_delay_sec)
                writer_wait_ms = max(
                    0.0,
                    (time.perf_counter() - float(item.get("enqueued_perf") or time.perf_counter())) * 1000.0,
                )
                with self._lock:
                    self._writer_wait_ms.append(writer_wait_ms)
                kind = item.get("kind")
                if kind == "session_start":
                    self.session_dir.mkdir(parents=True, exist_ok=True)
                    self.crops_dir.mkdir(parents=True, exist_ok=True)
                    self._atomic_write_json(self.manifest_path, self._manifest)
                    self._emit(
                        "LIVE_OCR_CROP_DEBUG_SESSION_STARTED",
                        extra=(
                            f"max_tasks={self.max_tasks}; max_bytes={self.max_bytes}; "
                            f"queue_size={self.queue_size}"
                        ),
                    )
                elif kind == "task":
                    self._write_task_item(item, writer_wait_ms)
                elif kind == "manifest_update":
                    self._atomic_write_json(self.manifest_path, self._manifest)
                elif kind == "capture_error":
                    metadata = dict(item["metadata"])
                    metadata["writer_wait_ms"] = round(writer_wait_ms, 3)
                    task_dir = self.crops_dir / safe_windows_name(
                        item["record_key"],
                        "capture_error",
                    )
                    task_dir.mkdir(parents=True, exist_ok=True)
                    metadata_path = task_dir / "metadata.json"
                    metadata["metadata_path"] = self._portable_path(metadata_path)
                    self._atomic_write_json(metadata_path, metadata)
                    self._task_metadata[item["record_key"]] = metadata
                    self._append_task_snapshot(metadata, "capture_error")
                elif kind == "update":
                    self._write_update_item(item, writer_wait_ms)
                elif kind == "stop":
                    self._finish_writer()
                    return
            except Exception as exc:
                row = {
                    "record_key": item.get("record_key"),
                    "task_id": item.get("task_id"),
                    "error": f"{type(exc).__name__}: {exc}",
                    "timestamp": _utc_iso(),
                }
                with self._lock:
                    self._write_errors.append(row)
                self._emit(
                    "LIVE_OCR_CROP_DEBUG_WRITE_FAILED",
                    task_id=item.get("task_id"),
                    reason=row["error"],
                )
            finally:
                self._queue.task_done()

    def _write_task_item(self, item: dict[str, Any], writer_wait_ms: float) -> None:
        task_id = int(item["task_id"])
        record_key = str(item["record_key"])
        metadata = dict(item["metadata"])
        self._emit(
            "LIVE_OCR_CROP_DEBUG_ITEM_QUEUED",
            task_id=task_id,
            extra=(
                f"copy_ms={float(metadata.get('copy_ms') or 0.0):.3f}; "
                f"enqueue_ms={float(metadata.get('enqueue_ms') or 0.0):.3f}; "
                f"writer_wait_ms={writer_wait_ms:.3f}"
            ),
        )
        raw_bytes = self._png_bytes(item["raw_image"])
        processed_bytes = self._png_bytes(item["processed_image"])
        payload_bytes = len(raw_bytes) + len(processed_bytes)
        with self._lock:
            projected = self._saved_image_bytes + payload_bytes
        if projected > self.image_budget_bytes:
            self._mark_limit("max_bytes")
            self._drop(task_id, "max_bytes", record_key=record_key)
            return

        folder_name = f"task_{task_id:06d}"
        task_dir = self.crops_dir / folder_name
        if task_dir.exists():
            generation = int(metadata.get("runtime_generation") or 0)
            task_dir = self.crops_dir / f"{folder_name}_g{generation:06d}"
        task_dir.mkdir(parents=True, exist_ok=True)
        raw_path = task_dir / "raw.png"
        processed_path = task_dir / "processed.png"
        metadata_path = task_dir / "metadata.json"
        self._atomic_write_bytes(raw_path, raw_bytes)
        self._atomic_write_bytes(processed_path, processed_bytes)

        raw_sha = hashlib.sha256(raw_bytes).hexdigest()
        processed_sha = hashlib.sha256(processed_bytes).hexdigest()
        metadata.update(
            {
                "writer_wait_ms": round(writer_wait_ms, 3),
                "raw_image_path": self._portable_path(raw_path),
                "processed_image_path": self._portable_path(processed_path),
                "metadata_path": self._portable_path(metadata_path),
                "raw_sha256": raw_sha,
                "processed_sha256": processed_sha,
                "raw_file_bytes": len(raw_bytes),
                "processed_file_bytes": len(processed_bytes),
                "processed_identical_to_raw": raw_sha == processed_sha,
                "saved_at": _utc_iso(),
            }
        )
        self._atomic_write_json(metadata_path, metadata)
        self._task_metadata[record_key] = metadata
        self._append_task_snapshot(metadata, "initial")
        with self._lock:
            self._saved_tasks += 1
            self._saved_image_bytes += payload_bytes
        self._emit(
            "LIVE_OCR_CROP_DEBUG_ITEM_SAVED",
            task_id=task_id,
            outcome=str(metadata.get("outcome") or "pending"),
            extra=f"raw_bytes={len(raw_bytes)}; processed_bytes={len(processed_bytes)}; writer_wait_ms={writer_wait_ms:.3f}",
        )

    def _write_update_item(self, item: dict[str, Any], writer_wait_ms: float) -> None:
        record_key = str(item["record_key"])
        metadata = self._task_metadata.get(record_key)
        if metadata is None:
            with self._lock:
                self._updates_dropped += 1
            return
        metadata.update(dict(item.get("fields") or {}))
        metadata["last_metadata_update_at"] = _utc_iso()
        metadata["last_metadata_writer_wait_ms"] = round(writer_wait_ms, 3)
        metadata_path = Path(str(metadata.get("metadata_path") or ""))
        if not metadata_path.is_absolute():
            metadata_path = self.project_root / metadata_path
        self._atomic_write_json(metadata_path, metadata)
        self._append_task_snapshot(metadata, "update")
        with self._lock:
            self._updates_saved += 1

    def _append_task_snapshot(self, metadata: dict[str, Any], phase: str) -> None:
        row = dict(metadata)
        row["record_phase"] = str(phase)
        row["recorded_at"] = _utc_iso()
        self.tasks_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.tasks_path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    def _finish_writer(self) -> None:
        ended_at = time.time()
        self._manifest["end_time"] = _utc_iso(ended_at)
        self._atomic_write_json(self.manifest_path, self._manifest)
        summary = self._build_summary(incomplete=False)
        self._atomic_write_json(self.summary_path, summary)
        with self._lock:
            self._final_summary = dict(summary)

    def _build_summary(self, *, incomplete: bool, reason: str = "") -> dict[str, Any]:
        with self._lock:
            copy_ms = list(self._copy_ms)
            enqueue_ms = list(self._enqueue_ms)
            writer_wait_ms = list(self._writer_wait_ms)
            dropped = list(self._dropped)
            errors = list(self._write_errors)
            accepted = len(self._accepted_records)
            saved = self._saved_tasks
            saved_bytes = self._saved_image_bytes
            max_depth = self._max_queue_depth
            updates_saved = self._updates_saved
            updates_dropped = self._updates_dropped
            limit_reason = self._limit_reason
        outcomes: dict[str, int] = {}
        for metadata in self._task_metadata.values():
            outcome = str(metadata.get("outcome") or "pending")
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
        return {
            "schema_version": SCHEMA_VERSION,
            "session_id": self.session_id,
            "session_path": self._portable_path(self.session_dir),
            "started_at": _utc_iso(self._started_at),
            "finished_at": _utc_iso(),
            "incomplete": bool(incomplete),
            "incomplete_reason": str(reason or ""),
            "accepted_tasks": accepted,
            "saved_tasks": saved,
            "saved_image_bytes": saved_bytes,
            "dropped_count": len(dropped),
            "dropped": dropped,
            "write_error_count": len(errors),
            "write_errors": errors,
            "metadata_updates_saved": updates_saved,
            "metadata_updates_dropped": updates_dropped,
            "max_queue_depth": max_depth,
            "queue_size": self.queue_size,
            "max_tasks": self.max_tasks,
            "max_bytes": self.max_bytes,
            "control_reserve_bytes": self.control_reserve_bytes,
            "image_budget_bytes": self.image_budget_bytes,
            "session_disk_bytes_before_final_summary": self._directory_size(self.session_dir),
            "limit_reason": limit_reason,
            "copy_ms": self._timing_summary(copy_ms),
            "enqueue_ms": self._timing_summary(enqueue_ms),
            "production_overhead_ms": self._timing_summary(
                [left + right for left, right in zip(copy_ms, enqueue_ms)]
            ),
            "writer_wait_ms": self._timing_summary(writer_wait_ms),
            "outcomes": outcomes,
            "writer_thread_alive": self._thread.is_alive(),
        }

    @staticmethod
    def _timing_summary(values: list[float]) -> dict[str, Any]:
        samples = sorted(float(value) for value in values)
        if not samples:
            return {"count": 0, "median": None, "mean": None, "p95": None, "max": None}
        p95_index = max(0, min(len(samples) - 1, int(len(samples) * 0.95 + 0.999999) - 1))
        return {
            "count": len(samples),
            "median": round(statistics.median(samples), 3),
            "mean": round(statistics.fmean(samples), 3),
            "p95": round(samples[p95_index], 3),
            "max": round(samples[-1], 3),
        }

    @staticmethod
    def _image_size(image: Any) -> dict[str, Any] | None:
        size = getattr(image, "size", None)
        if isinstance(size, (tuple, list)) and len(size) >= 2:
            return {"width": int(size[0]), "height": int(size[1])}
        shape = getattr(image, "shape", None)
        if isinstance(shape, (tuple, list)) and len(shape) >= 2:
            return {"width": int(shape[1]), "height": int(shape[0])}
        return None

    @staticmethod
    def _directory_size(path: Path) -> int:
        if not path.exists():
            return 0
        total = 0
        for candidate in path.rglob("*"):
            if not candidate.is_file():
                continue
            try:
                total += candidate.stat().st_size
            except OSError:
                pass
        return total

    def _task_defaults(self, metadata: dict[str, Any]) -> dict[str, Any]:
        row = {
            "session_id": self.session_id,
            "task_id": None,
            "runtime_generation": None,
            "no_text_generation": None,
            "active_task_id": None,
            "profile_name": None,
            "pipeline_mode": "live",
            "capture_backend": None,
            "ocr_backend": None,
            "ocr_model": None,
            "created_at": _utc_iso(),
            "capture_started_at": None,
            "capture_finished_at": None,
            "preprocess_started_at": None,
            "preprocess_finished_at": None,
            "ocr_started_at": None,
            "ocr_finished_at": None,
            "source_result": None,
            "raw_text": "",
            "normalized_text": "",
            "raw_image_path": None,
            "processed_image_path": None,
            "raw_sha256": None,
            "processed_sha256": None,
            "raw_size": None,
            "processed_size": None,
            "roi": None,
            "game_client_rect": None,
            "global_overlay_rect": None,
            "outcome": "pending",
            "classifier_decision": None,
            "gate_decision": None,
            "translation_started": False,
            "translation_applied": False,
            "translation_published": False,
            "memory_hit": None,
            "memory_stored": None,
            "stale_reason": None,
            "error": None,
        }
        row.update(dict(metadata or {}))
        for key in (
            "created_at",
            "capture_started_at",
            "capture_finished_at",
            "preprocess_started_at",
            "preprocess_finished_at",
            "ocr_started_at",
            "ocr_finished_at",
        ):
            value = row.get(key)
            if isinstance(value, (int, float)) and value > 0:
                row[key] = _utc_iso(float(value))
        return row

    @staticmethod
    def _png_bytes(image: Any) -> bytes:
        buffer = io.BytesIO()
        if hasattr(image, "save"):
            image.save(buffer, format="PNG")
        else:
            from PIL import Image

            Image.fromarray(image).save(buffer, format="PNG")
        return buffer.getvalue()

    @staticmethod
    def _atomic_write_bytes(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{threading.get_ident()}.tmp")
        with open(temp_path, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)

    @classmethod
    def _atomic_write_json(cls, path: Path, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        cls._atomic_write_bytes(path, data)
