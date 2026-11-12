"""
Translation engine dispatcher.

Current purpose:
- keep existing translator_core behavior unchanged;
- give main.py a stable translation interface;
- prepare a safe place for Bergamot and compare mode.

This module deliberately exposes the same function names that main.py already
used from translator_core.py:
- load_model(...)
- translate_text(...)

That lets main.py replace only one import:
    import translation_engine as translator_core
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict

try:
    import config
except Exception:  # pragma: no cover
    config = None

from translation_backends.marian_backend import MarianBackend
from translation_backends.bergamot_backend import BergamotBackend
from language_blocks import migrate_block1_backend
from translation_backends.translategemma_llama_backend import (
    get_backend_singleton as get_translategemma_llama_backend_singleton,
    get_speed_backend_singleton as get_translategemma_llama_speed_backend_singleton,
)
from translation_backends.block3_router_backend import Block3RouterBackend
from online_translation import ONLINE, memory_identity, validate_configuration


class _OnlineBackend:
    name = "online"
    allow_fallback = False
    supports_request_context = True
    prefers_whole_text = True

    def memory_key_for_request(self, request_context=None, target_lang=None):
        context = request_context or {}
        return memory_identity(context.get("online"), context.get("source_language", "en"),
                               target_lang or context.get("target_language", "uk"))

    def load_model(self, request_context=None):
        context = request_context or {}
        validate_configuration(context.get("online"), context.get("source_language", "en"),
                               context.get("target_language", "uk"))

    def cancel_active(self, reason=""):
        return ONLINE.cancel_active(reason)

    def shutdown(self):
        return ONLINE.shutdown()

    def get_last_translation_metrics(self):
        return ONLINE.get_last_translation_metrics()

    def diagnostics(self):
        return {"backend": self.name, "counters": dict(ONLINE.counters), "pending": len(ONLINE._flights)}


def online_memory_event(settings, hit):
    ONLINE._event("tm_hit" if hit else "tm_miss", settings)


class _LazyBlock2Backend:
    """Keep optional Block2 packs outside engine import and idle lifecycle."""

    name = "block2_router"
    allow_fallback = False
    supports_request_context = True
    prefers_whole_text = True

    def __init__(self, block1_bergamot):
        self._block1_bergamot = block1_bergamot
        self._instance = None
        self._lock = threading.RLock()

    def _get_instance(self):
        with self._lock:
            if self._instance is None:
                from translation_backends.block2_router_backend import Block2RouterBackend

                self._instance = Block2RouterBackend(self._block1_bergamot)
            return self._instance

    def __getattr__(self, name):
        return getattr(self._get_instance(), name)

    def _if_initialized(self, method, default, *args, **kwargs):
        with self._lock:
            if self._instance is None:
                return default
            return getattr(self._instance, method)(*args, **kwargs)

    def cancel_active(self, reason=""):
        return self._if_initialized("cancel_active", 0, reason=reason)

    def shutdown(self):
        return self._if_initialized("shutdown", {"stopped": True, "initialized": False})

    def diagnostics(self):
        return self._if_initialized("diagnostics", {"backend": self.name, "initialized": False})

    def get_last_translation_metrics(self):
        return self._if_initialized("get_last_translation_metrics", {})

    def log_pipeline_event(self, event, details):
        return self._if_initialized("log_pipeline_event", None, event, details)


_MARIAN_BACKEND = MarianBackend()
_BERGAMOT_BACKEND = BergamotBackend()
_TRANSLATEGEMMA_LLAMA_Q6_BACKEND = get_translategemma_llama_backend_singleton()
_TRANSLATEGEMMA_LLAMA_Q6_SPEED_BACKEND = get_translategemma_llama_speed_backend_singleton()
_BLOCK2_ROUTER_BACKEND = _LazyBlock2Backend(_BERGAMOT_BACKEND)
_BLOCK3_ROUTER_BACKEND = Block3RouterBackend()
_BACKENDS: Dict[str, Any] = {
    "online": _OnlineBackend(),
    "bergamot": _BERGAMOT_BACKEND,
    "translategemma_llama_q6_low_vram": _TRANSLATEGEMMA_LLAMA_Q6_BACKEND,
    "translategemma_llama_q6_speed": _TRANSLATEGEMMA_LLAMA_Q6_SPEED_BACKEND,
    "current_base": _MARIAN_BACKEND,
    "current": _MARIAN_BACKEND,
    "marian": _MARIAN_BACKEND,
    "block2_router": _BLOCK2_ROUTER_BACKEND,
    "block3_router": _BLOCK3_ROUTER_BACKEND,
}

_current_backend_name = "bergamot"
_fallback_backend_name = "current_base"
_last_translation = threading.local()
_quality_session_selection: dict[str, Any] | None = None


def configure_quality_session(selection: dict[str, Any]) -> None:
    """Apply one launcher decision to all Quality consumers in this process."""
    global _quality_session_selection, _TRANSLATEGEMMA_LLAMA_Q6_BACKEND
    global _TRANSLATEGEMMA_LLAMA_Q6_SPEED_BACKEND
    from runtime_devices import bind_quality_selection
    selection = selection if selection.get("device_binding") else bind_quality_selection(selection)
    _quality_session_selection = dict(selection)
    runtime_id = selection.get("runtime_id") if selection.get("available") else None
    if not runtime_id:
        return
    from translation_backends.translategemma_llama_backend import configure_session_runtime as configure_q6
    from translation_backends.milmmt_backend import configure_session_runtime as configure_milmmt

    low, speed = configure_q6(str(runtime_id))
    milmmt = configure_milmmt(str(runtime_id))
    for backend in (low, speed, milmmt):
        backend.configure_device_binding(selection["device_binding"])
    _TRANSLATEGEMMA_LLAMA_Q6_BACKEND = low
    _TRANSLATEGEMMA_LLAMA_Q6_SPEED_BACKEND = speed
    _BACKENDS["translategemma_llama_q6_low_vram"] = low
    _BACKENDS["translategemma_llama_q6_speed"] = speed
    _BLOCK3_ROUTER_BACKEND._milmmt = milmmt
    if _BLOCK2_ROUTER_BACKEND._instance is not None:
        _BLOCK2_ROUTER_BACKEND._instance._milmmt = milmmt


def mark_quality_runtime_readiness(ok: bool, error: str = "") -> None:
    if _quality_session_selection is not None:
        _quality_session_selection["readiness_result"] = "ready" if ok else "failed"
        _quality_session_selection["readiness_error"] = None if ok else error
        if not ok:
            _quality_session_selection["available"] = False
            _quality_session_selection["reason"] = "runtime_or_model_readiness_failure"


def _quality_requested(backend_name: str | None, request_context: dict[str, Any] | None) -> bool:
    if backend_name in {"translategemma_llama_q6_low_vram", "translategemma_llama_q6_speed"}:
        return True
    return backend_name in {"block2_router", "block3_router"} and str(
        (request_context or {}).get("translation_profile") or "fast"
    ) == "quality"


def _require_quality_runtime(backend_name: str | None, request_context: dict[str, Any] | None) -> None:
    if (_quality_session_selection is not None and not _quality_session_selection.get("available")
            and _quality_requested(backend_name, request_context)):
        raise RuntimeError("quality_runtime_unavailable:" + str(_quality_session_selection.get("reason")))


def _set_last_translation_backend(
    name: str,
    fallback_used: bool = False,
    target_lang: str | None = None,
    request_context: dict[str, Any] | None = None,
) -> None:
    backend = get_backend(name)
    _last_translation.backend_name = str(getattr(backend, "name", name) or name)
    _last_translation.memory_key = get_backend_memory_key(
        name,
        target_lang=target_lang,
        request_context=request_context,
    )
    _last_translation.fallback_used = bool(fallback_used)


def available_backends() -> list[str]:
    """Return names of translation backends currently available in this build."""
    return sorted(_BACKENDS.keys())


def get_default_backend_name() -> str:
    """
    Resolve backend name from config/env with safe fallback to current base core.
    """
    cfg_name = None
    if config is not None:
        cfg_name = getattr(config, "TRANSLATION_BACKEND", None)

    env_name = os.environ.get("GAME_TRANSLATOR_TRANSLATION_BACKEND")
    name = migrate_block1_backend(env_name or cfg_name or _current_backend_name or "marian").lower().strip()
    return name if name in _BACKENDS else _fallback_backend_name


def set_backend(name: str) -> bool:
    """
    Switch translation backend for the current process.

    Returns True if backend exists, False if unknown. This is not yet connected
    to the launcher UI; it is for the next experimental stage.
    """
    global _current_backend_name
    normalized = (name or "").lower().strip()
    if normalized not in _BACKENDS:
        return False
    previous_backend = get_backend(_current_backend_name)
    next_backend = _BACKENDS[normalized]
    if previous_backend in {
        _TRANSLATEGEMMA_LLAMA_Q6_BACKEND,
        _TRANSLATEGEMMA_LLAMA_Q6_SPEED_BACKEND,
        _BLOCK2_ROUTER_BACKEND,
        _BLOCK3_ROUTER_BACKEND,
    } and previous_backend is not next_backend:
        shutdown = getattr(previous_backend, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception:
                pass
    _current_backend_name = normalized
    return True


def get_backend(name: str | None = None) -> Any:
    """Return backend instance by name or current/default backend."""
    backend_name = (name or get_default_backend_name()).lower().strip()
    return _BACKENDS.get(backend_name, _BACKENDS[_fallback_backend_name])


def get_backend_memory_key(
    backend_name: str | None = None,
    target_lang: str | None = None,
    request_context: dict[str, Any] | None = None,
) -> str:
    """Return stable translation-memory backend key for the selected backend."""
    backend = get_backend(backend_name)
    request_key = getattr(backend, "memory_key_for_request", None)
    if callable(request_key):
        return str(request_key(request_context=request_context, target_lang=target_lang))
    target_key = getattr(backend, "memory_key_for_target", None)
    if callable(target_key) and target_lang:
        return str(target_key(target_lang) or getattr(backend, "name", backend_name or "current_base"))
    return str(getattr(backend, "memory_key", getattr(backend, "name", backend_name or "current_base")) or "current_base")


def backend_prefers_whole_text(backend_name: str | None = None) -> bool:
    return bool(getattr(get_backend(backend_name), "prefers_whole_text", False))


def get_last_backend_metrics(backend_name: str | None = None) -> dict[str, Any]:
    getter = getattr(get_backend(backend_name), "get_last_translation_metrics", None)
    if not callable(getter):
        return {}
    try:
        return dict(getter() or {})
    except Exception:
        return {}


def log_backend_pipeline_event(
    backend_name: str | None,
    event: str,
    details: dict[str, Any],
) -> None:
    logger = getattr(get_backend(backend_name), "log_pipeline_event", None)
    if callable(logger):
        try:
            logger(event, details)
        except Exception:
            pass


def get_last_translation_backend_name(default: str = "") -> str:
    """Return backend name that actually produced the latest translation in this thread."""
    return str(getattr(_last_translation, "backend_name", default) or default)


def get_last_translation_backend_memory_key(default: str = "") -> str:
    """Return memory key of the backend that actually produced the latest translation in this thread."""
    return str(getattr(_last_translation, "memory_key", default) or default)


def was_last_translation_fallback() -> bool:
    """Return True if the latest translation call used current_base fallback."""
    return bool(getattr(_last_translation, "fallback_used", False))


def load_model(
    backend_name: str | None = None,
    request_context: dict[str, Any] | None = None,
    status_callback: Any | None = None,
) -> None:
    """Load/warm current translation backend model if the backend supports it."""
    _require_quality_runtime(backend_name, request_context)
    backend = get_backend(backend_name)
    if status_callback is not None and backend_name in {
        "translategemma_llama_q6_low_vram", "translategemma_llama_q6_speed",
        "block2_router", "block3_router",
    }:
        if request_context and bool(getattr(backend, "supports_request_context", False)):
            return backend.load_model(request_context=request_context, status_callback=status_callback)
        return backend.load_model(status_callback=status_callback)
    if request_context and bool(getattr(backend, "supports_request_context", False)):
        try:
            return backend.load_model(request_context=request_context)
        except TypeError:
            pass
    return backend.load_model()


def cancel_active_translation(reason: str = "", backend_name: str | None = None) -> int:
    """Best-effort non-blocking cancellation for worker-backed translators."""
    backend = get_backend(backend_name)
    cancel = getattr(backend, "cancel_active", None)
    if not callable(cancel):
        return 0
    try:
        return int(cancel(reason=reason) or 0)
    except TypeError:
        return int(cancel() or 0)
    except Exception:
        return 0


def translate_text(
    text: str,
    src_lang: str = "en",
    target_lang: str = "uk",
    use_online: bool = True,
    backend_name: str | None = None,
    request_context: dict[str, Any] | None = None,
) -> str:
    """
    Translate text through selected backend.

    Signature stays compatible with old translator_core.translate_text.
    The extra backend_name argument is optional and unused by old call sites.
    """
    if use_online:
        context = dict(request_context or {})
        context.setdefault("source_language", src_lang)
        context.setdefault("target_language", target_lang)
        result = ONLINE.translate(text, context["source_language"], target_lang, context.get("online"),
                                  cancelled=context.get("cancelled"), explicit_refresh=bool(context.get("forced_refresh")),
                                  runtime_mode=context.get("runtime_mode", "overlay"))
        _set_last_translation_backend("online", fallback_used=False, target_lang=target_lang, request_context=context)
        return result.text

    _require_quality_runtime(backend_name, request_context)
    backend = get_backend(backend_name)
    try:
        kwargs = {
            "src_lang": src_lang,
            "target_lang": target_lang,
            "use_online": False,
        }
        if request_context and bool(getattr(backend, "supports_request_context", False)):
            kwargs["request_context"] = request_context
        result = backend.translate_text(text, **kwargs)
        _set_last_translation_backend(
            backend_name or get_default_backend_name(),
            fallback_used=False,
            target_lang=target_lang,
            request_context=request_context,
        )
        return result
    except Exception:
        if backend is _BACKENDS[_fallback_backend_name] or not bool(
            getattr(backend, "allow_fallback", True)
        ):
            raise
        result = _BACKENDS[_fallback_backend_name].translate_text(
            text,
            src_lang=src_lang,
            target_lang=target_lang,
            use_online=False,
        )
        _set_last_translation_backend(_fallback_backend_name, fallback_used=True, target_lang=target_lang)
        return result


def shutdown_backends() -> dict[str, Any]:
    """Best-effort backend shutdown hook for long-lived worker processes."""
    seen: set[int] = set()
    results: dict[str, Any] = {}
    for backend_name, backend in _BACKENDS.items():
        backend_id = id(backend)
        if backend_id in seen:
            continue
        seen.add(backend_id)
        result_key = str(getattr(backend, "name", backend_name) or backend_name)
        shutdown = getattr(backend, "shutdown", None)
        if callable(shutdown):
            try:
                results[result_key] = shutdown()
            except Exception as exc:
                results[result_key] = {
                    "stopped": False,
                    "error": f"{type(exc).__name__}:{exc}",
                }
        else:
            results[result_key] = {"stopped": True, "shutdown_hook": False}
    return results
