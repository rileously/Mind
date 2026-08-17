"""The Explorer "Send to Telegram" entry, and where a right-click sends to."""

import sys
import unittest
from pathlib import Path
from unittest import mock

from mind import shell_menu
from mind.telegram_send import SendError, target_chat


@unittest.skipUnless(sys.platform == "win32", "registry is Windows-only")
class RegistrationTests(unittest.TestCase):
    """Writes under HKCU, which needs no privileges, and cleans up after itself."""

    def setUp(self):
        self.was_registered = shell_menu.is_registered()
        self.previous_command = shell_menu.registered_command()

    def tearDown(self):
        shell_menu.unregister()
        if self.was_registered and self.previous_command:
            # Put a pre-existing entry back rather than leaving the machine changed.
            import winreg

            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, shell_menu.ALL_FILES_KEY) as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, shell_menu.MENU_LABEL)
            with winreg.CreateKey(
                winreg.HKEY_CURRENT_USER,
                f"{shell_menu.ALL_FILES_KEY}\\{shell_menu.COMMAND_SUBKEY}",
            ) as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, self.previous_command)

    def test_register_then_unregister_leaves_nothing_behind(self):
        shell_menu.register(Path(r"C:\Programs\Mind\Mind.exe"))
        self.assertTrue(shell_menu.is_registered())
        shell_menu.unregister()
        self.assertFalse(shell_menu.is_registered())

    def test_the_command_points_at_the_executable_with_the_send_flag(self):
        shell_menu.register(Path(r"C:\Programs\Mind\Mind.exe"))
        command = shell_menu.registered_command()
        self.assertIn("--telegram-send", command)
        self.assertIn(r"C:\Programs\Mind\Mind.exe", command)
        # Quoted, or a path containing spaces would arrive as several arguments.
        self.assertIn('"%1"', command)

    def test_registering_again_updates_a_stale_path(self):
        # The first-run installer moves the executable; the entry has to follow.
        shell_menu.register(Path(r"C:\Old\Mind.exe"))
        shell_menu.register(Path(r"C:\New\Mind.exe"))
        self.assertIn(r"C:\New\Mind.exe", shell_menu.registered_command())
        self.assertNotIn(r"C:\Old\Mind.exe", shell_menu.registered_command())

    def test_unregistering_when_absent_is_not_an_error(self):
        shell_menu.unregister()
        shell_menu.unregister()

    def test_nothing_is_written_when_running_from_source(self):
        # Registering from a source checkout would point Explorer at an
        # interpreter path that means nothing to the user.
        with mock.patch.object(shell_menu, "is_supported", return_value=False):
            shell_menu.apply(True)
        self.assertFalse(shell_menu.is_registered())


class TargetChatTests(unittest.TestCase):
    def test_defaults_to_the_only_allowed_chat(self):
        self.assertEqual(target_chat({"telegram_allowed_chat_ids": [42]}), 42)

    def test_a_chosen_chat_is_used_when_it_is_allowed(self):
        config = {"telegram_allowed_chat_ids": [1, 2, 3], "telegram_send_chat_id": "3"}
        self.assertEqual(target_chat(config), 3)

    def test_a_chosen_chat_outside_the_allowlist_is_ignored(self):
        # Otherwise this setting would be a way around the allowlist.
        config = {"telegram_allowed_chat_ids": [1, 2], "telegram_send_chat_id": "999"}
        self.assertEqual(target_chat(config), 1)

    def test_no_allowed_chat_refuses_rather_than_guessing(self):
        with self.assertRaises(SendError):
            target_chat({"telegram_allowed_chat_ids": []})


if __name__ == "__main__":
    unittest.main()
