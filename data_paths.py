"""Writable application paths, separate from immutable ResourceRegistry.

installation.json may explicitly select an absolute or install-relative data_root.
Path lookup is read-only; ensure() creates only the requested directory.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from language_blocks import installed_blocks

APP_ROOT = Path(__file__).resolve().parent
PATHS = {
    "profiles": "user/profiles", "tm": "user/tm", "history": "user/history",
    "settings": "user/settings", "packs": "user/translation-packs",
    "backups": "user/backups", "logs": "app/logs/runtime",
    "diagnostics": "app/logs/diagnostics", "sessions": "app/logs/sessions", "temp": "app/temp",
    "generated": "app/generated", "downloads": "app/downloads",
    "translation.cache": "app/cache/translation",
    "translategemma.cache": "app/cache/llama-cpp",
    "csanmt.cache": "app/cache/csanmt", "fugu.cache": "app/cache/fugu",
    "paddle.models": "app/cache/paddle", "paddle.home": "app/cache/paddle/home",
    "paddlex": "app/cache/paddle/paddlex", "hf.home": "app/cache/hf",
    "modelscope": "app/cache/modelscope", "torch": "app/cache/torch",
    "xdg": "app/cache/xdg", "npm": "app/cache/npm",
    "runtime_homes": "app/cache/runtime-homes",
    "vulkan.pipeline_cache": "app/cache/hardware/vulkan-pipelines",
}
_SESSION = f"{os.getpid()}-{uuid.uuid4().hex[:12]}"


class DataPaths:
    def __init__(self, app_root: Path = APP_ROOT):
        self.app_root = Path(app_root).resolve()
        metadata = self.app_root / "installation.json"
        config = json.loads(metadata.read_text(encoding="utf-8-sig")) if metadata.exists() else {}
        self.installed_blocks = installed_blocks(config)
        selected = Path(config.get("data_root", "data"))
        if ".." in selected.parts:
            raise ValueError("DATA_ROOT must not contain '..'")
        self.root = (selected if selected.is_absolute() else self.app_root / selected).resolve()
        if not selected.is_absolute() and not self.root.is_relative_to(self.app_root):
            raise ValueError("Portable DATA_ROOT escapes the application tree")
        if self.root == self.app_root or self.root.is_relative_to(self.app_root / "resources"):
            raise ValueError("DATA_ROOT must be separate from application/resources")
        self._temp_owner = None
        self._temp_lock = threading.RLock()

    def path(self, family: str, *parts: str) -> Path:
        if family in {"logs", "diagnostics"}:
            from session_logging import log_root
            root = log_root(self, family)
            if root is not None:
                candidate = root.joinpath(*parts)
                if not candidate.resolve().is_relative_to(root.resolve()):
                    raise ValueError("Unsafe session log path")
                return candidate
        relative = Path(PATHS[family], *parts)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Unsafe data path: {relative}")
        candidate = self.root / relative
        if not candidate.resolve().is_relative_to(self.root):
            raise ValueError(f"DATA_ROOT link escape: {candidate}")
        return candidate

    def ensure(self, family: str, *parts: str) -> Path:
        path = self.path(family, *parts)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def vulkan_pipeline_cache(self, runtime_build: str, compatibility_identity: str | None = None) -> Path:
        """Disposable shared cache namespace; lookup does not create directories."""
        parts = [runtime_build]
        if compatibility_identity is not None:
            parts.append(compatibility_identity)
        if any(not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,159}", part) for part in parts):
            raise ValueError("Invalid Vulkan cache namespace/identity")
        return self.path("vulkan.pipeline_cache", *parts)

    def validate(self, path: str | Path) -> Path:
        value = Path(path).resolve()
        if not value.is_relative_to(self.root):
            raise ValueError(f"Path outside configured DATA_ROOT: {value}")
        return value

    def temp(self, consumer: str = "core") -> Path:
        return self.path("temp", _SESSION, consumer)

    def activate_temp(self):
        """Activate process ownership only when TEMP is actually needed."""
        with self._temp_lock:
            if self._temp_owner is None:
                from temp_lifecycle import TempOwner
                self._temp_owner = TempOwner(self.path("temp"), _SESSION)
            return self._temp_owner

    def begin_temp_spawn(self):
        return self.activate_temp().begin_spawn()

    def finish_temp_spawn(self, token, pid=None):
        try:
            self.activate_temp().finish_spawn(token, pid)
        except OSError as exc:
            # On-disk pending intent remains conservative; do not fail a live child.
            from temp_lifecycle import diagnostic
            diagnostic({"rows": [{"status": "retained", "reason": "spawn_record_write_failed",
                                   "detail": str(exc)}]}, "startup")

    def popen_temp(self, *args, **kwargs):
        """Keep native child TEMP/IPC alive even if the parent is terminated."""
        import subprocess
        token = self.begin_temp_spawn()
        try:
            process = subprocess.Popen(*args, **kwargs)
        except FileNotFoundError:
            # CreateProcess did not start a child; there is no consumer to retain.
            self.finish_temp_spawn(token)
            raise
        except BaseException:
            # Conservatively retain an unresolved spawn if Popen did not return.
            raise
        self.finish_temp_spawn(token, process.pid)
        return process

    def environment_paths(self, consumer: str = "core") -> dict[str, Path]:
        hf = self.path("hf.home")
        temp = self.temp(consumer)
        return {
            "HF_HOME": hf, "HF_HUB_CACHE": hf / "hub",
            "HUGGINGFACE_HUB_CACHE": hf / "hub", "TRANSFORMERS_CACHE": hf / "hub",
            "TORCH_HOME": self.path("torch"), "XDG_CACHE_HOME": self.path("xdg"),
            "MODELSCOPE_CACHE": self.path("modelscope"),
            "PADDLE_HOME": self.path("paddle.home"), "PADDLE_PDX_CACHE_HOME": self.path("paddlex"),
            "TEMP": temp, "TMP": temp, "TMPDIR": temp,
            "MPLCONFIGDIR": self.path("xdg", "matplotlib"),
            "CUDA_CACHE_PATH": self.path("xdg", "cuda"),
            "GAME_TRANSLATOR_PADDLE_MODEL_ROOT": self.path("paddle.models"),
            "GAME_TRANSLATOR_PADDLE_TMP": temp / "paddle",
        }

    def child_environment_paths(self, consumer: str) -> dict[str, Path]:
        # Preserve backend-process isolation without changing the GUI/Windows HOME.
        home = self.path("runtime_homes", consumer)
        return {**self.environment_paths(consumer),
                "HOME": home / "home", "USERPROFILE": home / "home",
                "APPDATA": home / "appdata", "LOCALAPPDATA": home / "localappdata"}


DATA = DataPaths()
data_path = DATA.path


def log_file(name: str, app_root: str | Path = APP_ROOT) -> str:
    from session_logging import raw_enabled
    paths = DATA if Path(app_root).resolve() == APP_ROOT else DataPaths(Path(app_root))
    target = paths.path("logs", name)
    if raw_enabled():
        target.parent.mkdir(parents=True, exist_ok=True)
    return str(target)


def profile_data_dir(app_root: str | Path, cfg: dict, family: str) -> Path:
    paths = DATA if Path(app_root).resolve() == APP_ROOT else DataPaths(Path(app_root))
    profile = str(cfg.get("profile_dir") or "").strip()
    if profile:
        value = Path(profile).resolve()
        for root in (paths.path("profiles"), paths.app_root / "profiles"):
            if value.is_relative_to(root):
                relative = value.relative_to(root)
                return paths.path(family, str(relative))
        raise ValueError("Profile directory outside configured profiles root")
    name = re.sub(r'[\\/*?:"<>|]', "", str(cfg.get("game_name") or "unknown_game"))
    name = re.sub(r"\s+", " ", name).strip()[:120] or "unknown_game"
    return paths.path(family, name)


def rebase_profile_path(value: str) -> str:
    if not value:
        return value
    path = Path(value).resolve()
    old = APP_ROOT / "profiles"
    return str(DATA.path("profiles", str(path.relative_to(old)))) if path.is_relative_to(old) else value


def configure_process_data() -> None:
    """Called by application entry points before third-party imports."""
    DATA.activate_temp()
    env = DATA.environment_paths()
    DATA.temp().mkdir(parents=True, exist_ok=True)
    os.environ.update({key: str(path) for key, path in env.items()})
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    tempfile.tempdir = str(DATA.temp())


def initialize_data() -> None:
    from data_migration import migrate_persistent_data

    # Probe the selected root; a failure is fatal, never a fallback to AppData.
    DATA.root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=DATA.root) as probe:
        probe.write(b"DATA_ROOT")
        probe.flush()
    migrate_persistent_data(DATA)
    metadata = APP_ROOT / "installation.json"
    if not metadata.exists():
        with metadata.open("x", encoding="utf-8") as handle:
            json.dump({"data_root": "data"}, handle, indent=2)
    configure_process_data()
