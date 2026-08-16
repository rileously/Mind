"""The first-run install offer must appear exactly once, and only when useful."""

import sys
import unittest
from pathlib import Path

from mind import app_install
from mind.app_install import should_offer_install, source_description


class ShouldOfferInstallTests(unittest.TestCase):
    def setUp(self):
        self._frozen = getattr(sys, "frozen", False)
        self._executable = app_install.current_executable
        self._installed = app_install.installed_executable

    def tearDown(self):
        if self._frozen:
            sys.frozen = True
        elif hasattr(sys, "frozen"):
            del sys.frozen
        app_install.current_executable = self._executable
        app_install.installed_executable = self._installed

    def _pretend(self, running_from: str, installed_at: str = r"C:\Programs\Mind\Mind.exe"):
        sys.frozen = True
        app_install.current_executable = lambda: Path(running_from)
        app_install.installed_executable = lambda: Path(installed_at)

    def test_offered_when_running_from_downloads(self):
        self._pretend(r"C:\Users\Someone\Downloads\Mind.exe")
        self.assertTrue(should_offer_install({}))

    def test_not_offered_once_dismissed(self):
        self._pretend(r"C:\Users\Someone\Downloads\Mind.exe")
        self.assertFalse(should_offer_install({"install_prompt_dismissed": True}))

    def test_not_offered_when_already_installed(self):
        self._pretend(r"C:\Programs\Mind\Mind.exe")
        self.assertFalse(should_offer_install({}))

    def test_not_offered_when_running_from_source(self):
        # A developer running mind_app.pyw must never be asked to install.
        if hasattr(sys, "frozen"):
            del sys.frozen
        self.assertFalse(should_offer_install({}))

    def test_not_offered_on_a_minimized_login_launch(self):
        # Mind starting with Windows must not interrupt with a modal question,
        # and Windows opens dialogs minimized before the session is interactive.
        self._pretend(r"C:\Users\Someone\Downloads\Mind.exe")
        self.assertFalse(should_offer_install({}, minimized=True))

    def test_a_declined_offer_is_not_reopened_by_a_later_launch(self):
        self._pretend(r"C:\Users\Someone\Downloads\Mind.exe")
        self.assertTrue(should_offer_install({}))
        self.assertFalse(should_offer_install({"install_prompt_dismissed": True}))


class SourceDescriptionTests(unittest.TestCase):
    def test_paths_under_home_are_shown_relative(self):
        described = source_description(Path.home() / "Downloads" / "Mind.exe")
        self.assertEqual(described, "Downloads")

    def test_paths_outside_home_are_shown_in_full(self):
        described = source_description(Path(r"D:\Tools\Mind\Mind.exe"))
        self.assertIn("Tools", described)

    def test_deep_paths_are_shortened_for_the_dialog(self):
        # A long path wraps over several lines and buries the actual question.
        deep = Path.home() / "a" / "b" / "c" / "d" / "e" / "f" / "Mind.exe"
        described = source_description(deep)
        self.assertTrue(described.startswith("..."), described)
        self.assertLess(len(described), 40, described)
        self.assertTrue(described.endswith("f"), described)


class InstallGuardTests(unittest.TestCase):
    def test_installing_from_source_is_refused(self):
        if hasattr(sys, "frozen"):
            del sys.frozen
        with self.assertRaises(app_install.InstallError):
            app_install.install_to_programs()


if __name__ == "__main__":
    unittest.main()
