"""App-local OpenCC s2t wrapper used by production Block 3 Fast."""

from __future__ import annotations

import sys
import threading
from typing import Any


from resource_registry import resource_path

RUNTIME_DIR = resource_path("opencc.runtime")
OPENCC_VERSION = "1.4.1"


def preflight_opencc_s2t() -> dict[str, Any]:
    required = [
        RUNTIME_DIR / "opencc" / "__init__.py",
        RUNTIME_DIR / "opencc" / "clib" / "share" / "opencc" / "s2t.json",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    c_path_findings = [str(RUNTIME_DIR)] if RUNTIME_DIR.drive.lower() == "c:" else []
    return {
        "ok": not missing and not c_path_findings,
        "runtime_dir": str(RUNTIME_DIR),
        "version": OPENCC_VERSION,
        "profile": "s2t",
        "missing": missing,
        "c_path_findings": c_path_findings,
        "downloads_allowed": False,
    }


class OpenCCS2TConverter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._converter: Any | None = None

    def load(self) -> dict[str, Any]:
        with self._lock:
            if self._converter is not None:
                return self.diagnostics()
            preflight = preflight_opencc_s2t()
            if not preflight["ok"]:
                raise RuntimeError("opencc_s2t_preflight_failed:" + ";".join(preflight["missing"]))
            runtime = str(RUNTIME_DIR)
            if runtime not in sys.path:
                sys.path.insert(0, runtime)
            from opencc import OpenCC

            self._converter = OpenCC("s2t")
            return self.diagnostics()

    def convert(self, text: str) -> str:
        self.load()
        value = str(text or "")
        if not value:
            return ""
        return str(self._converter.convert(value))

    def diagnostics(self) -> dict[str, Any]:
        return {
            **preflight_opencc_s2t(),
            "loaded": self._converter is not None,
        }


__all__ = ["OPENCC_VERSION", "OpenCCS2TConverter", "preflight_opencc_s2t"]
