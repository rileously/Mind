import tempfile
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication

from mind.clipboard_history_dialog import ClipboardHistoryDialog
from mind.clipboard_history_store import ClipboardHistoryStore, detect_clipboard_category
from mind.config_store import ConfigStore
from mind.hotkeys import CLIPBOARD_HISTORY_SHORTCUTS, clipboard_history_shortcut_candidates


class ClipboardHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temporary.name)
        self.store = ClipboardHistoryStore(self.data_dir, max_unpinned=5)

    def tearDown(self):
        self.temporary.cleanup()

    def test_detect_category(self):
        self.assertEqual(detect_clipboard_category("https://google.com"), "link")
        self.assertEqual(detect_clipboard_category("www.github.com/rileously/Mind"), "link")
        self.assertEqual(detect_clipboard_category('{"name": "Mind", "version": 1}'), "json")
        self.assertEqual(detect_clipboard_category("def calculate_total(a, b):\n    return a + b"), "code")
        self.assertEqual(detect_clipboard_category("$1,250.00"), "number")
        self.assertEqual(detect_clipboard_category("Just normal meeting notes."), "text")

    def test_add_and_get_entries(self):
        self.store.add_entry("First clip")
        self.store.add_entry("Second clip")
        entries = self.store.get_entries()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["text"], "Second clip")
        self.assertEqual(entries[1]["text"], "First clip")

    def test_deduplication(self):
        self.store.add_entry("Repeated text")
        self.store.add_entry("Repeated text")
        entries = self.store.get_entries()
        self.assertEqual(len(entries), 1)

    def test_search_and_category_filter(self):
        self.store.add_entry("https://github.com/rileously/Mind")
        self.store.add_entry("Python def test(): pass")
        self.store.add_entry("Simple sentence")

        links = self.store.get_entries(category="link")
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["category"], "link")

        search = self.store.get_entries(query="github")
        self.assertEqual(len(search), 1)

    def test_pinning_and_ordering(self):
        e1 = self.store.add_entry("Old unpinned")
        e2 = self.store.add_entry("Important pinned")
        self.store.toggle_pin(e2["id"])
        self.store.add_entry("New unpinned")

        entries = self.store.get_entries()
        self.assertTrue(entries[0]["pinned"])
        self.assertEqual(entries[0]["text"], "Important pinned")

    def test_clear_unpinned(self):
        e1 = self.store.add_entry("Unpinned 1")
        e2 = self.store.add_entry("Pinned 1")
        self.store.toggle_pin(e2["id"])
        self.store.add_entry("Unpinned 2")

        removed = self.store.clear_unpinned()
        self.assertEqual(removed, 2)
        remaining = self.store.get_entries()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["text"], "Pinned 1")

    def test_shortcut_candidates(self):
        candidates = clipboard_history_shortcut_candidates("Ctrl+Alt+V")
        self.assertTrue(len(candidates) >= 1)
        self.assertEqual(candidates[0][0], "Ctrl+Alt+V")

    def test_dialog_lifecycle(self):
        config_store = ConfigStore(self.data_dir, self.data_dir)
        dialog = ClipboardHistoryDialog(config_store)
        dialog.history_store.add_entry("Sample clip for dialog")
        dialog.refresh_items()
        self.assertTrue(len(dialog._item_widgets) >= 1)
        dialog.dismiss()
        dialog.close()


if __name__ == "__main__":
    unittest.main()
