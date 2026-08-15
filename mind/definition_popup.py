from __future__ import annotations

import ctypes
import urllib.parse
from collections import OrderedDict
from ctypes import wintypes

from PySide6.QtCore import QObject, QPoint, QRect, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .dictionary import DefinitionLookupError, DefinitionResult, lookup_definition


VK_LBUTTON = 0x01
VK_ESCAPE = 0x1B


class _DefinitionWorkerSignals(QObject):
    completed = Signal(bool, object)


class _DefinitionWorker(QRunnable):
    def __init__(self, word: str):
        super().__init__()
        self.word = word
        self.signals = _DefinitionWorkerSignals()

    def run(self) -> None:
        try:
            self.signals.completed.emit(True, lookup_definition(self.word))
        except (DefinitionLookupError, OSError, ValueError) as exc:
            self.signals.completed.emit(False, str(exc))


class DefinitionPopup(QDialog):
    """Small, non-activating card positioned above a selected word."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._request_id = 0
        self._workers: dict[int, _DefinitionWorker] = {}
        self._cache: OrderedDict[str, DefinitionResult] = OrderedDict()
        self._avoid_rect: QRect | None = None
        self._current_word = ""
        self._source_url = ""
        self._user32 = ctypes.windll.user32
        self._user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self._mouse_was_down = False
        self._escape_was_down = False
        self.setObjectName("DefinitionPopup")
        self.setWindowTitle("Mind definition")
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumWidth(330)
        self.setMaximumWidth(430)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(7, 7, 7, 7)
        card = QWidget()
        card.setObjectName("DefinitionCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 15, 18, 14)
        layout.setSpacing(8)

        heading = QHBoxLayout()
        heading.setSpacing(8)
        self.word_label = QLabel()
        self.word_label.setObjectName("DefinitionWord")
        self.word_label.setTextFormat(Qt.PlainText)
        self.pronunciation_label = QLabel()
        self.pronunciation_label.setObjectName("DefinitionPronunciation")
        self.pronunciation_label.setTextFormat(Qt.PlainText)
        self.close_button = QPushButton("×")
        self.close_button.setObjectName("DefinitionClose")
        self.close_button.setAccessibleName("Close definition")
        self.close_button.setFocusPolicy(Qt.NoFocus)
        self.close_button.setFixedSize(28, 28)
        self.close_button.clicked.connect(self.dismiss)
        heading.addWidget(self.word_label)
        heading.addWidget(self.pronunciation_label)
        heading.addStretch()
        heading.addWidget(self.close_button)
        layout.addLayout(heading)

        self.part_label = QLabel()
        self.part_label.setObjectName("DefinitionPart")
        self.part_label.setTextFormat(Qt.PlainText)
        layout.addWidget(self.part_label)

        self.body_label = QLabel()
        self.body_label.setObjectName("DefinitionBody")
        self.body_label.setTextFormat(Qt.PlainText)
        self.body_label.setWordWrap(True)
        self.body_label.setMinimumWidth(294)
        self.body_label.setMaximumWidth(386)
        layout.addWidget(self.body_label)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(10)

        self.source_button = QPushButton()
        self.source_button.setObjectName("DefinitionSource")
        self.source_button.setFocusPolicy(Qt.NoFocus)
        self.source_button.setCursor(Qt.PointingHandCursor)
        self.source_button.clicked.connect(self._open_source)
        footer.addWidget(self.source_button, 0, Qt.AlignLeft)

        footer.addStretch()

        self.google_button = QPushButton("Search Google ↗")
        self.google_button.setObjectName("DefinitionGoogle")
        self.google_button.setFocusPolicy(Qt.NoFocus)
        self.google_button.setCursor(Qt.PointingHandCursor)
        self.google_button.clicked.connect(self._search_google)
        footer.addWidget(self.google_button, 0, Qt.AlignRight)

        layout.addLayout(footer)
        outer.addWidget(card)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.dismiss)
        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setInterval(20)
        self._dismiss_timer.timeout.connect(self._poll_for_dismissal)

    def lookup(self, word: str, avoid_rect: tuple[int, int, int, int] | None) -> None:
        self._request_id += 1
        request_id = self._request_id
        self._avoid_rect = self._rect_from_tuple(avoid_rect)
        cache_key = word.casefold()
        cached = self._cache.get(cache_key)
        if cached is not None:
            self._cache.move_to_end(cache_key)
            self.show_result(cached, avoid_rect)
            return

        self._show_loading(word)
        worker = _DefinitionWorker(word)
        self._workers[request_id] = worker
        worker.signals.completed.connect(
            lambda ok, payload, current=request_id, key=cache_key: self._lookup_completed(
                current, key, ok, payload
            )
        )
        QThreadPool.globalInstance().start(worker)

    def dismiss(self) -> None:
        """Hide the card and make any unfinished lookup result stale."""
        self._request_id += 1
        self._hide_timer.stop()
        self._dismiss_timer.stop()
        self.hide()

    def show_result(
        self,
        result: DefinitionResult,
        avoid_rect: tuple[int, int, int, int] | None = None,
    ) -> None:
        if avoid_rect is not None:
            self._avoid_rect = self._rect_from_tuple(avoid_rect)
        self._current_word = result.word
        self.word_label.setText(result.word)
        self.pronunciation_label.setText(
            f"/{result.pronunciation}/" if result.pronunciation else ""
        )
        first_part = result.senses[0].part_of_speech if result.senses else ""
        self.part_label.setText(first_part.upper() or "DEFINITION")
        body_parts: list[str] = []
        for index, sense in enumerate(result.senses, start=1):
            prefix = f"{index}. " if len(result.senses) > 1 else ""
            part = ""
            if index > 1 and sense.part_of_speech and sense.part_of_speech != first_part:
                part = f"{sense.part_of_speech} · "
            body_parts.append(f"{prefix}{part}{sense.definition}")
        self.body_label.setText("\n\n".join(body_parts))
        self._source_url = result.source_url
        self.source_button.setText(f"Source: {result.source_name}  ↗")
        self.source_button.setVisible(bool(self._source_url))
        self.google_button.setVisible(bool(self._current_word))
        self._show_above_selection()
        self._hide_timer.start(12000)

    def _show_loading(self, word: str) -> None:
        self._current_word = word
        self.word_label.setText(word)
        self.pronunciation_label.clear()
        self.part_label.setText("DEFINITION")
        self.body_label.setText("Looking up this word…")
        self.source_button.hide()
        self._source_url = ""
        self.google_button.setVisible(bool(self._current_word))
        self._show_above_selection()
        self._hide_timer.start(8000)

    def _show_error(self, message: str) -> None:
        self.pronunciation_label.clear()
        self.part_label.setText("DEFINITION")
        self.body_label.setText(message)
        self.source_button.hide()
        self._source_url = ""
        self.google_button.setVisible(bool(self._current_word))
        self._show_above_selection()
        self._hide_timer.start(4500)

    def _lookup_completed(self, request_id: int, cache_key: str, ok: bool, payload: object) -> None:
        self._workers.pop(request_id, None)
        if request_id != self._request_id:
            return
        if ok and isinstance(payload, DefinitionResult):
            self._cache[cache_key] = payload
            self._cache.move_to_end(cache_key)
            while len(self._cache) > 128:
                self._cache.popitem(last=False)
            self.show_result(payload)
            return
        self._show_error(str(payload))

    def _show_above_selection(self) -> None:
        self.adjustSize()
        avoid = self._avoid_rect
        if avoid is None:
            from PySide6.QtGui import QCursor

            cursor = QCursor.pos()
            avoid = QRect(cursor.x() - 60, cursor.y() - 18, 120, 36)
        screen = QApplication.screenAt(avoid.center()) or QApplication.primaryScreen()
        available = screen.availableGeometry()
        width = min(self.sizeHint().width(), self.maximumWidth())
        self.resize(width, self.sizeHint().height())
        height = self.height()
        gap = 9
        x = avoid.center().x() - width // 2
        x = max(available.left() + 8, min(x, available.right() - width - 8))
        y = avoid.top() - height - gap
        if y < available.top() + 8:
            y = avoid.bottom() + gap
        y = max(available.top() + 8, min(y, available.bottom() - height - 8))
        self.move(QPoint(x, y))
        self.show()
        self.raise_()
        if not self._dismiss_timer.isActive():
            self._mouse_was_down = bool(
                self._user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000
            )
            self._escape_was_down = bool(
                self._user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000
            )
            self._dismiss_timer.start()

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
        self._dismiss_if_outside(QPoint(int(point.x), int(point.y)))

    def _dismiss_if_outside(self, screen_point: QPoint) -> None:
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

    def _open_source(self) -> None:
        if self._source_url:
            QDesktopServices.openUrl(QUrl(self._source_url))
        self.dismiss()

    def _search_google(self) -> None:
        word = (self._current_word or self.word_label.text()).strip()
        if word:
            query = urllib.parse.quote_plus(word)
            QDesktopServices.openUrl(QUrl(f"https://www.google.com/search?q={query}"))
        self.dismiss()
