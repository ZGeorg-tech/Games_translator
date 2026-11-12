"""Production Block 3 Fast/Quality translation router."""

from __future__ import annotations

import atexit
import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

from translation_backends.csanmt_backend import get_backend_singleton as get_csanmt_singleton, preflight_csanmt
from translation_backends.fugu_backend import get_backend_singleton as get_fugu_singleton, preflight_fugu
from translation_backends.milmmt_backend import get_backend_singleton as get_milmmt_singleton, preflight_milmmt
from translation_backends.opencc_s2t import OPENCC_VERSION, OpenCCS2TConverter, preflight_opencc_s2t


APP_DIR = Path(__file__).resolve().parents[1]
from data_paths import data_path
import session_logging
LOG_DIR = data_path("logs", "translation", "block3")
TARGETS = {"zh", "zh_hant", "ja"}
PROFILES = {"fast", "quality"}


class Block3RouteError(RuntimeError):
    pass


def preflight_block3_route(target_lang: str, translation_profile: str, runtime_id: str | None = None) -> dict[str, Any]:
    target = str(target_lang or "ja").strip().lower().replace("-", "_")
    profile = str(translation_profile or "fast").strip().lower()
    if target not in TARGETS or profile not in PROFILES:
        return {
            "ok": False,
            "missing": [f"unsupported_block3_route:{profile}:en->{target}"],
            "c_path_findings": [],
        }
    if profile == "quality":
        checks = [preflight_milmmt(runtime_id)]
    elif target == "ja":
        checks = [preflight_fugu()]
    else:
        checks = [preflight_csanmt()]
    if profile == "fast" and target == "zh_hant":
        checks.append(preflight_opencc_s2t())
    missing = [item for check in checks for item in check.get("missing", [])]
    c_path_findings = [item for check in checks for item in check.get("c_path_findings", [])]
    return {
        "ok": not missing and not c_path_findings and all(bool(check.get("ok")) for check in checks),
        "target_lang": target,
        "translation_profile": profile,
        "missing": missing,
        "c_path_findings": c_path_findings,
        "checks": checks,
        "downloads_allowed": False,
    }


class Block3RouterBackend:
    name = "block3_router"
    display_name = "Block 3 profile router"
    allow_fallback = False
    supports_request_context = True
    prefers_whole_text = True
    supported_pairs = {("en", target) for target in TARGETS}

    def __init__(self) -> None:
        self._csanmt = get_csanmt_singleton()
        self._fugu = get_fugu_singleton()
        self._milmmt = get_milmmt_singleton()
        self._opencc = OpenCCS2TConverter()
        self._last_translation = threading.local()
        self._last_metrics: dict[str, Any] = {}
        self._last_route_artifacts: dict[str, Any] = {}
        self._metrics_lock = threading.Lock()
        self._log_lock = threading.Lock()
        self._session_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{id(self):x}"
        self._log_path = LOG_DIR / f"runtime_{self._session_id}.jsonl"
        atexit.register(self.shutdown)

    def _log(self, event: str, **details: Any) -> None:
        session_logging.component_event(event, self.name, details)
        if not session_logging.raw_enabled():
            return
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            safe = {
                key: value
                for key, value in details.items()
                if key not in {"source", "source_text", "translated_text", "prompt"}
            }
            row = {"timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, **safe}
            with self._log_lock:
                with session_logging.open_log(self._log_path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        except Exception:
            pass

    @staticmethod
    def _intent(
        source_text: str,
        src_lang: str,
        target_lang: str,
        request_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        context = dict(request_context or {})
        source = str(context.get("source_language") or src_lang or "en").strip().lower()
        target = str(context.get("target_language") or target_lang or "ja").strip().lower().replace("-", "_")
        profile = str(context.get("translation_profile") or "fast").strip().lower()
        if source != "en" or target not in TARGETS:
            raise Block3RouteError(f"unsupported_language_pair:{source}->{target}")
        if profile not in PROFILES:
            raise Block3RouteError(f"unsupported_translation_profile:{profile}")
        context.update({
            "block": 3,
            "source_language": "en",
            "resolved_source_language": "en",
            "target_language": target,
            "translation_profile": profile,
            "source_text": str(source_text or ""),
        })
        return context

    @staticmethod
    def _route_name(intent: dict[str, Any]) -> str:
        target = str(intent["target_language"])
        profile = str(intent["translation_profile"])
        if profile == "quality":
            return f"milmmt:en->{target}:quality"
        if target == "ja":
            return "fugumt:en->ja:fast"
        if target == "zh_hant":
            return "csanmt:en->zh+opencc:s2t"
        return "csanmt:en->zh:fast"

    def _memory_identity(self, intent: dict[str, Any]) -> str:
        target = str(intent["target_language"])
        profile = str(intent["translation_profile"])
        route = self._route_name(intent)
        if profile == "quality":
            version = f"milmmt:{self._milmmt.memory_key}"
        elif target == "ja":
            version = f"fugumt:{self._fugu.memory_key}"
        else:
            version = (
                f"csanmt:{self._csanmt.memory_key}"
                f"|opencc:{OPENCC_VERSION if target == 'zh_hant' else 'none'}"
            )
        digest = hashlib.sha256(
            f"block3|en|{target}|{profile}|{route}|{version}".encode("utf-8")
        ).hexdigest()[:20]
        return f"block3:en:{target}:{profile}:{route}:{digest}"

    def memory_key_for_request(
        self,
        request_context: dict[str, Any] | None = None,
        target_lang: str | None = None,
    ) -> str:
        context = dict(request_context or {})
        intent = self._intent(
            str(context.get("source_text") or ""),
            str(context.get("source_language") or "en"),
            str(target_lang or context.get("target_language") or "ja"),
            context,
        )
        return self._memory_identity(intent)

    def memory_key_for_target(self, target_lang: str) -> str:
        return str(getattr(self._last_translation, "memory_key", "") or f"block3:unresolved:{target_lang}")

    @property
    def memory_key(self) -> str:
        return str(getattr(self._last_translation, "memory_key", "") or "block3:unresolved")

    def preflight(self, intent: dict[str, Any]) -> dict[str, Any]:
        return preflight_block3_route(
            str(intent["target_language"]),
            str(intent["translation_profile"]),
            self._milmmt.runtime_id,
        )

    def load_model(self, request_context: dict[str, Any] | None = None, status_callback: Any | None = None) -> dict[str, Any]:
        context = dict(request_context or {})
        intent = self._intent(
            str(context.get("source_text") or "Save."),
            str(context.get("source_language") or "en"),
            str(context.get("target_language") or "ja"),
            context,
        )
        preflight = self.preflight(intent)
        if not preflight["ok"]:
            raise Block3RouteError(
                "block3_preflight_failed:" + ";".join([*preflight["missing"], *preflight["c_path_findings"]])
            )
        if intent["translation_profile"] == "fast":
            if intent["target_language"] == "ja":
                ready = self._fugu.load_model(request_context=intent)
            else:
                ready = self._csanmt.load_model(request_context=intent)
            if intent["target_language"] == "zh_hant":
                self._opencc.load()
        else:
            ready = self._milmmt.load_model(status_callback=status_callback) if status_callback else self._milmmt.load_model()
        return {"ok": True, "intent": intent, "ready": ready}

    def translate_text(
        self,
        text: str,
        src_lang: str = "en",
        target_lang: str = "ja",
        use_online: bool = False,
        request_context: dict[str, Any] | None = None,
    ) -> str:
        if use_online:
            raise Block3RouteError("block3_offline_only")
        source_text = str(text or "").strip()
        if not source_text:
            return ""
        intent = self._intent(source_text, src_lang, target_lang, request_context)
        preflight = self.preflight(intent)
        if not preflight["ok"]:
            raise Block3RouteError(
                "block3_preflight_failed:" + ";".join([*preflight["missing"], *preflight["c_path_findings"]])
            )
        route = self._route_name(intent)
        self._log(
            "block3_route_selected",
            route=route,
            target_language=intent["target_language"],
            translation_profile=intent["translation_profile"],
            runtime_mode=intent.get("runtime_mode", "overlay"),
        )
        started = time.perf_counter()
        route_metrics: dict[str, Any] = {}
        route_artifacts: dict[str, Any] = {}
        try:
            if intent["translation_profile"] == "quality":
                translated = self._milmmt.translate_text(
                    source_text,
                    src_lang="en",
                    target_lang=str(intent["target_language"]),
                    use_online=False,
                    request_context=intent,
                )
                route_metrics["milmmt_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
            elif intent["target_language"] == "ja":
                fugu_started = time.perf_counter()
                fugu_output = self._fugu.translate_text(
                    source_text,
                    src_lang="en",
                    target_lang="ja",
                    use_online=False,
                    request_context=intent,
                )
                route_metrics["fugu_ms"] = round((time.perf_counter() - fugu_started) * 1000.0, 3)
                route_metrics["fugu_request"] = self._fugu.get_last_translation_metrics()
                translated = fugu_output
            else:
                csanmt_started = time.perf_counter()
                csanmt_output = self._csanmt.translate_text(
                    source_text,
                    src_lang="en",
                    target_lang="zh",
                    use_online=False,
                    request_context=intent,
                )
                route_metrics["csanmt_ms"] = round((time.perf_counter() - csanmt_started) * 1000.0, 3)
                if intent["target_language"] == "zh_hant":
                    opencc_started = time.perf_counter()
                    self._log(
                        "OPENCC_S2T_BEGIN",
                        route=route,
                        source_chars=len(csanmt_output),
                    )
                    opencc_success = False
                    try:
                        translated = self._opencc.convert(csanmt_output)
                        opencc_success = True
                    finally:
                        opencc_elapsed_ms = (time.perf_counter() - opencc_started) * 1000.0
                        self._log(
                            "OPENCC_S2T_END",
                            route=route,
                            elapsed_ms=round(opencc_elapsed_ms, 3),
                            success=opencc_success,
                        )
                    route_metrics["opencc_ms"] = round((time.perf_counter() - opencc_started) * 1000.0, 3)
                    route_artifacts = {
                        "csanmt_zh_output": csanmt_output,
                        "opencc_output": translated,
                        "opencc_applied": True,
                        "opencc_ms": route_metrics["opencc_ms"],
                    }
                else:
                    translated = csanmt_output
        except Exception as exc:
            self._log("block3_route_error", route=route, error=f"{type(exc).__name__}:{exc}")
            raise
        if not str(translated or "").strip():
            raise Block3RouteError("block3_empty_translation")
        memory_key = self._memory_identity(intent)
        self._last_translation.memory_key = memory_key
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        metrics = {
            "backend": self.name,
            "route": route,
            "source_language": "en",
            "target_language": intent["target_language"],
            "translation_profile": intent["translation_profile"],
            "runtime_mode": intent.get("runtime_mode", "overlay"),
            "memory_key": memory_key,
            "backend_ms": round(elapsed_ms, 3),
            "total_backend_ms": round(elapsed_ms, 3),
            **route_metrics,
        }
        with self._metrics_lock:
            self._last_metrics = metrics
            self._last_route_artifacts = route_artifacts
        self._log("block3_translation_completed", **metrics)
        return str(translated).strip()

    def get_last_translation_metrics(self) -> dict[str, Any]:
        with self._metrics_lock:
            return dict(self._last_metrics)

    def get_last_route_artifacts(self) -> dict[str, Any]:
        with self._metrics_lock:
            return dict(self._last_route_artifacts)

    def log_pipeline_event(self, event: str, details: dict[str, Any]) -> None:
        self._log(str(event or "pipeline_event"), **dict(details or {}))

    def cancel_active(self, reason: str = "") -> int:
        return (
            int(self._csanmt.cancel_active(reason=reason) or 0)
            + int(self._fugu.cancel_active(reason=reason) or 0)
            + int(self._milmmt.cancel_active(reason=reason) or 0)
        )

    def shutdown(self) -> dict[str, Any]:
        csanmt = self._csanmt.shutdown(reason="block3_router_shutdown")
        fugu = self._fugu.shutdown(reason="block3_router_shutdown")
        mil = self._milmmt.shutdown(reason="block3_router_shutdown")
        return {
            "backend": self.name,
            "csanmt": csanmt,
            "fugu": fugu,
            "milmmt": mil,
            "stopped": bool(
                csanmt.get("stopped")
                and fugu.get("stopped")
                and mil.get("stopped")
            ),
        }

    def diagnostics(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "csanmt": self._csanmt.diagnostics(),
            "fugu": self._fugu.diagnostics(),
            "milmmt": self._milmmt.diagnostics(),
            "opencc": self._opencc.diagnostics(),
            "runtime_log": str(self._log_path),
        }


__all__ = ["Block3RouteError", "Block3RouterBackend", "preflight_block3_route"]
