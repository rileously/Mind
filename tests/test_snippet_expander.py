import unittest
from datetime import datetime

from mind.snippet_expander import expand_snippet_template


class SnippetExpanderTests(unittest.TestCase):
    def test_plain_text_unmodified(self):
        self.assertEqual(expand_snippet_template("Hello world"), "Hello world")
        self.assertEqual(expand_snippet_template(""), "")

    def test_date_placeholders(self):
        now = datetime.now()
        expected_long = now.strftime("%B %d, %Y")
        expected_iso = now.strftime("%Y-%m-%d")

        self.assertEqual(expand_snippet_template("{date}"), expected_long)
        self.assertEqual(expand_snippet_template("{today}"), expected_long)
        self.assertEqual(expand_snippet_template("{date:iso}"), expected_iso)
        self.assertEqual(expand_snippet_template("{date:short}"), expected_iso)

    def test_time_placeholders(self):
        now = datetime.now()
        expected_12h = now.strftime("%I:%M %p").lstrip("0")
        expected_24h = now.strftime("%H:%M")

        self.assertEqual(expand_snippet_template("{time}"), expected_12h)
        self.assertEqual(expand_snippet_template("{now}"), expected_12h)
        self.assertEqual(expand_snippet_template("{time:24h}"), expected_24h)

    def test_calendar_parts(self):
        now = datetime.now()
        self.assertEqual(expand_snippet_template("{day}"), now.strftime("%A"))
        self.assertEqual(expand_snippet_template("{weekday}"), now.strftime("%A"))
        self.assertEqual(expand_snippet_template("{month}"), now.strftime("%B"))
        self.assertEqual(expand_snippet_template("{year}"), str(now.year))

    def test_clipboard_placeholder(self):
        clip = "https://github.com/rileously/Mind"
        self.assertEqual(expand_snippet_template("Link: {clipboard}", clip), f"Link: {clip}")
        self.assertEqual(expand_snippet_template("Link: {clip}", clip), f"Link: {clip}")

    def test_uuid_placeholder(self):
        result = expand_snippet_template("{uuid}")
        self.assertEqual(len(result), 36)
        self.assertEqual(result.count("-"), 4)

    def test_random_placeholder(self):
        result = expand_snippet_template("Order #{random:1000-9999}")
        self.assertTrue(result.startswith("Order #"))
        num = int(result.replace("Order #", ""))
        self.assertTrue(1000 <= num <= 9999)

    def test_composite_template(self):
        now = datetime.now()
        template = "Meeting on {date} at {time} with {clipboard}"
        expanded = expand_snippet_template(template, "Alex")
        expected_date = now.strftime("%B %d, %Y")
        expected_time = now.strftime("%I:%M %p").lstrip("0")
        self.assertEqual(expanded, f"Meeting on {expected_date} at {expected_time} with Alex")


if __name__ == "__main__":
    unittest.main()
