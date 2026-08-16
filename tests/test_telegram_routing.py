"""The bridge is reachable by anyone who finds the bot, so these rules matter."""

import unittest

from mind.telegram_routing import (
    CommandRefused,
    is_authorized,
    is_remote_safe,
    parse_allowed_chat_ids,
    parse_message,
    remote_safe_commands,
    select_command,
)


COMMANDS = [
    {"trigger": "fix", "type": "ai", "prompt": "Fix grammar."},
    {"trigger": "sig", "type": "replacer-text", "value": "Kind regards"},
    {"trigger": "ip", "type": "replacer-shell", "value": "curl -s https://ifconfig.me"},
    {"trigger": "off", "type": "ai", "prompt": "Disabled.", "enabled": False},
]


class AuthorisationTests(unittest.TestCase):
    def test_empty_allowlist_authorises_nobody(self):
        # "Not configured" must never mean "open to everyone".
        self.assertFalse(is_authorized(12345, parse_allowed_chat_ids([])))
        self.assertFalse(is_authorized(12345, parse_allowed_chat_ids(None)))
        self.assertFalse(is_authorized(12345, parse_allowed_chat_ids("")))

    def test_listed_chat_is_allowed_and_others_are_not(self):
        allowed = parse_allowed_chat_ids([12345, 67890])
        self.assertTrue(is_authorized(12345, allowed))
        self.assertFalse(is_authorized(11111, allowed))

    def test_ids_may_be_typed_as_text(self):
        allowed = parse_allowed_chat_ids("12345, 67890  -100200")
        self.assertEqual(allowed, frozenset({12345, 67890, -100200}))

    def test_junk_entries_are_dropped_not_trusted(self):
        allowed = parse_allowed_chat_ids(["12345", "not-an-id", "", None])
        self.assertEqual(allowed, frozenset({12345}))

    def test_unparseable_sender_is_refused(self):
        allowed = parse_allowed_chat_ids([12345])
        self.assertFalse(is_authorized("abc", allowed))
        self.assertFalse(is_authorized(None, allowed))

    def test_a_true_flag_is_not_a_chat_id(self):
        self.assertEqual(parse_allowed_chat_ids(True), frozenset())


class ShellRefusalTests(unittest.TestCase):
    def test_shell_commands_are_refused(self):
        # A shell replacer over a chat bot is remote code execution.
        with self.assertRaises(CommandRefused):
            select_command(parse_message("/ip"), COMMANDS)

    def test_shell_commands_are_not_advertised(self):
        triggers = {c["trigger"] for c in remote_safe_commands(COMMANDS)}
        self.assertNotIn("ip", triggers)
        self.assertEqual(triggers, {"fix", "sig"})

    def test_ai_and_text_commands_are_allowed(self):
        self.assertTrue(is_remote_safe(COMMANDS[0]))
        self.assertTrue(is_remote_safe(COMMANDS[1]))
        self.assertFalse(is_remote_safe(COMMANDS[2]))

    def test_an_unknown_type_is_not_assumed_safe(self):
        self.assertFalse(is_remote_safe({"trigger": "x", "type": "future-thing"}))


class ParsingTests(unittest.TestCase):
    def test_slash_and_prefix_forms_both_work(self):
        self.assertEqual(parse_message("/fix hello").trigger, "fix")
        self.assertEqual(parse_message("?fix hello").trigger, "fix")
        self.assertEqual(parse_message("/fix hello").text, "hello")

    def test_group_chat_bot_suffix_is_stripped(self):
        self.assertEqual(parse_message("/fix@MindBot hello").trigger, "fix")

    def test_plain_text_has_no_trigger(self):
        request = parse_message("just some words")
        self.assertIsNone(request.trigger)
        self.assertEqual(request.text, "just some words")

    def test_empty_message_is_harmless(self):
        self.assertIsNone(parse_message("").trigger)
        self.assertIsNone(parse_message("   ").trigger)

    def test_a_custom_prefix_is_respected(self):
        self.assertEqual(parse_message("!fix hello", prefix="!").trigger, "fix")


class SelectionTests(unittest.TestCase):
    def test_plain_text_uses_the_default_command(self):
        command = select_command(parse_message("hello"), COMMANDS, default_trigger="fix")
        self.assertEqual(command["trigger"], "fix")

    def test_no_default_means_nothing_runs(self):
        self.assertIsNone(select_command(parse_message("hello"), COMMANDS))

    def test_disabled_commands_do_not_run(self):
        self.assertIsNone(select_command(parse_message("/off text"), COMMANDS))

    def test_unknown_trigger_returns_nothing(self):
        self.assertIsNone(select_command(parse_message("/nope text"), COMMANDS))

    def test_a_shell_default_is_still_refused(self):
        # The default is configured locally, but it must not become a way to
        # reach a shell command remotely.
        with self.assertRaises(CommandRefused):
            select_command(parse_message("hello"), COMMANDS, default_trigger="ip")


if __name__ == "__main__":
    unittest.main()
