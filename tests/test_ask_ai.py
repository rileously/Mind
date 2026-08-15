from __future__ import annotations

import os
import unittest

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from mind.ask_ai_popup import AskAiPopup
from mind.config_store import ConfigStore
from mind.selection import is_notion_input, is_question_text


class AskAiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_is_question_text_valid_questions(self):
        self.assertTrue(is_question_text("What is the capital of France?"))
        self.assertTrue(is_question_text("How does photosynthesis work?"))
        self.assertTrue(is_question_text("Is this working? "))
        self.assertTrue(is_question_text("“Why is the sky blue?”"))
        self.assertTrue(is_question_text('"Can you explain this?"'))
        self.assertTrue(is_question_text("[Who is the author?]"))

    def test_is_question_text_invalid_queries(self):
        self.assertFalse(is_question_text("This is just a normal sentence."))
        self.assertFalse(is_question_text("Hello world!"))
        self.assertFalse(is_question_text("?"))
        self.assertFalse(is_question_text("???"))
        self.assertFalse(is_question_text("   "))
        self.assertFalse(is_question_text(""))
        self.assertFalse(is_question_text(None))

    def test_is_notion_input_invalid_handles(self):
        self.assertFalse(is_notion_input(0))
        self.assertFalse(is_notion_input(999999999))

    def test_ask_ai_popup_lifecycle(self):
        store = ConfigStore()
        popup = AskAiPopup(store)
        try:
            self.assertFalse(popup.isVisible())
            popup.show_pill_for_question("What is AI?", avoid_rect=(100, 100, 200, 120))
            self.assertTrue(popup.isVisible())
            self.assertTrue(popup.pill_widget.isVisible())
            self.assertFalse(popup.card_widget.isVisible())

            # Test copy action
            popup._answer = "Artificial Intelligence is machine intelligence."
            popup._copy_answer()
            clipboard_text = self.app.clipboard().text()
            self.assertEqual(clipboard_text, "Artificial Intelligence is machine intelligence.")
            self.assertEqual(popup.copy_button.text(), "✓ Copied!")

            # Dismiss
            popup.dismiss()
            self.assertFalse(popup.isVisible())
        finally:
            popup.close()

    def test_ask_ai_worker_and_answer_display(self):
        store = ConfigStore()
        popup = AskAiPopup(store)
        try:
            popup.show_pill_for_question("What is Python?")
            popup.card_widget.show()
            popup._on_answer_received(popup._request_id, True, "Python is a programming language.")
            self.assertEqual(popup._answer, "Python is a programming language.")
            self.assertEqual(popup.answer_label.text(), "Python is a programming language.")
            self.assertTrue(popup.copy_button.isVisible())
            self.assertFalse(popup.progress_bar.isVisible())

            # Test error reception
            popup._on_answer_received(popup._request_id, False, "Network error")
            self.assertEqual(popup.answer_label.text(), "Network error")
            self.assertFalse(popup.copy_button.isVisible())
            self.assertFalse(popup.progress_bar.isVisible())
        finally:
            popup.close()

    def test_show_local_math_result(self):
        store = ConfigStore()
        popup = AskAiPopup(store)
        try:
            popup.show_local_math_result("15 * 4 + 10", "70", avoid_rect=(50, 50, 100, 70))
            self.assertTrue(popup.isVisible())
            self.assertTrue(popup.card_widget.isVisible())
            self.assertEqual(popup.title_label.text(), "Calculator")
            self.assertEqual(popup.question_label.text(), "15 * 4 + 10")
            self.assertEqual(popup.answer_label.text(), "= 70")
            self.assertTrue(popup.copy_button.isVisible())
        finally:
            popup.close()

    def test_show_phone_actions(self):
        store = ConfigStore()
        popup = AskAiPopup(store)
        try:
            info = {
                "raw": "7991234",
                "formatted": "+960 799-1234",
                "viber_url": "viber://chat?number=%2B9607991234",
                "telegram_url": "https://t.me/+9607991234",
                "whatsapp_url": "https://wa.me/9607991234",
                "tel_url": "tel:+9607991234",
            }
            popup.show_phone_actions(info, avoid_rect=(50, 50, 100, 70))
            self.assertTrue(popup.isVisible())
            self.assertTrue(popup.card_widget.isVisible())
            self.assertEqual(popup.title_label.text(), "Maldivian Phone")
            self.assertEqual(popup.answer_label.text(), "+960 799-1234")
            self.assertTrue(popup.copy_button.isVisible())
            self.assertTrue(popup.viber_button.isVisible())
            self.assertTrue(popup.telegram_button.isVisible())
            self.assertTrue(popup.whatsapp_button.isVisible())
            self.assertTrue(popup.tel_button.isVisible())
        finally:
            popup.close()


if __name__ == "__main__":
    unittest.main()
