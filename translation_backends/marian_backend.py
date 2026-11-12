"""
Local Marian translation backend wrapper. Online dispatch belongs to Online Core.
"""

from __future__ import annotations

import threading


_LEGACY_CORE = None
_LEGACY_CORE_LOCK = threading.Lock()


def _get_legacy_core():
    """Import Transformers/Marian only when current_base is actually selected."""
    global _LEGACY_CORE
    if _LEGACY_CORE is not None:
        return _LEGACY_CORE
    with _LEGACY_CORE_LOCK:
        if _LEGACY_CORE is None:
            import translator_core as legacy_translator_core

            _LEGACY_CORE = legacy_translator_core
    return _LEGACY_CORE


class MarianBackend:
    """Adapter for the current translator_core.py implementation."""

    name = "marian"
    memory_key = "current_base"
    display_name = "Current MarianMT"

    def load_model(self) -> None:
        """Delegate model warm/loading to current translator_core."""
        _get_legacy_core().load_model()

    def translate_text(
        self,
        text: str,
        src_lang: str = "en",
        target_lang: str = "uk",
        use_online: bool = True,
    ) -> str:
        """Translate text through existing translator_core."""
        return _get_legacy_core().translate_text(
            text,
            src_lang=src_lang,
            target_lang=target_lang,
            use_online=use_online,
        )
