"""Production adapter for app-local TranslateGemma Q6 through llama.cpp."""

from __future__ import annotations

import atexit
import ctypes
import http.client
import json
import os
import re
import socket
import subprocess
import threading
import time
import uuid
from collections import deque
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


from resource_registry import APP_ROOT as APP_DIR, RESOURCES, resource_path

RUNTIME_DIR = resource_path("llama.runtime")
LLAMA_SERVER_PATH = RUNTIME_DIR / "llama-server.exe"
MODEL_PATH = resource_path("translategemma.q6")
HF_CHAT_TEMPLATE_PATH = resource_path("translategemma.chat_template")
from data_paths import DATA, data_path
import session_logging
CACHE_ROOT = data_path("translategemma.cache")
LOG_ROOT = data_path("logs", "translation")
LOG_DIR = LOG_ROOT / "translategemma_llama_q6"
SPEED_LOG_DIR = LOG_ROOT / "translategemma_llama_q6_speed"

RUNTIME_VERSION = "b10057"
QUANT = "Q6_K"
GPU_LAYERS = 22
CTX_SIZE = 512
THREADS = 4
PARALLEL = 1
BATCH_SIZE = 512
UBATCH_SIZE = 512
MAX_TOKENS = 256
Q6_CACHE_RAM_MIB = 0
STARTUP_TIMEOUT_SEC = 45.0
TRANSLATION_TIMEOUT_SEC = 120.0
CONTROL_TIMEOUT_SEC = 5.0
SHUTDOWN_TIMEOUT_SEC = 12.0
PORT_START_ATTEMPTS = 2
SERVER_LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_FILES_TO_KEEP = 8
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000

REQUIRED_RUNTIME_FILES = (
    "llama-server.exe",
    "llama-server-impl.dll",
    "llama-common.dll",
    "llama.dll",
    "ggml.dll",
    "ggml-base.dll",
    "ggml-cuda.dll",
    "cudart64_13.dll",
    "cublas64_13.dll",
    "cublasLt64_13.dll",
)


class TranslateGemmaLlamaBackendError(RuntimeError):
    """Controlled llama.cpp backend failure with a stable localization code."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = str(code or "backend_unavailable")
        self.detail = str(detail or "")
        message = self.code if not self.detail else f"{self.code}: {self.detail}"
        super().__init__(message)


def _relative_to_app(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(APP_DIR.resolve()))
    except Exception:
        return str(path)


def _c_path_findings(paths: dict[str, str]) -> list[str]:
    return [
        f"{name}={value}"
        for name, value in paths.items()
        if str(value).lower().startswith("c:\\") and not Path(value).resolve().is_relative_to(DATA.root)
    ]


def _prompt_template_matches_official_snapshot() -> bool:
    if not HF_CHAT_TEMPLATE_PATH.is_file():
        return False
    try:
        template = HF_CHAT_TEMPLATE_PATH.read_text(encoding="utf-8")
    except OSError:
        return False
    markers = (
        "You are a professional ",
        "Produce only the ",
        "add_generation_prompt",
        "<start_of_turn>model\\n",
    )
    return all(marker in template for marker in markers)


def preflight_translategemma_llama_q6(runtime_id: str | None = None) -> dict[str, Any]:
    """Validate the existing app-local b10057 runtime and Q6 model."""
    runtime = RESOURCES[runtime_id].runtime if runtime_id else None
    runtime_dir = resource_path(runtime_id) if runtime_id else RUNTIME_DIR
    required = runtime.required_files if runtime else REQUIRED_RUNTIME_FILES
    missing_runtime = [str(runtime_dir / name) for name in required if not (runtime_dir / name).is_file()]
    missing_model = [] if MODEL_PATH.is_file() else [str(MODEL_PATH)]
    missing_template = [] if HF_CHAT_TEMPLATE_PATH.is_file() else [str(HF_CHAT_TEMPLATE_PATH)]
    template_matches = _prompt_template_matches_official_snapshot()
    if HF_CHAT_TEMPLATE_PATH.is_file() and not template_matches:
        missing_template.append(f"{HF_CHAT_TEMPLATE_PATH}:unexpected_template")

    error_code = ""
    if str(runtime_dir / "llama-server.exe") in missing_runtime:
        error_code = "llama_server_missing"
    elif missing_runtime:
        error_code = "backend_unavailable" if runtime and runtime.backend != "cuda" else "cuda_runtime_missing"
    elif missing_model:
        error_code = "gguf_missing"
    elif missing_template:
        error_code = "prompt_template_missing"

    missing = missing_runtime + missing_model + missing_template
    return {
        "ok": not missing,
        "error_code": error_code,
        "runtime_version": runtime.build if runtime else RUNTIME_VERSION,
        "runtime_dir": str(runtime_dir),
        "llama_server_path": str(runtime_dir / "llama-server.exe"),
        "model_path": str(MODEL_PATH),
        "model_size": MODEL_PATH.stat().st_size if MODEL_PATH.is_file() else 0,
        "chat_template_path": str(HF_CHAT_TEMPLATE_PATH),
        "template_matches": template_matches,
        "missing_runtime": missing_runtime,
        "missing_model": missing_model,
        "missing_template": missing_template,
        "missing": missing,
    }


def preflight_translategemma_llama_q6_speed() -> dict[str, Any]:
    result = dict(preflight_translategemma_llama_q6())
    result["profile"] = "translategemma_llama_q6_speed"
    result["gpu_layers"] = 35
    return result


def _build_child_env(log_dir: Path = LOG_DIR) -> tuple[dict[str, str], dict[str, str]]:
    paths = DATA.child_environment_paths("llama-cpp")
    for key in ("TEMP", "HOME", "APPDATA", "LOCALAPPDATA"):
        paths[key].mkdir(parents=True, exist_ok=True)

    effective = {name: str(path.resolve()) for name, path in paths.items()}
    effective["PATH"] = str(RUNTIME_DIR.resolve())
    effective["WORKING_DIRECTORY"] = str(APP_DIR.resolve())
    effective["LOG_DIRECTORY"] = str(log_dir.resolve())
    env = os.environ.copy()
    env.update(effective)
    env["PATH"] = effective["PATH"]
    return env, effective


def _find_free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _render_official_hf_prompt(source_text: str, target_lang: str) -> str:
    """Render the text branch of the official TranslateGemma HF chat template."""
    target_names = {"ru": "Russian", "uk": "Ukrainian"}
    target = str(target_lang or "").lower().strip()
    target_name = target_names.get(target)
    if not target_name:
        raise TranslateGemmaLlamaBackendError(
            "unsupported_language_pair", f"en->{target or 'unknown'}"
        )
    text = str(source_text or "").strip()
    return (
        "<bos><start_of_turn>user\n"
        f"You are a professional English (en) to {target_name} ({target}) translator. "
        "Your goal is to accurately convey the meaning and nuances of the original English text "
        f"while adhering to {target_name} grammar, vocabulary, and cultural sensitivities.\n"
        f"Produce only the {target_name} translation, without any additional explanations or "
        f"commentary. Please translate the following English text into {target_name}:\n\n\n"
        f"{text}<end_of_turn>\n<start_of_turn>model\n"
    )


def _get_windows_priority_class(pid: int | None) -> int | None:
    if os.name != "nt" or not pid:
        return None
    process_query_limited_information = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(
        process_query_limited_information, False, int(pid)
    )
    if not handle:
        return None
    try:
        value = ctypes.windll.kernel32.GetPriorityClass(handle)
        return int(value) if value else None
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


_LLAMA_PROFILE_SWITCH_LOCK = threading.RLock()
_ACTIVE_LLAMA_BACKEND: Any | None = None


class TranslateGemmaLlamaQ6Backend:
    name = "translategemma_llama_q6_low_vram"
    memory_key = "translategemma_llama_q6_low_vram_v1"
    display_name = "TranslateGemma Q6 Low-VRAM (llama.cpp, experimental)"
    allow_fallback = False
    supported_pairs = {("en", "ru"), ("en", "uk")}
    supports_request_context = True
    prefers_whole_text = False

    def __init__(
        self,
        startup_timeout_sec: float = STARTUP_TIMEOUT_SEC,
        translation_timeout_sec: float = TRANSLATION_TIMEOUT_SEC,
        *,
        name: str | None = None,
        memory_key: str | None = None,
        display_name: str | None = None,
        gpu_layers: int = GPU_LAYERS,
        log_dir: Path | None = None,
        cache_prompt_enabled: bool = False,
        server_cache_ram_mib: int | None = None,
        prefers_whole_text: bool = False,
        model_path: Path | None = None,
        quant: str | None = None,
        preflight_callback: Any | None = None,
        ctx_size: int = CTX_SIZE,
        max_output_tokens: int = MAX_TOKENS,
        runtime_id: str | None = None,
    ) -> None:
        self.name = str(name or type(self).name)
        self.memory_key = str(memory_key or type(self).memory_key)
        self.display_name = str(display_name or type(self).display_name)
        self.gpu_layers = int(gpu_layers)
        self.log_dir = Path(log_dir or LOG_DIR)
        self.cache_prompt_enabled = bool(cache_prompt_enabled)
        self.server_cache_ram_mib = (
            int(server_cache_ram_mib) if server_cache_ram_mib is not None else None
        )
        self.prefers_whole_text = bool(prefers_whole_text)
        self.model_path = Path(model_path or MODEL_PATH)
        self.quant = str(quant or QUANT)
        self.runtime_id = runtime_id
        self.device_binding: dict[str, Any] | None = None
        self.runtime_dir = resource_path(runtime_id) if runtime_id else RUNTIME_DIR
        self.runtime_definition = RESOURCES[runtime_id].runtime if runtime_id else None
        self.runtime_version = self.runtime_definition.build if self.runtime_definition else RUNTIME_VERSION
        self._vulkan_cache_control = None
        self.preflight_callback = preflight_callback or (
            (lambda: preflight_translategemma_llama_q6(runtime_id)) if runtime_id else preflight_translategemma_llama_q6
        )
        self.ctx_size = max(1, int(ctx_size))
        self.max_output_tokens = max(1, int(max_output_tokens))
        self.startup_timeout_sec = float(startup_timeout_sec)
        self.translation_timeout_sec = float(translation_timeout_sec)
        self._lock = threading.RLock()
        self._start_lock = threading.Lock()
        self._translation_lock = threading.Lock()
        self._restart_lock = threading.Lock()
        self._log_lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._server_log_thread: threading.Thread | None = None
        self._server_log_tail: deque[str] = deque(maxlen=240)
        self._server_facts: dict[str, Any] = {}
        self._state = "not_started"
        self._fatal_error = ""
        self._port: int | None = None
        self._command: list[str] = []
        self._effective_env: dict[str, str] = {}
        self._active_request: dict[str, Any] | None = None
        self._cancelled_request_ids: set[str] = set()
        self._generation = 0
        self._request_seq = 0
        self._process_start_count = 0
        self._translation_requests_sent = 0
        self._automatic_restart_count = 0
        self._restart_for_request_ids: set[str] = set()
        self._ready_ms: float | None = None
        self._last_translation_metrics: dict[str, Any] = {}
        self._vulkan_preparation: dict[str, Any] = {}
        self._session_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        self._adapter_log_path = self.log_dir / f"adapter_{self._session_id}.jsonl"
        self._server_log_path = self.log_dir / f"server_{self._session_id}.log"

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def configure_device_binding(self, binding: dict[str, Any]) -> None:
        if binding.get("status") != "mapped" or binding.get("runtime_id") != self.runtime_id:
            raise TranslateGemmaLlamaBackendError("runtime_device_mapping_failed", str(binding.get("reason")))
        if binding != self.device_binding:
            self.shutdown(reason="session_device_changed")
            self.device_binding = binding

    def _device_mismatch(self) -> dict[str, Any]:
        if self.device_binding is None:
            return {}
        expected = self.device_binding["runtime_device"]
        actual = self._server_facts.get("runtime_devices_used") or []
        correct = (len(actual) == 1 and actual[0]["runtime_device_id"] == expected["runtime_device_id"]
                   and actual[0]["name"] == expected["name"]
                   and (not expected.get("pci_bus_id") or actual[0]["pci_bus_id"] == expected["pci_bus_id"]))
        return {} if correct else {"runtime_device": {"expected": expected, "actual": actual,
                                                       "reason": "launched_runtime_device_mismatch"}}

    def _log(self, event: str, **details: Any) -> None:
        session_logging.component_event(event, self.name, details)
        if not session_logging.raw_enabled():
            return
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            row = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": event,
                "backend": self.name,
                "state": self.state,
                **details,
            }
            with self._log_lock:
                with session_logging.open_log(self._adapter_log_path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            self._rotate_logs("adapter_*.jsonl")
        except Exception:
            pass

    def _rotate_logs(self, pattern: str) -> None:
        # Session retention is the only deletion owner. Never rotate archives,
        # including when a developer probe imports this backend without a session.
        return

    def _record_server_fact(self, line: str) -> None:
        match = re.search(r"using device (\S+) \((.+)\) \(([^)]+)\) -", line)
        if match:
            row = {"runtime_device_id": match[1], "name": match[2], "pci_bus_id": match[3].lower()}
            devices = self._server_facts.setdefault("runtime_devices_used", [])
            if row not in devices:
                devices.append(row)
            if self.device_binding:
                self._log("runtime_device_confirmed" if not self._device_mismatch() else "runtime_device_mismatch",
                          device_binding=self.device_binding, actual_runtime_devices=list(devices))
        if "VK_PCACHE event=load " in line:
            event = dict(re.findall(r"\b([a-z_]+)=([^\s]+)", line.split("VK_PCACHE event=load ", 1)[1]))
            self._server_facts.setdefault("vulkan_cache_load_events", []).append(event)
        if re.search(rf"file type\s+=\s+{re.escape(self.quant)}", line):
            self._server_facts["quant"] = self.quant
        match = re.search(r"offloaded\s+(\d+)/(\d+)\s+layers to GPU", line)
        if match:
            self._server_facts["gpu_layers"] = int(match.group(1))
            self._server_facts["total_layers"] = int(match.group(2))
        for name, pattern in (
            ("ctx", r"n_ctx\s+=\s+(\d+)"),
            ("batch", r"n_batch\s+=\s+(\d+)"),
            ("ubatch", r"n_ubatch\s+=\s+(\d+)"),
            ("slots", r"n_slots\s+=\s+(\d+)"),
        ):
            match = re.search(pattern, line)
            if match:
                self._server_facts[name] = int(match.group(1))
        if "K (f16):" in line and "V (f16):" in line:
            self._server_facts["kv"] = "F16"
        if "model loaded" in line:
            self._server_facts["model_loaded"] = True
        if "listening on http://127.0.0.1:" in line:
            self._server_facts["loopback_listening"] = True

    def _server_log_loop(self, stream: Any) -> None:
        written = 0
        raw = session_logging.raw_enabled()
        unlimited_log = bool(raw and session_logging.context())
        try:
            if raw:
                self.log_dir.mkdir(parents=True, exist_ok=True)
            sink = session_logging.open_log(self._server_log_path, "w", encoding="utf-8", newline="\n") if raw else nullcontext()
            with sink as handle:
                for raw_line in iter(stream.readline, ""):
                    line = str(raw_line or "").rstrip("\r\n")
                    if not line:
                        continue
                    self._server_log_tail.append(line[-3000:])
                    self._record_server_fact(line)
                    if handle is not None and (unlimited_log or written < SERVER_LOG_MAX_BYTES):
                        encoded_len = len((line + "\n").encode("utf-8", errors="replace"))
                        if unlimited_log or written + encoded_len <= SERVER_LOG_MAX_BYTES:
                            handle.write(line + "\n")
                            handle.flush()
                            written += encoded_len
        except Exception as exc:
            self._log("server_log_reader_error", error=f"{type(exc).__name__}: {exc}")
        finally:
            try:
                stream.close()
            except Exception:
                pass
            self._rotate_logs("server_*.log")

    def _process_is_alive(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    def _next_request_id(self) -> str:
        with self._lock:
            self._request_seq += 1
            seq = self._request_seq
        return f"q6-{seq}-{uuid.uuid4().hex[:10]}"

    def _render_translation_prompt(
        self,
        source_text: str,
        source_lang: str,
        target_lang: str,
        request_context: dict[str, Any],
    ) -> str:
        return _render_official_hf_prompt(source_text, target_lang)

    def _clean_translation_output(self, text: str, target_lang: str) -> str:
        return str(text or "").strip()

    def _server_command(self, port: int) -> list[str]:
        command = [
            str(self.runtime_dir / "llama-server.exe" if self.runtime_id else LLAMA_SERVER_PATH),
            "--model",
            str(self.model_path),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--ctx-size",
            str(self.ctx_size),
            "--threads",
            str(THREADS),
            "--parallel",
            str(PARALLEL),
            "--gpu-layers",
            str(self.gpu_layers),
            "--cache-type-k",
            "f16",
            "--cache-type-v",
            "f16",
            "--flash-attn",
            "auto",
            "--offline",
            "--no-webui",
            "--no-jinja",
            "--no-cont-batching",
            "--log-verbosity",
            "4",
        ]
        if self.server_cache_ram_mib is not None:
            command.extend(["--cache-ram", str(self.server_cache_ram_mib)])
        if self.device_binding is not None:
            command.extend(["--device", self.device_binding["runtime_device"]["runtime_device_id"],
                            "--split-mode", "none", "--main-gpu", "0"])
        return command

    def _json_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        timeout: float = CONTROL_TIMEOUT_SEC,
    ) -> Any:
        with self._lock:
            port = self._port
        if not port:
            raise OSError("llama_server_port_unavailable")
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        body = None
        headers: dict[str, str] = {}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            if response.status >= 400:
                raise OSError(f"http_{response.status}:{raw[:500]!r}")
            return json.loads(raw.decode("utf-8")) if raw else {}
        finally:
            connection.close()

    def _health_ready(self) -> bool:
        try:
            payload = self._json_request("GET", "/health", timeout=1.5)
            return isinstance(payload, dict) and payload.get("status") == "ok"
        except Exception:
            return False

    def _facts_match_profile(self) -> tuple[bool, dict[str, Any]]:
        facts = dict(self._server_facts)
        expected = {
            "quant": self.quant,
            "gpu_layers": self.gpu_layers,
            "ctx": self.ctx_size,
            "batch": BATCH_SIZE,
            "ubatch": UBATCH_SIZE,
            "slots": PARALLEL,
            "kv": "F16",
            "model_loaded": True,
            "loopback_listening": True,
        }
        mismatches = {
            name: {"expected": value, "actual": facts.get(name)}
            for name, value in expected.items()
            if facts.get(name) != value
        }
        mismatches.update(self._device_mismatch())
        return not mismatches, mismatches

    def start(self, status_callback: Any | None = None) -> dict[str, Any]:
        global _ACTIVE_LLAMA_BACKEND
        with _LLAMA_PROFILE_SWITCH_LOCK:
            previous = _ACTIVE_LLAMA_BACKEND
            if previous is not None and previous is not self:
                previous.shutdown(reason=f"profile_switch_to:{self.name}")
            _ACTIVE_LLAMA_BACKEND = self
            try:
                if self.runtime_id == "llama.runtime.vulkan.pcache1" and not (
                    self._process_is_alive() and self.state in {"ready", "busy"}
                ):
                    from translation_backends.vulkan_preparation import default_cache_root, preparation_lock
                    lock_started = time.perf_counter()
                    with preparation_lock(default_cache_root(), on_wait=(
                        (lambda: status_callback("waiting")) if status_callback is not None else None
                    )):
                        self._log("vulkan_preparation_lock_acquired", wait_ms=round((time.perf_counter() - lock_started) * 1000, 3))
                        if status_callback is not None:
                            status_callback("loading")
                        return self._start_active_profile(status_callback)
                return self._start_active_profile(status_callback)
            except Exception:
                if _ACTIVE_LLAMA_BACKEND is self and not self._process_is_alive():
                    _ACTIVE_LLAMA_BACKEND = None
                raise

    def _start_active_profile(self, status_callback: Any | None = None) -> dict[str, Any]:
        with self._start_lock:
            with self._lock:
                if self._process_is_alive() and self._state in {"ready", "busy"}:
                    return self.diagnostics()
                old_process = self._process
            if old_process is not None:
                self._stop_process("replace_unusable_process")
            self._vulkan_preparation = {}

            preflight = self.preflight_callback()
            if not preflight["ok"]:
                detail = "; ".join(preflight["missing"][:4])
                error = TranslateGemmaLlamaBackendError(
                    preflight["error_code"] or "backend_unavailable", detail
                )
                with self._lock:
                    self._state = "error"
                    self._fatal_error = str(error)
                self._log("preflight_failed", error_code=error.code, detail=detail)
                raise error

            env, effective_env = _build_child_env(self.log_dir)
            if self.device_binding is not None:
                from runtime_devices import VISIBILITY_ENV
                for key in VISIBILITY_ENV:
                    env.pop(key, None)
                    effective_env.pop(key, None)
            for key in ("GGML_VK_PIPELINE_CACHE_ROOT", "GGML_VK_CACHE_FLUSH_EVENT", "GGML_VK_CACHE_FLUSH_DONE_EVENT"):
                env.pop(key, None)
            if self.runtime_id:
                env["PATH"] = effective_env["PATH"] = str(self.runtime_dir.resolve())
            findings = _c_path_findings(effective_env)
            if findings:
                error = TranslateGemmaLlamaBackendError(
                    "backend_unavailable", "app_local_env_points_to_c:" + "; ".join(findings)
                )
                with self._lock:
                    self._state = "error"
                    self._fatal_error = str(error)
                raise error

            last_error = ""
            for attempt in range(1, PORT_START_ATTEMPTS + 1):
                for key in ("GGML_VK_PIPELINE_CACHE_ROOT", "GGML_VK_CACHE_FLUSH_EVENT", "GGML_VK_CACHE_FLUSH_DONE_EVENT"):
                    env.pop(key, None)
                    effective_env.pop(key, None)
                if self.runtime_definition and "persistent_pipeline_cache" in self.runtime_definition.declared_capabilities:
                    try:
                        from translation_backends.vulkan_cache_control import VulkanCacheControl
                        self._vulkan_cache_control = VulkanCacheControl(self.runtime_version)
                        env.update(self._vulkan_cache_control.environment())
                        effective_env["GGML_VK_PIPELINE_CACHE_ROOT"] = str(self._vulkan_cache_control.root)
                    except Exception as exc:
                        self._log("vulkan_cache_disabled", reason=f"{type(exc).__name__}:{exc}")
                        if self.runtime_id == "llama.runtime.vulkan.pcache1":
                            raise TranslateGemmaLlamaBackendError("vulkan_preparation_failed", str(exc)) from exc
                port = _find_free_loopback_port()
                command = self._server_command(port)
                with self._lock:
                    self._state = "starting"
                    self._fatal_error = ""
                    self._port = port
                    self._command = command
                    self._effective_env = effective_env
                    self._server_facts = {}
                    self._server_log_tail.clear()
                    self._server_log_path = self.log_dir / (
                        f"server_{self._session_id}_{self._process_start_count + 1}.log"
                    )

                creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                creation_flags |= getattr(
                    subprocess, "BELOW_NORMAL_PRIORITY_CLASS", BELOW_NORMAL_PRIORITY_CLASS
                )
                started = time.perf_counter()
                try:
                    process = DATA.popen_temp(
                        command,
                        cwd=str(APP_DIR),
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        bufsize=1,
                        creationflags=creation_flags,
                        close_fds=True,
                    )
                except Exception as exc:
                    if self._vulkan_cache_control is not None:
                        self._vulkan_cache_control.close()
                        self._vulkan_cache_control = None
                    last_error = f"{type(exc).__name__}: {exc}"
                    continue

                with self._lock:
                    self._process = process
                    session_logging.register_process(process.pid, "llama_server")
                    self._process_start_count += 1
                assert process.stdout is not None
                self._server_log_thread = threading.Thread(
                    target=self._server_log_loop,
                    args=(process.stdout,),
                    name="translategemma-llama-server-log",
                    daemon=True,
                )
                self._server_log_thread.start()
                priority_class = _get_windows_priority_class(process.pid)
                self._log(
                    "server_process_started",
                    attempt=attempt,
                    pid=process.pid,
                    port=port,
                    process_priority="below_normal",
                    priority_class=priority_class,
                    runtime_version=self.runtime_version,
                    model_path=_relative_to_app(self.model_path),
                    quant=self.quant,
                    gpu_layers=self.gpu_layers,
                    ctx=self.ctx_size,
                    batch=BATCH_SIZE,
                    ubatch=UBATCH_SIZE,
                    threads=THREADS,
                    parallel=PARALLEL,
                    command=command,
                    device_binding=self.device_binding,
                )

                deadline = time.monotonic() + self.startup_timeout_sec
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        last_error = f"process_exited:{process.returncode}"
                        break
                    if self._health_ready():
                        facts_deadline = time.monotonic() + 2.0
                        profile_ok = False
                        mismatches: dict[str, Any] = {}
                        while time.monotonic() < facts_deadline:
                            profile_ok, mismatches = self._facts_match_profile()
                            if profile_ok:
                                break
                            time.sleep(0.05)
                        if not profile_ok:
                            last_error = "profile_confirmation_failed:" + json.dumps(
                                mismatches, ensure_ascii=False
                            )
                            break
                        if self.runtime_id == "llama.runtime.vulkan.pcache1":
                            try:
                                self._prepare_vulkan_cache(status_callback)
                            except Exception as exc:
                                error = exc if isinstance(exc, TranslateGemmaLlamaBackendError) else TranslateGemmaLlamaBackendError(
                                    "vulkan_preparation_failed", f"{type(exc).__name__}: {exc}"
                                )
                                with self._lock:
                                    self._state = "error"
                                    self._fatal_error = str(error)
                                self._log("vulkan_preparation_failed", error=str(error))
                                self._stop_process("vulkan_preparation_failed")
                                raise error
                        ready_ms = (time.perf_counter() - started) * 1000.0
                        with self._lock:
                            self._state = "ready"
                            self._ready_ms = round(ready_ms, 3)
                            self._fatal_error = ""
                        self._log(
                            "server_ready",
                            pid=process.pid,
                            port=port,
                            startup_to_ready_ms=round(ready_ms, 3),
                            process_priority="below_normal",
                            priority_class=priority_class,
                            confirmed_profile=dict(self._server_facts),
                        )
                        return self.diagnostics()
                    time.sleep(0.15)
                else:
                    last_error = f"readiness_timeout:{self.startup_timeout_sec:.1f}s"

                self._stop_process("startup_failed")
                if last_error.startswith("readiness_timeout"):
                    break

            code = "readiness_timeout" if last_error.startswith("readiness_timeout") else "model_load_failed"
            if last_error.startswith("process_exited"):
                code = "runtime_process_exited"
            error = TranslateGemmaLlamaBackendError(code, last_error)
            with self._lock:
                self._state = "error"
                self._fatal_error = str(error)
            self._log(
                "server_start_failed",
                error_code=code,
                detail=last_error,
                server_log_tail=list(self._server_log_tail)[-20:],
            )
            raise error

    def _prepare_vulkan_cache(self, status_callback: Any | None = None) -> None:
        from translation_backends.vulkan_preparation import (
            cache_snapshot, covered_families, persist_coverage, preparation_inputs,
        )

        family = {
            "translategemma_llama_q6_low_vram": "q6_low_prep_v1",
            "translategemma_llama_q6_speed": "q6_speed_prep_v1",
            "milmmt46_q6": "milmmt_prep_v1",
        }.get(self.name)
        control = self._vulkan_cache_control
        events = self._server_facts.get("vulkan_cache_load_events") or []
        if not family or control is None or len(events) != 1 or self.gpu_layers <= 0:
            raise TranslateGemmaLlamaBackendError("vulkan_preparation_failed", "cache_control_or_device_missing")
        event = events[0]
        if self.device_binding is not None:
            from runtime_devices import vulkan_cache_identity
            expected_identity = vulkan_cache_identity(self.device_binding["runtime_device"])
            if self._device_mismatch() or not expected_identity or event.get("identity") != expected_identity:
                raise TranslateGemmaLlamaBackendError("vulkan_preparation_failed", "bound_device_cache_identity_mismatch")
        root = control.root
        covered, before = covered_families(root, event)
        if family in covered:
            self._vulkan_preparation = {"family": family, "skipped": True, "cache": before, "warmups": []}
            self._log("vulkan_preparation_skipped", family=family, cache=before)
            return
        with self._lock:
            self._state = "preparing"
        if status_callback is not None:
            status_callback("preparing")
        self._log("vulkan_preparation_started", family=family, previous_coverage=sorted(covered), cache_load=event)
        warmups = []
        for source in preparation_inputs(family):
            prompt = self._render_translation_prompt(source, "en", "zh" if family == "milmmt_prep_v1" else "ru", {})
            started = time.perf_counter()
            response = self._json_request("POST", "/completion", {
                "prompt": prompt,
                "n_predict": self.max_output_tokens,
                "temperature": 0.0,
                "top_p": 1.0,
                "min_p": 0.0,
                "repeat_penalty": 1.0,
                "seed": 42,
                "stop": ["<end_of_turn>", "<eos>"],
                "cache_prompt": self.cache_prompt_enabled,
                "stream": False,
            }, timeout=self.translation_timeout_sec)
            if not isinstance(response, dict) or not str(response.get("content") or "").strip():
                raise TranslateGemmaLlamaBackendError("vulkan_preparation_failed", "empty_warmup_response")
            warmups.append({"prompt_chars": len(prompt), "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                            "prompt_tokens": (response.get("timings") or {}).get("prompt_n")})
        commit_started = time.perf_counter()
        if not control.flush(timeout_ms=15000):
            raise TranslateGemmaLlamaBackendError("vulkan_preparation_failed", "cache_flush_not_acknowledged")
        commit_ms = round((time.perf_counter() - commit_started) * 1000, 3)
        after = cache_snapshot(root, event)
        if after is None:
            raise TranslateGemmaLlamaBackendError("vulkan_preparation_failed", "cache_commit_invalid")
        persist_coverage(root, after, covered | {family})
        if cache_snapshot(root, event) != after:
            raise TranslateGemmaLlamaBackendError("vulkan_preparation_failed", "cache_changed_during_coverage_commit")
        self._vulkan_preparation = {"family": family, "skipped": False, "cache": after,
                                    "warmups": warmups, "cache_commit_ms": commit_ms}
        self._log("vulkan_preparation_complete", **self._vulkan_preparation)

    def load_model(self, status_callback: Any | None = None) -> dict[str, Any]:
        return self.start(status_callback=status_callback)

    def _close_active_connection(self, active: dict[str, Any]) -> None:
        connection = active.get("connection")
        if connection is None:
            return
        try:
            sock = getattr(connection, "sock", None)
            if sock is not None:
                sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            connection.close()
        except Exception:
            pass

    def cancel_active(self, reason: str = "") -> int:
        """Invalidate the active response and disconnect the b10057 HTTP request."""
        with self._lock:
            self._generation += 1
            active = self._active_request
            if active is None:
                return 0
            request_id = str(active["request_id"])
            active["cancelled"] = True
            active["cancel_reason"] = str(reason or "runtime_generation_invalidated")
            self._cancelled_request_ids.add(request_id)
            process = self._process
            pid = process.pid if process is not None and process.poll() is None else None
        self._close_active_connection(active)
        self._log(
            "translation_cancel_requested",
            request_id=request_id,
            reason=active["cancel_reason"],
            mechanism="http_client_disconnect",
            pid=pid,
        )
        monitor = threading.Thread(
            target=self._verify_cancel_released_slot,
            args=(request_id, pid),
            name="translategemma-llama-cancel-monitor",
            daemon=True,
        )
        monitor.start()
        return 1

    def _slots_busy(self) -> bool:
        payload = self._json_request("GET", "/slots", timeout=1.5)
        if not isinstance(payload, list):
            raise OSError(f"unexpected_slots_payload:{type(payload).__name__}")
        return any(bool(item.get("is_processing")) for item in payload if isinstance(item, dict))

    def _verify_cancel_released_slot(self, request_id: str, expected_pid: int | None) -> None:
        deadline = time.monotonic() + 3.0
        last_error = ""
        while time.monotonic() < deadline:
            with self._lock:
                process = self._process
                state = self._state
                current_pid = process.pid if process is not None and process.poll() is None else None
            if state in {"stopping", "stopped"} or current_pid != expected_pid:
                return
            try:
                if not self._slots_busy():
                    self._log(
                        "translation_cancel_slot_released",
                        request_id=request_id,
                        pid=expected_pid,
                    )
                    return
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(0.2)

        self._log(
            "translation_cancel_slot_stuck",
            request_id=request_id,
            pid=expected_pid,
            error=last_error,
        )
        self._restart_after_stuck_slot(request_id, expected_pid)

    def _restart_after_stuck_slot(self, request_id: str, expected_pid: int | None) -> None:
        with self._restart_lock:
            with self._lock:
                if request_id in self._restart_for_request_ids:
                    return
                self._restart_for_request_ids.add(request_id)
                process = self._process
                current_pid = process.pid if process is not None and process.poll() is None else None
                if self._state in {"stopping", "stopped"} or current_pid != expected_pid:
                    return
                self._automatic_restart_count += 1
            self._log(
                "controlled_restart_started",
                request_id=request_id,
                pid=expected_pid,
                reason="cancelled_slot_stuck",
            )
            self._stop_process("cancelled_slot_stuck")
            try:
                self.start()
                self._log("controlled_restart_ready", request_id=request_id)
            except Exception as exc:
                with self._lock:
                    self._state = "error"
                    self._fatal_error = f"{type(exc).__name__}: {exc}"
                self._log(
                    "controlled_restart_failed",
                    request_id=request_id,
                    error=f"{type(exc).__name__}: {exc}",
                )

    def translate_text(
        self,
        text: str,
        src_lang: str = "en",
        target_lang: str = "uk",
        use_online: bool = False,
        request_context: dict[str, Any] | None = None,
    ) -> str:
        if use_online:
            raise TranslateGemmaLlamaBackendError("backend_unavailable", "offline_only")
        source = str(src_lang or "en").lower().strip()
        target = str(target_lang or "uk").lower().strip()
        source_text = str(text or "").strip()
        if (source, target) not in self.supported_pairs:
            raise TranslateGemmaLlamaBackendError(
                "unsupported_language_pair", f"{source}->{target}"
            )
        if not source_text:
            return ""
        context = dict(request_context or {})
        context_fields = {
            "ocr_result_id": str(context.get("ocr_result_id") or ""),
            "source_hash": str(context.get("source_hash") or ""),
            "backend_request_index": int(context.get("backend_request_index") or 0),
            "backend_request_count": int(context.get("backend_request_count") or 0),
            "whole_text_enabled": bool(context.get("whole_text_enabled", False)),
            "block": int(context.get("block") or 1),
            "resolved_source_language": str(context.get("resolved_source_language") or source),
            "translation_profile": str(context.get("translation_profile") or ""),
            "runtime_mode": str(context.get("runtime_mode") or ""),
        }
        context_fields["route_identity"] = str(
            context.get("route_identity")
            or context.get("route")
            or (
                f"block{context_fields['block']}:{source}->{target}:"
                f"{context_fields['translation_profile'] or 'default'}"
            )
        )

        with self._translation_lock:
            self.start()
            request_id = self._next_request_id()
            prompt = self._render_translation_prompt(source_text, source, target, context)
            body = json.dumps(
                {
                    "prompt": prompt,
                    "n_predict": self.max_output_tokens,
                    "temperature": 0.0,
                    "top_p": 1.0,
                    "min_p": 0.0,
                    "repeat_penalty": 1.0,
                    "seed": 42,
                    "stop": ["<end_of_turn>", "<eos>"],
                    "cache_prompt": self.cache_prompt_enabled,
                    "stream": False,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            with self._lock:
                port = self._port
                generation = self._generation
            if not port:
                raise TranslateGemmaLlamaBackendError("backend_unavailable", "port_missing")
            connection = http.client.HTTPConnection(
                "127.0.0.1", port, timeout=self.translation_timeout_sec
            )
            active = {
                "request_id": request_id,
                "generation": generation,
                "connection": connection,
                "cancelled": False,
                "started_at": time.perf_counter(),
                "target_lang": target,
            }
            with self._lock:
                self._active_request = active
                self._state = "busy"
                self._translation_requests_sent += 1

            started = time.perf_counter()
            self._log(
                "translation_requested",
                request_id=request_id,
                generation=generation,
                target_lang=target,
                source_char_count=len(source_text),
                ctx_size=self.ctx_size,
                max_output_tokens=self.max_output_tokens,
                timeout_sec=self.translation_timeout_sec,
                cache_prompt_enabled=self.cache_prompt_enabled,
                **context_fields,
            )
            payload: dict[str, Any]
            try:
                connection.request(
                    "POST",
                    "/completion",
                    body=body,
                    headers={
                        "Content-Type": "application/json",
                        "Content-Length": str(len(body)),
                    },
                )
                response = connection.getresponse()
                raw = response.read()
                if response.status >= 400:
                    raise OSError(f"http_{response.status}:{raw[:500]!r}")
                payload = json.loads(raw.decode("utf-8"))
            except socket.timeout as exc:
                with self._lock:
                    active["cancelled"] = True
                    self._cancelled_request_ids.add(request_id)
                self._close_active_connection(active)
                self._verify_cancel_released_slot(request_id, self.diagnostics().get("pid"))
                self._log("translation_timeout", request_id=request_id)
                raise TranslateGemmaLlamaBackendError(
                    "translation_timeout", f"{self.translation_timeout_sec:.1f}s"
                ) from exc
            except Exception as exc:
                with self._lock:
                    cancelled = bool(active.get("cancelled")) or request_id in self._cancelled_request_ids
                    stale = generation != self._generation
                if cancelled or stale:
                    self._log(
                        "cancelled_response_dropped",
                        request_id=request_id,
                        generation=generation,
                        current_generation=self._generation,
                    )
                    return ""
                if not self._process_is_alive():
                    code = "runtime_process_exited"
                else:
                    code = "translation_failed"
                self._log(
                    "translation_error",
                    request_id=request_id,
                    error=f"{type(exc).__name__}: {exc}",
                )
                raise TranslateGemmaLlamaBackendError(
                    code, f"{type(exc).__name__}: {exc}"
                ) from exc
            finally:
                try:
                    connection.close()
                except Exception:
                    pass
                with self._lock:
                    if self._active_request is active:
                        self._active_request = None
                    self._cancelled_request_ids.discard(request_id)
                    if self._process_is_alive() and self._state not in {"stopping", "error"}:
                        self._state = "ready"

            with self._lock:
                cancelled = bool(active.get("cancelled")) or request_id in self._cancelled_request_ids
                stale = generation != self._generation
                self._cancelled_request_ids.discard(request_id)
            if cancelled or stale:
                self._log(
                    "late_translation_dropped",
                    request_id=request_id,
                    generation=generation,
                    current_generation=self._generation,
                )
                return ""

            content = payload.get("content")
            if not isinstance(content, str):
                raise TranslateGemmaLlamaBackendError(
                    "translation_failed", f"unexpected_response:{type(content).__name__}"
                )
            translated = self._clean_translation_output(content, target)
            if not translated:
                raise TranslateGemmaLlamaBackendError("translation_failed", "empty_translation")

            total_ms = (time.perf_counter() - started) * 1000.0
            timings = payload.get("timings") or {}
            prompt_ms = float(timings.get("prompt_ms") or 0.0)
            generation_ms = float(timings.get("predicted_ms") or 0.0)
            generated_tokens = int(payload.get("tokens_predicted") or 0)
            prompt_tokens = int(payload.get("tokens_evaluated") or 0)
            generated_tps = float(timings.get("predicted_per_second") or 0.0)
            cached_prompt_tokens = int(timings.get("cache_n") or 0)
            protocol_overhead_ms = max(0.0, total_ms - prompt_ms - generation_ms)
            stop_reason = "server_stop_unspecified"
            for key in ("stopped_eos", "stopped_word", "stopped_limit"):
                if payload.get(key):
                    stop_reason = key
                    break
            output_ceiling_reached = (
                generated_tokens >= self.max_output_tokens
                or stop_reason == "stopped_limit"
            )
            metrics = {
                "request_id": request_id,
                "target_lang": target,
                "source_char_count": len(source_text),
                "prompt_tokens": prompt_tokens,
                "generated_tokens": generated_tokens,
                "ctx_size": self.ctx_size,
                "max_output_tokens": self.max_output_tokens,
                "output_ceiling_reached": output_ceiling_reached,
                "prompt_ms": round(prompt_ms, 3),
                "generation_ms": round(generation_ms, 3),
                "generated_tokens_per_sec": round(generated_tps, 3),
                "total_backend_ms": round(total_ms, 3),
                "backend_ms": round(total_ms, 3),
                "protocol_overhead_ms": round(protocol_overhead_ms, 3),
                "cache_prompt_enabled": self.cache_prompt_enabled,
                "cached_prompt_tokens": cached_prompt_tokens,
                "stop_reason": stop_reason,
                "cancelled": False,
                "timed_out": False,
                **context_fields,
            }
            with self._lock:
                self._last_translation_metrics = metrics
            self._log("translation_finished", **metrics)
            if output_ceiling_reached:
                self._log(
                    "output_limit_reached",
                    request_id=request_id,
                    route_identity=context_fields["route_identity"],
                    ctx_size=self.ctx_size,
                    max_output_tokens=self.max_output_tokens,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=generated_tokens,
                    stop_reason=stop_reason,
                )
            return translated

    def get_last_translation_metrics(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._last_translation_metrics)

    def log_pipeline_event(self, event: str, details: dict[str, Any]) -> None:
        safe = dict(details or {})
        safe.pop("source", None)
        safe.pop("translated", None)
        self._log(str(event or "pipeline_timing"), **safe)

    def ping(self) -> dict[str, Any]:
        self.start()
        started = time.perf_counter()
        payload = self._json_request("GET", "/health", timeout=CONTROL_TIMEOUT_SEC)
        return {
            "ok": isinstance(payload, dict) and payload.get("status") == "ok",
            "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "payload": payload,
        }

    def status(self) -> dict[str, Any]:
        self.start()
        slots = self._json_request("GET", "/slots", timeout=CONTROL_TIMEOUT_SEC)
        return {"health": self.ping(), "slots": slots, "diagnostics": self.diagnostics()}

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            process = self._process
            pid = process.pid if process is not None and process.poll() is None else None
            priority_class = _get_windows_priority_class(pid)
            active = self._active_request
            return {
                "name": self.name,
                "display_name": self.display_name,
                "memory_key": self.memory_key,
                "translation_mode": f"offline:{self.memory_key}",
                "prefers_whole_text": self.prefers_whole_text,
                "cache_prompt_enabled": self.cache_prompt_enabled,
                "llama_server_command": list(self._command),
                "cache_ram_argument": self.server_cache_ram_mib,
                "cache_ram_effective_expected": self.server_cache_ram_mib,
                "ctx_size": self.ctx_size,
                "max_output_tokens": self.max_output_tokens,
                "gpu_layers_requested": self.gpu_layers,
                "state": self._state,
                "runtime_version": self.runtime_version,
                "pid": pid,
                "process_alive": self._process_is_alive(),
                "process_start_count": self._process_start_count,
                "port": self._port,
                "loopback_only": True,
                "priority_class": priority_class,
                "below_normal_priority": priority_class == BELOW_NORMAL_PRIORITY_CLASS,
                "ready_ms": self._ready_ms,
                "command": list(self._command),
                "model_path": str(self.model_path),
                "model_relative_path": _relative_to_app(self.model_path),
                "runtime_relative_path": _relative_to_app(self.runtime_dir),
                "confirmed_profile": dict(self._server_facts),
                "device_binding": self.device_binding,
                "device_binding_mismatch": self._device_mismatch(),
                "translation_requests_sent": self._translation_requests_sent,
                "active_request_id": active.get("request_id") if active else None,
                "generation": self._generation,
                "automatic_restart_count": self._automatic_restart_count,
                "last_translation_metrics": dict(self._last_translation_metrics),
                "vulkan_preparation": dict(self._vulkan_preparation),
                "fatal_error": self._fatal_error,
                "effective_env": dict(self._effective_env),
                "c_path_findings": _c_path_findings(self._effective_env),
                "adapter_log_path": str(self._adapter_log_path),
                "server_log_path": str(self._server_log_path),
                "server_log_tail": list(self._server_log_tail)[-20:],
            }

    def _stop_process(self, reason: str) -> dict[str, Any]:
        with self._lock:
            process = self._process
            if process is None:
                return {"stopped": True, "forced": False, "exit_code": None}
            pid = process.pid
        started = time.perf_counter()
        forced = False
        if process.poll() is None:
            if self._vulkan_cache_control is not None:
                try:
                    acknowledged = self._vulkan_cache_control.flush()
                    self._log("vulkan_cache_flush", acknowledged=acknowledged, reason=reason)
                except Exception as exc:
                    self._log("vulkan_cache_flush_failed", reason=f"{type(exc).__name__}:{exc}")
            try:
                process.terminate()
                process.wait(timeout=SHUTDOWN_TIMEOUT_SEC)
            except subprocess.TimeoutExpired:
                forced = True
                try:
                    process.kill()
                    process.wait(timeout=4.0)
                except Exception:
                    pass
            except Exception:
                forced = True
                try:
                    process.kill()
                    process.wait(timeout=4.0)
                except Exception:
                    pass
        thread = self._server_log_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.5)
        exit_code = process.poll()
        if self._vulkan_cache_control is not None:
            self._vulkan_cache_control.close()
            self._vulkan_cache_control = None
        with self._lock:
            if self._process is process:
                self._process = None
                self._port = None
            if self._state != "error":
                self._state = "stopped"
        result = {
            "stopped": exit_code is not None,
            "forced": forced,
            "exit_code": exit_code,
            "pid": pid,
            "reason": reason,
            "shutdown_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "zombie": process.poll() is None,
        }
        self._log("server_process_stopped", **result)
        return result

    def shutdown(self, reason: str = "backend_shutdown") -> dict[str, Any]:
        global _ACTIVE_LLAMA_BACKEND
        no_process = False
        with self._lock:
            if self._process is None:
                self._state = "stopped"
                no_process = True
            else:
                self._state = "stopping"
        if no_process:
            with _LLAMA_PROFILE_SWITCH_LOCK:
                if _ACTIVE_LLAMA_BACKEND is self:
                    _ACTIVE_LLAMA_BACKEND = None
            return {"stopped": True, "forced": False, "exit_code": None, "zombie": False}
        self.cancel_active(reason)
        result = self._stop_process(reason)
        with self._lock:
            self._active_request = None
            self._cancelled_request_ids.clear()
            self._state = "stopped"
        with _LLAMA_PROFILE_SWITCH_LOCK:
            if _ACTIVE_LLAMA_BACKEND is self:
                _ACTIVE_LLAMA_BACKEND = None
        return result


_backend_singleton = TranslateGemmaLlamaQ6Backend(
    server_cache_ram_mib=Q6_CACHE_RAM_MIB,
)
_speed_backend_singleton = TranslateGemmaLlamaQ6Backend(
    name="translategemma_llama_q6_speed",
    memory_key="translategemma_llama_q6_speed_v1",
    display_name="TranslateGemma Q6 Speed (llama.cpp, experimental)",
    gpu_layers=35,
    log_dir=SPEED_LOG_DIR,
    cache_prompt_enabled=False,
    server_cache_ram_mib=Q6_CACHE_RAM_MIB,
    prefers_whole_text=False,
)
atexit.register(_backend_singleton.shutdown)
atexit.register(_speed_backend_singleton.shutdown)


def get_backend_singleton() -> TranslateGemmaLlamaQ6Backend:
    return _backend_singleton


def get_speed_backend_singleton() -> TranslateGemmaLlamaQ6Backend:
    return _speed_backend_singleton


def configure_session_runtime(runtime_id: str) -> tuple[TranslateGemmaLlamaQ6Backend, TranslateGemmaLlamaQ6Backend]:
    global _backend_singleton, _speed_backend_singleton
    if _backend_singleton.runtime_id != runtime_id:
        _backend_singleton.shutdown(reason="session_runtime_changed")
        _backend_singleton = TranslateGemmaLlamaQ6Backend(
            server_cache_ram_mib=Q6_CACHE_RAM_MIB, runtime_id=runtime_id,
        )
        atexit.register(_backend_singleton.shutdown)
    if _speed_backend_singleton.runtime_id != runtime_id:
        _speed_backend_singleton.shutdown(reason="session_runtime_changed")
        _speed_backend_singleton = TranslateGemmaLlamaQ6Backend(
            name="translategemma_llama_q6_speed",
            memory_key="translategemma_llama_q6_speed_v1",
            display_name="TranslateGemma Q6 Speed (llama.cpp, experimental)",
            gpu_layers=35, log_dir=SPEED_LOG_DIR, cache_prompt_enabled=False,
            server_cache_ram_mib=Q6_CACHE_RAM_MIB, prefers_whole_text=False,
            runtime_id=runtime_id,
        )
        atexit.register(_speed_backend_singleton.shutdown)
    return _backend_singleton, _speed_backend_singleton
