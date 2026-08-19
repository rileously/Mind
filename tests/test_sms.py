"""Reading the phone's messages, without a phone.

What is tested is the parsing, because that is where this can go wrong
quietly. "content query" prints the body last and unescaped, so a message
containing a comma, an equals sign, or a newline is the interesting case
rather than the exotic one - on the phone this was written against, about half
of them span more than one line.

The rule with teeth is the last group: a message is never allowed to end up
attached to the wrong sender, however it is punctuated.
"""

import time
import unittest

from mind.sms import (
    Message,
    matching,
    parse_messages,
    read_messages,
    unread,
)


ONE = "Row: 0 _id=12, address=455, date=1787113598522, read=0, type=1, body=Your code is 4782"

TWO_LINE = """Row: 0 _id=12, address=Dhiraagu, date=1787113598522, read=1, type=1, body=Dear customer,
your balance is low."""

AWKWARD = (
    "Row: 0 _id=13, address=+9607771234, date=1787000000000, read=1, type=2, "
    "body=Total: 40, plus 5 = 45, see you"
)

MANY = """Row: 0 _id=12, address=455, date=1787113598522, read=0, type=1, body=first
Row: 1 _id=11, address=Bank, date=1787000000000, read=1, type=2, body=second
across two lines
Row: 2 _id=10, address=455, date=1786000000000, read=1, type=1, body=third"""

NOISE = """Warning: something the shell said
Row: 0 _id=12, address=455, date=1787113598522, read=1, type=1, body=hello"""


class ReadingRows(unittest.TestCase):
    def test_one_message(self):
        found = parse_messages(ONE)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].address, "455")
        self.assertEqual(found[0].body, "Your code is 4782")
        self.assertFalse(found[0].read)
        self.assertFalse(found[0].outgoing)

    def test_the_date_arrives_in_seconds(self):
        # The phone keeps milliseconds; everything in Python expects seconds.
        self.assertAlmostEqual(parse_messages(ONE)[0].when, 1787113598.522, places=3)

    def test_a_sent_message_knows_it_was_sent(self):
        self.assertTrue(parse_messages(AWKWARD)[0].outgoing)

    def test_a_message_that_spans_lines_keeps_both(self):
        found = parse_messages(TWO_LINE)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].body, "Dear customer,\nyour balance is low.")

    def test_commas_and_equals_signs_inside_a_message_survive(self):
        # Body is last in the projection precisely so this cannot split a row.
        found = parse_messages(AWKWARD)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].body, "Total: 40, plus 5 = 45, see you")
        self.assertEqual(found[0].address, "+9607771234")

    def test_several_messages_with_one_spanning_lines(self):
        found = parse_messages(MANY)
        self.assertEqual([m.body for m in found], ["first", "second\nacross two lines", "third"])
        self.assertEqual([m.address for m in found], ["455", "Bank", "455"])

    def test_anything_before_the_first_row_is_not_a_message(self):
        found = parse_messages(NOISE)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].body, "hello")

    def test_nothing_at_all(self):
        self.assertEqual(parse_messages(""), [])

    def test_a_row_with_no_body_is_still_a_message(self):
        found = parse_messages("Row: 0 _id=12, address=455, date=0, read=1, type=1")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].body, "")
        self.assertEqual(found[0].address, "455")


class TheWrongSender(unittest.TestCase):
    """A message must never be shown against somebody who did not send it."""

    def test_a_body_that_looks_like_a_row_does_not_start_one(self):
        # Only a line beginning with the marker begins a row; a quoted one
        # inside a message is part of that message.
        payload = (
            "Row: 0 _id=12, address=Alice, date=0, read=1, type=1, body=she wrote\n"
            "Row: 1 _id=99, address=Mallory, date=0, read=1, type=1, body=trust me"
        )
        found = parse_messages(payload)
        # Two rows, because the second marker does start at the beginning of a
        # line - which is exactly the case that has to keep working.
        self.assertEqual(len(found), 2)
        self.assertEqual(found[1].address, "Mallory")

    def test_an_indented_row_marker_belongs_to_the_message_above(self):
        payload = (
            "Row: 0 _id=12, address=Alice, date=0, read=1, type=1, body=look\n"
            "  Row: 1 _id=99, address=Mallory, date=0, read=1, type=1, body=trust me"
        )
        found = parse_messages(payload)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].address, "Alice")


class Skimming(unittest.TestCase):
    def test_the_preview_is_one_line(self):
        message = Message(body="two\nlines   with   spaces")
        self.assertEqual(message.preview, "two lines with spaces")

    def test_a_long_message_is_cut(self):
        message = Message(body="x" * 300)
        self.assertLessEqual(len(message.preview), 90)
        self.assertTrue(message.preview.endswith("…"))

    def test_unread_counts_only_what_came_in(self):
        messages = [
            Message(read=False, outgoing=False),
            Message(read=False, outgoing=True),
            Message(read=True, outgoing=False),
        ]
        self.assertEqual(unread(messages), 1)

    def test_search_looks_at_the_sender_as_well_as_the_message(self):
        messages = [Message(address="Bank", body="hello"), Message(address="455", body="code")]
        self.assertEqual(len(matching(messages, "bank")), 1)
        self.assertEqual(len(matching(messages, "CODE")), 1)
        self.assertEqual(len(matching(messages, "")), 2)

    def test_todays_message_shows_only_a_time(self):
        message = Message(when=time.time())
        self.assertRegex(message.when_label(), r"^\d\d:\d\d$")

    def test_a_message_with_no_date_says_nothing(self):
        self.assertEqual(Message().when_label(), "")


class AskingThePhone(unittest.TestCase):
    class FakePhone:
        def __init__(self, payload=""):
            self.payload = payload
            self.calls: list[tuple] = []

        def shell(self, *arguments, timeout=None):
            self.calls.append(arguments)
            return self.payload

    def test_the_body_is_asked_for_last(self):
        # The whole parsing strategy rests on this, so it is worth pinning.
        phone = self.FakePhone(ONE)
        read_messages(phone)
        projection = phone.calls[0][phone.calls[0].index("--projection") + 1]
        self.assertTrue(projection.endswith("body"))

    def test_the_limit_survives_the_shell_on_the_phone(self):
        # adb splits arguments again at the far end, so a sort with spaces has
        # to arrive quoted or content prints its usage instead of any rows.
        phone = self.FakePhone(ONE)
        read_messages(phone, limit=25)
        sort = phone.calls[0][phone.calls[0].index("--sort") + 1]
        self.assertTrue(sort.startswith("'") and sort.endswith("'"))
        self.assertIn("LIMIT 25", sort)

    def test_a_silly_limit_is_still_a_number(self):
        phone = self.FakePhone(ONE)
        read_messages(phone, limit=0)
        sort = phone.calls[0][phone.calls[0].index("--sort") + 1]
        self.assertIn("LIMIT 1", sort)


if __name__ == "__main__":
    unittest.main()
