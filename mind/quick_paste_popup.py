from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .selection import send_paste_input


VK_LBUTTON = 0x01
VK_ESCAPE = 0x1B


class QuickPastePopup(QDialog):
    """Small, non-activating floating pill offering a one-click Paste button for copied text."""

    pasted = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._target_hwnd = 0
        self._clipboard_text = ""
        self._avoid_rect: QRect | None = None
        self._user32 = ctypes.windll.user32
        self._user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self._mouse_was_down = False
        self._escape_was_down = False

        self.setObjectName("QuickPastePopup")
        self.setWindowTitle("Quick Paste")
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)

        card = QWidget()
        card.setObjectName("QuickPasteCard")
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(10, 6, 8, 6)
        card_layout.setSpacing(8)

        self.icon_label = QLabel("📋")
        self.icon_label.setObjectName("QuickPasteIcon")

        self.preview_label = QLabel()
        self.preview_label.setObjectName("QuickPastePreview")
        self.preview_label.setTextFormat(Qt.PlainText)
        self.preview_label.setMaximumWidth(260)

        self.paste_button = QPushButton("Paste")
        self.paste_button.setObjectName("QuickPasteButton")
        self.paste_button.setCursor(Qt.PointingHandCursor)
        self.paste_button.setFocusPolicy(Qt.NoFocus)
        self.paste_button.clicked.connect(self._do_paste)

        self.close_button = QPushButton("×")
        self.close_button.setObjectName("DefinitionClose")
        self.close_button.setAccessibleName("Dismiss quick paste")
        self.close_button.setCursor(Qt.PointingHandCursor)
        self.close_button.setFocusPolicy(Qt.NoFocus)
        self.close_button.setFixedSize(22, 22)
        self.close_button.clicked.connect(self.dismiss)

        card_layout.addWidget(self.icon_label)
        card_layout.addWidget(self.preview_label)
        card_layout.addWidget(self.paste_button)
        card_layout.addWidget(self.close_button)

        outer.addWidget(card)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.dismiss)

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setInterval(25)
        self._dismiss_timer.timeout.connect(self._poll_for_dismissal)

    def show_for_text(
        self,
        text: str,
        target_hwnd: int,
        avoid_rect: tuple[int, int, int, int] | None = None,
    ) -> None:
        cleaned = " ".join(text.split())
        if not cleaned:
            return
        self._clipboard_text = text
        self._target_hwnd = target_hwnd
        self._avoid_rect = self._rect_from_tuple(avoid_rect)

        display_text = cleaned if len(cleaned) <= 36 else cleaned[:33] + "…"
        self.preview_label.setText(f"“{display_text}”")

        self.adjustSize()
        self._position_popup()
        self.show()
        self.raise_()
        self._hide_timer.start(6000)

        if not self._dismiss_timer.isActive():
            self._mouse_was_down = bool(self._user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
            self._escape_was_down = bool(self._user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)
            self._dismiss_timer.start()

    def _do_paste(self) -> None:
        target = self._target_hwnd
        text = self._clipboard_text
        self.dismiss()
        if target:
            send_paste_input(target)
            self.pasted.emit(text)

    def dismiss(self) -> None:
        self._hide_timer.stop()
        self._dismiss_timer.stop()
        self.hide()

    def _position_popup(self) -> None:
        avoid = self._avoid_rect
        if avoid is None:
            from PySide6.QtGui import QCursor

            cursor = QCursor.pos()
            avoid = QRect(cursor.x() - 40, cursor.y() - 14, 80, 28)

        screen = QApplication.screenAt(avoid.center()) or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

        width = self.sizeHint().width()
        height = self.sizeHint().height()
        gap = 8

        x = avoid.center().x() - width // 2
        x = max(available.left() + 8, min(x, available.right() - width - 8))
        y = avoid.top() - height - gap
        if y < available.top() + 8:
            y = avoid.bottom() + gap
        y = max(available.top() + 8, min(y, available.bottom() - height - 8))

        self.setGeometry(x, y, width, height)

    def _poll_for_dismissal(self) -> None:
        mouse_down = bool(self._user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
        escape_down = bool(self._user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)
        mouse_pressed = mouse_down and not self._mouse_was_down
        escape_pressed = escape_down and not self._escape_was_down
        self._mouse_was_down = mouse_down
        self._escape_was_down = escape_down

        if escape_pressed:
            self.dismiss()
            return
        if not mouse_pressed:
            return

        point = wintypes.POINT()
        if not self._user32.GetCursorPos(ctypes.byref(point)):
            return
        screen_point = QPoint(int(point.x), int(point.y))
        if not self.frameGeometry().contains(screen_point):
            self.dismiss()

    @staticmethod
    def _rect_from_tuple(bounds: tuple[int, int, int, int] | None) -> QRect | None:
        if not bounds:
            return None
        return QRect(
            bounds[0],
            bounds[1],
            max(1, bounds[2] - bounds[0] + 1),
            max(1, bounds[3] - bounds[1] + 1),
        )
