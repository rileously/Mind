"""File browsing over a chat bot must never escape its configured root."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from mind.telegram_files import (
    Entry,
    PathRefused,
    entry_at,
    list_directory,
    resolve_root,
    resolve_within_root,
    unique_destination,
)


class ContainmentTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp()).resolve()
        self.root = self.base / "shared"
        (self.root / "sub").mkdir(parents=True)
        (self.root / "sub" / "note.txt").write_text("inside", encoding="utf-8")
        self.secret = self.base / "private"
        self.secret.mkdir()
        (self.secret / "secrets.txt").write_text("out of bounds", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_navigating_into_a_child_is_allowed(self):
        result = resolve_within_root(self.root, self.root, "sub")
        self.assertEqual(result, self.root / "sub")

    def test_the_root_itself_is_allowed(self):
        self.assertEqual(resolve_within_root(self.root, self.root, ""), self.root)

    def test_going_up_from_the_root_is_refused(self):
        with self.assertRaises(PathRefused):
            resolve_within_root(self.root, self.root, "..")

    def test_traversal_sequences_are_refused(self):
        for attempt in ("../private", "../../private", "sub/../../private"):
            with self.assertRaises(PathRefused, msg=attempt):
                resolve_within_root(self.root, self.root, attempt)

    def test_absolute_paths_outside_the_root_are_refused(self):
        with self.assertRaises(PathRefused):
            resolve_within_root(self.root, self.root, str(self.secret))

    def test_a_sibling_with_a_shared_prefix_is_refused(self):
        # "shared-other" starts with "shared"; a string comparison would let it through.
        sibling = self.base / "shared-other"
        sibling.mkdir()
        with self.assertRaises(PathRefused):
            resolve_within_root(self.root, self.root, str(sibling))

    @unittest.skipUnless(sys.platform == "win32", "Windows path forms")
    def test_windows_separators_and_case_are_handled(self):
        result = resolve_within_root(self.root, self.root, "SUB")
        self.assertEqual(result.resolve(), (self.root / "sub").resolve())

    def test_symlink_pointing_out_of_the_root_is_refused(self):
        link = self.root / "escape"
        try:
            os.symlink(self.secret, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks need privileges on this machine")
        with self.assertRaises(PathRefused):
            resolve_within_root(self.root, self.root, "escape")

    def test_going_up_from_a_subfolder_stays_inside(self):
        result = resolve_within_root(self.root, self.root / "sub", "..")
        self.assertEqual(result, self.root)


class ListingTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        (self.root / "bravo").mkdir()
        (self.root / "alpha").mkdir()
        (self.root / "zeta.txt").write_text("z", encoding="utf-8")
        (self.root / "apple.txt").write_text("a", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_folders_come_first_then_files_alphabetically(self):
        names = [e.name for e in list_directory(self.root)]
        self.assertEqual(names, ["alpha", "bravo", "apple.txt", "zeta.txt"])

    def test_missing_folder_raises_rather_than_returning_nothing(self):
        with self.assertRaises(PathRefused):
            list_directory(self.root / "does-not-exist")


class SelectionTests(unittest.TestCase):
    ENTRIES = [
        Entry("docs", True, 0),
        Entry("report.pdf", False, 1234),
    ]

    def test_selection_by_number(self):
        self.assertEqual(entry_at(self.ENTRIES, "2").name, "report.pdf")

    def test_selection_by_name(self):
        self.assertEqual(entry_at(self.ENTRIES, "docs").name, "docs")

    def test_out_of_range_returns_nothing(self):
        self.assertIsNone(entry_at(self.ENTRIES, "9"))
        self.assertIsNone(entry_at(self.ENTRIES, "0"))
        self.assertIsNone(entry_at(self.ENTRIES, ""))


class DestinationTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp()).resolve()

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_incoming_files_never_overwrite(self):
        first = unique_destination(self.folder, "photo.jpg")
        first.write_text("one", encoding="utf-8")
        second = unique_destination(self.folder, "photo.jpg")
        self.assertNotEqual(first, second)
        self.assertEqual(second.name, "photo (2).jpg")

    def test_a_path_in_the_filename_cannot_escape_the_inbox(self):
        # Telegram supplies the name, so it is untrusted input.
        destination = unique_destination(self.folder, r"..\..\evil.exe")
        self.assertEqual(destination.parent, self.folder)
        self.assertEqual(destination.name, "evil.exe")


class RootTests(unittest.TestCase):
    def test_blank_configuration_falls_back_to_the_home_folder(self):
        self.assertEqual(resolve_root(""), Path.home().resolve())
        self.assertEqual(resolve_root(None), Path.home().resolve())


if __name__ == "__main__":
    unittest.main()
