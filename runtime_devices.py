"""Pinned llama.cpp device enumeration and physical-adapter binding, without models."""
from __future__ import annotations

import ctypes as C
import json
import os
from pathlib import Path
import struct
import subprocess
import sys

from data_paths import DATA
from python_runtime import python_command
from resource_registry import RESOURCES, resource_path

VISIBILITY_ENV = ("CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER", "GGML_CUDA_DEVICES", "GGML_VK_VISIBLE_DEVICES")


def enumeration_environment() -> dict[str, str]:
    env = dict(os.environ)
    for key in VISIBILITY_ENV:
        env.pop(key, None)
    env.update({key: str(value) for key, value in DATA.child_environment_paths("runtime-devices").items()})
    return env


def enumerate_runtime_devices(runtime_id: str) -> dict:
    try:
        process = subprocess.run(
            python_command(Path(__file__).resolve(), "--native", runtime_id),
            env=enumeration_environment(), capture_output=True, text=True, encoding="utf-8",
            errors="backslashreplace", timeout=25, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if process.returncode:
            raise RuntimeError(process.stderr[-2500:] or process.stdout[-2500:])
        result = json.loads(process.stdout)
        result["native_diagnostics"] = process.stderr[-6000:]
        return result
    except (OSError, ValueError, subprocess.TimeoutExpired, RuntimeError) as error:
        return {"runtime_id": runtime_id, "status": "unavailable", "devices": [],
                "error": f"{type(error).__name__}:{error}"}


def _name(value) -> str:
    return " ".join(str(value or "").casefold().split())


def map_adapter_device(selection: dict, enumeration: dict) -> dict:
    adapter = selection.get("selected_adapter") or {}
    candidates = [d for d in enumeration.get("devices", []) if d.get("vendor") == adapter.get("vendor")
                  and d.get("device_type") == "gpu"]
    result = {"status": "unresolved", "basis": None, "runtime_device": None,
              "adapter_id": adapter.get("adapter_id"), "runtime_id": selection.get("runtime_id"),
              "reason": "runtime_device_not_visible"}
    if enumeration.get("status") != "ok":
        result["reason"] = "runtime_device_enumeration_failed"
        return result
    if adapter.get("classification") != "discrete":
        result["reason"] = "unsupported_vendor"
        return result
    if any(d.get("identity_enrichment_error") for d in candidates):
        result.update(status="ambiguous", reason="runtime_device_mapping_ambiguous")
        return result
    matches = []
    if adapter.get("luid") and any(d.get("luid") for d in candidates):
        matches = [d for d in candidates if d.get("luid") == adapter["luid"]]
        result["basis"] = "windows_luid_exact"
    elif adapter.get("pci_bus_id") and any(d.get("pci_bus_id") for d in candidates):
        matches = [d for d in candidates if d.get("pci_bus_id") == adapter["pci_bus_id"]]
        result["basis"] = "pci_bus_id_exact"
    else:
        # A chip ID or a name is not an instance identity: require uniqueness on both sides.
        peers = [a for a in selection.get("adapters_detected", [])
                 if a.get("classification") not in {"software", "virtual"}
                 and a.get("vendor_id") == adapter.get("vendor_id")
                 and a.get("device_id") == adapter.get("device_id")]
        matches = [d for d in candidates if adapter.get("vendor_id") is not None and adapter.get("device_id") is not None
                   and d.get("vendor_id") == adapter.get("vendor_id")
                   and d.get("device_id") == adapter.get("device_id")]
        result["basis"] = "unique_vendor_device_ids"
        if not matches:
            memory = adapter.get("dedicated_video_memory_bytes")
            matches = [d for d in candidates if _name(d.get("name")) == _name(adapter.get("name"))
                       and memory and d.get("memory_total_bytes")
                       and abs(memory - d["memory_total_bytes"]) <= max(64 * 2**20, memory * 0.05)]
            result["basis"] = "unique_vendor_name_memory"
            peers = [a for a in selection.get("adapters_detected", [])
                     if a.get("classification") not in {"software", "virtual"}
                     and a.get("vendor") == adapter.get("vendor") and _name(a.get("name")) == _name(adapter.get("name"))]
        if len(peers) != 1 and matches:
            result.update(status="ambiguous", reason="runtime_device_mapping_ambiguous")
            return result
    if len(matches) > 1:
        result.update(status="ambiguous", reason="runtime_device_mapping_ambiguous")
    elif len(matches) == 1:
        result.update(status="mapped", reason="mapped", runtime_device=matches[0])
    return result


def bind_quality_selection(selection: dict, enumeration: dict | None = None) -> dict:
    result = dict(selection)
    if not result.get("available"):
        return result
    enumeration = enumeration if enumeration is not None else enumerate_runtime_devices(result["runtime_id"])
    binding = map_adapter_device(result, enumeration)
    result.update(runtime_device_enumeration=enumeration, device_binding=binding)
    if binding["status"] != "mapped":
        result.update(available=False, reason=binding["reason"], selection_status="unavailable")
    return result


def vulkan_cache_identity(device: dict) -> str | None:
    keys = ("vendor_id", "device_id", "driver_version", "api_version", "pipeline_cache_uuid")
    if any(device.get(key) is None for key in keys):
        return None
    return (f"s2-v{device['vendor_id']:x}-d{device['device_id']:x}-r{device['driver_version']:x}"
            f"-a{device['api_version']:x}-{device['pipeline_cache_uuid']}")


class _Caps(C.Structure):
    _fields_ = [(key, C.c_bool) for key in ("async", "host_buffer", "buffer_from_host_ptr", "events")]


class _Props(C.Structure):
    _fields_ = [("name", C.c_char_p), ("description", C.c_char_p), ("free", C.c_size_t),
                ("total", C.c_size_t), ("type", C.c_int), ("device_id", C.c_char_p), ("caps", _Caps)]


def _function(dlls, name, args, result):
    for dll in dlls:
        try:
            fn = getattr(dll, name)
            fn.argtypes, fn.restype = args, result
            return fn
        except AttributeError:
            pass
    raise RuntimeError("native_symbol_missing:" + name)


def _decode(value):
    return value.decode("utf-8", errors="replace") if value else None


def _luid(raw):
    low, high = struct.unpack("<II", bytes(raw))
    return f"{high:08x}:{low:08x}" if high or low else None


def _cuda_identity(devices):
    driver = C.WinDLL(str(Path(os.environ["SystemRoot"]) / "System32/nvcuda.dll"))
    if _function([driver], "cuInit", [C.c_uint], C.c_int)(0) != 0:
        raise RuntimeError("cuda_driver_initialization_failed")
    pci = _function([driver], "cuDeviceGetPCIBusId", [C.c_void_p, C.c_int, C.c_int], C.c_int)
    luid = _function([driver], "cuDeviceGetLuid", [C.c_void_p, C.POINTER(C.c_uint), C.c_int], C.c_int)
    count = C.c_int()
    if _function([driver], "cuDeviceGetCount", [C.POINTER(C.c_int)], C.c_int)(C.byref(count)) != 0:
        raise RuntimeError("cuda_device_enumeration_failed")
    identities = {}
    for index in range(count.value):
        bus, value, mask = C.create_string_buffer(32), C.create_string_buffer(8), C.c_uint()
        if pci(bus, len(bus), index) == 0:
            identities[bus.value.decode().lower()] = {
                "luid": _luid(value.raw) if luid(value, C.byref(mask), index) == 0 else None,
                "vendor_id": 0x10DE, "vendor": "NVIDIA", "node_mask": mask.value,
            }
    for device in devices:
        device.update(identities.get(device["pci_bus_id"], {}))


class _VkID(C.Structure):
    _fields_ = [("sType", C.c_uint32), ("pNext", C.c_void_p), ("deviceUUID", C.c_ubyte * 16),
                ("driverUUID", C.c_ubyte * 16), ("deviceLUID", C.c_ubyte * 8),
                ("deviceNodeMask", C.c_uint32), ("deviceLUIDValid", C.c_uint32)]


class _VkPCI(C.Structure):
    _fields_ = [("sType", C.c_uint32), ("pNext", C.c_void_p)] + [
        (key, C.c_uint32) for key in ("domain", "bus", "device", "function")]


class _VkProperties(C.Structure):
    # The native API writes its full VkPhysicalDeviceProperties into this oversized aligned storage.
    _fields_ = [("sType", C.c_uint32), ("pNext", C.c_void_p), ("properties", C.c_uint64 * 256)]


class _VkApp(C.Structure):
    _fields_ = [("sType", C.c_uint32), ("pNext", C.c_void_p), ("name", C.c_char_p),
                ("version", C.c_uint32), ("engine", C.c_char_p), ("engineVersion", C.c_uint32),
                ("apiVersion", C.c_uint32)]


class _VkCreate(C.Structure):
    _fields_ = [("sType", C.c_uint32), ("pNext", C.c_void_p), ("flags", C.c_uint32),
                ("app", C.POINTER(_VkApp)), ("layers", C.c_uint32), ("layerNames", C.c_void_p),
                ("extensions", C.c_uint32), ("extensionNames", C.c_void_p)]


class _VkExtension(C.Structure):
    _fields_ = [("name", C.c_char * 256), ("version", C.c_uint32)]


class _VkMemoryType(C.Structure):
    _fields_ = [("flags", C.c_uint32), ("heap", C.c_uint32)]


class _VkMemoryHeap(C.Structure):
    _fields_ = [("size", C.c_uint64), ("flags", C.c_uint32)]


class _VkMemory(C.Structure):
    _fields_ = [("type_count", C.c_uint32), ("types", _VkMemoryType * 32),
                ("heap_count", C.c_uint32), ("heaps", _VkMemoryHeap * 16)]


def _vulkan_identities():
    loader = C.WinDLL(str(Path(os.environ["SystemRoot"]) / "System32/vulkan-1.dll"))
    app = _VkApp(sType=0, name=b"Games_translator_device_probe", apiVersion=(1 << 22) | (2 << 12))
    info, instance = _VkCreate(sType=1, app=C.pointer(app)), C.c_void_p()
    create = _function([loader], "vkCreateInstance", [C.POINTER(_VkCreate), C.c_void_p, C.POINTER(C.c_void_p)], C.c_int)
    if create(C.byref(info), None, C.byref(instance)) != 0:
        raise RuntimeError("vulkan_instance_creation_failed")
    destroy = _function([loader], "vkDestroyInstance", [C.c_void_p, C.c_void_p], None)
    try:
        enum = _function([loader], "vkEnumeratePhysicalDevices", [C.c_void_p, C.POINTER(C.c_uint32), C.c_void_p], C.c_int)
        count = C.c_uint32()
        if enum(instance, C.byref(count), None) != 0 or count.value > 256:
            raise RuntimeError("vulkan_physical_enumeration_failed")
        handles = (C.c_void_p * count.value)()
        if enum(instance, C.byref(count), handles) != 0:
            raise RuntimeError("vulkan_physical_enumeration_failed")
        props_fn = _function([loader], "vkGetPhysicalDeviceProperties2", [C.c_void_p, C.POINTER(_VkProperties)], None)
        extensions = _function([loader], "vkEnumerateDeviceExtensionProperties", [
            C.c_void_p, C.c_char_p, C.POINTER(C.c_uint32), C.POINTER(_VkExtension)], C.c_int)
        memory_fn = _function([loader], "vkGetPhysicalDeviceMemoryProperties", [C.c_void_p, C.POINTER(_VkMemory)], None)
        rows = []
        for handle in handles:
            identity = _VkID(sType=1000071004)
            pci = _VkPCI(sType=1000212000)
            extension_count = C.c_uint32()
            if extensions(handle, None, C.byref(extension_count), None) != 0:
                raise RuntimeError("vulkan_extension_enumeration_failed")
            available = (_VkExtension * extension_count.value)()
            if extensions(handle, None, C.byref(extension_count), available) != 0:
                raise RuntimeError("vulkan_extension_enumeration_failed")
            if any(ext.name == b"VK_EXT_pci_bus_info" for ext in available):
                identity.pNext = C.cast(C.pointer(pci), C.c_void_p)
            props = _VkProperties(sType=1000059001, pNext=C.cast(C.pointer(identity), C.c_void_p))
            props_fn(handle, C.byref(props))
            raw = bytes(props.properties)
            api, driver, vendor, chip, kind = struct.unpack_from("<IIIII", raw)
            memory = _VkMemory()
            memory_fn(handle, C.byref(memory))
            if memory.heap_count > 16:
                raise RuntimeError("vulkan_invalid_memory_properties")
            memory_total = sum(memory.heaps[i].size for i in range(memory.heap_count)
                               if kind == 1 or memory.heaps[i].flags & 1)
            rows.append({"name": raw[20:276].split(b"\0", 1)[0].decode("utf-8", errors="replace"),
                         "vendor_id": vendor, "device_id": chip,
                         "vendor": {0x10DE: "NVIDIA", 0x1002: "AMD", 0x8086: "Intel"}.get(vendor, "other"),
                         "luid": _luid(identity.deviceLUID) if identity.deviceLUIDValid else None,
                         "device_uuid": bytes(identity.deviceUUID).hex(),
                         "pipeline_cache_uuid": raw[276:292].hex(), "driver_version": driver,
                         "api_version": api, "physical_device_type": kind,
                         "physical_memory_total_bytes": memory_total,
                         "pci_bus_id": f"{pci.domain:04x}:{pci.bus:02x}:{pci.device:02x}.{pci.function:x}" if any(
                             (pci.domain, pci.bus, pci.device, pci.function)) else None})
        return rows
    finally:
        destroy(instance, None)


def native_devices(runtime_id):
    root = resource_path(runtime_id)
    backend = RESOURCES[runtime_id].runtime.backend
    with os.add_dll_directory(str(root)):
        dlls = [C.CDLL(str(root / name)) for name in ("ggml-base.dll", "ggml.dll")]
        # GGML uses LoadLibraryW, whose dependency search does not use ctypes' safe flags.
        backend_dll = C.CDLL(str(root / f"ggml-{backend}.dll"))
        reg = _function(dlls, "ggml_backend_load", [C.c_char_p], C.c_void_p)(
            str(root / f"ggml-{backend}.dll").encode("utf-8"))
        if not reg:
            raise RuntimeError("ggml_backend_registration_failed")
        count = _function(dlls, "ggml_backend_reg_dev_count", [C.c_void_p], C.c_size_t)(reg)
        get = _function(dlls, "ggml_backend_reg_dev_get", [C.c_void_p, C.c_size_t], C.c_void_p)
        props_fn = _function(dlls, "ggml_backend_dev_get_props", [C.c_void_p, C.POINTER(_Props)], None)
        devices = []
        for index in range(count):
            props = _Props()
            props_fn(get(reg, index), C.byref(props))
            devices.append({"runtime_device_id": _decode(props.name), "backend": backend,
                            "name": _decode(props.description), "memory_total_bytes": props.total,
                            "memory_free_bytes": props.free, "device_type": "gpu" if props.type == 1 else "other",
                            "native_device_type": props.type,
                            "pci_bus_id": _decode(props.device_id), "capabilities": {
                                key: bool(getattr(props.caps, key)) for key, _ in _Caps._fields_}})
        if backend == "cuda":
            _cuda_identity(devices)
        elif backend == "vulkan":
            native = _vulkan_identities()
            for device in devices:
                matches = [row for row in native if device["pci_bus_id"] and row["pci_bus_id"] == device["pci_bus_id"]]
                if not device["pci_bus_id"]:
                    matches = [row for row in native if _name(row["name"]) == _name(device["name"])
                               and row["physical_memory_total_bytes"] == device["memory_total_bytes"]
                               and row["physical_device_type"] == (2 if device["device_type"] == "gpu" else 1)]
                if len(matches) == 1:
                    device.update(matches[0])
                else:
                    if matches and len({row["vendor"] for row in matches}) == 1:
                        device["vendor"] = matches[0]["vendor"]
                    device["identity_enrichment_error"] = "ambiguous_vulkan_native_identity"
        return {"runtime_id": runtime_id, "status": "ok", "devices": devices,
                "source": "pinned_ggml_backend_dev_get_props + native Windows LUID APIs"}


if __name__ == "__main__":
    print(json.dumps(native_devices(sys.argv[2]), ensure_ascii=False))
