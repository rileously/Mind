import unittest

from mind.hotkeys import PALETTE_SHORTCUTS, shortcut_candidates


class HotkeyTests(unittest.TestCase):
    def test_preferred_shortcut_is_tried_first(self):
        candidates = shortcut_candidates("Ctrl+Shift+M")
        self.assertEqual(candidates[0][0], "Ctrl+Shift+M")
        self.assertEqual({item[0] for item in candidates}, set(PALETTE_SHORTCUTS))

    def test_invalid_preference_uses_default_and_keeps_fallbacks(self):
        candidates = shortcut_candidates("invalid")
        self.assertEqual(candidates[0][0], "Ctrl+Alt+M")
        self.assertEqual(len(candidates), len(PALETTE_SHORTCUTS))


if __name__ == "__main__":
    unittest.main()
