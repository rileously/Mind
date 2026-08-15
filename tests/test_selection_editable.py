from __future__ import annotations

import os
import unittest

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from mind.selection import is_editable_input_target


@unittest.skipUnless(os.name == "nt", "Windows UI inspection is required")
class SelectionEditableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_invalid_and_zero_hwnd_return_false(self):
        self.assertFalse(is_editable_input_target(0))
        self.assertFalse(is_editable_input_target(999999999))

    def test_editable_line_edit_returns_true(self):
        widget = QLineEdit("Editable test text")
        widget.resize(300, 40)
        widget.show()
        widget.setFocus()
        widget.selectAll()
        self.app.processEvents()
        try:
            hwnd = int(widget.winId())
            self.assertTrue(is_editable_input_target(hwnd))
        finally:
            widget.close()

    def test_readonly_line_edit_returns_false(self):
        widget = QLineEdit("Read only line text")
        widget.setReadOnly(True)
        widget.resize(300, 40)
        widget.show()
        widget.setFocus()
        widget.selectAll()
        self.app.processEvents()
        try:
            hwnd = int(widget.winId())
            self.assertFalse(is_editable_input_target(hwnd))
        finally:
            widget.close()

    def test_editable_text_edit_returns_true(self):
        widget = QTextEdit("Editable multiline text")
        widget.resize(300, 100)
        widget.show()
        widget.setFocus()
        widget.selectAll()
        self.app.processEvents()
        try:
            hwnd = int(widget.winId())
            self.assertTrue(is_editable_input_target(hwnd))
        finally:
            widget.close()

    def test_readonly_text_edit_returns_false(self):
        widget = QTextEdit("Read only text")
        widget.setReadOnly(True)
        widget.resize(300, 100)
        widget.show()
        widget.setFocus()
        widget.selectAll()
        self.app.processEvents()
        try:
            hwnd = int(widget.winId())
            self.assertFalse(is_editable_input_target(hwnd))
        finally:
            widget.close()

    def test_plain_text_edit_editable_and_readonly(self):
        editable = QPlainTextEdit("Plain text editable")
        editable.resize(300, 100)
        editable.show()
        editable.setFocus()
        editable.selectAll()
        self.app.processEvents()
        try:
            self.assertTrue(is_editable_input_target(int(editable.winId())))
        finally:
            editable.close()

        readonly = QPlainTextEdit("Plain text readonly")
        readonly.setReadOnly(True)
        readonly.resize(300, 100)
        readonly.show()
        readonly.setFocus()
        readonly.selectAll()
        self.app.processEvents()
        try:
            self.assertFalse(is_editable_input_target(int(readonly.winId())))
        finally:
            readonly.close()

    def test_static_label_returns_false(self):
        widget = QLabel("Static label text")
        widget.resize(300, 40)
        widget.show()
        self.app.processEvents()
        try:
            hwnd = int(widget.winId())
            self.assertFalse(is_editable_input_target(hwnd))
        finally:
            widget.close()

    def test_push_button_returns_false(self):
        widget = QPushButton("Click Me")
        widget.resize(300, 40)
        widget.show()
        self.app.processEvents()
        try:
            hwnd = int(widget.winId())
            self.assertFalse(is_editable_input_target(hwnd))
        finally:
            widget.close()


if __name__ == "__main__":
    unittest.main()
