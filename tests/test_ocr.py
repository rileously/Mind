import os
import subprocess
import unittest
from unittest.mock import patch

from PySide6.QtGui import QColor, QImage

from mind.ocr import OcrError, extract_text_from_image


@unittest.skipUnless(os.name == "nt", "Windows OCR is only available on Windows")
class OcrTests(unittest.TestCase):
    def setUp(self):
        self.image = QImage(320, 120, QImage.Format_ARGB32)
        self.image.fill(QColor("white"))

    def test_runs_bundled_ocr_without_a_shell(self):
        completed = subprocess.CompletedProcess([], 0, "Detected text".encode(), b"")
        with patch("mind.ocr.subprocess.run", return_value=completed) as run:
            self.assertEqual(extract_text_from_image(self.image), "Detected text")
        command = run.call_args.args[0]
        self.assertIn("powershell.exe", command[0].lower())
        self.assertIn("windows_ocr.ps1", " ".join(command).lower())
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_rejects_null_image(self):
        with self.assertRaisesRegex(OcrError, "could not be read"):
            extract_text_from_image(QImage())

    def test_reports_image_without_readable_text(self):
        completed = subprocess.CompletedProcess([], 0, b"", b"")
        with patch("mind.ocr.subprocess.run", return_value=completed):
            with self.assertRaisesRegex(OcrError, "No readable text"):
                extract_text_from_image(self.image)


if __name__ == "__main__":
    unittest.main()
