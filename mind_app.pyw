from __future__ import annotations

import ctypes
import hashlib
import runpy
import sys

from PySide6.QtWidgets import QApplication, QDialog

from mind.config_store import ConfigStore
from mind.main_window import MindWindow
from mind.paths import data_dir, engine_path
from mind.setup_wizard import SetupWizard
from mind.theme import app_icon, stylesheet


ERROR_ALREADY_EXISTS = 183
SW_RESTORE = 9
_app_mutex_handle: int | None = None


def _app_mutex_name() -> str:
    identity = str(data_dir()).casefold().encode("utf-8")
    suffix = hashlib.sha256(identity).hexdigest()[:16]
    return f"Local\\MindDesktop-{suffix}"


def _activate_existing_window() -> None:
    user32 = ctypes.windll.user32
    user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    user32.FindWindowW.restype = ctypes.c_void_p
    hwnd = user32.FindWindowW(None, "Mind")
    if hwnd:
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)


def _acquire_app_singleton() -> bool:
    global _app_mutex_handle
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.GetLastError.argtypes = []
    kernel32.GetLastError.restype = ctypes.c_ulong
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool

    handle = kernel32.CreateMutexW(None, False, _app_mutex_name())
    if not handle:
        raise ctypes.WinError(kernel32.GetLastError())
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        if "--minimized" not in sys.argv:
            _activate_existing_window()
        return False
    _app_mutex_handle = handle
    return True


def _release_app_singleton() -> None:
    global _app_mutex_handle
    if _app_mutex_handle:
        ctypes.windll.kernel32.CloseHandle(_app_mutex_handle)
        _app_mutex_handle = None


def run_engine() -> int:
    """Run the bundled keyboard engine inside the packaged executable."""
    target = engine_path()
    if not target.exists():
        raise FileNotFoundError(f"Mind engine file is missing: {target}")
    runpy.run_path(str(target), run_name="__main__")
    return 0


def main() -> int:
    if "--engine" in sys.argv:
        return run_engine()
    if not _acquire_app_singleton():
        return 0

    try:
        app = QApplication(sys.argv)
        app.setApplicationName("Mind")
        app.setOrganizationName("Mind")
        app.setWindowIcon(app_icon())
        app.setQuitOnLastWindowClosed(False)

        store = ConfigStore()
        config = store.load()
        app.setStyleSheet(stylesheet(str(config.get("theme", "system")), str(config.get("accent_color", "teal"))))
        if not config.get("onboarding_complete", False):
            wizard = SetupWizard(store)
            if wizard.exec() != QDialog.Accepted:
                return 0
            config = store.load()
            app.setStyleSheet(stylesheet(str(config.get("theme", "system")), str(config.get("accent_color", "teal"))))

        minimized = "--minimized" in sys.argv
        window = MindWindow(store, minimized=minimized)
        if not minimized:
            window.show()
        return app.exec()
    finally:
        _release_app_singleton()


if __name__ == "__main__":
    raise SystemExit(main())
