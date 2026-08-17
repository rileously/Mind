"""Runs the network scan on its own thread and remembers what it finds.

Owned by the application rather than by the page, so scanning continues while
the window is closed and a page that is not open costs nothing. A scan takes a
few seconds - a sweep of 254 addresses, then waiting for devices to name
themselves - which is exactly why it must never happen on the thread drawing
the interface.

The scanner owns the file too. Both the Wi-Fi Devices page and the Telegram
bridge read what it wrote rather than scanning for themselves, so there is one
scan on the machine however many things are watching.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from .config_store import ConfigStore
from .network_devices import Device, from_dict, merge, rename, scan, to_dict
from .telegram_system import read_network_devices


DEFAULT_INTERVAL_SECONDS = 60
MIN_INTERVAL_SECONDS = 15
# A device is kept "online" for a scan or two after it stops answering. Phones
# sleep between beacons, and a list that flickered between here and gone every
# minute would be unreadable.
ONLINE_GRACE_SECONDS = 210.0


class ScanWorker(QObject):
    """Does one scan when asked, and says what it found."""

    finished = Signal(list, list)
    failed = Signal(str)

    def run(self) -> None:
        try:
            observed = scan(read_network_devices)
        except Exception as exc:  # a scan must never take the thread down
            self.failed.emit(str(exc))
            return
        self.finished.emit(list(observed), [])


class NetworkScanner(QObject):
    """Scans on a timer, keeps the list, and announces what is new."""

    devices_changed = Signal(list)
    arrived = Signal(list)
    scanning = Signal(bool)
    log = Signal(str)

    def __init__(self, store: ConfigStore, parent: QObject | None = None):
        super().__init__(parent)
        self.store = store
        self._thread: QThread | None = None
        self._worker: ScanWorker | None = None
        self._busy = False
        self.devices: list[Device] = [
            device for device in (from_dict(item) for item in store.load_devices()) if device
        ]
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.scan_now)

    # -- lifecycle -------------------------------------------------------

    def start(self, interval_seconds: int | None = None) -> None:
        seconds = max(MIN_INTERVAL_SECONDS, int(interval_seconds or self.interval))
        self._timer.start(seconds * 1000)
        # The first scan happens now rather than a minute from now, so opening
        # the page shows something without waiting for the timer.
        QTimer.singleShot(0, self.scan_now)

    def stop(self) -> None:
        self._timer.stop()

    @property
    def is_running(self) -> bool:
        return self._timer.isActive()

    @property
    def interval(self) -> int:
        try:
            saved = int(self.store.load().get("network_scan_seconds", DEFAULT_INTERVAL_SECONDS))
        except (TypeError, ValueError):
            saved = DEFAULT_INTERVAL_SECONDS
        return max(MIN_INTERVAL_SECONDS, saved)

    def set_interval(self, seconds: int) -> None:
        config = self.store.load()
        config["network_scan_seconds"] = max(MIN_INTERVAL_SECONDS, int(seconds))
        self.store.save(config)
        if self.is_running:
            self._timer.start(self.interval * 1000)

    # -- scanning --------------------------------------------------------

    def scan_now(self) -> None:
        """Start a scan, unless one is already running.

        Overlapping scans would fight over the same multicast port and report
        each other's leftovers, so a slow scan simply means the next tick is
        skipped.
        """
        if self._busy:
            return
        self._busy = True
        self.scanning.emit(True)
        self._thread = QThread()
        self._worker = ScanWorker()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._scan_finished)
        self._worker.failed.connect(self._scan_failed)
        self._thread.start()

    def _tidy_thread(self) -> None:
        self._busy = False
        self.scanning.emit(False)
        thread = self._thread
        self._thread = None
        self._worker = None
        if thread is not None:
            thread.quit()
            thread.wait(2000)

    def _scan_failed(self, message: str) -> None:
        self.log.emit(f"Network scan failed: {message}")
        self._tidy_thread()

    def _scan_finished(self, observed: list, _unused: list) -> None:
        now = time.time()
        self.devices, arrivals = merge(
            self.devices, list(observed), now, online_grace=ONLINE_GRACE_SECONDS
        )
        self.store.save_devices([to_dict(device) for device in self.devices])
        self._tidy_thread()
        self.devices_changed.emit(list(self.devices))
        if arrivals:
            names = ", ".join(device.display_name for device in arrivals[:4])
            self.log.emit(f"New on the network: {names}")
            self.arrived.emit(list(arrivals))

    # -- naming ----------------------------------------------------------

    def rename_device(self, mac: str, name: str) -> None:
        self.devices = rename(self.devices, mac, name)
        self.store.save_devices([to_dict(device) for device in self.devices])
        self.devices_changed.emit(list(self.devices))

    def forget(self, mac: str) -> None:
        self.devices = [device for device in self.devices if device.mac != mac]
        self.store.save_devices([to_dict(device) for device in self.devices])
        self.devices_changed.emit(list(self.devices))
