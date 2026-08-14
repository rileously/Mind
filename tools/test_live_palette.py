"""Exercise the running Mind app's real global shortcut without calling an AI provider."""

from __future__ import annotations

import ctypes
import argparse
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mind.config_store import ConfigStore
from mind.hotkeys import PALETTE_SHORTCUTS


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
WM_CLOSE = 0x0010
WM_HOTKEY = 0x0312
MIND_PALETTE_HOTKEY_ID = 0x4D49
SW_RESTORE = 9


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]


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


def send_shortcut(modifiers: int, virtual_key: int) -> None:
    keys = []
    if modifiers & 0x0002:
        keys.append(0x11)
    if modifiers & 0x0001:
        keys.append(0x12)
    if modifiers & 0x0004:
        keys.append(0x10)
    values = [(key, 0) for key in keys]
    values.append((virtual_key, 0))
    values.append((virtual_key, KEYEVENTF_KEYUP))
    values.extend((key, KEYEVENTF_KEYUP) for key in reversed(keys))
    events = (INPUT * len(values))()
    for index, (key, flags) in enumerate(values):
        events[index].type = INPUT_KEYBOARD
        events[index].union.ki.wVk = key
        events[index].union.ki.dwFlags = flags
    sent = ctypes.windll.user32.SendInput(len(events), ctypes.byref(events), ctypes.sizeof(INPUT))
    if sent != len(events):
        raise RuntimeError(f"Only {sent}/{len(events)} shortcut events were sent.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--post-message", action="store_true")
    args = parser.parse_args()
    config = ConfigStore().load()
    shortcut = str(config.get("mind_palette_shortcut", "Ctrl+Alt+M"))
    if not config.get("mind_palette_enabled") or shortcut not in PALETTE_SHORTCUTS:
        raise RuntimeError("Mind Palette is not enabled with a known shortcut.")

    target_process = subprocess.Popen(
        [sys.executable, str(PROJECT_ROOT / "tools" / "palette_test_target.py")],
        cwd=PROJECT_ROOT,
    )
    target_hwnd = 0
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not target_hwnd:
            target_hwnd = find_window("Mind Palette Test Target")
            time.sleep(0.05)
        if not target_hwnd:
            raise RuntimeError("The diagnostic text target did not open.")
        ctypes.windll.user32.ShowWindow(target_hwnd, SW_RESTORE)
        ctypes.windll.user32.SetForegroundWindow(target_hwnd)
        time.sleep(0.25)
        modifiers, virtual_key = PALETTE_SHORTCUTS[shortcut]
        if args.post_message:
            mind_hwnd = find_window("Mind")
            if not mind_hwnd:
                raise RuntimeError("The Mind window was not found.")
            ctypes.windll.user32.PostMessageW(mind_hwnd, WM_HOTKEY, MIND_PALETTE_HOTKEY_ID, 0)
        else:
            send_shortcut(modifiers, virtual_key)

        palette_hwnd = 0
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not palette_hwnd:
            palette_hwnd = find_window("Mind Palette")
            time.sleep(0.05)
        if not palette_hwnd:
            raise RuntimeError(f"Mind did not open the palette for {shortcut}.")
        print(f"Live palette test passed with {shortcut}.")
        ctypes.windll.user32.PostMessageW(palette_hwnd, WM_CLOSE, 0, 0)
    finally:
        if target_hwnd:
            ctypes.windll.user32.PostMessageW(target_hwnd, WM_CLOSE, 0, 0)
        try:
            target_process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            target_process.terminate()
            target_process.wait(timeout=2)


if __name__ == "__main__":
    main()
