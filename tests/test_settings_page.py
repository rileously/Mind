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

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QWidget,
)

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

    def test_the_mailbox_settings_are_written_down(self):
        # Three fields that decide whose mail is opened. One of them silently
        # not saving is the failure this class exists for.
        self.page.telegram_enabled.setChecked(True)
        self.page.mail_watch.setChecked(True)
        self.page.mail_user.setText("someone@gmail.com")
        self.page.mail_senders.setText("rtl.mv")
        self.page._persist()
        saved = self.store.load()
        self.assertTrue(saved.get("mail_watch_enabled"))
        self.assertEqual(saved.get("mail_user"), "someone@gmail.com")
        self.assertEqual(saved.get("mail_senders"), "rtl.mv")

    def test_pointing_at_another_mailbox_starts_it_over(self):
        # UIDs belong to one mailbox. Carrying the old high mark into a new one
        # would skip every message already in it.
        self.store.save({**self.store.load(), "mail_user": "old@gmail.com", "mail_last_uid": 900})
        page = SettingsPage(self.store)
        self.addCleanup(page.deleteLater)
        page.mail_user.setText("new@gmail.com")
        page._persist()
        self.assertEqual(self.store.load().get("mail_last_uid"), 0)

    def test_the_mailbox_is_only_offered_with_the_bridge_on(self):
        # There is nowhere to send a ticket without Telegram, so watching a
        # mailbox then would only be reading somebody's mail.
        self.store.save({**self.store.load(), "telegram_enabled": False})
        page = SettingsPage(self.store)
        self.addCleanup(page.deleteLater)
        self.assertFalse(page.mail_watch.isEnabled())
        self.assertFalse(page.mail_user.isEnabled())
        self.assertFalse(page.mail_password.isEnabled())

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


class TabTests(unittest.TestCase):
    """Thirty-odd settings, grouped, so the one being looked for is findable."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ConfigStore(root=Path(self.temp.name) / "config")
        self.page = SettingsPage(self.store)
        self.addCleanup(self.page.deleteLater)

    def titles(self) -> list[str]:
        return [self.page.tabs.tabText(i) for i in range(self.page.tabs.count())]

    def test_the_settings_are_split_into_named_tabs(self):
        self.assertEqual(
            self.titles(),
            ["Writing", "Assistance", "Shortcuts", "Telegram", "Appearance", "System"],
        )

    def test_every_setting_row_landed_in_a_tab(self):
        # A row added to no layout would simply not appear, silently.
        rows = 0
        for index in range(self.page.tabs.count()):
            page = self.page.tabs.widget(index).widget()
            rows += len(page.findChildren(QFrame, options=Qt.FindChildrenRecursively))
        self.assertGreater(rows, 25)

    def test_no_tab_is_left_empty(self):
        for index in range(self.page.tabs.count()):
            page = self.page.tabs.widget(index).widget()
            self.assertTrue(
                page.findChildren(QWidget), self.page.tabs.tabText(index)
            )

    def test_each_tab_scrolls_on_its_own(self):
        # The Telegram tab is long; a page-wide scroll would push the tab bar off.
        for index in range(self.page.tabs.count()):
            self.assertIsInstance(self.page.tabs.widget(index), QScrollArea)


class AutoSaveTests(unittest.TestCase):
    """No Save button: a change is the instruction to save it."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ConfigStore(root=Path(self.temp.name) / "config")
        self.page = SettingsPage(self.store)
        self.addCleanup(self.page.deleteLater)

    def test_there_is_no_save_button_left(self):
        labels = [b.text().lower() for b in self.page.findChildren(QPushButton)]
        self.assertNotIn("save settings", labels)

    def test_a_toggle_writes_immediately(self):
        self.page.quick_paste.setChecked(not self.page.quick_paste.isChecked())
        self.assertEqual(
            bool(self.store.load().get("quick_paste_enabled")),
            self.page.quick_paste.isChecked(),
        )

    def test_the_page_says_it_saved(self):
        self.page.url_peek.setChecked(not self.page.url_peek.isChecked())
        self.assertIn("saved", self.page.save_status.text().lower())

    def test_loading_saved_values_does_not_write_them_back(self):
        # The trap this guards: refresh() setting controls looks exactly like the
        # user changing them, and on first launch would save defaults over
        # settings still being read.
        self.store.save({**self.store.load(), "url_peek_enabled": False})
        page = SettingsPage(self.store)
        self.addCleanup(page.deleteLater)
        self.assertFalse(bool(self.store.load().get("url_peek_enabled")))
        self.assertEqual(page.save_status.text(), "")

    def test_typing_is_not_written_on_every_keystroke(self):
        # Held briefly so a half-typed prefix is not saved letter by letter.
        self.page.prefix.setText("!")
        self.page._changed_soon()
        self.assertIn("saving", self.page.save_status.text().lower())
        self.assertEqual(str(self.store.load().get("prefix", "?")), "?")
        self.page._persist()
        self.assertEqual(str(self.store.load().get("prefix")), "!")

    def test_an_unusable_prefix_is_refused_and_said_so(self):
        self.page.prefix.setText("")
        self.page._persist()
        self.assertIn("not saved", self.page.save_status.text().lower())
        self.assertEqual(str(self.store.load().get("prefix", "?")), "?")

    def test_dependent_controls_follow_the_toggle_that_governs_them(self):
        self.page.telegram_enabled.setChecked(True)
        self.page.telegram_files.setChecked(False)
        self.assertTrue(self.page.telegram_files.isEnabled())
        self.assertFalse(self.page.telegram_print.isEnabled())
        self.page.telegram_files.setChecked(True)
        self.assertTrue(self.page.telegram_print.isEnabled())

    def test_switching_the_bridge_on_without_a_chat_id_says_what_is_missing(self):
        # It used to be a modal; on a page that saves as you go, a dialog on every
        # toggle would be unusable.
        self.page.telegram_enabled.setChecked(True)
        self.assertIn("chat id", self.page.save_status.text().lower())

    def test_a_segmented_control_change_is_saved(self):
        self.page.spinner.setCurrentIndex(self.page.spinner.findData("off"))
        # setCurrentIndex is loading, not choosing, so it must not have saved.
        self.assertNotEqual(str(self.store.load().get("spinner", "animated")), "off")
        self.page.spinner.changed.emit("off")
        self.assertEqual(str(self.store.load().get("spinner")), "off")


if __name__ == "__main__":
    unittest.main()


class CardReadingSettings(unittest.TestCase):
    """The one setting in Mind that sends a photograph off this PC."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ConfigStore(root=Path(self.temp.name) / "config")
        self.page = SettingsPage(self.store)
        self.addCleanup(self.page.deleteLater)

    def test_it_is_off_until_it_is_turned_on(self):
        self.assertFalse(self.store.load().get("card_ai_enabled", False))
        self.assertFalse(self.page.card_ai.isChecked())

    def test_turning_it_on_is_written_down(self):
        self.page.telegram_enabled.setChecked(True)
        self.page.card_ai.setChecked(True)
        self.page._persist()
        self.assertTrue(self.store.load().get("card_ai_enabled"))

    def test_it_is_only_offered_with_the_bridge_on(self):
        # Cards arrive over Telegram; there is nowhere else to send one from.
        self.store.save({**self.store.load(), "telegram_enabled": False})
        page = SettingsPage(self.store)
        self.addCleanup(page.deleteLater)
        self.assertFalse(page.card_ai.isEnabled())
