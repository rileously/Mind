from __future__ import annotations

import unittest

from mind.phone_tools import parse_maldivian_phone


class PhoneToolsTests(unittest.TestCase):
    def test_parses_7digit_mobile_numbers(self):
        res = parse_maldivian_phone("7991234")
        self.assertIsNotNone(res)
        self.assertEqual(res["local"], "7991234")
        self.assertEqual(res["international"], "+9607991234")
        self.assertEqual(res["formatted"], "+960 799-1234")
        self.assertEqual(res["digits"], "9607991234")
        self.assertIn("viber://", res["viber_url"])
        self.assertIn("t.me", res["telegram_url"])
        self.assertIn("wa.me", res["whatsapp_url"])
        self.assertIn("tel:", res["tel_url"])

    def test_parses_numbers_with_country_code(self):
        res1 = parse_maldivian_phone("+960 9781234")
        self.assertIsNotNone(res1)
        self.assertEqual(res1["local"], "9781234")

        res2 = parse_maldivian_phone("+960-777-1234")
        self.assertIsNotNone(res2)
        self.assertEqual(res2["local"], "7771234")

        res3 = parse_maldivian_phone("00960 7991234")
        self.assertIsNotNone(res3)
        self.assertEqual(res3["local"], "7991234")

        res4 = parse_maldivian_phone("(960) 978-1234")
        self.assertIsNotNone(res4)
        self.assertEqual(res4["local"], "9781234")

    def test_parses_landline_numbers(self):
        res1 = parse_maldivian_phone("3321234")
        self.assertIsNotNone(res1)
        self.assertEqual(res1["local"], "3321234")

        res2 = parse_maldivian_phone("6881234")
        self.assertIsNotNone(res2)
        self.assertEqual(res2["local"], "6881234")

    def test_ignores_non_maldivian_numbers(self):
        self.assertIsNone(parse_maldivian_phone("12345"))
        self.assertIsNone(parse_maldivian_phone("12345678901234567890"))
        self.assertIsNone(parse_maldivian_phone("Just some random text without phone"))
        self.assertIsNone(parse_maldivian_phone(""))
        self.assertIsNone(parse_maldivian_phone(None))


class PhoneCardTests(unittest.TestCase):
    """The card a selected number puts up, once there is a phone to ask."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def card(self, can_call: bool = True):
        import tempfile
        from pathlib import Path

        from mind.ask_ai_popup import AskAiPopup
        from mind.config_store import ConfigStore
        from mind.phone_tools import parse_maldivian_phone

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        popup = AskAiPopup(ConfigStore(root=Path(temp.name) / "config"))
        self.addCleanup(popup.deleteLater)
        info = parse_maldivian_phone("9322011")
        popup.show_phone_actions(info, None, can_call=can_call)
        return popup, info

    def test_the_name_replaces_the_heading_and_not_the_number(self):
        # The number is already on the card; writing it above itself would
        # print it twice.
        popup, info = self.card()
        popup.set_contact_name("9322011", "Dhipoz")
        self.assertEqual(popup.title_label.text(), "Dhipoz")
        self.assertEqual(popup.answer_label.text(), info["formatted"])
        self.assertNotEqual(popup.question_label.text(), info["formatted"])

    def test_an_answer_about_another_number_is_ignored(self):
        # The lookup easily outlives the card it was for: a number is selected,
        # the card goes, another is selected, and the first answer arrives.
        popup, _info = self.card()
        popup.set_contact_name("7654321", "Somebody Else")
        self.assertEqual(popup.title_label.text(), "Maldivian Phone")

    def test_a_number_with_no_contact_leaves_the_card_alone(self):
        popup, _info = self.card()
        popup.set_contact_name("9322011", "")
        self.assertEqual(popup.title_label.text(), "Maldivian Phone")

    def test_calling_from_the_phone_is_offered_only_when_there_is_one(self):
        # A button that could only fail is worse than no button.
        with_phone, _ = self.card(can_call=True)
        self.assertTrue(with_phone.phone_call_button.isVisible())
        without, _ = self.card(can_call=False)
        self.assertFalse(without.phone_call_button.isVisible())

    def test_the_button_asks_for_the_call_rather_than_placing_it(self):
        # The card knows nothing about phones. Whoever owns one listens.
        popup, info = self.card()
        asked = []
        popup.call_requested.connect(asked.append)
        popup._call_from_phone()
        self.assertEqual(asked, [info["international"]])


if __name__ == "__main__":
    unittest.main()
