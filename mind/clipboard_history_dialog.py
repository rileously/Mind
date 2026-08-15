from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .clipboard_history_store import ClipboardHistoryStore
from .config_store import ConfigStore
from .selection import send_paste_input


def format_relative_time(timestamp: float) -> str:
    diff = max(0, int(time.time() - timestamp))
    if diff < 60:
        return "Just now"
    if diff < 3600:
        return f"{diff // 60}m ago"
    if diff < 86400:
        return f"{diff // 3600}h ago"
    return f"{diff // 86400}d ago"


class ClipboardItemWidget(QFrame):
    paste_requested = Signal(str)
    pin_toggled = Signal(str)
    delete_requested = Signal(str)
    ai_transform_requested = Signal(str, str)  # (text, prompt)

    def __init__(self, entry: dict[str, Any], parent: QWidget | None = None):
        super().__init__(parent)
        self.entry = entry
        self.setObjectName("ClipboardHistoryItem")
        self.setCursor(Qt.PointingHandCursor)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # Header metadata row
        header = QHBoxLayout()
        header.setSpacing(8)

        # Category badge
        cat = self.entry.get("category", "text").capitalize()
        cat_badge = QLabel(cat)
        cat_badge.setObjectName("ClipboardCategoryBadge")
        header.addWidget(cat_badge)

        # Relative time
        time_text = format_relative_time(self.entry.get("timestamp", 0))
        time_label = QLabel(time_text)
        time_label.setObjectName("ClipboardTimeLabel")
        header.addWidget(time_label)

        # Count badge
        count_text = f"{self.entry.get('word_count', 0)} words · {self.entry.get('char_count', 0)} chars"
        count_label = QLabel(count_text)
        count_label.setObjectName("ClipboardCountLabel")
        header.addWidget(count_label)

        header.addStretch()

        # Action buttons
        pinned = self.entry.get("pinned", False)
        self.pin_btn = QPushButton("📌" if pinned else "📍")
        self.pin_btn.setObjectName("ClipboardItemActionPin")
        self.pin_btn.setToolTip("Unpin" if pinned else "Pin to top")
        self.pin_btn.clicked.connect(lambda: self.pin_toggled.emit(self.entry["id"]))
        header.addWidget(self.pin_btn)

        self.ai_btn = QPushButton("✦ AI")
        self.ai_btn.setObjectName("ClipboardItemActionAi")
        self.ai_btn.setToolTip("Transform with AI")
        self.ai_btn.clicked.connect(self._show_ai_menu)
        header.addWidget(self.ai_btn)

        self.delete_btn = QPushButton("🗑️")
        self.delete_btn.setObjectName("ClipboardItemActionDelete")
        self.delete_btn.setToolTip("Delete entry")
        self.delete_btn.clicked.connect(lambda: self.delete_requested.emit(self.entry["id"]))
        header.addWidget(self.delete_btn)

        layout.addLayout(header)

        # Preview text
        text = self.entry.get("text", "")
        clean_text = "\n".join(line for line in text.splitlines() if line.strip())
        preview = clean_text[:280] + ("…" if len(clean_text) > 280 else "")
        self.preview_label = QLabel(preview)
        self.preview_label.setObjectName("ClipboardItemPreview")
        self.preview_label.setWordWrap(True)
        layout.addWidget(self.preview_label)

    def _show_ai_menu(self) -> None:
        menu = QMenu(self)
        actions = [
            ("✦ Summarize", "Summarize clearly and concisely."),
            ("✦ Fix Grammar & Flow", "Fix grammar, spelling, punctuation, and flow."),
            ("✦ Translate to English", "Translate into fluent, natural English."),
            ("✦ Translate to Dhivehi", "Translate into natural, standard Dhivehi."),
            ("✦ Extract Code & Links", "Extract all code snippets and URLs."),
            ("✦ Action Items Checklist", "Extract concrete action items as a markdown checklist."),
        ]
        for title, prompt in actions:
            action = menu.addAction(title)
            action.triggered.connect(lambda _, p=prompt: self.ai_transform_requested.emit(self.entry["text"], p))
        menu.exec(self.ai_btn.mapToGlobal(QPoint(0, self.ai_btn.height())))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            # Check if clicked outside buttons
            self.paste_requested.emit(self.entry["text"])
        super().mousePressEvent(event)


class ClipboardHistoryDialog(QDialog):
    ai_action_requested = Signal(str, str)  # (text, prompt)

    def __init__(self, store: ConfigStore, parent: QWidget | None = None):
        super().__init__(parent, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setObjectName("ClipboardHistoryDialog")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.store = store
        self.history_store = ClipboardHistoryStore(store.root)
        self._current_category = "all"
        self._selected_index = 0
        self._item_widgets: list[ClipboardItemWidget] = []

        self.setMinimumWidth(440)
        self.setMaximumWidth(520)
        self.setFixedHeight(540)

        self._build_ui()

        # Keyboard shortcuts
        QShortcut(QKeySequence(Qt.Key_Escape), self, self.dismiss)
        QShortcut(QKeySequence(Qt.Key_Down), self, self._select_next)
        QShortcut(QKeySequence(Qt.Key_Up), self, self._select_prev)
        QShortcut(QKeySequence(Qt.Key_Return), self, self._paste_selected)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)

        self.shell = QWidget()
        self.shell.setObjectName("ClipboardHistoryShell")
        layout = QVBoxLayout(self.shell)
        layout.setContentsMargins(14, 14, 14, 12)
        layout.setSpacing(10)

        # Header Row
        header = QHBoxLayout()
        icon = QLabel("📋")
        icon.setObjectName("ClipboardHeaderIcon")
        header.addWidget(icon)

        title = QLabel("Clipboard History")
        title.setObjectName("ClipboardHeaderTitle")
        header.addWidget(title)

        header.addStretch()

        self.clear_btn = QPushButton("Clear unpinned")
        self.clear_btn.setObjectName("ClipboardClearButton")
        self.clear_btn.clicked.connect(self._clear_unpinned)
        header.addWidget(self.clear_btn)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("PopupCloseButton")
        close_btn.clicked.connect(self.dismiss)
        header.addWidget(close_btn)

        layout.addLayout(header)

        # Search Bar
        self.search_box = QLineEdit()
        self.search_box.setObjectName("ClipboardSearchInput")
        self.search_box.setPlaceholderText("Search history... (Press Enter to paste)")
        self.search_box.textChanged.connect(lambda: self.refresh_items())
        layout.addWidget(self.search_box)

        # Category Filter Tabs
        cat_bar = QHBoxLayout()
        cat_bar.setSpacing(6)
        self._cat_buttons = {}
        categories = [
            ("all", "All"),
            ("pinned", "📌 Pinned"),
            ("link", "🔗 Links"),
            ("code", "💻 Code"),
            ("text", "📝 Text"),
        ]
        for cat_id, label in categories:
            btn = QPushButton(label)
            btn.setObjectName("ClipboardCategoryFilterTab")
            btn.setCheckable(True)
            btn.setChecked(cat_id == "all")
            btn.clicked.connect(lambda _, c=cat_id: self._set_category(c))
            cat_bar.addWidget(btn)
            self._cat_buttons[cat_id] = btn
        cat_bar.addStretch()
        layout.addLayout(cat_bar)

        # Scrollable items area
        self.scroll = QScrollArea()
        self.scroll.setObjectName("ClipboardHistoryScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(6)
        self.list_layout.addStretch()
        self.scroll.setWidget(self.list_container)

        layout.addWidget(self.scroll, 1)

        # Footer status
        self.status_label = QLabel("Tip: Click or press Enter to paste directly into your app.")
        self.status_label.setObjectName("ClipboardFooterHint")
        layout.addWidget(self.status_label)

        outer.addWidget(self.shell)

    def _set_category(self, cat_id: str) -> None:
        self._current_category = cat_id
        for cid, btn in self._cat_buttons.items():
            btn.setChecked(cid == cat_id)
        self.refresh_items()

    def show_centered_or_cursor(self) -> None:
        self.refresh_items()
        cursor_pos = QCursor.pos()
        screen = QApplication.screenAt(cursor_pos) or QApplication.primaryScreen()
        screen_geo = screen.geometry() if screen else QRect(0, 0, 1920, 1080)

        # Center horizontally, place near top 20%
        x = screen_geo.x() + (screen_geo.width() - self.width()) // 2
        y = screen_geo.y() + max(80, (screen_geo.height() - self.height()) // 3)
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self.search_box.setFocus()
        self.search_box.selectAll()

    def refresh_items(self) -> None:
        query = self.search_box.text()
        entries = self.history_store.get_entries(query=query, category=self._current_category)

        # Clear existing widgets
        for item in self._item_widgets:
            item.setParent(None)
            item.deleteLater()
        self._item_widgets.clear()

        # Remove items from layout before stretch
        while self.list_layout.count() > 1:
            child = self.list_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if not entries:
            empty = QLabel("No clipboard history found.")
            empty.setObjectName("ClipboardEmptyLabel")
            empty.setAlignment(Qt.AlignCenter)
            self.list_layout.insertWidget(0, empty)
            self._selected_index = 0
            return

        for idx, entry in enumerate(entries):
            widget = ClipboardItemWidget(entry, self.list_container)
            widget.paste_requested.connect(self._do_paste)
            widget.pin_toggled.connect(self._toggle_pin)
            widget.delete_requested.connect(self._delete_entry)
            widget.ai_transform_requested.connect(self._on_ai_transform)
            self.list_layout.insertWidget(idx, widget)
            self._item_widgets.append(widget)

        self._selected_index = 0
        self._update_highlight()

    def _select_next(self) -> None:
        if not self._item_widgets:
            return
        self._selected_index = min(self._selected_index + 1, len(self._item_widgets) - 1)
        self._update_highlight()

    def _select_prev(self) -> None:
        if not self._item_widgets:
            return
        self._selected_index = max(self._selected_index - 1, 0)
        self._update_highlight()

    def _update_highlight(self) -> None:
        for idx, widget in enumerate(self._item_widgets):
            widget.setProperty("selected", idx == self._selected_index)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        if 0 <= self._selected_index < len(self._item_widgets):
            self.scroll.ensureWidgetVisible(self._item_widgets[self._selected_index])

    def _paste_selected(self) -> None:
        if 0 <= self._selected_index < len(self._item_widgets):
            text = self._item_widgets[self._selected_index].entry["text"]
            self._do_paste(text)

    def _do_paste(self, text: str) -> None:
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(text)
        self.dismiss()
        # Small delay then send paste keystroke
        send_paste_input()

    def _toggle_pin(self, entry_id: str) -> None:
        self.history_store.toggle_pin(entry_id)
        self.refresh_items()

    def _delete_entry(self, entry_id: str) -> None:
        self.history_store.delete_entry(entry_id)
        self.refresh_items()

    def _clear_unpinned(self) -> None:
        self.history_store.clear_unpinned()
        self.refresh_items()

    def _on_ai_transform(self, text: str, prompt: str) -> None:
        self.dismiss()
        self.ai_action_requested.emit(text, prompt)

    def dismiss(self) -> None:
        self.hide()

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.ActivationChange and not self.isActiveWindow():
            self.dismiss()
        super().changeEvent(event)
