from __future__ import annotations

import ctypes
from ctypes import wintypes
from PySide6.QtCore import QObject, QPoint, QRect, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QCursor, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import re

from .config_store import ConfigStore
from .converter_tools import ConversionResult
from .transform_client import TransformError, transform_text


VK_LBUTTON = 0x01
VK_ESCAPE = 0x1B

ASK_AI_SYSTEM_PROMPT = (
    "You are a fast, concise AI assistant for desktop tooltips. "
    "Provide a brief, direct, and compact answer to the user's question or math/number problem.\n"
    "Rules:\n"
    "1. Keep your entire answer short (1 to 3 sentences maximum, strictly under 50 words).\n"
    "2. For math/equations: Write clean, readable plain text (e.g. 3x^2, x = 5, 60 mph). Never use LaTeX dollar signs ($ or $$).\n"
    "3. Do not use bullet points, lists, greetings, headers, or multi-paragraph essays.\n"
    "4. Answer directly with factual clarity and no filler."
)


def _clean_math_formatting(text: str) -> str:
    """Strip LaTeX math dollar signs and markdown wrapper artifacts for clean display."""
    if not text:
        return ""
    cleaned = re.sub(r"\$\$(.*?)\$\$", r"\1", text)
    cleaned = re.sub(r"(?<!\w)\$([^$\n]+?)\$(?!\w)", r"\1", cleaned)
    return cleaned.strip()


class _AskAiWorkerSignals(QObject):
    completed = Signal(bool, str)


class _AskAiWorker(QRunnable):
    def __init__(self, config: dict, keys: list[str], question: str):
        super().__init__()
        self.config = config
        self.keys = keys
        self.question = question
        self.signals = _AskAiWorkerSignals()

    def run(self) -> None:
        try:
            answer = transform_text(
                self.config,
                self.keys,
                self.question,
                prompt="Answer this question.",
                system_prompt_override=ASK_AI_SYSTEM_PROMPT,
            )
            self.signals.completed.emit(True, _clean_math_formatting(answer))
        except Exception as exc:
            self.signals.completed.emit(False, str(exc))


class AskAiPopup(QDialog):
    # Asked for when the phone should ring a number the user has selected.
    # The popup does not know about phones; whoever owns one listens.
    call_requested = Signal(str)

    """Floating pill button that expands into an answer tooltip card with Copy action."""

    def __init__(self, store: ConfigStore, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self._question = ""
        self._answer = ""
        self._avoid_rect: QRect | None = None
        self._request_id = 0
        self._is_expanded = False
        self._workers: dict[int, _AskAiWorker] = {}

        self._user32 = ctypes.windll.user32
        self._user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self._mouse_was_down = False
        self._escape_was_down = False

        self.setObjectName("AskAiPopup")
        self.setWindowTitle("Ask AI")
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self._build_ui()

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.dismiss)

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setInterval(25)
        self._dismiss_timer.timeout.connect(self._poll_for_dismissal)

        self._copy_reset_timer = QTimer(self)
        self._copy_reset_timer.setSingleShot(True)
        self._copy_reset_timer.timeout.connect(self._reset_copy_button)

    def _build_ui(self) -> None:
        self.outer_layout = QVBoxLayout(self)
        self.outer_layout.setContentsMargins(6, 6, 6, 6)

        # 1. Compact Pill View
        self.pill_widget = QWidget()
        self.pill_widget.setObjectName("AskAiPill")
        pill_layout = QHBoxLayout(self.pill_widget)
        pill_layout.setContentsMargins(0, 0, 0, 0)

        self.ask_button = QPushButton("✦ Ask AI")
        self.ask_button.setObjectName("AskAiPillButton")
        self.ask_button.setCursor(Qt.PointingHandCursor)
        self.ask_button.setFocusPolicy(Qt.NoFocus)
        self.ask_button.clicked.connect(self._ask_ai_clicked)
        pill_layout.addWidget(self.ask_button)
        self.outer_layout.addWidget(self.pill_widget)

        # 2. Expanded Answer Card View
        self.card_widget = QWidget()
        self.card_widget.setObjectName("AskAiCard")
        card_layout = QVBoxLayout(self.card_widget)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(8)

        # Header
        header_layout = QHBoxLayout()
        header_layout.setSpacing(6)

        self.title_icon = QLabel("✦")
        self.title_icon.setObjectName("AskAiCardIcon")
        self.title_label = QLabel("Ask AI")
        self.title_label.setObjectName("AskAiCardTitle")

        self.close_button = QPushButton("×")
        self.close_button.setObjectName("DefinitionClose")
        self.close_button.setAccessibleName("Close answer")
        self.close_button.setFocusPolicy(Qt.NoFocus)
        self.close_button.setFixedSize(26, 26)
        self.close_button.clicked.connect(self.dismiss)

        header_layout.addWidget(self.title_icon)
        header_layout.addWidget(self.title_label)
        header_layout.addStretch()
        header_layout.addWidget(self.close_button)
        card_layout.addLayout(header_layout)

        # Question Preview
        self.question_label = QLabel()
        self.question_label.setObjectName("AskAiQuestion")
        self.question_label.setTextFormat(Qt.PlainText)
        self.question_label.setWordWrap(True)
        self.question_label.setMaximumWidth(380)
        card_layout.addWidget(self.question_label)

        # Progress Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("AskAiProgress")
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFixedHeight(3)
        self.progress_bar.hide()
        card_layout.addWidget(self.progress_bar)

        # Answer Body (in scroll area if long)
        self.answer_label = QLabel()
        self.answer_label.setObjectName("AskAiAnswer")
        self.answer_label.setTextFormat(Qt.PlainText)
        self.answer_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.answer_label.setWordWrap(True)
        self.answer_label.setMinimumWidth(320)
        self.answer_label.setMaximumWidth(420)
        card_layout.addWidget(self.answer_label)

        # Actions Footer
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(6)

        self.copy_button = QPushButton("📋 Copy")
        self.copy_button.setObjectName("AskAiCopyButton")
        self.copy_button.setCursor(Qt.PointingHandCursor)
        self.copy_button.setFocusPolicy(Qt.NoFocus)
        self.copy_button.clicked.connect(self._copy_answer)
        actions_layout.addWidget(self.copy_button)

        self.viber_button = QPushButton("💬 Viber")
        self.viber_button.setObjectName("AskAiActionBtn")
        self.viber_button.setCursor(Qt.PointingHandCursor)
        self.viber_button.setFocusPolicy(Qt.NoFocus)
        self.viber_button.clicked.connect(self._open_viber)
        self.viber_button.hide()
        actions_layout.addWidget(self.viber_button)

        self.telegram_button = QPushButton("✈ Telegram")
        self.telegram_button.setObjectName("AskAiActionBtn")
        self.telegram_button.setCursor(Qt.PointingHandCursor)
        self.telegram_button.setFocusPolicy(Qt.NoFocus)
        self.telegram_button.clicked.connect(self._open_telegram)
        self.telegram_button.hide()
        actions_layout.addWidget(self.telegram_button)

        self.whatsapp_button = QPushButton("🟢 WhatsApp")
        self.whatsapp_button.setObjectName("AskAiActionBtn")
        self.whatsapp_button.setCursor(Qt.PointingHandCursor)
        self.whatsapp_button.setFocusPolicy(Qt.NoFocus)
        self.whatsapp_button.clicked.connect(self._open_whatsapp)
        self.whatsapp_button.hide()
        actions_layout.addWidget(self.whatsapp_button)

        self.tel_button = QPushButton("📞 Call")
        self.tel_button.setObjectName("AskAiActionBtn")
        self.tel_button.setCursor(Qt.PointingHandCursor)
        self.tel_button.setFocusPolicy(Qt.NoFocus)
        self.tel_button.clicked.connect(self._open_tel)
        self.tel_button.hide()
        actions_layout.addWidget(self.tel_button)

        # Ringing it on the phone in your pocket, rather than handing the
        # number to whatever this PC thinks tel: means.
        self.phone_call_button = QPushButton("📱 Call from phone")
        self.phone_call_button.setObjectName("AskAiActionBtn")
        self.phone_call_button.setCursor(Qt.PointingHandCursor)
        self.phone_call_button.setFocusPolicy(Qt.NoFocus)
        self.phone_call_button.clicked.connect(self._call_from_phone)
        self.phone_call_button.hide()
        actions_layout.addWidget(self.phone_call_button)

        actions_layout.addStretch()

        card_layout.addLayout(actions_layout)
        self.outer_layout.addWidget(self.card_widget)
        self.card_widget.hide()

    def show_phone_actions(
        self,
        phone_info: dict[str, str],
        avoid_rect: tuple[int, int, int, int] | None = None,
        can_call: bool = False,
    ) -> None:
        self._question = phone_info.get("raw", "")
        self._answer = phone_info.get("formatted", "")
        self._phone_info = phone_info
        self._is_expanded = True
        self._avoid_rect = self._rect_from_tuple(avoid_rect)

        self.pill_widget.hide()
        self.card_widget.show()

        self.title_icon.setText("📞")
        self.title_label.setText("Maldivian Phone")
        # Replaced by the name if the phone knows one, which is why this says
        # what it says rather than naming the number twice.
        self.question_label.setText("Quick Contact Actions:")
        self.answer_label.setText(phone_info.get("formatted", ""))
        self.progress_bar.hide()
        self.copy_button.show()
        self.viber_button.show()
        self.telegram_button.show()
        self.whatsapp_button.show()
        self.tel_button.show()
        self.phone_call_button.setVisible(bool(can_call))
        self._reset_copy_button()

        self.adjustSize()
        self._position_popup()
        self.show()
        self._hide_timer.start(18000)
        self._dismiss_timer.start()

    def set_contact_name(self, number: str, name: str) -> None:
        """Put a name on the card, if the card is still about that number.

        The lookup outlives the tooltip easily - a number is selected, the card
        is dismissed, and the answer arrives afterwards - so what it is about
        is checked before anything is written.
        """
        info = getattr(self, "_phone_info", None)
        if not name or not info:
            return
        if number not in {info.get("local", ""), info.get("digits", ""), info.get("international", "")}:
            return
        # Only the heading. The number is already on the card, and writing it
        # into the line above it would print it twice.
        self.title_label.setText(name)
        self.adjustSize()
        self._position_popup()

    def _call_from_phone(self) -> None:
        info = getattr(self, "_phone_info", None)
        if not info:
            return
        self.call_requested.emit(info.get("international", "") or info.get("local", ""))
        self.dismiss()

    def _open_viber(self) -> None:
        if hasattr(self, "_phone_info") and self._phone_info.get("viber_url"):
            QDesktopServices.openUrl(QUrl(self._phone_info["viber_url"]))

    def _open_telegram(self) -> None:
        if hasattr(self, "_phone_info") and self._phone_info.get("telegram_url"):
            QDesktopServices.openUrl(QUrl(self._phone_info["telegram_url"]))

    def _open_whatsapp(self) -> None:
        if hasattr(self, "_phone_info") and self._phone_info.get("whatsapp_url"):
            QDesktopServices.openUrl(QUrl(self._phone_info["whatsapp_url"]))

    def _open_tel(self) -> None:
        if hasattr(self, "_phone_info") and self._phone_info.get("tel_url"):
            QDesktopServices.openUrl(QUrl(self._phone_info["tel_url"]))

    def _hide_phone_buttons(self) -> None:
        self.viber_button.hide()
        self.telegram_button.hide()
        self.whatsapp_button.hide()
        self.tel_button.hide()
        self.phone_call_button.hide()

    def show_local_math_result(
        self,
        expression: str,
        result: str,
        avoid_rect: tuple[int, int, int, int] | None = None,
    ) -> None:
        self._question = expression.strip()
        self._answer = result.strip()
        self._is_expanded = True
        self._avoid_rect = self._rect_from_tuple(avoid_rect)

        self.pill_widget.hide()
        self.card_widget.show()

        self.title_icon.setText("🧮")
        self.title_label.setText("Calculator")
        display_expr = self._question if len(self._question) <= 80 else self._question[:77] + "…"
        self.question_label.setText(display_expr)
        self.answer_label.setText(f"= {self._answer}")
        self.progress_bar.hide()
        self.copy_button.show()
        self._hide_phone_buttons()
        self._reset_copy_button()

        self.adjustSize()
        self._position_popup()
        self.show()
        self._hide_timer.start(15000)
        self._dismiss_timer.start()

    def show_converter_result(
        self,
        result: ConversionResult,
        avoid_rect: tuple[int, int, int, int] | None = None,
    ) -> None:
        self._question = result.input_text.strip()
        self._answer = result.output_text.strip()
        self._is_expanded = True
        self._avoid_rect = self._rect_from_tuple(avoid_rect)

        self.pill_widget.hide()
        self.card_widget.show()

        self.title_icon.setText(result.icon)
        self.title_label.setText(result.title)
        display_input = self._question if len(self._question) <= 80 else self._question[:77] + "…"
        self.question_label.setText(f"Input: {display_input}")
        self.answer_label.setText(self._answer)
        self.progress_bar.hide()
        self.copy_button.show()
        self._hide_phone_buttons()
        self._reset_copy_button()

        self.adjustSize()
        self._position_popup()
        self.show()
        self._hide_timer.start(18000)
        self._dismiss_timer.start()

    def show_pill_for_question(
        self,
        question: str,
        avoid_rect: tuple[int, int, int, int] | None = None,
    ) -> None:
        self._question = question.strip()
        self._answer = ""
        self._is_expanded = False
        self._avoid_rect = self._rect_from_tuple(avoid_rect)

        self.title_icon.setText("✦")
        self.title_label.setText("Ask AI")
        self._hide_phone_buttons()
        self.card_widget.hide()
        self.pill_widget.show()
        self.adjustSize()

        self._position_popup()
        self.show()
        self._hide_timer.start(8000)
        self._dismiss_timer.start()

    def _ask_ai_clicked(self) -> None:
        self._is_expanded = True
        self._request_id += 1
        request_id = self._request_id

        self.pill_widget.hide()
        self.card_widget.show()

        display_q = self._question if len(self._question) <= 120 else self._question[:117] + "…"
        self.question_label.setText(f"“{display_q}”")
        self.answer_label.setText("Thinking…")
        self.progress_bar.show()
        self.copy_button.hide()
        self._hide_phone_buttons()
        self.adjustSize()
        self._position_popup()

        config = self.store.load()
        keys = self.store.get_keys(config)

        worker = _AskAiWorker(config, keys, self._question)
        self._workers[request_id] = worker
        worker.signals.completed.connect(
            lambda ok, answer, req=request_id: self._on_answer_received(req, ok, answer)
        )
        QThreadPool.globalInstance().start(worker)
        self._hide_timer.start(35000)

    def _on_answer_received(self, request_id: int, ok: bool, answer: str) -> None:
        self._workers.pop(request_id, None)
        if request_id != self._request_id:
            return
        self.progress_bar.hide()
        if ok and answer:
            self._answer = _clean_math_formatting(answer)
            self.answer_label.setText(self._answer)
            self.copy_button.show()
            self._reset_copy_button()
        else:
            self._answer = ""
            error_msg = answer if answer else "Could not get an answer. Please check your API key and connection."
            self.answer_label.setText(error_msg)
            self.copy_button.hide()

        self.adjustSize()
        self._position_popup()
        self._hide_timer.start(25000)

    def _copy_answer(self) -> None:
        if not self._answer:
            return
        clipboard = QApplication.clipboard()
        clipboard.setText(self._answer)
        self.copy_button.setText("✓ Copied!")
        self._copy_reset_timer.start(2000)

    def _reset_copy_button(self) -> None:
        self.copy_button.setText("📋 Copy")

    def dismiss(self) -> None:
        self._request_id += 1
        self._workers.clear()
        self._hide_timer.stop()
        self._dismiss_timer.stop()
        self._copy_reset_timer.stop()
        self.hide()

    def _position_popup(self) -> None:
        margin = 10
        pos = QCursor.pos()
        bounds = self._avoid_rect or QRect(pos.x() - 8, pos.y() - 10, 16, 20)
        screen = QGuiApplication.screenAt(bounds.center()) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

        width = self.sizeHint().width()
        height = self.sizeHint().height()

        x = bounds.center().x() - width // 2
        x = max(avail.left() + margin, min(x, avail.right() - width - margin))

        # Prefer showing above selection
        y = bounds.top() - height - margin
        if y < avail.top() + margin:
            y = bounds.bottom() + margin

        self.setGeometry(x, y, width, height)

    def _poll_for_dismissal(self) -> None:
        user32 = self._user32
        mouse_down = bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
        escape_down = bool(user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)

        if escape_down and not self._escape_was_down:
            self.dismiss()
            return
        self._escape_was_down = escape_down

        if mouse_down and not self._mouse_was_down:
            point = wintypes.POINT()
            if user32.GetCursorPos(ctypes.byref(point)):
                cursor = QPoint(point.x, point.y)
                if not self.geometry().contains(cursor):
                    self.dismiss()
                    return
        self._mouse_was_down = mouse_down

    @staticmethod
    def _rect_from_tuple(rect_tuple: tuple[int, int, int, int] | None) -> QRect | None:
        if rect_tuple is None:
            return None
        x1, y1, x2, y2 = rect_tuple
        return QRect(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))
