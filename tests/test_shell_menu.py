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
        # The packaged handler is decided separately, in PackagedHandlerTests.
        # Left real, these would install and remove a package on the machine
        # running the tests.
        for name in ("register_package", "unregister_package"):
            patcher = mock.patch.object(
                shell_menu, name, return_value=False if name == "register_package" else None
            )
            self.addCleanup(patcher.stop)
            patcher.start()
        # Start from nothing registered, whatever this machine's own setting is;
        # tearDown puts the real entry back.
        shell_menu.unregister()

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
            self.assertFalse(shell_menu.apply(True))
        self.assertFalse(shell_menu.is_registered())

    def test_applying_reports_that_the_entry_is_there(self):
        # The caller has to be able to tell the user why a menu entry they
        # switched on is nowhere to be found.
        with mock.patch.object(shell_menu, "is_supported", return_value=True):
            self.assertTrue(shell_menu.apply(True, Path(r"C:\Programs\Mind\Mind.exe")))
            self.assertTrue(shell_menu.is_registered())
            self.assertTrue(shell_menu.apply(False))
            self.assertFalse(shell_menu.is_registered())

    def test_switching_off_succeeds_when_there_was_nothing_to_remove(self):
        with mock.patch.object(shell_menu, "is_supported", return_value=False):
            self.assertTrue(shell_menu.apply(False))


class PackagedHandlerTests(unittest.TestCase):
    """Which of the two menu mechanisms Mind ends up using, and when."""

    def test_the_package_is_preferred_and_replaces_the_registry_verb(self):
        # Both at once would put the same command in the menu twice.
        with (
            mock.patch.object(shell_menu, "is_supported", return_value=True),
            mock.patch.object(shell_menu, "register_package", return_value=True),
            mock.patch.object(shell_menu, "register") as register,
            mock.patch.object(shell_menu, "unregister") as unregister,
        ):
            self.assertTrue(shell_menu.apply(True, Path(r"C:\Programs\Mind\Mind.exe")))
        unregister.assert_called_once()
        register.assert_not_called()

    def test_the_registry_verb_is_used_when_the_package_is_refused(self):
        # An unsigned build, or a machine that does not trust the certificate,
        # still gets an entry rather than nothing at all.
        with (
            mock.patch.object(shell_menu, "is_supported", return_value=True),
            mock.patch.object(shell_menu, "register_package", return_value=False),
            mock.patch.object(shell_menu, "register") as register,
            mock.patch.object(shell_menu, "is_registered", return_value=True),
            mock.patch.object(shell_menu, "unregister_package") as unregister_package,
        ):
            self.assertTrue(shell_menu.apply(True, Path(r"C:\Programs\Mind\Mind.exe")))
        register.assert_called_once()
        unregister_package.assert_called_once()

    def test_switching_off_removes_both_mechanisms(self):
        with (
            mock.patch.object(shell_menu, "is_supported", return_value=True),
            mock.patch.object(shell_menu, "unregister") as unregister,
            mock.patch.object(shell_menu, "unregister_package") as unregister_package,
        ):
            self.assertTrue(shell_menu.apply(False))
        unregister.assert_called_once()
        unregister_package.assert_called_once()

    def test_a_package_from_an_older_mind_is_not_treated_as_current(self):
        # It would keep answering right-clicks with the previous build's handler.
        stale = f"{shell_menu.PACKAGE_NAME}_0.0.1.0_neutral__abcdefghijklm"
        with mock.patch.object(shell_menu, "registered_package_full_name", return_value=stale):
            self.assertFalse(shell_menu.package_is_current())
        current = (
            f"{shell_menu.PACKAGE_NAME}_{shell_menu.PACKAGE_VERSION}_neutral__abcdefghijklm"
        )
        with mock.patch.object(shell_menu, "registered_package_full_name", return_value=current):
            self.assertTrue(shell_menu.package_is_current())

    def test_no_package_registered_is_not_current(self):
        with mock.patch.object(shell_menu, "registered_package_full_name", return_value=""):
            self.assertFalse(shell_menu.package_is_current())

    def test_the_handler_is_not_looked_for_where_it_was_not_built(self):
        with mock.patch.object(shell_menu, "PACKAGE_FILE", "MindShellMenu.absent"):
            self.assertIsNone(shell_menu.bundled_handler_dir())

    def test_registering_the_package_needs_the_built_handler(self):
        with mock.patch.object(shell_menu, "bundled_handler_dir", return_value=None):
            self.assertFalse(shell_menu.register_package(Path(r"C:\Programs\Mind\Mind.exe")))

    def test_paths_with_quotes_cannot_break_out_of_the_powershell_argument(self):
        self.assertEqual(shell_menu._quote(r"C:\a'b"), "'C:\\a''b'")

    @unittest.skipUnless(sys.platform == "win32", "the compact menu is Windows-only")
    def test_a_registered_package_is_in_the_menu_a_right_click_opens(self):
        with mock.patch.object(shell_menu, "package_is_current", return_value=True):
            self.assertTrue(shell_menu.in_compact_menu())

    @unittest.skipUnless(sys.platform == "win32", "the compact menu is Windows-only")
    def test_without_the_package_it_depends_on_the_compact_menu_being_in_use(self):
        with (
            mock.patch.object(shell_menu, "package_is_current", return_value=False),
            mock.patch.object(shell_menu, "_compact_menu_in_use", return_value=True),
        ):
            self.assertFalse(shell_menu.in_compact_menu())
        with (
            mock.patch.object(shell_menu, "package_is_current", return_value=False),
            mock.patch.object(shell_menu, "_compact_menu_in_use", return_value=False),
        ):
            self.assertTrue(shell_menu.in_compact_menu())


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
