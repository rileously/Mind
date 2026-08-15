from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QCursor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from .selection import send_paste_input


class GhostTextOverlay(QDialog):
    accepted = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setObjectName("GhostTextOverlay")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._completion_text = ""
        self._target_hwnd = 0
        self._build_ui()
        self._setup_shortcuts()

    def _build_ui(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)

        self.frame = QFrame()
        self.frame.setObjectName("GhostTextFrame")
        layout = QHBoxLayout(self.frame)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        icon = QLabel("🪄")
        icon.setObjectName("GhostTextIcon")
        layout.addWidget(icon)

        self.text_label = QLabel("suggestion text...")
        self.text_label.setObjectName("GhostTextLabel")
        layout.addWidget(self.text_label)

        self.tab_badge = QLabel("Tab ↹")
        self.tab_badge.setObjectName("GhostTextTabBadge")
        layout.addWidget(self.tab_badge)

        outer.addWidget(self.frame)

    def _setup_shortcuts(self) -> None:
        tab_sc = QShortcut(QKeySequence(Qt.Key_Tab), self)
        tab_sc.activated.connect(self.accept_completion)
        enter_sc = QShortcut(QKeySequence(Qt.Key_Return), self)
        enter_sc.activated.connect(self.accept_completion)
        esc_sc = QShortcut(QKeySequence(Qt.Key_Escape), self)
        esc_sc.activated.connect(self.dismiss)

    def show_suggestion(self, suggestion: str, target_hwnd: int = 0) -> None:
        if not suggestion:
            return
        self._completion_text = suggestion
        self._target_hwnd = target_hwnd

        # Clean preview text
        clean_preview = suggestion.replace("\n", " ").strip()
        if len(clean_preview) > 50:
            clean_preview = clean_preview[:48] + "..."
        self.text_label.setText(clean_preview)

        self.adjustSize()

        # Position near cursor
        cursor_pos = QCursor.pos()
        screen = QApplication.screenAt(cursor_pos) or QApplication.primaryScreen()
        screen_geo = screen.geometry() if screen else QRect(0, 0, 1920, 1080)

        x = cursor_pos.x() + 15
        y = cursor_pos.y() + 20
        if x + self.width() > screen_geo.right() - 10:
            x = cursor_pos.x() - self.width() - 10
        if y + self.height() > screen_geo.bottom() - 10:
            y = cursor_pos.y() - self.height() - 10

        x = max(screen_geo.left() + 5, min(x, screen_geo.right() - self.width() - 5))
        y = max(screen_geo.top() + 5, min(y, screen_geo.bottom() - self.height() - 5))

        self.move(x, y)
        self.show()
        self.raise_()

    def mousePressEvent(self, event) -> None:
        self.accept_completion()

    def accept_completion(self) -> None:
        if not self._completion_text:
            self.dismiss()
            return
        text_to_paste = self._completion_text
        self.dismiss()
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(text_to_paste)
        send_paste_input(self._target_hwnd)
        self.accepted.emit(text_to_paste)

    def dismiss(self) -> None:
        self.hide()
