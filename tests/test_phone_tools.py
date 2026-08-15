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


if __name__ == "__main__":
    unittest.main()
