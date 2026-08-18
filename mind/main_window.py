from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
import tempfile
import uuid
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QThread, QThreadPool, QTimer, QUrl, Qt, Signal
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
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
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
    QSpinBox,
    QSystemTrayIcon,
    QTabWidget,
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
from .single_instance import action_message_id, show_message_id
from .shell_menu import apply as shell_menu_apply, describe as shell_menu_describe
from .telegram_bridge import PANEL_SCREEN, TelegramBridge
from .adb_client import AdbError, Phone
from .network_devices import local_ipv4
from .network_scanner import (
    DEFAULT_INTERVAL_SECONDS as NETWORK_DEFAULT_INTERVAL,
    BlockDevice,
    NetworkScanner,
    RouterFilterProbe,
    RouterTest,
)
from .phone_watch import PhoneWatcher, phone_for
from .telegram_ui import call_alert_text
from .windows_toast import dismiss_call, register as register_toasts, show_call, take_action
from .telegram_system import read_running_apps, read_visible_apps
from .watchers import (
    APP_KINDS as WATCHER_APP_KINDS,
    FOLDER_NEW as WATCHER_FOLDER_NEW,
    KINDS as WATCHER_KINDS,
    Watcher,
    from_dict as watcher_from_dict,
    kind_by_key as watcher_kind_by_key,
    new_watcher,
    to_dict as watcher_to_dict,
    toggled as watcher_toggled,
)
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


class WatcherDialog(QDialog):
    """Create or edit one watcher.

    The unit and the target field follow the kind, because "20" means percent
    for a battery and gigabytes for a disk, and a folder has no number at all.
    """

    def __init__(self, watcher: Watcher, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Watcher")
        self.setMinimumWidth(430)
        self._watcher = watcher
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        self.kind = QComboBox()
        for kind in WATCHER_KINDS:
            self.kind.addItem(kind.label, kind.key)
        index = self.kind.findData(watcher.kind)
        self.kind.setCurrentIndex(max(index, 0))
        self.kind.currentIndexChanged.connect(self._kind_changed)
        layout.addWidget(QLabel("Tell me when"))
        layout.addWidget(self.kind)

        self.threshold = QSpinBox()
        self.threshold.setRange(0, 100000)
        self.threshold.setValue(int(watcher.threshold))
        self.threshold_label = QLabel("")
        threshold_row = QHBoxLayout()
        threshold_row.addWidget(self.threshold)
        threshold_row.addWidget(self.threshold_label, 1)
        layout.addLayout(threshold_row)

        self.target = QLineEdit(watcher.target)
        # Apps are picked from what is actually on this PC rather than typed
        # from memory: nobody knows a program is called ApplicationFrameHost.exe
        # until they look. Editable, because the point may be to watch for a game
        # that is not running yet.
        self.app_picker = QComboBox()
        self.app_picker.setEditable(True)
        self.app_picker.setInsertPolicy(QComboBox.NoInsert)
        self.app_picker.setMinimumWidth(240)
        # An editable combo shows nothing when no row is chosen, and with the
        # list behind the arrow rather than in front of it the field read as an
        # empty text box. The placeholder says there is something to open.
        if self.app_picker.lineEdit() is not None:
            self.app_picker.lineEdit().setPlaceholderText("Pick one, or type a name")
        completer = self.app_picker.completer()
        if completer is not None:
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            # Matches anywhere in the name, so "chrome" finds it however the
            # program's file name begins.
            completer.setFilterMode(Qt.MatchContains)
        self.browse = QPushButton("Choose…")
        self.browse.clicked.connect(self._choose_folder)
        target_row = QHBoxLayout()
        target_row.addWidget(self.target, 1)
        target_row.addWidget(self.app_picker, 1)
        target_row.addWidget(self.browse)
        self.target_label = QLabel("")
        layout.addWidget(self.target_label)
        layout.addLayout(target_row)

        self.cooldown = QSpinBox()
        self.cooldown.setRange(0, 1440)
        self.cooldown.setValue(int(watcher.cooldown_minutes))
        cooldown_row = QHBoxLayout()
        cooldown_row.addWidget(self.cooldown)
        cooldown_row.addWidget(
            QLabel("minutes before saying it again (0 = say it once)"), 1
        )
        layout.addLayout(cooldown_row)

        buttons = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setProperty("primary", True)
        save.clicked.connect(self.accept)
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        self._kind_changed()

    def _kind_changed(self) -> None:
        kind = watcher_kind_by_key(str(self.kind.currentData()))
        if kind is None:
            return
        # A kind has a number only if it has a unit to measure it in. Folders,
        # apps, drives appearing and networks appearing have nothing to compare.
        wants_number = bool(kind.unit)
        self.threshold.setVisible(wants_number)
        self.threshold_label.setVisible(wants_number)
        self.threshold_label.setText(kind.unit)
        is_app = kind.key in WATCHER_APP_KINDS
        self.target.setVisible(kind.needs_target and not is_app)
        self.app_picker.setVisible(is_app)
        self.target_label.setVisible(kind.needs_target)
        self.browse.setVisible(kind.key == WATCHER_FOLDER_NEW)
        if kind.key == WATCHER_FOLDER_NEW:
            self.target_label.setText("Folder to watch")
        elif is_app:
            self._fill_app_picker()
            self.target_label.setText("Application — pick one, or type a name")
        else:
            self.target_label.setText("Drive, for example C:\\")
        if wants_number and not self.threshold.value():
            self.threshold.setValue(int(kind.default_threshold))

    def _fill_app_picker(self) -> None:
        """List what is on this PC, the ones with windows first.

        Filled when the app kinds are chosen rather than at startup, so the list
        is of what is running now and costs nothing for every other kind.
        """
        if self.app_picker.count():
            return
        chosen = self._watcher.target
        try:
            visible = read_visible_apps()
            running = sorted(read_running_apps())
        except OSError:
            visible, running = [], []
        seen: set[str] = set()
        for name, title in visible:
            # The window title is what a person recognises; the process name is
            # what is actually stored.
            self.app_picker.addItem(f"{name}  —  {title[:38]}" if title else name, name)
            seen.add(name)
        for name in running:
            if name not in seen:
                self.app_picker.addItem(name, name)
                seen.add(name)
        if chosen:
            index = self.app_picker.findData(chosen)
            if index >= 0:
                self.app_picker.setCurrentIndex(index)
            else:
                # A program that is not running now still has to be editable.
                # The index is cleared first, or the combo keeps pointing at its
                # first row while showing this text.
                self.app_picker.setCurrentIndex(-1)
                self.app_picker.setCurrentText(chosen)
        else:
            self.app_picker.setCurrentIndex(-1)
            self.app_picker.setCurrentText("")

    def _choose_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Folder to watch", self.target.text())
        if chosen:
            self.target.setText(chosen)

    def result_watcher(self) -> Watcher:
        kind = str(self.kind.currentData())
        if kind in WATCHER_APP_KINDS:
            # What is written in the box decides. When it still reads as the row
            # that was picked, that row's process name is used; when it has been
            # typed over, the typing wins - the app may not be running yet, so it
            # cannot be required to appear in the list.
            index = self.app_picker.currentIndex()
            typed = self.app_picker.currentText().strip()
            label = self.app_picker.itemText(index) if index >= 0 else ""
            stored = str(self.app_picker.itemData(index) or "") if index >= 0 else ""
            target = stored if stored and typed == label else typed.split("  —  ")[0].strip()
        else:
            target = self.target.text().strip()
        return replace(
            self._watcher,
            kind=kind,
            threshold=float(self.threshold.value()),
            target=target,
            cooldown_minutes=int(self.cooldown.value()),
        )


class NotificationsPage(QWidget):
    """Watchers: conditions about this PC that send a message to Telegram."""

    updated = Signal()

    def __init__(self, store: ConfigStore, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(18)
        root.addWidget(page_header(
            "Notifications",
            "Tell me when something happens on this PC, in Telegram.",
            "WATCHERS",
        ))

        toolbar_card = Card(variant="InsetCard")
        toolbar = QHBoxLayout(toolbar_card)
        toolbar.setContentsMargins(14, 12, 14, 12)
        toolbar.setSpacing(10)
        # The switch lives here, beside the things it governs. Buried in
        # Preferences it was the third feature in a row to sit built, configured
        # and silently switched off.
        self.enabled_switch = ToggleSwitch()
        self.enabled_switch.toggled.connect(self._set_enabled)
        self.state_label = QLabel("")
        self.state_label.setObjectName("Muted")
        self.state_label.setWordWrap(True)
        add = QPushButton("＋  New watcher")
        add.setProperty("primary", True)
        add.clicked.connect(self._add)
        self.edit_button = QPushButton("Edit")
        self.edit_button.clicked.connect(self._edit)
        self.delete_button = QPushButton("Delete")
        self.delete_button.setProperty("danger", True)
        self.delete_button.clicked.connect(self._delete)
        toolbar.addWidget(self.enabled_switch)
        toolbar.addWidget(self.state_label, 1)
        toolbar.addWidget(self.edit_button)
        toolbar.addWidget(self.delete_button)
        toolbar.addWidget(add)
        root.addWidget(toolbar_card)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Tell me when", "Repeat", "On"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.doubleClicked.connect(self._edit)
        self.table.itemSelectionChanged.connect(self._sync_actions)
        # Pausing is the most frequent edit, so it is a checkbox in the row
        # rather than a trip through the dialog.
        self.table.itemChanged.connect(self._on_item_changed)
        root.addWidget(self.table, 1)

        self.empty_label = QLabel(
            "No watchers yet. Add one to be told when the battery runs low, a disk "
            "fills up, the PC sits idle, or a file lands in a folder."
        )
        self.empty_label.setObjectName("Muted")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setWordWrap(True)
        root.addWidget(self.empty_label)

        self.watchers: list[Watcher] = []
        self._loading = False
        self.refresh()

    def refresh(self) -> None:
        self.watchers = [
            w for w in (watcher_from_dict(item) for item in self.store.load_watchers()) if w
        ]
        config = self.store.load()
        on = bool(config.get("watchers_enabled", False))
        telegram_on = bool(config.get("telegram_enabled", False))
        self._loading = True
        try:
            self.enabled_switch.setChecked(on)
        finally:
            self._loading = False
        self.enabled_switch.setEnabled(telegram_on)
        if not telegram_on:
            self.state_label.setText(
                "Watchers send to Telegram, and the bridge is off. Turn it on in "
                "Preferences → Telegram."
            )
        elif not on:
            self.state_label.setText("Watchers are off. Use the switch to start them.")
        elif not self.watchers:
            self.state_label.setText("On, but there is nothing to watch yet.")
        else:
            self.state_label.setText("Watching. Alerts go to your allowed chats.")
        self._loading = True
        try:
            self.table.setRowCount(len(self.watchers))
            for row, watcher in enumerate(self.watchers):
                label = QTableWidgetItem(watcher.label)
                label.setFlags(label.flags() & ~Qt.ItemIsEditable)
                repeat = QTableWidgetItem(
                    "once" if watcher.cooldown_minutes == 0 else f"every {watcher.cooldown_minutes} min"
                )
                repeat.setFlags(repeat.flags() & ~Qt.ItemIsEditable)
                switch = QTableWidgetItem("")
                switch.setFlags(
                    (switch.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable
                )
                switch.setCheckState(Qt.Checked if watcher.enabled else Qt.Unchecked)
                self.table.setItem(row, 0, label)
                self.table.setItem(row, 1, repeat)
                self.table.setItem(row, 2, switch)
        finally:
            self._loading = False
        self.empty_label.setVisible(not self.watchers)
        self.table.setVisible(bool(self.watchers))
        self._sync_actions()

    def _sync_actions(self) -> None:
        chosen = self.table.currentRow() >= 0 and bool(self.watchers)
        self.edit_button.setEnabled(chosen)
        self.delete_button.setEnabled(chosen)

    def _set_enabled(self, on: bool) -> None:
        """Start or stop watching, from the page the watchers live on.

        Writes the same setting Preferences shows, so the two can never disagree
        about whether anything is being watched.
        """
        if self._loading:
            return
        config = self.store.load()
        if bool(config.get("watchers_enabled", False)) == on:
            return
        config["watchers_enabled"] = on
        self.store.save(config)
        self.refresh()
        self.updated.emit()

    def _save(self) -> None:
        self.store.save_watchers([watcher_to_dict(w) for w in self.watchers])
        self.refresh()
        self.updated.emit()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 2:
            return
        row = item.row()
        if 0 <= row < len(self.watchers):
            self.watchers[row] = watcher_toggled(
                self.watchers[row], item.checkState() == Qt.Checked
            )
            self._save()

    def _add(self) -> None:
        dialog = WatcherDialog(new_watcher(WATCHER_KINDS[0].key), self)
        if dialog.exec() != QDialog.Accepted:
            return
        self.watchers.append(dialog.result_watcher())
        self._save()

    def _edit(self) -> None:
        row = self.table.currentRow()
        if not 0 <= row < len(self.watchers):
            return
        dialog = WatcherDialog(self.watchers[row], self)
        if dialog.exec() != QDialog.Accepted:
            return
        self.watchers[row] = dialog.result_watcher()
        self._save()

    def _delete(self) -> None:
        row = self.table.currentRow()
        if not 0 <= row < len(self.watchers):
            return
        del self.watchers[row]
        self._save()


ROUTER_PASSWORD_MASK = "•" * 10


class NetworkDevicesPage(QWidget):
    """What else is on this Wi-Fi, kept up to date while Mind runs."""

    updated = Signal()

    def __init__(self, store: ConfigStore, scanner, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self.scanner = scanner
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(18)
        root.addWidget(page_header(
            "Wi-Fi devices",
            "Everything Mind can see on this network, and when it was last here.",
            "NETWORK",
        ))

        toolbar_card = Card(variant="InsetCard")
        toolbar = QHBoxLayout(toolbar_card)
        toolbar.setContentsMargins(14, 12, 14, 12)
        toolbar.setSpacing(10)
        self.enabled_switch = ToggleSwitch()
        self.enabled_switch.toggled.connect(self._set_enabled)
        self.status_label = QLabel("")
        self.status_label.setObjectName("Muted")
        self.status_label.setWordWrap(True)
        self.interval = QSpinBox()
        self.interval.setRange(15, 3600)
        self.interval.setSuffix(" s")
        self.interval.setToolTip("How often to look")
        self.interval.valueChanged.connect(self._interval_changed)
        self.scan_button = QPushButton("Scan now")
        self.scan_button.setProperty("primary", True)
        self.scan_button.clicked.connect(self._scan_now)
        self.rename_button = QPushButton("Rename")
        self.rename_button.clicked.connect(self._rename)
        self.forget_button = QPushButton("Forget")
        self.forget_button.setProperty("danger", True)
        self.forget_button.clicked.connect(self._forget)
        # One button rather than two: a device is either on the Wi-Fi or it is
        # not, and the label says which way this click goes.
        self.block_button = QPushButton("Block")
        self.block_button.setProperty("danger", True)
        self.block_button.clicked.connect(self._toggle_block)
        toolbar.addWidget(self.enabled_switch)
        toolbar.addWidget(self.status_label, 1)
        toolbar.addWidget(self.interval)
        toolbar.addWidget(self.rename_button)
        toolbar.addWidget(self.forget_button)
        toolbar.addWidget(self.block_button)
        toolbar.addWidget(self.scan_button)
        root.addWidget(toolbar_card)

        # The router's own list is the only place a phone's real name lives, and
        # reaching it means signing in. Everyone's password is different, so it
        # is asked for here rather than assumed.
        router_card = Card(variant="InsetCard")
        router = QHBoxLayout(router_card)
        router.setContentsMargins(14, 12, 14, 12)
        router.setSpacing(10)
        self.router_address = QLineEdit()
        self.router_address.setPlaceholderText("Router address")
        self.router_address.setMaximumWidth(150)
        self.router_username = QLineEdit()
        self.router_username.setPlaceholderText("Username")
        self.router_username.setMaximumWidth(130)
        self.router_password = QLineEdit()
        self.router_password.setPlaceholderText("Password")
        self.router_password.setEchoMode(QLineEdit.Password)
        self.router_password.setMaximumWidth(150)
        self.router_test = QPushButton("Test")
        self.router_test.clicked.connect(self._test_router)
        # Blocking a device has to happen on the router, and which page does it
        # differs by firmware. This looks for that page without touching it, so
        # what comes back is an answer rather than a changed setting.
        self.router_filters = QPushButton("Find block controls")
        self.router_filters.setToolTip(
            "Ask the router which of its pages keeps a block list. Only reads."
        )
        self.router_filters.clicked.connect(self._probe_filters)
        self.router_status = QLabel("")
        self.router_status.setObjectName("Muted")
        self.router_status.setWordWrap(True)
        for field in (self.router_address, self.router_username, self.router_password):
            field.editingFinished.connect(self._save_router)
        router.addWidget(QLabel("Router"))
        router.addWidget(self.router_address)
        router.addWidget(self.router_username)
        router.addWidget(self.router_password)
        router.addWidget(self.router_test)
        router.addWidget(self.router_filters)
        router.addWidget(self.router_status, 1)
        root.addWidget(router_card)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Name", "IP address", "MAC address", "Vendor", "Status", "Last seen"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 6):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.table.doubleClicked.connect(self._rename)
        self.table.itemSelectionChanged.connect(self._sync_actions)
        root.addWidget(self.table, 1)

        self.empty_label = QLabel(
            "Nothing found yet. Turn the switch on to look — Mind pings every address "
            "on this network, asks devices to name themselves, and remembers what it finds."
        )
        self.empty_label.setObjectName("Muted")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setWordWrap(True)
        root.addWidget(self.empty_label)

        self._loading = False
        self._busy_blocking = False
        self.blocked: set[str] = set(scanner.blocked) if scanner is not None else set()
        if scanner is not None:
            scanner.devices_changed.connect(self._show_devices)
            scanner.blocked_changed.connect(self._show_blocked)
            scanner.scanning.connect(self._scanning)
        self.refresh()

    def refresh(self) -> None:
        config = self.store.load()
        on = bool(config.get("network_scan_enabled", False))
        self._loading = True
        try:
            self.enabled_switch.setChecked(on)
            self.interval.setValue(
                int(config.get("network_scan_seconds", NETWORK_DEFAULT_INTERVAL))
            )
            self.router_address.setText(str(config.get("router_address", "")))
            self.router_username.setText(str(config.get("router_username", "")))
            # Shown as a mask when one is stored, never as the password itself.
            self.router_password.setText(
                ROUTER_PASSWORD_MASK if self.store.get_router_password(config) else ""
            )
        finally:
            self._loading = False
        self._show_devices(list(self.scanner.devices) if self.scanner else [])

    def _show_devices(self, devices: list) -> None:
        now = time.time()
        self.devices = list(devices)
        self.table.setRowCount(len(self.devices))
        for row, device in enumerate(self.devices):
            # Blocked beats online: a device the router is refusing may still
            # be there, trying, and "Online" would read as though it worked.
            if device.mac in self.blocked:
                status = "Blocked"
            elif device.online:
                status = "Online"
            else:
                status = "Offline"
            values = [
                device.display_name,
                device.ip or "—",
                device.mac,
                device.vendor or "Unknown",
                status,
                device.seen_label(now),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row, column, item)
        self.empty_label.setVisible(not self.devices)
        self.table.setVisible(bool(self.devices))
        online = sum(1 for device in self.devices if device.online)
        if not bool(self.store.load().get("network_scan_enabled", False)):
            self.status_label.setText("Scanning is off.")
        else:
            self.status_label.setText(
                f"{online} online of {len(self.devices)} known."
                if self.devices
                else "Looking…"
            )
        self._sync_actions()

    def _scanning(self, busy: bool) -> None:
        self.scan_button.setEnabled(not busy)
        self.scan_button.setText("Scanning…" if busy else "Scan now")

    def _sync_actions(self) -> None:
        chosen = self.table.currentRow() >= 0 and bool(getattr(self, "devices", []))
        self.rename_button.setEnabled(chosen)
        self.forget_button.setEnabled(chosen)
        device = self._selected()
        blocked = device is not None and device.mac in self.blocked
        self.block_button.setText("Unblock" if blocked else "Block")
        self.block_button.setEnabled(chosen and not self._busy_blocking)
        if device is not None and self._is_this_pc(device):
            # Blocking the machine Mind is running on would cut the connection
            # that undoes it.
            self.block_button.setEnabled(False)
            self.block_button.setToolTip("This is this PC. Mind will not block it.")
        else:
            self.block_button.setToolTip(
                "Ask the router to refuse this device on every Wi-Fi network it has."
            )

    @staticmethod
    def _is_this_pc(device) -> bool:
        here = local_ipv4()
        return bool(here and device.ip and device.ip == here)

    def _show_blocked(self, blocked: list) -> None:
        self.blocked = set(blocked)
        self._show_devices(list(self.devices))

    def _toggle_block(self) -> None:
        """Block the selected device, or let it back on, after asking.

        The router is the only thing that can do either, and both are worth a
        question first: one takes a phone off the Wi-Fi, and the other puts it
        back.
        """
        device = self._selected()
        if device is None or self._busy_blocking:
            return
        if self._is_this_pc(device):
            return
        blocking = device.mac not in self.blocked
        question = (
            f"Block {device.display_name} from the Wi-Fi?\n\nThe router will refuse it "
            "on every network it broadcasts until you unblock it here."
            if blocking
            else f"Let {device.display_name} back onto the Wi-Fi?"
        )
        confirmed = QMessageBox.question(
            self,
            "Block device" if blocking else "Unblock device",
            question,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirmed != QMessageBox.Yes:
            return

        self._busy_blocking = True
        self.block_button.setEnabled(False)
        self.status_label.setText(
            f"Asking the router to {'block' if blocking else 'unblock'} {device.display_name}…"
        )
        self._block_thread = QThread(self)
        self._block_worker = BlockDevice(
            self.store, device.mac, device.display_name, blocking
        )
        self._block_worker.moveToThread(self._block_thread)
        self._block_thread.started.connect(self._block_worker.run)
        self._block_worker.done.connect(self._block_finished)
        self._block_thread.start()

    def _block_finished(self, worked: bool, message: str) -> None:
        self._busy_blocking = False
        self.status_label.setText(message)
        thread = getattr(self, "_block_thread", None)
        if thread is not None:
            thread.quit()
            thread.wait(2000)
        self._block_thread = None
        self._block_worker = None
        if not worked:
            QMessageBox.warning(self, "The router refused", message)
        # Either way the router is the authority on who is blocked, so the next
        # scan is what updates the list rather than anything assumed here.
        if self.scanner is not None:
            self.scanner.scan_now()
        self._sync_actions()

    def _selected(self):
        row = self.table.currentRow()
        devices = getattr(self, "devices", [])
        return devices[row] if 0 <= row < len(devices) else None

    def _set_enabled(self, on: bool) -> None:
        if self._loading:
            return
        config = self.store.load()
        config["network_scan_enabled"] = on
        self.store.save(config)
        if self.scanner is not None:
            self.scanner.start() if on else self.scanner.stop()
        self.refresh()
        self.updated.emit()

    def _save_router(self) -> None:
        """Keep the router details, with the password encrypted like the token.

        A password already saved shows as a mask, so leaving the field untouched
        must not overwrite it with the mask itself.
        """
        if self._loading:
            return
        config = self.store.load()
        config["router_address"] = self.router_address.text().strip()
        config["router_username"] = self.router_username.text().strip()
        typed = self.router_password.text()
        if typed != ROUTER_PASSWORD_MASK:
            config = self.store.set_router_password(config, typed)
        self.store.save(config)

    def _test_router(self) -> None:
        """Ask the router now, and say plainly what came back.

        On its own thread: signing in and reading a page takes seconds, and the
        window must not freeze while it happens.
        """
        self._save_router()
        self.router_test.setEnabled(False)
        self.router_status.setText("Asking the router…")
        self._router_thread = QThread(self)
        self._router_worker = RouterTest(self.store)
        self._router_worker.moveToThread(self._router_thread)
        self._router_thread.started.connect(self._router_worker.run)
        self._router_worker.done.connect(self._router_tested)
        self._router_thread.start()

    def _router_tested(self, message: str) -> None:
        self.router_status.setText(message)
        self.router_test.setEnabled(True)
        thread = getattr(self, "_router_thread", None)
        if thread is not None:
            thread.quit()
            thread.wait(2000)
        self._router_thread = None
        self._router_worker = None
        # A successful sign-in changes what the next scan can name.
        if message.startswith("Signed in") and self.scanner is not None:
            self.scanner.scan_now()

    def _probe_filters(self) -> None:
        """Look for the router page that blocks a device, on its own thread."""
        self._save_router()
        self.router_filters.setEnabled(False)
        self.router_status.setText("Looking through the router's pages…")
        self._filter_thread = QThread(self)
        self._filter_worker = RouterFilterProbe(self.store)
        self._filter_worker.moveToThread(self._filter_thread)
        self._filter_thread.started.connect(self._filter_worker.run)
        self._filter_worker.done.connect(self._filters_probed)
        self._filter_thread.start()

    def _filters_probed(self, message: str) -> None:
        self.router_status.setText(message)
        self.router_filters.setEnabled(True)
        thread = getattr(self, "_filter_thread", None)
        if thread is not None:
            thread.quit()
            thread.wait(2000)
        self._filter_thread = None
        self._filter_worker = None

    def _interval_changed(self, seconds: int) -> None:
        if self._loading or self.scanner is None:
            return
        self.scanner.set_interval(int(seconds))

    def _scan_now(self) -> None:
        if self.scanner is not None:
            self.scanner.scan_now()

    def _rename(self) -> None:
        device = self._selected()
        if device is None or self.scanner is None:
            return
        name, accepted = QInputDialog.getText(
            self,
            "Name this device",
            f"{device.ip or device.mac}\n\nWhat do you call it?",
            text=device.custom_name or device.display_name,
        )
        if accepted:
            self.scanner.rename_device(device.mac, name)

    def _forget(self) -> None:
        device = self._selected()
        if device is None or self.scanner is None:
            return
        # Forgetting only clears the history; a device still here is found again
        # by the next scan, which is worth saying so it is not a surprise.
        self.scanner.forget(device.mac)



class PhonePage(QWidget):
    """The phone, and the few things worth doing to it from a desk.

    Answering is the point. Everything else here exists to make answering
    possible: the pairing, the connection, and enough of the phone's state to
    tell whether it is listening at all.
    """

    updated = Signal()

    def __init__(self, store: ConfigStore, watcher=None, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self.watcher = watcher
        self._busy = False
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 26, 30, 26)
        root.setSpacing(16)
        root.addWidget(page_header(
            "Phone",
            "Answer, hang up and dial an Android phone from here.",
            "PHONE",
        ))

        toolbar_card = Card(variant="InsetCard")
        toolbar = QHBoxLayout(toolbar_card)
        toolbar.setContentsMargins(14, 12, 14, 12)
        toolbar.setSpacing(10)
        self.enabled_switch = ToggleSwitch()
        self.enabled_switch.toggled.connect(self._set_enabled)
        self.status_label = QLabel("")
        self.status_label.setObjectName("Muted")
        self.status_label.setWordWrap(True)
        self.answer_button = QPushButton("Answer")
        self.answer_button.setProperty("primary", True)
        self.answer_button.clicked.connect(self._answer)
        self.hangup_button = QPushButton("Hang up")
        self.hangup_button.setProperty("danger", True)
        self.hangup_button.clicked.connect(self._hang_up)
        toolbar.addWidget(self.enabled_switch)
        toolbar.addWidget(self.status_label, 1)
        toolbar.addWidget(self.answer_button)
        toolbar.addWidget(self.hangup_button)
        root.addWidget(toolbar_card)

        # Pairing is a one-off and the port it uses dies with the screen that
        # shows it, so the two fields sit together and are cleared after use.
        pair_card = Card(variant="InsetCard")
        pair = QHBoxLayout(pair_card)
        pair.setContentsMargins(14, 12, 14, 12)
        pair.setSpacing(10)
        self.pair_address = QLineEdit()
        self.pair_address.setPlaceholderText("Pairing address, e.g. 192.168.18.8:42299")
        self.pair_code = QLineEdit()
        self.pair_code.setPlaceholderText("Six-digit code")
        self.pair_code.setMaximumWidth(130)
        self.pair_button = QPushButton("Pair")
        self.pair_button.clicked.connect(self._pair)
        pair.addWidget(QLabel("Pair a phone"))
        pair.addWidget(self.pair_address, 1)
        pair.addWidget(self.pair_code)
        pair.addWidget(self.pair_button)
        root.addWidget(pair_card)

        connect_card = Card(variant="InsetCard")
        connect = QHBoxLayout(connect_card)
        connect.setContentsMargins(14, 12, 14, 12)
        connect.setSpacing(10)
        self.address = QLineEdit()
        self.address.setPlaceholderText("Phone address, e.g. 192.168.18.8:45217")
        self.address.editingFinished.connect(self._save_address)
        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self._connect)
        self.dial_box = QLineEdit()
        self.dial_box.setPlaceholderText("Number to call")
        self.dial_box.setMaximumWidth(180)
        self.dial_button = QPushButton("Call")
        self.dial_button.clicked.connect(self._dial)
        connect.addWidget(QLabel("Connection"))
        connect.addWidget(self.address, 1)
        connect.addWidget(self.connect_button)
        connect.addWidget(self.dial_box)
        connect.addWidget(self.dial_button)
        root.addWidget(connect_card)

        self.detail = QLabel("")
        self.detail.setObjectName("Muted")
        self.detail.setWordWrap(True)
        root.addWidget(self.detail)
        root.addStretch()

        self._loading = False
        if watcher is not None:
            watcher.call_changed.connect(self._call_changed)
            watcher.state_changed.connect(self._state_changed)
        self.refresh()

    # -- reading the page ------------------------------------------------

    def refresh(self) -> None:
        config = self.store.load()
        self._loading = True
        try:
            self.enabled_switch.setChecked(bool(config.get("phone_enabled", False)))
            self.address.setText(str(config.get("phone_address", "")))
        finally:
            self._loading = False
        self._show_state()

    def _show_state(self) -> None:
        watcher = self.watcher
        if watcher is None:
            self.status_label.setText("No phone watcher is running.")
            self._sync_actions()
            return
        if not watcher.ready:
            self.status_label.setText(
                "adb is not installed. Mind needs the Android platform tools to talk to a phone."
            )
        elif not bool(self.store.load().get("phone_enabled", False)):
            self.status_label.setText("Watching is off.")
        elif watcher.trouble:
            self.status_label.setText(watcher.trouble)
        else:
            call = watcher.call
            if call.ringing:
                who = call.number or "unknown number"
                self.status_label.setText(f"Ringing — {who}")
            elif call.busy:
                self.status_label.setText("In a call")
            else:
                self.status_label.setText("Idle")
        name = watcher.model or "No phone"
        charge = f"{watcher.battery}%" if watcher.battery >= 0 else "battery unknown"
        self.detail.setText(f"{name} · {charge}")
        self._sync_actions()

    def _sync_actions(self) -> None:
        watcher = self.watcher
        ringing = bool(watcher and watcher.call.ringing)
        busy = bool(watcher and watcher.call.busy)
        self.answer_button.setEnabled(ringing and not self._busy)
        self.hangup_button.setEnabled(busy and not self._busy)
        self.dial_button.setEnabled(not busy and not self._busy)

    def _call_changed(self, _call) -> None:
        self._show_state()

    def _state_changed(self, _model: str, _battery: int, _trouble: str) -> None:
        self._show_state()

    # -- doing things ----------------------------------------------------

    def _set_enabled(self, on: bool) -> None:
        if self._loading:
            return
        config = self.store.load()
        config["phone_enabled"] = on
        self.store.save(config)
        if self.watcher is not None:
            self.watcher.start() if on else self.watcher.stop()
        self.refresh()
        self.updated.emit()

    def _save_address(self) -> None:
        if self._loading:
            return
        config = self.store.load()
        config["phone_address"] = self.address.text().strip()
        self.store.save(config)

    def _act(self, what: str, action) -> None:
        """Run one phone command, and say what came back.

        These are quick - a keypress and an answer - so they run here rather
        than on a thread of their own; a phone that has gone away answers with
        a sentence rather than a wait, because adb gives up on its own.
        """
        self._busy = True
        self._sync_actions()
        try:
            action()
        except AdbError as exc:
            self.status_label.setText(str(exc))
        except Exception as exc:
            self.status_label.setText(f"{what} failed: {exc}")
        finally:
            self._busy = False
        if self.watcher is not None:
            self.watcher.poll_now()
        self._sync_actions()

    def _answer(self) -> None:
        self._act("Answering", lambda: phone_for(self.store).answer())

    def _hang_up(self) -> None:
        self._act("Hanging up", lambda: phone_for(self.store).hang_up())

    def _dial(self) -> None:
        number = self.dial_box.text().strip()
        if not number:
            return
        confirmed = QMessageBox.question(
            self,
            "Place a call",
            f"Call {number} from the phone?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirmed != QMessageBox.Yes:
            return
        self._act("Dialling", lambda: phone_for(self.store).dial(number))

    def _pair(self) -> None:
        address = self.pair_address.text().strip()
        code = self.pair_code.text().strip()

        def pair_and_remember():
            phone = Phone()
            phone.pair(address, code)
            # The pairing port is not the one to talk on afterwards, and the
            # phone shows the other on the same screen.
            self.status_label.setText("Paired. Now connect on the phone's own address.")
            self.pair_code.clear()

        self._act("Pairing", pair_and_remember)

    def _connect(self) -> None:
        address = self.address.text().strip()

        def connect_and_remember():
            phone = Phone()
            phone.connect(address)
            config = self.store.load()
            config["phone_address"] = address
            found = [device for device in phone.devices() if device.ready]
            if found:
                config["phone_serial"] = found[0].serial
            self.store.save(config)
            self.status_label.setText(f"Connected to {address}.")

        self._act("Connecting", connect_and_remember)


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
        self._tab_layouts: list[QVBoxLayout] = []
        # True while refresh() is putting saved values into the controls. Their
        # change signals fire all the same, and without this each one would write
        # the config straight back - and the first launch would save defaults over
        # settings it had not finished loading.
        self._loading = True
        # Typing and dragging a slider produce a change per keystroke and per
        # pixel, so writes are coalesced rather than made on each one.
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(450)
        self._save_timer.timeout.connect(self._persist)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(18)
        root.addWidget(page_header(
            "Preferences",
            "Everything is saved the moment you change it.",
            "",
        ))

        # Tabs rather than one long column. There are thirty-odd settings, and a
        # third of them are Telegram's; on one page the thing being looked for is
        # always somewhere off screen.
        self.tabs = QTabWidget()
        self.tabs.setObjectName("SettingsTabs")
        root.addWidget(self.tabs, 1)

        writing_layout = self._tab("Writing")
        assist_layout = self._tab("Assistance")
        shortcuts_layout = self._tab("Shortcuts")
        telegram_layout = self._tab("Telegram")
        appearance_layout = self._tab("Appearance")
        system_layout = self._tab("System")
        # Kept so the rows below can be read in the order they are built.
        # Every row is assigned to a tab below; nothing is left ungrouped.
        self.prefix = self._setting_row(
            writing_layout,
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
            writing_layout,
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
            writing_layout,
            "Keyboard delay",
            "Increase this if replacement glitches on a slow computer.",
            delay_holder,
            "◴",
        )
        self.autocorrect = ToggleSwitch()
        self._setting_row(
            writing_layout,
            "Realtime spelling",
            "Corrects clear English misspellings locally after Space. Press Backspace immediately to undo.",
            self.autocorrect,
            "✓",
        )
        self.word_definitions = ToggleSwitch()
        self._setting_row(
            assist_layout,
            "Word definitions",
            "Shows an English definition above single-word selections in other apps. Skipped while you are editing a text field. Only the selected word is looked up online.",
            self.word_definitions,
            "Aa",
        )
        self.quick_paste = ToggleSwitch()
        self._setting_row(
            assist_layout,
            "Quick paste tooltip",
            "Shows a floating Paste button when clicking into an editable field after copying text.",
            self.quick_paste,
            "📋",
        )
        self.converter_tooltips = ToggleSwitch()
        self._setting_row(
            assist_layout,
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
            writing_layout,
            "Correction strength",
            "Balanced catches everyday typing slips. Strong also tries harder two-letter errors.",
            self.autocorrect_strength,
            "≋",
        )
        self.autocorrect.toggled.connect(self.autocorrect_strength.setEnabled)
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
            system_layout,
            "Start Mind with Windows",
            "Automatically launch in the system tray when you sign in.",
            self.startup,
            "⊞",
        )
        self.mind_palette = ToggleSwitch()
        self._setting_row(
            shortcuts_layout,
            "Mind Palette",
            "Transform selected text with a shortcut or optional automatic popup.",
            self.mind_palette,
            "✦",
        )
        self.palette_auto_show = ToggleSwitch()
        self._setting_row(
            shortcuts_layout,
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
            shortcuts_layout,
            "Palette shortcut",
            "Mind automatically uses the next option if another app owns this shortcut.",
            self.palette_shortcut,
            "⌘",
        )
        self.mind_palette.toggled.connect(self.palette_shortcut.setEnabled)
        self.mind_palette.toggled.connect(self.palette_auto_show.setEnabled)
        self.screen_snip = ToggleSwitch()
        self._setting_row(
            shortcuts_layout,
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
            shortcuts_layout,
            "Snip shortcut",
            "Shortcut to start on-screen rectangular snipping.",
            self.snip_shortcut,
            "✀",
        )
        self.screen_snip.toggled.connect(self.snip_shortcut.setEnabled)
        self.clipboard_history = ToggleSwitch()
        self._setting_row(
            shortcuts_layout,
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
            shortcuts_layout,
            "Clipboard history shortcut",
            "Shortcut to open the floating clipboard history search modal.",
            self.clipboard_shortcut,
            "📋",
        )
        self.clipboard_history.toggled.connect(self.clipboard_shortcut.setEnabled)

        self.telegram_enabled = ToggleSwitch()
        self._setting_row(
            telegram_layout,
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
            telegram_layout,
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
            telegram_layout,
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
            telegram_layout,
            "Default command",
            "Applied to plain messages sent without a command. Leave empty to require "
            "a command every time.",
            self.telegram_default,
            "✈",
        )
        self.telegram_notifications = ToggleSwitch()
        self._setting_row(
            telegram_layout,
            "Telegram notifications",
            "Send Mind alerts, such as an available update, to your allowed chats.",
            self.telegram_notifications,
            "✈",
        )
        self.telegram_files = ToggleSwitch()
        self._setting_row(
            telegram_layout,
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
            telegram_layout,
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
            telegram_layout,
            "Save files to",
            "Where files sent to the bot are stored. Existing files are never overwritten.",
            self.telegram_inbox,
            "✈",
        )
        self.watchers_enabled = ToggleSwitch()
        self._setting_row(
            telegram_layout,
            "PC watchers",
            "Sends an alert when the battery runs low, a disk fills up, memory runs "
            "high, the PC sits idle, or a file lands in a watched folder. Add them on "
            "the Notifications page.",
            self.watchers_enabled,
            "✈",
        )

        self.telegram_print = ToggleSwitch()
        self._setting_row(
            telegram_layout,
            "Telegram printing",
            "Adds a Print button to files sent to the bot, which asks for the printer, "
            "the paper size, and colour or black and white. Printing spends paper and "
            "ink while you may not be in the room, so it is off until you turn it on.",
            self.telegram_print,
            "✈",
        )

        self.telegram_control = ToggleSwitch()
        self._setting_row(
            telegram_layout,
            "Telegram PC controls",
            "Check battery, memory and disk space, take a screenshot, lock or sleep "
            "this PC, and use the media keys from your phone.",
            self.telegram_control,
            "✈",
        )
        self.telegram_power = ToggleSwitch()
        self._setting_row(
            telegram_layout,
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
            telegram_layout,
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
            assist_layout,
            "Secret & Privacy Shield",
            "Alert and offer 1-click redaction when copying API keys, tokens, or credentials.",
            self.secret_shield,
            "🛡️",
        )
        self.url_peek = ToggleSwitch()
        self._setting_row(
            assist_layout,
            "URL & Media Quick Peek",
            "Show floating clean link, summary, and preview when selecting or copying links.",
            self.url_peek,
            "🔗",
        )
        self.ghost_text = ToggleSwitch()
        self._setting_row(
            assist_layout,
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
            assist_layout,
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
        updates_layout = system_layout
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

        about = QLabel(f"Mind {__version__} · Derived from SwiftSlate Desktop under the MIT License")
        about.setObjectName("Muted")
        about.setWordWrap(True)
        system_layout.addWidget(about)

        # No Save button: every control writes as it changes. What replaces it is
        # a line that says so, because a setting that saves silently and a setting
        # that quietly failed look identical.
        status_row = QHBoxLayout()
        self.save_status = QLabel("")
        self.save_status.setObjectName("Muted")
        status_row.addWidget(self.save_status, 1)
        root.addLayout(status_row)
        # Rows sit at the top of their tab rather than spreading down it.
        for layout in self._tab_layouts:
            layout.addStretch()
        self.refresh()
        self._connect_auto_save()

    def _connect_auto_save(self) -> None:
        """Have every control write the settings as it changes.

        Connected after the first refresh, so loading saved values cannot look
        like the user changing them. Each kind of control has its own signal, and
        they all go through the same debounced write.
        """
        for toggle in (
            self.autocorrect,
            self.word_definitions,
            self.quick_paste,
            self.converter_tooltips,
            self.startup,
            self.mind_palette,
            self.palette_auto_show,
            self.screen_snip,
            self.clipboard_history,
            self.telegram_enabled,
            self.telegram_notifications,
            self.telegram_files,
            self.telegram_print,
            self.watchers_enabled,
            self.telegram_control,
            self.telegram_power,
            self.telegram_send_menu,
            self.secret_shield,
            self.url_peek,
            self.ghost_text,
        ):
            # A toggle is a deliberate act with nothing to finish typing, so it
            # writes immediately rather than through the timer.
            toggle.toggled.connect(self._changed_now)
        # Its own signal, because a segmented control is buttons rather than a
        # combo box.
        self.spinner.changed.connect(self._changed_now)
        for combo in (
            self.autocorrect_strength,
            self.theme,
            self.accent,
            self.palette_shortcut,
            self.snip_shortcut,
            self.clipboard_shortcut,
            self.ghost_shortcut,
        ):
            combo.currentIndexChanged.connect(self._changed_now)
        for field in (
            self.prefix,
            self.telegram_token,
            self.telegram_chat_ids,
            self.telegram_default,
            self.telegram_files_root,
            self.telegram_inbox,
        ):
            field.textEdited.connect(self._changed_soon)
            # Leaving the field commits it at once, so a half-typed value is not
            # left waiting on a timer.
            field.editingFinished.connect(self._changed_now)
        self.delay.valueChanged.connect(self._changed_soon)

    def _changed_now(self) -> None:
        if self._loading:
            return
        self._save_timer.stop()
        self._persist()

    def _changed_soon(self) -> None:
        if self._loading:
            return
        self.save_status.setText("Saving…")
        self._save_timer.start()

    def _tab(self, title: str) -> QVBoxLayout:
        """Add a tab and return the layout its rows go into.

        Each one scrolls on its own, so a long tab cannot push the others out of
        reach on a small window.
        """
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 4, 2, 4)
        layout.setSpacing(7)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        self.tabs.addTab(scroll, title)
        # Rows sit at the top; the stretch is added once the tab is built.
        self._tab_layouts.append(layout)
        return layout

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
        # Putting saved values into the controls fires their change signals, which
        # would write straight back - and on first launch would save defaults over
        # settings that had not finished loading.
        self._loading = True
        try:
            self._load_into_controls()
        finally:
            self._loading = False

    def _load_into_controls(self) -> None:
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
        self.watchers_enabled.setChecked(bool(config.get("watchers_enabled", False)))
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
        """Kept as the name other code calls; the writing is in _persist."""
        self._persist()

    def _persist(self) -> None:
        """Write the settings as they stand, saying so on the page.

        Nothing here opens a dialog. This runs on a toggle, so a modal would
        interrupt someone halfway through changing three things - every message
        that used to be a dialog is now a line under the controls, which also
        means it stays readable instead of being dismissed and forgotten.
        """
        self._save_timer.stop()
        prefix = self.prefix.text().strip()
        if not prefix or any(character.isspace() for character in prefix):
            # Refused rather than saved: an empty or spaced prefix would stop
            # every command from triggering.
            self._report("Not saved: the command prefix cannot be empty or contain spaces.")
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
                "watchers_enabled": self.watchers_enabled.isChecked(),
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
        want_shell_menu = self.telegram_enabled.isChecked() and self.telegram_send_menu.isChecked()
        try:
            self.store.save(config)
            set_start_with_windows(self.startup.isChecked(), launcher_path())
            # Re-applied on every save so the entry follows the executable if the
            # first-run installer has since moved it.
            shell_menu_added = shell_menu_apply(want_shell_menu)
        except OSError as exc:
            # The one case still worth a dialog: nothing was written, and the
            # user's changes are about to be lost without them knowing.
            QMessageBox.critical(self, "Could not save settings", str(exc))
            self._report("Not saved.")
            return

        notes: list[str] = []
        if config.get("telegram_enabled") and not config.get("telegram_allowed_chat_ids"):
            # Anyone can message a bot, so the bridge refuses to connect without
            # an allowlist. Said here because switching the bridge on is exactly
            # when the missing piece matters.
            notes.append("Telegram needs at least one allowed chat ID before it will connect.")
        if want_shell_menu and not shell_menu_added:
            notes.append(f"'Send to Telegram' was not added. {shell_menu_describe()}")
        self._refresh_dependent_controls()
        self.updated.emit(
            str(self.theme.currentData()),
            self.mind_palette.isChecked(),
            str(self.palette_shortcut.currentData()),
            str(self.accent.currentData()),
        )
        self._report("Saved." if not notes else "Saved. " + " ".join(notes))

    def _report(self, message: str) -> None:
        self.save_status.setText(message)

    def _refresh_dependent_controls(self) -> None:
        """Enable and disable the controls that depend on another setting.

        Kept apart from refresh() so it can run after every write without
        replacing what is in the fields while someone is typing in them.
        """
        telegram_on = self.telegram_enabled.isChecked()
        files_on = self.telegram_files.isChecked()
        for widget in (
            self.telegram_token,
            self.telegram_chat_ids,
            self.telegram_default,
            self.telegram_notifications,
            self.telegram_files,
            self.telegram_control,
            self.telegram_send_menu,
        ):
            widget.setEnabled(telegram_on)
        for widget in (self.telegram_files_root, self.telegram_inbox):
            widget.setEnabled(telegram_on and files_on)
        self.telegram_print.setEnabled(telegram_on and files_on)
        self.watchers_enabled.setEnabled(telegram_on)
        self.telegram_power.setEnabled(telegram_on and self.telegram_control.isChecked())
        self.palette_auto_show.setEnabled(self.mind_palette.isChecked())
        self.palette_shortcut.setEnabled(self.mind_palette.isChecked())
        self.snip_shortcut.setEnabled(self.screen_snip.isChecked())
        self.clipboard_shortcut.setEnabled(self.clipboard_history.isChecked())
        self.ghost_shortcut.setEnabled(self.ghost_text.isChecked())
        self.autocorrect_strength.setEnabled(self.autocorrect.isChecked())

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
        # Owned by the window rather than the page, so scanning carries on while
        # the page is closed and there is only ever one scan on the machine.
        self.scanner = NetworkScanner(self.store, self)
        self.scanner.log.connect(self._log)
        self.scanner.arrived.connect(self._on_devices_arrived)
        self.phone_watcher = PhoneWatcher(self.store, self)
        self.phone_watcher.log.connect(self._log)
        self.phone_watcher.call_changed.connect(self._on_call_changed)
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
        QTimer.singleShot(0, self.configure_network_scan)
        QTimer.singleShot(0, self.configure_phone_watch)
        QTimer.singleShot(0, self._register_notifications)
        QTimer.singleShot(300, self._do_pending_action)
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
            ("◔", "Notifications", "watchers alerts battery disk memory idle folder telegram"),
            ("◈", "Wi-Fi devices", "network devices wifi lan ip mac vendor who is connected"),
            ("☎", "Phone", "android adb call answer hang up dial ring battery pair wireless debugging"),
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
        self.notifications = NotificationsPage(self.store)
        self.network = NetworkDevicesPage(self.store, self.scanner)
        self.phone = PhonePage(self.store, self.phone_watcher)
        self.settings = SettingsPage(self.store)
        self.diagnostics = DiagnosticsPage(self.store.root)
        for page in [
            self.dashboard,
            self.providers,
            self.commands,
            self.notifications,
            self.network,
            self.phone,
            self.settings,
            self.diagnostics,
        ]:
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
        self.notifications.updated.connect(self._config_updated)
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
        # By name rather than by number. The numbers had already drifted -
        # opening Notifications refreshed Settings - and inserting a page in
        # the middle would move every one below it again.
        pages = [
            self.dashboard,
            self.providers,
            self.commands,
            self.notifications,
            self.network,
            self.phone,
            self.settings,
            self.diagnostics,
        ]
        if 0 <= index < len(pages):
            refresh = getattr(pages[index], "refresh", None)
            if callable(refresh):
                refresh()

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
        # The watchers switch appears on two pages; neither may show a stale one.
        self.notifications.refresh()
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
        screens = QApplication.screens()
        if not screens:
            self.telegram.send_text(target, "No screen is available to capture.")
            return
        # Every monitor, not just the primary one. Asking a PC with two screens
        # for "the screen" and being shown one of them, with no way to see the
        # other, is the wrong answer to the question.
        sent_any = False
        for index, screen in enumerate(screens):
            shot = screen.grabWindow(0)
            if shot.isNull():
                continue
            destination = (
                Path(tempfile.gettempdir()) / f"mind-screen-{uuid.uuid4().hex[:10]}.png"
            )
            try:
                if not shot.save(str(destination), "PNG"):
                    continue
                size = shot.size()
                caption = screen.name() or f"Screen {index + 1}"
                if len(screens) > 1:
                    caption = f"{caption}  ({index + 1} of {len(screens)})"
                # As a photo: a screenshot asked for from a phone is meant to be
                # looked at, not downloaded first. Each monitor is its own panel,
                # so asking again replaces both pictures rather than stacking
                # four in the chat.
                self.telegram.send_image(
                    target,
                    destination,
                    caption=f"{caption}  ·  {size.width()}×{size.height()}",
                    panel=f"{PANEL_SCREEN}:{index}",
                )
                sent_any = True
            finally:
                # A screenshot can hold anything that was on screen; do not leave
                # it sitting in temp once it has been sent.
                try:
                    destination.unlink()
                except OSError:
                    pass
        if not sent_any:
            self.telegram.send_text(target, "Windows would not let Mind capture the screen.")

    def _register_notifications(self) -> None:
        """Claim the name Windows shows notifications under, and the protocol.

        Both live in the user's own part of the registry and are written every
        launch rather than once, because Mind moves when it is installed and a
        protocol pointing at a copy that is no longer there is worse than none.
        """
        import sys

        target = sys.executable
        if target.lower().endswith("python.exe") or target.lower().endswith("pythonw.exe"):
            # Running from source: point at whatever launched this, so a button
            # press reaches something that exists.
            target = f'{target}" "{Path(sys.argv[0]).resolve()}'
        register_toasts(target)

    def configure_phone_watch(self) -> None:
        """Start or stop watching the phone to match the saved setting."""
        if self._quitting:
            self.phone_watcher.stop()
            return
        wanted = bool(self.store.load().get("phone_enabled", False))
        if wanted and not self.phone_watcher.is_running:
            self.phone_watcher.start()
        elif not wanted and self.phone_watcher.is_running:
            self.phone_watcher.stop()

    def _do_pending_action(self) -> None:
        """Do whatever the notification button asked for.

        Read from the file rather than from the message, because a window
        message carries two numbers and an action needs a word. It is cleared
        as it is read, so a second press cannot act twice on a call that has
        already been dealt with.
        """
        action = take_action()
        if not action:
            return
        if action == "call/show":
            self.show_window()
            return
        phone = phone_for(self.store)
        try:
            if action == "call/answer":
                phone.answer()
                self._log("Answered the call from a notification")
            elif action == "call/reject":
                phone.hang_up()
                self._log("Rejected the call from a notification")
        except AdbError as exc:
            self._log(f"The phone could not be reached: {exc}")
        except Exception as exc:
            self._log(f"The call action failed: {exc}")
        dismiss_call()
        self.phone_watcher.poll_now()

    def _on_call_changed(self, call) -> None:
        """Say that the phone is ringing, wherever the user is.

        Only the arrival is announced. A call being answered or ending is
        something the person already knows about, having done it.
        """
        if not call.ringing:
            # The call is over, one way or another, and a notification about a
            # phone that has stopped ringing is only in the way.
            dismiss_call()
            return
        show_call(call.number, self.phone_watcher.model)
        self.notify_telegram(
            call_alert_text(call.number, self.phone_watcher.model),
            self.telegram.call_keyboard(),
        )

    def configure_network_scan(self) -> None:
        """Start or stop scanning to match the saved setting."""
        if self._quitting:
            self.scanner.stop()
            return
        wanted = bool(self.store.load().get("network_scan_enabled", False))
        if wanted and not self.scanner.is_running:
            self.scanner.start()
        elif not wanted and self.scanner.is_running:
            self.scanner.stop()

    def _on_devices_arrived(self, devices: list) -> None:
        """Tell Telegram about a device that has just joined.

        Sent as its own message per device rather than a digest: a stranger on
        the network is the one alert here worth arriving on its own.
        """
        for device in devices[:5]:
            where = f" at {device.ip}" if device.ip else ""
            self.notify_telegram(
                f"📶  New on the network: {device.display_name}{where}\n{device.mac}",
                self.telegram.device_alert_keyboard(device.mac, device.display_name),
            )

    def notify_telegram(self, message: str, reply_markup: dict | None = None) -> None:
        """Push an alert to every allowed chat, when the user asked for that."""
        config = self.store.load()
        if not config.get("telegram_notifications", False) or not self.telegram.is_running:
            return
        for chat_id in parse_allowed_chat_ids(config.get("telegram_allowed_chat_ids")):
            self.telegram.send_text(chat_id, message, reply_markup)

    def _settings_updated(self, theme: str, palette_enabled: bool, shortcut: str, accent: str) -> None:
        self.apply_theme(theme, accent)
        self.notifications.refresh()
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
            # A second launch asking for the window. It arrives even while Mind
            # is hidden in the tray, which is the whole point: the window is
            # shown here, through Qt, rather than by the other process.
            if native_message.message and native_message.message == show_message_id():
                self.show_window()
                return True, 0
            # A button on a notification was pressed, in another process.
            if native_message.message and native_message.message == action_message_id():
                self._do_pending_action()
                return True, 0
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

