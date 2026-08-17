from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
import tempfile
import uuid
from pathlib import Path

from PySide6.QtCore import QThreadPool, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QDesktopServices,
    QImage,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .autocomplete_engine import suggest_sentence_completion
from .config_store import ConfigStore
from .definition_popup import DefinitionPopup
from .dictionary import normalize_selected_word
from .engine_manager import EngineManager
from .ghost_text_overlay import GhostTextOverlay
from .hotkeys import (
    CLIPBOARD_HISTORY_SHORTCUTS,
    GHOST_TEXT_SHORTCUTS,
    PALETTE_SHORTCUTS,
    SNIP_SHORTCUTS,
    clipboard_history_shortcut_candidates,
    ghost_text_shortcut_candidates,
    shortcut_candidates,
    snip_shortcut_candidates,
)
from .math_tools import is_math_or_number_problem, solve_math_locally
from .paths import launcher_path
from .phone_tools import parse_maldivian_phone
from .ask_ai_popup import AskAiPopup
from .clipboard_history_dialog import ClipboardHistoryDialog
from .converter_tools import detect_and_convert
from .quick_paste_popup import QuickPastePopup
from .palette import MindPalette
from .secret_detector import detect_secrets
from .secret_shield_card import SecretShieldCard
from .snipping_overlay import SnippingOverlay
from .snip_card import SnipCard
from .url_peek_card import UrlPeekCard
from .url_tools import is_http_url
from .selection import (
    ClipboardImageSession,
    SelectionSession,
    is_editable_input_target,
    is_notion_input,
    is_question_text,
)
from .ocr import OcrError, extract_text_from_image
from .selection_monitor import SelectionMonitor
from .shell_menu import apply as shell_menu_apply, describe as shell_menu_describe
from .telegram_bridge import PANEL_SCREEN, TelegramBridge
from .telegram_routing import CommandRefused, parse_allowed_chat_ids, parse_message, select_command
from .transform_client import TransformError, transform_text
from .startup import is_start_with_windows_enabled, set_start_with_windows
from .theme import app_icon, qt_palette, stylesheet, theme_palette
from .ui_components import (
    Card,
    CommandDialog,
    PaletteCustomizeDialog,
    ProviderForm,
    SegmentedControl,
    ToggleSwitch,
    page_header,
    section_title,
)
from .updater import (
    DownloadedUpdate,
    ReleaseInfo,
    UpdateCheckWorker,
    UpdateDownloadWorker,
    UpdateError,
    launch_update_installer,
)


WM_HOTKEY = 0x0312
MIND_PALETTE_HOTKEY_ID = 0x4D49
MIND_SNIP_HOTKEY_ID = 0x534E
MIND_CLIPBOARD_HOTKEY_ID = 0x4348
MIND_GHOST_TEXT_HOTKEY_ID = 0x4754
# Triggers the engine handles itself, mirroring SYSTEM_COMMANDS in SwiftSlate.pyw.
# The engine runs as a standalone script and does not import this package, so the
# two lists are kept in step by hand; update both together.
SYSTEM_TRIGGERS = ("undo", "copy", "cut", "paste", "replace")


class DashboardPage(QWidget):
    engine_action = Signal()
    open_page = Signal(int)

    def __init__(self, store: ConfigStore, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(24)
        root.addWidget(page_header(
            "Your writing workspace",
            "A calm command center for everything Mind does across Windows.",
            "WORKSPACE",
        ))

        hero = Card(variant="HeroCard")
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(30, 28, 30, 28)
        hero_layout.setSpacing(28)
        copy = QVBoxLayout()
        copy.setSpacing(9)
        hero_pill = QLabel("●  SYSTEM-WIDE WRITING ASSISTANT")
        hero_pill.setObjectName("HeroPill")
        hero_pill.setMaximumWidth(238)
        copy.addWidget(hero_pill, 0, Qt.AlignLeft)
        self.hero_label = QLabel("Mind is ready when you are")
        self.hero_label.setObjectName("HeroTitle")
        self.hero_description = QLabel(
            "Start the engine once, then polish, summarize, translate, and reply without leaving the app you are typing in."
        )
        self.hero_description.setObjectName("Muted")
        self.hero_description.setWordWrap(True)
        self.hero_description.setMaximumWidth(650)
        copy.addWidget(self.hero_label)
        copy.addWidget(self.hero_description)
        hero_layout.addLayout(copy, 1)

        hero_action = QVBoxLayout()
        hero_action.setSpacing(10)
        hero_action.addStretch()
        self.engine_button = QPushButton("Start engine  →")
        self.engine_button.setProperty("primary", True)
        self.engine_button.setMinimumWidth(150)
        self.engine_button.clicked.connect(self.engine_action.emit)
        hero_action.addWidget(self.engine_button)
        hero_layout.addLayout(hero_action)
        root.addWidget(hero)

        stats = QHBoxLayout()
        stats.setSpacing(16)
        self.status_value = self._stat_card(
            stats, "●", "ENGINE", "Stopped", "Ready when you want system-wide assistance"
        )
        self.provider_value = self._stat_card(
            stats, "◇", "AI CONNECTION", "Gemini", "Encrypted and configured for this PC"
        )
        self.commands_value = self._stat_card(
            stats, "⌘", "COMMAND LIBRARY", "0 enabled", "Reusable actions available in every app"
        )
        root.addLayout(stats)

        getting_started = Card()
        started_layout = QHBoxLayout(getting_started)
        started_layout.setContentsMargins(26, 24, 26, 24)
        started_layout.setSpacing(24)
        quick_start = QVBoxLayout()
        quick_start.setSpacing(13)
        quick_start.addWidget(section_title(
            "Try your first transformation",
            "Open Notepad—or any editor—and type this exactly:",
        ))
        self.example = QLabel("i dont know what happened  ?fix")
        self.example.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.example.setObjectName("CodeBlock")
        quick_start.addWidget(self.example)
        actions = QHBoxLayout()
        provider_button = QPushButton("Manage connection")
        provider_button.clicked.connect(lambda: self.open_page.emit(1))
        commands_button = QPushButton("Browse commands  →")
        commands_button.clicked.connect(lambda: self.open_page.emit(2))
        actions.addWidget(provider_button)
        actions.addWidget(commands_button)
        actions.addStretch()
        quick_start.addLayout(actions)
        started_layout.addLayout(quick_start, 1)

        command_guide = Card(variant="InsetCard")
        command_guide.setMinimumWidth(270)
        guide_layout = QVBoxLayout(command_guide)
        guide_layout.setContentsMargins(18, 16, 18, 16)
        guide_layout.setSpacing(10)
        guide_title = QLabel("POPULAR COMMANDS")
        guide_title.setObjectName("CardEyebrow")
        guide_layout.addWidget(guide_title)
        for trigger, description in (
            ("?fix", "Correct writing"),
            ("?summarize", "Create a clean summary"),
            ("?reply", "Draft a useful response"),
        ):
            row = QHBoxLayout()
            key = QLabel(trigger)
            key.setObjectName("Kbd")
            detail = QLabel(description)
            detail.setObjectName("Muted")
            row.addWidget(key)
            row.addWidget(detail, 1)
            guide_layout.addLayout(row)
        guide_layout.addStretch()
        started_layout.addWidget(command_guide)
        root.addWidget(getting_started)
        root.addStretch()
        self.refresh()

    def _stat_card(
        self,
        parent: QHBoxLayout,
        icon: str,
        label: str,
        value: str,
        description: str,
    ) -> QLabel:
        card = Card(variant="StatCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 19)
        layout.setSpacing(8)
        top = QHBoxLayout()
        icon_label = QLabel(icon)
        icon_label.setObjectName("StatIcon")
        icon_label.setFixedSize(38, 38)
        icon_label.setAlignment(Qt.AlignCenter)
        eyebrow = QLabel(label)
        eyebrow.setObjectName("CardEyebrow")
        top.addWidget(icon_label)
        top.addStretch()
        top.addWidget(eyebrow, 0, Qt.AlignTop)
        value_label = QLabel(value)
        value_label.setObjectName("StatValue")
        detail = QLabel(description)
        detail.setObjectName("Muted")
        detail.setWordWrap(True)
        layout.addLayout(top)
        layout.addWidget(value_label)
        layout.addWidget(detail)
        parent.addWidget(card, 1)
        return value_label

    def set_engine_status(self, status: str) -> None:
        labels = {
            "stopped": "Stopped",
            "starting": "Starting…",
            "running": "Running",
            "stopping": "Stopping…",
            "error": "Needs attention",
        }
        self.status_value.setText(labels.get(status, status.title()))
        running = status in {"running", "starting"}
        self.engine_button.setText("Pause engine" if running else "Start engine  →")
        self.hero_label.setText("Mind is listening" if status == "running" else "Mind is ready when you are")
        self.hero_description.setText(
            "Use a trigger in any app. Mind only holds the minimum keystrokes needed to recognize commands."
            if status == "running"
            else "Start the engine, then type a command at the end of any text field."
        )

    def refresh(self) -> None:
        config = self.store.load()
        profile = str(config.get("provider_profile", config.get("provider", "gemini")))
        labels = {"gemini": "Gemini", "groq": "Groq", "ollama": "Ollama", "lmstudio": "LM Studio", "custom": "Custom"}
        self.provider_value.setText(labels.get(profile, profile.title()))
        commands = self.store.load_commands()
        enabled = sum(1 for command in commands if command.get("enabled", True))
        self.commands_value.setText(f"{enabled} enabled")
        prefix = str(config.get("prefix", "?"))
        self.example.setText(f"i dont know what happened  {prefix}fix")


class ProvidersPage(QWidget):
    updated = Signal()

    def __init__(self, store: ConfigStore, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(22)
        root.addWidget(page_header(
            "AI connections",
            "Choose where Mind sends transformations and keep credentials encrypted on this PC.",
            "PROVIDERS",
        ))
        card = Card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 24, 24, 24)
        card_layout.setSpacing(18)
        card_layout.addWidget(section_title("Active connection", "Cloud keys are protected with Windows DPAPI."))
        self.form = ProviderForm(store)
        self.form.saved.connect(self.updated.emit)
        card_layout.addWidget(self.form)
        root.addWidget(card)

        privacy = Card(accent=True)
        privacy_layout = QVBoxLayout(privacy)
        privacy_layout.setContentsMargins(22, 18, 22, 18)
        privacy_layout.addWidget(section_title("Local mode", "Choose Ollama or LM Studio to keep transformed text on your computer."))
        root.addWidget(privacy)
        root.addStretch()
        self.refresh()

    def refresh(self) -> None:
        self.form.load_config(self.store.load())


class CommandsPage(QWidget):
    updated = Signal()

    def __init__(self, store: ConfigStore, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(18)
        root.addWidget(page_header(
            "Command library",
            "Create reusable writing actions and invoke them directly inside any Windows app.",
            "AUTOMATIONS",
        ))

        toolbar_card = Card(variant="InsetCard")
        toolbar = QHBoxLayout(toolbar_card)
        toolbar.setContentsMargins(14, 12, 14, 12)
        toolbar.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search the command library…")
        self.search.setMaximumWidth(320)
        self.search.textChanged.connect(self._filter)
        add = QPushButton("＋  New command")
        add.setProperty("primary", True)
        add.clicked.connect(self._add)
        self.edit_button = QPushButton("Edit")
        self.edit_button.clicked.connect(self._edit)
        self.duplicate_button = QPushButton("Duplicate")
        self.duplicate_button.clicked.connect(self._duplicate)
        self.delete_button = QPushButton("Delete")
        self.delete_button.setProperty("danger", True)
        self.delete_button.clicked.connect(self._delete)
        toolbar.addWidget(self.search)
        toolbar.addStretch()
        toolbar.addWidget(self.edit_button)
        toolbar.addWidget(self.duplicate_button)
        toolbar.addWidget(self.delete_button)
        toolbar.addWidget(add)
        root.addWidget(toolbar_card)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Trigger", "Type", "Description", "On"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        header = self.table.horizontalHeader()
        # Start in the order the commands are stored in. That order is meaningful:
        # the engine matches triggers in file order and warns when a short trigger
        # shadows a longer one, so silently re-sorting the view would misrepresent
        # which command actually fires. Clicking a header still sorts.
        header.setSortIndicator(-1, Qt.AscendingOrder)
        header.setSortIndicatorShown(False)
        header.sectionClicked.connect(self._on_header_clicked)
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.doubleClicked.connect(self._edit)
        self.table.itemSelectionChanged.connect(self._sync_actions)
        # Toggling on or off is the most frequent edit by far; going through the
        # full edit dialog for it was needless friction.
        self.table.itemChanged.connect(self._on_item_changed)
        root.addWidget(self.table, 1)

        self.empty_label = QLabel()
        self.empty_label.setObjectName("Muted")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.empty_label.hide()
        root.addWidget(self.empty_label)

        delete_shortcut = QShortcut(QKeySequence.Delete, self.table)
        delete_shortcut.activated.connect(self._delete)
        for sequence in (QKeySequence(Qt.Key_Return), QKeySequence(Qt.Key_Enter)):
            QShortcut(sequence, self.table).activated.connect(self._edit)

        bottom = QHBoxLayout()
        self.count_label = QLabel()
        self.count_label.setObjectName("Muted")
        restore = QPushButton("Restore bundled commands")
        restore.clicked.connect(self._restore)
        bottom.addWidget(self.count_label)
        bottom.addStretch()
        bottom.addWidget(restore)
        root.addLayout(bottom)
        self.commands: list[dict] = []
        self.refresh()

    def refresh(self) -> None:
        self.commands = self.store.load_commands()
        self._render()

    def _render(self) -> None:
        query = self.search.text().strip().lower()
        shown = []
        for index, command in enumerate(self.commands):
            searchable = " ".join(str(value) for value in command.values()).lower()
            if not query or query in searchable:
                shown.append((index, command))

        # Sorting and the enabled checkbox both emit signals while rows are being
        # rebuilt; suspend them so a redraw is not mistaken for a user edit.
        self.table.setSortingEnabled(False)
        self._loading = True
        self.table.setRowCount(len(shown))
        labels = {"ai": "AI", "replacer-text": "Snippet", "replacer-shell": "Shell"}
        prefix = str(self.store.load().get("prefix", "?"))
        for row, (source_index, command) in enumerate(shown):
            kind = str(command.get("type", "ai"))
            description = str(command.get("prompt", command.get("value", ""))).replace("\n", " ")
            is_enabled = bool(command.get("enabled", True))
            trigger = str(command.get("trigger", ""))
            # Show what the user actually types. The bare trigger left people
            # guessing whether the prefix was part of it.
            values = [f"{prefix}{trigger}", labels.get(kind, kind), description, ""]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, source_index)
                if column == 3:
                    item.setFlags(
                        (item.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable
                    )
                    item.setCheckState(Qt.Checked if is_enabled else Qt.Unchecked)
                    item.setToolTip(
                        "Disable this command without deleting it"
                        if is_enabled
                        else "Enable this command"
                    )
                elif column == 2 and description:
                    # The column truncates long prompts, so keep the full text
                    # reachable without opening the editor.
                    item.setToolTip(description)
                if not is_enabled:
                    font = item.font()
                    font.setStrikeOut(column == 0)
                    item.setFont(font)
                    item.setForeground(QColor(150, 150, 150))
                self.table.setItem(row, column, item)
        self._loading = False
        self.table.setSortingEnabled(True)
        if not getattr(self, "_user_sorted", False):
            header = self.table.horizontalHeader()
            header.setSortIndicator(-1, Qt.AscendingOrder)
            header.setSortIndicatorShown(False)
        self._update_empty_state(len(shown), bool(query))
        self._sync_actions()
        enabled = sum(1 for command in self.commands if command.get("enabled", True))
        # The engine also handles ?undo, ?copy, ?cut, ?paste, and ?replace, which
        # are built in rather than stored here. Naming them keeps this total from
        # contradicting the higher trigger count reported in Diagnostics.
        self.count_label.setText(
            f"{len(self.commands)} commands · {enabled} enabled · "
            f"{len(SYSTEM_TRIGGERS)} built-in triggers always available"
        )

    def _on_header_clicked(self, _section: int) -> None:
        """Sort only once the user asks for it, then keep their choice."""
        self._user_sorted = True
        self.table.horizontalHeader().setSortIndicatorShown(True)

    def _update_empty_state(self, shown: int, filtered: bool) -> None:
        if shown:
            self.empty_label.hide()
            self.table.show()
            return
        self.table.hide()
        self.empty_label.setText(
            "No command matches your search. Try a different word, or clear the box "
            "to see the whole library."
            if filtered
            else "You have no commands yet. Select New command to create one, or "
            "Restore bundled commands to start from the defaults."
        )
        self.empty_label.show()

    def _sync_actions(self) -> None:
        """Only offer actions that can actually be carried out right now."""
        has_selection = self._selected_index() is not None
        self.edit_button.setEnabled(has_selection)
        self.duplicate_button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if getattr(self, "_loading", False) or item.column() != 3:
            return
        index = item.data(Qt.UserRole)
        if index is None:
            return
        index = int(index)
        if not 0 <= index < len(self.commands):
            return
        enabled = item.checkState() == Qt.Checked
        if bool(self.commands[index].get("enabled", True)) == enabled:
            return
        self.commands[index] = {**self.commands[index], "enabled": enabled}
        self._save()

    def _selected_index(self) -> int | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return int(item.data(Qt.UserRole)) if item else None

    def _add(self) -> None:
        dialog = CommandDialog(parent=self)
        if dialog.exec() == QDialog.Accepted:
            command = dialog.command()
            if self._duplicate_trigger(command["trigger"]):
                return
            self.commands.append(command)
            self._save()

    def _edit(self) -> None:
        index = self._selected_index()
        if index is None:
            QMessageBox.information(self, "Choose a command", "Select the command you want to edit.")
            return
        dialog = CommandDialog(self.commands[index], self)
        if dialog.exec() == QDialog.Accepted:
            command = dialog.command()
            if self._duplicate_trigger(command["trigger"], ignore=index):
                return
            self.commands[index] = command
            self._save()

    def _duplicate(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        command = dict(self.commands[index])
        base = str(command.get("trigger", "command"))
        candidate = f"{base}-copy"
        suffix = 2
        existing = {str(item.get("trigger", "")) for item in self.commands}
        while candidate in existing:
            candidate = f"{base}-copy-{suffix}"
            suffix += 1
        command["trigger"] = candidate
        self.commands.append(command)
        self._save()

    def _delete(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        trigger = self.commands[index].get("trigger", "")
        answer = QMessageBox.question(self, "Delete command", f"Delete the '{trigger}' command?")
        if answer == QMessageBox.Yes:
            del self.commands[index]
            self._save()

    def _restore(self) -> None:
        answer = QMessageBox.question(
            self,
            "Restore bundled commands",
            "Replace all current commands with the bundled defaults?",
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.store.restore_default_commands()
        except OSError as exc:
            QMessageBox.critical(self, "Could not restore commands", str(exc))
            return
        self.refresh()
        self.updated.emit()

    def _duplicate_trigger(self, trigger: str, ignore: int | None = None) -> bool:
        duplicate = any(
            index != ignore and str(command.get("trigger", "")) == trigger
            for index, command in enumerate(self.commands)
        )
        if duplicate:
            QMessageBox.warning(self, "Trigger already exists", f"A command named '{trigger}' already exists.")
        return duplicate

    def _save(self) -> None:
        self.store.save_commands(self.commands)
        self.refresh()
        self.updated.emit()

    def _filter(self) -> None:
        self._render()


class SettingsPage(QWidget):
    updated = Signal(str, bool, str, str)
    update_available = Signal(str, str)
    install_requested = Signal(str)

    def __init__(self, store: ConfigStore, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self.thread_pool = QThreadPool.globalInstance()
        self.latest_release: ReleaseInfo | None = None
        self._update_worker: UpdateCheckWorker | None = None
        self._download_worker: UpdateDownloadWorker | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(18)
        root.addWidget(page_header(
            "Preferences",
            "Shape how Mind listens, responds, looks, and starts with Windows.",
            "",
        ))

        behavior = QWidget()
        behavior_layout = QVBoxLayout(behavior)
        behavior_layout.setContentsMargins(0, 0, 0, 0)
        behavior_layout.setSpacing(7)
        behavior_layout.addWidget(self._group_title("Behavior"))
        self.prefix = self._setting_row(
            behavior_layout,
            "Command prefix",
            "The characters before each command to trigger text execution.",
            QLineEdit(),
            "⌨",
        )
        self.prefix.setFixedWidth(58)
        self.prefix.setAlignment(Qt.AlignCenter)
        self.prefix.setMaxLength(3)
        self.prefix.setObjectName("PrefixEdit")
        self.spinner = SegmentedControl(
            (("Animated", "animated"), ("Static", "static"), ("Off", "off"))
        )
        self._setting_row(
            behavior_layout,
            "Processing indicator",
            "Feedback shown while Mind is generating or replacing text.",
            self.spinner,
            "◌",
        )
        delay_holder = QWidget()
        delay_layout = QHBoxLayout(delay_holder)
        delay_layout.setContentsMargins(0, 0, 0, 0)
        delay_layout.setSpacing(10)
        self.delay = QSlider(Qt.Horizontal)
        self.delay.setRange(30, 500)
        self.delay.setFixedWidth(150)
        self.delay_value = QLabel("200 ms")
        self.delay_value.setObjectName("MonoValue")
        self.delay_value.setFixedWidth(52)
        self.delay_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.delay.valueChanged.connect(lambda value: self.delay_value.setText(f"{value} ms"))
        delay_layout.addWidget(self.delay)
        delay_layout.addWidget(self.delay_value)
        self._setting_row(
            behavior_layout,
            "Keyboard delay",
            "Increase this if replacement glitches on a slow computer.",
            delay_holder,
            "◴",
        )
        self.autocorrect = ToggleSwitch()
        self._setting_row(
            behavior_layout,
            "Realtime spelling",
            "Corrects clear English misspellings locally after Space. Press Backspace immediately to undo.",
            self.autocorrect,
            "✓",
        )
        self.word_definitions = ToggleSwitch()
        self._setting_row(
            behavior_layout,
            "Word definitions",
            "Shows an English definition above single-word selections in other apps. Skipped while you are editing a text field. Only the selected word is looked up online.",
            self.word_definitions,
            "Aa",
        )
        self.quick_paste = ToggleSwitch()
        self._setting_row(
            behavior_layout,
            "Quick paste tooltip",
            "Shows a floating Paste button when clicking into an editable field after copying text.",
            self.quick_paste,
            "📋",
        )
        self.converter_tooltips = ToggleSwitch()
        self._setting_row(
            behavior_layout,
            "Instant converter",
            "Shows real-time currency, unit, and timezone conversions above selected values.",
            self.converter_tooltips,
            "💱",
        )
        self.autocorrect_strength = QComboBox()
        self.autocorrect_strength.addItem("Conservative", "conservative")
        self.autocorrect_strength.addItem("Balanced (recommended)", "balanced")
        self.autocorrect_strength.addItem("Strong", "strong")
        self.autocorrect_strength.setMinimumWidth(190)
        self._setting_row(
            behavior_layout,
            "Correction strength",
            "Balanced catches everyday typing slips. Strong also tries harder two-letter errors.",
            self.autocorrect_strength,
            "≋",
        )
        self.autocorrect.toggled.connect(self.autocorrect_strength.setEnabled)
        root.addWidget(behavior)

        appearance = QWidget()
        appearance_layout = QVBoxLayout(appearance)
        appearance_layout.setContentsMargins(0, 0, 0, 0)
        appearance_layout.setSpacing(7)
        appearance_layout.addWidget(self._group_title("Appearance and startup"))
        self.theme = QComboBox()
        self.theme.addItem("Use Windows setting", "system")
        self.theme.addItem("Light", "light")
        self.theme.addItem("Dark", "dark")
        self.theme.setMinimumWidth(170)
        self._setting_row(appearance_layout, "Theme", "Switch between light and dark mode.", self.theme, "◐")
        self.accent = QComboBox()
        for label, value in (("Teal", "teal"), ("Blue", "blue"), ("Purple", "purple"), ("Rose", "rose"), ("Orange", "orange")):
            self.accent.addItem(label, value)
        self.accent.setMinimumWidth(170)
        self._setting_row(appearance_layout, "Accent color", "Changes buttons, highlights, and status colors.", self.accent, "●")
        self.startup = ToggleSwitch()
        self._setting_row(
            appearance_layout,
            "Start Mind with Windows",
            "Automatically launch in the system tray when you sign in.",
            self.startup,
            "⊞",
        )
        self.mind_palette = ToggleSwitch()
        self._setting_row(
            appearance_layout,
            "Mind Palette",
            "Transform selected text with a shortcut or optional automatic popup.",
            self.mind_palette,
            "✦",
        )
        self.palette_auto_show = ToggleSwitch()
        self._setting_row(
            appearance_layout,
            "Automatic Palette",
            "Opens when text is selected inside a text input field. Mind restores your clipboard after checking the selection.",
            self.palette_auto_show,
            "↗",
        )
        self.palette_shortcut = QComboBox()
        for shortcut in PALETTE_SHORTCUTS:
            self.palette_shortcut.addItem(shortcut, shortcut)
        self.palette_shortcut.setMinimumWidth(150)
        self._setting_row(
            appearance_layout,
            "Palette shortcut",
            "Mind automatically uses the next option if another app owns this shortcut.",
            self.palette_shortcut,
            "⌘",
        )
        self.mind_palette.toggled.connect(self.palette_shortcut.setEnabled)
        self.mind_palette.toggled.connect(self.palette_auto_show.setEnabled)
        self.screen_snip = ToggleSwitch()
        self._setting_row(
            appearance_layout,
            "Screen Snip (OCR & AI)",
            "Select any area of your screen with a crosshair to extract text or ask AI to explain.",
            self.screen_snip,
            "📸",
        )
        self.snip_shortcut = QComboBox()
        for shortcut in SNIP_SHORTCUTS:
            self.snip_shortcut.addItem(shortcut, shortcut)
        self.snip_shortcut.setMinimumWidth(150)
        self._setting_row(
            appearance_layout,
            "Snip shortcut",
            "Shortcut to start on-screen rectangular snipping.",
            self.snip_shortcut,
            "✀",
        )
        self.screen_snip.toggled.connect(self.snip_shortcut.setEnabled)
        self.clipboard_history = ToggleSwitch()
        self._setting_row(
            appearance_layout,
            "Clipboard History",
            "Keep a searchable history of copied items with instant paste and AI actions.",
            self.clipboard_history,
            "📋",
        )
        self.clipboard_shortcut = QComboBox()
        for shortcut in CLIPBOARD_HISTORY_SHORTCUTS:
            self.clipboard_shortcut.addItem(shortcut, shortcut)
        self.clipboard_shortcut.setMinimumWidth(150)
        self._setting_row(
            appearance_layout,
            "Clipboard history shortcut",
            "Shortcut to open the floating clipboard history search modal.",
            self.clipboard_shortcut,
            "📋",
        )
        self.clipboard_history.toggled.connect(self.clipboard_shortcut.setEnabled)

        self.telegram_enabled = ToggleSwitch()
        self._setting_row(
            appearance_layout,
            "Telegram bridge",
            "Use Mind commands from your phone. Send text or a photo to your bot and "
            "Mind replies with the result. Shell commands are never available remotely.",
            self.telegram_enabled,
            "✈",
        )
        self.telegram_token = QLineEdit()
        self.telegram_token.setEchoMode(QLineEdit.Password)
        self.telegram_token.setPlaceholderText("Bot token from @BotFather")
        self.telegram_token.setMinimumWidth(220)
        self._setting_row(
            appearance_layout,
            "Bot token",
            "Created by @BotFather in Telegram. Stored encrypted with Windows DPAPI, "
            "the same as your API keys.",
            self.telegram_token,
            "✈",
        )
        self.telegram_chat_ids = QLineEdit()
        self.telegram_chat_ids.setPlaceholderText("e.g. 123456789")
        self.telegram_chat_ids.setMinimumWidth(220)
        self._setting_row(
            appearance_layout,
            "Allowed chat IDs",
            "Only these chats may use the bot. Anyone can message a Telegram bot, so "
            "the bridge stays off until at least one ID is listed. Message @userinfobot "
            "to find yours.",
            self.telegram_chat_ids,
            "✈",
        )
        self.telegram_default = QLineEdit()
        self.telegram_default.setPlaceholderText("fix")
        self.telegram_default.setMaximumWidth(150)
        self._setting_row(
            appearance_layout,
            "Default command",
            "Applied to plain messages sent without a command. Leave empty to require "
            "a command every time.",
            self.telegram_default,
            "✈",
        )
        self.telegram_notifications = ToggleSwitch()
        self._setting_row(
            appearance_layout,
            "Telegram notifications",
            "Send Mind alerts, such as an available update, to your allowed chats.",
            self.telegram_notifications,
            "✈",
        )
        self.telegram_files = ToggleSwitch()
        self._setting_row(
            appearance_layout,
            "Telegram file access",
            "Browse folders and fetch files from this PC over Telegram, and save files "
            "sent to the bot. Anyone holding the bot token can read what you allow here, "
            "so keep the folder below as narrow as you can.",
            self.telegram_files,
            "✈",
        )
        self.telegram_files_root = QLineEdit()
        self.telegram_files_root.setPlaceholderText(str(Path.home()))
        self.telegram_files_root.setMinimumWidth(220)
        self._setting_row(
            appearance_layout,
            "Browsable folder",
            "Browsing cannot leave this folder. Leave empty to use your user folder. "
            "Point it at something narrow, such as a single shared folder.",
            self.telegram_files_root,
            "✈",
        )
        self.telegram_inbox = QLineEdit()
        self.telegram_inbox.setPlaceholderText(str(Path.home() / "Mind Inbox"))
        self.telegram_inbox.setMinimumWidth(220)
        self._setting_row(
            appearance_layout,
            "Save files to",
            "Where files sent to the bot are stored. Existing files are never overwritten.",
            self.telegram_inbox,
            "✈",
        )
        self.telegram_print = ToggleSwitch()
        self._setting_row(
            appearance_layout,
            "Telegram printing",
            "Adds a Print button to files sent to the bot, which asks for the printer, "
            "the paper size and what the document is. Printing spends paper and ink "
            "while you may not be in the room, so it is off until you turn it on.",
            self.telegram_print,
        )

        self.telegram_control = ToggleSwitch()
        self._setting_row(
            appearance_layout,
            "Telegram PC controls",
            "Check battery, memory and disk space, take a screenshot, lock or sleep "
            "this PC, and use the media keys from your phone.",
            self.telegram_control,
            "✈",
        )
        self.telegram_power = ToggleSwitch()
        self._setting_row(
            appearance_layout,
            "Allow shutdown from Telegram",
            "Adds /shutdown and /restart. Both ask first and wait a minute, and /abort "
            "stops them, but they can still close apps with unsaved work.",
            self.telegram_power,
            "✈",
        )
        for widget in (
            self.telegram_token,
            self.telegram_chat_ids,
            self.telegram_default,
            self.telegram_notifications,
            self.telegram_files,
            self.telegram_control,
        ):
            self.telegram_enabled.toggled.connect(widget.setEnabled)
        for widget in (self.telegram_files_root, self.telegram_inbox):
            self.telegram_files.toggled.connect(widget.setEnabled)
        self.telegram_send_menu = ToggleSwitch()
        self._setting_row(
            appearance_layout,
            "Right-click Send to Telegram",
            "Adds 'Send to Telegram' to the Windows right-click menu for any file or "
            "image. Windows 11 keeps unsigned entries under 'Show more options'.",
            self.telegram_send_menu,
            "✈",
        )
        self.telegram_control.toggled.connect(self.telegram_power.setEnabled)
        self.telegram_enabled.toggled.connect(self.telegram_send_menu.setEnabled)
        self.secret_shield = ToggleSwitch()
        self._setting_row(
            appearance_layout,
            "Secret & Privacy Shield",
            "Alert and offer 1-click redaction when copying API keys, tokens, or credentials.",
            self.secret_shield,
            "🛡️",
        )
        self.url_peek = ToggleSwitch()
        self._setting_row(
            appearance_layout,
            "URL & Media Quick Peek",
            "Show floating clean link, summary, and preview when selecting or copying links.",
            self.url_peek,
            "🔗",
        )
        self.ghost_text = ToggleSwitch()
        self._setting_row(
            appearance_layout,
            "Ghost Text & Sentence Finisher",
            "Suggest smart sentence continuations inline as you type.",
            self.ghost_text,
            "🪄",
        )
        self.ghost_shortcut = QComboBox()
        for shortcut in GHOST_TEXT_SHORTCUTS:
            self.ghost_shortcut.addItem(shortcut, shortcut)
        self.ghost_shortcut.setMinimumWidth(150)
        self._setting_row(
            appearance_layout,
            "Ghost Text shortcut",
            "Shortcut to trigger smart sentence continuation near cursor.",
            self.ghost_shortcut,
            "⌨️",
        )
        self.ghost_text.toggled.connect(self.ghost_shortcut.setEnabled)
        self.customize_palette = QPushButton("Customize actions and layout")
        self.customize_palette.clicked.connect(self._customize_palette)
        self._setting_row(
            appearance_layout,
            "Palette appearance",
            "Choose actions, order, columns, width, and selected-text preview.",
            self.customize_palette,
            "▦",
        )
        root.addWidget(appearance)

        updates = QWidget()
        updates_layout = QVBoxLayout(updates)
        updates_layout.setContentsMargins(0, 0, 0, 0)
        updates_layout.setSpacing(7)
        updates_layout.addWidget(self._group_title("Updates"))
        update_row = QFrame()
        update_row.setObjectName("SettingRow")
        update_layout = QHBoxLayout(update_row)
        update_layout.setContentsMargins(14, 11, 14, 11)
        update_layout.setSpacing(14)
        update_icon = QLabel("↻")
        update_icon.setObjectName("SettingIcon")
        update_icon.setFixedSize(34, 34)
        update_icon.setAlignment(Qt.AlignCenter)
        update_copy = QVBoxLayout()
        update_copy.setSpacing(2)
        update_title = QLabel("App updates")
        update_title.setObjectName("SettingTitle")
        self.update_status = QLabel(
            f"Mind {__version__} · Updates are checked automatically at startup."
        )
        self.update_status.setObjectName("Muted")
        self.update_status.setWordWrap(True)
        update_copy.addWidget(update_title)
        update_copy.addWidget(self.update_status)
        self.update_action = QPushButton("Download update")
        self.update_action.setProperty("primary", True)
        self.update_action.setVisible(False)
        self.update_action.clicked.connect(self._download_or_open_update)
        self.check_update_button = QPushButton("Check now")
        self.check_update_button.clicked.connect(lambda: self.check_for_updates(silent=False))
        update_layout.addWidget(update_icon)
        update_layout.addLayout(update_copy, 1)
        update_layout.addWidget(self.update_action)
        update_layout.addWidget(self.check_update_button)
        updates_layout.addWidget(update_row)
        root.addWidget(updates)

        save_row = QHBoxLayout()
        about = QLabel(f"Mind {__version__} · Derived from SwiftSlate Desktop under the MIT License")
        about.setObjectName("Muted")
        about.setWordWrap(True)
        save = QPushButton("Save settings")
        save.setProperty("primary", True)
        save.clicked.connect(self.save)
        save_row.addWidget(about, 1)
        save_row.addWidget(save)
        root.addLayout(save_row)
        root.addStretch()
        self.refresh()

    def _group_title(self, title: str) -> QLabel:
        label = QLabel(title.upper())
        label.setObjectName("SettingsGroupTitle")
        return label

    def _setting_row(
        self,
        layout: QVBoxLayout,
        title: str,
        description: str,
        control: QWidget,
        icon: str,
    ) -> QWidget:
        row = QFrame()
        row.setObjectName("SettingRow")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(14, 11, 14, 11)
        row_layout.setSpacing(14)
        icon_label = QLabel(icon)
        icon_label.setObjectName("SettingIcon")
        icon_label.setFixedSize(34, 34)
        icon_label.setAlignment(Qt.AlignCenter)
        row_layout.addWidget(icon_label)
        copy = QVBoxLayout()
        copy.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("SettingTitle")
        description_label = QLabel(description)
        description_label.setObjectName("Muted")
        description_label.setWordWrap(True)
        copy.addWidget(title_label)
        copy.addWidget(description_label)
        row_layout.addLayout(copy, 1)
        if not control.accessibleName():
            control.setAccessibleName(title)
        row_layout.addWidget(control)
        layout.addWidget(row)
        return control

    def refresh(self) -> None:
        config = self.store.load()
        self.prefix.setText(str(config.get("prefix", "?")))
        spinner_index = self.spinner.findData(config.get("spinner", "animated"))
        self.spinner.setCurrentIndex(max(spinner_index, 0))
        self.delay.setValue(int(config.get("key_delay", 200)))
        self.autocorrect.setChecked(bool(config.get("autocorrect_after_space", False)))
        self.word_definitions.setChecked(bool(config.get("word_definitions_enabled", True)))
        self.quick_paste.setChecked(bool(config.get("quick_paste_enabled", True)))
        self.converter_tooltips.setChecked(bool(config.get("converter_tooltips_enabled", True)))
        strength_index = self.autocorrect_strength.findData(config.get("autocorrect_strength", "balanced"))
        self.autocorrect_strength.setCurrentIndex(max(strength_index, 0))
        self.autocorrect_strength.setEnabled(self.autocorrect.isChecked())
        theme_index = self.theme.findData(config.get("theme", "system"))
        self.theme.setCurrentIndex(max(theme_index, 0))
        accent_index = self.accent.findData(config.get("accent_color", "teal"))
        self.accent.setCurrentIndex(max(accent_index, 0))
        self.startup.setChecked(is_start_with_windows_enabled())
        self.mind_palette.setChecked(bool(config.get("mind_palette_enabled", False)))
        self.palette_auto_show.setChecked(bool(config.get("mind_palette_auto_show_on_selection", False)))
        self.palette_auto_show.setEnabled(self.mind_palette.isChecked())
        shortcut = str(config.get("mind_palette_shortcut", "Ctrl+Alt+M"))
        shortcut_index = self.palette_shortcut.findData(shortcut)
        self.palette_shortcut.setCurrentIndex(max(shortcut_index, 0))
        self.palette_shortcut.setEnabled(self.mind_palette.isChecked())
        self.screen_snip.setChecked(bool(config.get("screen_snip_enabled", True)))
        snip_shortcut = str(config.get("screen_snip_shortcut", "Ctrl+Alt+S"))
        snip_index = self.snip_shortcut.findData(snip_shortcut)
        self.snip_shortcut.setCurrentIndex(max(snip_index, 0))
        self.snip_shortcut.setEnabled(self.screen_snip.isChecked())
        self.clipboard_history.setChecked(bool(config.get("clipboard_history_enabled", True)))
        clip_shortcut = str(config.get("clipboard_history_shortcut", "Ctrl+Alt+V"))
        clip_index = self.clipboard_shortcut.findData(clip_shortcut)
        self.clipboard_shortcut.setCurrentIndex(max(clip_index, 0))
        self.clipboard_shortcut.setEnabled(self.clipboard_history.isChecked())
        telegram_on = bool(config.get("telegram_enabled", False))
        self.telegram_enabled.setChecked(telegram_on)
        # Show that a token exists without ever displaying it.
        self.telegram_token.setText("" if not self.store.get_telegram_token(config) else "•" * 12)
        self.telegram_chat_ids.setText(
            ", ".join(str(i) for i in sorted(parse_allowed_chat_ids(config.get("telegram_allowed_chat_ids"))))
        )
        self.telegram_default.setText(str(config.get("telegram_default_command", "")))
        self.telegram_notifications.setChecked(bool(config.get("telegram_notifications", False)))
        files_on = bool(config.get("telegram_files_enabled", False))
        self.telegram_files.setChecked(files_on)
        self.telegram_files_root.setText(str(config.get("telegram_files_root", "")))
        self.telegram_inbox.setText(str(config.get("telegram_inbox", "")))
        for widget in (
            self.telegram_token,
            self.telegram_chat_ids,
            self.telegram_default,
            self.telegram_notifications,
            self.telegram_files,
        ):
            widget.setEnabled(telegram_on)
        # Printing needs the file-saving side: what it prints is what was saved.
        self.telegram_print.setChecked(bool(config.get("telegram_print_enabled", False)))
        self.telegram_print.setEnabled(telegram_on and files_on)
        control_on = bool(config.get("telegram_control_enabled", False))
        self.telegram_control.setChecked(control_on)
        self.telegram_power.setChecked(bool(config.get("telegram_power_enabled", False)))
        for widget in (self.telegram_files_root, self.telegram_inbox):
            widget.setEnabled(telegram_on and files_on)
        self.telegram_control.setEnabled(telegram_on)
        self.telegram_power.setEnabled(telegram_on and control_on)
        self.telegram_send_menu.setChecked(bool(config.get("telegram_send_menu_enabled", False)))
        self.telegram_send_menu.setEnabled(telegram_on)
        self.secret_shield.setChecked(bool(config.get("secret_shield_enabled", True)))
        self.url_peek.setChecked(bool(config.get("url_peek_enabled", True)))
        self.ghost_text.setChecked(bool(config.get("ghost_text_enabled", True)))
        ghost_sc = str(config.get("ghost_text_shortcut", "Ctrl+Alt+Space"))
        ghost_index = self.ghost_shortcut.findData(ghost_sc)
        self.ghost_shortcut.setCurrentIndex(max(ghost_index, 0))
        self.ghost_shortcut.setEnabled(self.ghost_text.isChecked())

    def save(self) -> None:
        prefix = self.prefix.text().strip()
        if not prefix or any(character.isspace() for character in prefix):
            QMessageBox.warning(self, "Invalid prefix", "Enter a short prefix without spaces.")
            return
        config = self.store.load()
        config.update(
            {
                "prefix": prefix,
                "spinner": self.spinner.currentData(),
                "key_delay": self.delay.value(),
                "autocorrect_after_space": self.autocorrect.isChecked(),
                "word_definitions_enabled": self.word_definitions.isChecked(),
                "quick_paste_enabled": self.quick_paste.isChecked(),
                "converter_tooltips_enabled": self.converter_tooltips.isChecked(),
                "autocorrect_strength": self.autocorrect_strength.currentData(),
                "theme": self.theme.currentData(),
                "accent_color": self.accent.currentData(),
                "start_with_windows": self.startup.isChecked(),
                "mind_palette_enabled": self.mind_palette.isChecked(),
                "mind_palette_auto_show_on_selection": self.palette_auto_show.isChecked(),
                "mind_palette_shortcut": self.palette_shortcut.currentData(),
                "screen_snip_enabled": self.screen_snip.isChecked(),
                "screen_snip_shortcut": self.snip_shortcut.currentData(),
                "clipboard_history_enabled": self.clipboard_history.isChecked(),
                "clipboard_history_shortcut": self.clipboard_shortcut.currentData(),
                "telegram_enabled": self.telegram_enabled.isChecked(),
                "telegram_allowed_chat_ids": sorted(
                    parse_allowed_chat_ids(self.telegram_chat_ids.text())
                ),
                "telegram_default_command": self.telegram_default.text().strip().lstrip("?/"),
                "telegram_notifications": self.telegram_notifications.isChecked(),
                "telegram_files_enabled": self.telegram_files.isChecked(),
                "telegram_files_root": self.telegram_files_root.text().strip(),
                "telegram_inbox": self.telegram_inbox.text().strip(),
                "telegram_print_enabled": self.telegram_print.isChecked(),
                "telegram_control_enabled": self.telegram_control.isChecked(),
                "telegram_power_enabled": self.telegram_power.isChecked(),
                "telegram_send_menu_enabled": self.telegram_send_menu.isChecked(),
                "secret_shield_enabled": self.secret_shield.isChecked(),
                "url_peek_enabled": self.url_peek.isChecked(),
                "ghost_text_enabled": self.ghost_text.isChecked(),
                "ghost_text_shortcut": self.ghost_shortcut.currentData(),
            }
        )
        # The token field shows a mask when one is already stored, so only write
        # a new value when the user actually typed one. Clearing the field on
        # purpose still removes the saved token.
        typed_token = self.telegram_token.text().strip()
        if typed_token != "•" * 12:
            config = self.store.set_telegram_token(config, typed_token)
        if config.get("telegram_enabled") and not config.get("telegram_allowed_chat_ids"):
            QMessageBox.warning(
                self,
                "Telegram needs an allowed chat",
                "Anyone can message a Telegram bot, so Mind will not connect until you "
                "list at least one chat ID that is allowed to use it.",
            )
        want_shell_menu = self.telegram_enabled.isChecked() and self.telegram_send_menu.isChecked()
        try:
            self.store.save(config)
            set_start_with_windows(self.startup.isChecked(), launcher_path())
            # Re-applied on every save so the entry follows the executable if the
            # first-run installer has since moved it.
            shell_menu_added = shell_menu_apply(want_shell_menu)
        except OSError as exc:
            QMessageBox.critical(self, "Could not save settings", str(exc))
            return
        if want_shell_menu and not shell_menu_added:
            # Otherwise the switch looks on while nothing was added, and the user
            # is left searching a menu that was never going to contain it.
            QMessageBox.information(
                self,
                "Right-click entry not added",
                "The setting is saved, but 'Send to Telegram' was not added to the "
                f"right-click menu. {shell_menu_describe()}",
            )
        self.updated.emit(
            str(self.theme.currentData()),
            self.mind_palette.isChecked(),
            str(self.palette_shortcut.currentData()),
            str(self.accent.currentData()),
        )
        QMessageBox.information(self, "Settings saved", "Your Mind settings have been updated.")

    def _customize_palette(self) -> None:
        dialog = PaletteCustomizeDialog(self.store, self)
        dialog.exec()

    def check_for_updates(self, silent: bool = False) -> None:
        if self._update_worker is not None:
            return
        self.check_update_button.setEnabled(False)
        self.check_update_button.setText("Checking…")
        self.update_status.setText(f"Mind {__version__} · Checking GitHub Releases…")
        self._update_worker = UpdateCheckWorker()
        self._update_worker.signals.completed.connect(
            lambda ok, payload: self._update_check_complete(ok, payload, silent)
        )
        self.thread_pool.start(self._update_worker)

    def _update_check_complete(self, ok: bool, payload: object, silent: bool) -> None:
        self._update_worker = None
        self.check_update_button.setEnabled(True)
        self.check_update_button.setText("Check now")
        if not ok:
            self.latest_release = None
            self.update_action.setVisible(False)
            self.update_status.setText(f"Mind {__version__} · {payload}")
            if not silent:
                QMessageBox.warning(self, "Could not check for updates", str(payload))
            return
        if payload is None:
            self.latest_release = None
            self.update_action.setVisible(False)
            self.update_status.setText(
                f"Mind {__version__} · You are up to date. No newer release is published."
            )
            return
        if not isinstance(payload, ReleaseInfo):
            self.update_status.setText(f"Mind {__version__} · The update response was invalid.")
            return
        release = payload
        self.latest_release = release
        self.update_status.setText(
            f"Mind {__version__} · Version {release.version} is available: {release.title}"
        )
        self.update_status.setToolTip(release.notes[:1200] if release.notes else release.title)
        self.update_action.setText(
            f"Download {release.version}" if release.asset_url else "View release"
        )
        self.update_action.setVisible(True)
        self.update_available.emit(release.version, release.title)

    def _download_or_open_update(self) -> None:
        release = self.latest_release
        if release is None:
            return
        if not release.asset_url:
            if release.page_url:
                QDesktopServices.openUrl(QUrl(release.page_url))
            return
        if self._download_worker is not None:
            return
        self.update_action.setEnabled(False)
        self.update_action.setText("Downloading…")
        self.update_status.setText(f"Downloading Mind {release.version} securely from GitHub…")
        self._download_worker = UpdateDownloadWorker(release)
        self._download_worker.signals.completed.connect(self._update_download_complete)
        self.thread_pool.start(self._download_worker)

    def _update_download_complete(self, ok: bool, payload: object) -> None:
        self._download_worker = None
        self.update_action.setEnabled(True)
        release = self.latest_release
        self.update_action.setText(
            f"Download {release.version}" if release and release.asset_url else "View release"
        )
        if not ok or not isinstance(payload, DownloadedUpdate):
            self.update_status.setText(f"Update download failed · {payload}")
            QMessageBox.warning(self, "Could not download update", str(payload))
            return
        downloaded = payload
        version = release.version if release else ""
        self.update_status.setText(
            f"Mind {version} is ready · SHA-256 {downloaded.sha256[:12]}…"
        )
        answer = QMessageBox.question(
            self,
            "Install Mind update",
            "The update downloaded successfully. Mind will close, keep a backup of the previous version, "
            "install the update, and reopen. Install now?",
        )
        if answer == QMessageBox.Yes:
            self.install_requested.emit(str(downloaded.path))


class DiagnosticsPage(QWidget):
    def __init__(self, data_root: Path, parent: QWidget | None = None):
        super().__init__(parent)
        self.data_root = data_root
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(18)
        root.addWidget(page_header(
            "Diagnostics",
            "Live engine events and troubleshooting details. API keys are never logged.",
            "SYSTEM HEALTH",
        ))
        toolbar_card = Card(variant="InsetCard")
        toolbar = QHBoxLayout(toolbar_card)
        toolbar.setContentsMargins(14, 12, 14, 12)
        open_folder = QPushButton("Open Mind data folder")
        open_folder.clicked.connect(lambda: os.startfile(str(self.data_root)))
        clear = QPushButton("Clear view")
        clear.clicked.connect(lambda: self.logs.clear())
        toolbar.addWidget(open_folder)
        toolbar.addWidget(clear)
        toolbar.addStretch()
        root.addWidget(toolbar_card)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setPlaceholderText("Engine events will appear here.")
        self.logs.setStyleSheet("font-family: 'Cascadia Mono'; font-size: 12px;")
        root.addWidget(self.logs, 1)

    def append(self, message: str) -> None:
        self.logs.appendPlainText(message)


class MindWindow(QMainWindow):
    def __init__(self, store: ConfigStore, minimized: bool = False):
        super().__init__()
        self.store = store
        self.config = store.load()
        self.engine = EngineManager(store.root, self)
        self.engine.status_changed.connect(self._engine_status_changed)
        self.engine.log_received.connect(self._log)
        self._quitting = False
        self._palette_hotkey_registered = False
        self._palette_shortcut = "Ctrl+Alt+M"
        self._palette_pending = False
        self._selection_pending = False
        self._snip_hotkey_registered = False
        self._snip_shortcut = "Ctrl+Alt+S"
        self._clipboard_hotkey_registered = False
        self._clipboard_shortcut = "Ctrl+Alt+V"
        self._ghost_text_hotkey_registered = False
        self._ghost_text_shortcut = "Ctrl+Alt+Space"
        self.palette: MindPalette | None = None
        self.definition_popup = DefinitionPopup()
        self.ask_ai_popup = AskAiPopup(self.store)
        self.quick_paste_popup = QuickPastePopup()
        self.quick_paste_popup.pasted.connect(self._on_quick_paste_pasted)
        self.snipping_overlay = SnippingOverlay()
        self.snip_card = SnipCard(self.store)
        self.snipping_overlay.snip_captured.connect(self._on_snip_captured)
        self.clipboard_history_dialog = ClipboardHistoryDialog(self.store)
        self.clipboard_history_dialog.ai_action_requested.connect(self._on_clipboard_ai_action)
        self.secret_shield_card = SecretShieldCard()
        self.url_peek_card = UrlPeekCard()
        self.url_peek_card.summarize_requested.connect(self._on_url_summarize_requested)
        self.ghost_text_overlay = GhostTextOverlay()
        self.telegram = TelegramBridge(self.store, self)
        self.telegram.log.connect(self._log)
        self.telegram.clipboard_requested.connect(self._on_telegram_clipboard_requested)
        self.telegram.clipboard_received.connect(self._on_telegram_clipboard_received)
        self.telegram.image_received.connect(self._on_telegram_image_received)
        self.telegram.screenshot_requested.connect(self._on_telegram_screenshot_requested)
        self._last_copied_text = ""
        self._last_copied_time = 0.0
        self._pasted_texts: set[str] = set()
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.dataChanged.connect(self._on_clipboard_data_changed)
        self.selection_monitor = SelectionMonitor(self)
        self.setWindowTitle("Mind • AI Writing Workspace")
        self.setWindowIcon(app_icon())
        self.resize(1240, 820)
        self.setMinimumSize(1020, 700)
        self._build_ui()
        self._build_tray()
        self.selection_monitor.set_ignored_window(int(self.winId()))
        self.selection_monitor.selection_gesture.connect(self._automatic_selection_requested)
        self.selection_monitor.single_click_gesture.connect(self._on_single_click_gesture)
        self.apply_theme(
            str(self.config.get("theme", "system")),
            str(self.config.get("accent_color", "teal")),
        )
        QTimer.singleShot(
            0,
            lambda: self.configure_palette(
                bool(self.config.get("mind_palette_enabled", False)),
                str(self.config.get("mind_palette_shortcut", "Ctrl+Alt+M")),
            ),
        )
        QTimer.singleShot(
            0,
            lambda: self.configure_snip(
                bool(self.config.get("screen_snip_enabled", True)),
                str(self.config.get("screen_snip_shortcut", "Ctrl+Alt+S")),
            ),
        )
        QTimer.singleShot(
            0,
            lambda: self.configure_clipboard_history(
                bool(self.config.get("clipboard_history_enabled", True)),
                str(self.config.get("clipboard_history_shortcut", "Ctrl+Alt+V")),
            ),
        )
        QTimer.singleShot(
            0,
            lambda: self.configure_ghost_text(
                bool(self.config.get("ghost_text_enabled", True)),
                str(self.config.get("ghost_text_shortcut", "Ctrl+Alt+Space")),
            ),
        )
        QTimer.singleShot(0, self.configure_telegram)
        QTimer.singleShot(0, self.sync_shell_menu)
        if self.config.get("start_engine_on_launch", False):
            QTimer.singleShot(300, self.engine.start)
        QTimer.singleShot(2500, lambda: self.settings.check_for_updates(silent=True))
        if minimized:
            QTimer.singleShot(0, self.hide)

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("AppRoot")
        self.setCentralWidget(root)
        main = QHBoxLayout(root)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(256)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 16, 12, 12)
        side.setSpacing(6)
        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(4, 0, 4, 0)
        logo = QLabel()
        logo.setObjectName("BrandLogo")
        logo.setFixedSize(36, 36)
        logo.setAlignment(Qt.AlignCenter)
        logo.setPixmap(app_icon(36).pixmap(34, 34))
        brand_copy = QVBoxLayout()
        brand_copy.setSpacing(0)
        brand = QLabel("Mind")
        brand.setObjectName("Brand")
        tagline = QLabel("AI WRITING WORKSPACE")
        tagline.setObjectName("BrandCaption")
        brand_copy.addWidget(brand)
        brand_copy.addWidget(tagline)
        brand_row.addWidget(logo)
        brand_row.addSpacing(8)
        brand_row.addLayout(brand_copy)
        side.addLayout(brand_row)
        side.addSpacing(14)

        self.nav_search = QLineEdit()
        self.nav_search.setObjectName("SidebarSearch")
        self.nav_search.setPlaceholderText("Search pages and settings")
        self.nav_search.setClearButtonEnabled(True)
        self.nav_search.textChanged.connect(self._filter_navigation)
        side.addWidget(self.nav_search)
        side.addSpacing(7)

        navigation_label = QLabel("WORKSPACE")
        navigation_label.setObjectName("CardEyebrow")
        side.addWidget(navigation_label)
        side.addSpacing(5)

        self.nav_buttons: list[QPushButton] = []
        nav_items = [
            ("▦", "Dashboard", "home overview engine workspace"),
            ("◇", "Providers", "api key gemini groq ollama lm studio connection model"),
            ("⌘", "Commands", "trigger automation writing actions"),
            ("⚙", "Preferences", "settings behavior typing spelling definition dictionary tooltip theme startup palette shortcut"),
            ("▥", "Diagnostics", "logs troubleshooting system health data folder"),
        ]
        for index, (icon, title, search_terms) in enumerate(nav_items):
            button = QPushButton(f"{icon}    {title}")
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setProperty("navTitle", title)
            button.setProperty("searchTerms", search_terms)
            button.clicked.connect(lambda checked=False, page=index: self.select_page(page))
            side.addWidget(button)
            self.nav_buttons.append(button)
        side.addStretch()

        status_card = Card(variant="StatusCard")
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(11, 10, 11, 9)
        status_layout.setSpacing(7)
        status_eyebrow = QLabel("ENGINE STATUS")
        status_eyebrow.setObjectName("CardEyebrow")
        self.sidebar_status = QLabel("●  Engine stopped")
        self.sidebar_status.setObjectName("Muted")
        self.sidebar_action = QPushButton("Start engine")
        self.sidebar_action.setObjectName("EngineAction")
        self.sidebar_action.clicked.connect(self.toggle_engine)
        status_layout.addWidget(status_eyebrow)
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        status_row.addWidget(self.sidebar_status, 1)
        status_row.addWidget(self.sidebar_action)
        status_layout.addLayout(status_row)
        version = QLabel(f"Mind {__version__}   ·   Local desktop")
        version.setObjectName("BrandCaption")
        status_layout.addWidget(version)
        side.addWidget(status_card)
        main.addWidget(sidebar)

        content = QWidget()
        content.setObjectName("ContentRoot")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(32, 28, 32, 28)
        self.stack = QStackedWidget()
        self.dashboard = DashboardPage(self.store)
        self.providers = ProvidersPage(self.store)
        self.commands = CommandsPage(self.store)
        self.settings = SettingsPage(self.store)
        self.diagnostics = DiagnosticsPage(self.store.root)
        for page in [self.dashboard, self.providers, self.commands, self.settings, self.diagnostics]:
            page.setObjectName("Page")
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setWidget(page)
            self.stack.addWidget(scroll)
        content_layout.addWidget(self.stack)
        main.addWidget(content, 1)

        self.dashboard.engine_action.connect(self.toggle_engine)
        self.dashboard.open_page.connect(self.select_page)
        self.providers.updated.connect(self._config_updated)
        self.commands.updated.connect(self._config_updated)
        self.settings.updated.connect(self._settings_updated)
        self.settings.update_available.connect(self._notify_update_available)
        self.settings.install_requested.connect(self._install_update)
        self.select_page(0)

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip("Mind — stopped")
        menu = QMenu()
        open_action = QAction("Open Mind", self)
        open_action.triggered.connect(self.show_window)
        snip_action = QAction("📸 Screen Snip (OCR & AI)", self)
        snip_action.triggered.connect(self.trigger_screen_snip)
        clip_action = QAction("📋 Clipboard History", self)
        clip_action.triggered.connect(self.trigger_clipboard_history)
        ghost_action = QAction("🪄 Ghost Text Finisher", self)
        ghost_action.triggered.connect(self.trigger_ghost_text)
        self.tray_engine_action = QAction("Start engine", self)
        self.tray_engine_action.triggered.connect(self.toggle_engine)
        quit_action = QAction("Quit Mind", self)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(open_action)
        menu.addAction(snip_action)
        menu.addAction(clip_action)
        menu.addAction(ghost_action)
        menu.addAction(self.tray_engine_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def select_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for button_index, button in enumerate(self.nav_buttons):
            button.setChecked(button_index == index)
        if index == 0:
            self.dashboard.refresh()
        elif index == 1:
            self.providers.refresh()
        elif index == 2:
            self.commands.refresh()
        elif index == 3:
            self.settings.refresh()

    def _filter_navigation(self, query: str) -> None:
        terms = query.strip().lower()
        for button in self.nav_buttons:
            haystack = f"{button.property('navTitle')} {button.property('searchTerms')}".lower()
            button.setVisible(not terms or terms in haystack)

    def toggle_engine(self) -> None:
        if self.engine.is_running:
            self.engine.stop()
        else:
            config = self.store.load()
            keys = self.store.get_keys(config)
            local = config.get("provider") == "custom"
            if not keys and not local:
                QMessageBox.warning(self, "Connect a provider", "Add an API key on the Providers page before starting Mind.")
                self.select_page(1)
                return
            self.engine.start()

    def apply_theme(self, choice: str, accent: str = "teal") -> None:
        app = QApplication.instance()
        app.setPalette(qt_palette(choice, accent))
        app.setStyleSheet(stylesheet(choice, accent))

    def show_window(self) -> None:
        self.show()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self) -> None:
        self._quitting = True
        self.configure_palette(False, self._palette_shortcut)
        self.configure_snip(False, self._snip_shortcut)
        self.configure_clipboard_history(False, self._clipboard_shortcut)
        self.configure_ghost_text(False, self._ghost_text_shortcut)
        self.definition_popup.dismiss()
        self.definition_popup.close()
        self.ask_ai_popup.dismiss()
        self.ask_ai_popup.close()
        self.quick_paste_popup.dismiss()
        self.quick_paste_popup.close()
        self.snipping_overlay.cancel_snip()
        self.snipping_overlay.close()
        self.snip_card.dismiss()
        self.snip_card.close()
        self.clipboard_history_dialog.dismiss()
        self.clipboard_history_dialog.close()
        self.secret_shield_card.dismiss()
        self.secret_shield_card.close()
        self.url_peek_card.dismiss()
        self.url_peek_card.close()
        self.ghost_text_overlay.dismiss()
        self.ghost_text_overlay.close()
        self.telegram.stop()
        self.engine.shutdown()
        self.tray.hide()
        QApplication.instance().quit()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._quitting:
            event.accept()
            return
        event.ignore()
        self.hide()
        self.tray.showMessage("Mind is still available", "Use the tray icon to reopen or quit Mind.", QSystemTrayIcon.Information, 2500)

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger}:
            self.show_window()

    def _engine_status_changed(self, status: str) -> None:
        self.dashboard.set_engine_status(status)
        running = status in {"running", "starting"}
        labels = {
            "running": "●  Engine running",
            "starting": "●  Engine starting…",
            "stopping": "●  Engine stopping…",
            "stopped": "●  Engine stopped",
            "error": "●  Engine needs attention",
        }
        self.sidebar_status.setText(labels.get(status, status))
        config = self.store.load()
        palette = theme_palette(
            str(config.get("theme", "system")),
            str(config.get("accent_color", "teal")),
        )
        color = palette["accent"] if status == "running" else palette["danger"] if status == "error" else palette["muted"]
        self.sidebar_status.setStyleSheet(f"color: {color};")
        self.sidebar_action.setText("Pause engine" if running else "Start engine")
        self.tray_engine_action.setText("Pause engine" if running else "Start engine")
        self.tray.setToolTip(f"Mind — {status}")

    def _config_updated(self) -> None:
        self.dashboard.refresh()
        self.configure_telegram()
        if self.engine.is_running:
            self._log("Configuration changed. The engine will hot-reload it.")

    def configure_telegram(self) -> None:
        """Start or stop the bridge to match the saved settings."""
        if self._quitting:
            self.telegram.stop()
            return
        config = self.store.load()
        wanted = bool(config.get("telegram_enabled", False))
        if wanted and not self.telegram.is_running:
            self.telegram.start()
        elif not wanted and self.telegram.is_running:
            self.telegram.stop()
            self._log("Telegram bridge stopped.")

    def _on_telegram_clipboard_requested(self, chat_id: object) -> None:
        clipboard = QApplication.clipboard()
        text = clipboard.text() if clipboard else ""
        # Sent through send_clipboard so it arrives with a copy button.
        self.telegram.send_clipboard(int(chat_id), text)

    def _on_telegram_clipboard_received(self, text: str) -> None:
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(text)

    def _on_telegram_image_received(self, path: object, context: object) -> None:
        """Read an image sent to the bot and reply with the text found in it.

        OCR needs a QImage, which has to be built on the GUI thread, so the
        worker hands the downloaded file over rather than decoding it itself.
        """
        details = context if isinstance(context, dict) else {}
        chat_id = int(details.get("chat_id", 0) or 0)
        source = Path(str(path))
        try:
            image = QImage(str(source))
            if image.isNull():
                self.telegram.send_text(chat_id, "Mind could not read that image.")
                return
            try:
                extracted = extract_text_from_image(image)
            except OcrError as exc:
                self.telegram.send_text(chat_id, f"Mind could not read that image: {exc}")
                return
            if not extracted.strip():
                self.telegram.send_text(chat_id, "No text was found in that image.")
                return

            caption = str(details.get("caption", "")).strip()
            if not caption:
                self.telegram.send_text(chat_id, extracted)
                return
            # A caption means "do this to what you read", e.g. send a photo with
            # the caption /summarize.
            request = parse_message(caption, str(self.store.load().get("prefix", "?")))
            command = None
            if request.trigger:
                try:
                    command = select_command(request, self.store.load_commands())
                except CommandRefused as exc:
                    self.telegram.send_text(chat_id, str(exc))
                    return
            if command is None:
                self.telegram.send_text(chat_id, extracted)
                return
            config = self.store.load()
            try:
                result = transform_text(
                    config,
                    self.store.get_keys(config),
                    extracted,
                    str(command.get("prompt", "")),
                )
            except TransformError as exc:
                self.telegram.send_text(chat_id, f"Read the image, but: {exc}")
                return
            self.telegram.send_text(chat_id, result)
        finally:
            # The download is scratch data; do not leave images in temp.
            try:
                source.unlink()
            except OSError:
                pass

    def _on_telegram_screenshot_requested(self, chat_id: object) -> None:
        """Capture the screen and send it back.

        Grabbing a screen has to happen on the GUI thread, so the bridge asks
        rather than doing it on its worker.
        """
        target = int(chat_id)
        screen = QApplication.primaryScreen()
        if screen is None:
            self.telegram.send_text(target, "No screen is available to capture.")
            return
        shot = screen.grabWindow(0)
        if shot.isNull():
            self.telegram.send_text(target, "Windows would not let Mind capture the screen.")
            return
        destination = Path(tempfile.gettempdir()) / f"mind-screen-{uuid.uuid4().hex[:10]}.png"
        try:
            if not shot.save(str(destination), "PNG"):
                self.telegram.send_text(target, "Could not save the screenshot.")
                return
            # As a photo: a screenshot asked for from a phone is meant to be
            # looked at, not downloaded first. As a panel, because the previous
            # one shows a screen that has since changed.
            self.telegram.send_image(
                target, destination, caption="Screen", panel=PANEL_SCREEN
            )
        finally:
            # A screenshot can hold anything that was on screen; do not leave it
            # sitting in temp once it has been sent.
            try:
                destination.unlink()
            except OSError:
                pass

    def notify_telegram(self, message: str) -> None:
        """Push an alert to every allowed chat, when the user asked for that."""
        config = self.store.load()
        if not config.get("telegram_notifications", False) or not self.telegram.is_running:
            return
        for chat_id in parse_allowed_chat_ids(config.get("telegram_allowed_chat_ids")):
            self.telegram.send_text(chat_id, message)

    def _settings_updated(self, theme: str, palette_enabled: bool, shortcut: str, accent: str) -> None:
        self.apply_theme(theme, accent)
        self.configure_palette(palette_enabled, shortcut)
        config = self.store.load()
        self.configure_snip(
            bool(config.get("screen_snip_enabled", True)),
            str(config.get("screen_snip_shortcut", "Ctrl+Alt+S")),
        )
        self.configure_clipboard_history(
            bool(config.get("clipboard_history_enabled", True)),
            str(config.get("clipboard_history_shortcut", "Ctrl+Alt+V")),
        )
        self.configure_ghost_text(
            bool(config.get("ghost_text_enabled", True)),
            str(config.get("ghost_text_shortcut", "Ctrl+Alt+Space")),
        )
        self._config_updated()

    def _notify_update_available(self, version: str, title: str) -> None:
        self.tray.showMessage(
            "Mind update available",
            f"Version {version} is ready: {title}. Open Preferences to install it.",
            QSystemTrayIcon.Information,
            5000,
        )

    def _install_update(self, download_path: str) -> None:
        try:
            launch_update_installer(download_path)
        except UpdateError as exc:
            QMessageBox.critical(self, "Could not install update", str(exc))
            return
        self.quit_app()

    def _log(self, message: str) -> None:
        self.diagnostics.append(message)

    def sync_shell_menu(self) -> None:
        """Make the Explorer entry match the saved setting on every launch.

        Saving Preferences is not enough on its own: an update writes a new
        executable, and a user who switched the entry on months ago never opens
        that page again. Reconciling here also puts the entry back if something
        else removed it.
        """
        enabled = bool(self.config.get("telegram_enabled", False)) and bool(
            self.config.get("telegram_send_menu_enabled", False)
        )
        try:
            if not shell_menu_apply(enabled):
                self._log("Send to Telegram: the right-click entry could not be written.")
        except OSError as exc:
            self._log(f"Send to Telegram: could not update the right-click entry ({exc}).")

    def configure_palette(self, enabled: bool, preferred_shortcut: str) -> None:
        user32 = ctypes.windll.user32
        hwnd = int(self.winId())
        self.selection_monitor.set_enabled(False)
        if self._palette_hotkey_registered:
            user32.UnregisterHotKey(hwnd, MIND_PALETTE_HOTKEY_ID)
            self._palette_hotkey_registered = False
        if not enabled:
            if self.palette:
                self.palette.close()
                self.palette = None
            self._configure_selection_monitor()
            return
        chosen = None
        for shortcut, modifiers, virtual_key in shortcut_candidates(preferred_shortcut):
            if user32.RegisterHotKey(hwnd, MIND_PALETTE_HOTKEY_ID, modifiers, virtual_key):
                chosen = shortcut
                break
        if chosen:
            self._palette_hotkey_registered = True
            self._palette_shortcut = chosen
            config = self.store.load()
            self._configure_selection_monitor(config)
            self._log(f"Mind Palette enabled: {chosen}")
            if chosen != preferred_shortcut:
                config["mind_palette_shortcut"] = chosen
                self.store.save(config)
                self.settings.refresh()
                self.tray.showMessage(
                    "Mind Palette ready",
                    f"{preferred_shortcut} was busy, so Mind is using {chosen}.",
                    QSystemTrayIcon.Information,
                    3500,
                )
            return

        config = self.store.load()
        config["mind_palette_enabled"] = False
        self.store.save(config)
        self._configure_selection_monitor(config)
        self.settings.refresh()
        self.tray.showMessage(
            "Mind Palette unavailable",
            "All available palette shortcuts are currently used by other applications.",
            QSystemTrayIcon.Warning,
            4000,
        )

    def configure_snip(self, enabled: bool, preferred_shortcut: str) -> None:
        user32 = ctypes.windll.user32
        hwnd = int(self.winId())
        if self._snip_hotkey_registered:
            user32.UnregisterHotKey(hwnd, MIND_SNIP_HOTKEY_ID)
            self._snip_hotkey_registered = False
        if not enabled:
            return
        chosen = None
        for shortcut, modifiers, virtual_key in snip_shortcut_candidates(preferred_shortcut):
            if user32.RegisterHotKey(hwnd, MIND_SNIP_HOTKEY_ID, modifiers, virtual_key):
                chosen = shortcut
                break
        if chosen:
            self._snip_hotkey_registered = True
            self._snip_shortcut = chosen
            self._log(f"Screen Snip enabled: {chosen}")
            if chosen != preferred_shortcut:
                config = self.store.load()
                config["screen_snip_shortcut"] = chosen
                self.store.save(config)
                self.settings.refresh()
            return

        config = self.store.load()
        config["screen_snip_enabled"] = False
        self.store.save(config)
        self.settings.refresh()

    def trigger_screen_snip(self) -> None:
        if self.palette and self.palette.isVisible():
            self.palette.close()
        if self.ask_ai_popup and self.ask_ai_popup.isVisible():
            self.ask_ai_popup.dismiss()
        if self.definition_popup and self.definition_popup.isVisible():
            self.definition_popup.dismiss()
        if self.quick_paste_popup and self.quick_paste_popup.isVisible():
            self.quick_paste_popup.dismiss()
        self.snipping_overlay.start_snip()

    def _on_snip_captured(self, pixmap, global_rect) -> None:
        self.snip_card.show_for_pixmap(pixmap, global_rect)

    def configure_clipboard_history(self, enabled: bool, preferred_shortcut: str) -> None:
        user32 = ctypes.windll.user32
        hwnd = int(self.winId())
        if self._clipboard_hotkey_registered:
            user32.UnregisterHotKey(hwnd, MIND_CLIPBOARD_HOTKEY_ID)
            self._clipboard_hotkey_registered = False
        if not enabled:
            return
        chosen = None
        for shortcut, modifiers, virtual_key in clipboard_history_shortcut_candidates(preferred_shortcut):
            if user32.RegisterHotKey(hwnd, MIND_CLIPBOARD_HOTKEY_ID, modifiers, virtual_key):
                chosen = shortcut
                break
        if chosen:
            self._clipboard_hotkey_registered = True
            self._clipboard_shortcut = chosen
            self._log(f"Clipboard History enabled: {chosen}")
            if chosen != preferred_shortcut:
                config = self.store.load()
                config["clipboard_history_shortcut"] = chosen
                self.store.save(config)
                self.settings.refresh()
            return

        config = self.store.load()
        config["clipboard_history_enabled"] = False
        self.store.save(config)
        self.settings.refresh()

    def trigger_clipboard_history(self) -> None:
        if self.palette and self.palette.isVisible():
            self.palette.close()
        if self.ask_ai_popup and self.ask_ai_popup.isVisible():
            self.ask_ai_popup.dismiss()
        if self.definition_popup and self.definition_popup.isVisible():
            self.definition_popup.dismiss()
        if self.quick_paste_popup and self.quick_paste_popup.isVisible():
            self.quick_paste_popup.dismiss()
        self.clipboard_history_dialog.show_centered_or_cursor()

    def _on_clipboard_ai_action(self, text: str, prompt: str) -> None:
        session = SelectionSession(selected_text=text, is_editable=False, source_hwnd=0)
        self.ask_ai_popup.show_for_selection(session, initial_prompt=prompt)

    def _on_url_summarize_requested(self, clean_url: str) -> None:
        session = SelectionSession(selected_text=clean_url, is_editable=False, source_hwnd=0)
        self.ask_ai_popup.show_for_selection(
            session,
            initial_prompt=f"Summarize the key information, main takeaways, and context from this webpage:\n{clean_url}",
        )

    def configure_ghost_text(self, enabled: bool, preferred_shortcut: str) -> None:
        user32 = ctypes.windll.user32
        hwnd = int(self.winId())
        if self._ghost_text_hotkey_registered:
            user32.UnregisterHotKey(hwnd, MIND_GHOST_TEXT_HOTKEY_ID)
            self._ghost_text_hotkey_registered = False
        if not enabled:
            return
        chosen = None
        for shortcut, modifiers, virtual_key in ghost_text_shortcut_candidates(preferred_shortcut):
            if user32.RegisterHotKey(hwnd, MIND_GHOST_TEXT_HOTKEY_ID, modifiers, virtual_key):
                chosen = shortcut
                break
        if chosen:
            self._ghost_text_hotkey_registered = True
            self._ghost_text_shortcut = chosen
            self._log(f"Ghost Text enabled: {chosen}")
            if chosen != preferred_shortcut:
                config = self.store.load()
                config["ghost_text_shortcut"] = chosen
                self.store.save(config)
                self.settings.refresh()
            return

        config = self.store.load()
        config["ghost_text_enabled"] = False
        self.store.save(config)
        self.settings.refresh()

    def trigger_ghost_text(self) -> None:
        if self.palette and self.palette.isVisible():
            self.palette.close()
        if self.ask_ai_popup and self.ask_ai_popup.isVisible():
            self.ask_ai_popup.dismiss()
        if self.definition_popup and self.definition_popup.isVisible():
            self.definition_popup.dismiss()
        if self.quick_paste_popup and self.quick_paste_popup.isVisible():
            self.quick_paste_popup.dismiss()
        target_hwnd = int(ctypes.windll.user32.GetForegroundWindow() or 0)
        session = SelectionSession.capture(target_hwnd, timeout=0.25) if target_hwnd else None
        text = session.text if session else ""
        if not text and hasattr(self, "_last_copied_text"):
            text = self._last_copied_text
        suggestion = suggest_sentence_completion(text) if text else None
        if not suggestion:
            suggestion = " please let me know if you need anything else."
        self.ghost_text_overlay.show_suggestion(suggestion, target_hwnd)

    def _configure_selection_monitor(self, config: dict | None = None) -> None:
        if self._quitting:
            self.selection_monitor.set_enabled(False)
            return
        self.selection_monitor.set_enabled(True)

    def nativeEvent(self, event_type, message):
        try:
            pointer = int(message)
            if not pointer:
                return super().nativeEvent(event_type, message)
            native_message = wintypes.MSG.from_address(pointer)
            if native_message.message == WM_HOTKEY:
                if native_message.wParam == MIND_PALETTE_HOTKEY_ID:
                    self._palette_requested()
                    return True, 0
                if native_message.wParam == MIND_SNIP_HOTKEY_ID:
                    self.trigger_screen_snip()
                    return True, 0
                if native_message.wParam == MIND_CLIPBOARD_HOTKEY_ID:
                    self.trigger_clipboard_history()
                    return True, 0
                if native_message.wParam == MIND_GHOST_TEXT_HOTKEY_ID:
                    self.trigger_ghost_text()
                    return True, 0
        except (TypeError, ValueError, OSError):
            pass
        return super().nativeEvent(event_type, message)

    def _palette_requested(self) -> None:
        if self.ask_ai_popup and self.ask_ai_popup.isVisible():
            self.ask_ai_popup.dismiss()
        if self.palette and self.palette.isVisible():
            self.palette.show_near_cursor()
            return
        if self._palette_pending:
            return
        target_hwnd = int(ctypes.windll.user32.GetForegroundWindow() or 0)
        if not target_hwnd:
            return
        self._palette_pending = True
        QTimer.singleShot(100, lambda: self._open_palette_for_selection(target_hwnd))

    def _automatic_selection_requested(
        self,
        target_hwnd: int,
        avoid_rect: tuple[int, int, int, int] | None,
    ) -> None:
        if self._selection_pending or target_hwnd == int(self.winId()):
            return
        self._selection_pending = True
        QTimer.singleShot(
            140,
            lambda: self._open_automatic_selection(target_hwnd, avoid_rect),
        )

    def _open_automatic_selection(
        self,
        target_hwnd: int,
        avoid_rect: tuple[int, int, int, int] | None,
    ) -> None:
        self._selection_pending = False
        if ctypes.windll.user32.GetForegroundWindow() != target_hwnd:
            return
        session = SelectionSession.capture(target_hwnd, timeout=0.35)
        if session is None:
            return
        config = self.store.load()
        word = normalize_selected_word(session.text)
        if (
            bool(config.get("word_definitions_enabled", True))
            and word is not None
            and not is_editable_input_target(target_hwnd)
        ):
            if self.palette and self.palette.isVisible():
                self.palette.close()
            if self.ask_ai_popup.isVisible():
                self.ask_ai_popup.dismiss()
            self.definition_popup.lookup(word, avoid_rect)
            return

        self.definition_popup.dismiss()

        phone_info = parse_maldivian_phone(session.text)
        if phone_info is not None and not is_notion_input(target_hwnd):
            if self.palette and self.palette.isVisible():
                self.palette.close()
            self.ask_ai_popup.show_phone_actions(phone_info, avoid_rect)
            return

        local_math_result = solve_math_locally(session.text)
        if local_math_result is not None and not is_notion_input(target_hwnd):
            if self.palette and self.palette.isVisible():
                self.palette.close()
            self.ask_ai_popup.show_local_math_result(session.text, local_math_result, avoid_rect)
            return

        if bool(config.get("converter_tooltips_enabled", True)) and not is_notion_input(target_hwnd):
            conversion = detect_and_convert(session.text)
            if conversion is not None:
                if self.palette and self.palette.isVisible():
                    self.palette.close()
                self.ask_ai_popup.show_converter_result(conversion, avoid_rect)
                return

        if bool(config.get("url_peek_enabled", True)) and is_http_url(session.text) and not is_notion_input(target_hwnd):
            if self.palette and self.palette.isVisible():
                self.palette.close()
            if self.ask_ai_popup.isVisible():
                self.ask_ai_popup.dismiss()
            if self.definition_popup.isVisible():
                self.definition_popup.dismiss()
            self.url_peek_card.show_for_url(session.text)
            return

        is_question = is_question_text(session.text)
        is_unsolved_math = is_math_or_number_problem(session.text)

        if (is_question or is_unsolved_math) and not is_notion_input(target_hwnd):
            if self.palette and self.palette.isVisible():
                self.palette.close()
            self.ask_ai_popup.show_pill_for_question(session.text, avoid_rect)
            return

        self.ask_ai_popup.dismiss()

        if bool(
            config.get("mind_palette_enabled", False)
            and config.get("mind_palette_auto_show_on_selection", False)
        ):
            if is_editable_input_target(target_hwnd):
                self._show_palette_for_session(session, avoid_rect)

    def _open_palette_for_selection(
        self,
        target_hwnd: int,
        allow_image: bool = True,
        notify_when_empty: bool = True,
        capture_timeout: float = 1.0,
        avoid_rect: tuple[int, int, int, int] | None = None,
    ) -> None:
        self._palette_pending = False
        if ctypes.windll.user32.GetForegroundWindow() != target_hwnd:
            return
        config = self.store.load()
        image_session = (
            ClipboardImageSession.capture(target_hwnd)
            if allow_image and bool(config.get("mind_palette_image_ocr_enabled", True))
            else None
        )
        session = SelectionSession.capture(target_hwnd, timeout=capture_timeout)
        if session is None:
            session = image_session
        if session is None and notify_when_empty:
            self.tray.showMessage(
                "Mind Palette",
                f"Select text or copy an image, then press {self._palette_shortcut}.",
                QSystemTrayIcon.Information,
                2500,
            )
            return
        if session is None:
            return
        self._show_palette_for_session(session, avoid_rect)

    def _show_palette_for_session(
        self,
        session: SelectionSession | ClipboardImageSession,
        avoid_rect: tuple[int, int, int, int] | None,
    ) -> None:
        if self.palette and self.palette.isVisible():
            return
        if self.ask_ai_popup and self.ask_ai_popup.isVisible():
            self.ask_ai_popup.dismiss()
        self.palette = MindPalette(self.store, session, avoid_rect=avoid_rect)
        self.palette.completed.connect(self._palette_completed)
        self.palette.finished.connect(self._palette_closed)
        self.palette.show_near_cursor()

    def _palette_completed(self, ok: bool, message: str) -> None:
        icon = QSystemTrayIcon.Information if ok else QSystemTrayIcon.Warning
        self.tray.showMessage("Mind Palette", message, icon, 2500)

    def _palette_closed(self, *_args) -> None:
        if self.palette:
            self.palette.deleteLater()
            self.palette = None

    def _on_clipboard_data_changed(self) -> None:
        clipboard = QApplication.clipboard()
        if not clipboard:
            return
        text = clipboard.text()
        if text and text.strip():
            self._last_copied_text = text
            self._last_copied_time = time.monotonic()
            self._pasted_texts.clear()
            if hasattr(self, "clipboard_history_dialog") and self.clipboard_history_dialog:
                self.clipboard_history_dialog.history_store.add_entry(text)
            config = self.store.load()
            if bool(config.get("secret_shield_enabled", True)) and hasattr(self, "secret_shield_card") and self.secret_shield_card:
                findings = detect_secrets(text)
                if findings:
                    self.secret_shield_card.show_for_findings(text, findings)

    def _on_single_click_gesture(
        self,
        target_hwnd: int,
        avoid_rect: tuple[int, int, int, int] | None,
    ) -> None:
        if target_hwnd == int(self.winId()):
            return
        config = self.store.load()
        if not bool(config.get("quick_paste_enabled", True)):
            return
        if not self._last_copied_text or self._last_copied_text in self._pasted_texts:
            return
        if time.monotonic() - self._last_copied_time > 180.0:
            return
        if not is_editable_input_target(target_hwnd):
            return
        if self.palette and self.palette.isVisible():
            self.palette.close()
        if self.definition_popup.isVisible():
            self.definition_popup.dismiss()
        if self.ask_ai_popup.isVisible():
            self.ask_ai_popup.dismiss()
        self.quick_paste_popup.show_for_text(self._last_copied_text, target_hwnd, avoid_rect)

    def _on_quick_paste_pasted(self, text: str) -> None:
        self._pasted_texts.add(text)

