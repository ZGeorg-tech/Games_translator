"""
Лёгкий монитор ресурсов для Games_translator.

Назначение:
- писать нагрузку наших тяжёлых процессов отдельно от OCR/LLM pipeline;
- не блокировать UI и перевод;
- работать даже если psutil или nvidia-smi недоступны.
"""

from __future__ import annotations

import json
import os
import ctypes
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None  # type: ignore


_MONITOR_STARTED = False
_MONITOR_LOCK = threading.Lock()
_LATEST_SAMPLE: Dict[str, Any] = {}
_REGISTERED_PROCESSES: Dict[int, Dict[str, Any]] = {}


def latest_resource_sample() -> Dict[str, Any]:
    """Return the latest sample from the existing monitor thread."""
    with _MONITOR_LOCK:
        return json.loads(json.dumps(_LATEST_SAMPLE)) if _LATEST_SAMPLE else {}


def register_resource_process(pid: int, role: str, name: str = "") -> bool:
    """Register one exact child PID without tracking every process with the same executable name."""
    from session_logging import raw_enabled
    if psutil is None or not raw_enabled():
        return False
    try:
        process = psutil.Process(int(pid))
        process.cpu_percent(interval=None)
        if not process.is_running():
            return False
        with _MONITOR_LOCK:
            _REGISTERED_PROCESSES[int(pid)] = {
                "process": process,
                "role": str(role or "external"),
                "registered_name": str(name or ""),
            }
        return True
    except Exception:
        return False


def unregister_resource_process(pid: int) -> None:
    with _MONITOR_LOCK:
        _REGISTERED_PROCESSES.pop(int(pid), None)


def _log_path(app_dir: str) -> str:
    from data_paths import log_file
    return log_file("resource_usage.log", app_dir)


def _write_event(app_dir: str, event: str, payload: Dict[str, Any]) -> None:
    from session_logging import open_log, diagnostic_event
    diagnostic_event(event, **payload)
    try:
        row = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "event": event,
            **payload,
        }
        with open_log(_log_path(app_dir), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _find_nvidia_smi() -> str:
    exe = shutil.which("nvidia-smi")
    if exe:
        return exe
    candidates = [
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "NVIDIA Corporation", "NVSMI", "nvidia-smi.exe"),
        os.path.join(os.environ.get("ProgramW6432", r"C:\Program Files"), "NVIDIA Corporation", "NVSMI", "nvidia-smi.exe"),
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return ""


def _run_command(cmd: List[str], timeout: float = 1.5) -> str:
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            return ""
        return (result.stdout or "").strip()
    except Exception:
        return ""


def _read_total_cpu_fallback() -> Optional[float]:
    raw = _run_command([
        "powershell",
        "-NoProfile",
        "-Command",
        "(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average"
    ], timeout=2.0)
    if not raw:
        return None
    try:
        return float(raw.splitlines()[0].strip().replace(",", "."))
    except Exception:
        return None


def _read_nvidia_gpu(nvidia_smi: str) -> Dict[str, Any]:
    if not nvidia_smi:
        return {"available": False}

    raw = _run_command([
        nvidia_smi,
        "--query-gpu=utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,power.draw",
        "--format=csv,noheader,nounits",
    ])
    if not raw:
        return {"available": False, "error": "nvidia_smi_no_output"}

    line = raw.splitlines()[0]
    parts = [p.strip() for p in line.split(",")]
    data: Dict[str, Any] = {"available": True}
    keys = ["gpu_util_pct", "gpu_mem_util_pct", "vram_used_mb", "vram_total_mb", "gpu_temp_c", "gpu_power_w"]
    for key, value in zip(keys, parts):
        try:
            if key == "gpu_power_w":
                data[key] = float(value)
            else:
                data[key] = int(float(value))
        except Exception:
            data[key] = value
    return data


def _read_nvidia_processes(nvidia_smi: str) -> List[Dict[str, Any]]:
    if not nvidia_smi:
        return []
    raw = _run_command([
        nvidia_smi,
        "--query-compute-apps=pid,process_name,used_memory",
        "--format=csv,noheader,nounits",
    ])
    rows: List[Dict[str, Any]] = []
    if not raw:
        return rows
    for line in raw.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            pid_value: Any = int(parts[0])
        except Exception:
            pid_value = parts[0]
        try:
            mem_value: Any = int(float(parts[2]))
        except Exception:
            mem_value = parts[2]
        rows.append({"pid": pid_value, "name": parts[1], "vram_mb": mem_value})
    return rows


def _safe_process_info(proc: Any) -> Optional[Dict[str, Any]]:
    if psutil is None:
        return None
    try:
        with proc.oneshot():
            return {
                "pid": int(proc.pid),
                "name": str(proc.name()),
                "cpu_pct": float(proc.cpu_percent(interval=None)),
                "ram_mb": round(float(proc.memory_info().rss) / 1024.0 / 1024.0, 1),
                "threads": int(proc.num_threads()),
                "status": str(proc.status()),
            }
    except Exception:
        return None


def _registered_process_snapshot() -> List[Dict[str, Any]]:
    if psutil is None:
        return []
    with _MONITOR_LOCK:
        registered = list(_REGISTERED_PROCESSES.items())
    rows: List[Dict[str, Any]] = []
    stale_pids: List[int] = []
    for pid, entry in registered:
        info = _safe_process_info(entry.get("process"))
        if info is None:
            stale_pids.append(pid)
            continue
        info["role"] = str(entry.get("role") or "external")
        registered_name = str(entry.get("registered_name") or "")
        if registered_name:
            info["registered_name"] = registered_name
        rows.append(info)
    if stale_pids:
        with _MONITOR_LOCK:
            for pid in stale_pids:
                _REGISTERED_PROCESSES.pop(pid, None)
    return rows


def _find_processes(names: List[str]) -> List[Any]:
    if psutil is None:
        return []
    wanted = {n.lower() for n in names}
    found = []
    try:
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                name = str(proc.info.get("name") or "").lower()
                if name in wanted:
                    found.append(proc)
            except Exception:
                continue
    except Exception:
        return []
    return found


def _process_from_hwnd(hwnd: Any) -> Any:
    if psutil is None:
        return None
    try:
        pid = ctypes.c_ulong(0)
        ctypes.windll.user32.GetWindowThreadProcessId(int(hwnd or 0), ctypes.byref(pid))
        if pid.value:
            proc = psutil.Process(int(pid.value))
            proc.cpu_percent(interval=None)
            return proc
    except Exception:
        pass
    return None


def _monitor_loop(app_dir: str, settings: Dict[str, Any]) -> None:
    interval = float(settings.get("interval_sec", 1.0) or 1.0)
    interval = max(0.5, min(10.0, interval))
    names = settings.get("process_names") or ["llama-server.exe"]
    if not isinstance(names, list):
        names = ["llama-server.exe"]
    names = [str(x) for x in names if str(x).strip()]
    include_self = bool(settings.get("include_self", True))
    enable_nvidia = bool(settings.get("nvidia", True))
    nvidia_smi = _find_nvidia_smi() if enable_nvidia else ""

    _write_event(app_dir, "RESOURCE_MONITOR_STARTED", {
        "psutil": bool(psutil is not None),
        "nvidia_smi": nvidia_smi or "",
        "interval_sec": interval,
        "process_names": names,
    })

    own_proc = None
    game_proc = _process_from_hwnd(settings.get("game_hwnd"))
    if psutil is not None and include_self:
        try:
            own_proc = psutil.Process(os.getpid())
            own_proc.cpu_percent(interval=None)
        except Exception:
            own_proc = None

    tracked = _find_processes(names)
    for proc in tracked:
        try:
            proc.cpu_percent(interval=None)
        except Exception:
            pass

    while True:
        time.sleep(interval)
        processes: List[Dict[str, Any]] = []
        seen_pids = set()

        if psutil is not None:
            if own_proc is not None:
                info = _safe_process_info(own_proc)
                if info:
                    info["role"] = "overlay"
                    processes.append(info)
                    seen_pids.add(int(info["pid"]))
            if game_proc is not None:
                info = _safe_process_info(game_proc)
                if info and int(info["pid"]) not in seen_pids:
                    info["role"] = "game"
                    processes.append(info)
                    seen_pids.add(int(info["pid"]))

            tracked = _find_processes(names)
            for proc in tracked:
                info = _safe_process_info(proc)
                if info and int(info["pid"]) not in seen_pids:
                    info["role"] = "external"
                    processes.append(info)
                    seen_pids.add(int(info["pid"]))
            for info in _registered_process_snapshot():
                if int(info["pid"]) not in seen_pids:
                    processes.append(info)
                    seen_pids.add(int(info["pid"]))
        else:
            processes.append({"role": "monitor", "error": "psutil_not_installed"})

        payload = {
            "timestamp": time.time(),
            "system_cpu_pct": float(psutil.cpu_percent(interval=None)) if psutil is not None else None,
            "total_cpu_pct_fallback": _read_total_cpu_fallback() if psutil is None else None,
            "processes": processes,
            "nvidia_gpu": _read_nvidia_gpu(nvidia_smi) if enable_nvidia else {"available": False},
            "nvidia_processes": _read_nvidia_processes(nvidia_smi) if enable_nvidia else [],
        }
        with _MONITOR_LOCK:
            _LATEST_SAMPLE.clear()
            _LATEST_SAMPLE.update(payload)
        _write_event(app_dir, "RESOURCE_SAMPLE", payload)


def start_resource_monitor(app_dir: str, session_config: Optional[Dict[str, Any]] = None) -> None:
    """Запускает daemon-monitor один раз за процесс overlay."""
    global _MONITOR_STARTED
    from session_logging import raw_enabled
    if not raw_enabled():
        return
    try:
        cfg = (session_config or {}).get("resource_monitor", {}) if isinstance(session_config, dict) else {}
        if not isinstance(cfg, dict):
            cfg = {}
        cfg = dict(cfg)
        if isinstance(session_config, dict):
            cfg["game_hwnd"] = int(session_config.get("game_hwnd") or 0)
        enabled = bool(cfg.get("enabled", True))
        if not enabled:
            return
        with _MONITOR_LOCK:
            if _MONITOR_STARTED:
                return
            _MONITOR_STARTED = True
        thread = threading.Thread(target=_monitor_loop, args=(app_dir, cfg), name="resource_monitor", daemon=True)
        thread.start()
    except Exception:
        try:
            _write_event(app_dir, "RESOURCE_MONITOR_START_ERROR", {"error": "failed_to_start"})
        except Exception:
            pass
