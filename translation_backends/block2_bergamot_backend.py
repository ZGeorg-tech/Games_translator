"""Persistent app-local Bergamot workers for Block 2 CJK -> English."""

from __future__ import annotations

import atexit
import json
import queue
import subprocess
import threading
import time
from typing import Any

from translation_backends.bergamot_backend import (
    BRIDGE_FILES,
    NODE_PATH,
    READY_TIMEOUT_SEC,
    REQUEST_TIMEOUT_SEC,
    WORKER_PATH,
    _build_worker_env,
    _c_path_findings,
    _reader_thread,
    _wait_for_payload,
)


from resource_registry import APP_ROOT as APP_DIR, resource_path
from data_paths import DATA
import session_logging

MODEL_DIRS = {
    "ja": resource_path("bergamot.jaen"),
    "zh": resource_path("bergamot.zhen"),
    "zh_hant": resource_path("bergamot.zh_hant_en"),
}


class Block2BergamotError(RuntimeError):
    pass


def preflight_block2_bergamot(source_lang: str | None = None) -> dict[str, Any]:
    sources = [source_lang] if source_lang in MODEL_DIRS else list(MODEL_DIRS)
    missing: list[str] = []
    if not NODE_PATH.is_file():
        missing.append(str(NODE_PATH))
    if not WORKER_PATH.is_file():
        missing.append(str(WORKER_PATH))
    missing.extend(str(path) for path in BRIDGE_FILES if not path.is_file())
    for source in sources:
        model_dir = MODEL_DIRS[source]
        if not model_dir.is_dir() or not any(path.is_file() for path in model_dir.rglob("*")):
            missing.append(str(model_dir))
    env = _build_worker_env(create=False)
    findings = _c_path_findings(env)
    return {
        "ok": not missing and not findings,
        "missing": missing,
        "c_path_findings": findings,
        "node_path": str(NODE_PATH),
        "worker_path": str(WORKER_PATH),
        "model_dirs": {key: str(value) for key, value in MODEL_DIRS.items()},
        "downloads_allowed": False,
    }


class _CjkEnglishWorker:
    def __init__(self, source_lang: str) -> None:
        if source_lang not in MODEL_DIRS:
            raise Block2BergamotError(f"unsupported_source_lang:{source_lang}")
        self.source_lang = source_lang
        self.target_lang = "en"
        self.model_dir = MODEL_DIRS[source_lang]
        self.proc: subprocess.Popen[str] | None = None
        self.out_queue: queue.Queue[str] | None = None
        self.stdout_lines: list[str] = []
        self.stderr_lines: list[str] = []
        self.ready_payload: dict[str, Any] = {}
        self.worker_start_ms = 0
        self.request_seq = 0
        self.lock = threading.RLock()

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> dict[str, Any]:
        with self.lock:
            if self.is_running():
                return dict(self.ready_payload)
            self.stop()
            preflight = preflight_block2_bergamot(self.source_lang)
            if not preflight["ok"]:
                raise Block2BergamotError(
                    "block2_bergamot_preflight_failed:"
                    + ";".join([*preflight["missing"], *preflight["c_path_findings"]])
                )
            env = _build_worker_env()
            started = time.perf_counter()
            self.proc = DATA.popen_temp(
                [
                    str(NODE_PATH), str(WORKER_PATH),
                    "--source-lang", self.source_lang,
                    "--target-lang", self.target_lang,
                    "--model-dir", str(self.model_dir),
                    "--warmup-text", "保存" if self.source_lang != "ja" else "セーブ",
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
            session_logging.register_process(self.proc.pid, "bergamot_cjk")
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
                raise Block2BergamotError(f"block2_bergamot_worker_ready_failed:{ready}")
            self.ready_payload = dict(ready)
            session_logging.component_event("ready", "block2_bergamot_cjk_en", {})
            return dict(ready)

    def translate(self, text: str, timeout_sec: int = REQUEST_TIMEOUT_SEC) -> dict[str, Any]:
        with self.lock:
            self.start()
            if not self.proc or not self.proc.stdin or not self.out_queue:
                raise Block2BergamotError("block2_bergamot_worker_not_ready")
            self.request_seq += 1
            request_id = self.request_seq
            started = time.perf_counter()
            try:
                self.proc.stdin.write(json.dumps({
                    "id": request_id,
                    "text": str(text or ""),
                    "source_lang": self.source_lang,
                    "target_lang": self.target_lang,
                }, ensure_ascii=False) + "\n")
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

    def stop(self) -> dict[str, Any]:
        proc = self.proc
        self.proc = None
        self.out_queue = None
        if proc is None:
            return {"stopped": True, "was_running": False}
        try:
            if proc.stdin and proc.poll() is None:
                proc.stdin.write(json.dumps({"command": "shutdown"}) + "\n")
                proc.stdin.flush()
                proc.stdin.close()
            proc.wait(timeout=2.0)
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=1.0)
                except Exception:
                    pass
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream:
                    stream.close()
            except Exception:
                pass
        return {"stopped": proc.poll() is not None, "was_running": True, "exit_code": proc.poll()}


class Block2BergamotBackend:
    name = "block2_bergamot_cjk_en"
    allow_fallback = False
    supported_pairs = {(source, "en") for source in MODEL_DIRS}

    def __init__(self) -> None:
        self._workers: dict[str, _CjkEnglishWorker] = {}
        self._last_metrics: dict[str, Any] = {}
        atexit.register(self.shutdown)

    def _worker_for(self, source_lang: str) -> _CjkEnglishWorker:
        worker = self._workers.get(source_lang)
        if worker is None:
            worker = _CjkEnglishWorker(source_lang)
            self._workers[source_lang] = worker
        return worker

    def load_model(self, source_lang: str = "ja") -> dict[str, Any]:
        return self._worker_for(source_lang).start()

    def translate_text(self, text: str, src_lang: str, target_lang: str = "en", use_online: bool = False) -> str:
        if use_online or (src_lang, target_lang) not in self.supported_pairs:
            raise Block2BergamotError(f"unsupported_language_pair:{src_lang}->{target_lang}")
        if not str(text or "").strip():
            return ""
        worker = self._worker_for(src_lang)
        payload = worker.translate(str(text))
        translated = str(payload.get("translated_text") or "").strip()
        if not payload.get("ok") or not translated:
            raise Block2BergamotError(str(payload.get("error") or "block2_bergamot_empty"))
        self._last_metrics = {
            "source_lang": src_lang,
            "target_lang": target_lang,
            "worker_elapsed_ms": int(float(payload.get("elapsed_ms") or 0)),
            "backend_ms": int(float(payload.get("wall_elapsed_ms") or 0)),
            "worker_start_ms": worker.worker_start_ms,
            "worker_request_seq": worker.request_seq,
        }
        return translated

    def get_last_translation_metrics(self) -> dict[str, Any]:
        return dict(self._last_metrics)

    def shutdown(self) -> dict[str, Any]:
        rows = {source: worker.stop() for source, worker in list(self._workers.items())}
        return {"backend": self.name, "workers": rows, "stopped": all(row.get("stopped") for row in rows.values())}

    def diagnostics(self) -> dict[str, Any]:
        result = preflight_block2_bergamot()
        result["backend"] = self.name
        result["workers"] = {
            source: {"running": worker.is_running(), "request_seq": worker.request_seq}
            for source, worker in self._workers.items()
        }
        return result


__all__ = ["Block2BergamotBackend", "Block2BergamotError", "MODEL_DIRS", "preflight_block2_bergamot"]
