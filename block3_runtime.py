"""Small request-context helpers for production Block 3 sessions."""

from __future__ import annotations

from typing import Any

from language_blocks import LANGUAGE_MODE_BLOCK3


BLOCK3_TARGETS = {"zh", "zh_hant", "ja"}
BLOCK3_PROFILES = {"fast", "quality"}


def is_block3_session(session_data: dict[str, Any] | None) -> bool:
    data = session_data if isinstance(session_data, dict) else {}
    translation = data.get("translation") if isinstance(data.get("translation"), dict) else {}
    language_mode = str(
        data.get("language_mode") or translation.get("language_mode") or ""
    ).strip().lower()
    return language_mode == LANGUAGE_MODE_BLOCK3


def block3_request_context(
    session_data: dict[str, Any] | None,
    *,
    source_text: str = "",
    runtime_mode: str = "overlay",
) -> dict[str, Any]:
    if not is_block3_session(session_data):
        return {}
    data = session_data if isinstance(session_data, dict) else {}
    translation = data.get("translation") if isinstance(data.get("translation"), dict) else {}
    target = str(translation.get("target_lang") or "ja").strip().lower().replace("-", "_")
    profile = str(translation.get("translation_profile") or "fast").strip().lower()
    if target not in BLOCK3_TARGETS:
        target = "ja"
    if profile not in BLOCK3_PROFILES:
        profile = "fast"
    return {
        "block": 3,
        "profile_id": str(data.get("profile_id") or ""),
        "source_language": "en",
        "resolved_source_language": "en",
        "target_language": target,
        "translation_profile": profile,
        "runtime_mode": str(runtime_mode or "overlay"),
        "source_text": str(source_text or ""),
    }


__all__ = ["BLOCK3_PROFILES", "BLOCK3_TARGETS", "block3_request_context", "is_block3_session"]
