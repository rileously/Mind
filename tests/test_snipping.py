import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QPoint, QRect, QSize
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication

from mind.config_store import ConfigStore
from mind.hotkeys import SNIP_SHORTCUTS, snip_shortcut_candidates
from mind.snip_card import SnipCard
from mind.snipping_overlay import SnippingOverlay


class SnippingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = ConfigStore(Path(self.temporary.name) / "data", Path(self.temporary.name))
        self.overlay = SnippingOverlay()
        self.card = SnipCard(self.store)

    def tearDown(self):
        self.overlay.close()
        self.card.close()
        self.temporary.cleanup()

    def test_snip_shortcut_candidates(self):
        candidates = snip_shortcut_candidates("Ctrl+Alt+S")
        self.assertTrue(len(candidates) >= 1)
        self.assertEqual(candidates[0][0], "Ctrl+Alt+S")

    def test_snipping_overlay_selection_rect(self):
        self.overlay._start_pos = QPoint(100, 100)
        self.overlay._current_pos = QPoint(300, 250)
        rect = self.overlay._selection_rect()
        self.assertEqual(rect, QRect(100, 100, 201, 151))

    def test_snip_card_show_for_pixmap(self):
        pixmap = QPixmap(200, 100)
        pixmap.fill()
        self.card.show_for_pixmap(pixmap, QRect(50, 50, 200, 100))
        self.assertTrue(self.card.isVisible())
        self.assertEqual(self.card.size_badge.text(), "200 × 100 px")
        self.card.dismiss()
        self.assertFalse(self.card.isVisible())

    def test_snip_card_copy_image(self):
        pixmap = QPixmap(50, 50)
        pixmap.fill()
        self.card._pixmap = pixmap
        self.card._copy_image()
        clipboard = QApplication.clipboard()
        self.assertFalse(clipboard.pixmap().isNull())


if __name__ == "__main__":
    unittest.main()
