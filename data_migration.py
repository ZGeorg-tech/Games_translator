"""Idempotent copy/verify/activate migration of legacy persistent user data."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from data_paths import DATA, DataPaths


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def sqlite_integrity(path: Path) -> str:
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        result = db.execute("PRAGMA integrity_check").fetchall()
    if result != [("ok",)]:
        raise RuntimeError(f"SQLite integrity failed: {path}: {result}")
    return "ok"


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _legacy_files(paths: DataPaths):
    root = paths.app_root / "profiles"
    if root.exists():
        if root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()):
            raise RuntimeError(f"Legacy profiles root is a link: {root}")
        for directory, dirs, files in os.walk(root, followlinks=False):
            for name in dirs:
                child = Path(directory) / name
                if child.is_symlink() or (hasattr(child, "is_junction") and child.is_junction()):
                    raise RuntimeError(f"Legacy profile link requires manual resolution: {child}")
            for name in files:
                source = Path(directory) / name
                relative = source.relative_to(root)
                if "ocr_benchmark_crops" in relative.parts:
                    continue
                if name.endswith((".sqlite-wal", ".sqlite-shm", ".sqlite-journal")):
                    continue
                family = "tm" if name == "translation_memory.sqlite" else (
                    "history" if name == "translation_history.jsonl" else "profiles")
                yield source, paths.path(family, str(relative)), family
    for name in ("profiles.json", "launcher_settings.json", "ocr_compare_enabled.flag"):
        source = paths.app_root / name
        if source.is_file():
            yield source, paths.path("settings", name), "settings"


def migrate_persistent_data(paths: DataPaths = DATA, manifest_path: Path | None = None) -> dict:
    journal = paths.path("backups", "m17", "migration.json")
    previous = json.loads(journal.read_text(encoding="utf-8")) if journal.exists() else None
    records = []
    conflicts = []
    for source, target, family in _legacy_files(paths):
        if not source.resolve().is_relative_to(paths.app_root) or source.is_symlink():
            raise RuntimeError(f"Unsafe legacy source: {source}")
        paths.validate(target)
        if source.suffix in {".sqlite", ".db"}:
            if any(Path(str(source) + suffix).exists() and Path(str(source) + suffix).stat().st_size
                   for suffix in ("-wal", "-journal")):
                raise RuntimeError(f"Active SQLite journal; close application before migration: {source}")
            sqlite_integrity(source)
        sha = digest(source)
        backup = paths.path("backups", "m17", "legacy", str(source.relative_to(paths.app_root)))
        if target.exists() and digest(target) != sha:
            conflicts.append({"old_path": str(source), "target_path": str(target), "reason": "different_content"})
        if backup.exists() and digest(backup) != sha:
            conflicts.append({"old_path": str(source), "target_path": str(backup), "reason": "backup_conflict"})
        records.append({"old_path": str(source), "target_path": str(target), "backup_path": str(backup),
                        "type": family, "size": source.stat().st_size, "file_count": 1,
                        "sha256_before": sha, "status": "planned"})
    if not records and previous and previous.get("status") == "complete":
        if manifest_path:
            _write_json(manifest_path, previous)
        return previous
    completed = []
    for record in (previous or {}).get("entries", []):
        if any(item["old_path"] == record["old_path"] for item in records):
            continue
        target = paths.validate(record["target_path"])
        backup = paths.validate(record["backup_path"])
        if not target.is_file() or not backup.is_file():
            raise RuntimeError(f"Incomplete migration evidence: {target}")
        if record.get("status") != "verified":
            if digest(target) != record["sha256_before"] or digest(backup) != record["sha256_before"]:
                raise RuntimeError(f"Interrupted migration conflict: {target}")
            record.update(status="verified", sha256_after=digest(target))
        completed.append(record)
    result = {"data_root": str(paths.root), "status": "conflict" if conflicts else "planned",
              "entries": completed + records, "conflicts": conflicts}
    # Evidence exists before the first persistent file is copied or moved.
    _write_json(journal, result)
    if manifest_path:
        _write_json(manifest_path, result)
    if conflicts:
        raise RuntimeError(f"Persistent DATA_ROOT migration conflict; originals retained: {journal}")
    for record in records:
        source, target, backup = (Path(record[key]) for key in ("old_path", "target_path", "backup_path"))
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            staging = target.with_name(target.name + ".m17-staging")
            shutil.copy2(source, staging)
            if digest(staging) != record["sha256_before"]:
                raise RuntimeError(f"Copy verification failed: {source}")
            if source.suffix in {".sqlite", ".db"}:
                sqlite_integrity(staging)
            os.replace(staging, target)
        if digest(source) != record["sha256_before"] or digest(target) != record["sha256_before"]:
            raise RuntimeError(f"Concurrent data change; original retained: {source}")
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            shutil.copy2(source, backup)
        if digest(backup) != record["sha256_before"]:
            raise RuntimeError(f"Backup verification failed: {source}")
        # All three copies checked; the legacy file is no longer a writable authority.
        source.unlink()
        record.update(status="verified", sha256_after=digest(target))
        if source.suffix in {".sqlite", ".db"}:
            record["sqlite_integrity"] = sqlite_integrity(target)
        _write_json(journal, result)
    result["status"] = "complete"
    _write_json(journal, result)
    if manifest_path:
        _write_json(manifest_path, result)
    return result
