"""Read-only Windows adapter facts. No runtime selection, persistence or inference.

adapter_id uses the Windows device interface path when available (stable for the
installed device, not a GPU serial number). A LUID fallback is explicitly scoped
to the adapter lifetime. Re-enumerate after hardware/driver changes.
"""
from __future__ import annotations

import ctypes as C
from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import json
import sys
import uuid


@dataclass(frozen=True)
class Adapter:
    adapter_id: str
    identity_scope: str
    device_path: str | None
    luid: str
    name: str
    vendor: str
    vendor_id: int
    device_id: int
    subsystem_id: int
    revision: int
    enumeration_index: int
    performance_rank: int | None
    classification: str
    classification_source: str
    dedicated_video_memory_bytes: int | None
    dedicated_system_memory_bytes: int | None
    shared_system_memory_bytes: int | None
    memory_source: str
    memory_quality: str
    sources: tuple[str, ...]


@dataclass(frozen=True)
class HardwareSnapshot:
    status: str
    adapters: tuple[Adapter, ...]
    diagnostics: tuple[str, ...]
    preference_source: str

    def get_adapter(self, adapter_id: str) -> Adapter | None:
        return next((a for a in self.adapters if a.adapter_id == adapter_id), None)

    def preferred_candidates(self) -> tuple[Adapter, ...]:
        """Hardware candidates only, NOT validated Quality/runtime support."""
        candidates = [a for a in self.adapters if a.classification not in {"software", "virtual"}]
        return tuple(sorted(candidates, key=lambda a: (
            a.performance_rank is None,
            a.performance_rank if a.performance_rank is not None else 0,
            {"discrete": 0, "integrated": 1, "physical_unknown": 2}[a.classification],
            a.adapter_id,
        )))

    def to_dict(self) -> dict:
        result = asdict(self)
        candidates = self.preferred_candidates()
        result.update(schema_version=1, preferred_candidate_id=candidates[0].adapter_id if candidates else None,
                      preferred_candidate_ids=[a.adapter_id for a in candidates],
                      runtime_selection_performed=False,
                      memory_semantics="Driver-reported capacity, not free VRAM or a process budget")
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def _normalize(rows: list[dict], errors: list[str]) -> HardwareSnapshot:
    adapters = []
    paths = Counter(row.get("device_path", "").casefold() for row in rows if row.get("device_path"))
    for row in rows:
        path = row.get("device_path") or None
        identity = "device:" + hashlib.sha256(path.casefold().encode("utf-8")).hexdigest() if path else "luid:" + row["luid"]
        # A rare duplicate device path must not collapse distinct logical adapters.
        duplicate = path and paths[path.casefold()] > 1
        if duplicate:
            errors.append("duplicate_device_identity:" + identity)
            identity += ":luid:" + row["luid"]
        flags = row.get("adapter_type")
        software = bool(row.get("dxgi_flags", 0) & 2) or row.get("is_hardware") is False
        virtual = flags is not None and bool(flags & ((1 << 6) | (1 << 7)))
        software |= flags is not None and bool(flags & (1 << 2))
        integrated = row.get("is_integrated")
        if virtual:
            kind, origin = "virtual", "D3DKMT:IndirectDisplayDevice/Paravirtualized"
        elif software:
            kind, origin = "software", "DXGI/DXCore/D3DKMT software flag"
        elif integrated is not None and row.get("is_hardware") is True:
            kind, origin = ("integrated" if integrated else "discrete"), "DXCore:IsHardware/IsIntegrated"
        elif flags is not None and flags & ((1 << 4) | (1 << 5)):
            kind, origin = ("integrated" if flags & (1 << 5) else "discrete"), "D3DKMT:HybridIntegrated/HybridDiscrete"
        else:
            kind, origin = "physical_unknown", "No reliable integrated/discrete property; no name/VRAM heuristic"
        memory = [row.get(k) for k in ("dedicated", "dedicated_system", "shared")]
        memory = [v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None for v in memory]
        adapters.append(Adapter(
            identity, "installed_device_interface" if path and not duplicate else "adapter_lifetime", path,
            row["luid"], row["name"], {0x10DE: "NVIDIA", 0x1002: "AMD", 0x8086: "Intel"}.get(row["vendor_id"], "other"),
            row["vendor_id"], row["device_id"], row.get("subsystem_id", 0), row.get("revision", 0),
            row["index"], row.get("performance_rank"), kind, origin, *memory,
            "DXGI:GetDesc1", "driver_reported" if all(v is not None for v in memory) else "partially_unknown",
            tuple(row.get("sources", ("DXGI",))),
        ))
    # Preserve enumeration order separately; canonical serialization is independent of it.
    adapters.sort(key=lambda a: a.adapter_id)
    status = "partial" if errors and adapters else "unavailable" if errors else "ok" if adapters else "empty"
    preference = "DXGI:HIGH_PERFORMANCE" if any(a.performance_rank is not None for a in adapters) else "classification_then_identity_not_performance_measurement"
    return HardwareSnapshot(status, tuple(adapters), tuple(errors), preference)


def enumerate_adapters() -> HardwareSnapshot:
    """Fresh, fail-soft snapshot. Importing this module performs no detection."""
    if sys.platform != "win32":
        return HardwareSnapshot("unavailable", (), ("unsupported_platform:" + sys.platform,), "unavailable")
    try:
        rows, errors = _enumerate_windows()
        return _normalize(rows, errors)
    except Exception as error:
        return HardwareSnapshot("unavailable", (), (f"detection_failed:{type(error).__name__}:{error}",), "unavailable")


# Fixed-width native declarations; only the OS collector loads DLLs or calls COM.
U32, I32, PTR = C.c_uint32, C.c_int32, C.c_void_p


class _GUID(C.Structure):
    _fields_ = [("data1", U32), ("data2", C.c_uint16), ("data3", C.c_uint16), ("data4", C.c_ubyte * 8)]


def _guid(value):
    return _GUID.from_buffer_copy(uuid.UUID(value).bytes_le)


class _LUID(C.Structure):
    _fields_ = [("low", U32), ("high", I32)]

    def key(self):
        return f"{self.high & 0xffffffff:08x}:{self.low:08x}"


class _Desc(C.Structure):
    _fields_ = [("name", C.c_wchar * 128), ("vendor", U32), ("device", U32),
                ("subsystem", U32), ("revision", U32), ("dedicated", C.c_size_t),
                ("dedicated_system", C.c_size_t), ("shared", C.c_size_t), ("luid", _LUID), ("flags", U32)]


class _DisplayHeader(C.Structure):
    _fields_ = [("type", U32), ("size", U32), ("luid", _LUID), ("id", U32)]


class _DisplayName(C.Structure):
    _fields_ = [("header", _DisplayHeader), ("path", C.c_wchar * 128)]


class _OpenAdapter(C.Structure):
    _fields_ = [("luid", _LUID), ("handle", U32)]


class _QueryAdapter(C.Structure):
    _fields_ = [("handle", U32), ("type", U32), ("data", PTR), ("size", U32)]


def _call(ptr, index, result, args, *values):
    address = C.cast(ptr, C.POINTER(C.POINTER(PTR))).contents[index]
    return C.WINFUNCTYPE(result, PTR, *args)(address)(ptr, *values)


def _release(ptr):
    if ptr:
        _call(ptr, 2, U32, ())


def _check(code, operation):
    if code < 0:
        raise OSError(f"{operation}:0x{code & 0xffffffff:08x}")


def _dll(name):
    # LOAD_LIBRARY_SEARCH_SYSTEM32; never load a shadow DLL from cwd/PATH.
    return C.WinDLL(name, winmode=0x800)


def _function(dll, name, args):
    fn = getattr(dll, name)
    fn.argtypes, fn.restype = args, I32
    return fn


def _device_path(luid):
    request = _DisplayName()
    request.header.type, request.header.size, request.header.luid = 4, C.sizeof(request), luid
    fn = _function(_dll("user32.dll"), "DisplayConfigGetDeviceInfo", (C.POINTER(_DisplayHeader),))
    code = fn(C.byref(request.header))
    if code != 0:
        raise OSError(f"DisplayConfigGetDeviceInfo:{code}")
    if not request.path:
        raise OSError("DisplayConfigGetDeviceInfo:empty_device_path")
    return request.path


def _adapter_type(luid):
    dll = _dll("gdi32.dll")
    opened = _OpenAdapter(luid, 0)
    fn = _function(dll, "D3DKMTOpenAdapterFromLuid", (C.POINTER(_OpenAdapter),))
    _check(fn(C.byref(opened)), "D3DKMTOpenAdapterFromLuid")
    try:
        flags = U32()
        query = _QueryAdapter(opened.handle, 15, C.cast(C.byref(flags), PTR), C.sizeof(flags))
        fn = _function(dll, "D3DKMTQueryAdapterInfo", (C.POINTER(_QueryAdapter),))
        _check(fn(C.byref(query)), "D3DKMTQueryAdapterInfo:ADAPTERTYPE")
        return flags.value
    finally:
        fn = _function(dll, "D3DKMTCloseAdapter", (C.POINTER(U32),))
        _check(fn(C.byref(U32(opened.handle))), "D3DKMTCloseAdapter")


def _dxcore_factory():
    dll = _dll("dxcore.dll")
    fn = _function(dll, "DXCoreCreateAdapterFactory", (C.POINTER(_GUID), C.POINTER(PTR)))
    factory = PTR()
    iid = _guid("78ee5945-c36e-4b13-a669-005dd11c0f06")
    _check(fn(C.byref(iid), C.byref(factory)), "DXCoreCreateAdapterFactory")
    return factory, dll


def _integrated_properties(factory, luid):
    adapter = PTR()
    iid = _guid("f0db4c7f-fe5a-42a2-bd62-f2a6cf6fc83e")
    _check(_call(factory, 4, I32, (C.POINTER(_LUID), C.POINTER(_GUID), C.POINTER(PTR)),
                 C.byref(luid), C.byref(iid), C.byref(adapter)), "DXCore:GetAdapterByLuid")
    try:
        values = []
        for prop in (11, 12):
            value = C.c_bool()
            _check(_call(adapter, 6, I32, (U32, C.c_size_t, PTR), prop, C.sizeof(value), C.byref(value)),
                   f"DXCore:GetProperty:{prop}")
            values.append(value.value)
        return values
    finally:
        _release(adapter)


def _performance_order(factory):
    factory6 = PTR()
    iid = _guid("c1b6694f-ff09-44a9-b03c-77900a0a1d17")
    _check(_call(factory, 0, I32, (C.POINTER(_GUID), C.POINTER(PTR)), C.byref(iid), C.byref(factory6)),
           "QueryInterface:IDXGIFactory6")
    iid = _guid("29038f61-3839-4626-91fd-086879011a05")
    order = {}
    try:
        for index in range(256):
            adapter = PTR()
            code = _call(factory6, 29, I32, (U32, U32, C.POINTER(_GUID), C.POINTER(PTR)),
                         index, 2, C.byref(iid), C.byref(adapter))
            if code & 0xffffffff == 0x887A0002:
                return order
            _check(code, "EnumAdapterByGpuPreference")
            try:
                desc = _Desc()
                _check(_call(adapter, 10, I32, (C.POINTER(_Desc),), C.byref(desc)), "GetDesc1:preference")
                order[desc.luid.key()] = index
            finally:
                _release(adapter)
        raise OSError("DXGI:preference_enumeration_limit")
    finally:
        _release(factory6)


def _enumerate_windows():
    dll = _dll("dxgi.dll")
    fn = _function(dll, "CreateDXGIFactory1", (C.POINTER(_GUID), C.POINTER(PTR)))
    iid = _guid("770aae78-f26f-4dba-a829-253c83d1b387")
    factory, core = PTR(), PTR()
    rows, errors = [], []
    _check(fn(C.byref(iid), C.byref(factory)), "CreateDXGIFactory1")
    try:
        try:
            order = _performance_order(factory)
        except Exception as error:
            order = {}
            errors.append(f"preference_unavailable:{error}")
        try:
            core, core_dll = _dxcore_factory()  # Keep the DLL alive until Release.
        except Exception as error:
            errors.append(f"classification_enrichment_unavailable:{error}")
        for index in range(256):
            adapter = PTR()
            code = _call(factory, 12, I32, (U32, C.POINTER(PTR)), index, C.byref(adapter))
            if code & 0xffffffff == 0x887A0002:
                break
            if code < 0:
                errors.append(f"EnumAdapters1:0x{code & 0xffffffff:08x}")
                break
            try:
                desc = _Desc()
                _check(_call(adapter, 10, I32, (C.POINTER(_Desc),), C.byref(desc)), "GetDesc1")
                row = dict(index=index, luid=desc.luid.key(), name=desc.name, vendor_id=desc.vendor,
                           device_id=desc.device, subsystem_id=desc.subsystem, revision=desc.revision,
                           dedicated=desc.dedicated, dedicated_system=desc.dedicated_system, shared=desc.shared,
                           dxgi_flags=desc.flags, performance_rank=order.get(desc.luid.key()), sources=["DXGI:GetDesc1"])
                for name, query, source in (
                    ("device_path", _device_path, "DisplayConfigGetDeviceInfo"),
                    ("adapter_type", _adapter_type, "D3DKMT:ADAPTERTYPE"),
                ):
                    try:
                        row[name] = query(desc.luid)
                        row["sources"].append(source)
                    except Exception as error:
                        errors.append(f"{desc.luid.key()}:{name}:{error}")
                if core:
                    try:
                        row["is_hardware"], row["is_integrated"] = _integrated_properties(core, desc.luid)
                        row["sources"].append("DXCore:IsHardware/IsIntegrated")
                    except Exception as error:
                        errors.append(f"{desc.luid.key()}:DXCore:{error}")
                rows.append(row)
            except Exception as error:
                errors.append(f"adapter_index:{index}:{error}")
            finally:
                _release(adapter)
        else:
            errors.append("DXGI:enumeration_limit")
        return rows, errors
    finally:
        _release(core)
        _release(factory)
