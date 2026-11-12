"""Production persistent MiLMMT-46-4B Q6_K adapter for Block 2 Quality."""

from __future__ import annotations

import os
import re
import threading
from typing import Any

from translation_backends.translategemma_llama_backend import (
    REQUIRED_RUNTIME_FILES,
    RUNTIME_DIR,
    RUNTIME_VERSION,
    TranslateGemmaLlamaBackendError,
    TranslateGemmaLlamaQ6Backend,
)


from resource_registry import RESOURCES, resource_path

MODEL_PATH = resource_path("milmmt.q6")
from data_paths import data_path
LOG_DIR = data_path("logs", "translation", "milmmt46_q6")
LANGUAGE_NAMES = {
    "ja": "Japanese",
    "zh": "Chinese (Simplified)",
    "zh_hant": "Chinese (Traditional)",
    "en": "English",
    "ru": "Russian",
}
CACHE_RAM_MIB = 0
MILMMT_CTX_SIZE = 1024
MILMMT_MAX_OUTPUT_TOKENS = 512


def preflight_milmmt(runtime_id: str | None = None) -> dict[str, Any]:
    runtime = RESOURCES[runtime_id].runtime if runtime_id else None
    runtime_dir = resource_path(runtime_id) if runtime_id else RUNTIME_DIR
    required = runtime.required_files if runtime else REQUIRED_RUNTIME_FILES
    missing_runtime = [str(runtime_dir / name) for name in required if not (runtime_dir / name).is_file()]
    missing_model = [] if MODEL_PATH.is_file() else [str(MODEL_PATH)]
    missing = missing_runtime + missing_model
    return {
        "ok": not missing,
        "error_code": (
            "llama_server_missing"
            if not (runtime_dir / "llama-server.exe").is_file()
            else ("backend_unavailable" if runtime and runtime.backend != "cuda" else "cuda_runtime_missing")
            if missing_runtime
            else "gguf_missing"
            if missing_model
            else ""
        ),
        "runtime_version": runtime.build if runtime else RUNTIME_VERSION,
        "runtime_dir": str(runtime_dir),
        "model_path": str(MODEL_PATH),
        "model_size": MODEL_PATH.stat().st_size if MODEL_PATH.is_file() else 0,
        "ctx_size": MILMMT_CTX_SIZE,
        "max_output_tokens": MILMMT_MAX_OUTPUT_TOKENS,
        "missing_runtime": missing_runtime,
        "missing_model": missing_model,
        "missing": missing,
        "downloads_allowed": False,
    }


class MiLMMTBackend(TranslateGemmaLlamaQ6Backend):
    name = "milmmt46_q6"
    memory_key = "milmmt46_4b_q6_official_prompt_v1"
    display_name = "MiLMMT-46-4B Q6_K"
    allow_fallback = False
    supported_pairs = {
        (source, target)
        for source in ("ja", "zh", "zh_hant")
        for target in ("en", "ru")
    } | {("en", target) for target in ("zh", "zh_hant", "ja")}
    supports_request_context = True
    prefers_whole_text = True

    def __init__(self, *, runtime_id: str | None = None, automatic_selection: bool = False) -> None:
        requested_gpu_layers = int(os.environ.get("GAME_TRANSLATOR_MILMMT_GPU_LAYERS", "999") or 999)
        super().__init__(
            startup_timeout_sec=75.0,
            translation_timeout_sec=180.0,
            name=self.name,
            memory_key=self.memory_key,
            display_name=self.display_name,
            gpu_layers=requested_gpu_layers,
            log_dir=LOG_DIR,
            cache_prompt_enabled=False,
            prefers_whole_text=True,
            model_path=MODEL_PATH,
            quant="Q6_K",
            preflight_callback=(lambda: preflight_milmmt(runtime_id)) if runtime_id else preflight_milmmt,
            runtime_id=runtime_id,
            ctx_size=MILMMT_CTX_SIZE,
            max_output_tokens=MILMMT_MAX_OUTPUT_TOKENS,
        )
        self._requested_gpu_layers = requested_gpu_layers
        self.automatic_selection = automatic_selection
        self._cpu_fallback_used = False
        self._cpu_fallback_lock = threading.Lock()

    def start(self, status_callback: Any | None = None) -> dict[str, Any]:
        try:
            return super().start(status_callback=status_callback)
        except Exception as first_error:
            if (self.automatic_selection or self.runtime_id == "llama.runtime.vulkan.pcache1" or self.gpu_layers <= 0
                    or os.environ.get("GAME_TRANSLATOR_MILMMT_DISABLE_CPU_FALLBACK") == "1"):
                raise
            with self._cpu_fallback_lock:
                if self.gpu_layers <= 0:
                    return super().start(status_callback=status_callback)
                self._log(
                    "gpu_start_failed_cpu_fallback",
                    requested_gpu_layers=self.gpu_layers,
                    error=f"{type(first_error).__name__}:{first_error}",
                )
                self.shutdown(reason="milmmt_gpu_start_failed")
                self.gpu_layers = 0
                self._cpu_fallback_used = True
                return super().start(status_callback=status_callback)

    def _server_command(self, port: int) -> list[str]:
        command = super()._server_command(port)
        command.extend([
            "--batch-size",
            "512",
            "--ubatch-size",
            "512",
            "--threads-batch",
            "4",
            "--no-warmup",
            "--cache-ram",
            str(CACHE_RAM_MIB),
        ])
        return command

    def _facts_match_profile(self) -> tuple[bool, dict[str, Any]]:
        ok, mismatches = super()._facts_match_profile()
        if "gpu_layers" in mismatches and self.gpu_layers >= 999:
            actual = int((mismatches["gpu_layers"] or {}).get("actual") or 0)
            total = int(self._server_facts.get("total_layers") or 0)
            if actual > 0 and actual == total:
                mismatches.pop("gpu_layers", None)
        if "gpu_layers" in mismatches and self.gpu_layers == 0:
            actual = (mismatches["gpu_layers"] or {}).get("actual")
            if actual in {None, 0}:
                mismatches.pop("gpu_layers", None)
        return not mismatches, mismatches

    def _render_translation_prompt(
        self,
        source_text: str,
        source_lang: str,
        target_lang: str,
        request_context: dict[str, Any],
    ) -> str:
        try:
            source_name = LANGUAGE_NAMES[source_lang]
            target_name = LANGUAGE_NAMES[target_lang]
        except KeyError as exc:
            raise TranslateGemmaLlamaBackendError(
                "unsupported_language_pair", f"{source_lang}->{target_lang}"
            ) from exc
        return (
            f"Translate this from {source_name} to {target_name}:\n"
            f"{source_name}: {source_text}\n"
            f"{target_name}:"
        )

    def _clean_translation_output(self, text: str, target_lang: str) -> str:
        value = str(text or "").strip()
        label = re.escape(LANGUAGE_NAMES[target_lang])
        value = re.sub(rf"^\s*{label}\s*:\s*", "", value, flags=re.IGNORECASE)
        return value.replace("<end_of_turn>", "").replace("<eos>", "").strip()

    def diagnostics(self) -> dict[str, Any]:
        result = super().diagnostics()
        result.update({
            "requested_gpu_layers": self._requested_gpu_layers,
            "cpu_fallback_used": self._cpu_fallback_used,
            "official_prompt": True,
            "rolling_context": False,
            "llama_server_command": list(result.get("command") or []),
            "cache_ram_argument": CACHE_RAM_MIB,
            "cache_ram_effective_expected": CACHE_RAM_MIB,
            "ctx_size": self.ctx_size,
            "max_output_tokens": self.max_output_tokens,
        })
        return result


_SINGLETON = MiLMMTBackend()


def get_backend_singleton() -> MiLMMTBackend:
    return _SINGLETON


def configure_session_runtime(runtime_id: str) -> MiLMMTBackend:
    global _SINGLETON
    if _SINGLETON.runtime_id != runtime_id or not _SINGLETON.automatic_selection:
        _SINGLETON.shutdown(reason="session_runtime_changed")
        _SINGLETON = MiLMMTBackend(runtime_id=runtime_id, automatic_selection=True)
    return _SINGLETON


__all__ = ["MiLMMTBackend", "get_backend_singleton", "preflight_milmmt"]
