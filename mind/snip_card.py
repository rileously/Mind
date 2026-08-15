from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QRect, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QCursor, QDesktopServices, QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .config_store import ConfigStore
from .ocr import OcrError, extract_text_from_image
from .transform_client import TransformError, transform_text


VK_LBUTTON = 0x01
VK_ESCAPE = 0x1B


class _WorkerSignals(QObject):
    finished = Signal(bool, str)
    status = Signal(str)


class _SnipWorker(QRunnable):
    def __init__(
        self,
        config: dict,
        keys: list[str],
        image: QImage,
        mode: str,  # "ocr" or "explain"
    ):
        super().__init__()
        self.config = config
        self.keys = keys
        self.image = image
        self.mode = mode
        self.signals = _WorkerSignals()

    def run(self) -> None:
        try:
            self.signals.status.emit("Reading image text with local OCR…")
            extracted = extract_text_from_image(self.image)
            if not extracted.strip():
                self.signals.finished.emit(False, "No readable text found in this snip.")
                return

            if self.mode == "ocr":
                self.signals.finished.emit(True, extracted.strip())
            elif self.mode == "explain":
                self.signals.status.emit("Analyzing with AI…")
                prompt = (
                    "Explain what is shown or solve the problem/error in the following text extracted from a screenshot. "
                    "Keep your explanation concise and direct (under 60 words):\n\n" + extracted
                )
                answer = transform_text(
                    self.config,
                    self.keys,
                    extracted,
                    prompt,
                    system_prompt_override=(
                        "You are a helpful AI screen assistant. Analyze the user's screenshot text "
                        "and explain it clearly and concisely without preamble."
                    ),
                )
                self.signals.finished.emit(True, answer.strip())
        except (OcrError, TransformError, OSError, ValueError) as exc:
            self.signals.finished.emit(False, str(exc))


class SnipCard(QDialog):
    """Floating action card that appears immediately after a screen snip is captured."""

    def __init__(
        self,
        store: ConfigStore,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.store = store
        self._pixmap: QPixmap | None = None
        self._avoid_rect: QRect | None = None
        self._result_text = ""

        self._user32 = ctypes.windll.user32
        self._user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self._mouse_was_down = False
        self._escape_was_down = False

        self.setObjectName("SnipCard")
        self.setWindowTitle("Screen Snip")
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self._build_ui()

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setInterval(25)
        self._dismiss_timer.timeout.connect(self._poll_for_dismissal)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        self.card = QWidget()
        self.card.setObjectName("SnipCardFrame")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(10)

        # Header: Icon + Title + Size + Close
        header = QHBoxLayout()
        header.setSpacing(8)

        icon = QLabel("📸")
        icon.setObjectName("SnipHeaderIcon")

        title = QLabel("Screen Snip")
        title.setObjectName("SnipHeaderTitle")

        self.size_badge = QLabel("0 × 0 px")
        self.size_badge.setObjectName("SnipSizeBadge")

        close_btn = QPushButton("×")
        close_btn.setObjectName("DefinitionClose")
        close_btn.setAccessibleName("Dismiss snip card")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setFocusPolicy(Qt.NoFocus)
        close_btn.setFixedSize(22, 22)
        close_btn.clicked.connect(self.dismiss)

        header.addWidget(icon)
        header.addWidget(title)
        header.addWidget(self.size_badge)
        header.addStretch()
        header.addWidget(close_btn)
        card_layout.addLayout(header)

        # Thumbnail Image Preview
        self.thumb_label = QLabel()
        self.thumb_label.setObjectName("SnipThumbnail")
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setMaximumHeight(140)
        card_layout.addWidget(self.thumb_label)

        # Progress Bar & Status
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFixedHeight(3)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        card_layout.addWidget(self.progress_bar)

        self.status_label = QLabel()
        self.status_label.setObjectName("SnipStatus")
        self.status_label.hide()
        card_layout.addWidget(self.status_label)

        # Result display area
        self.result_container = QFrame()
        self.result_container.setObjectName("SnipResultContainer")
        result_layout = QVBoxLayout(self.result_container)
        result_layout.setContentsMargins(10, 8, 10, 8)
        result_layout.setSpacing(6)

        self.result_label = QLabel()
        self.result_label.setObjectName("SnipResultText")
        self.result_label.setWordWrap(True)
        self.result_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.result_label.setMaximumWidth(340)

        self.copy_result_btn = QPushButton("📋 Copy Text")
        self.copy_result_btn.setObjectName("SnipActionPrimary")
        self.copy_result_btn.setCursor(Qt.PointingHandCursor)
        self.copy_result_btn.setFocusPolicy(Qt.NoFocus)
        self.copy_result_btn.clicked.connect(self._copy_result_text)

        result_layout.addWidget(self.result_label)
        result_layout.addWidget(self.copy_result_btn)
        self.result_container.hide()
        card_layout.addWidget(self.result_container)

        # Action Buttons Row
        self.actions_layout = QHBoxLayout()
        self.actions_layout.setSpacing(6)

        self.ocr_btn = QPushButton("📋 Extract Text")
        self.ocr_btn.setObjectName("SnipActionPrimary")
        self.ocr_btn.setCursor(Qt.PointingHandCursor)
        self.ocr_btn.setFocusPolicy(Qt.NoFocus)
        self.ocr_btn.clicked.connect(lambda: self._start_worker("ocr"))

        self.explain_btn = QPushButton("✦ Explain with AI")
        self.explain_btn.setObjectName("SnipActionSecondary")
        self.explain_btn.setCursor(Qt.PointingHandCursor)
        self.explain_btn.setFocusPolicy(Qt.NoFocus)
        self.explain_btn.clicked.connect(lambda: self._start_worker("explain"))

        self.copy_img_btn = QPushButton("📷 Copy")
        self.copy_img_btn.setObjectName("SnipActionSecondary")
        self.copy_img_btn.setCursor(Qt.PointingHandCursor)
        self.copy_img_btn.setFocusPolicy(Qt.NoFocus)
        self.copy_img_btn.clicked.connect(self._copy_image)

        self.save_img_btn = QPushButton("💾 Save")
        self.save_img_btn.setObjectName("SnipActionSecondary")
        self.save_img_btn.setCursor(Qt.PointingHandCursor)
        self.save_img_btn.setFocusPolicy(Qt.NoFocus)
        self.save_img_btn.clicked.connect(self._save_image)

        self.actions_layout.addWidget(self.ocr_btn)
        self.actions_layout.addWidget(self.explain_btn)
        self.actions_layout.addWidget(self.copy_img_btn)
        self.actions_layout.addWidget(self.save_img_btn)

        card_layout.addLayout(self.actions_layout)
        outer.addWidget(self.card)

    def show_for_pixmap(
        self,
        pixmap: QPixmap,
        avoid_rect: QRect | None = None,
    ) -> None:
        self._pixmap = pixmap
        self._avoid_rect = avoid_rect
        self._result_text = ""

        # Update size badge
        self.size_badge.setText(f"{pixmap.width()} × {pixmap.height()} px")

        # Scale thumbnail smoothly
        scaled = pixmap.scaled(
            QSize(300, 130),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.thumb_label.setPixmap(scaled)

        self.result_container.hide()
        self.progress_bar.hide()
        self.status_label.hide()
        self.ocr_btn.setEnabled(True)
        self.explain_btn.setEnabled(True)
        self.copy_img_btn.setEnabled(True)
        self.save_img_btn.setEnabled(True)

        self.adjustSize()
        self._position_popup()
        self.show()
        self.raise_()

        if not self._dismiss_timer.isActive():
            self._mouse_was_down = bool(self._user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
            self._escape_was_down = bool(self._user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)
            self._dismiss_timer.start()

    def _start_worker(self, mode: str) -> None:
        if not self._pixmap:
            return

        self.ocr_btn.setEnabled(False)
        self.explain_btn.setEnabled(False)
        self.progress_bar.show()
        self.status_label.show()
        self.status_label.setText("Processing snip…")
        self.result_container.hide()
        self.adjustSize()

        config = self.store.load()
        keys = self.store.load_api_keys()

        worker = _SnipWorker(config, keys, self._pixmap.toImage(), mode)
        worker.signals.status.connect(self.status_label.setText)
        worker.signals.finished.connect(self._on_worker_finished)
        QThreadPool.globalInstance().start(worker)

    def _on_worker_finished(self, success: bool, text: str) -> None:
        self.progress_bar.hide()
        self.status_label.hide()
        self.ocr_btn.setEnabled(True)
        self.explain_btn.setEnabled(True)

        if success:
            self._result_text = text
            self.result_label.setText(text)
            self.result_container.show()
            # Auto-copy extracted OCR text
            clipboard = QApplication.clipboard()
            if clipboard:
                clipboard.setText(text)
            self.copy_result_btn.setText("✓ Copied to Clipboard")
            QTimer.singleShot(2500, lambda: self.copy_result_btn.setText("📋 Copy Text"))
        else:
            self.status_label.setText(f"⚠️ {text}")
            self.status_label.show()

        self.adjustSize()
        self._position_popup()

    def _copy_result_text(self) -> None:
        if not self._result_text:
            return
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(self._result_text)
            self.copy_result_btn.setText("✓ Copied!")
            QTimer.singleShot(2000, lambda: self.copy_result_btn.setText("📋 Copy Text"))

    def _copy_image(self) -> None:
        if not self._pixmap:
            return
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setPixmap(self._pixmap)
            self.copy_img_btn.setText("✓ Copied")
            QTimer.singleShot(2000, lambda: self.copy_img_btn.setText("📷 Copy"))

    def _save_image(self) -> None:
        if not self._pixmap:
            return
        self.dismiss()
        file_path, _ = QFileDialog.getSaveFileName(
            None,
            "Save Screenshot",
            str(Path.home() / "Pictures" / "screenshot.png"),
            "PNG Image (*.png);;JPEG Image (*.jpg)",
        )
        if file_path:
            self._pixmap.save(file_path)

    def dismiss(self) -> None:
        self._dismiss_timer.stop()
        self.hide()

    def _position_popup(self) -> None:
        avoid = self._avoid_rect
        if avoid is None:
            cursor = QCursor.pos()
            avoid = QRect(cursor.x() - 40, cursor.y() - 14, 80, 28)

        screen = QApplication.screenAt(avoid.center()) or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

        width = self.sizeHint().width()
        height = self.sizeHint().height()
        gap = 10

        x = avoid.center().x() - width // 2
        x = max(available.left() + 10, min(x, available.right() - width - 10))
        y = avoid.bottom() + gap
        if y + height > available.bottom() - 10:
            y = avoid.top() - height - gap
        y = max(available.top() + 10, min(y, available.bottom() - height - 10))

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
