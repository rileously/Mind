from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import QObject, QRunnable, QRectF, QSize, Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPainter, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .config_store import ConfigStore
from .provider_client import ProviderError, test_provider
from .text_tools import LOCAL_TEXT_TOOLS


MODEL_OPTIONS = {
    "gemini": ["gemini-3.5-flash-lite", "gemini-3.6-flash"],
    "groq": ["openai/gpt-oss-120b", "qwen/qwen3.6-27b"],
    "ollama": ["llama3.2", "qwen3", "gemma3"],
    "lmstudio": ["local-model"],
    "custom": [],
}


PROFILE_LABELS = {
    "gemini": "Google Gemini",
    "groq": "Groq",
    "ollama": "Ollama (local)",
    "lmstudio": "LM Studio (local)",
    "custom": "Custom OpenAI-compatible",
}


PROVIDER_GUIDES = {
    "gemini": {
        "title": "Get your Gemini API key",
        "description": "1  Open Google AI Studio   ·   2  Create an API key   ·   3  Paste it below",
        "button": "Create Gemini API key  ↗",
        "url": "https://aistudio.google.com/apikey",
    },
    "groq": {
        "title": "Get your Groq API key",
        "description": "1  Open GroqCloud   ·   2  Create an API key   ·   3  Paste it below",
        "button": "Create Groq API key  ↗",
        "url": "https://console.groq.com/keys",
    },
    "ollama": {
        "title": "No API key required",
        "description": "Install Ollama, start it, and download a model such as llama3.2 before connecting.",
        "button": "Open Ollama quickstart  ↗",
        "url": "https://docs.ollama.com/quickstart",
    },
    "lmstudio": {
        "title": "No API key required",
        "description": "Install LM Studio, download a model, then start its local API server.",
        "button": "Download LM Studio  ↗",
        "url": "https://lmstudio.ai/download",
    },
    "custom": {
        "title": "Use your provider's dashboard",
        "description": "Create a key with your OpenAI-compatible provider, then paste it below. Local servers can leave it blank.",
        "button": "",
        "url": "",
    },
}


PALETTE_ACTION_LABELS = {
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


class Card(QFrame):
    def __init__(
        self,
        parent: QWidget | None = None,
        accent: bool = False,
        variant: str | None = None,
    ):
        super().__init__(parent)
        self.setObjectName(variant or ("AccentCard" if accent else "Card"))


class ToggleSwitch(QCheckBox):
    """Compact Windows-style on/off switch with native checkbox semantics."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setText("")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(42, 22)

    def sizeHint(self) -> QSize:
        return QSize(42, 22)

    def hitButton(self, position) -> bool:
        return self.rect().contains(position)

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        palette = self.palette()
        track_color = palette.color(QPalette.Highlight) if self.isChecked() else palette.color(QPalette.Mid)
        knob_color = palette.color(QPalette.HighlightedText)
        if not self.isEnabled():
            track_color.setAlpha(105)
            knob_color.setAlpha(145)
        track = QRectF(1, 2, self.width() - 2, self.height() - 4)
        painter.setPen(Qt.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(track, 9, 9)
        diameter = 14
        knob_x = self.width() - diameter - 4 if self.isChecked() else 4
        painter.setBrush(knob_color)
        painter.drawEllipse(QRectF(knob_x, 4, diameter, diameter))


class SegmentedControl(QWidget):
    """Small exclusive choice control matching the Fluent segmented-button pattern."""

    # Emitted with the chosen value when a person picks a segment. Deliberately
    # not emitted by setCurrentIndex: loading a saved value into the control is
    # not a change, and a settings page that saves on change would write it back.
    changed = Signal(str)

    def __init__(self, options: Iterable[tuple[str, str]], parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SegmentedControl")
        self._buttons: list[QPushButton] = []
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(0)
        values = list(options)
        for index, (label, value) in enumerate(values):
            button = QPushButton(label)
            button.setObjectName("SegmentButton")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setProperty("segmentData", value)
            if index == 0:
                button.setProperty("segmentPosition", "first")
            elif index == len(values) - 1:
                button.setProperty("segmentPosition", "last")
            else:
                button.setProperty("segmentPosition", "middle")
            self._group.addButton(button, index)
            self._buttons.append(button)
            layout.addWidget(button)
        if self._buttons:
            self._buttons[0].setChecked(True)
        # idClicked fires only for a click, so programmatic changes stay silent.
        self._group.idClicked.connect(
            lambda _id: self.changed.emit(self.currentData() or "")
        )

    def currentData(self) -> str | None:
        button = self._group.checkedButton()
        return str(button.property("segmentData")) if button else None

    def findData(self, value: object) -> int:
        for index, button in enumerate(self._buttons):
            if button.property("segmentData") == value:
                return index
        return -1

    def setCurrentIndex(self, index: int) -> None:
        if 0 <= index < len(self._buttons):
            self._buttons[index].setChecked(True)


def page_header(title: str, description: str, eyebrow: str = "MIND DESKTOP") -> QWidget:
    widget = QWidget()
    widget.setObjectName("PageHeader")
    widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    widget.setMinimumHeight(76 if eyebrow else 58)
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 4)
    layout.setSpacing(6)
    if eyebrow:
        eyebrow_label = QLabel(eyebrow.upper())
        eyebrow_label.setObjectName("PageEyebrow")
        layout.addWidget(eyebrow_label)
    heading = QLabel(title)
    heading.setObjectName("PageTitle")
    subtitle = QLabel(description)
    subtitle.setObjectName("Muted")
    subtitle.setWordWrap(True)
    layout.addWidget(heading)
    layout.addWidget(subtitle)
    return widget


def section_title(title: str, description: str = "") -> QWidget:
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(3)
    heading = QLabel(title)
    heading.setObjectName("SectionTitle")
    layout.addWidget(heading)
    if description:
        subtitle = QLabel(description)
        subtitle.setObjectName("Muted")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
    return widget


class ProviderForm(QWidget):
    saved = Signal()

    def __init__(self, store: ConfigStore, show_actions: bool = True, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self.thread_pool = QThreadPool.globalInstance()
        self._show_actions = show_actions

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        profile_form = QFormLayout()
        profile_form.setHorizontalSpacing(24)
        profile_form.setVerticalSpacing(14)
        profile_form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.profile = QComboBox()
        self.profile.setObjectName("ProviderSelector")
        self.profile.setMinimumHeight(48)
        self.profile.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.profile.setCursor(Qt.PointingHandCursor)
        self.profile.setToolTip("Choose an AI provider")
        self.profile.setAccessibleName("Provider dropdown")
        for value, label in PROFILE_LABELS.items():
            self.profile.addItem(label, value)
        self.profile.currentIndexChanged.connect(self._profile_changed)

        profile_selector = QFrame()
        profile_selector.setObjectName("ProviderSelectorShell")
        selector_layout = QHBoxLayout(profile_selector)
        selector_layout.setContentsMargins(0, 0, 0, 0)
        selector_layout.setSpacing(0)
        selector_layout.addWidget(self.profile, 1)
        self.profile_arrow = QPushButton("▾")
        self.profile_arrow.setObjectName("ProviderDropdownButton")
        self.profile_arrow.setFixedSize(48, 48)
        self.profile_arrow.setCursor(Qt.PointingHandCursor)
        self.profile_arrow.setToolTip("Open provider list")
        self.profile_arrow.setAccessibleName("Open provider list")
        self.profile_arrow.clicked.connect(self.profile.showPopup)
        selector_layout.addWidget(self.profile_arrow)
        profile_form.addRow("Provider", profile_selector)
        root.addLayout(profile_form)

        self.guide_card = Card(variant="InsetCard")
        guide_layout = QHBoxLayout(self.guide_card)
        guide_layout.setContentsMargins(16, 14, 16, 14)
        guide_layout.setSpacing(13)
        guide_mark = QLabel("↗")
        guide_mark.setObjectName("StatIcon")
        guide_mark.setFixedSize(38, 38)
        guide_mark.setAlignment(Qt.AlignCenter)
        guide_copy = QVBoxLayout()
        guide_copy.setSpacing(3)
        self.guide_title = QLabel()
        self.guide_title.setObjectName("SectionTitle")
        self.guide_description = QLabel()
        self.guide_description.setObjectName("Muted")
        self.guide_description.setWordWrap(True)
        guide_copy.addWidget(self.guide_title)
        guide_copy.addWidget(self.guide_description)
        self.guide_button = QPushButton()
        self.guide_button.clicked.connect(self._open_provider_guide)
        guide_layout.addWidget(guide_mark)
        guide_layout.addLayout(guide_copy, 1)
        guide_layout.addWidget(self.guide_button)
        root.addWidget(self.guide_card)

        form = QFormLayout()
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(14)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setInsertPolicy(QComboBox.NoInsert)
        form.addRow("Model", self.model)

        self.endpoint = QLineEdit()
        self.endpoint.setPlaceholderText("https://your-provider.example/v1")
        form.addRow("Endpoint", self.endpoint)

        key_box = QWidget()
        key_layout = QVBoxLayout(key_box)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.setSpacing(6)
        self.keys = QPlainTextEdit()
        self.keys.setPlaceholderText("One API key per line")
        self.keys.setMaximumHeight(92)
        self.keys.setTabChangesFocus(True)
        self.key_hint = QLabel("Keys are encrypted for your Windows account and never shown again.")
        self.key_hint.setObjectName("Muted")
        self.key_hint.setWordWrap(True)
        key_layout.addWidget(self.keys)
        key_layout.addWidget(self.key_hint)
        self.keys_label = QLabel("API keys")
        form.addRow(self.keys_label, key_box)
        root.addLayout(form)

        actions = QHBoxLayout()
        self.test_button = QPushButton("Test connection")
        self.test_button.clicked.connect(self.test_connection)
        actions.addWidget(self.test_button)
        self.test_result = QLabel("")
        self.test_result.setObjectName("Muted")
        self.test_result.setWordWrap(True)
        actions.addWidget(self.test_result, 1)
        if show_actions:
            self.save_button = QPushButton("Save provider")
            self.save_button.setProperty("primary", True)
            self.save_button.clicked.connect(self.save_to_store)
            actions.addWidget(self.save_button)
        root.addLayout(actions)
        self._update_provider_guide(str(self.profile.currentData()))

    def load_config(self, config: dict) -> None:
        profile = str(config.get("provider_profile", config.get("provider", "gemini")))
        index = self.profile.findData(profile)
        self.profile.setCurrentIndex(max(index, 0))
        self._update_provider_guide(profile)
        self._populate_models(profile, str(config.get("model", "")))
        self.endpoint.setText(str(config.get("endpoint", "")))
        count = len(self.store.get_keys(config))
        self.key_hint.setText(
            f"{count} key{'s' if count != 1 else ''} saved securely. Leave this blank to keep them."
            if count
            else "Keys are encrypted for your Windows account and never shown again."
        )
        self.keys.clear()

    def values(self) -> dict:
        profile = str(self.profile.currentData())
        defaults = self.store.provider_values(profile)
        return {
            "provider_profile": profile,
            "provider": defaults["provider"],
            "model": self.model.currentText().strip(),
            "endpoint": self.endpoint.text().strip(),
        }

    def entered_keys(self) -> list[str]:
        return [line.strip() for line in self.keys.toPlainText().splitlines() if line.strip()]

    def save_to_store(self) -> None:
        config = self.store.load()
        config.update(self.values())
        entered = self.entered_keys()
        if entered:
            config = self.store.set_keys(config, entered)
        if not config["model"]:
            QMessageBox.warning(self, "Model required", "Choose or enter a model name.")
            return
        self.store.save(config)
        self.load_config(config)
        self.test_result.setText("Provider settings saved.")
        self.saved.emit()

    def test_connection(self) -> None:
        values = self.values()
        keys = self.entered_keys() or self.store.get_keys()
        self.test_button.setEnabled(False)
        self.test_result.setText("Checking connection…")
        worker = ProviderTestWorker(
            values["provider_profile"], values["model"], values["endpoint"], keys
        )
        worker.signals.completed.connect(self._test_complete)
        self.thread_pool.start(worker)

    def _test_complete(self, ok: bool, message: str) -> None:
        self.test_button.setEnabled(True)
        self.test_result.setText(("✓ " if ok else "Could not connect: ") + message)

    def _profile_changed(self) -> None:
        profile = str(self.profile.currentData())
        self._update_provider_guide(profile)
        defaults = self.store.provider_values(profile)
        self._populate_models(profile, defaults["model"])
        self.endpoint.setText(defaults["endpoint"])
        custom = profile not in {"gemini", "groq"}
        self.endpoint.setEnabled(custom)
        if profile in {"ollama", "lmstudio"}:
            self.keys.setPlaceholderText("Optional for local providers")
            self.keys_label.setText("API key (optional)")
        else:
            self.keys.setPlaceholderText("One API key per line")
            self.keys_label.setText("API keys")

    def _update_provider_guide(self, profile: str) -> None:
        guide = PROVIDER_GUIDES.get(profile, PROVIDER_GUIDES["custom"])
        self.guide_title.setText(str(guide["title"]))
        self.guide_description.setText(str(guide["description"]))
        self.guide_button.setText(str(guide["button"]))
        self.guide_button.setVisible(bool(guide["url"]))

    def _open_provider_guide(self) -> None:
        profile = str(self.profile.currentData())
        url = str(PROVIDER_GUIDES.get(profile, PROVIDER_GUIDES["custom"])["url"])
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _populate_models(self, profile: str, selected: str) -> None:
        self.model.blockSignals(True)
        self.model.clear()
        self.model.addItems(MODEL_OPTIONS.get(profile, []))
        if selected:
            index = self.model.findText(selected)
            if index < 0:
                self.model.addItem(selected)
                index = self.model.count() - 1
            self.model.setCurrentIndex(index)
        self.model.blockSignals(False)


class _WorkerSignals(QObject):
    completed = Signal(bool, str)


class ProviderTestWorker(QRunnable):
    def __init__(self, profile: str, model: str, endpoint: str, keys: list[str]):
        super().__init__()
        self.profile = profile
        self.model = model
        self.endpoint = endpoint
        self.keys = keys
        self.signals = _WorkerSignals()

    def run(self) -> None:
        try:
            message = test_provider(self.profile, self.model, self.endpoint, self.keys)
            self.signals.completed.emit(True, message)
        except (ProviderError, OSError, ValueError) as exc:
            self.signals.completed.emit(False, str(exc))


class CommandDialog(QDialog):
    def __init__(self, command: dict | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Edit command" if command else "Add command")
        self.setMinimumWidth(560)
        self._command = command or {}

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)
        root.addWidget(section_title(self.windowTitle(), "Create a trigger that works in every text field."))

        form = QFormLayout()
        form.setSpacing(12)
        self.trigger = QLineEdit(str(self._command.get("trigger", "")))
        self.trigger.setPlaceholderText("Example: polish")
        form.addRow("Trigger", self.trigger)
        self.kind = QComboBox()
        self.kind.addItem("AI transformation", "ai")
        self.kind.addItem("Snippet / Fixed text", "replacer-text")
        self.kind.addItem("Shell command", "replacer-shell")
        index = self.kind.findData(self._command.get("type", "ai"))
        self.kind.setCurrentIndex(max(index, 0))
        self.kind.currentIndexChanged.connect(self._update_content_label)
        form.addRow("Type", self.kind)
        self.enabled = QCheckBox("Enabled")
        self.enabled.setChecked(bool(self._command.get("enabled", True)))
        form.addRow("", self.enabled)
        root.addLayout(form)

        self.content_header = QHBoxLayout()
        self.content_label = QLabel()
        self.content_label.setObjectName("SectionTitle")
        self.content_header.addWidget(self.content_label)
        self.content_header.addStretch()
        root.addLayout(self.content_header)

        self.snippet_vars_widget = QWidget()
        snippet_vars_layout = QHBoxLayout(self.snippet_vars_widget)
        snippet_vars_layout.setContentsMargins(0, 0, 0, 4)
        snippet_vars_layout.setSpacing(6)
        vars_label = QLabel("Insert:")
        vars_label.setStyleSheet("font-size: 11px; font-weight: 600; color: #888;")
        snippet_vars_layout.addWidget(vars_label)
        for tag in ["{date}", "{time}", "{datetime}", "{clipboard}", "{uuid}", "{weekday}"]:
            chip = QPushButton(tag)
            chip.setStyleSheet("font-size: 11px; padding: 2px 6px; border-radius: 4px;")
            chip.clicked.connect(lambda _, t=tag: self._insert_variable(t))
            snippet_vars_layout.addWidget(chip)
        snippet_vars_layout.addStretch()
        root.addWidget(self.snippet_vars_widget)

        self.content = QPlainTextEdit()
        self.content.setMinimumHeight(140)
        initial = self._command.get("prompt", "") if self._command.get("type", "ai") == "ai" else self._command.get("value", "")
        self.content.setPlainText(str(initial))
        root.addWidget(self.content)
        self.warning = QLabel("Shell commands run with your Windows user permissions. Only use commands you trust.")
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #F6B94A;")
        root.addWidget(self.warning)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._update_content_label()

    def _insert_variable(self, tag: str) -> None:
        self.content.insertPlainText(tag)
        self.content.setFocus()

    def command(self) -> dict:
        kind = str(self.kind.currentData())
        result = {
            "trigger": self.trigger.text().strip(),
            "type": kind,
            "enabled": self.enabled.isChecked(),
        }
        result["prompt" if kind == "ai" else "value"] = self.content.toPlainText().strip()
        return result

    def _update_content_label(self) -> None:
        kind = str(self.kind.currentData())
        self.content_label.setText("Transformation instruction" if kind == "ai" else "Snippet template" if kind == "replacer-text" else "Replacement value")
        self.snippet_vars_widget.setVisible(kind == "replacer-text")
        self.warning.setVisible(kind == "replacer-shell")

    def _validate(self) -> None:
        trigger = self.trigger.text().strip()
        if not trigger or len(trigger) > 50 or any(character.isspace() for character in trigger):
            QMessageBox.warning(self, "Invalid trigger", "Use 1–50 characters with no spaces.")
            return
        if not self.content.toPlainText().strip():
            QMessageBox.warning(self, "Content required", "Enter an instruction or replacement value.")
            return
        self.accept()


class PaletteCustomizeDialog(QDialog):
    def __init__(self, store: ConfigStore, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Customize Mind Palette")
        self.setMinimumSize(590, 600)

        config = store.load()
        commands = [
            command for command in store.load_commands()
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
        configured = config.get("mind_palette_actions", [])
        selected = [trigger for trigger in configured if isinstance(trigger, str) and trigger in by_trigger]
        remaining = [trigger for trigger in by_trigger if trigger not in selected]

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 24)
        root.setSpacing(16)
        root.addWidget(section_title(
            "Palette actions",
            "Check the actions you want. Drag them to control their order.",
        ))

        self.actions = QListWidget()
        self.actions.setAlternatingRowColors(True)
        self.actions.setDragDropMode(QAbstractItemView.InternalMove)
        self.actions.setDefaultDropAction(Qt.MoveAction)
        self.actions.setSelectionMode(QAbstractItemView.SingleSelection)
        for trigger in [*selected, *remaining]:
            command = by_trigger[trigger]
            label = str(command.get("label") or PALETTE_ACTION_LABELS.get(
                trigger, trigger.replace("-", " ").title()
            ))
            suffix = "offline" if command.get("type") == "local-text" else trigger
            item = QListWidgetItem(f"{label}   ({suffix})")
            item.setData(Qt.UserRole, trigger)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
            item.setCheckState(Qt.Checked if trigger in selected else Qt.Unchecked)
            item.setToolTip(str(command.get("description") or command.get("prompt", "")))
            self.actions.addItem(item)
        root.addWidget(self.actions, 1)

        appearance = Card()
        appearance_layout = QFormLayout(appearance)
        appearance_layout.setContentsMargins(18, 16, 18, 16)
        appearance_layout.setSpacing(12)
        self.columns = QComboBox()
        self.columns.addItem("One column", 1)
        self.columns.addItem("Two columns", 2)
        column_index = self.columns.findData(int(config.get("mind_palette_columns", 2)))
        self.columns.setCurrentIndex(max(column_index, 0))
        appearance_layout.addRow("Layout", self.columns)
        self.width = QComboBox()
        self.width.addItem("Compact", 340)
        self.width.addItem("Balanced", 390)
        self.width.addItem("Spacious", 460)
        width_index = self.width.findData(int(config.get("mind_palette_width", 390)))
        self.width.setCurrentIndex(max(width_index, 0))
        appearance_layout.addRow("Width", self.width)
        self.preview = QCheckBox("Show a short preview of selected text")
        self.preview.setChecked(bool(config.get("mind_palette_show_preview", True)))
        appearance_layout.addRow("", self.preview)
        self.image_ocr = QCheckBox("Enable local text extraction from copied images")
        self.image_ocr.setChecked(bool(config.get("mind_palette_image_ocr_enabled", True)))
        appearance_layout.addRow("", self.image_ocr)
        root.addWidget(appearance)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _save(self) -> None:
        selected: list[str] = []
        for index in range(self.actions.count()):
            item = self.actions.item(index)
            if item.checkState() == Qt.Checked:
                selected.append(str(item.data(Qt.UserRole)))
        if not selected:
            QMessageBox.warning(self, "Choose an action", "Select at least one action for Mind Palette.")
            return
        if len(selected) > 10:
            QMessageBox.warning(self, "Too many actions", "Choose up to 10 actions to keep the palette compact.")
            return
        config = self.store.load()
        config["mind_palette_actions"] = selected
        config["mind_palette_columns"] = int(self.columns.currentData())
        config["mind_palette_width"] = int(self.width.currentData())
        config["mind_palette_show_preview"] = self.preview.isChecked()
        config["mind_palette_image_ocr_enabled"] = self.image_ocr.isChecked()
        self.store.save(config)
        self.accept()
