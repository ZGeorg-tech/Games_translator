"""
OCR settings for backend selection and diagnostics.

Normal user-facing selection comes from launcher profile/session:
    session_config["ocr"]["engine"] = "tesseract", "paddle_fast", or "paddle"

This file only provides safe defaults and diagnostic switches.
"""

# Live backend used if launcher/session does not specify one.
OCR_BACKEND = "tesseract"

# Compare mode runs PaddleOCR in the background and writes ocr_compare_paddle.log.
# Keep OFF by default. Use only for diagnostics, because running two OCR engines
# at once can cause visible delays on CPU-only systems.
ENABLE_PADDLE_COMPARE = False

# Minimum delay between Paddle compare jobs.
PADDLE_COMPARE_INTERVAL_SEC = 6.0

# Timing log for the currently selected OCR backend.
# Written to ocr_timing.log.
ENABLE_OCR_TIMING_LOG = True
