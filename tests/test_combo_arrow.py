"""The arrow that says a field is a list.

Styling QComboBox::drop-down stops Qt drawing its own arrow, and Mind's theme
does style it. Nothing in a stylesheet puts one back - a border-drawn triangle
renders as a square, and Qt reads image: url() from a file rather than from
data - so every picker in Mind was drawn as a plain box.

That is a cosmetic fault everywhere except one place, where it was a functional
one: the application field on a watcher held every program on the PC and looked
like an empty text box. These tests hold the arrow in place, and hold the list
behind it.
"""

import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path


class ChevronTests(unittest.TestCase):
    """The arrow is painted to a file, because QSS cannot draw one."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # Painted into the data folder, so the tests get one of their own.
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.previous = os.environ.get("MIND_DATA_DIR")
        os.environ["MIND_DATA_DIR"] = self.temp.name
        self.addCleanup(self.restore)

    def restore(self):
        if self.previous is None:
            os.environ.pop("MIND_DATA_DIR", None)
        else:
            os.environ["MIND_DATA_DIR"] = self.previous

    def test_an_arrow_is_painted_and_kept(self):
        from mind.theme import chevron_file

        path = chevron_file("#9B9B9B")
        self.assertTrue(path)
        self.assertTrue(Path(path).is_file())
        self.assertGreater(Path(path).stat().st_size, 0)

    def test_painting_it_twice_reuses_the_one_on_disk(self):
        from mind.theme import chevron_file

        first = chevron_file("#9B9B9B")
        written = Path(first).stat().st_mtime_ns
        self.assertEqual(chevron_file("#9B9B9B"), first)
        self.assertEqual(Path(first).stat().st_mtime_ns, written)

    def test_each_colour_gets_its_own(self):
        # Light and dark do not share a muted grey, and one repainting over the
        # other would leave whichever theme came second with the wrong arrow.
        from mind.theme import chevron_file

        self.assertNotEqual(chevron_file("#9B9B9B"), chevron_file("#63748B"))

    def test_the_stylesheet_points_at_it(self):
        from mind.theme import stylesheet

        sheet = stylesheet("dark", "teal")
        self.assertIn("QComboBox::down-arrow", sheet)
        self.assertIn("image: url(", sheet)

    def test_a_folder_that_cannot_be_written_leaves_the_arrow_out(self):
        # A window that cannot be drawn is worse than a picker without an arrow.
        from mind.theme import stylesheet

        os.environ["MIND_DATA_DIR"] = str(Path(self.temp.name) / "nul" / "nope")
        with unittest.mock.patch("mind.theme.QImage.save", return_value=False):
            sheet = stylesheet("dark", "teal")
        self.assertNotIn("QComboBox::down-arrow { image: url(", sheet)
        self.assertIn("QComboBox", sheet)


class WatcherPickerTests(unittest.TestCase):
    """What the application field on a watcher actually holds."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def dialog(self):
        from mind.main_window import WatcherDialog
        from mind.watchers import APP_OPENED, new_watcher

        made = WatcherDialog(new_watcher(APP_OPENED))
        self.addCleanup(made.deleteLater)
        return made

    def test_the_programs_on_this_pc_are_in_the_list(self):
        picker = self.dialog().app_picker
        self.assertGreater(picker.count(), 0)

    def test_the_field_says_it_can_be_opened(self):
        # With no row chosen an editable combo shows nothing at all, and an
        # empty box with no arrow is indistinguishable from a text field.
        picker = self.dialog().app_picker
        self.assertTrue(picker.lineEdit().placeholderText())

    def test_it_stays_typeable_for_a_program_that_is_not_running(self):
        # The point may well be to watch for a game that has not started.
        picker = self.dialog().app_picker
        picker.setCurrentText("some-game.exe")
        self.assertEqual(picker.currentText(), "some-game.exe")


if __name__ == "__main__":
    unittest.main()
