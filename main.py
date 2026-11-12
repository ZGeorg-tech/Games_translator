import sys
sys.dont_write_bytecode = True
import os
import time
import threading
import queue

from console_output import configure_unicode_safe_console
from data_paths import DATA, data_path, log_file, initialize_data, rebase_profile_path
import session_logging


if __name__ == "__main__":
    configure_unicode_safe_console()
    initialize_data()
    session_logging.ensure_session("overlay")


_STARTUP_T0 = time.perf_counter()
_STARTUP_LAST = _STARTUP_T0
_STARTUP_TRACE_ENABLED = __name__ == "__main__"
_STARTUP_TRACE_LOCK = threading.RLock()
_STARTUP_TRACE_HANDLE = None
_STARTUP_LOG_PATH = os.path.join(
    str(data_path("logs")),
    "startup",
    f"overlay_startup_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}.log",
)


def _write_startup_trace(event: str, **details):
    """Write and immediately flush a startup marker before project imports."""
    global _STARTUP_LAST, _STARTUP_TRACE_HANDLE
    if not _STARTUP_TRACE_ENABLED:
        return
    session_logging.diagnostic_event(event, **details)
    if not session_logging.raw_enabled():
        return
    try:
        with _STARTUP_TRACE_LOCK:
            now = time.perf_counter()
            total_ms = int((now - _STARTUP_T0) * 1000)
            delta_ms = int((now - _STARTUP_LAST) * 1000)
            _STARTUP_LAST = now
            thread = threading.current_thread()
            safe_details = []
            for key, value in details.items():
                key_text = str(key)
                if any(part in key_text.lower() for part in ("text", "source", "translation", "prompt")):
                    continue
                value_text = " ".join(str(value).split())[:500]
                safe_details.append(f"{key_text}={value_text}")
            suffix = " " + " ".join(safe_details) if safe_details else ""
            line = (
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} marker={event} "
                f"elapsed_ms={total_ms} delta_ms={delta_ms} pid={os.getpid()} "
                f"thread_name={thread.name} thread_id={threading.get_ident()}{suffix}\n"
            )
            if _STARTUP_TRACE_HANDLE is None or _STARTUP_TRACE_HANDLE.closed:
                os.makedirs(os.path.dirname(_STARTUP_LOG_PATH), exist_ok=True)
                _STARTUP_TRACE_HANDLE = session_logging.open_log(
                    _STARTUP_LOG_PATH,
                    "a",
                    encoding="utf-8",
                    buffering=1,
                )
            _STARTUP_TRACE_HANDLE.write(line)
            _STARTUP_TRACE_HANDLE.flush()
    except Exception:
        pass


if _STARTUP_TRACE_ENABLED and session_logging.raw_enabled():
    os.environ["GAME_TRANSLATOR_STARTUP_TRACE"] = _STARTUP_LOG_PATH
    os.environ["GAME_TRANSLATOR_STARTUP_T0_PERF"] = repr(_STARTUP_T0)
_write_startup_trace("MAIN_PROCESS_ENTERED")
_write_startup_trace("BASE_IMPORTS_BEGIN")

# Ограничиваем внутренние пуллы потоков тяжёлых библиотек, если пользователь не задал свои значения.
# Это снижает риск, что OCR/ML забьют все ядра одновременно с игрой.
os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "4")
os.environ.setdefault("MKL_NUM_THREADS", "4")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "4")
os.environ.setdefault("PADDLE_NUM_THREADS", "4")
import json
import re
import difflib
import statistics
import math
import ctypes
import traceback
import faulthandler
import hashlib
import sqlite3
from enum import Enum
from ctypes import wintypes

from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout,
    QHBoxLayout, QPushButton, QComboBox, QSpinBox, QSizeGrip, QLayout, QSizePolicy, QCheckBox, QStyle, QDialog
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, pyqtSlot, QMutex, QMutexLocker, QWaitCondition, QTimer, QRect, QPoint, QSize
from PyQt6.QtGui import QFont, QPainter, QColor, QPen, QFontMetrics

_write_startup_trace("BASE_IMPORTS_END")
_write_startup_trace("PROJECT_IMPORTS_BEGIN")

# Импорт локальных компонентов проекта.
# Эти файлы НЕ заменяем и НЕ удаляем:
# - config.py хранит дефолтные настройки и пути;
# - ocr_engine.py выбирает OCR backend и пока делегирует в ocr_core.py;
# - translation_engine.py выбирает переводческий backend и пока делегирует в translator_core.py.
#
# Важно: импортируем их с прежними алиасами ocr_core / translator_core,
# чтобы остальной код main.py остался почти без изменений.
_write_startup_trace("PROJECT_IMPORT_BEGIN", module="config")
import config
_write_startup_trace("PROJECT_IMPORT_END", module="config")
_write_startup_trace("PROJECT_IMPORT_BEGIN", module="ocr_engine")
import ocr_engine as ocr_core
_write_startup_trace("PROJECT_IMPORT_END", module="ocr_engine")
_write_startup_trace("PROJECT_IMPORT_BEGIN", module="translation_engine")
import translation_engine as translator_core
from online_translation import persistence_allowed, publication_event
from quality_runtime_selector import log_quality_runtime_selection, quality_unavailable_message
_write_startup_trace("PROJECT_IMPORT_END", module="translation_engine")
from block2_runtime import block2_request_context, is_block2_session
from block3_runtime import block3_request_context, is_block3_session
from language_blocks import (
    BLOCK2,
    BLOCK3,
    apply_block_state,
    migrate_block1_backend,
    atomic_write_json,
    block_id_for_language_mode,
    ensure_language_blocks,
    fast_ocr_engine_for_block,
    normalize_quality_ocr_engine,
    ocr_engine_for_translation_profile,
    replace_block_state,
)
_write_startup_trace("PROJECT_IMPORT_BEGIN", module="visual_text_gate")
import visual_text_gate
_write_startup_trace("PROJECT_IMPORT_END", module="visual_text_gate")
_write_startup_trace("PROJECT_IMPORT_BEGIN", module="game_field_visual_gate")
import game_field_visual_gate
_write_startup_trace("PROJECT_IMPORT_END", module="game_field_visual_gate")
_write_startup_trace("PROJECT_IMPORT_BEGIN", module="game_background_paddle")
import game_background_paddle
_write_startup_trace("PROJECT_IMPORT_END", module="game_background_paddle")
_write_startup_trace("PROJECT_IMPORT_BEGIN", module="thread_growth_diagnostics")
import thread_growth_diagnostics
import startup_block3_diagnostics
import responsiveness_diagnostics as responsiveness
_write_startup_trace("PROJECT_IMPORT_END", module="thread_growth_diagnostics")
_write_startup_trace("PROJECT_IMPORT_BEGIN", module="translation_history")
import translation_history
_write_startup_trace("PROJECT_IMPORT_END", module="translation_history")
_write_startup_trace("PROJECT_IMPORT_BEGIN", module="translation_memory")
import translation_memory
_write_startup_trace("PROJECT_IMPORT_END", module="translation_memory")
_write_startup_trace("PROJECT_IMPORT_BEGIN", module="translation_pipeline_persistence")
import translation_pipeline_persistence
_write_startup_trace("PROJECT_IMPORT_END", module="translation_pipeline_persistence")
_write_startup_trace("PROJECT_IMPORT_BEGIN", module="ocr_benchmark_crops")
import ocr_benchmark_crops
_write_startup_trace("PROJECT_IMPORT_END", module="ocr_benchmark_crops")
_write_startup_trace("PROJECT_IMPORT_BEGIN", module="overlay_freshness")
import overlay_freshness
_write_startup_trace("PROJECT_IMPORT_END", module="overlay_freshness")
_write_startup_trace("PROJECT_IMPORT_BEGIN", module="post_ocr_classifier")
import post_ocr_classifier
_write_startup_trace("PROJECT_IMPORT_END", module="post_ocr_classifier")
_write_startup_trace("PROJECT_IMPORT_BEGIN", module="subtitle_sequence_gate")
from subtitle_sequence_gate import (
    FAST_SUBTITLES_STALE_GRACE_MS,
    FAST_SUBTITLES,
    FOLLOW_SOURCE,
    GAME_BACKGROUND,
    STATIC_PANEL,
    SubtitleSequenceGate,
    normalize_subtitle_settings,
)
_write_startup_trace("PROJECT_IMPORT_END", module="subtitle_sequence_gate")
try:
    _write_startup_trace("PROJECT_IMPORT_BEGIN", module="resource_monitor")
    import resource_monitor
    _write_startup_trace("PROJECT_IMPORT_END", module="resource_monitor")
except Exception:
    resource_monitor = None
    _write_startup_trace("PROJECT_IMPORT_ERROR", module="resource_monitor")
_write_startup_trace("PROJECT_IMPORT_BEGIN", module="localization")
from localization import tr, normalize_interface_lang
from subtitle_visuals import capture_scope, sample_background, paint_plate, SubtitleLabel
_write_startup_trace("PROJECT_IMPORT_END", module="localization")
_write_startup_trace("PROJECT_IMPORTS_END")

_TRANSLATION_PACK_SCHEMA_READY = set()
_TRANSLATION_PACK_SCHEMA_LOCK = threading.Lock()

_PADDLE_OCR_LOG_PATH = str(data_path("logs", "paddle_ocr.log"))


def _compact_log_value(value, limit=500):
    text = str(value)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def paddle_runtime_paths_snapshot():
    """Small snapshot of project-local Paddle cache/temp env used by production runtime."""
    keys = (
        "PADDLE_HOME",
        "PADDLE_PDX_CACHE_HOME",
        "MODELSCOPE_CACHE",
        "HF_HOME",
        "HUGGINGFACE_HUB_CACHE",
        "TRANSFORMERS_CACHE",
        "GAME_TRANSLATOR_PADDLE_MODEL_ROOT",
        "GAME_TRANSLATOR_PADDLE_TMP",
        "TEMP",
        "TMP",
    )
    fallback_root = str(data_path("paddle.models"))
    values = {key: os.environ.get(key, "") for key in keys}
    values.setdefault("GAME_TRANSLATOR_PADDLE_MODEL_ROOT", fallback_root)
    return values


def log_paddle_ocr_event(event: str, **details):
    session_logging.diagnostic_event(event, **details)
    if not session_logging.raw_enabled():
        return
    try:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        os.makedirs(os.path.dirname(_PADDLE_OCR_LOG_PATH), exist_ok=True)
        with session_logging.open_log(_PADDLE_OCR_LOG_PATH, "a", encoding="utf-8") as f:
            f.write("=" * 70 + "\n")
            f.write(f"TIME: {timestamp}\n")
            f.write(f"EVENT: {event}\n")
            for key, value in details.items():
                f.write(f"{key}: {_compact_log_value(value)}\n")
            f.write("=" * 70 + "\n\n")
    except Exception:
        pass


def current_thread_diagnostic():
    """Return compact thread context for freeze diagnostics."""
    try:
        app = QApplication.instance()
        is_ui_thread = bool(app and QThread.currentThread() == app.thread())
    except Exception:
        is_ui_thread = False
    try:
        thread_name = threading.current_thread().name
    except Exception:
        thread_name = "unknown"
    return f"thread={thread_name}; ui_thread={is_ui_thread}"

def log_startup_event(event: str, **details):
    """Пишет компактные этапы запуска без спама в рабочем OCR-цикле."""
    _write_startup_trace(event, **details)


def write_overlay_ready_marker():
    marker_path = str(os.environ.get("GAME_TRANSLATOR_OVERLAY_READY_MARKER", "") or "").strip()
    if not marker_path:
        return
    try:
        marker_abs = str(DATA.validate(marker_path))
        marker_dir = os.path.dirname(marker_abs)
        if marker_dir:
            os.makedirs(marker_dir, exist_ok=True)
        with open(marker_abs, "w", encoding="utf-8") as f:
            f.write(f"ready_at={time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"pid={os.getpid()}\n")
        log_startup_event("OVERLAY_READY_MARKER_WRITTEN", marker=marker_abs, pid=os.getpid())
    except Exception as exc:
        log_startup_event("OVERLAY_READY_MARKER_ERROR", marker=marker_path, error=f"{type(exc).__name__}: {exc}")


def ensure_translation_pack_schema(app_dir: str, session_config: dict) -> None:
    """Расширяет текущую SQLite-память полями для будущих пакетов переводов.

    Рабочий lookup/store остаётся совместимым со старой таблицей translations.
    Новые поля нужны для будущего экспорта/импорта: base, strict, literary,
    direct/VLM-режимы, пользовательские исправления и shareable-паки.
    """
    try:
        db_path = translation_memory._db_path(app_dir, session_config or {})
        if not os.path.exists(db_path):
            return
        schema_key = os.path.normcase(os.path.abspath(db_path))
        with _TRANSLATION_PACK_SCHEMA_LOCK:
            if schema_key in _TRANSLATION_PACK_SCHEMA_READY:
                return

        con = sqlite3.connect(db_path, timeout=3.0)
        try:
            cur = con.cursor()
            existing = {row[1] for row in cur.execute("PRAGMA table_info(translations)").fetchall()}
            additions = {
                "quality_mode": "TEXT DEFAULT 'base'",
                "pipeline_mode": "TEXT",
                "translator_backend": "TEXT",
                "llm_backend": "TEXT",
                "pack_id": "TEXT",
                "quality_flags": "TEXT",
                "user_corrected": "INTEGER NOT NULL DEFAULT 0",
                "shareable": "INTEGER NOT NULL DEFAULT 1",
            }
            for column, ddl in additions.items():
                if column not in existing:
                    cur.execute(f"ALTER TABLE translations ADD COLUMN {column} {ddl}")

            cur.execute("""
                UPDATE translations
                SET quality_mode = CASE
                    WHEN translation_mode LIKE '%+llm:strict:%' THEN 'strict'
                    WHEN translation_mode LIKE '%+llm:literary:%' THEN 'literary'
                    WHEN translation_mode LIKE '%+llm:direct:%' THEN 'llm_direct'
                    ELSE COALESCE(NULLIF(quality_mode, ''), 'base')
                END
            """)
            cur.execute("UPDATE translations SET pipeline_mode = COALESCE(pipeline_mode, mode)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_translations_pack_lookup ON translations(source_lang, target_lang, quality_mode, translation_mode)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_translations_shareable ON translations(shareable, user_corrected, quality_mode)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS translation_pack_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            cur.execute(
                "INSERT OR REPLACE INTO translation_pack_meta(key, value) VALUES(?, ?)",
                ("schema_version", "2")
            )
            cur.execute(
                "INSERT OR REPLACE INTO translation_pack_meta(key, value) VALUES(?, ?)",
                ("pack_strategy", "single_sqlite_with_quality_modes")
            )
            con.commit()
            with _TRANSLATION_PACK_SCHEMA_LOCK:
                _TRANSLATION_PACK_SCHEMA_READY.add(schema_key)
        finally:
            con.close()
    except Exception as exc:
        try:
            with session_logging.open_log(log_file("translation_memory_debug.log", app_dir), "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} PACK_SCHEMA_WARNING {type(exc).__name__}: {exc}\n")
        except Exception:
            pass



def mark_translation_memory_metadata(
    app_dir: str,
    session_config: dict,
    original_text: str,
    source_lang: str,
    target_lang: str,
    translation_mode: str,
    quality_mode: str,
    pipeline_mode: str,
    translator_backend: str = "",
    llm_backend: str = "",
) -> None:
    """Обновляет служебные поля memory-записи после store_translation().

    translation_memory.py пока сохраняет основной текст и ключ. Дополнительные поля
    нужны для будущего экспорта/импорта translation packs и разделения base/LLM-режимов.
    """
    if not persistence_allowed(session_config, translation_mode):
        return
    try:
        db_path = translation_memory._db_path(app_dir, session_config or {})
        if not os.path.exists(db_path):
            return

        ensure_translation_pack_schema(app_dir, session_config)
        con = sqlite3.connect(db_path, timeout=3.0)
        try:
            cur = con.cursor()
            cur.execute(
                """
                UPDATE translations
                SET quality_mode = ?,
                    pipeline_mode = ?,
                    translator_backend = COALESCE(NULLIF(?, ''), translator_backend),
                    llm_backend = COALESCE(NULLIF(?, ''), llm_backend)
                WHERE original_text = ?
                  AND source_lang = ?
                  AND target_lang = ?
                  AND translation_mode = ?
                """,
                (
                    quality_mode,
                    pipeline_mode,
                    translator_backend,
                    llm_backend,
                    original_text,
                    source_lang,
                    target_lang,
                    translation_mode,
                )
            )
            con.commit()
        finally:
            con.close()
    except Exception as exc:
        try:
            with session_logging.open_log(log_file("translation_memory_debug.log", app_dir), "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} PACK_METADATA_WARNING {type(exc).__name__}: {exc}\n")
        except Exception:
            pass


# Импорт Rust-модуля.
# Если .pyd не собран, не найден, несовместим с Python или ОС,
# переменная game_capture_core останется None, а движок автоматически уйдёт в GDI fallback.
try:
    import game_capture_core
except ImportError:
    game_capture_core = None

# Подключение Win32 API.
# user32/kernel32 нужны для хоткея, прозрачности окна, фокуса игры и PrintWindow.
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# pywin32 может отсутствовать или быть не установлен.
# В нормальной Windows-сборке он нужен для поиска окон и GDI-захвата.
win32gui = None
try:
    import win32gui
except ImportError:
    pass

# Константы для Windows API.
GWL_EXSTYLE = -20
GWLP_HWNDPARENT = -8
GW_OWNER = 4
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
CURSOR_SHOWING = 0x00000001
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


class CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hCursor", wintypes.HANDLE),
        ("ptScreenPos", wintypes.POINT),
    ]

# SetWindowPos/ShowWindow constants for harder activation of temporary selection layer.
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_NOOWNERZORDER = 0x0200
SW_SHOW = 5

# Ctrl + Alt + Shift + P.
#
# В v4 мы НЕ используем RegisterHotKey/nativeEvent.
# Причина: nativeEvent уже дал нативный краш 0xC000041D,
# а RegisterHotKey + PeekMessage не сработал стабильно в PyQt.
# Поэтому используем безопасный polling через GetAsyncKeyState():
# приложение каждые 50 мс спрашивает Windows, нажаты ли Ctrl+Alt+Shift+P.
# Это не резервирует хоткей в системе, зато не требует WinAPI callback и не валит процесс.
VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_MENU = 0x12  # Alt в WinAPI называется MENU
VK_LWIN = 0x5B
VK_RWIN = 0x5C
KEY_DOWN_MASK = 0x8000

# Горячие клавиши по умолчанию.
# Лаунчер может переопределить их через session_config["hotkeys"].
DEFAULT_HOTKEYS = {
    "toggle_translation_mode": {
        "display": "Ctrl+Alt+Shift+P",
        "keys": ["CTRL", "ALT", "SHIFT", "P"],
    },
    "toggle_translation_visibility": {
        "display": "Ctrl+Alt+Shift+H",
        "keys": ["CTRL", "ALT", "SHIFT", "H"],
    },
    "dynamic_area_translation": {
        "display": "Ctrl+Alt+Shift+D",
        "keys": ["CTRL", "ALT", "SHIFT", "D"],
    },
    "force_refresh": {
        "display": "Ctrl+Alt+Shift+R",
        "keys": ["CTRL", "ALT", "SHIFT", "R"],
    },
}

HOTKEY_ACTION_TITLES = {
    "toggle_translation_mode": "перевод/настройка",
    "toggle_translation_visibility": "скрыть перевод",
    "dynamic_area_translation": "динамический перевод",
    "force_refresh": "принудительно обновить перевод",
}

DEFAULT_DYNAMIC_SETTINGS = {
    "enabled": True,
    "display_time_sec": 6,
    "output_mode": "near",  # near / over
    # combined  — динамика как дополнение к обычному субтитровому оверлею;
    # standalone — отдельный динамический режим без постоянной рамки субтитров.
    "mode": "combined",
    # В combined-режиме основной перевод можно прятать на время выделения
    # динамической области, чтобы он не перекрывал оригинальный текст.
    # Пользователь может отключить это поведение в лаунчере.
    "hide_main_overlay": True,
    # Небольшой внутренний запас вокруг выделенной области.
    # Помогает, если пользователь выделил область слишком близко к буквам.
    # В интерфейс пока не выносим: это безопасная внутренняя страховка.
    "capture_padding_px": 8,
    # Auto-fit for the dynamic result popup.
    # First enlarge the popup up to max_rect_scale, then reduce font down to min_font_size.
    "auto_fit_enabled": True,
    "max_rect_scale": 2.0,
    "min_font_size": 12,
}

# Launcher already constrains the preferred Live Overlay font to 12..48.
# Reuse that established UI minimum without coupling Live state to Dynamic settings.
LIVE_OVERLAY_MIN_FONT_SIZE = 12


def wrapped_text_fits(text, font_name, font_size, width, height, horizontal_padding=0, vertical_padding=0):
    """Return whether wrapped text fits the supplied content rectangle."""
    try:
        font = QFont(str(font_name or "Arial"), int(font_size), QFont.Weight.Bold)
        metrics = QFontMetrics(font)
        content_w = max(1, int(width) - max(0, int(horizontal_padding)))
        content_h = max(1, int(height) - max(0, int(vertical_padding)))
        rect = metrics.boundingRect(
            QRect(0, 0, content_w, max(content_h, 2000)),
            int(Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextExpandTabs),
            str(text or ""),
        )
        return rect.width() <= content_w and rect.height() <= content_h
    except Exception:
        return True


def normalize_dynamic_settings(raw_settings):
    """Мягкая валидация настроек динамического перевода из session_config/profile."""
    result = dict(DEFAULT_DYNAMIC_SETTINGS)
    if not isinstance(raw_settings, dict):
        return result
    result["enabled"] = bool(raw_settings.get("enabled", result["enabled"]))
    result["hide_main_overlay"] = bool(raw_settings.get("hide_main_overlay", result.get("hide_main_overlay", True)))
    try:
        result["display_time_sec"] = max(1, min(30, int(raw_settings.get("display_time_sec", result["display_time_sec"]))))
    except Exception:
        pass
    try:
        result["capture_padding_px"] = max(0, min(30, int(raw_settings.get("capture_padding_px", result["capture_padding_px"]))))
    except Exception:
        pass

    result["auto_fit_enabled"] = bool(raw_settings.get("auto_fit_enabled", result.get("auto_fit_enabled", True)))
    try:
        result["max_rect_scale"] = max(1.0, min(3.0, float(raw_settings.get("max_rect_scale", result.get("max_rect_scale", 2.0)))))
    except Exception:
        result["max_rect_scale"] = 2.0
    try:
        result["min_font_size"] = max(6, min(32, int(raw_settings.get("min_font_size", result.get("min_font_size", 12)))))
    except Exception:
        result["min_font_size"] = 12

    output_mode = str(raw_settings.get("output_mode", result["output_mode"])).strip().lower()
    if output_mode in ("near", "over"):
        result["output_mode"] = output_mode

    mode = str(raw_settings.get("mode", result["mode"])).strip().lower()
    if mode in ("combined", "standalone"):
        result["mode"] = mode

    return result

DEFAULT_OCR_SETTINGS = {
    # tesseract — быстрый/совместимый режим;
    # paddle — текущий базовый качественный PaddleOCR-режим.
    # Paddle fast был проверен в step24 v1 и временно скрыт:
    # в live-тесте он оказался медленнее и хуже по качеству.
    "engine": "tesseract",
}


def normalize_ocr_settings(raw_settings):
    """Мягкая валидация настроек OCR из session_config/profile."""
    result = dict(DEFAULT_OCR_SETTINGS)
    if not isinstance(raw_settings, dict):
        return result
    engine = str(raw_settings.get("engine", result["engine"]) or "tesseract").strip().lower()
    if engine in {"paddle-fast", "paddlefast"}:
        engine = "paddle_fast"
    if engine in {"paddle-block2-fast", "paddle_block2", "paddlev6small"}:
        engine = "paddle_block2_fast"
    if engine in ("tesseract", "paddle_fast", "paddle_block2_fast", "paddle"):
        result["engine"] = engine
    if "advanced_override" in raw_settings:
        result["advanced_override"] = bool(raw_settings.get("advanced_override"))
    if "quality_engine" in raw_settings:
        result["quality_engine"] = str(raw_settings.get("quality_engine") or "").strip().lower()
    return result


def translation_dispatch_sentences(text, *, whole_text_enabled=False, post_ocr_policy="latin_en"):
    """Разделяет source для backend, сохраняя старый English minimum-length gate."""
    source_text = str(text or "").strip()
    if not source_text:
        return []
    sentences = [source_text] if whole_text_enabled else re.split(r'(?<=[?.!])\s+', source_text)
    asian_source = str(post_ocr_policy or "latin_en") in {"chinese", "japanese"}
    return [
        sentence.strip()
        for sentence in sentences
        if sentence.strip()
        and (
            asian_source
            or sum(1 for char in sentence.strip() if char.isalpha()) >= 4
        )
    ]


QUALITY_OCR_BACKENDS = {"paddle", "paddle_fast", "paddle_block2_fast"}
LIVE_PERSISTENT_PADDLE_BACKENDS = {"paddle", "paddle_fast"}


def is_paddle_family_backend(name):
    """True for Paddle OCR presets: paddle, paddle_fast, future paddle_* backends."""
    helper = getattr(ocr_core, "is_paddle_family_backend", None)
    if callable(helper):
        return bool(helper(name))
    value = str(name or "").strip().lower().replace("-", "_")
    return value == "paddle" or value.startswith("paddle_")


def uses_persistent_live_paddle_worker(name):
    """Block 1/3 English Paddle presets share one session-owned Live worker."""
    return str(name or "").strip().lower().replace("-", "_") in LIVE_PERSISTENT_PADDLE_BACKENDS


def is_quality_ocr_backend_name(name):
    """True for heavier/quality OCR backends that need visual-gate and task throttling."""
    return is_paddle_family_backend(name)


def current_ocr_backend_from_session(session_data):
    """Возвращает выбранный OCR backend из session_config."""
    return normalize_ocr_settings((session_data or {}).get("ocr", {})).get("engine", "tesseract")


def current_translation_backend_from_session(session_data):
    """Возвращает выбранный production translation backend из session_config/config."""
    try:
        if ((session_data or {}).get("translation") or {}).get("mode") == "online":
            return "online"
        raw = ((session_data or {}).get("translation", {}) or {}).get("backend", "")
        backend = str(migrate_block1_backend(raw) or "").strip().lower()
        if backend == "current":
            backend = "current_base"
        if backend in {
            "bergamot",
            "current_base",
            "marian",
            "translategemma_llama_q6_low_vram",
            "translategemma_llama_q6_speed",
            "block2_router",
            "block3_router",
        }:
            return backend
    except Exception:
        pass
    try:
        return translator_core.get_default_backend_name()
    except Exception:
        return "current_base"


def translation_request_context_for_session(session_data, source_text="", runtime_mode="overlay"):
    translation = (session_data or {}).get("translation") or {}
    if translation.get("mode") == "online":
        from online_translation import normalize_settings, request_source
        source = request_source(translation, source_text,
                                (session_data or {}).get("profile_id") or (session_data or {}).get("profile_path", "session"))
        return {"online": normalize_settings(translation.get("online")), "source_language": source,
                "target_language": translation.get("target_lang", "uk"), "runtime_mode": runtime_mode}
    if is_block2_session(session_data):
        return block2_request_context(
            session_data,
            source_text=source_text,
            runtime_mode=runtime_mode,
        )
    if is_block3_session(session_data):
        return block3_request_context(
            session_data,
            source_text=source_text,
            runtime_mode=runtime_mode,
        )
    return {}


def block3_translator_readiness_requirement(session_data):
    """Return the actual Block 3 translator required by the selected route."""
    context = block3_request_context(session_data, runtime_mode="overlay")
    if ((session_data or {}).get("translation") or {}).get("mode") == "online":
        context = {}
    if not context:
        return {
            "applies": False,
            "block": 0,
            "mode": "",
            "target": "",
            "translator_backend": "",
            "translator_readiness_key": "",
        }

    mode = str(context.get("translation_profile") or "fast")
    target = str(context.get("target_language") or "ja")
    if mode == "quality":
        translator_backend = "milmmt"
        readiness_key = "block3:quality:milmmt"
    elif target == "ja":
        translator_backend = "fugu"
        readiness_key = "block3:fast:fugu"
    else:
        # ZH and ZH-Hant share CSANMT. OpenCC remains a lightweight
        # route-local conversion and is not a separate readiness blocker.
        translator_backend = "csanmt"
        readiness_key = "block3:fast:csanmt"
    return {
        "applies": True,
        "block": 3,
        "mode": mode,
        "target": target,
        "translator_backend": translator_backend,
        "translator_readiness_key": readiness_key,
    }


def block3_pipeline_readiness_snapshot(
    session_data,
    *,
    paddle_prepare_state="not_needed",
    paddle_prepare_backend="",
    translator_ready_keys=None,
):
    """Combine existing OCR and translator lifecycle states for Block 3 Live."""
    requirement = block3_translator_readiness_requirement(session_data)
    if not requirement["applies"]:
        return {
            **requirement,
            "ocr_backend": current_ocr_backend_from_session(session_data),
            "ocr_ready": True,
            "translator_ready": True,
            "pipeline_ready": True,
        }

    ocr_backend = current_ocr_backend_from_session(session_data)
    ocr_ready = bool(
        not is_paddle_family_backend(ocr_backend)
        or (
            str(paddle_prepare_state or "") == "ready"
            and str(paddle_prepare_backend or "") == ocr_backend
        )
    )
    ready_keys = set(translator_ready_keys or ())
    translator_ready = requirement["translator_readiness_key"] in ready_keys
    return {
        **requirement,
        "ocr_backend": ocr_backend,
        "ocr_ready": ocr_ready,
        "translator_ready": translator_ready,
        "pipeline_ready": bool(ocr_ready and translator_ready),
    }


def base_translation_cache_mode_for_session(
    session_data,
    use_online,
    source_text="",
    runtime_mode="overlay",
):
    """Разделяет translation memory по реальному offline backend."""
    if use_online:
        context = translation_request_context_for_session(session_data, source_text, runtime_mode)
        key = translator_core.get_backend_memory_key("online", request_context=context,
                                                     target_lang=context["target_language"])
        return key, key, "online"
    backend_name = current_translation_backend_from_session(session_data)
    try:
        translation_settings = ((session_data or {}).get("translation", {}) or {})
        target_lang = str(translation_settings.get("target_lang") or "").lower().strip()
        request_context = translation_request_context_for_session(
            session_data,
            source_text=source_text,
            runtime_mode=runtime_mode,
        )
        backend_key = translator_core.get_backend_memory_key(
            backend_name,
            target_lang=target_lang,
            request_context=request_context,
        )
    except Exception:
        backend_key = backend_name or "current_base"
    backend_key = str(backend_key or "current_base").strip().lower()
    return f"offline:{backend_key}", backend_key, backend_name


def refresh_base_translation_cache_mode_from_last(current_mode, current_key, current_backend_name, use_online):
    """Если dispatcher включил fallback, переносит запись memory на фактический backend."""
    if use_online:
        return current_mode, current_key, current_backend_name
    try:
        actual_key = translator_core.get_last_translation_backend_memory_key(current_key)
        actual_name = translator_core.get_last_translation_backend_name(current_backend_name)
    except Exception:
        return current_mode, current_key, current_backend_name
    actual_key = str(actual_key or current_key or "current_base").strip().lower()
    actual_name = str(actual_name or current_backend_name or actual_key).strip().lower()
    if actual_key and actual_key != current_key:
        return f"offline:{actual_key}", actual_key, actual_name
    return current_mode, current_key, current_backend_name


def clear_global_override_cursor():
    """Сбрасывает зависший глобальный override-cursor Qt, если динамический слой не закрылся идеально."""
    try:
        guard = 0
        while QApplication.overrideCursor() is not None and guard < 8:
            QApplication.restoreOverrideCursor()
            guard += 1
    except Exception:
        pass

# Поддерживаемые клавиши для polling через GetAsyncKeyState.
# Если позже добавим мышь Mouse4/Mouse5, это будет отдельный слой проверки.
VK_CODE_BY_NAME = {
    "CTRL": VK_CONTROL,
    "CONTROL": VK_CONTROL,
    "SHIFT": VK_SHIFT,
    "ALT": VK_MENU,
    "MENU": VK_MENU,
    "ESC": 0x1B,
    "SPACE": 0x20,
    "TAB": 0x09,
    "ENTER": 0x0D,
    "BACKSPACE": 0x08,
    "INSERT": 0x2D,
    "DELETE": 0x2E,
    "HOME": 0x24,
    "END": 0x23,
    "PAGEUP": 0x21,
    "PAGEDOWN": 0x22,
    "UP": 0x26,
    "DOWN": 0x28,
    "LEFT": 0x25,
    "RIGHT": 0x27,
    "PAUSE": 0x13,
}
for _ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    VK_CODE_BY_NAME[_ch] = ord(_ch)
for _ch in "0123456789":
    VK_CODE_BY_NAME[_ch] = ord(_ch)
for _i in range(1, 13):
    VK_CODE_BY_NAME[f"F{_i}"] = 0x70 + (_i - 1)


class HotkeyPollingState:
    MODIFIERS = {"CTRL", "CONTROL", "ALT", "MENU", "SHIFT", "WIN"}

    def __init__(
        self,
        actions,
        *,
        modifier_grace_ms=120.0,
        release_confirm_ms=80.0,
        mode_toggle_cooldown_ms=500.0,
        action_cooldown_ms=300.0,
    ):
        self.modifier_grace_ms = float(modifier_grace_ms)
        self.release_confirm_ms = float(release_confirm_ms)
        self.mode_toggle_cooldown_ms = float(mode_toggle_cooldown_ms)
        self.action_cooldown_ms = float(action_cooldown_ms)
        self.modifier_last_down = {}
        self.actions = {
            str(action): {
                "phase": "released",
                "release_since": 0.0,
                "last_action": 0.0,
            }
            for action in actions
        }

    @staticmethod
    def _canonical_modifier(name):
        value = str(name or "").upper()
        return {"CONTROL": "CTRL", "MENU": "ALT"}.get(value, value)

    def ensure_action(self, action):
        return self.actions.setdefault(
            str(action),
            {"phase": "released", "release_since": 0.0, "last_action": 0.0},
        )

    def poll(self, action, keys, key_states, *, now_monotonic, dynamic_active=False):
        now = float(now_monotonic)
        normalized_keys = [str(key or "").upper() for key in keys if str(key or "").strip()]
        if not normalized_keys:
            return {"result": "none"}
        base_candidates = [key for key in normalized_keys if key not in self.MODIFIERS]
        base_key = base_candidates[-1] if base_candidates else normalized_keys[-1]
        modifier_names = [
            self._canonical_modifier(key)
            for key in normalized_keys
            if key != base_key and key in self.MODIFIERS
        ]
        state = self.ensure_action(action)

        modifier_states = {}
        modifier_grace_used = False
        for modifier in modifier_names:
            raw = key_states.get(modifier, {})
            down = bool(raw.get("down"))
            if down:
                self.modifier_last_down[modifier] = now
            last_down = float(self.modifier_last_down.get(modifier, 0.0) or 0.0)
            grace_active = bool(
                not down
                and last_down > 0.0
                and (now - last_down) * 1000.0 <= self.modifier_grace_ms
            )
            modifier_states[modifier] = {
                "down": down,
                "grace_active": grace_active,
            }
            modifier_grace_used = modifier_grace_used or grace_active

        base_state = key_states.get(base_key, {})
        base_down = bool(base_state.get("down"))
        base_pressed = bool(base_state.get("pressed_since_last_poll"))
        modifiers_active = all(
            item["down"] or item["grace_active"]
            for item in modifier_states.values()
        )
        current_down = bool(base_down and modifiers_active)
        edge = bool(modifiers_active and (base_down or base_pressed))
        all_keys_released = bool(
            not base_down
            and all(not item["down"] for item in modifier_states.values())
        )

        if state["phase"] == "pressed" and not current_down:
            state["phase"] = "waiting_for_release"
            state["release_since"] = now if all_keys_released else 0.0
        elif state["phase"] == "waiting_for_release":
            if all_keys_released:
                if state["release_since"] <= 0.0:
                    state["release_since"] = now
                if (now - state["release_since"]) * 1000.0 >= self.release_confirm_ms:
                    state["phase"] = "released"
                    state["release_since"] = 0.0
                    return {
                        "result": "rearmed",
                        "action": str(action),
                        "base_key": base_key,
                    }
            else:
                state["release_since"] = 0.0
            return {"result": "none"}

        if state["phase"] != "released" or not edge:
            return {"result": "none"}

        state["phase"] = "pressed" if current_down else "waiting_for_release"
        state["release_since"] = now if all_keys_released else 0.0
        cooldown_ms = (
            self.mode_toggle_cooldown_ms
            if str(action) == "toggle_translation_mode"
            else self.action_cooldown_ms
        )
        elapsed_ms = (
            max(0.0, now - float(state["last_action"])) * 1000.0
            if state["last_action"] > 0.0
            else float("inf")
        )
        cooldown_remaining_ms = max(0.0, cooldown_ms - elapsed_ms)
        if cooldown_remaining_ms > 0.0:
            result = "ignored"
            ignore_reason = (
                "mode_toggle_cooldown"
                if str(action) == "toggle_translation_mode"
                else "action_cooldown"
            )
        else:
            result = "action"
            ignore_reason = ""
            state["last_action"] = now
        return {
            "result": result,
            "action": str(action),
            "base_key": base_key,
            "current_down": current_down,
            "pressed_since_last_poll": base_pressed,
            "modifier_states": modifier_states,
            "modifier_grace_used": modifier_grace_used,
            "cooldown_remaining_ms": cooldown_remaining_ms,
            "ignore_reason": ignore_reason,
        }

# Используется при возврате фокуса в окно игры.
SW_RESTORE = 9

# Абсолютные пути. Это снижает риск, что профиль или crash_log создастся
# в другой папке, если приложение запущено из ярлыка или из IDE.
APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILES_DIR = str(data_path("profiles"))



def copy_default_hotkeys():
    """Глубокая копия дефолтных hotkeys без copy.deepcopy."""
    return {
        action: {
            "display": data["display"],
            "keys": list(data["keys"]),
        }
        for action, data in DEFAULT_HOTKEYS.items()
    }


def normalize_hotkeys(raw_hotkeys):
    """
    Мягкая валидация hotkeys из session_config.

    Старые профили без hotkeys не ломаются: получат дефолтные сочетания.
    Если в профиле есть только часть hotkeys, недостающие действия тоже берутся из дефолта.
    """
    result = copy_default_hotkeys()
    if not isinstance(raw_hotkeys, dict):
        return result

    for action, default_data in DEFAULT_HOTKEYS.items():
        item = raw_hotkeys.get(action)
        if not isinstance(item, dict):
            continue
        keys = item.get("keys")
        display = item.get("display")
        if isinstance(keys, list) and keys:
            clean_keys = [str(k).upper() for k in keys if str(k).strip()]
            if clean_keys:
                result[action] = {
                    "display": str(display) if display else "+".join(clean_keys),
                    "keys": clean_keys,
                }

    return result


# --- ЯВНЫЕ СИГНАТУРЫ WIN32 API ---
# ctypes без argtypes/restype сам предполагает 32-битные int-аргументы.
# На 64-битной Windows HWND и некоторые указатели имеют размер 64 бита.
# Неправильные сигнатуры WinAPI редко дают понятный Python Traceback;
# иногда они приводят к нативному падению процесса. Поэтому сигнатуры задаём явно.
def configure_winapi_signatures():
    try:
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
        user32.RegisterHotKey.restype = wintypes.BOOL

        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.UnregisterHotKey.restype = wintypes.BOOL

        user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
        user32.PeekMessageW.restype = wintypes.BOOL

        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long

        user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        user32.SetWindowLongW.restype = ctypes.c_long

        user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t

        user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t

        user32.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
        user32.GetWindow.restype = wintypes.HWND

        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND

        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL

        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL

        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = wintypes.HWND

        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD

        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.restype = ctypes.c_int

        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int

        # Для динамического выделения: принудительно захватываем мышь окном выбора,
        # чтобы игра не получала клики/движение одновременно с нашим слоем.
        user32.SetCapture.argtypes = [wintypes.HWND]
        user32.SetCapture.restype = wintypes.HWND

        user32.GetCapture.argtypes = []
        user32.GetCapture.restype = wintypes.HWND

        user32.ReleaseCapture.argtypes = []
        user32.ReleaseCapture.restype = wintypes.BOOL

        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user32.GetCursorPos.restype = wintypes.BOOL

        user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        user32.SetCursorPos.restype = wintypes.BOOL

        user32.GetClipCursor.argtypes = [ctypes.POINTER(wintypes.RECT)]
        user32.GetClipCursor.restype = wintypes.BOOL

        user32.ClipCursor.argtypes = [ctypes.POINTER(wintypes.RECT)]
        user32.ClipCursor.restype = wintypes.BOOL

        user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
        user32.GetCursorInfo.restype = wintypes.BOOL

        user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetClientRect.restype = wintypes.BOOL

        user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        user32.ClientToScreen.restype = wintypes.BOOL

        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindow.restype = wintypes.BOOL

        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetSystemMetrics.restype = ctypes.c_int

        user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.c_uint]
        user32.PrintWindow.restype = wintypes.BOOL

        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = ctypes.c_short

        kernel32.GetLastError.argtypes = []
        kernel32.GetLastError.restype = ctypes.c_ulong
    except Exception as e:
        # Если какая-то сигнатура не установилась, приложение не должно падать на импорте.
        print(f"[WINAPI SIGNATURE WARNING]: {e}", flush=True)


def paddle_start_to_start_remaining_ms(
    last_start_monotonic,
    now_monotonic,
    throttle_ms=3200.0,
):
    """Return the remaining start-to-start delay using only a monotonic clock."""
    try:
        last_start = float(last_start_monotonic or 0.0)
        now_value = float(now_monotonic)
        target_ms = max(0.0, float(throttle_ms))
    except (TypeError, ValueError):
        return 0.0
    if last_start <= 0.0:
        return 0.0
    elapsed_ms = max(0.0, now_value - last_start) * 1000.0
    return max(0.0, target_ms - elapsed_ms)


def paddle_live_scheduler_decision(
    *,
    last_start_monotonic,
    now_monotonic,
    throttle_ms=3200.0,
    visual_stable_event=False,
    backend_busy=False,
    paused=False,
    stopping=False,
    region_valid=True,
):
    """Pure scheduling decision shared by production and deterministic replay."""
    if stopping:
        return {"allowed": False, "reason": "session_stopping", "remaining_ms": 0.0}
    if paused:
        return {"allowed": False, "reason": "live_ocr_paused", "remaining_ms": 0.0}
    if not region_valid:
        return {"allowed": False, "reason": "invalid_region", "remaining_ms": 0.0}
    if backend_busy:
        return {
            "allowed": False,
            "reason": "paddle_backend_busy",
            "scheduler_class": "backend_busy",
            "remaining_ms": 0.0,
        }
    remaining_ms = paddle_start_to_start_remaining_ms(
        last_start_monotonic,
        now_monotonic,
        throttle_ms,
    )
    if visual_stable_event:
        return {
            "allowed": True,
            "reason": "visual_stable_immediate",
            "scheduler_class": "visual_stable_immediate",
            "remaining_ms": remaining_ms,
            "bypassed_throttle": remaining_ms > 0.0,
        }
    if remaining_ms > 0.0:
        return {
            "allowed": False,
            "reason": "start_to_start_throttle",
            "scheduler_class": "fallback_throttled",
            "remaining_ms": remaining_ms,
        }
    return {
        "allowed": True,
        "reason": "ready",
        "scheduler_class": "fallback_ready",
        "remaining_ms": 0.0,
    }


def window_focus_restore_decision(
    *,
    game_was_foreground,
    target_valid,
    session_stopping,
    current_foreground,
    target_hwnd,
):
    """Pure focus-ownership decision shared by production and replay."""
    target_hwnd = int(target_hwnd or 0)
    current_foreground = int(current_foreground or 0)
    if session_stopping:
        return {"restore": False, "reason": "session_stopping"}
    if not game_was_foreground:
        return {"restore": False, "reason": "game_was_not_foreground"}
    if not target_hwnd or not target_valid:
        return {"restore": False, "reason": "target_invalid"}
    if current_foreground == target_hwnd:
        return {"restore": False, "reason": "already_foreground"}
    return {"restore": True, "reason": "restore_saved_game_foreground"}


class LiveOverlayMouseMode(Enum):
    EDIT = "edit"
    GAMEPLAY = "gameplay"


def classify_cursor_clip(clip_rect, virtual_rect, game_rect, desktop_tolerance=1, game_tolerance=3):
    """Classify a Win32 cursor clip without treating a fullscreen desktop as a game clip."""
    clip = [int(value) for value in clip_rect] if clip_rect and len(clip_rect) == 4 else None
    virtual = [int(value) for value in virtual_rect] if virtual_rect and len(virtual_rect) == 4 else None
    game = [int(value) for value in game_rect] if game_rect and len(game_rect) == 4 else None
    desktop_delta = [abs(clip[index] - virtual[index]) for index in range(4)] if clip and virtual else None
    game_delta = [abs(clip[index] - game[index]) for index in range(4)] if clip and game else None

    if clip and virtual and all(delta <= int(desktop_tolerance) for delta in desktop_delta):
        classification = "desktop_unrestricted"
        matches_game = False
        looks_unrestricted = True
    elif clip and game and all(delta <= int(game_tolerance) for delta in game_delta):
        classification = "game_confinement"
        matches_game = True
        looks_unrestricted = False
    elif clip:
        classification = "other_restricted"
        matches_game = False
        looks_unrestricted = False
    else:
        classification = "unavailable"
        matches_game = None
        looks_unrestricted = None

    return {
        "clip_classification": classification,
        "clip_matches_game": matches_game,
        "clip_looks_unrestricted": looks_unrestricted,
        "desktop_edge_delta": desktop_delta,
        "game_edge_delta": game_delta,
        "desktop_tolerance": int(desktop_tolerance),
        "game_tolerance": int(game_tolerance),
    }


def live_game_clip_from_snapshot(snapshot, *, target_valid, session_stopping):
    """Return a confirmed game clip that may be released for one EDIT session."""
    snapshot = dict(snapshot or {})
    target_hwnd = int(snapshot.get("target_hwnd") or 0)
    foreground_hwnd = int(snapshot.get("foreground_hwnd") or 0)
    clip_rect = snapshot.get("clip_rect")
    if (
        session_stopping
        or not target_valid
        or not target_hwnd
        or foreground_hwnd != target_hwnd
        or snapshot.get("clip_classification") != "game_confinement"
        or snapshot.get("clip_looks_unrestricted")
        or not clip_rect
    ):
        return None
    return [int(value) for value in clip_rect]


def live_game_clip_restore_decision(
    *,
    previous_mode,
    new_mode,
    saved_clip,
    target_valid,
    session_stopping,
    foreground_matches_target,
    virtual_rect,
    game_rect,
):
    """Decide whether the saved EDIT clip may be restored during handoff."""
    if previous_mode != LiveOverlayMouseMode.EDIT or new_mode != LiveOverlayMouseMode.GAMEPLAY:
        return {"restore": False, "reason": "unexpected_transition"}
    if session_stopping:
        return {"restore": False, "reason": "session_stopping"}
    if not target_valid:
        return {"restore": False, "reason": "target_invalid"}
    if not foreground_matches_target:
        return {"restore": False, "reason": "foreground_restore_failed"}
    classification = classify_cursor_clip(saved_clip, virtual_rect, game_rect)
    if classification.get("clip_classification") != "game_confinement":
        return {"restore": False, "reason": "saved_clip_invalid"}
    return {"restore": True, "reason": "saved_game_clip"}


def cursor_post_interaction_decision(
    *,
    context,
    snapshot,
    target_valid,
    session_stopping,
    final_check,
):
    """Dynamic-selection-only bounded restore decision."""
    target_hwnd = int((context or {}).get("target_hwnd") or 0)
    if session_stopping:
        return {"action": "skip", "reason": "session_stopping"}
    if not bool((context or {}).get("game_was_foreground")):
        return {"action": "skip", "reason": "game_was_not_foreground"}
    if not target_hwnd or not target_valid:
        return {"action": "skip", "reason": "target_invalid"}
    if int((snapshot or {}).get("foreground_hwnd") or 0) != target_hwnd:
        return {"action": "skip", "reason": "foreground_changed"}
    if (snapshot or {}).get("clip_classification") == "game_confinement":
        return {"action": "self_restored", "reason": "game_restored_clip"}
    if not final_check:
        return {"action": "wait", "reason": "bounded_wait_active"}
    saved_clip = (context or {}).get("last_known_good_game_clip")
    saved_classification = classify_cursor_clip(
        saved_clip,
        (snapshot or {}).get("virtual_screen_rect"),
        (snapshot or {}).get("game_client_rect"),
    )
    if (snapshot or {}).get("clip_classification") != "desktop_unrestricted":
        return {"action": "skip", "reason": "current_clip_not_desktop"}
    if not saved_clip or saved_classification.get("clip_classification") != "game_confinement":
        return {"action": "skip", "reason": "last_known_good_clip_missing_or_invalid"}
    return {"action": "restore", "reason": "desktop_clip_after_interaction"}


class TranslationEngine(QThread):
    """
    Высокопроизводительный движок обработки захвата, OCR и перевода.

    Важная логика сохранена:
    1. Сначала пробуем новый быстрый Rust-захват через Windows Graphics Capture.
    2. Если Rust недоступен, ОС ниже Windows 10 или Rust падает — переключаемся на GDI.
    3. OCR и перевод выполняются в отдельном QThread, чтобы UI оверлея не зависал.

    Ничего из архитектуры захвата не удалено. Добавлен только более жёсткий fallback:
    если Rust-захват упал уже во время работы, движок переключится на GDI.
    """

    translation_ready = pyqtSignal(str, str, float)
    model_loaded_signal = pyqtSignal()
    ocr_growth_pending_deadline_requested = pyqtSignal(int, int)
    subtitle_stale_grace_requested = pyqtSignal(int, int)
    force_refresh_status_signal = pyqtSignal(str, int, str)
    adaptive_plate_sample = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.is_running = False
        self.is_loading_model = False
        self.game_hwnd = 0
        # last_text — это последний текст, который уже реально ушёл в переводчик.
        # Его нельзя путать с текущим сырым OCR-кандидатом.
        self.last_text = ""

        # Счётчик подряд идущих кадров, где OCR не увидел валидный текст.
        # Раньше автоочистка могла сработать слишком быстро: 2 плохих кадра ≈ 0.3–0.4 секунды.
        # Для игровых субтитров это слишком агрессивно, поэтому ниже добавлена временная защита.
        self.empty_frames_counter = 0
        # Время, когда началась текущая серия пустых/плохих OCR-кадров.
        # Для медленного PaddleOCR нельзя очищать перевод по количеству кадров:
        # 8 пустых кадров могут растянуться на 9–12 секунд.
        self.empty_started_time = 0.0


        # Последний хороший перевод держим на экране некоторое время, даже если OCR на 1–2 кадра споткнулся.
        # Это убирает мерцание и пустое окно на редких фразах.
        self.last_good_translation = ""
        self.last_good_source = ""
        self.last_good_time = 0.0

        self.mutex = QMutex()
        self._scheduler_condition = QWaitCondition()

        # По умолчанию хотим Rust. Но фактический режим выбирается в start_engine().
        self.capture_mode = "rust"
        self.rust_engine = None
        self._ocr_pipeline_stage = "idle"
        self.session_data = {}
        self._subtitle_gate = SubtitleSequenceGate()
        self._task_subtitle_sequence_ids = {}
        self._ocr_outcomes_by_task = {}

        # Пока пользователь двигает или меняет размер рамки в режиме настройки,
        # захват/OCR лучше поставить на короткую паузу. Иначе возможны микрофризы,
        # дергание курсора и перевод промежуточных кропов во время настройки.
        self.user_interaction_paused = False

        # --- ДИАГНОСТИКА ПРОПУСКОВ ОСНОВНОГО ОВЕРЛЕЯ ---
        # Если на экране есть фраза, но она не появляется в переводном оверлее,
        # нам нужно понимать, где именно она была отброшена:
        # OCR_EMPTY / OCR_GARBAGE / OCR_DUPLICATE / OCR_REGRESSION_SUBSET / TRANSLATION_EMPTY.
        # Лог пишется с защитой от спама: одинаковые причины не дублируются каждый кадр.
        self.ocr_diagnostics_path = log_file("overlay_ocr_diagnostics.log")
        self._last_diag_signature = ""
        self._last_diag_time = 0.0
        self._diag_repeat_count = 0

        # --- PADDLEOCR LIVE SAFETY ---
        # PaddleOCR качественнее Tesseract, но на CPU может занимать секунды на один кадр.
        # Поэтому в live-режиме нельзя гонять его по одному и тому же кадру снова и снова,
        # особенно когда субтитров нет. Эти поля дают дешёвый кеш по визуальному отпечатку кадра.
        self._last_live_ocr_signature = ""
        self._last_live_ocr_backend = ""
        self._last_live_ocr_text = ""
        self._last_live_ocr_time = 0.0
        self._last_paddle_empty_signature = ""
        self._last_paddle_empty_handled_at = 0.0

        # Быстрый Tesseract-gate для PaddleOCR live-режима.
        # PaddleOCR качественный, но тяжёлый. Поэтому перед дорогим Paddle-проходом
        # в постоянном оверлее мы используем дешёвый Tesseract как детектор:
        # изменился ли текст вообще. Если Tesseract видит тот же субтитр, Paddle
        # повторно не запускается. Это НЕ используется в динамическом переводе.

        # Дополнительная защита от непрерывной загрузки CPU: даже если кадр слегка
        # меняется и хеш считается новым, PaddleOCR не должен стартовать слишком часто.
        # После появления fast-gate интервал можно держать умеренным: новые фразы
        # проходят, а одинаковые режутся дешёвым Tesseract-предфильтром.
        # В step8 убираем жёсткий Tesseract-gate из live-цепочки. Он оказался
        # слишком агрессивным и блокировал реальные смены субтитров.
        # Вместо этого оставляем visual-hash/cooldown + runtime generation.
        self._paddle_live_min_interval_sec = 3.2
        self._paddle_cadence_last_start_monotonic = 0.0
        self._paddle_cadence_last_finish_monotonic = 0.0
        self._paddle_cadence_throttled_count = 0
        self._paddle_cadence_throttle_wait_ms_total = 0.0
        self._paddle_cadence_nonempty_results = 0
        self._paddle_cadence_start_to_start_ms = []
        self._paddle_cadence_finish_to_start_ms = []
        self._paddle_cadence_throttle_wait_ms = []
        self._paddle_cadence_inference_ms = []
        self._paddle_cadence_samples = []
        self._paddle_cadence_last_result_class = ""
        self._paddle_scheduler_delay_deadline_monotonic = 0.0
        self._paddle_scheduler_delay_started_monotonic = 0.0
        self._paddle_scheduler_pending_wait_ms = 0.0
        self._paddle_scheduler_pending_reason = ""
        self._paddle_fast_first_ocr_after_preload_logged = False
        self._paddle_first_ocr_after_preload_logged = set()
        # Step10: даже если visual-hash считает кадр тем же самым, нельзя
        # блокировать PaddleOCR бесконечно. Если фон/плашка почти не меняются,
        # разные субтитры могут попасть в один похожий отпечаток. Поэтому раз
        # в несколько секунд делаем контрольный recheck.
        self._paddle_same_frame_force_recheck_sec = 3.8

        # --- RUNTIME TRACE / CANCEL-LIKE ARCHITECTURE ---
        # Реально прервать уже выполняющийся PaddleOCR в Python мы не можем без
        # отдельного процесса. Но можем пометить задачу устаревшей, чтобы её
        # результат не применился после нажатия хоткея/скрытия/возврата в настройку.
        self.runtime_log_path = log_file("ocr_runtime.log")
        # Создаётся лениво только по явному environment switch. В обычном запуске
        # модуль recorder не импортируется, thread/queue/папки не создаются.
        self._live_ocr_crop_recorder = None
        self._runtime_generation = 0
        self._no_text_generation = 0
        self._runtime_task_seq = 0
        self._visual_epoch = 0
        self._visual_transition_pending = False
        self._visual_transition_pending_since = 0.0
        self._visual_transition_pending_state = ""
        self._held_translation_result = None
        self._latest_ocr_task_id = 0
        self._active_ocr_task_id = 0
        self._active_ocr_source = ""
        self._ui_translation_apply_token = None
        self._session_stopping = False
        self._live_cycle_in_progress = False
        self._force_refresh_request_seq = 0
        self._force_refresh_pending_request_id = 0
        self._force_refresh_pending_was_queued = False
        self._force_refresh_active_request_id = 0
        self._force_refresh_task_requests = {}
        # Для тяжёлого PaddleOCR не допускаем создание новой live-задачи,
        # пока предыдущая ещё выполняется. UI при этом остаётся отзывчивым:
        # хоткеи только инвалидируют generation и ставят паузу, не ждут OCR.
        self._ocr_backend_busy = False
        self._live_paddle_worker = None

        # --- STEP11: VISUAL TEXT-MASK GATE ---
        # Перед тяжёлым PaddleOCR мы больше не должны спрашивать другой OCR,
        # изменился ли текст. Сначала дешёво сравниваем рисунок букв: маску
        # контрастных штрихов в зоне субтитров. Это миллисекундный фильтр,
        # который решает: текст тот же, текст изменился, текст исчез.
        self._visual_text_mask_info = None
        self._visual_text_last_state = ""
        self._visual_text_last_event_time = 0.0
        self._visual_text_last_change_time = 0.0
        self._visual_text_empty_started_time = 0.0
        # Пороги намеренно вынесены в поля: если в тестах будет слишком
        # агрессивно/мягко, поправим без переписывания логики.
        self._visual_text_empty_density = 0.004
        self._visual_text_no_change_score = 0.075
        self._visual_text_changed_score = 0.185
        self._visual_text_density_delta_changed = 0.020
        self._visual_text_uncertain_recheck_sec = 1.2

        # Step12: стабильный старт и debounce. Первый кадр после включения
        # режима перевода может быть переходным/аномальным и не должен сразу
        # запускать PaddleOCR на 20 секунд. Средние изменения подтверждаем
        # короткой выдержкой, чтобы не ловить курсор/мигающий индикатор.
        self._visual_text_first_stabilize_sec = 0.45
        self._visual_text_change_debounce_sec = 0.22
        self._visual_text_strong_changed_score = 0.55
        self._visual_text_first_max_density = 0.70
        self._visual_text_pending_signature = ""
        self._visual_text_pending_since = 0.0
        self._visual_text_pending_info = None
        self._game_field_visual_gate = game_field_visual_gate.GameFieldVisualGate()
        self._last_visual_gate_mode_logged = ""
        self._game_background_paddle_state = game_background_paddle.GameBackgroundPaddleState()
        self.reset_ocr_growth_pending_locked()

        # --- STEP20: OUTPUT STABILITY ---
        # Keep a short-lived source memory even when visual-gate/runtime resets.
        # This prevents regressions such as: "... looks off" -> "... looks"
        # after hiding/showing the overlay or after dynamic mode.
        self._recent_good_source = ""
        self._recent_good_translation = ""
        self._recent_good_time = 0.0
        self._recent_good_ttl_sec = 18.0
        self._last_display_clear_time = 0.0
        self._post_clear_suspicious_text = ""
        self._post_clear_suspicious_since = 0.0
        # Last source explicitly cleared after OCR/visual empty. Prevents old cache-hit
        # resurrection when the subtitle already disappeared but OCR briefly sees stale text.
        self._last_cleared_source = ""
        self._last_cleared_time = 0.0
        self._last_cleared_translation = ""
        self._last_cleared_reason = ""
        self._last_cleared_generation = 0

        # --- STEP29: LIVE TEXT-LEVEL STABILITY ---
        # Лёгкий слой после OCR, но до перевода. Он не заменяет visual gate:
        # gate решает, когда запускать OCR, а этот слой не даёт переводить
        # промежуточные typewriter-фрагменты вроде "Her legs wer".
        self._ocr_growth_debounce_sec = 1.0
        self._ocr_growth_pending_text = ""
        self._ocr_growth_pending_norm = ""
        self._ocr_growth_pending_since = 0.0
        self._ocr_growth_pending_updated_at = 0.0
        self._ocr_growth_pending_generation = 0
        self._ocr_growth_pending_kind = ""
        self._ocr_growth_pending_created_after_visual_clear = False
        self._ocr_growth_pending_visual_density = 0.0
        self._ocr_growth_pending_gate_state = ""
        self._ocr_growth_pending_real_ocr_hits = 0
        self._ocr_growth_pending_recheck_requested = False
        self._ocr_growth_pending_deadline_monotonic = 0.0
        self._ocr_growth_pending_timer_token = 0
        self._ocr_growth_pending_timer_fired_token = 0
        self._ocr_growth_pending_timer_fired_monotonic = 0.0
        self._ocr_growth_pending_task_id = 0
        self._ocr_growth_pending_classifier = None
        self._ocr_growth_pending_source_gone = False
        self._ocr_growth_pending_empty_count = 0
        self._ocr_growth_recent_text = ""
        self._ocr_growth_recent_norm = ""
        self._ocr_growth_recent_time = 0.0
        self._ocr_growth_recent_generation = 0
        self._ocr_growth_sequence_count = 0
        self._ocr_growth_context_until = 0.0
        self._ocr_growth_sequence_window_sec = 2.0
        self._ocr_growth_apply_protect_sec = 1.2
        self._ocr_growth_apply_protected_until = 0.0
        self._ocr_growth_apply_protected_text = ""
        self._ocr_growth_apply_protected_generation = 0

        # На сложном/шумном фоне OCR может несколько раз подряд вернуть пустоту,
        # хотя visual mask плотная. В таком случае не держим старый перевод
        # бесконечно, но очищаем только после повторения.
        self._repeated_empty_dense_min_density = 0.018
        self._repeated_empty_dense_min_count = 2
        self._repeated_empty_dense_clear_sec = 0.9
        self._repeated_empty_signature = ""
        self._repeated_empty_density = 0.0
        self._repeated_empty_count = 0
        self._repeated_empty_since = 0.0
        self._repeated_empty_last_time = 0.0
        self._post_clear_same_source_state = ""
        self._post_clear_same_source_time = 0.0
        self._post_clear_same_source_generation = 0
        self._post_clear_same_source_text = ""
        self._post_clear_same_source_candidate = None
        self._dense_empty_clear_deadline = 0.0
        self._dense_empty_clear_generation = 0
        self._dense_empty_clear_source = ""

    def update_session_data(self, data):
        """
        Синхронизация настроек между UI-оверлеем и рабочим потоком.

        Почему делаем json-копию:
        обычный self.session_data = data сохраняет ссылку на тот же словарь.
        Если UI меняет словарь одновременно с рабочим потоком, можно получить
        нестабильное состояние. json.loads(json.dumps(...)) создаёт простую глубокую копию.
        """
        copied = json.loads(json.dumps(data))
        old_translation = (self.session_data or {}).get("translation", {})
        online_changed = old_translation != copied.get("translation", {}) and (
            old_translation.get("mode") == "online" or copied.get("translation", {}).get("mode") == "online")
        if old_translation != copied.get("translation", {}):
            from online_translation import ONLINE
            ONLINE.cancel_active("session_settings_changed")
        session_logging.update_state(copied)
        copied["subtitles"] = normalize_subtitle_settings(copied.get("subtitles", {}))
        with QMutexLocker(self.mutex):
            if capture_scope(copied) != capture_scope(self.session_data or {}):
                self._adaptive_capture_revision = getattr(self, "_adaptive_capture_revision", 0) + 1
            previous_gate_context = json.dumps(
                {
                    "profile_id": (self.session_data or {}).get("profile_id"),
                    "monitor_zone": (self.session_data or {}).get("monitor_zone", {}),
                    "background_mode": normalize_subtitle_settings(
                        (self.session_data or {}).get("subtitles", {})
                    )["background_mode"],
                    "visual_gate": (self.session_data or {}).get("visual_gate", {}),
                },
                sort_keys=True,
                ensure_ascii=True,
            )
            previous_ocr_backend = current_ocr_backend_from_session(self.session_data or {})
            previous_settings = normalize_subtitle_settings((self.session_data or {}).get("subtitles", {}))
            self.session_data = copied
            current_gate_context = json.dumps(
                {
                    "profile_id": copied.get("profile_id"),
                    "monitor_zone": copied.get("monitor_zone", {}),
                    "background_mode": normalize_subtitle_settings(
                        copied.get("subtitles", {})
                    )["background_mode"],
                    "visual_gate": copied.get("visual_gate", {}),
                },
                sort_keys=True,
                ensure_ascii=True,
            )
            current_ocr_backend = current_ocr_backend_from_session(copied)
            current_settings = normalize_subtitle_settings(copied.get("subtitles", {}))
            settings_changed = previous_settings != current_settings
            ocr_backend_changed = previous_ocr_backend != current_ocr_backend
            gate_context_changed = previous_gate_context != current_gate_context
            if ocr_backend_changed:
                self.reset_paddle_cadence_locked()
                self._scheduler_condition.wakeAll()
            if gate_context_changed:
                self.reset_visual_text_gate_state_locked()
                self._last_visual_gate_mode_logged = ""
            if settings_changed or ocr_backend_changed or gate_context_changed:
                self.reset_game_background_paddle_state_locked("session_config_changed")
            self._subtitle_gate.configure(
                background_mode=current_settings["background_mode"],
                display_policy=current_settings["display_policy"],
            )
            running = bool(self.is_running)
        if running and (settings_changed or ocr_backend_changed or gate_context_changed or online_changed):
            self.invalidate_runtime_tasks(
                "subtitle_mode_or_display_policy_changed"
                if settings_changed
                else (
                    f"ocr_backend_changed:{previous_ocr_backend}->{current_ocr_backend}"
                    if ocr_backend_changed
                    else "online_settings_changed" if online_changed else "visual_gate_context_changed"
                )
            )
        print("[ДВИЖОК] Настройки сессии синхронизированы.")

    def request_force_refresh(self):
        """Queue at most one fresh live capture/OCR/translation cycle."""
        with QMutexLocker(self.mutex):
            self._force_refresh_request_seq += 1
            requested_id = int(self._force_refresh_request_seq)
            unavailable_reason = ""
            if not self.is_running:
                unavailable_reason = "live_engine_not_running"
            elif self._session_stopping:
                unavailable_reason = "session_stopping"
            elif self.user_interaction_paused:
                unavailable_reason = "live_ocr_paused"
            elif not int(self.game_hwnd or 0):
                unavailable_reason = "invalid_game_window"

            already_pending = int(self._force_refresh_pending_request_id or 0)
            busy = bool(
                self._live_cycle_in_progress
                or self._ocr_backend_busy
                or self._force_refresh_active_request_id
                or self._force_refresh_task_requests
            )
            if unavailable_reason:
                status = "rejected"
                effective_id = requested_id
            elif already_pending:
                status = "queued"
                effective_id = already_pending
            else:
                self._force_refresh_pending_request_id = requested_id
                self._force_refresh_pending_was_queued = bool(busy)
                self._scheduler_condition.wakeAll()
                status = "queued" if busy else "immediate"
                effective_id = requested_id
            generation = int(self._runtime_generation)

        self.log_runtime_event(
            "FORCE_REFRESH_REQUESTED",
            generation=generation,
            reason=unavailable_reason or status,
            extra=(
                f"request_id={requested_id}; effective_request_id={effective_id}; "
                f"status={status}; coalesced={bool(already_pending)}"
            ),
        )
        if status == "rejected":
            self.log_runtime_event(
                "FORCE_REFRESH_REJECTED",
                generation=generation,
                reason=unavailable_reason,
                extra=f"request_id={requested_id}; task_created=false",
            )
            self.force_refresh_status_signal.emit("rejected", requested_id, unavailable_reason)
        elif status == "queued":
            self.log_runtime_event(
                "FORCE_REFRESH_QUEUED_PENDING",
                generation=generation,
                reason="coalesced_existing_pending" if already_pending else "live_cycle_busy",
                extra=f"request_id={effective_id}; coalesced={bool(already_pending)}",
            )
            self.force_refresh_status_signal.emit("updating", effective_id, status)
        else:
            self.log_runtime_event(
                "FORCE_REFRESH_STARTED_IMMEDIATELY",
                generation=generation,
                reason="next_fresh_capture",
                extra=f"request_id={effective_id}",
            )
            self.force_refresh_status_signal.emit("updating", effective_id, status)
        return {
            "accepted": status != "rejected",
            "status": status,
            "request_id": effective_id,
            "reason": unavailable_reason,
            "coalesced": bool(already_pending),
        }

    def take_force_refresh_request(self):
        """Consume a pending request only when no earlier forced result is outstanding."""
        with QMutexLocker(self.mutex):
            if self._force_refresh_active_request_id or self._force_refresh_task_requests:
                return None
            request_id = int(self._force_refresh_pending_request_id or 0)
            if not request_id:
                return None
            was_queued = bool(self._force_refresh_pending_was_queued)
            self._force_refresh_pending_request_id = 0
            self._force_refresh_pending_was_queued = False
            self._force_refresh_active_request_id = request_id
            generation = int(self._runtime_generation)
        if was_queued:
            self.log_runtime_event(
                "FORCE_REFRESH_PENDING_EXECUTED",
                generation=generation,
                reason="previous_cycle_finished",
                extra=f"request_id={request_id}; capture=fresh",
            )
        self.log_runtime_event(
            "FORCE_REFRESH_CYCLE_STARTED",
            generation=generation,
            reason="forced_live_cycle",
            extra=f"request_id={request_id}; capture=fresh",
        )
        self.force_refresh_status_signal.emit("updating", request_id, "cycle_started")
        return {"request_id": request_id, "was_queued": was_queued}

    def finish_force_refresh_task(self, task_id, *, success, reason):
        """Complete one forced task; idempotent for multiple terminal guards."""
        with QMutexLocker(self.mutex):
            request_id = int(self._force_refresh_task_requests.pop(int(task_id or 0), 0) or 0)
            generation = int(self._runtime_generation)
            pending = bool(self._force_refresh_pending_request_id)
            if request_id:
                self._scheduler_condition.wakeAll()
        if not request_id:
            return False
        self.log_runtime_event(
            "FORCE_REFRESH_SUCCESS" if success else "FORCE_REFRESH_REJECTED",
            task_id=int(task_id or 0),
            generation=generation,
            reason=str(reason or ("published" if success else "rejected")),
            extra=f"request_id={request_id}; pending={pending}",
        )
        self.force_refresh_status_signal.emit(
            "success" if success else "rejected",
            request_id,
            str(reason or ("published" if success else "rejected")),
        )
        return True

    def finish_unbound_force_refresh(self, reason):
        """Reject a consumed force request if the cycle ended before OCR task creation."""
        with QMutexLocker(self.mutex):
            request_id = int(self._force_refresh_active_request_id or 0)
            self._force_refresh_active_request_id = 0
            generation = int(self._runtime_generation)
        if not request_id:
            return False
        self.log_runtime_event(
            "FORCE_REFRESH_REJECTED",
            generation=generation,
            reason=str(reason or "ocr_task_not_created"),
            extra=f"request_id={request_id}; task_created=false",
        )
        self.force_refresh_status_signal.emit(
            "rejected",
            request_id,
            str(reason or "ocr_task_not_created"),
        )
        return True

    def clear_force_refresh_requests_locked(self):
        """Detach every outstanding forced request while the engine mutex is held."""
        requests = {}
        pending_id = int(self._force_refresh_pending_request_id or 0)
        if pending_id:
            requests[pending_id] = {"request_id": pending_id, "task_id": 0, "state": "pending"}
        active_id = int(self._force_refresh_active_request_id or 0)
        if active_id:
            requests[active_id] = {"request_id": active_id, "task_id": 0, "state": "active"}
        for task_id, request_id in self._force_refresh_task_requests.items():
            request_id = int(request_id or 0)
            if not request_id:
                continue
            requests[request_id] = {
                "request_id": request_id,
                "task_id": int(task_id or 0),
                "state": "task_bound",
            }
        self._force_refresh_pending_request_id = 0
        self._force_refresh_pending_was_queued = False
        self._force_refresh_active_request_id = 0
        self._force_refresh_task_requests.clear()
        if requests:
            self._scheduler_condition.wakeAll()
        return [requests[request_id] for request_id in sorted(requests)]

    def reject_cleared_force_refresh_requests(self, requests, *, generation, reason):
        """Emit one terminal rejection for each request detached by a lifecycle reset."""
        terminal_reason = str(reason or "force_refresh_invalidated")
        for request in requests or []:
            request_id = int(request.get("request_id") or 0)
            task_id = int(request.get("task_id") or 0)
            if not request_id:
                continue
            self.log_runtime_event(
                "FORCE_REFRESH_REJECTED",
                task_id=task_id or None,
                generation=int(generation or 0),
                reason=terminal_reason,
                extra=(
                    f"request_id={request_id}; lifecycle_cleanup=true; "
                    f"state={request.get('state') or 'unknown'}"
                ),
            )
            self.force_refresh_status_signal.emit("rejected", request_id, terminal_reason)

    def set_user_interaction_paused(self, paused, *, invalidate_runtime=True):
        """
        Временная пауза захвата/OCR на время ручной настройки рамки.

        Это не stop_engine(): поток остаётся живым, Rust/GDI не пересоздаются,
        модель не выгружается. Мы просто пропускаем кадры, пока пользователь
        двигает или меняет размер окна оверлея.
        """
        with QMutexLocker(self.mutex):
            changed = self.user_interaction_paused != bool(paused)
            self.user_interaction_paused = bool(paused)
            if changed and invalidate_runtime:
                self._runtime_generation += 1
                generation = self._runtime_generation
                subtitle_reset = self.reset_subtitle_gate_locked(
                    "runtime_paused" if paused else "runtime_resumed"
                )
                if (
                    self._game_field_visual_gate is not None
                    and normalize_subtitle_settings((self.session_data or {}).get("subtitles", {}))["background_mode"]
                    != GAME_BACKGROUND
                ):
                    self._game_field_visual_gate.reset(
                        "runtime_paused" if paused else "runtime_resumed"
                    )
                self.reset_game_background_paddle_state_locked(
                    "runtime_paused" if paused else "runtime_resumed"
                )
            else:
                generation = self._runtime_generation
                subtitle_reset = None
            if changed:
                self._scheduler_condition.wakeAll()
        if changed:
            if subtitle_reset is not None:
                self.log_subtitle_decision(subtitle_reset, generation=generation)
            self.log_runtime_event("OCR_RUNTIME_PAUSED" if paused else "OCR_RUNTIME_RESUMED", generation=generation)
            if invalidate_runtime:
                self.cancel_active_translation_request(
                    "runtime_paused" if paused else "runtime_resumed"
                )
            # После скрытия/динамики/ручной настройки кадр мог измениться, а старый
            # visual-cache уже не должен мешать первому нормальному recheck.
            if not paused:
                self.reset_visual_text_gate_state("runtime_resumed")

    def set_live_paddle_worker(self, worker):
        """Attach the session-owned Paddle worker without changing OCR task ownership."""
        with QMutexLocker(self.mutex):
            self._live_paddle_worker = worker
        if worker is not None:
            try:
                worker._thread_diagnostic_engine = self
            except Exception:
                pass

    def live_paddle_worker_for_backend(self, backend_name):
        with QMutexLocker(self.mutex):
            worker = self._live_paddle_worker
        if worker is None:
            return None
        try:
            if worker.matches_backend(backend_name) and worker.isRunning():
                return worker
        except Exception:
            return None
        return None

    def log_runtime_event(
        self,
        event,
        task_id=None,
        generation=None,
        text="",
        reason="",
        extra="",
        traceback_text="",
    ):
        """Подробный runtime-лог OCR-задач и отмены устаревших результатов."""
        session_logging.diagnostic_event(event, task_id=task_id, generation=generation)
        if not session_logging.raw_enabled():
            return
        try:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with session_logging.open_log(self.runtime_log_path, "a", encoding="utf-8") as f:
                f.write("=" * 70 + "\n")
                f.write(f"TIME: {timestamp}\n")
                f.write(f"EVENT: {event}\n")
                if task_id is not None:
                    f.write(f"TASK_ID: {task_id}\n")
                if generation is not None:
                    f.write(f"GENERATION: {generation}\n")
                if reason:
                    f.write(f"REASON: {str(reason)[:500]}\n")
                if text:
                    f.write(f"TEXT: {str(text)[:700]}\n")
                if extra:
                    f.write(f"DETAILS: {str(extra)[:900]}\n")
                if traceback_text:
                    f.write("TRACEBACK:\n")
                    f.write(str(traceback_text).rstrip() + "\n")
                f.write("=" * 70 + "\n\n")
        except Exception as e:
            print(f"[OCR RUNTIME LOG ERROR]: {e}", flush=True)
        recorder = self._live_ocr_crop_recorder
        if recorder is not None:
            try:
                recorder.note_runtime_event(
                    event,
                    task_id=task_id,
                    generation=generation,
                    text=text,
                    reason=reason,
                    extra=extra,
                )
            except Exception:
                pass
    def run_optional_ocr_diagnostic(
        self,
        operation,
        callback,
        *,
        task_id=None,
        generation=None,
    ):
        try:
            return callback()
        except Exception as exc:
            self.log_runtime_event(
                "OCR_DIAGNOSTIC_ERROR",
                task_id=task_id,
                generation=generation,
                reason=f"{type(exc).__name__}: {exc}",
                extra=f"operation={operation}",
            )
            return None

    def log_ocr_pipeline_error(self, exc, *, capture_mode, stage, traceback_text):
        with QMutexLocker(self.mutex):
            cfg = dict(self.session_data or {})
            task_id = int(self._active_ocr_task_id or 0)
            generation = int(self._runtime_generation)
        self.log_runtime_event(
            "OCR_PIPELINE_ERROR",
            task_id=task_id or None,
            generation=generation,
            reason=f"{type(exc).__name__}: {exc}",
            extra=(
                f"capture_mode={capture_mode}; "
                f"ocr_backend={current_ocr_backend_from_session(cfg)}; "
                f"stage={stage}"
            ),
            traceback_text=traceback_text,
        )
        self.finish_force_refresh_task(
            task_id,
            success=False,
            reason=f"pipeline_error:{stage}:{type(exc).__name__}",
        )

    def live_ocr_crop_debug_manifest(self):
        """Снимок только тех session-полей, которые нужны crop-диагностике."""
        with QMutexLocker(self.mutex):
            cfg = json.loads(json.dumps(self.session_data or {}))
            capture_backend = str(self.capture_mode or "")
            target_hwnd = int(self.game_hwnd or 0)
        translation_cfg = cfg.get("translation", {}) or {}
        ocr_backend = current_ocr_backend_from_session(cfg)
        backend = None
        try:
            backend = ocr_core.get_backend(ocr_backend)
        except Exception:
            pass
        preset = str(getattr(backend, "preset", "") or "")
        if ocr_backend == "paddle_fast":
            model_id = "PP-OCRv4_mobile_det + en_PP-OCRv4_mobile_rec"
        elif ocr_backend == "paddle":
            model_id = "PP-OCRv6_medium_det + PP-OCRv6_medium_rec"
        elif ocr_backend == "tesseract":
            model_id = "Tesseract legacy ocr_core pipeline"
        else:
            model_id = str(getattr(backend, "display_name", "") or ocr_backend)
        zone = dict(cfg.get("monitor_zone", {}) or {})
        return {
            "app_version": getattr(config, "APP_VERSION", None),
            "profile_name": str(cfg.get("game_name") or cfg.get("profile_id") or "profile"),
            "profile_id": cfg.get("profile_id"),
            "game_executable": cfg.get("game_executable"),
            "target_hwnd_at_start": target_hwnd,
            "source_lang": translation_cfg.get("source_lang"),
            "target_lang": translation_cfg.get("target_lang"),
            "ocr_backend": ocr_backend,
            "ocr_model_identifier": model_id,
            "ocr_preset": preset or None,
            "ocr_model_root": str(data_path("paddle.models")) if is_paddle_family_backend(ocr_backend) else None,
            "translator_backend": translation_cfg.get("backend"),
            "live_roi": {
                "left_offset": zone.get("left_offset"),
                "top_offset": zone.get("top_offset"),
                "width": zone.get("width"),
                "height": zone.get("height"),
            },
            "game_client_size": None,
            "capture_backend": capture_backend,
            "preprocessing_configuration": self.live_ocr_debug_preprocess_metadata(ocr_backend),
            "cadence_configuration": {
                "capture_loop_target_ms": 180,
                "capture_loop_min_sleep_ms": 25,
                "paddle_start_to_start_min_interval_sec": self._paddle_live_min_interval_sec,
                "paddle_same_frame_force_recheck_sec": self._paddle_same_frame_force_recheck_sec,
            },
        }

    @staticmethod
    def live_ocr_debug_preprocess_metadata(ocr_backend):
        backend_name = str(ocr_backend or "")
        if is_paddle_family_backend(backend_name):
            return {
                "profile": "paddle_backend_input_passthrough",
                "production_operations_before_backend": [],
                "resize_scale": 1.0,
                "threshold": None,
                "contrast": None,
                "sharpen": None,
                "backend_internal_operations": [
                    "PIL.convert(RGB)",
                    "numpy.array",
                    "PaddleOCR/PaddleX internal resize and normalization",
                ],
                "internal_tensor_capture": False,
            }
        if backend_name == "tesseract":
            return {
                "profile": "tesseract_legacy_internal_preprocess",
                "production_operations_before_backend": [],
                "resize_scale": None,
                "threshold": 140,
                "contrast": 3.0,
                "sharpen": None,
                "backend_internal_operations": [
                    "ocr_core.preprocess_image",
                    "edge padding crop",
                    "grayscale",
                    "contrast",
                    "threshold",
                    "optional autocrop",
                    "Tesseract upscale",
                ],
                "internal_tensor_capture": False,
            }
        return {
            "profile": "backend_input_passthrough",
            "production_operations_before_backend": [],
            "resize_scale": None,
            "threshold": None,
            "contrast": None,
            "sharpen": None,
            "backend_internal_operations": None,
            "internal_tensor_capture": False,
        }

    def ensure_live_ocr_crop_debug_recorder(self):
        if not session_logging.raw_enabled():
            return None
        if self._live_ocr_crop_recorder is not None:
            return self._live_ocr_crop_recorder
        if str(os.environ.get("GAME_TRANSLATOR_LIVE_OCR_CROP_DEBUG", "")).strip().lower() not in {
            "1", "true", "yes", "on", "enabled"
        }:
            return None
        try:
            from diagnostics.live_ocr_crop_recorder import create_live_ocr_crop_recorder

            recorder = create_live_ocr_crop_recorder(
                APP_DIR,
                self.live_ocr_crop_debug_manifest(),
                event_callback=self.log_runtime_event,
            )
            self._live_ocr_crop_recorder = recorder
            if recorder is not None:
                print(
                    f"[LIVE OCR CROP DEBUG] session: {recorder.session_dir}",
                    flush=True,
                )
            return recorder
        except Exception as exc:
            self.log_runtime_event(
                "LIVE_OCR_CROP_DEBUG_WRITE_FAILED",
                reason=f"recorder_start_failed:{type(exc).__name__}: {exc}",
            )
            return None

    def shutdown_live_ocr_crop_debug_recorder(self, timeout_sec=3.0):
        recorder = self._live_ocr_crop_recorder
        if recorder is None:
            return {"enabled": False}
        return recorder.shutdown(timeout_sec=timeout_sec)

    def subtitle_settings_snapshot(self, cfg=None):
        if cfg is not None:
            return normalize_subtitle_settings((cfg or {}).get("subtitles", {}))
        with QMutexLocker(self.mutex):
            return normalize_subtitle_settings((self.session_data or {}).get("subtitles", {}))

    def reset_game_background_paddle_state_locked(self, reason=""):
        self._game_background_paddle_state.reset()

    def ensure_game_background_pipeline_active(self, cfg=None):
        source = self.session_data if cfg is None else cfg
        settings = self.subtitle_settings_snapshot(source)
        if settings["background_mode"] != GAME_BACKGROUND:
            return False
        if not self._game_background_paddle_state.mark_pipeline_active():
            return False
        self.log_runtime_event(
            "GAME_BACKGROUND_PIPELINE_ACTIVE",
            generation=self._runtime_generation,
            extra=(
                f"pipeline={game_background_paddle.PIPELINE_NAME}; "
                f"ocr_backend={current_ocr_backend_from_session(source)}"
            ),
        )
        return True

    def subtitle_stale_grace_ms(self, cfg=None):
        settings = self.subtitle_settings_snapshot(cfg)
        if settings["display_policy"] != FAST_SUBTITLES:
            return 0
        return FAST_SUBTITLES_STALE_GRACE_MS

    @staticmethod
    def _timing_sample_summary(values):
        samples = sorted(float(value) for value in values if float(value) >= 0.0)
        if not samples:
            return {"count": 0, "median": None, "mean": None, "p95": None, "max": None}
        p95_index = max(0, min(len(samples) - 1, int((len(samples) * 0.95) + 0.999999) - 1))
        return {
            "count": len(samples),
            "median": round(float(statistics.median(samples)), 3),
            "mean": round(float(statistics.fmean(samples)), 3),
            "p95": round(float(samples[p95_index]), 3),
            "max": round(float(samples[-1]), 3),
        }

    def paddle_cadence_summary(self):
        with QMutexLocker(self.mutex):
            samples = list(self._paddle_cadence_samples)
            after_nonempty = [
                sample for sample in samples
                if sample.get("previous_result_class") == "nonempty"
            ]
            after_empty = [
                sample for sample in samples
                if sample.get("previous_result_class") == "empty"
            ]

            def summarize(sample_group):
                return {
                    "count": len(sample_group),
                    "start_to_start_ms": self._timing_sample_summary(
                        sample.get("start_to_start_ms", -1.0)
                        for sample in sample_group
                        if sample.get("start_to_start_ms") is not None
                    ),
                    "finish_to_start_ms": self._timing_sample_summary(
                        sample.get("finish_to_start_ms", -1.0)
                        for sample in sample_group
                        if sample.get("finish_to_start_ms") is not None
                    ),
                    "scheduler_wait_ms": self._timing_sample_summary(
                        sample.get("scheduler_wait_ms", 0.0)
                        for sample in sample_group
                    ),
                    "inference_ms": self._timing_sample_summary(
                        sample.get("inference_ms", 0.0)
                        for sample in sample_group
                    ),
                }

            return {
                "configured_interval_ms": round(self._paddle_live_min_interval_sec * 1000.0, 3),
                "start_to_start_ms": self._timing_sample_summary(self._paddle_cadence_start_to_start_ms),
                "finish_to_start_ms": self._timing_sample_summary(self._paddle_cadence_finish_to_start_ms),
                "throttle_wait_ms": self._timing_sample_summary(self._paddle_cadence_throttle_wait_ms),
                "inference_ms": self._timing_sample_summary(self._paddle_cadence_inference_ms),
                "throttled_count": int(self._paddle_cadence_throttled_count),
                "nonempty_results": int(self._paddle_cadence_nonempty_results),
                "all": summarize(samples),
                "after_nonempty": summarize(after_nonempty),
                "after_empty": summarize(after_empty),
                "samples": samples,
            }

    def reset_paddle_cadence_locked(self):
        self._paddle_cadence_last_start_monotonic = 0.0
        self._paddle_cadence_last_finish_monotonic = 0.0
        self._paddle_cadence_last_result_class = ""
        self._paddle_scheduler_delay_deadline_monotonic = 0.0
        self._paddle_scheduler_delay_started_monotonic = 0.0
        self._paddle_scheduler_pending_wait_ms = 0.0
        self._paddle_scheduler_pending_reason = ""
        self._paddle_cadence_throttled_count = 0
        self._paddle_cadence_throttle_wait_ms_total = 0.0
        self._paddle_cadence_nonempty_results = 0
        self._paddle_cadence_start_to_start_ms = []
        self._paddle_cadence_finish_to_start_ms = []
        self._paddle_cadence_throttle_wait_ms = []
        self._paddle_cadence_inference_ms = []
        self._paddle_cadence_samples = []

    def log_paddle_cadence_summary(self, reason):
        summary = self.paddle_cadence_summary()

        def compact(group):
            start_to_start = group.get("start_to_start_ms", {})
            return (
                f"count={group.get('count', 0)},"
                f"median={start_to_start.get('median')},"
                f"mean={start_to_start.get('mean')},"
                f"p95={start_to_start.get('p95')},"
                f"max={start_to_start.get('max')}"
            )

        self.log_runtime_event(
            "OCR_PADDLE_CADENCE_SUMMARY",
            generation=self._runtime_generation,
            reason=reason,
            extra=(
                f"configured_interval_ms={summary.get('configured_interval_ms')}; "
                f"all[{compact(summary.get('all', {}))}]; "
                f"after_nonempty[{compact(summary.get('after_nonempty', {}))}]; "
                f"after_empty[{compact(summary.get('after_empty', {}))}]; "
                f"throttled_count={summary.get('throttled_count')}; "
                f"nonempty_results={summary.get('nonempty_results')}"
            ),
        )

    def paddle_live_scheduler_gate(
        self,
        *,
        trigger_reason,
        region_valid=True,
        visual_stable_event=False,
    ):
        now_monotonic = time.monotonic()
        with QMutexLocker(self.mutex):
            decision = paddle_live_scheduler_decision(
                last_start_monotonic=self._paddle_cadence_last_start_monotonic,
                now_monotonic=now_monotonic,
                throttle_ms=self._paddle_live_min_interval_sec * 1000.0,
                visual_stable_event=visual_stable_event,
                backend_busy=self._ocr_backend_busy,
                paused=self.user_interaction_paused,
                stopping=(self._session_stopping or not self.is_running),
                region_valid=region_valid,
            )
            remaining_ms = max(0.0, float(decision.get("remaining_ms") or 0.0))
            reason = str(decision.get("reason") or "scheduler_blocked")
            scheduler_class = str(decision.get("scheduler_class") or reason)
            allowed = bool(decision.get("allowed"))
            cleared_pending = False

            if allowed and visual_stable_event:
                cleared_pending = bool(
                    self._paddle_scheduler_delay_deadline_monotonic > 0.0
                    or self._paddle_scheduler_pending_wait_ms > 0.0
                    or self._paddle_scheduler_pending_reason
                )
                self._paddle_scheduler_delay_deadline_monotonic = 0.0
                self._paddle_scheduler_delay_started_monotonic = 0.0
                self._paddle_scheduler_pending_wait_ms = 0.0
                self._paddle_scheduler_pending_reason = "visual_stable_immediate"

            if not allowed and reason == "start_to_start_throttle":
                self._paddle_cadence_throttled_count += 1
                self._paddle_cadence_throttle_wait_ms_total += remaining_ms
                self._paddle_cadence_throttle_wait_ms.append(remaining_ms)
                del self._paddle_cadence_throttle_wait_ms[:-512]
                self._paddle_scheduler_delay_deadline_monotonic = max(
                    self._paddle_scheduler_delay_deadline_monotonic,
                    now_monotonic + (remaining_ms / 1000.0),
                )
                if self._paddle_scheduler_delay_started_monotonic <= 0.0:
                    self._paddle_scheduler_delay_started_monotonic = now_monotonic
                self._paddle_scheduler_pending_wait_ms = max(
                    self._paddle_scheduler_pending_wait_ms,
                    remaining_ms,
                )
                self._paddle_scheduler_pending_reason = str(trigger_reason or reason)
            throttle_count = self._paddle_cadence_throttled_count
            throttle_wait_total = self._paddle_cadence_throttle_wait_ms_total

        if scheduler_class in {
            "visual_stable_immediate",
            "fallback_throttled",
            "backend_busy",
        }:
            self.log_runtime_event(
                "OCR_PADDLE_SCHEDULER_DECISION",
                generation=self._runtime_generation,
                reason=scheduler_class,
                extra=(
                    f"trigger={trigger_reason}; remaining_ms={remaining_ms:.3f}; "
                    f"visual_stable_event={bool(visual_stable_event)}; "
                    f"cleared_delayed_scheduler={cleared_pending}"
                ),
            )

        if allowed:
            return True
        if reason != "start_to_start_throttle":
            return False

        self.log_runtime_event(
            "OCR_PADDLE_CADENCE_DELAYED",
            generation=self._runtime_generation,
            reason=reason,
            extra=(
                f"trigger={trigger_reason}; remaining_ms={remaining_ms:.3f}; "
                f"configured_interval_ms={self._paddle_live_min_interval_sec * 1000.0:.3f}; "
                f"throttled_count={throttle_count}; "
                f"throttle_wait_ms_total={throttle_wait_total:.3f}"
            ),
        )

        wait_ms = max(1, int(math.ceil(remaining_ms)))
        with QMutexLocker(self.mutex):
            if (
                self.is_running
                and not self._session_stopping
                and not self.user_interaction_paused
            ):
                self._scheduler_condition.wait(self.mutex, wait_ms)
        return False

    def log_subtitle_decision(self, decision, *, task_id=None, generation=None):
        if not isinstance(decision, dict):
            return
        if decision.get("suppress_log"):
            return
        fields = (
            "subtitle_background_mode",
            "subtitle_display_policy",
            "subtitle_sequence_id",
            "translation_request_id",
            "normalized_text_hash",
            "source_length",
            "candidate_length",
            "similarity_ratio",
            "token_overlap",
            "length_ratio",
            "classification",
            "empty_count",
            "source_present",
            "source_gone",
            "translation_pending",
            "translation_visible",
            "sequence_created",
            "sequence_reused",
            "sequence_cancelled",
            "source_change_detected",
            "cancel_reason",
            "publish_allowed",
            "publish_rejected_reason",
            "stale_sequence_id",
            "stale_reason",
            "stale_since",
            "stale_grace_ms",
            "stale_deadline",
            "clear_reason",
            "replacement_sequence_id",
            "stale_grace_started",
            "stale_grace_cancelled",
            "new_sequence_replaced_old",
            "remaining_split_parts_cancelled",
        )
        details = "; ".join(f"{key}={decision.get(key)}" for key in fields)
        self.log_runtime_event(
            str(decision.get("event") or "SUBTITLE_GATE_EVENT"),
            task_id=task_id,
            generation=generation,
            reason=str(decision.get("cancel_reason") or decision.get("publish_rejected_reason") or ""),
            extra=details,
        )
        recorder = self._live_ocr_crop_recorder
        if recorder is not None:
            try:
                recorder.note_gate_decision(task_id, generation, decision)
            except Exception:
                pass

    def log_ocr_result_outcome(
        self,
        classification,
        *,
        final_outcome,
        task_id=None,
        generation=None,
        subtitle_sequence_id=None,
        exact_reason_code=None,
    ):
        if not isinstance(classification, dict):
            return
        settings = normalize_subtitle_settings((self.session_data or {}).get("subtitles", {}))
        if subtitle_sequence_id is None and task_id:
            subtitle_sequence_id = int(self._task_subtitle_sequence_ids.get(int(task_id), 0) or 0)
        fields = post_ocr_classifier.outcome_log_fields(
            classification,
            final_outcome=str(final_outcome or ""),
            exact_reason_code=exact_reason_code,
            task_id=task_id,
            subtitle_sequence_id=subtitle_sequence_id,
            background_mode=settings["background_mode"],
            display_policy=settings["display_policy"],
        )
        details = "; ".join(
            f"{key}={json.dumps(value, ensure_ascii=True, separators=(',', ':'))}"
            for key, value in fields.items()
        )
        self.log_runtime_event(
            "OCR_RESULT_OUTCOME",
            task_id=task_id,
            generation=generation,
            reason=str(fields.get("exact_reason_code") or ""),
            extra=details,
        )
        recorder = self._live_ocr_crop_recorder
        if recorder is not None:
            try:
                recorder.note_classifier_decision(
                    task_id,
                    generation,
                    classification,
                    final_outcome,
                    exact_reason_code,
                )
            except Exception:
                pass
        if str(final_outcome or "") in {
            "hard_garbage",
            "soft_garbage",
            "near_duplicate",
            "sequence_rejected",
            "translation_rejected",
        }:
            self.finish_force_refresh_task(
                task_id,
                success=False,
                reason=str(exact_reason_code or final_outcome or "post_ocr_rejected"),
            )

    def remember_ocr_task_classification(self, task_id, classification):
        if not task_id or not isinstance(classification, dict):
            return
        with QMutexLocker(self.mutex):
            self._ocr_outcomes_by_task.clear()
            self._ocr_outcomes_by_task[int(task_id)] = dict(classification)

    def ocr_task_classification(self, task_id):
        with QMutexLocker(self.mutex):
            return dict(self._ocr_outcomes_by_task.get(int(task_id or 0), {}) or {})

    def reset_subtitle_gate_locked(self, reason):
        decision = self._subtitle_gate.reset(str(reason or "reset"))
        self._task_subtitle_sequence_ids.clear()
        return decision

    def subtitle_sequence_for_task(self, task_id):
        with QMutexLocker(self.mutex):
            return int(self._task_subtitle_sequence_ids.get(int(task_id), 0) or 0)

    def is_subtitle_sequence_current(self, sequence_id):
        if not sequence_id:
            return True
        with QMutexLocker(self.mutex):
            return self._subtitle_gate.is_current(int(sequence_id))

    def register_subtitle_ocr_candidate(
        self,
        text,
        *,
        task_id,
        task_generation,
        force_new=False,
        preserve_visible=False,
    ):
        dropped_result = None
        clear_visible = False
        cancel_previous_translation = False
        stale_grace_request = None
        with QMutexLocker(self.mutex):
            settings = normalize_subtitle_settings((self.session_data or {}).get("subtitles", {}))
            self._subtitle_gate.configure(
                background_mode=settings["background_mode"],
                display_policy=settings["display_policy"],
            )
            decision = self._subtitle_gate.process_ocr_text(text, force_new=bool(force_new))
            if decision.get("sequence_reused"):
                result = {
                    "accept": False,
                    "decision": decision,
                    "task_generation": int(task_generation),
                    "subtitle_sequence_id": int(decision.get("subtitle_sequence_id", 0) or 0),
                    "clear_visible": False,
                }
            else:
                sequence_id = int(decision.get("subtitle_sequence_id", 0) or 0)
                had_visible = bool(self.last_good_translation or self._ui_translation_apply_token)
                replacing = bool(decision.get("new_sequence_replaced_old"))
                preserve_stale_visible = bool(
                    decision.get("preserve_visible_during_stale_grace")
                    or preserve_visible
                )
                if replacing or had_visible:
                    self._runtime_generation += 1
                    task_generation = self._runtime_generation
                    dropped_result = self._held_translation_result
                    self._held_translation_result = None
                    self.last_text = ""
                    if not preserve_stale_visible:
                        self.last_good_source = ""
                        self.last_good_time = 0.0
                        self._recent_good_source = ""
                        self._recent_good_translation = ""
                        self._recent_good_time = 0.0
                        self._ui_translation_apply_token = None
                        self.last_good_translation = ""
                    clear_visible = bool(had_visible and not preserve_stale_visible)
                cancel_previous_translation = bool(
                    replacing or decision.get("cancel_translation")
                )
                if decision.get("stale_grace_started"):
                    stale_grace_request = (
                        int(decision.get("stale_sequence_id", 0) or 0),
                        int(decision.get("stale_grace_ms", 0) or 0),
                    )
                self._latest_ocr_task_id = int(task_id)
                self._active_ocr_task_id = int(task_id)
                self._active_ocr_source = str(text or "")
                self._task_subtitle_sequence_ids.clear()
                self._task_subtitle_sequence_ids[int(task_id)] = sequence_id
                started = self._subtitle_gate.mark_translation_started(
                    sequence_id,
                    request_id=f"ocr_task_{int(task_id)}",
                )
                result = {
                    "accept": True,
                    "decision": decision,
                    "started": started,
                    "task_generation": int(task_generation),
                    "subtitle_sequence_id": sequence_id,
                    "clear_visible": clear_visible,
                }
        self.log_subtitle_decision(decision, task_id=task_id, generation=task_generation)
        if result.get("started"):
            self.log_subtitle_decision(result["started"], task_id=task_id, generation=task_generation)
        if dropped_result:
            self.log_translation_result_dropped(dropped_result, "subtitle_sequence_replaced")
        if cancel_previous_translation:
            self.cancel_active_translation_request("subtitle_sequence_replaced")
        if stale_grace_request and all(stale_grace_request):
            self.subtitle_stale_grace_requested.emit(*stale_grace_request)
        if clear_visible:
            self.translation_ready.emit("", "", 0.0)
        return result

    def note_pending_subtitle_source_change(
        self,
        text,
        *,
        task_id,
        task_generation,
    ):
        """Invalidate source A while a credible CJK source B still awaits confirmation."""
        dropped_result = None
        clear_visible = False
        cancel_previous_translation = False
        stale_grace_request = None
        with QMutexLocker(self.mutex):
            settings = normalize_subtitle_settings((self.session_data or {}).get("subtitles", {}))
            self._subtitle_gate.configure(
                background_mode=settings["background_mode"],
                display_policy=settings["display_policy"],
            )
            decision = self._subtitle_gate.note_source_change_candidate(text)
            if decision.get("source_change_detected"):
                preserve_stale_visible = bool(
                    decision.get("preserve_visible_during_stale_grace")
                )
                self._runtime_generation += 1
                task_generation = int(self._runtime_generation)
                dropped_result = self._held_translation_result
                self._held_translation_result = None
                self.last_text = ""
                self.last_good_source = ""
                self.last_good_time = 0.0
                self._recent_good_source = ""
                self._recent_good_translation = ""
                self._recent_good_time = 0.0
                if not preserve_stale_visible:
                    self._ui_translation_apply_token = None
                    self.last_good_translation = ""
                clear_visible = bool(decision.get("clear_visible"))
                cancel_previous_translation = bool(decision.get("cancel_translation"))
                self._task_subtitle_sequence_ids.clear()
                if decision.get("stale_grace_started"):
                    stale_grace_request = (
                        int(decision.get("stale_sequence_id", 0) or 0),
                        int(decision.get("stale_grace_ms", 0) or 0),
                    )
        if decision.get("source_change_detected"):
            self.log_subtitle_decision(decision, task_id=task_id, generation=task_generation)
        if dropped_result:
            self.log_translation_result_dropped(
                dropped_result,
                "subtitle_source_change_pending_confirmation",
            )
        if cancel_previous_translation:
            self.cancel_active_translation_request(
                "subtitle_source_change_pending_confirmation"
            )
        if stale_grace_request and all(stale_grace_request):
            self.subtitle_stale_grace_requested.emit(*stale_grace_request)
        if clear_visible:
            self.translation_ready.emit("", "", 0.0)
        return {
            "source_change_detected": bool(decision.get("source_change_detected")),
            "decision": decision,
            "task_generation": int(task_generation),
        }

    def process_subtitle_empty_result(self, *, origin, task_id=None, generation=None):
        dropped_result = None
        should_clear = False
        should_cancel = False
        stale_grace_request = None
        with QMutexLocker(self.mutex):
            settings = normalize_subtitle_settings((self.session_data or {}).get("subtitles", {}))
            self._subtitle_gate.configure(
                background_mode=settings["background_mode"],
                display_policy=settings["display_policy"],
            )
            decision = self._subtitle_gate.process_empty()
            if decision.get("sequence_cancelled"):
                preserve_stale_visible = bool(
                    decision.get("preserve_visible_during_stale_grace")
                )
                self._runtime_generation += 1
                generation = self._runtime_generation
                self._no_text_generation = self._runtime_generation
                dropped_result = self._held_translation_result
                self._held_translation_result = None
                if not preserve_stale_visible:
                    self._ui_translation_apply_token = None
                self._task_subtitle_sequence_ids.clear()
                self.last_text = ""
                self.last_good_source = ""
                if not preserve_stale_visible:
                    self.last_good_translation = ""
                self.last_good_time = 0.0
                self._recent_good_source = ""
                self._recent_good_translation = ""
                self._recent_good_time = 0.0
                self._last_display_clear_time = time.time()
                should_clear = bool(decision.get("clear_visible"))
                should_cancel = bool(decision.get("cancel_translation"))
                if decision.get("stale_grace_started"):
                    stale_grace_request = (
                        int(decision.get("stale_sequence_id", 0) or 0),
                        int(decision.get("stale_grace_ms", 0) or 0),
                    )
        decision["empty_origin"] = str(origin or "")
        self.log_subtitle_decision(decision, task_id=task_id, generation=generation)
        if dropped_result:
            self.log_translation_result_dropped(dropped_result, "subtitle_source_gone_follow_source")
        if should_cancel:
            self.cancel_active_translation_request(
                "subtitle_source_gone"
                if str(decision.get("cancel_reason") or "") == "source_gone"
                else "subtitle_source_gone_follow_source"
            )
        if stale_grace_request and all(stale_grace_request):
            self.subtitle_stale_grace_requested.emit(*stale_grace_request)
        if should_clear:
            self.translation_ready.emit("", "", 0.0)
        return decision

    def expire_subtitle_stale_grace(self, sequence_id, *, now=None):
        dropped_result = None
        visible_cleared = False
        reschedule_ms = 0
        with QMutexLocker(self.mutex):
            expired_sequence_id = int(sequence_id or 0)
            active_sequence = self._subtitle_gate.active
            current_sequence_id = int(active_sequence.sequence_id if active_sequence else 0)
            active_ocr_task_id = int(self._active_ocr_task_id or 0)
            active_task_sequence_id = int(
                self._task_subtitle_sequence_ids.get(active_ocr_task_id, 0) or 0
            )
            runtime_generation_before = int(self._runtime_generation)
            decision = self._subtitle_gate.expire_stale_grace(expired_sequence_id, now=now)
            reschedule_ms = int(decision.get("stale_grace_remaining_ms", 0) or 0)
            if decision.get("clear_visible"):
                visible_token = self._ui_translation_apply_token
                visible_token_sequence_id = int(
                    (visible_token or {}).get("subtitle_sequence_id", 0) or 0
                )
                visible_belongs_to_expired = bool(
                    not visible_token_sequence_id
                    or visible_token_sequence_id == expired_sequence_id
                )
                if visible_belongs_to_expired:
                    held_sequence_id = int(
                        (self._held_translation_result or {}).get("subtitle_sequence_id", 0) or 0
                    )
                    if held_sequence_id in {0, expired_sequence_id}:
                        dropped_result = self._held_translation_result
                        self._held_translation_result = None
                    self._ui_translation_apply_token = None
                    self.last_text = ""
                    self.last_good_translation = ""
                    self.last_good_source = ""
                    self.last_good_time = 0.0
                    self._recent_good_source = ""
                    self._recent_good_translation = ""
                    self._recent_good_time = 0.0
                    self._last_display_clear_time = time.time()
                    visible_cleared = True
                self._task_subtitle_sequence_ids = {
                    int(task_id): int(mapped_sequence_id)
                    for task_id, mapped_sequence_id in self._task_subtitle_sequence_ids.items()
                    if int(mapped_sequence_id or 0) != expired_sequence_id
                }
            generation = self._runtime_generation
            runtime_generation_after = int(self._runtime_generation)
            if decision.get("clear_visible") and not visible_cleared:
                reason = "visible_translation_owned_by_newer_sequence"
            elif decision.get("clear_visible"):
                reason = "stale_grace_expired"
            else:
                reason = str(decision.get("publish_rejected_reason") or "stale_grace_without_clear")
        self.log_subtitle_decision(decision, generation=generation)
        self.log_runtime_event(
            "SUBTITLE_STALE_GRACE_EXPIRY_DECISION",
            generation=generation,
            reason=reason,
            extra=(
                f"expired_sequence_id={expired_sequence_id}; "
                f"current_sequence_id={current_sequence_id}; "
                f"active_ocr_task_id={active_ocr_task_id}; "
                f"active_task_sequence_id={active_task_sequence_id}; "
                f"runtime_generation_before={runtime_generation_before}; "
                f"runtime_generation_after={runtime_generation_after}; "
                f"visible_cleared={visible_cleared}; "
                f"reschedule_ms={reschedule_ms}"
            ),
        )
        if dropped_result:
            self.log_translation_result_dropped(
                dropped_result,
                "subtitle_stale_grace_expired",
            )
        if visible_cleared:
            self.translation_ready.emit("", "", 0.0)
        return {
            "visible_cleared": visible_cleared,
            "reschedule_ms": reschedule_ms,
            "decision": decision,
        }

    def next_runtime_task(self, backend_name, pil_image):
        """Создаёт task_id и снимки generation/visual epoch для OCR-задачи."""
        dropped_result = None
        with QMutexLocker(self.mutex):
            self._runtime_task_seq += 1
            task_id = self._runtime_task_seq
            generation = self._runtime_generation
            visual_epoch = self._visual_epoch
            session_fp = overlay_freshness.session_fingerprint(self.session_data)
            if self._held_translation_result is not None:
                dropped_result = self._held_translation_result
                self._held_translation_result = None
            self._latest_ocr_task_id = task_id
            self._active_ocr_task_id = task_id
            self._active_ocr_source = ""
            self._ui_translation_apply_token = None
            force_refresh_request_id = int(self._force_refresh_active_request_id or 0)
            if force_refresh_request_id:
                self._force_refresh_task_requests[task_id] = force_refresh_request_id
                self._force_refresh_active_request_id = 0
        try:
            size = f"{pil_image.width}x{pil_image.height}"
        except Exception:
            size = "unknown"
        if dropped_result:
            self.log_translation_result_dropped(
                dropped_result,
                "newer_ocr_task_created",
                current_task_id=task_id,
            )
        self.log_runtime_event(
            "OCR_TASK_CREATED",
            task_id=task_id,
            generation=generation,
            extra=(
                f"backend={backend_name}; image_size={size}; visual_epoch={visual_epoch}; "
                f"force_refresh_request_id={force_refresh_request_id or 0}"
            ),
        )
        return task_id, generation, visual_epoch, session_fp

    def log_translation_result_dropped(self, result, reason, current_task_id=None):
        if not result:
            return
        self.log_online_publication(result, "dropped", reason)
        task_id = int(result.get("task_id", 0) or 0)
        generation = int(result.get("generation", 0) or 0)
        result_epoch = int(result.get("visual_epoch", 0) or 0)
        with QMutexLocker(self.mutex):
            current_epoch = int(self._visual_epoch or 0)
            gate_state = str(self._visual_text_last_state or "")
        wait_ms = int(max(0.0, time.time() - float(result.get("held_at", time.time()))) * 1000)
        enqueued_perf = float(result.get("enqueued_perf") or 0.0)
        freshness_wait_ms = round(
            max(0.0, time.perf_counter() - enqueued_perf) * 1000.0,
            3,
        ) if enqueued_perf else float(wait_ms)
        result.setdefault("pipeline_timings", {})["freshness_wait_ms"] = freshness_wait_ms
        extra = (
            f"result_type={result.get('result_type', '')}; result_epoch={result_epoch}; "
            f"current_epoch={current_epoch}; gate_state={gate_state}; wait_ms={wait_ms}; "
            f"current_task_id={current_task_id if current_task_id is not None else self._latest_ocr_task_id}"
        )
        self.log_runtime_event(
            "TRANSLATION_RESULT_DROPPED_VISUAL_STALE",
            task_id=task_id,
            generation=generation,
            text=str(result.get("source") or ""),
            reason=reason,
            extra=extra,
        )
        classification = result.get("ocr_classification")
        if isinstance(classification, dict) and classification:
            self.log_ocr_result_outcome(
                classification,
                final_outcome="translation_rejected",
                task_id=task_id,
                generation=generation,
                subtitle_sequence_id=result.get("subtitle_sequence_id"),
                exact_reason_code=str(reason or "translation_result_rejected"),
            )
        self.finish_force_refresh_task(
            task_id,
            success=False,
            reason=str(reason or "translation_result_rejected"),
        )

    def log_online_publication(self, result, phase, reason=""):
        if result.get("translator_backend_name") == "online" or result.get("result_type") == "backend:online":
            requests = result.get("backend_requests") or []
            metrics = dict(requests[-1]) if requests else {}
            metrics.setdefault("enqueued_perf", result.get("enqueued_perf"))
            publication_event(metrics, phase, "overlay", result.get("task_id"), result.get("generation"), reason)

    def mark_visual_transition_pending(self, state):
        with QMutexLocker(self.mutex):
            if not self._visual_transition_pending:
                self._visual_transition_pending_since = time.time()
            self._visual_transition_pending = True
            self._visual_transition_pending_state = str(state or "")
            self._ui_translation_apply_token = None

    def confirm_visual_transition(self, state):
        dropped_result = None
        dropped_restore = None
        with QMutexLocker(self.mutex):
            self._visual_epoch += 1
            epoch = self._visual_epoch
            self._visual_transition_pending = False
            self._visual_transition_pending_since = 0.0
            self._visual_transition_pending_state = ""
            dropped_result = self._held_translation_result
            self._held_translation_result = None
            self._ui_translation_apply_token = None
            dropped_restore = self._post_clear_same_source_candidate
            self._post_clear_same_source_candidate = None
        if dropped_result:
            self.log_translation_result_dropped(dropped_result, f"visual_transition_confirmed:{state}")
        if dropped_restore:
            self.log_runtime_event(
                "OCR_POST_CLEAR_SAME_SOURCE_RESTORE_DROPPED",
                task_id=int(dropped_restore.get("task_id", 0) or 0),
                generation=int(dropped_restore.get("generation", 0) or 0),
                text=str(dropped_restore.get("text") or ""),
                reason=f"visual_transition_confirmed:{state}",
                extra=f"candidate_epoch={dropped_restore.get('visual_epoch')}; current_epoch={epoch}",
            )
        if str(state or "") in {"changed", "empty"}:
            self.cancel_active_translation_request(f"visual_transition_{state}")
        return epoch

    def set_active_runtime_source(self, task_id, source):
        with QMutexLocker(self.mutex):
            if int(task_id) == int(self._active_ocr_task_id):
                self._active_ocr_source = str(source or "")

    def drop_post_clear_restore_candidate(self, reason, current_task_id=0, text=""):
        with QMutexLocker(self.mutex):
            candidate = self._post_clear_same_source_candidate
            if not candidate:
                return False
            if current_task_id and int(current_task_id) <= int(candidate.get("task_id", 0) or 0):
                return False
            dropped = dict(candidate)
            self._post_clear_same_source_candidate = None
        self.log_runtime_event(
            "OCR_POST_CLEAR_SAME_SOURCE_RESTORE_DROPPED",
            task_id=int(dropped.get("task_id", 0) or 0),
            generation=int(dropped.get("generation", 0) or 0),
            text=str(dropped.get("text") or ""),
            reason=str(reason or "candidate_dropped"),
            extra=f"current_task_id={current_task_id}; current_text={str(text or '')[:180]!r}",
        )
        return True

    def queue_translation_result(
        self,
        *,
        task_id,
        generation,
        visual_epoch,
        session_fingerprint,
        source,
        translated,
        proc_time,
        result_type,
        ocr_ms=0,
        memory_lookup_ms=0,
        backend_ms=0,
        translation_stage_ms=0,
        total_ms=0,
        from_cache=False,
        growth_accept_source="",
        growth_protection_sec=0.0,
        clear_restore_state=False,
        ocr_result_id="",
        source_hash="",
        translator_backend_name="",
        pipeline_started_perf=0.0,
        pipeline_timings=None,
        backend_requests=None,
        post_publish_persistence=None,
        ocr_classification=None,
    ):
        enqueue_started_perf = time.perf_counter()
        subtitle_sequence_id = self.subtitle_sequence_for_task(task_id)
        subtitle_settings = self.subtitle_settings_snapshot()
        result = {
            "task_id": int(task_id),
            "generation": int(generation),
            "visual_epoch": int(visual_epoch),
            "session_fingerprint": str(session_fingerprint or ""),
            "source": str(source or ""),
            "translated": str(translated or ""),
            "proc_time": float(proc_time or 0.0),
            "result_type": str(result_type or "backend"),
            "held_at": time.time(),
            "ocr_ms": int(ocr_ms or 0),
            "memory_lookup_ms": int(memory_lookup_ms or 0),
            "backend_ms": int(backend_ms or 0),
            "translation_stage_ms": int(translation_stage_ms or 0),
            "total_ms": int(total_ms or 0),
            "from_cache": bool(from_cache),
            "growth_accept_source": str(growth_accept_source or ""),
            "growth_protection_sec": float(growth_protection_sec or 0.0),
            "clear_restore_state": bool(clear_restore_state),
            "ocr_result_id": str(ocr_result_id or ""),
            "source_hash": str(source_hash or ""),
            "translator_backend_name": str(translator_backend_name or ""),
            "pipeline_started_perf": float(pipeline_started_perf or 0.0),
            "pipeline_timings": dict(pipeline_timings or {}),
            "backend_requests": list(backend_requests or []),
            "subtitle_sequence_id": int(subtitle_sequence_id),
            "subtitle_background_mode": subtitle_settings["background_mode"],
            "subtitle_display_policy": subtitle_settings["display_policy"],
            "post_publish_persistence": post_publish_persistence,
            "ocr_classification": dict(ocr_classification or {}),
            "enqueued_perf": 0.0,
            "ui_signal_emitted_perf": 0.0,
        }
        replaced = None
        with QMutexLocker(self.mutex):
            valid, reason = overlay_freshness.validate_translation_result(
                result,
                current_generation=self._runtime_generation,
                current_visual_epoch=self._visual_epoch,
                latest_task_id=self._latest_ocr_task_id,
                active_task_id=self._active_ocr_task_id,
                active_source=self._active_ocr_source,
                current_session_fingerprint=overlay_freshness.session_fingerprint(self.session_data),
                visual_pending=False,
                running=self.is_running,
                paused=self.user_interaction_paused,
                stopping=self._session_stopping,
            )
            if valid:
                if subtitle_sequence_id and not self._subtitle_gate.is_current(subtitle_sequence_id):
                    valid = False
                    reason = "subtitle_sequence_not_current"
            if valid:
                replaced = self._held_translation_result
                self._held_translation_result = result
                gate_state = str(self._visual_text_last_state or "")
                pending_state = str(self._visual_transition_pending_state or "")
                pending = bool(self._visual_transition_pending)
            else:
                gate_state = str(self._visual_text_last_state or "")
                pending_state = str(self._visual_transition_pending_state or "")
                pending = bool(self._visual_transition_pending)
        if not valid:
            self.log_translation_result_dropped(result, reason)
            return False
        if replaced:
            self.log_translation_result_dropped(replaced, "superseded_by_newer_result_same_task")
        result["enqueued_perf"] = time.perf_counter()
        result["pipeline_timings"]["result_enqueue_ms"] = round(
            (result["enqueued_perf"] - enqueue_started_perf) * 1000.0,
            3,
        )
        self.log_runtime_event(
            "TRANSLATION_RESULT_HELD_VISUAL_PENDING",
            task_id=task_id,
            generation=generation,
            text=source,
            reason="awaiting next stable visual frame",
            extra=(
                f"result_type={result_type}; result_epoch={visual_epoch}; current_epoch={self._visual_epoch}; "
                f"gate_state={gate_state}; visual_pending={pending}; pending_state={pending_state}"
            ),
        )
        self.log_online_publication(result, "held", "visual_transition_pending" if pending else "await_fresh_visual")
        if subtitle_settings["background_mode"] == GAME_BACKGROUND:
            return self.release_held_translation_result("game_background_ocr")
        return True

    def release_held_translation_result(self, gate_state="no_change"):
        subtitle_decisions = []
        with QMutexLocker(self.mutex):
            self._visual_transition_pending = False
            self._visual_transition_pending_since = 0.0
            self._visual_transition_pending_state = ""
            result = self._held_translation_result
            if not result:
                return False
            valid, reason = overlay_freshness.validate_translation_result(
                result,
                current_generation=self._runtime_generation,
                current_visual_epoch=self._visual_epoch,
                latest_task_id=self._latest_ocr_task_id,
                active_task_id=self._active_ocr_task_id,
                active_source=self._active_ocr_source,
                current_session_fingerprint=overlay_freshness.session_fingerprint(self.session_data),
                visual_pending=self._visual_transition_pending,
                running=self.is_running,
                paused=self.user_interaction_paused,
                stopping=self._session_stopping,
            )
            subtitle_sequence_id = int(result.get("subtitle_sequence_id", 0) or 0)
            if valid and subtitle_sequence_id:
                completed = self._subtitle_gate.mark_translation_completed(subtitle_sequence_id)
                subtitle_decisions.append(completed)
                if not completed.get("publish_allowed"):
                    valid = False
                    reason = str(completed.get("publish_rejected_reason") or "subtitle_sequence_rejected")
                else:
                    published = self._subtitle_gate.mark_translation_published(
                        subtitle_sequence_id,
                    )
                    subtitle_decisions.append(published)
                    if not published.get("publish_allowed"):
                        valid = False
                        reason = str(published.get("publish_rejected_reason") or "subtitle_publish_rejected")
            self._held_translation_result = None
            if not valid:
                apply_ms = 0
            else:
                apply_started = time.perf_counter()
                now = time.time()
                source = str(result.get("source") or "")
                translated = str(result.get("translated") or "")
                self.last_text = source
                self.last_good_source = source
                self.last_good_translation = translated
                self.last_good_time = now
                self._recent_good_source = source
                self._recent_good_translation = translated
                self._recent_good_time = now
                self._post_clear_suspicious_text = ""
                self._post_clear_suspicious_since = 0.0
                self.cancel_post_clear_dense_state_for_new_text_locked(source)
                self.reset_ocr_growth_pending_locked()
                if result.get("clear_restore_state"):
                    self._last_cleared_source = ""
                    self._last_cleared_time = 0.0
                    self._last_cleared_translation = ""
                    self._last_cleared_reason = ""
                    self._last_cleared_generation = 0
                    self._post_clear_same_source_candidate = None
                growth_source = str(result.get("growth_accept_source") or "")
                if growth_source:
                    protected_until = now + float(result.get("growth_protection_sec") or 0.0)
                    self._ocr_growth_apply_protected_until = protected_until
                    self._ocr_growth_apply_protected_text = source
                    self._ocr_growth_apply_protected_generation = self._runtime_generation
                self._ui_translation_apply_token = dict(result)
                apply_ms = int((time.perf_counter() - apply_started) * 1000)
        if not valid:
            for decision in subtitle_decisions:
                self.log_subtitle_decision(
                    decision,
                    task_id=int(result.get("task_id", 0) or 0),
                    generation=int(result.get("generation", 0) or 0),
                )
            self.log_translation_result_dropped(result, reason)
            return False

        wait_ms = int(max(0.0, time.time() - float(result.get("held_at", time.time()))) * 1000)
        enqueued_perf = float(result.get("enqueued_perf") or 0.0)
        freshness_wait_ms = round(
            max(0.0, time.perf_counter() - enqueued_perf) * 1000.0,
            3,
        ) if enqueued_perf else float(wait_ms)
        task_id = int(result.get("task_id", 0) or 0)
        generation = int(result.get("generation", 0) or 0)
        source = str(result.get("source") or "")
        translated = str(result.get("translated") or "")
        for decision in subtitle_decisions:
            self.log_subtitle_decision(decision, task_id=task_id, generation=generation)
        growth_source = str(result.get("growth_accept_source") or "")
        if growth_source:
            self.log_runtime_event(
                "OCR_TEXT_GROWTH_APPLY_PROTECTED",
                task_id=task_id,
                generation=generation,
                text=source,
                reason=f"translation applied from growth pending via {growth_source}",
                extra=f"protect_sec={float(result.get('growth_protection_sec') or 0.0):.2f}s",
            )
        self.log_runtime_event(
            "TRANSLATION_RESULT_RELEASED_VISUAL_STABLE",
            task_id=task_id,
            generation=generation,
            text=source,
            reason="visual frame remained stable",
            extra=(
                f"result_type={result.get('result_type')}; result_epoch={result.get('visual_epoch')}; "
                f"current_epoch={self._visual_epoch}; gate_state={gate_state}; wait_ms={wait_ms}"
            ),
        )
        self.log_online_publication(result, "released", "visual_stable")
        self.log_runtime_event(
            "TRANSLATION_PIPELINE_TIMING",
            task_id=task_id,
            generation=generation,
            text=source,
            extra=(
                f"ocr_ms={result.get('ocr_ms', 0)}; memory_lookup_ms={result.get('memory_lookup_ms', 0)}; "
                f"backend_ms={result.get('backend_ms', 0)}; barrier_wait_ms={wait_ms}; apply_ms={apply_ms}; "
                f"translation_stage_ms={result.get('translation_stage_ms', 0)}; total_before_barrier_ms={result.get('total_ms', 0)}; "
                f"total_with_barrier_ms={int(result.get('total_ms', 0)) + wait_ms + apply_ms}; from_cache={result.get('from_cache', False)}"
            ),
        )
        self.log_runtime_event(
            "OCR_TASK_APPLIED",
            task_id=task_id,
            generation=generation,
            text=source,
            extra=f"result_type={result.get('result_type')}; visual_epoch={result.get('visual_epoch')}; barrier_wait_ms={wait_ms}",
        )
        self.finish_force_refresh_task(
            task_id,
            success=True,
            reason="translation_published",
        )
        classification = result.get("ocr_classification")
        if isinstance(classification, dict) and classification:
            self.log_ocr_result_outcome(
                classification,
                final_outcome="translation_published",
                task_id=task_id,
                generation=generation,
                subtitle_sequence_id=result.get("subtitle_sequence_id"),
                exact_reason_code="published_after_freshness_and_sequence_checks",
            )
        signal_emitted_perf = time.perf_counter()
        result["ui_signal_emitted_perf"] = signal_emitted_perf
        with QMutexLocker(self.mutex):
            token = self._ui_translation_apply_token
            if token and int(token.get("task_id", -1)) == task_id:
                token["ui_signal_emitted_perf"] = signal_emitted_perf
                token.setdefault("pipeline_timings", {})["freshness_wait_ms"] = freshness_wait_ms
        translator_core.log_backend_pipeline_event(
            result.get("translator_backend_name"),
            "pipeline_result_released",
            {
                "ocr_result_id": result.get("ocr_result_id"),
                "source_hash": result.get("source_hash"),
                "source_char_count": len(source),
                "target_lang": str(((self.session_data or {}).get("translation", {}) or {}).get("target_lang", "")),
                "backend_request_count": len(result.get("backend_requests") or []),
                "whole_text_enabled": bool((result.get("pipeline_timings") or {}).get("whole_text_enabled", False)),
                "cache_prompt_enabled": bool((result.get("pipeline_timings") or {}).get("cache_prompt_enabled", False)),
                "freshness_wait_ms": freshness_wait_ms,
                "result_enqueue_ms": (result.get("pipeline_timings") or {}).get("result_enqueue_ms", 0.0),
            },
        )
        self.translation_ready.emit(source, translated, float(result.get("proc_time", 0.0)))
        persistence_job = result.get("post_publish_persistence")
        if callable(persistence_job):
            persistence_queued = translation_pipeline_persistence.submit(
                str(result.get("ocr_result_id") or f"subtitle_{subtitle_sequence_id}_{task_id}"),
                persistence_job,
            )
            self.log_runtime_event(
                "SUBTITLE_PERSISTENCE_QUEUED" if persistence_queued else "SUBTITLE_PERSISTENCE_REJECTED",
                task_id=task_id,
                generation=generation,
                reason="accepted translation published before persistence enqueue",
                extra=f"subtitle_sequence_id={subtitle_sequence_id}",
            )
        return True

    def is_ui_translation_result_current(self, source, translated):
        with QMutexLocker(self.mutex):
            token = self._ui_translation_apply_token
            if not token:
                return False
            if str(token.get("source") or "") != str(source or ""):
                return False
            if str(token.get("translated") or "") != str(translated or ""):
                return False
            subtitle_sequence_id = int(token.get("subtitle_sequence_id", 0) or 0)
            if subtitle_sequence_id and not self._subtitle_gate.is_current(subtitle_sequence_id):
                return False
            valid, _ = overlay_freshness.validate_translation_result(
                token,
                current_generation=self._runtime_generation,
                current_visual_epoch=self._visual_epoch,
                latest_task_id=self._latest_ocr_task_id,
                active_task_id=self._active_ocr_task_id,
                active_source=self._active_ocr_source,
                current_session_fingerprint=overlay_freshness.session_fingerprint(self.session_data),
                visual_pending=self._visual_transition_pending,
                running=self.is_running,
                paused=self.user_interaction_paused,
                stopping=self._session_stopping,
            )
            return bool(valid)

    def current_ui_translation_result_token(self, source, translated):
        with QMutexLocker(self.mutex):
            token = self._ui_translation_apply_token
            if not token:
                return None
            if str(token.get("source") or "") != str(source or ""):
                return None
            if str(token.get("translated") or "") != str(translated or ""):
                return None
            return dict(token)

    def begin_session_stopping(self):
        dropped_result = None
        with QMutexLocker(self.mutex):
            self._session_stopping = True
            self._runtime_generation += 1
            dropped_result = self._held_translation_result
            self._held_translation_result = None
            self._ui_translation_apply_token = None
            self._post_clear_same_source_candidate = None
            force_refresh_requests = self.clear_force_refresh_requests_locked()
            subtitle_reset = self.reset_subtitle_gate_locked("session_stopping")
            self.reset_game_background_paddle_state_locked("session_stopping")
            self.is_running = False
            self._scheduler_condition.wakeAll()
            generation = self._runtime_generation
        self.reject_cleared_force_refresh_requests(
            force_refresh_requests,
            generation=generation,
            reason="session_stopping",
        )
        self.log_paddle_cadence_summary("session_stopping")
        if dropped_result:
            self.log_translation_result_dropped(dropped_result, "session_stopping")
        self.log_runtime_event("OCR_TASKS_INVALIDATED", generation=generation, reason="session_stopping")
        self.log_subtitle_decision(subtitle_reset, generation=generation)
        self.cancel_active_translation_request("session_stopping")
        recorder = self._live_ocr_crop_recorder
        if recorder is not None:
            recorder.stop_accepting("session_stopping")

    def is_runtime_task_stale(self, generation):
        """True, если пользователь уже поменял режим/скрыл окно/остановил OCR после старта задачи."""
        with QMutexLocker(self.mutex):
            return (generation != self._runtime_generation) or (not self.is_running) or self.user_interaction_paused or self._session_stopping

    def was_task_invalidated_by_no_text(self, generation):
        """True, если задача старше момента, когда visual-gate очистил область как пустую."""
        with QMutexLocker(self.mutex):
            return bool(self._no_text_generation and generation < self._no_text_generation)

    def invalidate_runtime_tasks(self, reason=""):
        """Помечает текущие OCR-задачи устаревшими без ожидания их завершения."""
        dropped_result = None
        with QMutexLocker(self.mutex):
            self._runtime_generation += 1
            generation = self._runtime_generation
            force_refresh_requests = self.clear_force_refresh_requests_locked()
            dropped_result = self._held_translation_result
            self._held_translation_result = None
            self._ui_translation_apply_token = None
            self._post_clear_same_source_candidate = None
            if (
                self._game_field_visual_gate is not None
                and normalize_subtitle_settings((self.session_data or {}).get("subtitles", {}))["background_mode"]
                != GAME_BACKGROUND
            ):
                self._game_field_visual_gate.reset("runtime_generation_invalidated")
            self.reset_game_background_paddle_state_locked(reason or "runtime_generation_invalidated")
            subtitle_reset = self.reset_subtitle_gate_locked(reason or "runtime_tasks_invalidated")
        self.reject_cleared_force_refresh_requests(
            force_refresh_requests,
            generation=generation,
            reason=f"runtime_invalidated:{reason or 'runtime_tasks_invalidated'}",
        )
        if dropped_result:
            self.log_translation_result_dropped(dropped_result, f"runtime_invalidated:{reason}")
        self.log_runtime_event("OCR_TASKS_INVALIDATED", generation=generation, reason=reason)
        self.log_subtitle_decision(subtitle_reset, generation=generation)
        self.cancel_active_translation_request(reason or "runtime_tasks_invalidated")

    def cancel_active_translation_request(self, reason=""):
        """Отменяет worker-запрос, не заменяя generation/task stale checks."""
        try:
            backend_name = current_translation_backend_from_session(self.session_data)
            cancelled = translator_core.cancel_active_translation(
                reason=reason,
                backend_name=backend_name,
            )
            if cancelled:
                self.log_runtime_event(
                    "TRANSLATION_WORKER_REQUEST_CANCELLED",
                    generation=self._runtime_generation,
                    reason=reason,
                    extra=f"backend={backend_name}; cancelled={cancelled}",
                )
        except Exception:
            pass

    @pyqtSlot(int)
    def start_engine(self, hwnd):
        """
        Старт захвата и перевода.

        hwnd — дескриптор окна игры, выбранного в лаунчере.
        Координаты кропа здесь уже должны быть НЕ экранными,
        а относительными координатами внутри окна игры:

            left_offset = overlay_left - game_window_left
            top_offset  = overlay_top  - game_window_top
        """
        force_refresh_requests = []
        with QMutexLocker(self.mutex):
            if self.is_running:
                return

            if not self.stop_rust_capture_engine_locked("before_start_engine"):
                self.log_runtime_event(
                    "RUST_CAPTURE_START_ABORTED",
                    reason="previous_capture_stop_failed",
                )
                return

            self.game_hwnd = int(hwnd)
            self.is_running = True
            self.user_interaction_paused = False
            self.last_text = ""
            self.empty_frames_counter = 0
            self.empty_started_time = 0.0
            self._ocr_backend_busy = False
            self._no_text_generation = 0
            self.last_good_translation = ""
            self.last_good_source = ""
            self.last_good_time = 0.0
            self._recent_good_source = ""
            self._recent_good_translation = ""
            self._recent_good_time = 0.0
            self._last_display_clear_time = 0.0
            self._post_clear_suspicious_text = ""
            self._post_clear_suspicious_since = 0.0
            self._last_cleared_source = ""
            self._last_cleared_time = 0.0
            self._last_cleared_translation = ""
            self._last_cleared_reason = ""
            self._last_cleared_generation = 0
            self._last_live_ocr_signature = ""
            self._last_live_ocr_backend = ""
            self._last_live_ocr_text = ""
            self._last_live_ocr_time = 0.0
            self._last_paddle_empty_signature = ""
            self._last_paddle_empty_handled_at = 0.0
            self.reset_paddle_cadence_locked()
            self.reset_live_text_stability_state_locked()
            self.reset_visual_text_gate_state_locked()
            self.reset_game_background_paddle_state_locked("engine_started")
            self._runtime_generation += 1
            self._no_text_generation = 0
            self._runtime_task_seq = 0
            self._latest_ocr_task_id = 0
            self._active_ocr_task_id = 0
            self._active_ocr_source = ""
            self._ui_translation_apply_token = None
            self._held_translation_result = None
            self._session_stopping = False
            self._ocr_backend_busy = False
            self._live_cycle_in_progress = False
            force_refresh_requests = self.clear_force_refresh_requests_locked()
            self._paddle_fast_first_ocr_after_preload_logged = False
            self._paddle_first_ocr_after_preload_logged = set()
            self._scheduler_condition.wakeAll()
            self._last_diag_signature = ""
            self._last_diag_time = 0.0
            self._diag_repeat_count = 0
            subtitle_reset = self.reset_subtitle_gate_locked("engine_started")

            cfg = self.session_data
            zone = cfg.get("monitor_zone", {})

            # В Rust/GDI передаём координаты области внутри окна игры.
            # Защита max() нужна, чтобы не передать отрицательные значения или нулевой размер.
            cx = max(0, int(zone.get("left_offset", 0)))
            cy = max(0, int(zone.get("top_offset", 0)))
            cw = max(1, int(zone.get("width", 300)))
            ch = max(1, int(zone.get("height", 150)))

            # Новый продвинутый режим для Windows 10/11.
            # Условие оставлено таким же по смыслу: Rust используется только если модуль есть
            # и ОС поддерживает Windows Graphics Capture.
            if game_capture_core is not None and sys.getwindowsversion().major >= 10:
                try:
                    print(f"[ДВИЖОК] Запуск RustCaptureEngine: crop=({cx},{cy},{cw},{ch})")
                    self.rust_engine = game_capture_core.RustCaptureEngine(hwnd, cx, cy, cw, ch)
                    self.capture_mode = "rust"
                    print("[ДВИЖОК] Rust-модуль активен.")
                except Exception as e:
                    # Если Rust не стартовал — не падаем, а автоматически уходим на старый метод.
                    print(f"[ДВИЖОК] Ошибка запуска Rust ({e}). Откат на GDI.")
                    self.activate_gdi_fallback_locked()
            else:
                # Старые ОС, отсутствие Rust-модуля или несовместимость — сразу GDI.
                self.activate_gdi_fallback_locked()

        self.reject_cleared_force_refresh_requests(
            force_refresh_requests,
            generation=self._runtime_generation,
            reason="engine_restarted",
        )
        self.ensure_live_ocr_crop_debug_recorder()
        if not self.isRunning():
            self.start(QThread.Priority.NormalPriority)
        self.log_subtitle_decision(subtitle_reset, generation=self._runtime_generation)

    def activate_gdi_fallback_locked(self):
        """
        Внутреннее переключение на GDI.
        Вызывать только когда mutex уже заблокирован.
        """
        self.stop_rust_capture_engine_locked("activate_gdi_fallback")
        self.capture_mode = "gdi"
        print("[ДВИЖОК] Активирован резервный GDI-захват.")

    def stop_rust_capture_engine_locked(self, reason):
        """Detach and explicitly stop the current WGC session. Mutex must be held."""
        engine = self.rust_engine
        self.rust_engine = None
        if engine is None:
            return True
        started = time.perf_counter()
        try:
            stop_method = getattr(engine, "stop", None)
            if not callable(stop_method):
                raise RuntimeError("RustCaptureEngine.stop is unavailable")
            stop_method()
            self.log_runtime_event(
                "RUST_CAPTURE_STOPPED",
                reason=str(reason or "capture_stop"),
                extra=f"elapsed_ms={round((time.perf_counter() - started) * 1000.0, 3)}",
            )
            return True
        except Exception as exc:
            self.log_runtime_event(
                "RUST_CAPTURE_STOP_ERROR",
                reason=f"{type(exc).__name__}: {exc}",
                extra=(
                    f"stop_reason={str(reason or 'capture_stop')}; "
                    f"elapsed_ms={round((time.perf_counter() - started) * 1000.0, 3)}"
                ),
            )
            return False

    def activate_gdi_fallback(self):
        """
        Потокобезопасное переключение на GDI.
        Используется, если Rust упал уже во время получения кадра.
        """
        with QMutexLocker(self.mutex):
            self.activate_gdi_fallback_locked()

    @pyqtSlot()
    def stop_engine(self):
        """Остановка потока захвата и перевода."""
        force_refresh_requests = []
        engine_was_running = False
        with QMutexLocker(self.mutex):
            if not self.is_running:
                self.stop_rust_capture_engine_locked("stop_engine_not_running")
                force_refresh_requests = self.clear_force_refresh_requests_locked()
                generation = self._runtime_generation
            else:
                engine_was_running = True
                self.is_running = False
                self.stop_rust_capture_engine_locked("stop_engine")
                self._runtime_generation += 1
                force_refresh_requests = self.clear_force_refresh_requests_locked()
                self._scheduler_condition.wakeAll()
                subtitle_reset = self.reset_subtitle_gate_locked("stop_engine")
                self.reset_game_background_paddle_state_locked("stop_engine")
                generation = self._runtime_generation
        self.reject_cleared_force_refresh_requests(
            force_refresh_requests,
            generation=generation,
            reason="stop_engine" if engine_was_running else "stop_engine_not_running",
        )
        if not engine_was_running:
            return
        self.log_paddle_cadence_summary("stop_engine")
        self.log_runtime_event("ENGINE_STOP_REQUESTED", reason="stop_engine")
        self.log_subtitle_decision(subtitle_reset, generation=generation)
        self.cancel_active_translation_request("stop_engine")
        print("[ДВИЖОК] Поток захвата остановлен.")

    @pyqtSlot()
    def reset_memory(self):
        """
        Сброс памяти OCR/перевода.

        Нужен при смене языка/режима, чтобы:
        - старый перевод не висел после переключения режима;
        - visual-gate начал новую серию кадров без старой маски;
        - устаревшие OCR-задачи не применились после смены режима.
        """
        with QMutexLocker(self.mutex):
            self.last_text = ""
            self.empty_frames_counter = 0
            self.last_good_translation = ""
            self.last_good_source = ""
            self.last_good_time = 0.0
            self._recent_good_source = ""
            self._recent_good_translation = ""
            self._recent_good_time = 0.0
            self._last_display_clear_time = 0.0
            self._post_clear_suspicious_text = ""
            self._post_clear_suspicious_since = 0.0
            self._last_cleared_source = ""
            self._last_cleared_time = 0.0
            self._last_cleared_translation = ""
            self._last_cleared_reason = ""
            self._last_cleared_generation = 0
            self._last_live_ocr_signature = ""
            self._last_live_ocr_backend = ""
            self._last_live_ocr_text = ""
            self._last_live_ocr_time = 0.0
            self._last_paddle_empty_signature = ""
            self._last_paddle_empty_handled_at = 0.0
            self.reset_paddle_cadence_locked()
            self._scheduler_condition.wakeAll()
            self.reset_live_text_stability_state_locked()
            self.reset_visual_text_gate_state_locked()
            self.reset_game_background_paddle_state_locked("reset_memory")
            self._runtime_generation += 1
            self._no_text_generation = 0
            self._runtime_task_seq = 0
            self._latest_ocr_task_id = 0
            self._active_ocr_task_id = 0
            self._active_ocr_source = ""
            self._ui_translation_apply_token = None
            self._held_translation_result = None
            force_refresh_requests = self.clear_force_refresh_requests_locked()
            self._last_diag_signature = ""
            self._last_diag_time = 0.0
            self._diag_repeat_count = 0
            subtitle_reset = self.reset_subtitle_gate_locked("reset_memory")
            generation = self._runtime_generation
        self.reject_cleared_force_refresh_requests(
            force_refresh_requests,
            generation=generation,
            reason="reset_memory",
        )
        self.log_subtitle_decision(subtitle_reset, generation=generation)
        self.cancel_active_translation_request("reset_memory")

    @pyqtSlot()
    def async_load_model(self):
        """
        Предзагрузка модели в процессе оверлея.

        Важный факт: лаунчер запускает оверлей через QProcess, то есть отдельным процессом.
        Модель, загруженная в лаунчере, не переносится в память оверлея автоматически.
        Поэтому оверлей тоже должен прогреть offline-модель, иначе первый offline-перевод
        может дать фриз уже внутри оверлея.
        """
        with QMutexLocker(self.mutex):
            if self.is_loading_model:
                return
            self.is_loading_model = True

        # Перед загрузкой новой модели чистим VRAM/RAM, как было задумано в архитектуре.
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

        import gc
        gc.collect()

        try:
            translator_core.load_model()
        finally:
            with QMutexLocker(self.mutex):
                self.is_loading_model = False
            self.model_loaded_signal.emit()

    def run(self):
        """
        Главный цикл рабочего потока.

        Он не запускается при открытии оверлея в режиме настройки.
        Он запускается только при переходе в режим перевода через Ctrl+Alt+Shift+P.
        """
        from PIL import Image

        while True:
            start_time = time.time()

            with QMutexLocker(self.mutex):
                keep_running = self.is_running
                paused_by_ui = self.user_interaction_paused
                current_hwnd = self.game_hwnd if not self.is_loading_model and not paused_by_ui else 0
                mode = self.capture_mode
                engine_instance = self.rust_engine
                plate_context = (capture_scope(self.session_data), getattr(self, "_adaptive_capture_revision", 0),
                                 bool((self.session_data.get("visuals") or {}).get("adaptive_plate_enabled", False)))

            if not keep_running:
                break

            if current_hwnd and win32gui and win32gui.IsWindow(current_hwnd):
                if mode == "rust" and engine_instance is not None:
                    self.process_next_frame_rust(engine_instance, Image, plate_context=plate_context)
                else:
                    self.process_next_frame_gdi(current_hwnd, Image)

            # Цикл не должен жечь CPU. Интервал можно будет позже вынести в настройки.
            processing_time = int((time.time() - start_time) * 1000)
            sleep_time = max(25, 180 - processing_time)
            self.msleep(sleep_time)

    def adaptive_capture_context(self):
        with QMutexLocker(self.mutex):
            cfg = self.session_data or {}
            return (capture_scope(cfg), getattr(self, "_adaptive_capture_revision", 0),
                    bool((cfg.get("visuals") or {}).get("adaptive_plate_enabled", False)))

    def publish_adaptive_frame(self, image, context):
        if context[2]:
            self.adaptive_plate_sample.emit((context[0], context[1], sample_background(image)))

    def process_next_frame_rust(self, engine_instance, Image_module, plate_context=None):
        """Получение следующего кадра через Rust/Windows Graphics Capture."""
        if plate_context is None:
            plate_context = self.adaptive_capture_context()
        recorder = self._live_ocr_crop_recorder
        debug_started_at = time.time() if recorder is not None else 0.0
        debug_started_perf = time.perf_counter() if recorder is not None else 0.0
        try:
            frame_data = engine_instance.get_next_frame()
            if frame_data is None:
                return

            raw_bytes, width, height = frame_data
            if width <= 0 or height <= 0 or not raw_bytes:
                raise ValueError("invalid Rust capture frame buffer")

            pil_image = Image_module.frombytes("RGBA", (width, height), raw_bytes)
        except Exception as e:
            if recorder is not None:
                self.run_optional_ocr_diagnostic(
                    "rust_capture_recorder",
                    lambda: self.record_live_ocr_debug_capture_error(
                        "rust",
                        debug_started_at,
                        debug_started_perf,
                        e,
                    ),
                    generation=self._runtime_generation,
                )
            self.log_runtime_event(
                "CAPTURE_RUST_FAILED",
                generation=self._runtime_generation,
                reason=f"{type(e).__name__}: {e}",
                extra="stage=capture",
            )
            print(f"[RUST ERROR]: {e}. Переключаюсь на GDI.")
            self.publish_adaptive_frame(None, plate_context)
            self.activate_gdi_fallback()
            return

        self.publish_adaptive_frame(pil_image, plate_context)
        capture_context = None
        if recorder is not None:
            capture_context = self.run_optional_ocr_diagnostic(
                "rust_capture_context",
                lambda: self.live_ocr_debug_capture_context(
                    capture_backend="rust",
                    capture_started_at=debug_started_at,
                    capture_started_perf=debug_started_perf,
                    crop_size=pil_image.size,
                ),
                generation=self._runtime_generation,
            )
        self._ocr_pipeline_stage = "capture_completed"
        with QMutexLocker(self.mutex):
            self._live_cycle_in_progress = True
        try:
            self.process_ocr_and_translation(pil_image, capture_context=capture_context)
        except Exception as exc:
            self.log_ocr_pipeline_error(
                exc,
                capture_mode="rust",
                stage=str(self._ocr_pipeline_stage or "capture_completed"),
                traceback_text=traceback.format_exc(),
            )
        finally:
            with QMutexLocker(self.mutex):
                self._live_cycle_in_progress = False
            self.finish_unbound_force_refresh("cycle_finished_without_ocr_task")

    def process_next_frame_gdi(self, hwnd, Image_module):
        """
        Резервный старый метод захвата через Win32 API / PrintWindow.

        Используется:
        - на старых ОС;
        - если Rust-модуль отсутствует;
        - если Windows Graphics Capture не сработал;
        - если игра блокирует новый способ захвата.
        """
        import win32ui

        hwnd_dc, mfc_dc, save_dc, bitmap = None, None, None, None
        plate_context = self.adaptive_capture_context()
        recorder = self._live_ocr_crop_recorder
        debug_started_at = time.time() if recorder is not None else 0.0
        debug_started_perf = time.perf_counter() if recorder is not None else 0.0
        pil_image = None
        capture_context = None
        capture_error = None
        try:
            with QMutexLocker(self.mutex):
                cfg = self.session_data.copy()
                plate_context = (capture_scope(cfg), getattr(self, "_adaptive_capture_revision", 0),
                                 bool((cfg.get("visuals") or {}).get("adaptive_plate_enabled", False)))
            zone = cfg.get("monitor_zone", {})

            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width, height = right - left, bottom - top
            if width <= 0 or height <= 0:
                return

            hwnd_dc = win32gui.GetWindowDC(hwnd)
            mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
            save_dc = mfc_dc.CreateCompatibleDC()

            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
            save_dc.SelectObject(bitmap)

            # 2 — попытка захватить client/window content в современных приложениях.
            # 0 — запасной вариант, если первый вызов не дал результата.
            if not user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2):
                user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 0)

            bmpinfo = bitmap.GetInfo()
            bmpstr = bitmap.GetBitmapBits(True)

            full_img = Image_module.frombuffer(
                "RGB",
                (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
                bmpstr,
                "raw",
                "BGRX",
                0,
                1,
            )

            # Здесь используем left_offset/top_offset как координаты внутри окна игры.
            x = max(0, int(zone.get("left_offset", 0)))
            y = max(0, int(zone.get("top_offset", 0)))
            w = max(1, int(zone.get("width", 300)))
            h = max(1, int(zone.get("height", 150)))

            crop_box = (
                x,
                y,
                min(width, x + w),
                min(height, y + h),
            )

            if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
                return

            pil_image = full_img.crop(crop_box)
        except Exception as e:
            capture_error = e
            if recorder is not None:
                self.run_optional_ocr_diagnostic(
                    "gdi_capture_recorder",
                    lambda: self.record_live_ocr_debug_capture_error(
                        "gdi",
                        debug_started_at,
                        debug_started_perf,
                        e,
                    ),
                    generation=self._runtime_generation,
                )
            self.log_runtime_event(
                "CAPTURE_GDI_FAILED",
                generation=self._runtime_generation,
                reason=f"{type(e).__name__}: {e}",
                extra="stage=capture",
            )
            print(f"[GDI ERROR]: {e}")
        finally:
            # Гарантированная очистка GDI-дескрипторов.
            # Это важно, иначе при долгой игре можно накопить утечки ресурсов Windows.
            try:
                if bitmap:
                    win32gui.DeleteObject(bitmap.GetHandle())
                if save_dc:
                    save_dc.DeleteDC()
                if mfc_dc:
                    mfc_dc.DeleteDC()
                if hwnd_dc and hwnd:
                    win32gui.ReleaseDC(hwnd, hwnd_dc)
            except Exception:
                pass

        if capture_error is not None or pil_image is None:
            self.publish_adaptive_frame(None, plate_context)
            return
        self.publish_adaptive_frame(pil_image, plate_context)
        if recorder is not None:
            capture_context = self.run_optional_ocr_diagnostic(
                "gdi_capture_context",
                lambda: self.live_ocr_debug_capture_context(
                    capture_backend="gdi",
                    capture_started_at=debug_started_at,
                    capture_started_perf=debug_started_perf,
                    crop_size=pil_image.size,
                    crop_box=crop_box,
                    game_window_rect=(left, top, right, bottom),
                ),
                generation=self._runtime_generation,
            )
        self._ocr_pipeline_stage = "capture_completed"
        with QMutexLocker(self.mutex):
            self._live_cycle_in_progress = True
        try:
            self.process_ocr_and_translation(pil_image, capture_context=capture_context)
        except Exception as exc:
            self.log_ocr_pipeline_error(
                exc,
                capture_mode="gdi",
                stage=str(self._ocr_pipeline_stage or "capture_completed"),
                traceback_text=traceback.format_exc(),
            )
        finally:
            with QMutexLocker(self.mutex):
                self._live_cycle_in_progress = False
            self.finish_unbound_force_refresh("cycle_finished_without_ocr_task")

    def live_ocr_debug_capture_context(
        self,
        *,
        capture_backend,
        capture_started_at,
        capture_started_perf,
        crop_size,
        crop_box=None,
        game_window_rect=None,
    ):
        """Собирает геометрию только при включённой crop-диагностике."""
        capture_finished_at = time.time()
        with QMutexLocker(self.mutex):
            cfg = json.loads(json.dumps(self.session_data or {}))
            hwnd = int(self.game_hwnd or 0)
        zone = dict(cfg.get("monitor_zone", {}) or {})
        width = int(crop_size[0]) if crop_size else 0
        height = int(crop_size[1]) if crop_size else 0
        x = max(0, int(zone.get("left_offset", 0) or 0))
        y = max(0, int(zone.get("top_offset", 0) or 0))
        if crop_box is None:
            crop_box = (x, y, x + width, y + height)
        client_rect = None
        try:
            local_client = win32gui.GetClientRect(hwnd)
            client_origin = win32gui.ClientToScreen(hwnd, (0, 0))
            client_rect = {
                "left": int(client_origin[0]),
                "top": int(client_origin[1]),
                "right": int(client_origin[0] + (local_client[2] - local_client[0])),
                "bottom": int(client_origin[1] + (local_client[3] - local_client[1])),
                "width": int(local_client[2] - local_client[0]),
                "height": int(local_client[3] - local_client[1]),
            }
        except Exception:
            pass
        return {
            "capture_backend": str(capture_backend or ""),
            "game_hwnd": hwnd,
            "capture_started_at": capture_started_at,
            "capture_finished_at": capture_finished_at,
            "capture_duration_ms": round(
                max(0.0, time.perf_counter() - float(capture_started_perf or time.perf_counter())) * 1000.0,
                3,
            ),
            "game_client_rect": client_rect,
            "game_window_rect": list(game_window_rect) if game_window_rect else None,
            "roi": {
                "left_offset": x,
                "top_offset": y,
                "width": int(zone.get("width", width) or width),
                "height": int(zone.get("height", height) or height),
            },
            "actual_crop_rect": {
                "left": int(crop_box[0]),
                "top": int(crop_box[1]),
                "right": int(crop_box[2]),
                "bottom": int(crop_box[3]),
                "width": width,
                "height": height,
            },
            "global_overlay_rect": {
                "left": zone.get("left"),
                "top": zone.get("top"),
                "width": zone.get("width"),
                "height": zone.get("height"),
            },
        }

    def record_live_ocr_debug_capture_error(
        self,
        capture_backend,
        capture_started_at,
        capture_started_perf,
        exc,
    ):
        recorder = self._live_ocr_crop_recorder
        if recorder is None:
            return
        with QMutexLocker(self.mutex):
            generation = int(self._runtime_generation)
            no_text_generation = int(self._no_text_generation)
            profile_name = str((self.session_data or {}).get("game_name") or "profile")
        recorder.record_capture_error(
            {
                "pipeline_mode": "live",
                "profile_name": profile_name,
                "runtime_generation": generation,
                "no_text_generation": no_text_generation,
                "capture_backend": str(capture_backend or ""),
                "capture_started_at": capture_started_at,
                "capture_finished_at": time.time(),
                "capture_duration_ms": round(
                    max(0.0, time.perf_counter() - float(capture_started_perf or time.perf_counter())) * 1000.0,
                    3,
                ),
                "error": f"{type(exc).__name__}: {exc}",
            }
        )

    def log_ocr_diagnostic(self, reason, text="", extra=""):
        """
        Пишет компактный диагностический лог причин, почему основной оверлей
        не вывел новую фразу. Это не лог переводов, а лог решений фильтра/стабилизатора.

        Файл: overlay_ocr_diagnostics.log

        Важная защита: OCR-цикл работает часто, поэтому одинаковые причины
        не пишутся в файл каждый кадр. Если причина повторяется, она логируется
        не чаще одного раза примерно в 2 секунды.
        """
        if not session_logging.raw_enabled():
            return
        try:
            now = time.time()
            clean_text = (text or "").replace("\n", " ").strip()
            clean_extra = (extra or "").replace("\n", " ").strip()

            # Подпись нужна для антиспама. Берём нормализованный текст,
            # чтобы одинаковые OCR-строки не забивали лог.
            normalized = self.normalize_for_compare(clean_text)[:160]
            signature = f"{reason}|{normalized}|{clean_extra[:120]}"

            repeat_info = ""
            if signature == self._last_diag_signature:
                self._diag_repeat_count += 1
                if now - self._last_diag_time < 2.0:
                    return
                repeat_info = f" | repeats_since_last_write={self._diag_repeat_count}"
                self._diag_repeat_count = 0
            else:
                self._last_diag_signature = signature
                self._diag_repeat_count = 0

            self._last_diag_time = now
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

            with session_logging.open_log(self.ocr_diagnostics_path, "a", encoding="utf-8") as f:
                f.write("=" * 70 + "\n")
                f.write(f"TIME: {timestamp}\n")
                f.write(f"CAPTURE_MODE: {self.capture_mode}\n")
                f.write(f"REASON: {reason}{repeat_info}\n")
                if clean_text:
                    f.write(f"TEXT: {clean_text[:700]}\n")
                if clean_extra:
                    f.write(f"DETAILS: {clean_extra[:700]}\n")
                f.write("=" * 70 + "\n\n")
        except Exception as e:
            # Диагностика не должна ломать перевод.
            print(f"[OCR DIAGNOSTIC LOG ERROR]: {e}", flush=True)

    def reset_live_text_stability_state_locked(self):
        """Сбрасывает post-OCR pending/debounce состояние. Вызывать под mutex."""
        self.reset_ocr_growth_pending_locked(reason="live_text_stability_reset")
        self._ocr_growth_pending_text = ""
        self._ocr_growth_pending_norm = ""
        self._ocr_growth_pending_since = 0.0
        self._ocr_growth_pending_updated_at = 0.0
        self._ocr_growth_pending_generation = 0
        self._ocr_growth_recent_text = ""
        self._ocr_growth_recent_norm = ""
        self._ocr_growth_recent_time = 0.0
        self._ocr_growth_recent_generation = 0
        self._ocr_growth_sequence_count = 0
        self._ocr_growth_context_until = 0.0
        self._ocr_growth_apply_protected_until = 0.0
        self._ocr_growth_apply_protected_text = ""
        self._ocr_growth_apply_protected_generation = 0
        self._repeated_empty_signature = ""
        self._repeated_empty_density = 0.0
        self._repeated_empty_count = 0
        self._repeated_empty_since = 0.0
        self._repeated_empty_last_time = 0.0
        self.reset_post_clear_dense_state_locked()

    def reset_post_clear_dense_state_locked(self):
        """Сбрасывает узкую защиту post-clear restore -> dense OCR_EMPTY."""
        self._post_clear_same_source_state = ""
        self._post_clear_same_source_time = 0.0
        self._post_clear_same_source_generation = 0
        self._post_clear_same_source_text = ""
        self._dense_empty_clear_deadline = 0.0
        self._dense_empty_clear_generation = 0
        self._dense_empty_clear_source = ""

    def cancel_post_clear_dense_state_for_new_text_locked(self, text):
        """Не даёт старому dense-empty deadline очистить уже принятый новый субтитр."""
        tracked_text = self._post_clear_same_source_text
        if not tracked_text:
            return
        current_norm = self.normalize_for_compare(text)
        tracked_norm = self.normalize_for_compare(tracked_text)
        if not current_norm or not tracked_norm:
            self.reset_post_clear_dense_state_locked()
            return
        if current_norm == tracked_norm:
            return
        try:
            similarity = difflib.SequenceMatcher(None, current_norm, tracked_norm).ratio()
        except Exception:
            similarity = 0.0
        if similarity < 0.96:
            self.reset_post_clear_dense_state_locked()

    def reset_ocr_growth_pending_locked(
        self,
        *,
        final_outcome="growth_pending_cancelled",
        reason="pending_state_reset",
    ):
        """Сбрасывает только pending typewriter-кандидат без трогания repeated-empty счётчика."""
        previous_text = str(getattr(self, "_ocr_growth_pending_text", "") or "")
        previous_task_id = int(getattr(self, "_ocr_growth_pending_task_id", 0) or 0)
        previous_generation = int(getattr(self, "_ocr_growth_pending_generation", 0) or 0)
        previous_classifier = getattr(self, "_ocr_growth_pending_classifier", None)
        timer_token = int(getattr(self, "_ocr_growth_pending_timer_token", 0) or 0) + 1
        self._ocr_growth_pending_text = ""
        self._ocr_growth_pending_norm = ""
        self._ocr_growth_pending_since = 0.0
        self._ocr_growth_pending_updated_at = 0.0
        self._ocr_growth_pending_generation = 0
        self._ocr_growth_pending_kind = ""
        self._ocr_growth_pending_created_after_visual_clear = False
        self._ocr_growth_pending_visual_density = 0.0
        self._ocr_growth_pending_gate_state = ""
        self._ocr_growth_pending_real_ocr_hits = 0
        self._ocr_growth_pending_recheck_requested = False
        self._ocr_growth_pending_deadline_monotonic = 0.0
        self._ocr_growth_pending_timer_token = timer_token
        self._ocr_growth_pending_timer_fired_token = 0
        self._ocr_growth_pending_timer_fired_monotonic = 0.0
        self._ocr_growth_pending_task_id = 0
        self._ocr_growth_pending_classifier = None
        self._ocr_growth_pending_source_gone = False
        self._ocr_growth_pending_empty_count = 0
        self._ocr_growth_recent_text = ""
        self._ocr_growth_recent_norm = ""
        self._ocr_growth_recent_time = 0.0
        self._ocr_growth_recent_generation = 0
        self._ocr_growth_sequence_count = 0
        self._ocr_growth_context_until = 0.0
        self.ocr_growth_pending_deadline_requested.emit(timer_token, 0)
        if previous_text and isinstance(previous_classifier, dict):
            self.log_ocr_result_outcome(
                previous_classifier,
                final_outcome=final_outcome,
                task_id=previous_task_id,
                generation=previous_generation,
                exact_reason_code=reason,
            )

    def set_ocr_growth_pending_locked(
        self,
        text,
        norm,
        now,
        generation,
        kind,
        gate_state,
        visual_density,
        created_after_visual_clear,
        task_id=None,
        classification=None,
    ):
        """Создаёт pending-кандидат вместе с контекстом его реального OCR-hit."""
        previous_text = str(getattr(self, "_ocr_growth_pending_text", "") or "")
        previous_norm = str(getattr(self, "_ocr_growth_pending_norm", "") or "")
        if previous_text and previous_norm and previous_norm != str(norm or ""):
            previous_classifier = getattr(self, "_ocr_growth_pending_classifier", None)
            if isinstance(previous_classifier, dict):
                self.log_ocr_result_outcome(
                    previous_classifier,
                    final_outcome="growth_pending_replaced",
                    task_id=int(getattr(self, "_ocr_growth_pending_task_id", 0) or 0),
                    generation=int(getattr(self, "_ocr_growth_pending_generation", 0) or 0),
                    exact_reason_code="pending_candidate_replaced",
                )
        self._ocr_growth_pending_text = text
        self._ocr_growth_pending_norm = norm
        self._ocr_growth_pending_since = now
        self._ocr_growth_pending_updated_at = now
        self._ocr_growth_pending_generation = generation
        self._ocr_growth_pending_kind = str(kind or "")
        self._ocr_growth_pending_created_after_visual_clear = bool(created_after_visual_clear)
        self._ocr_growth_pending_visual_density = float(visual_density or 0.0)
        self._ocr_growth_pending_gate_state = str(gate_state or "")
        self._ocr_growth_pending_real_ocr_hits = 1
        self._ocr_growth_pending_recheck_requested = False
        self._ocr_growth_pending_task_id = int(task_id or 0)
        self._ocr_growth_pending_classifier = (
            dict(classification) if isinstance(classification, dict) else None
        )
        self._ocr_growth_pending_source_gone = False
        self._ocr_growth_pending_empty_count = 0

    def arm_ocr_growth_pending_deadline(self, delay_sec):
        try:
            delay_sec = max(0.01, float(delay_sec))
        except Exception:
            delay_sec = self._ocr_growth_debounce_sec
        with QMutexLocker(self.mutex):
            if not self._ocr_growth_pending_text:
                return 0
            self._ocr_growth_pending_timer_token = int(self._ocr_growth_pending_timer_token or 0) + 1
            token = self._ocr_growth_pending_timer_token
            self._ocr_growth_pending_timer_fired_token = 0
            self._ocr_growth_pending_timer_fired_monotonic = 0.0
            self._ocr_growth_pending_deadline_monotonic = time.monotonic() + delay_sec
        self.ocr_growth_pending_deadline_requested.emit(token, max(1, int(delay_sec * 1000)))
        return token

    def mark_ocr_growth_pending_timer_fired(self, token):
        fired_monotonic = time.monotonic()
        accepted = False
        with QMutexLocker(self.mutex):
            current_token = int(self._ocr_growth_pending_timer_token or 0)
            pending_task_id = int(self._ocr_growth_pending_task_id or 0)
            pending_generation = int(self._ocr_growth_pending_generation or 0)
            deadline_monotonic = float(self._ocr_growth_pending_deadline_monotonic or 0.0)
            pending_since = float(self._ocr_growth_pending_since or 0.0)
            if (
                self._ocr_growth_pending_text
                and int(token or 0) == current_token
            ):
                self._ocr_growth_pending_timer_fired_token = int(token)
                self._ocr_growth_pending_timer_fired_monotonic = fired_monotonic
                self._scheduler_condition.wakeAll()
                accepted = True
        pending_age_ms = max(0.0, time.time() - pending_since) * 1000.0 if pending_since else 0.0
        details = (
            f"timer_token={int(token or 0)}; task_id={pending_task_id}; "
            f"generation={pending_generation}; deadline_monotonic={deadline_monotonic:.6f}; "
            f"fired_monotonic={fired_monotonic:.6f}; pending_age_ms={pending_age_ms:.3f}"
        )
        self.log_runtime_event(
            "OCR_TEXT_GROWTH_DEADLINE_FIRED",
            task_id=pending_task_id or None,
            generation=pending_generation,
            extra=details,
        )
        if not accepted:
            self.log_runtime_event(
                "OCR_TEXT_GROWTH_DEADLINE_SKIPPED",
                task_id=pending_task_id or None,
                generation=pending_generation,
                reason="stale_token" if int(token or 0) != current_token else "pending_missing",
                extra=f"{details}; current_timer_token={current_token}",
            )
        return accepted

    def take_fired_ocr_growth_pending_token(self):
        with QMutexLocker(self.mutex):
            token = int(self._ocr_growth_pending_timer_fired_token or 0)
            self._ocr_growth_pending_timer_fired_token = 0
            return token

    def process_fired_ocr_growth_pending_if_due(self, behavior, gate_state=""):
        """Consume a fired growth deadline before any detector-recognizer early return."""
        processed_monotonic = time.monotonic()
        with QMutexLocker(self.mutex):
            fired_token = int(self._ocr_growth_pending_timer_fired_token or 0)
            current_token = int(self._ocr_growth_pending_timer_token or 0)
            pending_text = str(self._ocr_growth_pending_text or "")
            task_id = int(self._ocr_growth_pending_task_id or 0)
            generation = int(self._ocr_growth_pending_generation or 0)
            deadline_monotonic = float(self._ocr_growth_pending_deadline_monotonic or 0.0)
            fired_monotonic = float(self._ocr_growth_pending_timer_fired_monotonic or 0.0)
            pending_since = float(self._ocr_growth_pending_since or 0.0)
        if not fired_token:
            return None
        if deadline_monotonic > 0.0 and processed_monotonic < deadline_monotonic:
            return {
                "status": "waiting",
                "timer_token": fired_token,
                "remaining_ms": (deadline_monotonic - processed_monotonic) * 1000.0,
            }

        skip_reason = ""
        if fired_token != current_token:
            skip_reason = "stale_token"
        elif not pending_text:
            skip_reason = "pending_missing"

        decision = None
        if not skip_reason:
            decision = self.take_ocr_growth_pending_if_timer_expired(
                behavior,
                gate_state=gate_state,
                expected_timer_token=fired_token,
            )
            if decision is None:
                with QMutexLocker(self.mutex):
                    replacement_token = int(self._ocr_growth_pending_timer_token or 0)
                    replacement_text = str(self._ocr_growth_pending_text or "")
                    replacement_generation = int(self._ocr_growth_pending_generation or 0)
                if replacement_token != fired_token:
                    skip_reason = "stale_token"
                elif replacement_generation != generation:
                    skip_reason = "stale_generation"
                elif not replacement_text:
                    skip_reason = "pending_cancelled"
                else:
                    skip_reason = "pending_not_eligible"

        with QMutexLocker(self.mutex):
            if int(self._ocr_growth_pending_timer_fired_token or 0) == fired_token:
                self._ocr_growth_pending_timer_fired_token = 0
                self._ocr_growth_pending_timer_fired_monotonic = 0.0

        pending_age_ms = max(0.0, time.time() - pending_since) * 1000.0 if pending_since else 0.0
        deadline_lateness_ms = (
            max(0.0, processed_monotonic - deadline_monotonic) * 1000.0
            if deadline_monotonic > 0.0
            else 0.0
        )
        details = (
            f"timer_token={fired_token}; task_id={task_id}; generation={generation}; "
            f"deadline_monotonic={deadline_monotonic:.6f}; "
            f"fired_monotonic={fired_monotonic:.6f}; "
            f"processed_monotonic={processed_monotonic:.6f}; "
            f"pending_age_ms={pending_age_ms:.3f}; "
            f"deadline_lateness_ms={deadline_lateness_ms:.3f}"
        )
        if decision is None:
            self.log_runtime_event(
                "OCR_TEXT_GROWTH_DEADLINE_SKIPPED",
                task_id=task_id or None,
                generation=generation,
                reason=skip_reason,
                extra=details,
            )
            return {
                "status": "skipped",
                "skip_reason": skip_reason,
                "timer_token": fired_token,
            }

        self.log_runtime_event(
            "OCR_TEXT_GROWTH_DEADLINE_PROCESSED",
            task_id=task_id or None,
            generation=generation,
            text=str(decision.get("text") or pending_text),
            reason=(
                "real_ocr_recheck_requested"
                if decision.get("request_real_ocr")
                else "growth_pending_handed_to_post_ocr"
            ),
            extra=details,
        )
        return {
            "status": "processed",
            "decision": decision,
            "timer_token": fired_token,
            "pending_age_ms": pending_age_ms,
            "deadline_lateness_ms": deadline_lateness_ms,
        }

    def note_ocr_growth_pending_empty(self, *, display_policy, generation):
        with QMutexLocker(self.mutex):
            if (
                not self._ocr_growth_pending_text
                or int(self._ocr_growth_pending_generation or 0) != int(generation or 0)
            ):
                return None
            self._ocr_growth_pending_empty_count = int(self._ocr_growth_pending_empty_count or 0) + 1
            if self._ocr_growth_pending_empty_count < 2:
                return "hold"
            if str(display_policy or "") == FAST_SUBTITLES:
                self._ocr_growth_pending_source_gone = True
                return "source_gone_fast_hold"
            self.reset_ocr_growth_pending_locked(
                final_outcome="growth_pending_cancelled",
                reason="source_gone_follow_source",
            )
            return "cancelled_follow_source"

    def reset_paddle_visual_caches(self):
        """Сбрасывает дешёвые visual-cache поля PaddleOCR live-режима."""
        self._last_live_ocr_signature = ""
        self._last_live_ocr_backend = ""
        self._last_live_ocr_text = ""
        self._last_paddle_empty_signature = ""
        self._last_paddle_empty_handled_at = 0.0

    def reset_visual_text_gate_state_locked(self):
        """Сбрасывает состояние visual/text-mask gate. Вызывать под mutex."""
        self._visual_text_mask_info = None
        self._visual_text_last_state = ""
        self._visual_text_last_event_time = 0.0
        self._visual_text_last_change_time = 0.0
        self._visual_text_empty_started_time = 0.0
        self._visual_text_pending_signature = ""
        self._visual_text_pending_since = 0.0
        self._visual_text_pending_info = None
        self._visual_transition_pending = False
        self._visual_transition_pending_since = 0.0
        self._visual_transition_pending_state = ""
        self._held_translation_result = None
        self._ui_translation_apply_token = None
        self._post_clear_same_source_candidate = None
        if (
            self._game_field_visual_gate is not None
            and normalize_subtitle_settings((self.session_data or {}).get("subtitles", {}))["background_mode"]
            != GAME_BACKGROUND
        ):
            self._game_field_visual_gate.reset("engine_visual_gate_reset")
        self.reset_ocr_growth_pending_locked()

    def reset_visual_text_gate_state(self, reason=""):
        """Публичный безопасный сброс visual-gate после смены режима/скрытия/динамики."""
        with QMutexLocker(self.mutex):
            self.reset_visual_text_gate_state_locked()
        if reason:
            self.log_runtime_event("VISUAL_GATE_RESET", generation=self._runtime_generation, reason=reason)

    def check_growth_apply_visual_clear_guard(self, reason=""):
        """
        Short guard against immediate visual-change clear right after a growth-pending
        translation was applied. Empty/repeated-empty clear paths do not use this guard.
        """
        if reason != "visual_text_changed":
            return False, False, "", 0.0

        now = time.time()
        with QMutexLocker(self.mutex):
            protected_until = float(self._ocr_growth_apply_protected_until or 0.0)
            protected_text = self._ocr_growth_apply_protected_text
            protected_generation = int(self._ocr_growth_apply_protected_generation or 0)
            current_generation = self._runtime_generation

            if protected_until <= 0.0 or not protected_text:
                return False, False, "", 0.0

            if now < protected_until and protected_generation == current_generation:
                return True, False, protected_text, protected_until - now

            self._ocr_growth_apply_protected_until = 0.0
            self._ocr_growth_apply_protected_text = ""
            self._ocr_growth_apply_protected_generation = 0
            return False, True, protected_text, 0.0

    def clear_translation_due_to_visual_change(self, reason="visual_text_changed", details=""):
        """
        Немедленно очищает старый перевод при визуальной смене субтитра.

        Важно: это не только UI-clear. Очистка должна инвалидировать все уже
        запущенные OCR/translation/LLM-задачи. Иначе поздний результат старой
        generation может вернуть на экран старый перевод после исчезновения или
        смены субтитра.
        """
        suppress_clear, protection_expired, protected_text, remaining_sec = self.check_growth_apply_visual_clear_guard(reason)
        if suppress_clear:
            extra = f"{details or ''}; protected_remaining={remaining_sec:.2f}s"
            with QMutexLocker(self.mutex):
                self._runtime_generation += 1
                suppressed_generation = self._runtime_generation
                self._ocr_growth_apply_protected_generation = suppressed_generation
                self.reset_ocr_growth_pending_locked()
            self.log_ocr_diagnostic("OVERLAY_CLEAR_SUPPRESSED_AFTER_GROWTH_APPLY", protected_text, extra)
            self.log_runtime_event(
                "OVERLAY_CLEAR_SUPPRESSED_AFTER_GROWTH_APPLY",
                generation=suppressed_generation,
                text=protected_text,
                reason=reason,
                extra=extra,
            )
            self.cancel_active_translation_request(
                "visual_clear_suppressed_after_growth_apply"
            )
            return
        if protection_expired:
            self.log_runtime_event(
                "OVERLAY_CLEAR_ALLOWED_AFTER_GROWTH_PROTECTION_EXPIRED",
                generation=self._runtime_generation,
                text=protected_text,
                reason=reason,
                extra=details or "",
            )

        clear_generation = None
        dropped_result = None
        with QMutexLocker(self.mutex):
            old_text = self.last_text or self.last_good_source or self._recent_good_source
            old_translation = self.last_good_translation or self._recent_good_translation
            had_anything = bool(self.last_text or self.last_good_translation or self.last_good_source or self._recent_good_translation)
            self._runtime_generation += 1
            self._no_text_generation = self._runtime_generation
            clear_generation = self._runtime_generation
            if had_anything:
                self.last_text = ""
                self.last_good_translation = ""
                self.last_good_source = ""
                self.last_good_time = 0.0

                self._recent_good_source = ""
                self._recent_good_translation = ""
                self._recent_good_time = 0.0

                self._post_clear_suspicious_text = ""
                self._post_clear_suspicious_since = 0.0
                self._last_display_clear_time = time.time()
                self._last_cleared_source = old_text
                self._last_cleared_time = self._last_display_clear_time
                self._last_cleared_translation = old_translation
                self._last_cleared_reason = reason
                self._last_cleared_generation = clear_generation

                self.empty_frames_counter = 0
                self.empty_started_time = 0.0
                self.reset_paddle_visual_caches()

                self._last_live_ocr_signature = ""
                self._last_live_ocr_backend = ""
                self._last_live_ocr_text = ""
                self._last_paddle_empty_signature = ""
                dropped_result = self._held_translation_result
                self._held_translation_result = None
                self._ui_translation_apply_token = None
                self._post_clear_same_source_candidate = None
                self._active_ocr_source = ""
                self.reset_live_text_stability_state_locked()

            subtitle_reset = self.reset_subtitle_gate_locked(reason or "visual_text_changed")

        if dropped_result:
            self.log_translation_result_dropped(dropped_result, "visual_clear_applied")
        self.log_subtitle_decision(subtitle_reset, generation=clear_generation)
        self.cancel_active_translation_request(reason or "visual_text_changed")
        if had_anything:
            self.log_ocr_diagnostic("VISUAL_TEXT_CHANGED_CLEAR", old_text, details or reason)
            self.log_runtime_event(
                "OVERLAY_CLEARED_VISUAL_CHANGE",
                generation=clear_generation,
                text=old_text,
                reason=reason,
                extra=details or "",
            )
            self.translation_ready.emit("", "", 0.0)
        else:
            self.log_runtime_event(
                "OCR_TASKS_INVALIDATED",
                generation=clear_generation,
                reason=reason,
                extra=f"visual_change_without_display_state; {details or ''}",
            )

    def visual_gate_config_from_session(self):
        """Возвращает настройки visual-gate из профиля или безопасные дефолты."""
        try:
            with QMutexLocker(self.mutex):
                cfg = json.loads(json.dumps(self.session_data or {}))
            vg = cfg.get("visual_gate", {}) if isinstance(cfg.get("visual_gate", {}), dict) else {}
        except Exception:
            vg = {}

        return {
            "use_inner_roi": vg.get("use_inner_roi", True),
            "margin_x": float(vg.get("margin_x", 0.06)),
            "margin_top": float(vg.get("margin_top", 0.08)),
            "margin_bottom": float(vg.get("margin_bottom", 0.12)),
            "ignore_bottom_right": vg.get("ignore_bottom_right", True),
            "ignore_right_width": float(vg.get("ignore_right_width", 0.22)),
            "ignore_bottom_height": float(vg.get("ignore_bottom_height", 0.28)),
            "game_field": game_field_visual_gate.normalize_game_field_config(
                vg.get("game_field", {})
            ),
        }

    def game_field_visual_mask_gate(self, pil_image, behavior):
        # EXPERIMENTAL[game_field_visual_gate_v1]: inactive in the Paddle A/B live path.
        gate_cfg = self.visual_gate_config_from_session()
        result = self._game_field_visual_gate.process(
            pil_image,
            gate_config=gate_cfg,
        )
        state = str(result.get("state") or "no_candidate")
        details = (
            "gate_mode=game_background; "
            f"candidate_density={float(result.get('candidate_density') or 0.0):.5f}; "
            f"persistent_density={float(result.get('persistent_density') or 0.0):.5f}; "
            f"baseline_density={float(result.get('baseline_density') or 0.0):.5f}; "
            f"candidate_stability_age_ms={float(result.get('candidate_stability_age_ms') or 0.0):.1f}; "
            f"motion_score={float(result.get('motion_score') or 0.0):.4f}; "
            f"visual_stable_event={bool(result.get('visual_stable_event'))}; "
            f"reason={result.get('reason') or state}"
        )

        if state in {"candidate_growing", "candidate_stabilizing"} or (
            state in {"moving", "no_candidate"} and result.get("active_candidate")
        ):
            self.mark_visual_transition_pending(f"game_field_{state}")
        elif state in {"first", "changed"} and result.get("new_event"):
            self.confirm_visual_transition(state)
            self.clear_translation_due_to_visual_change(
                "game_field_visual_stable",
                details,
            )
        elif state == "same":
            self.release_held_translation_result("game_field_same")
        elif state == "cleared":
            self.confirm_visual_transition("empty")
            self.clear_translation_due_to_visual_change(
                "game_field_candidate_cleared",
                details,
            )

        if (
            result.get("state_changed")
            or result.get("visual_stable_event")
            or state == "cleared"
        ):
            self.log_runtime_event(
                "VISUAL_GATE_DECISION",
                generation=self._runtime_generation,
                reason=state,
                extra=details,
            )
        result["details"] = details
        return result

    def visual_text_mask_gate(self, pil_image, behavior):
        """
        Step24 v3: общая лёгкая проверка изменения текста ДО любого OCR.

        Отличия от step11:
        - gate может проверять внутреннюю ROI-область, а OCR получает полный кроп;
        - правый нижний угол можно игнорировать, чтобы не реагировать на мигающее "далее";
        - первый кадр стабилизируется перед запуском OCR;
        - средние изменения подтверждаются коротким debounce, чтобы не ловить курсор/мигание.
        """
        now = time.time()
        self.apply_dense_empty_clear_if_due(behavior)
        gate_cfg = self.visual_gate_config_from_session()
        info = visual_text_gate.make_text_mask(pil_image, gate_config=gate_cfg)
        if not info or not info.signature:
            return {"skip": False, "state": "unknown", "details": "mask_failed"}

        with QMutexLocker(self.mutex):
            previous = self._visual_text_mask_info
            last_change_time = self._visual_text_last_change_time
            last_text = self.last_text
            last_gate_state = self._visual_text_last_state
            pending_signature = self._visual_text_pending_signature
            pending_since = self._visual_text_pending_since

        is_empty = info.density < self._visual_text_empty_density
        roi_tail = f"; {getattr(info, 'roi_detail', '')}" if getattr(info, 'roi_detail', '') else ""

        # Визуально пустая зона: OCR не запускаем. Старый перевод чистится
        # по реальному времени в handle_empty_or_bad_ocr().
        if is_empty:
            subtitle_settings = self.subtitle_settings_snapshot()
            fast_subtitles = subtitle_settings["display_policy"] == FAST_SUBTITLES
            if previous is not None and str(last_gate_state or "") != "empty" and not fast_subtitles:
                self.confirm_visual_transition("empty")
            with QMutexLocker(self.mutex):
                self._visual_text_mask_info = info
                self._visual_text_last_state = "empty"
                self._visual_text_last_event_time = now
                self._visual_text_pending_signature = ""
                self._visual_text_pending_since = 0.0
                self._visual_text_pending_info = None
                self.reset_ocr_growth_pending_locked()
                if self._visual_text_empty_started_time <= 0.0:
                    self._visual_text_empty_started_time = now

            self.handle_empty_or_bad_ocr(behavior)
            self.log_ocr_diagnostic(
                "VISUAL_TEXT_EMPTY",
                "",
                f"density={info.density:.4f}{roi_tail}; OCR skipped before backend"
            )
            return {"skip": True, "state": "empty", "details": f"density={info.density:.4f}{roi_tail}"}

        with QMutexLocker(self.mutex):
            self._visual_text_empty_started_time = 0.0

        # Первый непустой кадр. Не даём Paddle сразу стартовать на переходном
        # или аномально плотном кадре: это как раз давало 20-секундный пустой старт.
        if previous is None or not previous.mask:
            first_details = f"density={info.density:.4f}{roi_tail}"

            if info.density >= self._visual_text_first_max_density:
                with QMutexLocker(self.mutex):
                    self._visual_text_mask_info = info
                    self._visual_text_last_state = "first_anomaly_baseline"
                    self._visual_text_last_event_time = now
                    self._visual_text_pending_signature = ""
                    self._visual_text_pending_since = 0.0
                    self._visual_text_pending_info = None
                self.log_ocr_diagnostic(
                    "VISUAL_TEXT_FIRST_WAIT_ANOMALY",
                    "",
                    first_details + "; very dense startup frame; OCR skipped"
                )
                return {"skip": True, "state": "first_wait_anomaly", "details": first_details}

            with QMutexLocker(self.mutex):
                if pending_signature == info.signature and pending_since > 0.0:
                    pending_age = now - pending_since
                else:
                    self._visual_text_pending_signature = info.signature
                    self._visual_text_pending_since = now
                    self._visual_text_pending_info = info
                    pending_age = 0.0

            if pending_age < self._visual_text_first_stabilize_sec:
                self.log_ocr_diagnostic(
                    "VISUAL_TEXT_FIRST_WAIT_STABLE",
                    "",
                    first_details + f"; stable_for={pending_age:.2f}s; OCR skipped"
                )
                return {"skip": True, "state": "first_wait_stable", "details": first_details + f"; stable_for={pending_age:.2f}s"}

            with QMutexLocker(self.mutex):
                self._visual_text_mask_info = info
                self._visual_text_last_state = "first"
                self._visual_text_last_event_time = now
                self._visual_text_last_change_time = now
                self._visual_text_pending_signature = ""
                self._visual_text_pending_since = 0.0
                self._visual_text_pending_info = None
            self.confirm_visual_transition("first")
            self.log_ocr_diagnostic(
                "VISUAL_TEXT_FIRST",
                "",
                first_details + "; OCR allowed"
            )
            return {"skip": False, "state": "first", "details": first_details}

        cmp = visual_text_gate.compare_masks(previous, info)
        details = (
            f"score={cmp.change_score:.3f}; density={info.density:.4f}; "
            f"density_delta={cmp.density_delta:.4f}; union={cmp.union_ratio:.4f}{roi_tail}"
        )

        changed = (
            cmp.change_score >= self._visual_text_changed_score
            or cmp.density_delta >= self._visual_text_density_delta_changed
        )
        no_change = cmp.change_score <= self._visual_text_no_change_score

        if no_change:
            restore_recheck = None
            with QMutexLocker(self.mutex):
                self._visual_text_mask_info = info
                self._visual_text_last_state = "no_change"
                self._visual_text_last_event_time = now
                self._visual_text_pending_signature = ""
                self._visual_text_pending_since = 0.0
                self._visual_text_pending_info = None
                candidate = self._post_clear_same_source_candidate
                if candidate and not bool(candidate.get("recheck_requested")):
                    candidate["recheck_requested"] = True
                    restore_recheck = dict(candidate)
            if restore_recheck:
                self.log_runtime_event(
                    "OCR_POST_CLEAR_SAME_SOURCE_RESTORE_RECHECK_REQUESTED",
                    task_id=int(restore_recheck.get("task_id", 0) or 0),
                    generation=int(restore_recheck.get("generation", 0) or 0),
                    text=str(restore_recheck.get("text") or ""),
                    reason="second sequential real OCR confirmation required",
                    extra=f"visual_epoch={restore_recheck.get('visual_epoch')}; gate_state=no_change",
                )
                return {"skip": False, "state": "post_clear_restore_recheck", "details": details}
            self.release_held_translation_result("no_change")
            self.log_ocr_diagnostic(
                "VISUAL_TEXT_NO_CHANGE",
                last_text,
                details + "; OCR skipped before backend"
            )
            return {"skip": True, "state": "no_change", "details": details}

        if changed:
            # Every changed mask, including a strong change, must remain
            # unchanged for the existing debounce before OCR is allowed.
            with QMutexLocker(self.mutex):
                if self._visual_text_pending_signature == info.signature and self._visual_text_pending_since > 0.0:
                    pending_age = now - self._visual_text_pending_since
                else:
                    self._visual_text_pending_signature = info.signature
                    self._visual_text_pending_since = now
                    self._visual_text_pending_info = info
                    pending_age = 0.0

            if pending_age < self._visual_text_change_debounce_sec:
                self.mark_visual_transition_pending("changed_wait_debounce")
                self.log_ocr_diagnostic(
                    "VISUAL_TEXT_CHANGED_WAIT_DEBOUNCE",
                    last_text,
                    details + f"; stable_for={pending_age:.2f}s; OCR skipped"
                )
                return {"skip": True, "state": "changed_wait_debounce", "details": details + f"; stable_for={pending_age:.2f}s"}

            self.confirm_visual_transition("changed")
            with QMutexLocker(self.mutex):
                self._visual_text_mask_info = info
                self._visual_text_last_state = "changed"
                self._visual_text_last_event_time = now
                self._visual_text_last_change_time = now
                self._visual_text_pending_signature = ""
                self._visual_text_pending_since = 0.0
                self._visual_text_pending_info = None
                self.reset_paddle_visual_caches()

            self.log_ocr_diagnostic("VISUAL_TEXT_CHANGED", last_text, details + "; OCR allowed")
            self.clear_translation_due_to_visual_change("visual_text_changed", details)
            return {"skip": False, "state": "changed", "details": details}

        # Сомнительная зона: фон/антиалиасинг могли чуть поменяться, но явной
        # смены текста нет. Не запускаем PaddleOCR слишком часто, но иногда
        # делаем контрольный проход, чтобы не пропустить слабое изменение.
        allow_uncertain = (now - last_change_time) >= self._visual_text_uncertain_recheck_sec
        with QMutexLocker(self.mutex):
            self._visual_text_mask_info = info
            self._visual_text_last_state = "uncertain"
            self._visual_text_last_event_time = now
            self._visual_text_pending_signature = ""
            self._visual_text_pending_since = 0.0
            self._visual_text_pending_info = None
            if allow_uncertain:
                self._visual_text_last_change_time = now
                self.reset_paddle_visual_caches()

        if allow_uncertain:
            self.log_ocr_diagnostic("VISUAL_TEXT_UNCERTAIN_RECHECK", last_text, details + "; OCR allowed")
            return {"skip": False, "state": "uncertain_recheck", "details": details}

        self.log_ocr_diagnostic("VISUAL_TEXT_UNCERTAIN_SKIP", last_text, details + "; OCR skipped before backend")
        return {"skip": True, "state": "uncertain_skip", "details": details}

    def make_ocr_frame_signature(self, pil_image):
        """
        Быстрый визуальный отпечаток OCR-кропа.

        Для PaddleOCR этот отпечаток важен как защита от повторного OCR одного
        и того же субтитра. В v25 он специально сделан грубее: сильнее уменьшаем
        картинку и квантуем яркость, чтобы мелкое мерцание фона/антиалиасинга не
        заставляло PaddleOCR снова обрабатывать визуально тот же самый текст.
        """
        try:
            # Очень грубый отпечаток только для решения "тот же кадр или нет".
            # В сам PaddleOCR уходит нормальная картинка. Чем грубее отпечаток,
            # тем меньше повторных тяжёлых OCR на мерцающем фоне/антиалиасинге.
            # Step10: отпечаток стал менее грубым.
            # В step9 слишком грубый hash иногда считал разные субтитры одним кадром,
            # из-за чего PaddleOCR не запускался, пока пользователь не переключал режимы.
            # Это всё ещё дешёвый preview только для сравнения кадров; в OCR уходит исходный кроп.
            small = pil_image.convert("L").resize((32, 10))
            data = bytes((px // 32 for px in small.tobytes()))
            return hashlib.blake2b(data, digest_size=16).hexdigest()
        except Exception:
            return ""

    def paddle_live_skip_same_frame(self, pil_image, behavior):
        """
        Защита live-режима PaddleOCR от повторного OCR одного и того же кадра.

        Проблема из теста: PaddleOCR в чистом режиме мог по 1–12 секунд
        обрабатывать один и тот же пустой кадр снова и снова, забивая CPU на 100%.

        Логика:
        - если визуальный кроп тот же самый и последний OCR уже ничего не нашёл,
          не запускаем Paddle повторно;
        - вместо этого периодически вызываем обычную обработку пустого OCR,
          чтобы старый перевод мог корректно очиститься;
        - если визуальный кроп тот же самый и текст уже был прочитан,
          повторно OCR тоже не нужен: это тот же субтитр.
        """
        signature = self.make_ocr_frame_signature(pil_image)
        if not signature:
            return False, signature

        now = time.time()
        with QMutexLocker(self.mutex):
            same_frame = (
                signature == self._last_live_ocr_signature
                and self._last_live_ocr_backend in QUALITY_OCR_BACKENDS
            )
            last_text = self._last_live_ocr_text
            last_empty_signature = self._last_paddle_empty_signature
            last_empty_handled_at = self._last_paddle_empty_handled_at
            last_live_ocr_time = self._last_live_ocr_time

        if not same_frame:
            return False, signature

        # Тот же кадр, и на нём уже был найден текст. Обычно повторный PaddleOCR не нужен.
        # Но step9 показал важный дефект: слишком грубый visual-hash мог считать
        # новый субтитр тем же самым кадром. Поэтому same-frame skip не должен быть
        # бесконечным: спустя короткий интервал делаем контрольный PaddleOCR-проход.
        if last_text:
            force_after = getattr(self, "_paddle_same_frame_force_recheck_sec", 3.8)
            if last_live_ocr_time > 0 and now - last_live_ocr_time >= force_after:
                self.log_ocr_diagnostic(
                    "OCR_PADDLE_FORCE_RECHECK_SAME_FRAME",
                    last_text,
                    f"same visual frame for {now - last_live_ocr_time:.2f}s; forcing PaddleOCR recheck"
                )
                return False, signature

            self.log_ocr_diagnostic(
                "OCR_PADDLE_SKIPPED_SAME_FRAME",
                last_text,
                "same visual frame already processed by PaddleOCR"
            )
            return True, signature

        # Тот же пустой кадр. Не гоняем тяжёлый OCR повторно.
        if signature == last_empty_signature:
            game_background_mode = (
                self.subtitle_settings_snapshot()["background_mode"] == GAME_BACKGROUND
            )
            if game_background_mode:
                force_after = getattr(self, "_paddle_same_frame_force_recheck_sec", 3.8)
                if last_live_ocr_time > 0 and now - last_live_ocr_time >= force_after:
                    self.log_ocr_diagnostic(
                        "OCR_PADDLE_FORCE_RECHECK_SAME_FRAME",
                        "",
                        "game-background empty confirmation requires a real OCR result",
                    )
                    return False, signature
                self.log_ocr_diagnostic(
                    "OCR_PADDLE_SKIPPED_EMPTY_FRAME",
                    "",
                    "same empty frame; waiting for bounded real OCR recheck",
                )
                return True, signature
            if now - last_empty_handled_at >= 0.35:
                with QMutexLocker(self.mutex):
                    self._last_paddle_empty_handled_at = now
                self.handle_empty_or_bad_ocr(behavior)
            self.log_ocr_diagnostic(
                "OCR_PADDLE_SKIPPED_EMPTY_FRAME",
                "",
                "same empty visual frame; heavy PaddleOCR call skipped"
            )
            return True, signature

        return False, signature




    def process_ocr_and_translation(self, pil_image, capture_context=None):
        """
        OCR → стабилизация строки → фильтрация мусора → перевод → сигнал в UI.

        Почему теперь есть стабилизатор:
        игра часто показывает текст постепенно, а Tesseract ловит промежуточные куски:
            "Whene" → "Whenever I was" → "Whenever I was with her..."
        Если переводить каждый такой кусок, получаются лишние и иногда смешные переводы.

        Поэтому новая логика такая:
        1. Берём OCR-строку.
        2. Чистим края строки от типичного OCR-мусора.
        3. Проверяем, не является ли это мусором.
        4. Ждём 2 стабильных/почти стабильных OCR-кадра.
        5. Только после этого отправляем текст в переводчик.
        6. Если OCR кратковременно пропал, не очищаем перевод мгновенно.
        """
        start_time = time.time()
        self._ocr_pipeline_stage = "capture_completed"

        with QMutexLocker(self.mutex):
            cfg = self.session_data.copy()
            last_text_snapshot = self.last_text
            last_good_translation_snapshot = self.last_good_translation
            last_good_source_snapshot = self.last_good_source
            last_good_time_snapshot = self.last_good_time
            recent_good_source_snapshot = self._recent_good_source
            recent_good_time_snapshot = self._recent_good_time
            last_display_clear_time_snapshot = self._last_display_clear_time

        force_refresh_context = self.take_force_refresh_request()
        forced_refresh = bool(force_refresh_context)

        zone = cfg.get("monitor_zone", {})
        trans = cfg.get("translation", {})
        behavior = cfg.get("behavior", {})

        ocr_backend = current_ocr_backend_from_session(cfg)
        src_lang = trans.get("source_lang", "en")
        trg_lang = trans.get("target_lang", "uk")
        use_online = trans.get("mode", "offline") == "online"
        subtitle_settings = self.subtitle_settings_snapshot(cfg)
        game_background_mode = subtitle_settings["background_mode"] == GAME_BACKGROUND
        fast_subtitles_policy = subtitle_settings["display_policy"] == FAST_SUBTITLES
        growth_timer_accept = None
        standalone_short_recheck = None
        standalone_short_recheck_confirmed = False
        growth_accept_source = ""
        growth_accept_reason = ""
        growth_deadline_result = None if forced_refresh else self.process_fired_ocr_growth_pending_if_due(
            behavior,
            gate_state="live_iteration_start",
        )
        if growth_deadline_result and growth_deadline_result.get("status") == "processed":
            timer_decision = growth_deadline_result.get("decision")
            if timer_decision and timer_decision.get("request_real_ocr"):
                standalone_short_recheck = timer_decision
            else:
                growth_timer_accept = timer_decision
            if growth_timer_accept:
                growth_accept_source = "timer"

        # Step24 v3: visual/text-mask gate теперь общий для всех live OCR-режимов.
        #
        # Раньше новая логика очистки работала только в качественном Paddle-режиме,
        # а Tesseract жил по старой схеме удержания перевода по кадрам. В результате
        # поведение overlay зависело от выбранного OCR. Это неправильно: смена
        # визуального субтитра должна очищать старый перевод одинаково в любом режиме.
        #
        # Правило теперь общее:
        #   1) сначала дешёвый visual/text-mask gate анализирует область субтитров;
        #   2) если текст визуально изменился — старый перевод очищается сразу;
        #   3) если область пустая — очистка идёт через общий короткий clear-delay;
        #   4) только после этого решаем, запускать ли OCR backend.
        if forced_refresh:
            if game_background_mode:
                self.ensure_game_background_pipeline_active(cfg)
                if not is_paddle_family_backend(ocr_backend):
                    self.log_runtime_event(
                        "OCR_TASK_NOT_CREATED",
                        generation=self._runtime_generation,
                        reason="force refresh game background requires Paddle OCR",
                        extra=f"ocr_backend={ocr_backend}",
                    )
                    return
            gate = {
                "skip": False,
                "state": "force_refresh",
                "visual_stable_event": True,
                "details": "forced fresh capture bypasses normal freshness/debounce/backoff",
            }
            visual_mask_info_snapshot = None
        elif growth_timer_accept:
            gate = {
                "skip": False,
                "state": "growth_pending_timer",
                "details": "fired growth deadline processed before visual gate/detector",
            }
            visual_mask_info_snapshot = None
        elif game_background_mode:
            self.ensure_game_background_pipeline_active(cfg)
            if not is_paddle_family_backend(ocr_backend):
                self.log_runtime_event(
                    "GAME_BACKGROUND_OCR_ERROR",
                    generation=self._runtime_generation,
                    reason="game_background_requires_paddle_family",
                    extra=f"ocr_backend={ocr_backend}",
                )
                self.log_runtime_event(
                    "OCR_TASK_NOT_CREATED",
                    generation=self._runtime_generation,
                    reason="game background requires Paddle OCR",
                    extra=f"ocr_backend={ocr_backend}",
                )
                return
            ready, _remaining_ms = self._game_background_paddle_state.ready()
            if not ready and not forced_refresh:
                return
            gate = {
                "skip": False,
                "state": "game_background_full_ocr",
                "details": "pipeline=sequential_full_paddle; fresh_crop=true; throttle_3_2=false",
            }
            visual_mask_info_snapshot = None
        else:
            gate = self.visual_text_mask_gate(pil_image, behavior)
            with QMutexLocker(self.mutex):
                visual_mask_info_snapshot = self._visual_text_mask_info
        gate_state = str(gate.get("state") or "")
        gate_mode = GAME_BACKGROUND if game_background_mode else STATIC_PANEL
        visual_gate_new_subtitle = bool(
            forced_refresh
            or (
                (not game_background_mode)
                and (
                    gate.get("visual_stable_event")
                    or gate_state in {"changed", "first"}
                )
            )
        )
        with QMutexLocker(self.mutex):
            gate_mode_changed = self._last_visual_gate_mode_logged != gate_mode
            if gate_mode_changed:
                self._last_visual_gate_mode_logged = gate_mode
        if gate_mode_changed:
            self.log_runtime_event(
                "VISUAL_GATE_MODE_SELECTED",
                generation=self._runtime_generation,
                reason=gate_mode,
                extra=f"gate_mode={gate_mode}; visual_stable_event={visual_gate_new_subtitle}",
            )
        visual_signature = str(getattr(visual_mask_info_snapshot, "signature", "") or "")
        try:
            visual_density = float(getattr(visual_mask_info_snapshot, "density", 0.0) or 0.0)
        except Exception:
            visual_density = 0.0

        if forced_refresh:
            gate_state = "force_refresh"
            visual_gate_new_subtitle = True
        elif standalone_short_recheck:
            gate_state = "standalone_short_recheck"
            visual_gate_new_subtitle = False
        elif growth_timer_accept:
            gate_state = "growth_pending_timer"
            visual_gate_new_subtitle = False
        if gate.get("skip"):
            if not growth_deadline_result and gate_state != "empty":
                timer_decision = self.take_ocr_growth_pending_if_timer_expired(behavior, gate_state=gate_state)
                if timer_decision and timer_decision.get("request_real_ocr"):
                    standalone_short_recheck = timer_decision
                else:
                    growth_timer_accept = timer_decision
            if standalone_short_recheck:
                gate_state = "standalone_short_recheck"
                visual_gate_new_subtitle = False
            elif growth_timer_accept:
                gate_state = "growth_pending_timer"
                growth_accept_source = "timer"
                visual_gate_new_subtitle = False
            else:
                scheduler_recheck_due = False
                if is_paddle_family_backend(ocr_backend) and gate_state in {"no_change", "same"}:
                    now_monotonic = time.monotonic()
                    with QMutexLocker(self.mutex):
                        scheduler_wait_pending = (
                            self._paddle_scheduler_pending_wait_ms > 0.0
                        )
                        scheduler_deadline = float(
                            self._paddle_scheduler_delay_deadline_monotonic or 0.0
                        )
                    scheduler_recheck_due = bool(
                        scheduler_wait_pending
                        and scheduler_deadline > 0.0
                        and now_monotonic >= scheduler_deadline
                    )
                if scheduler_recheck_due:
                    gate["skip"] = False
                    gate_state = "paddle_scheduler_recheck"
                    visual_gate_new_subtitle = False
                else:
                    # no_change может повторяться десятки раз в секунду. Не спамим runtime-log,
                    # подробности уже есть в overlay_ocr_diagnostics.log с антиспамом.
                    if gate_state != "no_change":
                        self.log_runtime_event(
                            "OCR_TASK_NOT_CREATED",
                            generation=self._runtime_generation,
                            reason=f"visual text-mask gate: {gate_state}",
                            extra=gate.get("details", "")
                        )
                    return

        # Step24 v7: если visual-gate явно разрешил OCR как новый/изменённый
        # субтитр, дальнейшие фильтры не должны сравнивать его со старой
        # переведённой строкой как с дублем или регрессией.
        #
        # Иначе Tesseract получал такую цепочку:
        #   visual-gate увидел смену → старый перевод очищен → OCR дал новый текст
        #   → старые duplicate/regression-фильтры всё ещё смотрели на snapshot
        #     прошлого субтитра и могли не пропустить новый перевод.
        #
        # Это не логика конкретного OCR backend. Это общее правило overlay:
        # визуально новый субтитр начинает новый цикл принятия текста.
        if visual_gate_new_subtitle:
            last_text_snapshot = ""
            last_good_translation_snapshot = ""
            last_good_source_snapshot = ""
            recent_good_source_snapshot = ""
            recent_good_time_snapshot = 0.0
            last_display_clear_time_snapshot = 0.0

        frame_signature = ""
        if is_quality_ocr_backend_name(ocr_backend) and not growth_timer_accept:
            with QMutexLocker(self.mutex):
                backend_busy = self._ocr_backend_busy
            if backend_busy:
                if game_background_mode:
                    self.log_runtime_event(
                        "GAME_BACKGROUND_OCR_OVERLAP_BLOCKED",
                        generation=self._runtime_generation,
                        reason="backend_busy",
                        extra=f"pipeline={game_background_paddle.PIPELINE_NAME}; gate_state={gate_state}",
                    )
                    self.log_runtime_event(
                        "OCR_TASK_NOT_CREATED",
                        generation=self._runtime_generation,
                        reason="game background Paddle busy",
                        extra="overlap=blocked; fresh crop will be captured after current task",
                    )
                    return
                gate_state = str(gate.get("state") or "")
                if is_paddle_family_backend(ocr_backend):
                    self.log_runtime_event(
                        "OCR_PADDLE_SCHEDULER_DECISION",
                        generation=self._runtime_generation,
                        reason="backend_busy",
                        extra=(
                            f"trigger=visual_gate_{gate_state or 'ocr_backend'}; "
                            f"visual_stable_event={visual_gate_new_subtitle}"
                        ),
                    )
                if game_background_mode and visual_gate_new_subtitle:
                    deferred = self._game_field_visual_gate.defer_stable_candidate(
                        gate_state,
                        str(gate.get("candidate_signature") or ""),
                    )
                    self.log_runtime_event(
                        "OCR_TASK_NOT_CREATED",
                        generation=self._runtime_generation,
                        reason="quality OCR busy; latest game-field candidate retained",
                        extra=(
                            f"gate_state={gate_state}; deferred={deferred}; "
                            f"candidate_signature={str(gate.get('candidate_signature') or '')[:16]}"
                        ),
                    )
                    return
                # Если visual-gate уже увидел смену текста, пока тяжёлый OCR ещё
                # обрабатывал старый кадр, старый результат применять нельзя.
                # Инвалидируем текущую задачу и ждём следующий стабильный кадр.
                if gate_state in {"changed", "first", "uncertain_recheck"}:
                    self.invalidate_runtime_tasks(f"visual_gate_{gate_state}_while_ocr_busy")
                    with QMutexLocker(self.mutex):
                        self.reset_visual_text_gate_state_locked()
                    self.log_runtime_event(
                        "OCR_TASK_NOT_CREATED",
                        generation=self._runtime_generation,
                        reason=f"quality OCR busy after visual gate: {gate_state}",
                        extra="stale running OCR invalidated; will retry on next stable frame"
                    )
                else:
                    self.log_runtime_event(
                        "OCR_TASK_NOT_CREATED",
                        generation=self._runtime_generation,
                        reason="quality OCR busy after visual gate",
                        extra=f"gate_state={gate_state}"
                    )
                return

            frame_signature = self.make_ocr_frame_signature(pil_image)

            # Для тяжёлого OCR оставляем вторую защиту от повторной обработки
            # полностью того же кадра. Для Tesseract она не нужна: он быстрый,
            # а общий visual-gate уже отсеивает отсутствие изменений.
            if (
                not game_background_mode
                and not forced_refresh
                and (not standalone_short_recheck)
                and gate.get("state") not in ("changed", "first", "uncertain_recheck", "post_clear_restore_recheck")
            ):
                should_skip, frame_signature = self.paddle_live_skip_same_frame(pil_image, behavior)
                if should_skip:
                    self.log_runtime_event("OCR_TASK_NOT_CREATED", generation=self._runtime_generation, reason="quality OCR visual/cooldown guard")
                    return

        if game_background_mode:
            scheduler_trigger = "game_background_sequential_full_paddle"
        else:
            scheduler_trigger = (
                "standalone_short_recheck"
                if standalone_short_recheck
                else f"visual_gate_{gate_state or 'ocr_backend'}"
            )

        if (
            is_paddle_family_backend(ocr_backend)
            and not growth_timer_accept
            and not game_background_mode
        ):
            image_size = getattr(pil_image, "size", (0, 0))
            region_valid = bool(
                isinstance(image_size, (tuple, list))
                and len(image_size) >= 2
                and int(image_size[0] or 0) > 0
                and int(image_size[1] or 0) > 0
            )
            if not self.paddle_live_scheduler_gate(
                trigger_reason=scheduler_trigger,
                region_valid=region_valid,
                visual_stable_event=visual_gate_new_subtitle,
            ):
                if game_background_mode and visual_gate_new_subtitle:
                    self._game_field_visual_gate.defer_stable_candidate(
                        gate_state,
                        str(gate.get("candidate_signature") or ""),
                    )
                self.log_runtime_event(
                    "OCR_TASK_NOT_CREATED",
                    generation=self._runtime_generation,
                    reason="paddle start-to-start scheduler",
                    extra=f"trigger={scheduler_trigger}; region_valid={region_valid}",
                )
                return

        if forced_refresh:
            task_source = "force_refresh"
        elif growth_timer_accept:
            task_source = "growth_pending_timer"
        elif game_background_mode:
            task_source = "game_background_full_ocr"
        elif standalone_short_recheck:
            task_source = "standalone_short_recheck"
        else:
            task_source = "growth_pending_timer" if growth_timer_accept else "ocr_backend"

        task_id, task_generation, task_visual_epoch, task_session_fingerprint = self.next_runtime_task(ocr_backend, pil_image)
        if game_background_mode and not growth_timer_accept:
            self.log_runtime_event(
                "GAME_BACKGROUND_OCR_CREATED",
                task_id=task_id,
                generation=task_generation,
                extra=f"pipeline={game_background_paddle.PIPELINE_NAME}; fresh_crop=true",
            )
            self._game_background_paddle_state.started()
            self.log_runtime_event(
                "GAME_BACKGROUND_OCR_STARTED",
                task_id=task_id,
                generation=task_generation,
                extra=f"backend={ocr_backend}; source={task_source}",
            )
        recorder = self._live_ocr_crop_recorder
        if recorder is not None:
            debug_capture = dict(capture_context or {})
            with QMutexLocker(self.mutex):
                debug_no_text_generation = int(self._no_text_generation)
                debug_active_task_id = int(self._active_ocr_task_id)
            if ocr_backend == "paddle_fast":
                debug_ocr_model = "PP-OCRv4_mobile_det + en_PP-OCRv4_mobile_rec"
            elif ocr_backend == "paddle":
                debug_ocr_model = "PP-OCRv6_medium_det + PP-OCRv6_medium_rec"
            elif ocr_backend == "tesseract":
                debug_ocr_model = "Tesseract legacy ocr_core pipeline"
            else:
                debug_ocr_model = ocr_backend
            preprocess_metadata = self.run_optional_ocr_diagnostic(
                "recorder_preprocess_metadata",
                lambda: self.live_ocr_debug_preprocess_metadata(ocr_backend),
                task_id=task_id,
                generation=task_generation,
            ) or {}
            preprocess_timestamp = time.time()
            self.run_optional_ocr_diagnostic(
                "recorder_record_task",
                lambda: recorder.record_task(
                    pil_image,
                    pil_image,
                    {
                    "task_id": task_id,
                    "runtime_generation": task_generation,
                    "no_text_generation": debug_no_text_generation,
                    "active_task_id": debug_active_task_id,
                    "visual_epoch": task_visual_epoch,
                    "profile_name": str(cfg.get("game_name") or cfg.get("profile_id") or "profile"),
                    "pipeline_mode": "live",
                    "capture_backend": debug_capture.get("capture_backend", self.capture_mode),
                    "game_hwnd": debug_capture.get("game_hwnd", self.game_hwnd),
                    "ocr_backend": ocr_backend,
                    "ocr_model": debug_ocr_model,
                    "created_at": time.time(),
                    "capture_started_at": debug_capture.get("capture_started_at"),
                    "capture_finished_at": debug_capture.get("capture_finished_at"),
                    "capture_duration_ms": debug_capture.get("capture_duration_ms"),
                    "preprocess_started_at": preprocess_timestamp,
                    "preprocess_finished_at": preprocess_timestamp,
                    "preprocess_duration_ms": 0.0,
                    "preprocessing_profile": preprocess_metadata.get("profile"),
                    "preprocessing_operations": preprocess_metadata.get(
                        "production_operations_before_backend"
                    ),
                    "backend_internal_processing": preprocess_metadata.get(
                        "backend_internal_operations"
                    ),
                    "resize_scale": preprocess_metadata.get("resize_scale"),
                    "threshold": preprocess_metadata.get("threshold"),
                    "contrast": preprocess_metadata.get("contrast"),
                    "sharpen": preprocess_metadata.get("sharpen"),
                    "raw_size": {
                        "width": int(getattr(pil_image, "width", 0) or 0),
                        "height": int(getattr(pil_image, "height", 0) or 0),
                        "mode": str(getattr(pil_image, "mode", "") or ""),
                    },
                    "processed_size": {
                        "width": int(getattr(pil_image, "width", 0) or 0),
                        "height": int(getattr(pil_image, "height", 0) or 0),
                        "mode": str(getattr(pil_image, "mode", "") or ""),
                    },
                    "roi": debug_capture.get("roi", dict(zone or {})),
                    "actual_crop_rect": debug_capture.get("actual_crop_rect"),
                    "game_client_rect": debug_capture.get("game_client_rect"),
                    "game_window_rect": debug_capture.get("game_window_rect"),
                    "global_overlay_rect": debug_capture.get("global_overlay_rect"),
                    "task_source": task_source,
                    "ocr_backend_called": not bool(growth_timer_accept),
                    "processed_identical_to_raw_expected": True,
                    "gate_decision": dict(gate or {}),
                    "visual_density": visual_density,
                    "visual_signature": visual_signature,
                    },
                ),
                task_id=task_id,
                generation=task_generation,
            )

        try:
            # Step17: debug PNG is useful during OCR/crop diagnostics, but saving it
            # on every OCR task is disk I/O. Keep it off by default for normal tests.
            if session_logging.raw_enabled() and bool(behavior.get("save_debug_captures", False)):
                pil_image.save(DATA.ensure("diagnostics") / f"debug_capture_{self.capture_mode}.png")
        except Exception:
            pass

        self.log_runtime_event("OCR_TASK_STARTED", task_id=task_id, generation=task_generation, extra=f"backend={ocr_backend}; source={task_source}; visual_epoch={task_visual_epoch}")
        self._ocr_pipeline_stage = "ocr_started"
        ocr_started_at = time.time()
        backend_raw_text = ""
        ocr_error = None
        if growth_timer_accept:
            backend_raw_text = str(growth_timer_accept.get("text", "") or "")
            raw_text = self.sanitize_text(backend_raw_text)
            ocr_ms = 0
        else:
            if is_quality_ocr_backend_name(ocr_backend):
                with QMutexLocker(self.mutex):
                    self._ocr_backend_busy = True
            if is_paddle_family_backend(ocr_backend):
                cadence_start = time.monotonic()
                with QMutexLocker(self.mutex):
                    previous_start = float(self._paddle_cadence_last_start_monotonic or 0.0)
                    previous_finish = float(self._paddle_cadence_last_finish_monotonic or 0.0)
                    previous_result_class = str(self._paddle_cadence_last_result_class or "")
                    scheduler_requested_wait_ms = float(
                        self._paddle_scheduler_pending_wait_ms or 0.0
                    )
                    scheduler_delay_started = float(
                        self._paddle_scheduler_delay_started_monotonic or 0.0
                    )
                    scheduler_wait_ms = (
                        max(0.0, cadence_start - scheduler_delay_started) * 1000.0
                        if scheduler_delay_started > 0.0
                        else 0.0
                    )
                    scheduler_reason = str(self._paddle_scheduler_pending_reason or "immediate")
                    scheduler_deadline = float(
                        self._paddle_scheduler_delay_deadline_monotonic or 0.0
                    )
                    remaining_at_start_ms = max(
                        0.0,
                        (scheduler_deadline - cadence_start) * 1000.0,
                    )
                    self._paddle_scheduler_delay_deadline_monotonic = 0.0
                    self._paddle_scheduler_delay_started_monotonic = 0.0
                    self._paddle_scheduler_pending_wait_ms = 0.0
                    self._paddle_scheduler_pending_reason = ""
                    self._paddle_cadence_last_start_monotonic = cadence_start
                    if previous_start:
                        self._paddle_cadence_start_to_start_ms.append(
                            max(0.0, cadence_start - previous_start) * 1000.0
                        )
                        del self._paddle_cadence_start_to_start_ms[:-512]
                    if previous_finish:
                        self._paddle_cadence_finish_to_start_ms.append(
                            max(0.0, cadence_start - previous_finish) * 1000.0
                        )
                        del self._paddle_cadence_finish_to_start_ms[:-512]
                    cadence_throttled_count = int(self._paddle_cadence_throttled_count or 0)
                    cadence_wait_total = float(self._paddle_cadence_throttle_wait_ms_total or 0.0)
                    cadence_nonempty = int(self._paddle_cadence_nonempty_results or 0)
                self.log_runtime_event(
                    "OCR_PADDLE_CADENCE_SAMPLE",
                    task_id=task_id,
                    generation=task_generation,
                    extra=(
                        f"finish_to_start_ms={max(0.0, cadence_start - previous_finish) * 1000.0 if previous_finish else -1.0:.3f}; "
                        f"start_to_start_ms={max(0.0, cadence_start - previous_start) * 1000.0 if previous_start else -1.0:.3f}; "
                        f"throttled_count={cadence_throttled_count}; "
                        f"throttle_wait_ms_total={cadence_wait_total:.3f}; "
                        f"nonempty_results={cadence_nonempty}; "
                        f"scheduler_wait_ms={scheduler_wait_ms:.3f}; "
                        f"scheduler_requested_wait_ms={scheduler_requested_wait_ms:.3f}; "
                        f"remaining_at_start_ms={remaining_at_start_ms:.3f}; "
                        f"scheduler_reason={scheduler_reason}; "
                        f"previous_result_class={previous_result_class or 'first'}"
                    ),
                )
            try:
                if is_paddle_family_backend(ocr_backend):
                    logged_backends = getattr(self, "_paddle_first_ocr_after_preload_logged", set())
                    if ocr_backend not in logged_backends:
                        logged_backends.add(ocr_backend)
                        self._paddle_first_ocr_after_preload_logged = logged_backends
                        self.log_runtime_event(
                            "PADDLE_FIRST_OCR_AFTER_PRELOAD",
                            task_id=task_id,
                            generation=task_generation,
                            extra=f"backend={ocr_backend}; first real subtitle OCR after prepare lifecycle",
                        )
                        log_paddle_ocr_event(
                            "PADDLE_FIRST_OCR_AFTER_PRELOAD",
                            backend=ocr_backend,
                            task_id=task_id,
                            generation=task_generation,
                            paths=paddle_runtime_paths_snapshot(),
                        )
                if ocr_backend == "paddle_fast" and not self._paddle_fast_first_ocr_after_preload_logged:
                    self._paddle_fast_first_ocr_after_preload_logged = True
                    self.log_runtime_event(
                        "PADDLE_FAST_FIRST_OCR_AFTER_PRELOAD",
                        task_id=task_id,
                        generation=task_generation,
                        extra="first real subtitle OCR for paddle_fast after prepare lifecycle",
                    )
                live_paddle_worker = (
                    self.live_paddle_worker_for_backend(ocr_backend)
                    if uses_persistent_live_paddle_worker(ocr_backend)
                    else None
                )
                if live_paddle_worker is not None:
                    backend_raw_text = live_paddle_worker.submit_ocr(
                        pil_image,
                        lang=src_lang,
                        task_id=task_id,
                        generation=task_generation,
                    )
                else:
                    if uses_persistent_live_paddle_worker(ocr_backend):
                        self.log_runtime_event(
                            "LIVE_PADDLE_WORKER_UNAVAILABLE_FALLBACK",
                            task_id=task_id,
                            generation=task_generation,
                            reason="session worker missing or not running",
                            extra=f"backend={ocr_backend}",
                        )
                    with responsiveness.task_scope(task_id, ocr_backend, src_lang, generation=task_generation, mode="live", active=1):
                        backend_raw_text = ocr_core.extract_text_from_pil_image(
                            pil_image,
                            lang=src_lang,
                            backend_name=ocr_backend,
                        )
                raw_text = self.sanitize_text(backend_raw_text)
            except Exception as exc:
                ocr_error = f"{type(exc).__name__}: {exc}"
                if game_background_mode:
                    backoff = self._game_background_paddle_state.finished(
                        "", 0.0, error=ocr_error
                    )
                    self.log_runtime_event(
                        "GAME_BACKGROUND_OCR_FINISHED",
                        task_id=task_id,
                        generation=task_generation,
                        reason="error",
                        extra=f"elapsed_ms=0; error={ocr_error}",
                    )
                    self.log_runtime_event(
                        "GAME_BACKGROUND_OCR_ERROR",
                        task_id=task_id,
                        generation=task_generation,
                        reason=ocr_error,
                    )
                    self.log_runtime_event(
                        "GAME_BACKGROUND_OCR_BACKOFF_SET",
                        task_id=task_id,
                        generation=task_generation,
                        reason=backoff["result_class"],
                        extra=f"backoff_ms={backoff['backoff_ms']:.0f}",
                    )
                if recorder is not None:
                    ocr_finished_at = time.time()
                    self.run_optional_ocr_diagnostic(
                        "recorder_ocr_error_update",
                        lambda: recorder.record_ocr_result(
                            task_id,
                            generation=task_generation,
                            source_result=task_source,
                            raw_text=str(backend_raw_text or ""),
                            normalized_text="",
                            ocr_started_at=ocr_started_at,
                            ocr_finished_at=ocr_finished_at,
                            ocr_duration_ms=max(0.0, ocr_finished_at - ocr_started_at) * 1000.0,
                            error=ocr_error,
                        ),
                        task_id=task_id,
                        generation=task_generation,
                    )
                raise
            finally:
                if is_quality_ocr_backend_name(ocr_backend):
                    with QMutexLocker(self.mutex):
                        self._ocr_backend_busy = False
            self._ocr_pipeline_stage = "ocr_completed"
            ocr_ms = int((time.time() - ocr_started_at) * 1000)
            if game_background_mode and not growth_timer_accept:
                self._ocr_pipeline_stage = "game_background_state_update"
                normalized_game_background_text = self.normalize_for_compare(raw_text)
                backoff = self._game_background_paddle_state.finished(
                    normalized_game_background_text,
                    float(ocr_ms),
                )
                self.log_runtime_event(
                    "GAME_BACKGROUND_OCR_FINISHED",
                    task_id=task_id,
                    generation=task_generation,
                    reason=backoff["result_class"],
                    text=raw_text,
                    extra=f"elapsed_ms={ocr_ms}",
                )
                source_event = {
                    "new": "GAME_BACKGROUND_SOURCE_NEW",
                    "same": "GAME_BACKGROUND_SOURCE_SAME",
                    "empty": "GAME_BACKGROUND_SOURCE_EMPTY",
                }.get(backoff["result_class"])
                if source_event:
                    self.log_runtime_event(
                        source_event,
                        task_id=task_id,
                        generation=task_generation,
                        text=raw_text,
                    )
                self.log_runtime_event(
                    "GAME_BACKGROUND_OCR_BACKOFF_SET",
                    task_id=task_id,
                    generation=task_generation,
                    reason=backoff["result_class"],
                    extra=f"backoff_ms={backoff['backoff_ms']:.0f}",
                )
            if is_paddle_family_backend(ocr_backend) and not growth_timer_accept:
                self._ocr_pipeline_stage = "cadence_logging"
                cadence_finish = time.monotonic()
                cadence_inference_ms = max(0.0, cadence_finish - cadence_start) * 1000.0
                start_to_start_ms = (
                    max(0.0, cadence_start - previous_start) * 1000.0
                    if previous_start
                    else None
                )
                finish_to_start_ms = (
                    max(0.0, cadence_start - previous_finish) * 1000.0
                    if previous_finish
                    else None
                )
                result_class = "nonempty" if raw_text else "empty"
                remaining_at_finish_ms = paddle_start_to_start_remaining_ms(
                    cadence_start,
                    cadence_finish,
                    self._paddle_live_min_interval_sec * 1000.0,
                )
                cadence_sample = {
                    "task_id": int(task_id),
                    "backend": str(ocr_backend),
                    "start_monotonic": round(cadence_start, 6),
                    "finish_monotonic": round(cadence_finish, 6),
                    "start_to_start_ms": (
                        round(start_to_start_ms, 3)
                        if start_to_start_ms is not None
                        else None
                    ),
                    "previous_start_to_start_ms": (
                        round(start_to_start_ms, 3)
                        if start_to_start_ms is not None
                        else None
                    ),
                    "finish_to_start_ms": (
                        round(finish_to_start_ms, 3)
                        if finish_to_start_ms is not None
                        else None
                    ),
                    "previous_finish_to_start_ms": (
                        round(finish_to_start_ms, 3)
                        if finish_to_start_ms is not None
                        else None
                    ),
                    "throttle_target_ms": round(
                        self._paddle_live_min_interval_sec * 1000.0,
                        3,
                    ),
                    "scheduler_wait_ms": round(scheduler_wait_ms, 3),
                    "scheduler_requested_wait_ms": round(
                        scheduler_requested_wait_ms,
                        3,
                    ),
                    "remaining_at_start_ms": round(remaining_at_start_ms, 3),
                    "remaining_at_finish_ms": round(remaining_at_finish_ms, 3),
                    "throttle_remaining_at_finish_ms": round(
                        remaining_at_finish_ms,
                        3,
                    ),
                    "scheduler_reason": scheduler_reason,
                    "reason_started": scheduler_trigger,
                    "reason_delayed": (
                        scheduler_reason if scheduler_wait_ms > 0.0 else ""
                    ),
                    "previous_result_class": previous_result_class or "first",
                    "result_class": result_class,
                    "inference_ms": round(cadence_inference_ms, 3),
                }
                with QMutexLocker(self.mutex):
                    self._paddle_cadence_last_finish_monotonic = cadence_finish
                    self._paddle_cadence_last_result_class = result_class
                    self._paddle_cadence_inference_ms.append(cadence_inference_ms)
                    del self._paddle_cadence_inference_ms[:-512]
                    self._paddle_cadence_samples.append(cadence_sample)
                    del self._paddle_cadence_samples[:-512]
                    if raw_text:
                        self._paddle_cadence_nonempty_results += 1
                    cadence_nonempty = int(self._paddle_cadence_nonempty_results or 0)
                    cadence_throttled_count = int(self._paddle_cadence_throttled_count or 0)
                log_paddle_ocr_event(
                    "PADDLE_LIVE_OCR_FINISHED",
                    backend=ocr_backend,
                    task_id=task_id,
                    generation=task_generation,
                    ocr_ms=ocr_ms,
                    text_len=len(raw_text),
                    image_size=getattr(pil_image, "size", ""),
                )
                self.log_runtime_event(
                    "OCR_PADDLE_CADENCE_RESULT",
                    task_id=task_id,
                    generation=task_generation,
                    extra=(
                        f"ocr_ms={ocr_ms}; inference_monotonic_ms={cadence_inference_ms:.3f}; "
                        f"nonempty={bool(raw_text)}; "
                        f"nonempty_results={cadence_nonempty}; "
                        f"throttled_count={cadence_throttled_count}; "
                        f"remaining_at_finish_ms={remaining_at_finish_ms:.3f}; "
                        f"scheduler_wait_ms={scheduler_wait_ms:.3f}; "
                        f"previous_result_class={previous_result_class or 'first'}"
                    ),
                )
        if recorder is not None:
            self._ocr_pipeline_stage = "recorder_update"
            ocr_finished_at = time.time()
            self.run_optional_ocr_diagnostic(
                "recorder_ocr_result_update",
                lambda: recorder.record_ocr_result(
                    task_id,
                    generation=task_generation,
                    source_result=task_source,
                    raw_text=str(backend_raw_text or ""),
                    normalized_text=raw_text,
                    ocr_started_at=ocr_started_at,
                    ocr_finished_at=ocr_finished_at,
                    ocr_duration_ms=ocr_ms,
                    error=ocr_error,
                ),
                task_id=task_id,
                generation=task_generation,
            )
        self._ocr_pipeline_stage = "post_ocr"
        self.log_runtime_event("OCR_TASK_FINISHED", task_id=task_id, generation=task_generation, text=raw_text, extra=f"backend={ocr_backend}; source={task_source}; text_len={len(raw_text)}; ocr_ms={ocr_ms}")

        def log_post_ocr_error(exc, stage, text_value=None):
            self.log_runtime_event(
                "OCR_POST_OCR_ERROR",
                task_id=task_id,
                generation=task_generation,
                text=raw_text if text_value is None else text_value,
                reason=f"{type(exc).__name__}: {exc}",
                extra=f"stage={stage}",
            )
            if raw_text and isinstance(ocr_classification, dict) and ocr_classification:
                self.log_ocr_result_outcome(
                    ocr_classification,
                    final_outcome="translation_rejected",
                    task_id=task_id,
                    generation=task_generation,
                    exact_reason_code=f"post_ocr_error:{stage}",
                )
            self.finish_force_refresh_task(
                task_id,
                success=False,
                reason=f"post_ocr_error:{stage}:{type(exc).__name__}",
            )

        self._ocr_pipeline_stage = "post_ocr"
        ocr_classification = {}
        if raw_text:
            try:
                ocr_classification = post_ocr_classifier.classify_post_ocr_text(
                    raw_text,
                    source_lang=src_lang,
                    zone=zone,
                    source_mode="live",
                )
                self.remember_ocr_task_classification(task_id, ocr_classification)
            except Exception as exc:
                log_post_ocr_error(exc, "garbage_filter", raw_text)
                return

        try:
            task_stale_after_ocr = self.is_runtime_task_stale(task_generation)
        except Exception as exc:
            log_post_ocr_error(exc, "stale_after_ocr", raw_text)
            return

        if task_stale_after_ocr:
            try:
                invalidated_by_no_text_after_ocr = self.was_task_invalidated_by_no_text(task_generation)
            except Exception as exc:
                log_post_ocr_error(exc, "no_text_generation_check_after_ocr", raw_text)
                return

            if invalidated_by_no_text_after_ocr:
                self.log_runtime_event("OCR_TASK_DROPPED_AFTER_NO_TEXT", task_id=task_id, generation=task_generation, text=raw_text, reason="subtitle area cleared before OCR finished")
            else:
                self.log_runtime_event("OCR_TASK_DROPPED_STALE_AFTER_OCR", task_id=task_id, generation=task_generation, text=raw_text, reason="generation changed / paused / stopped")
            if raw_text:
                self.log_ocr_result_outcome(
                    ocr_classification,
                    final_outcome="translation_rejected",
                    task_id=task_id,
                    generation=task_generation,
                    exact_reason_code=(
                        "stale_after_no_text"
                        if invalidated_by_no_text_after_ocr
                        else "stale_after_ocr"
                    ),
                )
            else:
                self.finish_force_refresh_task(
                    task_id,
                    success=False,
                    reason=(
                        "stale_after_no_text"
                        if invalidated_by_no_text_after_ocr
                        else "stale_after_ocr"
                    ),
                )
            return

        # Если строка пустая — фиксируем это отдельно от "мусора".
        # Так мы потом сможем понять, проблема была в автообрезке/OCR или в фильтрах после OCR.
        if not raw_text:
            self.drop_post_clear_restore_candidate(
                "real_ocr_recheck_empty",
                current_task_id=task_id,
            )
            if standalone_short_recheck:
                with QMutexLocker(self.mutex):
                    self.reset_ocr_growth_pending_locked()
                self.log_runtime_event(
                    "OCR_STANDALONE_SHORT_RECHECK_REJECTED",
                    task_id=task_id,
                    generation=task_generation,
                    reason="real OCR recheck returned empty text",
                    extra=f"expected={str(standalone_short_recheck.get('text') or '')[:180]!r}",
                )
            if is_quality_ocr_backend_name(ocr_backend):
                with QMutexLocker(self.mutex):
                    self._last_live_ocr_signature = frame_signature
                    self._last_live_ocr_backend = ocr_backend
                    self._last_live_ocr_text = ""
                    self._last_live_ocr_time = time.time()
                    self._last_paddle_empty_signature = frame_signature
                    self._last_paddle_empty_handled_at = time.time()
            empty_details = f"OCR backend {current_ocr_backend_from_session(cfg)} returned empty text after preprocessing"
            self.log_ocr_diagnostic("OCR_EMPTY", "", empty_details)
            self.log_runtime_event(
                "OCR_EMPTY",
                task_id=task_id,
                generation=task_generation,
                reason="OCR backend returned empty text after preprocessing",
                extra=empty_details,
            )
            self.finish_force_refresh_task(
                task_id,
                success=False,
                reason="ocr_empty",
            )
            if game_background_mode:
                pending_empty_action = self.note_ocr_growth_pending_empty(
                    display_policy=subtitle_settings["display_policy"],
                    generation=task_generation,
                )
                if pending_empty_action:
                    self.log_runtime_event(
                        "OCR_GROWTH_PENDING_SOURCE_EMPTY",
                        task_id=task_id,
                        generation=task_generation,
                        reason=pending_empty_action,
                        extra=f"display_policy={subtitle_settings['display_policy']}",
                    )
                empty_sequence_decision = self.process_subtitle_empty_result(
                    origin="game_background_real_ocr",
                    task_id=task_id,
                    generation=task_generation,
                )
                return
            empty_signature = frame_signature or visual_signature
            repeated_empty = self.note_repeated_empty_dense_area(
                empty_signature,
                visual_density,
                behavior,
                task_id=task_id,
                generation=task_generation,
            )
            if repeated_empty.get("should_clear"):
                self.handle_empty_or_bad_ocr(
                    behavior,
                    clear_event="OVERLAY_CLEARED_REPEATED_EMPTY",
                    diagnostic_reason="OCR_REPEATED_EMPTY_DENSE_AREA",
                    force_clear_after_seconds=repeated_empty.get("clear_after", self._repeated_empty_dense_clear_sec),
                    clear_reason_extra=repeated_empty.get("details", ""),
                )
            else:
                if repeated_empty.get("dense"):
                    self.log_ocr_diagnostic("OCR_EMPTY_DENSE_AREA_HOLD", "", repeated_empty.get("details", ""))
                self.handle_empty_or_bad_ocr(behavior)
            return

        if is_quality_ocr_backend_name(ocr_backend):
            with QMutexLocker(self.mutex):
                self._last_live_ocr_signature = frame_signature
                self._last_live_ocr_backend = ocr_backend
                self._last_live_ocr_text = raw_text
                self._last_live_ocr_time = time.time()
                self._last_paddle_empty_signature = ""

        single_cjk_han_cautious = bool(
            ocr_classification.get("post_ocr_policy") in {"chinese", "japanese"}
            and ocr_classification.get("reason_code") in {
                "single_han_pending",
                "single_kanji_pending",
            }
            and int(ocr_classification.get("source_script_units") or 0) == 1
        )

        def mark_single_cjk_han_classification_confirmed(reason_code):
            if not single_cjk_han_cautious:
                return
            pending_reason = str(ocr_classification.get("reason_code") or "")
            reason_codes = [
                str(reason)
                for reason in (ocr_classification.get("reason_codes") or [])
                if str(reason) != pending_reason
            ]
            if reason_code not in reason_codes:
                reason_codes.append(reason_code)
            ocr_classification.update(
                {
                    "accepted": True,
                    "classification": "cautious_short_text_confirmed",
                    "reason_code": reason_code,
                    "reason_codes": reason_codes,
                }
            )
            self.remember_ocr_task_classification(task_id, ocr_classification)

        if not ocr_classification.get("accepted") and not single_cjk_han_cautious:
            garbage_class = str(ocr_classification.get("classification") or "soft_garbage")
            reason_code = str(ocr_classification.get("reason_code") or "post_ocr_rejected")
            self.drop_post_clear_restore_candidate(
                f"real_ocr_recheck_{garbage_class}",
                current_task_id=task_id,
                text=raw_text,
            )
            if standalone_short_recheck:
                with QMutexLocker(self.mutex):
                    self.reset_ocr_growth_pending_locked(
                        final_outcome="growth_pending_cancelled",
                        reason=f"recheck_{garbage_class}",
                    )
                self.log_runtime_event(
                    "OCR_STANDALONE_SHORT_RECHECK_REJECTED",
                    task_id=task_id,
                    generation=task_generation,
                    text=raw_text,
                    reason="real OCR recheck returned garbage text",
                    extra=f"expected={str(standalone_short_recheck.get('text') or '')[:180]!r}",
                )
            garbage_details = (
                f"post_ocr_policy={ocr_classification.get('post_ocr_policy')}; "
                f"classification={garbage_class}; reason_code={reason_code}; "
                f"han={ocr_classification.get('han')}; "
                f"hiragana={ocr_classification.get('hiragana')}; "
                f"katakana={ocr_classification.get('katakana')}; "
                f"latin={ocr_classification.get('latin')}; "
                f"digits={ocr_classification.get('digits')}; "
                f"effective_text_units={ocr_classification.get('effective_text_units')}; "
                f"tokens={ocr_classification.get('token_count')}; "
                f"alpha_ratio={ocr_classification.get('alphabetic_ratio')}; "
                f"digit_ratio={ocr_classification.get('digit_ratio')}; "
                f"symbol_ratio={ocr_classification.get('symbol_ratio')}; "
                f"vowel_ratio={ocr_classification.get('vowel_ratio')}; "
                f"mixed_case={ocr_classification.get('mixed_case_anomaly_count')}; "
                f"isolated_tokens={ocr_classification.get('isolated_one_char_token_count')}; "
                f"joined_tokens={ocr_classification.get('joined_token_count')}"
            )
            self.log_ocr_diagnostic(
                f"OCR_{garbage_class.upper()}",
                raw_text,
                garbage_details
            )
            self.log_runtime_event(
                "OCR_GARBAGE",
                task_id=task_id,
                generation=task_generation,
                text=raw_text,
                reason=reason_code,
                extra=garbage_details,
            )
            self.log_ocr_result_outcome(
                ocr_classification,
                final_outcome=garbage_class,
                task_id=task_id,
                generation=task_generation,
                exact_reason_code=reason_code,
            )
            if game_background_mode:
                self.handle_empty_or_bad_ocr(behavior, clear_allowed=False)
            elif fast_subtitles_policy:
                self.process_subtitle_empty_result(
                    origin="static_visual_gate_garbage",
                    generation=self._runtime_generation,
                )
            else:
                self.handle_empty_or_bad_ocr(behavior)
            return

        # Отсекаем короткие одиночные обрывки, которые часто появляются во время печатающегося текста.
        # Примеры из лога: "citi", "eesk". Полноценные строки при этом не затрагиваются.
        try:
            is_short_fragment = self.is_unstable_short_fragment(
                raw_text,
                source_lang=src_lang,
                classification=ocr_classification,
            )
        except Exception as exc:
            log_post_ocr_error(exc, "short_fragment_filter", raw_text)
            return

        if is_short_fragment:
            self.drop_post_clear_restore_candidate(
                "real_ocr_recheck_short_fragment",
                current_task_id=task_id,
                text=raw_text,
            )
            if standalone_short_recheck:
                with QMutexLocker(self.mutex):
                    self.reset_ocr_growth_pending_locked()
                self.log_runtime_event(
                    "OCR_STANDALONE_SHORT_RECHECK_REJECTED",
                    task_id=task_id,
                    generation=task_generation,
                    text=raw_text,
                    reason="real OCR recheck returned unstable short fragment",
                    extra=f"expected={str(standalone_short_recheck.get('text') or '')[:180]!r}",
                )
            try:
                self.note_ocr_growth_short_reject(raw_text, generation=task_generation)
            except Exception as exc:
                log_post_ocr_error(exc, "ocr_text_growth_short_seed", raw_text)
                return
            short_fragment_reason = "single short fragment rejected before stabilization"
            self.log_ocr_diagnostic("OCR_SHORT_FRAGMENT", raw_text, short_fragment_reason)
            self.log_runtime_event(
                "OCR_SHORT_FRAGMENT",
                task_id=task_id,
                generation=task_generation,
                text=raw_text,
                reason=short_fragment_reason,
            )
            self.log_ocr_result_outcome(
                ocr_classification,
                final_outcome="soft_garbage",
                task_id=task_id,
                generation=task_generation,
                exact_reason_code="unstable_short_fragment",
            )
            self.handle_empty_or_bad_ocr(behavior, clear_allowed=False)
            return

        if standalone_short_recheck:
            expected_text = str(standalone_short_recheck.get("text") or "")
            expected_norm = str(standalone_short_recheck.get("norm") or "")
            recheck_pending_kind = str(standalone_short_recheck.get("pending_kind") or "")
            observed_norm = self.normalize_for_compare(raw_text)
            same_candidate = self.is_ocr_text_same_pending(expected_norm, observed_norm)

            if not same_candidate:
                with QMutexLocker(self.mutex):
                    self.reset_ocr_growth_pending_locked()
                self.log_runtime_event(
                    "OCR_STANDALONE_SHORT_RECHECK_REJECTED",
                    task_id=task_id,
                    generation=task_generation,
                    text=raw_text,
                    reason="real OCR recheck returned noticeably different text; old pending dropped",
                    extra=f"expected={expected_text[:180]!r}; observed_norm={observed_norm[:180]!r}",
                )
                standalone_short_recheck = None
            else:
                created_after_clear = bool(standalone_short_recheck.get("created_after_visual_clear"))
                origin_density = float(standalone_short_recheck.get("visual_density") or 0.0)
                origin_gate_state = str(standalone_short_recheck.get("origin_gate_state") or "")
                dense_post_clear_risk = bool(
                    created_after_clear
                    and origin_density >= float(self._visual_text_first_max_density or 0.70)
                )
                with QMutexLocker(self.mutex):
                    if (
                        self._ocr_growth_pending_generation == task_generation
                        and self.is_ocr_text_same_pending(self._ocr_growth_pending_norm, observed_norm)
                    ):
                        self._ocr_growth_pending_real_ocr_hits = max(
                            2,
                            int(self._ocr_growth_pending_real_ocr_hits or 0) + 1,
                        )
                    real_hits = int(self._ocr_growth_pending_real_ocr_hits or 0)

                recheck_details = (
                    f"expected={expected_text[:180]!r}; real_ocr_hits={real_hits}; "
                    f"created_after_visual_clear={created_after_clear}; origin_gate_state={origin_gate_state}; "
                    f"origin_density={origin_density:.4f}; dense_threshold={float(self._visual_text_first_max_density or 0.70):.4f}"
                )
                if dense_post_clear_risk:
                    self.log_ocr_diagnostic(
                        "OCR_STANDALONE_SHORT_POST_CLEAR_DENSE_HELD",
                        raw_text,
                        recheck_details,
                    )
                    self.log_runtime_event(
                        "OCR_STANDALONE_SHORT_POST_CLEAR_DENSE_HELD",
                        task_id=task_id,
                        generation=task_generation,
                        text=raw_text,
                        reason="one matching recheck is insufficient for post-clear dense standalone text",
                        extra=recheck_details,
                    )
                    self.log_ocr_result_outcome(
                        ocr_classification,
                        final_outcome="sequence_rejected",
                        task_id=task_id,
                        generation=task_generation,
                        exact_reason_code="post_clear_dense_single_recheck_held",
                    )
                    return

                with QMutexLocker(self.mutex):
                    self.reset_ocr_growth_pending_locked()
                standalone_short_recheck_confirmed = True
                if recheck_pending_kind == "single_han":
                    if str(ocr_classification.get("reason_code") or "") == "single_kanji_pending":
                        growth_accept_source = "single_kanji_recheck"
                        growth_accept_reason = "single_kanji_confirmed"
                    else:
                        growth_accept_source = "single_han_recheck"
                        growth_accept_reason = "single_han_confirmed"
                else:
                    growth_accept_source = "real_ocr_recheck"
                self.log_ocr_diagnostic(
                    "OCR_STANDALONE_SHORT_RECHECK_CONFIRMED",
                    raw_text,
                    recheck_details,
                )
                self.log_runtime_event(
                    "OCR_STANDALONE_SHORT_RECHECK_CONFIRMED",
                    task_id=task_id,
                    generation=task_generation,
                    text=raw_text,
                    reason="standalone short candidate confirmed by a real OCR recheck",
                    extra=recheck_details,
                )

        # OCR увидел валидную строку — плохой счётчик сбрасываем.
        with QMutexLocker(self.mutex):
            self.empty_frames_counter = 0
            self.empty_started_time = 0.0
            self._repeated_empty_signature = ""
            self._repeated_empty_density = 0.0
            self._repeated_empty_count = 0
            self._repeated_empty_since = 0.0
            self._repeated_empty_last_time = 0.0

        # Step24 v6: единое правило после visual-gate.
        #
        # Раньше live-overlay дополнительно ждал повторный OCR-hit от Tesseract.
        # После выноса visual/text-mask gate в общий слой это стало лишним: gate
        # уже ждёт стабильную картинку и не запускает повторный OCR на том же кадре.
        # Поэтому backend после gate должен только вернуть текст, а дальше общий
        # pipeline переводит его через память перевода/переводчик.
        stable_text = raw_text
        self.set_active_runtime_source(task_id, stable_text)
        game_growth_processed = False
        if game_background_mode:
            if (
                not forced_refresh
                and not growth_timer_accept
                and not standalone_short_recheck_confirmed
            ):
                try:
                    growth_decision = self.maybe_hold_ocr_growth_candidate(
                        stable_text,
                        behavior,
                        task_id=task_id,
                        generation=task_generation,
                        gate_state=gate_state,
                        visual_density=visual_density,
                        classification=ocr_classification,
                    )
                    game_growth_processed = True
                    if growth_decision.get("hold"):
                        self.log_ocr_result_outcome(
                            ocr_classification,
                            final_outcome="growth_pending",
                            task_id=task_id,
                            generation=task_generation,
                            exact_reason_code="text_growth_debounce",
                        )
                        return
                    if growth_decision.get("accepted_from_growth"):
                        growth_accept_source = growth_decision.get("source") or "debounce"
                        growth_accept_reason = str(growth_decision.get("reason_code") or "")
                except Exception as exc:
                    log_post_ocr_error(exc, "game_background_ocr_text_growth_stability", stable_text)
                    return

            if growth_accept_reason in {"single_han_confirmed", "single_kanji_confirmed"}:
                mark_single_cjk_han_classification_confirmed(growth_accept_reason)
            self.log_ocr_result_outcome(
                ocr_classification,
                final_outcome=(
                    "growth_pending_committed"
                    if growth_accept_source
                    else "accepted"
                ),
                task_id=task_id,
                generation=task_generation,
                exact_reason_code=(
                    growth_accept_reason
                    or (
                        "standalone_deadline_expired"
                        if growth_accept_source == "timer"
                        else str(ocr_classification.get("reason_code") or "accepted")
                    )
                ),
            )
            self._ocr_pipeline_stage = "subtitle_lifecycle"
            sequence_accept = self.register_subtitle_ocr_candidate(
                stable_text,
                task_id=task_id,
                task_generation=task_generation,
                force_new=forced_refresh,
                preserve_visible=forced_refresh,
            )
            if not sequence_accept.get("accept"):
                sequence_decision = sequence_accept.get("decision") or {}
                outcome = (
                    "near_duplicate"
                    if sequence_decision.get("sequence_reused")
                    else "sequence_rejected"
                )
                self.log_ocr_result_outcome(
                    ocr_classification,
                    final_outcome=outcome,
                    task_id=task_id,
                    generation=task_generation,
                    subtitle_sequence_id=sequence_accept.get("subtitle_sequence_id"),
                    exact_reason_code=str(sequence_decision.get("event") or outcome),
                )
                return
            task_generation = int(sequence_accept.get("task_generation", task_generation))
            last_text_snapshot = ""
            last_good_translation_snapshot = ""
            last_good_source_snapshot = ""
            recent_good_source_snapshot = ""
            recent_good_time_snapshot = 0.0
            last_display_clear_time_snapshot = 0.0
            with QMutexLocker(self.mutex):
                self._last_cleared_source = ""
                self._last_cleared_time = 0.0
                self._last_cleared_translation = ""
                self._last_cleared_reason = ""
                self._last_cleared_generation = 0

        # --- ЗАЩИТА ОТ OCR-РЕГРЕССИИ ---
        # Иногда OCR при широкой/сложной рамке попеременно видит:
        #   1) полную фразу из двух предложений;
        #   2) только первое предложение;
        #   3) только второе предложение или хвост с мусором.
        # Если мы уже перевели более полный текст, нельзя заменять его укороченным
        # подмножеством той же самой фразы. Иначе пользователь видит, что второе
        # предложение то появляется, то пропадает.
        try:
            is_subset_regression = self.is_ocr_regression_subset(stable_text, last_text_snapshot)
        except Exception as exc:
            log_post_ocr_error(exc, "regression_subset_filter", stable_text)
            return

        if is_subset_regression:
            subset_details = f"shorter fragment ignored; keeping last fuller text={last_text_snapshot[:180]!r}"
            self.log_ocr_diagnostic(
                "OCR_REGRESSION_SUBSET",
                stable_text,
                subset_details
            )
            self.log_runtime_event(
                "OCR_REGRESSION_SUBSET",
                task_id=task_id,
                generation=task_generation,
                text=stable_text,
                reason="post-OCR regression subset rejected candidate",
                extra=subset_details,
            )
            self.log_ocr_result_outcome(
                ocr_classification,
                final_outcome="near_duplicate",
                task_id=task_id,
                generation=task_generation,
                exact_reason_code="regression_subset",
            )
            return

        # Step20: keep last good source for a short TTL across visual/runtime resets.
        # This catches cases where the overlay was hidden/resumed and last_text was
        # cleared, but OCR returns a shorter version of the same subtitle.
        recent_sources = []
        if last_good_source_snapshot:
            recent_sources.append((last_good_source_snapshot, "last_good_source"))
        if recent_good_source_snapshot and (time.time() - recent_good_time_snapshot) <= self._recent_good_ttl_sec:
            recent_sources.append((recent_good_source_snapshot, "recent_good_source"))

        for previous_source, source_kind in recent_sources:
            try:
                is_recent_subset_regression = self.is_ocr_regression_subset(stable_text, previous_source)
            except Exception as exc:
                log_post_ocr_error(exc, f"recent_good_regression_filter:{source_kind}", stable_text)
                return

            if is_recent_subset_regression:
                recent_details = f"shorter fragment ignored by {source_kind}; keeping fuller text={previous_source[:180]!r}"
                self.log_ocr_diagnostic(
                    "OCR_REGRESSION_RECENT_GOOD",
                    stable_text,
                    recent_details
                )
                self.log_runtime_event(
                    "OCR_REGRESSION_RECENT_GOOD",
                    task_id=task_id,
                    generation=task_generation,
                    text=stable_text,
                    reason="post-OCR recent-good regression filter rejected candidate",
                    extra=recent_details,
                )
                self.log_ocr_result_outcome(
                    ocr_classification,
                    final_outcome="near_duplicate",
                    task_id=task_id,
                    generation=task_generation,
                    exact_reason_code=f"recent_good_regression:{source_kind}",
                )
                return

        # Step20: after the display was cleared, a short noisy OCR result must not
        # flash on screen. A real new subtitle will be detected again; a suspicious
        # fragment must repeat before we accept it.
        try:
            is_post_clear_suspicious = (
                (not visual_gate_new_subtitle)
                and (not standalone_short_recheck_confirmed)
                and self.is_post_clear_suspicious_ocr(stable_text, last_display_clear_time_snapshot)
            )
        except Exception as exc:
            log_post_ocr_error(exc, "post_clear_suspicious_filter", stable_text)
            return

        if is_post_clear_suspicious:
            try:
                post_clear_suspicious_confirmed = self.confirm_post_clear_suspicious_text(stable_text)
            except Exception as exc:
                log_post_ocr_error(exc, "post_clear_suspicious_confirm", stable_text)
                return

            if not post_clear_suspicious_confirmed:
                suspicious_reason = "recent display clear; suspicious OCR candidate must repeat before display"
                self.log_ocr_diagnostic(
                    "OCR_POST_CLEAR_SUSPICIOUS_HOLD",
                    stable_text,
                    suspicious_reason
                )
                self.log_runtime_event(
                    "OCR_POST_CLEAR_SUSPICIOUS_HOLD",
                    task_id=task_id,
                    generation=task_generation,
                    text=stable_text,
                    reason=suspicious_reason,
                )
                self.log_ocr_result_outcome(
                    ocr_classification,
                    final_outcome="sequence_rejected",
                    task_id=task_id,
                    generation=task_generation,
                    exact_reason_code="post_clear_suspicious_hold",
                )
                return

        with QMutexLocker(self.mutex):
            last_cleared_source_snapshot = self._last_cleared_source
            last_cleared_time_snapshot = float(self._last_cleared_time or 0.0)
        last_cleared_age = (time.time() - last_cleared_time_snapshot) if last_cleared_time_snapshot > 0.0 else 0.0
        recent_cleared_source_active = bool(
            not forced_refresh
            and last_cleared_source_snapshot
            and last_cleared_time_snapshot > 0.0
            and last_cleared_age <= 12.0
        )

        try:
            (
                cleared_source_similarity,
                cleared_source_similarity_reason,
                cleared_source_candidate_norm_len,
                _cleared_source_old_norm_len,
                cleared_source_candidate_word_count,
            ) = self.post_clear_source_similarity(stable_text, last_cleared_source_snapshot)
            is_cleared_same_source = bool(
                recent_cleared_source_active
                and cleared_source_similarity >= 0.96
            )
        except Exception as exc:
            log_post_ocr_error(exc, "post_clear_old_source_filter", stable_text)
            return

        dropped_restore_candidate = None
        if not is_cleared_same_source:
            with QMutexLocker(self.mutex):
                candidate = self._post_clear_same_source_candidate
                if candidate and int(task_id) > int(candidate.get("task_id", 0) or 0):
                    dropped_restore_candidate = dict(candidate)
                    self._post_clear_same_source_candidate = None
            if dropped_restore_candidate:
                self.log_runtime_event(
                    "OCR_POST_CLEAR_SAME_SOURCE_RESTORE_DROPPED",
                    task_id=int(dropped_restore_candidate.get("task_id", 0) or 0),
                    generation=int(dropped_restore_candidate.get("generation", 0) or 0),
                    text=str(dropped_restore_candidate.get("text") or ""),
                    reason="next_real_ocr_text_different",
                    extra=f"next_task_id={task_id}; next_text={stable_text[:180]!r}; visual_epoch={task_visual_epoch}",
                )

        if is_cleared_same_source:
            restore_action = self.handle_post_clear_same_source_restore(
                stable_text,
                task_id=task_id,
                task_generation=task_generation,
                task_visual_epoch=task_visual_epoch,
                task_session_fingerprint=task_session_fingerprint,
                ocr_backend=ocr_backend,
                age_sec=last_cleared_age,
                similarity=cleared_source_similarity,
                similarity_reason=cleared_source_similarity_reason,
                candidate_norm_len=cleared_source_candidate_norm_len,
                candidate_word_count=cleared_source_candidate_word_count,
                visual_density=visual_density,
                gate_state=gate_state,
                proc_time=time.time() - start_time,
            )
            if restore_action == "restored":
                self.log_ocr_result_outcome(
                    ocr_classification,
                    final_outcome="near_duplicate",
                    task_id=task_id,
                    generation=task_generation,
                    exact_reason_code="post_clear_same_source_restored",
                )
                return
            if restore_action == "candidate":
                self.log_ocr_result_outcome(
                    ocr_classification,
                    final_outcome="sequence_rejected",
                    task_id=task_id,
                    generation=task_generation,
                    exact_reason_code="post_clear_same_source_candidate_hold",
                )
                return
            if restore_action == "allowed_reapply":
                is_cleared_same_source = False

        if is_cleared_same_source:
            old_source_reason = "same or near-identical source was just cleared; suppressing stale cache/translation resurrection"
            old_source_details = (
                f"last_cleared_source={last_cleared_source_snapshot[:180]!r}; "
                f"age_sec={last_cleared_age:.2f}; "
                f"similarity={cleared_source_similarity:.4f}; "
                f"similarity_reason={cleared_source_similarity_reason}"
            )
            self.log_ocr_diagnostic(
                "OCR_POST_CLEAR_OLD_SOURCE_HOLD",
                stable_text,
                old_source_details
            )
            self.log_runtime_event(
                "OCR_POST_CLEAR_OLD_SOURCE_HOLD",
                task_id=task_id,
                generation=task_generation,
                text=stable_text,
                reason=old_source_reason,
                extra=old_source_details,
            )
            self.log_ocr_result_outcome(
                ocr_classification,
                final_outcome="sequence_rejected",
                task_id=task_id,
                generation=task_generation,
                exact_reason_code="post_clear_old_source_hold",
            )
            return

        try:
            is_cleared_source_subset = (
                recent_cleared_source_active
                and self.is_ocr_regression_subset(stable_text, last_cleared_source_snapshot)
            )
        except Exception as exc:
            log_post_ocr_error(exc, "post_clear_old_source_subset_filter", stable_text)
            return

        if is_cleared_source_subset:
            old_source_subset_reason = "shorter fragment of just-cleared source; suppressing stale cache/translation resurrection"
            old_source_subset_details = f"last_cleared_source={last_cleared_source_snapshot[:180]!r}; age_sec={last_cleared_age:.2f}"
            self.log_ocr_diagnostic(
                "OCR_POST_CLEAR_OLD_SOURCE_SUBSET_HOLD",
                stable_text,
                old_source_subset_details
            )
            self.log_runtime_event(
                "OCR_POST_CLEAR_OLD_SOURCE_SUBSET_HOLD",
                task_id=task_id,
                generation=task_generation,
                text=stable_text,
                reason=old_source_subset_reason,
                extra=old_source_subset_details,
            )
            self.log_ocr_result_outcome(
                ocr_classification,
                final_outcome="sequence_rejected",
                task_id=task_id,
                generation=task_generation,
                exact_reason_code="post_clear_old_source_subset_hold",
            )
            return

        # Полный дубль не переводим повторно.
        # Local post-clear guard for noisy fragments of the just-cleared source.
        try:
            if recent_cleared_source_active:
                (
                    is_cleared_source_noisy_subset,
                    cleared_source_noisy_ratio,
                    cleared_source_noisy_threshold,
                    cleared_source_noisy_new_len,
                    cleared_source_noisy_old_len,
                ) = self.is_post_clear_old_source_noisy_subset(stable_text, last_cleared_source_snapshot)
            else:
                is_cleared_source_noisy_subset = False
                cleared_source_noisy_ratio = 0.0
                cleared_source_noisy_threshold = 0.85
                cleared_source_noisy_new_len = 0
                cleared_source_noisy_old_len = 0
        except Exception as exc:
            log_post_ocr_error(exc, "post_clear_old_source_noisy_subset_filter", stable_text)
            return

        if is_cleared_source_noisy_subset:
            old_source_noisy_subset_reason = "noisy shorter fragment of just-cleared source; suppressing stale cache/translation resurrection"
            old_source_noisy_subset_details = (
                f"last_cleared_source={last_cleared_source_snapshot[:180]!r}; "
                f"age_sec={last_cleared_age:.2f}; "
                f"ratio={cleared_source_noisy_ratio:.4f}; "
                f"threshold={cleared_source_noisy_threshold:.2f}; "
                f"new_len={cleared_source_noisy_new_len}; "
                f"old_len={cleared_source_noisy_old_len}"
            )
            self.log_ocr_diagnostic(
                "OCR_POST_CLEAR_OLD_SOURCE_NOISY_SUBSET_HOLD",
                stable_text,
                old_source_noisy_subset_details
            )
            self.log_runtime_event(
                "OCR_POST_CLEAR_OLD_SOURCE_NOISY_SUBSET_HOLD",
                task_id=task_id,
                generation=task_generation,
                text=stable_text,
                reason=old_source_noisy_subset_reason,
                extra=old_source_noisy_subset_details,
            )
            self.log_ocr_result_outcome(
                ocr_classification,
                final_outcome="sequence_rejected",
                task_id=task_id,
                generation=task_generation,
                exact_reason_code="post_clear_old_source_noisy_subset_hold",
            )
            return

        try:
            if recent_cleared_source_active:
                is_cleared_source_fragment, cleared_source_fragment_details = self.is_post_clear_old_source_fragment(
                    stable_text,
                    last_cleared_source_snapshot,
                    last_cleared_age,
                )
            else:
                is_cleared_source_fragment = False
                cleared_source_fragment_details = "no recent cleared source"
        except Exception as exc:
            log_post_ocr_error(exc, "post_clear_old_source_fragment_filter", stable_text)
            return

        if is_cleared_source_fragment:
            old_source_fragment_reason = "truncated fragment of just-cleared source; suppressing stale translation resurrection"
            old_source_fragment_details = (
                f"candidate={stable_text[:180]!r}; "
                f"last_cleared_source={last_cleared_source_snapshot[:180]!r}; "
                f"{cleared_source_fragment_details}"
            )
            self.log_ocr_diagnostic(
                "OCR_POST_CLEAR_OLD_SOURCE_FRAGMENT_HOLD",
                stable_text,
                old_source_fragment_details
            )
            self.log_runtime_event(
                "OCR_POST_CLEAR_OLD_SOURCE_FRAGMENT_HOLD",
                task_id=task_id,
                generation=task_generation,
                text=stable_text,
                reason=old_source_fragment_reason,
                extra=f"backend={ocr_backend}; {old_source_fragment_details}",
            )
            self.log_ocr_result_outcome(
                ocr_classification,
                final_outcome="sequence_rejected",
                task_id=task_id,
                generation=task_generation,
                exact_reason_code="post_clear_old_source_fragment_hold",
            )
            return

        # Full duplicate is handled after post-clear old-source guards.
        if stable_text.lower() == last_text_snapshot.lower():
            duplicate_exact_reason = "same as last translated text"
            self.log_ocr_diagnostic("OCR_DUPLICATE_EXACT", stable_text, duplicate_exact_reason)
            self.log_runtime_event(
                "OCR_DUPLICATE_EXACT",
                task_id=task_id,
                generation=task_generation,
                text=stable_text,
                reason=duplicate_exact_reason,
            )
            self.log_ocr_result_outcome(
                ocr_classification,
                final_outcome="near_duplicate",
                task_id=task_id,
                generation=task_generation,
                exact_reason_code="duplicate_exact",
            )
            return

        # Почти дубль тоже не переводим повторно — защита от мелкого OCR-шума.
        # Но теперь есть важное исключение: если новый OCR-кандидат почти такой же,
        # но явно качественнее предыдущего уже переведённого варианта, мы принимаем его.
        # Реальный пример из тестов:
        #   было: "... Wombination ... looks. off"
        #   стало: "... combination ... looks off..."
        # Старый код отбрасывал вторую строку как duplicate_similar, хотя она лучше.
        try:
            similarity = difflib.SequenceMatcher(None, stable_text.lower(), last_text_snapshot.lower()).ratio()
        except Exception as exc:
            log_post_ocr_error(exc, "duplicate_similarity_filter", stable_text)
            return

        if similarity > 0.92:
            try:
                is_better_variant = self.is_better_ocr_variant(stable_text, last_text_snapshot)
            except Exception as exc:
                log_post_ocr_error(exc, "duplicate_better_variant_filter", stable_text)
                return

            if not is_better_variant:
                duplicate_details = f"similarity={similarity:.3f}; last={last_text_snapshot[:180]!r}"
                self.log_ocr_diagnostic("OCR_DUPLICATE_SIMILAR", stable_text, duplicate_details)
                self.log_runtime_event(
                    "OCR_DUPLICATE_SIMILAR",
                    task_id=task_id,
                    generation=task_generation,
                    text=stable_text,
                    reason="similar to last translated text",
                    extra=duplicate_details,
                )
                self.log_ocr_result_outcome(
                    ocr_classification,
                    final_outcome="near_duplicate",
                    task_id=task_id,
                    generation=task_generation,
                    exact_reason_code="duplicate_similar",
                )
                return

            self.log_ocr_diagnostic(
                "OCR_DUPLICATE_SIMILAR_BETTER_ACCEPTED",
                stable_text,
                f"similarity={similarity:.3f}; new candidate looks cleaner than last={last_text_snapshot[:180]!r}"
            )

        if (
            not forced_refresh
            and not game_growth_processed
            and not growth_timer_accept
            and not standalone_short_recheck_confirmed
        ):
            try:
                growth_decision = self.maybe_hold_ocr_growth_candidate(
                    stable_text,
                    behavior,
                    task_id=task_id,
                    generation=task_generation,
                    gate_state=gate_state,
                    visual_density=visual_density,
                    classification=ocr_classification,
                )
                if growth_decision.get("hold"):
                    self.log_ocr_result_outcome(
                        ocr_classification,
                        final_outcome="growth_pending",
                        task_id=task_id,
                        generation=task_generation,
                        exact_reason_code="text_growth_debounce",
                    )
                    return
                if growth_decision.get("accepted_from_growth"):
                    growth_accept_source = growth_decision.get("source") or "debounce"
                    growth_accept_reason = str(growth_decision.get("reason_code") or "")
            except Exception as exc:
                log_post_ocr_error(exc, "ocr_text_growth_stability", stable_text)
                return

        if not game_background_mode:
            if growth_accept_reason in {"single_han_confirmed", "single_kanji_confirmed"}:
                mark_single_cjk_han_classification_confirmed(growth_accept_reason)
            self.log_ocr_result_outcome(
                ocr_classification,
                final_outcome=(
                    "growth_pending_committed"
                    if growth_accept_source
                    else "accepted"
                ),
                task_id=task_id,
                generation=task_generation,
                exact_reason_code=(
                    growth_accept_reason
                    or (
                        "standalone_deadline_expired"
                        if growth_accept_source == "timer"
                        else str(ocr_classification.get("reason_code") or "accepted")
                    )
                ),
            )
            self._ocr_pipeline_stage = "subtitle_lifecycle"
            sequence_accept = self.register_subtitle_ocr_candidate(
                stable_text,
                task_id=task_id,
                task_generation=task_generation,
                force_new=bool(visual_gate_new_subtitle or forced_refresh),
                preserve_visible=forced_refresh,
            )
            if not sequence_accept.get("accept"):
                sequence_decision = sequence_accept.get("decision") or {}
                outcome = (
                    "near_duplicate"
                    if sequence_decision.get("sequence_reused")
                    else "sequence_rejected"
                )
                self.log_ocr_result_outcome(
                    ocr_classification,
                    final_outcome=outcome,
                    task_id=task_id,
                    generation=task_generation,
                    subtitle_sequence_id=sequence_accept.get("subtitle_sequence_id"),
                    exact_reason_code=str(sequence_decision.get("event") or outcome),
                )
                return
            task_generation = int(sequence_accept.get("task_generation", task_generation))

        print(f"[{self.capture_mode.upper()} OCR STABLE]: '{stable_text}'")

        try:
            task_stale_before_translation = self.is_runtime_task_stale(task_generation)
        except Exception as exc:
            log_post_ocr_error(exc, "stale_before_translation", stable_text)
            return

        if task_stale_before_translation:
            try:
                invalidated_by_no_text_before_translation = self.was_task_invalidated_by_no_text(task_generation)
            except Exception as exc:
                log_post_ocr_error(exc, "no_text_generation_check_before_translation", stable_text)
                return

            if invalidated_by_no_text_before_translation:
                self.log_runtime_event("OCR_TASK_DROPPED_AFTER_NO_TEXT", task_id=task_id, generation=task_generation, text=stable_text, reason="subtitle area cleared before translation")
            else:
                self.log_runtime_event("OCR_TASK_DROPPED_STALE_BEFORE_TRANSLATION", task_id=task_id, generation=task_generation, text=stable_text, reason="generation changed / paused / stopped")
            self.log_ocr_result_outcome(
                ocr_classification,
                final_outcome="translation_rejected",
                task_id=task_id,
                generation=task_generation,
                exact_reason_code=(
                    "stale_before_translation_after_no_text"
                    if invalidated_by_no_text_before_translation
                    else "stale_before_translation"
                ),
            )
            return

        self.set_active_runtime_source(task_id, stable_text)
        self._ocr_pipeline_stage = "translation"
        dispatch_post_ocr_policy = str(
            ocr_classification.get("post_ocr_policy")
            or post_ocr_classifier.post_ocr_policy_for_source(src_lang)
        )
        translation_mode_name, base_translator_backend_key, translator_backend_name = base_translation_cache_mode_for_session(
            cfg,
            use_online,
            source_text=stable_text,
            runtime_mode="overlay",
        )
        self.log_runtime_event(
            "OCR_TASK_ACCEPTED_FOR_TRANSLATION",
            task_id=task_id,
            generation=task_generation,
            text=stable_text,
            extra=f"backend={ocr_backend}; source_lang={src_lang}; target_lang={trg_lang}; translation_mode={translation_mode_name}; translator_backend={translator_backend_name}; gate_state={gate_state}; visual_epoch={task_visual_epoch}",
        )
        self.log_ocr_result_outcome(
            ocr_classification,
            final_outcome="translation_started",
            task_id=task_id,
            generation=task_generation,
            subtitle_sequence_id=self.subtitle_sequence_for_task(task_id),
            exact_reason_code=(
                "growth_pending_committed"
                if growth_accept_source
                else "accepted_for_translation"
            ),
        )
        base_translation_cache_mode_name = translation_mode_name
        translation_cache_mode_name = base_translation_cache_mode_name
        translation_from_memory = False
        source_hash = hashlib.sha256(stable_text.encode("utf-8", errors="replace")).hexdigest()[:12]
        ocr_result_id = f"ocr-{task_generation}-{task_id}-{source_hash}"
        translation_started_at = time.perf_counter()
        pipeline_started_perf = translation_started_at
        memory_lookup_ms = 0
        translator_ms = 0
        split_prepare_ms = 0.0
        backend_wait_ms = 0.0
        parts_join_ms = 0.0
        postprocess_ms = 0.0
        memory_store_ms = 0.0
        metadata_store_ms = 0.0
        history_write_ms = 0.0
        translation_log_write_ms = 0.0
        result_enqueue_ms = 0.0
        backend_requests = []
        whole_text_enabled = False
        cache_prompt_enabled = False
        q6_async_persistence = translator_backend_name in {
            "translategemma_llama_q6_low_vram",
            "translategemma_llama_q6_speed",
        }
        defer_sequence_persistence = game_background_mode
        llm_ms = 0
        llm_applied = False
        llm_mode = "off"
        llm_error = ""
        rough_translated = ""

        translated = ""
        cached_translation = ""

        try:
            memory_lookup_started_at = time.perf_counter()

            if not cached_translation:
                cached_translation = translation_memory.lookup_translation(
                    APP_DIR,
                    cfg,
                    original_text=stable_text,
                    source_lang=src_lang,
                    target_lang=trg_lang,
                    translation_mode=base_translation_cache_mode_name,
                )
                if cached_translation:
                    translation_cache_mode_name = base_translation_cache_mode_name

            memory_lookup_ms = int((time.perf_counter() - memory_lookup_started_at) * 1000)
        except Exception:
            cached_translation = ""
            memory_lookup_ms = 0

        if use_online:
            translator_core.online_memory_event(trans.get("online"), bool(cached_translation))
        if cached_translation:
            translated = cached_translation.strip()
            rough_translated = translated
            translation_from_memory = True
            self.log_ocr_diagnostic(
                "TRANSLATION_MEMORY_HIT",
                stable_text,
                f"target={trg_lang}; mode={translation_cache_mode_name}; lookup_ms={memory_lookup_ms}; translator skipped"
            )

        else:
            translator_started_at = time.perf_counter()
            split_started_at = time.perf_counter()
            whole_text_enabled = bool(
                use_online or translator_core.backend_prefers_whole_text(translator_backend_name)
            )
            accepted_sentences = translation_dispatch_sentences(
                stable_text,
                whole_text_enabled=whole_text_enabled,
                post_ocr_policy=dispatch_post_ocr_policy,
            )
            split_prepare_ms = (time.perf_counter() - split_started_at) * 1000.0
            translated_parts = []
            try:
                backend_obj = translator_core.get_backend(translator_backend_name)
                cache_prompt_enabled = bool(getattr(backend_obj, "cache_prompt_enabled", False))
            except Exception:
                cache_prompt_enabled = False

            backend_wait_started_at = time.perf_counter()
            subtitle_sequence_id = self.subtitle_sequence_for_task(task_id)
            translation_cancelled = False
            for request_index, clean_sentence in enumerate(accepted_sentences, start=1):
                if (
                    self.is_runtime_task_stale(task_generation)
                    or not self.is_subtitle_sequence_current(subtitle_sequence_id)
                ):
                    remaining = len(accepted_sentences) - request_index + 1
                    self.log_subtitle_decision(
                        {
                            "event": "SUBTITLE_TRANSLATION_REJECTED",
                            "subtitle_background_mode": subtitle_settings["background_mode"],
                            "subtitle_display_policy": subtitle_settings["display_policy"],
                            "subtitle_sequence_id": subtitle_sequence_id,
                            "publish_rejected_reason": "cancelled_before_split_part",
                            "remaining_split_parts_cancelled": remaining,
                        },
                        task_id=task_id,
                        generation=task_generation,
                    )
                    translated_parts = []
                    translation_cancelled = True
                    break
                request_started_at = time.perf_counter()
                part_tr = translator_core.translate_text(
                    clean_sentence,
                    src_lang=src_lang,
                    target_lang=trg_lang,
                    use_online=use_online,
                    backend_name=translator_backend_name,
                    request_context={
                        **translation_request_context_for_session(
                            cfg,
                            source_text=clean_sentence,
                            runtime_mode="overlay",
                        ),
                        "ocr_result_id": ocr_result_id,
                        "source_hash": source_hash,
                        "backend_request_index": request_index,
                        "backend_request_count": len(accepted_sentences),
                        "whole_text_enabled": whole_text_enabled,
                        "cancelled": lambda: self.is_runtime_task_stale(task_generation)
                            or not self.is_subtitle_sequence_current(subtitle_sequence_id),
                        "forced_refresh": forced_refresh,
                    },
                )
                request_wall_ms = (time.perf_counter() - request_started_at) * 1000.0
                request_metrics = translator_core.get_last_backend_metrics(translator_backend_name)
                request_metrics["request_wall_ms"] = round(request_wall_ms, 3)
                request_metrics["backend_request_index"] = request_index
                backend_requests.append(request_metrics)
                if (
                    self.is_runtime_task_stale(task_generation)
                    or not self.is_subtitle_sequence_current(subtitle_sequence_id)
                ):
                    remaining = len(accepted_sentences) - request_index
                    self.log_subtitle_decision(
                        {
                            "event": "SUBTITLE_TRANSLATION_REJECTED",
                            "subtitle_background_mode": subtitle_settings["background_mode"],
                            "subtitle_display_policy": subtitle_settings["display_policy"],
                            "subtitle_sequence_id": subtitle_sequence_id,
                            "publish_rejected_reason": "cancelled_after_split_part",
                            "remaining_split_parts_cancelled": remaining,
                        },
                        task_id=task_id,
                        generation=task_generation,
                    )
                    translated_parts = []
                    translation_cancelled = True
                    break
                if part_tr:
                    translated_parts.append(part_tr.strip())
            backend_wait_ms = (time.perf_counter() - backend_wait_started_at) * 1000.0
            if translation_cancelled:
                return

            join_started_at = time.perf_counter()
            translated = " ".join(translated_parts).strip()
            parts_join_ms = (time.perf_counter() - join_started_at) * 1000.0
            translator_ms = int((time.perf_counter() - translator_started_at) * 1000)
            postprocess_started_at = time.perf_counter()
            rough_translated = translated
            base_translation_cache_mode_name, base_translator_backend_key, translator_backend_name = refresh_base_translation_cache_mode_from_last(
                base_translation_cache_mode_name,
                base_translator_backend_key,
                translator_backend_name,
                use_online,
            )
            postprocess_ms = (time.perf_counter() - postprocess_started_at) * 1000.0

            if translated:
                # Сохраняем машинный перевод в базовый cache-key.
                translation_cache_mode_name = base_translation_cache_mode_name
                if not q6_async_persistence and not defer_sequence_persistence:
                    try:
                        memory_store_started_at = time.perf_counter()
                        try:
                            translation_memory.store_translation(
                                APP_DIR,
                                cfg,
                                original_text=stable_text,
                                translated_text=translated,
                                source_lang=src_lang,
                                target_lang=trg_lang,
                                translation_mode=base_translation_cache_mode_name,
                                mode="live_overlay",
                                ocr_engine=ocr_backend,
                            )
                        finally:
                            memory_store_ms = (time.perf_counter() - memory_store_started_at) * 1000.0
                        metadata_store_started_at = time.perf_counter()
                        try:
                            mark_translation_memory_metadata(
                                APP_DIR,
                                cfg,
                                original_text=stable_text,
                                source_lang=src_lang,
                                target_lang=trg_lang,
                                translation_mode=base_translation_cache_mode_name,
                                quality_mode="base",
                                pipeline_mode="live_overlay",
                                translator_backend=base_translator_backend_key,
                            )
                        finally:
                            metadata_store_ms = (time.perf_counter() - metadata_store_started_at) * 1000.0
                    except Exception:
                        pass


        if self.is_runtime_task_stale(task_generation):
            if self.was_task_invalidated_by_no_text(task_generation):
                self.log_runtime_event("TRANSLATION_RESULT_DROPPED_AFTER_NO_TEXT", task_id=task_id, generation=task_generation, text=stable_text, reason="subtitle area cleared before translation result applied")
            else:
                self.log_runtime_event("OCR_TASK_DROPPED_STALE_AFTER_TRANSLATION", task_id=task_id, generation=task_generation, text=stable_text, reason="generation changed / paused / stopped")
            self.finish_force_refresh_task(
                task_id,
                success=False,
                reason="stale_after_translation",
            )
            return

        # Если переводчик ничего не вернул, НЕ стираем предыдущий хороший перевод мгновенно.
        # Это как раз лечит ситуацию: "пустое окно, но при шевелении курсора перевод мелькает".
        if not translated:
            translation_empty_reason = "OCR text was stable, but translator returned empty result or all sentence parts were skipped"
            self.log_ocr_diagnostic(
                "TRANSLATION_EMPTY",
                stable_text,
                translation_empty_reason
            )
            self.log_runtime_event(
                "TRANSLATION_EMPTY",
                task_id=task_id,
                generation=task_generation,
                text=stable_text,
                reason=translation_empty_reason,
                extra=f"backend={ocr_backend}; translation_mode={translation_cache_mode_name}; llm_mode={llm_mode}; llm_error={llm_error}",
            )
            self.finish_force_refresh_task(
                task_id,
                success=False,
                reason="translation_empty",
            )
            self.handle_empty_or_bad_ocr(behavior, clear_allowed=False)
            return

        if persistence_allowed(cfg, translation_cache_mode_name):
            print(f"[ПЕРЕВОД]: '{translated}'")
        translation_log_write_ms = 0.0
        if not defer_sequence_persistence and persistence_allowed(cfg, translation_cache_mode_name):
            translation_log_started_at = time.perf_counter()
            ocr_core.log_translation(stable_text, translated)
            translation_log_write_ms = (time.perf_counter() - translation_log_started_at) * 1000.0

        translation_ms = int((time.perf_counter() - translation_started_at) * 1000)
        proc_time = time.time() - start_time
        total_ms = int(proc_time * 1000)
        self.log_ocr_diagnostic(
            "TRANSLATION_STAGE_TIMING",
            stable_text,
            f"ocr_ms={ocr_ms}; memory_lookup_ms={memory_lookup_ms}; translator_ms={translator_ms}; llm_ms={llm_ms}; llm_applied={llm_applied}; llm_mode={llm_mode}; translation_stage_ms={translation_ms}; total_ms={total_ms}; from_cache={translation_from_memory}"
        )

        # Step12: лёгкий журнал принятых переводов для будущей translation memory.
        # Пишем только текст, который реально прошёл фильтры OCR и был переведён.
        history_write_started_at = time.perf_counter()
        try:
            if defer_sequence_persistence:
                raise RuntimeError("deferred_until_subtitle_publish")
            translation_history.append_translation(
                APP_DIR,
                cfg,
                {
                    "mode": "live_overlay",
                    "ocr_engine": ocr_backend,
                    "source_lang": src_lang,
                    "target_lang": trg_lang,
                    "translation_mode": translation_cache_mode_name,
                    "from_cache": translation_from_memory,
                    "original_text": stable_text,
                    "translated_text": translated,
                    "ocr_ms": ocr_ms,
                    "translation_ms": translation_ms,
                    "memory_lookup_ms": memory_lookup_ms,
                    "translator_ms": translator_ms,
                    "llm_ms": llm_ms,
                    "llm_applied": llm_applied,
                    "llm_mode": llm_mode,
                    "llm_error": llm_error,
                    "rough_translated_text": rough_translated,
                    "total_ms": total_ms,
                },
            )
        except RuntimeError as exc:
            if str(exc) != "deferred_until_subtitle_publish":
                pass
        except Exception:
            pass

        history_write_ms = (time.perf_counter() - history_write_started_at) * 1000.0
        growth_apply_protection_sec = self.ocr_growth_apply_protection_seconds(behavior) if growth_accept_source else 0.0
        if self.is_runtime_task_stale(task_generation):
            self.log_runtime_event("OCR_TASK_DROPPED_STALE_BEFORE_UI", task_id=task_id, generation=task_generation, text=stable_text, reason="generation changed / paused / stopped before final UI update")
            return

        try:
            saved_crop = ocr_benchmark_crops.save_crop(
                APP_DIR,
                cfg,
                pil_image,
                mode="live_overlay",
                ocr_engine=ocr_backend,
                source_lang=src_lang,
                target_lang=trg_lang,
                original_text=stable_text,
                translated_text=translated,
                ocr_ms=ocr_ms,
                total_ms=total_ms,
            )
            if saved_crop:
                self.log_ocr_diagnostic("OCR_BENCHMARK_CROP_SAVED", stable_text, saved_crop)
        except Exception:
            pass

        pipeline_timings = {
            "memory_lookup_ms": round(float(memory_lookup_ms), 3),
            "split_prepare_ms": round(split_prepare_ms, 3),
            "backend_wait_ms": round(backend_wait_ms, 3),
            "parts_join_ms": round(parts_join_ms, 3),
            "postprocess_ms": round(postprocess_ms, 3),
            "memory_store_ms": round(memory_store_ms, 3),
            "metadata_store_ms": round(metadata_store_ms, 3),
            "history_write_ms": round(history_write_ms, 3),
            "translation_log_write_ms": round(translation_log_write_ms, 3),
            "whole_text_enabled": whole_text_enabled,
            "cache_prompt_enabled": cache_prompt_enabled,
            "cached_prompt_tokens": sum(int(row.get("cached_prompt_tokens") or 0) for row in backend_requests),
            "pipeline_overhead_ms": round(
                max(0.0, float(translation_ms) - float(memory_lookup_ms) - backend_wait_ms),
                3,
            ),
        }
        if translation_from_memory:
            result_type = "memory"
        else:
            result_type = f"backend:{translator_backend_name}"
        post_publish_persistence = None
        if defer_sequence_persistence and persistence_allowed(cfg, translation_cache_mode_name):
            persistence_cfg = json.loads(json.dumps(cfg, ensure_ascii=False))
            persistence_history_row = {
                "mode": "live_overlay",
                "ocr_engine": ocr_backend,
                "source_lang": src_lang,
                "target_lang": trg_lang,
                "translation_mode": translation_cache_mode_name,
                "from_cache": translation_from_memory,
                "original_text": stable_text,
                "translated_text": translated,
                "ocr_ms": ocr_ms,
                "translation_ms": translation_ms,
                "memory_lookup_ms": memory_lookup_ms,
                "translator_ms": translator_ms,
                "llm_ms": llm_ms,
                "llm_applied": llm_applied,
                "llm_mode": llm_mode,
                "llm_error": llm_error,
                "rough_translated_text": rough_translated,
                "total_ms": total_ms,
            }
            persistence_quality_mode = "base"
            persistence_llm_backend = ""

            def persist_published_subtitle():
                persistence_timings = {}
                started_at = time.perf_counter()
                ocr_core.log_translation(stable_text, translated)
                persistence_timings["translation_log_write_ms"] = round(
                    (time.perf_counter() - started_at) * 1000.0,
                    3,
                )
                if not translation_from_memory:
                    started_at = time.perf_counter()
                    translation_memory.store_translation(
                        APP_DIR,
                        persistence_cfg,
                        original_text=stable_text,
                        translated_text=translated,
                        source_lang=src_lang,
                        target_lang=trg_lang,
                        translation_mode=translation_cache_mode_name,
                        mode="live_overlay",
                        ocr_engine=ocr_backend,
                    )
                    persistence_timings["memory_store_ms"] = round(
                        (time.perf_counter() - started_at) * 1000.0,
                        3,
                    )
                    started_at = time.perf_counter()
                    mark_translation_memory_metadata(
                        APP_DIR,
                        persistence_cfg,
                        original_text=stable_text,
                        source_lang=src_lang,
                        target_lang=trg_lang,
                        translation_mode=translation_cache_mode_name,
                        quality_mode=persistence_quality_mode,
                        pipeline_mode="live_overlay",
                        translator_backend=(
                            base_translator_backend_key
                        ),
                        llm_backend=persistence_llm_backend,
                    )
                    persistence_timings["metadata_store_ms"] = round(
                        (time.perf_counter() - started_at) * 1000.0,
                        3,
                    )
                started_at = time.perf_counter()
                translation_history.append_translation(
                    APP_DIR,
                    persistence_cfg,
                    persistence_history_row,
                )
                persistence_timings["history_write_ms"] = round(
                    (time.perf_counter() - started_at) * 1000.0,
                    3,
                )
                return persistence_timings

            post_publish_persistence = persist_published_subtitle
        enqueue_started_at = time.perf_counter()
        self._ocr_pipeline_stage = "result_publish"
        queued = self.queue_translation_result(
            task_id=task_id,
            generation=task_generation,
            visual_epoch=task_visual_epoch,
            session_fingerprint=task_session_fingerprint,
            source=stable_text,
            translated=translated,
            proc_time=proc_time,
            result_type=result_type,
            ocr_ms=ocr_ms,
            memory_lookup_ms=memory_lookup_ms,
            backend_ms=translator_ms + llm_ms,
            translation_stage_ms=translation_ms,
            total_ms=total_ms,
            from_cache=translation_from_memory,
            growth_accept_source=growth_accept_source,
            growth_protection_sec=growth_apply_protection_sec,
            ocr_result_id=ocr_result_id,
            source_hash=source_hash,
            translator_backend_name=translator_backend_name,
            pipeline_started_perf=pipeline_started_perf,
            pipeline_timings=pipeline_timings,
            backend_requests=backend_requests,
            post_publish_persistence=post_publish_persistence,
            ocr_classification=ocr_classification,
        )
        result_enqueue_ms = (time.perf_counter() - enqueue_started_at) * 1000.0
        translator_core.log_backend_pipeline_event(
            translator_backend_name,
            "pipeline_result_enqueued",
            {
                "ocr_result_id": ocr_result_id,
                "source_hash": source_hash,
                "source_char_count": len(stable_text),
                "target_lang": trg_lang,
                "backend_name": translator_backend_name,
                "backend_request_count": len(backend_requests),
                "backend_requests": backend_requests,
                "backend_ms": round(backend_wait_ms, 3),
                "result_enqueue_ms": round(result_enqueue_ms, 3),
                **pipeline_timings,
            },
        )
        if q6_async_persistence and translated and not translation_from_memory and not defer_sequence_persistence:
            persistence_cfg = json.loads(json.dumps(cfg, ensure_ascii=False))
            persistence_values = {
                "original_text": stable_text,
                "translated_text": translated,
                "source_lang": src_lang,
                "target_lang": trg_lang,
                "translation_mode": base_translation_cache_mode_name,
                "ocr_engine": ocr_backend,
                "translator_backend": base_translator_backend_key,
            }

            def persist_q6_translation():
                persistence_timings = {}
                started_at = time.perf_counter()
                translation_memory.store_translation(
                    APP_DIR,
                    persistence_cfg,
                    original_text=persistence_values["original_text"],
                    translated_text=persistence_values["translated_text"],
                    source_lang=persistence_values["source_lang"],
                    target_lang=persistence_values["target_lang"],
                    translation_mode=persistence_values["translation_mode"],
                    mode="live_overlay",
                    ocr_engine=persistence_values["ocr_engine"],
                )
                persistence_timings["memory_store_ms"] = round(
                    (time.perf_counter() - started_at) * 1000.0,
                    3,
                )
                started_at = time.perf_counter()
                mark_translation_memory_metadata(
                    APP_DIR,
                    persistence_cfg,
                    original_text=persistence_values["original_text"],
                    source_lang=persistence_values["source_lang"],
                    target_lang=persistence_values["target_lang"],
                    translation_mode=persistence_values["translation_mode"],
                    quality_mode="base",
                    pipeline_mode="live_overlay",
                    translator_backend=persistence_values["translator_backend"],
                )
                persistence_timings["metadata_store_ms"] = round(
                    (time.perf_counter() - started_at) * 1000.0,
                    3,
                )
                return persistence_timings

            def log_q6_persistence_complete(details):
                translator_core.log_backend_pipeline_event(
                    translator_backend_name,
                    "pipeline_persistence_finished",
                    {
                        "ocr_result_id": ocr_result_id,
                        "source_hash": source_hash,
                        "source_char_count": len(stable_text),
                        "target_lang": trg_lang,
                        "backend_name": translator_backend_name,
                        **dict(details or {}),
                    },
                )

            persistence_queued = translation_pipeline_persistence.submit(
                ocr_result_id,
                persist_q6_translation,
                log_q6_persistence_complete,
            )
            translator_core.log_backend_pipeline_event(
                translator_backend_name,
                "pipeline_persistence_queued" if persistence_queued else "pipeline_persistence_rejected",
                {
                    "ocr_result_id": ocr_result_id,
                    "source_hash": source_hash,
                    "source_char_count": len(stable_text),
                    "target_lang": trg_lang,
                    "backend_name": translator_backend_name,
                    "persistence_log_path": translation_pipeline_persistence.log_path(),
                },
            )

    def handle_empty_or_bad_ocr(
        self,
        behavior,
        clear_allowed=True,
        clear_event="OVERLAY_CLEARED_NO_TEXT",
        diagnostic_reason="DISPLAY_CLEAR",
        force_clear_after_seconds=None,
        clear_reason_extra="",
    ):
        """
        Обработка кадров, где OCR не дал нормального текста.

        Step9: очистка перевода теперь может работать по реальному времени.
        Для Tesseract старый расчёт по кадрам был приемлем, потому что цикл быстрый.
        Для PaddleOCR один пустой/тяжёлый проход может занимать секунды, поэтому
        логика "8 пустых кадров" могла держать старый перевод не так, как visual-gate. Теперь очистка по исчезновению текста единая для всех OCR-режимов.
        """
        now = time.time()

        with QMutexLocker(self.mutex):
            self.empty_frames_counter += 1
            if self.empty_started_time <= 0.0:
                self.empty_started_time = now
            empty_started_time = self.empty_started_time
            last_text = self.last_text
            last_good_translation = self.last_good_translation
            last_good_time = self.last_good_time
            cfg_snapshot = self.session_data.copy()

        if not behavior.get("auto_clear", True):
            return

        ocr_backend = current_ocr_backend_from_session(cfg_snapshot)

        # Step24 v3: очистка после исчезновения текста теперь единая для всех OCR.
        # Старую Tesseract-логику по количеству пустых кадров больше не используем:
        # она заставляла перевод держаться фиксированное число OCR-циклов и вела себя
        # иначе, чем visual-gate.
        #
        # Смена текста очищается раньше в visual_text_mask_gate(). Здесь остаётся
        # только случай, когда область стала пустой или OCR несколько раз дал мусор.
        hold_seconds = float(behavior.get("last_translation_hold_seconds", 0.0))
        if force_clear_after_seconds is not None:
            clear_after_seconds = float(force_clear_after_seconds)
        else:
            clear_after_seconds = float(
                behavior.get(
                    "visual_empty_clear_seconds",
                    behavior.get("paddle_auto_clear_seconds", behavior.get("auto_clear_seconds", 1.5))
                )
            )

        # Если пользователь явно настроил удержание последнего перевода, соблюдаем его.
        # По умолчанию удержания нет: visual-gate уже решает, когда текст исчез/изменился.
        if last_good_translation and hold_seconds > 0.0 and (now - last_good_time) < hold_seconds:
            return

        if not clear_allowed:
            return

        should_clear = bool(last_text or last_good_translation) and ((now - empty_started_time) >= clear_after_seconds)
        clear_detail = f"empty_for={now - empty_started_time:.2f}s; clear_after={clear_after_seconds:.2f}s"
        if clear_reason_extra:
            clear_detail += f"; {clear_reason_extra}"

        clear_generation = None
        dropped_result = None
        with QMutexLocker(self.mutex):
            # Важно: проверяем не только last_text, но и last_good_translation.
            # Иначе возможна регрессия: экран уже очищен по тексту, но перевод ещё жив,
            # а поздняя OCR/LLM-задача снова применяет старый результат.
            if should_clear and (self.last_text or self.last_good_translation):
                last_text = self.last_text or self.last_good_source or self._recent_good_source
                self.last_text = ""
                self.last_good_translation = ""
                self.last_good_source = ""
                self.last_good_time = 0.0
                self._recent_good_source = ""
                self._recent_good_translation = ""
                self._recent_good_time = 0.0
                self._post_clear_suspicious_text = ""
                self._post_clear_suspicious_since = 0.0
                self._last_display_clear_time = time.time()
                self._last_cleared_source = last_text
                self._last_cleared_time = self._last_display_clear_time
                self._last_cleared_translation = last_good_translation
                self._last_cleared_reason = clear_event or diagnostic_reason
                self.empty_started_time = 0.0
                self._runtime_generation += 1
                self._no_text_generation = self._runtime_generation
                clear_generation = self._runtime_generation
                self._last_cleared_generation = clear_generation
                self._last_live_ocr_signature = ""
                self._last_live_ocr_backend = ""
                self._last_live_ocr_text = ""
                self._last_paddle_empty_signature = ""
                dropped_result = self._held_translation_result
                self._held_translation_result = None
                self._ui_translation_apply_token = None
                self._post_clear_same_source_candidate = None
                self._active_ocr_source = ""
                self.reset_live_text_stability_state_locked()
            else:
                should_clear = False

        if should_clear:
            if dropped_result:
                self.log_translation_result_dropped(dropped_result, "empty_clear_applied")
            self.cancel_active_translation_request(clear_event or diagnostic_reason)
            self.log_ocr_diagnostic(diagnostic_reason, last_text, clear_detail)
            self.log_runtime_event(clear_event, generation=clear_generation, text=last_text, extra=clear_detail)
            self.translation_ready.emit("", "", 0.0)
            print(f"[{self.capture_mode.upper()}] Очистка экрана субтитров после устойчивого пропадания OCR.")


    def post_clear_source_similarity(self, text, cleared_source):
        current_norm = self.normalize_for_compare(text)
        cleared_norm = self.normalize_for_compare(cleared_source)
        if not current_norm or not cleared_norm:
            return 0.0, "empty_norm", 0, 0, 0
        word_count = len(current_norm.split())
        if current_norm == cleared_norm:
            return 1.0, "exact_norm", len(current_norm), len(cleared_norm), word_count
        try:
            ratio = difflib.SequenceMatcher(None, current_norm, cleared_norm).ratio()
        except Exception:
            ratio = 0.0
        return ratio, f"seq_ratio={ratio:.4f}", len(current_norm), len(cleared_norm), word_count

    def handle_post_clear_same_source_restore(
        self,
        stable_text,
        task_id,
        task_generation,
        task_visual_epoch,
        task_session_fingerprint,
        ocr_backend,
        age_sec,
        similarity,
        similarity_reason,
        candidate_norm_len,
        candidate_word_count,
        visual_density,
        gate_state,
        proc_time,
    ):
        """Require two sequential real OCR hits before restoring a cleared subtitle."""
        max_restore_age_sec = 3.0
        min_norm_len = 28
        min_word_count = 5
        candidate_norm = self.normalize_for_compare(stable_text)

        with QMutexLocker(self.mutex):
            clear_reason = self._last_cleared_reason
            clear_generation = int(self._last_cleared_generation or 0)
            last_cleared_source = self._last_cleared_source
            last_cleared_translation = self._last_cleared_translation
            current_generation = self._runtime_generation
            current_visual_epoch = self._visual_epoch
            current_running = bool(self.is_running)
            current_paused = bool(self.user_interaction_paused)
            no_text_generation = int(self._no_text_generation or 0)
            previous_candidate = dict(self._post_clear_same_source_candidate or {})

        clear_reason_ok = clear_reason in {"visual_text_changed", "OVERLAY_CLEARED_VISUAL_CHANGE"}
        visual_text_like = (
            str(gate_state or "") != "empty"
            and float(visual_density or 0.0) >= float(self._visual_text_empty_density or 0.0)
        )
        task_current = (
            int(task_generation) == int(current_generation)
            and int(task_visual_epoch) == int(current_visual_epoch)
            and current_running
            and not current_paused
            and not (no_text_generation and int(task_generation) < no_text_generation)
        )
        long_enough = candidate_norm_len >= min_norm_len and candidate_word_count >= min_word_count
        dense_noisy_threshold = float(self._visual_text_first_max_density or 0.70)
        dense_noisy_frame = float(visual_density or 0.0) >= dense_noisy_threshold

        base_details = (
            f"backend={ocr_backend}; age_sec={age_sec:.2f}; max_age_sec={max_restore_age_sec:.2f}; "
            f"similarity={similarity:.4f}; similarity_reason={similarity_reason}; "
            f"clear_reason={clear_reason}; clear_generation={clear_generation}; "
            f"task_visual_epoch={task_visual_epoch}; current_visual_epoch={current_visual_epoch}; "
            f"gate_state={gate_state}; visual_density={float(visual_density or 0.0):.4f}; "
            f"candidate_norm_len={candidate_norm_len}; candidate_words={candidate_word_count}; "
            f"candidate={stable_text[:180]!r}; last_cleared_source={str(last_cleared_source or '')[:180]!r}"
        )

        if not (
            clear_reason_ok
            and task_current
            and age_sec <= max_restore_age_sec
            and long_enough
            and visual_text_like
        ):
            return ""

        if dense_noisy_frame:
            blocked_details = (
                f"{base_details}; dense_noisy_threshold={dense_noisy_threshold:.4f}; "
                "restore blocked; defer to existing post-clear old-source guards"
            )
            with QMutexLocker(self.mutex):
                if int(task_generation) != int(self._runtime_generation):
                    return ""
                self._post_clear_same_source_state = "blocked"
                self._post_clear_same_source_time = time.time()
                self._post_clear_same_source_generation = int(task_generation)
                self._post_clear_same_source_text = stable_text
            self.log_ocr_diagnostic(
                "OCR_POST_CLEAR_SAME_SOURCE_RESTORE_BLOCKED",
                stable_text,
                blocked_details,
            )
            self.log_runtime_event(
                "OCR_POST_CLEAR_SAME_SOURCE_RESTORE_BLOCKED",
                task_id=task_id,
                generation=task_generation,
                text=stable_text,
                reason="same recently-cleared source seen on dense/noisy visual frame",
                extra=blocked_details,
            )
            self.log_runtime_event(
                "OCR_POST_CLEAR_SAME_SOURCE_RESTORE_DROPPED",
                task_id=task_id,
                generation=task_generation,
                text=stable_text,
                reason="dense_noisy_frame",
                extra=blocked_details,
            )
            return ""

        restored_translation = str(last_cleared_translation or "").strip()
        previous_matches = bool(
            previous_candidate
            and int(previous_candidate.get("generation", -1)) == int(task_generation)
            and int(previous_candidate.get("visual_epoch", -1)) == int(task_visual_epoch)
            and int(previous_candidate.get("task_id", -2)) + 1 == int(task_id)
            and bool(previous_candidate.get("recheck_requested"))
            and self.normalize_for_compare(previous_candidate.get("text")) == candidate_norm
            and str(gate_state or "") == "post_clear_restore_recheck"
        )

        if not previous_matches:
            if previous_candidate:
                self.log_runtime_event(
                    "OCR_POST_CLEAR_SAME_SOURCE_RESTORE_DROPPED",
                    task_id=int(previous_candidate.get("task_id", 0) or 0),
                    generation=int(previous_candidate.get("generation", 0) or 0),
                    text=str(previous_candidate.get("text") or ""),
                    reason="candidate_not_confirmed_by_next_equivalent_ocr",
                    extra=f"next_task_id={task_id}; next_epoch={task_visual_epoch}; next_gate_state={gate_state}",
                )
            candidate = {
                "task_id": int(task_id),
                "generation": int(task_generation),
                "visual_epoch": int(task_visual_epoch),
                "text": stable_text,
                "norm": candidate_norm,
                "created_at": time.time(),
                "recheck_requested": False,
            }
            with QMutexLocker(self.mutex):
                if (
                    int(task_generation) != int(self._runtime_generation)
                    or int(task_visual_epoch) != int(self._visual_epoch)
                ):
                    return ""
                self._post_clear_same_source_candidate = candidate
                self._post_clear_same_source_state = "candidate"
                self._post_clear_same_source_time = time.time()
                self._post_clear_same_source_generation = int(task_generation)
                self._post_clear_same_source_text = stable_text
            self.log_ocr_diagnostic("OCR_POST_CLEAR_SAME_SOURCE_RESTORE_CANDIDATE", stable_text, base_details)
            self.log_runtime_event(
                "OCR_POST_CLEAR_SAME_SOURCE_RESTORE_CANDIDATE",
                task_id=task_id,
                generation=task_generation,
                text=stable_text,
                reason="first similar OCR hit held until a second sequential real OCR hit",
                extra=base_details,
            )
            return "candidate"

        with QMutexLocker(self.mutex):
            if (
                int(task_generation) != int(self._runtime_generation)
                or int(task_visual_epoch) != int(self._visual_epoch)
                or (not self.is_running)
                or self.user_interaction_paused
                or (self._no_text_generation and int(task_generation) < int(self._no_text_generation))
            ):
                return ""
            self._post_clear_same_source_candidate = None
            self._post_clear_same_source_state = "confirmed"
            self._post_clear_same_source_time = time.time()
            self._post_clear_same_source_generation = int(task_generation)
            self._post_clear_same_source_text = stable_text

        restored_from = "previous_translation" if restored_translation else "allowed_reapply"
        restore_details = f"{base_details}; restored_from={restored_from}; confirmation_task={task_id}"
        self.log_ocr_diagnostic("OCR_POST_CLEAR_SAME_SOURCE_RESTORE_CONFIRMED", stable_text, restore_details)
        self.log_runtime_event(
            "OCR_POST_CLEAR_SAME_SOURCE_RESTORE_CONFIRMED",
            task_id=task_id,
            generation=task_generation,
            text=stable_text,
            reason="second sequential equivalent OCR hit confirmed restore candidate",
            extra=restore_details,
        )

        if not restored_translation:
            return "allowed_reapply"

        self.set_active_runtime_source(task_id, stable_text)
        self.queue_translation_result(
            task_id=task_id,
            generation=task_generation,
            visual_epoch=task_visual_epoch,
            session_fingerprint=task_session_fingerprint,
            source=stable_text,
            translated=restored_translation,
            proc_time=proc_time,
            result_type="post_clear_restore",
            total_ms=int(float(proc_time or 0.0) * 1000),
            from_cache=True,
            clear_restore_state=True,
        )
        return "restored"

    def normalize_for_compare(self, text):
        """Normalize OCR text for loose internal comparisons."""
        try:
            value = str(text or "")
        except Exception:
            value = ""
        if not value:
            return ""

        value = value.casefold()
        replacements = {
            "\u00a0": " ",
            "\u2007": " ",
            "\u202f": " ",
            "\u2018": "'",
            "\u2019": "'",
            "\u201a": "'",
            "\u201b": "'",
            "\u2032": "'",
            "\u2035": "'",
            "\u201c": '"',
            "\u201d": '"',
            "\u201e": '"',
            "\u201f": '"',
            "\u00ab": '"',
            "\u00bb": '"',
            "\u2010": "-",
            "\u2011": "-",
            "\u2012": "-",
            "\u2013": "-",
            "\u2014": "-",
            "\u2015": "-",
            "\u2212": "-",
            "\u2026": "...",
        }
        for src, dst in replacements.items():
            value = value.replace(src, dst)

        value = re.sub(r"[\"'`]+", "", value)
        value = re.sub(r"[-_]+", " ", value)
        value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
        value = re.sub(r"\s+", " ", value).strip()
        return value

    def is_post_clear_old_source_noisy_subset(self, text, cleared_source):
        """Narrow post-clear guard for noisy shortened fragments of just-cleared text."""
        threshold = 0.85
        new_norm = self.normalize_for_compare(text)
        old_norm = self.normalize_for_compare(cleared_source)
        new_len = len(new_norm)
        old_len = len(old_norm)

        if not new_norm or not old_norm:
            return False, 0.0, threshold, new_len, old_len
        if new_len >= old_len:
            return False, 0.0, threshold, new_len, old_len
        if new_len < 28 or len(new_norm.split()) < 5:
            return False, 0.0, threshold, new_len, old_len
        if old_len - new_len < 12:
            return False, 0.0, threshold, new_len, old_len

        windows = [
            old_norm[:new_len],
            old_norm[-new_len:],
        ]
        step = max(8, new_len // 3)
        for start in range(0, max(1, old_len - new_len + 1), step):
            window = old_norm[start:start + new_len]
            if window:
                windows.append(window)

        best_ratio = 0.0
        for window in windows:
            ratio = difflib.SequenceMatcher(None, new_norm, window).ratio()
            if ratio > best_ratio:
                best_ratio = ratio

        return best_ratio >= threshold, best_ratio, threshold, new_len, old_len

    def is_post_clear_old_source_fragment(self, text, cleared_source, age_sec):
        """Narrow guard for a truncated old subtitle tail right after clear."""
        max_age_sec = 3.0
        token_threshold = 0.70
        bigram_threshold = 0.50
        new_norm = self.normalize_for_compare(text)
        old_norm = self.normalize_for_compare(cleared_source)
        new_len = len(new_norm)
        old_len = len(old_norm)

        if not new_norm or not old_norm:
            return False, f"empty_norm; age_sec={age_sec:.2f}"
        if age_sec > max_age_sec:
            return False, f"age_sec={age_sec:.2f}; max_age_sec={max_age_sec:.2f}"
        if new_len >= old_len:
            return False, f"not_shorter; new_len={new_len}; old_len={old_len}"
        if old_len < 42 or new_len < 28:
            return False, f"too_short_for_fragment_guard; new_len={new_len}; old_len={old_len}"
        if old_len - new_len < 14 or (new_len / max(1, old_len)) > 0.82:
            return False, f"not_substantially_shorter; new_len={new_len}; old_len={old_len}"

        new_words = new_norm.split()
        old_words = old_norm.split()
        if len(new_words) < 5 or len(old_words) < 8:
            return False, f"not_enough_words; new_words={len(new_words)}; old_words={len(old_words)}"

        old_counts = {}
        for word in old_words:
            old_counts[word] = old_counts.get(word, 0) + 1

        matched_tokens = 0
        temp_counts = dict(old_counts)
        for word in new_words:
            count = temp_counts.get(word, 0)
            if count > 0:
                matched_tokens += 1
                temp_counts[word] = count - 1
        token_overlap = matched_tokens / max(1, len(new_words))

        new_bigrams = list(zip(new_words, new_words[1:]))
        old_bigram_set = set(zip(old_words, old_words[1:]))
        matched_bigrams = sum(1 for pair in new_bigrams if pair in old_bigram_set)
        bigram_overlap = matched_bigrams / max(1, len(new_bigrams))

        substring_hit = new_norm in old_norm
        try:
            seq_ratio = difflib.SequenceMatcher(None, new_norm, old_norm).ratio()
        except Exception:
            seq_ratio = 0.0

        details = (
            f"age_sec={age_sec:.2f}; token_overlap={token_overlap:.3f}; "
            f"bigram_overlap={bigram_overlap:.3f}; seq_ratio={seq_ratio:.3f}; "
            f"new_len={new_len}; old_len={old_len}; new_words={len(new_words)}; "
            f"old_words={len(old_words)}"
        )
        if substring_hit:
            return True, details + "; reason=substring_fragment"
        if token_overlap >= token_threshold and bigram_overlap >= bigram_threshold:
            return True, details + "; reason=high_token_bigram_overlap"
        if token_overlap >= 0.78 and seq_ratio >= 0.58:
            return True, details + "; reason=high_token_sequence_overlap"
        return False, details + "; reason=below_threshold"

    def is_post_clear_suspicious_ocr(self, text, last_clear_time):
        """
        Step20: after subtitles disappear, animated background/tooltip noise can
        briefly look like text. Do not let such candidates flash on screen.
        """
        if not text or not last_clear_time:
            return False

        now = time.time()
        if now - float(last_clear_time or 0.0) > 1.6:
            return False

        norm = self.normalize_for_compare(text)
        if not norm:
            return True

        words = norm.split()
        letters = len(re.findall(r"[A-Za-zА-Яа-яІіЇїЄєҐґЁё]", text or ""))
        if letters < 18:
            return True

        # Short broken fragments without normal sentence ending are the common
        # case of flicker after an empty frame.
        if len(words) <= 5 and not re.search(r"[?.!…]$", str(text).strip()):
            return True

        # Cheap guard for short English-looking OCR debris.
        if len(words) >= 2:
            avg_word_len = sum(len(w) for w in words) / max(1, len(words))
            if avg_word_len < 2.6:
                return True

        return False

    def ocr_growth_debounce_seconds(self, behavior):
        try:
            value = float((behavior or {}).get("ocr_text_growth_debounce_sec", self._ocr_growth_debounce_sec))
        except Exception:
            value = self._ocr_growth_debounce_sec
        return max(0.45, min(1.6, value))

    def ocr_growth_apply_protection_seconds(self, behavior):
        try:
            value = float((behavior or {}).get("ocr_text_growth_apply_protect_sec", self._ocr_growth_apply_protect_sec))
        except Exception:
            value = self._ocr_growth_apply_protect_sec
        return max(0.8, min(1.6, value))

    def has_terminal_sentence_punctuation(self, text, post_ocr_policy="latin_en"):
        terminal_chars = r"?.!…"
        if str(post_ocr_policy or "latin_en") in {"chinese", "japanese"}:
            terminal_chars += "。！？"
        return bool(re.search(rf"[{re.escape(terminal_chars)}][\"')\]]*$", str(text or "").strip()))

    def text_growth_stats(self, text, post_ocr_policy="latin_en"):
        clean = str(text or "").strip()
        norm = self.normalize_for_compare(clean)
        words = norm.split()
        if str(post_ocr_policy or "latin_en") in {"chinese", "japanese"}:
            composition = post_ocr_classifier.analyze_script_composition(
                clean,
                "zh" if post_ocr_policy == "chinese" else "ja",
            )
            letters = int(composition.get("source_script_units") or 0)
        else:
            letters = len(re.findall(r"[A-Za-zА-Яа-яІіЇїЄєҐґЁё]", clean))
        return clean, norm, words, letters

    def is_ocr_growth_short_final(self, text, post_ocr_policy="latin_en"):
        """True for normal short subtitles that should not wait for typewriter debounce."""
        clean, norm, words, _letters = self.text_growth_stats(text, post_ocr_policy)
        if not clean or not norm:
            return False
        if (
            len(words) <= 4
            and len(clean) <= 54
            and self.has_terminal_sentence_punctuation(clean, post_ocr_policy)
        ):
            return True
        return False

    def is_ocr_growth_short_fragment_candidate(self, text, post_ocr_policy="latin_en"):
        """Short incomplete text that is risky during a typewriter/growth sequence."""
        clean, norm, words, letters = self.text_growth_stats(text, post_ocr_policy)
        if not clean or not norm:
            return False
        if self.is_ocr_growth_short_final(clean, post_ocr_policy):
            return False
        if self.has_terminal_sentence_punctuation(clean, post_ocr_policy):
            return False
        if str(post_ocr_policy or "latin_en") in {"chinese", "japanese"}:
            composition = post_ocr_classifier.analyze_script_composition(
                clean,
                "zh" if post_ocr_policy == "chinese" else "ja",
            )
            if composition.get("short_fragment_bypass_eligible"):
                return False
        return bool(len(words) <= 5 and letters <= 28)

    def is_probable_full_ocr_candidate(self, text, post_ocr_policy="latin_en"):
        """
        Long subtitle-looking text. Missing final punctuation is intentionally not
        enough to delay these candidates; Tesseract and games often omit it.
        """
        clean, norm, words, letters = self.text_growth_stats(text, post_ocr_policy)
        if not clean or not norm:
            return False
        if str(post_ocr_policy or "latin_en") in {"chinese", "japanese"}:
            composition = post_ocr_classifier.analyze_script_composition(
                clean,
                "zh" if post_ocr_policy == "chinese" else "ja",
            )
            if composition.get("short_fragment_bypass_eligible"):
                return True
        if len(words) >= 7 or letters >= 42:
            return True
        if len(words) >= 5 and re.search(r"[,;:]", clean):
            return True
        if len(words) >= 5 and re.search(r"[?.!…].+\w", clean):
            return True
        return False


    def is_ocr_text_same_pending(self, pending_norm, current_norm):
        if not pending_norm or not current_norm:
            return False
        if pending_norm == current_norm:
            return True
        try:
            return difflib.SequenceMatcher(None, pending_norm, current_norm).ratio() >= 0.96
        except Exception:
            return False

    def is_probable_text_growth_sequence(
        self,
        previous_text,
        new_text,
        timing_info=None,
        post_ocr_policy="latin_en",
    ):
        """
        True only when OCR text is actually growing from a recent previous fragment.
        Absence of punctuation is not used as the main signal here.
        """
        timing_info = timing_info or {}
        now = time.time()
        previous_clean, previous_norm, _previous_words, _previous_letters = self.text_growth_stats(
            previous_text,
            post_ocr_policy,
        )
        new_clean, new_norm, _new_words, _new_letters = self.text_growth_stats(
            new_text,
            post_ocr_policy,
        )
        if not previous_clean or not new_clean or not previous_norm or not new_norm:
            return False
        if len(new_norm) <= len(previous_norm):
            return False

        previous_time = float(timing_info.get("previous_time", 0.0) or 0.0)
        max_age = float(timing_info.get("max_age", self._ocr_growth_sequence_window_sec) or self._ocr_growth_sequence_window_sec)
        if previous_time > 0.0 and (now - previous_time) > max_age:
            return False

        growth_chars = len(new_norm) - len(previous_norm)
        minimum_growth_chars = (
            1
            if str(post_ocr_policy or "latin_en") in {"chinese", "japanese"}
            else 3
        )
        if growth_chars < minimum_growth_chars:
            return False

        if new_norm.startswith(previous_norm):
            return True

        prefix = new_norm[:len(previous_norm)]
        try:
            ratio = difflib.SequenceMatcher(None, previous_norm, prefix).ratio()
        except Exception:
            ratio = 0.0
        minimum_fuzzy_growth = (
            2
            if str(post_ocr_policy or "latin_en") in {"chinese", "japanese"}
            else 4
        )
        return bool(ratio >= 0.88 and growth_chars >= minimum_fuzzy_growth)

    def remember_ocr_growth_observation_locked(self, text, generation=None, sequence_hit=False):
        clean, norm, _words, _letters = self.text_growth_stats(text)
        if not clean or not norm:
            return
        now = time.time()
        current_generation = self._runtime_generation if generation is None else int(generation or 0)
        if sequence_hit:
            self._ocr_growth_sequence_count = max(1, int(self._ocr_growth_sequence_count or 0) + 1)
            self._ocr_growth_context_until = now + self._ocr_growth_sequence_window_sec
        else:
            self._ocr_growth_sequence_count = 0
        self._ocr_growth_recent_text = clean
        self._ocr_growth_recent_norm = norm
        self._ocr_growth_recent_time = now
        self._ocr_growth_recent_generation = current_generation

    def note_ocr_growth_short_reject(self, text, generation=None):
        """A short rejected OCR fragment can be the first seed of a growing subtitle."""
        with QMutexLocker(self.mutex):
            self.remember_ocr_growth_observation_locked(text, generation=generation, sequence_hit=False)

    def take_ocr_growth_pending_if_timer_expired(
        self,
        behavior,
        gate_state="",
        *,
        expected_timer_token=0,
    ):
        """Accept pending best_text when it stopped updating, without waiting for another OCR result."""
        now = time.time()
        now_monotonic = time.monotonic()
        debounce_sec = self.ocr_growth_debounce_seconds(behavior)
        with QMutexLocker(self.mutex):
            pending_text = self._ocr_growth_pending_text
            pending_updated_at = float(self._ocr_growth_pending_updated_at or 0.0)
            pending_since = float(self._ocr_growth_pending_since or 0.0)
            pending_generation = self._ocr_growth_pending_generation
            pending_kind = self._ocr_growth_pending_kind
            pending_norm = self._ocr_growth_pending_norm
            created_after_visual_clear = bool(self._ocr_growth_pending_created_after_visual_clear)
            pending_visual_density = float(self._ocr_growth_pending_visual_density or 0.0)
            pending_gate_state = self._ocr_growth_pending_gate_state
            real_ocr_hits = int(self._ocr_growth_pending_real_ocr_hits or 0)
            recheck_requested = bool(self._ocr_growth_pending_recheck_requested)
            sequence_count = int(self._ocr_growth_sequence_count or 0)
            current_generation = self._runtime_generation
            timer_token = int(self._ocr_growth_pending_timer_token or 0)
            deadline_monotonic = float(self._ocr_growth_pending_deadline_monotonic or 0.0)
            pending_classifier = (
                dict(self._ocr_growth_pending_classifier)
                if isinstance(self._ocr_growth_pending_classifier, dict)
                else {}
            )
            pending_source_gone = bool(self._ocr_growth_pending_source_gone)
            pending_task_id = int(self._ocr_growth_pending_task_id or 0)
        if not pending_text or pending_since <= 0.0:
            return None
        if expected_timer_token and int(expected_timer_token) != timer_token:
            return None
        if pending_generation != current_generation:
            with QMutexLocker(self.mutex):
                self.reset_ocr_growth_pending_locked(
                    final_outcome="growth_pending_cancelled",
                    reason="runtime_generation_changed",
                )
            return None

        if pending_source_gone:
            cancelled = False
            with QMutexLocker(self.mutex):
                if (
                    self._ocr_growth_pending_source_gone
                    and self._ocr_growth_pending_generation == current_generation
                    and self._ocr_growth_pending_timer_token == timer_token
                    and self.is_ocr_text_same_pending(self._ocr_growth_pending_norm, pending_norm)
                ):
                    self.reset_ocr_growth_pending_locked(
                        final_outcome="growth_pending_cancelled",
                        reason="source_gone_before_timer_commit",
                    )
                    cancelled = True
            if cancelled:
                self.log_runtime_event(
                    "OCR_TEXT_GROWTH_CANCELLED_SOURCE_GONE",
                    task_id=pending_task_id or None,
                    generation=current_generation,
                    text=pending_text,
                    reason="pending source disappeared before first timer commit",
                    extra=(
                        f"pending_kind={pending_kind}; timer_token={timer_token}; "
                        f"gate_state={gate_state}; real_ocr_hits={real_ocr_hits}"
                    ),
                )
            return None

        quiet_for = now - (pending_updated_at or pending_since)
        if deadline_monotonic > 0.0 and now_monotonic < deadline_monotonic:
            return None
        if deadline_monotonic <= 0.0 and quiet_for < debounce_sec:
            return None
        plausible_pending = bool(pending_classifier.get("plausible_natural_language"))
        pending_policy = str(pending_classifier.get("post_ocr_policy") or "latin_en")
        standalone_requires_recheck = bool(
            pending_kind == "standalone_short"
            and sequence_count == 0
            and self.is_ocr_growth_short_fragment_candidate(pending_text, pending_policy)
            and not plausible_pending
            and created_after_visual_clear
            and pending_visual_density >= float(self._visual_text_first_max_density or 0.70)
        )
        single_han_requires_recheck = pending_kind == "single_han"
        if standalone_requires_recheck or single_han_requires_recheck:
            if recheck_requested:
                return None
            with QMutexLocker(self.mutex):
                if (
                    self._ocr_growth_pending_generation != current_generation
                    or self._ocr_growth_pending_kind != pending_kind
                    or not self.is_ocr_text_same_pending(self._ocr_growth_pending_norm, pending_norm)
                ):
                    return None
                self._ocr_growth_pending_recheck_requested = True
            request_details = (
                f"pending_age={now - pending_since:.2f}s; quiet_for={quiet_for:.2f}s; "
                f"debounce={debounce_sec:.2f}s; gate_state={gate_state}; "
                f"origin_gate_state={pending_gate_state}; origin_density={pending_visual_density:.4f}; "
                f"created_after_visual_clear={created_after_visual_clear}; real_ocr_hits={real_ocr_hits}"
            )
            self.log_ocr_diagnostic(
                "OCR_STANDALONE_SHORT_RECHECK_REQUESTED",
                pending_text,
                request_details,
            )
            self.log_runtime_event(
                "OCR_STANDALONE_SHORT_RECHECK_REQUESTED",
                generation=current_generation,
                text=pending_text,
                reason=(
                    "single_han_pending"
                    if single_han_requires_recheck
                    else "standalone short pending requires one real OCR recheck"
                ),
                extra=f"{request_details}; pending_kind={pending_kind}",
            )
            return {
                "request_real_ocr": True,
                "text": pending_text,
                "norm": pending_norm,
                "task_id": pending_task_id,
                "generation": current_generation,
                "gate_state": pending_gate_state,
                "source_gone": pending_source_gone,
                "timer_token": timer_token,
                "deadline_monotonic": deadline_monotonic,
                "pending_age": now - pending_since,
                "quiet_for": quiet_for,
                "debounce": debounce_sec,
                "created_after_visual_clear": created_after_visual_clear,
                "visual_density": pending_visual_density,
                "origin_gate_state": pending_gate_state,
                "real_ocr_hits": real_ocr_hits,
                "pending_kind": pending_kind,
            }

        with QMutexLocker(self.mutex):
            accepted_text = self._ocr_growth_pending_text
            accepted_since = float(self._ocr_growth_pending_since or pending_since)
            accepted_updated_at = float(self._ocr_growth_pending_updated_at or pending_updated_at)
            accepted_classifier = (
                dict(self._ocr_growth_pending_classifier)
                if isinstance(self._ocr_growth_pending_classifier, dict)
                else {}
            )
            self.reset_ocr_growth_pending_locked(
                final_outcome="growth_pending_committed",
                reason="standalone_deadline_expired",
            )

        pending_age = now - accepted_since if accepted_since > 0.0 else 0.0
        accepted_quiet_for = now - (accepted_updated_at or accepted_since)
        self.log_runtime_event(
            "OCR_TEXT_GROWTH_ACCEPTED_BY_TIMER",
            generation=current_generation,
            text=accepted_text,
            reason="pending OCR text stopped updating; accepted without OCR recheck",
            extra=(
                f"pending_age={pending_age:.2f}s; quiet_for={accepted_quiet_for:.2f}s; "
                f"debounce={debounce_sec:.2f}s; gate_state={gate_state}; "
                f"timer_token={timer_token}; source_gone={pending_source_gone}"
            ),
        )
        return {
            "text": accepted_text,
            "task_id": pending_task_id,
            "generation": current_generation,
            "gate_state": pending_gate_state,
            "pending_age": pending_age,
            "quiet_for": accepted_quiet_for,
            "debounce": debounce_sec,
            "timer_token": timer_token,
            "classification": accepted_classifier,
            "source_gone": pending_source_gone,
            "deadline_monotonic": deadline_monotonic,
        }

    def maybe_hold_ocr_growth_candidate(
        self,
        text,
        behavior,
        task_id=None,
        generation=None,
        gate_state="",
        visual_density=0.0,
        classification=None,
    ):
        """
        Returns a decision dict:
        - hold=True means do not translate yet.
        - accepted_from_growth=True means continue to translation and protect apply briefly.
        """
        clean = str(text or "").strip()
        norm = self.normalize_for_compare(clean)
        if not clean or not norm:
            return {"hold": False, "accepted_from_growth": False}

        now = time.time()
        debounce_sec = self.ocr_growth_debounce_seconds(behavior)
        post_ocr_policy = str((classification or {}).get("post_ocr_policy") or "latin_en")
        cjk_policy = post_ocr_policy in {"chinese", "japanese"}
        single_cjk_han_cautious = bool(
            post_ocr_policy in {"chinese", "japanese"}
            and str((classification or {}).get("reason_code") or "") in {
                "single_han_pending",
                "single_kanji_pending",
            }
            and int((classification or {}).get("source_script_units") or 0) == 1
        )
        single_cjk_confirmed_reason = (
            "single_kanji_confirmed"
            if str((classification or {}).get("reason_code") or "") == "single_kanji_pending"
            else "single_han_confirmed"
        )
        short_final = self.is_ocr_growth_short_final(clean, post_ocr_policy)
        short_fragment = self.is_ocr_growth_short_fragment_candidate(clean, post_ocr_policy)
        full_candidate = self.is_probable_full_ocr_candidate(clean, post_ocr_policy)

        with QMutexLocker(self.mutex):
            pending_text = self._ocr_growth_pending_text
            pending_norm = self._ocr_growth_pending_norm
            pending_since = self._ocr_growth_pending_since
            pending_updated_at = self._ocr_growth_pending_updated_at
            pending_generation = self._ocr_growth_pending_generation
            pending_kind = self._ocr_growth_pending_kind
            recent_text = self._ocr_growth_recent_text
            recent_time = float(self._ocr_growth_recent_time or 0.0)
            context_active = bool(self._ocr_growth_context_until and now <= self._ocr_growth_context_until)
            sequence_count = int(self._ocr_growth_sequence_count or 0)
            current_generation = self._runtime_generation
            last_display_clear_time = float(self._last_display_clear_time or 0.0)
            created_after_visual_clear = bool(
                last_display_clear_time > 0.0
                and (now - last_display_clear_time) <= 4.0
            )

            if pending_generation and pending_generation != current_generation:
                self.reset_ocr_growth_pending_locked()
                pending_text = ""
                pending_norm = ""
                pending_since = 0.0
                pending_updated_at = 0.0
                recent_text = ""
                recent_time = 0.0
                context_active = False

            grows_pending = bool(
                pending_text
                and self.is_probable_text_growth_sequence(
                    pending_text,
                    clean,
                    {"previous_time": pending_updated_at or pending_since, "max_age": self._ocr_growth_sequence_window_sec},
                    post_ocr_policy,
                )
            )
            grows_recent = bool(
                (not pending_text)
                and recent_text
                and self.is_probable_text_growth_sequence(
                    recent_text,
                    clean,
                    {"previous_time": recent_time, "max_age": self._ocr_growth_sequence_window_sec},
                    post_ocr_policy,
                )
            )

            if single_cjk_han_cautious and not pending_text:
                self.set_ocr_growth_pending_locked(
                    clean,
                    norm,
                    now,
                    current_generation,
                    "single_han",
                    gate_state,
                    visual_density,
                    created_after_visual_clear,
                    task_id=task_id,
                    classification=classification,
                )
                self.remember_ocr_growth_observation_locked(
                    clean,
                    generation=current_generation,
                    sequence_hit=False,
                )
                action = "single_han_pending"
                pending_age = 0.0
            elif short_final and not pending_text:
                self.remember_ocr_growth_observation_locked(clean, generation=current_generation, sequence_hit=False)
                action = "short_final_accept"
                pending_age = 0.0
            elif pending_text:
                same_pending = self.is_ocr_text_same_pending(pending_norm, norm)
                if (
                    single_cjk_han_cautious
                    and pending_kind == "single_han"
                    and not same_pending
                ):
                    self.set_ocr_growth_pending_locked(
                        clean,
                        norm,
                        now,
                        current_generation,
                        "single_han",
                        gate_state,
                        visual_density,
                        created_after_visual_clear,
                        task_id=task_id,
                        classification=classification,
                    )
                    self.remember_ocr_growth_observation_locked(
                        clean,
                        generation=current_generation,
                        sequence_hit=False,
                    )
                    pending_age = 0.0
                    action = "single_han_pending"
                elif grows_pending:
                    self.set_ocr_growth_pending_locked(
                        clean,
                        norm,
                        now,
                        current_generation,
                        "growth_sequence",
                        gate_state,
                        visual_density,
                        created_after_visual_clear,
                        task_id=task_id,
                        classification=classification,
                    )
                    self.remember_ocr_growth_observation_locked(clean, generation=current_generation, sequence_hit=True)
                    pending_age = 0.0
                    action = "updated"
                elif same_pending:
                    self._ocr_growth_pending_real_ocr_hits = max(
                        1,
                        int(self._ocr_growth_pending_real_ocr_hits or 0) + 1,
                    )
                    quiet_for = now - (pending_updated_at or pending_since) if pending_since > 0.0 else 0.0
                    pending_age = now - pending_since if pending_since > 0.0 else 0.0
                    if pending_kind == "single_han" and single_cjk_han_cautious:
                        self.reset_ocr_growth_pending_locked(
                            final_outcome="growth_pending_committed",
                            reason=single_cjk_confirmed_reason,
                        )
                        action = "single_cjk_han_confirmed"
                    elif cjk_policy:
                        self.reset_ocr_growth_pending_locked(
                            final_outcome="growth_pending_committed",
                            reason="cjk_repeated_real_ocr_confirmed",
                        )
                        action = "cjk_repeated_confirmed"
                    elif short_fragment and sequence_count > 0:
                        action = "short_fragment_held"
                    elif quiet_for >= debounce_sec:
                        self.reset_ocr_growth_pending_locked(
                            final_outcome="growth_pending_committed",
                            reason="confirmed_by_repeated_ocr",
                        )
                        action = "accepted"
                    else:
                        action = "hold_same"
                elif short_fragment and context_active:
                    self.set_ocr_growth_pending_locked(
                        clean,
                        norm,
                        now,
                        current_generation,
                        "growth_sequence",
                        gate_state,
                        visual_density,
                        created_after_visual_clear,
                        task_id=task_id,
                        classification=classification,
                    )
                    self.remember_ocr_growth_observation_locked(clean, generation=current_generation, sequence_hit=True)
                    pending_age = 0.0
                    action = "short_fragment_held"
                elif full_candidate:
                    if cjk_policy:
                        self.reset_ocr_growth_pending_locked(
                            final_outcome="growth_pending_replaced",
                            reason="new_unrelated_cjk_candidate",
                        )
                        self.remember_ocr_growth_observation_locked(
                            clean,
                            generation=current_generation,
                            sequence_hit=False,
                        )
                        action = "accept_unrelated"
                    else:
                        self.reset_ocr_growth_pending_locked(
                            final_outcome="growth_pending_replaced",
                            reason="new_full_candidate",
                        )
                        action = "bypass_full"
                    pending_age = 0.0
                else:
                    self.reset_ocr_growth_pending_locked(
                        final_outcome="growth_pending_replaced",
                        reason="new_unrelated_candidate",
                    )
                    action = "accept_unrelated"
                    pending_age = 0.0
            elif grows_recent:
                self.set_ocr_growth_pending_locked(
                    clean,
                    norm,
                    now,
                    current_generation,
                    "growth_sequence",
                    gate_state,
                    visual_density,
                    created_after_visual_clear,
                    task_id=task_id,
                    classification=classification,
                )
                self.remember_ocr_growth_observation_locked(clean, generation=current_generation, sequence_hit=True)
                pending_age = 0.0
                action = "short_fragment_held" if short_fragment else "new_pending"
            elif short_fragment:
                self.set_ocr_growth_pending_locked(
                    clean,
                    norm,
                    now,
                    current_generation,
                    "standalone_short",
                    gate_state,
                    visual_density,
                    created_after_visual_clear,
                    task_id=task_id,
                    classification=classification,
                )
                self.remember_ocr_growth_observation_locked(clean, generation=current_generation, sequence_hit=False)
                pending_age = 0.0
                action = "standalone_short_pending"
            elif full_candidate:
                self.remember_ocr_growth_observation_locked(clean, generation=current_generation, sequence_hit=False)
                action = "bypass_full"
                pending_age = 0.0
            else:
                self.remember_ocr_growth_observation_locked(clean, generation=current_generation, sequence_hit=False)
                action = "accept"
                pending_age = 0.0

        source_change_result = None
        early_source_change_allowed = bool(
            cjk_policy and bool((classification or {}).get("accepted"))
        )
        if early_source_change_allowed and action in {
            "new_pending",
            "standalone_short_pending",
            "single_han_pending",
            "updated",
            "short_fragment_held",
            "hold_same",
        }:
            source_change_result = self.note_pending_subtitle_source_change(
                clean,
                task_id=task_id,
                task_generation=current_generation,
            )
            if source_change_result.get("source_change_detected"):
                current_generation = int(
                    source_change_result.get("task_generation", current_generation)
                )
                generation = current_generation
                with QMutexLocker(self.mutex):
                    if self.is_ocr_text_same_pending(self._ocr_growth_pending_norm, norm):
                        self._ocr_growth_pending_generation = current_generation

        timer_token = 0
        if action in {
            "new_pending",
            "standalone_short_pending",
            "single_han_pending",
            "updated",
            "short_fragment_held",
            "hold_same",
        }:
            remaining = debounce_sec
            if action == "hold_same":
                remaining = max(0.01, debounce_sec - max(0.0, quiet_for))
            timer_token = self.arm_ocr_growth_pending_deadline(remaining)

        if action == "short_final_accept":
            self.log_runtime_event(
                "OCR_TEXT_GROWTH_SHORT_FINAL_ACCEPTED",
                task_id=task_id,
                generation=generation,
                text=clean,
                reason="short subtitle has final punctuation",
                extra=f"debounce={debounce_sec:.2f}s; gate_state={gate_state}",
            )
            return {"hold": False, "accepted_from_growth": False}

        if action == "bypass_full":
            self.log_runtime_event(
                "OCR_TEXT_GROWTH_BYPASS_FULL_CANDIDATE",
                task_id=task_id,
                generation=generation,
                text=clean,
                reason="full OCR candidate accepted; missing final punctuation is not enough for pending",
                extra=f"debounce={debounce_sec:.2f}s; gate_state={gate_state}",
            )
            return {"hold": False, "accepted_from_growth": False}

        if action == "short_fragment_held":
            self.log_runtime_event(
                "OCR_TEXT_GROWTH_SHORT_FRAGMENT_HELD",
                task_id=task_id,
                generation=generation,
                text=clean,
                reason="short incomplete OCR fragment held in growth context",
                extra=f"pending_age={pending_age:.2f}s; debounce={debounce_sec:.2f}s; gate_state={gate_state}; timer_token={timer_token}",
            )
            return {"hold": True, "accepted_from_growth": False}

        if action in {"new_pending", "standalone_short_pending", "single_han_pending"}:
            self.log_runtime_event(
                "OCR_TEXT_GROWTH_PENDING",
                task_id=task_id,
                generation=generation,
                text=clean,
                reason=(
                    "single_han_pending"
                    if action == "single_han_pending"
                    else "OCR text is waiting for text-level debounce"
                ),
                extra=(
                    f"debounce={debounce_sec:.2f}s; gate_state={gate_state}; "
                    f"action={action}; timer_token={timer_token}; "
                    f"source_change_detected={'false' if action == 'single_han_pending' else 'not_checked'}"
                ),
            )
            return {"hold": True, "accepted_from_growth": False}

        if action == "single_cjk_han_confirmed":
            self.log_runtime_event(
                "OCR_TEXT_GROWTH_ACCEPTED",
                task_id=task_id,
                generation=generation,
                text=clean,
                reason=single_cjk_confirmed_reason,
                extra=f"gate_state={gate_state}; real_ocr_hits=2",
            )
            return {
                "hold": False,
                "accepted_from_growth": True,
                "source": "single_cjk_han_confirmation",
                "reason_code": single_cjk_confirmed_reason,
            }

        if action == "cjk_repeated_confirmed":
            self.log_runtime_event(
                "OCR_TEXT_GROWTH_ACCEPTED",
                task_id=task_id,
                generation=generation,
                text=clean,
                reason="cjk_repeated_real_ocr_confirmed",
                extra=f"gate_state={gate_state}; real_ocr_hits=2",
            )
            return {
                "hold": False,
                "accepted_from_growth": True,
                "source": "cjk_repeated_real_ocr",
                "reason_code": "cjk_repeated_real_ocr_confirmed",
            }

        if action == "updated":
            self.log_runtime_event(
                "OCR_TEXT_GROWTH_UPDATED",
                task_id=task_id,
                generation=generation,
                text=clean,
                reason="OCR candidate extends pending text",
                extra=f"pending_age={pending_age:.2f}s; debounce={debounce_sec:.2f}s; gate_state={gate_state}; timer_token={timer_token}",
            )
            return {"hold": True, "accepted_from_growth": False}

        if action == "hold_same":
            self.log_runtime_event(
                "OCR_TEXT_GROWTH_PENDING",
                task_id=task_id,
                generation=generation,
                text=clean,
                reason="pending OCR candidate has not reached debounce",
                extra=f"pending_age={pending_age:.2f}s; debounce={debounce_sec:.2f}s; gate_state={gate_state}; timer_token={timer_token}",
            )
            return {"hold": True, "accepted_from_growth": False}

        if action == "accepted":
            self.log_runtime_event(
                "OCR_TEXT_GROWTH_DEBOUNCE_EXPIRED",
                task_id=task_id,
                generation=generation,
                text=clean,
                reason="pending OCR candidate stopped growing",
                extra=f"pending_age={pending_age:.2f}s; debounce={debounce_sec:.2f}s; gate_state={gate_state}",
            )
            self.log_runtime_event(
                "OCR_TEXT_GROWTH_ACCEPTED",
                task_id=task_id,
                generation=generation,
                text=clean,
                reason="text-level debounce accepted candidate for translation",
                extra=f"gate_state={gate_state}",
            )

        if action == "accept_unrelated" and cjk_policy:
            self.log_runtime_event(
                "OCR_CJK_UNRELATED_CANDIDATE_ACCEPTED",
                task_id=task_id,
                generation=generation,
                text=clean,
                reason="candidate is not a prefix/subset growth of pending CJK text",
                extra=f"gate_state={gate_state}; post_ocr_policy={post_ocr_policy}",
            )

        return {"hold": False, "accepted_from_growth": action == "accepted", "source": "debounce" if action == "accepted" else ""}

    def note_repeated_empty_dense_area(self, visual_signature, visual_density, behavior, task_id=None, generation=None):
        """
        Tracks OCR_EMPTY on a dense/noisy visual mask. Returns details dict.
        It does not clear by itself; caller routes clear through handle_empty_or_bad_ocr().
        """
        now = time.time()
        try:
            density = float(visual_density or 0.0)
        except Exception:
            density = 0.0
        try:
            min_density = float((behavior or {}).get("repeated_empty_dense_min_density", self._repeated_empty_dense_min_density))
        except Exception:
            min_density = self._repeated_empty_dense_min_density
        min_density = max(self._visual_text_empty_density * 2.0, min_density)

        if density < min_density:
            with QMutexLocker(self.mutex):
                self._repeated_empty_signature = ""
                self._repeated_empty_density = 0.0
                self._repeated_empty_count = 0
                self._repeated_empty_since = 0.0
                self._repeated_empty_last_time = 0.0
            return {
                "dense": False,
                "count": 0,
                "age": 0.0,
                "should_clear": False,
                "details": f"density={density:.4f}; min_density={min_density:.4f}",
            }

        signature = str(visual_signature or "")
        with QMutexLocker(self.mutex):
            prev_signature = self._repeated_empty_signature
            prev_density = float(self._repeated_empty_density or 0.0)
            prev_since = float(self._repeated_empty_since or 0.0)
            last_seen_age = now - float(self._repeated_empty_last_time or 0.0)
            same_area = bool(
                prev_since > 0.0
                and last_seen_age <= 2.4
                and (
                    (signature and signature == prev_signature)
                    or abs(density - prev_density) <= 0.040
                    or (density >= min_density and prev_density >= min_density)
                )
            )
            if same_area:
                self._repeated_empty_count += 1
                if self._repeated_empty_since <= 0.0:
                    self._repeated_empty_since = now
            else:
                self._repeated_empty_signature = signature
                self._repeated_empty_density = density
                self._repeated_empty_count = 1
                self._repeated_empty_since = now
                prev_since = now
            self._repeated_empty_last_time = now
            count = int(self._repeated_empty_count)
            since = float(self._repeated_empty_since or prev_since or now)

        try:
            min_count = int((behavior or {}).get("repeated_empty_dense_min_count", self._repeated_empty_dense_min_count))
        except Exception:
            min_count = self._repeated_empty_dense_min_count
        try:
            clear_after = float((behavior or {}).get("repeated_empty_dense_clear_sec", self._repeated_empty_dense_clear_sec))
        except Exception:
            clear_after = self._repeated_empty_dense_clear_sec
        min_count = max(2, min_count)
        clear_after = max(0.45, min(1.5, clear_after))
        age = now - since
        should_clear = bool(count >= min_count and age >= clear_after)
        details = (
            f"density={density:.4f}; min_density={min_density:.4f}; "
            f"count={count}; min_count={min_count}; age={age:.2f}s; clear_after={clear_after:.2f}s"
        )

        armed_details = ""
        armed_source = ""
        if count == 1:
            with QMutexLocker(self.mutex):
                restore_state = self._post_clear_same_source_state
                restore_time = float(self._post_clear_same_source_time or 0.0)
                restore_generation = int(self._post_clear_same_source_generation or 0)
                current_generation = int(self._runtime_generation)
                state_age = now - restore_time if restore_time > 0.0 else 0.0
                can_arm = bool(
                    restore_state in {"blocked", "restored", "allowed_reapply"}
                    and restore_generation == current_generation
                    and state_age <= 8.0
                    and self._dense_empty_clear_deadline <= 0.0
                )
                if can_arm:
                    self._dense_empty_clear_deadline = now + clear_after
                    self._dense_empty_clear_generation = current_generation
                    self._dense_empty_clear_source = self._post_clear_same_source_text
                    armed_source = self._dense_empty_clear_source
                    armed_details = (
                        f"state={restore_state}; state_age={state_age:.2f}s; "
                        f"deadline_in={clear_after:.2f}s; generation={current_generation}; {details}"
                    )

        if armed_details:
            self.log_ocr_diagnostic("DENSE_EMPTY_CLEAR_ARMED", armed_source, armed_details)
            self.log_runtime_event(
                "DENSE_EMPTY_CLEAR_ARMED",
                task_id=task_id,
                generation=generation,
                text=armed_source,
                reason="first dense OCR_EMPTY after post-clear same-source restore decision",
                extra=armed_details,
            )

        self.log_runtime_event(
            "OCR_REPEATED_EMPTY_DENSE_AREA" if count >= min_count else "OCR_EMPTY_DENSE_AREA_HOLD",
            task_id=task_id,
            generation=generation,
            reason="OCR_EMPTY on dense visual mask",
            extra=details,
        )
        return {
            "dense": True,
            "count": count,
            "age": age,
            "clear_after": clear_after,
            "should_clear": should_clear,
            "details": details,
        }

    def apply_dense_empty_clear_if_due(self, behavior):
        """Завершает pending clear, даже если visual gate больше не запускает OCR."""
        now = time.time()
        with QMutexLocker(self.mutex):
            deadline = float(self._dense_empty_clear_deadline or 0.0)
            armed_generation = int(self._dense_empty_clear_generation or 0)
            current_generation = int(self._runtime_generation)
            source = self._dense_empty_clear_source
            restore_state = self._post_clear_same_source_state

            if deadline <= 0.0:
                return False
            if (
                armed_generation != current_generation
                or not self.is_running
                or self.user_interaction_paused
            ):
                self.reset_post_clear_dense_state_locked()
                return False
            if now < deadline:
                return False

            had_display_state = bool(self.last_text or self.last_good_translation)
            overdue_sec = now - deadline
            self._dense_empty_clear_deadline = 0.0
            self._dense_empty_clear_generation = 0
            self._dense_empty_clear_source = ""

        if not had_display_state:
            with QMutexLocker(self.mutex):
                self.reset_post_clear_dense_state_locked()
            return False

        forced_behavior = dict(behavior or {})
        forced_behavior["auto_clear"] = True
        forced_behavior["last_translation_hold_seconds"] = 0.0
        clear_details = (
            f"state={restore_state}; deadline_overdue={overdue_sec:.2f}s; "
            f"armed_generation={armed_generation}; source={str(source or '')[:180]!r}"
        )
        self.handle_empty_or_bad_ocr(
            forced_behavior,
            clear_event="DENSE_EMPTY_CLEAR_APPLIED",
            diagnostic_reason="DENSE_EMPTY_CLEAR_APPLIED",
            force_clear_after_seconds=0.0,
            clear_reason_extra=clear_details,
        )
        return True

    def confirm_post_clear_suspicious_text(self, text):
        """Return True only if the same suspicious post-clear candidate repeats."""
        now = time.time()
        norm = self.normalize_for_compare(text)
        with QMutexLocker(self.mutex):
            old = self._post_clear_suspicious_text
            old_since = self._post_clear_suspicious_since
            if old:
                ratio = difflib.SequenceMatcher(None, norm, old).ratio()
                if ratio >= 0.94 and (now - old_since) >= 0.18:
                    self._post_clear_suspicious_text = ""
                    self._post_clear_suspicious_since = 0.0
                    return True
            self._post_clear_suspicious_text = norm
            self._post_clear_suspicious_since = now
            return False

    def is_ocr_regression_subset(self, new_text, previous_text):
        """
        Проверяет, не является ли новый OCR-текст укороченным фрагментом
        уже переведённой более полной фразы.

        Пример проблемы:
            previous: "That's not to mention ... Something's not right with this"
            new:      "That's not to mention ..."

        В таком случае новый текст НЕ должен затирать полный перевод.
        """
        if not new_text or not previous_text:
            return False

        new_norm = self.normalize_for_compare(new_text)
        prev_norm = self.normalize_for_compare(previous_text)

        if not new_norm or not prev_norm:
            return False

        # Нельзя блокировать реально новые короткие фразы.
        # Поэтому проверяем именно подмножество предыдущей фразы и заметную разницу длины.
        if len(new_norm) >= len(prev_norm):
            return False

        # Если новый текст почти полностью содержится в предыдущем более длинном тексте,
        # считаем это регрессией OCR.
        # Step20: trailing losses can be very short but visually damaging:
        # "... looks off" -> "... looks" is only a few characters shorter,
        # but must not replace the fuller phrase. Require a reasonably long
        # candidate to avoid blocking genuinely short UI labels.
        if new_norm in prev_norm and len(prev_norm) - len(new_norm) >= 4 and len(new_norm) >= 16:
            return True

        # Если строка чуть повреждена OCR, прямое `in` может не сработать.
        # Тогда сравниваем похожесть нового текста с началом/концом/окном предыдущего.
        # Это ловит случаи вроде "middl" vs "middle", но не блокирует несвязанные фразы.
        if len(new_norm) >= 18:
            windows = [
                prev_norm[:len(new_norm)],
                prev_norm[-len(new_norm):],
            ]

            # Дополнительно проверяем скользящие окна, но редко и по короткой строке,
            # поэтому это не должно заметно грузить приложение.
            step = max(8, len(new_norm) // 3)
            for start in range(0, max(1, len(prev_norm) - len(new_norm) + 1), step):
                windows.append(prev_norm[start:start + len(new_norm)])

            for part in windows:
                if not part:
                    continue
                ratio = difflib.SequenceMatcher(None, new_norm, part).ratio()
                if ratio >= 0.92 and len(prev_norm) - len(new_norm) >= 12:
                    return True

        return False

    def ocr_variant_quality_score(self, text):
        """
        Локальная оценка качества OCR-варианта для случая почти одинаковых строк.

        Это НЕ общий литературный редактор и НЕ замена OCR. Нужна только для выбора
        между двумя почти одинаковыми вариантами, когда один содержит очевидный
        OCR-сбой, а второй выглядит нормальнее.
        """
        text = text or ""
        low = text.lower()

        score = 0

        # Положительные признаки нормального английского текста.
        good_words = [
            "i'm", "im", "expert", "combination", "clothes", "looks", "looks off",
            "something", "right", "middle", "button", "mention",
        ]
        for word in good_words:
            if word in low:
                score += 3

        # Нормальная связка "looks off" важнее, чем ошибочная "looks. off".
        if re.search(r"\blooks\s+off\b", low):
            score += 10
        if re.search(r"\blooks\s*[.,]\s+off\b", low):
            score -= 10

        # Явные OCR-сбои из реальных логов.
        bad_patterns = [
            r"\bwombination\b",
            r"\bcopiation\b",
            r"\bclobes\b",
            r"\bkoy\b",
            r"\bwww\b",
            r"\bvv\b",
            r"\boo,\b",
            r"\bfee\b",
        ]
        for pattern in bad_patterns:
            if re.search(pattern, low):
                score -= 12

        # Штраф за явный хвостовой мусор.
        if re.search(r"\s+(?:WwW|Vv|Koy|Lo|oS)\s*$", text, flags=re.IGNORECASE):
            score -= 15

        # Строки с большим количеством букв полезнее совсем коротких обрезков,
        # но длина не должна побеждать качество любой ценой.
        letters = len(re.findall(r"[A-Za-zА-Яа-яІіЇїЄєҐґЁё]", text))
        score += min(letters // 12, 8)

        return score

    def is_better_ocr_variant(self, new_text, previous_text):
        """
        Разрешает заменить почти одинаковый OCR-текст, если новый вариант явно лучше.

        Это нужно после duplicate-similar проверки: иначе одна плохая стабильная строка
        может надолго заблокировать нормальный вариант.
        """
        if not new_text or not previous_text:
            return False

        new_norm = self.normalize_for_compare(new_text)
        prev_norm = self.normalize_for_compare(previous_text)
        if not new_norm or not prev_norm:
            return False

        # Не считаем "лучшим" сильное укорочение фразы.
        if len(new_norm) + 8 < len(prev_norm):
            return False

        new_score = self.ocr_variant_quality_score(new_text)
        prev_score = self.ocr_variant_quality_score(previous_text)

        return new_score >= prev_score + 8

    def is_unstable_short_fragment(self, text, source_lang="en", classification=None):
        """
        Отбраковка коротких одиночных обрывков.

        Это не общий фильтр мусора, а защита от промежуточного OCR во время появления текста.
        Например, в твоём логе были одиночные обрывки "citi" и "eesk".
        """
        clean = (text or "").strip()
        if not clean:
            return True

        policy = str(
            (classification or {}).get("post_ocr_policy")
            or post_ocr_classifier.post_ocr_policy_for_source(source_lang)
        )
        if policy in {"chinese", "japanese"}:
            metrics = (
                classification
                if isinstance(classification, dict) and classification
                else post_ocr_classifier.analyze_script_composition(clean, source_lang)
            )
            if bool(metrics.get("accepted", True)) and int(metrics.get("source_script_units") or 0) >= 2:
                return False
            if (
                policy in {"chinese", "japanese"}
                and str(metrics.get("reason_code") or "") in {
                    "single_han_pending",
                    "single_kanji_pending",
                }
                and int(metrics.get("source_script_units") or 0) == 1
            ):
                return False

        words = [w.strip(".,!?\"'()-").lower() for w in clean.split() if w.strip()]
        if not words:
            return True

        # Одно короткое слово без знаков завершения чаще всего является кусочком недописанной фразы.
        if self.has_terminal_sentence_punctuation(clean):
            return False

        if len(words) == 1:
            only = words[0]
            if len(only) <= 4:
                return True

            # "Looks" и похожие слова могут быть полноценной короткой игровой репликой,
            # поэтому слова длиной 5+ не режем автоматически.
            return False

        return False

    def sanitize_text(self, text):
        """
        Лёгкая пост-очистка OCR-строки.

        Основная OCR-очистка теперь находится в ocr_core.clean_ocr_text().
        Здесь оставлена страховка на случай, если в OCR-модуле вернулась строка с краевыми точками/мусором.
        """
        text = re.sub(r'^[.\s]+|[.\s]+$', '', text or "")
        text = re.sub(r'\s+', ' ', text).strip()
        return text.strip()



class OverlayModelWarmUpThread(QThread):
    """
    Отдельный поток предзагрузки модели перевода внутри процесса оверлея.

    Почему это отдельный QThread, а не обычный threading.Thread:
    - PyQt-сигналы безопаснее использовать из QThread;
    - мы не вызываем методы TranslationEngine из чужого Python-thread;
    - модель загружается в том же процессе оверлея, поэтому translator_core._instance
      остаётся прогретым для последующих переводов.

    Важно: лаунчер и оверлей — разные процессы. Прогрев в лаунчере не переносит
    веса модели в память процесса оверлея. Поэтому оффлайн-режим прогревается здесь повторно.
    """

    status_signal = pyqtSignal(str)
    ready_signal = pyqtSignal(bool, str)

    def __init__(self, target_lang, interface_lang="ru", backend_name="", source_lang="en", request_context=None):
        super().__init__()
        self.target_lang = target_lang
        self.interface_lang = normalize_interface_lang(interface_lang)
        self.backend_name = str(backend_name or "").strip().lower()
        self.source_lang = str(source_lang or "en").strip().lower()
        self.request_context = dict(request_context or {})

    def run(self):
        try:
            is_translategemma_llama_q6 = self.backend_name == "translategemma_llama_q6_low_vram"
            is_translategemma_llama_q6_speed = self.backend_name == "translategemma_llama_q6_speed"
            is_translategemma_llama_q6_family = is_translategemma_llama_q6 or is_translategemma_llama_q6_speed
            is_translategemma = is_translategemma_llama_q6_family
            is_block_router = self.backend_name in {"block2_router", "block3_router"}
            if is_translategemma_llama_q6_family:
                log_startup_event("TRANSLATEGEMMA_WARMUP_BEGIN", backend=self.backend_name)
            if is_translategemma_llama_q6_speed:
                status_key = "overlay_translategemma_llama_q6_speed_loading"
            elif is_translategemma_llama_q6:
                status_key = "overlay_translategemma_llama_q6_loading"
            else:
                status_key = "overlay_init_model"
            self.status_signal.emit(tr(self.interface_lang, status_key))

            def preparation_status(stage):
                if stage in {"waiting", "preparing"}:
                    self.status_signal.emit(tr(self.interface_lang, "overlay_vulkan_preparing"))
                elif stage == "loading":
                    self.status_signal.emit(tr(self.interface_lang, status_key))

            if is_block_router:
                translator_core.load_model(
                    self.backend_name,
                    request_context=self.request_context,
                    status_callback=preparation_status,
                )
                self.ready_signal.emit(True, tr(self.interface_lang, "overlay_model_ready"))
                return

            if is_translategemma:
                translator_core.load_model(self.backend_name, status_callback=preparation_status)
                ready_key = (
                    "overlay_translategemma_llama_q6_speed_ready"
                    if is_translategemma_llama_q6_speed
                    else "overlay_translategemma_llama_q6_ready"
                )
                self.ready_signal.emit(
                    True,
                    tr(self.interface_lang, ready_key),
                )
                if is_translategemma_llama_q6_family:
                    log_startup_event("TRANSLATEGEMMA_WARMUP_END", backend=self.backend_name)
                return

            # Греем именно ту языковую пару, которую пользователь выбрал.
            # translate_text сам подгрузит MarianMT-модель для en-uk или en-ru.
            translator_core.translate_text(
                "Warm up system. Initialize neural network weights.",
                src_lang=self.source_lang,
                target_lang=self.target_lang,
                use_online=False,
                backend_name=self.backend_name or None,
            )

            self.ready_signal.emit(True, tr(self.interface_lang, "overlay_ai_ready", hotkey="Ctrl+Alt+Shift+P"))
        except Exception as e:
            if self.backend_name in {
                "translategemma_llama_q6_low_vram",
                "translategemma_llama_q6_speed",
            }:
                log_startup_event(
                    "TRANSLATEGEMMA_WARMUP_ERROR",
                    backend=self.backend_name,
                    error=f"{type(e).__name__}: {e}",
                )
            if str(e).startswith("quality_runtime_unavailable:"):
                msg = quality_unavailable_message({"reason": str(e).split(":", 1)[1]})
            elif self.backend_name == "translategemma_llama_q6_speed":
                msg = tr(
                    self.interface_lang,
                    "overlay_translategemma_llama_q6_speed_error",
                    error=str(e),
                )
            elif self.backend_name == "translategemma_llama_q6_low_vram":
                error_code = str(getattr(e, "code", "") or "")
                specific_key = {
                    "readiness_timeout": "overlay_translategemma_llama_q6_error_readiness_timeout",
                    "runtime_process_exited": "overlay_translategemma_llama_q6_error_runtime_process_exited",
                    "translation_timeout": "overlay_translategemma_llama_q6_error_translation_timeout",
                    "request_cancelled": "overlay_translategemma_llama_q6_error_request_cancelled",
                    "backend_unavailable": "overlay_translategemma_llama_q6_error_backend_unavailable",
                    "model_load_failed": "overlay_translategemma_llama_q6_error_model_load_failed",
                }.get(error_code, "overlay_translategemma_llama_q6_error")
                msg = tr(self.interface_lang, specific_key, error=str(e))
            else:
                msg = f"[ОШИБКА ПРОГРЕВА МОДЕЛИ]: {e}"
            print(msg, flush=True)
            self.ready_signal.emit(False, msg)


class LivePaddleOcrWorker(QThread):
    """Session-owned sequential Paddle worker for Block 1/3 Live OCR."""

    ready_signal = pyqtSignal(str, bool, str, int, str)
    _STOP_SENTINEL = object()

    def __init__(self, backend_name="paddle_fast", source_lang="en"):
        super().__init__()
        self.backend_name = str(backend_name or "paddle_fast")
        self.source_lang = str(source_lang or "en")
        self.worker_identity = id(self)
        self.backend_initialization_count = 0
        self.backend_identity = 0
        self.backend_context_identity = 0
        self.worker_thread_ident = 0
        self.worker_thread_native_id = 0
        self._request_queue = queue.Queue()
        self._ready_event = threading.Event()
        self._state_lock = threading.RLock()
        self._accepting_requests = True
        self._stop_event = threading.Event()
        self._ready_ok = False
        self._ready_error = ""
        self._request_seq = 0
        self._active_request_count = 0
        self._max_active_request_count = 0
        self._thread_diagnostic_engine = None

    def matches_backend(self, backend_name):
        return str(backend_name or "").strip().lower().replace("-", "_") == self.backend_name

    def wait_until_ready(self, timeout_sec=180.0):
        return bool(self._ready_event.wait(max(0.0, float(timeout_sec))))

    def submit_ocr(self, pil_image, *, lang="en", task_id=0, generation=0, timeout_sec=180.0):
        if not self.wait_until_ready(timeout_sec):
            raise TimeoutError("LIVE_PADDLE_WORKER_READY_TIMEOUT")
        with self._state_lock:
            if not self._ready_ok:
                raise RuntimeError(self._ready_error or "LIVE_PADDLE_BACKEND_NOT_READY")
            if not self._accepting_requests or self._stop_event.is_set():
                raise RuntimeError("LIVE_PADDLE_WORKER_STOPPING")
            self._request_seq += 1
            request_seq = int(self._request_seq)
        response_queue = queue.Queue(maxsize=1)
        self._request_queue.put(
            {
                "request_seq": request_seq,
                "task_id": int(task_id or 0),
                "generation": int(generation or 0),
                "enqueued_ns": time.perf_counter_ns(),
                "image": pil_image,
                "lang": str(lang or self.source_lang),
                "response_queue": response_queue,
            }
        )
        try:
            response = response_queue.get(timeout=max(0.1, float(timeout_sec)))
        except queue.Empty as exc:
            raise TimeoutError("LIVE_PADDLE_REQUEST_TIMEOUT") from exc
        error = str(response.get("error") or "")
        if error:
            raise RuntimeError(error)
        return str(response.get("text") or "")

    def stop_worker(self, reason="session_close"):
        with self._state_lock:
            if self._stop_event.is_set():
                return False
            self._accepting_requests = False
            self._stop_event.set()
        self._request_queue.put(self._STOP_SENTINEL)
        log_paddle_ocr_event(
            "LIVE_PADDLE_WORKER_STOP_REQUESTED",
            backend=self.backend_name,
            worker_identity=self.worker_identity,
            reason=str(reason or "session_close"),
        )
        return True

    def _backend_identity_snapshot(self):
        try:
            backend = ocr_core.get_backend(self.backend_name)
            context = getattr(backend, "_ocr", None)
            return id(backend), id(context) if context is not None else 0
        except Exception:
            return 0, 0

    def run(self):
        self.worker_thread_ident = int(threading.get_ident())
        self.worker_thread_native_id = int(threading.get_native_id())
        startup_block3_diagnostics.emit_startup_diagnostic(
            "PADDLE_PREPARE_STARTED",
            backend=self.backend_name,
            worker_thread_ident=self.worker_thread_ident,
            worker_thread_native_id=self.worker_thread_native_id,
        )
        log_paddle_ocr_event(
            "LIVE_PADDLE_WORKER_CREATED",
            backend=self.backend_name,
            worker_identity=self.worker_identity,
            worker_thread_ident=self.worker_thread_ident,
        )
        thread_growth_diagnostics.capture_thread_snapshot(
            "LIVE_PADDLE_WORKER_CREATED",
            engine=self._thread_diagnostic_engine,
            live_worker=self,
        )
        log_startup_event("PADDLE_WARMUP_BEGIN", backend=self.backend_name)
        thread_growth_diagnostics.capture_thread_snapshot(
            "LIVE_PADDLE_WARMUP_BEGIN",
            engine=self._thread_diagnostic_engine,
            live_worker=self,
        )
        try:
            result = ocr_core.warm_up_backend(self.backend_name, lang=self.source_lang)
            ok = bool(result.get("ok"))
            error = str(result.get("error") or "")
            elapsed_ms = int(result.get("elapsed_ms") or 0)
            text = str(result.get("text") or "")
            backend_identity, context_identity = self._backend_identity_snapshot()
            with self._state_lock:
                self._ready_ok = ok
                self._ready_error = error
                self.backend_initialization_count = 1 if ok else 0
                self.backend_identity = int(backend_identity or 0)
                self.backend_context_identity = int(context_identity or 0)
            if ok:
                log_startup_event("PADDLE_WARMUP_END", backend=self.backend_name, elapsed_ms=elapsed_ms)
                log_paddle_ocr_event(
                    "LIVE_PADDLE_BACKEND_READY",
                    backend=self.backend_name,
                    worker_identity=self.worker_identity,
                    worker_thread_ident=self.worker_thread_ident,
                    backend_identity=backend_identity,
                    backend_context_identity=context_identity,
                    backend_initialization_count=self.backend_initialization_count,
                    elapsed_ms=elapsed_ms,
                )
            else:
                log_startup_event(
                    "PADDLE_WARMUP_ERROR",
                    backend=self.backend_name,
                    elapsed_ms=elapsed_ms,
                    error=error,
                )
            thread_growth_diagnostics.capture_thread_snapshot(
                "LIVE_PADDLE_WARMUP_END",
                engine=self._thread_diagnostic_engine,
                live_worker=self,
                extra={"ok": ok, "elapsed_ms": elapsed_ms, "error": error[:500]},
            )
            self._ready_event.set()
            self.ready_signal.emit(self.backend_name, ok, error, elapsed_ms, text)
            if not ok:
                return

            while True:
                request = self._request_queue.get()
                if request is self._STOP_SENTINEL:
                    break
                response_queue = request["response_queue"]
                request_seq = int(request.get("request_seq") or 0)
                task_id = int(request.get("task_id") or 0)
                generation = int(request.get("generation") or 0)
                started = time.perf_counter()
                log_paddle_ocr_event(
                    "LIVE_PADDLE_REQUEST",
                    backend=self.backend_name,
                    worker_identity=self.worker_identity,
                    worker_thread_ident=self.worker_thread_ident,
                    request_seq=request_seq,
                    task_id=task_id,
                    generation=generation,
                )
                response = {"text": "", "error": ""}
                with self._state_lock:
                    self._active_request_count += 1
                    self._max_active_request_count = max(
                        self._max_active_request_count,
                        self._active_request_count,
                    )
                try:
                    with responsiveness.task_scope(task_id, self.backend_name, request["lang"], generation=generation,
                            queue_wait_ms=(time.perf_counter_ns() - request["enqueued_ns"]) / 1e6,
                            active=self._active_request_count, pending=self._request_queue.qsize(), mode="live_persistent"):
                        response["text"] = ocr_core.extract_text_from_pil_image(
                            request.get("image"),
                            lang=str(request.get("lang") or self.source_lang),
                            backend_name=self.backend_name,
                        )
                except Exception as exc:
                    response["error"] = f"{type(exc).__name__}: {exc}"
                finally:
                    with self._state_lock:
                        self._active_request_count = max(0, self._active_request_count - 1)
                elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
                backend_identity, context_identity = self._backend_identity_snapshot()
                log_paddle_ocr_event(
                    "LIVE_PADDLE_RESULT",
                    backend=self.backend_name,
                    worker_identity=self.worker_identity,
                    worker_thread_ident=self.worker_thread_ident,
                    backend_identity=backend_identity,
                    backend_context_identity=context_identity,
                    backend_initialization_count=self.backend_initialization_count,
                    request_seq=request_seq,
                    task_id=task_id,
                    generation=generation,
                    elapsed_ms=elapsed_ms,
                    text_len=len(str(response.get("text") or "")),
                    error=str(response.get("error") or "")[:500],
                )
                if request_seq in {1, 10, 25, 50, 100}:
                    thread_growth_diagnostics.capture_thread_snapshot(
                        f"LIVE_OCR_AFTER_{request_seq}",
                        engine=self._thread_diagnostic_engine,
                        live_worker=self,
                        extra={
                            "request_elapsed_ms": elapsed_ms,
                            "text_len": len(str(response.get("text") or "")),
                            "error": str(response.get("error") or "")[:500],
                        },
                    )
                response_queue.put(response)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            with self._state_lock:
                self._ready_ok = False
                self._ready_error = error
            self._ready_event.set()
            log_startup_event("PADDLE_WARMUP_ERROR", backend=self.backend_name, error=error)
            self.ready_signal.emit(self.backend_name, False, error, 0, "")
        finally:
            with self._state_lock:
                self._accepting_requests = False
            self._ready_event.set()
            while True:
                try:
                    pending = self._request_queue.get_nowait()
                except queue.Empty:
                    break
                if pending is self._STOP_SENTINEL:
                    continue
                try:
                    pending["response_queue"].put_nowait(
                        {"text": "", "error": "LIVE_PADDLE_WORKER_STOPPED"}
                    )
                except Exception:
                    pass
            log_paddle_ocr_event(
                "LIVE_PADDLE_WORKER_STOPPED",
                backend=self.backend_name,
                worker_identity=self.worker_identity,
                worker_thread_ident=self.worker_thread_ident,
                backend_initialization_count=self.backend_initialization_count,
            )


class PaddleOcrPrepareThread(QThread):
    """Background Paddle OCR preload without translation or overlay lifecycle changes."""

    ready_signal = pyqtSignal(str, bool, str, int, str)

    def __init__(self, backend_name="paddle_fast", source_lang="en"):
        super().__init__()
        self.backend_name = str(backend_name or "paddle_fast")
        self.source_lang = str(source_lang or "en")

    def run(self):
        log_startup_event("PADDLE_WARMUP_BEGIN", backend=self.backend_name)
        try:
            result = ocr_core.warm_up_backend(self.backend_name, lang=self.source_lang)
            ok = bool(result.get("ok"))
            error = str(result.get("error") or "")
            elapsed_ms = int(result.get("elapsed_ms") or 0)
            text = str(result.get("text") or "")
            if ok:
                log_startup_event("PADDLE_WARMUP_END", backend=self.backend_name, elapsed_ms=elapsed_ms)
            else:
                log_startup_event(
                    "PADDLE_WARMUP_ERROR",
                    backend=self.backend_name,
                    elapsed_ms=elapsed_ms,
                    error=error,
                )
            self.ready_signal.emit(self.backend_name, ok, error, elapsed_ms, text)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            log_startup_event("PADDLE_WARMUP_ERROR", backend=self.backend_name, error=error)
            self.ready_signal.emit(self.backend_name, False, error, 0, "")


class OverlayStoragePrepareThread(QThread):
    """Prepare translation-memory storage after the UI ready marker."""

    ready_signal = pyqtSignal(str, str)

    def __init__(self, session_config):
        super().__init__()
        self.session_config = json.loads(json.dumps(session_config or {}))

    def run(self):
        log_startup_event("TRANSLATION_STORAGE_PREPARE_BEGIN")
        try:
            memory_path = translation_memory.ensure_database(APP_DIR, self.session_config) or ""
            if memory_path:
                self.session_config["translation_memory_path"] = memory_path
            ensure_translation_pack_schema(APP_DIR, self.session_config)
            log_startup_event("TRANSLATION_STORAGE_PREPARE_END", memory_ready=bool(memory_path))
            self.ready_signal.emit(str(memory_path), "")
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            log_startup_event("TRANSLATION_STORAGE_PREPARE_ERROR", error=error)
            self.ready_signal.emit("", error)


class OverlayAsyncShutdownThread(QThread):
    """Runs blocking backend/thread shutdown only after all overlay windows are hidden."""

    stage_signal = pyqtSignal(str, object)
    completed_signal = pyqtSignal(object)

    def __init__(
        self,
        engine_thread,
        model_warmup_thread,
        paddle_prepare_thread,
        storage_prepare_thread,
        dynamic_worker,
    ):
        super().__init__()
        self.engine_thread = engine_thread
        self.model_warmup_thread = model_warmup_thread
        self.paddle_prepare_thread = paddle_prepare_thread
        self.storage_prepare_thread = storage_prepare_thread
        self.dynamic_worker = dynamic_worker

    @staticmethod
    def _stop_qthread(thread, wait_ms, terminate_wait_ms):
        if thread is None or not thread.isRunning():
            return {"was_running": False, "terminated": False}
        if thread.wait(wait_ms):
            return {"was_running": True, "terminated": False}
        thread.terminate()
        thread.wait(terminate_wait_ms)
        return {"was_running": True, "terminated": True}

    @staticmethod
    def _stop_dynamic_worker(thread):
        if thread is None:
            return {"was_running": False, "terminated": False}
        try:
            thread.stop_worker("overlay_close")
        except Exception:
            pass
        if not thread.isRunning():
            return {"was_running": False, "terminated": False}
        if thread.wait(30000):
            return {"was_running": True, "terminated": False}
        thread.terminate()
        thread.wait(2000)
        return {"was_running": True, "terminated": True}

    def run(self):
        started = time.perf_counter()
        result = {"errors": []}
        persistent_live_paddle = bool(
            self.paddle_prepare_thread is not None
            and hasattr(self.paddle_prepare_thread, "stop_worker")
        )
        if persistent_live_paddle:
            try:
                result["live_paddle_stop_requested"] = bool(
                    self.paddle_prepare_thread.stop_worker("overlay_close")
                )
            except Exception as exc:
                result["errors"].append(f"live_paddle_stop:{type(exc).__name__}:{exc}")
        try:
            engine_started = time.perf_counter()
            result["engine"] = self._stop_qthread(
                self.engine_thread,
                30000 if persistent_live_paddle else 2500,
                1000,
            )
            result["engine_ms"] = int((time.perf_counter() - engine_started) * 1000)
        except Exception as exc:
            result["errors"].append(f"engine:{type(exc).__name__}:{exc}")

        dynamic_started = time.perf_counter()
        self.stage_signal.emit("DYNAMIC_WORKER_STOP_REQUESTED", {})
        try:
            result["dynamic_worker"] = self._stop_dynamic_worker(self.dynamic_worker)
        except Exception as exc:
            result["errors"].append(f"dynamic_worker:{type(exc).__name__}:{exc}")
        result["dynamic_worker_ms"] = int((time.perf_counter() - dynamic_started) * 1000)
        self.stage_signal.emit(
            "DYNAMIC_WORKER_STOPPED",
            {
                "elapsed_ms": result["dynamic_worker_ms"],
                "result": result.get("dynamic_worker", {}),
            },
        )

        crop_debug_started = time.perf_counter()
        try:
            result["live_ocr_crop_debug"] = (
                self.engine_thread.shutdown_live_ocr_crop_debug_recorder(timeout_sec=3.0)
            )
        except Exception as exc:
            result["errors"].append(f"live_ocr_crop_debug:{type(exc).__name__}:{exc}")
        result["live_ocr_crop_debug_ms"] = int(
            (time.perf_counter() - crop_debug_started) * 1000
        )

        persistence_started = time.perf_counter()
        self.stage_signal.emit("TRANSLATION_PERSISTENCE_FLUSH_STARTED", {})
        try:
            result["translation_persistence"] = translation_pipeline_persistence.shutdown(timeout_sec=8.0)
        except Exception as exc:
            result["errors"].append(f"translation_persistence:{type(exc).__name__}:{exc}")
        result["translation_persistence_ms"] = int((time.perf_counter() - persistence_started) * 1000)
        self.stage_signal.emit(
            "TRANSLATION_PERSISTENCE_FLUSH_COMPLETED",
            {
                "elapsed_ms": result["translation_persistence_ms"],
                "result": result.get("translation_persistence", {}),
            },
        )

        backend_started = time.perf_counter()
        self.stage_signal.emit("TRANSLATION_BACKEND_SHUTDOWN_STARTED", {})
        try:
            translator_core.cancel_active_translation(reason="overlay_close")
            result["translation_backends"] = translator_core.shutdown_backends()
        except Exception as exc:
            result["errors"].append(f"translation_backend:{type(exc).__name__}:{exc}")
        result["translation_backend_ms"] = int((time.perf_counter() - backend_started) * 1000)
        self.stage_signal.emit(
            "TRANSLATION_BACKEND_SHUTDOWN_COMPLETED",
            {"elapsed_ms": result["translation_backend_ms"]},
        )

        for key, thread, wait_ms, terminate_wait_ms in (
            ("model_warmup", self.model_warmup_thread, 2500, 1500),
            (
                "paddle_prepare",
                self.paddle_prepare_thread,
                30000 if persistent_live_paddle else 1500,
                1000,
            ),
            ("storage_prepare", self.storage_prepare_thread, 1500, 1000),
        ):
            try:
                result[key] = self._stop_qthread(thread, wait_ms, terminate_wait_ms)
            except Exception as exc:
                result["errors"].append(f"{key}:{type(exc).__name__}:{exc}")

        result["total_ms"] = int((time.perf_counter() - started) * 1000)
        result["thread_states"] = {key: bool(thread is not None and thread.isRunning()) for key, thread in (
            ("engine", self.engine_thread), ("dynamic", self.dynamic_worker), ("model_warmup", self.model_warmup_thread),
            ("paddle_prepare", self.paddle_prepare_thread), ("storage_prepare", self.storage_prepare_thread))}
        self.stage_signal.emit("OVERLAY_SHUTDOWN_THREAD_STATES", result["thread_states"])
        self.completed_signal.emit(result)



class DynamicSelectionOverlay(QWidget):
    """
    Полноэкранный/оконный слой выделения временной области для динамического перевода.

    Это не постоянная зона субтитров. Пользователь вызывает слой хоткеем,
    мышью рисует прямоугольник, после отпускания кнопки приложение делает
    один скрин выбранной области и переводит её.
    """

    rect_selected = pyqtSignal(object)
    selection_cancelled = pyqtSignal()

    def __init__(self, geometry_rect, interface_lang="uk", parent=None, owner_hwnd=0):
        super().__init__(parent)
        self.interface_lang = normalize_interface_lang(interface_lang)
        self.owner_hwnd = int(owner_hwnd or 0)
        self.start_pos = None
        self.current_pos = None
        self.selection_rect = QRect()
        # После отпускания мыши мы на короткий момент делаем слой полностью
        # прозрачным, но не закрываем его до завершения захвата. Это нужно для
        # игровых tooltip-подсказок: игра ещё не получает фокус обратно, подсказка
        # не успевает исчезнуть, а ImageGrab не видит наш слой выделения.
        self.transparent_for_capture = False
        self.override_cursor_active = False
        # Первые миллисекунды после показа слоя Windows/игра могут ещё держать
        # foreground/raw-input за игровым окном. Поэтому мы коротко "вооружаем"
        # слой и не начинаем выделение, пока не сделаны повторные попытки захвата.
        self.selection_ready = False
        self._input_capture_timers = []

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setGeometry(geometry_rect)
        self._apply_native_tool_window()

        self.hint_label = QLabel(tr(self.interface_lang, "dynamic_select_hint"), self)
        self.hint_label.setStyleSheet(
            "color: white; background: rgba(0,0,0,180); font-size: 12px; "
            "font-weight: bold; padding: 6px; border-radius: 4px;"
        )
        self.hint_label.adjustSize()
        self.hint_label.move(12, 12)

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_native_tool_window()
        # На первом запуске после старта оверлея игра иногда остаётся foreground-окном
        # и продолжает получать raw mouse input. Поэтому слой выделения сначала
        # несколько раз жёстко забирает foreground/focus/capture, и только после
        # короткой паузы разрешает старт выделения.
        self.selection_ready = False
        self.force_input_capture()
        self._schedule_input_action(25, self.force_input_capture)
        self._schedule_input_action(75, self.force_input_capture)
        self._schedule_input_action(140, self.force_input_capture)
        self._schedule_input_action(190, self.mark_selection_ready)

    def _apply_native_tool_window(self):
        """Keep the interactive selector owned by the overlay and out of taskbar/Alt+Tab."""
        try:
            hwnd = int(self.winId())
            styles = int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE))
            styles = (styles | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, styles)
            if self.owner_hwnd and user32.IsWindow(self.owner_hwnd):
                user32.SetWindowLongPtrW(hwnd, GWLP_HWNDPARENT, self.owner_hwnd)
        except Exception as exc:
            print(f"[DYNAMIC SELECT WINDOW STYLE WARNING]: {exc}", flush=True)

    def native_window_inventory(self):
        hwnd = int(self.winId())
        pid = wintypes.DWORD()
        class_buffer = ctypes.create_unicode_buffer(256)
        title_buffer = ctypes.create_unicode_buffer(512)
        try:
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        except Exception:
            pid.value = 0
        try:
            user32.GetClassNameW(hwnd, class_buffer, len(class_buffer))
        except Exception:
            pass
        try:
            user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer))
        except Exception:
            pass
        styles = int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE))
        owner_hwnd = int(user32.GetWindow(hwnd, GW_OWNER) or 0)
        parent_hwnd = int(user32.GetParent(hwnd) or 0)
        return {
            "hwnd": hwnd,
            "pid": int(pid.value),
            "process_executable": os.path.abspath(sys.executable),
            "class_name": class_buffer.value,
            "window_title": title_buffer.value,
            "extended_styles": styles,
            "owner_hwnd": owner_hwnd,
            "parent_hwnd": parent_hwnd,
            "ws_ex_appwindow": bool(styles & WS_EX_APPWINDOW),
            "ws_ex_toolwindow": bool(styles & WS_EX_TOOLWINDOW),
            "ws_ex_noactivate": bool(styles & WS_EX_NOACTIVATE),
            "qt_window_flags": int(self.windowFlags().value),
        }

    def _schedule_input_action(self, delay_ms, callback):
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(callback)
        timer.timeout.connect(lambda timer=timer: self._discard_input_timer(timer))
        self._input_capture_timers.append(timer)
        timer.start(max(1, int(delay_ms)))

    def _discard_input_timer(self, timer):
        try:
            self._input_capture_timers.remove(timer)
        except ValueError:
            pass

    def stop_input_capture_timers(self):
        for timer in list(self._input_capture_timers):
            timer.stop()
        self._input_capture_timers.clear()

    def mark_selection_ready(self):
        self.selection_ready = True
        self.force_input_capture()

    def force_input_capture(self):
        """Принудительно делаем слой выделения активным получателем мыши."""
        attached_threads = []
        try:
            self.raise_()
            self.show()
            self.activateWindow()
            self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        except Exception:
            pass

        try:
            hwnd = int(self.winId())

            # AttachThreadInput повышает шанс SetForegroundWindow сработать,
            # когда foreground пока принадлежит игре. Это важно именно для первого
            # запуска динамического выделения и после переключения режимов.
            current_thread = kernel32.GetCurrentThreadId()
            fg_hwnd = user32.GetForegroundWindow()
            thread_ids = set()
            if fg_hwnd:
                thread_ids.add(user32.GetWindowThreadProcessId(fg_hwnd, None))
            thread_ids.add(user32.GetWindowThreadProcessId(hwnd, None))

            for tid in thread_ids:
                if tid and tid != current_thread:
                    try:
                        if user32.AttachThreadInput(current_thread, tid, True):
                            attached_threads.append(tid)
                    except Exception:
                        pass

            try:
                user32.ShowWindow(hwnd, SW_SHOW)
                user32.SetWindowPos(
                    hwnd, HWND_TOPMOST,
                    int(self.x()), int(self.y()), int(self.width()), int(self.height()),
                    SWP_SHOWWINDOW | SWP_NOOWNERZORDER,
                )
                user32.SetForegroundWindow(hwnd)
                user32.SetActiveWindow(hwnd)
                user32.SetFocus(hwnd)
                user32.SetCapture(hwnd)
            finally:
                for tid in attached_threads:
                    try:
                        user32.AttachThreadInput(current_thread, tid, False)
                    except Exception:
                        pass
        except Exception as e:
            print(f"[DYNAMIC SELECT CAPTURE WARNING]: {e}", flush=True)

        try:
            self.grabMouse()
        except Exception:
            pass

        # Не используем QApplication.setOverrideCursor(): это глобальный курсор всего
        # приложения, и если Windows/игра перехватит фокус в неудачный момент, крест
        # может остаться в обычном окне настройки оверлея. Вид курсора задаётся
        # локально через self.setCursor(Qt.CrossCursor).
        self.override_cursor_active = False

    def release_input_capture(self, reason=""):
        """Release only capture owned by this selection window."""
        self.stop_input_capture_timers()
        qt_released = False
        win32_released = False
        capture_before = 0
        try:
            if QWidget.mouseGrabber() is self:
                self.releaseMouse()
                qt_released = True
        except Exception:
            pass
        try:
            hwnd = int(self.winId())
            capture_before = int(user32.GetCapture() or 0)
            if capture_before == hwnd:
                win32_released = bool(user32.ReleaseCapture())
        except Exception:
            pass
        return {
            "reason": str(reason or ""),
            "capture_before": capture_before,
            "qt_released": qt_released,
            "win32_released": win32_released,
        }

    def closeEvent(self, event):
        self.release_input_capture("close_event")
        if self.override_cursor_active:
            try:
                QApplication.restoreOverrideCursor()
            except Exception:
                pass
            self.override_cursor_active = False
        clear_global_override_cursor()
        super().closeEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.selection_cancelled.emit()
            self.close()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if not self.selection_ready:
            # Не даём первому слишком раннему клику уйти в игру, но и не начинаем
            # выделение, пока окно ещё не забрало ввод достаточно надёжно.
            self.force_input_capture()
            event.accept()
            return

        if event.button() == Qt.MouseButton.RightButton:
            self.selection_cancelled.emit()
            self.close()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_pos = event.position().toPoint()
            self.current_pos = self.start_pos
            self.selection_rect = QRect(self.start_pos, self.current_pos).normalized()
            self.update()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.start_pos is not None:
            self.current_pos = event.position().toPoint()
            self.selection_rect = QRect(self.start_pos, self.current_pos).normalized()
            self.update()
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.start_pos is not None:
            local_rect = QRect(self.start_pos, event.position().toPoint()).normalized()
            self.start_pos = None
            self.current_pos = None

            if local_rect.width() < 8 or local_rect.height() < 8:
                self.selection_cancelled.emit()
                self.close()
                return

            global_top_left = self.mapToGlobal(local_rect.topLeft())
            global_rect = QRect(global_top_left, local_rect.size())

            # Не закрываем окно сразу. Сначала делаем его полностью прозрачным
            # для отрисовки, чтобы родитель успел снять выбранную область, пока
            # игра ещё не получила фокус и всплывающая подсказка не исчезла.
            self.transparent_for_capture = True
            self.selection_rect = QRect()
            self.hint_label.hide()
            self.update()
            QApplication.processEvents()

            self.release_input_capture("selection_completed")
            self.rect_selected.emit(global_rect)
            event.accept()

    def paintEvent(self, event):
        if self.transparent_for_capture:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Лёгкое затемнение области игры, чтобы пользователь понимал, что он в режиме выделения.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 80))
        painter.drawRect(self.rect())

        if not self.selection_rect.isNull():
            painter.setPen(QPen(QColor(0, 220, 255, 230), 2, Qt.PenStyle.SolidLine))
            painter.setBrush(QColor(0, 220, 255, 35))
            painter.drawRect(self.selection_rect)


class DynamicTranslationThread(QThread):
    """Persistent sequential OCR+translation worker for Dynamic requests."""

    finished_signal = pyqtSignal(int, str, str, object, str)
    request_finished = pyqtSignal(int, str)
    diagnostic_signal = pyqtSignal(str, str)
    _STOP_SENTINEL = object()

    def __init__(self):
        super().__init__()
        self.pil_image = None
        self.session_config = {}
        self.source_rect = QRect()
        self.interface_lang = "uk"
        self.request_id = 0
        self._request_queue = queue.Queue()
        self._state_lock = threading.RLock()
        self._accepting_requests = True
        self._stop_event = threading.Event()
        self._queued_request_ids = set()
        self._request_backends = {}
        self._cancelled_requests = {}
        self._cancel_event = threading.Event()
        self._cancel_reason = ""
        self._cancel_diagnostic_written = False
        self._worker_stop_logged = False

    def _emit_lifecycle_event(self, event, *, request_id=0, ocr_backend="", outcome="", busy=None):
        request_id = int(request_id or 0)
        if busy is None:
            busy = self.is_busy()
        fields = {
            "request_id": request_id,
            "ocr_backend": str(ocr_backend or ""),
            "outcome": str(outcome or ""),
            "busy": bool(busy),
        }
        log_paddle_ocr_event(str(event), **fields)
        responsiveness.event(str(event), task_id=request_id, backend=ocr_backend, outcome=outcome,
                             active=int(bool(self.request_id)), pending=len(self._queued_request_ids))
        details = "; ".join(f"{key}={value}" for key, value in fields.items())
        self.diagnostic_signal.emit(str(event), details)

    def submit_request(self, pil_image, session_config, source_rect, interface_lang="uk", request_id=0):
        request_id = int(request_id or 0)
        if request_id <= 0:
            return False
        snapshot = {
            "request_id": request_id,
            "enqueued_ns": time.perf_counter_ns(),
            "image": pil_image,
            "session_config": json.loads(json.dumps(session_config)),
            "source_rect": QRect(source_rect),
            "interface_lang": normalize_interface_lang(interface_lang),
        }
        ocr_backend = current_ocr_backend_from_session(snapshot["session_config"])
        with self._state_lock:
            if not self._accepting_requests or self._stop_event.is_set():
                return False
            if request_id == self.request_id or request_id in self._queued_request_ids:
                return False
            self._queued_request_ids.add(request_id)
            self._request_backends[request_id] = ocr_backend
            busy = bool(self.request_id or self._queued_request_ids)
        self._request_queue.put(snapshot)
        responsiveness.event("request_enqueued", task_id=request_id, backend=ocr_backend,
                             lang=(snapshot["session_config"].get("translation") or {}).get("source_lang", ""),
                             active=int(bool(self.request_id)), pending=len(self._queued_request_ids))
        self._emit_lifecycle_event(
            "DYNAMIC_REQUEST_QUEUED",
            request_id=request_id,
            ocr_backend=ocr_backend,
            busy=busy,
        )
        return True

    def active_request_id(self):
        with self._state_lock:
            return int(self.request_id or 0)

    def has_request(self, request_id):
        request_id = int(request_id or 0)
        with self._state_lock:
            return bool(request_id and (request_id == self.request_id or request_id in self._queued_request_ids))

    def is_busy(self):
        with self._state_lock:
            return bool(self.request_id or self._queued_request_ids)

    def cancel_request(self, reason="cancelled", *, request_id=0):
        reason = str(reason or "cancelled")
        with self._state_lock:
            request_id = int(request_id or self.request_id or 0)
            if request_id <= 0 or (
                request_id != self.request_id and request_id not in self._queued_request_ids
            ):
                return False
            already_cancelled = request_id in self._cancelled_requests
            self._cancelled_requests[request_id] = reason
            ocr_backend = self._request_backends.get(request_id, "")
            if request_id == self.request_id:
                self._cancel_reason = reason
                self._cancel_event.set()
            busy = bool(self.request_id or self._queued_request_ids)
        if not already_cancelled:
            self._emit_lifecycle_event(
                "DYNAMIC_REQUEST_CANCELLED",
                request_id=request_id,
                ocr_backend=ocr_backend,
                outcome="cancelled",
                busy=busy,
            )
        return True

    def stop_worker(self, reason="worker_stop"):
        reason = str(reason or "worker_stop")
        cancelled = []
        with self._state_lock:
            self._accepting_requests = False
            self._stop_event.set()
            request_ids = list(self._queued_request_ids)
            if self.request_id:
                request_ids.insert(0, int(self.request_id))
            for request_id in request_ids:
                if request_id not in self._cancelled_requests:
                    self._cancelled_requests[request_id] = reason
                    cancelled.append((request_id, self._request_backends.get(request_id, "")))
            if self.request_id:
                self._cancel_reason = reason
                self._cancel_event.set()
            should_log = not self._worker_stop_logged
            self._worker_stop_logged = True
            active_request_id = int(self.request_id or 0)
            active_backend = self._request_backends.get(active_request_id, "")
            busy = bool(self.request_id or self._queued_request_ids)
        for request_id, ocr_backend in cancelled:
            self._emit_lifecycle_event(
                "DYNAMIC_REQUEST_CANCELLED",
                request_id=request_id,
                ocr_backend=ocr_backend,
                outcome="cancelled",
                busy=busy,
            )
        if should_log:
            self._emit_lifecycle_event(
                "DYNAMIC_WORKER_STOP_REQUESTED",
                request_id=active_request_id,
                ocr_backend=active_backend,
                busy=busy,
            )
            self._request_queue.put(self._STOP_SENTINEL)
        return True

    def is_request_cancelled(self):
        with self._state_lock:
            return bool(
                self._cancel_event.is_set()
                or self._stop_event.is_set()
                or int(self.request_id or 0) in self._cancelled_requests
            )

    def _log_cancelled_once(self, stage):
        if self._cancel_diagnostic_written:
            return
        self._cancel_diagnostic_written = True
        details = (
            f"request_id={self.request_id}; stage={stage}; "
            f"reason={self._cancel_reason or 'cancelled'}"
        )
        log_paddle_ocr_event(
            "DYNAMIC_REQUEST_RESULT_REJECTED",
            request_id=self.request_id,
            stage=stage,
            reason=self._cancel_reason or "cancelled",
        )
        self.diagnostic_signal.emit("DYNAMIC_REQUEST_RESULT_REJECTED", details)

    def _emit_result(self, source, translated, reason):
        if self.is_request_cancelled():
            metrics = self.take_online_publication_timing(self.request_id)
            if metrics:
                publication_event(metrics, "dropped", "dynamic", self.request_id, reason="cancelled")
            self._log_cancelled_once("publish")
            return False
        responsiveness.event("result_signal_emit", task_id=self.request_id)
        self.finished_signal.emit(
            self.request_id,
            str(source or ""),
            str(translated or ""),
            self.source_rect,
            str(reason or ""),
        )
        return True

    def take_online_publication_timing(self, request_id):
        with self._state_lock:
            return getattr(self, "_online_publication_timings", {}).pop(int(request_id), {})

    def run(self):
        self._emit_lifecycle_event("DYNAMIC_WORKER_STARTED", busy=False)
        try:
            while True:
                request = self._request_queue.get()
                if request is self._STOP_SENTINEL:
                    break
                request_id = int(request.get("request_id") or 0)
                with self._state_lock:
                    self._queued_request_ids.discard(request_id)
                    self.request_id = request_id
                    self.pil_image = request.get("image")
                    self.session_config = request.get("session_config") or {}
                    self.source_rect = QRect(request.get("source_rect") or QRect())
                    self.interface_lang = normalize_interface_lang(request.get("interface_lang", "uk"))
                    self._cancel_event = threading.Event()
                    self._cancel_reason = self._cancelled_requests.get(request_id, "")
                    self._cancel_diagnostic_written = False
                    ocr_backend = self._request_backends.get(
                        request_id,
                        current_ocr_backend_from_session(self.session_config),
                    )
                    cancelled_before_start = bool(
                        self._stop_event.is_set() or request_id in self._cancelled_requests
                    )
                    if cancelled_before_start:
                        self._cancel_event.set()
                if cancelled_before_start:
                    self._log_cancelled_once("before_start")
                    outcome = "cancelled"
                else:
                    if (self.session_config.get("translation") or {}).get("mode") == "online":
                        session_logging.event("ONLINE_DYNAMIC_QUEUE", online_runtime="dynamic", task_id=request_id,
                            online_dynamic_queue_ms=(time.perf_counter_ns()-request["enqueued_ns"])/1e6)
                    self._emit_lifecycle_event(
                        "DYNAMIC_REQUEST_STARTED",
                        request_id=request_id,
                        ocr_backend=ocr_backend,
                        busy=True,
                    )
                    with responsiveness.task_scope(request_id, ocr_backend,
                            (self.session_config.get("translation") or {}).get("source_lang", ""),
                            queue_wait_ms=(time.perf_counter_ns() - request["enqueued_ns"]) / 1e6,
                            active=1, pending=len(self._queued_request_ids), mode="dynamic"):
                        outcome = self._process_current_request()
                        responsiveness.event("task_outcome", outcome=outcome)
                with self._state_lock:
                    if request_id in self._cancelled_requests or self._cancel_event.is_set():
                        outcome = "cancelled"
                    self._cancelled_requests.pop(request_id, None)
                    self._request_backends.pop(request_id, None)
                    self.request_id = 0
                    self.pil_image = None
                    self.session_config = {}
                    self.source_rect = QRect()
                    self._cancel_event = threading.Event()
                    self._cancel_reason = ""
                    busy = bool(self._queued_request_ids)
                self.request_finished.emit(request_id, str(outcome or "error"))
                self._emit_lifecycle_event(
                    "DYNAMIC_REQUEST_FINISHED",
                    request_id=request_id,
                    ocr_backend=ocr_backend,
                    outcome=str(outcome or "error"),
                    busy=busy,
                )
        finally:
            self._emit_lifecycle_event("DYNAMIC_WORKER_STOPPED", busy=False)

    def _process_current_request(self):
        raw_text = ""
        try:
            if self.is_request_cancelled():
                self._log_cancelled_once("before_ocr")
                return "cancelled"
            started_at = time.time()
            trans = self.session_config.get("translation", {})
            src_lang = trans.get("source_lang", "en")
            trg_lang = trans.get("target_lang", "uk")
            use_online = trans.get("mode", "offline") == "online"

            ocr_backend = current_ocr_backend_from_session(self.session_config)
            image_size = getattr(self.pil_image, "size", "")
            try:
                rect_size = f"{self.source_rect.width()}x{self.source_rect.height()}"
            except Exception:
                rect_size = ""
            ocr_started_at = time.time()
            if is_paddle_family_backend(ocr_backend):
                details = f"backend={ocr_backend}; image_size={image_size}; rect_size={rect_size}; {current_thread_diagnostic()}"
                log_paddle_ocr_event("DYNAMIC_OCR_STARTED", backend=ocr_backend, image_size=image_size, rect_size=rect_size, thread=current_thread_diagnostic())
                self.diagnostic_signal.emit("DYNAMIC_OCR_STARTED", details)
            raw_text = ocr_core.extract_text_from_pil_image(self.pil_image, lang=src_lang, backend_name=ocr_backend)
            raw_text = re.sub(r"\s+", " ", raw_text or "").strip()
            ocr_ms = int((time.time() - ocr_started_at) * 1000)
            if self.is_request_cancelled():
                self._log_cancelled_once("after_ocr")
                return "cancelled"
            if raw_text:
                log_paddle_ocr_event(
                    "DYNAMIC_OCR_RESULT_OUTCOME",
                    request_id=self.request_id,
                    final_outcome="accepted",
                    exact_reason_code="dynamic_nonempty_text",
                    source_len=len(raw_text),
                )
            if is_paddle_family_backend(ocr_backend):
                details = f"backend={ocr_backend}; image_size={image_size}; rect_size={rect_size}; ocr_ms={ocr_ms}; source_len={len(raw_text)}; {current_thread_diagnostic()}"
                log_paddle_ocr_event("DYNAMIC_OCR_FINISHED", backend=ocr_backend, image_size=image_size, rect_size=rect_size, ocr_ms=ocr_ms, source_len=len(raw_text), thread=current_thread_diagnostic())
                self.diagnostic_signal.emit("DYNAMIC_OCR_FINISHED", details)
                if ocr_ms > 700:
                    warning_details = f"stage=ocr; backend={ocr_backend}; ocr_ms={ocr_ms}; image_size={image_size}; rect_size={rect_size}; {current_thread_diagnostic()}"
                    log_paddle_ocr_event("DYNAMIC_PADDLE_BUSY_UI_WARNING", stage="ocr", backend=ocr_backend, ocr_ms=ocr_ms, image_size=image_size, rect_size=rect_size, thread=current_thread_diagnostic())
                    self.diagnostic_signal.emit("DYNAMIC_PADDLE_BUSY_UI_WARNING", warning_details)

            if not raw_text:
                if is_paddle_family_backend(ocr_backend):
                    total_ms = int((time.time() - started_at) * 1000)
                    details = f"backend={ocr_backend}; reason=empty_ocr; ocr_ms={ocr_ms}; total_ms={total_ms}; source_len=0; translation_len=0; {current_thread_diagnostic()}"
                    log_paddle_ocr_event("DYNAMIC_TRANSLATION_FINISHED", backend=ocr_backend, reason="empty_ocr", ocr_ms=ocr_ms, total_ms=total_ms, source_len=0, translation_len=0, thread=current_thread_diagnostic())
                    self.diagnostic_signal.emit("DYNAMIC_TRANSLATION_FINISHED", details)
                self._emit_result("", "", "empty_ocr")
                return "empty"

            translation_mode_name, base_translator_backend_key, translator_backend_name = base_translation_cache_mode_for_session(
                self.session_config,
                use_online,
                source_text=raw_text,
                runtime_mode="dynamic",
            )
            base_translation_cache_mode_name = translation_mode_name
            translation_cache_mode_name = base_translation_cache_mode_name
            translation_from_memory = False
            translated = ""
            translation_started_at = time.time()
            if is_paddle_family_backend(ocr_backend):
                details = f"backend={ocr_backend}; image_size={image_size}; rect_size={rect_size}; source_len={len(raw_text)}; {current_thread_diagnostic()}"
                log_paddle_ocr_event("DYNAMIC_TRANSLATION_STARTED", backend=ocr_backend, image_size=image_size, rect_size=rect_size, source_len=len(raw_text), thread=current_thread_diagnostic())
                self.diagnostic_signal.emit("DYNAMIC_TRANSLATION_STARTED", details)
            memory_lookup_ms = 0
            translator_ms = 0
            llm_ms = 0
            llm_applied = False
            llm_mode = "off"
            llm_error = ""
            rough_translated = ""

            try:
                memory_lookup_started_at = time.time()
                cached_translation = ""


                if not cached_translation:
                    cached_translation = translation_memory.lookup_translation(
                        APP_DIR,
                        self.session_config,
                        original_text=raw_text,
                        source_lang=src_lang,
                        target_lang=trg_lang,
                        translation_mode=base_translation_cache_mode_name,
                    )
                    if cached_translation:
                        translation_cache_mode_name = base_translation_cache_mode_name

                memory_lookup_ms = int((time.time() - memory_lookup_started_at) * 1000)
            except Exception:
                cached_translation = ""
                memory_lookup_ms = 0

            if use_online:
                translator_core.online_memory_event(trans.get("online"), bool(cached_translation))
            if cached_translation:
                translated = cached_translation.strip()
                rough_translated = translated
                translation_from_memory = True

            else:
                translator_started_at = time.time()
                translated = translator_core.translate_text(
                    raw_text,
                    src_lang=src_lang,
                    target_lang=trg_lang,
                    use_online=use_online,
                    backend_name=translator_backend_name,
                    request_context={**translation_request_context_for_session(
                        self.session_config, source_text=raw_text, runtime_mode="dynamic"),
                        "cancelled": self.is_request_cancelled},
                )
                if self.is_request_cancelled():
                    self._log_cancelled_once("after_translation")
                    return "cancelled"
                translated = str(translated or "").strip()
                translator_ms = int((time.time() - translator_started_at) * 1000)
                rough_translated = translated
                base_translation_cache_mode_name, base_translator_backend_key, translator_backend_name = refresh_base_translation_cache_mode_from_last(
                    base_translation_cache_mode_name,
                    base_translator_backend_key,
                    translator_backend_name,
                    use_online,
                )

                if translated:
                    translation_cache_mode_name = base_translation_cache_mode_name
                    try:
                        translation_memory.store_translation(
                            APP_DIR,
                            self.session_config,
                            original_text=raw_text,
                            translated_text=translated,
                            source_lang=src_lang,
                            target_lang=trg_lang,
                            translation_mode=base_translation_cache_mode_name,
                            mode="dynamic_area",
                            ocr_engine=ocr_backend,
                        )
                        mark_translation_memory_metadata(
                            APP_DIR,
                            self.session_config,
                            original_text=raw_text,
                            source_lang=src_lang,
                            target_lang=trg_lang,
                            translation_mode=base_translation_cache_mode_name,
                            quality_mode="base",
                            pipeline_mode="dynamic_area",
                            translator_backend=base_translator_backend_key,
                        )
                    except Exception:
                        pass

            if self.is_request_cancelled():
                self._log_cancelled_once("before_result_persistence")
                return "cancelled"
            if use_online and translated:
                metrics = {} if translation_from_memory else translator_core.get_last_backend_metrics("online")
                metrics.setdefault("online_completed_perf", time.perf_counter())
                with self._state_lock:
                    if not hasattr(self, "_online_publication_timings"):
                        self._online_publication_timings = {}
                    self._online_publication_timings[self.request_id] = metrics
                    while len(self._online_publication_timings) > 32:
                        self._online_publication_timings.pop(next(iter(self._online_publication_timings)))
                publication_event(metrics, "ready", "dynamic", self.request_id)
            if translated:
                try:
                    if persistence_allowed(self.session_config, translation_cache_mode_name):
                        ocr_core.log_translation(raw_text, translated)
                except Exception:
                    pass
                try:
                    translation_history.append_translation(
                        APP_DIR,
                        self.session_config,
                        {
                            "mode": "dynamic_area",
                            "ocr_engine": ocr_backend,
                            "source_lang": src_lang,
                            "target_lang": trg_lang,
                            "translation_mode": translation_cache_mode_name,
                            "from_cache": translation_from_memory,
                            "original_text": raw_text,
                            "translated_text": translated,
                            "ocr_ms": ocr_ms,
                            "translation_ms": int((time.time() - translation_started_at) * 1000),
                            "memory_lookup_ms": memory_lookup_ms,
                            "translator_ms": translator_ms,
                            "llm_ms": llm_ms,
                            "llm_applied": llm_applied,
                            "llm_mode": llm_mode,
                            "llm_error": llm_error,
                            "rough_translated_text": rough_translated,
                            "total_ms": int((time.time() - started_at) * 1000),
                        },
                    )
                except Exception:
                    pass
                try:
                    ocr_benchmark_crops.save_crop(
                        APP_DIR,
                        self.session_config,
                        self.pil_image,
                        mode="dynamic_area",
                        ocr_engine=ocr_backend,
                        source_lang=src_lang,
                        target_lang=trg_lang,
                        original_text=raw_text,
                        translated_text=translated,
                        ocr_ms=ocr_ms,
                        total_ms=int((time.time() - started_at) * 1000),
                    )
                except Exception:
                    pass
            translation_ms = int((time.time() - translation_started_at) * 1000)
            total_ms = int((time.time() - started_at) * 1000)
            if is_paddle_family_backend(ocr_backend):
                details = (
                    f"backend={ocr_backend}; image_size={image_size}; rect_size={rect_size}; "
                    f"ocr_ms={ocr_ms}; translation_ms={translation_ms}; total_ms={total_ms}; "
                    f"source_len={len(raw_text)}; translation_len={len(translated or '')}; "
                    f"translator_ms={translator_ms}; llm_ms={llm_ms}; from_cache={translation_from_memory}; {current_thread_diagnostic()}"
                )
                log_paddle_ocr_event(
                    "DYNAMIC_TRANSLATION_FINISHED",
                    backend=ocr_backend,
                    image_size=image_size,
                    rect_size=rect_size,
                    ocr_ms=ocr_ms,
                    translation_ms=translation_ms,
                    total_ms=total_ms,
                    source_len=len(raw_text),
                    translation_len=len(translated or ""),
                    translator_ms=translator_ms,
                    llm_ms=llm_ms,
                    from_cache=translation_from_memory,
                    thread=current_thread_diagnostic(),
                )
                self.diagnostic_signal.emit("DYNAMIC_TRANSLATION_FINISHED", details)
                if translation_ms > 700:
                    warning_details = f"stage=translation; backend={ocr_backend}; translation_ms={translation_ms}; total_ms={total_ms}; {current_thread_diagnostic()}"
                    log_paddle_ocr_event("DYNAMIC_PADDLE_BUSY_UI_WARNING", stage="translation", backend=ocr_backend, translation_ms=translation_ms, total_ms=total_ms, thread=current_thread_diagnostic())
                    self.diagnostic_signal.emit("DYNAMIC_PADDLE_BUSY_UI_WARNING", warning_details)
            published = self._emit_result(
                raw_text,
                translated,
                "ok" if str(translated or "").strip() else "translation_empty",
            )
            if not published:
                return "cancelled"
            return "success" if str(translated or "").strip() else "empty"
        except Exception as e:
            print(f"[DYNAMIC TRANSLATION ERROR]: {e}", flush=True)
            if self.is_request_cancelled():
                self._log_cancelled_once("exception_after_cancel")
                return "cancelled"
            self._emit_result(raw_text, "", f"error:{type(e).__name__}")
            return "error"


class DynamicResultPopup(QWidget):
    """Временное окно результата динамического перевода."""

    def __init__(self, text, geometry_rect, visuals, display_seconds=6, parent=None, font_size_override=None):
        super().__init__(parent)
        self.visuals = {key: value for key, value in visuals.items() if key != "adaptive_plate_enabled"}
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        # Не активируем popup как отдельное рабочее окно.
        # Иначе клик/курсор по результату перевода может забрать фокус у игры.
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setGeometry(geometry_rect)

        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)
        self.label = SubtitleLabel(text)
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setFont(QFont(
            visuals.get("font_name", "Arial"),
            int(font_size_override if font_size_override is not None else visuals.get("font_size", 19)),
            QFont.Weight.Bold,
        ))
        self.label.setStyleSheet(f"color: {visuals.get('font_color', '#FFFFFF')}; line-height: 1.25;")
        self.label.set_visuals(self.visuals)
        layout.addWidget(self.label)
        self.setLayout(layout)

        self.display_seconds = max(1, int(display_seconds))
        self.close_timer = QTimer(self)
        self.close_timer.setSingleShot(True)
        self.close_timer.timeout.connect(self.close)
        self.close_timer.start(self.display_seconds * 1000)

    def showEvent(self, event):
        """Делаем окно результата прозрачным для мыши и неактивирующим на уровне WinAPI."""
        super().showEvent(event)
        try:
            hwnd = int(self.winId())
            styles = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(
                hwnd,
                GWL_EXSTYLE,
                styles | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
            )
            user32.SetWindowPos(
                hwnd,
                HWND_TOPMOST,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER,
            )
        except Exception as e:
            print(f"[DYNAMIC POPUP STYLE ERROR]: {e}", flush=True)

    def closeEvent(self, event):
        self.close_timer.stop()
        super().closeEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_plate(painter, self.rect(), self.visuals, default_opacity=185)


class GameTranslatorOverlay(QWidget):
    """
    Графический полупрозрачный интерфейс оверлея субтитров.

    Режимы работы:
    1. Режим настройки — оверлей кликабельный, видны настройки, можно двигать и менять размер.
    2. Режим перевода — оверлей click-through, игра активна, оверлей только показывает перевод.

    Переключение режимов — только Ctrl+Alt+Shift+P.
    Временное скрытие/показ переводной плашки — Ctrl+Alt+Shift+H.

    Кнопку «Фиксация» намеренно убрали, потому что по текущей логике она не нужна.
    """

    def __init__(self, session_file):
        log_startup_event("OVERLAY_INIT_START")
        log_startup_event("OVERLAY_WIDGET_CREATE_BEGIN")
        super().__init__()
        log_startup_event("OVERLAY_WIDGET_CREATE_END")
        self.session_file = session_file
        self.session_config = {}
        self.live_mouse_mode = None
        self.target_hwnd = 0
        self.drag_position = None
        self.model_warmup_thread = None
        self.paddle_ocr_prepare_thread = None
        self.storage_prepare_thread = None
        self._post_ready_initialization_started = False
        self._startup_paddle_waiting_for_model = False
        self._startup_diagnostic_mode = ""
        self._startup_diagnostic_sequence = None
        self._block3_translator_ready_keys = set()
        self._block3_translator_errors = {}
        self._block3_pending_warmup_key = ""
        self._block3_readiness_log_signature = None
        self._block3_live_enabled_signature = None
        self._block3_ready_hint_signature = None
        self._translation_model_ready_for_hint = False
        self._translation_model_error_for_hint = ""
        self._block2_pending_model_warmup = False
        self._selected_ocr_ready_for_hint = False
        self.paddle_ocr_prepare_state = "not_needed"
        self.paddle_ocr_prepare_backend = ""
        self.paddle_ocr_prepare_error = ""
        self.paddle_ocr_prepare_elapsed_ms = 0

        # Состояние временного скрытия перевода.
        # Нужна для ситуаций, когда переводная плашка перекрывает оригинальный текст,
        # например варианты выбора в игре. Горячая клавиша Ctrl+Alt+Shift+H
        # скрывает только визуальный вывод перевода, но не останавливает OCR/переводчик.
        self.translation_output_hidden = False
        self.last_displayed_source = ""
        self.last_displayed_translation = ""
        self.last_displayed_updated_at = 0.0

        # Состояние динамического перевода выделенной области.
        self.dynamic_selection_overlay = None
        self.dynamic_worker = None
        self._dynamic_request_snapshots = {}
        self.dynamic_result_popup = None
        self._dynamic_request_seq = 0
        self._active_dynamic_request_id = 0
        self._cancelled_dynamic_request_reasons = {}
        self._dynamic_cancel_pending_resume_request_id = 0
        self._dynamic_restore_request_id = 0
        self._dynamic_capture_request_id = 0
        self._dynamic_capture_rect = QRect()
        self._dynamic_live_pause_owned = False
        self._dynamic_shutdown_rejection_logged = False
        self._live_mouse_transition_seq = 0
        self._live_edit_saved_game_clip = None
        self._live_edit_saved_target_hwnd = 0
        self._window_interaction_contexts = {}
        self._window_interaction_seq = 0
        self._cursor_restore_timers = []
        self._last_known_good_game_clip = None
        self._last_known_good_target_hwnd = 0
        self.dynamic_restore_hidden_state = False
        self.dynamic_started_at = 0.0
        # True, если основной оверлей был спрятан именно динамическим режимом.
        # Нужен, чтобы после динамического перевода вернуть оверлей автоматически,
        # но не включать его, если пользователь сам заранее спрятал плашку хоткеем.
        self.dynamic_hidden_by_dynamic = False
        # В PaddleOCR-режиме постоянный OCR надо временно останавливать, пока
        # пользователь делает динамический перевод или вручную скрыл плашку.
        # Иначе тяжёлый PaddleOCR продолжает обрабатывать старую рамку субтитров
        # одновременно с динамическим OCR и забивает CPU.
        self._paddle_live_pause_reason = ""
        self.ui_events_log_path = log_file("ui_events.log")

        # Пауза OCR во время ручного перемещения/изменения размера рамки.
        # Нужна, чтобы настройка оверлея не вызывала микрофризы и перевод
        # промежуточных кропов, пока пользователь ещё двигает окно.
        self.geometry_adjustment_pause_ms = 350
        self._geometry_adjustment_active = False
        self._session_stopping = False
        self._shutdown_started_at = 0.0
        self._shutdown_thread = None

        self.load_session_configuration()
        log_startup_event("SESSION_CONFIG_LOADED")

        # Язык интерфейса приходит из лаунчера через session_config["interface"].
        # Он не связан с языком, на который переводится игра.
        self.interface_lang = normalize_interface_lang(
            self.session_config.get("interface", {}).get("lang", "ru")
        )

        # Hotkeys теперь приходят из лаунчера через session_config.
        # Если профиль старый и hotkeys там нет — используем безопасные дефолты.
        self.hotkeys = normalize_hotkeys(self.session_config.get("hotkeys", {}))
        self.session_config["hotkeys"] = self.hotkeys
        self.hotkey_down_states = {action: False for action in self.hotkeys.keys()}
        self._hotkey_poll_interval_ms = 20
        self._hotkey_poll_state = HotkeyPollingState(self.hotkeys.keys())

        self.session_config["dynamic"] = normalize_dynamic_settings(self.session_config.get("dynamic", {}))
        self.session_config["ocr"] = normalize_ocr_settings(self.session_config.get("ocr", {}))
        self.session_config["subtitles"] = normalize_subtitle_settings(self.session_config.get("subtitles", {}))

        try:
            if resource_monitor is not None:
                resource_monitor.start_resource_monitor(APP_DIR, self.session_config)
        except Exception:
            pass

        translation_cfg = self.session_config.get("translation", {}) or {}
        diagnostic_candidate = bool(
            startup_block3_diagnostics.selected_startup_mode()
            and is_block3_session(self.session_config)
            and str(translation_cfg.get("translation_profile", "fast") or "fast").lower() == "fast"
            and str(translation_cfg.get("target_lang", "") or "").lower().replace("-", "_")
            in {"zh", "zh_hant"}
            and str(translation_cfg.get("backend", "block3_router") or "block3_router").lower()
            == "block3_router"
            and current_ocr_backend_from_session(self.session_config) == "paddle_fast"
        )
        if diagnostic_candidate:
            startup_block3_diagnostics.configure_startup_diagnostics(
                process_started_monotonic=_STARTUP_T0,
                metadata={
                    "language_mode": self.session_config.get("language_mode"),
                    "translation_backend": translation_cfg.get("backend"),
                    "translation_profile": translation_cfg.get("translation_profile"),
                    "source_lang": translation_cfg.get("source_lang"),
                    "target_lang": translation_cfg.get("target_lang"),
                    "ocr_backend": current_ocr_backend_from_session(self.session_config),
                    "game_hwnd": self.session_config.get("game_hwnd"),
                },
            )
            self._startup_diagnostic_mode = startup_block3_diagnostics.selected_startup_mode()

        try:
            log_startup_event("OCR_BACKEND_SET_START", backend=self.session_config["ocr"].get("engine", "tesseract"))
            ocr_core.set_backend(self.session_config["ocr"].get("engine", "tesseract"))
            log_startup_event("OCR_BACKEND_SET_DONE", backend=self.session_config["ocr"].get("engine", "tesseract"))
        except Exception as e:
            print(f"[OCR BACKEND WARNING]: {e}", flush=True)
        self.dynamic_only_mode = self.is_dynamic_only_mode()

        log_startup_event("RESOLVE_TARGET_HWND_START")
        self.target_hwnd = self.resolve_target_hwnd()
        log_startup_event("RESOLVE_TARGET_HWND_DONE", hwnd=self.target_hwnd)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        log_startup_event("INIT_UI_START")
        self.init_ui()
        log_startup_event("INIT_UI_DONE")
        clear_global_override_cursor()
        self.apply_geometry_from_session()

        # Поток движка перевода.
        log_startup_event("TRANSLATION_ENGINE_CREATE_START")
        self.engine_thread = TranslationEngine()
        self.engine_thread.update_session_data(self.session_config)
        log_startup_event("TRANSLATION_ENGINE_CREATE_DONE")
        self.engine_thread.translation_ready.connect(self.update_translation_ui)
        self.engine_thread.adaptive_plate_sample.connect(self.on_adaptive_plate_sample)
        self.engine_thread.model_loaded_signal.connect(self.on_model_warmup_done)
        self.engine_thread.force_refresh_status_signal.connect(self.on_force_refresh_status)
        self.engine_thread.ocr_growth_pending_deadline_requested.connect(
            self.schedule_ocr_growth_pending_deadline
        )
        self.engine_thread.subtitle_stale_grace_requested.connect(
            self.schedule_subtitle_stale_grace
        )

        self.ocr_growth_pending_timer = QTimer(self)
        self.ocr_growth_pending_timer.setSingleShot(True)
        self.ocr_growth_pending_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.ocr_growth_pending_timer.timeout.connect(self.on_ocr_growth_pending_deadline)
        self._ocr_growth_pending_timer_token = 0

        self.subtitle_stale_grace_timer = QTimer(self)
        self.subtitle_stale_grace_timer.setSingleShot(True)
        self.subtitle_stale_grace_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.subtitle_stale_grace_timer.timeout.connect(self.on_subtitle_stale_grace_expired)
        self._subtitle_stale_sequence_id = 0

        self.dynamic_capture_timer = QTimer(self)
        self.dynamic_capture_timer.setSingleShot(True)
        self.dynamic_capture_timer.timeout.connect(self.on_dynamic_capture_timer)

        self.dynamic_restore_timer = QTimer(self)
        self.dynamic_restore_timer.setSingleShot(True)
        self.dynamic_restore_timer.timeout.connect(self.on_dynamic_restore_timer)

        # Таймер возобновления OCR после ручной настройки рамки.
        # Каждый move/resize перезапускает таймер; фактическое возобновление
        # происходит только когда пользователь перестал двигать окно.
        self.geometry_resume_timer = QTimer(self)
        self.geometry_resume_timer.setSingleShot(True)
        self.geometry_resume_timer.timeout.connect(self.resume_capture_after_geometry_adjustment)

        # Глобальный хоткей обрабатываем без RegisterHotKey/nativeEvent.
        # v2 с nativeEvent падал нативным крашем, v3 с RegisterHotKey+PeekMessage
        # запускался, но хоткей не срабатывал.
        # Поэтому v4 использует безопасный опрос GetAsyncKeyState():
        # оверлей каждые 50 мс проверяет, зажаты ли Ctrl+Alt+Shift+P.
        # В v6 добавлен второй polling-хоткей Ctrl+Alt+Shift+H для временного
        # скрытия/возврата переводной плашки поверх игры.
        # Это работает даже когда активным стало окно игры, потому что GetAsyncKeyState
        # читает глобальное состояние клавиатуры Windows.
        self.hotkey_timer = QTimer(self)
        self.hotkey_timer.timeout.connect(self.check_global_hotkey)
        self.hotkey_timer.start(self._hotkey_poll_interval_ms)
        startup_block3_diagnostics.emit_startup_diagnostic(
            "LIVE_HOTKEY_POLLING_STARTED",
            poll_interval_ms=self._hotkey_poll_interval_ms,
        )
        print(f"[HOTKEY] Режим polling. Активные хоткеи: {self.build_hotkey_hint_text()}", flush=True)

        # Тяжёлые backend/storage warmup стартуют только после ready-marker из Qt event loop.
        # Launcher должен увидеть готовый UI, а не ждать модели и SQLite.
        log_startup_event("POST_READY_INITIALIZATION_DEFERRED")
        log_startup_event("OVERLAY_INIT_DONE")

    @property
    def is_click_through(self):
        return self.live_mouse_mode == LiveOverlayMouseMode.GAMEPLAY

    def start_post_ready_initialization(self):
        if self._post_ready_initialization_started:
            return
        self._post_ready_initialization_started = True
        log_startup_event("POST_READY_INITIALIZATION_BEGIN")
        self.start_translation_storage_prepare()

        backend_name = current_translation_backend_from_session(self.session_config)
        q6_backend = backend_name in {
            "translategemma_llama_q6_low_vram",
            "translategemma_llama_q6_speed",
        }
        paddle_selected = is_paddle_family_backend(self.selected_ocr_backend_name())
        offline_mode = self.mode_selector.currentText() == "offline"
        self._startup_paddle_waiting_for_model = bool(q6_backend and paddle_selected and offline_mode)
        self.update_block3_pipeline_readiness("post_ready_initialization_begin")

        if self._startup_diagnostic_mode and offline_mode:
            self._startup_paddle_waiting_for_model = False
            self._startup_diagnostic_sequence = startup_block3_diagnostics.Block3StartupSequence(
                self._startup_diagnostic_mode
            )
            for component in self._startup_diagnostic_sequence.deferred_components():
                startup_block3_diagnostics.mark_component_deferred(
                    component,
                    reason=f"diagnostic_{self._startup_diagnostic_mode}",
                )
            for component in self._startup_diagnostic_sequence.initial_components():
                self._start_startup_diagnostic_component(component)
            log_startup_event(
                "POST_READY_INITIALIZATION_SCHEDULED",
                startup_diagnostic_mode=self._startup_diagnostic_mode,
            )
            return

        block3_requirement = block3_translator_readiness_requirement(self.session_config)
        if (
            offline_mode
            and paddle_selected
            and block3_requirement.get("translator_backend") == "csanmt"
        ):
            log_startup_event(
                "BLOCK3_PRODUCTION_STARTUP_POLICY",
                policy="parallel",
                target=block3_requirement.get("target"),
                ocr_backend=self.selected_ocr_backend_name(),
                translator_backend="csanmt",
            )
        log_startup_event("MODEL_WARMUP_START_REQUEST")
        self.start_model_warmup()
        if self._startup_paddle_waiting_for_model:
            log_startup_event("PADDLE_WARMUP_WAITING_FOR_TRANSLATEGEMMA", backend=backend_name)
            if self.model_warmup_thread is None or not self.model_warmup_thread.isRunning():
                self._start_deferred_startup_paddle()
        else:
            self.start_paddle_ocr_prepare("overlay_startup_post_ready")
        log_startup_event("POST_READY_INITIALIZATION_SCHEDULED")

    def _start_startup_diagnostic_component(self, component):
        component = str(component or "").strip().lower()
        if component == "csanmt":
            startup_block3_diagnostics.mark_component_requested(
                "csanmt",
                backend=current_translation_backend_from_session(self.session_config),
            )
            log_startup_event(
                "MODEL_WARMUP_START_REQUEST",
                startup_diagnostic_mode=self._startup_diagnostic_mode,
            )
            self.start_model_warmup()
            if self.model_warmup_thread is None or not self.model_warmup_thread.isRunning():
                startup_block3_diagnostics.emit_startup_diagnostic(
                    "CSANMT_START_ERROR",
                    error="model_warmup_thread_not_started",
                )
                self._finish_startup_diagnostic_component("csanmt", False)
            return
        if component == "paddle":
            startup_block3_diagnostics.mark_component_requested(
                "paddle",
                backend=self.selected_ocr_backend_name(),
            )
            if not self.start_paddle_ocr_prepare(
                f"startup_diagnostic_{self._startup_diagnostic_mode}"
            ):
                startup_block3_diagnostics.emit_startup_diagnostic(
                    "PADDLE_PREPARE_ERROR",
                    error="paddle_prepare_not_started",
                )
                self._finish_startup_diagnostic_component("paddle", False)

    def _finish_startup_diagnostic_component(self, component, ok):
        sequence = self._startup_diagnostic_sequence
        if sequence is None:
            return
        for next_component in sequence.component_finished(component):
            QTimer.singleShot(
                0,
                lambda name=next_component: self._start_startup_diagnostic_component(name),
            )

    def start_translation_storage_prepare(self):
        if self.storage_prepare_thread is not None and self.storage_prepare_thread.isRunning():
            return
        log_startup_event("TRANSLATION_STORAGE_PREPARE_SCHEDULED")
        self.storage_prepare_thread = OverlayStoragePrepareThread(self.session_config)
        self.storage_prepare_thread.ready_signal.connect(self.on_translation_storage_prepare_finished)
        self.storage_prepare_thread.start()

    @pyqtSlot(str, str)
    def on_translation_storage_prepare_finished(self, memory_path, error):
        if memory_path:
            self.session_config["translation_memory_path"] = str(memory_path)
            self.engine_thread.update_session_data(self.session_config)
            print(f"[TRANSLATION MEMORY] ready: {memory_path}", flush=True)
            log_startup_event("TRANSLATION_MEMORY_READY")
        elif error:
            print(f"[TRANSLATION MEMORY WARNING]: {error}", flush=True)
        else:
            print("[TRANSLATION MEMORY] not initialized; see translation_memory_debug.log", flush=True)

    def _start_deferred_startup_paddle(self):
        if not self._startup_paddle_waiting_for_model:
            return
        self._startup_paddle_waiting_for_model = False
        self.start_paddle_ocr_prepare("overlay_startup_after_translategemma")

    def load_session_configuration(self):
        """Загрузка session_config.json, созданного лаунчером."""
        if not os.path.exists(self.session_file):
            print("[ОШИБКА КОНФИГА]: session_config.json не найден.")
            sys.exit(1)

        try:
            with open(self.session_file, "r", encoding="utf-8") as f:
                self.session_config = json.load(f)
            session_logging.update_state(self.session_config)
            for key in ("profile_path", "profile_dir"):
                if self.session_config.get(key):
                    self.session_config[key] = rebase_profile_path(self.session_config[key])
                    candidate = os.path.realpath(self.session_config[key])
                    if os.path.commonpath([candidate, os.path.realpath(PROFILES_DIR)]) != os.path.realpath(PROFILES_DIR):
                        raise ValueError("Profile path outside configured profiles root")
            self.session_config, migration = ensure_language_blocks(self.session_config)
            active_block = block_id_for_language_mode(
                self.session_config.get("language_mode")
                or (self.session_config.get("translation", {}) or {}).get("language_mode")
            )
            self.session_config = apply_block_state(self.session_config, active_block)
            translator_core.configure_quality_session(
                self.session_config.get("quality_runtime_selection") or {
                    "available": False, "reason": "session_hardware_selection_missing"
                }
            )
            if active_block in {BLOCK2, BLOCK3}:
                self.session_config["llm"] = {"mode": "off", "enabled": False, "backend": "none"}
                if active_block == BLOCK2:
                    log_startup_event(
                        "block2_profile_loaded",
                        legacy_profile=migration.get("legacy_profile"),
                        block2_recovered=migration.get("block2_created_or_recovered"),
                    )
                else:
                    log_startup_event(
                        "block3_profile_loaded",
                        legacy_profile=migration.get("legacy_profile"),
                        block3_recovered=migration.get("block3_created_or_recovered"),
                    )
        except Exception as e:
            print(f"[ОШИБКА КОНФИГА]: {e}")
            sys.exit(1)

    def is_dynamic_only_mode(self):
        """True, если пользователь выбрал отдельный режим динамического перевода."""
        dynamic_cfg = normalize_dynamic_settings(self.session_config.get("dynamic", {}))
        return dynamic_cfg.get("mode", "combined") == "standalone"

    def init_ui(self):
        self.setMinimumSize(64, 48)
        self.main_layout = QVBoxLayout()
        self.main_layout.setContentsMargins(10, 10, 10, 10)
        self.main_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)

        # --- ВЕРХНЯЯ ПАНЕЛЬ НАСТРОЕК (ПАНЕЛЬ УПРАВЛЕНИЯ) ---
        # Видна только в режиме настройки.
        # В режиме перевода она скрывается, чтобы не мешать игре и OCR.
        self.control_widget = QWidget()
        control_panel = QVBoxLayout(self.control_widget)
        control_panel.setContentsMargins(0, 0, 0, 0)
        self.control_layout = QHBoxLayout()
        control_panel.addLayout(self.control_layout)
        self.control_layout.setContentsMargins(0, 0, 0, 0)
        self.control_layout.setSpacing(6)

        if self.dynamic_only_mode:
            title_text = (
                f"🎮 {tr(self.interface_lang, 'overlay_dynamic_title')}: "
                f"{self.session_config.get('game_name', tr(self.interface_lang, 'overlay_game_default'))}"
            )
        else:
            title_text = f"🎮 {tr(self.interface_lang, 'overlay_zone')}: {self.session_config.get('game_name', tr(self.interface_lang, 'overlay_game_default'))}"
        self.lbl_title = QLabel(title_text)
        self.lbl_title.setStyleSheet(
            "color: #00ff00; font-weight: bold; font-size: 11px; "
            "background: rgba(0,0,0,180); padding: 4px; border-radius: 3px;"
        )
        self.control_layout.addWidget(self.lbl_title)

        block2_active = is_block2_session(self.session_config)
        block3_active = is_block3_session(self.session_config)
        block_router_active = block2_active or block3_active
        translation_cfg = self.session_config.get("translation", {}) or {}

        # Block 2 source language. Block 1 keeps its fixed EN source unchanged.
        self.source_selector = QComboBox()
        self.source_selector.addItem(tr(self.interface_lang, "language_display_ja"), "ja")
        self.source_selector.addItem(tr(self.interface_lang, "language_display_zh"), "zh")
        self.source_selector.setCurrentIndex(max(0, self.source_selector.findData(translation_cfg.get("source_lang", "ja"))))
        self.source_selector.currentIndexChanged.connect(self.on_live_settings_changed)
        self.source_selector.setVisible(block2_active)
        self.source_selector.setStyleSheet(
            "background: #222; color: white; font-size: 11px; padding: 2px; border: 1px solid #444;"
        )
        self.control_layout.addWidget(self.source_selector)

        # Селектор языка перевода.
        # Менять можно только в режиме настройки, потому что в режиме перевода панель скрыта.
        self.lang_selector = QComboBox()
        target_options = (
            ["en", "ru", "uk"]
            if block2_active
            else ["zh", "zh_hant", "ja"]
            if block3_active
            else ["uk", "ru"]
        )
        target_labels = {
            "zh": "language_display_zh_s",
            "zh_hant": "language_display_zh_t",
            "ja": "language_display_ja",
        }
        for target_code in target_options:
            label_key = target_labels.get(target_code)
            self.lang_selector.addItem(
                tr(self.interface_lang, label_key) if label_key else target_code.upper(),
                target_code,
            )
        default_target = "en" if block2_active else "ja" if block3_active else "uk"
        self.lang_selector.setCurrentIndex(max(0, self.lang_selector.findData(translation_cfg.get("target_lang", default_target))))
        self.lang_selector.currentIndexChanged.connect(self.on_live_settings_changed)
        self.lang_selector.setStyleSheet(
            "background: #222; color: white; font-size: 11px; padding: 2px; border: 1px solid #444;"
        )
        self.control_layout.addWidget(self.lang_selector)

        self.block2_profile_selector = QComboBox()
        self.block2_profile_selector.addItem(tr(self.interface_lang, "block2_profile_fast"), "fast")
        self.block2_profile_selector.addItem(tr(self.interface_lang, "block2_profile_quality"), "quality")
        self.block2_profile_selector.setCurrentIndex(max(0, self.block2_profile_selector.findData(translation_cfg.get("translation_profile", "fast"))))
        self.block2_profile_selector.currentIndexChanged.connect(self.on_live_settings_changed)
        self.block2_profile_selector.setVisible(block_router_active)
        self.block2_profile_selector.setStyleSheet(
            "background: #222; color: white; font-size: 11px; padding: 2px; border: 1px solid #444;"
        )
        self.control_layout.addWidget(self.block2_profile_selector)

        self.block_quality_ocr_selector = QComboBox()
        fast_ocr_engine = fast_ocr_engine_for_block(BLOCK2)
        self.block_quality_ocr_selector.addItem(
            tr(self.interface_lang, "ocr_engine_paddle_block2_fast"),
            fast_ocr_engine,
        )
        self.block_quality_ocr_selector.addItem(
            tr(self.interface_lang, "ocr_engine_paddle"),
            "paddle",
        )
        quality_ocr_engine = current_ocr_backend_from_session(self.session_config)
        self.block_quality_ocr_selector.setCurrentIndex(
            max(0, self.block_quality_ocr_selector.findData(quality_ocr_engine))
        )
        self.block_quality_ocr_selector.setVisible(block2_active)
        self.block_quality_ocr_selector.setEnabled(
            block2_active
            and str(translation_cfg.get("translation_profile", "fast")) == "quality"
        )
        self.block_quality_ocr_selector.setToolTip(
            tr(self.interface_lang, "ocr_engine_label")
        )
        self.block_quality_ocr_selector.setStyleSheet(
            "QComboBox { background: #222; color: white; font-size: 11px; padding: 2px; border: 1px solid #444; }"
            "QComboBox:disabled { background: #303030; color: #888; border: 1px solid #383838; }"
        )
        self.block_quality_ocr_selector.currentIndexChanged.connect(
            self.on_live_settings_changed
        )
        self.control_layout.addWidget(self.block_quality_ocr_selector)

        self.block2_script_selector = QComboBox()
        self.block2_script_selector.addItem(tr(self.interface_lang, "block2_script_auto"), "auto")
        self.block2_script_selector.addItem(tr(self.interface_lang, "block2_script_simplified"), "zh")
        self.block2_script_selector.addItem(tr(self.interface_lang, "block2_script_traditional"), "zh_hant")
        self.block2_script_selector.setCurrentIndex(max(0, self.block2_script_selector.findData(translation_cfg.get("chinese_script_mode", "auto"))))
        self.block2_script_selector.currentIndexChanged.connect(self.on_live_settings_changed)
        self.block2_script_selector.setVisible(block2_active and translation_cfg.get("source_lang") == "zh")
        self.block2_script_selector.setStyleSheet(
            "background: #222; color: white; font-size: 11px; padding: 2px; border: 1px solid #444;"
        )
        self.control_layout.addWidget(self.block2_script_selector)

        # Селектор режима ИИ: offline / online.
        self.mode_selector = QComboBox()
        self.mode_selector.addItems(["offline", "online"])
        self.mode_selector.setCurrentText(self.session_config.get("translation", {}).get("mode", "offline"))
        self.mode_selector.currentTextChanged.connect(self.on_live_settings_changed)
        self.mode_selector.setStyleSheet(
            "background: #222; color: white; font-size: 11px; padding: 2px; border: 1px solid #444;"
        )
        self.control_layout.addWidget(self.mode_selector)
        self.online_settings_button = QPushButton()
        self.online_settings_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DriveNetIcon))
        self.online_settings_button.setToolTip(tr(self.interface_lang, "online_settings"))
        self.online_settings_button.setAccessibleName(tr(self.interface_lang, "online_settings"))
        self.online_settings_button.setFixedSize(24, 24)
        self.online_settings_button.clicked.connect(self.edit_online_settings)
        self.control_layout.addWidget(self.online_settings_button)

        # Быстрый переключатель динамического перевода прямо в оверлее.
        # Серьёзные настройки динамического режима остаются в лаунчере,
        # но включить/выключить функцию во время игры должно быть можно быстро.
        self.btn_dynamic_toggle = QPushButton(self.build_dynamic_toggle_text())
        self.btn_dynamic_toggle.setFixedHeight(24)
        self.btn_dynamic_toggle.setStyleSheet(
            "background: #263238; color: white; font-size: 11px; padding: 2px 6px; "
            "border: 1px solid #455a64; border-radius: 3px;"
        )
        self.btn_dynamic_toggle.clicked.connect(self.toggle_dynamic_enabled)
        self.control_layout.addWidget(self.btn_dynamic_toggle)

        dynamic_cfg = normalize_dynamic_settings(self.session_config.get("dynamic", {}))
        self.lbl_dynamic_display_time = QLabel(tr(self.interface_lang, "dynamic_display_time"))
        self.lbl_dynamic_display_time.setStyleSheet(
            "color: white; font-size: 11px; background: transparent;"
        )
        self.control_layout.addWidget(self.lbl_dynamic_display_time)

        self.dynamic_display_time_spin = QSpinBox()
        self.dynamic_display_time_spin.setRange(1, 30)
        self.dynamic_display_time_spin.setValue(int(dynamic_cfg.get("display_time_sec", 6)))
        self.dynamic_display_time_spin.setFixedWidth(58)
        self.dynamic_display_time_spin.setStyleSheet(
            "background: #222; color: white; font-size: 11px; padding: 2px; border: 1px solid #444;"
        )
        self.dynamic_display_time_spin.valueChanged.connect(self.on_dynamic_display_time_changed)
        self.control_layout.addWidget(self.dynamic_display_time_spin)

        # Вместо кнопки «Фиксация» оставлена подсказка.
        # Переключение режима теперь только через горячие клавиши.
        self.lbl_hotkey_hint = QLabel(self.build_hotkey_hint_text())
        self.lbl_hotkey_hint.setStyleSheet(
            "color: #dddddd; font-size: 11px; background: rgba(0,0,0,150); "
            "padding: 4px; border-radius: 3px;"
        )
        self.control_layout.addWidget(self.lbl_hotkey_hint)

        self.control_layout.addStretch()

        visual_controls = QHBoxLayout()
        control_panel.addLayout(visual_controls)
        self.adaptive_plate_toggle = QCheckBox(tr(self.interface_lang, "adaptive_plate"))
        self.adaptive_plate_toggle.setStyleSheet("color: white; background: rgba(0, 0, 0, 180); padding: 3px;")
        self.adaptive_plate_toggle.setToolTip(tr(self.interface_lang, "adaptive_plate_tooltip"))
        self.adaptive_plate_toggle.setChecked(bool(self.session_config.get("visuals", {}).get("adaptive_plate_enabled", False)))
        self.adaptive_plate_toggle.toggled.connect(self.on_adaptive_plate_toggled)
        visual_controls.addWidget(self.adaptive_plate_toggle)
        self.btn_text_settings = QPushButton()
        self.btn_text_settings.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView))
        self.btn_text_settings.setToolTip(tr(self.interface_lang, "text_settings_button"))
        self.btn_text_settings.clicked.connect(self.open_text_settings)
        self.btn_text_settings.setFixedSize(26, 24)
        visual_controls.addWidget(self.btn_text_settings)
        visual_controls.addStretch()

        # Крестик закрытия окна.
        # При закрытии сохраняем профиль, останавливаем поток и возвращаемся в лаунчер
        # через штатный on_overlay_finished() в launcher.py.
        self.btn_close = QPushButton("✖")
        self.btn_close.setFixedSize(24, 24)
        self.btn_close.setStyleSheet(
            "background-color: #b71c1c; color: white; font-weight: bold; font-size: 12px; "
            "border-radius: 3px; border: none;"
        )
        self.btn_close.setToolTip(tr(self.interface_lang, "overlay_close_tooltip"))
        self.btn_close.clicked.connect(self.close)
        self.control_layout.addWidget(self.btn_close)

        self.main_layout.addWidget(self.control_widget)

        # --- ЦЕНТРАЛЬНЫЙ БЛОК ВЫВОДА СУБТИТРОВ ---
        initial_overlay_text = tr(self.interface_lang, "overlay_init_model")
        if self.dynamic_only_mode:
            initial_overlay_text = tr(self.interface_lang, "overlay_setup_mode", hotkey=self.get_hotkey_display("toggle_translation_mode"))
        self.lbl_translated = SubtitleLabel(initial_overlay_text)
        self.lbl_translated.setMinimumSize(0, 0)
        self.lbl_translated.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.lbl_translated.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_translated.setWordWrap(True)

        v_cfg = self.session_config.get("visuals", {})
        self.lbl_translated.setFont(
            QFont(
                v_cfg.get("font_name", "Arial"),
                int(v_cfg.get("font_size", 19)),
                QFont.Weight.Bold,
            )
        )
        self.lbl_translated.setStyleSheet(f"color: {v_cfg.get('font_color', '#FFFFFF')}; line-height: 1.3;")
        self.lbl_translated.set_visuals(v_cfg)
        self.main_layout.addWidget(self.lbl_translated)
        self._live_overlay_fitted_font_size = int(v_cfg.get("font_size", 19))
        self.live_overlay_refit_timer = QTimer(self)
        self.live_overlay_refit_timer.setSingleShot(True)
        self.live_overlay_refit_timer.timeout.connect(self.refit_current_live_translation)

        self.force_refresh_status_label = QLabel(self)
        self.force_refresh_status_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        self.force_refresh_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.force_refresh_status_label.setStyleSheet(
            "background-color: rgba(20, 20, 20, 220); color: #FFFFFF; "
            "border: 1px solid rgba(255, 255, 255, 90); border-radius: 3px; "
            "padding: 5px 8px; font-size: 11px; font-weight: bold;"
        )
        self.force_refresh_status_label.hide()
        self.model_preparation_status_label = QLabel(self)
        self.model_preparation_status_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.model_preparation_status_label.setStyleSheet(
            "background-color: rgba(20, 20, 20, 220); color: #FFFFFF; "
            "border: 1px solid rgba(255, 255, 255, 90); border-radius: 3px; "
            "padding: 5px 8px; font-size: 11px;"
        )
        self.model_preparation_status_label.hide()
        self.model_preparation_timer = QTimer(self)
        self.model_preparation_timer.timeout.connect(self.advance_model_preparation_status)
        self._model_preparation_text = ""
        self._model_preparation_frame = 0
        self._force_refresh_visual_state = "hidden"
        self._force_refresh_visual_request_id = 0
        self._force_refresh_status_timeout_ms = 1050
        self.force_refresh_status_timer = QTimer(self)
        self.force_refresh_status_timer.setSingleShot(True)
        self.force_refresh_status_timer.timeout.connect(self.hide_force_refresh_status)

        self.setLayout(self.main_layout)

        # --- МАРКЕР ИЗМЕНЕНИЯ РАЗМЕРА ОКНА (SIZE GRIP) ---
        self.sizer_grip = QSizeGrip(self)
        self.sizer_grip.setFixedSize(16, 16)
        self.sizer_grip.setStyleSheet("background: rgba(0, 255, 0, 100); border-radius: 2px;")

    def apply_geometry_from_session(self):
        """Восстановление позиции и размера оверлея из профиля/session_config."""
        zone = self.session_config.get("monitor_zone", {})
        width = max(64, int(zone.get("width", 650)))
        height = max(48, int(zone.get("height", 180)))
        zone["width"] = width
        zone["height"] = height
        self.setGeometry(
            int(zone.get("left", 350)),
            int(zone.get("top", 600)),
            width,
            height,
        )

    def minimumSizeHint(self):
        return QSize(64, 48)

    def safe_profile_name(self, name):
        """Безопасное имя файла профиля Windows."""
        return re.sub(r'[\\/*?:"<>|]', "", name).strip()

    def resolve_target_hwnd(self):
        """
        Получение дескриптора окна игры.

        Основной путь: hwnd передаётся лаунчером в session_config.
        Резервный путь: поиск окна по названию, чтобы старые профили без hwnd не ломались.
        """
        hwnd = int(self.session_config.get("game_hwnd", 0) or 0)
        if hwnd and win32gui and win32gui.IsWindow(hwnd):
            return hwnd

        game_name = self.session_config.get("game_name", "")
        if win32gui and game_name:
            found = win32gui.FindWindow(None, game_name)
            if found and win32gui.IsWindow(found):
                return int(found)

        return 0

    def _app_owned_window_handles(self):
        handles = set()
        for window in (self, self.dynamic_selection_overlay, self.dynamic_result_popup):
            if window is None:
                continue
            try:
                handles.add(int(window.winId()))
            except Exception:
                pass
        return handles

    @staticmethod
    def _win32_rect_values(rect):
        return [
            int(rect.left),
            int(rect.top),
            int(rect.right),
            int(rect.bottom),
        ]

    def is_valid_target_window(self, hwnd):
        try:
            hwnd = int(hwnd or 0)
            return bool(hwnd and user32.IsWindow(hwnd))
        except Exception:
            return False

    def cursor_diagnostic_snapshot(self, target_hwnd=0):
        """Read-only cursor/capture/clip diagnostics for ownership transitions."""
        snapshot = {
            "target_hwnd": int(target_hwnd or 0),
            "foreground_hwnd": 0,
            "capture_hwnd": 0,
            "cursor_pos": None,
            "cursor_visible": None,
            "cursor_flags": None,
            "clip_rect": None,
            "virtual_screen_rect": None,
            "game_client_rect": None,
            "clip_matches_game": None,
            "clip_looks_unrestricted": None,
            "clip_classification": "unavailable",
            "desktop_edge_delta": None,
            "game_edge_delta": None,
            "desktop_tolerance": 1,
            "game_tolerance": 3,
        }
        try:
            snapshot["foreground_hwnd"] = int(user32.GetForegroundWindow() or 0)
        except Exception:
            pass
        try:
            snapshot["capture_hwnd"] = int(user32.GetCapture() or 0)
        except Exception:
            pass
        try:
            point = wintypes.POINT()
            if user32.GetCursorPos(ctypes.byref(point)):
                snapshot["cursor_pos"] = [int(point.x), int(point.y)]
        except Exception:
            pass
        try:
            cursor_info = CURSORINFO()
            cursor_info.cbSize = ctypes.sizeof(CURSORINFO)
            if user32.GetCursorInfo(ctypes.byref(cursor_info)):
                snapshot["cursor_flags"] = int(cursor_info.flags)
                snapshot["cursor_visible"] = bool(cursor_info.flags & CURSOR_SHOWING)
        except Exception:
            pass
        try:
            clip_rect = wintypes.RECT()
            if user32.GetClipCursor(ctypes.byref(clip_rect)):
                snapshot["clip_rect"] = self._win32_rect_values(clip_rect)
        except Exception:
            pass
        try:
            virtual_left = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
            virtual_top = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
            virtual_width = int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
            virtual_height = int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
            snapshot["virtual_screen_rect"] = [
                virtual_left,
                virtual_top,
                virtual_left + virtual_width,
                virtual_top + virtual_height,
            ]
        except Exception:
            pass
        try:
            target_hwnd = int(target_hwnd or 0)
            if target_hwnd and user32.IsWindow(target_hwnd):
                client_rect = wintypes.RECT()
                if user32.GetClientRect(target_hwnd, ctypes.byref(client_rect)):
                    top_left = wintypes.POINT(client_rect.left, client_rect.top)
                    bottom_right = wintypes.POINT(client_rect.right, client_rect.bottom)
                    if (
                        user32.ClientToScreen(target_hwnd, ctypes.byref(top_left))
                        and user32.ClientToScreen(target_hwnd, ctypes.byref(bottom_right))
                    ):
                        snapshot["game_client_rect"] = [
                            int(top_left.x),
                            int(top_left.y),
                            int(bottom_right.x),
                            int(bottom_right.y),
                        ]
        except Exception:
            pass
        snapshot.update(
            classify_cursor_clip(
                snapshot.get("clip_rect"),
                snapshot.get("virtual_screen_rect"),
                snapshot.get("game_client_rect"),
            )
        )
        return snapshot

    def log_cursor_diagnostic_transition(self, interaction_kind, phase, target_hwnd, reason=""):
        snapshot = self.cursor_diagnostic_snapshot(target_hwnd)
        self.log_ui_event(
            "CURSOR_DIAGNOSTIC_TRANSITION",
            (
                f"interaction={interaction_kind}; phase={phase}; reason={reason}; "
                f"snapshot={json.dumps(snapshot, ensure_ascii=True, separators=(',', ':'))}"
            ),
        )
        return snapshot

    def begin_window_interaction(self, interaction_kind, target_hwnd, reason=""):
        """Track an interactive dynamic selection window; live EDIT uses its own state."""
        interaction_kind = str(interaction_kind or "interaction")
        existing = self._window_interaction_contexts.get(interaction_kind)
        if existing is not None:
            self.log_ui_event(
                "WINDOW_INTERACTION_ENTER_REUSED",
                f"interaction={interaction_kind}; reason={reason}",
            )
            return existing

        self.cancel_cursor_restore_checks("new_interaction")
        target_hwnd = int(target_hwnd or 0)
        before_snapshot = self.cursor_diagnostic_snapshot(target_hwnd)
        foreground_before = int(before_snapshot.get("foreground_hwnd") or 0)
        target_valid = self.is_valid_target_window(target_hwnd)
        self.observe_last_known_good_game_clip(
            target_hwnd,
            before_snapshot,
            reason=f"before_{interaction_kind}",
        )
        self._window_interaction_seq += 1
        context = {
            "interaction_id": int(self._window_interaction_seq),
            "interaction": interaction_kind,
            "target_hwnd": target_hwnd,
            "foreground_before": foreground_before,
            "game_was_foreground": bool(target_valid and foreground_before == target_hwnd),
            "target_valid_on_enter": target_valid,
            "reason": str(reason or ""),
            "created_monotonic": time.monotonic(),
            "cursor_before": before_snapshot,
            "last_known_good_game_clip": (
                list(self._last_known_good_game_clip)
                if self._last_known_good_game_clip is not None
                and self._last_known_good_target_hwnd == target_hwnd
                else None
            ),
        }
        self._window_interaction_contexts[interaction_kind] = context
        self.log_ui_event(
            "WINDOW_INTERACTION_ENTER",
            (
                f"interaction={interaction_kind}; target_hwnd={target_hwnd}; "
                f"foreground_before={foreground_before}; "
                f"game_was_foreground={context['game_was_foreground']}; "
                f"target_valid={target_valid}; reason={reason}"
            ),
        )
        self.log_cursor_diagnostic_transition(
            interaction_kind,
            "enter",
            target_hwnd,
            reason,
        )
        self.log_cursor_state_event("CURSOR_STATE_BEFORE_INTERACTION", context, before_snapshot, reason=reason)
        return context

    def finish_window_interaction(self, interaction_kind, reason=""):
        """Finish a dynamic selection interaction and preserve its existing recovery."""
        interaction_kind = str(interaction_kind or "interaction")
        context = self._window_interaction_contexts.pop(interaction_kind, None)
        if context is None:
            self.log_ui_event(
                "WINDOW_INTERACTION_EXIT_WITHOUT_CONTEXT",
                f"interaction={interaction_kind}; reason={reason}",
            )
            return False

        target_hwnd = int(context.get("target_hwnd") or 0)
        try:
            current_foreground = int(user32.GetForegroundWindow() or 0)
        except Exception:
            current_foreground = 0
        target_valid = self.is_valid_target_window(target_hwnd)
        decision = window_focus_restore_decision(
            game_was_foreground=bool(context.get("game_was_foreground")),
            target_valid=target_valid,
            session_stopping=self._session_stopping,
            current_foreground=current_foreground,
            target_hwnd=target_hwnd,
        )
        attempted = bool(decision.get("restore"))
        restored = bool(
            decision.get("reason") == "already_foreground"
            or current_foreground == target_hwnd
        )
        if attempted:
            try:
                user32.ShowWindow(target_hwnd, SW_RESTORE)
                restored = bool(user32.SetForegroundWindow(target_hwnd))
            except Exception:
                restored = False

        self.log_ui_event(
            "WINDOW_INTERACTION_EXIT",
            (
                f"interaction={interaction_kind}; target_hwnd={target_hwnd}; "
                f"foreground_before={context.get('foreground_before', 0)}; "
                f"foreground_at_exit={current_foreground}; "
                f"game_was_foreground={bool(context.get('game_was_foreground'))}; "
                f"target_valid={target_valid}; restore_attempted={attempted}; "
                f"restore_result={restored}; decision={decision.get('reason')}; reason={reason}"
            ),
        )
        self.log_cursor_diagnostic_transition(
            interaction_kind,
            "exit",
            target_hwnd,
            reason,
        )
        after_snapshot = self.cursor_diagnostic_snapshot(target_hwnd)
        self.log_cursor_state_event("CURSOR_STATE_AFTER_INTERACTION", context, after_snapshot, reason=reason)
        if attempted:
            self.log_cursor_diagnostic_transition(
                interaction_kind,
                "foreground_restored",
                target_hwnd,
                reason,
            )
        self.schedule_cursor_restore_checks(context, reason)
        return restored

    def log_cursor_state_event(self, event, context, snapshot, **details):
        payload = {
            "interaction_id": int((context or {}).get("interaction_id") or 0),
            "interaction_type": str((context or {}).get("interaction") or ""),
            "target_hwnd": int((context or {}).get("target_hwnd") or 0),
            "foreground_before": int((context or {}).get("foreground_before") or 0),
            "game_was_foreground": bool((context or {}).get("game_was_foreground")),
            "snapshot": dict(snapshot or {}),
        }
        payload.update(details)
        self.log_ui_event(
            str(event),
            json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
        )

    def observe_last_known_good_game_clip(self, target_hwnd, snapshot=None, reason=""):
        target_hwnd = int(target_hwnd or 0)
        snapshot = dict(snapshot or self.cursor_diagnostic_snapshot(target_hwnd))
        interaction_active = bool(self._window_interaction_contexts)
        valid = bool(
            not self._session_stopping
            and self.is_click_through
            and not interaction_active
            and self.is_valid_target_window(target_hwnd)
            and int(snapshot.get("foreground_hwnd") or 0) == target_hwnd
            and snapshot.get("clip_classification") == "game_confinement"
            and not snapshot.get("clip_looks_unrestricted")
            and snapshot.get("clip_rect")
        )
        if not valid:
            return False
        self._last_known_good_game_clip = list(snapshot["clip_rect"])
        self._last_known_good_target_hwnd = target_hwnd
        return True

    def cancel_cursor_restore_checks(self, reason=""):
        for timer, _interaction_id in list(self._cursor_restore_timers):
            try:
                timer.stop()
                timer.deleteLater()
            except Exception:
                pass
        self._cursor_restore_timers.clear()

    def schedule_cursor_restore_checks(self, context, reason=""):
        """Keep the legacy bounded checks only for dynamic selection."""
        if self._session_stopping:
            self.log_cursor_state_event(
                "CURSOR_RESTORE_SKIPPED",
                context,
                self.cursor_diagnostic_snapshot(context.get("target_hwnd")),
                restore_reason="session_stopping",
            )
            return
        if not bool(context.get("game_was_foreground")):
            self.log_cursor_state_event(
                "CURSOR_RESTORE_SKIPPED",
                context,
                self.cursor_diagnostic_snapshot(context.get("target_hwnd")),
                restore_reason="game_was_not_foreground",
            )
            return

        interaction_id = int(context.get("interaction_id") or 0)
        for attempt, delay_ms in enumerate((0, 50, 150, 300), start=1):
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(
                lambda context=context, attempt=attempt, final=(delay_ms == 300):
                    self.check_cursor_restore_after_interaction(context, attempt, final)
            )
            timer.timeout.connect(
                lambda timer=timer, interaction_id=interaction_id:
                    self.discard_cursor_restore_timer(timer, interaction_id)
            )
            self._cursor_restore_timers.append((timer, interaction_id))
            timer.start(delay_ms)

    def discard_cursor_restore_timer(self, timer, interaction_id):
        try:
            self._cursor_restore_timers.remove((timer, interaction_id))
        except ValueError:
            pass
        timer.deleteLater()

    def stop_cursor_restore_checks_for_interaction(self, interaction_id):
        for timer, timer_interaction_id in list(self._cursor_restore_timers):
            if int(timer_interaction_id) != int(interaction_id):
                continue
            try:
                timer.stop()
                timer.deleteLater()
            except Exception:
                pass
            try:
                self._cursor_restore_timers.remove((timer, timer_interaction_id))
            except ValueError:
                pass

    def check_cursor_restore_after_interaction(self, context, attempt, final):
        target_hwnd = int(context.get("target_hwnd") or 0)
        interaction_id = int(context.get("interaction_id") or 0)
        snapshot = self.cursor_diagnostic_snapshot(target_hwnd)
        decision = cursor_post_interaction_decision(
            context=context,
            snapshot=snapshot,
            target_valid=self.is_valid_target_window(target_hwnd),
            session_stopping=self._session_stopping,
            final_check=final,
        )
        if decision["action"] == "skip":
            self.log_cursor_state_event(
                "CURSOR_RESTORE_SKIPPED",
                context,
                snapshot,
                restore_attempt=attempt,
                restore_reason=decision["reason"],
            )
            self.stop_cursor_restore_checks_for_interaction(interaction_id)
            return
        if decision["action"] == "self_restored":
            self.observe_last_known_good_game_clip(target_hwnd, snapshot, "game_self_restore")
            self.log_cursor_state_event(
                "CURSOR_GAME_SELF_RESTORE_DETECTED",
                context,
                snapshot,
                restore_attempt=attempt,
                restore_reason="game_restored_clip",
            )
            self.stop_cursor_restore_checks_for_interaction(interaction_id)
            return
        if decision["action"] == "wait":
            return

        self.log_cursor_state_event(
            "CURSOR_GAME_SELF_RESTORE_TIMEOUT",
            context,
            snapshot,
            restore_attempt=attempt,
            restore_reason="bounded_wait_expired",
        )
        saved_clip = context.get("last_known_good_game_clip")
        if int(context.get("target_hwnd") or 0) != self._last_known_good_target_hwnd:
            skip_reason = "last_known_good_target_mismatch"
        else:
            skip_reason = ""
        if skip_reason:
            self.log_cursor_state_event(
                "CURSOR_RESTORE_SKIPPED",
                context,
                snapshot,
                restore_attempt=attempt,
                restore_reason=skip_reason,
            )
            return

        rect = wintypes.RECT(*[int(value) for value in saved_clip])
        try:
            ctypes.set_last_error(0)
            win32_result = bool(user32.ClipCursor(ctypes.byref(rect)))
            win32_error = int(ctypes.get_last_error())
        except Exception as exc:
            win32_result = False
            win32_error = f"{type(exc).__name__}:{exc}"
        self.log_cursor_state_event(
            "CURSOR_OVERLAY_RESTORE_ATTEMPTED",
            context,
            snapshot,
            restore_attempt=attempt,
            restore_reason="desktop_clip_after_interaction",
            win32_result=win32_result,
            win32_error=win32_error,
        )
        verified = self.cursor_diagnostic_snapshot(target_hwnd)
        if win32_result and verified.get("clip_classification") == "game_confinement":
            self.log_cursor_state_event(
                "CURSOR_OVERLAY_RESTORE_SUCCEEDED",
                context,
                verified,
                restore_attempt=attempt,
                restore_reason="verified_game_confinement",
                win32_result=win32_result,
                win32_error=win32_error,
            )
        else:
            self.log_cursor_state_event(
                "CURSOR_OVERLAY_RESTORE_FAILED",
                context,
                verified,
                restore_attempt=attempt,
                restore_reason="clip_verification_failed",
                win32_result=win32_result,
                win32_error=win32_error,
            )

    def log_window_ownership_transition(
        self,
        event,
        state,
        *,
        window=None,
        restore_attempted=False,
        restore_result=False,
    ):
        try:
            target_hwnd = int(self.resolve_target_hwnd() or 0)
            foreground_hwnd = int(user32.GetForegroundWindow() or 0)
            overlay_hwnd = int((window or self).winId())
            styles = int(user32.GetWindowLongW(overlay_hwnd, GWL_EXSTYLE))
            self.log_ui_event(
                event,
                (
                    f"target_hwnd={target_hwnd}; foreground_hwnd={foreground_hwnd}; "
                    f"overlay_hwnd={overlay_hwnd}; state={state}; "
                    f"no_activate={bool(styles & WS_EX_NOACTIVATE)}; "
                    f"click_through={bool(styles & WS_EX_TRANSPARENT)}; "
                    f"restore_attempted={bool(restore_attempted)}; "
                    f"restore_result={bool(restore_result)}"
                ),
            )
        except Exception as exc:
            self.log_ui_event(event, f"state={state}; diagnostic_error={type(exc).__name__}:{exc}")

    def focus_game_window(self, hwnd, reason=""):
        """Restore game foreground only when one of our interactive windows owns it."""
        attempted = False
        restored = False
        try:
            hwnd = int(hwnd or 0)
            if not hwnd:
                return False
            foreground = int(user32.GetForegroundWindow() or 0)
            if foreground == hwnd:
                restored = True
                return True
            if foreground not in self._app_owned_window_handles():
                return False
            attempted = True
            user32.ShowWindow(hwnd, SW_RESTORE)
            restored = bool(user32.SetForegroundWindow(hwnd))
            return restored
        except Exception as e:
            print(f"[FOCUS ERROR]: {e}")
            return False
        finally:
            self.log_window_ownership_transition(
                "WINDOW_OWNERSHIP_FOCUS_RESTORE",
                reason or "restore_game",
                restore_attempted=attempted,
                restore_result=restored,
            )

    def apply_gameplay_window_style(self):
        hwnd = int(self.winId())
        styles = int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE))
        styles |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, styles)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER,
        )
        return styles

    def apply_edit_window_style(self):
        hwnd = int(self.winId())
        styles = int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE))
        styles &= ~(WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
        styles |= WS_EX_LAYERED | WS_EX_TOOLWINDOW
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, styles)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        return styles

    def log_live_mouse_transition(self, event, *, previous_mode, new_mode, reason, **details):
        payload = {
            "previous_mode": previous_mode.value if isinstance(previous_mode, LiveOverlayMouseMode) else "",
            "new_mode": new_mode.value if isinstance(new_mode, LiveOverlayMouseMode) else "",
            "target_hwnd": int(details.pop("target_hwnd", 0) or 0),
            "reason": str(reason or ""),
        }
        payload.update(details)
        self.log_ui_event(
            event,
            json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
        )

    def release_live_overlay_capture(self, *, previous_mode, new_mode, reason):
        capture_before = 0
        capture_after = 0
        released = False
        try:
            overlay_hwnd = int(self.winId())
            capture_before = int(user32.GetCapture() or 0)
            if capture_before == overlay_hwnd:
                released = bool(user32.ReleaseCapture())
            capture_after = int(user32.GetCapture() or 0)
        except Exception as exc:
            self.log_live_mouse_transition(
                "LIVE_MOUSE_TRANSITION_FAILED",
                previous_mode=previous_mode,
                new_mode=new_mode,
                reason=reason,
                stage="release_capture",
                error=f"{type(exc).__name__}:{exc}",
            )
        if capture_before:
            self.log_live_mouse_transition(
                "LIVE_MOUSE_CAPTURE_RELEASED",
                previous_mode=previous_mode,
                new_mode=new_mode,
                reason=reason,
                capture_before=capture_before,
                capture_after=capture_after,
                released=released,
            )
        return {
            "capture_before": capture_before,
            "capture_after": capture_after,
            "released": released,
        }

    def initialize_live_mouse_mode(self):
        self.enter_live_edit_mode("startup")

    def enter_live_edit_mode(self, reason=""):
        previous_mode = self.live_mouse_mode
        new_mode = LiveOverlayMouseMode.EDIT
        target_hwnd = int(self.resolve_target_hwnd() or 0)
        target_valid = self.is_valid_target_window(target_hwnd)
        before = self.cursor_diagnostic_snapshot(target_hwnd)
        saved_clip = live_game_clip_from_snapshot(
            before,
            target_valid=target_valid,
            session_stopping=self._session_stopping,
        )

        self._live_mouse_transition_seq += 1
        self._live_edit_saved_game_clip = list(saved_clip) if saved_clip else None
        self._live_edit_saved_target_hwnd = target_hwnd if saved_clip else 0
        capture = self.release_live_overlay_capture(
            previous_mode=previous_mode,
            new_mode=new_mode,
            reason=reason,
        )

        clip_released = False
        clip_error = 0
        if saved_clip:
            try:
                ctypes.set_last_error(0)
                clip_released = bool(user32.ClipCursor(None))
                clip_error = int(ctypes.get_last_error())
            except Exception as exc:
                clip_error = f"{type(exc).__name__}:{exc}"
                self.log_live_mouse_transition(
                    "LIVE_MOUSE_TRANSITION_FAILED",
                    previous_mode=previous_mode,
                    new_mode=new_mode,
                    reason=reason,
                    target_hwnd=target_hwnd,
                    stage="release_game_clip",
                    error=clip_error,
                )
            self.log_live_mouse_transition(
                "LIVE_MOUSE_GAME_CLIP_SAVED",
                previous_mode=previous_mode,
                new_mode=new_mode,
                reason=reason,
                target_hwnd=target_hwnd,
                foreground_before=int(before.get("foreground_hwnd") or 0),
                saved_clip=saved_clip,
                clip_released=clip_released,
                win32_error=clip_error,
            )

        self.live_mouse_mode = new_mode
        styles = 0
        try:
            styles = int(self.apply_edit_window_style())
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.show()
            self.raise_()
            self.activateWindow()
            self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        except Exception as exc:
            self.log_live_mouse_transition(
                "LIVE_MOUSE_TRANSITION_FAILED",
                previous_mode=previous_mode,
                new_mode=new_mode,
                reason=reason,
                target_hwnd=target_hwnd,
                stage="apply_edit_window",
                error=f"{type(exc).__name__}:{exc}",
            )

        cursor_result = self.center_cursor_on_setup_area()
        try:
            foreground_after = int(user32.GetForegroundWindow() or 0)
        except Exception:
            foreground_after = 0
        self.log_live_mouse_transition(
            "LIVE_MOUSE_MODE_ENTER_EDIT",
            previous_mode=previous_mode,
            new_mode=new_mode,
            reason=reason,
            target_hwnd=target_hwnd,
            foreground_before=int(before.get("foreground_hwnd") or 0),
            foreground_after=foreground_after,
            saved_clip=saved_clip,
            capture_before=capture["capture_before"],
            capture_after=capture["capture_after"],
            overlay_styles=styles,
            cursor_position=cursor_result.get("position"),
            cursor_positioned=cursor_result.get("positioned"),
        )
        return True

    def enter_live_gameplay_mode(self, target_hwnd, reason=""):
        previous_mode = self.live_mouse_mode
        new_mode = LiveOverlayMouseMode.GAMEPLAY
        target_hwnd = int(target_hwnd or 0)
        target_valid = self.is_valid_target_window(target_hwnd)
        self._live_mouse_transition_seq += 1
        self.drag_position = None
        capture = self.release_live_overlay_capture(
            previous_mode=previous_mode,
            new_mode=new_mode,
            reason=reason,
        )

        try:
            focus_widget = QApplication.focusWidget()
            if focus_widget is not None and (focus_widget is self or self.isAncestorOf(focus_widget)):
                focus_widget.clearFocus()
            self.clearFocus()
        except Exception:
            pass

        self.live_mouse_mode = new_mode
        styles = 0
        try:
            styles = int(self.apply_gameplay_window_style())
        except Exception as exc:
            self.log_live_mouse_transition(
                "LIVE_MOUSE_TRANSITION_FAILED",
                previous_mode=previous_mode,
                new_mode=new_mode,
                reason=reason,
                target_hwnd=target_hwnd,
                stage="apply_gameplay_window",
                error=f"{type(exc).__name__}:{exc}",
            )

        try:
            foreground_before = int(user32.GetForegroundWindow() or 0)
        except Exception:
            foreground_before = 0
        foreground_attempted = False
        foreground_result = False
        if target_valid and not self._session_stopping:
            foreground_attempted = True
            try:
                user32.ShowWindow(target_hwnd, SW_RESTORE)
                foreground_result = bool(user32.SetForegroundWindow(target_hwnd))
            except Exception as exc:
                self.log_live_mouse_transition(
                    "LIVE_MOUSE_TRANSITION_FAILED",
                    previous_mode=previous_mode,
                    new_mode=new_mode,
                    reason=reason,
                    target_hwnd=target_hwnd,
                    stage="restore_foreground",
                    error=f"{type(exc).__name__}:{exc}",
                )
        try:
            foreground_after = int(user32.GetForegroundWindow() or 0)
        except Exception:
            foreground_after = 0
        self.log_live_mouse_transition(
            "LIVE_MOUSE_FOREGROUND_RESTORE",
            previous_mode=previous_mode,
            new_mode=new_mode,
            reason=reason,
            target_hwnd=target_hwnd,
            foreground_before=foreground_before,
            foreground_after=foreground_after,
            attempted=foreground_attempted,
            result=foreground_result,
        )

        diagnostic = self.cursor_diagnostic_snapshot(target_hwnd)
        saved_clip = (
            list(self._live_edit_saved_game_clip)
            if self._live_edit_saved_game_clip is not None
            and self._live_edit_saved_target_hwnd == target_hwnd
            else None
        )
        restore_decision = live_game_clip_restore_decision(
            previous_mode=previous_mode,
            new_mode=new_mode,
            saved_clip=saved_clip,
            target_valid=target_valid,
            session_stopping=self._session_stopping,
            foreground_matches_target=foreground_after == target_hwnd,
            virtual_rect=diagnostic.get("virtual_screen_rect"),
            game_rect=diagnostic.get("game_client_rect"),
        )
        if restore_decision["restore"]:
            rect = wintypes.RECT(*saved_clip)
            try:
                ctypes.set_last_error(0)
                clip_result = bool(user32.ClipCursor(ctypes.byref(rect)))
                clip_error = int(ctypes.get_last_error())
            except Exception as exc:
                clip_result = False
                clip_error = f"{type(exc).__name__}:{exc}"
            self.log_live_mouse_transition(
                "LIVE_MOUSE_GAME_CLIP_RESTORED" if clip_result else "LIVE_MOUSE_TRANSITION_FAILED",
                previous_mode=previous_mode,
                new_mode=new_mode,
                reason=reason,
                target_hwnd=target_hwnd,
                saved_clip=saved_clip,
                restored_clip=saved_clip if clip_result else None,
                stage="restore_game_clip",
                win32_result=clip_result,
                win32_error=clip_error,
            )
        else:
            self.log_live_mouse_transition(
                "LIVE_MOUSE_GAME_CLIP_RESTORE_SKIPPED",
                previous_mode=previous_mode,
                new_mode=new_mode,
                reason=reason,
                target_hwnd=target_hwnd,
                saved_clip=saved_clip,
                restore_reason=restore_decision["reason"],
            )

        self._live_edit_saved_game_clip = None
        self._live_edit_saved_target_hwnd = 0
        self.log_live_mouse_transition(
            "LIVE_MOUSE_MODE_ENTER_GAMEPLAY",
            previous_mode=previous_mode,
            new_mode=new_mode,
            reason=reason,
            target_hwnd=target_hwnd,
            foreground_before=foreground_before,
            foreground_after=foreground_after,
            saved_clip=saved_clip,
            capture_before=capture["capture_before"],
            capture_after=capture["capture_after"],
            overlay_styles=styles,
        )
        return True

    def sync_geometry_to_session(self):
        """
        Синхронизация геометрии оверлея с session_config.

        Критически важное разделение:
        - left/top — абсолютная позиция оверлея на экране;
        - left_offset/top_offset — позиция области захвата внутри окна игры.

        Именно это исправляет проблему, когда в OCR попадала не та область
        или debug_ocr.png/debug_capture показывал белое поле/полосы.
        """
        geom = self.geometry()
        zone = self.session_config.setdefault("monitor_zone", {})

        # Абсолютные координаты оверлея на экране — нужны для восстановления UI.
        zone["left"] = int(geom.left())
        zone["top"] = int(geom.top())
        zone["width"] = max(64, int(geom.width()))
        zone["height"] = max(48, int(geom.height()))

        hwnd = self.target_hwnd or self.resolve_target_hwnd()
        if hwnd and win32gui and win32gui.IsWindow(hwnd):
            game_left, game_top, game_right, game_bottom = win32gui.GetWindowRect(hwnd)

            offset_x = int(geom.left() - game_left)
            offset_y = int(geom.top() - game_top)

            # Относительные координаты внутри окна игры — нужны для Rust/GDI crop.
            zone["left_offset"] = max(0, offset_x)
            zone["top_offset"] = max(0, offset_y)

            game_w = max(1, int(game_right - game_left))
            game_h = max(1, int(game_bottom - game_top))

            # Не даём кропу выйти за границы окна игры.
            zone["width"] = max(1, min(max(64, zone["width"]), game_w - zone["left_offset"]))
            zone["height"] = max(1, min(max(48, zone["height"]), game_h - zone["top_offset"]))

        if hasattr(self, "engine_thread"):
            self.engine_thread.update_session_data(self.session_config)

    def save_profile_to_disk(self):
        """Сохранение текущей геометрии и настроек оверлея в профиль игры.

        Step14: единый источник профиля — profiles/<game>/profile.json.
        Оверлей больше не создаёт старый файл profiles/<game>_profile.json.
        Если старый legacy-файл остался после прошлых версий, удаляем его после
        успешного сохранения нового profile.json.
        """
        self.sync_geometry_to_session()

        game_name = self.session_config.get("game_name", "")
        if not game_name:
            return

        os.makedirs(PROFILES_DIR, exist_ok=True)
        safe_name = self.safe_profile_name(game_name)

        # Основной путь приходит от лаунчера через session_config. Если вдруг
        # запустили оверлей старым session_config, строим новый путь сами.
        profile_path = self.session_config.get("profile_path")
        profile_dir = self.session_config.get("profile_dir")

        if not profile_path:
            profile_dir = os.path.join(PROFILES_DIR, safe_name)
            profile_path = os.path.join(profile_dir, "profile.json")

        if not profile_dir:
            profile_dir = os.path.dirname(profile_path)

        os.makedirs(profile_dir, exist_ok=True)

        # Держим пути в session_config актуальными, чтобы все последующие модули
        # писали файлы в одну папку игры.
        self.session_config["profile_dir"] = profile_dir
        self.session_config["profile_path"] = profile_path

        block_id = block_id_for_language_mode(
            self.session_config.get("language_mode")
            or (self.session_config.get("translation", {}) or {}).get("language_mode")
        )
        self.session_config, _migration = ensure_language_blocks(self.session_config)
        self.session_config = replace_block_state(
            self.session_config,
            block_id,
            {
                "translation": self.session_config.get("translation", {}),
                "monitor_zone": self.session_config.get("monitor_zone", {}),
                "dynamic": self.session_config.get("dynamic", {}),
                "ocr": self.session_config.get("ocr", {}),
                "subtitles": self.session_config.get("subtitles", {}),
                "visual_gate": self.session_config.get("visual_gate", {}),
            },
            make_active=True,
        )
        profile_payload = dict(self.session_config)
        profile_payload.pop("quality_runtime_selection", None)
        atomic_write_json(profile_path, profile_payload)

        # Удаляем старый legacy-файл, если он был создан прошлыми версиями
        # или старым кодом оверлея.
        legacy_profile_path = os.path.join(PROFILES_DIR, f"{safe_name}_profile.json")
        try:
            if os.path.abspath(legacy_profile_path) != os.path.abspath(profile_path) and os.path.exists(legacy_profile_path):
                os.remove(legacy_profile_path)
                print(f"[ПРОФИЛЬ] Удалён старый legacy-профиль: {legacy_profile_path}")
        except Exception as e:
            print(f"[ПРОФИЛЬ] Не удалось удалить legacy-профиль: {e}")

        # Также обновляем временный session_config, пока лаунчер ещё не удалил его.
        atomic_write_json(self.session_file, self.session_config)

        print(f"[ПРОФИЛЬ] Сохранён: {profile_path}")

    def start_model_warmup(self):
        log_startup_event("START_MODEL_WARMUP_ENTER")
        """
        Предзагрузка модели в оверлее.

        Для online-режима локальную модель загружать не нужно.
        Для offline-режима используем отдельный QThread.

        Важная правка v2:
        в предыдущей версии я запускал self.engine_thread.async_load_model через обычный
        threading.Thread. Это было лишним риском, потому что метод принадлежит Qt-объекту.
        Здесь загрузка вынесена в отдельный чистый QThread OverlayModelWarmUpThread.
        """
        if self.mode_selector.currentText() != "offline":
            if self._startup_paddle_waiting_for_model:
                self._startup_paddle_waiting_for_model = False
                self.start_paddle_ocr_prepare("online_mode_selected")
            from online_translation import validate_configuration, OnlineError
            try:
                context = translation_request_context_for_session(self.session_config)
                validate_configuration(context.get("online"), context["source_language"], context["target_language"])
                self._translation_model_ready_for_hint = True
                self._translation_model_error_for_hint = ""
            except OnlineError as exc:
                self._translation_model_ready_for_hint = False
                self._translation_model_error_for_hint = tr(self.interface_lang, "online_error_" + exc.code)
                self.lbl_translated.setText(self._translation_model_error_for_hint)
                return
            self.lbl_translated.setText(
                tr(self.interface_lang, "overlay_online_ready", hotkey=self.get_hotkey_display("toggle_translation_mode"))
            )
            return

        readiness_requirement = block3_translator_readiness_requirement(self.session_config)
        readiness_key = str(readiness_requirement.get("translator_readiness_key") or "")
        if readiness_key and readiness_key in self._block3_translator_ready_keys:
            self._block3_pending_warmup_key = ""
            self.update_block3_pipeline_readiness("translator_readiness_reused")
            return

        if self.model_warmup_thread and self.model_warmup_thread.isRunning():
            if is_block2_session(self.session_config):
                self._translation_model_ready_for_hint = False
                self._translation_model_error_for_hint = ""
                if not self._block2_pending_model_warmup:
                    self._block2_pending_model_warmup = True
                    self.model_warmup_thread.finished.connect(self.start_pending_block2_model_warmup)
            running_key = str(
                getattr(self.model_warmup_thread, "block3_readiness_key", "") or ""
            )
            if readiness_key and readiness_key != running_key:
                self._block3_pending_warmup_key = readiness_key
            self.update_block3_pipeline_readiness("translator_warmup_already_running")
            return

        backend_name = current_translation_backend_from_session(self.session_config)
        if backend_name == "translategemma_llama_q6_speed":
            status_key = "overlay_translategemma_llama_q6_speed_loading"
        elif backend_name == "translategemma_llama_q6_low_vram":
            status_key = "overlay_translategemma_llama_q6_loading"
        else:
            status_key = "overlay_init_model"
        self.on_model_warmup_status(tr(self.interface_lang, status_key))

        target_lang = self.current_overlay_target_lang()
        source_lang = str((self.session_config.get("translation", {}) or {}).get("source_lang", "en"))
        self.model_warmup_thread = OverlayModelWarmUpThread(
            target_lang,
            self.interface_lang,
            backend_name,
            source_lang,
            translation_request_context_for_session(
                self.session_config,
                source_text="保存" if source_lang == "zh" else "セーブ" if source_lang == "ja" else "",
                runtime_mode="dynamic" if self.dynamic_only_mode else "overlay",
            ),
        )
        self.model_warmup_thread.block3_readiness_key = readiness_key
        self.model_warmup_thread.block3_translator_backend = str(
            readiness_requirement.get("translator_backend") or ""
        )
        if readiness_key:
            self._block3_pending_warmup_key = readiness_key
            self._block3_translator_errors.pop(readiness_key, None)
        else:
            self._translation_model_ready_for_hint = False
            self._translation_model_error_for_hint = ""
        self.model_warmup_thread.status_signal.connect(self.on_model_warmup_status)
        self.model_warmup_thread.ready_signal.connect(self.on_overlay_warmup_finished)
        if backend_name in {
            "translategemma_llama_q6_low_vram",
            "translategemma_llama_q6_speed",
        }:
            log_startup_event("TRANSLATEGEMMA_WARMUP_SCHEDULED", backend=backend_name)
        self.model_warmup_thread.start()
        log_startup_event("START_MODEL_WARMUP_THREAD_STARTED")
        self.update_block3_pipeline_readiness("translator_warmup_started")

    def start_pending_block2_model_warmup(self):
        if not self._block2_pending_model_warmup:
            return
        self._block2_pending_model_warmup = False
        if not self._session_stopping:
            self.start_model_warmup()

    @pyqtSlot(bool, str)
    def on_overlay_warmup_finished(self, ok, message):
        """Обновление UI после прогрева модели внутри оверлея."""
        completed_thread = self.sender() or self.model_warmup_thread
        if is_block2_session(self.session_config) and (
                self._block2_pending_model_warmup or completed_thread is not self.model_warmup_thread):
            # The finished signal starts warmup for the latest selected route.
            log_startup_event("MODEL_WARMUP_FINISHED", ok=ok, stale=True)
            return
        self.model_preparation_timer.stop()
        self.model_preparation_status_label.hide()
        log_startup_event("MODEL_WARMUP_FINISHED", ok=ok)
        selection = self.session_config.get("quality_runtime_selection")
        if isinstance(selection, dict) and completed_thread is not None:
            backend = str(getattr(completed_thread, "backend_name", "") or "")
            context = getattr(completed_thread, "request_context", {}) or {}
            if backend in {"translategemma_llama_q6_low_vram", "translategemma_llama_q6_speed"} or (
                backend in {"block2_router", "block3_router"}
                and context.get("translation_profile") == "quality"
            ):
                selection["readiness_result"] = "ready" if ok else "failed"
                selection["readiness_error"] = None if ok else str(message or "model_warmup_failed")
                if not ok:
                    selection["reason"] = "runtime_or_model_readiness_failure"
                    selection["available"] = False
                translator_core.mark_quality_runtime_readiness(bool(ok), str(message or ""))
                try:
                    log_quality_runtime_selection(selection, "overlay_quality_readiness")
                except OSError:
                    pass
        completed_readiness_key = str(
            getattr(completed_thread, "block3_readiness_key", "") or ""
        )
        if completed_readiness_key:
            if ok:
                self._block3_translator_ready_keys.add(completed_readiness_key)
                self._block3_translator_errors.pop(completed_readiness_key, None)
            else:
                self._block3_translator_ready_keys.discard(completed_readiness_key)
                self._block3_translator_errors[completed_readiness_key] = str(message or "")
            if self._block3_pending_warmup_key == completed_readiness_key:
                self._block3_pending_warmup_key = ""
        if self._startup_paddle_waiting_for_model:
            QTimer.singleShot(0, self._start_deferred_startup_paddle)
        if self._startup_diagnostic_sequence is not None:
            if not ok:
                startup_block3_diagnostics.emit_startup_diagnostic(
                    "CSANMT_START_ERROR",
                    error=str(message or "model_warmup_failed")[:500],
                )
            self._finish_startup_diagnostic_component("csanmt", bool(ok))
        current_requirement = block3_translator_readiness_requirement(self.session_config)
        if self.mode_selector.currentText() == "online":
            # An already-running offline warmup may complete after a mode switch.
            # Keep its diagnostics, but do not overwrite online readiness/result UI.
            if not self._session_stopping:
                self.start_model_warmup()
            return
        if current_requirement.get("applies"):
            current_key = str(current_requirement.get("translator_readiness_key") or "")
            is_current_result = bool(current_key and current_key == completed_readiness_key)
            self.update_block3_pipeline_readiness(
                "translator_ready" if ok else "translator_error",
                refresh_ui=bool(ok or not is_current_result),
            )
            if not ok and is_current_result and self.live_mouse_mode != LiveOverlayMouseMode.GAMEPLAY:
                self.lbl_translated.setText(message)
            if current_key and current_key not in self._block3_translator_ready_keys:
                if current_key != completed_readiness_key:
                    self._block3_pending_warmup_key = current_key
                    QTimer.singleShot(0, self.start_model_warmup)
            return

        self._translation_model_ready_for_hint = bool(ok)
        self._translation_model_error_for_hint = "" if ok else str(message or "")

        if self.live_mouse_mode != LiveOverlayMouseMode.GAMEPLAY:
            if ok:
                ocr_prepare_key = self.current_ocr_prepare_status_key()
                if is_block2_session(self.session_config) or not ocr_prepare_key:
                    self.set_idle_translation_hint("model_warmup_finished")
                else:
                    self.set_ocr_prepare_status_text(ocr_prepare_key)
            else:
                self.lbl_translated.setText(message)


    def selected_ocr_backend_name(self):
        try:
            return current_ocr_backend_from_session(self.session_config)
        except Exception:
            return "tesseract"

    def current_block3_pipeline_readiness(self):
        return block3_pipeline_readiness_snapshot(
            self.session_config,
            paddle_prepare_state=self.paddle_ocr_prepare_state,
            paddle_prepare_backend=self.paddle_ocr_prepare_backend,
            translator_ready_keys=self._block3_translator_ready_keys,
        )

    def current_pipeline_hint_readiness(self):
        """Combine the selected OCR and translator readiness for the idle hint."""
        block3 = self.current_block3_pipeline_readiness()
        if block3.get("applies"):
            return block3

        ocr_backend = self.selected_ocr_backend_name()
        ocr_ready = bool(
            self._selected_ocr_ready_for_hint
            if not is_paddle_family_backend(ocr_backend)
            else (
                self.paddle_ocr_prepare_state == "ready"
                and self.paddle_ocr_prepare_backend == ocr_backend
            )
        )
        translator_backend = current_translation_backend_from_session(self.session_config)
        block = 2 if translator_backend == "block2_router" else 1
        if translator_backend == "online":
            block = 3 if is_block3_session(self.session_config) else 2 if is_block2_session(self.session_config) else 1
        translator_ready = bool(self._translation_model_ready_for_hint)
        return {
            "applies": True,
            "block": block,
            "mode": str((self.session_config.get("translation", {}) or {}).get("translation_profile") or ""),
            "target": self.current_overlay_target_lang(),
            "ocr_backend": ocr_backend,
            "translator_backend": translator_backend,
            "translator_readiness_key": "",
            "ocr_ready": ocr_ready,
            "translator_ready": translator_ready,
            "pipeline_ready": bool(ocr_ready and translator_ready),
        }

    @staticmethod
    def block3_readiness_details(snapshot, reason=""):
        return (
            f"block={snapshot.get('block', 0)}; mode={snapshot.get('mode', '')}; "
            f"target={snapshot.get('target', '')}; ocr_backend={snapshot.get('ocr_backend', '')}; "
            f"translator_backend={snapshot.get('translator_backend', '')}; "
            f"ocr_ready={bool(snapshot.get('ocr_ready'))}; "
            f"translator_ready={bool(snapshot.get('translator_ready'))}; "
            f"pipeline_ready={bool(snapshot.get('pipeline_ready'))}; reason={reason}"
        )

    def set_block3_pipeline_preparing_status(self, snapshot, reason=""):
        if (
            not snapshot.get("applies")
            or self.dynamic_only_mode
            or not hasattr(self, "lbl_translated")
            or self.is_click_through
        ):
            return
        readiness_key = str(snapshot.get("translator_readiness_key") or "")
        translator_error = str(self._block3_translator_errors.get(readiness_key) or "")
        if translator_error:
            self.lbl_translated.setText(translator_error)
            return
        if not snapshot.get("ocr_ready") and self.paddle_ocr_prepare_state == "error":
            status_key = self.current_ocr_prepare_status_key()
            if status_key:
                self.set_ocr_prepare_status_text(status_key)
                return
        if not snapshot.get("ocr_ready") and not snapshot.get("translator_ready"):
            status_key = "overlay_pipeline_preparing"
        elif not snapshot.get("ocr_ready"):
            status_key = "ocr_prepare_loading"
        else:
            status_key = "overlay_init_model"
        self.lbl_translated.setText(tr(self.interface_lang, status_key))
        self.log_ui_event(
            "OVERLAY_PREPARE_STATUS_SET",
            f"status_key={status_key}; {self.block3_readiness_details(snapshot, reason)}",
        )

    def update_block3_pipeline_readiness(self, reason="", refresh_ui=True):
        snapshot = self.current_block3_pipeline_readiness()
        if not snapshot.get("applies") or self.dynamic_only_mode:
            return snapshot

        signature = (
            snapshot.get("mode"),
            snapshot.get("target"),
            snapshot.get("ocr_backend"),
            snapshot.get("translator_readiness_key"),
            bool(snapshot.get("ocr_ready")),
            bool(snapshot.get("translator_ready")),
            bool(snapshot.get("pipeline_ready")),
        )
        details = self.block3_readiness_details(snapshot, reason)
        if signature != self._block3_readiness_log_signature:
            self._block3_readiness_log_signature = signature
            self.log_ui_event("PIPELINE_READINESS_CHANGED", details)
            event_details = {
                "block": snapshot.get("block"),
                "mode": snapshot.get("mode"),
                "target": snapshot.get("target"),
                "ocr_backend": snapshot.get("ocr_backend"),
                "translator_backend": snapshot.get("translator_backend"),
                "required_ocr_ready": bool(snapshot.get("ocr_ready")),
                "required_translator_ready": bool(snapshot.get("translator_ready")),
                "ocr_ready": bool(snapshot.get("ocr_ready")),
                "translator_ready": bool(snapshot.get("translator_ready")),
                "pipeline_ready": bool(snapshot.get("pipeline_ready")),
                "reason": str(reason or ""),
            }
            log_startup_event("PIPELINE_READINESS_CHANGED", **event_details)
            startup_block3_diagnostics.emit_startup_diagnostic(
                "PIPELINE_READINESS_CHANGED",
                **event_details,
            )

        if snapshot.get("pipeline_ready"):
            if signature != self._block3_live_enabled_signature:
                self._block3_live_enabled_signature = signature
                self.log_ui_event("LIVE_HOTKEY_ENABLED", details)
                startup_block3_diagnostics.emit_startup_diagnostic(
                    "LIVE_HOTKEY_ENABLED",
                    block=snapshot.get("block"),
                    mode=snapshot.get("mode"),
                    target=snapshot.get("target"),
                    ocr_backend=snapshot.get("ocr_backend"),
                    translator_backend=snapshot.get("translator_backend"),
                    ocr_ready=True,
                    translator_ready=True,
                    pipeline_ready=True,
                    reason=str(reason or ""),
                )
            if refresh_ui and self.live_mouse_mode == LiveOverlayMouseMode.EDIT:
                self.set_idle_translation_hint(reason or "pipeline_ready")
        else:
            self._block3_live_enabled_signature = None
            self._block3_ready_hint_signature = None
            if refresh_ui:
                self.set_block3_pipeline_preparing_status(snapshot, reason)
        return snapshot


    def set_ocr_prepare_status_text(self, key):
        if hasattr(self, "lbl_translated") and not self.is_click_through:
            self.lbl_translated.setText(tr(self.interface_lang, key))
            backend = self.selected_ocr_backend_name()
            hotkey = self.get_hotkey_display("toggle_translation_mode")
            details = f"status_key={key}; hotkey={hotkey}; translation_mode_on={self.is_click_through}; backend={backend}"
            self.log_ui_event("OVERLAY_PREPARE_STATUS_SET", details)
            log_paddle_ocr_event(
                "OVERLAY_PREPARE_STATUS_SET",
                status_key=key,
                hotkey=hotkey,
                translation_mode_on=self.is_click_through,
                backend=backend,
            )

    def set_idle_translation_hint(self, reason=""):
        if not hasattr(self, "lbl_translated") or self.is_click_through:
            return
        readiness = GameTranslatorOverlay.current_pipeline_hint_readiness(self)
        if not self.dynamic_only_mode and not readiness.get("pipeline_ready"):
            if readiness.get("translator_backend") == "online" and self._translation_model_error_for_hint:
                self.lbl_translated.setText(self._translation_model_error_for_hint)
            elif int(readiness.get("block", 0) or 0) == 3:
                self.set_block3_pipeline_preparing_status(readiness, reason)
            elif int(readiness.get("block", 0) or 0) == 2 and getattr(self, "_translation_model_error_for_hint", ""):
                self.lbl_translated.setText(self._translation_model_error_for_hint)
            elif not readiness.get("ocr_ready"):
                status_key = self.current_ocr_prepare_status_key()
                if status_key:
                    self.set_ocr_prepare_status_text(status_key)
            elif int(readiness.get("block", 0) or 0) == 2 and not readiness.get("translator_ready"):
                self.lbl_translated.setText(tr(self.interface_lang, "overlay_init_model"))
            return
        hotkey = self.get_hotkey_display("toggle_translation_mode")
        self.lbl_translated.setText(tr(self.interface_lang, "overlay_hint_press_translate_hotkey", hotkey=hotkey))
        backend = self.selected_ocr_backend_name()
        details = (
            f"hint_key=overlay_hint_press_translate_hotkey; hotkey={hotkey}; "
            f"translation_mode_on={self.is_click_through}; backend={backend}; "
            f"ocr_ready={bool(readiness.get('ocr_ready'))}; "
            f"translator_ready={bool(readiness.get('translator_ready'))}; "
            f"pipeline_ready={bool(readiness.get('pipeline_ready'))}; reason={reason}"
        )
        if int(readiness.get("block", 0) or 0) == 3:
            hint_signature = (
                readiness.get("mode"),
                readiness.get("target"),
                readiness.get("ocr_backend"),
                readiness.get("translator_readiness_key"),
            )
            if hint_signature != self._block3_ready_hint_signature:
                self._block3_ready_hint_signature = hint_signature
                ready_details = self.block3_readiness_details(readiness, reason)
                self.log_ui_event("UI_TRANSLATION_READY_HINT_SHOWN", ready_details)
                startup_block3_diagnostics.emit_startup_diagnostic(
                    "UI_TRANSLATION_READY_HINT_SHOWN",
                    reason=str(reason or ""),
                    hotkey=hotkey,
                    block=readiness.get("block"),
                    mode=readiness.get("mode"),
                    target=readiness.get("target"),
                    ocr_backend=readiness.get("ocr_backend"),
                    translator_backend=readiness.get("translator_backend"),
                    ocr_ready=True,
                    translator_ready=True,
                    pipeline_ready=True,
                )
        else:
            startup_block3_diagnostics.emit_startup_diagnostic(
                "UI_TRANSLATION_READY_HINT_SHOWN",
                reason=str(reason or ""),
                hotkey=hotkey,
                block=readiness.get("block"),
                ocr_backend=readiness.get("ocr_backend"),
                translator_backend=readiness.get("translator_backend"),
                ocr_ready=bool(readiness.get("ocr_ready")),
                translator_ready=bool(readiness.get("translator_ready")),
                pipeline_ready=bool(readiness.get("pipeline_ready")),
            )
        self.log_ui_event("OVERLAY_HINT_SET", details)
        log_paddle_ocr_event(
            "OVERLAY_HINT_SET",
            hint_key="overlay_hint_press_translate_hotkey",
            hotkey=hotkey,
            translation_mode_on=self.is_click_through,
            backend=backend,
            reason=reason,
        )

    def restore_idle_hint_after_prepare(self):
        if self.is_click_through:
            return
        key = self.current_ocr_prepare_status_key()
        if key in {"ocr_prepare_loading", "ocr_prepare_error", "ocr_prepare_missing_model", "ocr_prepare_download_required"}:
            return
        self.set_idle_translation_hint("after_ocr_prepare")
        if (is_block2_session(self.session_config) and not self.dynamic_only_mode
                and not self.current_pipeline_hint_readiness().get("pipeline_ready")):
            return
        self.log_ui_event(
            "OVERLAY_HINT_RESTORED_AFTER_PREPARE",
            f"hint_key=overlay_hint_press_translate_hotkey; hotkey={self.get_hotkey_display('toggle_translation_mode')}; translation_mode_on={self.is_click_through}; backend={self.selected_ocr_backend_name()}",
        )
        log_paddle_ocr_event(
            "OVERLAY_HINT_RESTORED_AFTER_PREPARE",
            hint_key="overlay_hint_press_translate_hotkey",
            hotkey=self.get_hotkey_display("toggle_translation_mode"),
            translation_mode_on=self.is_click_through,
            backend=self.selected_ocr_backend_name(),
        )

    def current_ocr_prepare_status_key(self):
        selected_backend = self.selected_ocr_backend_name()
        if not is_paddle_family_backend(selected_backend):
            return ""
        if self.paddle_ocr_prepare_state == "running":
            return "ocr_prepare_loading"
        if self.paddle_ocr_prepare_backend != selected_backend:
            return ""
        if self.paddle_ocr_prepare_state == "ready":
            return "ocr_prepare_ready"
        if self.paddle_ocr_prepare_state == "error":
            error_text = self.paddle_ocr_prepare_error or ""
            if "PADDLE_OCR_MODEL_DOWNLOAD_REQUIRED" in error_text:
                return "ocr_prepare_download_required"
            if "PADDLE_OCR_MODEL_MISSING" in error_text:
                return "ocr_prepare_missing_model"
            return "ocr_prepare_error"
        return ""

    def start_paddle_ocr_prepare(self, reason="overlay_startup"):
        """Preload selected Paddle-family backend before the first real live OCR."""
        backend_name = self.selected_ocr_backend_name()
        if not is_paddle_family_backend(backend_name):
            self.paddle_ocr_prepare_state = "not_needed"
            self.paddle_ocr_prepare_backend = ""
            self._selected_ocr_ready_for_hint = True
            self.engine_thread.set_live_paddle_worker(None)
            self.update_block3_pipeline_readiness("ocr_not_required")
            if self.live_mouse_mode == LiveOverlayMouseMode.EDIT:
                self.set_idle_translation_hint("ocr_not_required")
            return False
        if self.paddle_ocr_prepare_state == "ready" and self.paddle_ocr_prepare_backend == backend_name:
            if uses_persistent_live_paddle_worker(backend_name):
                self.engine_thread.set_live_paddle_worker(self.paddle_ocr_prepare_thread)
            self.update_block3_pipeline_readiness("ocr_readiness_reused")
            if is_block2_session(self.session_config):
                self.restore_idle_hint_after_prepare()
            return True
        if self.paddle_ocr_prepare_state == "error" and self.paddle_ocr_prepare_backend == backend_name:
            return False
        if self.paddle_ocr_prepare_thread is not None and self.paddle_ocr_prepare_thread.isRunning():
            current_worker = self.paddle_ocr_prepare_thread
            same_backend = str(getattr(current_worker, "backend_name", "") or "") == backend_name
            if same_backend:
                if uses_persistent_live_paddle_worker(backend_name):
                    self.engine_thread.set_live_paddle_worker(current_worker)
                self.set_ocr_prepare_status_text("ocr_prepare_loading")
                self.update_block3_pipeline_readiness("ocr_prepare_already_running")
                return True
            if hasattr(current_worker, "stop_worker"):
                current_worker.stop_worker("ocr_backend_changed")
                current_worker.wait(30000)
            elif current_worker.isRunning():
                self.set_ocr_prepare_status_text("ocr_prepare_loading")
                return True

        trans = self.session_config.get("translation", {}) or {}
        source_lang = str(trans.get("source_lang", "en") or "en")
        self.paddle_ocr_prepare_state = "running"
        self.paddle_ocr_prepare_backend = backend_name
        self._selected_ocr_ready_for_hint = False
        self.paddle_ocr_prepare_error = ""
        self.paddle_ocr_prepare_elapsed_ms = 0

        try:
            log_startup_event("PADDLE_OCR_PREPARE_STARTED", backend=backend_name, source_lang=source_lang, reason=reason)
        except Exception:
            pass
        self.log_ui_event("PADDLE_OCR_PREPARE_STARTED", f"backend={backend_name}; source_lang={source_lang}; reason={reason}")
        log_paddle_ocr_event(
            "PADDLE_OCR_PREPARE_STARTED",
            backend=backend_name,
            source_lang=source_lang,
            reason=reason,
            paths=paddle_runtime_paths_snapshot(),
        )
        self.set_ocr_prepare_status_text("ocr_prepare_loading")

        if uses_persistent_live_paddle_worker(backend_name):
            self.paddle_ocr_prepare_thread = LivePaddleOcrWorker(backend_name, source_lang)
            self.engine_thread.set_live_paddle_worker(self.paddle_ocr_prepare_thread)
        else:
            self.paddle_ocr_prepare_thread = PaddleOcrPrepareThread(backend_name, source_lang)
            self.engine_thread.set_live_paddle_worker(None)
        self.paddle_ocr_prepare_thread.ready_signal.connect(self.on_paddle_ocr_prepare_finished)
        log_startup_event("PADDLE_WARMUP_SCHEDULED", backend=backend_name, reason=reason)
        self.paddle_ocr_prepare_thread.start()
        self.update_block3_pipeline_readiness("ocr_prepare_started")
        return True

    @pyqtSlot(str, bool, str, int, str)
    def on_paddle_ocr_prepare_finished(self, backend_name, ok, error, elapsed_ms, text):
        backend_name = str(backend_name or self.paddle_ocr_prepare_backend or "")
        completed_worker = self.sender()
        if is_block2_session(self.session_config) and (
                backend_name != self.selected_ocr_backend_name() or
                (completed_worker is not None and completed_worker is not self.paddle_ocr_prepare_thread)):
            self.log_ui_event("PADDLE_OCR_PREPARE_STALE", f"backend={backend_name}")
            return
        self.paddle_ocr_prepare_elapsed_ms = int(elapsed_ms or 0)
        self.paddle_ocr_prepare_error = str(error or "")
        if ok:
            self.paddle_ocr_prepare_state = "ready"
            self.paddle_ocr_prepare_backend = backend_name
            self._selected_ocr_ready_for_hint = True
            startup_block3_diagnostics.emit_startup_diagnostic(
                "PADDLE_READY",
                backend=backend_name,
                prepare_elapsed_ms=int(elapsed_ms or 0),
            )
            self._finish_startup_diagnostic_component("paddle", True)
            log_startup_event("PADDLE_OCR_PREPARE_READY", backend=backend_name, elapsed_ms=elapsed_ms, text=str(text)[:120])
            self.log_ui_event("PADDLE_OCR_PREPARE_READY", f"backend={backend_name}; elapsed_ms={elapsed_ms}; text={str(text)[:120]}")
            log_paddle_ocr_event(
                "PADDLE_OCR_PREPARE_READY",
                backend=backend_name,
                elapsed_ms=elapsed_ms,
                text=str(text)[:120],
                paths=paddle_runtime_paths_snapshot(),
            )
            if is_block2_session(self.session_config) and getattr(self, "_translation_model_error_for_hint", ""):
                self.set_idle_translation_hint("ocr_ready_after_translator_error")
            else:
                self.set_ocr_prepare_status_text("ocr_prepare_ready")
            self.update_block3_pipeline_readiness("ocr_ready")
            QTimer.singleShot(1200, self.restore_idle_hint_after_prepare)
            return

        self.paddle_ocr_prepare_state = "error"
        self.paddle_ocr_prepare_backend = backend_name
        self._selected_ocr_ready_for_hint = False
        startup_block3_diagnostics.emit_startup_diagnostic(
            "PADDLE_PREPARE_ERROR",
            backend=backend_name,
            elapsed_ms=int(elapsed_ms or 0),
            error=str(error or "")[:500],
        )
        self._finish_startup_diagnostic_component("paddle", False)
        log_startup_event("PADDLE_OCR_PREPARE_ERROR", backend=backend_name, elapsed_ms=elapsed_ms, error=str(error)[:300])
        self.log_ui_event("PADDLE_OCR_PREPARE_ERROR", f"backend={backend_name}; elapsed_ms={elapsed_ms}; error={str(error)[:500]}")
        log_paddle_ocr_event(
            "PADDLE_OCR_PREPARE_ERROR",
            backend=backend_name,
            elapsed_ms=elapsed_ms,
            error=str(error)[:500],
            paths=paddle_runtime_paths_snapshot(),
        )
        status_key = "ocr_prepare_error"
        if "PADDLE_OCR_MODEL_DOWNLOAD_REQUIRED" in str(error):
            log_startup_event("PADDLE_OCR_MODEL_DOWNLOAD_REQUIRED", backend=backend_name)
            self.log_ui_event("PADDLE_OCR_MODEL_DOWNLOAD_REQUIRED", str(error)[:700])
            log_paddle_ocr_event("PADDLE_OCR_MODEL_DOWNLOAD_REQUIRED", backend=backend_name, error=str(error)[:700], paths=paddle_runtime_paths_snapshot())
            status_key = "ocr_prepare_download_required"
        elif "PADDLE_OCR_MODEL_MISSING" in str(error):
            log_startup_event("PADDLE_OCR_MODEL_MISSING", backend=backend_name)
            self.log_ui_event("PADDLE_OCR_MODEL_MISSING", str(error)[:700])
            log_paddle_ocr_event("PADDLE_OCR_MODEL_MISSING", backend=backend_name, error=str(error)[:700], paths=paddle_runtime_paths_snapshot())
            status_key = "ocr_prepare_missing_model"
        self.set_ocr_prepare_status_text(status_key)
        self.update_block3_pipeline_readiness("ocr_error")


    def pause_capture_for_geometry_adjustment(self):
        """
        Ставит рабочий OCR/capture-поток на короткую паузу во время настройки рамки.

        Важно: это работает только в режиме настройки, когда оверлей кликабельный.
        В режиме перевода click-through поведение не меняем.
        """
        if self.is_click_through:
            return

        adjustment_started = not bool(self._geometry_adjustment_active)
        if adjustment_started:
            self.capture_thread_growth_snapshot("BEFORE_ROI_CHANGE")
        self._geometry_adjustment_active = True
        if hasattr(self, "subtitle_stale_grace_timer"):
            self.subtitle_stale_grace_timer.stop()
            self._subtitle_stale_sequence_id = 0

        if hasattr(self, "engine_thread") and self.engine_thread:
            self.engine_thread.set_user_interaction_paused(True)

        if hasattr(self, "geometry_resume_timer"):
            self.geometry_resume_timer.start(self.geometry_adjustment_pause_ms)

    def resume_capture_after_geometry_adjustment(self):
        """
        Возобновляет OCR/capture после того, как пользователь перестал двигать рамку.
        """
        if self.is_click_through:
            return

        # Если левая кнопка всё ещё удерживается на самом оверлее, ждём дальше.
        if self.drag_position is not None:
            if hasattr(self, "geometry_resume_timer"):
                self.geometry_resume_timer.start(self.geometry_adjustment_pause_ms)
            return

        self._geometry_adjustment_active = False
        self.sync_geometry_to_session()

        if hasattr(self, "engine_thread") and self.engine_thread:
            # После изменения геометрии старые OCR-кандидаты уже невалидны.
            self.engine_thread.reset_memory()
            self.engine_thread.set_user_interaction_paused(False)
        self.capture_thread_growth_snapshot("AFTER_ROI_CHANGE")

    def resizeEvent(self, event):
        super().resizeEvent(event)

        # Qt может прислать resizeEvent очень рано, пока init_ui ещё не закончил создание
        # всех дочерних виджетов. Поэтому перед обращением к маркеру размера проверяем hasattr.
        if hasattr(self, "sizer_grip"):
            self.sizer_grip.move(
                self.width() - self.sizer_grip.width(),
                self.height() - self.sizer_grip.height(),
            )
        self.position_force_refresh_status()
        self.position_model_preparation_status()
        self.schedule_live_overlay_refit()

        # При изменении размера сразу обновляем профиль в памяти, но OCR/capture
        # временно ставим на паузу, чтобы не переводить промежуточные кропы.
        if not self.is_click_through and hasattr(self, "session_config"):
            self.pause_capture_for_geometry_adjustment()
            self.sync_geometry_to_session()

    def moveEvent(self, event):
        super().moveEvent(event)

        # В старом коде сохранялся resize, но не move.
        # Из-за этого пользователь мог перетащить рамку, а координаты кропа оставались старыми.
        if self.live_mouse_mode != LiveOverlayMouseMode.GAMEPLAY:
            self.pause_capture_for_geometry_adjustment()
            self.sync_geometry_to_session()

    def live_overlay_font_limits(self):
        visuals = self.session_config.get("visuals", {}) or {}
        try:
            preferred = max(1, int(visuals.get("font_size", 19)))
        except Exception:
            preferred = 19
        return preferred, min(preferred, LIVE_OVERLAY_MIN_FONT_SIZE)

    def apply_live_overlay_auto_fit(self, text, reason="translation_publish"):
        """Fit Live Overlay text without changing profile settings or geometry."""
        label = getattr(self, "lbl_translated", None)
        if label is None:
            return {
                "preferred_font_size": 0,
                "minimum_font_size": 0,
                "fitted_font_size": 0,
                "fits": True,
                "content_width": 0,
                "content_height": 0,
            }
        visuals = self.session_config.get("visuals", {}) or {}
        font_name = str(visuals.get("font_name", "Arial") or "Arial")
        preferred, minimum = self.live_overlay_font_limits()
        try:
            layout = getattr(self, "main_layout", None)
            if layout is not None:
                layout.activate()
        except Exception:
            pass
        content_rect = label.contentsRect()
        content_width = max(1, int(content_rect.width()))
        content_height = max(1, int(content_rect.height()))
        value = str(text or "")
        fitted = preferred
        if value:
            while fitted > minimum and not wrapped_text_fits(
                value,
                font_name,
                fitted,
                content_width,
                content_height,
            ):
                fitted -= 1
        fits = wrapped_text_fits(
            value,
            font_name,
            fitted,
            content_width,
            content_height,
        ) if value else True
        label.setFont(QFont(font_name, fitted, QFont.Weight.Bold))
        previous = int(getattr(self, "_live_overlay_fitted_font_size", preferred) or preferred)
        self._live_overlay_fitted_font_size = fitted
        if previous != fitted or reason == "translation_publish":
            try:
                self.log_ui_event(
                    "LIVE_OVERLAY_FONT_FIT",
                    (
                        f"reason={reason}; preferred={preferred}; minimum={minimum}; fitted={fitted}; "
                        f"fits={fits}; content={content_width}x{content_height}; text_length={len(value)}"
                    ),
                )
            except Exception:
                pass
        return {
            "preferred_font_size": preferred,
            "minimum_font_size": minimum,
            "fitted_font_size": fitted,
            "fits": bool(fits),
            "content_width": content_width,
            "content_height": content_height,
        }

    def refit_current_live_translation(self):
        if self._session_stopping:
            return
        label = getattr(self, "lbl_translated", None)
        current = str(getattr(self, "last_displayed_translation", "") or "")
        if label is None or not current or label.text() != current:
            return
        self.apply_live_overlay_auto_fit(current, reason="overlay_resize")

    def schedule_live_overlay_refit(self):
        timer = getattr(self, "live_overlay_refit_timer", None)
        if timer is None or self._session_stopping:
            return
        current = str(getattr(self, "last_displayed_translation", "") or "")
        label = getattr(self, "lbl_translated", None)
        if current and label is not None and label.text() == current:
            timer.start(0)

    @pyqtSlot(str)
    def on_model_warmup_status(self, text):
        published = str(getattr(self, "last_displayed_translation", "") or "")
        if not published or self.lbl_translated.text() != published:
            self.lbl_translated.setText(text)
        label = self.model_preparation_status_label
        label.setText(text)
        self._model_preparation_text = text
        self._model_preparation_frame = 0
        self.position_model_preparation_status()
        label.show()
        label.raise_()
        if text == tr(self.interface_lang, "overlay_vulkan_preparing"):
            self.model_preparation_timer.start(600)
        else:
            self.model_preparation_timer.stop()

    def advance_model_preparation_status(self):
        self._model_preparation_frame = (self._model_preparation_frame + 1) % 4
        base = self._model_preparation_text.rstrip(". …")
        self.model_preparation_status_label.setText(base + "." * (self._model_preparation_frame + 1))
        self.position_model_preparation_status()

    def position_model_preparation_status(self):
        label = getattr(self, "model_preparation_status_label", None)
        if label is None:
            return
        label.setWordWrap(True)
        label.setMaximumWidth(max(80, self.width() - 24))
        label.adjustSize()
        label.move(12, max(12, self.height() - label.height() - 12))

    def position_force_refresh_status(self):
        label = getattr(self, "force_refresh_status_label", None)
        if label is None:
            return
        margin = 12
        label.adjustSize()
        label.move(
            max(margin, self.width() - label.width() - margin),
            max(margin, self.height() - label.height() - margin),
        )

    @pyqtSlot(str, int, str)
    def on_force_refresh_status(self, state, request_id, reason=""):
        label = getattr(self, "force_refresh_status_label", None)
        timer = getattr(self, "force_refresh_status_timer", None)
        if label is None or timer is None or self._session_stopping:
            return
        normalized_state = str(state or "").strip().lower()
        request_id = int(request_id or 0)
        current_request_id = int(getattr(self, "_force_refresh_visual_request_id", 0) or 0)
        if normalized_state == "updating":
            timer.stop()
            self._force_refresh_visual_request_id = request_id
            localization_key = "force_refresh_status_updating"
        elif normalized_state in {"success", "rejected"}:
            if current_request_id and request_id != current_request_id:
                return
            self._force_refresh_visual_request_id = 0
            localization_key = (
                "force_refresh_status_success"
                if normalized_state == "success"
                else "force_refresh_status_rejected"
            )
        else:
            return
        self._force_refresh_visual_state = normalized_state
        label.setText(tr(self.interface_lang, localization_key))
        self.position_force_refresh_status()
        label.show()
        label.raise_()
        self.log_ui_event(
            "FORCE_REFRESH_STATUS_SHOWN",
            f"state={normalized_state}; request_id={request_id}; reason={reason}",
        )
        if normalized_state in {"success", "rejected"}:
            timer.start(int(self._force_refresh_status_timeout_ms))

    def hide_force_refresh_status(self):
        timer = getattr(self, "force_refresh_status_timer", None)
        if timer is not None:
            timer.stop()
        label = getattr(self, "force_refresh_status_label", None)
        previous_state = str(getattr(self, "_force_refresh_visual_state", "hidden") or "hidden")
        if label is not None:
            label.hide()
        self._force_refresh_visual_state = "hidden"
        self._force_refresh_visual_request_id = 0
        if previous_state != "hidden":
            self.log_ui_event("FORCE_REFRESH_STATUS_HIDDEN", f"previous_state={previous_state}")

    def edit_online_settings(self):
        from online_ui import OnlineSettingsDialog
        translation = self.session_config.setdefault("translation", {})
        dialog = OnlineSettingsDialog(self, translation.get("online"), self.interface_lang,
            translation.get("source_lang", "en"), translation.get("target_lang", "uk"))
        if dialog.exec() == QDialog.DialogCode.Accepted:
            translation["online"] = dialog.result_settings
            online_active = translation.get("mode") == "online"
            if online_active:
                self.engine_thread.invalidate_runtime_tasks("online_settings_changed")
            self.engine_thread.update_session_data(self.session_config)
            if online_active:
                self.cancel_active_dynamic_request("online_settings_changed")
            self.save_profile_to_disk()
            if online_active:
                self.start_model_warmup()

    def on_live_settings_changed(self):
        """Изменение языка или режима перевода из панели настройки оверлея."""
        translation = self.session_config.setdefault("translation", {})
        previous_translation_state = (
            str(translation.get("source_lang", "") or ""),
            str(translation.get("target_lang", "") or ""),
            str(translation.get("translation_profile", "") or ""),
            str(translation.get("chinese_script_mode", "") or ""),
            str(translation.get("backend", "") or ""),
            str(translation.get("mode", "") or ""),
        )
        previous_target_lang = str(translation.get("target_lang", "") or "")
        previous_ocr_backend = current_ocr_backend_from_session(self.session_config)
        selected_target_lang = self.current_overlay_target_lang()
        target_changed = previous_target_lang != selected_target_lang
        if target_changed:
            self.capture_thread_growth_snapshot(
                "BEFORE_TARGET_CHANGE",
                extra={"from": previous_target_lang, "to": selected_target_lang},
            )
        translation["target_lang"] = selected_target_lang
        block2_active = is_block2_session(self.session_config)
        block3_active = is_block3_session(self.session_config)
        block_router_active = block2_active or block3_active
        translation["mode"] = self.mode_selector.currentText()
        if block2_active:
            translation["source_lang"] = self.current_overlay_source_lang()
            translation["translation_profile"] = self.current_overlay_block2_profile()
            translation["chinese_script_mode"] = self.current_overlay_chinese_script_mode()
            translation["backend"] = "block2_router"
            self.block2_script_selector.setVisible(translation["source_lang"] == "zh")
            self.log_ui_event(
                "block2_state_changed",
                f"source={translation['source_lang']}; target={translation['target_lang']}; profile={translation['translation_profile']}; script={translation['chinese_script_mode']}",
            )
        elif block3_active:
            translation["source_lang"] = "en"
            translation["translation_profile"] = self.current_overlay_block2_profile()
            translation.pop("chinese_script_mode", None)
            translation["backend"] = "block3_router"
            self.block2_script_selector.setVisible(False)
            self.log_ui_event(
                "block3_state_changed",
                f"source=en; target={translation['target_lang']}; profile={translation['translation_profile']}",
            )

        if block2_active:
            ocr_cfg = self.session_config.setdefault("ocr", {})
            quality_engine = self.current_overlay_quality_ocr_engine(BLOCK2)
            desired_ocr = ocr_engine_for_translation_profile(
                BLOCK2,
                translation["translation_profile"],
                quality_engine,
            )
            ocr_cfg["quality_engine"] = quality_engine
            ocr_cfg["engine"] = desired_ocr
            ocr_cfg["advanced_override"] = False
            self.refresh_overlay_quality_ocr_selector()
            if previous_ocr_backend != desired_ocr:
                ocr_core.set_backend(desired_ocr)
                self.paddle_ocr_prepare_state = "not_started"

        print(f"[ЖИВОЙ КОНФИГ] {self.session_config['translation']}")

        self.engine_thread.update_session_data(self.session_config)
        session_logging.update_state(self.session_config)
        current_translation_state = (
            str(translation.get("source_lang", "") or ""),
            str(translation.get("target_lang", "") or ""),
            str(translation.get("translation_profile", "") or ""),
            str(translation.get("chinese_script_mode", "") or ""),
            str(translation.get("backend", "") or ""),
            str(translation.get("mode", "") or ""),
        )
        translation_changed = previous_translation_state != current_translation_state
        ocr_changed = previous_ocr_backend != current_ocr_backend_from_session(self.session_config)
        if translation_changed:
            self.cancel_active_dynamic_request("translation_settings_changed")
            self.engine_thread.reset_memory()
        self.update_block3_pipeline_readiness("settings_changed")
        try:
            self.save_profile_to_disk()
        except Exception as exc:
            self.log_ui_event("BLOCK2_PROFILE_SAVE_FAILED", f"error={type(exc).__name__}:{exc}")

        if translation_changed and self.mode_selector.currentText() == "offline":
            self.start_model_warmup()
        elif translation_changed:
            self.start_model_warmup()

        if translation_changed or ocr_changed:
            self.start_paddle_ocr_prepare(
                "quality_ocr_changed" if ocr_changed and not translation_changed else "settings_changed"
            )
        if target_changed:
            self.capture_thread_growth_snapshot(
                "AFTER_TARGET_CHANGE",
                extra={"from": previous_target_lang, "to": selected_target_lang},
            )

    def current_overlay_source_lang(self):
        if is_block2_session(self.session_config) and hasattr(self, "source_selector"):
            return str(self.source_selector.currentData() or "ja")
        if is_block3_session(self.session_config):
            return "en"
        return str((self.session_config.get("translation", {}) or {}).get("source_lang", "en"))

    def current_overlay_target_lang(self):
        if hasattr(self, "lang_selector"):
            return str(self.lang_selector.currentData() or self.lang_selector.currentText() or "uk").lower()
        return str((self.session_config.get("translation", {}) or {}).get("target_lang", "uk"))

    def current_overlay_block2_profile(self):
        if hasattr(self, "block2_profile_selector"):
            value = str(self.block2_profile_selector.currentData() or "fast")
            return value if value in {"fast", "quality"} else "fast"
        return "fast"

    def current_overlay_quality_ocr_engine(self, block_id=None):
        selected_block = block_id or block_id_for_language_mode(
            self.session_config.get("language_mode")
        )
        value = (self.session_config.get("ocr", {}) or {}).get("quality_engine")
        if (hasattr(self, "block_quality_ocr_selector")
                and self.block_quality_ocr_selector.isEnabled()
                and self.current_overlay_block2_profile() == "quality"):
            value = self.block_quality_ocr_selector.currentData() or value
        return normalize_quality_ocr_engine(value, selected_block)

    def refresh_overlay_quality_ocr_selector(self):
        if not hasattr(self, "block_quality_ocr_selector"):
            return
        block2_active = is_block2_session(self.session_config)
        self.block_quality_ocr_selector.setVisible(block2_active)
        if block2_active:
            # Fast displays its actual OCR, not the separately saved Quality choice.
            engine = current_ocr_backend_from_session(self.session_config)
            blocked = self.block_quality_ocr_selector.blockSignals(True)
            try:
                self.block_quality_ocr_selector.setCurrentIndex(
                    max(0, self.block_quality_ocr_selector.findData(engine))
                )
            finally:
                self.block_quality_ocr_selector.blockSignals(blocked)
        self.block_quality_ocr_selector.setEnabled(
            block2_active and self.current_overlay_block2_profile() == "quality"
        )

    def current_overlay_chinese_script_mode(self):
        if hasattr(self, "block2_script_selector"):
            value = str(self.block2_script_selector.currentData() or "auto")
            return value if value in {"auto", "zh", "zh_hant"} else "auto"
        return "auto"

    def get_hotkey_display(self, action):
        """Возвращает человекочитаемое сочетание для подсказок в оверлее."""
        return self.hotkeys.get(action, DEFAULT_HOTKEYS[action]).get("display", DEFAULT_HOTKEYS[action]["display"])

    def build_hotkey_hint_text(self):
        """Строка подсказки в верхней панели оверлея."""
        mode = self.get_hotkey_display("toggle_translation_mode")
        hide = self.get_hotkey_display("toggle_translation_visibility")
        dynamic = self.get_hotkey_display("dynamic_area_translation")
        return tr(self.interface_lang, "overlay_hotkey_hint", mode=mode, hide=hide, dynamic=dynamic)

    def log_ui_event(self, event, details=""):
        """Отдельный лог UI/хоткеев, чтобы видеть, дошло ли действие до интерфейса."""
        if not session_logging.raw_enabled():
            return
        try:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with session_logging.open_log(self.ui_events_log_path, "a", encoding="utf-8") as f:
                f.write("=" * 70 + "\n")
                f.write(f"TIME: {timestamp}\n")
                f.write(f"EVENT: {event}\n")
                f.write(f"CLICK_THROUGH: {self.is_click_through}\n")
                f.write(f"HIDDEN: {self.translation_output_hidden}\n")
                if details:
                    f.write(f"DETAILS: {str(details)[:900]}\n")
                f.write("=" * 70 + "\n\n")
        except Exception as e:
            print(f"[UI EVENT LOG ERROR]: {e}", flush=True)

    def is_paddle_ocr_selected(self):
        """True, если в текущем профиле выбран качественный OCR PaddleOCR."""
        try:
            return is_quality_ocr_backend_name(current_ocr_backend_from_session(self.session_config))
        except Exception:
            return False

    def capture_thread_growth_snapshot(self, checkpoint, *, extra=None):
        """Fail-safe opt-in snapshot bound to the current production lifecycle."""
        try:
            engine = getattr(self, "engine_thread", None)
            live_worker = getattr(engine, "_live_paddle_worker", None) if engine else None
            return thread_growth_diagnostics.capture_thread_snapshot(
                checkpoint,
                engine=engine,
                live_worker=live_worker,
                dynamic_worker=getattr(self, "dynamic_worker", None),
                session_data=getattr(self, "session_config", None),
                extra=extra,
            )
        except Exception:
            return None

    def ensure_dynamic_worker(self):
        if self._session_stopping:
            return None
        worker = self.dynamic_worker
        if worker is not None and worker.isRunning():
            return worker
        worker = DynamicTranslationThread()
        worker.diagnostic_signal.connect(self.on_dynamic_diagnostic)
        worker.finished_signal.connect(self.on_dynamic_translation_ready)
        worker.request_finished.connect(self.on_dynamic_request_finished)
        worker.finished.connect(self.on_dynamic_worker_stopped)
        self.dynamic_worker = worker
        worker.start()
        return worker

    @pyqtSlot(int, str)
    def on_dynamic_request_finished(self, request_id, outcome):
        request_id = int(request_id or 0)
        outcome = str(outcome or "error")
        if outcome != "success":
            self._dynamic_request_snapshots.pop(request_id, None)
        if request_id != int(self._dynamic_cancel_pending_resume_request_id or 0):
            return
        self._dynamic_cancel_pending_resume_request_id = 0
        if (
            self._session_stopping
            or int(self._active_dynamic_request_id or 0) > 0
            or self.dynamic_only_mode
            or not self._dynamic_live_pause_owned
        ):
            return
        self._dynamic_live_pause_owned = False
        self.log_ui_event(
            "DYNAMIC_LIVE_RESUME",
            f"request_id={request_id}; reason=cancelled_request_finished; backend={self.selected_ocr_backend_name()}",
        )
        self.engine_thread.set_user_interaction_paused(False, invalidate_runtime=False)
        self.capture_thread_growth_snapshot(
            "AFTER_RETURN_TO_LIVE",
            extra={"request_id": request_id, "reason": "cancelled_request_finished"},
        )

    @pyqtSlot()
    def on_dynamic_worker_stopped(self):
        if self._session_stopping:
            return
        self.log_ui_event(
            "DYNAMIC_WORKER_STOPPED",
            "reason=unexpected_worker_stop; busy=False",
        )

    def cancel_active_dynamic_request(self, reason, *, close_windows=True):
        request_id = int(self._active_dynamic_request_id or 0)
        self.dynamic_capture_timer.stop()
        self.dynamic_restore_timer.stop()
        self._dynamic_capture_request_id = 0
        self._dynamic_restore_request_id = 0
        self._active_dynamic_request_id = 0
        worker = self.dynamic_worker
        if request_id:
            self.remember_cancelled_dynamic_request(request_id, reason)
        if worker is not None and worker.has_request(request_id):
            worker.cancel_request(reason, request_id=request_id)
        if close_windows:
            for window_name in ("dynamic_selection_overlay", "dynamic_result_popup"):
                window = getattr(self, window_name, None)
                if window is None:
                    continue
                try:
                    if hasattr(window, "release_input_capture"):
                        window.release_input_capture(reason)
                    window.close()
                except Exception:
                    pass
                setattr(self, window_name, None)
        if "dynamic_selection" in self._window_interaction_contexts:
            self.finish_window_interaction("dynamic_selection", reason)
        if request_id:
            self.log_ui_event(
                "DYNAMIC_REQUEST_CANCELLED",
                f"request_id={request_id}; reason={reason}",
            )
        return request_id

    def remember_cancelled_dynamic_request(self, request_id, reason):
        request_id = int(request_id or 0)
        if not request_id:
            return
        self._cancelled_dynamic_request_reasons[request_id] = str(reason or "cancelled")
        while len(self._cancelled_dynamic_request_reasons) > 32:
            oldest_request_id = next(iter(self._cancelled_dynamic_request_reasons))
            self._cancelled_dynamic_request_reasons.pop(oldest_request_id, None)

    def dismiss_dynamic_request_by_hotkey(self, reason):
        request_id = int(self._active_dynamic_request_id or 0)
        if not request_id:
            return False

        self.remember_cancelled_dynamic_request(request_id, reason)
        self.dynamic_capture_timer.stop()
        self.dynamic_restore_timer.stop()
        self._dynamic_capture_request_id = 0
        self._dynamic_restore_request_id = 0

        worker = self.dynamic_worker
        request_executing = bool(worker is not None and worker.has_request(request_id))
        if request_executing:
            self._dynamic_cancel_pending_resume_request_id = request_id
            worker.cancel_request(reason, request_id=request_id)

        selection = self.dynamic_selection_overlay
        if selection is not None:
            try:
                selection.release_input_capture(reason)
                selection.hide()
                selection.close()
            except Exception:
                pass
            self.dynamic_selection_overlay = None

        popup = self.dynamic_result_popup
        if popup is not None:
            try:
                if hasattr(popup, "close_timer"):
                    popup.close_timer.stop()
                popup.hide()
                popup.close()
            except Exception:
                pass
            self.dynamic_result_popup = None

        if "dynamic_selection" in self._window_interaction_contexts:
            self.finish_window_interaction("dynamic_selection", reason)

        self.log_ui_event(
            "DYNAMIC_REQUEST_CANCELLED",
            (
                f"request_id={request_id}; reason={reason}; "
                "late_result_reason=dynamic_cancelled_result_suppressed"
            ),
        )
        defer_live_resume = bool(
            request_executing
            or int(self._dynamic_cancel_pending_resume_request_id or 0) > 0
        )
        if not self.restore_translation_after_dynamic(
            request_id,
            reason,
            resume_live=not defer_live_resume,
        ):
            self._active_dynamic_request_id = 0
        return True

    def handle_dynamic_hotkey(self):
        popup_visible = False
        if self.dynamic_result_popup is not None:
            try:
                popup_visible = bool(self.dynamic_result_popup.isVisible())
            except Exception:
                popup_visible = True
        if popup_visible:
            return self.dismiss_dynamic_request_by_hotkey("dynamic_manual_hotkey_dismiss")

        if self.dynamic_selection_overlay is not None:
            return self.dismiss_dynamic_request_by_hotkey("dynamic_hotkey_selection_cancel")

        if int(self._active_dynamic_request_id or 0) > 0:
            return self.dismiss_dynamic_request_by_hotkey("dynamic_hotkey_pending_cancel")

        self.start_dynamic_selection()
        return True

    def begin_dynamic_request(self):
        self.cancel_active_dynamic_request("superseded_by_new_request")
        self._dynamic_request_seq += 1
        self._active_dynamic_request_id = int(self._dynamic_request_seq)
        self.dynamic_started_at = time.time()
        self.log_ui_event(
            "DYNAMIC_REQUEST_STARTED",
            f"request_id={self._active_dynamic_request_id}",
        )
        return self._active_dynamic_request_id

    def is_dynamic_request_current(self, request_id):
        return bool(
            not self._session_stopping
            and int(request_id or 0) > 0
            and int(request_id or 0) == int(self._active_dynamic_request_id or 0)
        )

    def pause_live_for_dynamic(self, request_id):
        if self._session_stopping or self.dynamic_only_mode:
            return False
        if self._dynamic_live_pause_owned:
            self.log_ui_event("DYNAMIC_LIVE_PAUSE_REUSED", f"request_id={request_id}")
            return True
        if not hasattr(self, "engine_thread") or not self.engine_thread:
            return False
        if bool(self.engine_thread.user_interaction_paused):
            self.log_ui_event(
                "DYNAMIC_LIVE_PAUSE_NOT_OWNED",
                f"request_id={request_id}; reason=already_paused",
            )
            return False
        self.capture_thread_growth_snapshot(
            "BEFORE_DYNAMIC",
            extra={"request_id": int(request_id or 0)},
        )
        self._dynamic_live_pause_owned = True
        self.log_ui_event(
            "DYNAMIC_LIVE_PAUSE",
            f"request_id={request_id}; backend={self.selected_ocr_backend_name()}",
        )
        if self.is_paddle_ocr_selected():
            self.log_ui_event("PADDLE_LIVE_PAUSE", f"dynamic_request_id={request_id}")
        self.engine_thread.set_user_interaction_paused(True, invalidate_runtime=True)
        self.capture_thread_growth_snapshot(
            "AFTER_DYNAMIC",
            extra={"request_id": int(request_id or 0)},
        )
        return True

    def resume_live_after_dynamic(self, request_id, reason):
        if self._session_stopping or not self.is_dynamic_request_current(request_id):
            return False
        if self.dynamic_only_mode or not self._dynamic_live_pause_owned:
            return False
        pending_cancelled_request_id = int(
            getattr(self, "_dynamic_cancel_pending_resume_request_id", 0) or 0
        )
        if pending_cancelled_request_id > 0:
            self.log_ui_event(
                "DYNAMIC_LIVE_RESUME_DEFERRED",
                (
                    f"request_id={request_id}; reason={reason}; "
                    f"pending_cancelled_request_id={pending_cancelled_request_id}"
                ),
            )
            return False
        self._dynamic_live_pause_owned = False
        self.log_ui_event(
            "DYNAMIC_LIVE_RESUME",
            f"request_id={request_id}; reason={reason}; backend={self.selected_ocr_backend_name()}",
        )
        if self.is_paddle_ocr_selected():
            self.log_ui_event(
                "PADDLE_LIVE_RESUME",
                f"dynamic_request_id={request_id}; reason={reason}",
            )
        self.engine_thread.set_user_interaction_paused(False, invalidate_runtime=False)
        self.capture_thread_growth_snapshot(
            "AFTER_RETURN_TO_LIVE",
            extra={"request_id": int(request_id or 0), "reason": str(reason or "")},
        )
        return True

    @pyqtSlot()
    def on_dynamic_capture_timer(self):
        request_id = int(self._dynamic_capture_request_id or 0)
        rect = QRect(self._dynamic_capture_rect)
        self._dynamic_capture_request_id = 0
        if self.is_dynamic_request_current(request_id):
            self.capture_dynamic_region(request_id, rect)

    @pyqtSlot()
    def on_dynamic_restore_timer(self):
        request_id = int(self._dynamic_restore_request_id or 0)
        self._dynamic_restore_request_id = 0
        if request_id:
            self.restore_translation_after_dynamic(request_id, "popup_timeout")

    def set_paddle_live_ocr_paused(self, paused, reason=""):
        """
        Безопасно ставит постоянный OCR-поток на паузу в PaddleOCR-режиме.

        Это не влияет на Tesseract. Для PaddleOCR это важно: один проход может
        занимать секунды, поэтому во время скрытой плашки или динамического
        перевода постоянный OCR не должен параллельно молотить старую область.
        """
        if not self.is_paddle_ocr_selected():
            return
        if not hasattr(self, "engine_thread") or not self.engine_thread:
            return
        desired_paused = bool(paused)
        if bool(self.engine_thread.user_interaction_paused) == desired_paused:
            self.log_ui_event(
                "PADDLE_LIVE_PAUSE_REUSED" if desired_paused else "PADDLE_LIVE_RESUME_REUSED",
                reason,
            )
            return
        self._paddle_live_pause_reason = reason if paused else ""
        self.log_ui_event("PADDLE_LIVE_PAUSE" if paused else "PADDLE_LIVE_RESUME", reason)
        self.capture_thread_growth_snapshot(
            "BEFORE_PAUSE" if desired_paused else "BEFORE_RESUME",
            extra={"reason": str(reason or "")},
        )
        try:
            self.engine_thread.invalidate_runtime_tasks(f"paddle_live_pause={paused}; reason={reason}")
            self.engine_thread.set_user_interaction_paused(
                desired_paused,
                invalidate_runtime=False,
            )
        except Exception:
            pass
        self.capture_thread_growth_snapshot(
            "AFTER_PAUSE" if desired_paused else "AFTER_RESUME",
            extra={"reason": str(reason or "")},
        )

    def build_dynamic_toggle_text(self):
        """Текст кнопки включения/выключения динамического перевода в оверлее."""
        enabled = bool(self.session_config.get("dynamic", {}).get("enabled", True))
        return tr(self.interface_lang, "overlay_dynamic_on" if enabled else "overlay_dynamic_off")

    def toggle_dynamic_enabled(self):
        """Быстрое включение/выключение динамического перевода из режима настройки оверлея."""
        dynamic = self.session_config.setdefault("dynamic", normalize_dynamic_settings({}))
        dynamic["enabled"] = not bool(dynamic.get("enabled", True))
        self.btn_dynamic_toggle.setText(self.build_dynamic_toggle_text())
        self.engine_thread.update_session_data(self.session_config)

    def on_dynamic_display_time_changed(self, value):
        """Сохраняет launcher-compatible dynamic.display_time_sec из панели оверлея."""
        dynamic = normalize_dynamic_settings(self.session_config.get("dynamic", {}))
        dynamic["display_time_sec"] = max(1, min(30, int(value)))
        self.session_config["dynamic"] = dynamic
        self.engine_thread.update_session_data(self.session_config)
        try:
            self.save_profile_to_disk()
            self.log_ui_event(
                "DYNAMIC_DISPLAY_TIME_CHANGED",
                f"display_time_sec={dynamic['display_time_sec']}",
            )
        except Exception as exc:
            self.log_ui_event(
                "DYNAMIC_DISPLAY_TIME_SAVE_FAILED",
                f"error={type(exc).__name__}:{exc}",
            )

    def is_key_down(self, vk_code):
        """
        Проверка глобального состояния клавиши через WinAPI GetAsyncKeyState.

        Почему так:
        - nativeEvent() в PyQt уже дал нативное падение процесса;
        - RegisterHotKey + PeekMessage в v3 не сработал на твоём запуске;
        - GetAsyncKeyState не использует callback и поэтому значительно безопаснее.

        Минус метода: хоткей не «резервируется» в Windows.
        Если другая программа тоже слушает эту клавишу, она тоже может отреагировать.
        Поэтому пользователь может переназначить хоткеи в лаунчере под конкретную игру.
        """
        try:
            return bool(self.get_async_key_state(vk_code) & KEY_DOWN_MASK)
        except Exception as e:
            print(f"[HOTKEY STATE ERROR]: {e}", flush=True)
            return False

    @staticmethod
    def get_async_key_state(vk_code):
        return int(user32.GetAsyncKeyState(int(vk_code))) & 0xFFFF


    def handle_hotkey_action(self, action, details=""):
        """Выполнение действия по имени hotkey action."""
        self.log_ui_event(
            "HOTKEY_ACTION",
            f"action={action}; hotkey={self.get_hotkey_display(action)}; {details}",
        )
        if action == "toggle_translation_mode":
            print(f"[ХОТКЕЙ] {self.get_hotkey_display(action)} обнаружен. Переключение режима.", flush=True)
            self.toggle_translation_mode()
        elif action == "toggle_translation_visibility":
            print(f"[ХОТКЕЙ] {self.get_hotkey_display(action)} обнаружен. Скрыть/показать перевод.", flush=True)
            self.toggle_translation_output_visibility()
        elif action == "dynamic_area_translation":
            print(f"[ХОТКЕЙ] {self.get_hotkey_display(action)} обнаружен. Динамический перевод.", flush=True)
            self.handle_dynamic_hotkey()
        elif action == "force_refresh":
            result = self.engine_thread.request_force_refresh()
            self.log_ui_event(
                "FORCE_REFRESH_HOTKEY",
                (
                    f"status={result.get('status')}; request_id={result.get('request_id', 0)}; "
                    f"reason={result.get('reason', '')}"
                ),
            )

    def check_global_hotkey(self):
        """
        Опрос глобальных хоткеев через GetAsyncKeyState.

        Теперь сочетания не зашиты в код жёстко. Они приходят из лаунчера:
        - toggle_translation_mode: режим настройки ↔ режим перевода;
        - toggle_translation_visibility: скрыть/показать переводную плашку.

        Debounce общий:
        - нажали сочетание → действие сработало один раз;
        - пока клавиши удерживаются → повторно не срабатывает;
        - отпустили → можно сработать снова.
        """
        try:
            now_monotonic = time.monotonic()
            raw_states = {}

            def read_named_key(name):
                key_name = str(name or "").upper()
                canonical_name = HotkeyPollingState._canonical_modifier(key_name)
                if canonical_name in raw_states:
                    return raw_states[canonical_name]
                if canonical_name == "WIN":
                    raw_value = (
                        self.get_async_key_state(VK_LWIN)
                        | self.get_async_key_state(VK_RWIN)
                    ) & 0xFFFF
                else:
                    vk_code = VK_CODE_BY_NAME.get(key_name)
                    raw_value = self.get_async_key_state(vk_code) if vk_code is not None else 0
                state = {
                    "down": bool(raw_value & KEY_DOWN_MASK),
                    "pressed_since_last_poll": bool(raw_value & 0x0001),
                }
                raw_states[canonical_name] = state
                raw_states[key_name] = state
                return state

            for modifier_name in ("CTRL", "ALT", "SHIFT"):
                read_named_key(modifier_name)

            for action in list(self.hotkeys.keys()):
                data = self.hotkeys.get(action, DEFAULT_HOTKEYS.get(action, {}))
                keys = list(data.get("keys", [])) if isinstance(data, dict) else []
                key_states = {}
                for key in keys:
                    key_name = str(key or "").upper()
                    state = read_named_key(key_name)
                    key_states[key_name] = state
                    key_states[HotkeyPollingState._canonical_modifier(key_name)] = state
                dynamic_active = bool(
                    int(self._active_dynamic_request_id or 0) > 0
                    or self.dynamic_selection_overlay is not None
                )
                decision = self._hotkey_poll_state.poll(
                    action,
                    keys,
                    key_states,
                    now_monotonic=now_monotonic,
                    dynamic_active=dynamic_active,
                )
                result = str(decision.get("result") or "none")
                if result == "none":
                    continue
                if result == "rearmed":
                    self.log_ui_event(
                        "HOTKEY_REARMED",
                        (
                            f"action={action}; base_key={decision.get('base_key')}; "
                            f"poll_interval_ms={self._hotkey_poll_interval_ms}"
                        ),
                    )
                    continue
                details = (
                    f"action={action}; base_key={decision.get('base_key')}; "
                    f"current_down={decision.get('current_down')}; "
                    f"pressed_since_last_poll={decision.get('pressed_since_last_poll')}; "
                    f"modifier_states={json.dumps(decision.get('modifier_states') or {}, ensure_ascii=True)}; "
                    f"modifier_grace_used={decision.get('modifier_grace_used')}; "
                    f"poll_interval_ms={self._hotkey_poll_interval_ms}; edge_to_action_ms=0.0; "
                    f"cooldown_remaining_ms={float(decision.get('cooldown_remaining_ms') or 0.0):.3f}"
                )
                self.log_ui_event("HOTKEY_EDGE_DETECTED", details)
                if result == "ignored":
                    self.log_ui_event(
                        "HOTKEY_ACTION_IGNORED",
                        f"{details}; reason={decision.get('ignore_reason')}",
                    )
                    continue
                self.handle_hotkey_action(action, details)

        except Exception as e:
            print(f"[HOTKEY POLL ERROR]: {e}", flush=True)

    def start_dynamic_selection(self):
        """
        Запускает режим временного выделения области для динамического перевода.

        Первая версия:
        - пользователь нажимает hotkey;
        - поверх окна игры появляется слой выделения;
        - пользователь рисует прямоугольник мышью;
        - приложение делает скрин выбранной области;
        - OCR+перевод выполняются один раз;
        - результат показывается временным popup-окном.
        """
        if self._session_stopping:
            return
        self.log_ui_event("DYNAMIC_SELECTION_REQUESTED")
        dynamic_cfg = normalize_dynamic_settings(self.session_config.get("dynamic", {}))
        self.session_config["dynamic"] = dynamic_cfg
        if not dynamic_cfg.get("enabled", True):
            if not self.is_click_through:
                self.lbl_translated.setText(tr(self.interface_lang, "dynamic_disabled_status"))
            print("[DYNAMIC] Динамический перевод выключен в настройках.", flush=True)
            return

        request_id = self.begin_dynamic_request()
        self.pause_live_for_dynamic(request_id)

        if self.dynamic_selection_overlay is not None:
            # В v1 закрытое окно выделения могло оставаться в переменной и блокировать
            # повторный запуск динамического перевода. Проверяем видимость и чистим stale-ссылку.
            try:
                if self.dynamic_selection_overlay.isVisible():
                    return
            except Exception:
                pass
            self.dynamic_selection_overlay = None

        # Если на экране ещё висит прошлый результат динамического перевода,
        # убираем его до нового выделения. Иначе пользователь может случайно
        # захватить старый перевод вместе с оригинальным текстом и получить кашу.
        if self.dynamic_result_popup is not None:
            try:
                self.dynamic_result_popup.hide()
                self.dynamic_result_popup.close()
            except Exception:
                pass
            self.dynamic_result_popup = None
            # Даём Qt/Windows один короткий цикл убрать старый popup с экрана,
            # чтобы он не попал в следующий динамический скрин.
            QApplication.processEvents()

        target_hwnd = self.resolve_target_hwnd()
        if not target_hwnd or not win32gui or not win32gui.IsWindow(target_hwnd):
            self.lbl_translated.setText(tr(self.interface_lang, "overlay_game_not_found"))
            self.restore_translation_after_dynamic(request_id, "target_window_missing")
            return

        self.begin_window_interaction(
            "dynamic_selection",
            target_hwnd,
            "dynamic_selection_started",
        )
        try:
            left, top, right, bottom = win32gui.GetWindowRect(target_hwnd)
            game_rect = QRect(int(left), int(top), max(1, int(right-left)), max(1, int(bottom-top)))
        except Exception as e:
            print(f"[DYNAMIC] Не удалось получить геометрию окна игры: {e}", flush=True)
            self.finish_window_interaction("dynamic_selection", "target_geometry_error")
            self.restore_translation_after_dynamic(request_id, "target_geometry_error")
            return

        # На время выделения можно спрятать основной перевод, чтобы пользователь видел оригинал.
        # В настройках dynamic.hide_main_overlay пользователь может отключить это поведение.
        #
        # Важно: если пользователь запускает динамический перевод повторно, пока основной
        # оверлей уже спрятан предыдущим динамическим запуском, не перезаписываем
        # dynamic_restore_hidden_state=True. Иначе после завершения popup основной
        # оверлей останется спрятанным и его придётся возвращать хоткеем вручную.
        hide_main = bool(dynamic_cfg.get("hide_main_overlay", True))
        if not self.dynamic_hidden_by_dynamic:
            self.dynamic_restore_hidden_state = self.translation_output_hidden

        if self.is_click_through and hide_main and not self.translation_output_hidden:
            self.dynamic_hidden_by_dynamic = True
            self.translation_output_hidden = True
            self.lbl_translated.hide()
            self.update()

        self.dynamic_selection_overlay = DynamicSelectionOverlay(
            game_rect,
            self.interface_lang,
            owner_hwnd=int(self.winId()),
        )
        self.dynamic_selection_overlay.rect_selected.connect(
            lambda rect, request_id=request_id: self.on_dynamic_rect_selected(request_id, rect)
        )
        self.dynamic_selection_overlay.selection_cancelled.connect(
            lambda request_id=request_id: self.on_dynamic_selection_cancelled(request_id)
        )
        self.dynamic_selection_overlay.destroyed.connect(lambda: setattr(self, "dynamic_selection_overlay", None))
        self.dynamic_selection_overlay.show()
        self.dynamic_selection_overlay.raise_()
        self.dynamic_selection_overlay.activateWindow()
        self.dynamic_selection_overlay.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        try:
            self.log_ui_event(
                "DYNAMIC_SELECTION_WINDOW_INVENTORY",
                json.dumps(
                    self.dynamic_selection_overlay.native_window_inventory(),
                    ensure_ascii=True,
                    separators=(",", ":"),
                ),
            )
        except Exception as exc:
            self.log_ui_event(
                "DYNAMIC_SELECTION_WINDOW_INVENTORY_ERROR",
                f"{type(exc).__name__}:{exc}",
            )
        self.log_window_ownership_transition(
            "WINDOW_OWNERSHIP_DYNAMIC_SELECTION",
            "dynamic_selection",
            window=self.dynamic_selection_overlay,
        )
        QTimer.singleShot(
            210,
            lambda target_hwnd=target_hwnd: self.log_cursor_diagnostic_transition(
                "dynamic_selection",
                "capture_armed",
                target_hwnd,
                "dynamic_selection_ready",
            ),
        )

    def on_dynamic_selection_cancelled(self, request_id):
        """Отмена динамического выделения: возвращаем прежнее состояние основного вывода."""
        if not self.is_dynamic_request_current(request_id):
            return
        if self.dynamic_selection_overlay is not None:
            try:
                self.dynamic_selection_overlay.release_input_capture("selection_cancelled")
                self.dynamic_selection_overlay.hide()
            except Exception:
                pass
        self.finish_window_interaction("dynamic_selection", "dynamic_selection_cancelled")
        self.dynamic_selection_overlay = None
        self.restore_translation_after_dynamic(request_id, "selection_cancelled")

    def on_dynamic_rect_selected(self, request_id, rect):
        """Получен глобальный QRect выделенной области. Захват делаем до возврата фокуса игре."""
        if not self.is_dynamic_request_current(request_id):
            return
        self.log_ui_event("DYNAMIC_RECT_SELECTED", f"rect={rect}")
        selected = QRect(rect)
        # Критический момент для игровых tooltip-подсказок:
        # слой выделения уже стал прозрачным для отрисовки, но ещё не закрыт.
        # Поэтому игра не успевает вернуть себе фокус и убрать подсказку,
        # а ImageGrab снимает чистую область без нашего прямоугольника.
        self._dynamic_capture_request_id = int(request_id)
        self._dynamic_capture_rect = QRect(selected)
        self.dynamic_capture_timer.start(35)

    def capture_dynamic_region(self, request_id, rect):
        """Captures the selected area and queues it in the session Dynamic worker."""
        if not self.is_dynamic_request_current(request_id):
            return
        try:
            from PIL import ImageGrab

            original_rect = QRect(rect)
            dynamic_cfg = normalize_dynamic_settings(self.session_config.get("dynamic", {}))
            padding = int(dynamic_cfg.get("capture_padding_px", 8))
            capture_rect = QRect(original_rect).adjusted(-padding, -padding, padding, padding)

            # Не даём расширенному кропу выйти за пределы окна игры, если можем получить его границы.
            try:
                hwnd = self.resolve_target_hwnd()
                if hwnd and win32gui and win32gui.IsWindow(hwnd):
                    gl, gt, gr, gb = win32gui.GetWindowRect(hwnd)
                    capture_rect = capture_rect.intersected(QRect(int(gl), int(gt), max(1, int(gr-gl)), max(1, int(gb-gt))))
            except Exception:
                pass

            left = int(capture_rect.left())
            top = int(capture_rect.top())
            right = int(capture_rect.right()) + 1
            bottom = int(capture_rect.bottom()) + 1

            if right - left < 8 or bottom - top < 8:
                if self.dynamic_selection_overlay is not None:
                    try:
                        self.dynamic_selection_overlay.release_input_capture("selection_too_small")
                        self.dynamic_selection_overlay.close()
                    except Exception:
                        pass
                    self.dynamic_selection_overlay = None
                self.finish_window_interaction("dynamic_selection", "selection_too_small")
                self.restore_translation_after_dynamic(request_id, "selection_too_small")
                return

            # Важно: к этому моменту слой выделения остаётся активным, но уже ничего
            # не рисует. Это даёт шанс снять всплывающую подсказку до того, как
            # игра получит фокус и уберёт её.
            QApplication.processEvents()
            with responsiveness.phase("dynamic_capture", task_id=request_id):
                img = ImageGrab.grab(bbox=(left, top, right, bottom))

            # Теперь слой выделения больше не нужен. Важно: не закрываем его
            # мгновенно через close(), потому что Windows может на кадр вернуть
            # foreground окну IDE/лаунчера. Сначала прячем слой, сразу возвращаем
            # фокус игре, а физическое закрытие делаем чуть позже.
            if self.dynamic_selection_overlay is not None:
                overlay_to_close = self.dynamic_selection_overlay
                try:
                    overlay_to_close.release_input_capture("dynamic_capture_completed")
                except Exception:
                    pass
                try:
                    overlay_to_close.hide()
                except Exception:
                    pass
                self.finish_window_interaction(
                    "dynamic_selection",
                    "dynamic_selection_completed",
                )
                try:
                    overlay_to_close.close()
                except Exception:
                    pass
                self.dynamic_selection_overlay = None
            else:
                self.finish_window_interaction(
                    "dynamic_selection",
                    "dynamic_selection_completed",
                )

            try:
                if session_logging.raw_enabled():
                    img.save(DATA.ensure("diagnostics") / "debug_dynamic_capture.png")
            except Exception:
                pass

            worker = self.ensure_dynamic_worker()
            if worker is None:
                raise RuntimeError("dynamic_worker_unavailable")
            request_session_config = json.loads(json.dumps(self.session_config))
            self._dynamic_request_snapshots[int(request_id)] = request_session_config
            while len(self._dynamic_request_snapshots) > 32:
                oldest_request_id = next(iter(self._dynamic_request_snapshots))
                self._dynamic_request_snapshots.pop(oldest_request_id, None)
            accepted = worker.submit_request(
                img,
                request_session_config,
                QRect(rect),
                self.interface_lang,
                request_id=request_id,
            )
            if not accepted:
                self._dynamic_request_snapshots.pop(int(request_id), None)
                raise RuntimeError("dynamic_worker_rejected_request")
        except Exception as e:
            print(f"[DYNAMIC CAPTURE ERROR]: {e}", flush=True)
            if self.dynamic_selection_overlay is not None:
                try:
                    self.dynamic_selection_overlay.release_input_capture("capture_error")
                    self.dynamic_selection_overlay.close()
                except Exception:
                    pass
                self.dynamic_selection_overlay = None
            self.finish_window_interaction("dynamic_selection", "capture_error")
            self.restore_translation_after_dynamic(request_id, "capture_error")

    @pyqtSlot(str, str)
    def on_dynamic_diagnostic(self, event, details):
        """Mirror dynamic worker diagnostics into the regular UI log."""
        if self._session_stopping:
            if self._dynamic_shutdown_rejection_logged:
                return
            self._dynamic_shutdown_rejection_logged = True
            self.log_ui_event(
                "DYNAMIC_TRANSLATION_RESULT_DROPPED",
                "reason=session_stopping; source=worker_diagnostic",
            )
            return
        self.log_ui_event(str(event or "DYNAMIC_DIAGNOSTIC"), str(details or ""))

    @pyqtSlot(int, str, str, object, str)
    def on_dynamic_translation_ready(self, request_id, source, translated, rect, outcome_reason):
        """Показывает результат динамического перевода."""
        responsiveness.event("result_signal_receive", task_id=request_id, outcome=outcome_reason)
        request_id = int(request_id or 0)
        request_session_config = self._dynamic_request_snapshots.pop(request_id, None)
        worker = getattr(self, "dynamic_worker", None)
        timing_getter = getattr(worker, "take_online_publication_timing", None)
        online_timing = timing_getter(request_id) if callable(timing_getter) else {}
        if self._session_stopping:
            if online_timing:
                publication_event(online_timing, "dropped", "dynamic", request_id, reason="session_stopping")
            if not self._dynamic_shutdown_rejection_logged:
                self._dynamic_shutdown_rejection_logged = True
                self.log_ui_event(
                    "DYNAMIC_TRANSLATION_RESULT_DROPPED",
                    f"request_id={request_id}; reason=session_stopping",
                )
            return
        if not self.is_dynamic_request_current(request_id):
            if online_timing:
                publication_event(online_timing, "dropped", "dynamic", request_id, reason="request_not_current")
            cancelled_requests = getattr(self, "_cancelled_dynamic_request_reasons", {})
            cancel_reason = cancelled_requests.pop(int(request_id or 0), "")
            self.log_ui_event(
                "DYNAMIC_TRANSLATION_RESULT_DROPPED",
                (
                    f"request_id={request_id}; "
                    f"reason={'dynamic_cancelled_result_suppressed' if cancel_reason else 'request_not_current'}; "
                    f"cancel_reason={cancel_reason}"
                ),
            )
            return
        source = str(source or "").strip()
        translated = str(translated or "").strip()
        if not source:
            self.log_ui_event(
                "DYNAMIC_EMPTY_RESULT",
                f"request_id={request_id}; reason={outcome_reason or 'empty_ocr'}; popup_created=False",
            )
            self.restore_translation_after_dynamic(request_id, outcome_reason or "empty_ocr")
            return
        if not translated:
            self.log_ui_event(
                "DYNAMIC_TRANSLATION_EMPTY",
                (
                    f"request_id={request_id}; source_len={len(source)}; "
                    f"reason={outcome_reason or 'translation_empty'}; popup_created=False"
                ),
            )
            self.restore_translation_after_dynamic(request_id, outcome_reason or "translation_empty")
            return
        request_session_config = request_session_config or self.session_config
        dynamic_cfg = normalize_dynamic_settings(request_session_config.get("dynamic", {}))
        text = translated
        backend = current_ocr_backend_from_session(request_session_config)
        popup_started_at = time.time()
        rect_obj = QRect(rect)
        if is_paddle_family_backend(backend):
            details = (
                f"backend={backend}; rect_size={rect_obj.width()}x{rect_obj.height()}; "
                f"source_len={len(source or '')}; translation_len={len(translated or '')}; {current_thread_diagnostic()}"
            )
            self.log_ui_event("DYNAMIC_POPUP_RENDER_STARTED", details)
            log_paddle_ocr_event(
                "DYNAMIC_POPUP_RENDER_STARTED",
                backend=backend,
                rect_size=f"{rect_obj.width()}x{rect_obj.height()}",
                source_len=len(source or ""),
                translation_len=len(translated or ""),
                thread=current_thread_diagnostic(),
            )
        self.show_dynamic_result_popup(
            request_id,
            text,
            QRect(rect),
            dynamic_cfg,
            session_config=request_session_config,
        )
        if online_timing:
            publication_event(online_timing, "published", "dynamic", request_id)
        self.log_cursor_diagnostic_transition(
            "dynamic_popup",
            "shown",
            self.resolve_target_hwnd(),
            "dynamic_popup_published",
        )
        self.resume_live_after_dynamic(request_id, "dynamic_popup_published")
        if is_paddle_family_backend(backend):
            popup_ms = int((time.time() - popup_started_at) * 1000)
            details = (
                f"backend={backend}; rect_size={rect_obj.width()}x{rect_obj.height()}; "
                f"popup_ms={popup_ms}; source_len={len(source or '')}; translation_len={len(translated or '')}; {current_thread_diagnostic()}"
            )
            self.log_ui_event("DYNAMIC_POPUP_RENDER_FINISHED", details)
            log_paddle_ocr_event(
                "DYNAMIC_POPUP_RENDER_FINISHED",
                backend=backend,
                rect_size=f"{rect_obj.width()}x{rect_obj.height()}",
                popup_ms=popup_ms,
                source_len=len(source or ""),
                translation_len=len(translated or ""),
                thread=current_thread_diagnostic(),
            )
            if popup_ms > 700:
                warning_details = f"stage=popup_render; backend={backend}; popup_ms={popup_ms}; {current_thread_diagnostic()}"
                self.log_ui_event("DYNAMIC_PADDLE_BUSY_UI_WARNING", warning_details)
                log_paddle_ocr_event("DYNAMIC_PADDLE_BUSY_UI_WARNING", stage="popup_render", backend=backend, popup_ms=popup_ms, thread=current_thread_diagnostic())

        # Popup is WS_EX_NOACTIVATE. Focus ownership was already resolved when
        # the selection interaction ended, so showing the result must not steal it.

    def _dynamic_text_fits(self, text, font_name, font_size, width, height):
        """Cheap Qt font-metrics fit check for dynamic popup text."""
        return wrapped_text_fits(
            text,
            font_name,
            font_size,
            width,
            height,
            horizontal_padding=16,
            vertical_padding=16,
        )

    def calculate_dynamic_popup_layout(self, text, selected_rect, dynamic_cfg, visuals):
        """Calculates popup rect and font size with bounded auto-fit behavior."""
        output_mode = str(dynamic_cfg.get("output_mode", "near"))
        font_name = str(visuals.get("font_name", "Arial") or "Arial")
        try:
            base_font_size = int(visuals.get("font_size", 19))
        except Exception:
            base_font_size = 19
        try:
            min_font_size = int(dynamic_cfg.get("min_font_size", 12))
        except Exception:
            min_font_size = 12
        min_font_size = max(6, min(base_font_size, min_font_size))
        try:
            max_scale = float(dynamic_cfg.get("max_rect_scale", 2.0))
        except Exception:
            max_scale = 2.0
        max_scale = max(1.0, min(3.0, max_scale))

        screen = QApplication.screenAt(QPoint(selected_rect.left(), selected_rect.top()))
        available = screen.availableGeometry() if screen else QApplication.primaryScreen().availableGeometry()

        if output_mode == "over":
            base_x = selected_rect.left()
            base_y = selected_rect.top()
            base_w = max(40, selected_rect.width())
            base_h = max(24, selected_rect.height())
        else:
            base_w = max(320, min(760, selected_rect.width() + 120))
            base_h = 110
            base_x = selected_rect.left()
            base_y = selected_rect.bottom() + 10

        auto_fit = bool(dynamic_cfg.get("auto_fit_enabled", True))
        def fits(font_size, width, height):
            outline_pad = 0
            if visuals.get("outline_enabled", False):
                outline_pad = math.ceil(float(visuals.get("outline_thickness", 3)) * font_size / 19.0 * self.logicalDpiY() / 96.0)
            return self._dynamic_text_fits(text, font_name, font_size, width - 2 * outline_pad, height - 2 * outline_pad)

        chosen_w = int(base_w)
        chosen_h = int(base_h)
        chosen_font = int(base_font_size)

        if auto_fit and text:
            max_w = min(int(max(base_w, base_w * max_scale)), max(80, available.width() - 20))
            max_h = min(int(max(base_h, base_h * max_scale)), max(40, available.height() - 20))
            if not fits(chosen_font, chosen_w, chosen_h):
                chosen_w = max(chosen_w, min(max_w, int(base_w * max_scale)))
                chosen_h = max(chosen_h, min(max_h, int(base_h * max_scale)))

                # If expanded rect still does not fit, reduce font down to user limit.
                while chosen_font > min_font_size and not fits(chosen_font, chosen_w, chosen_h):
                    chosen_font -= 1

                # Height can still be too low for wrapped text; allow final bounded height growth.
                if not fits(chosen_font, chosen_w, chosen_h):
                    for test_h in range(chosen_h + 10, max_h + 1, 10):
                        if fits(chosen_font, chosen_w, test_h):
                            chosen_h = test_h
                            break
                    else:
                        chosen_h = max_h

        x = int(base_x)
        y = int(base_y)
        if output_mode != "over":
            if y + chosen_h > available.bottom():
                y = selected_rect.top() - chosen_h - 10
            if y < available.top():
                y = available.top() + 10

        if x + chosen_w > available.right():
            x = max(available.left(), available.right() - chosen_w - 10)
        if x < available.left():
            x = available.left() + 10
        if y + chosen_h > available.bottom():
            y = max(available.top(), available.bottom() - chosen_h - 10)
        if y < available.top():
            y = available.top() + 10

        return QRect(int(x), int(y), int(chosen_w), int(chosen_h)), int(chosen_font)

    def show_dynamic_result_popup(
        self,
        request_id,
        text,
        selected_rect,
        dynamic_cfg,
        session_config=None,
    ):
        """Создаёт временное окно с результатом динамического перевода."""
        if not self.is_dynamic_request_current(request_id):
            return
        visuals = self.session_config.get("visuals", {})
        display_seconds = int(dynamic_cfg.get("display_time_sec", 6))
        popup_rect, popup_font_size = self.calculate_dynamic_popup_layout(text, selected_rect, dynamic_cfg, visuals)

        if self.dynamic_result_popup:
            try:
                self.dynamic_result_popup.close()
            except Exception:
                pass

        popup = DynamicResultPopup(
            text,
            popup_rect,
            visuals,
            display_seconds,
            font_size_override=popup_font_size,
        )
        self.dynamic_result_popup = popup
        popup.source_rect = QRect(selected_rect)
        try:
            popup.destroyed.connect(
                lambda *_args, popup=popup: (
                    setattr(self, "dynamic_result_popup", None)
                    if self.dynamic_result_popup is popup
                    else None
                )
            )
        except Exception:
            pass
        popup.show()
        self.log_ui_event(
            "DYNAMIC_POPUP_SHOW",
            f"request_id={request_id}; rect={popup_rect}; font={popup_font_size}; auto_fit={bool(dynamic_cfg.get('auto_fit_enabled', True))}; scale={dynamic_cfg.get('max_rect_scale')}; min_font={dynamic_cfg.get('min_font_size')}",
        )
        self.log_window_ownership_transition(
            "WINDOW_OWNERSHIP_DYNAMIC_POPUP",
            "dynamic_popup",
            window=popup,
        )

        self._dynamic_restore_request_id = int(request_id)
        self.dynamic_restore_timer.start((display_seconds * 1000) + 150)

    def restore_translation_after_dynamic(self, request_id, reason="dynamic_finished", *, resume_live=True):
        """Возвращает состояние основного перевода после динамического выделения/показа."""
        if not self.is_dynamic_request_current(request_id):
            return False
        self.dynamic_capture_timer.stop()
        self.dynamic_restore_timer.stop()
        clear_global_override_cursor()
        self.setCursor(Qt.CursorShape.ArrowCursor)
        if not self.dynamic_only_mode and self.is_click_through:
            # Если плашку спрятал сам динамический режим, возвращаем то состояние,
            # которое было до первого динамического выделения. Если пользователь
            # спрятал плашку вручную до запуска динамики, состояние остаётся скрытым.
            if self.dynamic_hidden_by_dynamic:
                self.translation_output_hidden = bool(self.dynamic_restore_hidden_state)
                self.dynamic_hidden_by_dynamic = False
            else:
                self.translation_output_hidden = bool(self.dynamic_restore_hidden_state)

            if self.translation_output_hidden:
                self.lbl_translated.hide()
            else:
                self.lbl_translated.show()
                # Do not resurrect an old live subtitle after dynamic popup closes.
                # If live OCR produced a fresh result during dynamic mode, it is safe to restore.
                # If not, keep the label empty and let resumed OCR fill it again.
                if self.last_displayed_translation.strip() and float(self.last_displayed_updated_at or 0.0) >= float(self.dynamic_started_at or 0.0):
                    self.lbl_translated.setText(self.last_displayed_translation)
                    self.apply_live_overlay_auto_fit(
                        self.last_displayed_translation,
                        reason="dynamic_restore",
                    )
                else:
                    self.lbl_translated.setText("")
                    self.log_ui_event("DYNAMIC_RESTORE_OLD_TRANSLATION_SUPPRESSED")
            self.update()

        if resume_live:
            self.resume_live_after_dynamic(request_id, reason)
        self._active_dynamic_request_id = 0
        self._dynamic_restore_request_id = 0
        self._dynamic_capture_request_id = 0
        self.log_ui_event(
            "DYNAMIC_REQUEST_FINISHED",
            f"request_id={request_id}; reason={reason}",
        )
        return True


    def toggle_translation_output_visibility(self):
        """
        Временно скрывает или возвращает переводную плашку в режиме перевода.

        Это НЕ выключает OCR, переводчик и Rust/GDI-захват.
        Задача функции — только дать игроку быстро увидеть оригинальный текст под оверлеем.

        Поведение:
        - работает только в режиме перевода;
        - Ctrl+Alt+Shift+H скрывает QLabel и отключает рисование фоновой плашки;
        - повторное Ctrl+Alt+Shift+H возвращает последний известный перевод;
        - при возврате в режим настройки скрытие сбрасывается.
        """
        self.log_ui_event("TOGGLE_TRANSLATION_VISIBILITY_REQUESTED")
        self.subtitle_stale_grace_timer.stop()
        self._subtitle_stale_sequence_id = 0
        try:
            self.engine_thread.invalidate_runtime_tasks("toggle_translation_visibility")
        except Exception:
            pass

        if self.live_mouse_mode == LiveOverlayMouseMode.EDIT:
            # В режиме настройки нет смысла скрывать плашку: пользователь и так видит рамку
            # и может менять размер/позицию. Игнорируем хоткей, чтобы не запутывать состояние.
            return

        # Ручной hotkey скрытия/показа считается намерением пользователя и
        # сбрасывает автоматическое восстановление после динамического режима.
        self.dynamic_hidden_by_dynamic = False
        self.translation_output_hidden = not self.translation_output_hidden

        if self.translation_output_hidden:
            self.lbl_translated.hide()
            # В PaddleOCR-режиме скрытие плашки обычно нужно, чтобы посмотреть
            # оригинал или вызвать динамический перевод. Постоянный OCR в этот
            # момент только грузит CPU, поэтому ставим его на паузу.
            self.set_paddle_live_ocr_paused(True, "translation_hidden")
            print("[OVERLAY] Переводная плашка временно скрыта.", flush=True)
        else:
            self.lbl_translated.show()
            if self.last_displayed_translation.strip():
                self.lbl_translated.setText(self.last_displayed_translation)
                self.apply_live_overlay_auto_fit(
                    self.last_displayed_translation,
                    reason="visibility_restore",
                )
            self.set_paddle_live_ocr_paused(False, "translation_visible")
            print("[OVERLAY] Переводная плашка снова показана.", flush=True)

        self.update()

    def center_cursor_on_setup_area(self):
        """Переносит курсор в видимую область настройки оверлея."""
        try:
            if hasattr(self, "control_widget") and self.control_widget.isVisible():
                target_pos = self.control_widget.mapToGlobal(self.control_widget.rect().center())
            else:
                target_pos = self.mapToGlobal(self.rect().center())
            self.setCursor(Qt.CursorShape.ArrowCursor)
            positioned = bool(user32.SetCursorPos(int(target_pos.x()), int(target_pos.y())))
            return {
                "position": [int(target_pos.x()), int(target_pos.y())],
                "positioned": positioned,
            }
        except Exception as exc:
            return {
                "position": None,
                "positioned": False,
                "error": f"{type(exc).__name__}:{exc}",
            }

    def toggle_translation_mode(self):
        """Переключение: режим настройки ↔ режим перевода.

        В обычном режиме это включает/выключает постоянный OCR субтитровой рамки.
        В отдельном динамическом режиме постоянной OCR-рамки нет: хоткей просто
        прячет/показывает небольшую панель управления, а динамический перевод
        вызывается отдельным hotkey.
        """
        self.log_ui_event("TOGGLE_TRANSLATION_MODE_REQUESTED")
        if (
            self.live_mouse_mode == LiveOverlayMouseMode.EDIT
            and not self.dynamic_only_mode
            and is_block3_session(self.session_config)
        ):
            readiness = self.current_block3_pipeline_readiness()
            if not readiness.get("pipeline_ready"):
                details = self.block3_readiness_details(readiness, "live_hotkey_attempt")
                self.log_ui_event("LIVE_HOTKEY_BLOCKED_NOT_READY", details)
                event_details = {
                    "block": readiness.get("block"),
                    "mode": readiness.get("mode"),
                    "target": readiness.get("target"),
                    "ocr_backend": readiness.get("ocr_backend"),
                    "translator_backend": readiness.get("translator_backend"),
                    "ocr_ready": bool(readiness.get("ocr_ready")),
                    "translator_ready": bool(readiness.get("translator_ready")),
                    "pipeline_ready": False,
                }
                log_startup_event("LIVE_HOTKEY_BLOCKED_NOT_READY", **event_details)
                startup_block3_diagnostics.emit_startup_diagnostic(
                    "LIVE_HOTKEY_BLOCKED_NOT_READY",
                    **event_details,
                )
                self.set_block3_pipeline_preparing_status(
                    readiness,
                    "live_hotkey_blocked_not_ready",
                )
                return
        self.subtitle_stale_grace_timer.stop()
        self._subtitle_stale_sequence_id = 0
        try:
            self.engine_thread.invalidate_runtime_tasks("toggle_translation_mode")
        except Exception:
            pass

        if self.live_mouse_mode == LiveOverlayMouseMode.EDIT:
            # Переход из режима настройки в рабочий режим.
            target_hwnd = self.resolve_target_hwnd()
            if not target_hwnd:
                self.lbl_translated.setText(tr(self.interface_lang, "overlay_game_not_found"))
                return

            self.target_hwnd = target_hwnd
            self.session_config["game_hwnd"] = int(target_hwnd)
            self.sync_geometry_to_session()

            selected_ocr_backend = self.selected_ocr_backend_name()
            paddle_ready = (
                self.paddle_ocr_prepare_state == "ready"
                and self.paddle_ocr_prepare_backend == selected_ocr_backend
            )
            if is_paddle_family_backend(selected_ocr_backend) and not paddle_ready:
                self.start_paddle_ocr_prepare("before_live_start")
                if self.paddle_ocr_prepare_state == "error":
                    error_text = self.paddle_ocr_prepare_error or ""
                    if "PADDLE_OCR_MODEL_DOWNLOAD_REQUIRED" in error_text:
                        self.set_ocr_prepare_status_text("ocr_prepare_download_required")
                    elif "PADDLE_OCR_MODEL_MISSING" in error_text:
                        self.set_ocr_prepare_status_text("ocr_prepare_missing_model")
                    else:
                        self.set_ocr_prepare_status_text("ocr_prepare_error")
                else:
                    self.set_ocr_prepare_status_text("ocr_prepare_loading")
                self.log_ui_event(
                    "PADDLE_OCR_PREPARE_BLOCKS_LIVE",
                    f"state={self.paddle_ocr_prepare_state}; backend={selected_ocr_backend}; prepared_backend={self.paddle_ocr_prepare_backend}",
                )
                log_paddle_ocr_event(
                    "PADDLE_OCR_PREPARE_BLOCKS_LIVE",
                    backend=selected_ocr_backend,
                    state=self.paddle_ocr_prepare_state,
                    prepared_backend=self.paddle_ocr_prepare_backend,
                    translation_mode_on=self.is_click_through,
                )
                try:
                    log_startup_event(
                        "PADDLE_OCR_PREPARE_BLOCKS_LIVE",
                        backend=selected_ocr_backend,
                        state=self.paddle_ocr_prepare_state,
                        prepared_backend=self.paddle_ocr_prepare_backend,
                    )
                except Exception:
                    pass
                return

            self.translation_output_hidden = False
            self.dynamic_hidden_by_dynamic = False

            if self.dynamic_only_mode:
                # Отдельный динамический режим: постоянный субтитровый оверлей не нужен.
                # Прячем панель полностью. QTimer с hotkey-polling продолжает работать,
                # поэтому пользователь может вызывать dynamic hotkey и возвращаться в настройки.
                self.hide()
                self.enter_live_gameplay_mode(target_hwnd, "enter_dynamic_only_gameplay")
                return

            self.control_widget.hide()
            self.sizer_grip.hide()
            self.lbl_translated.show()
            self.lbl_translated.setText("")
            self.apply_live_overlay_auto_fit("", reason="gameplay_enter")

            self.enter_live_gameplay_mode(target_hwnd, "enter_gameplay")

            self.engine_thread.update_session_data(self.session_config)
            self.engine_thread.set_user_interaction_paused(False)
            self.engine_thread.start_engine(target_hwnd)

        else:
            # Возврат из рабочего режима в режим настройки.
            if self.dynamic_only_mode:
                # В отдельном динамическом режиме engine_thread не запускался.
                self.translation_output_hidden = False
                self.control_widget.show()
                self.sizer_grip.show()
                self.lbl_translated.show()
                self.lbl_translated.setText(
                    tr(self.interface_lang, "overlay_setup_mode", hotkey=self.get_hotkey_display("toggle_translation_mode"))
                )
                self.show()
                self.enter_live_edit_mode("enter_dynamic_only_edit")
                self.update()
                return

            self.engine_thread.stop_engine()
            self.translation_output_hidden = False
            self.dynamic_hidden_by_dynamic = False
            self.control_widget.show()
            self.sizer_grip.show()
            self.lbl_translated.show()
            self.lbl_translated.setText(tr(self.interface_lang, "overlay_setup_mode", hotkey=self.get_hotkey_display("toggle_translation_mode")))
            self.apply_live_overlay_auto_fit("", reason="edit_enter")

            self.show()
            self.enter_live_edit_mode("enter_edit")
            # Native EDIT state is already active before any bounded engine wait.
            if self.is_paddle_ocr_selected():
                self.engine_thread.wait(5)
            else:
                self.engine_thread.wait(1500)

        self.update()

    # Алиас оставлен специально, чтобы старые вызовы не падали,
    # если где-то в проекте ещё осталась ссылка на старое имя метода.
    def toggle_click_through_mode(self):
        self.toggle_translation_mode()

    def on_adaptive_plate_sample(self, sample):
        scope, revision, rgb = sample
        if (scope == capture_scope(self.session_config)
                and revision == getattr(self.engine_thread, "_adaptive_capture_revision", 0)):
            self._adaptive_plate_sample = sample
            self.update()

    def current_adaptive_plate_rgb(self):
        sample = getattr(self, "_adaptive_plate_sample", None)
        if (sample and sample[0] == capture_scope(self.session_config)
                and sample[1] == getattr(self.engine_thread, "_adaptive_capture_revision", 0)):
            return sample[2]
        return None

    def on_adaptive_plate_toggled(self, enabled):
        self.apply_visual_settings({**self.session_config.get("visuals", {}), "adaptive_plate_enabled": bool(enabled)})

    def apply_visual_settings(self, visuals):
        adaptive_changed = bool(self.session_config.get("visuals", {}).get("adaptive_plate_enabled", False)) != bool(visuals.get("adaptive_plate_enabled", False))
        self.session_config["visuals"] = dict(visuals)
        self.adaptive_plate_toggle.blockSignals(True)
        self.adaptive_plate_toggle.setChecked(bool(visuals.get("adaptive_plate_enabled", False)))
        self.adaptive_plate_toggle.blockSignals(False)
        self.lbl_translated.setStyleSheet(f"color: {visuals.get('font_color', '#FFFFFF')}; line-height: 1.3;")
        self.lbl_translated.set_visuals(visuals)
        self.apply_live_overlay_auto_fit(self.lbl_translated.text(), reason="visual_settings")
        # Visual-only changes must not enter OCR/translation preparation paths.
        with QMutexLocker(self.engine_thread.mutex):
            if adaptive_changed:
                self._adaptive_plate_sample = None
                self.engine_thread._adaptive_capture_revision = getattr(self.engine_thread, "_adaptive_capture_revision", 0) + 1
            self.engine_thread.session_data["visuals"] = dict(visuals)
        popup = getattr(self, "dynamic_result_popup", None)
        popup_visuals = {key: value for key, value in visuals.items() if key != "adaptive_plate_enabled"}
        if popup is not None and popup.visuals != popup_visuals:
            popup.visuals = popup_visuals
            popup.label.setStyleSheet(f"color: {visuals.get('font_color', '#FFFFFF')}; line-height: 1.25;")
            popup.label.set_visuals(popup_visuals)
            dynamic_cfg = normalize_dynamic_settings(self.session_config.get("dynamic", {}))
            rect, font_size = self.calculate_dynamic_popup_layout(popup.label.text(), getattr(popup, "source_rect", popup.geometry()), dynamic_cfg, visuals)
            popup.setGeometry(rect)
            popup.label.setFont(QFont(visuals.get("font_name", "Arial"), font_size, QFont.Weight.Bold))
            popup.update()
        self.save_profile_to_disk()
        self.update()

    def open_text_settings(self):
        from launcher import TextSettingsDialog
        dialog = TextSettingsDialog(self.session_config.get("visuals", {}), self, self.interface_lang)
        if dialog.exec():
            self.apply_visual_settings(dialog.result_settings)

    def apply_visual_localization(self):
        if hasattr(self, "online_settings_button"):
            self.online_settings_button.setToolTip(tr(self.interface_lang, "online_settings"))
            self.online_settings_button.setAccessibleName(tr(self.interface_lang, "online_settings"))
        self.adaptive_plate_toggle.setText(tr(self.interface_lang, "adaptive_plate"))
        self.adaptive_plate_toggle.setToolTip(tr(self.interface_lang, "adaptive_plate_tooltip"))
        self.btn_text_settings.setToolTip(tr(self.interface_lang, "text_settings_button"))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self.live_mouse_mode == LiveOverlayMouseMode.EDIT:
            # Режим настройки: видимая рамка, которую можно двигать и менять по размеру.
            painter.setPen(QPen(QColor(0, 255, 0, 220), 3, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(0, 0, 0, 80))
            painter.drawRect(self.rect())
        else:
            # Режим перевода: полупрозрачная подложка под текст.
            # Раньше это было жёстко задано через config.DRAW_BACKGROUND_PLATE.
            # Теперь лаунчер передаёт визуальные настройки через session_config["visuals"],
            # чтобы пользователь мог менять фон и прозрачность в отдельном окне настроек текста.
            if not self.translation_output_hidden:
                visuals = {"draw_background_plate": config.DRAW_BACKGROUND_PLATE, **(self.session_config.get("visuals") or {})}
                paint_plate(painter, self.rect(), visuals, self.current_adaptive_plate_rgb())

    @pyqtSlot()
    def on_model_warmup_done(self):
        if self.live_mouse_mode == LiveOverlayMouseMode.EDIT:
            ocr_prepare_key = self.current_ocr_prepare_status_key()
            if ocr_prepare_key:
                self.set_ocr_prepare_status_text(ocr_prepare_key)
            else:
                self.set_idle_translation_hint("model_warmup_done")

    @pyqtSlot()
    def on_subtitle_stale_grace_expired(self):
        sequence_id = int(getattr(self, "_subtitle_stale_sequence_id", 0) or 0)
        self._subtitle_stale_sequence_id = 0
        if sequence_id:
            result = self.engine_thread.expire_subtitle_stale_grace(sequence_id) or {}
            reschedule_ms = int(result.get("reschedule_ms", 0) or 0)
            if reschedule_ms > 0:
                self.schedule_subtitle_stale_grace(sequence_id, reschedule_ms)

    @pyqtSlot(int, int)
    def schedule_subtitle_stale_grace(self, sequence_id, delay_ms):
        sequence_id = int(sequence_id or 0)
        delay_ms = int(delay_ms or 0)
        if sequence_id <= 0 or delay_ms <= 0:
            return
        if (
            self.subtitle_stale_grace_timer.isActive()
            and self._subtitle_stale_sequence_id == sequence_id
        ):
            return
        self._subtitle_stale_sequence_id = sequence_id
        self.subtitle_stale_grace_timer.start(max(1, delay_ms))

    @pyqtSlot(int, int)
    def schedule_ocr_growth_pending_deadline(self, token, delay_ms):
        self._ocr_growth_pending_timer_token = int(token or 0)
        if int(delay_ms or 0) <= 0:
            self.ocr_growth_pending_timer.stop()
            return
        self.ocr_growth_pending_timer.start(max(1, int(delay_ms)))

    @pyqtSlot()
    def on_ocr_growth_pending_deadline(self):
        token = int(getattr(self, "_ocr_growth_pending_timer_token", 0) or 0)
        if token:
            self.engine_thread.mark_ocr_growth_pending_timer_fired(token)

    @pyqtSlot(str, str, float)
    def update_translation_ui(self, source, translated, proc_time):
        receive_started_perf = time.perf_counter()
        if self._session_stopping:
            return
        if translated.strip() and not self.engine_thread.is_ui_translation_result_current(source, translated):
            self.engine_thread.log_runtime_event(
                "TRANSLATION_RESULT_DROPPED_VISUAL_STALE",
                text=source,
                reason="queued UI signal no longer owns current apply token",
                extra="stage=gui_apply",
            )
            return
        token = self.engine_thread.current_ui_translation_result_token(source, translated) or {}
        if not translated.strip():
            self.subtitle_stale_grace_timer.stop()
            self._subtitle_stale_sequence_id = 0
        else:
            # Любой новый валидный перевод немедленно заменяет stale-текст.
            self.subtitle_stale_grace_timer.stop()
            self._subtitle_stale_sequence_id = 0
        emitted_perf = float(token.get("ui_signal_emitted_perf") or 0.0)
        queue_wait_until_main_ms = round(
            max(0.0, receive_started_perf - emitted_perf) * 1000.0,
            3,
        ) if emitted_perf else 0.0
        # Сохраняем последний удачный вывод независимо от того, скрыта плашка или нет.
        # Это позволяет нажать Ctrl+Alt+Shift+H повторно и вернуть актуальный перевод.
        self.last_displayed_source = source or ""
        self.last_displayed_translation = translated or ""
        self.last_displayed_updated_at = time.time()
        main_result_receive_ms = round(
            (time.perf_counter() - receive_started_perf) * 1000.0,
            3,
        )

        if self.live_mouse_mode != LiveOverlayMouseMode.GAMEPLAY:
            # Если PaddleOCR закончил старую задачу уже после возврата в режим настройки,
            # не даём позднему результату перезаписать служебный текст настройки.
            return

        if self.translation_output_hidden:
            # В режиме временного скрытия не очищаем и не обновляем QLabel на экране.
            # OCR/перевод продолжают работать в фоне, но игрок видит оригинал игры.
            return

        overlay_apply_started_perf = time.perf_counter()
        if not translated.strip():
            self.lbl_translated.setText("")
            self.apply_live_overlay_auto_fit("", reason="translation_cleared")
        else:
            self.lbl_translated.show()
            self.lbl_translated.setText(translated)
            self.apply_live_overlay_auto_fit(translated, reason="translation_publish")
        overlay_apply_ms = round(
            (time.perf_counter() - overlay_apply_started_perf) * 1000.0,
            3,
        )
        pipeline_started_perf = float(token.get("pipeline_started_perf") or 0.0)
        time_to_publish_ms = round(
            (time.perf_counter() - pipeline_started_perf) * 1000.0,
            3,
        ) if pipeline_started_perf else 0.0
        timings = dict(token.get("pipeline_timings") or {})
        timings.update(
            {
                "queue_wait_until_main_ms": queue_wait_until_main_ms,
                "main_result_receive_ms": main_result_receive_ms,
                "overlay_apply_ms": overlay_apply_ms,
                "time_to_publish_ms": time_to_publish_ms,
            }
        )
        translator_core.log_backend_pipeline_event(
            token.get("translator_backend_name"),
            "pipeline_result_published",
            {
                "ocr_result_id": token.get("ocr_result_id"),
                "source_hash": token.get("source_hash"),
                "source_char_count": len(str(source or "")),
                "backend_name": token.get("translator_backend_name"),
                "backend_request_count": len(token.get("backend_requests") or []),
                **timings,
            },
        )
        self.engine_thread.log_runtime_event(
            "TRANSLATION_PIPELINE_PUBLISHED",
            task_id=token.get("task_id"),
            generation=token.get("generation"),
            reason="translation label updated",
            extra=(
                f"ocr_result_id={token.get('ocr_result_id')}; source_hash={token.get('source_hash')}; "
                f"queue_wait_until_main_ms={queue_wait_until_main_ms}; main_result_receive_ms={main_result_receive_ms}; "
                f"overlay_apply_ms={overlay_apply_ms}; time_to_publish_ms={time_to_publish_ms}"
            ),
        )
        self.engine_thread.log_online_publication(token, "published", "visual_stable")

    def start_async_overlay_close(self):
        if self._session_stopping:
            return

        self.capture_thread_growth_snapshot("SHUTDOWN_BEGIN")
        self._session_stopping = True
        self._shutdown_started_at = time.perf_counter()
        self.hide_force_refresh_status()
        self.engine_thread.log_runtime_event("OVERLAY_STOP_REQUESTED")
        self.release_live_overlay_capture(
            previous_mode=self.live_mouse_mode,
            new_mode=self.live_mouse_mode,
            reason="session_stopping",
        )
        self.cancel_cursor_restore_checks("session_stopping")
        self.cancel_active_dynamic_request("session_stopping", close_windows=True)
        for interaction_kind in list(
            getattr(self, "_window_interaction_contexts", {})
        ):
            self.finish_window_interaction(interaction_kind, "session_stopping")
        self._dynamic_live_pause_owned = False
        worker = self.dynamic_worker
        if worker is not None:
            try:
                worker.stop_worker("session_stopping")
            except Exception:
                pass

        for timer_name in (
            "hotkey_timer",
            "geometry_resume_timer",
            "subtitle_stale_grace_timer",
            "dynamic_capture_timer",
            "dynamic_restore_timer",
            "force_refresh_status_timer",
            "live_overlay_refit_timer",
        ):
            try:
                timer = getattr(self, timer_name, None)
                if timer is not None:
                    timer.stop()
            except Exception:
                pass

        try:
            self.engine_thread.begin_session_stopping()
        except Exception as exc:
            print(f"[CLOSE ENGINE INVALIDATION ERROR]: {exc}", flush=True)

        self.engine_thread.log_runtime_event("OVERLAY_HIDE_REQUESTED")
        hide_started = time.perf_counter()
        for window_name in ("dynamic_selection_overlay", "dynamic_result_popup"):
            try:
                window = getattr(self, window_name, None)
                if window is not None:
                    if hasattr(window, "stop_input_capture_timers"):
                        window.stop_input_capture_timers()
                    if hasattr(window, "close_timer"):
                        window.close_timer.stop()
                    window.hide()
            except Exception:
                pass
        self.hide()
        app = QApplication.instance()
        if app is not None:
            app.processEvents()
        hide_ms = int((time.perf_counter() - hide_started) * 1000)
        self.engine_thread.log_runtime_event(
            "OVERLAY_HIDE_COMPLETED",
            extra=f"elapsed_ms={hide_ms}; visible={self.isVisible()}",
        )

        try:
            self.save_profile_to_disk()
        except Exception as exc:
            print(f"[CLOSE SAVE ERROR]: {exc}", flush=True)
        print("__OVERLAY_CLOSE_REQUESTED__", flush=True)

        self.engine_thread.log_runtime_event("ENGINE_ASYNC_SHUTDOWN_STARTED")
        self._shutdown_thread = OverlayAsyncShutdownThread(
            self.engine_thread,
            self.model_warmup_thread,
            self.paddle_ocr_prepare_thread,
            getattr(self, "storage_prepare_thread", None),
            self.dynamic_worker,
        )
        self._shutdown_thread.stage_signal.connect(self.on_async_shutdown_stage)
        self._shutdown_thread.completed_signal.connect(self.on_async_shutdown_completed)
        self._shutdown_thread.start()

    @pyqtSlot(str, object)
    def on_async_shutdown_stage(self, event_name, details):
        extra = ""
        if isinstance(details, dict):
            extra = "; ".join(f"{key}={value}" for key, value in details.items())
        self.engine_thread.log_runtime_event(str(event_name), extra=extra)

    @pyqtSlot(object)
    def on_async_shutdown_completed(self, result):
        details = result if isinstance(result, dict) else {}
        elapsed_ms = int((time.perf_counter() - self._shutdown_started_at) * 1000)
        self.engine_thread.log_runtime_event(
            "OVERLAY_SHUTDOWN_COMPLETED",
            extra=(
                f"elapsed_ms={elapsed_ms}; shutdown_total_ms={details.get('total_ms', 0)}; "
                f"errors={details.get('errors', [])}; backends={details.get('translation_backends', {})}"
            ),
        )
        self.engine_thread.log_runtime_event(
            "OVERLAY_PROCESS_EXIT_REQUESTED",
            extra=(
                f"elapsed_ms={elapsed_ms}; shutdown_total_ms={details.get('total_ms', 0)}; "
                f"errors={details.get('errors', [])}"
            ),
        )
        self.capture_thread_growth_snapshot("SHUTDOWN_END", extra={"elapsed_ms": elapsed_ms})
        startup_block3_diagnostics.finalize_startup_diagnostics("overlay_shutdown_completed")
        print("__OVERLAY_SHUTDOWN_COMPLETED__", flush=True)
        app = QApplication.instance()
        if app is not None:
            app.exit(0)

    def mousePressEvent(self, event):
        if self.live_mouse_mode == LiveOverlayMouseMode.EDIT and event.button() == Qt.MouseButton.LeftButton:
            self.pause_capture_for_geometry_adjustment()
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if (
            self.live_mouse_mode == LiveOverlayMouseMode.EDIT
            and event.buttons() == Qt.MouseButton.LeftButton
            and self.drag_position is not None
        ):
            self.pause_capture_for_geometry_adjustment()
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        # После перетаскивания фиксируем координаты в session_config.
        if self.live_mouse_mode == LiveOverlayMouseMode.EDIT:
            self.sync_geometry_to_session()
            if hasattr(self, "geometry_resume_timer"):
                self.geometry_resume_timer.start(120)
        self.drag_position = None
        event.accept()

    def closeEvent(self, event):
        """
        Событие гарантированного закрытия оверлея.

        Исправление v4 по твоему отчёту:
        окно оверлея закрывалось, но launcher.py не появлялся.
        Причина: процесс оверлея мог оставаться живым из-за фонового QThread
        предзагрузки модели. Пока процесс жив, QProcess.finished в лаунчере не вызывается.

        Поэтому при закрытии делаем жёсткий, но контролируемый порядок:
        1. сохраняем профиль;
        2. печатаем маркер для launcher.py, что закрытие запрошено пользователем;
        3. останавливаем таймер хоткея;
        4. останавливаем движок перевода;
        5. если поток прогрева модели ещё жив — завершаем его принудительно;
        6. просим QApplication выйти.
        """
        try:
            self.save_profile_to_disk()
        except Exception as e:
            print(f"[CLOSE SAVE ERROR]: {e}", flush=True)

        # Этот маркер читает launcher.py. Если процесс оверлея по какой-то причине
        # не завершится сам, лаунчер всё равно сможет вернуть своё окно и добить процесс.
        print("__OVERLAY_CLOSE_REQUESTED__", flush=True)

        try:
            if hasattr(self, "hotkey_timer") and self.hotkey_timer:
                self.hotkey_timer.stop()
        except Exception:
            pass

        try:
            self.engine_thread.stop_engine()
            if not self.engine_thread.wait(2000):
                print("[CLOSE] TranslationEngine не остановился штатно. Принудительное завершение.", flush=True)
                self.engine_thread.terminate()
                self.engine_thread.wait(1000)
        except Exception as e:
            print(f"[CLOSE ENGINE ERROR]: {e}", flush=True)

        self.start_async_overlay_close()
        event.ignore()
        return

        try:
            translator_core.cancel_active_translation(reason="overlay_close")
            translator_core.shutdown_backends()
        except Exception as e:
            print(f"[CLOSE TRANSLATION BACKEND ERROR]: {e}", flush=True)

        try:
            if self.model_warmup_thread and self.model_warmup_thread.isRunning():
                if not self.model_warmup_thread.wait(2500):
                    print("[CLOSE] Поток прогрева модели ещё работает. Принудительное завершение.", flush=True)
                    self.model_warmup_thread.terminate()
                    self.model_warmup_thread.wait(1500)
        except Exception as e:
            print(f"[CLOSE WARMUP ERROR]: {e}", flush=True)

        try:
            if self.paddle_ocr_prepare_thread and self.paddle_ocr_prepare_thread.isRunning():
                print("[CLOSE] Paddle OCR prepare thread is still running. Terminating.", flush=True)
                self.paddle_ocr_prepare_thread.terminate()
                self.paddle_ocr_prepare_thread.wait(1500)
        except Exception as e:
            print(f"[CLOSE PADDLE OCR PREPARE ERROR]: {e}", flush=True)

        try:
            if self.dynamic_worker and self.dynamic_worker.isRunning():
                print("[CLOSE] Поток динамического перевода ещё работает. Принудительное завершение.", flush=True)
                self.dynamic_worker.terminate()
                self.dynamic_worker.wait(1000)
            if self.dynamic_selection_overlay:
                self.dynamic_selection_overlay.close()
            if self.dynamic_result_popup:
                self.dynamic_result_popup.close()
        except Exception as e:
            print(f"[CLOSE DYNAMIC ERROR]: {e}", flush=True)

        event.accept()

        # Если это последнее окно, Qt обычно сам завершает приложение.
        # Но из-за фоновых потоков/Tool-window это не всегда происходит предсказуемо,
        # поэтому явно просим event loop завершиться.
        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(0, app.quit)




def install_crash_logging():
    """
    Дополнительный лог ошибок оверлея.

    launcher.py видит только stdout/stderr процесса. Если Python падает обычным исключением,
    traceback должен попасть в crash_log.txt через лаунчер. Но если падает нативная часть
    Qt/torch/драйвера/WinAPI, traceback может не появиться. faulthandler даёт шанс увидеть
    хотя бы место нативного падения в overlay_native_crash.log.
    """
    if not session_logging.raw_enabled():
        return
    try:
        fault_path = log_file("overlay_native_crash.log")
        fault_handle = session_logging.open_log(fault_path, "w", encoding="utf-8")
        faulthandler.enable(file=fault_handle, all_threads=True)
        # Держим handle живым до конца процесса.
        globals()["_overlay_fault_handle"] = fault_handle
    except Exception:
        pass

    def _excepthook(exc_type, exc_value, exc_tb):
        session_logging.event("UNHANDLED_EXCEPTION")
        try:
            path = log_file("overlay_exception.log")
            with session_logging.open_log(path, "w", encoding="utf-8") as f:
                traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
        except Exception:
            pass

        traceback.print_exception(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook
    def _thread_excepthook(args):
        log_startup_event("UNHANDLED_BACKGROUND_EXCEPTION", pid=os.getpid(), thread=args.thread.name,
                          native_thread_id=getattr(args.thread, "native_id", None))
        _excepthook(args.exc_type, args.exc_value, args.exc_traceback)
    threading.excepthook = _thread_excepthook

if __name__ == "__main__":
    log_startup_event("PROCESS_ENTRY")
    thread_growth_diagnostics.capture_thread_snapshot("PROCESS_BASELINE")
    install_crash_logging()
    configure_winapi_signatures()

    if sys.platform == "win32":
        try:
            # DPI awareness нужен, чтобы координаты окна/оверлея не ехали на мониторах
            # с масштабированием Windows 125% / 150%.
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass

    if len(sys.argv) < 2:
        print("Использование: main.py <session_config_json_path>")
        sys.exit(1)

    session_cfg_path = str(DATA.validate(sys.argv[1]))

    try:
        log_startup_event("QAPPLICATION_CREATE_BEGIN")
        app = responsiveness.application_class()(sys.argv)
        responsiveness.install_ui_probe(app)
        log_startup_event("QAPPLICATION_CREATE_END")

        # Критично для чистого динамического режима.
        # В этом режиме основное окно оверлея может быть скрыто, а временный слой
        # выделения на короткий момент закрывается до появления окна результата.
        # Если оставить стандартное поведение Qt, QApplication может решить, что
        # "последнее окно закрыто", завершить процесс оверлея и вернуть лаунчер.
        # Поэтому автозавершение по закрытию последнего окна отключаем, а реальный
        # выход из приложения по-прежнему выполняется явно в closeEvent основного
        # GameTranslatorOverlay через app.quit().
        app.setQuitOnLastWindowClosed(False)
        app.aboutToQuit.connect(lambda: log_startup_event("PROCESS_SHUTDOWN_BEGIN"))
        app.aboutToQuit.connect(translator_core.shutdown_backends)

        log_startup_event("MAIN_WINDOW_CREATE_BEGIN")
        log_startup_event("OVERLAY_CREATE_START")
        overlay = GameTranslatorOverlay(session_cfg_path)
        log_startup_event("OVERLAY_CREATE_DONE")
        log_startup_event("MAIN_WINDOW_CREATE_END")
        log_startup_event("OVERLAY_SHOW_START")
        overlay.show()
        overlay.initialize_live_mouse_mode()
        log_startup_event("OVERLAY_SHOW_DONE")

        def mark_overlay_event_loop_ready():
            log_startup_event("READY_MARKER_CREATE_BEGIN")
            write_overlay_ready_marker()
            log_startup_event("READY_MARKER_CREATE_END")
            QTimer.singleShot(0, overlay.start_post_ready_initialization)

        QTimer.singleShot(0, mark_overlay_event_loop_ready)
        log_startup_event("QT_EVENT_LOOP_ENTER")
        sys.exit(app.exec())
    except Exception:
        traceback.print_exc()
        raise
