"""
ocr_benchmark_crops.py

Вспомогательный модуль для сохранения принятых OCR-crop'ов.

Важно:
- сохраняем не каждый кадр, а только crop, который реально прошёл фильтры OCR
  и был принят/переведён;
- это нужно для отдельных benchmark-этапов;
- в обычном live-тестировании теперь выключено по умолчанию, чтобы не засорять
  профиль PNG-файлами и metadata.jsonl.

Включение позже:
behavior["save_ocr_benchmark_crops"] = True
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any
import session_logging

_seen_by_dir: dict[str, set[str]] = {}




def _resolve_game_dir(app_dir: str, cfg: dict[str, Any]) -> str:
    from data_paths import profile_data_dir
    return str(profile_data_dir(app_dir, cfg or {}, "diagnostics"))


def _image_hash(pil_image) -> str:
    try:
        img = pil_image.convert("RGB")
        raw = img.tobytes()
        size = f"{img.size[0]}x{img.size[1]}".encode("ascii", errors="ignore")
        return hashlib.blake2b(size + b"\0" + raw, digest_size=10).hexdigest()
    except Exception:
        return hashlib.blake2b(str(time.time()).encode("utf-8"), digest_size=10).hexdigest()


def _text_hash(text: str) -> str:
    return hashlib.blake2b(str(text or "").encode("utf-8", errors="ignore"), digest_size=8).hexdigest()


def _count_pngs(path: str) -> int:
    try:
        return sum(1 for name in os.listdir(path) if name.lower().endswith(".png"))
    except Exception:
        return 0


def save_crop(
    app_dir: str,
    session_config: dict[str, Any] | None,
    pil_image,
    *,
    mode: str,
    ocr_engine: str,
    source_lang: str,
    target_lang: str,
    original_text: str,
    translated_text: str = "",
    ocr_ms: int | None = None,
    total_ms: int | None = None,
) -> str:
    """
    Сохраняет принятый OCR-crop как PNG + строку metadata.jsonl.

    Управляется флагом:
        session_config["behavior"]["save_ocr_benchmark_crops"]

    По умолчанию False:
    - для обычной проверки приложения crop'ы больше не пишутся в профиль;
    - если снова понадобится benchmark-набор, флаг можно включить в профиле/настройках.
    """
    try:
        cfg = session_config or {}
        behavior = cfg.get("behavior", {}) if isinstance(cfg.get("behavior", {}), dict) else {}
        enabled = bool(behavior.get("save_ocr_benchmark_crops", False))
        if not enabled or not session_logging.raw_enabled():
            return ""

        original_text = str(original_text or "").strip()
        if not original_text:
            return ""

        game_dir = _resolve_game_dir(app_dir, cfg)
        out_dir = os.path.join(game_dir, "ocr_benchmark_crops")
        os.makedirs(out_dir, exist_ok=True)

        max_files = int(behavior.get("ocr_benchmark_max_crops", 160) or 160)
        if max_files > 0 and _count_pngs(out_dir) >= max_files:
            return ""

        img_hash = _image_hash(pil_image)
        seen = _seen_by_dir.setdefault(out_dir, set())
        if img_hash in seen:
            return ""

        # Avoid duplicating already saved crop images after app restart.
        existing = [name for name in os.listdir(out_dir) if img_hash in name and name.lower().endswith(".png")]
        if existing:
            seen.add(img_hash)
            return ""

        stamp = time.strftime("%Y%m%d_%H%M%S")
        text_id = _text_hash(original_text)
        safe_mode = re.sub(r"[^a-zA-Z0-9_-]+", "_", mode or "ocr")[:32] or "ocr"
        filename = f"{stamp}_{safe_mode}_{ocr_engine or 'ocr'}_{img_hash}_{text_id}.png"
        path = os.path.join(out_dir, filename)
        pil_image.save(path)
        seen.add(img_hash)

        meta_path = os.path.join(out_dir, "metadata.jsonl")
        row = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "file": filename,
            "mode": mode,
            "ocr_engine": ocr_engine,
            "source_lang": source_lang,
            "target_lang": target_lang,
            "image_size": f"{getattr(pil_image, 'width', 0)}x{getattr(pil_image, 'height', 0)}",
            "original_text": original_text,
            "translated_text": str(translated_text or "").strip(),
            "ocr_ms": ocr_ms,
            "total_ms": total_ms,
            "image_hash": img_hash,
            "text_hash": text_id,
        }
        with open(meta_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return path
    except Exception:
        return ""
