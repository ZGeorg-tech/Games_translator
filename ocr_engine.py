"""
OCR engine dispatcher with optional PaddleOCR compare mode.

This is still a safe backend layer:
- the live application continues to use the selected OCR backend;
- default selected backend is still Tesseract;
- PaddleOCR can be enabled only for comparison, without replacing Tesseract.

Compare mode is intentionally non-invasive:
- main.py still calls extract_text_from_pil_image(...);
- Tesseract returns immediately as before;
- PaddleOCR comparison runs in a background thread when enabled;
- results are written to ocr_compare_paddle.log.

How to enable compare mode:
1. Install PaddleOCR dependencies.
2. Run enable_paddle_compare.cmd, or create file ocr_compare_enabled.flag in the project root.
3. Run the app normally.

How to disable:
- run disable_paddle_compare.cmd, or delete ocr_compare_enabled.flag.
"""

from __future__ import annotations

import os
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

try:
    import config
except Exception:  # pragma: no cover - defensive fallback for isolated tests
    config = None

try:
    import ocr_settings
except Exception:  # settings file is optional, but shipped in this step
    ocr_settings = None

from ocr_backends.tesseract_backend import TesseractBackend
from ocr_backends.paddle_backend import PaddleBackend


_PROJECT_ROOT = Path(__file__).resolve().parent
from data_paths import data_path
import session_logging
_COMPARE_FLAG_FILE = data_path("settings", "ocr_compare_enabled.flag")
_COMPARE_LOG_FILE = data_path("logs", "ocr_compare_paddle.log")
_TIMING_LOG_FILE = data_path("logs", "ocr_timing.log")
_PADDLE_BACKEND_NAMES = {"paddle", "paddle_fast", "paddle_block2_fast"}

_BACKENDS: Dict[str, Any] = {
    "tesseract": TesseractBackend(),
    # Paddle quality keeps the current stable PP-OCRv6 medium path.
    "paddle": PaddleBackend(preset="quality"),
    # Paddle fast is a separate PP-OCRv4 mobile path. It does not replace quality.
    "paddle_fast": PaddleBackend(preset="fast"),
    # Block 2 Fast uses the existing app-local PP-OCRv6 Small detector/recognizer.
    "paddle_block2_fast": PaddleBackend(preset="block2_fast"),
}

_current_backend_name = "tesseract"


def is_paddle_family_backend(name: Any) -> bool:
    """True for Paddle OCR presets: paddle, paddle_fast, future paddle_* backends."""
    value = str(name or "").strip().lower().replace("-", "_")
    return value == "paddle" or value.startswith("paddle_")


# Background compare state. We keep only one compare task active so OCR loop cannot
# pile up heavy Paddle jobs and freeze the overlay.
_compare_lock = threading.Lock()
_compare_busy = False
_compare_last_started_at = 0.0
_compare_unavailable_logged = False
_compare_last_selected_text_key = ""
_compare_empty_logged_at = 0.0



def _get_setting(name: str, default: Any = None) -> Any:
    """Read setting from config.py first, then ocr_settings.py, then fallback."""
    if config is not None and hasattr(config, name):
        return getattr(config, name)
    if ocr_settings is not None and hasattr(ocr_settings, name):
        return getattr(ocr_settings, name)
    return default


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y", "enable", "enabled"}


def _normalize_backend_name(name: Any) -> str:
    value = str(name or "").lower().strip()
    if value in {"paddle-fast", "paddlefast"}:
        return "paddle_fast"
    if value in {"paddle-block2-fast", "paddle_block2", "paddlev6small"}:
        return "paddle_block2_fast"
    return value


def available_backends() -> list[str]:
    """Return names of OCR backends currently known to this build."""
    return sorted(_BACKENDS.keys())


def get_default_backend_name() -> str:
    """
    Resolve active OCR backend.

    For this step the default remains Tesseract. PaddleOCR can be used later as a
    selected backend, but for now the important mode is comparison.
    """
    cfg_name = _get_setting("OCR_BACKEND", None)
    env_name = os.environ.get("GAME_TRANSLATOR_OCR_BACKEND")
    name = _normalize_backend_name(env_name or cfg_name or _current_backend_name or "tesseract")
    return name if name in _BACKENDS else "tesseract"


def set_backend(name: str) -> bool:
    """Switch OCR backend for the current process."""
    global _current_backend_name
    normalized = _normalize_backend_name(name)
    if normalized not in _BACKENDS:
        return False
    _current_backend_name = normalized
    return True


def get_backend(name: str | None = None) -> Any:
    """Return backend instance by name or current/default backend."""
    backend_name = _normalize_backend_name(name or get_default_backend_name())
    return _BACKENDS.get(backend_name, _BACKENDS["tesseract"])


def _compare_enabled() -> bool:
    """Return True if Paddle compare mode is enabled."""
    if not session_logging.raw_enabled():
        return False
    env_value = os.environ.get("GAME_TRANSLATOR_OCR_COMPARE")
    if env_value is not None:
        return _truthy(env_value)
    if _COMPARE_FLAG_FILE.exists():
        return True
    return _truthy(_get_setting("ENABLE_PADDLE_COMPARE", False))


def _compare_interval_sec() -> float:
    try:
        return float(_get_setting("PADDLE_COMPARE_INTERVAL_SEC", 6.0))
    except Exception:
        return 6.0


def _write_compare_log(block: str) -> None:
    """Append one comparison block to ocr_compare_paddle.log."""
    if not session_logging.raw_enabled():
        return
    try:
        _COMPARE_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with session_logging.open_log(_COMPARE_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(block.rstrip() + "\n")
    except Exception:
        # Compare logging must never break live OCR.
        pass




def _timing_log_enabled() -> bool:
    """Return True if OCR timing log should be written."""
    return session_logging.raw_enabled() and _truthy(_get_setting("ENABLE_OCR_TIMING_LOG", True))


def _write_timing_log(block: str) -> None:
    """Append one OCR timing block. Timing log must never break OCR."""
    if not _timing_log_enabled():
        return
    try:
        _TIMING_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with session_logging.open_log(_TIMING_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(block.rstrip() + "\n")
    except Exception:
        pass

def _log_paddle_unavailable_once(error_text: str) -> None:
    global _compare_unavailable_logged
    if _compare_unavailable_logged:
        return
    _compare_unavailable_logged = True
    _write_compare_log(
        "=" * 70
        + "\n"
        + f"TIME: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        + "PADDLE_COMPARE: UNAVAILABLE\n"
        + "DETAILS: PaddleOCR backend could not run. Install paddleocr/paddlepaddle or disable compare mode.\n"
        + f"ERROR: {error_text}\n"
        + "=" * 70
    )


def _normalize_for_short_log(text: str, limit: int = 500) -> str:
    text = (text or "").replace("\r", " ").replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit] + " ...[cut]"




def _compare_text_key(text: str) -> str:
    """Compact key for deciding whether this OCR text is worth comparing again."""
    import re
    value = (text or "").lower().strip()
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"[^a-z0-9а-яіїєґё一-龥ぁ-んァ-ン가-힣 ]+", "", value)
    return value[:240]


def _schedule_paddle_compare(img: Any, lang: str, selected_backend_name: str, selected_text: str) -> None:
    """
    Run PaddleOCR comparison in the background, but very lightly.

    Step 2 proved that PaddleOCR quality is promising, but running it too often
    can starve the overlay/key timers on CPU-only Windows machines. Step 3 only
    compares meaningful NEW OCR text and skips empty frames / duplicates.
    """
    global _compare_busy, _compare_last_started_at, _compare_last_selected_text_key, _compare_empty_logged_at

    if not _compare_enabled():
        return

    selected_text_clean = (selected_text or "").strip()
    if not selected_text_clean:
        # Empty frames are not useful for OCR quality comparison and were causing
        # heavy Paddle calls while the game had no subtitle on screen.
        return

    text_key = _compare_text_key(selected_text_clean)
    if len(text_key) < 4:
        return

    now = time.time()
    # Larger default interval for live overlay safety. Can be overridden later in ocr_settings.py.
    interval = max(4.0, _compare_interval_sec())

    with _compare_lock:
        if _compare_busy:
            return
        if text_key == _compare_last_selected_text_key:
            return
        if now - _compare_last_started_at < interval:
            return
        _compare_busy = True
        _compare_last_started_at = now
        _compare_last_selected_text_key = text_key

    try:
        img_copy = img.copy() if hasattr(img, "copy") else img
    except Exception:
        img_copy = img

    def worker() -> None:
        global _compare_busy
        started = time.time()
        paddle_text = ""
        error = ""
        try:
            paddle = get_backend("paddle")
            paddle_text = paddle.extract_text_from_pil_image(img_copy, lang=lang)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            _log_paddle_unavailable_once(error)
        finally:
            elapsed_ms = int((time.time() - started) * 1000)
            if not error:
                block = (
                    "=" * 70
                    + "\n"
                    + f"TIME: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    + f"LANG: {lang}\n"
                    + f"SELECTED_BACKEND: {selected_backend_name}\n"
                    + f"PADDLE_ELAPSED_MS: {elapsed_ms}\n"
                    + "\n"
                    + "TESSERACT_OR_SELECTED:\n"
                    + _normalize_for_short_log(selected_text)
                    + "\n\n"
                    + "PADDLEOCR:\n"
                    + _normalize_for_short_log(paddle_text)
                    + "\n"
                    + "=" * 70
                )
                _write_compare_log(block)
            with _compare_lock:
                _compare_busy = False

    threading.Thread(target=worker, name="PaddleOCRCompareWorker", daemon=True).start()


def extract_text_from_pil_image(img: Any, lang: str = "eng", backend_name: str | None = None) -> str:
    """
    Extract text from PIL image through the selected OCR backend.

    Signature stays compatible with old ocr_core.extract_text_from_pil_image.
    Step 4 adds real backend selection: live OCR can be Tesseract or PaddleOCR.

    Compare mode remains diagnostic only. It is skipped when PaddleOCR is already
    the selected live backend so we do not run Paddle twice on the same frame.
    """
    backend_name_resolved = _normalize_backend_name(backend_name or get_default_backend_name())
    backend = get_backend(backend_name_resolved)

    started = time.time()
    error_text = ""
    text = ""
    try:
        text = backend.extract_text_from_pil_image(img, lang=lang)
        return text
    except Exception as e:
        error_text = f"{type(e).__name__}: {e}"
        # If PaddleOCR is selected and fails, use Tesseract as emergency fallback.
        # This prevents one broken Paddle install from making the overlay unusable.
        if backend_name_resolved != "tesseract":
            try:
                fallback = get_backend("tesseract")
                text = fallback.extract_text_from_pil_image(img, lang=lang)
                error_text += " | FALLBACK_TO_TESSERACT_USED"
                return text
            except Exception as e2:
                error_text += f" | FALLBACK_ERROR: {type(e2).__name__}: {e2}"
        raise
    finally:
        elapsed_ms = int((time.time() - started) * 1000)
        size_text = "unknown"
        try:
            size_text = f"{img.width}x{img.height}"
        except Exception:
            pass
        _write_timing_log(
            "=" * 70
            + "\n"
            + f"TIME: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            + f"OCR_BACKEND: {backend_name_resolved}\n"
            + f"LANG: {lang}\n"
            + f"IMAGE_SIZE: {size_text}\n"
            + f"ELAPSED_MS: {elapsed_ms}\n"
            + f"TEXT_LEN: {len(text or '')}\n"
            + (f"ERROR: {error_text}\n" if error_text else "")
            + "TEXT:\n"
            + _normalize_for_short_log(text)
            + "\n"
            + "=" * 70
        )

        # Compare is non-invasive: it never changes the returned text.
        # Do not run Paddle compare when a quality OCR backend is already selected.
        if not is_paddle_family_backend(backend_name_resolved) and not error_text:
            _schedule_paddle_compare(img, lang, backend_name_resolved, text)


def warm_up_backend(name: str, lang: str = "en") -> Dict[str, Any]:
    """Load and run one OCR pass for a backend without touching app lifecycle state."""
    backend_name = _normalize_backend_name(name)
    if backend_name not in _BACKENDS:
        return {
            "ok": False,
            "backend": backend_name,
            "elapsed_ms": 0,
            "text": "",
            "error": f"unknown_backend: {backend_name}",
        }

    started = time.time()
    text = ""
    try:
        from PIL import Image, ImageDraw, ImageFont

        img = Image.new("RGB", (320, 80), (8, 8, 10))
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.load_default(size=18)
        except TypeError:
            font = ImageFont.load_default()
        draw.text((24, 28), "Warmup", fill=(245, 245, 245), font=font)

        backend = get_backend(backend_name)
        text = backend.extract_text_from_pil_image(img, lang=lang)
        return {
            "ok": True,
            "backend": backend_name,
            "elapsed_ms": int((time.time() - started) * 1000),
            "text": _normalize_for_short_log(text, limit=120),
            "error": "",
        }
    except Exception as exc:
        return {
            "ok": False,
            "backend": backend_name,
            "elapsed_ms": int((time.time() - started) * 1000),
            "text": _normalize_for_short_log(text, limit=120),
            "error": f"{type(exc).__name__}: {exc}",
        }


def log_translation(eng_text: str, translated_text: str = "") -> None:
    """
    Keep old logging behavior through the Tesseract legacy backend.

    Later, when profiles become folders, this can route into profile translation
    memory instead.
    """
    get_backend("tesseract").log_translation(eng_text, translated_text)
