from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .config_store import ConfigStore, DEFAULT_CONFIG
from .paths import launcher_path
from .startup import set_start_with_windows
from .theme import app_icon
from .ui_components import Card, ProviderForm


class SetupWizard(QDialog):
    def __init__(self, store: ConfigStore, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Set up Mind")
        self.setMinimumSize(960, 700)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(48, 36, 48, 34)
        root.setSpacing(24)

        top = QHBoxLayout()
        brand = QLabel()
        brand.setObjectName("BrandLogo")
        brand.setFixedSize(50, 50)
        brand.setAlignment(Qt.AlignCenter)
        brand.setPixmap(app_icon(50).pixmap(46, 46))
        brand_copy = QVBoxLayout()
        brand_copy.setSpacing(0)
        brand_name = QLabel("Mind")
        brand_name.setObjectName("Brand")
        brand_caption = QLabel("PERSONAL WRITING SYSTEM")
        brand_caption.setObjectName("BrandCaption")
        brand_copy.addWidget(brand_name)
        brand_copy.addWidget(brand_caption)
        top.addWidget(brand)
        top.addSpacing(10)
        top.addLayout(brand_copy)
        top.addStretch()
        self.progress = QLabel("Step 1 of 4")
        self.progress.setObjectName("SoftBadge")
        top.addWidget(self.progress)
        root.addLayout(top)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._welcome_page())
        self.stack.addWidget(self._provider_page())
        self.stack.addWidget(self._behavior_page())
        self.stack.addWidget(self._ready_page())
        self.stack.currentChanged.connect(self._page_changed)
        root.addWidget(self.stack, 1)

        navigation = QHBoxLayout()
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(self._back)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        self.next_button = QPushButton("Continue")
        self.next_button.setProperty("primary", True)
        self.next_button.clicked.connect(self._next)
        navigation.addWidget(self.cancel_button)
        navigation.addStretch()
        navigation.addWidget(self.back_button)
        navigation.addWidget(self.next_button)
        root.addLayout(navigation)
        self._page_changed(0)

    def _welcome_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 20, 18, 18)
        layout.setSpacing(16)
        layout.addStretch()
        eyebrow = QLabel("WELCOME TO YOUR NEW WRITING FLOW")
        eyebrow.setObjectName("PageEyebrow")
        eyebrow.setAlignment(Qt.AlignCenter)
        layout.addWidget(eyebrow)
        title = QLabel("Your writing, upgraded everywhere.")
        title.setObjectName("PageTitle")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        description = QLabel(
            "Mind sits quietly in Windows and turns short commands into polished writing—"
            "without making you switch apps or break focus."
        )
        description.setObjectName("Muted")
        description.setAlignment(Qt.AlignCenter)
        description.setWordWrap(True)
        description.setMaximumWidth(620)
        holder = QHBoxLayout()
        holder.addStretch()
        holder.addWidget(description)
        holder.addStretch()
        layout.addLayout(holder)

        self.import_existing = QCheckBox("Import my existing SwiftSlate provider and commands")
        self.import_existing.setChecked(self.store.legacy_available)
        self.import_existing.setVisible(self.store.legacy_available)
        import_holder = QHBoxLayout()
        import_holder.addStretch()
        import_holder.addWidget(self.import_existing)
        import_holder.addStretch()
        layout.addLayout(import_holder)

        features = QHBoxLayout()
        features.setSpacing(14)
        for icon, title_text, body in (
            ("⌁", "Works everywhere", "Use Mind in mail, browsers, Office, chat, and text editors."),
            ("▣", "Private by design", "Keys stay protected by Windows and transformed text is not archived."),
            ("⌘", "Built around you", "Create commands, tune behavior, and choose cloud or local AI."),
        ):
            feature = Card(variant="InsetCard")
            feature_layout = QVBoxLayout(feature)
            feature_layout.setContentsMargins(18, 16, 18, 17)
            feature_layout.setSpacing(7)
            mark = QLabel(icon)
            mark.setObjectName("StatIcon")
            mark.setFixedSize(36, 36)
            mark.setAlignment(Qt.AlignCenter)
            feature_title = QLabel(title_text)
            feature_title.setObjectName("SectionTitle")
            feature_body = QLabel(body)
            feature_body.setObjectName("Muted")
            feature_body.setWordWrap(True)
            feature_layout.addWidget(mark)
            feature_layout.addWidget(feature_title)
            feature_layout.addWidget(feature_body)
            feature_layout.addStretch()
            features.addWidget(feature, 1)
        layout.addLayout(features)
        layout.addStretch()
        return page

    def _provider_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(18)
        eyebrow = QLabel("CHOOSE YOUR AI")
        eyebrow.setObjectName("PageEyebrow")
        title = QLabel("Connect your writing engine")
        title.setObjectName("PageTitle")
        subtitle = QLabel("Use a cloud provider or keep everything local with Ollama or LM Studio.")
        subtitle.setObjectName("Muted")
        layout.addWidget(eyebrow)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        card = Card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 24, 24, 24)
        self.provider_form = ProviderForm(self.store, show_actions=False)
        self.provider_form.load_config(DEFAULT_CONFIG)
        card_layout.addWidget(self.provider_form)
        layout.addWidget(card)
        layout.addStretch()
        return page

    def _behavior_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(18)
        eyebrow = QLabel("PERSONALIZE")
        eyebrow.setObjectName("PageEyebrow")
        title = QLabel("Make Mind feel like yours")
        title.setObjectName("PageTitle")
        subtitle = QLabel("These choices can be changed later from Settings.")
        subtitle.setObjectName("Muted")
        layout.addWidget(eyebrow)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        card = Card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 24, 24, 24)
        card_layout.setSpacing(18)

        prefix_row = QHBoxLayout()
        prefix_text = QVBoxLayout()
        prefix_title = QLabel("Command prefix")
        prefix_title.setObjectName("SectionTitle")
        prefix_hint = QLabel("Mind will listen for commands such as ?fix.")
        prefix_hint.setObjectName("Muted")
        prefix_text.addWidget(prefix_title)
        prefix_text.addWidget(prefix_hint)
        self.prefix = QLineEdit("?")
        self.prefix.setMaxLength(3)
        self.prefix.setFixedWidth(90)
        prefix_row.addLayout(prefix_text)
        prefix_row.addStretch()
        prefix_row.addWidget(self.prefix)
        card_layout.addLayout(prefix_row)

        spinner_row = QHBoxLayout()
        spinner_text = QVBoxLayout()
        spinner_title = QLabel("Processing indicator")
        spinner_title.setObjectName("SectionTitle")
        spinner_hint = QLabel("Show a small indicator while your provider is responding.")
        spinner_hint.setObjectName("Muted")
        spinner_text.addWidget(spinner_title)
        spinner_text.addWidget(spinner_hint)
        self.spinner = QComboBox()
        self.spinner.addItem("Animated", "animated")
        self.spinner.addItem("Static", "static")
        self.spinner.addItem("Off", "off")
        spinner_row.addLayout(spinner_text)
        spinner_row.addStretch()
        spinner_row.addWidget(self.spinner)
        card_layout.addLayout(spinner_row)

        self.start_with_windows = QCheckBox("Open Mind when I sign in to Windows")
        self.start_with_windows.setChecked(False)
        card_layout.addWidget(self.start_with_windows)
        layout.addWidget(card)
        layout.addStretch()
        return page

    def _ready_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 24, 20, 20)
        layout.setSpacing(16)
        layout.addStretch()
        badge = QLabel("SETUP COMPLETE")
        badge.setObjectName("HeroPill")
        badge.setAlignment(Qt.AlignCenter)
        badge.setMaximumWidth(150)
        badge_holder = QHBoxLayout()
        badge_holder.addStretch()
        badge_holder.addWidget(badge)
        badge_holder.addStretch()
        layout.addLayout(badge_holder)
        check = QLabel("✓")
        check.setAlignment(Qt.AlignCenter)
        check.setObjectName("Accent")
        check.setStyleSheet("font-size: 58px; font-weight: 800;")
        layout.addWidget(check)
        title = QLabel("Mind is ready")
        title.setObjectName("PageTitle")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        description = QLabel(
            "Open the dashboard, start the engine, then type a sentence followed by ?fix in Notepad or any other app."
        )
        description.setObjectName("Muted")
        description.setAlignment(Qt.AlignCenter)
        description.setWordWrap(True)
        layout.addWidget(description)
        self.start_engine = QCheckBox("Start the Mind engine when I open the dashboard")
        self.start_engine.setChecked(False)
        start_holder = QHBoxLayout()
        start_holder.addStretch()
        start_holder.addWidget(self.start_engine)
        start_holder.addStretch()
        layout.addLayout(start_holder)
        layout.addStretch()
        return page

    def _next(self) -> None:
        index = self.stack.currentIndex()
        if index == 0 and self.import_existing.isVisible() and self.import_existing.isChecked():
            self.stack.setCurrentIndex(2)
            return
        if index == 1 and not self._validate_provider():
            return
        if index < self.stack.count() - 1:
            self.stack.setCurrentIndex(index + 1)
            return
        self._finish()

    def _back(self) -> None:
        index = self.stack.currentIndex()
        if index == 2 and self.import_existing.isVisible() and self.import_existing.isChecked():
            self.stack.setCurrentIndex(0)
        elif index > 0:
            self.stack.setCurrentIndex(index - 1)

    def _page_changed(self, index: int) -> None:
        self.progress.setText(f"Step {index + 1} of {self.stack.count()}")
        self.back_button.setVisible(index > 0)
        self.next_button.setText("Open workspace  →" if index == self.stack.count() - 1 else "Continue  →")

    def _validate_provider(self) -> bool:
        values = self.provider_form.values()
        if not values["model"]:
            QMessageBox.warning(self, "Model required", "Choose or enter the model you want Mind to use.")
            return False
        if values["provider_profile"] in {"gemini", "groq"} and not self.provider_form.entered_keys():
            QMessageBox.warning(self, "API key required", "Enter at least one API key for this provider.")
            return False
        if values["provider"] == "custom" and not values["endpoint"]:
            QMessageBox.warning(self, "Endpoint required", "Enter the local or custom provider endpoint.")
            return False
        return True

    def _finish(self) -> None:
        try:
            importing = self.import_existing.isVisible() and self.import_existing.isChecked()
            if importing:
                config = self.store.import_legacy()
            else:
                config = dict(DEFAULT_CONFIG)
                config.update(self.provider_form.values())
                config = self.store.set_keys(config, self.provider_form.entered_keys())
                self.store.ensure_commands()
            prefix = self.prefix.text().strip() or "?"
            if any(character.isspace() for character in prefix):
                raise ValueError("The command prefix cannot contain spaces.")
            config["prefix"] = prefix
            config["spinner"] = self.spinner.currentData()
            config["start_with_windows"] = self.start_with_windows.isChecked()
            config["start_engine_on_launch"] = self.start_engine.isChecked()
            config["onboarding_complete"] = True
            self.store.save(config)
            set_start_with_windows(config["start_with_windows"], launcher_path())
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.critical(self, "Setup could not finish", str(exc))
            return
        self.accept()
