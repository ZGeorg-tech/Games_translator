"""Canonical app-owned resource paths. Lookup never loads or creates resources."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType


APP_ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Resource:
    relative_path: str
    owner: str
    consumers: tuple[str, ...]
    runtime: RuntimeDefinition | None = None


@dataclass(frozen=True)
class RuntimeDefinition:
    runtime_id: str
    backend: str
    supported_vendors: tuple[str, ...]
    qualified_vendors: tuple[str, ...]
    platform: str
    architecture: str
    build: str | None
    model_resource_ids: tuple[str, ...]
    declared_capabilities: tuple[str, ...]
    required_files: tuple[str, ...]
    manifest_complete: bool
    qualification_evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not set(self.qualified_vendors).issubset(self.supported_vendors):
            raise ValueError("qualified_vendors must be supported_vendors")


@dataclass(frozen=True)
class RuntimeAvailability:
    definition: RuntimeDefinition
    path: Path
    resource_root_exists: bool
    installed: bool
    missing_files: tuple[str, ...]
    structurally_ready: bool


_LLAMA_MODELS = ("translategemma.q6", "milmmt.q6")
_CUDA_LLAMA_FILES = (
    "llama-server.exe", "llama-server-impl.dll", "llama-common.dll", "llama.dll",
    "ggml.dll", "ggml-base.dll", "ggml-cuda.dll", "cudart64_13.dll",
    "cublas64_13.dll", "cublasLt64_13.dll",
)
_VULKAN_LLAMA_FILES = (
    "llama-server.exe", "llama-server-impl.dll", "llama-common.dll", "llama.dll",
    "ggml.dll", "ggml-base.dll", "ggml-vulkan.dll", "mtmd.dll", "libomp140.x86_64.dll",
    "msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll",
    "ggml-cpu-alderlake.dll", "ggml-cpu-cannonlake.dll", "ggml-cpu-cascadelake.dll",
    "ggml-cpu-cooperlake.dll", "ggml-cpu-haswell.dll", "ggml-cpu-icelake.dll",
    "ggml-cpu-ivybridge.dll", "ggml-cpu-piledriver.dll", "ggml-cpu-sandybridge.dll",
    "ggml-cpu-sapphirerapids.dll", "ggml-cpu-skylakex.dll", "ggml-cpu-sse42.dll",
    "ggml-cpu-x64.dll", "ggml-cpu-zen4.dll",
)
_VULKAN_PCACHE_FILES = (
    "llama-server.exe", "llama-server-impl.dll", "llama-common.dll", "llama.dll",
    "ggml.dll", "ggml-base.dll", "ggml-vulkan.dll", "ggml-cpu.dll", "mtmd.dll",
    "msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll", "vcomp140.dll",
    "LICENSE", "runtime_manifest.json",
)
_CUDA_LLAMA_RESOURCE = Resource(
    "resources/shared/runtimes/llama-cpp/cuda/b10057", "shared", ("block1", "block2", "block3"),
    RuntimeDefinition("llama.runtime.cuda", "cuda", ("NVIDIA",), ("NVIDIA",), "win32", "x86_64", "b10057",
                      _LLAMA_MODELS, ("gguf", "gpu_offload"), _CUDA_LLAMA_FILES, True,
                      ("M07 shared native runtime smoke", "Q6/MiLMMT production preflight and smoke")),
)


RESOURCES = MappingProxyType({
    "app.python": Resource("resources/shared/runtimes/python/3.13.5", "shared", ("core", "block3")),
    "app.environment": Resource("resources/shared/packages/core/py313-20260925", "shared", ("core", "block3")),
    "app.assets": Resource("assets", "shared", ("core",)),
    "tesseract.runtime": Resource("resources/shared/ocr/tesseract-runtime/5.5.0.20241111", "shared", ("block1", "block3")),
    "tesseract.tessdata": Resource("resources/shared/ocr/tesseract-eng/5.5.0.20241111", "shared", ("block1", "block3")),
    "translation.models": Resource("models/translation", "shared", ("block1", "block2", "block3")),
    "bergamot.node": Resource("resources/shared/runtimes/node/24.18.0/node.exe", "shared", ("block1", "block2")),
    "bergamot.runtime": Resource("resources/shared/runtimes/bergamot/0.4.9", "shared", ("block1", "block2")),
    "bergamot.enru": Resource("resources/shared/models/bergamot-en-ru/base-memory", "shared", ("block1", "block2")),
    "bergamot.enuk": Resource("resources/shared/models/bergamot-en-uk/base-memory", "shared", ("block1", "block2")),
    "bergamot.jaen": Resource("resources/block2/models/bergamot-ja-en/cjk_icu_base_U4VUAW3STh-bF0Sr-dX69g", "block2", ("block2",)),
    "bergamot.zhen": Resource("resources/block2/models/bergamot-zh-en/cjk_icu_base_LQeOIbF7Sbq3XA8lsRPotw", "block2", ("block2",)),
    "bergamot.zh_hant_en": Resource("resources/block2/models/bergamot-zht-en/zh_hant_openlid_zh_tw_lr0002_WJi5Ozi7SZWC6hgfD5GhTA", "block2", ("block2",)),
    "bergamot.wasm": Resource("models/translation/bergamot_wasm", "shared", ("block1", "block2")),
    "quality.bergamot": Resource("translation_quality/packs/block1", "shared", ("block1", "block2")),
    "quality.block2_fast": Resource("translation_quality/packs/block2_fast", "block2", ("block2",)),
    "quality.block2_quality": Resource("translation_quality/packs/block2_quality", "block2", ("block2",)),
    "llama.runtime": _CUDA_LLAMA_RESOURCE,  # Compatibility ID used by current Q6/MiLMMT backends.
    "llama.runtime.cuda": _CUDA_LLAMA_RESOURCE,
    "llama.runtime.vulkan": Resource(
        "resources/shared/runtimes/llama-cpp/vulkan/b10057", "shared", ("block1", "block2", "block3"),
        RuntimeDefinition("llama.runtime.vulkan", "vulkan", ("NVIDIA", "AMD", "Intel"), ("NVIDIA",),
                          "win32", "x86_64", "b10057", _LLAMA_MODELS, ("gguf", "gpu_offload"),
                          _VULKAN_LLAMA_FILES, True,
                          ("M23 b10057 native closure and Q6/MiLMMT inference on NVIDIA RTX 3060",
                           "tools/gpu_portability_m23/results/M23_REPORT.md",
                           "AMD/Intel devices, driver variance and game VRAM contention unqualified")),
    ),
    "llama.runtime.hip": Resource(
        "resources/shared/runtimes/llama-cpp/hip", "shared", ("block1", "block2", "block3"),
        RuntimeDefinition("llama.runtime.hip", "hip", ("AMD",), (),
                          "win32", "x86_64", None, _LLAMA_MODELS, ("gguf", "gpu_offload"),
                          ("llama-server.exe",), False),
    ),
    "llama.runtime.vulkan.pcache1": Resource(
        "resources/shared/runtimes/llama-cpp/vulkan/b10057-pcache1", "shared", ("block1", "block2", "block3"),
        RuntimeDefinition("llama.runtime.vulkan.pcache1", "vulkan", ("NVIDIA", "AMD", "Intel"), ("NVIDIA",),
                          "win32", "x86_64", "b10057-pcache1", _LLAMA_MODELS,
                          ("gguf", "gpu_offload", "persistent_pipeline_cache"), _VULKAN_PCACHE_FILES, True,
                          ("M25 controlled build: Q6 Low/Speed and MiLMMT, NVIDIA RTX 3060",
                           "tools/gpu_portability_m25/results/patched_vulkan_qualification.json",
                           "tools/gpu_portability_m25/results/shared_cache_production_validation.json",
                           "AMD/Intel and other device/driver combinations not measured")),
    ),
    "translategemma.q6": Resource("resources/block1/models/translategemma-q6/4b-it-Q6_K/translategemma-4b-it-Q6_K.gguf", "block1", ("block1",)),
    "translategemma.chat_template": Resource("resources/block1/models/translategemma-q6/4b-it-Q6_K/chat_template.jinja", "block1", ("block1",)),
    "milmmt.q6": Resource("resources/shared/models/milmmt-q6/4b-v0.1-Q6_K/MiLMMT-46-4B-Q6_K.gguf", "shared", ("block2", "block3")),
    "csanmt.runtime": Resource("resources/block3/packages/csanmt/py313-tf2.20.0-ms1.37.1", "block3", ("block3",)),
    "csanmt.model": Resource("resources/block3/models/csanmt-en-zh/en2zh_base", "block3", ("block3",)),
    "csanmt.worker": Resource("translation_workers/csanmt_worker.py", "block3", ("block3",)),
    "fugu.model": Resource("resources/block3/models/fugu-en-ja/fugumt_en_ja", "block3", ("block3",)),
    "fugu.worker": Resource("translation_workers/fugu_worker.py", "block3", ("block3",)),
    "opencc.runtime": Resource("resources/block3/runtimes/opencc-s2t/1.4.1", "block3", ("block3",)),
    "marian.enru": Resource("resources/block1/models/marian-en-ru/bb09c99d180016eac6819df3dae68edb1690fdee", "block1", ("block1",)),
    "paddle.fast_det": Resource("resources/shared/ocr/paddle-en-fast/v4-mobile/PP-OCRv4_mobile_det", "shared", ("block1", "block3")),
    "paddle.fast_rec": Resource("resources/shared/ocr/paddle-en-fast/v4-mobile/en_PP-OCRv4_mobile_rec", "shared", ("block1", "block3")),
    "paddle.full_det": Resource("resources/shared/ocr/paddle-full/v6-medium/PP-OCRv6_medium_det", "shared", ("block1", "block2", "block3")),
    "paddle.full_rec": Resource("resources/shared/ocr/paddle-full/v6-medium/PP-OCRv6_medium_rec", "shared", ("block1", "block2", "block3")),
    "paddle.block2_det": Resource("resources/block2/ocr/paddle-cjk-fast/v6-small/PP-OCRv6_small_det", "block2", ("block2",)),
    "paddle.block2_rec": Resource("resources/block2/ocr/paddle-cjk-fast/v6-small/PP-OCRv6_small_rec", "block2", ("block2",)),
})


class ResourceRegistry:
    def __init__(self, app_root: Path = APP_ROOT):
        self.app_root = Path(app_root)

    def path(self, resource_id: str) -> Path:
        return self.app_root / RESOURCES[resource_id].relative_path

    def exists(self, resource_id: str) -> bool:
        """Check only the requested path; backend-specific readiness stays local."""
        return self.path(resource_id).exists()

    def runtime_definitions(self) -> tuple[RuntimeDefinition, ...]:
        """Known variants, including uninstalled ones; no filesystem access."""
        return tuple(resource.runtime for resource_id, resource in RESOURCES.items()
                     if resource.runtime is not None and resource_id == resource.runtime.runtime_id)

    def runtime_status(self, runtime_id: str) -> RuntimeAvailability:
        resource = RESOURCES[runtime_id]
        definition = resource.runtime
        if definition is None or runtime_id != definition.runtime_id:
            raise KeyError(runtime_id)
        path = self.path(runtime_id)
        root_exists = path.is_dir()
        missing = tuple(name for name in definition.required_files if not (path / name).is_file())
        installed = (path / "llama-server.exe").is_file()
        return RuntimeAvailability(definition, path, root_exists, installed, missing,
                                   installed and definition.manifest_complete and not missing)

    def installed_runtime_variants(self) -> tuple[RuntimeAvailability, ...]:
        return tuple(status for definition in self.runtime_definitions()
                     if (status := self.runtime_status(definition.runtime_id)).installed)

    def runtime_compatible_vendor(self, runtime_id: str, vendor: str) -> bool:
        """Declared vendor capability, not device readiness or measured performance."""
        definition = RESOURCES[runtime_id].runtime
        if definition is None or runtime_id != definition.runtime_id:
            raise KeyError(runtime_id)
        return vendor in definition.supported_vendors

    def runtime_qualified_for_vendor(self, runtime_id: str, vendor: str) -> bool:
        """Qualification scope only; this does not check installation or adapter hardware."""
        definition = RESOURCES[runtime_id].runtime
        if definition is None or runtime_id != definition.runtime_id:
            raise KeyError(runtime_id)
        return vendor in definition.qualified_vendors

    def runtime_qualification_probe_eligible(self, runtime_id: str, vendor: str,
                                             adapter_classification: str) -> bool:
        """Explicit probe of a declared capability on a physical adapter."""
        status = self.runtime_status(runtime_id)
        return (vendor in status.definition.supported_vendors
                and adapter_classification in {"discrete", "integrated", "physical_unknown"}
                and status.installed and status.structurally_ready)

    def runtime_selection_eligible(self, runtime_id: str, vendor: str,
                                   adapter_classification: str) -> bool:
        """Vendor-scoped production gate; no automatic runtime selection is performed."""
        return (self.runtime_qualification_probe_eligible(runtime_id, vendor, adapter_classification)
                and self.runtime_qualified_for_vendor(runtime_id, vendor))


REGISTRY = ResourceRegistry()
resource_path = REGISTRY.path
