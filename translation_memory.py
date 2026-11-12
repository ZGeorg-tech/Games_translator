"""
translation_memory.py

Step15: simple offline translation memory for each game profile.

Purpose:
- avoid translating the same accepted OCR text repeatedly;
- prepare the future Steam Workshop/community profile direction;
- keep the first version safe, local and fully offline.

Storage:
DATA_ROOT/user/tm/<game>/translation_memory.sqlite

This module is intentionally defensive: any DB error returns an empty cache hit
or silently skips saving. Translation memory must never break OCR/overlay work.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import time
from typing import Any
from data_paths import profile_data_dir, log_file
from online_translation import persistence_allowed
import session_logging

_SCHEMA = """
CREATE TABLE IF NOT EXISTS translations (
    key_hash TEXT PRIMARY KEY,
    original_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    translated_text TEXT NOT NULL,
    source_lang TEXT NOT NULL,
    target_lang TEXT NOT NULL,
    translation_mode TEXT NOT NULL,
    mode TEXT,
    ocr_engine TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    seen_count INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_translations_langs
ON translations(source_lang, target_lang, translation_mode);
"""




def _resolve_game_dir(app_dir: str, cfg: dict[str, Any]) -> str:
    return str(profile_data_dir(app_dir, cfg, "tm"))


def _normalize_text(text: str) -> str:
    """
    Normalize text for exact-ish cache keys.

    We keep punctuation because in games punctuation can carry meaning:
    pause, hesitation, question, interrupted phrase. We only normalize casing and
    whitespace so the same OCR text with tiny spacing differences can match.
    """
    text = str(text or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def _key(original_text: str, source_lang: str, target_lang: str, translation_mode: str) -> tuple[str, str]:
    normalized = _normalize_text(original_text)
    raw = "\u241f".join([
        normalized,
        str(source_lang or "").lower().strip(),
        str(target_lang or "").lower().strip(),
        str(translation_mode or "").lower().strip(),
    ])
    return hashlib.blake2b(raw.encode("utf-8", errors="ignore"), digest_size=16).hexdigest(), normalized


def _db_path(app_dir: str, cfg: dict[str, Any]) -> str:
    game_dir = _resolve_game_dir(app_dir, cfg or {})
    os.makedirs(game_dir, exist_ok=True)
    return os.path.join(game_dir, "translation_memory.sqlite")


def _debug_log(app_dir: str, message: str) -> None:
    """Best-effort diagnostic log. Translation memory must never break the overlay."""
    try:
        path = log_file("translation_memory_debug.log", app_dir)
        with session_logging.open_log(path, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except Exception:
        pass


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=1.0)
    conn.executescript(_SCHEMA)
    return conn


def ensure_database(app_dir: str, session_config: dict[str, Any] | None) -> str:
    """
    Create/open the translation memory database for the active game profile.

    Step16: unlike lookup/store, this is called at overlay startup so the file
    appears immediately in DATA_ROOT/user/tm/<game>/translation_memory.sqlite. This also
    makes path problems visible before the second test pass.
    """
    try:
        cfg = session_config or {}
        path = _db_path(app_dir, cfg)
        with _connect(path):
            pass
        _debug_log(app_dir, f"MEMORY_DB_READY path={path}")
        return path
    except Exception as e:
        _debug_log(app_dir, f"MEMORY_DB_ERROR error={e!r}")
        return ""


def lookup_translation(
    app_dir: str,
    session_config: dict[str, Any] | None,
    *,
    original_text: str,
    source_lang: str,
    target_lang: str,
    translation_mode: str,
) -> str:
    """Return cached translation or empty string."""
    if not persistence_allowed(session_config, translation_mode):
        return ""
    try:
        original_text = str(original_text or "").strip()
        if not original_text:
            return ""

        cfg = session_config or {}
        key_hash, _normalized = _key(original_text, source_lang, target_lang, translation_mode)
        path = _db_path(app_dir, cfg)
        now = time.strftime("%Y-%m-%d %H:%M:%S")

        with _connect(path) as conn:
            row = conn.execute(
                "SELECT translated_text FROM translations WHERE key_hash = ?",
                (key_hash,),
            ).fetchone()
            if not row:
                return ""
            conn.execute(
                "UPDATE translations SET last_seen = ?, seen_count = seen_count + 1 WHERE key_hash = ?",
                (now, key_hash),
            )
            return str(row[0] or "").strip()
    except Exception as e:
        try:
            _debug_log(app_dir, f"MEMORY_LOOKUP_ERROR error={e!r}")
        except Exception:
            pass
        return ""


def store_translation(
    app_dir: str,
    session_config: dict[str, Any] | None,
    *,
    original_text: str,
    translated_text: str,
    source_lang: str,
    target_lang: str,
    translation_mode: str,
    mode: str = "live_overlay",
    ocr_engine: str = "",
) -> None:
    """Store or update one translation memory row."""
    if not persistence_allowed(session_config, translation_mode):
        return
    try:
        original_text = str(original_text or "").strip()
        translated_text = str(translated_text or "").strip()
        if not original_text or not translated_text:
            return

        cfg = session_config or {}
        key_hash, normalized = _key(original_text, source_lang, target_lang, translation_mode)
        path = _db_path(app_dir, cfg)
        now = time.strftime("%Y-%m-%d %H:%M:%S")

        with _connect(path) as conn:
            conn.execute(
                """
                INSERT INTO translations(
                    key_hash, original_text, normalized_text, translated_text,
                    source_lang, target_lang, translation_mode, mode, ocr_engine,
                    first_seen, last_seen, seen_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(key_hash) DO UPDATE SET
                    translated_text = excluded.translated_text,
                    last_seen = excluded.last_seen,
                    seen_count = translations.seen_count + 1,
                    mode = excluded.mode,
                    ocr_engine = excluded.ocr_engine
                """,
                (
                    key_hash,
                    original_text,
                    normalized,
                    translated_text,
                    str(source_lang or ""),
                    str(target_lang or ""),
                    str(translation_mode or ""),
                    str(mode or ""),
                    str(ocr_engine or ""),
                    now,
                    now,
                ),
            )
    except Exception as e:
        try:
            _debug_log(app_dir, f"MEMORY_STORE_ERROR error={e!r}")
        except Exception:
            pass
        return
