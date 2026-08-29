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

from dataclasses import dataclass, replace

from .config_store import ConfigStore
from .network_devices import Device, from_dict, merge, rename, scan, to_dict, vendor_for
from .router_client import (
    BORING_NAMES,
    BlockList,
    RouterError,
    RouterSession,
    Ssid,
    fetch_devices,
    read_networks,
    set_blocked,
    set_network_enabled,
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


def router_facts(store: ConfigStore) -> RouterReading:
    """What only the router knows: the names, who it keeps off, what it broadcasts.

    All of it from one sign-in, because they are pages of the same conversation
    and signing in three times a minute for them would be silly.

    Failure is silent here: a wrong password should not stop the scan that works
    without it, and the Test button is where a person goes to find out why. The
    block list is allowed to fail on its own too - a firmware without that page
    still has names worth reading.

    Anything at all, not only a RouterError. An address saved as "." reached
    urllib as a hostname and came back as a UnicodeError from the IDNA encoder,
    which travelled up and failed the whole scan - so the sweep of the network,
    which needs no router and no password, stopped working because of a setting
    it never reads. Whatever goes wrong with the router, the scan continues
    without it.
    """
    address, username, password = router_credentials(store)
    if not (address and username and password):
        return NOTHING_FROM_THE_ROUTER
    try:
        session = RouterSession(address)
        session.sign_in(username, password)
        devices = session.devices()
    except Exception:
        return NOTHING_FROM_THE_ROUTER
    facts = {
        device.mac: RouterFact(
            name=device.name, kind=device.kind, network=device.network
        )
        for device in devices
        if device.name or device.kind or device.network
    }
    try:
        blocked = frozenset(BlockList(session).blocked_macs())
    except Exception:
        blocked = frozenset()
    try:
        networks = read_networks(session)
    except Exception:
        # A firmware that publishes no WLAN list still has names and a block
        # list worth having. The page that switches a network off simply has
        # nothing to offer on this router.
        networks = ()
    return RouterReading(facts, blocked, networks)


def router_names(store: ConfigStore) -> dict[str, str]:
    """The names the router knows, keyed by MAC."""
    return {
        mac: fact.name for mac, fact in router_facts(store).facts.items() if fact.name
    }


def without_boilerplate_names(devices: list[Device]) -> list[Device]:
    """Let go of a remembered name that was never a name.

    A phone that sent no host name of its own left the SSID it joined as the
    only word in its router row, and that was written down as what the device is
    called - ten of them here, every one called SSID2. The reading no longer
    offers those, but merge() keeps a name once learned, so the ones already
    written down would outlive the fix. They are dropped on the way in, and the
    next scan, or failing that the address, says something distinct instead. A
    name the user typed is theirs and is never touched.
    """
    return [
        device
        if device.custom_name or not BORING_NAMES.match(device.hostname)
        else replace(device, hostname="")
        for device in devices
    ]


def with_the_blocked(devices: list[Device], blocked: set[str], now: float) -> list[Device]:
    """Give every address the router is refusing a row, met or not.

    A blocked device cannot answer a scan - that is what blocking it does - so
    the only ones on the page are those Mind happened to meet before it was
    blocked. Anyone blocked from the router's own pages, or from another PC, or
    before Mind was installed, is refused by the router and missing from the one
    list that offers to let them back on. That is the shape of "some people
    cannot get on the Wi-Fi and nothing here says why": the row that would
    explain it is the row that cannot appear.

    So the router's list is the authority on who exists here, not the scan. What
    it names and nothing has met gets a row saying what little is known - the
    address, whoever made the device, blocked, never seen - which is enough to
    select it and let it back on.
    """
    known = {device.mac for device in devices}
    unmet = [
        Device(
            mac=mac,
            vendor=vendor_for(mac),
            first_seen=now,
            last_seen=0.0,
            online=False,
        )
        for mac in sorted(blocked)
        if mac not in known
    ]
    return devices + unmet if unmet else devices


@dataclass(frozen=True)
class RouterFact:
    """What the router adds to a device the scan already found."""

    name: str = ""
    kind: str = ""
    network: str = ""


@dataclass(frozen=True)
class RouterReading:
    """Everything one sign-in brought back.

    A record rather than a tuple that grows: each of these can fail on its own
    and the other two are still worth having, so what came back has to say
    which of it did.
    """

    facts: dict[str, RouterFact]
    blocked: frozenset[str]
    networks: tuple[Ssid, ...] = ()


# What comes back when there is no router to ask, or it would not answer.
NOTHING_FROM_THE_ROUTER = RouterReading({}, frozenset(), ())

# What the router knows about a device it has never heard of.
_NOTHING = RouterFact()


class ScanWorker(QObject):
    """Does one scan when asked, and says what it found."""

    finished = Signal(list, list, list)
    failed = Signal(str)

    def __init__(self, store: ConfigStore, parent: QObject | None = None):
        super().__init__(parent)
        self.store = store

    def run(self) -> None:
        reading = NOTHING_FROM_THE_ROUTER
        try:
            observed = scan(read_network_devices)
            reading = router_facts(self.store)
            facts = reading.facts
            if facts:
                # The router's name wins over whatever the device said about
                # itself, because it is the one the owner typed - into the
                # phone, or into the router's own page. What kind of thing it is
                # only the router knows at all.
                observed = [
                    replace(
                        item,
                        hostname=facts.get(item.mac, _NOTHING).name or item.hostname,
                        kind=facts.get(item.mac, _NOTHING).kind or item.kind,
                        network=facts.get(item.mac, _NOTHING).network or item.network,
                    )
                    for item in observed
                ]
        except Exception as exc:  # a scan must never take the thread down
            self.failed.emit(str(exc))
            return
        self.finished.emit(
            list(observed), sorted(reading.blocked), list(reading.networks)
        )


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
    # Where it has got to. Blocking is a dozen requests across two lists and
    # every network the router has, which on a tired ONT is long enough that a
    # still status line reads as a hang.
    step = Signal(str)

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
        who = self.name or self.mac
        try:
            self.step.emit("Signing in to the router…")
            set_blocked(address, username, password, self.mac, self.blocked, self.name)
        except RouterError as exc:
            self.done.emit(False, str(exc))
            return
        except Exception as exc:
            self.done.emit(False, f"The router could not be changed: {exc}")
            return
        said = "is now blocked from the Wi-Fi" if self.blocked else "can use the Wi-Fi again"
        self.done.emit(True, f"{who} {said}.")


class SwitchNetwork(QObject):
    """Puts one Wi-Fi network on or off the air, and says what happened.

    The counterpart to blocking a device: this refuses everyone at once, which
    is the honest way to ask for a guest network that should not be there this
    week. On its own thread for the same reason - it is a read, a write and a
    read back, and none of that belongs on the thread drawing the window.
    """

    done = Signal(bool, str, list)
    step = Signal(str)

    def __init__(
        self,
        store: ConfigStore,
        index: int,
        label: str,
        enabled: bool,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.store = store
        self.index = index
        self.label = label
        self.enabled = enabled

    def run(self) -> None:
        address, username, password = router_credentials(self.store)
        if not (address and username and password):
            self.done.emit(
                False, "Fill in the router's address, username and password first.", []
            )
            return
        try:
            self.step.emit(
                f"Asking the router to turn {self.label} "
                f"{'on' if self.enabled else 'off'}…"
            )
            networks = set_network_enabled(
                address, username, password, self.index, self.enabled
            )
        except RouterError as exc:
            self.done.emit(False, str(exc), [])
            return
        except Exception as exc:
            self.done.emit(False, f"The router could not be changed: {exc}", [])
            return
        said = "is on the air again" if self.enabled else "is off the air"
        self.done.emit(True, f"{self.label} {said}.", list(networks))


class NetworkScanner(QObject):
    """Scans on a timer, keeps the list, and announces what is new."""

    devices_changed = Signal(list)
    # Who the router is keeping off the Wi-Fi, which only it can say.
    blocked_changed = Signal(list)
    # The networks it broadcasts, and which of them are on the air.
    networks_changed = Signal(list)
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
        # Held only for as long as Mind runs. Whether a network is on the air is
        # the router's to say and changes without Mind being told, so a
        # remembered answer would be a stale one presented as current.
        self.networks: list[Ssid] = []
        self.devices: list[Device] = without_boilerplate_names(
            [device for device in (from_dict(item) for item in store.load_devices()) if device]
        )
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

    def _scan_finished(self, observed: list, blocked: list, networks: list) -> None:
        now = time.time()
        self.blocked = set(blocked)
        # Kept only when the router answered. A scan that could not reach it
        # says nothing about the networks, and nothing is not the same as none
        # - emptying the list there would read as "this router has no Wi-Fi".
        if networks:
            self.networks = list(networks)
        self.devices, arrivals = merge(
            self.devices, list(observed), now, online_grace=ONLINE_GRACE_SECONDS
        )
        self.devices = with_the_blocked(self.devices, self.blocked, now)
        self.store.save_devices([to_dict(device) for device in self.devices])
        self.store.save_blocked(sorted(self.blocked))
        self._tidy_thread()
        self.blocked_changed.emit(sorted(self.blocked))
        self.networks_changed.emit(list(self.networks))
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
