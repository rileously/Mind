import unittest

from mind.theme import ACCENTS, theme_palette


class ThemeTests(unittest.TestCase):
    def test_each_accent_changes_theme_highlights(self):
        colors = {theme_palette("dark", name)["accent"] for name in ACCENTS}
        self.assertEqual(len(colors), len(ACCENTS))

    def test_invalid_accent_falls_back_to_teal(self):
        self.assertEqual(
            theme_palette("light", "invalid")["accent"],
            theme_palette("light", "teal")["accent"],
        )


if __name__ == "__main__":
    unittest.main()
