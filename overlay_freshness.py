"""Pure helpers for validating live-overlay translation result freshness."""

from __future__ import annotations

import json
import re
from typing import Any


def normalize_source(text: Any) -> str:
    value = str(text or "").casefold()
    value = re.sub(r"[^\w\s']+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def session_fingerprint(session_data: dict[str, Any] | None) -> str:
    cfg = session_data or {}
    translation = cfg.get("translation", {}) or {}
    ocr = cfg.get("ocr", {}) or {}
    llm = cfg.get("llm", {}) or {}
    subtitles = cfg.get("subtitles", {}) or {}
    payload = {
        "source_lang": str(translation.get("source_lang", "en")),
        "target_lang": str(translation.get("target_lang", "uk")),
        "translation_mode": str(translation.get("mode", "offline")),
        "translator_backend": str(translation.get("backend", "")),
        "ocr_backend": str(ocr.get("engine", "tesseract")),
        "llm_enabled": bool(llm.get("enabled", False)),
        "llm_mode": str(llm.get("mode", "off")),
        "subtitle_background_mode": str(subtitles.get("background_mode", "static_panel")),
        "subtitle_display_policy": str(subtitles.get("display_policy", "follow_source")),
    }
    if translation.get("mode") == "online":
        from online_translation import normalize_settings
        payload["online"] = normalize_settings(translation.get("online"))
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def validate_translation_result(
    result: dict[str, Any],
    *,
    current_generation: int,
    current_visual_epoch: int,
    latest_task_id: int,
    active_task_id: int,
    active_source: str,
    current_session_fingerprint: str,
    visual_pending: bool,
    running: bool,
    paused: bool,
    stopping: bool,
) -> tuple[bool, str]:
    if stopping:
        return False, "session_stopping"
    if not running:
        return False, "engine_not_running"
    if paused:
        return False, "engine_paused"
    if int(result.get("generation", -1)) != int(current_generation):
        return False, "generation_mismatch"
    if int(result.get("visual_epoch", -1)) != int(current_visual_epoch):
        return False, "visual_epoch_mismatch"
    task_id = int(result.get("task_id", -1))
    if task_id != int(latest_task_id):
        return False, "newer_ocr_task_exists"
    if task_id != int(active_task_id):
        return False, "active_task_mismatch"
    if visual_pending:
        return False, "visual_transition_pending"
    if str(result.get("session_fingerprint") or "") != str(current_session_fingerprint or ""):
        return False, "session_fingerprint_mismatch"
    if normalize_source(result.get("source")) != normalize_source(active_source):
        return False, "active_source_mismatch"
    return True, "current"
