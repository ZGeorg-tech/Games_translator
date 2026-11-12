"""Session-only Quality LLM runtime policy over detected hardware and Registry facts."""

from __future__ import annotations

from dataclasses import asdict
import json
from datetime import datetime, timezone

from data_paths import data_path
from hardware_detection import HardwareSnapshot, enumerate_adapters
from resource_registry import REGISTRY


CUDA = "llama.runtime.cuda"
AMD_VULKAN = "llama.runtime.vulkan.pcache1"
_REASONS_RU = {
    "no_physical_gpu": "физическая видеокарта не обнаружена",
    "only_integrated_gpu": "обнаружена только встроенная видеокарта",
    "no_classified_discrete_gpu": "дискретная видеокарта не подтверждена",
    "unsupported_vendor": "для этой видеокарты Quality пока не поддерживается",
    "required_runtime_not_installed": "не установлен нужный runtime Quality",
    "runtime_structurally_unavailable": "runtime Quality неполон или недоступен",
    "runtime_not_qualified": "runtime не квалифицирован для видеокарты",
    "session_hardware_selection_missing": "в сессии отсутствует выбор видеокарты",
    "runtime_or_model_readiness_failure": "runtime или модель Quality не прошли проверку готовности",
    "runtime_device_not_visible": "выбранная видеокарта не видна нужному runtime",
    "runtime_device_mapping_ambiguous": "не удалось однозначно связать выбранную видеокарту с runtime",
    "runtime_device_enumeration_failed": "не удалось перечислить устройства runtime Quality",
}


def quality_unavailable_message(selection: dict) -> str:
    reason = str(selection.get("reason") or "runtime_or_model_readiness_failure")
    return "Quality недоступен: " + _REASONS_RU.get(reason, reason) + ". Fast остаётся доступен."


def select_quality_runtime(snapshot: HardwareSnapshot, registry=REGISTRY) -> dict:
    candidates = snapshot.preferred_candidates()
    discrete = [adapter for adapter in candidates if adapter.classification == "discrete"]
    chosen = discrete[0] if discrete else None
    result = {
        "hardware_status": snapshot.status,
        "adapters_detected": [asdict(adapter) for adapter in snapshot.adapters],
        "hardware_diagnostics": list(snapshot.diagnostics),
        "preference_source": snapshot.preference_source,
        "selected_adapter": asdict(chosen) if chosen else None,
        "vendor": chosen.vendor if chosen else None,
        "classification": chosen.classification if chosen else None,
        "runtime_id": None,
        "selection_status": "unavailable",
        "selection_basis": "M21_preferred_discrete_then_vendor_policy",
        "available": False,
        "reason": "",
        "runtime_availability": None,
        "readiness_result": "not_started",
        "readiness_error": None,
    }
    if not chosen:
        result["reason"] = (
            "no_physical_gpu" if not candidates else
            "only_integrated_gpu" if all(a.classification == "integrated" for a in candidates) else
            "no_classified_discrete_gpu"
        )
        return result
    if chosen.vendor == "NVIDIA":
        runtime_id = CUDA
    elif chosen.vendor == "AMD":
        runtime_id = AMD_VULKAN
    else:
        result["reason"] = "unsupported_vendor"
        return result
    result["runtime_id"] = runtime_id
    status = registry.runtime_status(runtime_id)
    result["runtime_availability"] = {
        "installed": status.installed,
        "structurally_ready": status.structurally_ready,
        "missing_files": list(status.missing_files),
    }
    if not registry.runtime_compatible_vendor(runtime_id, chosen.vendor):
        result["reason"] = "unsupported_vendor"
    elif not status.installed:
        result["reason"] = "required_runtime_not_installed"
    elif not status.structurally_ready:
        result["reason"] = "runtime_structurally_unavailable"
    elif chosen.vendor == "NVIDIA" and not registry.runtime_selection_eligible(
        runtime_id, chosen.vendor, chosen.classification
    ):
        result["reason"] = "runtime_not_qualified"
    elif chosen.vendor == "AMD" and not registry.runtime_qualification_probe_eligible(
        runtime_id, chosen.vendor, chosen.classification
    ):
        result["reason"] = "runtime_structurally_unavailable"
    else:
        result["available"] = True
        result["selection_status"] = (
            "qualified" if registry.runtime_qualified_for_vendor(runtime_id, chosen.vendor) else "provisional"
        )
        result["reason"] = "selected"
    return result


def detect_quality_runtime() -> dict:
    from runtime_devices import bind_quality_selection
    return bind_quality_selection(select_quality_runtime(enumerate_adapters()))


def log_quality_runtime_selection(selection: dict, event: str) -> None:
    """Persistent diagnostics stay under DATA_ROOT; no profile hardware truth."""
    from session_logging import open_log, raw_enabled, runtime_selected
    runtime_selected(selection)
    if not raw_enabled():
        return
    path = data_path("diagnostics", "quality_runtime_selection.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"timestamp": datetime.now(timezone.utc).isoformat(), "event": event, **selection}
    with open_log(path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
