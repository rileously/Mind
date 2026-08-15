from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, QUrl, Signal
from PySide6.QtGui import QCursor, QDesktopServices
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

from .url_tools import UrlMetadata, extract_quick_metadata, strip_tracking_params


class UrlPeekCard(QDialog):
    summarize_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setObjectName("UrlPeekCard")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._current_url = ""
        self._clean_url = ""
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        self.frame = QWidget()
        self.frame.setObjectName("UrlPeekFrame")
        layout = QVBoxLayout(self.frame)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(9)

        # Header
        header = QHBoxLayout()
        header.setSpacing(8)

        self.domain_badge = QLabel("🌐 example.com")
        self.domain_badge.setObjectName("UrlPeekDomainBadge")
        header.addWidget(self.domain_badge)

        header.addStretch()

        close_btn = QPushButton("✕")
        close_btn.setObjectName("PopupCloseButton")
        close_btn.clicked.connect(self.dismiss)
        header.addWidget(close_btn)

        layout.addLayout(header)

        # Title
        self.title_label = QLabel("Page Title")
        self.title_label.setObjectName("UrlPeekTitle")
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)

        # URL Preview
        self.url_preview = QLabel("https://...")
        self.url_preview.setObjectName("UrlPeekUrlText")
        self.url_preview.setWordWrap(True)
        layout.addWidget(self.url_preview)

        # Action Buttons
        actions = QHBoxLayout()
        actions.setSpacing(8)

        self.clean_btn = QPushButton("🧹 Copy Clean Link")
        self.clean_btn.setObjectName("UrlPeekCleanButton")
        self.clean_btn.clicked.connect(self._on_copy_clean_clicked)
        actions.addWidget(self.clean_btn)

        self.ai_btn = QPushButton("✦ Summarize")
        self.ai_btn.setObjectName("UrlPeekAiButton")
        self.ai_btn.clicked.connect(self._on_ai_summarize_clicked)
        actions.addWidget(self.ai_btn)

        self.open_btn = QPushButton("🌐 Open")
        self.open_btn.setObjectName("UrlPeekOpenButton")
        self.open_btn.clicked.connect(self._on_open_clicked)
        actions.addWidget(self.open_btn)

        layout.addLayout(actions)
        outer.addWidget(self.frame)

        self.setFixedWidth(390)

    def show_for_url(self, url_str: str, metadata: UrlMetadata | None = None) -> None:
        self._current_url = url_str
        self._clean_url = strip_tracking_params(url_str)

        if not metadata:
            metadata = extract_quick_metadata(url_str)

        self.domain_badge.setText(f"🌐 {metadata.domain}")
        self.title_label.setText(metadata.title[:75] + ("..." if len(metadata.title) > 75 else ""))
        display_url = self._clean_url
        if len(display_url) > 65:
            display_url = display_url[:62] + "..."
        self.url_preview.setText(display_url)
        self.clean_btn.setText("🧹 Copy Clean Link")

        self.adjustSize()

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

    def _on_copy_clean_clicked(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(self._clean_url)
        self.clean_btn.setText("✓ Copied Clean!")

    def _on_ai_summarize_clicked(self) -> None:
        self.summarize_requested.emit(self._clean_url)
        self.dismiss()

    def _on_open_clicked(self) -> None:
        QDesktopServices.openUrl(QUrl(self._clean_url))
        self.dismiss()

    def dismiss(self) -> None:
        self.hide()
