"""
Tesseract OCR backend wrapper.

This file does NOT contain new OCR logic. It delegates to the existing
ocr_core.py, so the current behavior of the application stays the same.
The purpose is architectural: main.py will no longer depend directly on a
specific OCR engine. Later we can add paddle_backend.py next to this file.
"""

from __future__ import annotations

from typing import Any

import ocr_core as legacy_ocr_core


class TesseractBackend:
    """Adapter for the current Tesseract-based OCR implementation."""

    name = "tesseract"
    display_name = "Tesseract OCR"

    def extract_text_from_pil_image(self, img: Any, lang: str = "eng") -> str:
        """
        Extract text from a PIL image using the existing ocr_core pipeline.

        Parameters are kept compatible with ocr_core.extract_text_from_pil_image
        so old code and new backend code can coexist safely.
        """
        return legacy_ocr_core.extract_text_from_pil_image(img, lang=lang)

    def log_translation(self, eng_text: str, translated_text: str = "") -> None:
        """Delegate translation logging to the existing ocr_core logger."""
        legacy_ocr_core.log_translation(eng_text, translated_text)
