"""
Production Bergamot backend for Block 1 EN -> RU/UK offline translation.

This adapter reuses the laboratory persistent Node worker, but keeps production
state isolated from benchmark runners. It does not download models and only
uses app-local paths.
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from translation_quality import BergamotQualityLayer, QualityMatch, QualityPostprocessResult


from resource_registry import APP_ROOT as APP_DIR, resource_path

NODE_PATH = resource_path("bergamot.node")
BERGAMOT_TOOL_DIR = resource_path("bergamot.runtime")
WORKER_PATH = BERGAMOT_TOOL_DIR / "bergamot_persistent_worker.js"
MODEL_DIRS = {"ru": resource_path("bergamot.enru"), "uk": resource_path("bergamot.enuk")}
from data_paths import DATA, data_path
import session_logging
QUALITY_LOG_DIR = data_path("logs", "translation", "bergamot")
TMP_DIR = DATA.temp("bergamot")
CACHE_DIR = data_path("translation.cache")
BRIDGE_FILES = tuple(BERGAMOT_TOOL_DIR / "worker_cjs" / name for name in (
    "bergamot-translator-worker.js", "bergamot-translator-worker.wasm", "translator-worker.cjs"))
REQUEST_TIMEOUT_SEC = 8
READY_TIMEOUT_SEC = 45


class BergamotBackendError(RuntimeError):
    """Controlled Bergamot backend error."""


def _maybe_fix_mojibake(text: str) -> str:
    value = str(text or "")
    if not value:
        return value
    marker_count = sum(value.count(marker) for marker in ("Р", "С", "Гђ", "Г‘"))
    if marker_count < 2:
        return value
    best = value
    for encoding in ("cp1251", "latin1"):
        try:
            candidate = value.encode(encoding, errors="strict").decode("utf-8", errors="strict")
        except Exception:
            continue
        old_score = sum(value.count(marker) for marker in ("Р", "С", "Гђ", "Г‘"))
        new_score = sum(candidate.count(marker) for marker in ("Р", "С", "Гђ", "Г‘"))
        if re.search(r"[\u0400-\u04FF]", candidate) and new_score < old_score:
            best = candidate
            break
    return best


def _reader_thread(stream: Any, out_queue: queue.Queue[str] | None, sink: list[str]) -> None:
    try:
        for line in iter(stream.readline, ""):
            text = str(line).rstrip("\r\n")
            sink.append(text)
            if out_queue is None and session_logging.context() and session_logging.raw_enabled():
                try:
                    path = data_path("logs", "translation", "bergamot", f"stderr_{id(sink)}.log")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with session_logging.open_log(path, "a", encoding="utf-8") as handle:
                        handle.write(text + "\n")
                except OSError:
                    pass
            if out_queue is not None:
                out_queue.put(text)
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _parse_json_line(line: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(line)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _wait_for_payload(
    out_queue: queue.Queue[str],
    predicate: Any,
    timeout_sec: float,
) -> dict[str, Any]:
    deadline = time.perf_counter() + timeout_sec
    while time.perf_counter() < deadline:
        remaining = max(0.05, deadline - time.perf_counter())
        try:
            line = out_queue.get(timeout=remaining)
        except queue.Empty:
            continue
        payload = _parse_json_line(line)
        if payload is not None and predicate(payload):
            return payload
    raise TimeoutError("bergamot_worker_response_timeout")


def _model_dir_for(target_lang: str) -> Path:
    return MODEL_DIRS[target_lang]


def _build_worker_env(*, create: bool = True) -> dict[str, str]:
    paths = DATA.child_environment_paths("bergamot")
    if create:
        DATA.activate_temp()
        for key in ("TEMP", "HOME", "APPDATA", "LOCALAPPDATA"):
            paths[key].mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({key: str(path) for key, path in paths.items()})
    env.update(
        {
            "TEMP": str(TMP_DIR),
            "TMP": str(TMP_DIR),
            "NPM_CONFIG_CACHE": str(data_path("npm")),
            "npm_config_cache": str(data_path("npm")),
            "NPM_CONFIG_PREFIX": str(data_path("generated", "npm_prefix")),
            "npm_config_prefix": str(data_path("generated", "npm_prefix")),
            "BERGAMOT_MODEL_ROOT": str(resource_path("bergamot.enru").parent),
            "BERGAMOT_WASM_ROOT": str(resource_path("bergamot.wasm")),
            "NODE_RUNTIME_DIR": str(NODE_PATH.parent),
            "GAME_TRANSLATOR_TRANSLATION_MODEL_ROOT": str(resource_path("translation.models")),
        }
    )
    env["PATH"] = str(NODE_PATH.parent) + os.pathsep + env.get("PATH", "")
    return env


def _c_path_findings(env: dict[str, str]) -> list[str]:
    checked_keys = [
        "TEMP",
        "TMP",
        "XDG_CACHE_HOME",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "NPM_CONFIG_CACHE",
        "npm_config_cache",
        "NPM_CONFIG_PREFIX",
        "npm_config_prefix",
        "BERGAMOT_MODEL_ROOT",
        "BERGAMOT_WASM_ROOT",
        "NODE_RUNTIME_DIR",
        "GAME_TRANSLATOR_TRANSLATION_MODEL_ROOT",
    ]
    findings: list[str] = []
    for key in checked_keys:
        value = str(env.get(key) or "")
        if value.lower().startswith("c:\\") and not Path(value).resolve().is_relative_to(DATA.root):
            findings.append(f"{key}={value}")
    return findings


def preflight_bergamot_target(target_lang: str) -> dict[str, Any]:
    """Проверяет app-local Block 1 leg без запуска Node worker."""
    target = str(target_lang or "").strip().lower()
    missing: list[str] = []
    if target not in {"ru", "uk"}:
        missing.append(f"unsupported_target:{target}")
    model_dir = _model_dir_for(target)
    for path in (NODE_PATH, WORKER_PATH, model_dir, *BRIDGE_FILES):
        if not path.exists():
            missing.append(str(path))
    env = _build_worker_env(create=False)
    c_path_findings = _c_path_findings(env)
    return {
        "ok": not missing and not c_path_findings,
        "target_lang": target,
        "model_dir": str(model_dir),
        "missing": missing,
        "c_path_findings": c_path_findings,
    }


class _BergamotWorker:
    def __init__(self, target_lang: str):
        self.target_lang = target_lang
        self.source_lang = "en"
        self.proc: subprocess.Popen[str] | None = None
        self.out_queue: queue.Queue[str] | None = None
        self.stdout_lines: list[str] = []
        self.stderr_lines: list[str] = []
        self.ready_payload: dict[str, Any] = {}
        self.worker_start_ms = 0
        self.request_seq = 0
        self.lock = threading.RLock()

    @property
    def model_dir(self) -> Path:
        return _model_dir_for(self.target_lang)

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> dict[str, Any]:
        with self.lock:
            if self.is_running():
                return self.ready_payload
            self.stop()
            if not NODE_PATH.is_file():
                raise BergamotBackendError(f"portable_node_missing:{NODE_PATH}")
            if not WORKER_PATH.is_file():
                raise BergamotBackendError(f"bergamot_worker_missing:{WORKER_PATH}")
            if not self.model_dir.is_dir():
                raise BergamotBackendError(f"bergamot_model_missing:{self.model_dir}")

            env = _build_worker_env()
            findings = _c_path_findings(env)
            if findings:
                raise BergamotBackendError("bergamot_env_points_to_c:" + "; ".join(findings))

            started = time.perf_counter()
            self.proc = DATA.popen_temp(
                [
                    str(NODE_PATH),
                    str(WORKER_PATH),
                    "--source-lang",
                    self.source_lang,
                    "--target-lang",
                    self.target_lang,
                    "--model-dir",
                    str(self.model_dir),
                    "--warmup-text",
                    "Hello.",
                ],
                cwd=str(APP_DIR),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                bufsize=1,
            )
            self.out_queue = queue.Queue()
            session_logging.register_process(self.proc.pid, "bergamot")
            self.stdout_lines = []
            self.stderr_lines = []
            threading.Thread(target=_reader_thread, args=(self.proc.stdout, self.out_queue, self.stdout_lines), daemon=True).start()
            threading.Thread(target=_reader_thread, args=(self.proc.stderr, None, self.stderr_lines), daemon=True).start()
            try:
                ready = _wait_for_payload(
                    self.out_queue,
                    lambda payload: payload.get("type") == "ready",
                    READY_TIMEOUT_SEC,
                )
            except Exception:
                self.stop()
                raise
            self.worker_start_ms = int((time.perf_counter() - started) * 1000)
            if not ready.get("ok"):
                self.stop()
                raise BergamotBackendError(f"bergamot_worker_ready_failed:{ready}")
            self.ready_payload = ready
            session_logging.component_event("ready", "bergamot", {})
            return ready

    def stop(self, graceful_timeout_sec: float = 2.0, terminate_timeout_sec: float = 1.0) -> dict[str, Any]:
        proc = self.proc
        self.proc = None
        self.out_queue = None
        if proc is None:
            return {
                "stopped": True,
                "was_running": False,
                "shutdown_sent": False,
                "terminated": False,
                "killed": False,
                "exit_code": None,
            }
        result = {
            "stopped": False,
            "was_running": proc.poll() is None,
            "shutdown_sent": False,
            "terminated": False,
            "killed": False,
            "exit_code": proc.poll(),
        }
        try:
            if proc.stdin and proc.poll() is None:
                proc.stdin.write(json.dumps({"command": "shutdown"}, ensure_ascii=False) + "\n")
                proc.stdin.flush()
                result["shutdown_sent"] = True
        except Exception:
            pass
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=max(0.1, float(graceful_timeout_sec)))
        except subprocess.TimeoutExpired:
            try:
                result["terminated"] = True
                proc.terminate()
                proc.wait(timeout=max(0.1, float(terminate_timeout_sec)))
            except subprocess.TimeoutExpired:
                try:
                    result["killed"] = True
                    proc.kill()
                    proc.wait(timeout=max(0.1, float(terminate_timeout_sec)))
                except Exception:
                    pass
            except Exception:
                try:
                    result["killed"] = True
                    proc.kill()
                    proc.wait(timeout=max(0.1, float(terminate_timeout_sec)))
                except Exception:
                    pass
        except Exception:
            try:
                result["killed"] = True
                proc.kill()
                proc.wait(timeout=max(0.1, float(terminate_timeout_sec)))
            except Exception:
                pass
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream:
                    stream.close()
            except Exception:
                pass
        result["exit_code"] = proc.poll()
        result["stopped"] = proc.poll() is not None
        return result

    def translate(self, text: str, timeout_sec: int = REQUEST_TIMEOUT_SEC) -> dict[str, Any]:
        with self.lock:
            self.start()
            if not self.proc or not self.out_queue or not self.proc.stdin:
                raise BergamotBackendError("bergamot_worker_not_ready")
            if self.proc.poll() is not None:
                self.stop()
                self.start()
            self.request_seq += 1
            request_id = self.request_seq
            request = {
                "id": request_id,
                "text": text,
                "source_lang": self.source_lang,
                "target_lang": self.target_lang,
            }
            started = time.perf_counter()
            try:
                self.proc.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
                self.proc.stdin.flush()
                payload = _wait_for_payload(
                    self.out_queue,
                    lambda item: item.get("id") == request_id or item.get("request_id") == request_id,
                    timeout_sec,
                )
            except Exception:
                self.stop()
                raise
            payload["wall_elapsed_ms"] = int((time.perf_counter() - started) * 1000)
            return payload


class BergamotBackend:
    """Offline Bergamot WASM base-memory backend with the Block 1 quality layer."""

    name = "bergamot"
    display_name = "Bergamot WASM Base Memory Beam 2 + Quality Layer"
    supported_pairs = {("en", "ru"), ("en", "uk")}

    def __init__(
        self,
        quality_layer: BergamotQualityLayer | None = None,
        quality_event_logger: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._workers: dict[str, _BergamotWorker] = {}
        self._quality_log_lock = threading.Lock()
        session_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
        self._quality_log_path = QUALITY_LOG_DIR / f"quality_{session_id}.jsonl"
        event_logger = quality_event_logger or self._log_quality_event
        self._quality_layer = quality_layer or BergamotQualityLayer(event_logger=event_logger)
        atexit.register(self.shutdown)

    @property
    def memory_key(self) -> str:
        return self._quality_layer.memory_key

    def memory_key_for_target(self, target_lang: str) -> str:
        return self._quality_layer.memory_key_for_target(target_lang)

    def _log_quality_event(self, event: str, details: dict[str, Any]) -> None:
        session_logging.component_event(event, "bergamot", details)
        if not session_logging.raw_enabled():
            return
        try:
            QUALITY_LOG_DIR.mkdir(parents=True, exist_ok=True)
            row = {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "event": str(event or "BERGAMOT_QUALITY_EVENT"),
                **dict(details or {}),
            }
            with self._quality_log_lock:
                with session_logging.open_log(self._quality_log_path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        except Exception:
            pass

    def load_model(self) -> None:
        """Warm the selected/default Block 1 target language worker."""
        target_lang = os.environ.get("GAME_TRANSLATOR_TARGET_LANG", "").strip().lower()
        if target_lang not in {"ru", "uk"}:
            try:
                import config

                target_lang = str(getattr(config, "CURRENT_LANG", "uk") or "uk").lower().strip()
            except Exception:
                target_lang = "uk"
        if target_lang not in {"ru", "uk"}:
            target_lang = "uk"
        self._worker_for(target_lang).start()

    def shutdown(self) -> dict[str, Any]:
        result = {"backend": self.name, "workers": {}, "stopped": True}
        for target_lang, worker in list(self._workers.items()):
            try:
                worker_result = worker.stop()
                result["workers"][target_lang] = worker_result
                result["stopped"] = bool(result["stopped"] and worker_result.get("stopped"))
            except Exception as exc:
                result["workers"][target_lang] = {
                    "stopped": False,
                    "error": f"{type(exc).__name__}:{exc}",
                }
                result["stopped"] = False
        return result

    def translate_text(
        self,
        text: str,
        src_lang: str = "en",
        target_lang: str = "uk",
        use_online: bool = True,
    ) -> str:
        result = self.translate_with_metadata(
            text,
            src_lang=src_lang,
            target_lang=target_lang,
            use_online=use_online,
        )
        if result.get("ok"):
            return str(result.get("translated_text") or "")
        error = str(result.get("error") or "bergamot_translation_failed")
        if error == "empty_source_text":
            return ""
        raise BergamotBackendError(error)

    def translate_with_metadata(
        self,
        text: str,
        src_lang: str = "en",
        target_lang: str = "uk",
        use_online: bool = True,
    ) -> dict[str, Any]:
        source_text = str(text or "").strip()
        source_lang = str(src_lang or "en").lower().strip()
        target = str(target_lang or "uk").lower().strip()
        started = time.perf_counter()
        if not source_text:
            return self._result(source_text, "", source_lang, target, False, "empty_source_text", started)
        if use_online:
            return self._result(source_text, "", source_lang, target, False, "bergamot_offline_only", started)
        if (source_lang, target) not in self.supported_pairs:
            return self._result(source_text, "", source_lang, target, False, "unsupported_language_pair", started)

        quality_match = self._quality_layer.lookup(source_text, target)
        if quality_match.hit:
            quality_result = self._quality_layer.process_translation(
                source_text,
                quality_match.translated_text,
                target,
                quality_match,
            )
            return self._result(
                source_text,
                quality_result.translated_text,
                source_lang,
                target,
                True,
                "",
                started,
                quality_match=quality_match,
                quality_result=quality_result,
                used_worker=False,
            )

        protected_context = self._quality_layer.prepare_protected_source(source_text)
        worker = self._worker_for(target)
        payload = worker.translate(protected_context.worker_source)
        translated = _maybe_fix_mojibake(str(payload.get("translated_text") or "")).strip()
        ok = bool(payload.get("ok")) and bool(translated)
        error = "" if ok else str(payload.get("error") or payload.get("error_message") or "empty_translation")
        quality_result = (
            self._quality_layer.process_translation(
                source_text,
                translated,
                target,
                quality_match,
                protected_context,
            )
            if ok
            else QualityPostprocessResult(translated)
        )
        return self._result(
            source_text,
            quality_result.translated_text,
            source_lang,
            target,
            ok,
            error,
            started,
            quality_match=quality_match,
            quality_result=quality_result,
            used_worker=True,
            worker_elapsed_ms=int(float(payload.get("elapsed_ms") or 0)),
            worker_wall_ms=int(float(payload.get("wall_elapsed_ms") or 0)),
            worker_start_ms=worker.worker_start_ms,
            worker_ready=dict(worker.ready_payload),
            worker_request_seq=worker.request_seq,
        )

    def _worker_for(self, target_lang: str) -> _BergamotWorker:
        if target_lang not in {"ru", "uk"}:
            raise BergamotBackendError(f"unsupported_target_lang:{target_lang}")
        worker = self._workers.get(target_lang)
        if worker is None:
            worker = _BergamotWorker(target_lang)
            self._workers[target_lang] = worker
        return worker

    def _result(
        self,
        source_text: str,
        translated_text: str,
        source_lang: str,
        target_lang: str,
        ok: bool,
        error: str,
        started: float,
        *,
        quality_match: QualityMatch | None = None,
        quality_result: QualityPostprocessResult | None = None,
        used_worker: bool = False,
        worker_elapsed_ms: int = 0,
        worker_wall_ms: int = 0,
        worker_start_ms: int = 0,
        worker_ready: dict[str, Any] | None = None,
        worker_request_seq: int = 0,
    ) -> dict[str, Any]:
        quality = quality_match
        processed = quality_result
        return {
            "backend": self.name,
            "backend_memory_key": self.memory_key_for_target(target_lang),
            "source_lang": source_lang,
            "target_lang": target_lang,
            "source_text": source_text,
            "translated_text": _maybe_fix_mojibake(str(translated_text or "")).strip(),
            "ok": bool(ok),
            "success": bool(ok),
            "error": str(error or ""),
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "quality_layer_enabled": bool(quality.pack_enabled) if quality else False,
            "quality_pack_id": quality.pack_id if quality else "",
            "quality_pack_version": quality.pack_version if quality else "",
            "quality_pack_digest": quality.pack_digest if quality else "",
            "quality_rule_id": quality.rule_id if quality else "",
            "quality_rule_type": quality.rule_type if quality else "",
            "quality_category": quality.category if quality else "",
            "quality_action": quality.action if quality else "none",
            "quality_hit": bool(quality.hit) if quality else False,
            "quality_key": quality.rule_id if quality else "",
            "quality_pack": quality.pack_id if quality else "",
            "quality_reference_target": quality.reference_target if quality else "",
            "quality_context_hint_ids": list(quality.context_hint_ids) if quality else [],
            "quality_applied_rule_ids": list(processed.applied_rule_ids) if processed else [],
            "quality_structure_changes": list(processed.structure_changes) if processed else [],
            "quality_validation_warnings": list(processed.validation_warnings) if processed else [],
            "protected_literal_count": int(processed.protected_expected_count) if processed else 0,
            "protected_restored_count": int(processed.protected_restored_count) if processed else 0,
            "protected_missing_indexes": list(processed.protected_missing_indexes) if processed else [],
            "protected_duplicate_indexes": list(processed.protected_duplicate_indexes) if processed else [],
            "protected_unknown_sentinel_count": int(processed.protected_unknown_sentinel_count) if processed else 0,
            "protected_restore_failed": bool(processed.protected_restore_failed) if processed else False,
            "used_worker": bool(used_worker),
            "worker_elapsed_ms": int(worker_elapsed_ms),
            "worker_wall_ms": int(worker_wall_ms),
            "worker_start_ms": int(worker_start_ms),
            "worker_ready": worker_ready or {},
            "worker_request_seq": int(worker_request_seq),
            "node_path": str(NODE_PATH),
            "worker_path": str(WORKER_PATH),
            "model_dir": str(_model_dir_for(target_lang)) if target_lang in {"ru", "uk"} else "",
        }

    def diagnostics(self) -> dict[str, Any]:
        env = _build_worker_env(create=False)
        quality = self._quality_layer.diagnostics()
        return {
            "backend": self.name,
            "memory_key": self.memory_key,
            "memory_keys": dict(quality.get("memory_keys") or {}),
            "node_path": str(NODE_PATH),
            "worker_path": str(WORKER_PATH),
            "quality_layer": quality,
            "quality_pack_paths": {
                target: details.get("path", "")
                for target, details in (quality.get("packs") or {}).items()
            },
            "quality_event_log_path": str(self._quality_log_path),
            "model_root": str(resource_path("bergamot.enru").parent),
            "model_enru": str(_model_dir_for("ru")),
            "model_enuk": str(_model_dir_for("uk")),
            "global_node_used": False,
            "c_path_findings": _c_path_findings(env),
            "env_paths": {
                key: env.get(key, "")
                for key in (
                    "TEMP",
                    "TMP",
                    "XDG_CACHE_HOME",
                    "HOME",
                    "USERPROFILE",
                    "APPDATA",
                    "LOCALAPPDATA",
                    "NPM_CONFIG_CACHE",
                    "NPM_CONFIG_PREFIX",
                    "BERGAMOT_MODEL_ROOT",
                    "BERGAMOT_WASM_ROOT",
                    "NODE_RUNTIME_DIR",
                    "GAME_TRANSLATOR_TRANSLATION_MODEL_ROOT",
                )
            },
            "workers": {
                lang: {
                    "running": worker.is_running(),
                    "request_seq": worker.request_seq,
                    "worker_start_ms": worker.worker_start_ms,
                    "model_dir": str(worker.model_dir),
                }
                for lang, worker in self._workers.items()
            },
        }
