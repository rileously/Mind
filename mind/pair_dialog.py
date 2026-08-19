"""Showing a phone a code to look at, instead of asking somebody to type one.

The window is deliberately thin. It makes a name and a password, draws them as
a QR code, and waits for a phone to advertise itself under that name. Everything
that knows how any of that works lives in adb_pairing; this only has to keep
the waiting off the interface thread and say what happened.
"""

from __future__ import annotations

import io

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from .adb_pairing import (
    PairingError,
    new_name,
    new_password,
    pair,
    qr_payload,
    wait_for_phone,
)


# Big enough to scan from a hand's length away without filling the screen.
QR_SCALE = 8
QR_BORDER = 3


def qr_pixmap(payload: str, scale: int = QR_SCALE) -> QPixmap:
    """The code as something Qt can draw. Empty if it cannot be drawn."""
    try:
        import segno
    except ImportError:
        return QPixmap()
    buffer = io.BytesIO()
    segno.make(payload, error="m").save(buffer, kind="png", scale=scale, border=QR_BORDER)
    image = QImage()
    if not image.loadFromData(buffer.getvalue()):
        return QPixmap()
    return QPixmap.fromImage(image)


class _PairSignals(QObject):
    finished = Signal(bool, str)


class PairWorker(QRunnable):
    """Waits for a phone to read the code, then pairs with it."""

    def __init__(self, name: str, password: str):
        super().__init__()
        self.name = name
        self.password = password
        self.signals = _PairSignals()
        self._wanted = True

    def cancel(self) -> None:
        self._wanted = False

    def run(self) -> None:
        address = wait_for_phone(self.name, keep_going=lambda: self._wanted)
        if not self._wanted:
            return
        if not address:
            self.signals.finished.emit(
                False, "No phone read the code. The camera has to be pointed at it."
            )
            return
        try:
            self.signals.finished.emit(True, pair(address, self.password))
        except PairingError as exc:
            self.signals.finished.emit(False, str(exc))


class PairDialog(QDialog):
    """A code on the screen, and a phone looking at it."""

    paired = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pair a phone")
        self.setModal(True)
        self._worker: PairWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)

        steps = QLabel(
            "On the phone, open <b>Settings, Developer options, Wireless "
            "debugging</b>, then <b>Pair device with QR code</b>, and point it "
            "at this."
        )
        steps.setWordWrap(True)
        root.addWidget(steps)

        self.code = QLabel()
        self.code.setAlignment(Qt.AlignCenter)
        root.addWidget(self.code)

        self.status = QLabel("Waiting for a phone to read it...")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.close_button)
        root.addLayout(buttons)

        self._start()

    def _start(self) -> None:
        name, password = new_name(), new_password()
        pixmap = qr_pixmap(qr_payload(name, password))
        if pixmap.isNull():
            # Without a code there is nothing to look at, and the six-digit way
            # is still there on the page behind this.
            self.code.setText("This copy of Mind cannot draw a QR code.")
            self.status.setText("Pair with the address and six-digit code instead.")
            return
        self.code.setPixmap(pixmap)
        self._worker = PairWorker(name, password)
        self._worker.signals.finished.connect(self._done)
        QThreadPool.globalInstance().start(self._worker)

    def _done(self, ok: bool, message: str) -> None:
        self.status.setText(message)
        if ok:
            self.paired.emit(message)
            self.close_button.setText("Done")

    def reject(self) -> None:
        # The worker outlives the window otherwise, and goes on looking for a
        # phone nobody is pairing any more.
        if self._worker is not None:
            self._worker.cancel()
        super().reject()
