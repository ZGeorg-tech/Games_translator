"""
visual_text_gate.py

Лёгкий visual/text-mask gate для PaddleOCR live-режима.

Модуль не читает текст. Его задача — дешёво определить, изменился ли
рисунок букв в области субтитров. PaddleOCR всё равно получает полный
OCR-кроп, а gate может проверять только внутреннюю ROI-область и
игнорировать правый нижний угол с мигающими индикаторами "далее".
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

try:
    from PIL import ImageOps, ImageFilter
except Exception:  # pragma: no cover
    ImageOps = None
    ImageFilter = None


@dataclass
class TextMaskInfo:
    signature: str
    mask: bytes
    width: int
    height: int
    density: float
    nonzero: int
    active_total: int = 0
    roi_detail: str = ""


@dataclass
class TextMaskCompare:
    change_score: float
    union_ratio: float
    density_delta: float
    xor_count: int
    union_count: int


def _cfg_float(cfg: dict[str, Any], key: str, default: float, min_value: float, max_value: float) -> float:
    try:
        value = float(cfg.get(key, default))
    except Exception:
        value = default
    return max(min_value, min(max_value, value))


def _cfg_bool(cfg: dict[str, Any], key: str, default: bool) -> bool:
    value = cfg.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "вкл", "да"}
    return bool(value)


def _crop_gate_roi(pil_image, cfg: dict[str, Any]) -> tuple[Any, str]:
    """
    Возвращает ROI для visual-gate. Это НЕ влияет на полный кроп,
    который позже получает PaddleOCR.
    """
    if pil_image is None:
        return pil_image, "full"

    use_inner_roi = _cfg_bool(cfg, "use_inner_roi", True)
    if not use_inner_roi:
        return pil_image, "full"

    w, h = pil_image.size
    if w <= 10 or h <= 10:
        return pil_image, "full-small"

    margin_x = _cfg_float(cfg, "margin_x", 0.06, 0.0, 0.35)
    margin_top = _cfg_float(cfg, "margin_top", 0.08, 0.0, 0.40)
    margin_bottom = _cfg_float(cfg, "margin_bottom", 0.12, 0.0, 0.45)

    left = int(round(w * margin_x))
    right = int(round(w * (1.0 - margin_x)))
    top = int(round(h * margin_top))
    bottom = int(round(h * (1.0 - margin_bottom)))

    if right <= left + 8 or bottom <= top + 8:
        return pil_image, "full-roi-too-small"

    roi = pil_image.crop((left, top, right, bottom))
    return roi, f"inner_roi={left},{top},{right-left},{bottom-top}; full={w}x{h}"


def make_text_mask(
    pil_image,
    target_width: int = 160,
    edge_threshold: int = 28,
    gate_config: dict[str, Any] | None = None,
) -> TextMaskInfo:
    """
    Возвращает бинарную маску контрастных элементов, похожих на буквы.

    Важно: это НЕ OCR и НЕ точная детекция текста. Это дешёвый индикатор
    изменения текстового рисунка. В PaddleOCR всё равно передаётся исходный полный кроп.
    """
    if pil_image is None:
        return TextMaskInfo("", b"", 0, 0, 0.0, 0, 0, "none")

    cfg = gate_config or {}

    try:
        roi_image, roi_detail = _crop_gate_roi(pil_image, cfg)
        gray = roi_image.convert("L")
        w, h = gray.size
        if w <= 0 or h <= 0:
            return TextMaskInfo("", b"", 0, 0, 0.0, 0, 0, roi_detail)

        tw = max(48, int(target_width))
        th = max(12, int(round(h * (tw / float(w)))))
        gray = gray.resize((tw, th))

        if ImageOps is not None:
            gray = ImageOps.autocontrast(gray)

        edges = gray.filter(ImageFilter.FIND_EDGES) if ImageFilter is not None else gray
        binary = edges.point(lambda p: 255 if p >= edge_threshold else 0)

        if ImageFilter is not None:
            binary = binary.filter(ImageFilter.MaxFilter(3))

        raw = binary.tobytes()
        mask_arr = bytearray(1 if b else 0 for b in raw)
        active_arr = bytearray(1 for _ in raw)

        # Типовой мигающий индикатор "далее" находится в правом нижнем углу
        # текстовой плашки. Для PaddleOCR он остаётся в полном кропе, но для
        # gate-а его лучше игнорировать, чтобы не запускать OCR от декоративной кнопки.
        if _cfg_bool(cfg, "ignore_bottom_right", True) and tw > 10 and th > 10:
            right_width = _cfg_float(cfg, "ignore_right_width", 0.22, 0.0, 0.60)
            bottom_height = _cfg_float(cfg, "ignore_bottom_height", 0.28, 0.0, 0.70)
            ix0 = int(round(tw * (1.0 - right_width)))
            iy0 = int(round(th * (1.0 - bottom_height)))
            ix0 = max(0, min(tw, ix0))
            iy0 = max(0, min(th, iy0))
            for y in range(iy0, th):
                row = y * tw
                for x in range(ix0, tw):
                    idx = row + x
                    mask_arr[idx] = 0
                    active_arr[idx] = 0
            roi_detail += f"; ignore_br={ix0},{iy0},{tw-ix0},{th-iy0}"

        mask = bytes(mask_arr)
        active_total = int(sum(active_arr)) or len(mask) or 1
        nonzero = sum(mask)
        density = nonzero / float(active_total)
        signature = hashlib.blake2b(mask, digest_size=16).hexdigest()
        return TextMaskInfo(signature, mask, tw, th, density, nonzero, active_total, roi_detail)
    except Exception:
        return TextMaskInfo("", b"", 0, 0, 0.0, 0, 0, "error")


def compare_masks(previous: TextMaskInfo | None, current: TextMaskInfo | None) -> TextMaskCompare:
    if not previous or not current or not previous.mask or not current.mask:
        return TextMaskCompare(1.0, 1.0, 1.0, 0, 0)

    if len(previous.mask) != len(current.mask):
        return TextMaskCompare(1.0, 1.0, abs(current.density - previous.density), 0, 0)

    xor_count = 0
    union_count = 0
    for a, b in zip(previous.mask, current.mask):
        if a or b:
            union_count += 1
            if a != b:
                xor_count += 1

    if union_count <= 0:
        change_score = 0.0
        union_ratio = 0.0
    else:
        change_score = xor_count / float(union_count)
        active_total = int(getattr(current, "active_total", 0)) or len(current.mask) or 1
        union_ratio = union_count / float(active_total)

    return TextMaskCompare(
        change_score=change_score,
        union_ratio=union_ratio,
        density_delta=abs(current.density - previous.density),
        xor_count=xor_count,
        union_count=union_count,
    )
