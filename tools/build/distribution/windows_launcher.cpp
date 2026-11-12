#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <string>
#include <vector>

namespace {
int fail(const std::wstring& message, DWORD error) {
    const auto details = message + L"\n\nCode: " + std::to_wstring(error);
    MessageBoxW(nullptr, details.c_str(), L"Games_translator", MB_OK | MB_ICONERROR);
    return 1;
}
}

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    std::vector<wchar_t> module(32768);
    const DWORD length = GetModuleFileNameW(nullptr, module.data(), static_cast<DWORD>(module.size()));
    if (length == 0 || length >= module.size()) {
        return fail(L"Could not locate the Games_translator installation.", GetLastError());
    }
    const std::wstring executable(module.data(), length);
    const auto root = executable.substr(0, executable.find_last_of(L"\\/"));
    const auto python = root + L"\\resources\\shared\\runtimes\\python\\3.13.5\\python.exe";
    auto command = L"\"" + python + L"\" -I -B -X utf8 python_runtime.py launcher.py";

    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION child{};
    if (!CreateProcessW(python.c_str(), command.data(), nullptr, nullptr, FALSE,
                        CREATE_NO_WINDOW, nullptr, root.c_str(), &startup, &child)) {
        return fail(L"Could not start the bundled Python. Extract all release parts into the same folder.",
                    GetLastError());
    }
    CloseHandle(child.hThread);
    DWORD exit_code = 0;
    const bool exited = WaitForSingleObject(child.hProcess, INFINITE) == WAIT_OBJECT_0
                        && GetExitCodeProcess(child.hProcess, &exit_code);
    const DWORD error = exited ? exit_code : GetLastError();
    CloseHandle(child.hProcess);
    if (!exited || exit_code != 0) {
        return fail(L"The launcher stopped unexpectedly. Check that the application files are complete.",
                    error);
    }
    return 0;
}
