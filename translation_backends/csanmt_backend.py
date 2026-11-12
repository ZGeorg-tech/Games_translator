"""Persistent production adapter for the app-local Block 3 CSANMT runtime."""

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

RUNTIME_ROOT = resource_path("csanmt.runtime")
MODEL_DIR = resource_path("csanmt.model")
DEDICATED_PYTHON = python_executable()
WORKER_PATH = resource_path("csanmt.worker")
from data_paths import DATA, data_path
import session_logging
CACHE_ROOT = data_path("csanmt.cache")
LOG_DIR = data_path("logs", "translation", "csanmt")
BEAM_SIZE = 2
INTRA_OP_THREADS = 2
INTER_OP_THREADS = 1
STARTUP_TIMEOUT_SEC = 180.0
TRANSLATION_TIMEOUT_SEC = 30.0
SHUTDOWN_TIMEOUT_SEC = 12.0


class CSANMTBackendError(RuntimeError):
    pass


def _required_paths() -> list[Path]:
    return [
        DEDICATED_PYTHON,
        APP_DIR / "python_runtime.py",
        package_directory("csanmt"),
        WORKER_PATH,
        MODEL_DIR,
        MODEL_DIR / "configuration.json",
        MODEL_DIR / "tf_ckpts" / "ckpt-0.index",
        MODEL_DIR / "tf_ckpts" / "ckpt-0.meta",
    ]


def _c_path_findings(paths: dict[str, str]) -> list[str]:
    return [
        f"{name}={value}"
        for name, value in paths.items()
        if str(value).lower().startswith("c:\\") and not Path(value).resolve().is_relative_to(DATA.root)
    ]


def _build_worker_env(worker_log: Path) -> tuple[dict[str, str], dict[str, str]]:
    paths = DATA.child_environment_paths("csanmt")
    for key in ("TEMP", "HOME", "APPDATA", "LOCALAPPDATA"):
        paths[key].mkdir(parents=True, exist_ok=True)
    effective = {name: str(path.resolve()) for name, path in paths.items()}
    effective.update(
        {
            "GAME_TRANSLATOR_TRANSLATION_MODEL_ROOT": str(
                resource_path("translation.models").resolve()
            ),
            "GAME_TRANSLATOR_CSANMT_WORKER_LOG": str(worker_log.resolve()),
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
            "TF_CPP_MIN_LOG_LEVEL": "2",
            "TF_NUM_INTRAOP_THREADS": str(INTRA_OP_THREADS),
            "TF_NUM_INTEROP_THREADS": str(INTER_OP_THREADS),
            "OMP_NUM_THREADS": str(INTRA_OP_THREADS),
            "MKL_NUM_THREADS": str(INTRA_OP_THREADS),
            "OPENBLAS_NUM_THREADS": str(INTRA_OP_THREADS),
            "NUMEXPR_NUM_THREADS": str(INTRA_OP_THREADS),
        }
    )
    effective.update(
        {
            name: env[name]
            for name in (
                "TF_NUM_INTRAOP_THREADS",
                "TF_NUM_INTEROP_THREADS",
                "OMP_NUM_THREADS",
                "MKL_NUM_THREADS",
                "OPENBLAS_NUM_THREADS",
                "NUMEXPR_NUM_THREADS",
            )
        }
    )
    return env, effective


def preflight_csanmt() -> dict[str, Any]:
    required = _required_paths()
    missing = [str(path) for path in required if not path.exists()]
    inspected = {
        "dedicated_python": str(DEDICATED_PYTHON),
        "worker_path": str(WORKER_PATH),
        "model_dir": str(MODEL_DIR),
        "cache_root": str(CACHE_ROOT),
        "log_dir": str(LOG_DIR),
    }
    findings = _c_path_findings(inspected)
    return {
        "ok": not missing and not findings,
        "model_id": "iic/nlp_csanmt_translation_en2zh_base",
        "model_path": str(MODEL_DIR),
        "python_path": str(DEDICATED_PYTHON),
        "worker_path": str(WORKER_PATH),
        "beam_size": BEAM_SIZE,
        "intra_op_threads": INTRA_OP_THREADS,
        "inter_op_threads": INTER_OP_THREADS,
        "missing": missing,
        "c_path_findings": findings,
        "downloads_allowed": False,
    }


def _build_worker_command(importtime_enabled: bool | None = None) -> list[str]:
    enabled = (
        startup_block3_diagnostics.coldstart_importtime_enabled()
        if importtime_enabled is None
        else bool(importtime_enabled)
    )
    command = python_command(WORKER_PATH, unbuffered=True, domain="csanmt")
    if enabled:
        command[1:1] = ["-X", "importtime"]
    return command


class CSANMTBackend:
    name = "csanmt_en2zh_base"
    memory_key = "csanmt_en2zh_base_beam2_threads2_v1"
    display_name = "CSANMT EN->ZH-S"
    allow_fallback = False
    supports_request_context = True
    prefers_whole_text = True
    supported_pairs = {("en", "zh")}

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
        self._ready_payload: dict[str, Any] = {}
        self._effective_env: dict[str, str] = {}
        self._session_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        self._log_path = LOG_DIR / f"adapter_{self._session_id}.jsonl"
        self._worker_log_path = LOG_DIR / f"worker_{self._session_id}.jsonl"
        self._stderr_log_path = LOG_DIR / f"stderr_{self._session_id}.log"
        self._importtime_log_path = LOG_DIR / f"importtime_{self._session_id}.log"

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

    def _process_alive(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    def _next_request_id(self) -> str:
        with self._lock:
            self._request_seq += 1
            return f"csanmt-{self._request_seq}-{time.time_ns()}"

    def _send(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            process = self._process
            if process is None or process.poll() is not None or process.stdin is None:
                raise CSANMTBackendError("csanmt_worker_not_running")
            process.stdin.write(line + "\n")
            process.stdin.flush()

    def _register_telemetry(self, pid: int) -> None:
        session_logging.register_process(pid, "csanmt_worker")
        try:
            import resource_monitor

            registered = resource_monitor.register_resource_process(
                pid,
                role="translation_csanmt",
                name="CSANMT worker",
            )
            self._log("CSANMT_TELEMETRY_REGISTERED", worker_pid=pid, registered=registered)
            if registered:
                self._registered_pid = int(pid)
        except Exception as exc:
            self._log(
                "CSANMT_TELEMETRY_REGISTER_ERROR",
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
        if event == "worker_start":
            actual_pid = int(payload.get("pid") or 0)
            if actual_pid:
                previous_pid = self._registered_pid
                self._worker_pid = actual_pid
                if previous_pid and previous_pid != actual_pid:
                    self._unregister_telemetry(previous_pid)
                    self._register_telemetry(actual_pid)
            startup_block3_diagnostics.emit_startup_diagnostic(
                "CSANMT_WORKER_STARTED",
                worker_pid=actual_pid or self._worker_pid,
            )
        elif event == "heavy_import_begin":
            startup_block3_diagnostics.emit_startup_diagnostic(
                "CSANMT_HEAVY_IMPORT_BEGIN",
                worker_pid=self._worker_pid,
            )
        elif event == "heavy_import_end":
            startup_block3_diagnostics.emit_startup_diagnostic(
                "CSANMT_HEAVY_IMPORT_END",
                worker_pid=self._worker_pid,
                import_ms=payload.get("elapsed_ms"),
            )
        elif event == "model_load_begin":
            startup_block3_diagnostics.emit_startup_diagnostic(
                "CSANMT_MODEL_LOAD_BEGIN",
                worker_pid=self._worker_pid,
                model_path=payload.get("model_path"),
                beam_size=payload.get("beam_size"),
            )
            startup_block3_diagnostics.emit_startup_diagnostic(
                "CSANMT_MODEL_LOAD_STARTED",
                worker_pid=self._worker_pid,
                model_path=payload.get("model_path"),
                beam_size=payload.get("beam_size"),
            )
        elif event == "model_ready":
            startup_block3_diagnostics.emit_startup_diagnostic(
                "CSANMT_MODEL_READY",
                worker_pid=self._worker_pid,
                load_ms=payload.get("load_ms"),
                rss_mb=payload.get("rss_mb"),
            )
        elif event == "model_load_end":
            startup_block3_diagnostics.emit_startup_diagnostic(
                "CSANMT_MODEL_LOAD_END",
                worker_pid=self._worker_pid,
                load_ms=payload.get("load_ms"),
            )
        elif event == "warmup_begin":
            startup_block3_diagnostics.emit_startup_diagnostic(
                "CSANMT_WARMUP_BEGIN",
                worker_pid=self._worker_pid,
            )
            startup_block3_diagnostics.emit_startup_diagnostic(
                "CSANMT_WARMUP_STARTED",
                worker_pid=self._worker_pid,
            )
        elif event == "warmup_end":
            startup_block3_diagnostics.emit_startup_diagnostic(
                "CSANMT_WARMUP_END",
                worker_pid=self._worker_pid,
                warmup_ms=payload.get("elapsed_ms"),
            )
        elif event == "ready":
            startup_block3_diagnostics.emit_startup_diagnostic(
                "CSANMT_READY",
                worker_pid=self._worker_pid,
                ready_ms=payload.get("ready_ms"),
                load_ms=payload.get("load_ms"),
                warmup_ms=payload.get("warmup_ms"),
                model_loads=payload.get("model_loads"),
                warmups=payload.get("warmups"),
            )
            startup_block3_diagnostics.emit_startup_diagnostic(
                "CSANMT_RUNTIME_MODULES",
                worker_pid=self._worker_pid,
                runtime_modules=payload.get("runtime_modules") or {},
            )
        if "max_active_requests" in payload:
            with self._lock:
                self._worker_max_active_requests = max(
                    self._worker_max_active_requests,
                    int(payload.get("max_active_requests") or 0),
                )
        lifecycle_logs = {
            "worker_start": "CSANMT_WORKER_START",
            "heavy_import_begin": "CSANMT_HEAVY_IMPORT_BEGIN",
            "heavy_import_end": "CSANMT_HEAVY_IMPORT_END",
            "model_load_begin": "CSANMT_MODEL_LOAD_BEGIN",
            "model_load_end": "CSANMT_MODEL_LOAD_END",
            "model_ready": "CSANMT_MODEL_READY",
            "warmup_begin": "CSANMT_WARMUP_BEGIN",
            "warmup_end": "CSANMT_WARMUP_END",
            "ready": "CSANMT_WORKER_READY",
        }
        if event in lifecycle_logs:
            safe = {
                key: value
                for key, value in payload.items()
                if key not in {"event", "translated_text"}
            }
            self._log(lifecycle_logs[event], **safe)
        if event == "ready":
            with self._lock:
                self._ready_payload = dict(payload)
                self._state = "ready"
            self._ready_event.set()
            return
        if event == "fatal_error":
            self._fail_pending(str(payload.get("error") or "worker_fatal_error"))
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
                    self._fail_pending(f"invalid_csanmt_worker_json:{type(exc).__name__}")
                    return
                if not isinstance(payload, dict):
                    self._fail_pending("invalid_csanmt_worker_payload")
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
                self._fail_pending(f"unexpected_csanmt_worker_eof:exit_code={code}")
            self._stopped_event.set()

    def _stderr_loop(self, stream: Any) -> None:
        try:
            for raw_line in iter(stream.readline, ""):
                line = str(raw_line).rstrip("\r\n")
                if line:
                    importtime_line = bool(
                        startup_block3_diagnostics.coldstart_importtime_enabled()
                        and line.lstrip().startswith("import time:")
                    )
                    if not importtime_line:
                        self._stderr_tail.append(line[-2000:])
                    if not session_logging.raw_enabled():
                        continue
                    target_log = (
                        self._importtime_log_path if importtime_line else self._stderr_log_path
                    )
                    try:
                        LOG_DIR.mkdir(parents=True, exist_ok=True)
                        with session_logging.open_log(target_log,
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
        threads = dict(payload.get("threads") or {})
        expected = {
            "TF_NUM_INTRAOP_THREADS": str(INTRA_OP_THREADS),
            "TF_NUM_INTEROP_THREADS": str(INTER_OP_THREADS),
            "OMP_NUM_THREADS": str(INTRA_OP_THREADS),
            "MKL_NUM_THREADS": str(INTRA_OP_THREADS),
            "OPENBLAS_NUM_THREADS": str(INTRA_OP_THREADS),
            "NUMEXPR_NUM_THREADS": str(INTRA_OP_THREADS),
        }
        mismatches = {
            key: {"expected": value, "actual": threads.get(key)}
            for key, value in expected.items()
            if str(threads.get(key)) != value
        }
        if int(payload.get("beam_size") or -1) != BEAM_SIZE:
            mismatches["beam_size"] = {
                "expected": BEAM_SIZE,
                "actual": payload.get("beam_size"),
            }
        runtime = dict(payload.get("runtime") or {})
        expected_python = str(DEDICATED_PYTHON.resolve()).lower()
        actual_python = str(runtime.get("sys_executable") or "").lower()
        if actual_python != expected_python:
            mismatches["sys_executable"] = {
                "expected": expected_python,
                "actual": actual_python,
            }
        expected_site = str(
            (RUNTIME_ROOT / "Lib" / "site-packages").resolve()
        ).lower()
        for name in ("tensorflow_file", "modelscope_file", "torch_file", "numpy_file"):
            actual = str(runtime.get(name) or "").lower()
            if not actual.startswith(expected_site):
                mismatches[name] = {
                    "expected_prefix": expected_site,
                    "actual": actual,
                }
        python_root = resource_path("app.python").resolve()
        for name in ("sys_prefix", "sys_base_prefix", "sys_base_executable"):
            value = runtime.get(name)
            if not value or not Path(value).resolve().is_relative_to(python_root):
                mismatches[name] = value
        if runtime.get("user_site_enabled") is not False or not runtime.get("isolated"):
            mismatches["python_isolation"] = runtime
        if runtime.get("package_domain") != "csanmt":
            mismatches["package_domain"] = runtime.get("package_domain")
        forbidden = (
            str((resource_path("app.environment") / "Lib" / "site-packages").resolve()).lower(),
            str(
                (
                    APP_DIR
                    / "tools"
                    / "block3_fast_mt_lab"
                    / ".venv"
                    / "Lib"
                    / "site-packages"
                ).resolve()
            ).lower(),
        )
        foreign_paths = [
            value
            for value in runtime.get("sys_path") or []
            if (not value or not Path(value).resolve().is_relative_to(APP_DIR)
                or any(item in str(value).lower() for item in forbidden))
        ]
        if foreign_paths:
            mismatches["foreign_sys_path"] = foreign_paths
        if mismatches:
            raise CSANMTBackendError(f"csanmt_runtime_mismatch:{mismatches}")

    def start(self) -> dict[str, Any]:
        with self._start_lock:
            with self._lock:
                if self._process_alive() and self._state in {"ready", "busy"}:
                    return dict(self._ready_payload)
            preflight = preflight_csanmt()
            if not preflight["ok"]:
                raise CSANMTBackendError(
                    "csanmt_preflight_failed:"
                    + ";".join([*preflight["missing"], *preflight["c_path_findings"]])
                )
            env, effective = _build_worker_env(self._worker_log_path)
            findings = _c_path_findings(effective)
            if findings:
                raise CSANMTBackendError("csanmt_env_points_to_c:" + ";".join(findings))
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
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            importtime_enabled = startup_block3_diagnostics.coldstart_importtime_enabled()
            process_args = _build_worker_command(importtime_enabled)
            if importtime_enabled:
                self._importtime_log_path = LOG_DIR / f"importtime_{self._session_id}_{self._worker_creations}.log"
                startup_block3_diagnostics.emit_startup_diagnostic(
                    "CSANMT_IMPORT_PROFILE_CONFIGURED",
                    import_profile_path=str(self._importtime_log_path),
                    method="python_-X_importtime",
                )
            startup_block3_diagnostics.emit_startup_diagnostic(
                "CSANMT_PROCESS_SPAWN_BEGIN",
                python_path=str(DEDICATED_PYTHON),
                importtime=importtime_enabled,
                python_flags=["-X", "importtime"] if importtime_enabled else [],
            )
            process = DATA.popen_temp(
                process_args,
                cwd=str(APP_DIR),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="backslashreplace",
                bufsize=1,
                creationflags=creation_flags,
            )
            with self._lock:
                self._process = process
                session_logging.register_process(process.pid, "csanmt_control")
                self._worker_pid = process.pid
            startup_block3_diagnostics.emit_startup_diagnostic(
                "CSANMT_CONTROL_PROCESS_STARTED",
                worker_pid=process.pid,
                control_pid=process.pid,
            )
            startup_block3_diagnostics.emit_startup_diagnostic(
                "CSANMT_PROCESS_STARTED",
                worker_pid=process.pid,
                control_pid=process.pid,
            )
            self._register_telemetry(process.pid)
            assert process.stdout is not None and process.stderr is not None
            self._stdout_thread = threading.Thread(
                target=self._stdout_loop,
                args=(process.stdout,),
                name="csanmt-worker-stdout",
                daemon=True,
            )
            self._stderr_thread = threading.Thread(
                target=self._stderr_loop,
                args=(process.stderr,),
                name="csanmt-worker-stderr",
                daemon=True,
            )
            self._stdout_thread.start()
            self._stderr_thread.start()
            if not self._ready_event.wait(timeout=STARTUP_TIMEOUT_SEC):
                self._log(
                    "CSANMT_WORKER_START_TIMEOUT",
                    timeout_sec=STARTUP_TIMEOUT_SEC,
                    worker_pid=self._worker_pid,
                    stderr_tail=list(self._stderr_tail)[-20:],
                )
                self.shutdown(reason="startup_timeout")
                raise CSANMTBackendError(f"csanmt_worker_startup_timeout:{STARTUP_TIMEOUT_SEC}s")
            with self._lock:
                payload = dict(self._ready_payload)
                fatal = self._fatal_error
                state = self._state
            if state != "ready":
                self.shutdown(reason="startup_error")
                raise CSANMTBackendError(fatal or f"csanmt_worker_not_ready:{state}")
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
        target_lang: str = "zh",
        use_online: bool = False,
        request_context: dict[str, Any] | None = None,
    ) -> str:
        if use_online:
            raise CSANMTBackendError("csanmt_offline_only")
        source = str(src_lang or "en").strip().lower()
        target = str(target_lang or "zh").strip().lower().replace("-", "_")
        source_text = str(text or "").strip()
        if (source, target) not in self.supported_pairs:
            raise CSANMTBackendError(f"unsupported_language_pair:{source}->{target}")
        if not source_text:
            return ""
        requested_target = str((request_context or {}).get("target_language") or target)
        with self._translation_lock:
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
            started = time.perf_counter()
            self._log(
                "CSANMT_TRANSLATE_BEGIN",
                request_id=request_id,
                target=requested_target,
                source_chars=len(source_text),
                worker_pid=pid,
            )
            failure = ""
            success = False
            try:
                self._send(
                    {
                        "command": "translate",
                        "request_id": request_id,
                        "text": source_text,
                        "source_lang": "en",
                        "target_lang": "zh",
                        "requested_target": requested_target,
                    }
                )
                try:
                    payload = waiter.get(timeout=TRANSLATION_TIMEOUT_SEC)
                except queue.Empty as exc:
                    with self._lock:
                        self._pending.pop(request_id, None)
                        self._ignored_request_ids.add(request_id)
                    raise CSANMTBackendError(
                        f"csanmt_translation_timeout:{TRANSLATION_TIMEOUT_SEC}s"
                    ) from exc
                if payload.get("cancelled"):
                    raise CSANMTBackendError("csanmt_translation_cancelled")
                if not payload.get("ok"):
                    raise CSANMTBackendError(str(payload.get("error") or "csanmt_translation_failed"))
                translated = str(payload.get("translated_text") or "").strip()
                if not translated:
                    raise CSANMTBackendError("csanmt_empty_translation")
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
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                with self._lock:
                    self._pending.pop(request_id, None)
                    if self._active_request_id == request_id:
                        self._active_request_id = ""
                    self._request_timings.append(elapsed_ms)
                    if self._process_alive() and self._state != "stopping":
                        self._state = "ready"
                self._log(
                    "CSANMT_TRANSLATE_END",
                    request_id=request_id,
                    target=requested_target,
                    source_chars=len(source_text),
                    elapsed_ms=round(elapsed_ms, 3),
                    worker_pid=pid,
                    success=success,
                    failure=failure,
                )

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
                "error": "csanmt_translation_cancelled",
            }
        )
        self._log("CSANMT_TRANSLATE_CANCELLED", request_id=request_id, reason=reason)
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
            return {
                "name": self.name,
                "memory_key": self.memory_key,
                "state": self._state,
                "pid": self._worker_pid if process is not None and process.poll() is None else None,
                "control_pid": process.pid if process is not None and process.poll() is None else None,
                "process_alive": self._process_alive(),
                "worker_creations": self._worker_creations,
                "model_loads": int(self._ready_payload.get("model_loads") or 0),
                "warmups": int(self._ready_payload.get("warmups") or 0),
                "load_ms": self._ready_payload.get("load_ms"),
                "warmup_ms": self._ready_payload.get("warmup_ms"),
                "beam_size": self._ready_payload.get("beam_size"),
                "threads": dict(self._ready_payload.get("threads") or {}),
                "runtime": dict(self._ready_payload.get("runtime") or {}),
                "requests_sent": self._requests_sent,
                "requests_succeeded": self._requests_succeeded,
                "requests_failed": self._requests_failed,
                "max_active_requests": self._worker_max_active_requests,
                "request_median_ms": round(statistics.median(timings), 3) if timings else None,
                "request_p95_ms": self._p95(timings),
                "pending_requests": len(self._pending),
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
            control_pid = process.pid
            pid = self._worker_pid or control_pid
            if process.poll() is not None:
                self._process = None
                self._worker_pid = None
                self._state = "stopped"
                self._unregister_telemetry(pid)
                if control_pid != pid:
                    self._unregister_telemetry(control_pid)
                return {"backend": self.name, "stopped": True, "pid": pid, "exit_code": process.poll()}
            self._state = "stopping"
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
        if control_pid != pid:
            self._unregister_telemetry(control_pid)
        self._log(
            "CSANMT_WORKER_STOPPED",
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


_SINGLETON = CSANMTBackend()
atexit.register(_SINGLETON.shutdown)


def get_backend_singleton() -> CSANMTBackend:
    return _SINGLETON


__all__ = ["CSANMTBackend", "CSANMTBackendError", "get_backend_singleton", "preflight_csanmt"]
