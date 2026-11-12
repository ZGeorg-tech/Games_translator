"""Opt-in Block 3 CSANMT/Paddle startup diagnostics.

The module is deliberately inert unless the startup A/B/C diagnostic mode,
GAME_TRANSLATOR_COLDSTART_DIAGNOSTICS, or the explicit importtime mode is
enabled. It records production startup events and resource samples without
changing the normal parallel startup policy.
"""

from __future__ import annotations

import atexit
import ctypes
import json
import math
import os
import re
import statistics
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


APP_DIR = Path(__file__).resolve().parent
from data_paths import DATA, data_path
import session_logging
DIAGNOSTIC_DIR = data_path("diagnostics")
VALID_STARTUP_MODES = {"parallel", "csanmt_first", "paddle_first"}
_ENV_MODE = "GAME_TRANSLATOR_STARTUP_DIAGNOSTIC_MODE"
_ENV_LOG = "GAME_TRANSLATOR_STARTUP_DIAGNOSTIC_LOG"
_ENV_RESULT = "GAME_TRANSLATOR_STARTUP_DIAGNOSTIC_RESULT"
_ENV_COLDSTART = "GAME_TRANSLATOR_COLDSTART_DIAGNOSTICS"
_ENV_COLDSTART_IMPORTTIME = "GAME_TRANSLATOR_COLDSTART_IMPORTTIME"


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


def coldstart_diagnostics_requested() -> bool:
    return session_logging.raw_enabled() and _truthy(os.environ.get(_ENV_COLDSTART))


def coldstart_importtime_enabled() -> bool:
    return session_logging.raw_enabled() and _truthy(os.environ.get(_ENV_COLDSTART_IMPORTTIME))


def coldstart_diagnostics_enabled() -> bool:
    # Explicit import profiling needs the same phase/resource infrastructure,
    # but it remains the only flag that may enable ``-X importtime``.
    return coldstart_diagnostics_requested() or coldstart_importtime_enabled()


def selected_startup_mode() -> str:
    if not session_logging.raw_enabled():
        return ""
    value = str(os.environ.get(_ENV_MODE, "") or "").strip().lower()
    if value in VALID_STARTUP_MODES:
        return value
    # Cold-start diagnostics observe the production policy; they never select a
    # serial A/B/C ordering by themselves.
    return "parallel" if coldstart_diagnostics_enabled() else ""


def diagnostics_enabled() -> bool:
    return bool(selected_startup_mode())


_IMPORTTIME_RE = re.compile(
    r"^\s*import time:\s*(?P<self>\d+)\s*\|\s*(?P<cumulative>\d+)\s*\|\s*(?P<module>.+?)\s*$"
)


def parse_python_importtime(path: str | Path) -> dict[str, Any]:
    """Parse CPython ``-X importtime`` output without importing target packages."""
    import_path = Path(path)
    rows: list[dict[str, Any]] = []
    if import_path.is_file():
        for line in import_path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = _IMPORTTIME_RE.match(line)
            if not match:
                continue
            module = match.group("module").strip()
            rows.append(
                {
                    "module": module,
                    "self_us": int(match.group("self")),
                    "cumulative_us": int(match.group("cumulative")),
                }
            )

    package_totals: dict[str, dict[str, int]] = {}
    for row in rows:
        top_level = str(row["module"]).split(".", 1)[0]
        entry = package_totals.setdefault(
            top_level,
            {"self_us": 0, "max_cumulative_us": 0, "import_rows": 0},
        )
        entry["self_us"] += int(row["self_us"])
        entry["max_cumulative_us"] = max(
            entry["max_cumulative_us"],
            int(row["cumulative_us"]),
        )
        entry["import_rows"] += 1

    def used(prefixes: tuple[str, ...]) -> bool:
        return any(
            row["module"] == prefix or str(row["module"]).startswith(prefix + ".")
            for row in rows
            for prefix in prefixes
        )

    categories = {
        "tensorflow": ("tensorflow",),
        "keras": ("keras",),
        "modelscope": ("modelscope",),
        "torch": ("torch",),
        "numpy": ("numpy",),
        "protobuf/grpc": ("google.protobuf", "grpc"),
        "h5py": ("h5py",),
    }
    return {
        "path": str(import_path),
        "available": bool(rows),
        "import_rows": len(rows),
        "total_self_ms": round(sum(int(row["self_us"]) for row in rows) / 1000.0, 3),
        "max_cumulative_ms": round(
            max((int(row["cumulative_us"]) for row in rows), default=0) / 1000.0,
            3,
        ),
        "slowest_imports": [
            {
                "module": row["module"],
                "self_ms": round(int(row["self_us"]) / 1000.0, 3),
                "cumulative_ms": round(int(row["cumulative_us"]) / 1000.0, 3),
            }
            for row in sorted(rows, key=lambda item: int(item["self_us"]), reverse=True)[:25]
        ],
        "top_level_packages": [
            {
                "package": package,
                "self_ms": round(values["self_us"] / 1000.0, 3),
                "max_cumulative_ms": round(values["max_cumulative_us"] / 1000.0, 3),
                "import_rows": values["import_rows"],
            }
            for package, values in sorted(
                package_totals.items(),
                key=lambda item: item[1]["self_us"],
                reverse=True,
            )[:50]
        ],
        "required_package_usage": {
            name: used(prefixes) for name, prefixes in categories.items()
        },
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(item) for item in value]
    return str(value)


def _path_is_inside_project(path: Path) -> bool:
    try:
        path.resolve().relative_to(DATA.root)
        return True
    except Exception:
        return False


def _project_local_output(env_name: str, default_path: Path) -> Path:
    if session_logging.context():
        return default_path
    raw = str(os.environ.get(env_name, "") or "").strip()
    candidate = Path(raw).expanduser() if raw else default_path
    if not candidate.is_absolute():
        candidate = DATA.root / candidate
    if not _path_is_inside_project(candidate):
        return default_path
    return candidate.resolve()


def _percentile(values: Iterable[float], fraction: float) -> float | None:
    ordered = sorted(float(value) for value in values if value is not None)
    if not ordered:
        return None
    index = min(
        len(ordered) - 1,
        max(0, int(math.ceil((len(ordered) - 1) * fraction))),
    )
    return round(ordered[index], 3)


def _metric_summary(values: Iterable[float]) -> dict[str, float | None]:
    clean = [float(value) for value in values if value is not None]
    if not clean:
        return {"median": None, "p95": None, "max": None}
    return {
        "median": round(float(statistics.median(clean)), 3),
        "p95": _percentile(clean, 0.95),
        "max": round(max(clean), 3),
    }


class Block3StartupSequence:
    """Small shared coordinator used by production and the controlled smoke."""

    def __init__(self, mode: str) -> None:
        normalized = str(mode or "").strip().lower()
        if normalized not in VALID_STARTUP_MODES:
            raise ValueError(f"unsupported_startup_diagnostic_mode:{mode}")
        self.mode = normalized
        self._lock = threading.Lock()
        self._completed: set[str] = set()
        self._released: set[str] = set()

    def initial_components(self) -> tuple[str, ...]:
        if self.mode == "csanmt_first":
            return ("csanmt",)
        if self.mode == "paddle_first":
            return ("paddle",)
        return ("csanmt", "paddle")

    def deferred_components(self) -> tuple[str, ...]:
        if self.mode == "csanmt_first":
            return ("paddle",)
        if self.mode == "paddle_first":
            return ("csanmt",)
        return ()

    def component_finished(self, component: str) -> tuple[str, ...]:
        name = str(component or "").strip().lower()
        if name not in {"csanmt", "paddle"}:
            return ()
        with self._lock:
            self._completed.add(name)
            if self.mode == "csanmt_first" and name == "csanmt" and "paddle" not in self._released:
                self._released.add("paddle")
                return ("paddle",)
            if self.mode == "paddle_first" and name == "paddle" and "csanmt" not in self._released:
                self._released.add("csanmt")
                return ("csanmt",)
        return ()


class _StartupDiagnosticSession:
    def __init__(
        self,
        mode: str,
        *,
        process_started_monotonic: float | None,
        metadata: dict[str, Any] | None,
    ) -> None:
        self.mode = mode
        self.coldstart = coldstart_diagnostics_enabled()
        self.coldstart_requested = coldstart_diagnostics_requested()
        self.importtime_enabled = coldstart_importtime_enabled()
        self.overlay_pid = int(os.getpid())
        self.csanmt_control_pid = 0
        self.csanmt_worker_pid = 0
        self.process_t0 = float(process_started_monotonic or time.perf_counter())
        self.process_wall_time = time.time() - max(0.0, time.perf_counter() - self.process_t0)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        default_prefix = "block3_coldstart" if self.coldstart else "startup_block3"
        default_stem = DIAGNOSTIC_DIR / f"{default_prefix}_{stamp}_{self.overlay_pid}"
        self.log_path = _project_local_output(_ENV_LOG, default_stem.with_suffix(".jsonl"))
        self.result_path = _project_local_output(_ENV_RESULT, default_stem.with_suffix(".json"))
        self.metadata = dict(metadata or {})
        self.game_pid = self._pid_from_hwnd(int(self.metadata.get("game_hwnd") or 0))
        self.sample_interval_sec = 0.25 if self.coldstart else 1.0
        self._lock = threading.RLock()
        self._events: list[dict[str, Any]] = []
        self._first_event: dict[str, dict[str, Any]] = {}
        self._resource_samples: list[dict[str, Any]] = []
        self._deferred_started: dict[str, float] = {}
        self._deferred_wait_ms: dict[str, float] = {}
        self._both_ready_emitted = False
        self._finalized = False
        self._resource_stop = threading.Event()
        self._resource_processes: dict[int, Any] = {}
        self._page_fault_telemetry_seen = False
        self._system_cpu_primed = False
        try:
            import psutil  # type: ignore

            psutil.cpu_percent(interval=None)
            self._system_cpu_primed = True
        except Exception:
            pass
        self._resource_thread = threading.Thread(
            target=self._resource_loop,
            name="block3-startup-diagnostic-resources",
            daemon=True,
        )
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.result_path.parent.mkdir(parents=True, exist_ok=True)
        self.emit(
            "PROCESS_START",
            process_start_wall_timestamp=datetime.fromtimestamp(
                self.process_wall_time,
                tz=timezone.utc,
            ).isoformat(),
        )
        self.emit("STARTUP_MODE_SELECTED", **self.metadata)
        self._resource_thread.start()

    @staticmethod
    def _pid_from_hwnd(hwnd: int) -> int:
        if hwnd <= 0 or os.name != "nt":
            return 0
        try:
            pid = ctypes.c_ulong(0)
            ctypes.windll.user32.GetWindowThreadProcessId(
                int(hwnd),
                ctypes.byref(pid),
            )
            return int(pid.value or 0)
        except Exception:
            return 0

    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self.process_t0) * 1000.0, 3)

    def _append_row(self, row: dict[str, Any]) -> None:
        session_logging.diagnostic_event(str(row.get("event", "")), **{k: v for k, v in row.items() if k != "event"})
        with session_logging.open_log(self.log_path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    def emit(self, event: str, **details: Any) -> dict[str, Any]:
        event_name = str(event or "UNKNOWN")
        should_emit_both = False
        with self._lock:
            if self._finalized and event_name != "DIAGNOSTIC_FINALIZED":
                return {}
            if event_name in {"CSANMT_PROCESS_STARTED", "CSANMT_CONTROL_PROCESS_STARTED"}:
                control_pid = int(
                    details.get("control_pid")
                    or details.get("worker_pid")
                    or details.get("pid")
                    or 0
                )
                if control_pid:
                    self.csanmt_control_pid = control_pid
            elif event_name == "CSANMT_WORKER_STARTED":
                worker_pid = int(details.get("worker_pid") or details.get("pid") or 0)
                if worker_pid:
                    self.csanmt_worker_pid = worker_pid
            effective_csanmt_pid = self.csanmt_worker_pid or self.csanmt_control_pid
            row = {
                **_safe_value(details),
                "timestamp": _utc_now(),
                "elapsed_ms": self.elapsed_ms(),
                "event": event_name,
                "startup_mode": self.mode,
                "overlay_pid": self.overlay_pid,
                "csanmt_pid": effective_csanmt_pid or None,
                "csanmt_control_pid": self.csanmt_control_pid or None,
                "csanmt_worker_pid": self.csanmt_worker_pid or None,
            }
            self._events.append(row)
            self._first_event.setdefault(event_name, row)
            if event_name == "RESOURCE_SAMPLE":
                self._resource_samples.append(row)
            self._append_row(row)
            if (
                not self._both_ready_emitted
                and "PADDLE_READY" in self._first_event
                and "CSANMT_READY" in self._first_event
            ):
                self._both_ready_emitted = True
                should_emit_both = True
        if should_emit_both:
            both_row = self.emit("BOTH_COMPONENTS_READY")
            self._resource_stop.set()
            return both_row
        return row

    def mark_deferred(self, component: str, **details: Any) -> None:
        name = str(component or "").strip().lower()
        if name not in {"paddle", "csanmt"}:
            return
        with self._lock:
            self._deferred_started.setdefault(name, time.perf_counter())
        self.emit(f"{name.upper()}_START_DEFERRED", **details)

    def mark_requested(self, component: str, **details: Any) -> None:
        name = str(component or "").strip().lower()
        if name not in {"paddle", "csanmt"}:
            return
        deferred_wait_ms = 0.0
        with self._lock:
            deferred_started = self._deferred_started.pop(name, None)
            if deferred_started is not None:
                deferred_wait_ms = round((time.perf_counter() - deferred_started) * 1000.0, 3)
                self._deferred_wait_ms[name] = deferred_wait_ms
        event = "PADDLE_PREPARE_REQUESTED" if name == "paddle" else "CSANMT_START_REQUESTED"
        self.emit(event, deferred_wait_ms=deferred_wait_ms, **details)

    def _direct_process_info(self, pid: int, role: str) -> dict[str, Any] | None:
        if pid <= 0:
            return None
        try:
            import psutil  # type: ignore

            process = self._resource_processes.get(pid)
            if process is None:
                process = psutil.Process(pid)
                process.cpu_percent(interval=None)
                self._resource_processes[pid] = process
            with process.oneshot():
                info = process.memory_info()
                row: dict[str, Any] = {
                    "role": role,
                    "pid": pid,
                    "cpu_pct": float(process.cpu_percent(interval=None)),
                    "rss_mb": round(float(info.rss) / 1024.0 / 1024.0, 3),
                    "threads": int(process.num_threads()),
                }
                try:
                    io = process.io_counters()
                    row["read_bytes"] = int(io.read_bytes)
                    row["write_bytes"] = int(io.write_bytes)
                    row["read_count"] = int(io.read_count)
                    row["write_count"] = int(io.write_count)
                except Exception:
                    row["read_bytes"] = None
                    row["write_bytes"] = None
                    row["read_count"] = None
                    row["write_count"] = None
                page_fault_count = None
                for attr in ("num_page_faults", "page_faults", "pfaults"):
                    value = getattr(info, attr, None)
                    if isinstance(value, (int, float)):
                        page_fault_count = int(value)
                        break
                row["page_fault_count"] = page_fault_count
                if page_fault_count is not None:
                    self._page_fault_telemetry_seen = True
                return row
        except Exception:
            return None

    def _resource_snapshot(self) -> dict[str, Any]:
        latest: dict[str, Any] = {}
        try:
            import resource_monitor

            latest = resource_monitor.latest_resource_sample()
        except Exception:
            latest = {}

        system_available_ram_mb = None
        system_cpu_pct = latest.get("system_cpu_pct")
        try:
            import psutil  # type: ignore

            system_available_ram_mb = round(float(psutil.virtual_memory().available) / 1024.0 / 1024.0, 3)
            system_cpu_pct = float(psutil.cpu_percent(interval=None))
        except Exception:
            pass

        process_rows: dict[str, dict[str, Any]] = {}
        for item in latest.get("processes") or []:
            role = str(item.get("role") or "")
            pid = int(item.get("pid") or 0)
            if role in {"overlay", "game"} or (
                role == "translation_csanmt"
                and self.csanmt_worker_pid > 0
                and pid == self.csanmt_worker_pid
            ):
                copied = dict(item)
                if "ram_mb" in copied and "rss_mb" not in copied:
                    copied["rss_mb"] = copied.pop("ram_mb")
                process_rows[role] = copied

        overlay = self._direct_process_info(self.overlay_pid, "overlay")
        if overlay is not None:
            process_rows["overlay"] = overlay
        if self.csanmt_control_pid:
            control = self._direct_process_info(
                self.csanmt_control_pid,
                "translation_csanmt_control",
            )
            if control is not None:
                process_rows["translation_csanmt_control"] = control
        if self.csanmt_worker_pid:
            worker = self._direct_process_info(
                self.csanmt_worker_pid,
                "translation_csanmt",
            )
            if worker is not None:
                process_rows["translation_csanmt"] = worker

        if self.game_pid:
            direct_game = self._direct_process_info(self.game_pid, "game")
            if direct_game is not None:
                process_rows["game"] = direct_game

        game = process_rows.get("game")
        if game is not None:
            direct_game = self._direct_process_info(int(game.get("pid") or 0), "game")
            if direct_game is not None:
                process_rows["game"] = direct_game

        gpu_processes = latest.get("nvidia_processes") or []
        if game is not None:
            game_gpu = next(
                (
                    row
                    for row in gpu_processes
                    if int(row.get("pid") or 0) == int(game.get("pid") or 0)
                ),
                None,
            )
            if game_gpu:
                process_rows["game"]["gpu_vram_mb"] = game_gpu.get("vram_mb")

        return {
            "system_cpu_pct": system_cpu_pct,
            "system_available_ram_mb": system_available_ram_mb,
            "processes": process_rows,
            "gpu": latest.get("nvidia_gpu") or {"available": False},
            "gpu_processes": gpu_processes,
            "project_drive_disk_telemetry": {
                "available": False,
                "reason": "reliable_logical_drive_to_physical_disk_mapping_not_available",
            },
        }

    def _resource_loop(self) -> None:
        while not self._resource_stop.is_set():
            try:
                self.emit("RESOURCE_SAMPLE", resource=self._resource_snapshot())
            except Exception:
                pass
            self._resource_stop.wait(self.sample_interval_sec)

    def _event_elapsed(self, name: str) -> float | None:
        row = self._first_event.get(name)
        return float(row["elapsed_ms"]) if row and row.get("elapsed_ms") is not None else None

    @staticmethod
    def _delta(end: float | None, start: float | None) -> float | None:
        if end is None or start is None:
            return None
        return round(max(0.0, end - start), 3)

    def _component_summary(self, component: str) -> dict[str, Any]:
        if component == "csanmt":
            requested = self._event_elapsed("CSANMT_START_REQUESTED")
            spawn_begin = self._event_elapsed("CSANMT_PROCESS_SPAWN_BEGIN")
            started = self._event_elapsed("CSANMT_CONTROL_PROCESS_STARTED")
            if started is None:
                started = self._event_elapsed("CSANMT_PROCESS_STARTED")
            import_begin = self._event_elapsed("CSANMT_HEAVY_IMPORT_BEGIN")
            import_end = self._event_elapsed("CSANMT_HEAVY_IMPORT_END")
            load_started = self._event_elapsed("CSANMT_MODEL_LOAD_BEGIN")
            if load_started is None:
                load_started = self._event_elapsed("CSANMT_MODEL_LOAD_STARTED")
            model_ready = self._event_elapsed("CSANMT_MODEL_LOAD_END")
            if model_ready is None:
                model_ready = self._event_elapsed("CSANMT_MODEL_READY")
            warmup_started = self._event_elapsed("CSANMT_WARMUP_BEGIN")
            if warmup_started is None:
                warmup_started = self._event_elapsed("CSANMT_WARMUP_STARTED")
            warmup_end = self._event_elapsed("CSANMT_WARMUP_END")
            ready = self._event_elapsed("CSANMT_READY")
            return {
                "requested_ms": requested,
                "process_spawn_begin_ms": spawn_begin,
                "process_started_ms": started,
                "heavy_import_begin_ms": import_begin,
                "heavy_import_end_ms": import_end,
                "model_load_started_ms": load_started,
                "model_ready_ms": model_ready,
                "warmup_started_ms": warmup_started,
                "warmup_end_ms": warmup_end,
                "ready_ms": ready,
                "spawn_ms": self._delta(started, spawn_begin or requested),
                "worker_bootstrap_ms": self._delta(import_begin, started),
                "import_ms": self._delta(import_end, import_begin),
                "load_ms": self._delta(model_ready, load_started),
                "warmup_ms": self._delta(warmup_end or ready, warmup_started),
                "total_active_startup_ms": self._delta(ready, requested),
                "deferred_wait_ms": round(self._deferred_wait_ms.get(component, 0.0), 3),
            }
        requested = self._event_elapsed("PADDLE_PREPARE_REQUESTED")
        started = self._event_elapsed("PADDLE_PREPARE_STARTED")
        import_begin = self._event_elapsed("PADDLE_IMPORT_BEGIN")
        import_end = self._event_elapsed("PADDLE_IMPORT_END")
        create_begin = self._event_elapsed("PADDLE_BACKEND_CREATE_BEGIN")
        create_end = self._event_elapsed("PADDLE_BACKEND_CREATE_END")
        load_started = self._event_elapsed("PADDLE_MODEL_LOAD_BEGIN")
        if load_started is None:
            load_started = self._event_elapsed("PADDLE_MODEL_LOAD_STARTED")
        model_ready = self._event_elapsed("PADDLE_MODEL_LOAD_END")
        if model_ready is None:
            model_ready = self._event_elapsed("PADDLE_MODEL_READY")
        warmup_started = self._event_elapsed("PADDLE_WARMUP_BEGIN")
        if warmup_started is None:
            warmup_started = self._event_elapsed("PADDLE_WARMUP_STARTED")
        warmup_end = self._event_elapsed("PADDLE_WARMUP_END")
        ready = self._event_elapsed("PADDLE_READY")
        return {
            "requested_ms": requested,
            "prepare_started_ms": started,
            "import_begin_ms": import_begin,
            "import_end_ms": import_end,
            "backend_create_begin_ms": create_begin,
            "backend_create_end_ms": create_end,
            "model_load_started_ms": load_started,
            "model_ready_ms": model_ready,
            "warmup_started_ms": warmup_started,
            "warmup_end_ms": warmup_end,
            "ready_ms": ready,
            "start_dispatch_ms": self._delta(started, requested),
            "import_ms": self._delta(import_end, import_begin),
            "backend_create_ms": self._delta(create_end, create_begin),
            "model_load_ms": self._delta(model_ready, load_started),
            "warmup_ms": self._delta(warmup_end or ready, warmup_started),
            "total_active_startup_ms": self._delta(ready, requested),
            "deferred_wait_ms": round(self._deferred_wait_ms.get(component, 0.0), 3),
        }

    def _resource_summary(self) -> dict[str, Any]:
        resources = [row.get("resource") or {} for row in self._resource_samples]
        result: dict[str, Any] = {
            "sample_count": len(resources),
            "system_cpu_pct": _metric_summary(
                item.get("system_cpu_pct") for item in resources
            ),
            "system_available_ram_mb_min": None,
            "processes": {},
            "gpu": {},
        }
        available = [
            float(item["system_available_ram_mb"])
            for item in resources
            if item.get("system_available_ram_mb") is not None
        ]
        if available:
            result["system_available_ram_mb_min"] = round(min(available), 3)

        for role in ("overlay", "translation_csanmt_control", "translation_csanmt", "game"):
            rows = [
                (item.get("processes") or {}).get(role)
                for item in resources
                if (item.get("processes") or {}).get(role)
            ]
            cpu_values = [row.get("cpu_pct") for row in rows if row.get("cpu_pct") is not None]
            rss_values = [row.get("rss_mb") for row in rows if row.get("rss_mb") is not None]
            thread_values = [row.get("threads") for row in rows if row.get("threads") is not None]
            reads = [row.get("read_bytes") for row in rows if row.get("read_bytes") is not None]
            writes = [row.get("write_bytes") for row in rows if row.get("write_bytes") is not None]
            read_counts = [row.get("read_count") for row in rows if row.get("read_count") is not None]
            write_counts = [row.get("write_count") for row in rows if row.get("write_count") is not None]
            page_faults = [
                row.get("page_fault_count")
                for row in rows
                if row.get("page_fault_count") is not None
            ]
            result["processes"][role] = {
                "sample_count": len(rows),
                "sampled_pids": sorted(
                    {int(row.get("pid") or 0) for row in rows if int(row.get("pid") or 0) > 0}
                ),
                "cpu_pct": _metric_summary(cpu_values),
                "peak_rss_mb": round(max(rss_values), 3) if rss_values else None,
                "peak_threads": int(max(thread_values)) if thread_values else None,
                "read_bytes_delta": int(max(reads) - min(reads)) if reads else None,
                "write_bytes_delta": int(max(writes) - min(writes)) if writes else None,
                "read_count_delta": int(max(read_counts) - min(read_counts)) if read_counts else None,
                "write_count_delta": int(max(write_counts) - min(write_counts)) if write_counts else None,
                "page_fault_delta": int(max(page_faults) - min(page_faults)) if page_faults else None,
            }

        gpu_rows = [item.get("gpu") or {} for item in resources]
        for key in ("gpu_util_pct", "gpu_mem_util_pct", "vram_used_mb", "gpu_temp_c", "gpu_power_w"):
            values = [row.get(key) for row in gpu_rows if isinstance(row.get(key), (int, float))]
            result["gpu"][key] = _metric_summary(values)
        return result

    def _phase_resource_summary(
        self,
        start_event: str,
        end_event: str,
        process_role: str,
    ) -> dict[str, Any]:
        start_ms = self._event_elapsed(start_event)
        end_ms = self._event_elapsed(end_event)
        if start_ms is None or end_ms is None:
            return {
                "start_event": start_event,
                "end_event": end_event,
                "wall_ms": None,
                "sample_count": 0,
            }
        samples = [
            row
            for row in self._resource_samples
            if start_ms <= float(row.get("elapsed_ms") or -1.0) <= end_ms
        ]
        process_rows = []
        system_cpu = []
        for sample in samples:
            resource = sample.get("resource") or {}
            if isinstance(resource.get("system_cpu_pct"), (int, float)):
                system_cpu.append(float(resource["system_cpu_pct"]))
            processes = resource.get("processes") or {}
            row = processes.get(process_role)
            if row is None and process_role == "translation_csanmt":
                row = processes.get("translation_csanmt_control")
            if row:
                process_rows.append(row)

        def metric_delta(name: str) -> int | float | None:
            values = [row.get(name) for row in process_rows if row.get(name) is not None]
            if len(values) < 2:
                return None
            return round(float(values[-1]) - float(values[0]), 3)

        cpu_values = [
            float(row["cpu_pct"])
            for row in process_rows
            if isinstance(row.get("cpu_pct"), (int, float))
        ]
        return {
            "start_event": start_event,
            "end_event": end_event,
            "wall_ms": self._delta(end_ms, start_ms),
            "sample_count": len(samples),
            "process_role": process_role,
            "process_sample_count": len(process_rows),
            "process_cpu_pct": _metric_summary(cpu_values),
            "system_cpu_pct": _metric_summary(system_cpu),
            "rss_delta_mb": metric_delta("rss_mb"),
            "read_bytes_delta": metric_delta("read_bytes"),
            "write_bytes_delta": metric_delta("write_bytes"),
            "read_count_delta": metric_delta("read_count"),
            "write_count_delta": metric_delta("write_count"),
            "page_fault_delta": metric_delta("page_fault_count"),
        }

    def _phase_analysis(self) -> dict[str, Any]:
        definitions = {
            "csanmt_worker_bootstrap": (
                "CSANMT_PROCESS_SPAWN_BEGIN",
                "CSANMT_HEAVY_IMPORT_BEGIN",
                "translation_csanmt",
            ),
            "csanmt_imports": (
                "CSANMT_HEAVY_IMPORT_BEGIN",
                "CSANMT_HEAVY_IMPORT_END",
                "translation_csanmt",
            ),
            "csanmt_model_load": (
                "CSANMT_MODEL_LOAD_BEGIN",
                "CSANMT_MODEL_LOAD_END",
                "translation_csanmt",
            ),
            "csanmt_warmup": (
                "CSANMT_WARMUP_BEGIN",
                "CSANMT_WARMUP_END",
                "translation_csanmt",
            ),
            "paddle_imports": (
                "PADDLE_IMPORT_BEGIN",
                "PADDLE_IMPORT_END",
                "overlay",
            ),
            "paddle_backend_model_create_load": (
                "PADDLE_BACKEND_CREATE_BEGIN",
                "PADDLE_BACKEND_CREATE_END",
                "overlay",
            ),
            "paddle_warmup": (
                "PADDLE_WARMUP_BEGIN",
                "PADDLE_WARMUP_END",
                "overlay",
            ),
        }
        return {
            name: self._phase_resource_summary(*definition)
            for name, definition in definitions.items()
        }

    def _first_event_detail(self, event: str, key: str) -> Any:
        row = self._first_event.get(event) or {}
        return row.get(key)

    def build_summary(self, reason: str) -> dict[str, Any]:
        paddle_ready = self._event_elapsed("PADDLE_READY")
        csanmt_ready = self._event_elapsed("CSANMT_READY")
        both_ready = self._event_elapsed("BOTH_COMPONENTS_READY")
        ui_hint = self._event_elapsed("UI_TRANSLATION_READY_HINT_SHOWN")
        live_enabled = self._event_elapsed("LIVE_HOTKEY_ENABLED")
        ready_values = [value for value in (paddle_ready, csanmt_ready) if value is not None]
        import_profile_path = self._first_event_detail(
            "CSANMT_IMPORT_PROFILE_CONFIGURED",
            "import_profile_path",
        )
        runtime_modules = self._first_event_detail(
            "CSANMT_RUNTIME_MODULES",
            "runtime_modules",
        )
        return {
            "schema_version": 2 if self.coldstart else 1,
            "created_at": _utc_now(),
            "mode": self.mode,
            "coldstart_diagnostics": self.coldstart,
            "coldstart_diagnostics_enabled": self.coldstart,
            "coldstart_diagnostics_requested": self.coldstart_requested,
            "importtime_enabled": self.importtime_enabled,
            "sample_interval_ms": int(self.sample_interval_sec * 1000.0),
            "sampling_interval_ms": int(self.sample_interval_sec * 1000.0),
            "process_start": datetime.fromtimestamp(self.process_wall_time, tz=timezone.utc).isoformat(),
            "overlay_pid": self.overlay_pid,
            "game_pid": self.game_pid or None,
            "csanmt_pid": (self.csanmt_worker_pid or self.csanmt_control_pid) or None,
            "csanmt_control_pid": self.csanmt_control_pid or None,
            "csanmt_worker_pid": self.csanmt_worker_pid or None,
            "log_path": str(self.log_path),
            "result_path": str(self.result_path),
            "metadata": _safe_value(self.metadata),
            "shutdown_reason": str(reason or ""),
            "paddle_ready_ms": paddle_ready,
            "csanmt_ready_ms": csanmt_ready,
            "first_component_ready_ms": min(ready_values) if ready_values else None,
            "both_ready_ms": both_ready,
            "csanmt": self._component_summary("csanmt"),
            "paddle": self._component_summary("paddle"),
            "resource": self._resource_summary(),
            "phase_analysis": self._phase_analysis(),
            "import_profile": parse_python_importtime(import_profile_path)
            if import_profile_path
            else {"available": False, "path": ""},
            "runtime_modules": runtime_modules or {},
            "page_fault_telemetry": "available"
            if self._page_fault_telemetry_seen
            else "unavailable",
            "project_drive_disk_telemetry": {
                "available": False,
                "reason": "reliable_logical_drive_to_physical_disk_mapping_not_available",
            },
            "readiness": {
                "ui_hint_ms": ui_hint,
                "live_enabled_ms": live_enabled,
                "actual_both_ready_ms": both_ready,
                "ui_premature_ready_delta_ms": self._delta(both_ready, ui_hint),
                "live_premature_ready_delta_ms": self._delta(both_ready, live_enabled),
            },
            "event_count": len(self._events),
            "both_components_ready": both_ready is not None,
            "c_path_findings": [
                value
                for value in (str(self.log_path), str(self.result_path))
                if value.lower().startswith("c:\\")
            ],
        }

    def finalize(self, reason: str = "process_exit") -> dict[str, Any]:
        with self._lock:
            if self._finalized:
                try:
                    return json.loads(self.result_path.read_text(encoding="utf-8"))
                except Exception:
                    return {}
            self._resource_stop.set()
        if self._resource_thread is not threading.current_thread():
            self._resource_thread.join(timeout=1.5)
        with self._lock:
            summary = self.build_summary(reason)
            session_logging.write_snapshot(self.result_path, summary)
            self._finalized = True
            row = {
                "timestamp": _utc_now(),
                "elapsed_ms": self.elapsed_ms(),
                "event": "DIAGNOSTIC_FINALIZED",
                "startup_mode": self.mode,
                "overlay_pid": self.overlay_pid,
                "csanmt_pid": (self.csanmt_worker_pid or self.csanmt_control_pid) or None,
                "csanmt_control_pid": self.csanmt_control_pid or None,
                "csanmt_worker_pid": self.csanmt_worker_pid or None,
                "result_path": str(self.result_path),
                "reason": str(reason or ""),
            }
            self._append_row(row)
            return summary


_SESSION_LOCK = threading.Lock()
_SESSION: _StartupDiagnosticSession | None = None


def configure_startup_diagnostics(
    *,
    process_started_monotonic: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    global _SESSION
    mode = selected_startup_mode()
    if not mode:
        return False
    with _SESSION_LOCK:
        if _SESSION is None:
            _SESSION = _StartupDiagnosticSession(
                mode,
                process_started_monotonic=process_started_monotonic,
                metadata=metadata,
            )
    return True


def emit_startup_diagnostic(event: str, **details: Any) -> dict[str, Any]:
    session = _SESSION
    if session is None:
        return {}
    try:
        return session.emit(event, **details)
    except Exception:
        return {}


def mark_component_deferred(component: str, **details: Any) -> None:
    session = _SESSION
    if session is not None:
        try:
            session.mark_deferred(component, **details)
        except Exception:
            pass


def mark_component_requested(component: str, **details: Any) -> None:
    session = _SESSION
    if session is not None:
        try:
            session.mark_requested(component, **details)
        except Exception:
            pass


def finalize_startup_diagnostics(reason: str = "process_exit") -> dict[str, Any]:
    session = _SESSION
    if session is None:
        return {}
    try:
        return session.finalize(reason)
    except Exception:
        return {}


def current_diagnostic_paths() -> dict[str, str]:
    session = _SESSION
    if session is None:
        return {}
    return {"jsonl": str(session.log_path), "result": str(session.result_path)}


atexit.register(finalize_startup_diagnostics)


__all__ = [
    "Block3StartupSequence",
    "VALID_STARTUP_MODES",
    "coldstart_diagnostics_enabled",
    "coldstart_diagnostics_requested",
    "coldstart_importtime_enabled",
    "configure_startup_diagnostics",
    "current_diagnostic_paths",
    "diagnostics_enabled",
    "emit_startup_diagnostic",
    "finalize_startup_diagnostics",
    "mark_component_deferred",
    "mark_component_requested",
    "parse_python_importtime",
    "selected_startup_mode",
]
