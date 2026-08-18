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

from dataclasses import replace

from .config_store import ConfigStore
from .network_devices import Device, from_dict, merge, rename, scan, to_dict
from .router_client import (
    BlockList,
    RouterError,
    RouterSession,
    fetch_devices,
    set_blocked,
    survey_filters,
    survey_summary,
)
from .telegram_system import read_network_devices


DEFAULT_INTERVAL_SECONDS = 60
MIN_INTERVAL_SECONDS = 15
# A device is kept "online" for a scan or two after it stops answering. Phones
# sleep between beacons, and a list that flickered between here and gone every
# minute would be unreadable.
ONLINE_GRACE_SECONDS = 210.0


def router_credentials(store: ConfigStore) -> tuple[str, str, str]:
    """The router's address and sign-in, or empty strings if it is not set up."""
    config = store.load()
    return (
        str(config.get("router_address", "")).strip(),
        str(config.get("router_username", "")).strip(),
        store.get_router_password(config),
    )


def router_facts(store: ConfigStore) -> tuple[dict[str, str], set[str]]:
    """What only the router knows: the real names, and who it is keeping off.

    Both come from one sign-in, because they are two pages of the same
    conversation and signing in twice a minute for them would be silly.

    Failure is silent here: a wrong password should not stop the scan that works
    without it, and the Test button is where a person goes to find out why. The
    block list is allowed to fail on its own too - a firmware without that page
    still has names worth reading.
    """
    address, username, password = router_credentials(store)
    if not (address and username and password):
        return {}, set()
    try:
        session = RouterSession(address)
        session.sign_in(username, password)
        devices = session.devices()
    except RouterError:
        return {}, set()
    names = {device.mac: device.hostname for device in devices if device.hostname}
    try:
        blocked = set(BlockList(session).state().blocked_macs)
    except RouterError:
        blocked = set()
    return names, blocked


def router_names(store: ConfigStore) -> dict[str, str]:
    """The names the router knows, keyed by MAC."""
    return router_facts(store)[0]


class ScanWorker(QObject):
    """Does one scan when asked, and says what it found."""

    finished = Signal(list, list)
    failed = Signal(str)

    def __init__(self, store: ConfigStore, parent: QObject | None = None):
        super().__init__(parent)
        self.store = store

    def run(self) -> None:
        blocked: set[str] = set()
        try:
            observed = scan(read_network_devices)
            names, blocked = router_facts(self.store)
            if names:
                # The router's name wins over whatever the device said about
                # itself, because it is the one the owner typed into the phone.
                observed = [
                    replace(item, hostname=names.get(item.mac, item.hostname))
                    for item in observed
                ]
        except Exception as exc:  # a scan must never take the thread down
            self.failed.emit(str(exc))
            return
        self.finished.emit(list(observed), sorted(blocked))


class RouterTest(QObject):
    """Tries the router once and reports what came back, in words."""

    done = Signal(str)

    def __init__(self, store: ConfigStore, parent: QObject | None = None):
        super().__init__(parent)
        self.store = store

    def run(self) -> None:
        config = self.store.load()
        address = str(config.get("router_address", "")).strip()
        username = str(config.get("router_username", "")).strip()
        password = self.store.get_router_password(config)
        if not (address and username and password):
            self.done.emit("Fill in the address, username and password first.")
            return
        probe = self.store.root / "router-probe.txt"
        try:
            devices, notes = fetch_devices(
                address, username, password, probe_into=probe
            )
        except RouterError as exc:
            self.done.emit(f"{exc} What it returned was saved to {probe}")
            return
        except Exception as exc:
            self.done.emit(f"The router could not be read: {exc}")
            return
        named = sum(1 for device in devices if device.hostname)
        message = f"Signed in. {len(devices)} devices, {named} with names."
        if notes:
            message += " " + " ".join(notes) + "."
        self.done.emit(message)


class RouterFilterProbe(QObject):
    """Looks for the router page that could block a device, and says what it found.

    A separate button from Test because it asks for something different: Test
    proves the password works, this asks what this firmware calls its block
    list. It only reads - forty GETs at most, no form is ever submitted - so
    running it cannot change a setting on the router.
    """

    done = Signal(str)

    def __init__(self, store: ConfigStore, parent: QObject | None = None):
        super().__init__(parent)
        self.store = store

    def run(self) -> None:
        config = self.store.load()
        address = str(config.get("router_address", "")).strip()
        username = str(config.get("router_username", "")).strip()
        password = self.store.get_router_password(config)
        if not (address and username and password):
            self.done.emit("Fill in the address, username and password first.")
            return
        probe = self.store.root / "router-filter-probe.txt"
        try:
            pages, notes = survey_filters(address, username, password, probe_into=probe)
        except RouterError as exc:
            self.done.emit(str(exc))
            return
        except Exception as exc:
            self.done.emit(f"The router could not be read: {exc}")
            return
        message = " ".join(survey_summary(pages))
        if notes:
            message += f" Details are in {probe.name}."
        self.done.emit(message)


class BlockDevice(QObject):
    """Blocks or unblocks one device on the router, and says what happened.

    On its own thread like everything else that talks to the router: this is
    several requests, and the window must not freeze while a phone is being
    put off the Wi-Fi.
    """

    done = Signal(bool, str)

    def __init__(
        self,
        store: ConfigStore,
        mac: str,
        name: str,
        blocked: bool,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.store = store
        self.mac = mac
        self.name = name
        self.blocked = blocked

    def run(self) -> None:
        address, username, password = router_credentials(self.store)
        if not (address and username and password):
            self.done.emit(False, "Fill in the router's address, username and password first.")
            return
        try:
            set_blocked(address, username, password, self.mac, self.blocked, self.name)
        except RouterError as exc:
            self.done.emit(False, str(exc))
            return
        except Exception as exc:
            self.done.emit(False, f"The router could not be changed: {exc}")
            return
        said = "is now blocked from the Wi-Fi" if self.blocked else "can use the Wi-Fi again"
        self.done.emit(True, f"{self.name or self.mac} {said}.")


class NetworkScanner(QObject):
    """Scans on a timer, keeps the list, and announces what is new."""

    devices_changed = Signal(list)
    # Who the router is keeping off the Wi-Fi, which only it can say.
    blocked_changed = Signal(list)
    arrived = Signal(list)
    scanning = Signal(bool)
    log = Signal(str)

    def __init__(self, store: ConfigStore, parent: QObject | None = None):
        super().__init__(parent)
        self.store = store
        self._thread: QThread | None = None
        self._worker: ScanWorker | None = None
        self._busy = False
        self.blocked: set[str] = set()
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
        self._worker = ScanWorker(self.store)
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

    def _scan_finished(self, observed: list, blocked: list) -> None:
        now = time.time()
        self.blocked = set(blocked)
        self.devices, arrivals = merge(
            self.devices, list(observed), now, online_grace=ONLINE_GRACE_SECONDS
        )
        self.store.save_devices([to_dict(device) for device in self.devices])
        self._tidy_thread()
        self.blocked_changed.emit(sorted(self.blocked))
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
