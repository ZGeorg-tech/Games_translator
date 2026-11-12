"""Unicode-safe console streams for Windows runtime diagnostics."""

from __future__ import annotations

import sys
from typing import Any, TextIO


class UnicodeSafeTextStream:
    """Proxy a text stream without allowing diagnostic output to break runtime."""

    _game_translator_unicode_safe = True

    def __init__(self, stream: TextIO):
        self._stream = stream

    def write(self, value: Any) -> int:
        text = str(value)
        try:
            written = self._stream.write(text)
            return len(text) if written is None else int(written)
        except UnicodeError:
            try:
                encoding = getattr(self._stream, "encoding", None) or "ascii"
                escaped = text.encode(encoding, errors="backslashreplace").decode(encoding)
                written = self._stream.write(escaped)
                return len(text) if written is None else int(written)
            except Exception:
                return len(text)
        except Exception:
            return len(text)

    def flush(self) -> None:
        try:
            self._stream.flush()
        except Exception:
            pass

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


def configure_unicode_safe_console() -> None:
    """Prefer UTF-8 and install a fail-safe proxy for stdout and stderr."""

    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or getattr(stream, "_game_translator_unicode_safe", False):
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except Exception:
                pass
        setattr(sys, stream_name, UnicodeSafeTextStream(stream))
