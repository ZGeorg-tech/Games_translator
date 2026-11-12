"""Derived coverage for the b10057-pcache1 Vulkan pipeline cache."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import re
import struct
import time
import uuid
from typing import Callable

from data_paths import DATA


RUNTIME_ID = "llama.runtime.vulkan.pcache1"
BUILD = "b10057-pcache1"
NATIVE_REVISION = "0bd0ec60998d0f71ec45471b633bf2403ac81956"
CACHE_SCHEMA = 2
COVERAGE_SCHEMA = 1
FAMILIES = {"q6_low_prep_v1", "q6_speed_prep_v1", "milmmt_prep_v1"}
_IDENTITY = re.compile(r"s2-v([0-9a-f]+)-d([0-9a-f]+)-r([0-9a-f]+)-a([0-9a-f]+)-([0-9a-f]{32})\Z")
_GENERATION = re.compile(r"[0-9a-f]{32}\Z")
MAX_BLOB = 256 * 1024 * 1024

_MI_SENTENCES = (
    "The captain marked a safe crossing on the old map.",
    "A scout reached the northern gate before sunset.",
    "The village sent food to the travelers at dawn.",
    "Two guards watched the bridge from the stone tower.",
    "A messenger carried fresh orders to the eastern camp.",
    "The old storehouse still held supplies for the journey.",
    "The healer asked everyone to rest before the next march.",
    "A narrow road led through the forest toward the river.",
    "The party waited until the patrol left the market square.",
    "The commander checked the inventory and counted every torch.",
    "A broken cart blocked the road beside the city wall.",
    "The blacksmith repaired the armor while the horses rested.",
    "The map showed another entrance beyond the western hills.",
    "A fisherman warned the group about the rising water.",
    "The gatekeeper opened the passage after reading the letter.",
    "The travelers agreed to meet again near the watchtower.",
    "A second messenger brought news from the nearby village.",
    "The captain ordered the group to protect the remaining supplies.",
    "The road became quieter after the storm passed.",
    "At sunrise the scouts returned with a clearer route.",
    "The team prepared its equipment for the final crossing.",
    "The villagers waited for a signal from the lookout.",
    "The soldiers carried their wounded companion to safety.",
    "The last note explained where the hidden path began.",
)


def preparation_inputs(family: str) -> tuple[str, ...]:
    if family in {"q6_low_prep_v1", "q6_speed_prep_v1"}:
        return ("Open the map.",)
    if family == "milmmt_prep_v1":
        # The same 7/17-sentence classes used by the M24c shape probe.
        return (
            " ".join(_MI_SENTENCES[(5 + i) % len(_MI_SENTENCES)] for i in range(7)),
            " ".join(_MI_SENTENCES[i] for i in range(17)),
        )
    raise ValueError(f"Unknown preparation family: {family}")


@contextmanager
def preparation_lock(root: Path, timeout_sec: float = 600.0, on_wait: Callable[[], None] | None = None):
    """Serialize server launch as well as preparation, so a waiter loads the new cache."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / "prepare.lock").open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + timeout_sec
        waiting = False
        while True:
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if not waiting and on_wait is not None:
                    on_wait()
                    waiting = True
                if time.monotonic() >= deadline:
                    raise TimeoutError("vulkan_preparation_lock_timeout")
                time.sleep(0.1)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def cache_snapshot(root: Path, load_event: dict[str, str] | None) -> dict[str, str] | None:
    """Validate the native current generation, metadata, and Vulkan device header."""
    if not load_event or load_event.get("build") != BUILD:
        return None
    identity = str(load_event.get("identity") or "")
    match = _IDENTITY.fullmatch(identity)
    if not match:
        return None
    location = root / identity
    try:
        pointer = (location / "current").read_text(encoding="ascii")
        tokens = pointer.split()
        if len(tokens) != 3 or tokens[0] != "GTPC2" or not _GENERATION.fullmatch(tokens[1]):
            return None
        if tokens[2] != "-" and not _GENERATION.fullmatch(tokens[2]):
            return None
        generation = tokens[1]
        blob_path = location / (generation + ".bin")
        size = blob_path.stat().st_size
        if size < 32 or size > MAX_BLOB:
            return None
        blob = blob_path.read_bytes()
        header = struct.unpack_from("<IIII", blob)
        if header != (32, 1, int(match[1], 16), int(match[2], 16)) or blob[16:32].hex() != match[5]:
            return None
        digest = hashlib.sha256(blob).hexdigest()
        metadata = (location / (generation + ".meta")).read_text(encoding="ascii").split()
        expected = ["GTPC", str(CACHE_SCHEMA), BUILD, NATIVE_REVISION,
                    str(int(match[1], 16)), str(int(match[2], 16)),
                    str(int(match[3], 16)), str(int(match[4], 16)), match[5], str(size), digest]
        if metadata != expected:
            return None
        return {"identity": identity, "generation": generation, "sha256": digest, "bytes": size}
    except (OSError, ValueError, struct.error, UnicodeError):
        return None


def covered_families(root: Path, load_event: dict[str, str] | None) -> tuple[set[str], dict[str, str] | None]:
    snapshot = cache_snapshot(root, load_event)
    if not snapshot or load_event.get("status") != "loaded" or load_event.get("generation") != snapshot["generation"]:
        return set(), snapshot
    try:
        marker = json.loads((root / snapshot["identity"] / "coverage.json").read_text(encoding="utf-8"))
        if (marker.get("schema") != COVERAGE_SCHEMA or marker.get("runtime_build") != BUILD
                or marker.get("native_revision") != NATIVE_REVISION
                or marker.get("cache_schema") != CACHE_SCHEMA
                or any(marker.get(key) != snapshot[key] for key in ("identity", "generation", "sha256"))):
            return set(), snapshot
        families = marker.get("families")
        if not isinstance(families, list) or any(item not in FAMILIES for item in families):
            return set(), snapshot
        return set(families), snapshot
    except (OSError, ValueError, TypeError, AttributeError, UnicodeError):
        return set(), snapshot


def persist_coverage(root: Path, snapshot: dict[str, str], families: set[str]) -> None:
    if not families or not families <= FAMILIES:
        raise ValueError("Invalid Vulkan preparation coverage")
    location = root / snapshot["identity"]
    marker = location / "coverage.json"
    temporary = location / ("coverage." + uuid.uuid4().hex + ".tmp")
    payload = {"schema": COVERAGE_SCHEMA, "runtime_build": BUILD,
               "native_revision": NATIVE_REVISION, "cache_schema": CACHE_SCHEMA,
               **{key: snapshot[key] for key in ("identity", "generation", "sha256")},
               "families": sorted(families)}
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, marker)
    finally:
        temporary.unlink(missing_ok=True)


def default_cache_root() -> Path:
    return DATA.vulkan_pipeline_cache(BUILD)
