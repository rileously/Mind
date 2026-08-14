import unittest

from mind.text_tools import TextToolError, run_text_tool


class TextToolTests(unittest.TestCase):
    def test_clean_spacing_normalizes_horizontal_and_blank_space(self):
        result = run_text_tool(
            "local-clean-spacing",
            "  First\t line  \r\n\r\n\r\n  Second\u00a0line  ",
        )
        self.assertEqual(result.text, "First line\n\nSecond line")
        self.assertTrue(result.replace)

    def test_lines_to_bullets_replaces_existing_list_markers(self):
        result = run_text_tool("local-bullets", "1. Alpha\n* Beta\n\u2022 Gamma")
        self.assertEqual(result.text, "- Alpha\n- Beta\n- Gamma")

    def test_lines_to_bullets_requires_multiple_lines(self):
        with self.assertRaisesRegex(TextToolError, "two or more lines"):
            run_text_tool("local-bullets", "Only one line")

    def test_deduplicate_lines_preserves_first_spelling_and_order(self):
        result = run_text_tool(
            "local-dedupe-lines",
            "Alpha\nbeta\nALPHA\nGamma\nBeta",
        )
        self.assertEqual(result.text, "Alpha\nbeta\nGamma")
        self.assertEqual(result.message, "Removed 2 duplicate lines.")

    def test_case_tools_are_local_replacements(self):
        upper = run_text_tool("local-uppercase", "Mind 123")
        lower = run_text_tool("local-lowercase", "MIND 123")
        self.assertEqual(upper.text, "MIND 123")
        self.assertEqual(lower.text, "mind 123")
        self.assertTrue(upper.replace)
        self.assertTrue(lower.replace)

    def test_writing_statistics_does_not_replace_selection(self):
        text = "One short sentence.\n\nA second paragraph!"
        result = run_text_tool("local-writing-stats", text)
        self.assertEqual(result.text, text)
        self.assertFalse(result.replace)
        self.assertIn("6 words", result.message)
        self.assertIn("2 sentences", result.message)
        self.assertIn("2 paragraphs", result.message)
        self.assertIn("~1 min read", result.message)

    def test_blank_input_and_unknown_tool_are_rejected(self):
        with self.assertRaisesRegex(TextToolError, "Select some text"):
            run_text_tool("local-uppercase", "  ")
        with self.assertRaisesRegex(TextToolError, "not available"):
            run_text_tool("missing", "text")


if __name__ == "__main__":
    unittest.main()
