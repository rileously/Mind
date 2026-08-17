// "Send to Telegram" for the Windows 11 compact context menu.
//
// The compact menu only shows commands from a package with identity, reached
// through IExplorerCommand; plain registry verbs are pushed under "Show more
// options". A COM object cannot be written in Python because the shell loads it
// in process, so this is the one piece of Mind that has to be compiled.
//
// The work itself is still Mind's: Invoke starts Mind.exe with --telegram-send,
// one process per file, exactly as the registry verb does. Nothing about
// Telegram lives here, so this file does not change when the sending does.

#include <windows.h>
#include <shlobj_core.h>
#include <shlwapi.h>
#include <strsafe.h>

#include <new>
#include <string>

#pragma comment(lib, "shlwapi.lib")
#pragma comment(lib, "ole32.lib")

// {54804E3A-9FD3-4AE0-B0F5-BC7A113F16C9} — also named in AppxManifest.xml, and
// the two have to stay in step or Explorer finds nothing to load.
static const CLSID CLSID_MindSendToTelegram = {
    0x54804e3a, 0x9fd3, 0x4ae0, {0xb0, 0xf5, 0xbc, 0x7a, 0x11, 0x3f, 0x16, 0xc9}};

static const wchar_t kMenuLabel[] = L"Send to Telegram";
static const wchar_t kExecutableName[] = L"Mind.exe";
static const wchar_t kSendArgument[] = L"--telegram-send";

static HINSTANCE g_module = nullptr;
static LONG g_objects = 0;

static std::wstring ModuleDirectory() {
    wchar_t path[MAX_PATH] = {};
    if (GetModuleFileNameW(g_module, path, ARRAYSIZE(path)) == 0) {
        return std::wstring();
    }
    PathRemoveFileSpecW(path);
    return std::wstring(path);
}

// The handler ships beside Mind.exe, but a build that puts it in a subfolder
// should keep working rather than silently offering a command that does nothing.
static std::wstring FindExecutable() {
    std::wstring directory = ModuleDirectory();
    if (directory.empty()) {
        return std::wstring();
    }
    for (int level = 0; level < 2; ++level) {
        std::wstring candidate = directory + L"\\" + kExecutableName;
        if (PathFileExistsW(candidate.c_str())) {
            return candidate;
        }
        wchar_t parent[MAX_PATH] = {};
        if (FAILED(StringCchCopyW(parent, ARRAYSIZE(parent), directory.c_str()))) {
            break;
        }
        PathRemoveFileSpecW(parent);
        directory.assign(parent);
    }
    return std::wstring();
}

static bool StartSend(const std::wstring& executable, const std::wstring& path) {
    // CreateProcessW may modify the command line, so it cannot be a literal.
    std::wstring command;
    command.reserve(executable.size() + path.size() + 32);
    command.append(L"\"").append(executable).append(L"\" ");
    command.append(kSendArgument).append(L" \"").append(path).append(L"\"");

    STARTUPINFOW startup = {};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION process = {};
    if (!CreateProcessW(executable.c_str(), &command[0], nullptr, nullptr, FALSE, 0,
                        nullptr, nullptr, &startup, &process)) {
        return false;
    }
    // Nothing here waits on the send: the surrogate host this runs in should not
    // be held open, and Mind reports its own failures.
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return true;
}

class SendToTelegramCommand : public IExplorerCommand {
public:
    SendToTelegramCommand() : references_(1) { InterlockedIncrement(&g_objects); }

    // IUnknown
    IFACEMETHODIMP QueryInterface(REFIID riid, void** result) override {
        if (result == nullptr) {
            return E_POINTER;
        }
        if (riid == IID_IUnknown || riid == IID_IExplorerCommand) {
            *result = static_cast<IExplorerCommand*>(this);
            AddRef();
            return S_OK;
        }
        *result = nullptr;
        return E_NOINTERFACE;
    }

    IFACEMETHODIMP_(ULONG) AddRef() override { return InterlockedIncrement(&references_); }

    IFACEMETHODIMP_(ULONG) Release() override {
        const LONG remaining = InterlockedDecrement(&references_);
        if (remaining == 0) {
            delete this;
        }
        return remaining;
    }

    // IExplorerCommand
    IFACEMETHODIMP GetTitle(IShellItemArray*, PWSTR* name) override {
        return SHStrDupW(kMenuLabel, name);
    }

    IFACEMETHODIMP GetIcon(IShellItemArray*, PWSTR* icon) override {
        const std::wstring executable = FindExecutable();
        if (executable.empty()) {
            *icon = nullptr;
            return E_NOTIMPL;
        }
        // Mind's own icon, so the entry is recognisable rather than blank.
        const std::wstring resource = executable + L",0";
        return SHStrDupW(resource.c_str(), icon);
    }

    IFACEMETHODIMP GetToolTip(IShellItemArray*, PWSTR* tip) override {
        *tip = nullptr;
        return E_NOTIMPL;
    }

    IFACEMETHODIMP GetCanonicalName(GUID* name) override {
        *name = GUID_NULL;
        return S_OK;
    }

    IFACEMETHODIMP GetState(IShellItemArray* items, BOOL, EXPCMDSTATE* state) override {
        // Hidden rather than greyed out when there is nothing to send to: a dead
        // entry in the menu is worse than no entry at all.
        *state = ECS_HIDDEN;
        if (items == nullptr || FindExecutable().empty()) {
            return S_OK;
        }
        DWORD count = 0;
        if (FAILED(items->GetCount(&count)) || count == 0) {
            return S_OK;
        }
        for (DWORD index = 0; index < count; ++index) {
            IShellItem* item = nullptr;
            if (FAILED(items->GetItemAt(index, &item)) || item == nullptr) {
                continue;
            }
            SFGAOF attributes = 0;
            const bool is_file =
                SUCCEEDED(item->GetAttributes(SFGAO_FOLDER | SFGAO_FILESYSTEM, &attributes)) &&
                (attributes & SFGAO_FILESYSTEM) && !(attributes & SFGAO_FOLDER);
            item->Release();
            if (is_file) {
                // One sendable file is enough; folders in the selection are
                // skipped when the command runs.
                *state = ECS_ENABLED;
                break;
            }
        }
        return S_OK;
    }

    IFACEMETHODIMP Invoke(IShellItemArray* items, IBindCtx*) override {
        if (items == nullptr) {
            return S_OK;
        }
        const std::wstring executable = FindExecutable();
        if (executable.empty()) {
            return E_FAIL;
        }
        DWORD count = 0;
        if (FAILED(items->GetCount(&count))) {
            return E_FAIL;
        }
        for (DWORD index = 0; index < count; ++index) {
            IShellItem* item = nullptr;
            if (FAILED(items->GetItemAt(index, &item)) || item == nullptr) {
                continue;
            }
            PWSTR path = nullptr;
            if (SUCCEEDED(item->GetDisplayName(SIGDN_FILESYSPATH, &path)) && path != nullptr) {
                if (!PathIsDirectoryW(path)) {
                    StartSend(executable, path);
                }
                CoTaskMemFree(path);
            }
            item->Release();
        }
        return S_OK;
    }

    IFACEMETHODIMP GetFlags(EXPCMDFLAGS* flags) override {
        *flags = ECF_DEFAULT;
        return S_OK;
    }

    IFACEMETHODIMP EnumSubCommands(IEnumExplorerCommand** commands) override {
        *commands = nullptr;
        return E_NOTIMPL;
    }

private:
    ~SendToTelegramCommand() { InterlockedDecrement(&g_objects); }

    LONG references_;
};

class CommandFactory : public IClassFactory {
public:
    CommandFactory() : references_(1) { InterlockedIncrement(&g_objects); }

    IFACEMETHODIMP QueryInterface(REFIID riid, void** result) override {
        if (result == nullptr) {
            return E_POINTER;
        }
        if (riid == IID_IUnknown || riid == IID_IClassFactory) {
            *result = static_cast<IClassFactory*>(this);
            AddRef();
            return S_OK;
        }
        *result = nullptr;
        return E_NOINTERFACE;
    }

    IFACEMETHODIMP_(ULONG) AddRef() override { return InterlockedIncrement(&references_); }

    IFACEMETHODIMP_(ULONG) Release() override {
        const LONG remaining = InterlockedDecrement(&references_);
        if (remaining == 0) {
            delete this;
        }
        return remaining;
    }

    IFACEMETHODIMP CreateInstance(IUnknown* outer, REFIID riid, void** result) override {
        if (result == nullptr) {
            return E_POINTER;
        }
        *result = nullptr;
        if (outer != nullptr) {
            return CLASS_E_NOAGGREGATION;
        }
        SendToTelegramCommand* command = new (std::nothrow) SendToTelegramCommand();
        if (command == nullptr) {
            return E_OUTOFMEMORY;
        }
        const HRESULT hr = command->QueryInterface(riid, result);
        command->Release();
        return hr;
    }

    IFACEMETHODIMP LockServer(BOOL lock) override {
        if (lock) {
            InterlockedIncrement(&g_objects);
        } else {
            InterlockedDecrement(&g_objects);
        }
        return S_OK;
    }

private:
    ~CommandFactory() { InterlockedDecrement(&g_objects); }

    LONG references_;
};

extern "C" BOOL WINAPI DllMain(HINSTANCE module, DWORD reason, void*) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_module = module;
        DisableThreadLibraryCalls(module);
    }
    return TRUE;
}

extern "C" HRESULT __stdcall DllGetClassObject(REFCLSID clsid, REFIID riid, void** result) {
    if (result == nullptr) {
        return E_POINTER;
    }
    *result = nullptr;
    if (clsid != CLSID_MindSendToTelegram) {
        return CLASS_E_CLASSNOTAVAILABLE;
    }
    CommandFactory* factory = new (std::nothrow) CommandFactory();
    if (factory == nullptr) {
        return E_OUTOFMEMORY;
    }
    const HRESULT hr = factory->QueryInterface(riid, result);
    factory->Release();
    return hr;
}

extern "C" HRESULT __stdcall DllCanUnloadNow() {
    return g_objects == 0 ? S_OK : S_FALSE;
}
