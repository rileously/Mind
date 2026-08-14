from __future__ import annotations

from PySide6.QtCore import QObject, QPoint, QRect, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QCursor, QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .config_store import ConfigStore
from .math_tools import MathInputError, normalize_math_text, sum_number_list
from .ocr import OcrError, extract_text_from_image
from .selection import ClipboardImageSession, SelectionSession
from .theme import app_icon
from .text_tools import (
    LOCAL_TEXT_TOOLS,
    TextToolError,
    TextToolResult,
    run_text_tool,
)
from .text_direction import (
    DHIVEHI_RETRY_PROMPT,
    common_dhivehi_translation,
    contains_thaana,
    is_clean_dhivehi_translation,
    is_dhivehi_trigger,
    prepare_dhivehi_output,
)
from .transform_client import TransformError, transform_text


ACTION_NAMES = {
    "fix": "Fix writing",
    "improve": "Improve clarity",
    "summarize": "Summarize",
    "action-items": "Extract action items",
    "english": "Translate to English",
    "bullets": "Turn into bullets",
    "shorten": "Make concise",
    "expand": "Add detail",
    "formal": "Make formal",
    "casual": "Make casual",
    "emoji": "Add emojis",
    "human": "Sound natural",
    "reply": "Write a reply",
    "dhivehi": "Translate to Dhivehi",
}
ACTION_ICONS = {
    "fix": "✓",
    "improve": "✦",
    "summarize": "≡",
    "action-items": "☑",
    "english": "A",
    "bullets": "•",
    "shorten": "↘",
    "expand": "↗",
    "formal": "◆",
    "casual": "◇",
    "emoji": "☺",
    "human": "◌",
    "reply": "↩",
    "dhivehi": "ހ",
    "local-clean-spacing": "⌁",
    "local-writing-stats": "#",
}

PREFERRED_ACTIONS = (
    "fix",
    "improve",
    "summarize",
    "action-items",
    "formal",
    "shorten",
    "reply",
    "dhivehi",
    "local-clean-spacing",
    "local-writing-stats",
)
IMAGE_SUMMARY_PROMPT = (
    "Summarize the extracted image text clearly and concisely. Preserve important names, "
    "numbers, dates, decisions, and action items. Return only the summary."
)
IMAGE_MATH_PROMPT = (
    "Solve the mathematical problem extracted from an image. Correct obvious OCR mistakes "
    "in operators or notation only when unambiguous (for example ×, ÷, −, superscripts, or "
    "a letter confused with a digit). Handle arithmetic, algebraic equations, percentages, "
    "and short word problems. Show compact, readable steps and end with 'Answer:' followed "
    "by the final result. Do not add unrelated explanation."
)


class WorkerSignals(QObject):
    finished = Signal(bool, str)
    status = Signal(str)


def _transform_for_action(
    config: dict,
    keys: list[str],
    text: str,
    prompt: str,
    trigger: str,
) -> str:
    if trigger == "ocr-math":
        text = normalize_math_text(text)
    dhivehi = is_dhivehi_trigger(trigger)
    needs_strong_gemini = dhivehi or trigger == "ocr-math"
    dhivehi_model = (
        "gemini-3.6-flash"
        if needs_strong_gemini and config.get("provider") == "gemini"
        else None
    )
    result = common_dhivehi_translation(text) if dhivehi else None
    if result is None:
        result = transform_text(
            config,
            keys,
            text,
            prompt,
            temperature_override=0.0 if dhivehi else None,
            model_override=dhivehi_model,
        )
    if dhivehi:
        if not is_clean_dhivehi_translation(result, text):
            result = transform_text(
                config,
                keys,
                text,
                DHIVEHI_RETRY_PROMPT,
                temperature_override=0.0,
                model_override=dhivehi_model,
            )
        if not is_clean_dhivehi_translation(result, text):
            raise TransformError(
                "The model returned translation notes instead of clean Dhivehi. Please try again."
            )
        result = prepare_dhivehi_output(result)
    return result


class TransformWorker(QRunnable):
    def __init__(self, config: dict, keys: list[str], text: str, prompt: str, trigger: str):
        super().__init__()
        self.config = config
        self.keys = keys
        self.text = text
        self.prompt = prompt
        self.trigger = trigger
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            result = _transform_for_action(
                self.config, self.keys, self.text, self.prompt, self.trigger
            )
            self.signals.finished.emit(True, result)
        except (TransformError, OSError, ValueError) as exc:
            self.signals.finished.emit(False, str(exc))


class ImageOcrWorker(QRunnable):
    def __init__(
        self,
        config: dict,
        keys: list[str],
        session: ClipboardImageSession,
        action: dict,
    ):
        super().__init__()
        self.config = config
        self.keys = keys
        self.image = session.image.copy()
        self.action = action
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            self.signals.status.emit("Reading image locally…")
            text = extract_text_from_image(self.image)
            trigger = str(self.action.get("trigger", ""))
            if trigger == "ocr-extract":
                result = text
            elif trigger == "ocr-sum":
                result = sum_number_list(text)
            else:
                self.signals.status.emit("Transforming extracted text…")
                result = _transform_for_action(
                    self.config,
                    self.keys,
                    text,
                    str(self.action.get("prompt", "")),
                    trigger,
                )
            self.signals.finished.emit(True, result)
        except (MathInputError, OcrError, TransformError, OSError, ValueError) as exc:
            self.signals.finished.emit(False, str(exc))


class MindPalette(QDialog):
    completed = Signal(bool, str)

    def __init__(
        self,
        store: ConfigStore,
        session: SelectionSession | ClipboardImageSession,
        parent: QWidget | None = None,
        avoid_rect: tuple[int, int, int, int] | None = None,
    ):
        super().__init__(parent)
        self.store = store
        self.session = session
        self._avoid_rect = (
            QRect(
                avoid_rect[0],
                avoid_rect[1],
                max(1, avoid_rect[2] - avoid_rect[0] + 1),
                max(1, avoid_rect[3] - avoid_rect[1] + 1),
            )
            if avoid_rect
            else None
        )
        self._working = False
        self.setWindowTitle("Mind Palette")
        self.setObjectName("PaletteDialog")
        # Qt.Popup dismisses the Palette when the selection is cleared by clicking back
        # into the source text, clicking another window, or switching applications.
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        config = self.store.load()
        width = int(config.get("mind_palette_width", 390))
        self.setFixedWidth(width if width in {340, 390, 460} else 390)

        shell = QWidget(self)
        shell.setObjectName("PaletteShell")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(shell)
        layout = QVBoxLayout(shell)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(12)

        heading_row = QHBoxLayout()
        logo = QLabel()
        logo.setObjectName("BrandLogo")
        logo.setFixedSize(34, 34)
        logo.setAlignment(Qt.AlignCenter)
        logo.setPixmap(app_icon(34).pixmap(32, 32))
        heading = QLabel("Quick actions")
        heading.setObjectName("SectionTitle")
        self.is_image = getattr(session, "kind", "text") == "image"
        hint = QLabel("Image actions" if self.is_image else "Choose an action")
        hint.setObjectName("SoftBadge")
        heading_row.addWidget(logo)
        heading_row.addSpacing(2)
        heading_row.addWidget(heading)
        heading_row.addStretch()
        heading_row.addWidget(hint)
        layout.addLayout(heading_row)

        if self.is_image:
            self.preview = QLabel()
            self.preview.setObjectName("PaletteImagePreview")
            self.preview.setAlignment(Qt.AlignCenter)
            self.preview.setFixedHeight(130)
            preview_width = max(280, self.width() - 36)
            pixmap = QPixmap.fromImage(session.image).scaled(
                preview_width,
                120,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            self.preview.setPixmap(pixmap)
            self.preview.setVisible(bool(config.get("mind_palette_show_preview", True)))
            layout.addWidget(self.preview)
            image_info = QLabel(
                f"{session.image.width()} × {session.image.height()} image · OCR runs locally"
            )
            image_info.setObjectName("Muted")
            layout.addWidget(image_info)
        else:
            preview = self.session.text.replace("\r", " ").replace("\n", " ").strip()
            if len(preview) > 100:
                preview = preview[:97].rstrip() + "…"
            self.preview = QLabel(preview)
            self.preview.setObjectName("PalettePreview")
            self.preview.setWordWrap(True)
            if contains_thaana(preview):
                self.preview.setProperty("thaana", True)
                self.preview.setLayoutDirection(Qt.RightToLeft)
                # AlignAbsolute prevents Qt from mirroring AlignRight back to the left when
                # the widget itself uses a right-to-left layout direction.
                self.preview.setAlignment(Qt.AlignRight | Qt.AlignVCenter | Qt.AlignAbsolute)
                self.preview.setText(prepare_dhivehi_output(preview))
            self.preview.setVisible(bool(config.get("mind_palette_show_preview", True)))
            layout.addWidget(self.preview)

        self.action_grid = QGridLayout()
        self.action_grid.setHorizontalSpacing(8)
        self.action_grid.setVerticalSpacing(8)
        self.actions = self._available_image_actions() if self.is_image else self._available_actions()
        self.buttons: list[QPushButton] = []
        columns = int(config.get("mind_palette_columns", 2))
        columns = columns if columns in {1, 2} else 2
        for index, command in enumerate(self.actions):
            trigger = str(command.get("trigger", ""))
            title = str(command.get("label") or ACTION_NAMES.get(trigger, trigger.replace("-", " ").title()))
            icon = ACTION_ICONS.get(trigger, "→")
            button = QPushButton(f"{icon}   {title}")
            button.setObjectName("PaletteAction")
            button.clicked.connect(lambda checked=False, item=command: self._run(item))
            self.action_grid.addWidget(button, index // columns, index % columns)
            self.buttons.append(button)
        layout.addLayout(self.action_grid)
        if not self.actions:
            empty = QLabel("Enable an AI command in Mind to use the palette.")
            empty.setObjectName("Muted")
            empty.setWordWrap(True)
            layout.addWidget(empty)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        self.progress.hide()
        layout.addWidget(self.progress)

        footer = QHBoxLayout()
        self.status = QLabel("OCR stays local" if self.is_image else "Transforms in place")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        footer.addWidget(self.status)
        footer.addStretch()
        escape = QLabel("Esc")
        escape.setObjectName("Kbd")
        footer.addWidget(escape)
        layout.addLayout(footer)

    def show_near_cursor(self) -> None:
        self.adjustSize()
        cursor = QCursor.pos()
        avoid = self._avoid_rect or QRect(cursor.x() - 8, cursor.y() - 12, 16, 24)
        screen = QApplication.screenAt(avoid.center()) or QApplication.primaryScreen()
        available = screen.availableGeometry()
        width = self.width()
        height = self.height()
        gap = 14
        expanded = avoid.adjusted(-8, -8, 8, 8)
        candidates = [
            QPoint(avoid.left(), avoid.bottom() + gap),
            QPoint(avoid.right() - width, avoid.bottom() + gap),
            QPoint(avoid.left(), avoid.top() - height - gap),
            QPoint(avoid.right() - width, avoid.top() - height - gap),
            QPoint(avoid.right() + gap, avoid.center().y() - height // 2),
            QPoint(avoid.left() - width - gap, avoid.center().y() - height // 2),
        ]
        chosen = next((
            point for point in candidates
            if available.contains(QRect(point, self.size()))
            and not QRect(point, self.size()).intersects(expanded)
        ), None)
        if chosen is None:
            margin = 8
            corners = [
                QPoint(available.left() + margin, available.top() + margin),
                QPoint(available.right() - width - margin, available.top() + margin),
                QPoint(available.left() + margin, available.bottom() - height - margin),
                QPoint(available.right() - width - margin, available.bottom() - height - margin),
            ]

            def placement_score(point: QPoint) -> tuple[int, int]:
                rect = QRect(point, self.size())
                overlap = rect.intersected(expanded)
                overlap_area = overlap.width() * overlap.height() if not overlap.isEmpty() else 0
                distance = abs(rect.center().x() - avoid.center().x()) + abs(
                    rect.center().y() - avoid.center().y()
                )
                return overlap_area, -distance

            chosen = min(corners, key=placement_score)
        self.move(chosen)
        self.show()
        self.raise_()
        self.activateWindow()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Escape and not self._working:
            self.reject()
            return
        if (
            event.key() in {Qt.Key_Backspace, Qt.Key_Delete}
            and not self._working
            and isinstance(self.session, SelectionSession)
        ):
            virtual_key = 0x08 if event.key() == Qt.Key_Backspace else 0x2E
            session = self.session
            event.accept()
            self.hide()
            self.reject()
            QTimer.singleShot(80, lambda: session.delete_selected_text(virtual_key))
            return
        super().keyPressEvent(event)

    def _available_actions(self) -> list[dict]:
        commands = [
            command for command in self.store.load_commands()
            if command.get("type", "ai") == "ai" and command.get("enabled", True)
        ]
        by_trigger = {str(command.get("trigger", "")): command for command in commands}
        by_trigger.update({
            tool.trigger: {
                "trigger": tool.trigger,
                "type": "local-text",
                "label": tool.label,
                "description": tool.description,
            }
            for tool in LOCAL_TEXT_TOOLS
        })
        config = self.store.load()
        configured = config.get("mind_palette_actions", [])
        chosen = [by_trigger[trigger] for trigger in configured if isinstance(trigger, str) and trigger in by_trigger]
        if not chosen:
            chosen = [by_trigger[trigger] for trigger in PREFERRED_ACTIONS if trigger in by_trigger]
        return chosen[:10]

    def _available_image_actions(self) -> list[dict]:
        commands = [
            command for command in self.store.load_commands()
            if command.get("type", "ai") == "ai" and command.get("enabled", True)
        ]
        by_trigger = {str(command.get("trigger", "")): command for command in commands}
        actions: list[dict] = [
            {"trigger": "ocr-extract", "type": "local", "label": "Extract text"},
            {"trigger": "ocr-sum", "type": "local", "label": "Sum numbers"},
            {
                "trigger": "ocr-math",
                "type": "ai",
                "label": "Solve math",
                "prompt": IMAGE_MATH_PROMPT,
            },
        ]
        if "dhivehi" in by_trigger:
            actions.append({**by_trigger["dhivehi"], "label": "Extract + Dhivehi"})
        if "fix" in by_trigger:
            actions.append({**by_trigger["fix"], "label": "Extract + fix writing"})
        actions.append({
            "trigger": "ocr-summarize",
            "type": "ai",
            "label": "Extract + summarize",
            "prompt": IMAGE_SUMMARY_PROMPT,
        })
        return actions

    def _run(self, command: dict) -> None:
        if self._working:
            return
        self._working = True
        for button in self.buttons:
            button.setEnabled(False)
        self.progress.show()
        self.status.setText("Reading image locally…" if self.is_image else "Mind is working…")
        config = self.store.load()
        trigger = str(command.get("trigger", ""))
        if command.get("type") == "local-text":
            try:
                result = run_text_tool(trigger, self.session.text)
            except TextToolError as exc:
                self._local_error(str(exc))
                return
            self._local_finished(result)
            return
        if self.is_image:
            worker = ImageOcrWorker(
                config,
                self.store.get_keys(config),
                self.session,
                command,
            )
        else:
            worker = TransformWorker(
                config,
                self.store.get_keys(config),
                self.session.text,
                str(command.get("prompt", "")),
                trigger,
            )
        worker.signals.status.connect(self.status.setText)
        worker.signals.finished.connect(self._finished)
        QThreadPool.globalInstance().start(worker)

    def _local_error(self, message: str) -> None:
        self._working = False
        self.progress.hide()
        self.status.setText(message)
        for button in self.buttons:
            button.setEnabled(True)
        self.completed.emit(False, message)

    def _local_finished(self, result: TextToolResult) -> None:
        if not result.replace:
            self._working = False
            self.progress.hide()
            self.status.setText(result.message)
            for button in self.buttons:
                button.setEnabled(True)
            self.completed.emit(True, result.message)
            return

        self.hide()
        replaced = self.session.replace(result.text)
        self.completed.emit(
            replaced,
            result.message if replaced else "Result copied to clipboard.",
        )
        self.accept()

    def _finished(self, ok: bool, message: str) -> None:
        if not ok:
            self._working = False
            self.progress.hide()
            self.status.setText(message)
            for button in self.buttons:
                button.setEnabled(True)
            self.completed.emit(False, message)
            return

        self.hide()
        replaced = self.session.replace(message)
        success_message = "Text pasted." if self.is_image else "Text replaced."
        self.completed.emit(replaced, success_message if replaced else "Result copied to clipboard.")
        self.accept()
