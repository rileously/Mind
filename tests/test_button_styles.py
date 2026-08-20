"""Colour on buttons, which Telegram accepts only in three flavours.

Bot API 9.4 added a "style" field taking exactly "primary", "success" or
"danger"; anything else comes back as "invalid button style" and the whole
message is refused rather than the field ignored. That refusal is the thing
worth protecting against, because it turns a coloured panel into no panel.

The other rule here is restraint: a keyboard where every button is coloured
communicates nothing, so the seat map colours only the seats that mean
something and leaves the free ones alone.
"""

import unittest

from mind.ferry_client import Leg, Sailing
from mind.telegram_client import _uncoloured
from mind.telegram_ui import (
    STYLE_DANGER,
    STYLE_PRIMARY,
    STYLE_SUCCESS,
    build_held_keyboard,
    build_seat_map_keyboard,
    build_seats_confirm_keyboard,
    button,
    strip_styles,
)


ALLOWED = {"primary", "success", "danger"}


def boat(free=(1, 2, 3, 4, 5), taken=(6, 7)):
    return Sailing(
        fare=65.0,
        legs=(
            Leg(
                schedule_id="1",
                route="RTL109",
                departs="20260821083000",
                arrives="20260821092000",
                from_name="A",
                to_name="B",
                from_code="104",
                to_code="110",
                free_seats=tuple(free),
                taken_seats=tuple(taken),
            ),
        ),
    )


def styles_in(markup) -> list:
    found = []
    for row in markup["inline_keyboard"]:
        for item in row:
            if isinstance(item, dict) and "style" in item:
                found.append(item["style"])
    return found


class TheVocabulary(unittest.TestCase):
    def test_only_the_three_telegram_accepts_are_named(self):
        self.assertEqual({STYLE_PRIMARY, STYLE_SUCCESS, STYLE_DANGER}, ALLOWED)

    def test_a_plain_button_carries_no_style_at_all(self):
        # Absent rather than empty: Telegram parses the field if it is there.
        made = button("Menu", "m")
        self.assertNotIn("style", made)

    def test_a_coloured_button_carries_one(self):
        self.assertEqual(button("Pay", "p", STYLE_SUCCESS)["style"], STYLE_SUCCESS)


class TheSeatMap(unittest.TestCase):
    def test_a_taken_seat_is_red(self):
        markup = build_seat_map_keyboard(boat(), 0)
        reds = [
            item
            for row in markup["inline_keyboard"]
            for item in row
            if item.get("style") == STYLE_DANGER
        ]
        self.assertEqual(len(reds), 2)
        self.assertTrue(all(item["text"] == "✕" for item in reds))

    def test_a_picked_seat_is_green(self):
        markup = build_seat_map_keyboard(boat(), 0, picked=[2, 3])
        greens = [
            item
            for row in markup["inline_keyboard"]
            for item in row
            if item.get("style") == STYLE_SUCCESS
        ]
        self.assertEqual({item["text"] for item in greens}, {"✓2", "✓3"})

    def test_a_free_seat_is_left_alone(self):
        # The restraint that makes the other two readable.
        markup = build_seat_map_keyboard(boat(), 0, picked=[2])
        free = [
            item
            for row in markup["inline_keyboard"]
            for item in row
            if item.get("text") in {"1", "4", "5"}
        ]
        self.assertTrue(free)
        self.assertTrue(all("style" not in item for item in free))

    def test_a_taken_seat_stays_untappable(self):
        # Colour is not permission: red seats must still go nowhere.
        markup = build_seat_map_keyboard(boat(), 0)
        for row in markup["inline_keyboard"]:
            for item in row:
                if item.get("style") == STYLE_DANGER:
                    self.assertNotIn(".", item["callback_data"])

    def test_every_style_used_is_one_telegram_takes(self):
        markup = build_seat_map_keyboard(boat(), 0, picked=[2])
        self.assertTrue(set(styles_in(markup)) <= ALLOWED)


class TheActions(unittest.TestCase):
    def test_holding_is_the_coloured_button(self):
        markup = build_seats_confirm_keyboard(0, [[11, 12]])
        self.assertEqual(styles_in(markup), [STYLE_PRIMARY])

    def test_paying_is_the_coloured_button(self):
        markup = build_held_keyboard(0, [11, 12])
        self.assertEqual(styles_in(markup), [STYLE_SUCCESS])

    def test_nothing_is_coloured_when_paying_is_not_offered(self):
        markup = build_held_keyboard(0, [11], can_pay=False)
        self.assertEqual(styles_in(markup), [])


class TheFallback(unittest.TestCase):
    """An older Telegram refuses the message rather than ignoring the field."""

    def test_styles_come_out_of_a_payload(self):
        payload = {
            "chat_id": 1,
            "reply_markup": {
                "inline_keyboard": [[{"text": "a", "callback_data": "x", "style": "primary"}]]
            },
        }
        plain = _uncoloured(payload)
        self.assertIsNotNone(plain)
        self.assertNotIn("style", plain["reply_markup"]["inline_keyboard"][0][0])
        # Everything else is left exactly as it was.
        self.assertEqual(plain["chat_id"], 1)
        self.assertEqual(plain["reply_markup"]["inline_keyboard"][0][0]["text"], "a")

    def test_the_original_is_not_modified(self):
        payload = {
            "reply_markup": {
                "inline_keyboard": [[{"text": "a", "callback_data": "x", "style": "danger"}]]
            }
        }
        _uncoloured(payload)
        self.assertIn("style", payload["reply_markup"]["inline_keyboard"][0][0])

    def test_a_payload_with_no_colour_is_not_worth_retrying(self):
        # None means "nothing to strip", so the client raises instead of
        # sending the identical request a second time.
        self.assertIsNone(_uncoloured({"reply_markup": {"inline_keyboard": [[{"text": "a"}]]}}))
        self.assertIsNone(_uncoloured({"chat_id": 1}))
        self.assertIsNone(_uncoloured(None))

    def test_a_refusal_over_colour_is_retried_without_it(self):
        from mind.telegram_client import TelegramClient, TelegramError

        bot = TelegramClient("1:token")
        seen = []

        def refuse_colour(method, payload=None):
            seen.append(payload)
            if _uncoloured(payload) is not None:
                raise TelegramError("Telegram rejected sendMessage: invalid button style")
            return {"message_id": 7}

        bot._request = refuse_colour
        payload = {
            "reply_markup": {
                "inline_keyboard": [[{"text": "a", "callback_data": "x", "style": "primary"}]]
            }
        }
        self.assertEqual(bot._call("sendMessage", payload), {"message_id": 7})
        self.assertEqual(len(seen), 2)
        self.assertNotIn("style", seen[1]["reply_markup"]["inline_keyboard"][0][0])

    def test_any_other_refusal_is_not_swallowed(self):
        from mind.telegram_client import TelegramClient, TelegramError

        bot = TelegramClient("1:token")

        def refuse(method, payload=None):
            raise TelegramError("Telegram rejected sendMessage: chat not found")

        bot._request = refuse
        with self.assertRaises(TelegramError):
            bot._call("sendMessage", {"reply_markup": {"inline_keyboard": [[
                {"text": "a", "callback_data": "x", "style": "primary"}
            ]]}})

    def test_the_ui_helper_strips_a_whole_keyboard(self):
        markup = build_seat_map_keyboard(boat(), 0, picked=[2])
        self.assertTrue(styles_in(markup))
        self.assertEqual(styles_in(strip_styles(markup)), [])


if __name__ == "__main__":
    unittest.main()
