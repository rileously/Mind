import unittest

from mind.autocomplete_engine import get_local_smart_completion, suggest_sentence_completion
from mind.hotkeys import GHOST_TEXT_SHORTCUTS, ghost_text_shortcut_candidates


class AutocompleteEngineTests(unittest.TestCase):
    def test_exact_phrase_matching(self):
        suggestion = get_local_smart_completion("Looking forward to")
        self.assertIsNotNone(suggestion)
        self.assertIn("hearing from you soon", suggestion)

    def test_partial_phrase_completion(self):
        suggestion = suggest_sentence_completion("Please let me know if")
        self.assertIsNotNone(suggestion)
        self.assertTrue("questions" in suggestion or "clarification" in suggestion)

    def test_closing_remarks(self):
        suggestion = suggest_sentence_completion("Thank you for your")
        self.assertIsNotNone(suggestion)
        self.assertIn("time and assistance", suggestion)

    def test_punctuation_abbreviations(self):
        suggestion = suggest_sentence_completion("Use tools e.g.")
        self.assertEqual(suggestion, " for example,")

    def test_ghost_text_shortcut_candidates(self):
        candidates = ghost_text_shortcut_candidates("Ctrl+Alt+Space")
        self.assertTrue(len(candidates) >= 1)
        self.assertEqual(candidates[0][0], "Ctrl+Alt+Space")


if __name__ == "__main__":
    unittest.main()
