"""Small disposable text target used by the live Mind Palette diagnostic."""

import argparse
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLineEdit


parser = argparse.ArgumentParser()
parser.add_argument("--report")
args = parser.parse_args()


class TestField(QLineEdit):
    def closeEvent(self, event):
        if args.report:
            Path(args.report).write_text(self.text(), encoding="utf-8")
        super().closeEvent(event)


app = QApplication([])
field = TestField("Mind Palette diagnostic text")
field.setWindowTitle("Mind Palette Test Target")
field.resize(460, 54)
field.show()
field.selectAll()
field.setFocus()
QTimer.singleShot(100, field.activateWindow)
raise SystemExit(app.exec())
