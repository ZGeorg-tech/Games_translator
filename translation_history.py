"""
translation_history.py

Лёгкий журнал переводов для подготовки будущей translation memory.
Пишем только принятые переводы, которые реально были показаны/использованы,
а не все промежуточные OCR-кандидаты.

Step13:
- используем папку профиля игры из session_config["profile_dir"], если она есть;
- не плодим точные дубли original+translation+target_lang+mode в одном файле;
- история не должна ломать OCR/перевод при любых ошибках записи.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any
from data_paths import profile_data_dir
from online_translation import persistence_allowed

# Кэш уже записанных ключей по path. Нужен, чтобы не читать весь файл на каждый перевод.
_seen_by_path: dict[str, set[str]] = {}
_loaded_paths: set[str] = set()




def _text_hash(text: str) -> str:
    return hashlib.blake2b((text or "").encode("utf-8", errors="ignore"), digest_size=12).hexdigest()


def _normalize_for_key(text: str) -> str:
    """Мягкая нормализация для ключа дублей, не для вывода пользователю."""
    text = str(text or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def _record_key(original: str, translated: str, target_lang: str, mode: str) -> str:
    raw = "\u241f".join([
        _normalize_for_key(original),
        _normalize_for_key(translated),
        str(target_lang or ""),
        str(mode or ""),
    ])
    return _text_hash(raw)


def _load_seen_keys(path: str) -> set[str]:
    """Лениво читает существующий history-файл и строит набор ключей дублей."""
    if path in _loaded_paths:
        return _seen_by_path.setdefault(path, set())

    seen = _seen_by_path.setdefault(path, set())
    _loaded_paths.add(path)

    if not os.path.exists(path):
        return seen

    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                original = str(row.get("original_text", "") or "")
                translated = str(row.get("translated_text", "") or "")
                target_lang = str(row.get("target_lang", "") or "")
                mode = str(row.get("translation_mode", "") or "")
                if original and translated:
                    seen.add(_record_key(original, translated, target_lang, mode))
    except Exception:
        pass

    return seen


def _resolve_game_dir(app_dir: str, cfg: dict[str, Any]) -> str:
    """Возвращает папку игры. Новый launcher передаёт profile_dir, старые версии — только game_name."""
    return str(profile_data_dir(app_dir, cfg, "history"))


def append_translation(app_dir: str, session_config: dict[str, Any] | None, record: dict[str, Any]) -> None:
    """Безопасно дописывает одну строку JSONL в profiles/<game>/translation_history.jsonl."""
    if not persistence_allowed(session_config, record.get("translation_mode", "")):
        return
    try:
        cfg = session_config or {}
        game_name = cfg.get("game_name") or "unknown_game"
        game_dir = _resolve_game_dir(app_dir, cfg)
        os.makedirs(game_dir, exist_ok=True)

        trans = cfg.get("translation", {}) if isinstance(cfg.get("translation", {}), dict) else {}
        ocr = cfg.get("ocr", {}) if isinstance(cfg.get("ocr", {}), dict) else {}

        original = str(record.get("original_text", "") or "").strip()
        translated = str(record.get("translated_text", "") or "").strip()
        if not original or not translated:
            return

        target_lang = str(record.get("target_lang") or trans.get("target_lang", "") or "")
        translation_mode = str(record.get("translation_mode") or trans.get("mode", "") or "")

        path = os.path.join(game_dir, "translation_history.jsonl")
        seen = _load_seen_keys(path)
        key = _record_key(original, translated, target_lang, translation_mode)
        if key in seen:
            return

        row = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "game_name": game_name,
            "mode": record.get("mode", "live_overlay"),
            "ocr_engine": record.get("ocr_engine") or ocr.get("engine", ""),
            "source_lang": record.get("source_lang") or trans.get("source_lang", ""),
            "target_lang": target_lang,
            "translation_mode": translation_mode,
            "original_text": original,
            "translated_text": translated,
            "ocr_ms": record.get("ocr_ms"),
            "translation_ms": record.get("translation_ms"),
            "from_cache": bool(record.get("from_cache", False)),
            "text_hash": _text_hash(original),
            "record_hash": key,
        }

        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        seen.add(key)
    except Exception:
        # История переводов не должна ломать OCR/перевод.
        return
