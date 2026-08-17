"""The menu, its buttons, and the command list Telegram publishes.

The rule these tests protect: a button is only offered when the setting behind it
is on, and a tap carries an index that keeps meaning the same action even in a
message sent before the settings changed.
"""

import unittest

from mind.telegram_client import MAX_COPY_TEXT_CHARS, escape_html, split_for_telegram
from mind.telegram_ui import (
    CB_MEDIA,
    CB_MENU,
    MEDIA_KEYS,
    MENU_ACTIONS,
    bot_commands,
    build_copy_keyboard,
    build_main_menu,
    build_media_keyboard,
    build_power_keyboard,
    commands_signature,
    media_key_at,
    menu_action_at,
    menu_text,
)


def buttons(markup: dict) -> list[dict]:
    return [button for row in markup["inline_keyboard"] for button in row]


def labels(markup: dict) -> list[str]:
    return [button["text"] for button in buttons(markup)]


ALL_ON = {
    "telegram_files_enabled": True,
    "telegram_control_enabled": True,
    "telegram_power_enabled": True,
}


class MainMenuTests(unittest.TestCase):
    def test_a_switched_off_feature_is_not_offered(self):
        # A button that answers with a refusal is worse than no button.
        menu = build_main_menu({})
        self.assertFalse(any("Files" in label for label in labels(menu)))
        self.assertFalse(any("Screenshot" in label for label in labels(menu)))
        # The two that need nothing are always there.
        self.assertTrue(any("Clipboard" in label for label in labels(menu)))
        self.assertTrue(any("Commands" in label for label in labels(menu)))

    def test_everything_is_offered_when_everything_is_on(self):
        menu = build_main_menu(ALL_ON)
        self.assertEqual(len(buttons(menu)), len(MENU_ACTIONS))

    def test_the_menu_is_two_columns(self):
        rows = build_main_menu(ALL_ON)["inline_keyboard"]
        self.assertTrue(all(len(row) <= 2 for row in rows))

    def test_indexes_survive_a_setting_being_switched_off(self):
        # A menu sent while files were on must not repoint at another action
        # once they are off, or a tap would run something the user did not pick.
        full = build_main_menu(ALL_ON)
        clip = next(b for b in buttons(full) if "Clipboard" in b["text"])
        reduced = build_main_menu({})
        still_clip = next(b for b in buttons(reduced) if "Clipboard" in b["text"])
        self.assertEqual(clip["callback_data"], still_clip["callback_data"])

    def test_every_button_carries_a_menu_action(self):
        for button in buttons(build_main_menu(ALL_ON)):
            action, _, raw = button["callback_data"].partition(":")
            self.assertEqual(action, CB_MENU)
            self.assertIsNotNone(menu_action_at(int(raw)))

    def test_an_index_from_a_future_version_is_refused_rather_than_guessed(self):
        self.assertIsNone(menu_action_at(len(MENU_ACTIONS)))
        self.assertIsNone(menu_action_at(None))
        self.assertIsNone(menu_action_at(-1))

    def test_payloads_stay_inside_telegrams_64_byte_cap(self):
        for button in buttons(build_main_menu(ALL_ON)):
            self.assertLessEqual(len(button["callback_data"].encode("utf-8")), 64)

    def test_the_text_says_which_features_are_off(self):
        # Otherwise a short menu looks broken rather than configured.
        self.assertIn("File access is off", menu_text({}))
        self.assertNotIn("File access is off", menu_text(ALL_ON))

    def test_the_host_name_is_escaped_into_the_html(self):
        self.assertIn("&lt;pc&gt;", menu_text(ALL_ON, "<pc>"))


class MediaKeyboardTests(unittest.TestCase):
    def test_the_transport_keys_are_one_row(self):
        rows = build_media_keyboard()["inline_keyboard"]
        self.assertEqual(len(rows[0]), len(MEDIA_KEYS))

    def test_each_key_maps_to_something_press_media_key_understands(self):
        for index, (_label, key) in enumerate(MEDIA_KEYS):
            self.assertEqual(media_key_at(index), key)

    def test_an_unknown_index_presses_nothing(self):
        self.assertEqual(media_key_at(len(MEDIA_KEYS)), "")
        self.assertEqual(media_key_at(None), "")

    def test_buttons_carry_the_media_action(self):
        for button in build_media_keyboard()["inline_keyboard"][0]:
            self.assertTrue(button["callback_data"].startswith(f"{CB_MEDIA}:"))


class CopyButtonTests(unittest.TestCase):
    def test_short_text_gets_a_copy_button(self):
        markup = build_copy_keyboard("ssh-rsa AAAA")
        self.assertIsNotNone(markup)
        self.assertEqual(buttons(markup)[0]["copy_text"], {"text": "ssh-rsa AAAA"})

    def test_text_over_the_cap_gets_no_button(self):
        # copy_text carries its payload in the button, so Telegram would reject it.
        self.assertIsNone(build_copy_keyboard("x" * (MAX_COPY_TEXT_CHARS + 1)))

    def test_an_empty_clipboard_gets_no_button(self):
        self.assertIsNone(build_copy_keyboard("   "))


class PowerKeyboardTests(unittest.TestCase):
    def test_the_confirmation_names_the_action_it_will_take(self):
        shutdown = labels(build_power_keyboard("shutdown", "w:1"))
        self.assertTrue(any("shut down" in label.lower() for label in shutdown))
        restart = labels(build_power_keyboard("restart", "w:2"))
        self.assertTrue(any("restart" in label.lower() for label in restart))

    def test_cancel_is_offered_beside_it(self):
        self.assertTrue(
            any("cancel" in label.lower() for label in labels(build_power_keyboard("shutdown", "w:1")))
        )


class PublishedCommandTests(unittest.TestCase):
    def test_only_the_commands_the_settings_allow_are_published(self):
        names = [entry["command"] for entry in bot_commands({})]
        self.assertIn("menu", names)
        self.assertNotIn("files", names)
        self.assertNotIn("lock", names)
        self.assertNotIn("shutdown", names)

        names = [entry["command"] for entry in bot_commands(ALL_ON)]
        self.assertIn("files", names)
        self.assertIn("lock", names)
        self.assertIn("shutdown", names)

    def test_a_users_own_commands_are_published_after_the_built_in_ones(self):
        commands = [{"trigger": "fix", "type": "ai", "prompt": "Fix the grammar"}]
        published = bot_commands(ALL_ON, commands)
        self.assertEqual(published[-1], {"command": "fix", "description": "Fix the grammar"})

    def test_a_trigger_telegram_would_reject_is_left_out(self):
        # Telegram only accepts lowercase letters, digits and underscores.
        commands = [
            {"trigger": "make it nice", "type": "ai", "prompt": "x"},
            {"trigger": "fix!", "type": "ai", "prompt": "x"},
            {"trigger": "перевод", "type": "ai", "prompt": "x"},
            {"trigger": "ok_2", "type": "ai", "prompt": "x"},
        ]
        names = [entry["command"] for entry in bot_commands(ALL_ON, commands)]
        self.assertIn("ok_2", names)
        for rejected in ("make it nice", "fix!", "перевод"):
            self.assertNotIn(rejected, names)

    def test_a_command_never_shadows_a_built_in_one(self):
        commands = [{"trigger": "menu", "type": "ai", "prompt": "Something else"}]
        published = bot_commands(ALL_ON, commands)
        menu_entries = [e for e in published if e["command"] == "menu"]
        self.assertEqual(len(menu_entries), 1)
        self.assertEqual(menu_entries[0]["description"], "Show the button menu")

    def test_descriptions_are_shortened_to_what_telegram_accepts(self):
        commands = [{"trigger": "long", "type": "ai", "prompt": "y" * 400}]
        entry = [e for e in bot_commands(ALL_ON, commands) if e["command"] == "long"][0]
        self.assertLessEqual(len(entry["description"]), 100)

    def test_a_shell_command_is_never_published(self):
        # remote_safe_commands keeps these off the bridge; the menu must not
        # advertise what the bridge will refuse to run.
        commands = [{"trigger": "deploy", "type": "replacer-shell", "value": "rm -rf /"}]
        names = [entry["command"] for entry in bot_commands(ALL_ON, commands)]
        self.assertNotIn("deploy", names)

    def test_the_signature_changes_only_when_the_published_list_would(self):
        first = commands_signature(ALL_ON, [{"trigger": "fix", "type": "ai", "prompt": "a"}])
        again = commands_signature(ALL_ON, [{"trigger": "fix", "type": "ai", "prompt": "a"}])
        self.assertEqual(first, again)
        changed = commands_signature(ALL_ON, [{"trigger": "fix", "type": "ai", "prompt": "b"}])
        self.assertNotEqual(first, changed)
        fewer = commands_signature({}, [{"trigger": "fix", "type": "ai", "prompt": "a"}])
        self.assertNotEqual(first, fewer)


class MessageFormattingTests(unittest.TestCase):
    def test_html_escaping_covers_what_would_break_a_message(self):
        self.assertEqual(escape_html("a<b>&c"), "a&lt;b&gt;&amp;c")

    def test_short_text_is_one_piece(self):
        self.assertEqual(split_for_telegram("hello"), ["hello"])

    def test_empty_text_says_so_rather_than_failing(self):
        # Telegram rejects an empty message outright.
        self.assertEqual(split_for_telegram("   "), ["(empty result)"])

    def test_long_text_is_split_at_a_line_break(self):
        body = ("line\n" * 40).rstrip("\n")
        pieces = split_for_telegram(body, limit=20)
        self.assertTrue(all(len(piece) <= 20 for piece in pieces))
        # Nothing is lost, and no piece starts mid-word.
        self.assertEqual("".join(pieces).replace("\n", ""), body.replace("\n", ""))
        self.assertTrue(all(piece.startswith("line") for piece in pieces))

    def test_a_single_long_run_is_still_split(self):
        pieces = split_for_telegram("x" * 45, limit=20)
        self.assertEqual([len(piece) for piece in pieces], [20, 20, 5])


if __name__ == "__main__":
    unittest.main()
