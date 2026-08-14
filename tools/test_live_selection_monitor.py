"""Exercise the global mouse gesture monitor against a disposable text window."""

from __future__ import annotations

import ctypes
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from mind.selection import SelectionSession
from mind.selection_monitor import SelectionMonitor


SW_RESTORE = 9
WM_CLOSE = 0x0010
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
    app = QApplication([])
    process = subprocess.Popen(
        [sys.executable, str(PROJECT_ROOT / "tools" / "palette_test_target.py")],
        cwd=PROJECT_ROOT,
    )
    state = {"target": 0, "detected": 0, "selected_text": "", "samples": []}
    monitor = SelectionMonitor()
    original_update = monitor._tracker.update

    def record_sample(sample):
        state["samples"].append(sample)
        return original_update(sample)

    monitor._tracker.update = record_sample
    def capture_selection(hwnd: int, _bounds) -> None:
        state["detected"] = hwnd
        session = SelectionSession.capture(hwnd, timeout=0.5)
        state["selected_text"] = session.text if session else ""

    monitor.selection_gesture.connect(capture_selection)
    monitor.set_enabled(True)

    def prepare() -> None:
        hwnd = find_window("Mind Palette Test Target")
        if not hwnd:
            QTimer.singleShot(50, prepare)
            return
        state["target"] = hwnd
        ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
        if not ctypes.windll.user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            320,
            240,
            0,
            0,
            SWP_NOSIZE | SWP_SHOWWINDOW,
        ):
            raise ctypes.WinError()
        rect = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        state.update(
            start_x=rect.left + 24,
            end_x=min(rect.right - 24, rect.left + 284),
            y=rect.top + max(38, (rect.bottom - rect.top) // 2),
        )
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        QTimer.singleShot(250, press)

    def press() -> None:
        ctypes.windll.user32.SetCursorPos(state["start_x"], state["y"])
        QTimer.singleShot(150, mouse_down)

    def mouse_down() -> None:
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        QTimer.singleShot(180, move)

    def move() -> None:
        ctypes.windll.user32.SetCursorPos(state["end_x"], state["y"])
        QTimer.singleShot(180, release)

    def release() -> None:
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        QTimer.singleShot(350, finish)

    def finish() -> None:
        app.quit()

    QTimer.singleShot(0, prepare)
    QTimer.singleShot(4000, app.quit)
    app.exec()
    if state["target"]:
        ctypes.windll.user32.PostMessageW(state["target"], WM_CLOSE, 0, 0)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait(timeout=2)
    if state["detected"] != state["target"]:
        down = [sample for sample in state["samples"] if sample.down]
        windows = sorted({sample.hwnd for sample in state["samples"]})
        down_windows = sorted({sample.hwnd for sample in down})
        tail = [
            (sample.down, sample.hwnd, sample.x, sample.y, sample.blocked_modifier)
            for sample in state["samples"][-8:]
        ]
        first_down = (
            (down[0].hwnd, down[0].x, down[0].y, down[0].blocked_modifier)
            if down else None
        )
        raise RuntimeError(
            "The global mouse monitor did not recognize the diagnostic drag "
            f"({len(down)} down samples; windows={windows}; down_windows={down_windows}; "
            f"target={state['target']}; detected={state['detected']}; "
            f"first_down={first_down}; tail={tail})."
        )
    if not state["selected_text"].strip():
        raise RuntimeError("The recognized drag did not expose selected text to Mind.")
    print("Live selection monitor and capture test passed.")


if __name__ == "__main__":
    main()
