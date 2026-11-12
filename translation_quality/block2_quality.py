"""Production Block 2 Quality mechanisms promoted from Step 07 v02."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from translation_quality.block2_fast import (
    Block2FastLayer,
    ProtectedText,
    normalized_compare,
    protect_pivot,
    protect_source,
    validate_output,
)


from resource_registry import resource_path

PACK_DIR = resource_path("quality.block2_quality")
PACK_PATHS = {
    "ja": PACK_DIR / "ja_quality_v1.json",
    "zh": PACK_DIR / "zh_quality_v1.json",
    "zh_hant": PACK_DIR / "zh_hant_quality_v1.json",
}


@dataclass
class QualityRouteResult:
    text: str
    success: bool
    decision: str
    retries: int = 0
    failures: list[str] = field(default_factory=list)
    pivot_en: str = ""
    elapsed_ms: float = 0.0
    layer_ms: float = 0.0


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise RuntimeError(f"block2_quality_pack_invalid:{path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=1)
def load_packs() -> dict[str, dict[str, Any]]:
    packs = {key: _read_json(path) for key, path in PACK_PATHS.items()}
    for source_lang, pack in packs.items():
        if pack.get("source_lang") != source_lang:
            raise RuntimeError(f"block2_quality_pack_source_mismatch:{source_lang}")
    return packs


@lru_cache(maxsize=1)
def pack_digest() -> str:
    payload = "|".join(f"{key}:{_sha256(path)}" for key, path in sorted(PACK_PATHS.items()))
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _source_expectations(source: str, source_lang: str, target_lang: str) -> tuple[ProtectedText, list[str]]:
    protected = protect_source(source, source_lang)
    expected = [
        str(item.get(target_lang) or "").strip()
        for item in protected.items
        if str(item.get(target_lang) or "").strip()
    ]
    return protected, expected


def _adaptive_failures(source: str, source_lang: str, target_lang: str, text: str) -> tuple[list[str], ProtectedText]:
    protected, expected = _source_expectations(source, source_lang, target_lang)
    failures = validate_output(text, target_lang)
    compact = normalized_compare(text)
    for value in expected:
        if normalized_compare(value) not in compact:
            kind = "japanese_cultural" if source_lang == "ja" else "chinese_known_term"
            failures.append(f"{kind}_missing:{value}")
    for item in protected.items:
        source_value = str(item.get("source_value") or "")
        if re.search(r"\d", source_value):
            digits = re.findall(r"\d+(?:[.,]\d+)?", source_value)
            if digits and not all(digit.replace(",", ".") in text.replace(",", ".") for digit in digits):
                failures.append("structural_literal_missing")
    return sorted(set(failures)), protected


class Block2QualityLayer:
    """RAW-first adaptive layer with targeted retry and protected UK pivot."""

    def __init__(self, event_logger: Callable[[str, dict[str, Any]], None] | None = None) -> None:
        load_packs()
        self._event_logger = event_logger
        self._fast_layer = Block2FastLayer()

    @property
    def digest(self) -> str:
        return pack_digest()

    def _log(self, event: str, **details: Any) -> None:
        if self._event_logger:
            self._event_logger(event, details)

    def _direct_adaptive(
        self,
        source: str,
        source_lang: str,
        target_lang: str,
        translate_direct: Callable[[str, str, str, dict[str, Any]], str],
        request_context: dict[str, Any],
    ) -> QualityRouteResult:
        started = time.perf_counter()
        raw = str(translate_direct(source, source_lang, target_lang, request_context) or "").strip()
        layer_started = time.perf_counter()
        failures, protected = _adaptive_failures(source, source_lang, target_lang, raw)
        layer_ms = (time.perf_counter() - layer_started) * 1000.0
        if not failures:
            return QualityRouteResult(raw, True, "raw_accepted", elapsed_ms=(time.perf_counter() - started) * 1000.0, layer_ms=layer_ms)

        # Only objective structural/cultural/known-term failures trigger one retry.
        retryable = any(
            failure.startswith(("japanese_cultural", "chinese_known_term", "structural_literal"))
            for failure in failures
        )
        if not retryable or not protected.items:
            return QualityRouteResult(raw, bool(raw), "raw_fallback_no_safe_retry", failures=failures, elapsed_ms=(time.perf_counter() - started) * 1000.0, layer_ms=layer_ms)

        retry_context = dict(request_context)
        retry_context["quality_retry"] = "cultural" if source_lang == "ja" else "known_term"
        self._log("block2_quality_retry", source_lang=source_lang, target_lang=target_lang, reason=failures[:4])
        retry_raw = str(translate_direct(protected.worker_source, source_lang, target_lang, retry_context) or "").strip()
        retry_result = self._fast_layer.finish(retry_raw, protected, target_lang)
        layer_ms += retry_result.elapsed_ms
        if retry_result.accepted:
            return QualityRouteResult(
                retry_result.text, True, "protected_retry_accepted", retries=1,
                failures=failures, elapsed_ms=(time.perf_counter() - started) * 1000.0, layer_ms=layer_ms,
            )
        return QualityRouteResult(
            raw, bool(raw), "protected_retry_rejected_raw_fallback", retries=1,
            failures=sorted(set(failures + retry_result.failures)),
            elapsed_ms=(time.perf_counter() - started) * 1000.0, layer_ms=layer_ms,
        )

    def translate(
        self,
        source: str,
        source_lang: str,
        target_lang: str,
        translate_direct: Callable[[str, str, str, dict[str, Any]], str],
        translate_english: Callable[[str, str], str],
        request_context: dict[str, Any],
    ) -> QualityRouteResult:
        if target_lang in {"en", "ru"}:
            return self._direct_adaptive(source, source_lang, target_lang, translate_direct, request_context)
        if target_lang != "uk":
            return QualityRouteResult("", False, "unsupported_target", failures=[target_lang])

        started = time.perf_counter()
        pivot_result = self._direct_adaptive(source, source_lang, "en", translate_direct, request_context)
        if not pivot_result.success or not pivot_result.text:
            return QualityRouteResult("", False, "pivot_failed", retries=pivot_result.retries, failures=pivot_result.failures)
        _source_protected, _expected = _source_expectations(source, source_lang, "en")
        pivot_protected = protect_pivot(pivot_result.text, _source_protected.items, "uk")
        worker_output = str(translate_english(pivot_protected.worker_source, "uk") or "").strip()
        final = self._fast_layer.finish(worker_output, pivot_protected, "uk")
        if not final.accepted:
            return QualityRouteResult(
                final.text, False, "uk_protected_pivot_rejected",
                retries=pivot_result.retries, failures=final.failures,
                pivot_en=pivot_result.text, elapsed_ms=(time.perf_counter() - started) * 1000.0,
                layer_ms=pivot_result.layer_ms + pivot_protected.elapsed_ms + final.elapsed_ms,
            )
        return QualityRouteResult(
            final.text, True, "uk_protected_pivot_accepted", retries=pivot_result.retries,
            pivot_en=pivot_result.text, elapsed_ms=(time.perf_counter() - started) * 1000.0,
            layer_ms=pivot_result.layer_ms + pivot_protected.elapsed_ms + final.elapsed_ms,
        )


__all__ = ["Block2QualityLayer", "QualityRouteResult", "PACK_PATHS", "load_packs", "pack_digest"]
