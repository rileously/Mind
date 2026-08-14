import unittest

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from mind.definition_popup import DefinitionPopup
from mind.dictionary import DefinitionResult, DefinitionSense


class DefinitionPopupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.popup = DefinitionPopup()
        self.result = DefinitionResult(
            word="window",
            pronunciation="wɪndoʊ",
            senses=(DefinitionSense("noun", "An opening in a wall."),),
            source_name="Test dictionary",
            source_url="https://www.datamuse.com/api/",
        )
        self.popup.show_result(self.result, (500, 500, 590, 530))
        self.app.processEvents()
        self.popup._dismiss_timer.stop()

    def tearDown(self):
        self.popup.dismiss()
        self.popup.deleteLater()
        self.app.processEvents()

    def test_click_inside_keeps_popup_open(self):
        self.popup._dismiss_if_outside(self.popup.frameGeometry().center())
        self.assertTrue(self.popup.isVisible())

    def test_click_outside_dismisses_popup_and_invalidates_lookup(self):
        request_id = self.popup._request_id
        self.popup._dismiss_if_outside(QPoint(-10000, -10000))
        self.assertFalse(self.popup.isVisible())
        self.assertGreater(self.popup._request_id, request_id)

    def test_dismissed_worker_cannot_reopen_popup(self):
        request_id = self.popup._request_id
        self.popup.dismiss()
        self.popup._lookup_completed(request_id, "window", True, self.result)
        self.app.processEvents()
        self.assertFalse(self.popup.isVisible())
        self.assertNotIn("window", self.popup._cache)


if __name__ == "__main__":
    unittest.main()
