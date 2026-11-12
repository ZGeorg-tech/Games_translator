"""Session diagnostics only: never owns resources, profiles, TM or IPC.

Environment is established before importing modules with static log paths.
Each Python process has a private output directory; the launcher alone commits
session.json. User mode discards legacy raw sinks, not worker IPC streams.
"""
from __future__ import annotations

import atexit
import codecs
import json
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import uuid
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from functools import wraps

SESSION_ENV = "GAME_TRANSLATOR_LOG_SESSION"
POLICY_ENV = "GAME_TRANSLATOR_LOG_POLICY"
USER_MAX_BYTES = 1024 * 1024
USER_CRITICAL_BYTES = 128 * 1024
USER_MAX_PROCESSES = 32
_TOKEN = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
_lock = threading.RLock()
_joined = set()
_events = set()
_state = {}
_components = {}
_console_decoders = {}
_fault_handle = None


def _best_effort(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (OSError, ValueError, TypeError, KeyError):
            # Diagnostic storage failure must not break a translation/lifecycle.
            return None
    return wrapped

# Values, not just field names, are constrained. Arbitrary reason/error strings
# and nested payloads are deliberately excluded from the User report.
ENUMS = {
    "online_runtime": {"overlay", "dynamic", "authorization"},
    "online_phase": {"ready", "held", "released", "published", "dropped"},
    "online_publish_reason": {"await_fresh_visual", "visual_transition_pending", "visual_stable", "cancelled",
        "generation_mismatch", "visual_epoch_mismatch", "newer_ocr_task_exists", "active_task_mismatch",
        "session_fingerprint_mismatch", "active_source_mismatch", "session_stopping", "engine_not_running",
        "engine_paused", "request_not_current", "visual_transition_confirmed:empty", "visual_transition_confirmed:changed",
        "visual_clear_applied", "newer_ocr_task_created", "superseded_by_newer_result_same_task", "subtitle_sequence_not_current"},
    "online_origin": {"local", "remote", "transport"},
    "online_provider_code": {"InvalidApiKey", "invalid_api_key", "API_KEY_INVALID", "UNAUTHENTICATED",
        "Arrearage", "insufficient_quota", "QuotaExhausted", "FreeQuotaExhausted", "QUOTA_EXCEEDED",
        "BILLING_DISABLED", "DAILY_LIMIT_EXCEEDED", "ModelNotFound", "ModelAccessDenied", "model_not_found",
        "PERMISSION_DENIED", "rate_limit_exceeded", "Throttling.RateQuota", "InvalidWorkspace", "SERVICE_DISABLED",
        "RATE_LIMIT_EXCEEDED", "keyInvalid", "dailyLimitExceeded", "userRateLimitExceeded", "rateLimitExceeded",
        "accessNotConfigured", "401000", "401015", "403000", "403001", "429000", "429001",
        "52001", "52002", "52003", "54000", "54001", "54003", "54004", "54005", "58000", "58001", "58002", "90107",
        "401", "402", "403", "404", "408", "429", "502", "503", "504", "5", "7",
        "authentication", "permission_denied", "payment_required", "provider_overloaded", "provider_unavailable",
        "context_length_exceeded", "max_tokens_exceeded", "token_limit_exceeded", "string_too_long"},
    "online_provider": {"google", "deepl", "qwen_mt", "gemini", "openai", "yandex", "deepseek", "azure",
                        "google_cloud", "baidu", "gigachat", "openrouter"},
    "online_model": {"web", "free", "pro", "qwen-mt-flash", "qwen-mt-plus", "gemini-3.1-flash-lite",
                     "gemini-3.6-flash", "gpt-6-luna", "gpt-6.1-sol", "standard", "advanced", "enterprise",
                     "deepseek-flash", "deepseek-v4-pro", "GigaChat-2", "GigaChat-2-Pro", "nmt"},
    "online_cause": {"certificate_verify_failed", "tls_error", "proxy_error", "connection_error", "redirect",
                     "google_http_error", "translation_not_found", "runtime_error", "timeout", "rate_limit"},
    "online_region": {"ap-southeast-1", "cn-beijing", "us-east-1"},
    "online_status": {"success", "unverified", "invalid_key", "credential_unavailable", "no_model_access",
        "invalid_region_workspace", "unsupported_pair", "unsupported_model", "quota", "rate_limit",
        "network", "timeout", "invalid_response", "cancelled", "busy", "empty", "invalid_configuration",
        "coalesced", "stale_drop", "tm_hit", "tm_miss", "started", "tls_certificate", "credential_missing",
        "forbidden", "not_found", "service_unavailable"},
    "cleanup_phase": {"startup", "shutdown"},
    "block": {"block1", "block2", "block3", "launcher", "overlay"},
    "profile": {"fast", "quality"},
    "source_lang": {"en", "zh", "zh_hant", "ja"},
    "target_lang": {"en", "ru", "uk", "zh", "zh_hant", "ja"},
    "ocr_backend": {"tesseract", "paddle_fast", "paddle_block2_fast", "paddle"},
    "translation_backend": {"auto", "online", "bergamot", "current_base", "block2_router", "block3_router",
                            "translategemma_llama_q6_low_vram", "translategemma_llama_q6_speed"},
    "component": {"bergamot", "block2_router", "block3_router", "csanmt_en2zh_base", "fugumt_en_ja",
                  "block2_bergamot_cjk_en", "milmmt46_q6", "translategemma_llama_q6",
                  "translategemma_llama_q6_low_vram", "translategemma_llama_q6_speed"},
    "hardware_runtime": {"cuda", "vulkan", "cpu", "unavailable"},
    "translation_mode": {"offline", "online"},
    "display_policy": {"follow_source", "fast_subtitles"},
    "background_mode": {"static_panel", "game_background"},
    "chinese_script_mode": {"auto", "simplified", "traditional", "zh", "zh_hant"},
    "status": {"active", "completed", "failed", "startup_failed", "crashed"},
    "reason": {"clean_exit", "shutdown_failed", "shutdown_incomplete", "unexpected_crash",
               "forced_process_kill", "shutdown_timeout_forced_kill", "startup_timeout_forced_kill",
               "session_config_failed", "session_config_exception", "main_missing", "failed_to_start"},
}
NUMBERS = {"pid", "worker_pid", "control_pid", "exit_code", "task_id", "generation", "elapsed_ms", "online_http_status",
           "online_admission_ms", "online_credentials_ms", "online_connect_ms", "online_http_ms", "online_response_read_ms",
           "online_parse_ms", "online_adapter_ms", "online_wait_ms", "online_publish_wait_ms", "online_dynamic_queue_ms",
           "online_timeout_s", "online_google_worker_ms",
           "request_id", "request_count", "prompt_tokens", "completion_tokens", "total_tokens", "billed_characters", "cost_usd", "online_retry_after_s",
           "deleted_count", "retained_count", "freed_bytes",
           "duration_ms", "rss_bytes", "cpu_percent", "threads", "ctx", "max_output_tokens"}
USER_EVENTS = {
    "ONLINE_HTTP_TIMING", "ONLINE_PUBLICATION", "ONLINE_DYNAMIC_QUEUE", "ONLINE_REQUEST_WAIT",
    "ONLINE_TRANSLATION",
    "TEMP_CLEANUP",
    "PROCESS_JOINED", "PROCESS_EXIT", "SESSION_CREATED", "SESSION_FINISHED", "SESSION_STATE",
    "LAUNCH_OVERLAY_CLICKED", "SESSION_CONFIG_READY", "SESSION_CONFIG_FAILED", "SESSION_CONFIG_EXCEPTION",
    "LAUNCHER_OVERLAY_PROCESS_STARTED", "LAUNCHER_OVERLAY_PROCESS_ERROR", "LAUNCHER_OVERLAY_EXIT_CLASSIFIED",
    "LAUNCHER_OVERLAY_READY_MARKER_DETECTED", "LAUNCHER_OVERLAY_SHUTDOWN_TIMEOUT",
    "MAIN_PROCESS_ENTERED", "PROCESS_ENTRY", "PROCESS_SHUTDOWN_BEGIN", "UNHANDLED_EXCEPTION",
    "UNHANDLED_BACKGROUND_EXCEPTION", "BOTH_COMPONENTS_READY", "PADDLE_READY", "PADDLE_WARMUP_BEGIN",
    "PADDLE_WARMUP_END", "CSANMT_WORKER_STARTED", "CSANMT_READY", "CSANMT_MODEL_LOAD_BEGIN",
    "CSANMT_MODEL_LOAD_END", "OCR_PIPELINE_ERROR", "ENGINE_ASYNC_SHUTDOWN_STARTED",
    "ENGINE_ASYNC_SHUTDOWN_COMPLETED", "OVERLAY_STOP_REQUESTED", "OVERLAY_HIDE_REQUESTED",
    "server_process_started", "server_ready", "server_stopped", "worker_start", "ready", "shutdown",
    "output_limit_reached", "runtime_device_confirmed", "runtime_device_mismatch",
    "CSANMT_WORKER_FATAL_ERROR", "FUGU_WORKER_FATAL_ERROR", "FUGU_READY", "FUGU_WORKER_STARTED",
    "block2_route_error", "block2_route_selected", "block2_translation_completed",
    "block3_route_error", "block3_route_selected", "block3_translation_completed",
    "FORCE_REFRESH_REQUESTED", "FORCE_REFRESH_STARTED", "FORCE_REFRESH_REJECTED", "FORCE_REFRESH_SUCCESS",
}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def policy():
    value = os.environ.get(POLICY_ENV, "off").lower()
    return value if value in {"developer", "user"} else "off"


def enabled():
    return policy() != "off"


def raw_enabled():
    return policy() == "developer"


def sanitize(fields):
    clean = {}
    for key, value in fields.items():
        if key in ENUMS and isinstance(value, str) and value in ENUMS[key]:
            clean[key] = value
        elif key in NUMBERS and type(value) in (int, float) and abs(value) < 1e18:
            clean[key] = value
    return clean


def sessions_root(paths=None):
    if paths is None:
        from data_paths import DATA
        paths = DATA
    return paths.path("sessions")


def context(paths=None):
    if not enabled():
        return None
    name = os.environ.get(SESSION_ENV, "")
    if not name:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", name):
        raise ValueError("Invalid diagnostic session ID")
    root = sessions_root(paths)
    result = root / name
    if not result.resolve().is_relative_to(root.resolve()):
        raise ValueError("Diagnostic session escapes DATA_ROOT")
    return result


def log_root(paths, family):
    root = context(paths)
    return root / "processes" / _TOKEN / ("runtime" if family == "logs" else "diagnostics") if root else None


def _atomic(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _identity(pid):
    try:
        import psutil
    except ImportError:
        # CSANMT's isolated package domain need not install a telemetry package.
        # Retention treats an unknown creation time conservatively as still live.
        return {"pid": pid, "created": None}
    try:
        return {"pid": pid, "created": psutil.Process(pid).create_time()}
    except psutil.Error:
        return {"pid": pid, "created": None}


@_best_effort
def register_process(pid=None, role="python"):
    root = context()
    if root is None:
        return
    pid = int(pid or os.getpid())
    key = (str(root), pid)
    with _lock:
        if key in _joined:
            return
        with (nullcontext() if raw_enabled() else _retention_lock(root.parent)):
            if not raw_enabled() and len(list((root / "processes").glob("*/process.json"))) >= USER_MAX_PROCESSES:
                return
            record = _identity(pid)
            record.update({"role": role if raw_enabled() else "process", "joined_at": now()})
            name = _TOKEN if pid == os.getpid() else f"{_TOKEN}-child-{pid}"
            _atomic(root / "processes" / name / "process.json", record)
            _joined.add(key)


def event(name, /, **fields):
    root = context()
    if root is None:
        return
    if not raw_enabled() and name not in USER_EVENTS:
        return
    try:
        with _lock:
            register_process()
            if (str(root), os.getpid()) not in _joined:
                return
            folder = root / "processes" / _TOKEN
            critical = name in {"SESSION_FINISHED", "UNHANDLED_EXCEPTION", "UNHANDLED_BACKGROUND_EXCEPTION",
                                "LAUNCHER_OVERLAY_EXIT_CLASSIFIED", "LAUNCHER_OVERLAY_PROCESS_ERROR"}
            path = folder / ("critical.jsonl" if critical else "events.jsonl")
            # One writer per file, serialized threads. Critical events have a
            # separate budget and deduplication, so a noisy stream cannot eat it.
            if not raw_enabled():
                fields = sanitize(fields)
                limit = USER_CRITICAL_BYTES if critical else USER_MAX_BYTES
                if path.exists() and path.stat().st_size >= limit:
                    return
                signature = (str(root), name, json.dumps(fields, sort_keys=True))
                if critical and signature in _events:
                    return
                if critical:
                    _events.add(signature)
            row = {"time": now(), "event": name, "pid": os.getpid(), **fields}
            line = json.dumps(row, ensure_ascii=False, default=str) + "\n"
            if not raw_enabled() and (path.stat().st_size if path.exists() else 0) + len(line.encode("utf-8")) > limit:
                return
            with path.open("a", encoding="utf-8") as stream:
                stream.write(line)
    except (OSError, ValueError, TypeError):
        pass


def open_log(path, mode="a", **kwargs):
    """Explicit raw diagnostic sink, not a global open() or stdout hook."""
    return open(path if raw_enabled() else os.devnull, mode, **kwargs)


def diagnostic_event(name, /, **fields):
    # Developer already has the original full-format record at this call site.
    if not raw_enabled():
        event(name, **fields)


@_best_effort
def append_console(chunk, *, final=False):
    if not raw_enabled() or context() is None:
        return
    from data_paths import data_path
    key = str(context())
    decoder = _console_decoders.setdefault(key, codecs.getincrementaldecoder("utf-8")("backslashreplace"))
    text = decoder.decode(bytes(chunk), final=final)
    if final:
        _console_decoders.pop(key, None)
    path = data_path("logs", "overlay_stdout_stderr.log")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open_log(path, "a", encoding="utf-8") as stream:
        stream.write(text)


@_best_effort
def exception_record(exc):
    event("UNHANDLED_EXCEPTION")
    if not raw_enabled() or context() is None:
        return
    from data_paths import data_path
    import traceback
    path = data_path("logs", "process_exception.log")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open_log(path, "a", encoding="utf-8") as stream:
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=stream)


class _LauncherConsole:
    def __init__(self, stream):
        self.stream = stream

    def write(self, text):
        result = self.stream.write(text)
        self._record(text)
        return result

    @_best_effort
    def _record(self, text):
        if not raw_enabled() or context() is None:
            return
        from data_paths import data_path
        path = data_path("logs", "launcher_console.log")
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock, open_log(path, "a", encoding="utf-8") as stream:
            stream.write(str(text))

    def __getattr__(self, name):
        return getattr(self.stream, name)


@_best_effort
def install_launcher_logging():
    """Python console only; never installed in protocol workers or native pipes."""
    if not raw_enabled():
        return
    global _fault_handle
    import faulthandler
    from data_paths import log_file
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if stream is not None and not isinstance(stream, _LauncherConsole):
            setattr(sys, name, _LauncherConsole(stream))
    _fault_handle = open_log(log_file("launcher_native_crash.log"), "a", encoding="utf-8")
    faulthandler.enable(file=_fault_handle, all_threads=True)


@_best_effort
def stop_launcher_logging():
    global _fault_handle
    if _fault_handle is not None:
        import faulthandler
        faulthandler.disable()
        _fault_handle.close()
        _fault_handle = None
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if isinstance(stream, _LauncherConsole):
            setattr(sys, name, stream.stream)


@_best_effort
def component_event(name, component, fields):
    diagnostic_event(name, **{"component": component, **fields})
    if not any(part in name.lower() for part in ("ready", "process_started", "model_load_end")):
        return
    root = context()
    if root is None:
        return
    register_process()
    if (str(root), os.getpid()) not in _joined:
        return
    with _lock:
        components = _components.setdefault(str(root), set())
        if component in components:
            return
        write = sorted(components | {component})
        if not raw_enabled():
            write = [value for value in write if value in ENUMS["component"]]
        _atomic(root / "processes" / _TOKEN / "components.json", {"actual_backends": write})
        components.add(component)


@_best_effort
def runtime_selected(selection):
    root = context()
    if root is None:
        return
    register_process()
    if (str(root), os.getpid()) not in _joined:
        return
    # Full hardware inventory remains in the Developer source log only.
    runtime = str(selection.get("runtime_id", selection.get("runtime_kind", "")))
    kind = next((value for value in ("cuda", "vulkan") if value in runtime), "unavailable")
    state = {"hardware_runtime": kind}
    _atomic(root / "processes" / _TOKEN / "hardware.json", state)
    event("SESSION_STATE", **state)


def write_snapshot(path, value):
    if raw_enabled():
        _atomic(Path(path), value)


def configuration(cfg):
    t, o = cfg.get("translation", {}), cfg.get("ocr", {})
    modes = {"euro_en_to_ru_uk": "block1", "asian_to_en_ru_uk": "block2", "euro_en_to_asian": "block3"}
    runtime = cfg.get("quality_runtime_selection", {}) or {}
    block = modes.get(t.get("language_mode", cfg.get("language_mode")), "overlay")
    profile = t.get("translation_profile", "fast")
    if block == "block1":
        profile = "quality" if t.get("backend") in {"translategemma_llama_q6_low_vram", "translategemma_llama_q6_speed"} else "fast"
    return sanitize({"block": block,
                     "online_provider": (t.get("online") or {}).get("provider") if t.get("mode") == "online" else None,
                     "online_model": (t.get("online") or {}).get("model") if t.get("mode") == "online" else None,
                     "online_region": (t.get("online") or {}).get("region") if t.get("mode") == "online" else None,
                     "profile": profile, "source_lang": t.get("source_lang", "en"),
                     "target_lang": t.get("target_lang"), "ocr_backend": o.get("engine"),
                     "translation_backend": t.get("backend"),
                     "translation_mode": t.get("mode"), "chinese_script_mode": t.get("chinese_script_mode"),
                     "display_policy": cfg.get("subtitles", {}).get("display_policy"),
                     "background_mode": cfg.get("subtitles", {}).get("background_mode"),
                     "hardware_runtime": next((kind for kind in ("cuda", "vulkan")
                         if kind in str(runtime.get("runtime_id", ""))), None)})


@_best_effort
def initial_configuration(cfg):
    directory = context()
    if directory is None:
        return
    path = directory / "session.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest["owner"]["pid"] == os.getpid():
        manifest["initial"] = configuration(cfg)
        manifest["final"] = manifest["initial"]
        _atomic(path, manifest)
    update_state(cfg)


@_best_effort
def update_state(cfg):
    root = context()
    if root is None:
        return
    register_process()
    if (str(root), os.getpid()) not in _joined:
        return
    state = configuration(cfg)
    with _lock:
        if _state.get(str(root)) == state:
            return
        _atomic(root / "processes" / _TOKEN / "state.json", {"time": now(), **state})
        _state[str(root)] = state
        event("SESSION_STATE", **state)


@contextmanager
def _retention_lock(root):
    import msvcrt
    root.mkdir(parents=True, exist_ok=True)
    with (root / "retention.lock").open("a+b") as handle:
        if handle.seek(0, 2) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def create_session(cfg=None, *, kind="overlay"):
    if not enabled():
        return None
    root = sessions_root()
    initial = configuration(cfg or {})
    if kind == "launcher":
        initial["block"] = "launcher"
    name = datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + "_" + initial["block"] + "_" + initial["profile"] + "_" + uuid.uuid4().hex[:12]
    directory = root / name
    with _retention_lock(root):
        directory.mkdir()
        _atomic(directory / "session.json", {"schema": 1, "session_id": name, "created_at": now(),
                "policy": policy(), "kind": kind, "owner": _identity(os.getpid()),
                "status": "active", "initial": initial, "final": initial, "ended_at": None,
                "processes": [_identity(os.getpid())], "pids": [os.getpid()], "process_count": 1, "error": None})
    os.environ[SESSION_ENV] = name
    register_process()
    event("SESSION_CREATED", **initial)
    return name


def ensure_session(kind):
    if context() is None:
        create_session(kind=kind)
    register_process()


def child_environment():
    return {POLICY_ENV: policy(), SESSION_ENV: os.environ.get(SESSION_ENV, "") if enabled() else ""}


def _aggregate_processes(observations):
    """One physical identity per entry; retain role/metric-directory provenance."""
    known_created = {}
    for observation in observations:
        if observation.get("created") is not None:
            known_created.setdefault(observation["pid"], set()).add(observation["created"])
    grouped = {}
    for observation in observations:
        created = observation.get("created")
        known = known_created.get(observation["pid"], set())
        # Isolated workers may not have psutil. A single known identity of the
        # same PID supplies its creation time; conflicting identities stay apart.
        if created is None and len(known) == 1:
            created = next(iter(known))
        key = (observation["pid"], created)
        if key not in grouped:
            grouped[key] = {**observation, "created": created, "roles": [], "process_directories": []}
        process = grouped[key]
        role = observation.get("role")
        if role and role not in process["roles"]:
            process["roles"].append(role)
        directory = observation.get("process_directory")
        if directory and directory not in process["process_directories"]:
            process["process_directories"].append(directory)
    return list(grouped.values())


@_best_effort
def finish_session(status="completed", *, reason="clean_exit", exit_code=0):
    directory = context()
    if directory is None:
        return
    path = directory / "session.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest["owner"]["pid"] != os.getpid() or manifest["status"] != "active":
        return
    event("SESSION_FINISHED", status=status, reason=reason, exit_code=exit_code)
    states = []
    processes = []
    components = set()
    hardware = []
    for record in (directory / "processes").glob("*/process.json"):
        processes.append({**json.loads(record.read_text(encoding="utf-8")),
                          "process_directory": record.parent.relative_to(directory).as_posix()})
    for record in (directory / "processes").glob("*/state.json"):
        states.append(json.loads(record.read_text(encoding="utf-8")))
    for record in (directory / "processes").glob("*/components.json"):
        components.update(json.loads(record.read_text(encoding="utf-8"))["actual_backends"])
    for record in (directory / "processes").glob("*/hardware.json"):
        hardware.append(json.loads(record.read_text(encoding="utf-8")))
    observations = processes
    processes = _aggregate_processes(observations)
    manifest.update({"status": status, "ended_at": now(), "processes": processes,
                     "process_observations": observations, "process_count": len(processes),
                     "process_observation_count": len(observations),
                     "pids": sorted({record["pid"] for record in processes}),
                     "final": max(states, key=lambda s: s["time"]) if states else manifest["initial"],
                     "actual_backends": sorted(components), "hardware_runtimes": hardware,
                     "error": None if status == "completed" else sanitize({"reason": reason, "exit_code": exit_code})})
    with _retention_lock(directory.parent):
        _atomic(path, manifest)
    return True


@_best_effort
def retain_sessions():
    """Only explicitly completed User sessions; live processes/links veto deletion."""
    if policy() != "user":
        return []
    import psutil
    root = sessions_root()
    deleted = []
    with _retention_lock(root):
        candidates = []
        for folder in root.iterdir():
            if not folder.is_dir() or folder.is_symlink() or folder.is_junction():
                continue
            try:
                record = json.loads((folder / "session.json").read_text(encoding="utf-8"))
                if record.get("schema") != 1 or record.get("session_id") != folder.name or record.get("policy") != "user":
                    continue
                if record.get("status") == "active" or not record.get("ended_at"):
                    continue
                live = False
                for item in (folder / "processes").glob("*/process.json"):
                    process = json.loads(item.read_text(encoding="utf-8"))
                    if process["pid"] == record["owner"]["pid"] and record.get("kind") == "overlay":
                        continue
                    try:
                        created = psutil.Process(process["pid"]).create_time()
                        if process["created"] is None or created == process["created"]:
                            live = True
                    except psutil.NoSuchProcess:
                        pass
                candidates.append((record["ended_at"], folder, live))
            except (OSError, ValueError, KeyError, psutil.Error):
                continue
        candidates.sort(reverse=True)
        for _, folder, live in candidates[3:]:
            if live or any(p.is_symlink() or p.is_junction() for p in folder.rglob("*")):
                continue
            shutil.rmtree(folder)
            deleted.append(folder.name)
    return deleted


def user_report(directory):
    """Local report assembled only from allowlisted records, even for Dev input."""
    lines = ["Games_translator: диагностика сессии"]
    for path in sorted(Path(directory).glob("processes/*/*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("event") in USER_EVENTS:
                stamp = row.get("time", "")
                try:
                    stamp = datetime.fromisoformat(stamp).isoformat()
                except (ValueError, TypeError):
                    stamp = ""
                lines.append(json.dumps({"time": stamp, "event": row["event"], **sanitize(row)}, ensure_ascii=False))
    return "\n".join(lines)


def _exit():
    event("PROCESS_EXIT")


def process_result(exit_code):
    event("PROCESS_EXIT", exit_code=exit_code)
    # No-op in inherited workers: only the owning launcher/standalone entry
    # may finalize a manifest, never a CSANMT/Fugu child.
    finished = finish_session("completed" if exit_code == 0 else "failed",
                              reason="clean_exit" if exit_code == 0 else "unexpected_crash", exit_code=exit_code)
    if finished:
        retain_sessions()


atexit.register(_exit)
