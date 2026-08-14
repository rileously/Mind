import unittest
from unittest.mock import patch

from mind.dictionary import (
    DefinitionLookupError,
    DefinitionResult,
    DefinitionSense,
    lookup_definition,
    normalize_selected_word,
    parse_datamuse_response,
    parse_wiktionary_response,
)


class SelectedWordTests(unittest.TestCase):
    def test_accepts_one_word_with_edge_punctuation(self):
        self.assertEqual(normalize_selected_word("  “serendipity,”  "), "serendipity")
        self.assertEqual(normalize_selected_word("mother-in-law"), "mother-in-law")
        self.assertEqual(normalize_selected_word("don't"), "don't")

    def test_rejects_phrases_sentences_numbers_and_symbols(self):
        for text in ("two words", "a full sentence.", "version2", "hello_world", "--"):
            with self.subTest(text=text):
                self.assertIsNone(normalize_selected_word(text))

    def test_rejects_broken_joiners(self):
        for text in ("-hello", "hello-", "rock--roll", "don''t"):
            with self.subTest(text=text):
                self.assertIsNone(normalize_selected_word(text))


class DictionaryParsingTests(unittest.TestCase):
    def test_parses_exact_datamuse_result(self):
        payload = [{
            "word": "serendipity",
            "tags": ["n", "pron:S EH R AH N", "ipa_pron:/ˌsɛrənˈdɪpɪti/"],
            "defs": [
                "n\tA fortunate discovery made by chance.",
                "n\tAn unexpected but beneficial occurrence.",
                "n\tA third definition that should not be shown.",
            ],
        }]
        result = parse_datamuse_response(payload, "serendipity")
        self.assertIsNotNone(result)
        self.assertEqual(result.pronunciation, "ˌsɛrənˈdɪpɪti")
        self.assertEqual(result.senses[0].part_of_speech, "noun")
        self.assertEqual(len(result.senses), 2)

    def test_ignores_non_exact_datamuse_result(self):
        payload = [{"word": "hello", "defs": ["n\tA greeting."]}]
        self.assertIsNone(parse_datamuse_response(payload, "help"))

    def test_parses_wiktionary_html_as_plain_text(self):
        payload = {
            "en": [{
                "partOfSpeech": "Noun",
                "definitions": [{
                    "definition": "A <a href='/wiki/test'>fortunate</a> discovery<sup>1</sup>.",
                }],
            }],
        }
        result = parse_wiktionary_response(payload, "serendipity")
        self.assertEqual(result.senses[0].definition, "A fortunate discovery.")
        self.assertEqual(result.senses[0].part_of_speech, "noun")
        self.assertIn("serendipity", result.source_url)


class DictionaryLookupTests(unittest.TestCase):
    def setUp(self):
        self.fallback = DefinitionResult(
            word="hello",
            pronunciation="",
            senses=(DefinitionSense("interjection", "A greeting."),),
            source_name="Wiktionary · CC BY-SA",
            source_url="https://en.wiktionary.org/wiki/hello",
        )

    @patch("mind.dictionary._lookup_wiktionary")
    @patch("mind.dictionary._lookup_datamuse")
    def test_falls_back_when_primary_has_no_definition(self, datamuse, wiktionary):
        datamuse.return_value = None
        wiktionary.return_value = self.fallback
        self.assertEqual(lookup_definition("hello"), self.fallback)
        wiktionary.assert_called_once()

    @patch("mind.dictionary._lookup_datamuse")
    def test_rejects_multiple_words_before_network_lookup(self, datamuse):
        with self.assertRaises(DefinitionLookupError):
            lookup_definition("hello there")
        datamuse.assert_not_called()


if __name__ == "__main__":
    unittest.main()
