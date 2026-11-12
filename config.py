import os
import json

# --- Дефолтные глобальные настройки интерфейса и путей ---
from resource_registry import resource_path

TESSERACT_PATH = str(resource_path("tesseract.runtime") / "tesseract.exe")
TESSDATA_PATH = str(resource_path("tesseract.tessdata"))
FONT_NAME = "Arial"
FONT_SIZE = 19
FONT_COLOR = "#FFFFFF"
OUTLINE_COLOR = "#000000"
OUTLINE_THICKNESS = 3
DRAW_BACKGROUND_PLATE = True

# --- Файлы конфигурации ---
from data_paths import data_path
PROFILES_FILE = str(data_path("settings", "profiles.json"))

# --- Текущие глобальные настройки (в памяти) ---
CURRENT_GAME_NAME = "-- Виберіть вікно гри --"
CURRENT_LANG = "uk"
TRANSLATION_MODE = "offline"
TRANSLATION_BACKEND = "bergamot"

# Базовый эталон зоны со всеми фичами (для валидации)
DEFAULT_ZONE = {
    "width": 650,
    "height": 180,
    "left": 400,
    "top": 700,
    "left_offset": 100,
    "top_offset": 500,

    # --- НАСТРОЙКИ ЯЗЫКА И OCR ---
    "source_lang": "en",  # 'en' (Англ)
    "ocr_langs": ["en"],

    # --- НАСТРОЙКИ ФИЛЬТРА МУСОРА ---
    "min_text_len": 3,
    "vowels_min_pct": 0.24,
    "vowels_max_pct": 0.65,
    "max_garbage_symbols_pct": 0.45,

    # --- АВТООЧИСТКА ---
    "auto_clear": True,
    "auto_clear_delay": 2,

    # --- ВИЗУАЛ ---
    "font_size": 19,
    "font_color": "#FFFFFF"
}

MONITOR_ZONE = DEFAULT_ZONE.copy()

# --- ЕДИНАЯ ТОЧКА СИНХРОНИЗАЦИИ ПОТОКОВ ---
SHARED_STATE = {
    "setup_mode": True,
    "translation_in_progress": False,
    "last_text": "",
    "game_hwnd": 0,
    "monitor_zone": DEFAULT_ZONE.copy(),
    "current_lang": "uk",
    "translation_mode": "offline",
    "translation_backend": "bergamot"
}

_profiles_cache = None


def init_config():
    global _profiles_cache, CURRENT_LANG, TRANSLATION_MODE, CURRENT_GAME_NAME
    if _profiles_cache is not None: return

    if not os.path.exists(PROFILES_FILE):
        _profiles_cache = {
            "global": {"last_game": "-- Виберіть вікно гри --", "lang": "uk", "mode": "offline"},
            "profiles": {}
        }
        _save_cache_to_disk()
    else:
        try:
            with open(PROFILES_FILE, 'r', encoding='utf-8') as f:
                _profiles_cache = json.load(f)
        except Exception as e:
            print(f"[ОШИБКА КФГ] Ошибка чтения JSON, сброс к дефолту: {e}")
            _profiles_cache = {
                "global": {"last_game": "-- Виберіть вікно гри --", "lang": "uk", "mode": "offline"},
                "profiles": {}
            }

    g_settings = _profiles_cache.get("global", {})
    CURRENT_LANG = g_settings.get("lang", "uk")
    TRANSLATION_MODE = g_settings.get("mode", "offline")
    CURRENT_GAME_NAME = g_settings.get("last_game", "-- Виберіть вікно гри --")

    SHARED_STATE["current_lang"] = CURRENT_LANG
    SHARED_STATE["translation_mode"] = TRANSLATION_MODE
    SHARED_STATE["translation_backend"] = TRANSLATION_BACKEND


def _save_cache_to_disk():
    global _profiles_cache
    try:
        os.makedirs(os.path.dirname(PROFILES_FILE), exist_ok=True)
        with open(PROFILES_FILE, 'w', encoding='utf-8') as f:
            json.dump(_profiles_cache, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"[ОШИБКА КФГ] Не удалось записать файл на диск: {e}")




