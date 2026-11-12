"""Persistent production adapter for the app-local Block 3 FuguMT probe."""

from __future__ import annotations

import atexit
import json
import os
import queue
import statistics
import subprocess
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import startup_block3_diagnostics


from resource_registry import APP_ROOT as APP_DIR, resource_path
from python_runtime import package_directory, python_command, python_executable

MODEL_DIR = resource_path("fugu.model")
PYTHON_PATH = python_executable()
WORKER_PATH = resource_path("fugu.worker")
from data_paths import DATA, data_path
import session_logging
CACHE_ROOT = data_path("fugu.cache")
LOG_DIR = data_path("logs", "translation", "fugu")
BEAM_SIZE = 1
CPU_THREADS = 2
INTEROP_THREADS = 1
STARTUP_TIMEOUT_SEC = 90.0
TRANSLATION_TIMEOUT_SEC = 30.0
SHUTDOWN_TIMEOUT_SEC = 12.0


class FuguBackendError(RuntimeError):
    pass


def _required_paths() -> list[Path]:
    return [
        PYTHON_PATH,
        APP_DIR / "python_runtime.py",
        package_directory(),
        WORKER_PATH,
        MODEL_DIR,
        MODEL_DIR / "config.json",
        MODEL_DIR / "pytorch_model.bin",
        MODEL_DIR / "source.spm",
        MODEL_DIR / "target.spm",
    ]


def _c_path_findings(paths: dict[str, str]) -> list[str]:
    return [
        f"{name}={value}"
        for name, value in paths.items()
        if str(value).lower().startswith("c:\\") and not Path(value).resolve().is_relative_to(DATA.root)
    ]


def _build_worker_env(worker_log: Path) -> tuple[dict[str, str], dict[str, str]]:
    paths = DATA.child_environment_paths("fugu")
    for key in ("TEMP", "HOME", "APPDATA", "LOCALAPPDATA"):
        paths[key].mkdir(parents=True, exist_ok=True)
    effective = {name: str(path.resolve()) for name, path in paths.items()}
    effective.update(
        {
            "GAME_TRANSLATOR_TRANSLATION_MODEL_ROOT": str(
                resource_path("translation.models").resolve()
            ),
            "GAME_TRANSLATOR_FUGU_WORKER_LOG": str(worker_log.resolve()),
        }
    )
    env = os.environ.copy()
    env.update(effective)
    env.update(
        {
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8:backslashreplace",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "GAME_TRANSLATOR_ALLOW_MODEL_DOWNLOADS": "0",
            "CUDA_VISIBLE_DEVICES": "-1",
            "TOKENIZERS_PARALLELISM": "false",
            "OMP_NUM_THREADS": str(CPU_THREADS),
            "MKL_NUM_THREADS": str(CPU_THREADS),
            "OPENBLAS_NUM_THREADS": str(CPU_THREADS),
            "NUMEXPR_NUM_THREADS": str(CPU_THREADS),
        }
    )
    effective.update(
        {
            name: env[name]
            for name in (
                "CUDA_VISIBLE_DEVICES",
                "OMP_NUM_THREADS",
                "MKL_NUM_THREADS",
                "OPENBLAS_NUM_THREADS",
                "NUMEXPR_NUM_THREADS",
            )
        }
    )
    return env, effective


def preflight_fugu() -> dict[str, Any]:
    required = _required_paths()
    missing = [str(path) for path in required if not path.exists()]
    inspected = {
        "python_path": str(PYTHON_PATH),
        "worker_path": str(WORKER_PATH),
        "model_dir": str(MODEL_DIR),
        "cache_root": str(CACHE_ROOT),
        "log_dir": str(LOG_DIR),
    }
    findings = _c_path_findings(inspected)
    return {
        "ok": not missing and not findings,
        "model_id": "staka/fugumt-en-ja",
        "model_path": str(MODEL_DIR),
        "python_path": str(PYTHON_PATH),
        "worker_path": str(WORKER_PATH),
        "beam_size": BEAM_SIZE,
        "cpu_threads": CPU_THREADS,
        "interop_threads": INTEROP_THREADS,
        "device": "cpu",
        "missing": missing,
        "c_path_findings": findings,
        "downloads_allowed": False,
    }


class FuguBackend:
    name = "fugumt_en_ja"
    # Keep the accepted probe identity so promotion does not split existing TM entries.
    memory_key = "fugumt_en_ja_beam1_threads2_cpu_probe_v1"
    display_name = "FuguMT EN->JA"
    allow_fallback = False
    supports_request_context = True
    prefers_whole_text = True
    supported_pairs = {("en", "ja")}

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._start_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._translation_lock = threading.Lock()
        self._log_lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._worker_pid: int | None = None
        self._registered_pid: int | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._ready_event = threading.Event()
        self._stopped_event = threading.Event()
        self._pending: dict[str, queue.Queue[dict[str, Any]]] = {}
        self._ignored_request_ids: set[str] = set()
        self._stderr_tail: deque[str] = deque(maxlen=80)
        self._state = "not_started"
        self._fatal_error = ""
        self._request_seq = 0
        self._active_request_id = ""
        self._worker_creations = 0
        self._requests_sent = 0
        self._requests_succeeded = 0
        self._requests_failed = 0
        self._worker_max_active_requests = 0
        self._request_timings: list[float] = []
        self._request_cpu_pcts: list[float] = []
        self._request_logical_cores: list[float] = []
        self._request_thread_counts: list[int] = []
        self._peak_rss_mb = 0.0
        self._ready_payload: dict[str, Any] = {}
        self._last_translation_metrics: dict[str, Any] = {}
        self._effective_env: dict[str, str] = {}
        self._session_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        self._log_path = LOG_DIR / f"adapter_{self._session_id}.jsonl"
        self._worker_log_path = LOG_DIR / f"worker_{self._session_id}.jsonl"
        self._stderr_log_path = LOG_DIR / f"stderr_{self._session_id}.log"

    def _log(self, event: str, **details: Any) -> None:
        session_logging.component_event(event, self.name, details)
        if not session_logging.raw_enabled():
            return
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            row = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": event,
                "state": self._state,
                **details,
            }
            with self._log_lock:
                with session_logging.open_log(self._log_path, "a", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        except Exception:
            pass

    @staticmethod
    def _startup_event(event: str, **details: Any) -> None:
        try:
            startup_block3_diagnostics.emit_startup_diagnostic(event, **details)
        except Exception:
            pass

    def _process_alive(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    def _next_request_id(self) -> str:
        with self._lock:
            self._request_seq += 1
            return f"fugu-{self._request_seq}-{time.time_ns()}"

    def _send(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            process = self._process
            if process is None or process.poll() is not None or process.stdin is None:
                raise FuguBackendError("fugu_worker_not_running")
            process.stdin.write(line + "\n")
            process.stdin.flush()

    def _register_telemetry(self, pid: int) -> None:
        session_logging.register_process(pid, "fugu_worker")
        try:
            import resource_monitor

            registered = resource_monitor.register_resource_process(
                pid,
                role="translation_fugu",
                name="FuguMT worker",
            )
            self._log("FUGU_TELEMETRY_REGISTERED", worker_pid=pid, registered=registered)
            if registered:
                self._registered_pid = int(pid)
        except Exception as exc:
            self._log(
                "FUGU_TELEMETRY_REGISTER_ERROR",
                worker_pid=pid,
                error=f"{type(exc).__name__}:{exc}",
            )

    def _unregister_telemetry(self, pid: int) -> None:
        try:
            import resource_monitor

            resource_monitor.unregister_resource_process(pid)
            if self._registered_pid == int(pid):
                self._registered_pid = None
        except Exception:
            pass

    def _fail_pending(self, error: str) -> None:
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
            self._fatal_error = error
            self._state = "error"
        for waiter in pending:
            waiter.put({"ok": False, "error": error})
        self._ready_event.set()

    def _handle_payload(self, payload: dict[str, Any]) -> None:
        event = str(payload.get("event") or "")
        request_id = str(payload.get("request_id") or "")
        lifecycle_logs = {
            "worker_start": "FUGU_WORKER_STARTED",
            "heavy_import_begin": "FUGU_HEAVY_IMPORT_BEGIN",
            "heavy_import_end": "FUGU_HEAVY_IMPORT_END",
            "model_load_begin": "FUGU_MODEL_LOAD_BEGIN",
            "model_load_end": "FUGU_MODEL_LOAD_END",
            "warmup_begin": "FUGU_WARMUP_BEGIN",
            "warmup_end": "FUGU_WARMUP_END",
            "ready": "FUGU_READY",
            "shutdown_ack": "FUGU_SHUTDOWN_ACK",
            "stopped": "FUGU_WORKER_STOPPED",
        }
        if event == "worker_start":
            actual_pid = int(payload.get("pid") or 0)
            if actual_pid:
                previous_pid = self._registered_pid
                self._worker_pid = actual_pid
                if previous_pid and previous_pid != actual_pid:
                    self._unregister_telemetry(previous_pid)
                    self._register_telemetry(actual_pid)
        if "max_active_requests" in payload:
            with self._lock:
                self._worker_max_active_requests = max(
                    self._worker_max_active_requests,
                    int(payload.get("max_active_requests") or 0),
                )
        rss_mb = payload.get("rss_mb")
        if rss_mb is not None:
            with self._lock:
                self._peak_rss_mb = max(self._peak_rss_mb, float(rss_mb))
        if event in lifecycle_logs:
            safe = {
                key: value
                for key, value in payload.items()
                if key not in {"event", "translated_text"}
            }
            mapped = lifecycle_logs[event]
            self._log(mapped, **safe)
            self._startup_event(mapped, **safe)
        if event == "ready":
            with self._lock:
                self._ready_payload = dict(payload)
                self._state = "ready"
            self._ready_event.set()
            return
        if event == "fatal_error":
            error = str(payload.get("error") or "worker_fatal_error")
            self._log("FUGU_WORKER_FATAL_ERROR", error=error)
            self._fail_pending(error)
            return
        if event == "translation_result" and request_id:
            with self._lock:
                ignored = request_id in self._ignored_request_ids
                self._ignored_request_ids.discard(request_id)
                waiter = self._pending.pop(request_id, None)
                if not self._pending and self._process_alive() and self._state != "stopping":
                    self._state = "ready"
            if waiter is not None and not ignored:
                waiter.put(payload)
            return
        if event in {"shutdown_ack", "stopped"}:
            self._stopped_event.set()

    def _stdout_loop(self, stream: Any) -> None:
        try:
            for raw_line in iter(stream.readline, ""):
                line = str(raw_line).strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except Exception as exc:
                    self._fail_pending(f"invalid_fugu_worker_json:{type(exc).__name__}")
                    return
                if not isinstance(payload, dict):
                    self._fail_pending("invalid_fugu_worker_payload")
                    return
                self._handle_payload(payload)
        finally:
            try:
                stream.close()
            except Exception:
                pass
            if self._state not in {"stopping", "stopped", "error"}:
                process = self._process
                code = process.poll() if process is not None else None
                self._fail_pending(f"unexpected_fugu_worker_eof:exit_code={code}")
            self._stopped_event.set()

    def _stderr_loop(self, stream: Any) -> None:
        try:
            for raw_line in iter(stream.readline, ""):
                line = str(raw_line).rstrip("\r\n")
                if not line:
                    continue
                self._stderr_tail.append(line[-2000:])
                try:
                    if not session_logging.raw_enabled():
                        continue
                    LOG_DIR.mkdir(parents=True, exist_ok=True)
                    with session_logging.open_log(self._stderr_log_path,
                        "a", encoding="utf-8", errors="backslashreplace", newline="\n"
                    ) as handle:
                        handle.write(line + "\n")
                except Exception:
                    pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    @staticmethod
    def _verify_ready_payload(payload: dict[str, Any]) -> None:
        mismatches: dict[str, Any] = {}
        expected = {
            "beam_size": BEAM_SIZE,
            "cpu_threads": CPU_THREADS,
            "interop_threads": INTEROP_THREADS,
            "device": "cpu",
            "model_loads": 1,
            "warmups": 1,
        }
        for key, value in expected.items():
            if payload.get(key) != value:
                mismatches[key] = {"expected": value, "actual": payload.get(key)}
        if bool(payload.get("cuda_available")):
            mismatches["cuda_available"] = {"expected": False, "actual": True}
        if bool(payload.get("gpu_execution")):
            mismatches["gpu_execution"] = {"expected": False, "actual": True}
        runtime = dict(payload.get("runtime") or {})
        expected_python = str(PYTHON_PATH.resolve()).lower()
        if str(runtime.get("sys_executable") or "").lower() != expected_python:
            mismatches["sys_executable"] = {
                "expected": expected_python,
                "actual": runtime.get("sys_executable"),
            }
        if mismatches:
            raise FuguBackendError(f"fugu_runtime_mismatch:{mismatches}")

    def start(self) -> dict[str, Any]:
        with self._start_lock:
            with self._lock:
                if self._process_alive() and self._state in {"ready", "busy"}:
                    return dict(self._ready_payload)
            preflight = preflight_fugu()
            if not preflight["ok"]:
                raise FuguBackendError(
                    "fugu_preflight_failed:"
                    + ";".join([*preflight["missing"], *preflight["c_path_findings"]])
                )
            env, effective = _build_worker_env(self._worker_log_path)
            findings = _c_path_findings(effective)
            if findings:
                raise FuguBackendError("fugu_env_points_to_c:" + ";".join(findings))
            with self._lock:
                self._state = "starting"
                self._fatal_error = ""
                self._ready_payload = {}
                self._effective_env = effective
                self._worker_pid = None
                self._ready_event.clear()
                self._stopped_event.clear()
                self._stderr_tail.clear()
                self._worker_creations += 1
            command = python_command(WORKER_PATH, unbuffered=True)
            self._log(
                "FUGU_WORKER_SPAWN_BEGIN",
                python_path=str(PYTHON_PATH),
                model_path=str(MODEL_DIR),
                beam_size=BEAM_SIZE,
                cpu_threads=CPU_THREADS,
                device="cpu",
            )
            self._startup_event(
                "FUGU_WORKER_SPAWN_BEGIN",
                python_path=str(PYTHON_PATH),
                model_path=str(MODEL_DIR),
            )
            process = DATA.popen_temp(
                command,
                cwd=str(APP_DIR),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="backslashreplace",
                bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            with self._lock:
                self._process = process
                session_logging.register_process(process.pid, "fugu_control")
                self._worker_pid = process.pid
            self._log("FUGU_PROCESS_STARTED", worker_pid=process.pid)
            self._startup_event("FUGU_PROCESS_STARTED", worker_pid=process.pid)
            self._register_telemetry(process.pid)
            assert process.stdout is not None and process.stderr is not None
            self._stdout_thread = threading.Thread(
                target=self._stdout_loop,
                args=(process.stdout,),
                name="fugu-worker-stdout",
                daemon=True,
            )
            self._stderr_thread = threading.Thread(
                target=self._stderr_loop,
                args=(process.stderr,),
                name="fugu-worker-stderr",
                daemon=True,
            )
            self._stdout_thread.start()
            self._stderr_thread.start()
            if not self._ready_event.wait(timeout=STARTUP_TIMEOUT_SEC):
                self._log(
                    "FUGU_WORKER_START_TIMEOUT",
                    timeout_sec=STARTUP_TIMEOUT_SEC,
                    worker_pid=self._worker_pid,
                    stderr_tail=list(self._stderr_tail)[-20:],
                )
                self.shutdown(reason="startup_timeout")
                raise FuguBackendError(f"fugu_worker_startup_timeout:{STARTUP_TIMEOUT_SEC}s")
            with self._lock:
                payload = dict(self._ready_payload)
                fatal = self._fatal_error
                state = self._state
            if state != "ready":
                self.shutdown(reason="startup_error")
                raise FuguBackendError(fatal or f"fugu_worker_not_ready:{state}")
            try:
                self._verify_ready_payload(payload)
            except Exception:
                self.shutdown(reason="runtime_mismatch")
                raise
            return payload

    def load_model(self, request_context: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.start()

    def translate_text(
        self,
        text: str,
        src_lang: str = "en",
        target_lang: str = "ja",
        use_online: bool = False,
        request_context: dict[str, Any] | None = None,
    ) -> str:
        if use_online:
            raise FuguBackendError("fugu_offline_only")
        source = str(src_lang or "en").strip().lower()
        target = str(target_lang or "ja").strip().lower().replace("-", "_")
        source_text = str(text or "").strip()
        if (source, target) not in self.supported_pairs:
            raise FuguBackendError(f"unsupported_language_pair:{source}->{target}")
        if not source_text:
            return ""

        total_started = time.perf_counter()
        with self._translation_lock:
            queue_wait_ms = (time.perf_counter() - total_started) * 1000.0
            self.start()
            request_id = self._next_request_id()
            waiter: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            with self._lock:
                self._pending[request_id] = waiter
                self._active_request_id = request_id
                self._state = "busy"
                self._requests_sent += 1
                process = self._process
                pid = self._worker_pid or (process.pid if process is not None else None)
            self._log(
                "FUGU_TRANSLATE_BEGIN",
                request_id=request_id,
                source_chars=len(source_text),
                backend="Fugu",
                beam_size=BEAM_SIZE,
                cpu_threads=CPU_THREADS,
                queue_wait_ms=round(queue_wait_ms, 3),
                worker_pid=pid,
            )
            success = False
            failure = ""
            payload: dict[str, Any] = {}
            try:
                self._send(
                    {
                        "command": "translate",
                        "request_id": request_id,
                        "text": source_text,
                        "source_lang": "en",
                        "target_lang": "ja",
                    }
                )
                try:
                    payload = waiter.get(timeout=TRANSLATION_TIMEOUT_SEC)
                except queue.Empty as exc:
                    with self._lock:
                        self._pending.pop(request_id, None)
                        self._ignored_request_ids.add(request_id)
                    raise FuguBackendError(
                        f"fugu_translation_timeout:{TRANSLATION_TIMEOUT_SEC}s"
                    ) from exc
                if payload.get("cancelled"):
                    raise FuguBackendError("fugu_translation_cancelled")
                if not payload.get("ok"):
                    raise FuguBackendError(str(payload.get("error") or "fugu_translation_failed"))
                translated = str(payload.get("translated_text") or "").strip()
                if not translated:
                    raise FuguBackendError("fugu_empty_translation")
                success = True
                with self._lock:
                    self._requests_succeeded += 1
                return translated
            except Exception as exc:
                failure = f"{type(exc).__name__}:{exc}"
                with self._lock:
                    self._requests_failed += 1
                raise
            finally:
                total_ms = (time.perf_counter() - total_started) * 1000.0
                inference_ms = float(payload.get("inference_ms") or payload.get("elapsed_ms") or 0.0)
                metrics = {
                    "request_id": request_id,
                    "backend": "Fugu",
                    "source_char_count": len(source_text),
                    "beam_size": BEAM_SIZE,
                    "cpu_threads": CPU_THREADS,
                    "queue_wait_ms": round(queue_wait_ms, 3),
                    "inference_ms": round(inference_ms, 3),
                    "total_backend_ms": round(total_ms, 3),
                    "backend_ms": round(total_ms, 3),
                    "success": success,
                    "error": failure or str(payload.get("error") or ""),
                    "generated_tokens": payload.get("generated_tokens"),
                    "rss_mb": payload.get("rss_mb"),
                    "process_cpu_pct": payload.get("process_cpu_pct"),
                    "logical_cores_used": payload.get("logical_cores_used"),
                    "thread_count": payload.get("thread_count"),
                    "gpu_process_usage_mb": payload.get("gpu_process_usage_mb", 0),
                    "gpu_execution": bool(payload.get("gpu_execution", False)),
                    "worker_pid": pid,
                }
                with self._lock:
                    self._pending.pop(request_id, None)
                    if self._active_request_id == request_id:
                        self._active_request_id = ""
                    self._request_timings.append(total_ms)
                    if payload.get("process_cpu_pct") is not None:
                        self._request_cpu_pcts.append(float(payload["process_cpu_pct"]))
                    if payload.get("logical_cores_used") is not None:
                        self._request_logical_cores.append(float(payload["logical_cores_used"]))
                    if payload.get("thread_count") is not None:
                        self._request_thread_counts.append(int(payload["thread_count"]))
                    self._last_translation_metrics = metrics
                    if self._process_alive() and self._state != "stopping":
                        self._state = "ready"
                self._log("FUGU_TRANSLATE_END", **metrics)

    def get_last_translation_metrics(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._last_translation_metrics)

    def cancel_active(self, reason: str = "") -> int:
        with self._lock:
            request_id = self._active_request_id
            waiter = self._pending.pop(request_id, None) if request_id else None
            if request_id:
                self._ignored_request_ids.add(request_id)
                self._active_request_id = ""
        if waiter is None:
            return 0
        waiter.put(
            {
                "ok": False,
                "cancelled": True,
                "error": "fugu_translation_cancelled",
            }
        )
        self._log("FUGU_TRANSLATE_CANCELLED", request_id=request_id, reason=reason)
        return 1

    @staticmethod
    def _p95(values: list[float]) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * 0.95 + 0.999999)))
        return round(ordered[index], 3)

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            process = self._process
            timings = list(self._request_timings)
            cpu_pcts = list(self._request_cpu_pcts)
            logical_cores = list(self._request_logical_cores)
            thread_counts = list(self._request_thread_counts)
            return {
                "name": self.name,
                "memory_key": self.memory_key,
                "state": self._state,
                "pid": self._worker_pid if process is not None and process.poll() is None else None,
                "process_alive": self._process_alive(),
                "worker_creations": self._worker_creations,
                "model_loads": int(self._ready_payload.get("model_loads") or 0),
                "model_load_count": int(
                    self._ready_payload.get("model_load_count")
                    or self._ready_payload.get("model_loads")
                    or 0
                ),
                "warmups": int(self._ready_payload.get("warmups") or 0),
                "load_ms": self._ready_payload.get("load_ms"),
                "warmup_ms": self._ready_payload.get("warmup_ms"),
                "beam_size": self._ready_payload.get("beam_size"),
                "cpu_threads": self._ready_payload.get("cpu_threads"),
                "interop_threads": self._ready_payload.get("interop_threads"),
                "device": self._ready_payload.get("device"),
                "cuda_available": self._ready_payload.get("cuda_available"),
                "gpu_execution": self._ready_payload.get("gpu_execution"),
                "rss_mb": self._ready_payload.get("rss_mb"),
                "runtime": dict(self._ready_payload.get("runtime") or {}),
                "requests_sent": self._requests_sent,
                "requests_succeeded": self._requests_succeeded,
                "requests_failed": self._requests_failed,
                "max_active_requests": self._worker_max_active_requests,
                "request_median_ms": round(statistics.median(timings), 3) if timings else None,
                "request_p95_ms": self._p95(timings),
                "request_max_ms": round(max(timings), 3) if timings else None,
                "request_cpu_avg_pct": round(statistics.fmean(cpu_pcts), 3) if cpu_pcts else None,
                "request_logical_cores_avg": (
                    round(statistics.fmean(logical_cores), 3) if logical_cores else None
                ),
                "peak_thread_count": max(thread_counts) if thread_counts else None,
                "peak_rss_mb": round(self._peak_rss_mb, 3),
                "pending_requests": len(self._pending),
                "last_translation_metrics": dict(self._last_translation_metrics),
                "fatal_error": self._fatal_error,
                "effective_env": dict(self._effective_env),
                "c_path_findings": _c_path_findings(self._effective_env),
                "adapter_log": str(self._log_path),
                "worker_log": str(self._worker_log_path),
                "stderr_log": str(self._stderr_log_path),
                "stderr_tail": list(self._stderr_tail)[-12:],
            }

    def shutdown(self, reason: str = "") -> dict[str, Any]:
        with self._lock:
            process = self._process
            if process is None:
                self._state = "stopped"
                return {"backend": self.name, "stopped": True, "pid": None}
            pid = self._worker_pid or process.pid
            if process.poll() is not None:
                exit_code = process.poll()
                self._process = None
                self._worker_pid = None
                self._state = "stopped"
                self._unregister_telemetry(pid)
                return {
                    "backend": self.name,
                    "stopped": True,
                    "pid": pid,
                    "exit_code": exit_code,
                }
            self._state = "stopping"
        self._log("FUGU_SHUTDOWN_REQUESTED", worker_pid=pid, reason=reason)
        self._startup_event("FUGU_SHUTDOWN_REQUESTED", worker_pid=pid, reason=reason)
        self.cancel_active(reason or "backend_shutdown")
        self._stopped_event.clear()
        try:
            self._send({"command": "shutdown"})
        except Exception:
            pass
        try:
            process.wait(timeout=SHUTDOWN_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            try:
                process.terminate()
                process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                    process.wait(timeout=3.0)
                except Exception:
                    pass
            except Exception:
                pass
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass
        for thread in (self._stdout_thread, self._stderr_thread):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=1.0)
        exit_code = process.poll()
        with self._lock:
            self._process = None
            self._worker_pid = None
            self._state = "stopped"
            self._pending.clear()
            self._active_request_id = ""
        self._unregister_telemetry(pid)
        self._log(
            "FUGU_WORKER_STOPPED",
            worker_pid=pid,
            exit_code=exit_code,
            reason=reason,
        )
        return {
            "backend": self.name,
            "stopped": process.poll() is not None,
            "pid": pid,
            "exit_code": exit_code,
        }


_SINGLETON = FuguBackend()
atexit.register(_SINGLETON.shutdown)


def get_backend_singleton() -> FuguBackend:
    return _SINGLETON


__all__ = ["FuguBackend", "FuguBackendError", "get_backend_singleton", "preflight_fugu"]
