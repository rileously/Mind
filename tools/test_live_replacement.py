"""Verify selection capture and replacement against a disposable real Qt text field."""

from __future__ import annotations

import ctypes
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication

from mind.selection import SelectionSession


WM_CLOSE = 0x0010
SW_RESTORE = 9


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


def main() -> None:
    app = QApplication([])
    with tempfile.TemporaryDirectory() as temporary:
        report = Path(temporary) / "result.txt"
        process = subprocess.Popen(
            [
                sys.executable,
                str(PROJECT_ROOT / "tools" / "palette_test_target.py"),
                "--report",
                str(report),
            ],
            cwd=PROJECT_ROOT,
        )
        hwnd = 0
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not hwnd:
                hwnd = find_window("Mind Palette Test Target")
                app.processEvents()
                time.sleep(0.05)
            if not hwnd:
                raise RuntimeError("The replacement test target did not open.")
            ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            time.sleep(0.25)
            session = SelectionSession.capture(hwnd)
            if session is None:
                raise RuntimeError("Mind could not capture the selected diagnostic text (no selection returned).")
            if session.text != "Mind Palette diagnostic text":
                raise RuntimeError(f"Mind captured unexpected diagnostic text: {session.text!r}")
            if not session.replace("Replaced by Mind"):
                raise RuntimeError("Mind reported that replacement failed.")
            time.sleep(0.25)
            ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            process.wait(timeout=3)
            actual = report.read_text(encoding="utf-8")
            if actual != "Replaced by Mind":
                raise RuntimeError(f"Target contains {actual!r} after replacement.")
            print("Live selection replacement test passed.")
        finally:
            if hwnd and process.poll() is None:
                ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=2)


if __name__ == "__main__":
    main()
