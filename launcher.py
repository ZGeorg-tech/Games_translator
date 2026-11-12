import sys
sys.dont_write_bytecode = True
import os
import json
import re
import time
import hashlib
import unicodedata

from console_output import configure_unicode_safe_console
from resource_registry import resource_path
from python_runtime import python_command
from data_paths import DATA, data_path, log_file, initialize_data, rebase_profile_path
import session_logging
from online_translation import normalize_settings as normalize_online_settings, validate_configuration as validate_online_configuration, OnlineError
from quality_runtime_selector import detect_quality_runtime, log_quality_runtime_selection, quality_unavailable_message


LAUNCHER_SETTINGS_FILE = str(data_path("settings", "launcher_settings.json"))


def read_launcher_settings():
    try:
        with open(LAUNCHER_SETTINGS_FILE, "r", encoding="utf-8") as handle:
            settings = json.load(handle)
            return settings if isinstance(settings, dict) else {}
    except (OSError, ValueError):
        return {}


def launcher_log_policy(settings):
    return "developer" if settings.get("developer_logging") is True else "off"


if __name__ == "__main__":
    configure_unicode_safe_console()
    initialize_data()
    os.environ[session_logging.POLICY_ENV] = launcher_log_policy(read_launcher_settings())
    os.environ.pop(session_logging.SESSION_ENV, None)
    session_logging.ensure_session("launcher")
    session_logging.install_launcher_logging()

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QGroupBox, QFormLayout, QSpinBox, QDoubleSpinBox,
    QDialog, QMessageBox, QColorDialog, QCheckBox, QTextEdit, QPlainTextEdit, QProgressBar, QScrollArea, QStyle
)
from PyQt6.QtCore import Qt, QProcess, QTimer, QSize, QProcessEnvironment
from PyQt6.QtGui import QFont, QIcon, QColor
from launcher_style import apply_launcher_style, status_style, WARNING_DOCUMENT_CSS

import config
from language_blocks import (
    BLOCK1,
    BLOCK2,
    BLOCK3,
    BLOCKS_KEY,
    apply_block_state,
    atomic_write_json,
    block_id_for_language_mode,
    ensure_language_blocks,
    fast_ocr_engine_for_block,
    normalize_block_state,
    migrate_block1_backend,
    normalize_quality_ocr_engine,
    ocr_engine_for_translation_profile,
    replace_block_state,
)
from game_field_visual_gate import DEFAULT_GAME_FIELD_CONFIG, normalize_game_field_config
from localization import tr, interface_lang_button_text, interface_lang_icon_file, next_interface_lang, normalize_interface_lang
from subtitle_visuals import SubtitleLabel
from subtitle_sequence_gate import (
    FAST_SUBTITLES,
    FOLLOW_SOURCE,
    GAME_BACKGROUND,
    STATIC_PANEL,
    normalize_subtitle_settings,
)
from translation_backends.translategemma_llama_backend import (
    preflight_translategemma_llama_q6,
)
from translation_backends.block2_bergamot_backend import preflight_block2_bergamot
from translation_backends.bergamot_backend import preflight_bergamot_target
from translation_backends.milmmt_backend import preflight_milmmt
from translation_backends.block3_router_backend import preflight_block3_route

# Абсолютные пути важны: если приложение запустить из ярлыка или IDE,
# относительные пути могут уехать в другую рабочую папку.
APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILES_DIR = str(data_path("profiles"))
SESSION_FILE = str(DATA.temp("launcher") / "session_config.json")
ASSETS_DIR = str(resource_path("app.assets"))
ICONS_DIR = os.path.join(ASSETS_DIR, "icons")

LAUNCHER_TIMING_LOG = str(data_path("logs", "launcher_timing.log"))


def normalize_profile_display_name(value):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(value or ""))).strip()


def normalize_profile_path(value):
    if not value:
        return ""
    return os.path.normcase(os.path.normpath(os.path.abspath(rebase_profile_path(str(value).strip()))))


def profile_path_is_app_local(value):
    normalized = normalize_profile_path(value)
    if not normalized:
        return False
    try:
        root = os.path.realpath(normalize_profile_path(PROFILES_DIR))
        return os.path.commonpath([os.path.realpath(normalized), root]) == root
    except Exception:
        return False


def stable_profile_id(value):
    normalized = normalize_profile_path(value)
    return hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()[:20] if normalized else ""


def classify_overlay_exit(
    *,
    exit_code,
    crashed,
    traceback_present,
    user_requested_close,
    shutdown_completed,
    forced_kill,
):
    clean_user_stop = bool(
        user_requested_close
        and shutdown_completed
        and not forced_kill
        and int(exit_code) == 0
        and not crashed
        and not traceback_present
    )
    shutdown_timeout = bool(user_requested_close and forced_kill)
    abnormal_exit = bool(int(exit_code) != 0 or crashed or traceback_present)
    unexpected_crash = bool(
        not forced_kill and (int(exit_code) != 0 or crashed or traceback_present)
    )
    return {
        "clean_user_stop": clean_user_stop,
        "shutdown_timeout": shutdown_timeout,
        "unexpected_crash": unexpected_crash,
        "failed_exit": abnormal_exit and not shutdown_timeout,
        "reason": (
            "shutdown_timeout_forced_kill"
            if shutdown_timeout
            else "forced_process_kill"
            if forced_kill
            else "unexpected_crash"
            if unexpected_crash and not user_requested_close
            else "shutdown_failed"
            if unexpected_crash
            else "shutdown_incomplete"
            if user_requested_close and not shutdown_completed
            else "clean_exit"
        ),
    }


def log_launcher_timing(event, **details):
    """Компактный лог пути от кнопки лаунчера до старта main.py."""
    try:
        session_logging.diagnostic_event(event, **details)
        if not session_logging.raw_enabled():
            return
        path = log_file("launcher_timing.log")
        extra = ""
        if details:
            extra = " " + " ".join(f"{k}={v}" for k, v in details.items())
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with session_logging.open_log(path, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} EVENT={event}{extra}\n")
    except Exception:
        pass


def normalize_profile_subtitle_settings(raw_settings, *, profile_id=""):
    return normalize_subtitle_settings(raw_settings)


# --- ГОРЯЧИЕ КЛАВИШИ ПО УМОЛЧАНИЮ ---
# Эти сочетания выбраны как безопасные: они редко используются играми.
# Пользователь сможет переназначить их в лаунчере под конкретную игру.
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

# --- НАСТРОЙКИ ТЕКСТА ПО УМОЛЧАНИЮ ---
# Это настройки вывода перевода в оверлее. Они хранятся в профиле игры
# и передаются в main.py через session_config["visuals"].
# На этом шаге даём пользователю только безопасные визуальные параметры,
# которые уже поддерживаются оверлеем или легко применяются без ломки архитектуры.
DEFAULT_TEXT_SETTINGS = {
    "font_name": config.FONT_NAME,
    "font_size": config.FONT_SIZE,
    "font_color": config.FONT_COLOR,
    "outline_color": config.OUTLINE_COLOR,
    "outline_thickness": config.OUTLINE_THICKNESS,
    "draw_background_plate": config.DRAW_BACKGROUND_PLATE,
    "background_opacity": 135,
}

# --- НАСТРОЙКИ ДИНАМИЧЕСКОГО ПЕРЕВОДА ПО УМОЛЧАНИЮ ---
# Динамический перевод — это разовый перевод области, которую пользователь
# выделяет мышью поверх игры. Это не заменяет постоянный перевод субтитров,
# а дополняет его для кнопок выбора, записок, интерфейса и случайного текста.
DEFAULT_DYNAMIC_SETTINGS = {
    "enabled": True,
    "display_time_sec": 6,
    "output_mode": "near",  # near / over
    # combined  — динамика как дополнение к обычному субтитровому оверлею;
    # standalone — отдельный динамический режим без постоянной рамки субтитров.
    "mode": "combined",
    # В combined-режиме пользователь сам решает: прятать основной перевод
    # на время выделения динамической области или оставлять его видимым.
    "hide_main_overlay": True,
    "auto_fit_enabled": True,
    "max_rect_scale": 2.0,
    "min_font_size": 12,
}


# --- НАСТРОЙКИ LLM-РЕДАКТОРА ПО УМОЛЧАНИЮ ---
# Это только каркас подключаемого LLM-слоя.
# По умолчанию выключен и не влияет на текущий рабочий перевод.
DEFAULT_LLM_SETTINGS = {
    "enabled": False,
    "mode": "off",          # off / strict / literary / direct
    "backend": "none",      # none / llama_cpp_server позже
    "context_lines": 5,
    "timeout_sec": 8,
    "endpoint": "http://127.0.0.1:8080/completion",
    "apply_to_live": True,
    "apply_to_dynamic": True,
}


def normalize_llm_settings(raw_settings):
    """Мягкая валидация LLM-настроек из профиля/лаунчера."""
    result = dict(DEFAULT_LLM_SETTINGS)
    if isinstance(raw_settings, dict):
        result.update(raw_settings)

    mode = str(result.get("mode", "off") or "off").lower().strip()
    if mode != "off":
        mode = "off"
    result["mode"] = mode
    result["enabled"] = bool(result.get("enabled", False)) and mode != "off"

    backend = str(result.get("backend", "none") or "none").lower().strip()
    if backend not in {"none", "llama_cpp_server"}:
        backend = "none"
    result["backend"] = backend

    try:
        result["context_lines"] = max(0, min(10, int(result.get("context_lines", 5))))
    except Exception:
        result["context_lines"] = 5

    try:
        result["timeout_sec"] = max(1, min(120, int(result.get("timeout_sec", 8))))
    except Exception:
        result["timeout_sec"] = 8

    result["endpoint"] = str(result.get("endpoint") or DEFAULT_LLM_SETTINGS["endpoint"]).strip()
    result["apply_to_live"] = bool(result.get("apply_to_live", True))
    result["apply_to_dynamic"] = bool(result.get("apply_to_dynamic", True))
    return result




DEFAULT_RESOURCE_MONITOR_SETTINGS = {
    "enabled": True,
    "interval_sec": 1.0,
    "include_self": True,
    "nvidia": True,
    "process_names": ["llama-server.exe"],
}


def normalize_resource_monitor_settings(raw_settings):
    """Мягкая валидация настроек мониторинга ресурсов."""
    result = dict(DEFAULT_RESOURCE_MONITOR_SETTINGS)
    if isinstance(raw_settings, dict):
        result.update(raw_settings)
    result["enabled"] = bool(result.get("enabled", True))
    result["include_self"] = bool(result.get("include_self", True))
    result["nvidia"] = bool(result.get("nvidia", True))
    try:
        result["interval_sec"] = max(0.5, min(10.0, float(result.get("interval_sec", 1.0))))
    except Exception:
        result["interval_sec"] = 1.0
    names = result.get("process_names")
    if not isinstance(names, list):
        names = ["llama-server.exe"]
    result["process_names"] = [str(x).strip() for x in names if str(x).strip()] or ["llama-server.exe"]
    return result


def llm_settings_from_mode(mode):
    """Создаёт настройки LLM по выбранному пункту простого UI."""
    mode = str(mode or "off").lower().strip()
    if mode != "off":
        mode = "off"

    result = dict(DEFAULT_LLM_SETTINGS)
    result["mode"] = mode
    result["enabled"] = mode != "off"

    result["backend"] = "llama_cpp_server" if mode != "off" else "none"
    result["endpoint"] = DEFAULT_LLM_SETTINGS["endpoint"]
    return normalize_llm_settings(result)


# --- ЯЗЫКОВЫЕ РЕЖИМЫ ЛАУНЧЕРА ---
# Это каркас будущих продуктовых блоков. Сейчас он меняет только настройки сессии:
# source_lang / target_lang / допустимые OCR-движки. Новые переводчики и OCR-модели
# будут подключаться следующими шагами через эти же поля, без переписывания UI.
LANGUAGE_MODE_EURO = "euro_en_to_ru_uk"
LANGUAGE_MODE_ASIAN_TO_EURO = "asian_to_en_ru_uk"
LANGUAGE_MODE_EURO_TO_ASIAN = "euro_en_to_asian"

LOCAL_TRANSLATOR_AUTO = "auto"
LOCAL_TRANSLATOR_BERGAMOT = "bergamot"
LOCAL_TRANSLATOR_CURRENT_BASE = "current_base"
LOCAL_TRANSLATOR_TRANSLATEGEMMA_LLAMA_Q6 = "translategemma_llama_q6_low_vram"
LOCAL_TRANSLATOR_TRANSLATEGEMMA_LLAMA_Q6_SPEED = "translategemma_llama_q6_speed"
LOCAL_TRANSLATOR_OPTIONS = (
    LOCAL_TRANSLATOR_AUTO,
    LOCAL_TRANSLATOR_BERGAMOT,
    LOCAL_TRANSLATOR_CURRENT_BASE,
    LOCAL_TRANSLATOR_TRANSLATEGEMMA_LLAMA_Q6,
    LOCAL_TRANSLATOR_TRANSLATEGEMMA_LLAMA_Q6_SPEED,
)
BLOCK2_PROFILE_FAST = "fast"
BLOCK2_PROFILE_QUALITY = "quality"

LANGUAGE_MODE_ORDER = [
    LANGUAGE_MODE_EURO,
    LANGUAGE_MODE_ASIAN_TO_EURO,
    LANGUAGE_MODE_EURO_TO_ASIAN,
]

ENGLISH_OCR_OPTIONS = ("tesseract", "paddle_fast", "paddle")


LANGUAGE_MODE_CONFIGS = {
    LANGUAGE_MODE_EURO: {
        "default_source": "en",
        "source_options": [("en", "language_display_en")],
        "default_target": "uk",
        "target_options": [("uk", "language_display_uk"), ("ru", "language_display_ru")],
        "default_ocr": "tesseract",
        "ocr_options": ENGLISH_OCR_OPTIONS,
    },
    LANGUAGE_MODE_ASIAN_TO_EURO: {
        "default_source": "ja",
        "source_options": [("ja", "language_display_ja_hint"), ("zh", "language_display_zh_hint")],
        "default_target": "en",
        "target_options": [
            ("en", "language_display_en"),
            ("uk", "language_display_uk"),
            ("ru", "language_display_ru"),
        ],
        "default_ocr": "paddle_block2_fast",
        "ocr_options": ["paddle_block2_fast", "paddle"],
    },
    LANGUAGE_MODE_EURO_TO_ASIAN: {
        "default_source": "en",
        "source_options": [("en", "language_display_en")],
        "default_target": "ja",
        "target_options": [
            ("zh", "language_display_zh_s"),
            ("zh_hant", "language_display_zh_t"),
            ("ja", "language_display_ja"),
        ],
        "default_ocr": "tesseract",
        "ocr_options": ENGLISH_OCR_OPTIONS,
    },
}


def normalize_language_mode(value):
    """Мягкая валидация языкового режима из профиля/настроек лаунчера."""
    value = str(value or LANGUAGE_MODE_EURO).strip().lower()
    aliases = {
        "euro": LANGUAGE_MODE_EURO,
        "en_ru_uk": LANGUAGE_MODE_EURO,
        "asian": LANGUAGE_MODE_ASIAN_TO_EURO,
        "cjk_to_euro": LANGUAGE_MODE_ASIAN_TO_EURO,
        "en_to_asian": LANGUAGE_MODE_EURO_TO_ASIAN,
    }
    value = aliases.get(value, value)
    return value if value in LANGUAGE_MODE_CONFIGS else LANGUAGE_MODE_EURO


def language_mode_config(mode):
    """Возвращает безопасную конфигурацию языкового режима."""
    return LANGUAGE_MODE_CONFIGS[normalize_language_mode(mode)]


def language_option_text(interface_lang, label):
    """
    Текст пункта source/target language в лаунчере.

    Для обычных языков оставляем старые подписи как есть.
    Для азиатских языков используем ключи локализации, чтобы рядом с иероглифами
    была понятная подсказка на текущем языке интерфейса.
    """
    label = str(label or "")
    if label.startswith("language_display_"):
        return tr(interface_lang, label)
    return label


def infer_language_mode_from_translation(translation):
    """Пытается восстановить режим для старого профиля без language_mode."""
    if not isinstance(translation, dict):
        return LANGUAGE_MODE_EURO
    src = str(translation.get("source_lang", "en") or "en").lower()
    tgt = str(translation.get("target_lang", "uk") or "uk").lower()
    if src in ("ja", "zh", "zh_hans", "zh_hant", "zh-hans", "zh-hant"):
        return LANGUAGE_MODE_ASIAN_TO_EURO
    if src == "en" and tgt in ("ja", "zh", "zh_hans", "zh_hant", "zh-hans", "zh-hant"):
        return LANGUAGE_MODE_EURO_TO_ASIAN
    return LANGUAGE_MODE_EURO


def normalize_local_translator_preference(value):
    """Нормализует сохранённый пользовательский выбор Block 1 translator."""
    raw = str(migrate_block1_backend(value) or LOCAL_TRANSLATOR_AUTO).lower().strip()
    aliases = {
        "current": LOCAL_TRANSLATOR_CURRENT_BASE,
        "marian": LOCAL_TRANSLATOR_CURRENT_BASE,
        "translategemma_q6": LOCAL_TRANSLATOR_TRANSLATEGEMMA_LLAMA_Q6,
        "translategemma_llama_q6": LOCAL_TRANSLATOR_TRANSLATEGEMMA_LLAMA_Q6,
        "translategemma_q6_speed": LOCAL_TRANSLATOR_TRANSLATEGEMMA_LLAMA_Q6_SPEED,
    }
    raw = aliases.get(raw, raw)
    return raw if raw in LOCAL_TRANSLATOR_OPTIONS else LOCAL_TRANSLATOR_AUTO


def resolve_block1_translation_backend(preference, source_lang, target_lang):
    """Возвращает production backend без молчаливой подмены неподходящего выбора."""
    preference = normalize_local_translator_preference(preference)
    source = str(source_lang or "en").lower().strip()
    target = str(target_lang or "uk").lower().strip()

    if preference == LOCAL_TRANSLATOR_AUTO:
        return LOCAL_TRANSLATOR_BERGAMOT
    if preference == LOCAL_TRANSLATOR_CURRENT_BASE and not (source == "en" and target == "ru"):
        return None
    if preference == LOCAL_TRANSLATOR_TRANSLATEGEMMA_LLAMA_Q6 and not (
        source == "en" and target in {"ru", "uk"}
    ):
        return None
    if preference == LOCAL_TRANSLATOR_TRANSLATEGEMMA_LLAMA_Q6_SPEED and not (
        source == "en" and target in {"ru", "uk"}
    ):
        return None
    return preference


def normalize_dynamic_settings(raw_settings):
    """Мягкая валидация настроек динамического перевода из профиля."""
    result = dict(DEFAULT_DYNAMIC_SETTINGS)
    if not isinstance(raw_settings, dict):
        return result

    result["enabled"] = bool(raw_settings.get("enabled", result["enabled"]))
    result["hide_main_overlay"] = bool(raw_settings.get("hide_main_overlay", result.get("hide_main_overlay", True)))

    try:
        result["display_time_sec"] = max(1, min(30, int(raw_settings.get("display_time_sec", result["display_time_sec"]))))
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



# --- НАСТРОЙКИ OCR ПО УМОЛЧАНИЮ ---
# tesseract — быстрый/совместимый режим для слабых машин;
# paddle_fast — PP-OCRv4 mobile speed/quality preset for EN OCR.
# paddle — текущий базовый качественный PaddleOCR-режим.
DEFAULT_OCR_SETTINGS = {
    "engine": "tesseract",
}


def normalize_ocr_settings(raw_settings):
    """Мягкая валидация OCR-настроек из профиля."""
    result = dict(DEFAULT_OCR_SETTINGS)
    if not isinstance(raw_settings, dict):
        return result
    engine = str(raw_settings.get("engine", result["engine"]) or "tesseract").strip().lower()
    if engine in {"paddle-fast", "paddlefast"}:
        engine = "paddle_fast"
    if engine in {"paddle-block2-fast", "paddle_block2", "paddlev6small"}:
        engine = "paddle_block2_fast"
    # Сейчас в live зарегистрированы Tesseract, Paddle Fast и Paddle Quality. Будущие OCR-движки
    # будут добавляться сюда после реального backend-подключения.
    if engine in ("tesseract", "paddle_fast", "paddle_block2_fast", "paddle"):
        result["engine"] = engine
    if "advanced_override" in raw_settings:
        result["advanced_override"] = bool(raw_settings.get("advanced_override"))
    if "quality_engine" in raw_settings:
        result["quality_engine"] = str(raw_settings.get("quality_engine") or "").strip().lower()
    return result


SUBTITLE_BACKGROUND_OPTIONS = (STATIC_PANEL, GAME_BACKGROUND)
SUBTITLE_DISPLAY_POLICY_OPTIONS = (FOLLOW_SOURCE, FAST_SUBTITLES)
GAME_BACKGROUND_PADDLE_BACKENDS = frozenset({"paddle_fast", "paddle_block2_fast", "paddle"})
ENGLISH_SOURCE_LANGUAGE_MODES = frozenset({LANGUAGE_MODE_EURO, LANGUAGE_MODE_EURO_TO_ASIAN})


def english_ocr_options_for_subtitles(display_policy, background_mode):
    """Единая OCR-матрица English-source Block 1/3."""
    allowed = list(ENGLISH_OCR_OPTIONS)
    if str(display_policy or FOLLOW_SOURCE) == FAST_SUBTITLES:
        allowed = [engine for engine in allowed if engine != "paddle"]
    if str(background_mode or STATIC_PANEL) == GAME_BACKGROUND:
        allowed = [engine for engine in allowed if engine != "tesseract"]
    return allowed


def compatible_ocr_options(language_mode, display_policy, background_mode):
    """Возвращает допустимые OCR без связывания OCR с translation profile."""
    normalized_mode = normalize_language_mode(language_mode)
    if normalized_mode in ENGLISH_SOURCE_LANGUAGE_MODES:
        return english_ocr_options_for_subtitles(display_policy, background_mode)
    allowed = list(language_mode_config(normalized_mode).get("ocr_options", ()))
    if str(background_mode or STATIC_PANEL) == GAME_BACKGROUND:
        allowed = [engine for engine in allowed if engine in GAME_BACKGROUND_PADDLE_BACKENDS]
    return allowed


def game_background_ocr_options(language_mode, display_policy=FOLLOW_SOURCE):
    """Возвращает существующие Paddle OCR, совместимые с игровым фоном блока."""
    return compatible_ocr_options(language_mode, display_policy, GAME_BACKGROUND)


def required_game_background_ocr(language_mode, display_policy=FOLLOW_SOURCE):
    """Выбирает штатный Paddle OCR блока без введения нового backend ID."""
    cfg = language_mode_config(language_mode)
    compatible = game_background_ocr_options(language_mode, display_policy)
    default_ocr = str(cfg.get("default_ocr") or "")
    if default_ocr in compatible:
        return default_ocr
    return compatible[0] if compatible else "paddle"


def normalize_text_settings(raw_settings):
    """Мягкая валидация визуальных настроек текста из профиля."""
    result = dict(DEFAULT_TEXT_SETTINGS)
    if not isinstance(raw_settings, dict):
        return result

    if "font_name" in raw_settings:
        result["font_name"] = str(raw_settings.get("font_name") or DEFAULT_TEXT_SETTINGS["font_name"])

    try:
        result["font_size"] = max(12, min(48, int(raw_settings.get("font_size", result["font_size"]))))
    except Exception:
        pass

    def clean_hex_color(value, fallback):
        value = str(value or "").strip()
        if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            return value.upper()
        return fallback

    result["font_color"] = clean_hex_color(raw_settings.get("font_color"), result["font_color"])
    result["outline_color"] = clean_hex_color(raw_settings.get("outline_color"), result["outline_color"])
    result["outline_enabled"] = bool(raw_settings.get("outline_enabled", False))

    try:
        result["outline_thickness"] = max(0, min(8, int(raw_settings.get("outline_thickness", result["outline_thickness"]))))
    except Exception:
        pass

    result["draw_background_plate"] = bool(raw_settings.get("draw_background_plate", result["draw_background_plate"]))
    result["adaptive_plate_enabled"] = bool(raw_settings.get("adaptive_plate_enabled", False))
    result["background_color"] = clean_hex_color(raw_settings.get("background_color"), "#000000")

    try:
        result["background_opacity"] = max(0, min(255, int(raw_settings.get("background_opacity", result["background_opacity"]))))
    except Exception:
        pass

    return result

HOTKEY_TITLES = {
    "toggle_translation_mode": "Режим настройки / перевода",
    "toggle_translation_visibility": "Скрыть / показать перевод",
    "dynamic_area_translation": "Динамический перевод",
    "force_refresh": "Принудительно обновить перевод",
}


def hotkey_title_for_lang(action, interface_lang="uk"):
    """Локализованное название действия горячей клавиши."""
    interface_lang = normalize_interface_lang(interface_lang)
    if action == "toggle_translation_mode":
        return tr(interface_lang, "hotkey_action_toggle_mode")
    if action == "toggle_translation_visibility":
        return tr(interface_lang, "hotkey_action_toggle_hide")
    if action == "dynamic_area_translation":
        return tr(interface_lang, "hotkey_action_dynamic")
    if action == "force_refresh":
        return tr(interface_lang, "hotkey_action_force_refresh")
    return HOTKEY_TITLES.get(action, action)


def copy_default_hotkeys():
    """Глубокая копия дефолтных хоткеев без зависимости от copy.deepcopy."""
    return {
        action: {
            "display": data["display"],
            "keys": list(data["keys"]),
        }
        for action, data in DEFAULT_HOTKEYS.items()
    }


def normalize_hotkeys(raw_hotkeys):
    """
    Мягкая валидация структуры hotkeys из старого/нового профиля.

    Если в старом профиле hotkeys нет — используем дефолт.
    Если в профиле есть только часть хоткеев — недостающие берём из дефолта.
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


# Разрешённые основные клавиши для пользовательских хоткеев.
# Модификаторы Ctrl/Alt/Shift разрешены только вместе с одной из этих клавиш.
ALLOWED_BASE_KEYS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ") | set("0123456789") | {f"F{i}" for i in range(1, 13)}

# Эти клавиши не разрешаем ни одиночно, ни в сочетаниях.
# Причина: они слишком часто используются самой игрой, Windows или диалогами приложения.
BLOCKED_HOTKEY_KEYS = {
    "ESC", "ENTER", "SPACE", "TAB",
    "INSERT", "DELETE", "HOME", "END", "PAGEUP", "PAGEDOWN",
    "UP", "DOWN", "LEFT", "RIGHT",
    "BACKSPACE", "PAUSE", "WIN",
}

MODIFIER_KEYS = {"CTRL", "ALT", "SHIFT", "WIN"}


def qt_enum_value(value):
    """Безопасно получаем числовой код Qt enum/int для PyQt6."""
    return getattr(value, "value", int(value) if isinstance(value, int) else value)


def vk_to_name(vk_code):
    """Преобразование Windows nativeVirtualKey в нашу строку hotkey-конфига."""
    try:
        vk = int(vk_code)
    except Exception:
        return None

    # A-Z. На Windows это обычные ASCII-коды.
    if 0x41 <= vk <= 0x5A:
        return chr(vk)

    # 0-9.
    if 0x30 <= vk <= 0x39:
        return chr(vk)

    # F1-F12.
    if 0x70 <= vk <= 0x7B:
        return f"F{vk - 0x70 + 1}"

    special = {
        0x1B: "ESC",
        0x20: "SPACE",
        0x09: "TAB",
        0x0D: "ENTER",
        0x08: "BACKSPACE",
        0x2D: "INSERT",
        0x2E: "DELETE",
        0x24: "HOME",
        0x23: "END",
        0x21: "PAGEUP",
        0x22: "PAGEDOWN",
        0x26: "UP",
        0x28: "DOWN",
        0x25: "LEFT",
        0x27: "RIGHT",
        0x13: "PAUSE",
        0x5B: "WIN",
        0x5C: "WIN",
    }
    return special.get(vk)


def qt_key_to_name(key):
    """
    Fallback-преобразование Qt key code в нашу строку hotkey-конфига.

    Основной путь в окне захвата хоткея — event.nativeVirtualKey(), потому что
    он стабильнее на Windows и лучше работает с разными раскладками клавиатуры.
    Эта функция оставлена как запасной путь.
    """
    try:
        key_value = qt_enum_value(key)
    except Exception:
        key_value = key

    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if key_value == qt_enum_value(getattr(Qt.Key, f"Key_{ch}")):
            return ch

    for ch in "0123456789":
        if key_value == qt_enum_value(getattr(Qt.Key, f"Key_{ch}")):
            return ch

    for i in range(1, 13):
        if key_value == qt_enum_value(getattr(Qt.Key, f"Key_F{i}")):
            return f"F{i}"

    special = {
        qt_enum_value(Qt.Key.Key_Escape): "ESC",
        qt_enum_value(Qt.Key.Key_Space): "SPACE",
        qt_enum_value(Qt.Key.Key_Tab): "TAB",
        qt_enum_value(Qt.Key.Key_Return): "ENTER",
        qt_enum_value(Qt.Key.Key_Enter): "ENTER",
        qt_enum_value(Qt.Key.Key_Backspace): "BACKSPACE",
        qt_enum_value(Qt.Key.Key_Insert): "INSERT",
        qt_enum_value(Qt.Key.Key_Delete): "DELETE",
        qt_enum_value(Qt.Key.Key_Home): "HOME",
        qt_enum_value(Qt.Key.Key_End): "END",
        qt_enum_value(Qt.Key.Key_PageUp): "PAGEUP",
        qt_enum_value(Qt.Key.Key_PageDown): "PAGEDOWN",
        qt_enum_value(Qt.Key.Key_Up): "UP",
        qt_enum_value(Qt.Key.Key_Down): "DOWN",
        qt_enum_value(Qt.Key.Key_Left): "LEFT",
        qt_enum_value(Qt.Key.Key_Right): "RIGHT",
        qt_enum_value(Qt.Key.Key_Pause): "PAUSE",
    }
    return special.get(key_value)


def validate_hotkey_keys(keys, interface_lang="ru"):
    """
    Проверяет пользовательский хоткей по нашим правилам.

    Разрешаем:
    - одиночные A-Z / 0-9 / F1-F12;
    - Ctrl/Alt/Shift + A-Z / 0-9 / F1-F12;
    - любые сочетания из 1-3 модификаторов Ctrl/Alt/Shift + одна основная клавиша.

    Запрещаем:
    - одиночные Ctrl/Alt/Shift/Win;
    - Win как модификатор;
    - Space/Tab/Esc/Enter/стрелки/navigation-клавиши в любом виде.
    """
    normalized = [str(k).upper() for k in keys if str(k).strip()]
    if not normalized:
        return False, tr(interface_lang, "hotkey_empty")

    if any(k in BLOCKED_HOTKEY_KEYS for k in normalized):
        return False, tr(interface_lang, "hotkey_blocked")

    if any(k not in MODIFIER_KEYS and k not in ALLOWED_BASE_KEYS for k in normalized):
        return False, tr(interface_lang, "hotkey_unsupported")

    base_keys = [k for k in normalized if k not in MODIFIER_KEYS]
    modifiers = [k for k in normalized if k in MODIFIER_KEYS]

    if "WIN" in modifiers:
        return False, tr(interface_lang, "hotkey_blocked")

    if len(base_keys) != 1:
        return False, tr(interface_lang, "hotkey_one_base_required")

    if len(set(modifiers)) != len(modifiers):
        return False, tr(interface_lang, "hotkey_duplicate_modifiers")

    return True, ""


def make_hotkey_display(keys):
    """Красивый display в стабильном порядке: Ctrl+Alt+Shift+Win+Key."""
    if not keys:
        return "Не назначено"

    order = ["CTRL", "ALT", "SHIFT", "WIN"]
    normalized = [str(k).upper() for k in keys]
    display_parts = []

    for mod in order:
        if mod in normalized:
            display_parts.append({
                "CTRL": "Ctrl",
                "ALT": "Alt",
                "SHIFT": "Shift",
                "WIN": "Win",
            }[mod])

    for key in normalized:
        if key not in order:
            display_parts.append(key)

    return "+".join(display_parts) if display_parts else "Не назначено"


class HotkeyCaptureDialog(QDialog):
    """
    Маленькое окно захвата нового хоткея.

    Работает просто:
    - пользователь нажимает одиночную клавишу или сочетание;
    - модификаторы Ctrl/Alt/Shift/Win записываются вместе с основной клавишей;
    - одни только Ctrl/Alt/Shift/Win не принимаются, нужна основная клавиша.
    """

    def __init__(self, action_title, current_display, parent=None, interface_lang="ru"):
        super().__init__(parent)
        self.interface_lang = normalize_interface_lang(interface_lang)
        self.setWindowTitle(tr(self.interface_lang, "hotkey_dialog_title"))
        self.setModal(True)
        self.resize(470, 240)

        self.captured_keys = None
        self.captured_display = None

        layout = QVBoxLayout()
        self.lbl_title = QLabel(tr(self.interface_lang, "hotkey_dialog_action", action=action_title))
        self.lbl_title.setProperty("uiRole", "heading")
        self.lbl_title.setWordWrap(True)
        layout.addWidget(self.lbl_title)

        self.lbl_current = QLabel(tr(self.interface_lang, "hotkey_dialog_current", current=current_display))
        layout.addWidget(self.lbl_current)

        self.lbl_instruction = QLabel(tr(self.interface_lang, "hotkey_dialog_instruction"))
        self.lbl_instruction.setWordWrap(True)
        self.lbl_instruction.setProperty("uiRole", "muted")
        layout.addWidget(self.lbl_instruction)

        self.lbl_warning = QLabel(tr(self.interface_lang, "hotkey_warning"))
        self.lbl_warning.setWordWrap(True)
        self.lbl_warning.setProperty("uiRole", "warning")
        layout.addWidget(self.lbl_warning)

        self.lbl_preview = QLabel(tr(self.interface_lang, "hotkey_waiting"))
        self.lbl_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_preview.setProperty("uiRole", "keyCapture")
        layout.addWidget(self.lbl_preview)

        self.setLayout(layout)

    def keyPressEvent(self, event):
        if event.isAutoRepeat():
            return

        # Esc используем только как отмену назначения, а не как допустимый хоткей.
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return

        modifiers = event.modifiers()
        keys = []

        if modifiers & Qt.KeyboardModifier.ControlModifier:
            keys.append("CTRL")
        if modifiers & Qt.KeyboardModifier.AltModifier:
            keys.append("ALT")
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            keys.append("SHIFT")
        if modifiers & Qt.KeyboardModifier.MetaModifier:
            keys.append("WIN")

        # На Windows nativeVirtualKey надёжнее, чем event.key():
        # так буквы A-Z лучше ловятся даже при разных раскладках клавиатуры.
        key_name = None
        try:
            key_name = vk_to_name(event.nativeVirtualKey())
        except Exception:
            key_name = None

        if not key_name:
            key_name = qt_key_to_name(event.key())

        if not key_name:
            self.lbl_preview.setText(tr(self.interface_lang, "hotkey_not_supported"))
            return

        if key_name not in keys:
            keys.append(key_name)

        ok, error = validate_hotkey_keys(keys, self.interface_lang)
        if not ok:
            self.lbl_preview.setText(error)
            return

        self.captured_keys = keys
        self.captured_display = make_hotkey_display(keys)
        self.lbl_preview.setText(self.captured_display)
        self.accept()



class TextSettingsDialog(QDialog):
    """
    Отдельное окно настроек текста перевода.

    Это первый шаг к разгрузке главного экрана лаунчера:
    - главное окно оставляем компактным;
    - серьёзные настройки открываются отдельными понятными окнами;
    - всё сохраняется в профиль игры и передаётся в оверлей.
    """

    def __init__(self, settings, parent=None, interface_lang="uk"):
        super().__init__(parent)
        self.interface_lang = normalize_interface_lang(interface_lang)
        self.result_settings = normalize_text_settings(settings)
        self.current_font_color = self.result_settings.get("font_color", "#FFFFFF")
        self.current_outline_color = self.result_settings.get("outline_color", "#000000")

        self.setWindowTitle(tr(self.interface_lang, "text_settings_dialog_title"))
        self.setModal(True)
        self.setMinimumSize(480, 440)

        layout = QVBoxLayout()
        form = QFormLayout()

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(12, 48)
        self.font_size_spin.setValue(int(self.result_settings.get("font_size", 19)))
        form.addRow(tr(self.interface_lang, "font_size"), self.font_size_spin)

        self.btn_font_color = QPushButton()
        self.btn_font_color.clicked.connect(self.choose_font_color)
        form.addRow(tr(self.interface_lang, "font_color"), self.btn_font_color)

        self.chk_outline = QCheckBox(tr(self.interface_lang, "outline_enabled"))
        self.chk_outline.setChecked(self.result_settings.get("outline_enabled", False))
        form.addRow("", self.chk_outline)
        self.btn_outline_color = QPushButton()
        self.btn_outline_color.clicked.connect(self.choose_outline_color)
        form.addRow(tr(self.interface_lang, "outline_color"), self.btn_outline_color)
        self.outline_thickness_spin = QSpinBox()
        self.outline_thickness_spin.setRange(0, 8)
        self.outline_thickness_spin.setValue(self.result_settings["outline_thickness"])
        form.addRow(tr(self.interface_lang, "outline_thickness"), self.outline_thickness_spin)

        self.chk_background_plate = QCheckBox(tr(self.interface_lang, "background_plate"))
        self.chk_background_plate.setChecked(bool(self.result_settings.get("draw_background_plate", True)))
        form.addRow("", self.chk_background_plate)

        self.chk_adaptive_plate = QCheckBox(tr(self.interface_lang, "adaptive_plate"))
        self.chk_adaptive_plate.setToolTip(tr(self.interface_lang, "adaptive_plate_tooltip"))
        self.chk_adaptive_plate.setChecked(self.result_settings.get("adaptive_plate_enabled", False))
        form.addRow("", self.chk_adaptive_plate)

        self.background_opacity_spin = QSpinBox()
        self.background_opacity_spin.setRange(0, 255)
        self.background_opacity_spin.setValue(int(self.result_settings.get("background_opacity", 135)))
        form.addRow(tr(self.interface_lang, "background_opacity"), self.background_opacity_spin)

        layout.addLayout(form)

        self.preview_label = SubtitleLabel(tr(self.interface_lang, "text_settings_preview"))
        self.preview_label.preview_background = True
        self.preview_label.setMinimumHeight(90)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setWordWrap(True)
        layout.addWidget(self.preview_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_save = QPushButton(tr(self.interface_lang, "save_and_close"))
        self.btn_save.setProperty("uiRole", "primary")
        self.btn_cancel = QPushButton(tr(self.interface_lang, "cancel"))
        self.btn_save.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_save)
        btn_row.addWidget(self.btn_cancel)
        layout.addLayout(btn_row)

        self.setLayout(layout)
        self.text_form = form
        self.update_color_button()
        self.update_preview()

        self.font_size_spin.valueChanged.connect(self.update_preview)
        self.background_opacity_spin.valueChanged.connect(self.update_preview)
        self.chk_background_plate.stateChanged.connect(self.update_preview)
        self.chk_adaptive_plate.toggled.connect(self.update_preview)
        self.chk_outline.toggled.connect(self.update_preview)
        self.outline_thickness_spin.valueChanged.connect(self.update_preview)

    def apply_localization(self, lang):
        self.interface_lang = normalize_interface_lang(lang)
        self.setWindowTitle(tr(self.interface_lang, "text_settings_dialog_title"))
        for field, key in ((self.font_size_spin, "font_size"), (self.btn_font_color, "font_color"),
                           (self.btn_outline_color, "outline_color"), (self.outline_thickness_spin, "outline_thickness"),
                           (self.background_opacity_spin, "background_opacity")):
            self.text_form.labelForField(field).setText(tr(self.interface_lang, key))
        self.chk_outline.setText(tr(self.interface_lang, "outline_enabled"))
        self.chk_background_plate.setText(tr(self.interface_lang, "background_plate"))
        self.chk_adaptive_plate.setText(tr(self.interface_lang, "adaptive_plate"))
        self.chk_adaptive_plate.setToolTip(tr(self.interface_lang, "adaptive_plate_tooltip"))
        self.preview_label.setText(tr(self.interface_lang, "text_settings_preview"))
        self.btn_save.setText(tr(self.interface_lang, "save_and_close"))
        self.btn_cancel.setText(tr(self.interface_lang, "cancel"))
        self.update_color_button()

    def update_color_button(self):
        """Обновляет кнопку выбора цвета."""
        self.btn_font_color.setText(f"{tr(self.interface_lang, 'choose_color')}  {self.current_font_color}")
        self.btn_font_color.setStyleSheet(
            f"background-color: {self.current_font_color}; color: {'#FFFFFF' if QColor(self.current_font_color).lightness() < 128 else '#000000'}; font-weight: bold;"
        )
        self.btn_outline_color.setText(f"{tr(self.interface_lang, 'choose_color')}  {self.current_outline_color}")
        self.btn_outline_color.setStyleSheet(f"background-color: {self.current_outline_color}; color: {'#FFFFFF' if QColor(self.current_outline_color).lightness() < 128 else '#000000'};")

    def update_preview(self):
        """The preview uses the same glyph/plate renderer as Live and Dynamic."""
        size = int(self.font_size_spin.value())
        self.preview_label.setFont(QFont(self.result_settings.get("font_name", "Arial"), size, QFont.Weight.Bold))
        self.preview_label.setStyleSheet(
            f"color: {self.current_font_color}; background: transparent;"
        )
        self.preview_label.set_visuals(self.collect_settings())

    def choose_outline_color(self):
        color = QColorDialog.getColor(QColor(self.current_outline_color), self, tr(self.interface_lang, "outline_color"))
        if color.isValid():
            self.current_outline_color = color.name().upper()
            self.update_color_button()
            self.update_preview()

    def choose_font_color(self):
        """Открывает системный выбор цвета текста."""
        initial = QColor(self.current_font_color)
        color = QColorDialog.getColor(initial, self, tr(self.interface_lang, "color_dialog_font"))
        if not color.isValid():
            return
        self.current_font_color = color.name().upper()
        self.update_color_button()
        self.update_preview()

    def collect_settings(self):
        return normalize_text_settings({
            **self.result_settings,
            "font_size": int(self.font_size_spin.value()),
            "font_color": self.current_font_color,
            "draw_background_plate": bool(self.chk_background_plate.isChecked()),
            "adaptive_plate_enabled": self.chk_adaptive_plate.isChecked(),
            "background_opacity": int(self.background_opacity_spin.value()),
            "outline_enabled": self.chk_outline.isChecked(),
            "outline_color": self.current_outline_color,
            "outline_thickness": self.outline_thickness_spin.value(),
        })

    def accept(self):
        """Перед закрытием собираем результат в result_settings."""
        self.result_settings = self.collect_settings()
        super().accept()




class DynamicSettingsDialog(QDialog):
    """
    Отдельное окно настроек динамического перевода.

    На этом этапе делаем безопасную первую версию:
    - включить/выключить динамический перевод;
    - время показа временного перевода;
    - где показывать результат: рядом с выделением или поверх выделенной области.

    Сам хоткей динамического перевода настраивается в общем окне горячих клавиш,
    чтобы все hotkey-настройки оставались в одном месте.
    """

    def __init__(self, settings, parent=None, interface_lang="uk"):
        super().__init__(parent)
        self.interface_lang = normalize_interface_lang(interface_lang)
        self.result_settings = normalize_dynamic_settings(settings)

        self.setWindowTitle(tr(self.interface_lang, "dynamic_settings_dialog_title"))
        self.setModal(True)
        self.resize(560, 405)

        layout = QVBoxLayout()

        self.lbl_info = QLabel(tr(self.interface_lang, "dynamic_settings_info"))
        self.lbl_info.setWordWrap(True)
        self.lbl_info.setProperty("uiRole", "muted")
        layout.addWidget(self.lbl_info)

        form = QFormLayout()

        self.chk_enabled = QCheckBox(tr(self.interface_lang, "dynamic_enabled"))
        self.chk_enabled.setChecked(bool(self.result_settings.get("enabled", True)))
        form.addRow("", self.chk_enabled)

        # Важно: динамический перевод может работать в двух разных сценариях.
        # combined   — обычный субтитровый оверлей + динамическое выделение по хоткею.
        # standalone — отдельный режим без постоянной рамки субтитров: только выделение областей.
        self.use_mode_combo = QComboBox()
        self.use_mode_combo.addItem(tr(self.interface_lang, "dynamic_use_combined"), "combined")
        self.use_mode_combo.addItem(tr(self.interface_lang, "dynamic_use_standalone"), "standalone")
        current_use_mode = str(self.result_settings.get("mode", "combined"))
        use_idx = self.use_mode_combo.findData(current_use_mode)
        self.use_mode_combo.setCurrentIndex(use_idx if use_idx >= 0 else 0)
        form.addRow(tr(self.interface_lang, "dynamic_use_mode"), self.use_mode_combo)

        self.chk_hide_main_overlay = QCheckBox(tr(self.interface_lang, "dynamic_hide_main_overlay"))
        self.chk_hide_main_overlay.setChecked(bool(self.result_settings.get("hide_main_overlay", True)))
        form.addRow("", self.chk_hide_main_overlay)

        self.display_time_spin = QSpinBox()
        self.display_time_spin.setRange(1, 30)
        self.display_time_spin.setValue(int(self.result_settings.get("display_time_sec", 6)))
        form.addRow(tr(self.interface_lang, "dynamic_display_time"), self.display_time_spin)

        self.output_mode_combo = QComboBox()
        self.output_mode_combo.addItem(tr(self.interface_lang, "dynamic_output_near"), "near")
        self.output_mode_combo.addItem(tr(self.interface_lang, "dynamic_output_over"), "over")
        current_mode = str(self.result_settings.get("output_mode", "near"))
        idx = self.output_mode_combo.findData(current_mode)
        self.output_mode_combo.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow(tr(self.interface_lang, "dynamic_output_mode"), self.output_mode_combo)

        self.chk_auto_fit = QCheckBox(tr(self.interface_lang, "dynamic_auto_fit"))
        self.chk_auto_fit.setChecked(bool(self.result_settings.get("auto_fit_enabled", True)))
        form.addRow("", self.chk_auto_fit)

        self.max_rect_scale_spin = QDoubleSpinBox()
        self.max_rect_scale_spin.setRange(1.0, 3.0)
        self.max_rect_scale_spin.setSingleStep(0.1)
        self.max_rect_scale_spin.setDecimals(1)
        self.max_rect_scale_spin.setValue(float(self.result_settings.get("max_rect_scale", 2.0)))
        form.addRow(tr(self.interface_lang, "dynamic_max_rect_scale"), self.max_rect_scale_spin)

        self.min_font_size_spin = QSpinBox()
        self.min_font_size_spin.setRange(6, 32)
        self.min_font_size_spin.setValue(int(self.result_settings.get("min_font_size", 12)))
        form.addRow(tr(self.interface_lang, "dynamic_min_font_size"), self.min_font_size_spin)

        layout.addLayout(form)
        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_save = QPushButton(tr(self.interface_lang, "save_and_close"))
        self.btn_save.setProperty("uiRole", "primary")
        self.btn_cancel = QPushButton(tr(self.interface_lang, "cancel"))
        self.btn_save.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_save)
        btn_row.addWidget(self.btn_cancel)
        layout.addLayout(btn_row)

        self.setLayout(layout)

    def accept(self):
        self.result_settings = normalize_dynamic_settings({
            "enabled": bool(self.chk_enabled.isChecked()),
            "display_time_sec": int(self.display_time_spin.value()),
            "output_mode": self.output_mode_combo.currentData() or "near",
            "mode": self.use_mode_combo.currentData() or "combined",
            "hide_main_overlay": bool(self.chk_hide_main_overlay.isChecked()),
            "auto_fit_enabled": bool(self.chk_auto_fit.isChecked()),
            "max_rect_scale": float(self.max_rect_scale_spin.value()),
            "min_font_size": int(self.min_font_size_spin.value()),
        })
        super().accept()


class HotkeysSettingsDialog(QDialog):
    """
    Отдельное окно настройки горячих клавиш.

    Логика вынесена из главного экрана лаунчера:
    - главный экран остаётся компактным;
    - пользователь меняет хоткеи в отдельном понятном окне;
    - результат сохраняется в профиль игры через LauncherWindow.
    """

    def __init__(self, hotkeys, parent=None, interface_lang="uk"):
        super().__init__(parent)
        self.interface_lang = normalize_interface_lang(interface_lang)
        self.result_hotkeys = normalize_hotkeys(hotkeys)
        self.hotkey_buttons = {}

        self.setWindowTitle(tr(self.interface_lang, "hotkeys_settings_dialog_title"))
        self.setModal(True)
        self.resize(520, 320)

        layout = QVBoxLayout()

        self.lbl_warning = QLabel(tr(self.interface_lang, "hotkey_warning"))
        self.lbl_warning.setWordWrap(True)
        self.lbl_warning.setProperty("uiRole", "warning")
        layout.addWidget(self.lbl_warning)

        form = QFormLayout()

        self.btn_hotkey_mode = QPushButton()
        self.btn_hotkey_mode.clicked.connect(lambda: self.change_hotkey("toggle_translation_mode"))
        form.addRow(tr(self.interface_lang, "hotkey_mode_label"), self.btn_hotkey_mode)
        self.hotkey_buttons["toggle_translation_mode"] = self.btn_hotkey_mode

        self.btn_hotkey_hide = QPushButton()
        self.btn_hotkey_hide.clicked.connect(lambda: self.change_hotkey("toggle_translation_visibility"))
        form.addRow(tr(self.interface_lang, "hotkey_hide_label"), self.btn_hotkey_hide)
        self.hotkey_buttons["toggle_translation_visibility"] = self.btn_hotkey_hide

        self.btn_hotkey_dynamic = QPushButton()
        self.btn_hotkey_dynamic.clicked.connect(lambda: self.change_hotkey("dynamic_area_translation"))
        form.addRow(tr(self.interface_lang, "hotkey_dynamic_label"), self.btn_hotkey_dynamic)
        self.hotkey_buttons["dynamic_area_translation"] = self.btn_hotkey_dynamic

        self.btn_hotkey_force_refresh = QPushButton()
        self.btn_hotkey_force_refresh.clicked.connect(lambda: self.change_hotkey("force_refresh"))
        form.addRow(tr(self.interface_lang, "hotkey_force_refresh_label"), self.btn_hotkey_force_refresh)
        self.hotkey_buttons["force_refresh"] = self.btn_hotkey_force_refresh

        layout.addLayout(form)

        self.btn_reset_hotkeys = QPushButton(tr(self.interface_lang, "reset_hotkeys"))
        self.btn_reset_hotkeys.clicked.connect(self.reset_hotkeys_to_default)
        layout.addWidget(self.btn_reset_hotkeys)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_save = QPushButton(tr(self.interface_lang, "save_and_close"))
        self.btn_save.setProperty("uiRole", "primary")
        self.btn_cancel = QPushButton(tr(self.interface_lang, "cancel"))
        self.btn_save.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_save)
        btn_row.addWidget(self.btn_cancel)
        layout.addLayout(btn_row)

        self.setLayout(layout)
        self.refresh_hotkey_buttons()

    def refresh_hotkey_buttons(self):
        """Обновляет подписи кнопок текущими назначениями."""
        for action, button in self.hotkey_buttons.items():
            button.setText(
                self.result_hotkeys.get(action, DEFAULT_HOTKEYS[action]).get(
                    "display", DEFAULT_HOTKEYS[action]["display"]
                )
            )

    def change_hotkey(self, action):
        """Открывает окно захвата нового хоткея для выбранного действия."""
        current = self.result_hotkeys.get(action, DEFAULT_HOTKEYS[action])
        dialog = HotkeyCaptureDialog(
            hotkey_title_for_lang(action, self.interface_lang),
            current.get("display", DEFAULT_HOTKEYS[action]["display"]),
            self,
            interface_lang=self.interface_lang,
        )

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        new_keys = dialog.captured_keys
        new_display = dialog.captured_display
        if not new_keys or not new_display:
            return

        # Не даём назначить одно и то же сочетание на два разных действия.
        for other_action, other_data in self.result_hotkeys.items():
            if other_action != action and other_data.get("keys") == new_keys:
                QMessageBox.warning(
                    self,
                    tr(self.interface_lang, "hotkey_conflict_title"),
                    tr(
                        self.interface_lang,
                        "hotkey_conflict_body",
                        hotkey=new_display,
                        action=hotkey_title_for_lang(other_action, self.interface_lang),
                    ),
                )
                return

        # Мягкое предупреждение: эти клавиши разрешены, но часто заняты играми.
        conflict_prone = set("WASD") | set("1234567890") | {f"F{i}" for i in range(1, 13)}
        if any(k in conflict_prone for k in new_keys):
            QMessageBox.information(
                self,
                tr(self.interface_lang, "hotkey_soft_warning_title"),
                tr(self.interface_lang, "hotkey_soft_warning_body"),
            )

        self.result_hotkeys[action] = {
            "display": new_display,
            "keys": list(new_keys),
        }
        self.refresh_hotkey_buttons()

    def reset_hotkeys_to_default(self):
        """Одна кнопка возврата к безопасным дефолтным сочетаниям."""
        self.result_hotkeys = copy_default_hotkeys()
        self.refresh_hotkey_buttons()



class ImportantWarningDialog(QDialog):
    """
    Отдельное окно важных предупреждений.

    Это не настройка движка, а пользовательская информация:
    - риски античитов;
    - ограничения OCR и перевода;
    - возможные проблемы с режимами экрана;
    - правила отчётов об ошибках.
    """

    def __init__(self, parent=None, interface_lang="uk"):
        super().__init__(parent)
        self.interface_lang = normalize_interface_lang(interface_lang)

        self.setWindowTitle(tr(self.interface_lang, "important_dialog_title"))
        self.setModal(True)
        self.resize(760, 620)

        layout = QVBoxLayout()

        self.lbl_header = QLabel(tr(self.interface_lang, "important_header"))
        self.lbl_header.setWordWrap(True)
        self.lbl_header.setProperty("uiRole", "warningBanner")
        layout.addWidget(self.lbl_header)

        self.txt_warning = QTextEdit()
        self.txt_warning.setReadOnly(True)
        # Предупреждение выводится как HTML, чтобы заголовки, решения и списки
        # были визуально читаемыми, а не одной большой plain-text простынёй.
        html_text = tr(self.interface_lang, "important_html")
        if not html_text or html_text == "important_html":
            html_text = tr(self.interface_lang, "important_text")
        else:
            prefix, marker, rest = html_text.partition("<style>")
            if marker and "</style>" in rest:
                html_text = prefix + marker + WARNING_DOCUMENT_CSS + "</style>" + rest.split("</style>", 1)[1]
        self.txt_warning.setHtml(html_text)
        layout.addWidget(self.txt_warning)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_close = QPushButton(tr(self.interface_lang, "close"))
        self.btn_close.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_close)
        layout.addLayout(btn_row)

        self.setLayout(layout)



class OverlayLoadingSplash(QWidget):
    """Small topmost loading window shown while the overlay process becomes ready."""

    def __init__(self, interface_lang="ru", parent=None):
        super().__init__(parent)
        self.interface_lang = normalize_interface_lang(interface_lang)
        self.dot_count = 0
        self.base_text = tr(self.interface_lang, "status_overlay_starting")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(344, 96)

        layout = QVBoxLayout()
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        self.lbl_status = QLabel(self.base_text)
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setProperty("uiRole", "heading")
        self.lbl_status.setWordWrap(True)
        layout.addWidget(self.lbl_status)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(10)
        layout.addWidget(self.progress)

        self.setLayout(layout)

        self.dots_timer = QTimer(self)
        self.dots_timer.timeout.connect(self.update_dots)

    def show_loading(self):
        self.base_text = tr(self.interface_lang, "status_overlay_starting")
        self.dot_count = 0
        self.lbl_status.setText(self.base_text)
        self.center_on_screen()
        self.show()
        self.raise_()

    def hide_loading(self):
        self.dots_timer.stop()
        self.hide()

    def update_dots(self):
        self.dot_count = (self.dot_count + 1) % 4
        suffix = "." * self.dot_count
        self.lbl_status.setText(f"{self.base_text}{suffix}")

    def center_on_screen(self):
        try:
            screen = QApplication.primaryScreen()
            if not screen:
                return
            rect = screen.availableGeometry()
            x = rect.x() + (rect.width() - self.width()) // 2
            y = rect.y() + (rect.height() - self.height()) // 2
            self.move(x, y)
        except Exception:
            pass


class LauncherWindow(QWidget):
    """
    Главное окно запуска.

    Логика сохранена:
    - пользователь выбирает активное окно игры или сохранённый профиль;
    - выбирает язык/режим/размер шрифта и хоткеи;
    - лаунчер создаёт session_config.json;
    - лаунчер прячется;
    - оверлей запускается отдельным процессом;
    - после закрытия оверлея лаунчер снова показывается.
    """

    def __init__(self):
        super().__init__()
        config.init_config()

        # Язык интерфейса — глобальная настройка приложения.
        # Он не связан с языком перевода игры.
        self.interface_lang = self.load_interface_lang()

        self.setWindowTitle(tr(self.interface_lang, "app_title"))
        screen = self.screen()
        height = min(810, screen.availableGeometry().height() - 48) if screen else 810
        self.setMinimumSize(680, 460)
        self.resize(680, height)

        os.makedirs(PROFILES_DIR, exist_ok=True)

        self.overlay_process = None
        self.current_game_name = ""
        self.current_profile_path = ""
        self.current_profile_id = ""
        self._profile_load_in_progress = False
        self._last_session_config_error = ""

        # Настраиваемые горячие клавиши.
        # Храним в памяти лаунчера, потом пишем в session_config.json и профиль игры.
        self.hotkeys = copy_default_hotkeys()
        self.hotkeys_modified = False
        self.hotkey_buttons = {}

        # Настройки текста перевода.
        # Они вынесены из главного экрана в отдельное окно, чтобы лаунчер
        # не превращался в перегруженную "панель Боинга".
        self.text_settings = normalize_text_settings({})
        self.text_settings_modified = False

        # Настройки динамического перевода.
        # Они нужны для будущей/текущей функции разового перевода выделенной области:
        # пользователь нажимает хоткей, выделяет мышью текст, получает временный перевод.
        self.dynamic_settings = normalize_dynamic_settings({})
        self.dynamic_settings_modified = False

        # Две независимые оси автоматических субтитров:
        # стратегия обнаружения фона и политика времени жизни перевода.
        self.subtitle_settings = normalize_subtitle_settings({})
        self.subtitle_settings_modified = False

        # Настройки подключаемого LLM-редактора перевода.
        # Это простой пользовательский уровень: off / strict / literary.
        # Продвинутые параметры backend/model_path будут вынесены отдельно позже.
        self.llm_settings = normalize_llm_settings({})
        self.llm_settings_modified = False

        # Настройки OCR-движка.
        # Tesseract = скорость/совместимость, PaddleOCR = качество/нагрузка.
        # Step10: последний выбранный OCR запоминается на уровне лаунчера,
        # чтобы при следующем запуске не приходилось снова выбирать PaddleOCR.
        self.launcher_settings = self.load_launcher_settings()
        # last_profile_path остаётся только подсказкой для явного восстановления
        # combo. До выбора профиля он не является profile-bound write target.
        self.subtitle_settings = normalize_subtitle_settings(
            self.launcher_settings.get("subtitles", self.subtitle_settings)
        )
        self.installed_language_modes = tuple(
            mode for mode in LANGUAGE_MODE_ORDER if block_id_for_language_mode(mode) in DATA.installed_blocks
        )
        self.language_mode = self.installed_language_mode(self.launcher_settings.get("language_mode"))
        self.language_mode_modified = False
        self._loaded_profile_data, _profile_migration = ensure_language_blocks({})
        self._block_profile_change_in_progress = False
        self.block2_translation_profile = BLOCK2_PROFILE_FAST
        self.block2_chinese_script_mode = "auto"
        self.local_translator_preference = normalize_local_translator_preference(
            self.launcher_settings.get("local_translator", LOCAL_TRANSLATOR_AUTO)
        )
        self.local_translator_modified = False
        self.selected_translation_backend = ""
        self.quality_runtime_selection = detect_quality_runtime()
        try:
            log_quality_runtime_selection(self.quality_runtime_selection, "launcher_detection")
        except OSError:
            pass
        self.llm_settings = normalize_llm_settings(self.launcher_settings.get("llm", self.llm_settings))
        self.ocr_settings = normalize_ocr_settings(self.launcher_settings.get("ocr", {}))
        self.ocr_settings_modified = False

        # Буфер stdout/stderr процесса оверлея.
        # Нужен, потому что теперь launcher.py читает вывод не только после завершения,
        # но и во время работы: так он может увидеть маркер закрытия оверлея.
        self.overlay_output_buffer = bytearray()
        self.overlay_close_requested = False
        self.overlay_shutdown_completed = False
        self.overlay_forced_kill = False
        self.overlay_forced_kill_reason = ""
        self.overlay_shutdown_timeout_timer = QTimer(self)
        self.overlay_shutdown_timeout_timer.setSingleShot(True)
        self.overlay_shutdown_timeout_timer.timeout.connect(self.force_finish_overlay_if_needed)
        self.overlay_launch_started_at = 0.0
        self.overlay_launch_backend = ""
        self.overlay_launch_mode = ""
        self.overlay_loading_active = False
        self.overlay_ready_marker_path = ""
        self.overlay_loading_splash = None
        self.overlay_start_timeout_timer = QTimer(self)
        self.overlay_start_timeout_timer.setSingleShot(True)
        self.overlay_start_timeout_timer.timeout.connect(self.on_overlay_start_timeout)
        self.overlay_ready_poll_timer = QTimer(self)
        self.overlay_ready_poll_timer.setInterval(250)
        self.overlay_ready_poll_timer.timeout.connect(self.check_overlay_ready_marker)

        self.init_ui()
        if not self.quality_runtime_selection["available"]:
            hint = quality_unavailable_message(self.quality_runtime_selection)
            self.local_translator_combo.setToolTip(hint)
            self.block2_translation_profile_combo.setToolTip(hint)
        self.refresh_active_windows()
        self.scan_saved_profiles()
        self.lbl_status.setText(tr(self.interface_lang, "status_ready"))

    def init_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)
        scroll = QScrollArea()
        scroll.setObjectName("launcherScroll")
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)

        # --- ВЕРХНЯЯ СТРОКА: ПЕРЕКЛЮЧАТЕЛЬ ЯЗЫКА ИНТЕРФЕЙСА ---
        # Язык интерфейса не связан с языком перевода игры.
        lang_row = QHBoxLayout()
        self.btn_help = QPushButton()
        self.btn_help.clicked.connect(self.open_help)
        lang_row.addWidget(self.btn_help)
        lang_row.addStretch()

        self.lbl_interface_language = QLabel()
        self.btn_interface_language = QPushButton()
        self.btn_interface_language.setMinimumWidth(92)
        self.btn_interface_language.setIconSize(QSize(24, 16))
        self.btn_interface_language.clicked.connect(self.cycle_interface_language)

        lang_row.addWidget(self.lbl_interface_language)
        lang_row.addWidget(self.btn_interface_language)
        layout.addLayout(lang_row)

        # --- ВЕРХНИЙ ПЕРЕКЛЮЧАТЕЛЬ ЯЗЫКОВЫХ РЕЖИМОВ ---
        # Это крупный режим приложения, а не мелкая настройка.
        # Кнопки меняют набор доступных языков и OCR-движков, но не ломают
        # общие блоки профилей, хоткеев, динамики и запуска оверлея.
        self.language_mode_row = QHBoxLayout()
        self.language_mode_buttons = {}
        for mode_id in self.installed_language_modes:
            btn = QPushButton()
            btn.setProperty("uiRole", "languageMode")
            btn.setMinimumHeight(32)
            btn.clicked.connect(lambda checked=False, m=mode_id: self.set_language_mode(m))
            self.language_mode_buttons[mode_id] = btn
            self.language_mode_row.addWidget(btn)
        layout.addLayout(self.language_mode_row)

        # --- БЛОК ВЫБОРА ИГРЫ ---
        self.group_game = QGroupBox()
        game_layout = QVBoxLayout()

        self.lbl_active_window = QLabel()
        game_layout.addWidget(self.lbl_active_window)

        hbox_active = QHBoxLayout()
        self.active_windows_combo = QComboBox()
        self.refresh_btn = QPushButton()
        self.refresh_btn.setMinimumWidth(90)
        self.refresh_btn.clicked.connect(self.refresh_active_windows)
        hbox_active.addWidget(self.active_windows_combo, 1)
        hbox_active.addWidget(self.refresh_btn)
        game_layout.addLayout(hbox_active)

        self.lbl_saved_profile = QLabel()
        game_layout.addWidget(self.lbl_saved_profile)

        self.saved_profiles_combo = QComboBox()
        self.saved_profiles_combo.currentTextChanged.connect(self.on_profile_selected)
        game_layout.addWidget(self.saved_profiles_combo)

        self.group_game.setLayout(game_layout)
        layout.addWidget(self.group_game)

        # --- БЛОК НАСТРОЕК СЕССИИ ---
        self.group_settings = QGroupBox()
        form_layout = QFormLayout()
        form_layout.setVerticalSpacing(5)

        self.source_lang_combo = QComboBox()
        self.lbl_source_language = QLabel()
        form_layout.addRow(self.lbl_source_language, self.source_lang_combo)

        self.lang_combo = QComboBox()
        self.lbl_target_language = QLabel()
        form_layout.addRow(self.lbl_target_language, self.lang_combo)
        self.populate_language_combos()

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["offline", "online"])
        self.mode_combo.setCurrentText(config.TRANSLATION_MODE)
        self.mode_combo.currentTextChanged.connect(self.on_translation_mode_changed)
        self.lbl_translation_mode = QLabel()
        form_layout.addRow(self.lbl_translation_mode, self.mode_combo)
        self.online_settings = normalize_online_settings()
        self.online_settings_button = QPushButton(tr(self.interface_lang, "online_settings"))
        self.online_settings_button.clicked.connect(self.edit_online_settings)
        form_layout.addRow(self.online_settings_button)

        self.local_translator_combo = QComboBox()
        self.populate_local_translator_combo(self.local_translator_preference)
        self.local_translator_combo.currentIndexChanged.connect(self.on_local_translator_changed)
        self.lang_combo.currentIndexChanged.connect(self.on_target_language_changed)
        self.source_lang_combo.currentIndexChanged.connect(self.on_source_language_changed)
        self.lbl_local_translator = QLabel()
        form_layout.addRow(self.lbl_local_translator, self.local_translator_combo)

        self.block2_translation_profile_combo = QComboBox()
        self.block2_translation_profile_combo.currentIndexChanged.connect(self.on_block2_translation_profile_changed)
        self.lbl_block2_translation_profile = QLabel()
        form_layout.addRow(self.lbl_block2_translation_profile, self.block2_translation_profile_combo)

        self.block2_chinese_script_combo = QComboBox()
        self.block2_chinese_script_combo.currentIndexChanged.connect(self.on_block2_chinese_script_changed)
        self.lbl_block2_chinese_script = QLabel()
        form_layout.addRow(self.lbl_block2_chinese_script, self.block2_chinese_script_combo)
        self.populate_block2_controls(
            self.block2_translation_profile,
            self.block2_chinese_script_mode,
        )

        self.ocr_engine_combo = QComboBox()
        self.lbl_ocr_engine = QLabel()
        self.populate_ocr_engine_combo()
        self.ocr_engine_combo.currentIndexChanged.connect(self.on_ocr_engine_changed)
        form_layout.addRow(self.lbl_ocr_engine, self.ocr_engine_combo)

        self.subtitle_background_combo = QComboBox()
        self.lbl_subtitle_background = QLabel()
        self.populate_subtitle_background_combo()
        self.subtitle_background_combo.currentIndexChanged.connect(self.on_subtitle_settings_changed)
        form_layout.addRow(self.lbl_subtitle_background, self.subtitle_background_combo)

        self.subtitle_display_policy_combo = QComboBox()
        self.lbl_subtitle_display_policy = QLabel()
        self.populate_subtitle_display_policy_combo()
        self.subtitle_display_policy_combo.currentIndexChanged.connect(self.on_subtitle_settings_changed)
        form_layout.addRow(self.lbl_subtitle_display_policy, self.subtitle_display_policy_combo)

        self.btn_text_settings = QPushButton()
        self.btn_text_settings.clicked.connect(self.open_text_settings_dialog)
        form_layout.addRow("", self.btn_text_settings)

        self.btn_hotkeys_settings = QPushButton()
        self.btn_hotkeys_settings.clicked.connect(self.open_hotkeys_settings_dialog)
        form_layout.addRow("", self.btn_hotkeys_settings)

        self.btn_dynamic_settings = QPushButton()
        self.btn_dynamic_settings.clicked.connect(self.open_dynamic_settings_dialog)
        form_layout.addRow("", self.btn_dynamic_settings)

        self.developer_logging_checkbox = QCheckBox()
        self.developer_logging_checkbox.setChecked(launcher_log_policy(self.launcher_settings) == "developer")
        self.developer_logging_checkbox.toggled.connect(self.save_launcher_settings)
        form_layout.addRow("", self.developer_logging_checkbox)

        self.btn_important_warning = QPushButton()
        self.btn_important_warning.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning))
        self.btn_important_warning.clicked.connect(self.open_important_warning_dialog)
        self.btn_important_warning.setProperty("uiRole", "warningAction")
        form_layout.addRow("", self.btn_important_warning)

        self.group_settings.setLayout(form_layout)
        layout.addWidget(self.group_settings)
        layout.addStretch()
        content.setLayout(layout)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        # Кнопка запуска доступна сразу.
        # Модель перевода грузится уже в процессе оверлея, потому что лаунчер и оверлей — разные процессы.
        self.btn_launch = QPushButton()
        self.btn_launch.setEnabled(True)
        self.btn_launch.setProperty("uiRole", "launch")
        self.btn_launch.clicked.connect(self.launch_overlay)
        outer.addWidget(self.btn_launch)

        self.lbl_status = QLabel()
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet(status_style("success"))
        outer.addWidget(self.lbl_status)

        for combo in content.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(12)
        self.apply_localization()

    def load_interface_lang(self):
        """Читает глобальный язык интерфейса из profiles.json/config.PROFILES_FILE."""
        try:
            if os.path.exists(config.PROFILES_FILE):
                with open(config.PROFILES_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return normalize_interface_lang(data.get("global", {}).get("interface_lang", "uk"))
        except Exception:
            pass
        return "uk"

    def save_interface_lang(self):
        """Сохраняет глобальный язык интерфейса в profiles.json, не трогая профили игр."""
        try:
            data = {}
            if os.path.exists(config.PROFILES_FILE):
                with open(config.PROFILES_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            if not isinstance(data, dict):
                data = {}
            data.setdefault("global", {})["interface_lang"] = self.interface_lang
            data.setdefault("profiles", data.get("profiles", {}))
            with open(config.PROFILES_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"[LOCALIZATION SAVE ERROR]: {e}")

    def cycle_interface_language(self):
        """Переключает язык интерфейса циклически без перезапуска лаунчера."""
        self.interface_lang = next_interface_lang(self.interface_lang)
        self.save_interface_lang()
        self.apply_localization()
        self.lbl_status.setText(tr(self.interface_lang, "status_ready"))


    def language_icon_path(self, lang):
        """
        Возвращает абсолютный путь к PNG-иконке языка.

        Иконки лежат в assets/icons и не зависят от системных emoji-шрифтов.
        Если файл отсутствует, кнопка языка просто покажет текстовый fallback.
        """
        icon_file = interface_lang_icon_file(lang)
        if not icon_file:
            return ""
        return os.path.join(ICONS_DIR, icon_file)

    def apply_interface_language_icon(self):
        """Применяет PNG-иконку текущего языка к кнопке переключения интерфейса."""
        if not hasattr(self, "btn_interface_language"):
            return

        icon_path = self.language_icon_path(self.interface_lang)
        if icon_path and os.path.exists(icon_path):
            self.btn_interface_language.setIcon(QIcon(icon_path))
        else:
            # Безопасный fallback: если пользователь не скопировал assets/icons,
            # интерфейс не ломается, остаётся короткий текст языка.
            self.btn_interface_language.setIcon(QIcon())

    def hotkey_title(self, action):
        """Локализованное название действия hotkey."""
        return hotkey_title_for_lang(action, self.interface_lang)

    def apply_localization(self):
        """Применяет язык интерфейса к уже созданным виджетам лаунчера."""
        self.setWindowTitle(tr(self.interface_lang, "app_title"))
        if hasattr(self, "btn_help"):
            self.btn_help.setText(tr(self.interface_lang, "help_title"))
        for dialog in self.findChildren(TextSettingsDialog):
            dialog.apply_localization(self.interface_lang)

        if hasattr(self, "lbl_interface_language"):
            self.lbl_interface_language.setText(tr(self.interface_lang, "interface_language_label") + ":")
        if hasattr(self, "btn_interface_language"):
            self.btn_interface_language.setText(interface_lang_button_text(self.interface_lang))
            self.apply_interface_language_icon()

        if hasattr(self, "group_game"):
            self.group_game.setTitle(tr(self.interface_lang, "group_game"))
        if hasattr(self, "lbl_active_window"):
            self.lbl_active_window.setText(tr(self.interface_lang, "active_window_label"))
        if hasattr(self, "refresh_btn"):
            self.refresh_btn.setText(tr(self.interface_lang, "refresh_windows"))
        if hasattr(self, "lbl_saved_profile"):
            self.lbl_saved_profile.setText(tr(self.interface_lang, "saved_profile_label"))

        if hasattr(self, "group_settings"):
            self.group_settings.setTitle(tr(self.interface_lang, "group_session_settings"))
        if hasattr(self, "developer_logging_checkbox"):
            self.developer_logging_checkbox.setText(tr(self.interface_lang, "developer_logging"))
            hint = tr(self.interface_lang, "developer_logging_hint")
            self.developer_logging_checkbox.setToolTip(hint)
            self.developer_logging_checkbox.setAccessibleDescription(hint)
        if hasattr(self, "language_mode_buttons"):
            self.apply_language_mode_buttons_state()
        if hasattr(self, "lbl_source_language"):
            self.lbl_source_language.setText(tr(self.interface_lang, "source_language"))
        if hasattr(self, "lbl_target_language"):
            self.lbl_target_language.setText(tr(self.interface_lang, "target_language"))
        # При смене локализации нужно пересобрать списки языков.
        # Иначе combobox оставляет старые подписи: например, "китайский"
        # после переключения лаунчера на английский интерфейс.
        if hasattr(self, "source_lang_combo") and hasattr(self, "lang_combo"):
            self.populate_language_combos()
        if hasattr(self, "lbl_translation_mode"):
            self.lbl_translation_mode.setText(tr(self.interface_lang, "translation_mode"))
        if hasattr(self, "online_settings_button"):
            self.online_settings_button.setText(tr(self.interface_lang, "online_settings"))
        if hasattr(self, "lbl_local_translator"):
            self.lbl_local_translator.setText(tr(self.interface_lang, "local_translator_label"))
        if hasattr(self, "local_translator_combo"):
            self.populate_local_translator_combo(self.current_local_translator_preference())
        if hasattr(self, "lbl_block2_translation_profile"):
            self.lbl_block2_translation_profile.setText(tr(self.interface_lang, "block2_translation_profile_label"))
        if hasattr(self, "lbl_block2_chinese_script"):
            self.lbl_block2_chinese_script.setText(tr(self.interface_lang, "block2_chinese_script_label"))
        if hasattr(self, "block2_translation_profile_combo"):
            self.populate_block2_controls()
        if hasattr(self, "lbl_ocr_engine"):
            self.lbl_ocr_engine.setText(tr(self.interface_lang, "ocr_engine_label"))
        if hasattr(self, "ocr_engine_combo"):
            self.populate_ocr_engine_combo()
        if hasattr(self, "lbl_subtitle_background"):
            self.lbl_subtitle_background.setText(tr(self.interface_lang, "subtitle_background_label"))
        if hasattr(self, "subtitle_background_combo"):
            self.populate_subtitle_background_combo()
        if hasattr(self, "lbl_subtitle_display_policy"):
            self.lbl_subtitle_display_policy.setText(tr(self.interface_lang, "subtitle_display_policy_label"))
        if hasattr(self, "subtitle_display_policy_combo"):
            self.populate_subtitle_display_policy_combo()
        if hasattr(self, "block2_translation_profile_combo"):
            self.update_language_mode_control_visibility()
        if hasattr(self, "btn_text_settings"):
            self.btn_text_settings.setText(tr(self.interface_lang, "text_settings_button"))
        if hasattr(self, "btn_hotkeys_settings"):
            self.btn_hotkeys_settings.setText(tr(self.interface_lang, "hotkeys_settings_button"))
        if hasattr(self, "btn_dynamic_settings"):
            self.btn_dynamic_settings.setText(tr(self.interface_lang, "dynamic_settings_button"))
        if hasattr(self, "btn_important_warning"):
            self.btn_important_warning.setText(tr(self.interface_lang, "important_button").removeprefix("\u2757 "))
        if hasattr(self, "btn_launch"):
            self.btn_launch.setText(tr(self.interface_lang, "launch_overlay"))

        # Обновляем плейсхолдеры combobox без перезагрузки списков и без сброса выбора пользователя.
        if hasattr(self, "active_windows_combo") and self.active_windows_combo.count() > 0:
            self.active_windows_combo.setItemText(0, tr(self.interface_lang, "active_window_placeholder"))
        if hasattr(self, "saved_profiles_combo") and self.saved_profiles_combo.count() > 0:
            self.saved_profiles_combo.setItemText(0, tr(self.interface_lang, "saved_profile_placeholder"))

    def apply_language_mode_buttons_state(self):
        """Обновляет подписи и состояние трёх крупных кнопок языковых режимов."""
        if not hasattr(self, "language_mode_buttons"):
            return
        labels = {
            LANGUAGE_MODE_EURO: tr(self.interface_lang, "language_mode_euro"),
            LANGUAGE_MODE_ASIAN_TO_EURO: tr(self.interface_lang, "language_mode_asian_to_euro"),
            LANGUAGE_MODE_EURO_TO_ASIAN: tr(self.interface_lang, "language_mode_euro_to_asian"),
        }
        for mode_id, btn in self.language_mode_buttons.items():
            is_active = normalize_language_mode(self.language_mode) == mode_id
            btn.setText(labels.get(mode_id, mode_id))
            btn.setEnabled(not is_active)

    def installed_language_mode(self, value):
        mode = normalize_language_mode(value)
        return mode if mode in self.installed_language_modes else self.installed_language_modes[0]

    def set_language_mode(self, mode_id):
        """Переключает крупный языковой режим лаунчера."""
        if self._profile_load_in_progress:
            return
        mode_id = normalize_language_mode(mode_id)
        if mode_id == self.language_mode or mode_id not in self.installed_language_modes:
            return
        self.capture_current_block_ui_state()
        self.language_mode = mode_id
        self.language_mode_modified = True
        block_id = block_id_for_language_mode(mode_id)
        self._loaded_profile_data = apply_block_state(self._loaded_profile_data, block_id)
        self.restore_active_block_ui_state()
        self.populate_language_combos()
        self.populate_local_translator_combo(self.current_local_translator_preference())
        self.populate_subtitle_background_combo(preserve_current_ui=False)
        self.populate_subtitle_display_policy_combo(preserve_current_ui=False)
        self.populate_ocr_engine_combo(preserve_current_ui=False)
        self.populate_block2_controls(
            self.block2_translation_profile,
            self.block2_chinese_script_mode,
        )
        self.update_language_mode_control_visibility()
        self.apply_language_mode_buttons_state()
        self.save_launcher_settings()
        self.persist_current_profile_state("language_mode_changed")

    def capture_current_block_ui_state(self):
        """Copies current widgets into the active block snapshot in profile memory."""
        if self._block_profile_change_in_progress or self._profile_load_in_progress:
            return
        self._loaded_profile_data, _status = ensure_language_blocks(self._loaded_profile_data)
        block_id = block_id_for_language_mode(self.language_mode)
        state = normalize_block_state(
            self._loaded_profile_data[BLOCKS_KEY].get(block_id),
            block_id,
            self._loaded_profile_data,
        )
        translation = dict(state.get("translation") or {})
        translation.update({
            "language_mode": self.language_mode,
            "source_lang": self.current_source_lang_from_ui(),
            "target_lang": self.current_target_lang_from_ui(),
            "mode": self.mode_combo.currentText(),
            "online": normalize_online_settings(getattr(self, "online_settings", None)),
        })
        if block_id == BLOCK1:
            translation["backend_preference"] = self.current_local_translator_preference()
        elif block_id == BLOCK2:
            translation.update({
                "translation_profile": self.current_block2_translation_profile(),
                "chinese_script_mode": self.current_block2_chinese_script_mode(),
                "backend": "block2_router",
            })
        else:
            translation.update({
                "source_lang": "en",
                "translation_profile": self.current_block2_translation_profile(),
                "backend": "block3_router",
            })
            translation.pop("chinese_script_mode", None)
        state["translation"] = translation
        state["dynamic"] = normalize_dynamic_settings(self.dynamic_settings)
        state["subtitles"] = normalize_subtitle_settings(self.current_subtitle_settings_from_ui())
        ocr = self.current_ocr_settings_from_ui()
        if block_id == BLOCK2:
            previous_ocr = state.get("ocr") if isinstance(state.get("ocr"), dict) else {}
            ocr["advanced_override"] = bool(previous_ocr.get("advanced_override", False))
        state["ocr"] = ocr
        self._loaded_profile_data = replace_block_state(
            self._loaded_profile_data,
            block_id,
            state,
            make_active=True,
        )

    def selected_profile_path_for_persistence(self):
        """Return only the profile explicitly selected in the saved-profile combo."""
        if self._profile_load_in_progress or not hasattr(self, "saved_profiles_combo"):
            return ""
        try:
            profile_path = normalize_profile_path(self.saved_profiles_combo.currentData())
        except Exception:
            return ""
        if not profile_path_is_app_local(profile_path) or not os.path.isfile(profile_path):
            return ""
        return profile_path

    def restore_active_block_ui_state(self):
        block_id = block_id_for_language_mode(self.language_mode)
        self._loaded_profile_data = apply_block_state(self._loaded_profile_data, block_id)
        state = self._loaded_profile_data[BLOCKS_KEY][block_id]
        translation = state.get("translation", {})
        self._block_profile_change_in_progress = True
        try:
            self.online_settings = normalize_online_settings(translation.get("online"))
            self.mode_combo.blockSignals(True)
            self.mode_combo.setCurrentText(translation.get("mode", "offline"))
            self.mode_combo.blockSignals(False)
            self.dynamic_settings = normalize_dynamic_settings(state.get("dynamic", {}))
            self.subtitle_settings = normalize_subtitle_settings(state.get("subtitles", {}))
            self.ocr_settings = normalize_ocr_settings(state.get("ocr", {}))
            self.local_translator_preference = normalize_local_translator_preference(
                translation.get("backend_preference", self.local_translator_preference)
            )
            self.block2_translation_profile = str(translation.get("translation_profile") or "fast")
            self.block2_chinese_script_mode = str(translation.get("chinese_script_mode") or "auto")
        finally:
            self._block_profile_change_in_progress = False

    def persist_current_profile_state(self, reason="ui_changed"):
        if self._block_profile_change_in_progress or self._profile_load_in_progress:
            return
        profile_path = self.selected_profile_path_for_persistence()
        if not profile_path:
            return
        try:
            self.current_profile_path = profile_path
            self.current_profile_id = stable_profile_id(profile_path)
            self.capture_current_block_ui_state()
            atomic_write_json(profile_path, self._loaded_profile_data)
            log_launcher_timing(
                "BLOCK_PROFILE_STATE_SAVED",
                reason=reason,
                block=block_id_for_language_mode(self.language_mode),
                profile_id=stable_profile_id(profile_path),
            )
            if block_id_for_language_mode(self.language_mode) == BLOCK2:
                log_launcher_timing(
                    "block2_state_changed",
                    reason=reason,
                    profile_id=stable_profile_id(profile_path),
                )
            elif block_id_for_language_mode(self.language_mode) == BLOCK3:
                log_launcher_timing(
                    "block3_state_changed",
                    reason=reason,
                    profile_id=stable_profile_id(profile_path),
                )
        except Exception as exc:
            log_launcher_timing("BLOCK_PROFILE_STATE_SAVE_FAILED", reason=reason, error=f"{type(exc).__name__}:{exc}")

    def current_block2_translation_profile(self):
        if hasattr(self, "block2_translation_profile_combo"):
            value = str(self.block2_translation_profile_combo.currentData() or "fast")
        else:
            value = str(getattr(self, "block2_translation_profile", "fast"))
        return value if value in {BLOCK2_PROFILE_FAST, BLOCK2_PROFILE_QUALITY} else BLOCK2_PROFILE_FAST

    def current_block2_chinese_script_mode(self):
        if hasattr(self, "block2_chinese_script_combo"):
            value = str(self.block2_chinese_script_combo.currentData() or "auto")
        else:
            value = str(getattr(self, "block2_chinese_script_mode", "auto"))
        return value if value in {"auto", "zh", "zh_hant"} else "auto"

    def populate_block2_controls(self, preferred_profile=None, preferred_script=None):
        if not hasattr(self, "block2_translation_profile_combo"):
            return
        profile = str(
            preferred_profile
            if preferred_profile is not None
            else self.current_block2_translation_profile()
        )
        if profile not in {BLOCK2_PROFILE_FAST, BLOCK2_PROFILE_QUALITY}:
            profile = BLOCK2_PROFILE_FAST
        script = str(
            preferred_script
            if preferred_script is not None
            else self.current_block2_chinese_script_mode()
        )
        if script not in {"auto", "zh", "zh_hant"}:
            script = "auto"
        self.block2_translation_profile_combo.blockSignals(True)
        self.block2_translation_profile_combo.clear()
        self.block2_translation_profile_combo.addItem(tr(self.interface_lang, "block2_profile_fast"), "fast")
        self.block2_translation_profile_combo.addItem(tr(self.interface_lang, "block2_profile_quality"), "quality")
        self.block2_translation_profile_combo.setCurrentIndex(max(0, self.block2_translation_profile_combo.findData(profile)))
        self.block2_translation_profile_combo.blockSignals(False)
        self.block2_chinese_script_combo.blockSignals(True)
        self.block2_chinese_script_combo.clear()
        self.block2_chinese_script_combo.addItem(tr(self.interface_lang, "block2_script_auto"), "auto")
        self.block2_chinese_script_combo.addItem(tr(self.interface_lang, "block2_script_simplified"), "zh")
        self.block2_chinese_script_combo.addItem(tr(self.interface_lang, "block2_script_traditional"), "zh_hant")
        self.block2_chinese_script_combo.setCurrentIndex(max(0, self.block2_chinese_script_combo.findData(script)))
        self.block2_chinese_script_combo.blockSignals(False)

    def update_language_mode_control_visibility(self):
        is_block2 = self.language_mode == LANGUAGE_MODE_ASIAN_TO_EURO
        is_block3 = self.language_mode == LANGUAGE_MODE_EURO_TO_ASIAN
        is_block_router = is_block2 or is_block3
        for widget in (self.lbl_block2_translation_profile, self.block2_translation_profile_combo):
            widget.setVisible(is_block_router)
        chinese_options_visible = is_block2 and self.current_source_lang_from_ui() == "zh"
        for widget in (self.lbl_block2_chinese_script, self.block2_chinese_script_combo):
            widget.setVisible(chinese_options_visible)
        for widget in (self.lbl_local_translator, self.local_translator_combo):
            widget.setVisible(not is_block_router)
        self.mode_combo.setEnabled(True)
        self.update_translation_backend_controls()

    def update_translation_backend_controls(self):
        offline = self.mode_combo.currentText() != "online"
        for name in ("local_translator_combo", "block2_translation_profile_combo"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(offline)

    def on_translation_mode_changed(self, _value):
        self.update_translation_backend_controls()
        self.persist_current_profile_state("translation_mode_changed")

    def open_help(self):
        lang = normalize_interface_lang(self.interface_lang)
        title = tr(lang, "help_title")
        path = resource_path("app.assets") / "help" / f"{lang}.txt"
        try:
            guide = path.read_text(encoding="utf-8").replace("{logs_path}", str(data_path("sessions")))
        except (OSError, UnicodeError):
            QMessageBox.warning(self, title, tr(lang, "help_unavailable"))
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setMinimumSize(400, 300)
        screen = self.screen()
        height = min(620, screen.availableGeometry().height() - 64) if screen else 620
        dialog.resize(660, height)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        heading = QLabel(tr(lang, "app_title") + " | " + title)
        heading.setProperty("uiRole", "heading")
        layout.addWidget(heading)
        editor = QPlainTextEdit()
        body_font = editor.font()
        body_font.setPointSizeF(body_font.pointSizeF() + 2)
        editor.setFont(body_font)
        editor.setPlainText(guide)
        editor.setReadOnly(True)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(editor, 1)
        buttons = QHBoxLayout()
        buttons.addStretch()
        close = QPushButton(tr(lang, "close"))
        close.clicked.connect(dialog.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        dialog.exec()

    def edit_online_settings(self):
        from online_ui import OnlineSettingsDialog
        dialog = OnlineSettingsDialog(self, getattr(self, "online_settings", None), self.interface_lang,
            self.current_source_lang_from_ui(), self.current_target_lang_from_ui())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.online_settings = dialog.result_settings
            self.persist_current_profile_state("online_settings_changed")

    def on_block2_translation_profile_changed(self):
        if self._block_profile_change_in_progress or self._profile_load_in_progress:
            return
        self.block2_translation_profile = self.current_block2_translation_profile()
        if self.language_mode == LANGUAGE_MODE_ASIAN_TO_EURO:
            reason = "block2_translation_profile_changed"
        else:
            reason = "block3_translation_profile_changed"
        self.populate_ocr_engine_combo(preserve_current_ui=False)
        self.ocr_settings = self.current_ocr_settings_from_ui()
        self.persist_current_profile_state(reason)

    def on_block2_chinese_script_changed(self):
        if self._block_profile_change_in_progress or self._profile_load_in_progress:
            return
        self.block2_chinese_script_mode = self.current_block2_chinese_script_mode()
        self.persist_current_profile_state("block2_chinese_script_changed")

    def populate_language_combos(self):
        """Заполняет source/target language списки согласно текущему языковому режиму."""
        if not hasattr(self, "source_lang_combo") or not hasattr(self, "lang_combo"):
            return

        cfg = language_mode_config(self.language_mode)
        current_source = self.source_lang_combo.currentData() if self.source_lang_combo.count() else None
        current_target = self.lang_combo.currentData() if self.lang_combo.count() else None
        if self.language_mode in {LANGUAGE_MODE_EURO, LANGUAGE_MODE_ASIAN_TO_EURO, LANGUAGE_MODE_EURO_TO_ASIAN}:
            try:
                block_id = block_id_for_language_mode(self.language_mode)
                block_translation = self._loaded_profile_data[BLOCKS_KEY][block_id]["translation"]
                current_source = block_translation.get("source_lang", current_source)
                current_target = block_translation.get("target_lang", current_target)
            except Exception:
                pass

        self.source_lang_combo.blockSignals(True)
        self.lang_combo.blockSignals(True)
        self.source_lang_combo.clear()
        self.lang_combo.clear()

        for code, label in cfg["source_options"]:
            self.source_lang_combo.addItem(language_option_text(self.interface_lang, label), code)
        for code, label in cfg["target_options"]:
            self.lang_combo.addItem(language_option_text(self.interface_lang, label), code)

        source_code = current_source if current_source in [x[0] for x in cfg["source_options"]] else cfg["default_source"]
        target_code = current_target if current_target in [x[0] for x in cfg["target_options"]] else cfg["default_target"]

        source_index = self.source_lang_combo.findData(source_code)
        target_index = self.lang_combo.findData(target_code)
        self.source_lang_combo.setCurrentIndex(max(0, source_index))
        self.lang_combo.setCurrentIndex(max(0, target_index))

        # Если в режиме исходный язык фиксирован, показываем его, но не даём менять.
        self.source_lang_combo.setEnabled(len(cfg["source_options"]) > 1)

        self.source_lang_combo.blockSignals(False)
        self.lang_combo.blockSignals(False)

    def current_source_lang_from_ui(self):
        """Возвращает исходный язык текущего языкового режима."""
        if hasattr(self, "source_lang_combo") and self.source_lang_combo.count() > 0:
            return str(self.source_lang_combo.currentData() or language_mode_config(self.language_mode)["default_source"])
        return language_mode_config(self.language_mode)["default_source"]

    def current_target_lang_from_ui(self):
        """Возвращает целевой язык текущего языкового режима."""
        if hasattr(self, "lang_combo") and self.lang_combo.count() > 0:
            return str(self.lang_combo.currentData() or language_mode_config(self.language_mode)["default_target"])
        return language_mode_config(self.language_mode)["default_target"]

    def current_local_translator_preference(self):
        if hasattr(self, "local_translator_combo") and self.local_translator_combo.count() > 0:
            return normalize_local_translator_preference(self.local_translator_combo.currentData())
        return normalize_local_translator_preference(getattr(self, "local_translator_preference", LOCAL_TRANSLATOR_AUTO))

    def populate_local_translator_combo(self, preferred=None):
        """Заполняет Block 1 translator combo и отключает Marian для EN→UK."""
        if not hasattr(self, "local_translator_combo"):
            return

        preference = normalize_local_translator_preference(
            preferred if preferred is not None else self.current_local_translator_preference()
        )
        if (
            normalize_language_mode(self.language_mode) == LANGUAGE_MODE_EURO
            and resolve_block1_translation_backend(
                preference,
                self.current_source_lang_from_ui(),
                self.current_target_lang_from_ui(),
            ) is None
        ):
            preference = LOCAL_TRANSLATOR_AUTO
        self.local_translator_preference = preference
        combo = self.local_translator_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(tr(self.interface_lang, "local_translator_auto"), LOCAL_TRANSLATOR_AUTO)
        combo.addItem(tr(self.interface_lang, "local_translator_bergamot"), LOCAL_TRANSLATOR_BERGAMOT)
        combo.addItem(tr(self.interface_lang, "local_translator_current_base"), LOCAL_TRANSLATOR_CURRENT_BASE)
        combo.addItem(
            tr(self.interface_lang, "local_translator_translategemma_llama_q6"),
            LOCAL_TRANSLATOR_TRANSLATEGEMMA_LLAMA_Q6,
        )
        combo.addItem(
            tr(self.interface_lang, "local_translator_translategemma_llama_q6_speed"),
            LOCAL_TRANSLATOR_TRANSLATEGEMMA_LLAMA_Q6_SPEED,
        )

        selected_index = combo.findData(preference)
        combo.setCurrentIndex(max(0, selected_index))

        current_base_index = combo.findData(LOCAL_TRANSLATOR_CURRENT_BASE)
        current_base_available = (
            self.current_source_lang_from_ui() == "en" and self.current_target_lang_from_ui() == "ru"
        )
        item = combo.model().item(current_base_index) if current_base_index >= 0 else None
        if item is not None:
            item.setEnabled(current_base_available)
            item.setToolTip("" if current_base_available else tr(self.interface_lang, "local_translator_current_base_uk_warning"))
        combo.setItemData(
            current_base_index,
            "" if current_base_available else tr(self.interface_lang, "local_translator_current_base_uk_warning"),
            Qt.ItemDataRole.ToolTipRole,
        )
        combo.setToolTip("" if current_base_available else tr(self.interface_lang, "local_translator_current_base_uk_warning"))
        combo.blockSignals(False)

    def on_target_language_changed(self):
        if self._profile_load_in_progress:
            return
        self.populate_local_translator_combo(self.current_local_translator_preference())
        self.persist_current_profile_state("target_language_changed")

    def on_source_language_changed(self):
        if self._profile_load_in_progress:
            return
        self.populate_local_translator_combo(self.current_local_translator_preference())
        self.update_language_mode_control_visibility()
        self.persist_current_profile_state("source_language_changed")

    def on_local_translator_changed(self):
        if self._profile_load_in_progress:
            return
        self.local_translator_preference = self.current_local_translator_preference()
        self.local_translator_modified = True
        self.save_launcher_settings()
        self.persist_current_profile_state("local_translator_changed")


    def current_llm_settings_from_ui(self):
        """Возвращает настройки LLM-редактора из простого UI лаунчера."""
        mode = "off"
        return llm_settings_from_mode(mode)


    def populate_ocr_engine_combo(self, *, preserve_current_ui=True):
        """Заполняет список OCR-движков с учётом текущего языкового режима."""
        if not hasattr(self, "ocr_engine_combo"):
            return

        cfg = language_mode_config(self.language_mode)
        stored_ocr = normalize_ocr_settings(getattr(self, "ocr_settings", {}))
        current = stored_ocr.get("engine", cfg["default_ocr"])
        if preserve_current_ui:
            try:
                current = str(self.ocr_engine_combo.currentData() or current)
            except Exception:
                pass

        subtitle_settings = normalize_subtitle_settings(getattr(self, "subtitle_settings", {}))
        background_mode = subtitle_settings["background_mode"]
        display_policy = subtitle_settings["display_policy"]
        if hasattr(self, "subtitle_background_combo") and self.subtitle_background_combo.count():
            try:
                background_mode = str(
                    self.subtitle_background_combo.currentData() or background_mode
                )
            except Exception:
                pass
        if hasattr(self, "subtitle_display_policy_combo") and self.subtitle_display_policy_combo.count():
            try:
                display_policy = str(
                    self.subtitle_display_policy_combo.currentData() or display_policy
                )
            except Exception:
                pass

        block_id = block_id_for_language_mode(self.language_mode)
        if block_id == BLOCK2:
            translation_profile = self.current_block2_translation_profile()
            quality_engine = normalize_quality_ocr_engine(
                stored_ocr.get("quality_engine"),
                block_id,
            )
            fast_engine = fast_ocr_engine_for_block(block_id)
            allowed = (
                [fast_engine]
                if translation_profile == BLOCK2_PROFILE_FAST
                else [fast_engine, "paddle"]
            )
            current = ocr_engine_for_translation_profile(
                block_id,
                translation_profile,
                quality_engine,
            )
            stored_ocr["quality_engine"] = quality_engine
        else:
            allowed = compatible_ocr_options(self.language_mode, display_policy, background_mode)
            if current not in allowed:
                current = cfg.get("default_ocr", allowed[0] if allowed else "tesseract")
            if current not in allowed and allowed:
                current = allowed[0]

        labels = {
            "tesseract": tr(self.interface_lang, "ocr_engine_tesseract"),
            "paddle_fast": tr(self.interface_lang, "ocr_engine_paddle_fast"),
            "paddle_block2_fast": tr(self.interface_lang, "ocr_engine_paddle_block2_fast"),
            "paddle": tr(self.interface_lang, "ocr_engine_paddle"),
        }

        self.ocr_engine_combo.blockSignals(True)
        self.ocr_engine_combo.clear()
        for engine in allowed:
            self.ocr_engine_combo.addItem(labels.get(engine, engine), engine)

        index = self.ocr_engine_combo.findData(current)
        if index < 0:
            index = 0
        self.ocr_engine_combo.setCurrentIndex(index)
        self.ocr_engine_combo.setEnabled(len(allowed) > 1)
        self.ocr_engine_combo.blockSignals(False)

        selected = str(self.ocr_engine_combo.currentData() or current)
        stored_ocr["engine"] = selected
        if block_id == BLOCK2 and self.current_block2_translation_profile() == BLOCK2_PROFILE_QUALITY:
            stored_ocr["quality_engine"] = normalize_quality_ocr_engine(selected, block_id)
        self.ocr_settings = stored_ocr

    def current_ocr_settings_from_ui(self):
        """Возвращает выбранный OCR-движок из UI лаунчера."""
        engine = "tesseract"
        if hasattr(self, "ocr_engine_combo"):
            try:
                engine = str(self.ocr_engine_combo.currentData() or "tesseract")
            except Exception:
                pass
        result = normalize_ocr_settings(getattr(self, "ocr_settings", {}))
        block_id = block_id_for_language_mode(self.language_mode)
        if block_id == BLOCK2:
            quality_engine = normalize_quality_ocr_engine(
                result.get("quality_engine"),
                block_id,
            )
            if self.current_block2_translation_profile() == BLOCK2_PROFILE_QUALITY:
                quality_engine = normalize_quality_ocr_engine(engine, block_id)
            result["quality_engine"] = quality_engine
            result["engine"] = ocr_engine_for_translation_profile(
                block_id,
                self.current_block2_translation_profile(),
                quality_engine,
            )
        else:
            result["engine"] = engine
        return normalize_ocr_settings(result)

    def current_subtitle_settings_from_ui(self):
        background_mode = STATIC_PANEL
        display_policy = FOLLOW_SOURCE
        if hasattr(self, "subtitle_background_combo"):
            background_mode = str(self.subtitle_background_combo.currentData() or STATIC_PANEL)
        if hasattr(self, "subtitle_display_policy_combo"):
            display_policy = str(self.subtitle_display_policy_combo.currentData() or FOLLOW_SOURCE)
        return normalize_subtitle_settings({
            "background_mode": background_mode,
            "display_policy": display_policy,
        })

    def populate_subtitle_background_combo(self, *, preserve_current_ui=True):
        if not hasattr(self, "subtitle_background_combo"):
            return
        current = normalize_subtitle_settings(getattr(self, "subtitle_settings", {}))["background_mode"]
        if preserve_current_ui:
            try:
                current = str(self.subtitle_background_combo.currentData() or current)
            except Exception:
                pass
        combo = self.subtitle_background_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(tr(self.interface_lang, "subtitle_background_static_panel"), STATIC_PANEL)
        combo.addItem(tr(self.interface_lang, "subtitle_background_game_background"), GAME_BACKGROUND)
        index = combo.findData(current)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def populate_subtitle_display_policy_combo(self, *, preserve_current_ui=True):
        if not hasattr(self, "subtitle_display_policy_combo"):
            return
        current = normalize_subtitle_settings(getattr(self, "subtitle_settings", {}))["display_policy"]
        if preserve_current_ui:
            try:
                current = str(self.subtitle_display_policy_combo.currentData() or current)
            except Exception:
                pass
        combo = self.subtitle_display_policy_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(tr(self.interface_lang, "subtitle_display_follow_source"), FOLLOW_SOURCE)
        combo.addItem(tr(self.interface_lang, "subtitle_display_fast_subtitles"), FAST_SUBTITLES)
        index = combo.findData(current)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def on_subtitle_settings_changed(self):
        if self._profile_load_in_progress:
            return
        previous_ocr = self.current_ocr_settings_from_ui().get("engine")
        self.subtitle_settings = self.current_subtitle_settings_from_ui()
        self.subtitle_settings_modified = True
        self.populate_ocr_engine_combo()
        self.ocr_settings = self.current_ocr_settings_from_ui()
        if previous_ocr != self.ocr_settings.get("engine"):
            self.ocr_settings_modified = True
        self.save_launcher_settings()
        self.persist_current_profile_state("subtitle_settings_changed")

    def load_launcher_settings(self):
        """Читает лёгкие настройки лаунчера, не привязанные к конкретному профилю игры."""
        return read_launcher_settings()

    def save_launcher_settings(self):
        """Сохраняет последний выбранный OCR-движок как дефолт для следующего запуска лаунчера."""
        try:
            data = dict(getattr(self, "launcher_settings", {}) or {})
            if hasattr(self, "developer_logging_checkbox"):
                data["developer_logging"] = self.developer_logging_checkbox.isChecked()
            data["language_mode"] = normalize_language_mode(getattr(self, "language_mode", LANGUAGE_MODE_EURO))
            data["local_translator"] = self.current_local_translator_preference()
            data["ocr"] = normalize_ocr_settings(getattr(self, "ocr_settings", {}))
            data["llm"] = normalize_llm_settings(getattr(self, "llm_settings", {}))
            data["subtitles"] = normalize_subtitle_settings(
                self.current_subtitle_settings_from_ui()
                if hasattr(self, "subtitle_background_combo")
                else getattr(self, "subtitle_settings", {})
            )
            if profile_path_is_app_local(getattr(self, "current_profile_path", "")):
                data["last_profile_path"] = normalize_profile_path(self.current_profile_path)
                data["last_profile_id"] = stable_profile_id(self.current_profile_path)
            os.makedirs(os.path.dirname(LAUNCHER_SETTINGS_FILE), exist_ok=True)
            with open(LAUNCHER_SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            self.launcher_settings = data
        except Exception:
            pass

    def on_ocr_engine_changed(self):
        """Пользователь сменил OCR-движок в лаунчере."""
        if self._profile_load_in_progress:
            return
        self.ocr_settings = self.current_ocr_settings_from_ui()
        self.ocr_settings_modified = True
        self.save_launcher_settings()
        self.persist_current_profile_state("ocr_engine_changed")

    def safe_profile_name(self, name):
        """Безопасное имя папки профиля Windows."""
        clean = re.sub(r'[\\/*?:"<>|]', "", normalize_profile_display_name(name))
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean[:120] or "unknown_game"

    def profile_dir_for_game(self, game_name, create=False):
        """Новая структура профиля: profiles/<game>/..."""
        path = os.path.join(PROFILES_DIR, self.safe_profile_name(game_name))
        if create:
            os.makedirs(path, exist_ok=True)
        return path

    def profile_path_for_game(self, game_name, create_dir=False):
        """Новый основной файл настроек игры: profiles/<game>/profile.json."""
        return os.path.join(self.profile_dir_for_game(game_name, create=create_dir), "profile.json")

    def legacy_profile_path_for_game(self, game_name):
        """Старый формат до step13: profiles/<game>_profile.json."""
        return os.path.join(PROFILES_DIR, f"{self.safe_profile_name(game_name)}_profile.json")


    # Прогрев модели в лаунчере убран: оверлей запускается отдельным процессом
    # и загружает модель в своей памяти. Лаунчер не переводит текст сам.

    def open_text_settings_dialog(self):
        """Открывает отдельное окно настроек текста перевода."""
        dialog = TextSettingsDialog(
            self.text_settings,
            parent=self,
            interface_lang=self.interface_lang,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self.text_settings = normalize_text_settings(dialog.result_settings)
        self.text_settings_modified = True
        self.persist_current_profile_state("text_settings_changed")
        self.lbl_status.setStyleSheet(status_style("success"))
        self.lbl_status.setText(tr(self.interface_lang, "text_settings_saved"))

    def open_dynamic_settings_dialog(self):
        """Открывает отдельное окно настроек динамического перевода."""
        dialog = DynamicSettingsDialog(
            self.dynamic_settings,
            parent=self,
            interface_lang=self.interface_lang,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self.dynamic_settings = normalize_dynamic_settings(dialog.result_settings)
        self.dynamic_settings_modified = True
        self.persist_current_profile_state("dynamic_settings_changed")
        self.lbl_status.setStyleSheet(status_style("success"))
        self.lbl_status.setText(tr(self.interface_lang, "dynamic_settings_saved"))

    def open_hotkeys_settings_dialog(self):
        """Открывает отдельное окно настройки горячих клавиш."""
        dialog = HotkeysSettingsDialog(
            self.hotkeys,
            parent=self,
            interface_lang=self.interface_lang,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self.hotkeys = normalize_hotkeys(dialog.result_hotkeys)
        self.hotkeys_modified = True
        self.lbl_status.setStyleSheet(status_style("success"))
        self.lbl_status.setText(tr(self.interface_lang, "hotkeys_settings_saved"))

    def open_important_warning_dialog(self):
        """Открывает окно важных предупреждений для пользователя."""
        dialog = ImportantWarningDialog(parent=self, interface_lang=self.interface_lang)
        dialog.exec()

    def refresh_hotkey_buttons(self):
        """Обновляет подписи кнопок хоткеев в лаунчере."""
        for action, button in self.hotkey_buttons.items():
            button.setText(self.hotkeys.get(action, DEFAULT_HOTKEYS[action]).get("display", DEFAULT_HOTKEYS[action]["display"]))

    def set_hotkeys_from_profile(self, profile_data):
        """Загрузка hotkeys из профиля с мягким fallback на дефолт."""
        self.hotkeys = normalize_hotkeys(profile_data.get("hotkeys", {}))
        self.hotkeys_modified = False
        self.refresh_hotkey_buttons()

    def change_hotkey(self, action):
        """Открывает окно захвата нового хоткея для выбранного действия."""
        current = self.hotkeys.get(action, DEFAULT_HOTKEYS[action])
        dialog = HotkeyCaptureDialog(
            self.hotkey_title(action),
            current.get("display", DEFAULT_HOTKEYS[action]["display"]),
            self,
            interface_lang=self.interface_lang,
        )

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        new_keys = dialog.captured_keys
        new_display = dialog.captured_display
        if not new_keys or not new_display:
            return

        # Не даём назначить одно и то же сочетание на два разных действия.
        for other_action, other_data in self.hotkeys.items():
            if other_action != action and other_data.get("keys") == new_keys:
                QMessageBox.warning(
                    self,
                    tr(self.interface_lang, "hotkey_conflict_title"),
                    tr(self.interface_lang, "hotkey_conflict_body", hotkey=new_display, action=self.hotkey_title(other_action)),
                )
                return

        # Дополнительное мягкое предупреждение: эти клавиши разрешены, но часто заняты играми.
        # Не запрещаем их, потому что пользователь может специально настраивать хоткеи под конкретную игру.
        conflict_prone = set("WASD") | set("1234567890") | {f"F{i}" for i in range(1, 13)}
        if any(k in conflict_prone for k in new_keys):
            QMessageBox.information(
                self,
                tr(self.interface_lang, "hotkey_soft_warning_title"),
                tr(self.interface_lang, "hotkey_soft_warning_body"),
            )

        self.hotkeys[action] = {
            "display": new_display,
            "keys": list(new_keys),
        }
        self.hotkeys_modified = True
        self.refresh_hotkey_buttons()

    def reset_hotkeys_to_default(self):
        """Одна кнопка возврата к безопасным дефолтным сочетаниям."""
        self.hotkeys = copy_default_hotkeys()
        self.hotkeys_modified = True
        self.refresh_hotkey_buttons()
        self.lbl_status.setStyleSheet(status_style("success"))
        self.lbl_status.setText(tr(self.interface_lang, "status_hotkeys_reset"))

    def scan_saved_profiles(self):
        """Сканирование профилей игр.

        Step13: новый формат — profiles/<game>/profile.json.
        Старый формат profiles/<game>_profile.json читаем для совместимости,
        но если уже есть новая папка игры, дубль из legacy-файла не показываем.
        """
        self.saved_profiles_combo.blockSignals(True)
        self.saved_profiles_combo.clear()
        self.saved_profiles_combo.addItem(tr(self.interface_lang, "saved_profile_placeholder"))

        profiles = []
        seen_safe = set()

        if os.path.exists(PROFILES_DIR):
            # Новый формат: папка игры + profile.json.
            for item_name in sorted(os.listdir(PROFILES_DIR), key=str.lower):
                item_path = os.path.join(PROFILES_DIR, item_name)
                profile_path = os.path.join(item_path, "profile.json")
                if os.path.isdir(item_path) and os.path.exists(profile_path):
                    display_name = item_name
                    try:
                        with open(profile_path, "r", encoding="utf-8") as f:
                            prof = json.load(f)
                        display_name = str(prof.get("game_name") or item_name)
                    except Exception:
                        pass
                    display_name = normalize_profile_display_name(display_name)
                    safe = self.safe_profile_name(display_name).casefold()
                    if safe not in seen_safe:
                        seen_safe.add(safe)
                        profiles.append((display_name, normalize_profile_path(profile_path)))

            # Legacy-формат: profiles/<game>_profile.json.
            for file_name in sorted(os.listdir(PROFILES_DIR), key=str.lower):
                if file_name.endswith("_profile.json"):
                    display_name = file_name.replace("_profile.json", "")
                    display_name = normalize_profile_display_name(display_name)
                    safe = self.safe_profile_name(display_name).casefold()
                    if safe not in seen_safe:
                        seen_safe.add(safe)
                        profiles.append(
                            (
                                display_name,
                                normalize_profile_path(os.path.join(PROFILES_DIR, file_name)),
                            )
                        )

        for name, profile_path in profiles:
            self.saved_profiles_combo.addItem(name, profile_path)

        self.saved_profiles_combo.blockSignals(False)

    def refresh_active_windows(self):
        """
        Сканирование активных окон Windows.

        Исправление: теперь в combo хранится не только title, но и hwnd через addItem(title, hwnd).
        Это нужно, чтобы оверлей привязывался к конкретному окну, а не искал его потом только по названию.
        """
        self.active_windows_combo.clear()
        self.active_windows_combo.addItem(tr(self.interface_lang, "active_window_placeholder"), 0)

        try:
            import win32gui
            import pywintypes
            import win32process
            import psutil
        except Exception as e:
            self.lbl_status.setStyleSheet(status_style("error"))
            self.lbl_status.setText(tr(self.interface_lang, "error_win32_modules", error=e))
            return

        ignore_processes = {
            "explorer.exe", "chrome.exe", "firefox.exe", "msedge.exe",
            "discord.exe", "pycharm64.exe", "python.exe", "pythonw.exe"
        }
        gate_test_scene_title_markers = ("Gate Test Scene", "OCR Visual Gate Test")

        def enum_windows_proc(hwnd, extra):
            visible = bool(win32gui.IsWindowVisible(hwnd))
            if not visible:
                return True

            title = win32gui.GetWindowText(hwnd)
            if not title or hwnd == int(self.winId()) or title in ["Program Manager", "Start"]:
                return True

            is_gate_test_scene = any(marker in title for marker in gate_test_scene_title_markers)
            class_name = ""
            minimized = False
            try:
                class_name = win32gui.GetClassName(hwnd)
            except Exception:
                pass
            try:
                minimized = bool(win32gui.IsIconic(hwnd))
            except Exception:
                pass

            proc_name = ""
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                proc_name = psutil.Process(pid).name().lower()
                if proc_name in ignore_processes and not is_gate_test_scene:
                    return True
            except Exception:
                pass

            if is_gate_test_scene:
                log_launcher_timing(
                    "GATE_TEST_SCENE_WINDOW_ENUMERATED",
                    hwnd=int(hwnd),
                    title=title,
                    process=proc_name or "<unknown>",
                    class_name=class_name or "<unknown>",
                    visible=int(visible),
                    minimized=int(minimized),
                )

            self.active_windows_combo.addItem(title, int(hwnd))
            return True

        try:
            win32gui.EnumWindows(enum_windows_proc, None)
        except pywintypes.error as error:
            self.active_windows_combo.clear()
            self.active_windows_combo.addItem(tr(self.interface_lang, "active_window_placeholder"), 0)
            log_launcher_timing("WINDOW_ENUMERATION_FAILED", error=str(error), retry="next_refresh")

    def restore_current_game_selection(self):
        """
        Возвращает выбранный профиль/окно после закрытия оверлея и пересканирования списков.

        Раньше после возврата из оверлея launcher.py заново заполнял combo-box профилей
        и активных окон, из-за чего визуально снова появлялось "Выберите...".
        Пользователь менял настройки, но запускать было нечего, пока он повторно не выбирал профиль.
        """
        game_name = normalize_profile_display_name(self.current_game_name)
        saved_path = normalize_profile_path(
            self.current_profile_path
            or (self.launcher_settings or {}).get("last_profile_path", "")
        )

        idx = self.saved_profiles_combo.findData(saved_path) if saved_path else -1
        if idx >= 0:
            self.saved_profiles_combo.setCurrentIndex(idx)
            self.current_profile_path = saved_path
            self.current_profile_id = stable_profile_id(saved_path)
            log_launcher_timing(
                "PROFILE_RESTORED",
                profile_id=self.current_profile_id,
                profile_path=saved_path,
                normalized_game=game_name,
            )
            return

        normalized_game = game_name.casefold()
        idx = -1
        for combo_index in range(1, self.saved_profiles_combo.count()):
            if normalize_profile_display_name(self.saved_profiles_combo.itemText(combo_index)).casefold() == normalized_game:
                idx = combo_index
                break
        if idx >= 0:
            self.saved_profiles_combo.setCurrentIndex(idx)
            return

        idx = -1
        for combo_index in range(1, self.active_windows_combo.count()):
            if normalize_profile_display_name(self.active_windows_combo.itemText(combo_index)).casefold() == normalized_game:
                idx = combo_index
                break
        if idx >= 0:
            self.active_windows_combo.setCurrentIndex(idx)
            return
        if saved_path:
            log_launcher_timing(
                "PROFILE_RESTORE_FAILED",
                reason="profile_not_found",
                profile_id=stable_profile_id(saved_path),
                profile_path=saved_path,
                normalized_game=game_name,
            )

    def on_profile_selected(self, text):
        """Загрузка настроек из сохранённого профиля в UI лаунчера."""
        combo_path = normalize_profile_path(self.saved_profiles_combo.currentData())
        if not combo_path:
            self.current_profile_path = ""
            self.current_profile_id = ""
            if int(self.active_windows_combo.currentData() or 0) == 0:
                self.current_game_name = ""
            return
        if self._profile_load_in_progress:
            return
        profile_path = combo_path
        if not profile_path_is_app_local(profile_path) or not os.path.isfile(profile_path):
            self.current_profile_path = ""
            self.current_profile_id = ""
            return

        self._profile_load_in_progress = True
        subtitle_ocr_normalized = False
        translator_normalized = False
        load_error = None
        try:
            with open(profile_path, "r", encoding="utf-8") as f:
                prof = json.load(f)

            prof, migration = ensure_language_blocks(prof)
            initial_mode = self.installed_language_mode(
                prof.get("language_mode")
                or (prof.get("translation", {}) or {}).get("language_mode")
                or infer_language_mode_from_translation(prof.get("translation", {}))
            )
            prof = apply_block_state(prof, block_id_for_language_mode(initial_mode))
            self._loaded_profile_data = prof
            log_launcher_timing(
                "block2_profile_loaded",
                profile_id=stable_profile_id(profile_path),
                legacy_profile=migration.get("legacy_profile"),
                block2_created_or_recovered=migration.get("block2_created_or_recovered"),
            )

            self.current_profile_path = profile_path
            self.current_profile_id = stable_profile_id(profile_path)
            self.current_game_name = normalize_profile_display_name(
                prof.get("game_name") or text
            )

            self.source_lang_combo.blockSignals(True)
            self.lang_combo.blockSignals(True)
            self.mode_combo.blockSignals(True)
            if hasattr(self, "block2_translation_profile_combo"):
                self.block2_translation_profile_combo.blockSignals(True)
                self.block2_chinese_script_combo.blockSignals(True)

            translation_block = prof.get("translation", {}) if isinstance(prof.get("translation", {}), dict) else {}
            self.language_mode = normalize_language_mode(prof.get("language_mode") or translation_block.get("language_mode") or infer_language_mode_from_translation(translation_block))
            self.populate_language_combos()

            source_index = self.source_lang_combo.findData(translation_block.get("source_lang", language_mode_config(self.language_mode)["default_source"]))
            target_index = self.lang_combo.findData(translation_block.get("target_lang", language_mode_config(self.language_mode)["default_target"]))
            if source_index >= 0:
                self.source_lang_combo.setCurrentIndex(source_index)
            if target_index >= 0:
                self.lang_combo.setCurrentIndex(target_index)

            self.mode_combo.setCurrentText(translation_block.get("mode", "offline"))
            self.online_settings = normalize_online_settings(translation_block.get("online"))
            loaded_translator_preference = normalize_local_translator_preference(
                translation_block.get("backend_preference", LOCAL_TRANSLATOR_AUTO)
            )
            self.local_translator_preference = loaded_translator_preference
            self.local_translator_modified = False
            self.populate_local_translator_combo(self.local_translator_preference)
            translator_normalized = (
                migration.get("block1_backend_migrated", False)
                or migration.get("postedit_migrated", False)
                or loaded_translator_preference != self.current_local_translator_preference()
            )
            self.block2_translation_profile = str(translation_block.get("translation_profile") or "fast")
            self.block2_chinese_script_mode = str(translation_block.get("chinese_script_mode") or "auto")
            self.populate_block2_controls(
                self.block2_translation_profile,
                self.block2_chinese_script_mode,
            )
            self.text_settings = normalize_text_settings(prof.get("visuals", {}))
            self.text_settings_modified = False
            self.dynamic_settings = normalize_dynamic_settings(prof.get("dynamic", {}))
            self.dynamic_settings_modified = False
            self.subtitle_settings = normalize_profile_subtitle_settings(
                prof.get("subtitles", {}),
                profile_id=self.current_profile_id,
            )
            self.subtitle_settings_modified = False
            self.populate_subtitle_background_combo(preserve_current_ui=False)
            self.populate_subtitle_display_policy_combo(preserve_current_ui=False)
            self.llm_settings = normalize_llm_settings(prof.get("llm", {}))
            self.llm_settings_modified = False
            self.ocr_settings = normalize_ocr_settings(prof.get("ocr", {}))
            self.ocr_settings_modified = False
            loaded_ocr_engine = self.ocr_settings.get("engine")
            self.populate_ocr_engine_combo(preserve_current_ui=False)
            normalized_ocr_engine = self.current_ocr_settings_from_ui().get("engine")
            subtitle_ocr_normalized = loaded_ocr_engine != normalized_ocr_engine
            self.active_windows_combo.setCurrentIndex(0)
            self.set_hotkeys_from_profile(prof)

            self.source_lang_combo.blockSignals(False)
            self.lang_combo.blockSignals(False)
            self.mode_combo.blockSignals(False)
            if hasattr(self, "block2_translation_profile_combo"):
                self.block2_translation_profile_combo.blockSignals(False)
                self.block2_chinese_script_combo.blockSignals(False)
            self.apply_language_mode_buttons_state()
            self.update_language_mode_control_visibility()
            self.lbl_status.setStyleSheet(status_style("success"))
            self.lbl_status.setText(tr(self.interface_lang, "status_profile_loaded"))
        except Exception as e:
            load_error = e
            self.lbl_status.setStyleSheet(status_style("error"))
            self.lbl_status.setText(tr(self.interface_lang, "error_profile_load", error=e))
        finally:
            for combo_name in (
                "source_lang_combo",
                "lang_combo",
                "mode_combo",
                "local_translator_combo",
                "llm_mode_combo",
                "block2_translation_profile_combo",
                "block2_chinese_script_combo",
                "ocr_engine_combo",
                "subtitle_background_combo",
                "subtitle_display_policy_combo",
            ):
                combo = getattr(self, combo_name, None)
                if combo is not None:
                    combo.blockSignals(False)
            self._profile_load_in_progress = False

        if load_error is not None:
            self.current_profile_path = ""
            self.current_profile_id = ""
            self.saved_profiles_combo.blockSignals(True)
            self.saved_profiles_combo.setCurrentIndex(0)
            self.saved_profiles_combo.blockSignals(False)
            return

        if subtitle_ocr_normalized:
            self.ocr_settings = self.current_ocr_settings_from_ui()
            self.ocr_settings_modified = True
        if translator_normalized:
            self.local_translator_preference = self.current_local_translator_preference()
            self.local_translator_modified = True
        self.save_launcher_settings()
        if subtitle_ocr_normalized or translator_normalized:
            self.persist_current_profile_state("profile_selection_compatibility_normalized")

    def find_active_hwnd_by_title(self, title):
        """
        Резервный поиск hwnd по названию.

        Нужен для случая, когда пользователь выбрал сохранённый профиль,
        а не активное окно из верхнего списка. Если окно с таким названием открыто,
        мы всё равно найдём hwnd и передадим его оверлею.
        """
        if not title or "--" in title:
            return 0

        try:
            import win32gui
            import pywintypes
        except Exception:
            return 0

        found_hwnd = 0
        normalized_title = normalize_profile_display_name(title).casefold()

        def enum_windows_proc(hwnd, extra):
            nonlocal found_hwnd
            try:
                window_title = normalize_profile_display_name(win32gui.GetWindowText(hwnd)).casefold()
                if win32gui.IsWindowVisible(hwnd) and window_title == normalized_title:
                    found_hwnd = int(hwnd)
            except Exception:
                pass
            return True

        try:
            win32gui.EnumWindows(enum_windows_proc, None)
        except pywintypes.error as e:
            # EnumWindows иногда выбрасывает pywintypes.error при обрыве/сбое перечисления окон.
            # Для запуска оверлея это не критично: hwnd можно будет уточнить уже в main.py.
            try:
                log_launcher_timing("FIND_HWND_ENUMWINDOWS_ERROR", error=str(e))
            except Exception:
                pass
            return 0
        except Exception as e:
            try:
                log_launcher_timing("FIND_HWND_ERROR", error=str(e))
            except Exception:
                pass
            return 0

        return found_hwnd

    def fail_session_config(self, reason, **details):
        reason = str(reason or "unknown")
        self._last_session_config_error = reason
        profile_path = normalize_profile_path(
            self.saved_profiles_combo.currentData()
            or self.current_profile_path
        )
        normalized_game = normalize_profile_display_name(
            self.current_game_name
            or self.saved_profiles_combo.currentText()
            or self.active_windows_combo.currentText()
        )
        fingerprint_source = "|".join(
            (
                stable_profile_id(profile_path),
                normalized_game.casefold(),
                str(self.current_source_lang_from_ui()),
                str(self.current_target_lang_from_ui()),
            )
        )
        payload = {
            "reason": reason,
            "profile_id": stable_profile_id(profile_path),
            "profile_path": profile_path,
            "combo_current_data": self.saved_profiles_combo.currentData(),
            "combo_display_text": normalize_profile_display_name(self.saved_profiles_combo.currentText()),
            "profile_found": bool(profile_path and os.path.isfile(profile_path)),
            "normalized_game": normalized_game,
            "session_fingerprint": hashlib.sha256(
                fingerprint_source.encode("utf-8", errors="replace")
            ).hexdigest()[:20],
        }
        payload.update(details)
        log_launcher_timing("SESSION_CONFIG_FAILED", **payload)
        if self.overlay_loading_active or self.overlay_ready_marker_path or self.overlay_launch_started_at:
            self.restore_launcher_idle_after_start_failure(reason)
        return False

    def generate_session_json(self):
        """
        Создание session_config.json для оверлея.

        session_config — временный файл текущей сессии.
        Профиль игры — постоянный файл в profiles/.
        """
        self._last_session_config_error = ""
        saved_text = normalize_profile_display_name(self.saved_profiles_combo.currentText())
        saved_profile_path = normalize_profile_path(self.saved_profiles_combo.currentData())
        active_text = normalize_profile_display_name(self.active_windows_combo.currentText())
        active_hwnd = int(self.active_windows_combo.currentData() or 0)
        game_hwnd = 0

        if active_hwnd and active_text and "--" not in active_text:
            self.current_game_name = active_text
            game_hwnd = active_hwnd
            profile_path = normalize_profile_path(
                self.profile_path_for_game(self.current_game_name, create_dir=True)
            )
        elif saved_profile_path and saved_text and "--" not in saved_text:
            if not profile_path_is_app_local(saved_profile_path):
                return self.fail_session_config("profile_path_invalid")
            if not os.path.isfile(saved_profile_path):
                return self.fail_session_config("profile_not_found")
            self.current_game_name = saved_text
            game_hwnd = self.find_active_hwnd_by_title(saved_text)
            profile_path = saved_profile_path
        else:
            self.lbl_status.setStyleSheet(status_style("error"))
            self.lbl_status.setText(tr(self.interface_lang, "error_select_game"))
            return self.fail_session_config("profile_not_selected")

        if not game_hwnd:
            return self.fail_session_config(
                "target_window_not_found",
                profile_path=profile_path,
            )

        safe_name = self.safe_profile_name(self.current_game_name)
        profile_dir = os.path.dirname(profile_path)
        if not profile_path_is_app_local(profile_path):
            return self.fail_session_config("profile_path_invalid", profile_path=profile_path)
        os.makedirs(profile_dir, exist_ok=True)
        legacy_profile_path = self.legacy_profile_path_for_game(self.current_game_name)
        read_profile_path = profile_path if os.path.exists(profile_path) else legacy_profile_path

        # Подгружаем дефолтную зону из config.py.
        # Если профиль уже есть — накладываем сохранённые настройки поверх дефолта.
        zone = config.DEFAULT_ZONE.copy()
        loaded_profile = {}

        if os.path.exists(read_profile_path):
            try:
                with open(read_profile_path, "r", encoding="utf-8") as f:
                    loaded_profile = json.load(f)
                loaded_profile, _migration = ensure_language_blocks(loaded_profile)
                current_block_id = block_id_for_language_mode(self.language_mode)
                loaded_profile = apply_block_state(loaded_profile, current_block_id)
                self._loaded_profile_data = loaded_profile
                zone.update(loaded_profile.get("monitor_zone", {}))
            except Exception:
                pass

        # Если пользователь не менял хоткеи вручную в текущем окне лаунчера,
        # а в существующем профиле уже есть hotkeys — берём профильные.
        # Если менял — уважаем текущий выбор пользователя.
        if (not self.hotkeys_modified) and loaded_profile.get("hotkeys"):
            hotkeys_for_session = normalize_hotkeys(loaded_profile.get("hotkeys", {}))
        else:
            hotkeys_for_session = normalize_hotkeys(self.hotkeys)

        # Визуальные настройки текста.
        # Если профиль уже существовал и пользователь не открывал окно настроек текста,
        # берём профильные значения. Если пользователь менял настройки в текущем лаунчере —
        # уважаем текущий выбор.
        if (not self.text_settings_modified) and loaded_profile.get("visuals"):
            visuals_for_session = normalize_text_settings(loaded_profile.get("visuals", {}))
        else:
            visuals_for_session = normalize_text_settings(self.text_settings)

        # Настройки динамического перевода.
        if (not self.dynamic_settings_modified) and loaded_profile.get("dynamic"):
            dynamic_for_session = normalize_dynamic_settings(loaded_profile.get("dynamic", {}))
        else:
            dynamic_for_session = normalize_dynamic_settings(self.dynamic_settings)

        ui_subtitles_for_session = self.current_subtitle_settings_from_ui()
        subtitles_for_session = dict(ui_subtitles_for_session)
        self.subtitle_settings = subtitles_for_session
        previous_ocr = self.current_ocr_settings_from_ui().get("engine")
        self.populate_ocr_engine_combo()
        self.ocr_settings = self.current_ocr_settings_from_ui()
        if previous_ocr != self.ocr_settings.get("engine"):
            self.ocr_settings_modified = True

        # Настройки LLM-редактора перевода.
        # По умолчанию выключен. Если пользователь выбрал strict/literary,
        # main.py применит безопасный fallback, пока реальный backend не настроен.
        if (not self.llm_settings_modified) and loaded_profile.get("llm"):
            llm_for_session = normalize_llm_settings(loaded_profile.get("llm", {}))
        else:
            llm_for_session = self.current_llm_settings_from_ui()
        self.llm_settings = llm_for_session

        # Настройки OCR-движка. Берём текущий выбор из UI, потому что это
        # осознанная настройка пользователя для конкретной игры/профиля.
        raw_ocr_backend = str(self.ocr_engine_combo.currentData() or "").strip().lower()
        if raw_ocr_backend not in {"tesseract", "paddle_fast", "paddle_block2_fast", "paddle"}:
            return self.fail_session_config(
                "ocr_backend_invalid",
                ocr_backend=raw_ocr_backend,
            )
        if (
            subtitles_for_session.get("background_mode") == GAME_BACKGROUND
            and raw_ocr_backend not in {"paddle_fast", "paddle_block2_fast", "paddle"}
        ):
            return self.fail_session_config(
                "game_background_requires_paddle",
                ocr_backend=raw_ocr_backend,
            )
        ocr_for_session = self.current_ocr_settings_from_ui()
        if normalize_language_mode(self.language_mode) == LANGUAGE_MODE_ASIAN_TO_EURO:
            try:
                ocr_for_session["advanced_override"] = bool(
                    self._loaded_profile_data[BLOCKS_KEY][BLOCK2]["ocr"].get("advanced_override", False)
                )
            except Exception:
                ocr_for_session["advanced_override"] = False
        self.ocr_settings = ocr_for_session
        self.save_launcher_settings()

        # Step12: настройки visual-gate пока не выводим в UI, но уже храним
        # в профиле игры. Позже это станет базой для расширенных настроек.
        default_visual_gate = {
            "use_inner_roi": True,
            "margin_x": 0.06,
            "margin_top": 0.08,
            "margin_bottom": 0.12,
            "ignore_bottom_right": True,
            "ignore_right_width": 0.22,
            "ignore_bottom_height": 0.28,
            "game_field": dict(DEFAULT_GAME_FIELD_CONFIG),
        }
        visual_gate_for_session = default_visual_gate.copy()
        loaded_visual_gate = loaded_profile.get("visual_gate", {})
        if isinstance(loaded_visual_gate, dict):
            visual_gate_for_session.update(loaded_visual_gate)
            visual_gate_for_session["game_field"] = normalize_game_field_config(
                loaded_visual_gate.get("game_field", {})
            )

        current_language_mode = normalize_language_mode(self.language_mode)
        if current_language_mode in {LANGUAGE_MODE_ASIAN_TO_EURO, LANGUAGE_MODE_EURO_TO_ASIAN}:
            llm_for_session = normalize_llm_settings({"mode": "off"})
        source_lang_for_session = self.current_source_lang_from_ui()
        target_lang_for_session = self.current_target_lang_from_ui()
        if (
            not source_lang_for_session
            or not target_lang_for_session
            or source_lang_for_session == target_lang_for_session
        ):
            return self.fail_session_config(
                "language_pair_invalid",
                source_lang=source_lang_for_session,
                target_lang=target_lang_for_session,
            )
        local_translator_preference = self.current_local_translator_preference()
        is_block2 = current_language_mode == LANGUAGE_MODE_ASIAN_TO_EURO
        is_block3 = current_language_mode == LANGUAGE_MODE_EURO_TO_ASIAN
        is_block_router = is_block2 or is_block3
        use_online = self.mode_combo.currentText() == "online"
        if use_online:
            try:
                validate_online_configuration(getattr(self, "online_settings", None),
                                              source_lang_for_session, target_lang_for_session)
            except OnlineError as exc:
                self.lbl_status.setText(tr(self.interface_lang, "online_error_" + exc.code))
                return self.fail_session_config("online_configuration_invalid", reason=exc.code)
        block2_translation_profile = self.current_block2_translation_profile()
        block2_chinese_script_mode = self.current_block2_chinese_script_mode()
        translation_backend_for_session = (
            "block2_router"
            if is_block2
            else "block3_router"
            if is_block3
            else resolve_block1_translation_backend(
                local_translator_preference,
                source_lang_for_session,
                target_lang_for_session,
            )
        )
        if not translation_backend_for_session and not use_online:
            if local_translator_preference == LOCAL_TRANSLATOR_CURRENT_BASE:
                message = tr(self.interface_lang, "local_translator_current_base_uk_warning")
            elif local_translator_preference == LOCAL_TRANSLATOR_TRANSLATEGEMMA_LLAMA_Q6:
                message = tr(self.interface_lang, "local_translator_translategemma_llama_q6_pair_warning")
            elif local_translator_preference == LOCAL_TRANSLATOR_TRANSLATEGEMMA_LLAMA_Q6_SPEED:
                message = tr(self.interface_lang, "local_translator_translategemma_llama_q6_speed_pair_warning")
            else:
                message = tr(self.interface_lang, "local_translator_translategemma_pair_warning")
            self.lbl_status.setStyleSheet(status_style("error"))
            self.lbl_status.setText(message)
            QMessageBox.warning(
                self,
                tr(self.interface_lang, "local_translator_unavailable_title"),
                message,
            )
            return self.fail_session_config(
                "translation_backend_invalid",
                backend_preference=local_translator_preference,
                source_lang=source_lang_for_session,
                target_lang=target_lang_for_session,
            )
        quality_requested = not use_online and (
            (is_block_router and block2_translation_profile == "quality")
            or translation_backend_for_session in {
                LOCAL_TRANSLATOR_TRANSLATEGEMMA_LLAMA_Q6,
                LOCAL_TRANSLATOR_TRANSLATEGEMMA_LLAMA_Q6_SPEED,
            }
        )
        if quality_requested and not self.quality_runtime_selection["available"]:
            reason = self.quality_runtime_selection["reason"]
            message = quality_unavailable_message(self.quality_runtime_selection)
            self.lbl_status.setStyleSheet(status_style("error"))
            self.lbl_status.setText(message)
            QMessageBox.warning(self, tr(self.interface_lang, "local_translator_unavailable_title"), message)
            return self.fail_session_config("quality_runtime_unavailable", reason=reason)
        quality_runtime_id = self.quality_runtime_selection.get("runtime_id") if quality_requested else None
        if is_block2 and not use_online:
            if block2_translation_profile == "fast":
                sources = [source_lang_for_session]
                if source_lang_for_session == "zh" and block2_chinese_script_mode == "auto":
                    sources = ["zh", "zh_hant"]
                preflights = [preflight_block2_bergamot(source) for source in sources]
                if target_lang_for_session in {"ru", "uk"}:
                    preflights.append(preflight_bergamot_target(target_lang_for_session))
                missing = [item for result in preflights for item in result.get("missing", [])]
                c_findings = [item for result in preflights for item in result.get("c_path_findings", [])]
                block2_preflight_ok = not missing and not c_findings
            else:
                preflight = preflight_milmmt(quality_runtime_id)
                missing = list(preflight.get("missing") or [])
                c_findings = list(preflight.get("c_path_findings") or [])
                if target_lang_for_session == "uk":
                    block1_preflight = preflight_bergamot_target("uk")
                    missing.extend(block1_preflight.get("missing", []))
                    c_findings.extend(block1_preflight.get("c_path_findings", []))
                block2_preflight_ok = bool(preflight.get("ok")) and not missing and not c_findings
            if not block2_preflight_ok:
                detail = str((missing or c_findings or ["unknown"])[0])
                if block2_translation_profile == "quality":
                    self.quality_runtime_selection.update(
                        readiness_result="failed", readiness_error=detail,
                        reason="runtime_or_model_readiness_failure",
                    )
                    log_quality_runtime_selection(self.quality_runtime_selection, "launcher_preflight_failed")
                message = tr(self.interface_lang, "block2_backend_missing", detail=detail)
                self.lbl_status.setStyleSheet(status_style("error"))
                self.lbl_status.setText(message)
                QMessageBox.warning(self, tr(self.interface_lang, "local_translator_unavailable_title"), message)
                return self.fail_session_config(
                    "block2_preflight_failed",
                    translation_profile=block2_translation_profile,
                    detail=detail,
                )
        if is_block3 and not use_online:
            preflight = preflight_block3_route(
                target_lang_for_session,
                block2_translation_profile,
                quality_runtime_id,
            )
            if not preflight.get("ok"):
                missing = list(preflight.get("missing") or [])
                c_findings = list(preflight.get("c_path_findings") or [])
                detail = str((missing or c_findings or ["unknown"])[0])
                if block2_translation_profile == "quality":
                    self.quality_runtime_selection.update(
                        readiness_result="failed", readiness_error=detail,
                        reason="runtime_or_model_readiness_failure",
                    )
                    log_quality_runtime_selection(self.quality_runtime_selection, "launcher_preflight_failed")
                message = tr(self.interface_lang, "block2_backend_missing", detail=detail)
                self.lbl_status.setStyleSheet(status_style("error"))
                self.lbl_status.setText(message)
                QMessageBox.warning(self, tr(self.interface_lang, "local_translator_unavailable_title"), message)
                return self.fail_session_config(
                    "block3_preflight_failed",
                    translation_profile=block2_translation_profile,
                    target_lang=target_lang_for_session,
                    detail=detail,
                )
        if not use_online and translation_backend_for_session in {
            LOCAL_TRANSLATOR_TRANSLATEGEMMA_LLAMA_Q6,
            LOCAL_TRANSLATOR_TRANSLATEGEMMA_LLAMA_Q6_SPEED,
        }:
            is_speed = translation_backend_for_session == LOCAL_TRANSLATOR_TRANSLATEGEMMA_LLAMA_Q6_SPEED
            preflight = preflight_translategemma_llama_q6(quality_runtime_id)
            if not preflight.get("ok"):
                missing = list(preflight.get("missing") or [])
                detail = str(missing[0] if missing else preflight.get("error_code") or "unknown")
                error_code = str(preflight.get("error_code") or "backend_unavailable")
                self.quality_runtime_selection.update(
                    readiness_result="failed", readiness_error=detail,
                    reason="runtime_or_model_readiness_failure",
                )
                log_quality_runtime_selection(self.quality_runtime_selection, "launcher_preflight_failed")
                key = {
                    "gguf_missing": "local_translator_translategemma_llama_q6_missing_gguf",
                    "llama_server_missing": "local_translator_translategemma_llama_q6_missing_server",
                    "cuda_runtime_missing": "local_translator_translategemma_llama_q6_missing_cuda",
                }.get(error_code, "local_translator_translategemma_llama_q6_missing")
                message = tr(self.interface_lang, key, detail=detail)
                log_launcher_timing(
                    "TRANSLATEGEMMA_LLAMA_Q6_SPEED_PREFLIGHT_FAILED" if is_speed else "TRANSLATEGEMMA_LLAMA_Q6_PREFLIGHT_FAILED",
                    error_code=error_code,
                    detail=detail,
                    missing_count=len(missing),
                )
                self.lbl_status.setStyleSheet(status_style("error"))
                self.lbl_status.setText(message)
                QMessageBox.warning(
                    self,
                    tr(self.interface_lang, "local_translator_unavailable_title"),
                    message,
                )
                return self.fail_session_config(
                    "translation_backend_unavailable",
                    backend=translation_backend_for_session,
                    backend_error_code=error_code,
                    detail=detail,
                )
            log_launcher_timing(
                "TRANSLATEGEMMA_LLAMA_Q6_SPEED_PREFLIGHT_READY" if is_speed else "TRANSLATEGEMMA_LLAMA_Q6_PREFLIGHT_READY"
            )
        self.local_translator_preference = local_translator_preference
        self.selected_translation_backend = translation_backend_for_session

        session_data = {
            "profile_id": stable_profile_id(profile_path),
            "language_mode": current_language_mode,
            "game_name": self.current_game_name,
            "profile_dir": profile_dir,
            "profile_path": profile_path,
            "game_hwnd": game_hwnd,
            "monitor_zone": zone,
            "hotkeys": hotkeys_for_session,
            "dynamic": dynamic_for_session,
            "subtitles": subtitles_for_session,
            "llm": llm_for_session,
            "interface": {
                "lang": self.interface_lang,
            },
            "translation": {
                "language_mode": current_language_mode,
                "source_lang": source_lang_for_session,
                "target_lang": target_lang_for_session,
                "mode": self.mode_combo.currentText(),
                "online": normalize_online_settings(getattr(self, "online_settings", None)),
                "backend_preference": local_translator_preference,
                "backend": translation_backend_for_session,
                **(
                    {
                        "translation_profile": block2_translation_profile,
                        "chinese_script_mode": block2_chinese_script_mode,
                        "advanced_overrides": dict(
                            ((loaded_profile.get("translation", {}) or {}).get("advanced_overrides") or {})
                        ),
                    }
                    if is_block2
                    else {
                        "translation_profile": block2_translation_profile,
                    }
                    if is_block3
                    else {}
                ),
            },
            "ocr": ocr_for_session,
            "visuals": visuals_for_session,
            "visual_gate": visual_gate_for_session,
            "resource_monitor": normalize_resource_monitor_settings(loaded_profile.get("resource_monitor", {})),
            "behavior": {
                "auto_clear": zone.get("auto_clear", True),
                "auto_clear_delay": zone.get("auto_clear_delay", 2),
            },
        }

        profile_payload = dict(loaded_profile)
        profile_payload.update(session_data)
        profile_payload, _migration = ensure_language_blocks(profile_payload)
        profile_payload = replace_block_state(
            profile_payload,
            BLOCK2 if is_block2 else BLOCK3 if is_block3 else BLOCK1,
            {
                "translation": session_data["translation"],
                "monitor_zone": session_data["monitor_zone"],
                "dynamic": session_data["dynamic"],
                "ocr": session_data["ocr"],
                "subtitles": session_data["subtitles"],
                "visual_gate": session_data["visual_gate"],
            },
            make_active=True,
        )
        session_data = dict(profile_payload)
        session_data["quality_runtime_selection"] = dict(self.quality_runtime_selection)
        self._loaded_profile_data = profile_payload

        try:
            atomic_write_json(SESSION_FILE, session_data)

            # Профиль создаём или обновляем сразу, чтобы первичный выбор игры был сохранён.
            # Step14: единственный рабочий файл профиля — profiles/<game>/profile.json.
            atomic_write_json(profile_path, profile_payload)
        except Exception as exc:
            return self.fail_session_config(
                "session_config_write_failed",
                profile_path=profile_path,
                error=f"{type(exc).__name__}:{exc}",
            )

        self.current_game_name = normalize_profile_display_name(self.current_game_name)
        self.current_profile_path = normalize_profile_path(profile_path)
        self.current_profile_id = stable_profile_id(profile_path)
        self.save_launcher_settings()

        # Если остался старый файл profiles/<game>_profile.json, удаляем его после
        # успешного сохранения новой структуры. Чтение legacy оставлено только как
        # миграционный fallback для старых установок.
        try:
            if os.path.abspath(legacy_profile_path) != os.path.abspath(profile_path) and os.path.exists(legacy_profile_path):
                os.remove(legacy_profile_path)
        except Exception:
            pass

        return True

    def overlay_launch_elapsed_ms(self):
        if not self.overlay_launch_started_at:
            return 0
        return int((time.perf_counter() - self.overlay_launch_started_at) * 1000)

    def current_translation_mode_label(self):
        try:
            if hasattr(self, "mode_combo"):
                return str(self.mode_combo.currentText() or "")
        except Exception:
            pass
        return ""

    def create_overlay_ready_marker_path(self):
        # Readiness IPC is not a diagnostic log and is not subject to retention.
        marker_dir = str(DATA.temp("launcher"))
        os.makedirs(marker_dir, exist_ok=True)
        stamp = int(time.time() * 1000)
        marker_name = f"overlay_ready_{stamp}_{os.getpid()}.flag"
        self.overlay_ready_marker_path = os.path.join(marker_dir, marker_name)
        log_launcher_timing(
            "LAUNCHER_OVERLAY_READY_MARKER_CREATED",
            elapsed_ms=self.overlay_launch_elapsed_ms(),
            marker=self.overlay_ready_marker_path,
            selected_backend=self.overlay_launch_backend,
            selected_mode=self.overlay_launch_mode,
        )
        return self.overlay_ready_marker_path

    def show_overlay_loading(self):
        self.overlay_launch_started_at = time.perf_counter()
        self.overlay_launch_backend = self.current_ocr_settings_from_ui().get("engine", "tesseract")
        self.overlay_launch_mode = self.current_translation_mode_label()
        self.overlay_loading_active = True
        self.create_overlay_ready_marker_path()
        if hasattr(self, "btn_launch"):
            self.btn_launch.setEnabled(False)
        if hasattr(self, "lbl_status"):
            self.lbl_status.setStyleSheet(status_style("info"))
            self.lbl_status.setText(tr(self.interface_lang, "status_overlay_starting"))
        if not self.overlay_loading_splash:
            self.overlay_loading_splash = OverlayLoadingSplash(self.interface_lang)
        self.overlay_loading_splash.interface_lang = self.interface_lang
        self.overlay_loading_splash.show_loading()
        QApplication.processEvents()
        log_launcher_timing(
            "LAUNCHER_LOADING_SHOWN",
            elapsed_ms=0,
            marker=self.overlay_ready_marker_path,
            selected_backend=self.overlay_launch_backend,
            selected_mode=self.overlay_launch_mode,
        )

    def hide_overlay_loading(self, reason="", pid=0):
        if hasattr(self, "overlay_start_timeout_timer"):
            self.overlay_start_timeout_timer.stop()
        if hasattr(self, "overlay_ready_poll_timer"):
            self.overlay_ready_poll_timer.stop()
        if self.overlay_loading_active:
            log_launcher_timing(
                "LAUNCHER_LOADING_HIDDEN",
                pid=pid,
                elapsed_ms=self.overlay_launch_elapsed_ms(),
                marker=self.overlay_ready_marker_path,
                selected_backend=self.overlay_launch_backend,
                selected_mode=self.overlay_launch_mode,
                reason=reason,
            )
        if self.overlay_loading_splash:
            self.overlay_loading_splash.hide_loading()
        self.overlay_loading_active = False

    def restore_launcher_idle_after_start_failure(self, reason="session_config_failed"):
        """Полностью возвращает launcher в idle после ошибки до старта overlay."""
        had_pending_launch = bool(
            self.overlay_loading_active
            or self.overlay_ready_marker_path
            or self.overlay_launch_started_at
        )
        if not had_pending_launch:
            return

        marker_path = str(self.overlay_ready_marker_path or "")
        elapsed_ms = self.overlay_launch_elapsed_ms()
        self.hide_overlay_loading(reason=reason)

        if marker_path:
            try:
                marker_abs = os.path.abspath(marker_path)
                marker_dir = str(DATA.temp("launcher").resolve())
                marker_name = os.path.basename(marker_abs)
                if (
                    os.path.commonpath((marker_abs, marker_dir)) == marker_dir
                    and marker_name.startswith("overlay_ready_")
                    and marker_name.endswith(".flag")
                    and os.path.isfile(marker_abs)
                ):
                    os.remove(marker_abs)
            except Exception:
                pass

        self.overlay_ready_marker_path = ""
        self.overlay_launch_started_at = 0.0
        self.overlay_launch_backend = ""
        self.overlay_launch_mode = ""
        self.overlay_output_buffer = bytearray()
        self.overlay_close_requested = False
        self.overlay_shutdown_completed = False
        self.overlay_forced_kill = False
        self.overlay_forced_kill_reason = ""
        try:
            if self.overlay_process and self.overlay_process.state() == QProcess.ProcessState.NotRunning:
                self.overlay_process = None
        except Exception:
            pass

        self.setEnabled(True)
        self.show()
        self.raise_()
        self.activateWindow()
        if hasattr(self, "btn_launch"):
            self.btn_launch.setEnabled(True)
            self.btn_launch.setText(tr(self.interface_lang, "launch_overlay"))
        if hasattr(self, "lbl_status") and self.lbl_status.text() == tr(
            self.interface_lang, "status_overlay_starting"
        ):
            self.lbl_status.setStyleSheet(status_style("success"))
            self.lbl_status.setText(tr(self.interface_lang, "status_ready"))
        log_launcher_timing(
            "LAUNCHER_IDLE_RESTORED_AFTER_START_FAILURE",
            reason=str(reason or "session_config_failed"),
            elapsed_ms=elapsed_ms,
            marker=marker_path,
        )
        self.finish_logging_session("startup_failed", reason)

    def finish_logging_session(self, status, reason, exit_code=0):
        if not getattr(self, "_logging_overlay_session", False):
            return
        session_logging.finish_session(status, reason=reason, exit_code=exit_code)
        session_logging.retain_sessions()
        self._logging_overlay_session = False
        previous = getattr(self, "_logging_launcher_session", "")
        if previous:
            os.environ[session_logging.SESSION_ENV] = previous
        else:
            os.environ.pop(session_logging.SESSION_ENV, None)
        os.environ[session_logging.POLICY_ENV] = self._logging_launcher_policy

    def on_overlay_process_started(self):
        self.overlay_last_pid = int(self.overlay_process.processId()) if self.overlay_process else 0
        pid = int(self.overlay_process.processId()) if self.overlay_process else 0
        if pid:
            temp_spawn = getattr(self, "_overlay_temp_spawn", None)
            if temp_spawn:
                DATA.finish_temp_spawn(temp_spawn, pid)
                self._overlay_temp_spawn = None
            session_logging.register_process(pid, "overlay_control")
        log_launcher_timing(
            "LAUNCHER_OVERLAY_PROCESS_STARTED",
            pid=pid,
            elapsed_ms=self.overlay_launch_elapsed_ms(),
            marker=self.overlay_ready_marker_path,
            selected_backend=self.overlay_launch_backend,
            selected_mode=self.overlay_launch_mode,
        )
        self.hide()
        log_launcher_timing("LAUNCHER_HIDDEN", pid=pid, elapsed_ms=self.overlay_launch_elapsed_ms())

    def check_overlay_ready_marker(self):
        if not self.overlay_loading_active or not self.overlay_ready_marker_path:
            return
        if not os.path.exists(self.overlay_ready_marker_path):
            return
        pid = int(self.overlay_process.processId()) if self.overlay_process else 0
        log_launcher_timing(
            "LAUNCHER_OVERLAY_READY_MARKER_DETECTED",
            pid=pid,
            elapsed_ms=self.overlay_launch_elapsed_ms(),
            marker=self.overlay_ready_marker_path,
            selected_backend=self.overlay_launch_backend,
            selected_mode=self.overlay_launch_mode,
        )
        self.hide_overlay_loading(reason="overlay_ready_marker", pid=pid)

    def on_overlay_start_timeout(self):
        if not self.overlay_loading_active:
            return
        state = ""
        try:
            state = int(self.overlay_process.state().value) if self.overlay_process else ""
        except Exception:
            state = ""
        pid = int(self.overlay_process.processId()) if self.overlay_process else 0
        marker_exists = bool(self.overlay_ready_marker_path and os.path.exists(self.overlay_ready_marker_path))
        buffered_output_bytes = len(self.overlay_output_buffer)
        try:
            if self.overlay_process:
                buffered_output_bytes += int(self.overlay_process.bytesAvailable())
        except Exception:
            pass
        log_launcher_timing(
            "LAUNCHER_OVERLAY_START_TIMEOUT",
            pid=pid,
            elapsed_ms=self.overlay_launch_elapsed_ms(),
            marker=self.overlay_ready_marker_path,
            selected_backend=self.overlay_launch_backend,
            selected_mode=self.overlay_launch_mode,
            state=state,
        )
        self.hide_overlay_loading(reason="timeout")
        try:
            if self.overlay_process and self.overlay_process.state() != QProcess.ProcessState.NotRunning:
                log_launcher_timing(
                    "LAUNCHER_OVERLAY_TIMEOUT_TERMINATION_REQUESTED",
                    pid=pid,
                    state=state,
                    method="terminate_then_kill_after_1500ms",
                    elapsed_ms=self.overlay_launch_elapsed_ms(),
                    marker=self.overlay_ready_marker_path,
                    marker_exists=marker_exists,
                    stdout_stderr_bytes=buffered_output_bytes,
                    selected_backend=self.overlay_launch_backend,
                    selected_mode=self.overlay_launch_mode,
                )
                self.overlay_process.terminate()
                QTimer.singleShot(1500, self.kill_overlay_after_timeout_if_needed)
        except Exception:
            pass
        self.show()
        self.raise_()
        self.activateWindow()
        if hasattr(self, "btn_launch"):
            self.btn_launch.setEnabled(True)
        if hasattr(self, "lbl_status"):
            self.lbl_status.setStyleSheet(status_style("error"))
            self.lbl_status.setText(tr(self.interface_lang, "error_overlay_start_timeout"))

    def kill_overlay_after_timeout_if_needed(self):
        try:
            if self.overlay_process and self.overlay_process.state() != QProcess.ProcessState.NotRunning:
                state = int(self.overlay_process.state().value)
                pid = int(self.overlay_process.processId())
                buffered_output_bytes = len(self.overlay_output_buffer) + int(self.overlay_process.bytesAvailable())
                log_launcher_timing(
                    "LAUNCHER_OVERLAY_TIMEOUT_TERMINATION_REQUESTED",
                    pid=pid,
                    state=state,
                    method="kill",
                    elapsed_ms=self.overlay_launch_elapsed_ms(),
                    marker=self.overlay_ready_marker_path,
                    marker_exists=bool(self.overlay_ready_marker_path and os.path.exists(self.overlay_ready_marker_path)),
                    stdout_stderr_bytes=buffered_output_bytes,
                    selected_backend=self.overlay_launch_backend,
                    selected_mode=self.overlay_launch_mode,
                )
                self.overlay_forced_kill = True
                self.overlay_forced_kill_reason = "startup_timeout_forced_kill"
                self.overlay_process.kill()
        except Exception:
            pass

    def on_overlay_process_error(self, error):
        pid = (int(self.overlay_process.processId()) if self.overlay_process else 0) or getattr(self, "overlay_last_pid", 0)
        try:
            error_name = error.name
        except Exception:
            error_name = str(error)
        log_launcher_timing(
            "LAUNCHER_OVERLAY_PROCESS_ERROR",
            pid=pid,
            elapsed_ms=self.overlay_launch_elapsed_ms(),
            marker=self.overlay_ready_marker_path,
            selected_backend=self.overlay_launch_backend,
            selected_mode=self.overlay_launch_mode,
            error=error_name,
            user_requested_close=self.overlay_close_requested,
            shutdown_completed=self.overlay_shutdown_completed,
            forced_kill=self.overlay_forced_kill,
        )
        self.hide_overlay_loading(reason="process_error", pid=pid)
        self.show()
        self.raise_()
        self.activateWindow()
        if hasattr(self, "btn_launch"):
            self.btn_launch.setEnabled(True)
        if hasattr(self, "lbl_status"):
            if self.overlay_close_requested:
                self.lbl_status.setStyleSheet(status_style("warning"))
                key = "error_overlay_shutdown_timeout" if self.overlay_forced_kill else "status_overlay_closing"
                self.lbl_status.setText(tr(self.interface_lang, key))
            else:
                self.lbl_status.setStyleSheet(status_style("error"))
                self.lbl_status.setText(tr(self.interface_lang, "error_overlay_launch"))
        if error_name == "FailedToStart":
            temp_spawn = getattr(self, "_overlay_temp_spawn", None)
            if temp_spawn:
                DATA.finish_temp_spawn(temp_spawn)
                self._overlay_temp_spawn = None
            self.finish_logging_session("startup_failed", "failed_to_start", -1)

    def session_logging_configuration(self):
        """Resolve the active UI selection before naming a diagnostic session."""
        mode = normalize_language_mode(self.language_mode)
        block = block_id_for_language_mode(mode)
        source, target = self.current_source_lang_from_ui(), self.current_target_lang_from_ui()
        translation = {"language_mode": mode, "source_lang": source, "target_lang": target}
        translation["mode"] = self.mode_combo.currentText()
        if translation["mode"] == "online":
            translation.update(backend="online", online=normalize_online_settings(getattr(self, "online_settings", None)))
            return {"translation": translation, "ocr": self.current_ocr_settings_from_ui()}
        if block == BLOCK1:
            translation["backend"] = resolve_block1_translation_backend(
                self.current_local_translator_preference(), source, target)
        else:
            translation["backend"] = "block2_router" if block == BLOCK2 else "block3_router"
            # This combo shows the active block's restored selection (B2 or B3).
            translation["translation_profile"] = str(self.block2_translation_profile_combo.currentData() or "fast")
        return {"translation": translation, "ocr": self.current_ocr_settings_from_ui()}

    def launch_overlay(self):
        """Запуск оверлея и скрытие лаунчера."""
        overlay_policy = launcher_log_policy({"developer_logging": self.developer_logging_checkbox.isChecked()})
        if overlay_policy == "off" and session_logging.enabled():
            # Turning diagnostics off also retires an existing launcher log sink.
            session_logging.finish_session()
            session_logging.stop_launcher_logging()
            os.environ.pop(session_logging.SESSION_ENV, None)
            os.environ[session_logging.POLICY_ENV] = "off"
        self._logging_launcher_session = os.environ.get(session_logging.SESSION_ENV, "")
        self._logging_launcher_policy = session_logging.policy()
        os.environ.pop(session_logging.SESSION_ENV, None)
        os.environ[session_logging.POLICY_ENV] = overlay_policy
        if session_logging.enabled():
            session_logging.create_session(self.session_logging_configuration())
        self._logging_overlay_session = True
        log_launcher_timing("LAUNCH_OVERLAY_CLICKED")
        self.show_overlay_loading()
        try:
            session_ok = self.generate_session_json()
        except Exception as e:
            log_launcher_timing("SESSION_CONFIG_EXCEPTION", error=str(e))
            self.restore_launcher_idle_after_start_failure("session_config_exception")
            self.lbl_status.setStyleSheet(status_style("error"))
            self.lbl_status.setText(tr(self.interface_lang, "error_overlay_launch_detail", error=e))
            return

        if not session_ok:
            if not self._last_session_config_error:
                self.fail_session_config("unknown")
            self.restore_launcher_idle_after_start_failure("session_config_failed")
            return
        log_launcher_timing("SESSION_CONFIG_READY")
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as handle:
                session_logging.initial_configuration(json.load(handle))
        except (OSError, ValueError):
            pass

        self.overlay_output_buffer = bytearray()
        self.overlay_last_pid = 0
        self.overlay_close_requested = False
        self.overlay_shutdown_completed = False
        self.overlay_forced_kill = False
        self.overlay_forced_kill_reason = ""

        self.overlay_process = QProcess(self)
        self.overlay_process.finished.connect(self.on_overlay_finished)
        self.overlay_process.started.connect(self.on_overlay_process_started)
        self.overlay_process.errorOccurred.connect(self.on_overlay_process_error)
        self.overlay_process.readyReadStandardOutput.connect(self.on_overlay_output_ready)
        self.overlay_process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.overlay_process.setWorkingDirectory(APP_DIR)

        main_path = os.path.join(APP_DIR, "main.py")
        if not os.path.exists(main_path):
            self.restore_launcher_idle_after_start_failure("main_missing")
            self.lbl_status.setStyleSheet(status_style("error"))
            self.lbl_status.setText(tr(self.interface_lang, "error_main_missing"))
            return

        env = QProcessEnvironment.systemEnvironment()
        for key, value in session_logging.child_environment().items():
            env.insert(key, value)
        env.insert("PYTHONIOENCODING", "utf-8:backslashreplace")
        for key, value in {
            "OMP_NUM_THREADS": "4",
            "OPENBLAS_NUM_THREADS": "4",
            "MKL_NUM_THREADS": "4",
            "NUMEXPR_NUM_THREADS": "4",
            "PADDLE_NUM_THREADS": "4",
        }.items():
            if not env.contains(key):
                env.insert(key, value)
        paddle_env = {key: str(path) for key, path in DATA.environment_paths("overlay").items()}
        try:
            DATA.temp("overlay").mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.fail_session_config("profile_load_failed", error=str(exc))
            self.restore_launcher_idle_after_start_failure("session_config_failed")
            return
        for key, value in paddle_env.items():
            env.insert(key, value)
        env.insert("GAME_TRANSLATOR_TRANSLATION_BACKEND", self.selected_translation_backend)
        try:
            env.insert("GAME_TRANSLATOR_TARGET_LANG", str(self.current_target_lang_from_ui() or "uk"))
        except Exception:
            env.insert("GAME_TRANSLATOR_TARGET_LANG", "uk")
        if self.overlay_ready_marker_path:
            env.insert("GAME_TRANSLATOR_OVERLAY_READY_MARKER", self.overlay_ready_marker_path)
        self.overlay_process.setProcessEnvironment(env)

        command = python_command(main_path, SESSION_FILE)
        log_launcher_timing("QPROCESS_START_CALLED", python=command[0], main=main_path)
        self._overlay_temp_spawn = DATA.begin_temp_spawn()
        self.overlay_process.start(command[0], command[1:])
        log_launcher_timing("QPROCESS_START_RETURNED", state=int(self.overlay_process.state().value))
        self.overlay_ready_poll_timer.start()
        self.overlay_start_timeout_timer.start(45000)

    def on_overlay_output_ready(self):
        """
        Чтение stdout/stderr оверлея во время работы процесса.

        Исправление v4: если пользователь нажал крестик оверлея, main.py печатает
        маркер __OVERLAY_CLOSE_REQUESTED__. Если после этого процесс оверлея
        завис из-за фонового потока, лаунчер не должен оставаться скрытым.
        """
        if not self.overlay_process:
            return

        chunk = self.overlay_process.readAllStandardOutput().data()
        if not chunk:
            return

        self.overlay_output_buffer.extend(chunk)
        session_logging.append_console(chunk)

        text = bytes(self.overlay_output_buffer).decode("utf-8", errors="backslashreplace")

        if "__OVERLAY_CLOSE_REQUESTED__" in text and not self.overlay_close_requested:
            self.overlay_close_requested = True
            self.show_launcher_after_overlay_close_request()
        if "__OVERLAY_SHUTDOWN_COMPLETED__" in text and not self.overlay_shutdown_completed:
            self.overlay_shutdown_completed = True
            if not getattr(self, "_close_after_overlay_shutdown", False):
                self.overlay_shutdown_timeout_timer.stop()
            log_launcher_timing(
                "LAUNCHER_OVERLAY_SHUTDOWN_COMPLETED_MARKER",
                pid=int(self.overlay_process.processId()) if self.overlay_process else 0,
                elapsed_ms=self.overlay_launch_elapsed_ms(),
                profile_id=self.current_profile_id,
            )

    def show_launcher_after_overlay_close_request(self):
        """
        Возвращает лаунчер сразу после закрытия окна оверлея.

        Это страховка на случай, если сам процесс оверлея ещё не завершился.
        Через небольшой интервал launcher.py проверит QProcess и принудительно
        завершит его, если он завис на фоне.
        """
        self.scan_saved_profiles()
        self.refresh_active_windows()
        self.restore_current_game_selection()
        self.show()
        self.raise_()
        self.activateWindow()
        self.lbl_status.setStyleSheet(status_style("success"))
        self.lbl_status.setText(tr(self.interface_lang, "status_overlay_closing"))

        self.overlay_shutdown_timeout_timer.start(30000)

    def force_finish_overlay_if_needed(self):
        """Bounded fallback only when a user-requested shutdown never completed."""
        if not self.overlay_process:
            return

        if self.overlay_process.state() == QProcess.ProcessState.NotRunning:
            return

        if self.overlay_close_requested and (not self.overlay_shutdown_completed or getattr(self, "_close_after_overlay_shutdown", False)):
            self.overlay_forced_kill = True
            self.overlay_forced_kill_reason = "shutdown_timeout_forced_kill"
            log_launcher_timing(
                "LAUNCHER_OVERLAY_SHUTDOWN_TIMEOUT",
                reason=self.overlay_forced_kill_reason,
                pid=int(self.overlay_process.processId()),
                profile_id=self.current_profile_id,
                profile_path=self.current_profile_path,
            )
            self.overlay_process.terminate()
            QTimer.singleShot(1500, self.kill_overlay_after_shutdown_timeout_if_needed)

    def kill_overlay_after_shutdown_timeout_if_needed(self):
        if (
            self.overlay_process
            and self.overlay_forced_kill
            and self.overlay_process.state() != QProcess.ProcessState.NotRunning
        ):
            log_launcher_timing("LAUNCHER_OVERLAY_KILL_REQUESTED", pid=int(self.overlay_process.processId()), reason=self.overlay_forced_kill_reason)
            self.overlay_process.kill()

    def on_overlay_finished(self, exit_code, exit_status):
        """
        Возврат в лаунчер после закрытия оверлея.

        Это штатная точка возврата: пользователь нажал крестик в оверлее,
        оверлей сохранил профиль и завершился, QProcess сообщил лаунчеру о завершении.
        """
        finished_pid = (int(self.overlay_process.processId()) if self.overlay_process else 0) or getattr(self, "overlay_last_pid", 0)
        self.overlay_shutdown_timeout_timer.stop()
        self.hide_overlay_loading(reason="process_finished", pid=finished_pid)
        raw_bytes = bytes(self.overlay_output_buffer)
        if self.overlay_process:
            tail = self.overlay_process.readAllStandardOutput().data()
            raw_bytes += tail
            session_logging.append_console(tail, final=True)
        raw_output = raw_bytes.decode("utf-8", errors="backslashreplace")

        # The overlay has exited: readiness IPC is no longer in use.
        marker = self.overlay_ready_marker_path
        if marker:
            try:
                marker_path = DATA.validate(marker)
                if marker_path.parent == DATA.temp("launcher"):
                    marker_path.unlink(missing_ok=True)
            except (OSError, ValueError) as exc:
                log_launcher_timing("TEMP_IPC_CLEANUP_FAILED", reason=str(exc))

        if os.path.exists(SESSION_FILE):
            try:
                os.remove(SESSION_FILE)
            except Exception:
                pass

        self.scan_saved_profiles()
        self.refresh_active_windows()
        self.restore_current_game_selection()
        self.show()
        self.raise_()
        self.activateWindow()
        if hasattr(self, "btn_launch"):
            self.btn_launch.setEnabled(True)

        # Важная диагностика v2:
        # раньше crash_log.txt мог содержать только stdout оверлея без exit_code.
        # Если процесс завершался нативным падением Qt/torch/WinAPI, Python traceback мог отсутствовать.
        # Поэтому теперь всегда записываем код завершения и статус процесса.
        crashed = (exit_status == QProcess.ExitStatus.CrashExit)

        user_requested_close = self.overlay_close_requested or ("__OVERLAY_CLOSE_REQUESTED__" in raw_output)
        shutdown_completed = self.overlay_shutdown_completed or ("__OVERLAY_SHUTDOWN_COMPLETED__" in raw_output)
        exit_classification = classify_overlay_exit(
            exit_code=exit_code,
            crashed=crashed,
            traceback_present=("Traceback" in raw_output),
            user_requested_close=user_requested_close,
            shutdown_completed=shutdown_completed,
            forced_kill=self.overlay_forced_kill,
        )
        clean_user_stop = exit_classification["clean_user_stop"]
        shutdown_timeout = exit_classification["shutdown_timeout"]
        failed = exit_classification["failed_exit"]
        log_launcher_timing(
            "LAUNCHER_OVERLAY_EXIT_CLASSIFIED",
            pid=finished_pid,
            exit_code=exit_code,
            exit_status=exit_status.name if hasattr(exit_status, "name") else exit_status,
            user_requested_close=user_requested_close,
            shutdown_completed=shutdown_completed,
            clean_user_stop=clean_user_stop,
            forced_kill=self.overlay_forced_kill,
            reason=self.overlay_forced_kill_reason or exit_classification["reason"],
            profile_id=self.current_profile_id,
        )

        if failed:
            self.lbl_status.setStyleSheet(status_style("error"))
            self.lbl_status.setText(tr(self.interface_lang, "error_overlay_launch"))

            with session_logging.open_log(log_file("crash_log.txt"), "w", encoding="utf-8") as f:
                f.write("=== OVERLAY PROCESS RESULT ===\n")
                f.write(f"exit_code: {exit_code}\n")
                f.write(f"exit_status: {exit_status.name if hasattr(exit_status, 'name') else exit_status}\n")
                f.write("\n=== OVERLAY STDOUT/STDERR ===\n")
                f.write(raw_output if raw_output else "<empty>\n")

            # Если main.py успел создать дополнительные логи, они останутся рядом:
            # - overlay_exception.log для обычных Python-исключений;
            # - overlay_native_crash.log для нативных падений.
        elif shutdown_timeout:
            self.lbl_status.setStyleSheet(status_style("warning"))
            self.lbl_status.setText(tr(self.interface_lang, "error_overlay_shutdown_timeout"))
        else:
            crash_log = log_file("crash_log.txt")
            if session_logging.raw_enabled() and os.path.exists(crash_log) and not session_logging.context():
                try:
                    os.remove(crash_log)
                except Exception:
                    pass
            self.lbl_status.setStyleSheet(status_style("success"))
            self.lbl_status.setText(tr(self.interface_lang, "status_overlay_closed"))

        self.finish_logging_session(
            "completed" if exit_classification["reason"] == "clean_exit" else "failed",
            self.overlay_forced_kill_reason or exit_classification["reason"], int(exit_code),
        )
        self.overlay_process = None
        if getattr(self, "_close_after_overlay_shutdown", False):
            self._close_after_overlay_shutdown = False
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event):
        """Закрытие лаунчера завершает оставшиеся потоки/процессы."""
        if (self.overlay_process and self.overlay_process.state() != QProcess.ProcessState.NotRunning
                and self.overlay_close_requested):
            self._close_after_overlay_shutdown = True
            log_launcher_timing("LAUNCHER_CLOSE_DEFERRED", pid=int(self.overlay_process.processId()), shutdown_completed=self.overlay_shutdown_completed)
            if not self.overlay_shutdown_timeout_timer.isActive():
                self.overlay_shutdown_timeout_timer.start(30000)
            self.hide()
            event.ignore()
            return
        if self.overlay_loading_splash:
            self.overlay_loading_splash.hide_loading()
            self.overlay_loading_splash.close()
        if self.overlay_process and self.overlay_process.state() != QProcess.ProcessState.NotRunning:
            self.overlay_forced_kill = True
            self.overlay_forced_kill_reason = "launcher_closed_with_active_overlay"
            log_launcher_timing("LAUNCHER_OVERLAY_KILL_REQUESTED", pid=int(self.overlay_process.processId()), reason=self.overlay_forced_kill_reason)
            self.overlay_process.kill()
            self.overlay_process.waitForFinished(1500)

        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    apply_launcher_style(app)
    window = LauncherWindow()
    window.show()
    code = app.exec()
    session_logging.finish_session("completed" if code == 0 else "failed", exit_code=code)
    session_logging.retain_sessions()
    sys.exit(code)
