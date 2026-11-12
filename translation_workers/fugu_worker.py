"""Persistent app-local FuguMT EN->JA JSONL worker for Block 3 Fast."""

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
from python_runtime import sentencepiece_model_argument
from data_paths import DATA, data_path
import session_logging

MODEL_DIR = resource_path("fugu.model")
WORKER_LOG = Path(
    os.environ.get("GAME_TRANSLATOR_FUGU_WORKER_LOG")
    or data_path("logs", "translation", "fugu", f"worker_{os.getpid()}.jsonl")
)
BEAM_SIZE = 1
CPU_THREADS = 2
MAX_NEW_TOKENS = 128


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_log(event: str, **details: Any) -> None:
    session_logging.component_event(event, "fugumt_en_ja", details)
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


def _process_stats(process: Any) -> dict[str, Any]:
    try:
        memory = process.memory_info()
        cpu = process.cpu_times()
        return {
            "rss_bytes": int(memory.rss),
            "rss_mb": round(float(memory.rss) / (1024.0 * 1024.0), 3),
            "cpu_seconds": float(cpu.user + cpu.system),
            "thread_count": int(process.num_threads()),
        }
    except Exception:
        return {
            "rss_bytes": None,
            "rss_mb": None,
            "cpu_seconds": None,
            "thread_count": None,
        }


def _load_translator() -> tuple[Any, Any, dict[str, Any]]:
    _emit("heavy_import_begin")
    import_started = time.perf_counter()
    with contextlib.redirect_stdout(sys.stderr):
        import psutil
        import torch
        import transformers
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    torch.set_num_threads(CPU_THREADS)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass
    import_ms = (time.perf_counter() - import_started) * 1000.0
    _emit("heavy_import_end", elapsed_ms=round(import_ms, 3))

    _emit(
        "model_load_begin",
        model_path=str(MODEL_DIR),
        beam_size=BEAM_SIZE,
        cpu_threads=CPU_THREADS,
    )
    load_started = time.perf_counter()
    with contextlib.redirect_stdout(sys.stderr):
        tokenizer = AutoTokenizer.from_pretrained(sentencepiece_model_argument(MODEL_DIR), local_files_only=True)
        model = AutoModelForSeq2SeqLM.from_pretrained(str(MODEL_DIR), local_files_only=True)
        model.to("cpu")
        model.eval()
        # The checkpoint carries max_length=512. Runtime uses one explicit limit:
        # max_new_tokens=128. Clearing the inherited in-memory value avoids a
        # misleading dual-length warning without modifying the checkpoint.
        model.generation_config.max_length = None
    load_ms = (time.perf_counter() - load_started) * 1000.0
    process = psutil.Process()
    first_parameter = next(model.parameters())
    stats = _process_stats(process)
    info = {
        "import_ms": round(import_ms, 3),
        "load_ms": round(load_ms, 3),
        "beam_size": BEAM_SIZE,
        "cpu_threads": int(torch.get_num_threads()),
        "interop_threads": int(torch.get_num_interop_threads()),
        "device": str(first_parameter.device),
        "dtype": str(first_parameter.dtype),
        "cuda_available": bool(torch.cuda.is_available()),
        "gpu_execution": False,
        "model_loads": 1,
        "model_load_count": 1,
        "generation_length_limit": "max_new_tokens",
        "max_new_tokens": MAX_NEW_TOKENS,
        "runtime": {
            "sys_executable": sys.executable,
            "torch_version": torch.__version__,
            "torch_file": str(torch.__file__),
            "transformers_version": transformers.__version__,
            "transformers_file": str(transformers.__file__),
        },
        **stats,
    }
    _emit("model_load_end", **info)
    return tokenizer, model, info


def _translate(tokenizer: Any, model: Any, text: str) -> tuple[str, int]:
    import torch

    encoded = tokenizer(
        str(text or ""),
        return_tensors="pt",
        truncation=True,
        max_length=512,
    )
    with torch.inference_mode():
        generated = model.generate(
            **encoded,
            do_sample=False,
            num_beams=BEAM_SIZE,
            max_new_tokens=MAX_NEW_TOKENS,
        )
    output = tokenizer.batch_decode(generated, skip_special_tokens=True)[0].strip()
    return output, int(generated.shape[-1])


def main() -> int:
    _emit("worker_start", worker_log=str(WORKER_LOG))
    findings = _validate_environment()
    if findings:
        _emit("fatal_error", error="c_path_findings", c_path_findings=findings)
        return 2
    if not MODEL_DIR.is_dir():
        _emit("fatal_error", error=f"model_missing:{MODEL_DIR}")
        return 2

    tokenizer = None
    model = None
    process = None
    started = time.perf_counter()
    requests = 0
    successes = 0
    failures = 0
    active_requests = 0
    max_active_requests = 0
    try:
        tokenizer, model, load_info = _load_translator()
        import psutil

        process = psutil.Process()
        _emit("warmup_begin")
        warmup_started = time.perf_counter()
        warmup_output, warmup_tokens = _translate(tokenizer, model, "Save.")
        warmup_ms = (time.perf_counter() - warmup_started) * 1000.0
        if not warmup_output:
            raise RuntimeError("warmup_empty_translation")
        ready_stats = _process_stats(process)
        _emit(
            "warmup_end",
            elapsed_ms=round(warmup_ms, 3),
            generated_tokens=warmup_tokens,
            warmups=1,
            **ready_stats,
        )
        _emit(
            "ready",
            ready_ms=round((time.perf_counter() - started) * 1000.0, 3),
            load_ms=load_info["load_ms"],
            warmup_ms=round(warmup_ms, 3),
            beam_size=BEAM_SIZE,
            cpu_threads=load_info["cpu_threads"],
            interop_threads=load_info["interop_threads"],
            device=load_info["device"],
            dtype=load_info["dtype"],
            cuda_available=load_info["cuda_available"],
            gpu_execution=False,
            gpu_process_usage_mb=0,
            model_loads=1,
            model_load_count=1,
            warmups=1,
            generation_length_limit="max_new_tokens",
            max_new_tokens=MAX_NEW_TOKENS,
            runtime=load_info["runtime"],
            c_path_findings=[],
            **ready_stats,
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
                    **_process_stats(process),
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
            before_stats = _process_stats(process)
            _write_log(
                "translation_begin",
                request_id=request_id,
                source_chars=len(source_text),
                beam_size=BEAM_SIZE,
                cpu_threads=CPU_THREADS,
            )
            try:
                if not request_id:
                    raise ValueError("missing_request_id")
                if not source_text:
                    raise ValueError("empty_source")
                translated, generated_tokens = _translate(tokenizer, model, source_text)
                if not translated:
                    raise RuntimeError("empty_translation")
                elapsed_ms = (time.perf_counter() - request_started) * 1000.0
                after_stats = _process_stats(process)
                cpu_before = before_stats.get("cpu_seconds")
                cpu_after = after_stats.get("cpu_seconds")
                cpu_elapsed_ms = (
                    max(0.0, float(cpu_after) - float(cpu_before)) * 1000.0
                    if cpu_before is not None and cpu_after is not None
                    else None
                )
                process_cpu_pct = (
                    round(cpu_elapsed_ms * 100.0 / elapsed_ms, 3)
                    if cpu_elapsed_ms is not None and elapsed_ms > 0.0
                    else None
                )
                successes += 1
                _emit(
                    "translation_result",
                    request_id=request_id,
                    ok=True,
                    translated_text=translated,
                    elapsed_ms=round(elapsed_ms, 3),
                    inference_ms=round(elapsed_ms, 3),
                    generated_tokens=generated_tokens,
                    cpu_elapsed_ms=round(cpu_elapsed_ms, 3) if cpu_elapsed_ms is not None else None,
                    process_cpu_pct=process_cpu_pct,
                    logical_cores_used=round(process_cpu_pct / 100.0, 3) if process_cpu_pct is not None else None,
                    gpu_process_usage_mb=0,
                    gpu_execution=False,
                    max_active_requests=max_active_requests,
                    **after_stats,
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
                    inference_ms=round(elapsed_ms, 3),
                    gpu_process_usage_mb=0,
                    gpu_execution=False,
                    max_active_requests=max_active_requests,
                    **_process_stats(process),
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
        _emit(
            "stopped",
            requests=requests,
            successes=successes,
            failures=failures,
            max_active_requests=max_active_requests,
            **(_process_stats(process) if process is not None else {}),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
