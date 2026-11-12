"""Persistent app-local CSANMT EN->ZH-S JSONL worker for Block 3 Fast."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


from resource_registry import resource_path
from data_paths import DATA, data_path
import session_logging

MODEL_DIR = resource_path("csanmt.model")
WORKER_LOG = Path(
    os.environ.get("GAME_TRANSLATOR_CSANMT_WORKER_LOG")
    or data_path("logs", "translation", "csanmt", f"worker_{os.getpid()}.jsonl")
)
BEAM_SIZE = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_log(event: str, **details: Any) -> None:
    session_logging.component_event(event, "csanmt_en2zh_base", details)
    if not session_logging.raw_enabled():
        return
    try:
        WORKER_LOG.parent.mkdir(parents=True, exist_ok=True)
        row = {"timestamp": _now(), "event": event, "pid": os.getpid(), **details}
        with session_logging.open_log(WORKER_LOG, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        pass


def _emit(event: str, **details: Any) -> None:
    payload = {"event": event, "pid": os.getpid(), **details}
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    _write_log(event, **details)


def _effective_threads() -> dict[str, str]:
    names = (
        "TF_NUM_INTRAOP_THREADS",
        "TF_NUM_INTEROP_THREADS",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    )
    return {name: str(os.environ.get(name) or "") for name in names}


def _validate_environment() -> list[str]:
    findings = []
    for name in (
        "TEMP",
        "TMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "MODELSCOPE_CACHE",
        "HF_HOME",
        "HF_HUB_CACHE",
        "HUGGINGFACE_HUB_CACHE",
        "TRANSFORMERS_CACHE",
        "TORCH_HOME",
        "XDG_CACHE_HOME",
    ):
        value = str(os.environ.get(name) or "")
        if value and not Path(value).resolve().is_relative_to(DATA.root):
            findings.append(f"{name}={value}")
    return findings


def _runtime_modules_summary() -> dict[str, Any]:
    top_level = sorted(
        {
            str(name).split(".", 1)[0]
            for name in sys.modules
            if str(name).strip()
        }
    )

    def imported(*prefixes: str) -> bool:
        return any(
            name == prefix or name.startswith(prefix + ".")
            for name in sys.modules
            for prefix in prefixes
        )

    return {
        "top_level_packages": top_level,
        "top_level_count": len(top_level),
        "required_package_usage": {
            "tensorflow": imported("tensorflow"),
            "keras": imported("keras"),
            "modelscope": imported("modelscope"),
            "torch": imported("torch"),
            "numpy": imported("numpy"),
            "protobuf/grpc": imported("google.protobuf", "grpc"),
            "h5py": imported("h5py"),
        },
        "torch_imported": imported("torch"),
    }


def _load_translator() -> tuple[Any, dict[str, Any]]:
    _emit("heavy_import_begin")
    import_started = time.perf_counter()
    with contextlib.redirect_stdout(sys.stderr):
        from modelscope.models import Model
        from modelscope.pipelines import pipeline
        from modelscope.utils.constant import Tasks
        import modelscope
        import tensorflow
        import torch
        import numpy
        import site

    import_ms = (time.perf_counter() - import_started) * 1000.0
    runtime_modules = _runtime_modules_summary()
    _emit(
        "heavy_import_end",
        elapsed_ms=round(import_ms, 3),
        runtime_modules=runtime_modules,
    )

    _emit("model_load_begin", model_path=str(MODEL_DIR), beam_size=BEAM_SIZE)
    started = time.perf_counter()
    with contextlib.redirect_stdout(sys.stderr):
        model = Model.from_pretrained(
            str(MODEL_DIR),
            device="cpu",
            beam_size=BEAM_SIZE,
        )
        translator = pipeline(task=Tasks.translation, model=model, device="cpu")
    load_ms = (time.perf_counter() - started) * 1000.0
    info = {
        "load_ms": round(load_ms, 3),
        "import_ms": round(import_ms, 3),
        "beam_size": int(model.params.get("beam_size", -1)),
        "threads": _effective_threads(),
        "model_loads": 1,
        "runtime": {
            "sys_executable": sys.executable,
            "sys_base_executable": sys._base_executable,
            "sys_prefix": sys.prefix,
            "sys_base_prefix": sys.base_prefix,
            "user_site_enabled": site.ENABLE_USER_SITE,
            "isolated": bool(sys.flags.isolated and sys.flags.no_user_site),
            "package_domain": getattr(sys, "_games_translator_package_domain", None),
            "tensorflow_file": str(tensorflow.__file__),
            "modelscope_file": str(modelscope.__file__),
            "torch_file": str(torch.__file__),
            "numpy_file": str(numpy.__file__),
            "sys_path": list(sys.path),
        },
        "runtime_modules": runtime_modules,
    }
    _emit("model_load_end", **info)
    _emit("model_ready", **info)
    return translator, info


def _translate(translator: Any, text: str) -> str:
    # ModelScope/TensorFlow occasionally writes diagnostics to stdout; keep JSONL stdout clean.
    with contextlib.redirect_stdout(sys.stderr):
        response = translator(input=str(text or ""))
    if not isinstance(response, dict):
        raise RuntimeError(f"unexpected_csanmt_response:{type(response).__name__}")
    return str(response.get("translation") or response.get("text") or "").strip()


def main() -> int:
    _emit("worker_start", worker_log=str(WORKER_LOG))
    findings = _validate_environment()
    if findings:
        _emit("fatal_error", error="c_path_findings", c_path_findings=findings)
        return 2
    if not MODEL_DIR.is_dir():
        _emit("fatal_error", error=f"model_missing:{MODEL_DIR}")
        return 2

    translator = None
    started = time.perf_counter()
    requests = 0
    successes = 0
    failures = 0
    active_requests = 0
    max_active_requests = 0
    try:
        translator, load_info = _load_translator()
        _emit("warmup_begin")
        warmup_started = time.perf_counter()
        warmup_output = _translate(translator, "Save.")
        warmup_ms = (time.perf_counter() - warmup_started) * 1000.0
        if not warmup_output:
            raise RuntimeError("warmup_empty_translation")
        _emit("warmup_end", elapsed_ms=round(warmup_ms, 3), warmups=1)
        _emit(
            "ready",
            ready_ms=round((time.perf_counter() - started) * 1000.0, 3),
            load_ms=load_info["load_ms"],
            warmup_ms=round(warmup_ms, 3),
            beam_size=load_info["beam_size"],
            threads=load_info["threads"],
            model_loads=1,
            warmups=1,
            runtime=load_info["runtime"],
            runtime_modules=load_info["runtime_modules"],
            c_path_findings=[],
        )

        for raw_line in sys.stdin:
            line = str(raw_line).strip()
            if not line:
                continue
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("request_must_be_object")
            except Exception as exc:
                _emit("protocol_error", error=f"{type(exc).__name__}:{exc}")
                continue

            command = str(request.get("command") or "")
            if command == "shutdown":
                _emit(
                    "shutdown_ack",
                    requests=requests,
                    successes=successes,
                    failures=failures,
                    max_active_requests=max_active_requests,
                )
                break
            if command == "ping":
                _emit("pong", state="ready")
                continue
            if command == "status":
                _emit(
                    "status",
                    state="ready",
                    requests=requests,
                    successes=successes,
                    failures=failures,
                    max_active_requests=max_active_requests,
                )
                continue
            if command != "translate":
                _emit("protocol_error", error=f"unsupported_command:{command}")
                continue

            request_id = str(request.get("request_id") or "")
            source_text = str(request.get("text") or "").strip()
            requests += 1
            active_requests += 1
            max_active_requests = max(max_active_requests, active_requests)
            request_started = time.perf_counter()
            _write_log(
                "translation_begin",
                request_id=request_id,
                source_chars=len(source_text),
                requested_target=str(request.get("requested_target") or "zh"),
            )
            try:
                if not request_id:
                    raise ValueError("missing_request_id")
                if not source_text:
                    raise ValueError("empty_source")
                translated = _translate(translator, source_text)
                if not translated:
                    raise RuntimeError("empty_translation")
                elapsed_ms = (time.perf_counter() - request_started) * 1000.0
                successes += 1
                _emit(
                    "translation_result",
                    request_id=request_id,
                    ok=True,
                    translated_text=translated,
                    elapsed_ms=round(elapsed_ms, 3),
                    max_active_requests=max_active_requests,
                )
            except Exception as exc:
                elapsed_ms = (time.perf_counter() - request_started) * 1000.0
                failures += 1
                _emit(
                    "translation_result",
                    request_id=request_id,
                    ok=False,
                    error=f"{type(exc).__name__}:{exc}",
                    elapsed_ms=round(elapsed_ms, 3),
                    max_active_requests=max_active_requests,
                )
            finally:
                active_requests = max(0, active_requests - 1)
    except Exception as exc:
        _emit(
            "fatal_error",
            error=f"{type(exc).__name__}:{exc}",
            traceback=traceback.format_exc()[-4000:],
        )
        return 2
    finally:
        try:
            session = getattr(translator, "_session", None)
            if session is not None:
                session.close()
        except Exception:
            pass
        _emit(
            "stopped",
            requests=requests,
            successes=successes,
            failures=failures,
            max_active_requests=max_active_requests,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
