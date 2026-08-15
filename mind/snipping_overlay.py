from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QCursor, QFont, QGuiApplication, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget


class SnippingOverlay(QWidget):
    """Full-screen interactive snipping tool overlay with a dark scrim and drag-to-crop selection."""

    snip_captured = Signal(QPixmap, QRect)
    snip_cancelled = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.MaximizeUsingFullscreenGeometryHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setCursor(Qt.CrossCursor)

        self._start_pos: QPoint | None = None
        self._current_pos: QPoint | None = None
        self._is_selecting = False
        self._screen_pixmap: QPixmap | None = None

    def start_snip(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if not screen:
            return

        # Capture the entire screen before displaying the overlay
        geo = screen.geometry()
        self._screen_pixmap = screen.grabWindow(0, geo.x(), geo.y(), geo.width(), geo.height())

        self.setGeometry(geo)
        self._start_pos = None
        self._current_pos = None
        self._is_selecting = False
        self.showFullScreen()
        self.raise_()
        self.activateWindow()

    def _selection_rect(self) -> QRect:
        if not self._start_pos or not self._current_pos:
            return QRect()
        return QRect(self._start_pos, self._current_pos).normalized()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._start_pos = event.pos()
            self._current_pos = event.pos()
            self._is_selecting = True
            self.update()
        elif event.button() == Qt.RightButton:
            self.cancel_snip()

    def mouseMoveEvent(self, event) -> None:
        if self._is_selecting:
            self._current_pos = event.pos()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._is_selecting:
            self._is_selecting = False
            rect = self._selection_rect()
            self.hide()

            if rect.width() > 12 and rect.height() > 12 and self._screen_pixmap:
                cropped = self._screen_pixmap.copy(rect)
                global_rect = QRect(
                    self.mapToGlobal(rect.topLeft()),
                    rect.size(),
                )
                self.snip_captured.emit(cropped, global_rect)
            else:
                self.snip_cancelled.emit()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Escape, Qt.Key_Back):
            self.cancel_snip()
        else:
            super().keyPressEvent(event)

    def cancel_snip(self) -> None:
        self._is_selecting = False
        self.hide()
        self.snip_cancelled.emit()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        # 1. Draw base screen capture
        if self._screen_pixmap:
            painter.drawPixmap(0, 0, self._screen_pixmap)

        # 2. Dim entire screen
        painter.fillRect(self.rect(), QColor(0, 0, 0, 110))

        # 3. If dragging, cut out and highlight selection
        rect = self._selection_rect()
        if not rect.isEmpty() and self._screen_pixmap:
            # Redraw un-dimmed cropped region
            painter.drawPixmap(rect, self._screen_pixmap, rect)

            # Border
            pen = QPen(QColor(13, 148, 136), 2, Qt.SolidLine)
            painter.setPen(pen)
            painter.drawRect(rect)

            # Size tooltip badge
            size_text = f"{rect.width()} × {rect.height()} px"
            painter.setFont(QFont("Segoe UI", 9, QFont.Bold))
            badge_rect = QRect(rect.left() + 4, rect.bottom() + 6, 110, 22)
            if badge_rect.bottom() > self.height() - 10:
                badge_rect.moveBottom(rect.top() - 6)
            painter.fillRect(badge_rect, QColor(20, 20, 20, 220))
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(badge_rect, Qt.AlignCenter, size_text)
