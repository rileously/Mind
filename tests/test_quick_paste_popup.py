import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from mind.quick_paste_popup import QuickPastePopup


class QuickPastePopupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.popup = QuickPastePopup()
        self.popup.show_for_text("https://github.com/rileously/Mind", 12345, (500, 500, 590, 530))
        self.app.processEvents()
        self.popup._dismiss_timer.stop()

    def tearDown(self):
        self.popup.dismiss()
        self.popup.deleteLater()
        self.app.processEvents()

    def test_popup_shows_preview_text(self):
        self.assertTrue(self.popup.isVisible())
        self.assertIn("https://github.com", self.popup.preview_label.text())

    def test_long_text_is_truncated(self):
        long_text = "A" * 100
        self.popup.show_for_text(long_text, 12345)
        self.assertTrue(self.popup.preview_label.text().endswith("…”"))

    def test_paste_button_triggers_paste_and_dismisses(self):
        pasted_payloads = []
        self.popup.pasted.connect(pasted_payloads.append)

        with patch("mind.quick_paste_popup.send_paste_input") as send_paste_mock:
            self.popup.paste_button.click()
            self.app.processEvents()
            send_paste_mock.assert_called_once_with(12345)

        self.assertFalse(self.popup.isVisible())
        self.assertEqual(len(pasted_payloads), 1)

    def test_click_inside_keeps_popup_open(self):
        self.popup._poll_for_dismissal()
        self.assertTrue(self.popup.isVisible())

    def test_dismiss_hides_popup(self):
        self.popup.dismiss()
        self.assertFalse(self.popup.isVisible())


if __name__ == "__main__":
    unittest.main()
