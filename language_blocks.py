"""Profile helpers for the existing Block 1 / Block 2 / Block 3 language modes.

The project still has one ``profile.json`` per game.  This module only keeps
block-specific snapshots inside that file and provides atomic JSON writes; it
does not own UI state or introduce a second profile manager.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any


LANGUAGE_MODE_BLOCK1 = "euro_en_to_ru_uk"
LANGUAGE_MODE_BLOCK2 = "asian_to_en_ru_uk"
LANGUAGE_MODE_BLOCK3 = "euro_en_to_asian"
BLOCK1 = "block1"
BLOCK2 = "block2"
BLOCK3 = "block3"
ALL_BLOCKS = (BLOCK1, BLOCK2, BLOCK3)
BLOCKS_KEY = "language_blocks"

BLOCK2_FAST_OCR = "paddle_block2_fast"
BLOCK3_FAST_OCR = "paddle_fast"
QUALITY_FULL_OCR = "paddle"

_BLOCK_FIELDS = (
    "translation",
    "monitor_zone",
    "dynamic",
    "ocr",
    "subtitles",
    "visual_gate",
)


def _mapping(value: Any) -> dict[str, Any]:
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def migrate_block1_backend(value: Any) -> Any:
    """Migrate retired Slim selections without changing other saved choices."""
    if str(value or "").strip().lower() in {
        "translategemma_slim", "translategemma_compact_slim", "translategemma",
    }:
        return "translategemma_llama_q6_low_vram"
    return value


def _migrate_block1_translation(translation: dict[str, Any]) -> None:
    old_backend = translation.get("backend")
    for key in ("backend_preference", "backend"):
        if key in translation:
            translation[key] = migrate_block1_backend(translation[key])
    if old_backend != translation.get("backend"):
        translation.setdefault("backend_preference", translation["backend"])


def block_id_for_language_mode(language_mode: Any) -> str:
    value = str(language_mode or "").strip().lower()
    if value == LANGUAGE_MODE_BLOCK2:
        return BLOCK2
    if value == LANGUAGE_MODE_BLOCK3:
        return BLOCK3
    return BLOCK1


def installed_blocks(metadata: dict[str, Any]) -> tuple[str, ...]:
    if "installed_blocks" not in metadata:
        return ALL_BLOCKS
    value = metadata["installed_blocks"]
    if not isinstance(value, list) or len(value) != 1 or value[0] not in ALL_BLOCKS:
        raise ValueError("installation.json must declare exactly one valid installed block")
    return (value[0],)


def fast_ocr_engine_for_block(block_id: Any) -> str:
    """Return the fixed Fast-profile OCR for a language block."""
    return BLOCK2_FAST_OCR if str(block_id or "") == BLOCK2 else BLOCK3_FAST_OCR


def normalize_quality_ocr_engine(value: Any, block_id: Any) -> str:
    """Normalize the saved Block 2 Quality OCR preference."""
    fast_engine = fast_ocr_engine_for_block(block_id)
    normalized = str(value or "").strip().lower()
    aliases = {
        "paddle-fast": "paddle_fast",
        "paddlefast": "paddle_fast",
        "paddle-block2-fast": "paddle_block2_fast",
        "paddle_block2": "paddle_block2_fast",
        "paddlev6small": "paddle_block2_fast",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in {fast_engine, QUALITY_FULL_OCR} else QUALITY_FULL_OCR


def ocr_engine_for_translation_profile(
    block_id: Any,
    translation_profile: Any,
    quality_engine: Any,
) -> str:
    """Resolve active OCR without changing the selected translation backend."""
    if str(translation_profile or "fast").strip().lower() == "quality":
        return normalize_quality_ocr_engine(quality_engine, block_id)
    return fast_ocr_engine_for_block(block_id)


def default_block2_translation() -> dict[str, Any]:
    return {
        "language_mode": LANGUAGE_MODE_BLOCK2,
        "source_lang": "ja",
        "target_lang": "en",
        "mode": "offline",
        "translation_profile": "fast",
        "chinese_script_mode": "auto",
        "backend": "block2_router",
        "advanced_overrides": {
            "translation_backend": "",
            "ocr_backend": "",
        },
    }


def default_block3_translation() -> dict[str, Any]:
    return {
        "language_mode": LANGUAGE_MODE_BLOCK3,
        "source_lang": "en",
        "target_lang": "ja",
        "mode": "offline",
        "translation_profile": "fast",
        "backend": "block3_router",
    }


def _legacy_block1_state(profile: dict[str, Any]) -> dict[str, Any]:
    translation = _mapping(profile.get("translation"))
    _migrate_block1_translation(translation)
    translation.setdefault("language_mode", LANGUAGE_MODE_BLOCK1)
    translation.setdefault("source_lang", "en")
    translation.setdefault("target_lang", "uk")
    return {
        field: _mapping(profile.get(field))
        for field in _BLOCK_FIELDS
    } | {"translation": translation}


def _default_block2_state(profile: dict[str, Any]) -> dict[str, Any]:
    inherited = {
        field: _mapping(profile.get(field))
        for field in _BLOCK_FIELDS
        if field not in {"translation", "ocr"}
    }
    inherited["translation"] = default_block2_translation()
    inherited["ocr"] = {
        "engine": BLOCK2_FAST_OCR,
        "quality_engine": QUALITY_FULL_OCR,
        "advanced_override": False,
    }
    inherited["initialized_from_active_profile"] = True
    return inherited


def _default_block3_state(profile: dict[str, Any]) -> dict[str, Any]:
    inherited = {
        field: _mapping(profile.get(field))
        for field in _BLOCK_FIELDS
        if field not in {"translation", "ocr"}
    }
    inherited["translation"] = default_block3_translation()
    inherited["ocr"] = {
        "engine": BLOCK3_FAST_OCR,
        "advanced_override": False,
    }
    inherited["initialized_from_active_profile"] = True
    return inherited


def _default_state_for_block(profile: dict[str, Any], block_id: str) -> dict[str, Any]:
    if block_id == BLOCK2:
        return _default_block2_state(profile)
    if block_id == BLOCK3:
        return _default_block3_state(profile)
    return _legacy_block1_state(profile)


def normalize_block_state(
    value: Any,
    block_id: str,
    profile: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        state = _default_state_for_block(profile, block_id)
        if "online" in state["translation"]:
            from online_translation import normalize_settings
            state["translation"]["online"] = normalize_settings(state["translation"]["online"])
        return state

    state = copy.deepcopy(value)
    fallback = _default_state_for_block(profile, block_id)
    for field in _BLOCK_FIELDS:
        if not isinstance(state.get(field), dict):
            state[field] = _mapping(fallback.get(field))

    translation = state["translation"]
    if block_id == BLOCK1:
        _migrate_block1_translation(translation)
        translation["language_mode"] = LANGUAGE_MODE_BLOCK1
        translation.setdefault("source_lang", "en")
        translation.setdefault("target_lang", "uk")
    elif block_id == BLOCK2:
        defaults = default_block2_translation()
        for key, default in defaults.items():
            if key == "advanced_overrides":
                overrides = translation.get(key)
                if not isinstance(overrides, dict):
                    overrides = {}
                for override_key, override_default in default.items():
                    overrides.setdefault(override_key, override_default)
                translation[key] = overrides
            else:
                translation.setdefault(key, default)
        translation["language_mode"] = LANGUAGE_MODE_BLOCK2
        if translation.get("source_lang") not in {"ja", "zh"}:
            translation["source_lang"] = "ja"
        if translation.get("target_lang") not in {"en", "ru", "uk"}:
            translation["target_lang"] = "en"
        if translation.get("translation_profile") not in {"fast", "quality"}:
            translation["translation_profile"] = "fast"
        if translation.get("chinese_script_mode") not in {"auto", "zh", "zh_hant"}:
            translation["chinese_script_mode"] = "auto"
        translation["backend"] = "block2_router"
        state["ocr"].setdefault("engine", BLOCK2_FAST_OCR)
        state["ocr"]["quality_engine"] = normalize_quality_ocr_engine(
            state["ocr"].get("quality_engine"),
            BLOCK2,
        )
        state["ocr"].setdefault("advanced_override", False)
        state["ocr"]["engine"] = ocr_engine_for_translation_profile(
            BLOCK2,
            translation["translation_profile"],
            state["ocr"]["quality_engine"],
        )
    else:
        defaults = default_block3_translation()
        for key, default in defaults.items():
            translation.setdefault(key, default)
        translation["language_mode"] = LANGUAGE_MODE_BLOCK3
        translation["source_lang"] = "en"
        if translation.get("target_lang") not in {"zh", "zh_hant", "ja"}:
            translation["target_lang"] = "ja"
        if translation.get("translation_profile") not in {"fast", "quality"}:
            translation["translation_profile"] = "fast"
        translation.setdefault("mode", "offline")
        translation["backend"] = "block3_router"
        block3_ocr = str(state["ocr"].get("engine") or BLOCK3_FAST_OCR).strip().lower()
        block3_ocr = {
            "paddle-fast": "paddle_fast",
            "paddlefast": "paddle_fast",
        }.get(block3_ocr, block3_ocr)
        if block3_ocr not in {"tesseract", "paddle_fast", "paddle"}:
            block3_ocr = BLOCK3_FAST_OCR
        state["ocr"]["engine"] = block3_ocr
        state["ocr"].pop("quality_engine", None)
        state["ocr"].setdefault("advanced_override", False)
    if "online" in translation:
        from online_translation import normalize_settings
        translation["online"] = normalize_settings(translation["online"])
    return state


def ensure_language_blocks(profile: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a migrated profile copy and compact migration diagnostics."""
    result = copy.deepcopy(profile) if isinstance(profile, dict) else {}
    llm = result.get("llm")
    postedit_migrated = isinstance(llm, dict) and str(llm.get("mode") or "").strip().lower() in {"strict", "literary", "direct"}
    if postedit_migrated:
        llm.update(mode="off", enabled=False)
    raw_blocks = result.get(BLOCKS_KEY)
    blocks = raw_blocks if isinstance(raw_blocks, dict) else {}
    block1_valid = isinstance(blocks.get(BLOCK1), dict)
    block2_valid = isinstance(blocks.get(BLOCK2), dict)
    block3_valid = isinstance(blocks.get(BLOCK3), dict)
    result[BLOCKS_KEY] = {
        BLOCK1: normalize_block_state(blocks.get(BLOCK1), BLOCK1, result),
        BLOCK2: normalize_block_state(blocks.get(BLOCK2), BLOCK2, result),
        BLOCK3: normalize_block_state(blocks.get(BLOCK3), BLOCK3, result),
    }
    old_translation = _mapping(blocks.get(BLOCK1)).get("translation") if block1_valid else result.get("translation")
    migrated_translation = _mapping(old_translation)
    _migrate_block1_translation(migrated_translation)
    block1_backend_migrated = migrated_translation != _mapping(old_translation)
    if block_id_for_language_mode(
        result.get("language_mode") or _mapping(result.get("translation")).get("language_mode")
    ) == BLOCK1:
        root_translation = _mapping(result.get("translation"))
        migrated_root = _mapping(root_translation)
        _migrate_block1_translation(migrated_root)
        block1_backend_migrated |= migrated_root != root_translation
        if migrated_root != root_translation:
            result["translation"] = migrated_root
    return result, {
        "postedit_migrated": postedit_migrated,
        "block1_backend_migrated": block1_backend_migrated,
        "legacy_profile": not isinstance(raw_blocks, dict),
        "block1_recovered": not block1_valid,
        "block2_created_or_recovered": not block2_valid,
        "block3_created_or_recovered": not block3_valid,
    }


def snapshot_active_block(profile: dict[str, Any], block_id: str | None = None) -> dict[str, Any]:
    result, _status = ensure_language_blocks(profile)
    selected = block_id or block_id_for_language_mode(
        result.get("language_mode") or _mapping(result.get("translation")).get("language_mode")
    )
    current = {
        field: _mapping(result.get(field))
        for field in _BLOCK_FIELDS
    }
    result[BLOCKS_KEY][selected] = normalize_block_state(current, selected, result)
    return result


def apply_block_state(profile: dict[str, Any], block_id: str) -> dict[str, Any]:
    result, _status = ensure_language_blocks(profile)
    selected = block_id if block_id in {BLOCK1, BLOCK2, BLOCK3} else BLOCK1
    state = normalize_block_state(result[BLOCKS_KEY].get(selected), selected, result)
    result[BLOCKS_KEY][selected] = state
    for field in _BLOCK_FIELDS:
        result[field] = _mapping(state.get(field))
    language_mode = {
        BLOCK1: LANGUAGE_MODE_BLOCK1,
        BLOCK2: LANGUAGE_MODE_BLOCK2,
        BLOCK3: LANGUAGE_MODE_BLOCK3,
    }[selected]
    result["language_mode"] = language_mode
    result.setdefault("translation", {})["language_mode"] = language_mode
    return result


def replace_block_state(
    profile: dict[str, Any],
    block_id: str,
    state: dict[str, Any],
    *,
    make_active: bool,
) -> dict[str, Any]:
    result, _status = ensure_language_blocks(profile)
    selected = block_id if block_id in {BLOCK1, BLOCK2, BLOCK3} else BLOCK1
    result[BLOCKS_KEY][selected] = normalize_block_state(state, selected, result)
    return apply_block_state(result, selected) if make_active else result


def atomic_write_json(path: str | os.PathLike[str], payload: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=4)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass
