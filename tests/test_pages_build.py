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


if __name__ == "__main__":
    unittest.main()
