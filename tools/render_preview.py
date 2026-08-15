"""Render Mind screens for local visual QA without changing real user settings."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from mind.config_store import ConfigStore
from mind.ask_ai_popup import AskAiPopup
from mind.clipboard_history_dialog import ClipboardHistoryDialog
from mind.converter_tools import detect_and_convert
from mind.definition_popup import DefinitionPopup
from mind.dictionary import DefinitionResult, DefinitionSense
from mind.ghost_text_overlay import GhostTextOverlay
from mind.main_window import MindWindow
from mind.palette import MindPalette
from mind.quick_paste_popup import QuickPastePopup
from mind.secret_detector import detect_secrets
from mind.secret_shield_card import SecretShieldCard
from mind.selection import SelectionSession
from mind.setup_wizard import SetupWizard
from mind.snip_card import SnipCard
from mind.theme import stylesheet
from mind.ui_components import PaletteCustomizeDialog
from mind.url_peek_card import UrlPeekCard
from mind.url_tools import extract_quick_metadata


def main() -> None:
    app = QApplication([])
    app.setStyleSheet(stylesheet("dark"))
    output = PROJECT_ROOT / "artifacts" / "mind-onboarding.png"
    dashboard_output = PROJECT_ROOT / "artifacts" / "mind-dashboard.png"
    providers_output = PROJECT_ROOT / "artifacts" / "mind-providers.png"
    commands_output = PROJECT_ROOT / "artifacts" / "mind-commands.png"
    settings_output = PROJECT_ROOT / "artifacts" / "mind-settings.png"
    updates_output = PROJECT_ROOT / "artifacts" / "mind-updates.png"
    diagnostics_output = PROJECT_ROOT / "artifacts" / "mind-diagnostics.png"
    palette_output = PROJECT_ROOT / "artifacts" / "mind-palette.png"
    customize_output = PROJECT_ROOT / "artifacts" / "mind-palette-customize.png"
    definition_output = PROJECT_ROOT / "artifacts" / "mind-definition.png"
    quick_paste_output = PROJECT_ROOT / "artifacts" / "mind-quick-paste.png"
    converter_output = PROJECT_ROOT / "artifacts" / "mind-converter.png"
    snip_card_output = PROJECT_ROOT / "artifacts" / "mind-snip-card.png"
    clipboard_output = PROJECT_ROOT / "artifacts" / "mind-clipboard-history.png"
    secret_shield_output = PROJECT_ROOT / "artifacts" / "mind-secret-shield.png"
    url_peek_output = PROJECT_ROOT / "artifacts" / "mind-url-peek.png"
    ghost_text_output = PROJECT_ROOT / "artifacts" / "mind-ghost-text.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        store = ConfigStore(Path(temporary) / "data", PROJECT_ROOT)
        wizard = SetupWizard(store)
        wizard.show()
        app.processEvents()
        wizard.grab().save(str(output))
        wizard.close()
        config = store.load()
        config["onboarding_complete"] = True
        store.save(config)
        store.ensure_commands()
        window = MindWindow(store)
        window.show()
        app.processEvents()
        window.grab().save(str(dashboard_output))
        for index, destination in (
            (1, providers_output),
            (2, commands_output),
            (3, settings_output),
            (4, diagnostics_output),
        ):
            window.select_page(index)
            app.processEvents()
            window.grab().save(str(destination))
        window.select_page(3)
        settings_scroll = window.stack.currentWidget()
        settings_scroll.verticalScrollBar().setValue(settings_scroll.verticalScrollBar().maximum())
        app.processEvents()
        window.grab().save(str(updates_output))
        palette = MindPalette(
            store,
            SelectionSession(1, "hey can you send me the project update tomorrow", None),
        )
        palette.show_near_cursor()
        app.processEvents()
        palette.grab().save(str(palette_output))
        palette.close()
        definition = DefinitionPopup()
        definition.show_result(DefinitionResult(
            word="serendipity",
            pronunciation="ˌserənˈdipitē",
            senses=(
                DefinitionSense(
                    "noun",
                    "The occurrence of events by chance in a happy or beneficial way.",
                ),
                DefinitionSense(
                    "noun",
                    "An unexpected discovery that turns out to be valuable.",
                ),
            ),
            source_name="Datamuse · WordNet & Wiktionary",
            source_url="https://www.datamuse.com/api/",
        ))
        app.processEvents()
        definition.grab().save(str(definition_output))
        definition.close()
        quick_paste = QuickPastePopup()
        quick_paste.show_for_text("https://github.com/rileously/Mind", 1)
        app.processEvents()
        quick_paste.grab().save(str(quick_paste_output))
        quick_paste.close()
        conv_popup = AskAiPopup(store)
        conv_result = detect_and_convert("$49.99")
        if conv_result:
            conv_popup.show_converter_result(conv_result)
        app.processEvents()
        conv_popup.grab().save(str(converter_output))
        conv_popup.close()
        snip_card = SnipCard(store)
        sample_pixmap = QPixmap(360, 180)
        sample_pixmap.fill()
        snip_card.show_for_pixmap(sample_pixmap)
        app.processEvents()
        snip_card.grab().save(str(snip_card_output))
        snip_card.close()
        clip_dialog = ClipboardHistoryDialog(store)
        clip_dialog.history_store.add_entry("https://github.com/rileously/Mind - System-wide AI writing assistant")
        clip_dialog.history_store.add_entry("def transform_text(prompt, text):\n    return client.generate(prompt + text)")
        pinned = clip_dialog.history_store.add_entry("MVR 15,250.00 invoice balance due for quarterly services")
        if pinned:
            clip_dialog.history_store.toggle_pin(pinned["id"])
        clip_dialog.history_store.add_entry("Team brainstorm: 1. Voice dictation, 2. Dynamic snippets, 3. Clipboard history")
        clip_dialog.show_centered_or_cursor()
        app.processEvents()
        clip_dialog.grab().save(str(clipboard_output))
        clip_dialog.close()
        shield_card = SecretShieldCard()
        sample_secret = "export OPENAI_API_KEY=sk-proj-9xL2pQ8mK5vZ1wY4tE7rB0nC3aD6eF8gH1jK2lM3nP4qR5sT6uV7wX8yZ9"
        findings = detect_secrets(sample_secret)
        shield_card.show_for_findings(sample_secret, findings)
        app.processEvents()
        shield_card.grab().save(str(secret_shield_output))
        shield_card.close()
        url_card = UrlPeekCard()
        sample_url = "https://github.com/rileously/Mind?utm_source=twitter&utm_medium=social&utm_campaign=launch"
        meta = extract_quick_metadata(sample_url, html_snippet="<title>rileously/Mind: Universal AI writing and editing companion</title>")
        url_card.show_for_url(sample_url, meta)
        app.processEvents()
        url_card.grab().save(str(url_peek_output))
        url_card.close()
        ghost_overlay = GhostTextOverlay()
        ghost_overlay.show_suggestion(" hearing from you soon regarding the project timeline.")
        app.processEvents()
        ghost_overlay.grab().save(str(ghost_text_output))
        ghost_overlay.close()
        app.setStyleSheet(stylesheet("dark", "purple"))
        customize = PaletteCustomizeDialog(store)
        customize.show()
        app.processEvents()
        customize.grab().save(str(customize_output))
        customize.close()
        window.tray.hide()
        window.hide()
    print(output)
    print(dashboard_output)
    print(providers_output)
    print(commands_output)
    print(settings_output)
    print(updates_output)
    print(diagnostics_output)
    print(palette_output)
    print(customize_output)
    print(definition_output)
    print(quick_paste_output)
    print(converter_output)
    print(snip_card_output)
    print(clipboard_output)
    print(secret_shield_output)
    print(url_peek_output)
    print(ghost_text_output)


if __name__ == "__main__":
    main()
