from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_MENU = 0x12
SELF_INPUT_TAG = 0x53534B


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
    # All three members are required for the native INPUT structure's 64-bit size.
    # A keyboard-only union is too small, causing SendInput to reject every event.
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]


def _key(vk: int, flags: int = 0) -> INPUT:
    event = INPUT()
    event.type = INPUT_KEYBOARD
    event.union.ki.wVk = vk
    event.union.ki.dwFlags = flags
    event.union.ki.dwExtraInfo = SELF_INPUT_TAG
    return event


def _send_ctrl_key(vk: int) -> bool:
    events = (INPUT * 4)(
        _key(VK_CONTROL),
        _key(vk),
        _key(vk, KEYEVENTF_KEYUP),
        _key(VK_CONTROL, KEYEVENTF_KEYUP),
    )
    sent = ctypes.windll.user32.SendInput(4, ctypes.byref(events), ctypes.sizeof(INPUT))
    return sent == 4


def _send_key(vk: int) -> bool:
    events = (INPUT * 2)(
        _key(vk),
        _key(vk, KEYEVENTF_KEYUP),
    )
    sent = ctypes.windll.user32.SendInput(2, ctypes.byref(events), ctypes.sizeof(INPUT))
    return sent == 2


def _modifiers_released(timeout: float = 0.8) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000 for vk in (VK_CONTROL, VK_MENU)):
            return True
        QApplication.processEvents()
        time.sleep(0.015)
    return False


def _pump_events(duration: float) -> None:
    """Keep servicing delayed Windows clipboard rendering during short waits."""
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)


def clone_mime_data(source: QMimeData | None) -> QMimeData | None:
    if source is None:
        return None
    copy = QMimeData()
    for format_name in source.formats():
        copy.setData(format_name, source.data(format_name))
    if source.hasUrls() and not copy.hasUrls():
        copy.setUrls([QUrl(url) for url in source.urls()])
    return copy


class SelectionSession:
    """Capture a selection and later replace it while preserving the clipboard."""

    kind = "text"

    def __init__(self, target_hwnd: int, text: str, original_clipboard: QMimeData | None):
        self.target_hwnd = target_hwnd
        self.text = text
        self.original_clipboard = original_clipboard

    @classmethod
    def capture(cls, target_hwnd: int, timeout: float = 1.0) -> "SelectionSession | None":
        if not target_hwnd or not _modifiers_released():
            return None
        clipboard = QApplication.clipboard()
        original = clone_mime_data(clipboard.mimeData())
        sequence = ctypes.windll.user32.GetClipboardSequenceNumber()
        clipboard.clear()
        QApplication.processEvents()
        cleared_sequence = ctypes.windll.user32.GetClipboardSequenceNumber()
        if not _send_ctrl_key(0x43):  # C
            _restore_clipboard(original)
            return None

        deadline = time.monotonic() + max(0.1, min(float(timeout), 1.0))
        changed = False
        while time.monotonic() < deadline:
            QApplication.processEvents()
            current = ctypes.windll.user32.GetClipboardSequenceNumber()
            if current not in {sequence, cleared_sequence} and clipboard.mimeData().hasText():
                changed = True
                break
            time.sleep(0.015)
        selected = clipboard.text() if changed else ""
        _restore_clipboard(original)
        if not selected.strip():
            return None
        return cls(target_hwnd, selected, original)

    def replace(self, result: str) -> bool:
        if not result:
            return False
        user32 = ctypes.windll.user32
        if not user32.IsWindow(self.target_hwnd):
            QApplication.clipboard().setText(result)
            return False
        user32.SetForegroundWindow(self.target_hwnd)
        _pump_events(0.12)
        if user32.GetForegroundWindow() != self.target_hwnd or not _modifiers_released(0.4):
            QApplication.clipboard().setText(result)
            return False

        clipboard = QApplication.clipboard()
        clipboard.setText(result)
        _pump_events(0.12)
        pasted = _send_ctrl_key(0x56)  # V
        # QClipboard can use delayed rendering on Windows. The target's Ctrl+V request
        # is serviced by Mind's event loop, so sleeping here can make the target receive
        # an empty clipboard and delete the selected text. Pump until paste is committed.
        _pump_events(0.55)
        if pasted:
            _restore_clipboard(self.original_clipboard)
            _pump_events(0.08)
        return pasted

    def delete_selected_text(self, virtual_key: int) -> bool:
        """Return focus to the source and forward Backspace or Delete safely."""
        if virtual_key not in {0x08, 0x2E}:  # VK_BACK, VK_DELETE
            return False
        user32 = ctypes.windll.user32
        if not user32.IsWindow(self.target_hwnd):
            return False
        user32.SetForegroundWindow(self.target_hwnd)
        _pump_events(0.08)
        if user32.GetForegroundWindow() != self.target_hwnd or not _modifiers_released(0.4):
            return False
        return _send_key(virtual_key)


class ClipboardImageSession:
    """Keep a copied image intact while OCR output is pasted at the current caret."""

    kind = "image"

    def __init__(self, target_hwnd: int, image: QImage, original_clipboard: QMimeData | None):
        self.target_hwnd = target_hwnd
        self.image = image.copy()
        self.original_clipboard = original_clipboard
        self.text = ""

    @classmethod
    def capture(cls, target_hwnd: int) -> "ClipboardImageSession | None":
        if not target_hwnd or not _modifiers_released():
            return None
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()
        if mime is None or not mime.hasImage():
            return None
        image = clipboard.image()
        if image.isNull():
            image_data = mime.imageData()
            image = image_data if isinstance(image_data, QImage) else QImage()
        if image.isNull():
            return None
        return cls(target_hwnd, image, clone_mime_data(mime))

    def replace(self, result: str) -> bool:
        if not result:
            return False
        user32 = ctypes.windll.user32
        if not user32.IsWindow(self.target_hwnd):
            QApplication.clipboard().setText(result)
            return False
        user32.SetForegroundWindow(self.target_hwnd)
        _pump_events(0.12)
        if user32.GetForegroundWindow() != self.target_hwnd or not _modifiers_released(0.4):
            QApplication.clipboard().setText(result)
            return False

        clipboard = QApplication.clipboard()
        clipboard.setText(result)
        _pump_events(0.12)
        pasted = _send_ctrl_key(0x56)  # V
        _pump_events(0.55)
        if pasted:
            _restore_clipboard(self.original_clipboard)
            _pump_events(0.08)
        return pasted


def _restore_clipboard(original: QMimeData | None) -> None:
    clipboard = QApplication.clipboard()
    if original is None or not original.formats():
        clipboard.clear()
    else:
        clipboard.setMimeData(clone_mime_data(original))
    QApplication.processEvents()
