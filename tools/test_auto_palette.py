"""Verify that mouse-selecting text opens the running Mind Palette automatically."""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
WM_CLOSE = 0x0010
SW_RESTORE = 9
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOSIZE = 0x0001
SWP_SHOWWINDOW = 0x0040


def find_window(title: str) -> int:
    matches: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def callback(hwnd, _lparam):
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length:
            buffer = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buffer, length + 1)
            if buffer.value == title:
                matches.append(int(hwnd))
        return True

    ctypes.windll.user32.EnumWindows(callback, 0)
    return matches[0] if matches else 0


def activate_window(hwnd: int) -> bool:
    user32 = ctypes.windll.user32
    foreground = user32.GetForegroundWindow()
    foreground_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    attached = bool(
        foreground_thread
        and target_thread
        and foreground_thread != target_thread
        and user32.AttachThreadInput(foreground_thread, target_thread, True)
    )
    try:
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(foreground_thread, target_thread, False)
    return int(user32.GetForegroundWindow() or 0) == hwnd


def rects_intersect(first: wintypes.RECT, second: wintypes.RECT) -> bool:
    return not (
        first.right <= second.left
        or first.left >= second.right
        or first.bottom <= second.top
        or first.top >= second.bottom
    )


def window_at(x: int, y: int) -> int:
    hovered = ctypes.windll.user32.WindowFromPoint(wintypes.POINT(x, y))
    return int(ctypes.windll.user32.GetAncestor(hovered, 2) or hovered or 0)


def main() -> None:
    ctypes.windll.user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    ctypes.windll.user32.SetWindowPos.restype = wintypes.BOOL
    sys.path.insert(0, str(PROJECT_ROOT))
    from mind.config_store import ConfigStore

    config = ConfigStore().load()
    if not config.get("mind_palette_enabled"):
        raise RuntimeError("Mind Palette is not enabled.")
    if not config.get("mind_palette_auto_show_on_selection"):
        raise RuntimeError("Automatic Palette display is not enabled.")

    process = subprocess.Popen(
        [sys.executable, str(PROJECT_ROOT / "tools" / "palette_test_target.py")],
        cwd=PROJECT_ROOT,
    )
    target_hwnd = 0
    palette_hwnd = 0
    original_cursor = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(original_cursor))
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not target_hwnd:
            target_hwnd = find_window("Mind Palette Test Target")
            time.sleep(0.05)
        if not target_hwnd:
            raise RuntimeError("The diagnostic text target did not open.")

        ctypes.windll.user32.ShowWindow(target_hwnd, SW_RESTORE)
        if not ctypes.windll.user32.SetWindowPos(
            target_hwnd,
            HWND_TOPMOST,
            320,
            240,
            0,
            0,
            SWP_NOSIZE | SWP_SHOWWINDOW,
        ):
            raise ctypes.WinError()
        rect = wintypes.RECT()
        if not ctypes.windll.user32.GetWindowRect(target_hwnd, ctypes.byref(rect)):
            raise RuntimeError("Mind could not inspect the diagnostic target window.")
        y = rect.top + max(38, (rect.bottom - rect.top) // 2)
        start_x = rect.left + 24
        end_x = min(rect.right - 24, start_x + 260)
        if not activate_window(target_hwnd):
            raise RuntimeError("The diagnostic text target could not become the foreground window.")
        time.sleep(0.25)
        if find_window("Mind Palette"):
            raise RuntimeError("A Palette was already open before the diagnostic selection.")
        ctypes.windll.user32.SetCursorPos(start_x, y)
        time.sleep(0.15)
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.12)
        ctypes.windll.user32.SetCursorPos(end_x, y)
        time.sleep(0.12)
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not palette_hwnd:
            palette_hwnd = find_window("Mind Palette")
            time.sleep(0.05)
        if not palette_hwnd:
            cursor = wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(cursor))
            raise RuntimeError(
                "Mouse-selected text did not open Mind Palette "
                f"(target={target_hwnd}, foreground="
                f"{int(ctypes.windll.user32.GetForegroundWindow() or 0)}, "
                f"start_window={window_at(start_x, y)}, "
                f"end_window={window_at(end_x, y)}, cursor=({cursor.x}, {cursor.y}))."
            )
        palette_rect = wintypes.RECT()
        if not ctypes.windll.user32.GetWindowRect(palette_hwnd, ctypes.byref(palette_rect)):
            raise RuntimeError("Mind could not inspect the Palette position.")
        selection_rect = wintypes.RECT(
            min(start_x, end_x) - 8,
            y - 20,
            max(start_x, end_x) + 8,
            y + 20,
        )
        if rects_intersect(palette_rect, selection_rect):
            raise RuntimeError("Mind Palette appeared over the selected text.")

        ctypes.windll.user32.SetCursorPos(start_x, y)
        time.sleep(0.15)
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        deadline = time.monotonic() + 1
        saw_close = False
        while time.monotonic() < deadline:
            saw_close = saw_close or not find_window("Mind Palette")
            time.sleep(0.05)
        if not saw_close or find_window("Mind Palette"):
            raise RuntimeError("Mind Palette stayed open after the text was unselected.")
        palette_hwnd = 0
        print("Automatic Palette placement and dismissal test passed.")
    finally:
        ctypes.windll.user32.SetCursorPos(original_cursor.x, original_cursor.y)
        if palette_hwnd:
            ctypes.windll.user32.PostMessageW(palette_hwnd, WM_CLOSE, 0, 0)
        if target_hwnd:
            ctypes.windll.user32.PostMessageW(target_hwnd, WM_CLOSE, 0, 0)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=2)


if __name__ == "__main__":
    main()
