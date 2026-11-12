"""Production Block 2 Fast/Quality translation router."""

from __future__ import annotations

import atexit
import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

from block2_runtime import ChineseScriptResolver
from translation_backends.block2_bergamot_backend import (
    Block2BergamotBackend,
    preflight_block2_bergamot,
)
from translation_backends.milmmt_backend import get_backend_singleton as get_milmmt_singleton, preflight_milmmt
from translation_quality.block2_fast import (
    OUTPUT_INTEGRITY_VERSION,
    Block2FastLayer,
    protect_pivot,
    protect_source,
    validate_output_integrity,
)
from translation_quality.block2_quality import Block2QualityLayer, pack_digest as quality_pack_digest


APP_DIR = Path(__file__).resolve().parents[1]
from data_paths import data_path
import session_logging
LOG_DIR = data_path("logs", "translation", "block2")


class Block2RouteError(RuntimeError):
    pass


class Block2RouterBackend:
    name = "block2_router"
    display_name = "Block 2 profile router"
    allow_fallback = False
    supports_request_context = True
    prefers_whole_text = True
    supported_pairs = {
        (source, target)
        for source in ("ja", "zh", "zh_hant")
        for target in ("en", "ru", "uk")
    }

    def __init__(self, block1_bergamot: Any) -> None:
        self._block1_bergamot = block1_bergamot
        self._cjk_bergamot = Block2BergamotBackend()
        self._milmmt = get_milmmt_singleton()
        self._fast = Block2FastLayer()
        self._quality = Block2QualityLayer(event_logger=self._quality_event)
        self._script_resolver = ChineseScriptResolver()
        self._last_translation = threading.local()
        self._last_metrics: dict[str, Any] = {}
        self._metrics_lock = threading.Lock()
        self._log_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._request_seq = 0
        self._session_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{id(self):x}"
        self._log_path = LOG_DIR / f"runtime_{self._session_id}.jsonl"
        atexit.register(self.shutdown)

    def _log(self, event: str, **details: Any) -> None:
        session_logging.component_event(event, self.name, details)
        if not session_logging.raw_enabled():
            return
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            text_diagnostic = event == "block2_fast_pivot_diagnostic"
            safe = {
                key: value
                for key, value in details.items()
                if text_diagnostic or key not in {"source", "source_text", "translated_text", "prompt"}
            }
            row = {"timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, **safe}
            with self._log_lock:
                with session_logging.open_log(self._log_path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        except Exception:
            pass

    def _quality_event(self, event: str, details: dict[str, Any]) -> None:
        self._log(event, **details)

    def _request_correlation_id(self, intent: dict[str, Any]) -> str:
        for key in ("translation_request_id", "request_id", "ocr_result_id"):
            value = str(intent.get(key) or "").strip()
            if value:
                part = int(intent.get("backend_request_index") or 0)
                return f"{key}:{value}" + (f":part:{part}" if part > 0 else "")
        with self._request_lock:
            self._request_seq += 1
            sequence = self._request_seq
        return f"{self._session_id}:{sequence}"

    @staticmethod
    def _validation_details(accepted: bool | None, reasons: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
        normalized = [str(reason) for reason in reasons if str(reason)]
        return {
            "accepted": accepted,
            "reason": normalized[0] if normalized else "",
            "reasons": normalized,
        }

    def _new_fast_pivot_diagnostic(
        self,
        *,
        route: str,
        request_id: str,
        source_text: str,
    ) -> dict[str, Any]:
        return {
            "route": route,
            "request_id": request_id,
            "source_text": source_text,
            "first_leg_en": "",
            "final_translation": "",
            "first_leg_validation": self._validation_details(None),
            "final_validation": self._validation_details(None),
            "status": "pending",
            "decision": "pivot",
            "active_stage": "first_leg",
        }

    def _log_fast_pivot_diagnostic(self, diagnostic: dict[str, Any]) -> None:
        self._log(
            "block2_fast_pivot_diagnostic",
            **{
                key: value
                for key, value in diagnostic.items()
                if key != "active_stage"
            },
        )

    def _resolve_intent(
        self,
        source_text: str,
        src_lang: str,
        target_lang: str,
        request_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        context = dict(request_context or {})
        source = str(context.get("source_language") or src_lang or "ja").strip().lower().replace("-", "_")
        target = str(context.get("target_language") or target_lang or "en").strip().lower()
        profile = str(context.get("translation_profile") or "fast").strip().lower()
        if source not in {"ja", "zh", "zh_hant"} or target not in {"en", "ru", "uk"}:
            raise Block2RouteError(f"unsupported_language_pair:{source}->{target}")
        if profile not in {"fast", "quality"}:
            raise Block2RouteError(f"unsupported_translation_profile:{profile}")
        resolution_reason = "not_chinese"
        resolved_source = source
        script_resolution: dict[str, Any] = {}
        if source in {"zh", "zh_hant"}:
            script_mode = str(context.get("chinese_script_mode") or ("zh_hant" if source == "zh_hant" else "auto"))
            script_resolution = self._script_resolver.resolve_detailed(
                source_text,
                mode=script_mode,
                session_key=str(context.get("profile_id") or "session"),
            )
            resolved_source = str(script_resolution["effective_source_lang"])
            resolution_reason = str(script_resolution["decision_reason"])
        context.update({
            "block": 2,
            "source_language": "zh" if source == "zh_hant" else source,
            "resolved_source_language": resolved_source,
            "target_language": target,
            "translation_profile": profile,
        })
        self._log(
            "block2_script_resolved",
            source_language=source,
            resolved_source_language=resolved_source,
            reason=resolution_reason,
            profile_id=str(context.get("profile_id") or ""),
            **(
                {
                    "configured_chinese_mode": script_resolution.get("configured_chinese_mode"),
                    "previous_auto_state": script_resolution.get("previous_auto_state"),
                    "simplified_evidence": script_resolution.get("simplified_evidence"),
                    "traditional_evidence": script_resolution.get("traditional_evidence"),
                    "ambiguous_evidence": script_resolution.get("ambiguous_evidence"),
                    "decision": script_resolution.get("decision"),
                    "decision_reason": script_resolution.get("decision_reason"),
                    "effective_source_lang": script_resolution.get("effective_source_lang"),
                    "new_auto_state": script_resolution.get("new_auto_state"),
                    "source_hash": script_resolution.get("source_hash"),
                }
                if script_resolution
                else {}
            ),
        )
        return context

    def _memory_identity(self, intent: dict[str, Any]) -> str:
        source = str(intent["resolved_source_language"])
        target = str(intent["target_language"])
        profile = str(intent["translation_profile"])
        if profile == "fast":
            translator = (
                "bergamot_cjk_en"
                if target == "en"
                else f"bergamot_cjk_en+bergamot_en_{target}_beam2"
            )
            pack = f"{self._fast.digest}|integrity:{OUTPUT_INTEGRITY_VERSION}"
        else:
            translator = (
                "milmmt46_4b_q6_official+bergamot_en_uk_beam2"
                if target == "uk"
                else "milmmt46_4b_q6_official_direct"
            )
            pack = quality_pack_digest()
        pivot = (
            str(self._block1_bergamot.memory_key_for_target(target))
            if (profile == "fast" and target in {"ru", "uk"}) or (profile == "quality" and target == "uk")
            else "none"
        )
        game_pack_digest = str(
            (intent.get("advanced_overrides") or {}).get("game_pack_digest") or "none"
        )
        digest_source = (
            f"block2|{source}|{target}|{profile}|{translator}|{pack}|{pivot}|{game_pack_digest}"
        )
        digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:20]
        return f"block2:{source}:{target}:{profile}:{translator}:{digest}"

    def memory_key_for_request(self, request_context: dict[str, Any] | None = None, target_lang: str | None = None) -> str:
        context = dict(request_context or {})
        source_text = str(context.get("source_text") or "")
        intent = self._resolve_intent(
            source_text,
            str(context.get("source_language") or "ja"),
            str(target_lang or context.get("target_language") or "en"),
            context,
        )
        return self._memory_identity(intent)

    def memory_key_for_target(self, target_lang: str) -> str:
        return str(getattr(self._last_translation, "memory_key", "") or f"block2:unresolved:{target_lang}")

    @property
    def memory_key(self) -> str:
        return str(getattr(self._last_translation, "memory_key", "") or "block2:unresolved")

    def preflight(self, intent: dict[str, Any]) -> dict[str, Any]:
        profile = str(intent["translation_profile"])
        source = str(intent["resolved_source_language"])
        target = str(intent["target_language"])
        primary = preflight_block2_bergamot(source) if profile == "fast" else preflight_milmmt(self._milmmt.runtime_id)
        missing = list(primary.get("missing") or [])
        c_findings = list(primary.get("c_path_findings") or [])
        needs_block1_leg = (
            (profile == "fast" and target in {"ru", "uk"})
            or (profile == "quality" and target == "uk")
        )
        if needs_block1_leg:
            block1 = self._block1_bergamot.diagnostics()
            model_key = f"model_en{target}"
            model_path = Path(str(block1.get(model_key) or ""))
            if not model_path.is_dir():
                missing.append(str(model_path))
            c_findings.extend(block1.get("c_path_findings") or [])
        return {"ok": not missing and not c_findings, "missing": missing, "c_path_findings": c_findings}

    def load_model(self, request_context: dict[str, Any] | None = None, status_callback: Any | None = None) -> dict[str, Any]:
        context = dict(request_context or {})
        intent = self._resolve_intent(
            str(context.get("source_text") or "保存"),
            str(context.get("source_language") or "ja"),
            str(context.get("target_language") or "en"),
            context,
        )
        preflight = self.preflight(intent)
        if not preflight["ok"]:
            raise Block2RouteError("block2_preflight_failed:" + ";".join([*preflight["missing"], *preflight["c_path_findings"]]))
        if intent["translation_profile"] == "fast":
            ready = self._cjk_bergamot.load_model(intent["resolved_source_language"])
        else:
            ready = self._milmmt.load_model(status_callback=status_callback) if status_callback else self._milmmt.load_model()
        needs_block1_leg = (
            (intent["translation_profile"] == "fast" and intent["target_language"] in {"ru", "uk"})
            or (intent["translation_profile"] == "quality" and intent["target_language"] == "uk")
        )
        if needs_block1_leg:
            self._block1_bergamot._worker_for(intent["target_language"]).start()
        return {"ok": True, "intent": intent, "ready": ready}

    def _translate_english(self, text: str, target_lang: str) -> str:
        return self._block1_bergamot.translate_text(
            text, src_lang="en", target_lang=target_lang, use_online=False
        )

    def _require_fast_output_integrity(
        self,
        *,
        stage: str,
        source_text: str,
        output_text: str,
        target_lang: str,
    ) -> dict[str, Any]:
        result = validate_output_integrity(source_text, output_text, target_lang)
        if result.accepted:
            return dict(result.metrics)
        self._log(
            "block2_fast_output_integrity_rejected",
            stage=stage,
            reason=result.primary_reason,
            reasons=list(result.failures),
            raw_output_hash=hashlib.sha256(str(output_text or "").encode("utf-8")).hexdigest()[:20],
            target_language=target_lang,
            output_integrity_version=OUTPUT_INTEGRITY_VERSION,
            **result.metrics,
        )
        raise Block2RouteError(
            f"block2_fast_output_integrity_failed:{stage}:" + ",".join(result.failures)
        )

    def _translate_fast(
        self,
        source_text: str,
        intent: dict[str, Any],
        pivot_diagnostic: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        source = str(intent["resolved_source_language"])
        target = str(intent["target_language"])
        exact = self._fast.exact(source_text, source, target)
        if exact is not None and exact.accepted:
            self._log("block2_fast_layer_hit", source_language=source, target_language=target, rule_id=exact.rule_id)
            if pivot_diagnostic is not None:
                pivot_diagnostic.update({
                    "final_translation": exact.text,
                    "first_leg_validation": self._validation_details(True, ["not_applicable_exact"]),
                    "final_validation": self._validation_details(True),
                    "status": "accepted",
                    "decision": "exact",
                    "active_stage": "complete",
                })
            return exact.text, {"decision": "exact", "fast_layer_ms": exact.elapsed_ms, "first_leg_ms": 0, "second_leg_ms": 0}

        protected_source = protect_source(source_text, source)
        if pivot_diagnostic is not None:
            pivot_diagnostic["active_stage"] = "first_leg_backend"
        first_started = time.perf_counter()
        first_raw = self._cjk_bergamot.translate_text(
            protected_source.worker_source, src_lang=source, target_lang="en", use_online=False
        )
        first_ms = (time.perf_counter() - first_started) * 1000.0
        first = self._fast.finish(first_raw, protected_source, "en")
        if pivot_diagnostic is not None:
            pivot_diagnostic["first_leg_en"] = first.text
            pivot_diagnostic["first_leg_validation"] = self._validation_details(
                bool(first.accepted), first.failures
            )
            pivot_diagnostic["active_stage"] = "first_leg_validation"
        if not first.accepted:
            raise Block2RouteError("block2_fast_first_leg_validation_failed:" + ",".join(first.failures))
        try:
            self._require_fast_output_integrity(
                stage="first_leg_raw",
                source_text=source_text,
                output_text=first_raw,
                target_lang="en",
            )
            self._require_fast_output_integrity(
                stage="first_leg_finished",
                source_text=source_text,
                output_text=first.text,
                target_lang="en",
            )
        except Exception as exc:
            if pivot_diagnostic is not None:
                pivot_diagnostic["first_leg_validation"] = self._validation_details(
                    False, [f"{type(exc).__name__}:{exc}"]
                )
            raise
        if target == "en":
            return first.text, {"decision": "single_leg", "fast_layer_ms": protected_source.elapsed_ms + first.elapsed_ms, "first_leg_ms": first_ms, "second_leg_ms": 0}

        protected_pivot = protect_pivot(first.text, protected_source.items, target)
        if pivot_diagnostic is not None:
            pivot_diagnostic["active_stage"] = "final_backend"
        second_started = time.perf_counter()
        second_raw = self._translate_english(protected_pivot.worker_source, target)
        second_ms = (time.perf_counter() - second_started) * 1000.0
        final = self._fast.finish(second_raw, protected_pivot, target)
        if pivot_diagnostic is not None:
            pivot_diagnostic["final_translation"] = final.text
            pivot_diagnostic["final_validation"] = self._validation_details(
                bool(final.accepted), final.failures
            )
            pivot_diagnostic["active_stage"] = "final_validation"
        if not final.accepted:
            raise Block2RouteError("block2_fast_second_leg_validation_failed:" + ",".join(final.failures))
        try:
            self._require_fast_output_integrity(
                stage="second_leg_raw",
                source_text=first.text,
                output_text=second_raw,
                target_lang=target,
            )
            self._require_fast_output_integrity(
                stage="second_leg_finished",
                source_text=first.text,
                output_text=final.text,
                target_lang=target,
            )
        except Exception as exc:
            if pivot_diagnostic is not None:
                pivot_diagnostic["final_validation"] = self._validation_details(
                    False, [f"{type(exc).__name__}:{exc}"]
                )
            raise
        if pivot_diagnostic is not None:
            pivot_diagnostic.update({
                "status": "accepted",
                "decision": "pivot",
                "active_stage": "complete",
            })
        return final.text, {
            "decision": "pivot", "fast_layer_ms": protected_source.elapsed_ms + first.elapsed_ms + protected_pivot.elapsed_ms + final.elapsed_ms,
            "first_leg_ms": first_ms, "second_leg_ms": second_ms,
            "output_integrity_version": OUTPUT_INTEGRITY_VERSION,
        }

    def _translate_quality(self, source_text: str, intent: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        def direct(text: str, source: str, target: str, context: dict[str, Any]) -> str:
            return self._milmmt.translate_text(
                text, src_lang=source, target_lang=target, use_online=False, request_context=context
            )

        result = self._quality.translate(
            source_text,
            str(intent["resolved_source_language"]),
            str(intent["target_language"]),
            direct,
            self._translate_english,
            intent,
        )
        if not result.success or not result.text:
            raise Block2RouteError("block2_quality_validation_failed:" + ",".join(result.failures))
        return result.text, {
            "decision": result.decision,
            "quality_retries": result.retries,
            "quality_layer_ms": result.layer_ms,
            "pivot_used": bool(result.pivot_en),
        }

    def translate_text(
        self,
        text: str,
        src_lang: str = "ja",
        target_lang: str = "en",
        use_online: bool = False,
        request_context: dict[str, Any] | None = None,
    ) -> str:
        if use_online:
            raise Block2RouteError("block2_offline_only")
        source_text = str(text or "").strip()
        if not source_text:
            return ""
        started = time.perf_counter()
        intent = self._resolve_intent(source_text, src_lang, target_lang, request_context)
        route = f"{intent['resolved_source_language']}->{intent['target_language']}:{intent['translation_profile']}"
        request_id = self._request_correlation_id(intent)
        pivot_diagnostic = (
            self._new_fast_pivot_diagnostic(
                route=route,
                request_id=request_id,
                source_text=source_text,
            )
            if intent["translation_profile"] == "fast"
            and intent["target_language"] in {"ru", "uk"}
            else None
        )
        preflight = self.preflight(intent)
        if not preflight["ok"]:
            if pivot_diagnostic is not None:
                reasons = ["preflight_failed", *preflight["missing"], *preflight["c_path_findings"]]
                pivot_diagnostic.update({
                    "first_leg_validation": self._validation_details(False, reasons),
                    "final_validation": self._validation_details(False, ["first_leg_unavailable"]),
                    "status": "rejected",
                })
                self._log_fast_pivot_diagnostic(pivot_diagnostic)
            self._log("block2_route_error", request_id=request_id, reason="preflight_failed", missing=preflight["missing"], c_path_findings=preflight["c_path_findings"])
            raise Block2RouteError("block2_preflight_failed:" + ";".join([*preflight["missing"], *preflight["c_path_findings"]]))
        self._log("block2_route_selected", route=route, request_id=request_id, runtime_mode=intent.get("runtime_mode", "overlay"))
        try:
            if intent["translation_profile"] == "fast":
                translated, route_metrics = self._translate_fast(source_text, intent, pivot_diagnostic)
            else:
                translated, route_metrics = self._translate_quality(source_text, intent)
        except Exception as exc:
            if pivot_diagnostic is not None:
                active_stage = str(pivot_diagnostic.get("active_stage") or "first_leg")
                validation_key = "first_leg_validation" if active_stage.startswith("first_leg") else "final_validation"
                current_validation = dict(pivot_diagnostic.get(validation_key) or {})
                if current_validation.get("accepted") is None:
                    pivot_diagnostic[validation_key] = self._validation_details(
                        False, [f"{type(exc).__name__}:{exc}"]
                    )
                if validation_key == "first_leg_validation":
                    pivot_diagnostic["final_validation"] = self._validation_details(
                        False, ["first_leg_rejected"]
                    )
                pivot_diagnostic["status"] = "rejected"
                self._log_fast_pivot_diagnostic(pivot_diagnostic)
            self._log("block2_route_error", route=route, request_id=request_id, error=f"{type(exc).__name__}:{exc}")
            raise
        if pivot_diagnostic is not None:
            pivot_diagnostic["final_translation"] = translated
            if (pivot_diagnostic.get("final_validation") or {}).get("accepted") is None:
                pivot_diagnostic["final_validation"] = self._validation_details(True)
            pivot_diagnostic["status"] = "accepted"
            self._log_fast_pivot_diagnostic(pivot_diagnostic)
        memory_key = self._memory_identity(intent)
        self._last_translation.memory_key = memory_key
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        metrics = {
            "backend": self.name,
            "route": route,
            "source_language": intent["source_language"],
            "resolved_source_language": intent["resolved_source_language"],
            "target_language": intent["target_language"],
            "translation_profile": intent["translation_profile"],
            "runtime_mode": intent.get("runtime_mode", "overlay"),
            "request_id": request_id,
            "memory_key": memory_key,
            "backend_ms": round(elapsed_ms, 3),
            "total_backend_ms": round(elapsed_ms, 3),
            **route_metrics,
        }
        with self._metrics_lock:
            self._last_metrics = metrics
        self._log("block2_translation_completed", **metrics)
        return translated

    def get_last_translation_metrics(self) -> dict[str, Any]:
        with self._metrics_lock:
            return dict(self._last_metrics)

    def log_pipeline_event(self, event: str, details: dict[str, Any]) -> None:
        self._log(str(event or "pipeline_event"), **dict(details or {}))

    def cancel_active(self, reason: str = "") -> int:
        return int(self._milmmt.cancel_active(reason=reason) or 0)

    def shutdown(self) -> dict[str, Any]:
        cjk = self._cjk_bergamot.shutdown()
        mil = self._milmmt.shutdown(reason="block2_router_shutdown")
        return {"backend": self.name, "cjk": cjk, "milmmt": mil, "stopped": bool(cjk.get("stopped") and mil.get("stopped"))}

    def diagnostics(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "fast_pack_digest": self._fast.digest,
            "fast_output_integrity_version": OUTPUT_INTEGRITY_VERSION,
            "quality_pack_digest": self._quality.digest,
            "bergamot": self._cjk_bergamot.diagnostics(),
            "milmmt": self._milmmt.diagnostics(),
            "runtime_log": str(self._log_path),
        }


__all__ = ["Block2RouterBackend", "Block2RouteError"]
