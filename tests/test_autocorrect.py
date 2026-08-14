from __future__ import annotations

import unittest

from mind.autocorrect import CompletedToken, LocalAutocorrect, completed_token


class AutocorrectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.autocorrect = LocalAutocorrect()

    def test_completed_token_keeps_trailing_punctuation(self) -> None:
        self.assertEqual(completed_token("Please fix teh,"), CompletedToken("teh", ","))

    def test_completed_token_rejects_urls_paths_identifiers_and_commands(self) -> None:
        self.assertIsNone(completed_token("example.com"))
        self.assertIsNone(completed_token("folder\\mispelled"))
        self.assertIsNone(completed_token("snake_mispelled"))
        self.assertIsNone(completed_token("?mispelled"))

    def test_local_autocorrect_handles_clear_common_errors(self) -> None:
        self.assertEqual(self.autocorrect.suggest("teh"), "the")
        self.assertEqual(self.autocorrect.suggest("Teh"), "The")
        self.assertEqual(self.autocorrect.suggest("wrod"), "word")
        self.assertEqual(self.autocorrect.suggest("recieve"), "receive")
        self.assertEqual(self.autocorrect.suggest("helo"), "hello")

    def test_balanced_mode_handles_everyday_ambiguous_typos(self) -> None:
        self.assertEqual(self.autocorrect.suggest("plase", "balanced"), "please")
        self.assertEqual(self.autocorrect.suggest("prbkm", "balanced"), "problem")
        self.assertEqual(self.autocorrect.suggest("thar", "balanced"), "that")
        self.assertEqual(self.autocorrect.suggest("crnt", "balanced"), "can't")

    def test_conservative_and_strong_modes_have_distinct_sensitivity(self) -> None:
        self.assertIsNone(self.autocorrect.suggest("plase", "conservative"))
        self.assertIsNone(self.autocorrect.suggest("hapenning", "balanced"))
        self.assertEqual(self.autocorrect.suggest("hapenning", "strong"), "happening")

    def test_balanced_mode_catches_up_on_a_fast_typed_phrase(self) -> None:
        correction = self.autocorrect.correct_tail(
            "plase fix these prbkm in here thar you think crnt fix",
            "balanced",
        )

        self.assertIsNotNone(correction)
        self.assertEqual(
            correction.corrected,
            "please fix these problem in here that you think can't fix",
        )

    def test_phrase_correction_stays_in_latest_sentence_and_skips_urls(self) -> None:
        correction = self.autocorrect.correct_tail(
            "plase wait. Visit exampel.com then plase reply",
            "balanced",
        )

        self.assertIsNotNone(correction)
        self.assertEqual(correction.original, "Visit exampel.com then plase reply")
        self.assertEqual(correction.corrected, "Visit exampel.com then please reply")

    def test_local_autocorrect_leaves_valid_or_sensitive_words_alone(self) -> None:
        self.assertIsNone(self.autocorrect.suggest("form"))
        self.assertIsNone(self.autocorrect.suggest("Musheer"))
        self.assertIsNone(self.autocorrect.suggest("API"))
        self.assertIsNone(self.autocorrect.suggest("x1"))
        self.assertIsNone(self.autocorrect.suggest("is"))

    def test_local_autocorrect_uses_safe_override_for_ambiguous_guess(self) -> None:
        # A raw frequency dictionary prefers "dress" here. Mind's explicit correction is
        # deliberately used instead of accepting the unrelated top-frequency suggestion.
        self.assertEqual(self.autocorrect.suggest("adress"), "address")


if __name__ == "__main__":
    unittest.main()
