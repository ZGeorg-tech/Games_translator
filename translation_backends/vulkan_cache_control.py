"""DataPaths namespace and bounded flush handshake for patched Vulkan workers."""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import uuid
from data_paths import DATA, DataPaths


class VulkanCacheControl:
    def __init__(self, runtime_build: str, paths: DataPaths = DATA):
        self.root = paths.vulkan_pipeline_cache(runtime_build)
        self.root.mkdir(parents=True, exist_ok=True)
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel.CreateEventW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
        self._kernel.CreateEventW.restype = wintypes.HANDLE
        for name in ("SetEvent", "ResetEvent", "CloseHandle"):
            function = getattr(self._kernel, name)
            function.argtypes = (wintypes.HANDLE,)
            function.restype = wintypes.BOOL
        self._kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        self._kernel.WaitForSingleObject.restype = wintypes.DWORD
        namespace = "Local\\GamesTranslator.VkPcache." + uuid.uuid4().hex
        self._request_name, self._done_name = namespace + ".request", namespace + ".done"
        self._request = self._kernel.CreateEventW(None, True, False, self._request_name)
        self._done = self._kernel.CreateEventW(None, True, False, self._done_name)
        if not self._request or not self._done:
            error = ctypes.get_last_error()
            self.close()
            raise OSError(error, "Vulkan cache flush event creation failed")

    def environment(self) -> dict[str, str]:
        return {"GGML_VK_PIPELINE_CACHE_ROOT": str(self.root),
                "GGML_VK_CACHE_FLUSH_EVENT": self._request_name,
                "GGML_VK_CACHE_FLUSH_DONE_EVENT": self._done_name}

    def flush(self, timeout_ms: int = 5000) -> bool:
        if not self._request or not self._done:
            return False
        if not self._kernel.ResetEvent(self._done) or not self._kernel.SetEvent(self._request):
            return False
        return self._kernel.WaitForSingleObject(self._done, timeout_ms) == 0

    def close(self) -> None:
        for name in ("_request", "_done"):
            handle = getattr(self, name, None)
            if handle:
                self._kernel.CloseHandle(handle)
                setattr(self, name, None)
