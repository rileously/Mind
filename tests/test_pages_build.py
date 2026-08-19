"""Every page can be built.

This exists because of a name that was used and never imported. The whole suite
passed - 754 tests - and the application would not start, because nothing in it
had ever constructed that page. A test that only tests behaviour cannot catch a
window that cannot be drawn.

So this builds every page in the sidebar, and the window that holds them. It
asserts almost nothing beyond that, because there is almost nothing to assert:
if the constructor runs, the name exists, the import is there, and the layout
is buildable.
"""

import tempfile
import unittest
from pathlib import Path

from mind.config_store import ConfigStore


class PageBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ConfigStore(root=Path(self.temp.name) / "config")

    def build(self, name: str, *args):
        import mind.main_window as window

        page = getattr(window, name)(*args)
        self.addCleanup(page.deleteLater)
        return page

    def test_every_page_in_the_sidebar_can_be_built(self):
        for name, args in (
            ("DashboardPage", (self.store,)),
            ("ProvidersPage", (self.store,)),
            ("CommandsPage", (self.store,)),
            ("NotificationsPage", (self.store,)),
            ("NetworkDevicesPage", (self.store, None)),
            ("PhonePage", (self.store, None)),
            ("MessagesPage", (self.store,)),
            ("SettingsPage", (self.store,)),
            ("DiagnosticsPage", (self.store.root,)),
        ):
            with self.subTest(page=name):
                self.assertIsNotNone(self.build(name, *args))

    def test_the_sidebar_and_the_pages_stay_in_step(self):
        # A tap carries a position in the list. A page added to one and not the
        # other would send every button below it to the wrong place.
        import mind.main_window as window

        source = Path(window.__file__).read_text(encoding="utf-8")
        navigation = source.count('button.setProperty("navTitle"')
        self.assertEqual(navigation, 1, "the sidebar is built in one loop")

    def test_a_page_that_refreshes_can_be_refreshed(self):
        for name, args in (
            ("DashboardPage", (self.store,)),
            ("NetworkDevicesPage", (self.store, None)),
            ("PhonePage", (self.store, None)),
            ("MessagesPage", (self.store,)),
            ("SettingsPage", (self.store,)),
        ):
            with self.subTest(page=name):
                page = self.build(name, *args)
                refresh = getattr(page, "refresh", None)
                if callable(refresh):
                    refresh()


class ThePairingWindow(unittest.TestCase):
    """It has to open, and drawing the code must not be what stops it."""

    def setUp(self):
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])

    def test_a_code_can_be_drawn(self):
        from mind.adb_pairing import new_name, new_password, qr_payload
        from mind.pair_dialog import qr_pixmap

        pixmap = qr_pixmap(qr_payload(new_name(), new_password()))
        self.assertFalse(pixmap.isNull())
        self.assertGreater(pixmap.width(), 100)

    def test_the_window_opens_and_closes_without_leaving_a_worker(self):
        from mind.pair_dialog import PairDialog

        dialog = PairDialog()
        self.addCleanup(dialog.deleteLater)
        self.assertIsNotNone(dialog._worker)
        dialog.reject()
        # Cancelled, so the thread stops looking for a phone nobody is pairing.
        self.assertFalse(dialog._worker._wanted)


class MessagesPageStatus(unittest.TestCase):
    """What the Messages page says while it does not yet have messages.

    The search box redraws the list on every keystroke, and the redraw writes
    the status line. So a keystroke arriving while the phone is being asked, or
    after the asking failed, must not replace the real reason with a cheerful
    guess that the inbox is empty.
    """

    def setUp(self):
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ConfigStore(root=Path(self.temp.name) / "config")
        import mind.main_window as window

        self.page = window.MessagesPage(self.store)
        self.addCleanup(self.page.deleteLater)

    def test_before_anything_is_asked_it_says_so(self):
        self.page._render()
        self.assertIn("Press Refresh", self.page.status_label.text())

    def test_a_keystroke_while_asking_does_not_claim_an_empty_inbox(self):
        self.page._busy = True
        self.page.search.setText("anything")
        self.assertIn("Asking the phone", self.page.status_label.text())

    def test_a_keystroke_after_a_failure_keeps_the_failure(self):
        self.page._arrived(False, "device 'pixel' not found")
        self.page.search.setText("anything")
        self.assertIn("not found", self.page.status_label.text())

    def test_a_phone_with_no_messages_reads_differently_from_never_asking(self):
        self.page._arrived(True, [])
        self.assertIn("No messages on the phone", self.page.status_label.text())

    def test_no_phone_paired_is_said_without_starting_a_worker(self):
        self.page.refresh()
        self.assertIn("No phone is paired", self.page.status_label.text())
        self.assertFalse(self.page._busy)


if __name__ == "__main__":
    unittest.main()


class EveryTextFieldSaves(unittest.TestCase):
    """A field that is not wired to the save does nothing at all.

    It takes what is typed, shows it, and forgets it the moment the page is
    rebuilt - which reads as the setting having no effect rather than as a
    field that was never connected. Two of them shipped that way.
    """

    def setUp(self):
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ConfigStore(root=Path(self.temp.name) / "config")
        import mind.main_window as window

        self.page = window.SettingsPage(self.store)
        self.addCleanup(self.page.deleteLater)

    @staticmethod
    def listens(widget, signal_name):
        from PySide6.QtCore import QMetaMethod

        meta = widget.metaObject()
        for index in range(meta.methodCount()):
            method = meta.method(index)
            if method.methodType() == QMetaMethod.Signal:
                if bytes(method.name()).decode() == signal_name:
                    if widget.isSignalConnected(method):
                        return True
        return False

    def test_no_line_edit_is_left_unconnected(self):
        from PySide6.QtWidgets import QLineEdit

        loose = [
            field.placeholderText() or field.objectName() or "unnamed"
            for field in self.page.findChildren(QLineEdit)
            if not self.listens(field, "textEdited")
            and not self.listens(field, "editingFinished")
        ]
        self.assertEqual(loose, [], f"these fields never save what is typed: {loose}")
