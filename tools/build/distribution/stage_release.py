"""Stage and verify a local Windows release candidate. Never publishes anything."""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import mmap
import os
from pathlib import Path
import re
import shutil
import sys
from zipfile import ZIP_STORED, ZipFile

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from check_distribution import (
    ALL_BLOCKS, POLICY, PUBLIC_NOTICES, RESOURCES, ROOT, UI_FILES, allowed, check,
    installation_template, source_closure,
)

HERE = SCRIPT_DIR.relative_to(ROOT)
BUILD_SOURCES = {
    HERE / name for name in (
        "windows_launcher.cpp", "stage_release.py", "check_distribution.py",
        "production_allowlist.json", "DISTRIBUTION_POLICY.md",
    )
}
PART_BYTES = 1900 * 1024**2
REPARSE = 0x400
PRIVATE_ROOTS = tuple(
    str(path).replace("\\", "/").casefold().encode("utf-8")
    for path in (ROOT, Path.home())
)
BINARY_ROOTS = tuple({
    value.encode(encoding)
    for root in (ROOT, Path.home())
    for path in (str(root), root.as_posix())
    for value in (path, path.lower(), path.upper())
    for encoding in ("utf-8", "utf-16-le")
})
SECRET = re.compile(
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    rb"\b(?:AIza[\w-]{35}|sk-[\w-]{24,}|gh[pousr]_[A-Za-z0-9]{36,}|AKIA[A-Z0-9]{16})\b"
)
TEXT = {".py", ".cpp", ".rs", ".json", ".jsonl", ".toml", ".lock", ".yml", ".yaml",
        ".md", ".txt", ".svg", ".ps1", ".js", ".cjs", ".pth", ".ini", ".pem"}
# The pinned patched build contains absolute local build paths in multiple DLLs.
# Omit the whole variant, not individual DLLs that would break native closure.
HELD_RESOURCES = {"llama.runtime.vulkan.pcache1": "local build paths embedded in DLLs"}
CAPTURE_EXTENSION = (Path(RESOURCES["app.environment"].relative_path) /
                     "Lib/site-packages/game_capture_core/game_capture_core.cp313-win_amd64.pyd")
OMIT_RUNTIME_PATHS = {
    "app.environment": (
        "Lib/site-packages/maturin", "Lib/site-packages/maturin-1.13.3.dist-info", "share/man",
        "Lib/site-packages/Crypto/SelfTest", "Lib/site-packages/game_capture_core-0.1.0.dist-info/sboms",
    ),
    "csanmt.runtime": ("Include", "Scripts"),
    "csanmt.model": (".msc", ".mv", "._____temp"),
    "opencc.runtime": ("bin",),  # Relocation wrapper, unused by the production Python API.
}


def reparse(path):
    return bool(path.lstat().st_file_attributes & REPARSE)


def tree(root, prune=False):
    if reparse(root):
        raise ValueError(f"Reparse root: {root.relative_to(ROOT)}")
    for folder, dirs, files in os.walk(root, followlinks=False, onerror=_raise):
        current = Path(folder)
        for name in dirs[:]:
            path = current / name
            if reparse(path):
                raise ValueError(f"Reparse point: {path.relative_to(ROOT)}")
            if (prune and name.casefold() in POLICY["forbidden_components"]
                    and not allowed(path.relative_to(ROOT).as_posix())):
                dirs.remove(name)
        for name in sorted(files):
            path = current / name
            if reparse(path):
                raise ValueError(f"Reparse point: {path.relative_to(ROOT)}")
            yield path


def _raise(exc):
    raise exc


def first_party():
    paths = {Path(name) for name in POLICY["root_files"] if name.endswith(".py")}
    for name in POLICY["source_roots"]:
        for path in tree(ROOT / name, prune=True):
            relative = path.relative_to(ROOT)
            if allowed(relative.as_posix()) and (
                path.suffix == ".py"
                or relative.parts[:2] in {("translation_quality", "packs"), ("translation_quality", "schema")}
            ) and relative.parts[:2] != ("translation_quality", "tools"):
                paths.add(relative)
    return paths | set(map(Path, UI_FILES | PUBLIC_NOTICES))


def package_resource_ids(block):
    # The shared dispatcher can fall back to Tesseract even when it is not a UI choice.
    fallback = {"tesseract.runtime", "tesseract.tessdata"} if block == "block2" else set()
    return tuple(
        key for key in POLICY["resource_ids"]
        if key not in HELD_RESOURCES and (
            block is None or block in RESOURCES[key].consumers or "core" in RESOURCES[key].consumers or key in fallback
        )
    )


def resource_payload(ids):
    resources = defaultdict(set)
    for resource_id in ids:
        if not RESOURCES[resource_id].relative_path.startswith("resources/"):
            continue
        root = ROOT / RESOURCES[resource_id].relative_path
        if reparse(root):
            raise ValueError(f"Resource root is a reparse point: {resource_id}")
        files = [root] if root.is_file() else tree(root, prune=True)
        for path in files:
            relative = path.relative_to(ROOT)
            if allowed(relative.as_posix()) and path.suffix.lower() not in {".bat", ".cmd", ".ps1", ".pdb"}:
                if any(path.is_relative_to(root / child) for child in OMIT_RUNTIME_PATHS.get(resource_id, ())):
                    continue
                resources[resource_id].add(relative)
        if not resources[resource_id]:
            raise ValueError(f"Empty resource: {resource_id}")
    if "bergamot.node" in resources:
        node = ROOT / RESOURCES["bergamot.node"].relative_path
        resources["bergamot.node"].add(node.with_name("LICENSE").relative_to(ROOT))
    return resources


def copy_files(paths, target, source_root=ROOT):
    checked = set()
    tasks = []
    for relative in sorted(paths):
        source = source_root / relative
        for path in (source, *list(source.parents)[:len(relative.parts)-1]):
            if path not in checked:
                if reparse(path):
                    raise ValueError(f"Reparse point in input: {relative}")
                if path != source:
                    checked.add(path)
        dest = target / relative
        tasks.append((source, dest))
    for folder in {dest.parent for _, dest in tasks}:
        folder.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        for _ in pool.map(lambda pair: shutil.copyfile(*pair), tasks):
            pass


def inspect(root):
    findings = []
    files = {}
    for file in tree(root):
        relative = file.relative_to(root).as_posix()
        files[relative] = file.stat().st_size
        if file.suffix.lower() in TEXT or file.name in {"METADATA", "RECORD", "LICENSE", "NOTICE"}:
            body = file.read_bytes()
            # Pattern definitions in the scanner are source, not leaked values.
            if SECRET.search(body) and relative not in {
                (HERE / "check_distribution.py").as_posix(), (HERE / "stage_release.py").as_posix()
            }:
                findings.append({"path": relative, "reason": "credential-shaped text; review required"})
            normalized = body.replace(b"\\\\", b"\\").replace(b"\\", b"/").lower()
            if any(marker in normalized for marker in PRIVATE_ROOTS):
                findings.append({"path": relative, "reason": "local developer path"})
        elif file.suffix.lower() in {".dll", ".exe", ".pyd", ".wasm", ".gguf"} and files[relative]:
            with file.open("rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as body:
                if any(body.find(marker) != -1 for marker in BINARY_ROOTS):
                    findings.append({"path": relative, "reason": "local developer path in binary"})
    return files, findings


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def stage(output, verify_only=False, capture_extension=None, block=None, resource_input=ROOT):
    if source_closure():
        raise ValueError("Production source closure check failed")
    source = output / "source"
    windows = output / "Windows staging"
    common = first_party()
    rust = {Path("game_capture_core") / name for name in (
        "Cargo.toml", "Cargo.lock", "pyproject.toml", "src/lib.rs")}
    source_paths = common | BUILD_SOURCES | rust | {Path(".gitignore"), Path("SOURCE_DISTRIBUTION.md")}
    ids = package_resource_ids(block)
    resources = resource_payload(ids)
    binaries = set().union(*resources.values())
    unused_packs = {Path(RESOURCES[key].relative_path) for key in POLICY["resource_ids"]
                    if key.startswith("quality.") and key not in ids}
    binary_sources = {path for path in common if not any(path.is_relative_to(pack) for pack in unused_packs)}
    binary_paths = binary_sources | {Path("Games_translator.exe")} | binaries
    if check([path.as_posix() for path in binary_paths]):
        raise ValueError("Windows payload contains a forbidden path")
    if not verify_only:
        output.mkdir(parents=True)
        copy_files(source_paths, source)
        copy_files(binary_sources | {Path("Games_translator.exe")}, windows)
        if reparse(resource_input):
            raise ValueError("Reparse point in resource input")
        copy_files(binaries, windows, resource_input)
        if capture_extension:
            if reparse(capture_extension) or not capture_extension.is_file():
                raise ValueError("Expected a regular build artifact for the capture extension")
            shutil.copyfile(capture_extension, windows / CAPTURE_EXTENSION)
        write_json(windows / "installation.json", installation_template(block))
    if json.loads((windows / "installation.json").read_text(encoding="utf-8")) != installation_template(block):
        raise ValueError("Non-portable installation metadata")
    source_files, source_findings = inspect(source)
    windows_files, windows_findings = inspect(windows)
    assert set(source_files) == {path.as_posix() for path in source_paths}
    assert set(windows_files) == {"installation.json"} | {path.as_posix() for path in binary_paths}
    for key in ids:
        if key in resources:
            continue
        prefix = RESOURCES[key].relative_path
        resources[key] = {path for path in binary_sources if path.as_posix() == prefix
                          or path.as_posix().startswith(prefix + "/")}
        if not resources[key]:
            raise ValueError(f"Empty source resource: {key}")
    report = {
        "block": block,
        "excluded_other_blocks": sorted(set(POLICY["resource_ids"]) - set(ids) - set(HELD_RESOURCES)),
        "publication_approved": False,
        "held_resources": HELD_RESOURCES,
        "omitted_runtime_extras": OMIT_RUNTIME_PATHS,
        "capture_extension_sha256": sha256((windows / CAPTURE_EXTENSION).read_bytes()).hexdigest(),
        "source": {"files": len(source_files), "bytes": sum(source_files.values()), "findings": source_findings},
        "windows": {"files": len(windows_files), "bytes": sum(windows_files.values()), "findings": windows_findings},
        "resources": {key: {"path": RESOURCES[key].relative_path, "files": len(paths),
                            "bytes": sum(windows_files[p.as_posix()] for p in paths),
                            "consumers": RESOURCES[key].consumers} for key, paths in resources.items()},
        "resources_by_block": {name: sorted(set(resources).intersection(package_resource_ids(name)))
                               for name in ALL_BLOCKS},
    }
    write_json(output / "staging_manifest.json", report)
    if block:
        write_json(output / "files_manifest.json", dict(sorted(windows_files.items())))
    return int(bool(source_findings or windows_findings))


def pack(root, folder, label, split):
    filename = folder / (label + ".zip")
    paths = {p.relative_to(root).as_posix(): p for p in tree(root)}
    with ZipFile(filename, "x", compression=ZIP_STORED) as archive:
        for name, path in sorted(paths.items()):
            archive.write(path, name)
    with ZipFile(filename) as archive:
        if set(archive.namelist()) != set(paths) or archive.testzip() is not None:
            raise ValueError(f"Archive verification failed: {filename.name}")
    size = filename.stat().st_size
    rows = []
    with filename.open("rb") as stream:
        whole_hash = sha256()
        for index, start in enumerate(range(0, size, PART_BYTES if split else size), 1):
            count = min(PART_BYTES, size - start) if split else size
            target = filename.with_suffix(f".zip.{index:03d}") if split else filename
            digest = sha256()
            dest = target.open("xb") if split else None
            try:
                while count:
                    block = stream.read(min(count, 8 * 1024**2))
                    if not block:
                        raise EOFError(filename.name)
                    digest.update(block)
                    whole_hash.update(block)
                    if dest:
                        dest.write(block)
                    count -= len(block)
            finally:
                if dest:
                    dest.close()
            with target.open("rb") as saved:
                if sha256_file(saved) != digest.hexdigest() or target.stat().st_size >= 2 * 1024**3:
                    raise ValueError(f"Part verification failed: {target.name}")
            rows.append({"name": target.name, "bytes": target.stat().st_size, "sha256": digest.hexdigest()})
    if split:
        filename.unlink()
    return {"zip_name": filename.name, "zip_sha256": whole_hash.hexdigest(), "zip_bytes": size,
            "files": len(paths), "parts": rows}


def sha256_file(stream):
    digest = sha256()
    for block in iter(lambda: stream.read(8 * 1024**2), b""):
        digest.update(block)
    return digest.hexdigest()


def archives(output, block=None):
    source, windows = output / "source", output / "Windows staging"
    if stage(output, verify_only=True, block=block):
        raise ValueError("Unresolved staging findings")
    folder = output / "archives-local-only"
    folder.mkdir()
    result = {"publication_approved": False, "block": block}
    if not block:
        result["source"] = pack(source, folder, "Games_translator-source", split=False)
    label = f"Games_translator-Block{block[-1]}-Windows-x64" if block else "Games_translator-windows-x64"
    result["windows"] = pack(windows, folder, label, split=True)
    write_json(folder / "archives.json", result)
    windows_parts = result["windows"]["parts"]
    join = "copy /b " + "+".join(row["name"] for row in windows_parts) + " " + result["windows"]["zip_name"]
    instructions = (
        "# Локальный кандидат сборки. Не публиковать.\n\n"
        "Сверьте SHA256 каждой части с archives.json (PowerShell: Get-FileHash -Algorithm SHA256). "
        "В командной строке Windows (cmd.exe), из этой папки, выполните:\n\n"
        f"    {join}\n\n"
        "Сверьте SHA256 объединённого ZIP, распакуйте его в доступную для записи папку "
        "и запустите Games_translator.exe. Папка data создаётся при первом запуске.\n\n"
        "Нужны все части этого Windows ZIP. Другие комплекты не требуются. "
        "Vulkan b10057-pcache1 не включён. Список ресурсов и их потребителей "
        "находится в staging_manifest.json на уровень выше.\n"
    )
    if block:
        instructions += (
            "\nДля Paddle и Quality распакуйте комплект в путь без кириллицы и других "
            "не-ASCII символов. Пробелы допустимы.\n"
        )
    (folder / "UNPACK.md").write_text(instructions, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("stage", "verify", "archives"))
    parser.add_argument("output", type=Path)
    parser.add_argument("--capture-extension", type=Path)
    parser.add_argument("--block", choices=ALL_BLOCKS)
    parser.add_argument("--resource-input", type=Path, default=ROOT)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "tools") or output == ROOT / "tools":
        parser.error("Use a dedicated output directory inside tools/")
    if args.capture_extension and args.phase != "stage":
        parser.error("--capture-extension is only accepted by stage")
    if args.phase in ("stage", "verify"):
        raise SystemExit(stage(output, verify_only=args.phase == "verify", capture_extension=args.capture_extension,
                               block=args.block, resource_input=args.resource_input.absolute()))
    archives(output, block=args.block)
