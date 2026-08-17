"""The Preferences page can actually be built.

A settings row added with an argument missing raised only when the page was
constructed, which no test did. The result was an executable that could not
start at all: PyInstaller showed a traceback dialog, and because that dialog
keeps the process alive, even the installer's startup check believed the app was
running. Building the page here is cheap and catches that before it ships.
"""

import tempfile
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication, QCheckBox, QLineEdit

from mind.config_store import ConfigStore
from mind.main_window import SettingsPage


class SettingsPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ConfigStore(root=Path(self.temp.name) / "config")
        self.page = SettingsPage(self.store)
        self.addCleanup(self.page.deleteLater)

    def test_the_page_builds(self):
        # The whole point: every row's arguments are checked by constructing it.
        self.assertTrue(self.page.layout().count() > 0)

    def test_every_telegram_toggle_exists_and_can_be_read(self):
        # Each of these is saved by name, so a missing one would silently drop a
        # setting rather than fail loudly.
        for name in (
            "telegram_files",
            "telegram_print",
            "telegram_control",
            "telegram_power",
            "telegram_notifications",
            "telegram_send_menu",
        ):
            widget = getattr(self.page, name, None)
            self.assertIsNotNone(widget, name)
            self.assertIsInstance(widget.isChecked(), bool, name)

    def test_the_printing_toggle_starts_off(self):
        # Printing spends paper on a machine the user may not be beside.
        self.assertFalse(self.page.telegram_print.isChecked())

    def test_the_paths_it_saves_are_present(self):
        for name in ("telegram_files_root", "telegram_inbox"):
            self.assertIsInstance(getattr(self.page, name), QLineEdit, name)

    def test_reloading_the_page_applies_the_saved_settings(self):
        self.store.save({**self.store.load(), "telegram_print_enabled": True})
        page = SettingsPage(self.store)
        self.addCleanup(page.deleteLater)
        self.assertTrue(page.telegram_print.isChecked())

    def test_printing_is_only_offered_alongside_file_access(self):
        # It prints what was saved, so it cannot work without saving.
        self.store.save(
            {
                **self.store.load(),
                "telegram_enabled": True,
                "telegram_files_enabled": False,
                "telegram_print_enabled": True,
            }
        )
        page = SettingsPage(self.store)
        self.addCleanup(page.deleteLater)
        self.assertFalse(page.telegram_print.isEnabled())


if __name__ == "__main__":
    unittest.main()
