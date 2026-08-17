"""Inline keyboards carry indexes, never paths.

Telegram caps callback_data at 64 bytes, so a button refers to a position in the
listing the chat is currently looking at. That keeps the payload tiny whatever a
file is called, and means a button can never smuggle a path of its own.
"""

import unittest

from mind.telegram_files import (
    BUTTON_PAGE_SIZE,
    CB_GET,
    CB_HOME,
    CB_OPEN,
    CB_PAGE,
    CB_UP,
    Entry,
    build_keyboard,
    callback_data,
    parse_callback,
)


def make_entries(folders: int, files: int) -> list[Entry]:
    entries = [Entry(f"folder-{i}", True, 0) for i in range(folders)]
    entries += [Entry(f"file-{i}.txt", False, 1024 * (i + 1)) for i in range(files)]
    return entries


def all_buttons(markup: dict) -> list[dict]:
    return [button for row in markup["inline_keyboard"] for button in row]


class CallbackEncodingTests(unittest.TestCase):
    def test_round_trip(self):
        self.assertEqual(parse_callback(callback_data(CB_OPEN, 12)), (CB_OPEN, 12))
        self.assertEqual(parse_callback(callback_data(CB_UP)), (CB_UP, None))

    def test_malformed_data_does_not_raise(self):
        self.assertEqual(parse_callback("o:notanumber"), ("o", None))
        self.assertEqual(parse_callback(""), ("", None))
        self.assertEqual(parse_callback("garbage"), ("garbage", None))

    def test_payloads_stay_far_inside_the_64_byte_cap(self):
        # A 200-character filename must not change the payload size at all.
        entries = [Entry("x" * 200, False, 10)] * 30
        markup = build_keyboard(entries, page=1, at_root=False)
        for button in all_buttons(markup):
            self.assertLessEqual(len(button["callback_data"].encode("utf-8")), 64)


class KeyboardLayoutTests(unittest.TestCase):
    def test_folders_open_and_files_download(self):
        markup = build_keyboard(make_entries(1, 1), page=1, at_root=True)
        actions = [parse_callback(b["callback_data"])[0] for b in all_buttons(markup)]
        self.assertIn(CB_OPEN, actions)
        self.assertIn(CB_GET, actions)

    def test_up_and_home_appear_only_below_the_root(self):
        at_root = all_buttons(build_keyboard(make_entries(2, 0), 1, at_root=True))
        self.assertNotIn(CB_UP, [b["callback_data"] for b in at_root])
        self.assertNotIn(CB_HOME, [b["callback_data"] for b in at_root])

        deeper = all_buttons(build_keyboard(make_entries(2, 0), 1, at_root=False))
        self.assertIn(CB_UP, [b["callback_data"] for b in deeper])
        self.assertIn(CB_HOME, [b["callback_data"] for b in deeper])

    def test_indexes_are_absolute_across_pages(self):
        # A number must keep meaning the same entry after paging, or the second
        # page would download the wrong file.
        entries = make_entries(0, BUTTON_PAGE_SIZE * 2)
        page_two = build_keyboard(entries, page=2, at_root=False)
        indexes = [
            parse_callback(b["callback_data"])[1]
            for b in all_buttons(page_two)
            if parse_callback(b["callback_data"])[0] == CB_GET
        ]
        self.assertEqual(indexes[0], BUTTON_PAGE_SIZE)

    def test_paging_buttons_appear_only_where_they_lead_somewhere(self):
        single = all_buttons(build_keyboard(make_entries(2, 0), 1, at_root=True))
        self.assertNotIn(CB_PAGE, [parse_callback(b["callback_data"])[0] for b in single])

        many = make_entries(0, BUTTON_PAGE_SIZE * 3)
        first = all_buttons(build_keyboard(many, page=1, at_root=True))
        labels = [b["text"] for b in first]
        self.assertTrue(any("Next" in label for label in labels))
        self.assertFalse(any("Back" in label for label in labels))

        last_page = all_buttons(build_keyboard(many, page=3, at_root=True))
        labels = [b["text"] for b in last_page]
        self.assertTrue(any("Back" in label for label in labels))
        self.assertFalse(any("Next" in label for label in labels))

    def test_a_page_beyond_the_end_is_clamped_rather_than_empty(self):
        markup = build_keyboard(make_entries(3, 0), page=99, at_root=True)
        self.assertTrue(all_buttons(markup))

    def test_long_names_are_shortened_for_the_button_face(self):
        markup = build_keyboard([Entry("y" * 120, True, 0)], 1, at_root=False)
        opener = [
            b for b in all_buttons(markup)
            if parse_callback(b["callback_data"])[0] == CB_OPEN
        ][0]
        self.assertLess(len(opener["text"]), 60)
        self.assertIn("…", opener["text"])


if __name__ == "__main__":
    unittest.main()
