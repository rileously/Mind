"""Render Mind screens for local visual QA without changing real user settings."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication

from mind.config_store import ConfigStore
from mind.main_window import MindWindow
from mind.palette import MindPalette
from mind.selection import SelectionSession
from mind.setup_wizard import SetupWizard
from mind.theme import stylesheet
from mind.ui_components import PaletteCustomizeDialog


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


if __name__ == "__main__":
    main()
