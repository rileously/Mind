"""Reaching the copy of Mind already running.

Closing Mind hides its window, and Windows keeps the handle. A second launch
that restored that handle itself produced the bug this file exists for: a
window with a correct title bar and nothing inside it, because Qt still
believed the widget was hidden and never laid its children out.

So what is tested is the difference between the two ways of showing a hidden
window - the one that leaves it blank, and the one Mind now uses.
"""

import ctypes
import unittest
from ctypes import wintypes

from mind.single_instance import (
    SHOW_MESSAGE_NAME,
    ask_running_instance_to_show,
    find_window,
    show_message_id,
)


SW_RESTORE = 9


class MessageTests(unittest.TestCase):
    def test_the_message_number_is_the_same_every_time_it_is_asked_for(self):
        # Two copies of Mind register the same name and must agree on the
        # number, which is the whole mechanism.
        self.assertTrue(show_message_id())
        self.assertEqual(show_message_id(), show_message_id())

    def test_it_is_registered_under_a_name_only_mind_would_use(self):
        registered = ctypes.windll.user32.RegisterWindowMessageW(SHOW_MESSAGE_NAME)
        self.assertEqual(int(registered), show_message_id())

    def test_asking_when_nothing_is_running_says_so_rather_than_raising(self):
        # No window by that title exists in a test run, so there is nobody to
        # ask - and that has to be an answer, not a crash.
        if find_window():
            self.skipTest("a copy of Mind is running on this machine")
        self.assertFalse(ask_running_instance_to_show())


class HiddenWindowTests(unittest.TestCase):
    """The bug itself, in the smallest form that still shows it."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def build_hidden_window(self, title: str):
        from PySide6.QtWidgets import QLabel, QMainWindow

        window = QMainWindow()
        window.setWindowTitle(title)
        window.setCentralWidget(QLabel("the contents of the window"))
        window.resize(320, 200)
        window.show()
        self.app.processEvents()
        window.hide()  # what closing Mind does
        self.app.processEvents()
        self.addCleanup(window.deleteLater)
        return window

    def handle_of(self, title: str) -> int:
        user32 = ctypes.windll.user32
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowW.restype = wintypes.HWND
        return int(user32.FindWindowW(None, title) or 0)

    def test_restoring_the_handle_from_outside_leaves_the_window_empty(self):
        # The bug: the frame comes back, the contents do not. If this ever
        # stops being true, the fix below is no longer needed.
        window = self.build_hidden_window("MindHiddenWindowTest-poked")
        handle = self.handle_of("MindHiddenWindowTest-poked")
        self.assertTrue(handle)
        ctypes.windll.user32.ShowWindow(wintypes.HWND(handle), SW_RESTORE)
        self.app.processEvents()
        self.assertTrue(ctypes.windll.user32.IsWindowVisible(wintypes.HWND(handle)))
        self.assertFalse(window.isVisible())
        self.assertFalse(window.centralWidget().isVisible())

    def test_showing_it_through_qt_brings_the_contents_back_with_it(self):
        window = self.build_hidden_window("MindHiddenWindowTest-asked")
        window.show()
        window.showNormal()
        self.app.processEvents()
        self.assertTrue(window.isVisible())
        self.assertTrue(window.centralWidget().isVisible())

    def test_a_hidden_window_still_receives_what_is_posted_to_it(self):
        # Why asking works at all: Mind sits hidden in the tray, and the
        # message has to arrive there.
        arrived = []

        from PySide6.QtWidgets import QMainWindow

        class Listener(QMainWindow):
            def nativeEvent(self, event_type, message):
                native = wintypes.MSG.from_address(int(message))
                if native.message == show_message_id():
                    arrived.append(True)
                    return True, 0
                return super().nativeEvent(event_type, message)

        window = Listener()
        window.setWindowTitle("MindHiddenWindowTest-listening")
        window.show()
        self.app.processEvents()
        window.hide()
        self.app.processEvents()
        handle = self.handle_of("MindHiddenWindowTest-listening")
        self.addCleanup(window.deleteLater)
        self.assertTrue(handle)
        ctypes.windll.user32.PostMessageW(
            wintypes.HWND(handle), show_message_id(), 0, 0
        )
        for _ in range(20):
            self.app.processEvents()
        self.assertTrue(arrived)


if __name__ == "__main__":
    unittest.main()
