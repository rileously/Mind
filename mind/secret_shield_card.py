from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .secret_detector import SecretFinding, redact_all_secrets


class SecretShieldCard(QDialog):
    redacted = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setObjectName("SecretShieldCard")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._raw_text = ""
        self._findings: list[SecretFinding] = []
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        self.frame = QWidget()
        self.frame.setObjectName("SecretShieldFrame")
        layout = QVBoxLayout(self.frame)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        # Header
        header = QHBoxLayout()
        header.setSpacing(8)

        icon = QLabel("🛡️")
        icon.setObjectName("SecretShieldIcon")
        header.addWidget(icon)

        title = QLabel("Sensitive Data Detected")
        title.setObjectName("SecretShieldTitle")
        header.addWidget(title)

        header.addStretch()

        close_btn = QPushButton("✕")
        close_btn.setObjectName("PopupCloseButton")
        close_btn.clicked.connect(self.dismiss)
        header.addWidget(close_btn)

        layout.addLayout(header)

        # Types row
        self.type_badge = QLabel("API Key")
        self.type_badge.setObjectName("SecretShieldTypeBadge")
        layout.addWidget(self.type_badge)

        # Masked Preview container
        self.preview_box = QFrame()
        self.preview_box.setObjectName("SecretShieldPreviewContainer")
        preview_layout = QVBoxLayout(self.preview_box)
        preview_layout.setContentsMargins(10, 8, 10, 8)
        self.preview_label = QLabel()
        self.preview_label.setObjectName("SecretShieldPreviewText")
        self.preview_label.setWordWrap(True)
        preview_layout.addWidget(self.preview_label)
        layout.addWidget(self.preview_box)

        # Actions row
        actions = QHBoxLayout()
        actions.setSpacing(8)

        self.redact_btn = QPushButton("🔒 Redact and Copy")
        self.redact_btn.setObjectName("SecretShieldRedactButton")
        self.redact_btn.clicked.connect(self._on_redact_clicked)
        actions.addWidget(self.redact_btn)

        self.keep_btn = QPushButton("Keep Original")
        self.keep_btn.setObjectName("SecretShieldKeepButton")
        self.keep_btn.clicked.connect(self.dismiss)
        actions.addWidget(self.keep_btn)

        layout.addLayout(actions)
        outer.addWidget(self.frame)

        self.setFixedWidth(380)

    def show_for_findings(self, raw_text: str, findings: list[SecretFinding]) -> None:
        if not findings:
            return
        self._raw_text = raw_text
        self._findings = findings

        types = list(dict.fromkeys(f.secret_type for f in findings))
        self.type_badge.setText(" • ".join(types))

        # Show first 2 masked findings
        preview_snippets = [f.masked_text for f in findings[:2]]
        if len(findings) > 2:
            preview_snippets.append(f"+ {len(findings) - 2} more secrets")
        self.preview_label.setText("\n".join(preview_snippets))

        self.adjustSize()

        # Position near cursor
        cursor_pos = QCursor.pos()
        screen = QApplication.screenAt(cursor_pos) or QApplication.primaryScreen()
        screen_geo = screen.geometry() if screen else QRect(0, 0, 1920, 1080)

        x = cursor_pos.x() + 20
        y = cursor_pos.y() + 20
        if x + self.width() > screen_geo.right() - 15:
            x = cursor_pos.x() - self.width() - 15
        if y + self.height() > screen_geo.bottom() - 15:
            y = cursor_pos.y() - self.height() - 15

        x = max(screen_geo.left() + 10, min(x, screen_geo.right() - self.width() - 10))
        y = max(screen_geo.top() + 10, min(y, screen_geo.bottom() - self.height() - 10))

        self.move(x, y)
        self.show()
        self.raise_()

    def _on_redact_clicked(self) -> None:
        redacted_text = redact_all_secrets(self._raw_text)
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(redacted_text)
        self.redacted.emit(redacted_text)
        self.dismiss()

    def dismiss(self) -> None:
        self.hide()
