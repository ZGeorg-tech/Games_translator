"""Isolated application Python bootstrap and worker command resolution."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from resource_registry import APP_ROOT, resource_path


def python_executable() -> Path:
    return resource_path("app.python") / "python.exe"


def package_directory(domain: str = "core") -> Path:
    resources = {"core": "app.environment", "csanmt": "csanmt.runtime"}
    if domain not in resources:
        raise RuntimeError(f"Unknown Python package domain: {domain}")
    return resource_path(resources[domain]) / "Lib" / "site-packages"


def sentencepiece_model_argument(model_path: Path) -> str:
    """Use the same local model without a Unicode prefix for the native tokenizer."""
    if (os.name == "nt" and not str(model_path).isascii() and Path.cwd() == APP_ROOT
            and model_path.is_relative_to(APP_ROOT)):
        return str(model_path.relative_to(APP_ROOT))
    return str(model_path)


def python_command(script: str | Path, *args: str, unbuffered: bool = False,
                   domain: str = "core") -> list[str]:
    package_directory(domain)  # Validate the domain without requiring its installation.
    flags = ["-I", "-B", "-X", "utf8"]
    if domain != "core":
        flags.extend(["-X", f"games_translator_domain={domain}"])
    if unbuffered:
        flags.append("-u")
    return [str(python_executable()), *flags, str(APP_ROOT / "python_runtime.py"),
            str(script), *map(str, args)]


def configure_runtime() -> None:
    if getattr(sys, "_games_translator_runtime_configured", False):
        return
    runtime = resource_path("app.python").resolve()
    if Path(sys.executable).resolve().parent != runtime:
        raise RuntimeError("Application bootstrap requires its registered app-local Python")
    if not sys.flags.isolated or not Path(sys.base_prefix).resolve().is_relative_to(runtime):
        raise RuntimeError("Application Python isolation configuration is missing")
    # -X is available during the startup .pth hook, before any package imports.
    domain = sys._xoptions.get("games_translator_domain", "core")
    packages = package_directory(domain)
    if not packages.is_dir():
        raise RuntimeError(f"Application package domain is missing: {packages}")
    sys.dont_write_bytecode = True
    for name in ("PYTHONHOME", "PYTHONPATH", "PYTHONUSERBASE", "VIRTUAL_ENV"):
        os.environ.pop(name, None)
    os.environ["PYTHONNOUSERSITE"] = "1"
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8:backslashreplace"
    from data_paths import configure_process_data
    configure_process_data()
    import site
    site.ENABLE_USER_SITE = False
    # Preserve installed .pth hooks, including PyWin32 DLL discovery.
    site.addsitedir(str(packages))
    sys._games_translator_package_domain = domain
    sys._games_translator_runtime_configured = True
    from session_logging import register_process
    register_process()


if __name__ == "__main__":
    configure_runtime()
    import runpy
    target = Path(sys.argv[1] if len(sys.argv) > 1 else APP_ROOT / "launcher.py").resolve()
    if not target.is_relative_to(APP_ROOT) or not target.is_file():
        raise RuntimeError(f"Invalid application script: {target}")
    sys.argv = [str(target), *sys.argv[2:]]
    try:
        runpy.run_path(str(target), run_name="__main__")
    except BaseException as exc:
        from session_logging import process_result, exception_record
        if not isinstance(exc, SystemExit):
            exception_record(exc)
        code = (exc.code or 0) if isinstance(exc, SystemExit) else 1
        process_result(code if isinstance(code, int) else 1)
        raise
    else:
        from session_logging import process_result
        process_result(0)
