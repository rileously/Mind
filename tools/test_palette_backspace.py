"""Verify Backspace dismisses Mind Palette and erases the source selection."""

from __future__ import annotations

import ctypes
import subprocess
import sys
import tempfile
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mind.config_store import ConfigStore
from mind.hotkeys import PALETTE_SHORTCUTS
from tools.test_live_palette import INPUT, find_window, send_shortcut


KEYEVENTF_KEYUP = 0x0002
SW_RESTORE = 9
VK_BACK = 0x08
WM_CLOSE = 0x0010


def send_key(virtual_key: int) -> None:
    events = (INPUT * 2)()
    events[0].type = 1
    events[0].union.ki.wVk = virtual_key
    events[1].type = 1
    events[1].union.ki.wVk = virtual_key
    events[1].union.ki.dwFlags = KEYEVENTF_KEYUP
    sent = ctypes.windll.user32.SendInput(2, ctypes.byref(events), ctypes.sizeof(INPUT))
    if sent != 2:
        raise RuntimeError(f"Only {sent}/2 Backspace events were sent.")


def main() -> None:
    config = ConfigStore().load()
    shortcut = str(config.get("mind_palette_shortcut", "Ctrl+Alt+M"))
    if not config.get("mind_palette_enabled") or shortcut not in PALETTE_SHORTCUTS:
        raise RuntimeError("Mind Palette is not enabled with a known shortcut.")

    with tempfile.TemporaryDirectory() as directory:
        report_path = Path(directory) / "result.txt"
        target_process = subprocess.Popen(
            [
                sys.executable,
                str(PROJECT_ROOT / "tools" / "palette_test_target.py"),
                "--report",
                str(report_path),
            ],
            cwd=PROJECT_ROOT,
        )
        target_hwnd = 0
        palette_hwnd = 0
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
            send_shortcut(*PALETTE_SHORTCUTS[shortcut])

            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and not palette_hwnd:
                palette_hwnd = find_window("Mind Palette")
                time.sleep(0.05)
            if not palette_hwnd:
                raise RuntimeError("Mind Palette did not open for the selected text.")

            send_key(VK_BACK)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and find_window("Mind Palette"):
                time.sleep(0.05)
            if find_window("Mind Palette"):
                raise RuntimeError("Backspace did not dismiss Mind Palette.")
            palette_hwnd = 0
            time.sleep(0.25)
        finally:
            if palette_hwnd:
                ctypes.windll.user32.PostMessageW(palette_hwnd, WM_CLOSE, 0, 0)
            if target_hwnd:
                ctypes.windll.user32.PostMessageW(target_hwnd, WM_CLOSE, 0, 0)
            try:
                target_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                target_process.terminate()
                target_process.wait(timeout=2)

        if report_path.read_text(encoding="utf-8"):
            raise RuntimeError("Backspace closed the Palette but did not erase the selected text.")
    print("Palette Backspace forwarding test passed.")


if __name__ == "__main__":
    main()
