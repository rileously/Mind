"""Watching the phones, on a thread that is not the one drawing the window.

Every adb command is a process launched, a Wi-Fi round trip and an answer
parsed - a fifth of a second on a good day and several seconds when a phone has
wandered off. None of that may happen on the interface thread, which is why this
exists at all rather than the page asking the phones directly.

More than one phone, because a house has more than one and a call arrives at
whichever it arrives at. Each is polled on its own and each keeps its own state,
so a phone that has gone quiet costs the others nothing but the time its own
visit takes.

What identifies a phone is not the address it answers on. Wireless debugging
changes its port whenever it restarts, and the same handset shows up twice at
once - by address and by the name it advertises. The hardware serial is the same
either way, and that is what a phone is known by here.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, replace

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from .adb_client import (
    AdbError,
    CallState,
    Phone,
    attached,
    find_adb,
    mdns_services,
    restart_server,
)
from .config_store import ConfigStore


DEFAULT_POLL_SECONDS = 4
MIN_POLL_SECONDS = 2
# What an advertised name looks like: adb-<hardware serial>-<code>._adb-...
MDNS_SERIAL = re.compile(r"^adb-([A-Za-z0-9]+)-")


@dataclass(frozen=True)
class PhoneEntry:
    """One phone Mind has been told about."""

    id: str
    serial: str = ""
    address: str = ""
    label: str = ""
    hardware: str = ""

    @property
    def name(self) -> str:
        return self.label or self.hardware or self.serial or "Phone"


@dataclass(frozen=True)
class PhoneStatus:
    """What one phone was doing when it was last asked."""

    entry: PhoneEntry
    call: CallState = field(default_factory=CallState)
    model: str = ""
    battery: int = -1
    trouble: str = ""
    # Set when the phone answered to a different serial than the one saved, so
    # the watcher can write the working one down rather than rediscovering it
    # on every poll for the rest of the session.
    found_serial: str = ""

    @property
    def name(self) -> str:
        return self.model or self.entry.name

    @property
    def away(self) -> bool:
        return bool(self.trouble)


def rediscovered(entry: PhoneEntry, devices: list) -> str:
    """The serial adb is using for this phone now, if it differs from ours.

    Wireless debugging hands out a new port whenever it comes back, and adb
    finds the phone again over mDNS under whatever name that produced. A serial
    written down once therefore stops working while the phone is sitting on the
    same network, plugged in, perfectly reachable - so the fix is to ask adb
    what it is calling the phone rather than to insist on the old answer.

    The hardware serial is what makes this safe: it is stamped into the mDNS
    name and does not change, so the phone is matched by what it is rather than
    by how it is currently addressed.
    """
    for device in devices:
        if not getattr(device, "ready", True):
            continue
        if same_serial(device.serial, entry.serial):
            return device.serial
        found = hardware_from_serial(device.serial)
        if found and entry.hardware and found == entry.hardware:
            return device.serial
    return ""


# adb is restarted at most this often, however many phones are missing and
# however often the poll comes round.
NUDGE_SECONDS = 120.0
_last_nudge: float | None = None


def advertised(entry: PhoneEntry, services: list) -> str:
    """Where this phone says it is, whether or not adb has reached it.

    The address is the part that keeps changing, and mDNS carries the current
    one even when the device list is empty - which is the state a phone is in
    after wireless debugging has come back on a different port and adb has not
    noticed yet.

    Matched on the hardware serial, which mDNS puts in the advertised name.
    """
    for name, address in services:
        if entry.hardware and entry.hardware in name:
            return address
        if entry.serial and name and name in entry.serial:
            return address
    return ""


def hardware_from_serial(serial: str) -> str:
    """The hardware serial hidden in an advertised name, if it is one.

    Saves asking the phone: adb-5C061VDCR0003N-dtKL0C._adb-tls-connect._tcp is
    that handset saying its own serial out loud.
    """
    found = MDNS_SERIAL.match(serial or "")
    return found.group(1) if found else ""


def configured_phones(store: ConfigStore) -> list[PhoneEntry]:
    """The phones Mind is watching, in the order they were added.

    A single phone saved by an older version is carried across rather than
    forgotten - it is the phone somebody set up, and a list of none would look
    like it had been lost.
    """
    config = store.load()
    saved = config.get("phones")
    entries: list[PhoneEntry] = []
    if isinstance(saved, list):
        for index, item in enumerate(saved):
            if not isinstance(item, dict):
                continue
            serial = str(item.get("serial", "")).strip()
            if not serial:
                continue
            entries.append(
                PhoneEntry(
                    id=str(item.get("id") or f"p{index + 1}"),
                    serial=serial,
                    address=str(item.get("address", "")).strip(),
                    label=str(item.get("label", "")).strip(),
                    hardware=str(item.get("hardware", "")).strip()
                    or hardware_from_serial(serial),
                )
            )
    if entries:
        return entries

    single = str(config.get("phone_serial", "")).strip()
    if single:
        return [
            PhoneEntry(
                id="p1",
                serial=single,
                address=str(config.get("phone_address", "")).strip(),
                hardware=hardware_from_serial(single),
            )
        ]
    return []


def save_phones(store: ConfigStore, entries: list[PhoneEntry]) -> None:
    config = store.load()
    config["phones"] = [
        {
            "id": entry.id,
            "serial": entry.serial,
            "address": entry.address,
            "label": entry.label,
            "hardware": entry.hardware,
        }
        for entry in entries
    ]
    if entries and not str(config.get("phone_serial", "")).strip():
        # The one a call is dialled from when nothing says otherwise.
        config["phone_serial"] = entries[0].serial
    store.save(config)


def forget_phone(entries: list[PhoneEntry], phone_id: str) -> list[PhoneEntry]:
    """Every phone but that one.

    Removing it from the list is what stops Mind watching it, and stops it
    being reconnected: nothing visits a phone that is not configured, so the
    nudge that goes looking for a missing one never runs for this one again.
    """
    if not phone_id:
        return list(entries)
    return [entry for entry in entries if entry.id != phone_id]


def next_id(entries: list[PhoneEntry]) -> str:
    taken = {entry.id for entry in entries}
    index = 1
    while f"p{index}" in taken:
        index += 1
    return f"p{index}"


def same_serial(one: str, other: str) -> bool:
    """Whether two serials name the same phone.

    adb lists an mDNS device with a trailing dot - the root label every fully
    qualified name ends in - and will not accept the name without it. A serial
    saved without that dot therefore looks different from the one adb reports
    and is refused when it is used, which reads as "device not found" for a
    phone sitting on the same network answering pings.
    """
    return (one or "").rstrip(".") == (other or "").rstrip(".") and bool(one or other)


def merge_phone(entries: list[PhoneEntry], found: PhoneEntry) -> list[PhoneEntry]:
    """Add a phone, or update the one it turns out to be.

    The same handset arrives under two serials at once - its address and its
    advertised name - and under a different address after every restart. Two
    entries for one phone would ring twice and be answered on whichever was
    asked first, so anything carrying a hardware serial already known is that
    phone with a new way of being reached.
    """
    updated: list[PhoneEntry] = []
    matched = False
    for entry in entries:
        same = same_serial(entry.serial, found.serial) or (
            bool(found.hardware) and entry.hardware == found.hardware
        )
        if same and not matched:
            matched = True
            updated.append(
                replace(
                    entry,
                    serial=found.serial or entry.serial,
                    address=found.address or entry.address,
                    label=found.label or entry.label,
                    hardware=found.hardware or entry.hardware,
                )
            )
        else:
            updated.append(entry)
    if not matched:
        updated.append(replace(found, id=found.id or next_id(entries)))
    return updated


def phone_for(store: ConfigStore, phone_id: str = "") -> Phone:
    """The phone to act on: the one named, or the one calls are placed from."""
    entries = configured_phones(store)
    if phone_id:
        for entry in entries:
            if entry.id == phone_id:
                return Phone(serial=entry.serial)
    serial = str(store.load().get("phone_serial", "")).strip()
    # Only if it is still one of the configured phones. It is written when a
    # handset is chosen and not cleared when that handset is reached a
    # different way, so on its own it will happily name an address that
    # stopped existing several reconnections ago.
    for entry in entries:
        if same_serial(entry.serial, serial):
            return Phone(serial=entry.serial)
    return Phone(serial=entries[0].serial if entries else "")


class ContactLookup(QObject):
    """Asks a phone who a number belongs to, off the drawing thread.

    A second at worst, and the tooltip it is for appears the instant a number
    is selected - so the card is shown first with the number, and the name
    arrives into it if there is one.
    """

    found = Signal(str, str)  # the number asked about, and the name

    def __init__(self, store: ConfigStore, number: str, parent: QObject | None = None):
        super().__init__(parent)
        self.store = store
        self.number = number

    def run(self) -> None:
        try:
            name = phone_for(self.store).contact_name(self.number)
        except Exception:  # a tooltip must never take anything down
            name = ""
        self.found.emit(self.number, name)


class PhonePoll(QObject):
    """One visit to every phone: what each is doing, and how each is.

    A phone that cannot be reached is not an error worth a dialog - it is in
    somebody's pocket in another building. It reports itself as away and the
    next visit tries again.
    """

    finished = Signal(list)

    def __init__(self, store: ConfigStore, parent: QObject | None = None):
        super().__init__(parent)
        self.store = store

    def run(self) -> None:
        statuses = [self._visit(entry) for entry in configured_phones(self.store)]
        if any(status.away for status in statuses):
            # Asked once, and only when something is actually missing, so a
            # house where every phone answers pays nothing for this.
            try:
                devices = attached()
            except AdbError:
                devices = []
            # Not conditional on there being any: an empty device list is the
            # state this exists for, and mDNS still knows where the phone is.
            statuses = [
                self._again(status, devices) if status.away else status
                for status in statuses
            ]
        self.finished.emit(statuses)

    def _again(self, status: PhoneStatus, devices: list) -> PhoneStatus:
        """Try the phone once more, under the serial adb is using for it."""
        serial = rediscovered(status.entry, devices)
        # Compared exactly, not with same_serial: a trailing dot is the whole
        # difference in the case this exists to fix, and adb refuses the name
        # without it.
        if serial and serial != status.entry.serial:
            fresh = self._visit(replace(status.entry, serial=serial))
            if not fresh.away:
                return replace(fresh, found_serial=serial)
        return self._from_mdns(status)

    def _from_mdns(self, status: PhoneStatus) -> PhoneStatus:
        """Nudge adb when it has lost a phone that is still announcing itself.

        The device list can be empty while the phone advertises perfectly well:
        wireless debugging came back on a different port and adb has not looked
        again. Connecting to the advertised address does not help - that port
        wants a TLS handshake only adb's own auto-connect performs - so the
        thing that works is making adb start over.

        Only when mDNS says the phone is there, so a handset genuinely out of
        the house never causes this, and not more often than the interval
        below, because the poll comes round every few seconds and restarting
        adb on each one would be worse than the problem.

        The phone is still away when this returns. It arrives on a later visit.
        """
        global _last_nudge
        if not advertised(status.entry, mdns_services()):
            return status
        now = time.monotonic()
        if _last_nudge is not None and now - _last_nudge < NUDGE_SECONDS:
            return status
        _last_nudge = now
        restart_server()
        return status

    def _visit(self, entry: PhoneEntry) -> PhoneStatus:
        phone = Phone(serial=entry.serial)
        try:
            # The name costs a second visit, so it is asked for only when there
            # is a call to put it against.
            state = phone.call_state()
            if state.busy:
                state = phone.call_state(with_name=True)
        except AdbError as exc:
            # One reconnection attempt, because the usual reason a phone stops
            # answering is that wireless debugging came back on another port.
            if entry.address:
                try:
                    phone.connect(entry.address)
                    state = phone.call_state()
                except AdbError:
                    return PhoneStatus(entry, trouble=str(exc))
            else:
                return PhoneStatus(entry, trouble=str(exc))
        except Exception as exc:  # a poll must never take the thread down
            return PhoneStatus(entry, trouble=str(exc))

        model, battery = "", -1
        try:
            model = phone.model()
            battery = phone.battery()
        except AdbError:
            pass
        return PhoneStatus(entry, state, model, battery, "")


class PhoneWatcher(QObject):
    """Keeps an eye on every phone, and says when one of them rings.

    Owned by the application rather than by the page, like the network scanner,
    so a call arriving is noticed while the window is closed - which is the only
    time it matters.
    """

    call_changed = Signal(object)  # the PhoneStatus whose call changed
    state_changed = Signal(list)
    log = Signal(str)

    def __init__(self, store: ConfigStore, parent: QObject | None = None):
        super().__init__(parent)
        self.store = store
        self.statuses: list[PhoneStatus] = []
        self._busy = False
        self._thread: QThread | None = None
        self._worker: PhonePoll | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.poll_now)

    # -- what the rest of Mind asks it ------------------------------------

    def status(self, phone_id: str = "") -> PhoneStatus | None:
        for status in self.statuses:
            if not phone_id or status.entry.id == phone_id:
                return status
        return None

    @property
    def busy_status(self) -> PhoneStatus | None:
        """The phone with something happening on it, if any one has."""
        for status in self.statuses:
            if status.call.busy:
                return status
        return None

    @property
    def interval(self) -> int:
        try:
            saved = int(self.store.load().get("phone_poll_seconds", DEFAULT_POLL_SECONDS))
        except (TypeError, ValueError):
            saved = DEFAULT_POLL_SECONDS
        return max(MIN_POLL_SECONDS, saved)

    @property
    def is_running(self) -> bool:
        return self._timer.isActive()

    @property
    def ready(self) -> bool:
        return bool(find_adb())

    # -- the loop ---------------------------------------------------------

    def start(self) -> None:
        self._timer.start(self.interval * 1000)
        QTimer.singleShot(0, self.poll_now)

    def stop(self) -> None:
        self._timer.stop()

    def poll_now(self) -> None:
        """Visit the phones, unless a visit is already under way.

        Overlapping visits would queue behind each other on a phone that has
        gone quiet and arrive in a burst when it comes back, so a slow visit
        simply means the next tick is skipped.
        """
        if self._busy:
            return
        self._busy = True
        self._thread = QThread()
        self._worker = PhonePoll(self.store)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._polled)
        self._thread.start()

    def _polled(self, found: list) -> None:
        previous = {status.entry.id: status for status in self.statuses}
        fresh: list[PhoneStatus] = []
        changed: list[PhoneStatus] = []
        for status in found:
            was = previous.get(status.entry.id)
            call = status.call
            # Android clears the incoming number the moment a call connects, so
            # a call that arrived as Dhipoz would become nobody a poll later -
            # on the notification counting it out, of all places.
            if was and call.busy and not call.number and was.call.busy and was.call.number:
                call = replace(call, number=was.call.number, name=was.call.name)
                status = replace(status, call=call)
            fresh.append(status)
            if not was or (was.call.state, was.call.number) != (call.state, call.number):
                changed.append(status)
                if call.ringing and not (was and was.call.ringing):
                    who = call.caller or "an unknown number"
                    self.log.emit(f"{status.name} is ringing: {who}")
        self.statuses = fresh
        self._remember_serials(fresh)
        self._tidy_thread()
        self.state_changed.emit(list(self.statuses))
        for status in changed:
            self.call_changed.emit(status)

    def _remember_serials(self, statuses: list) -> None:
        """Write down any serial that turned out to be the working one.

        Only when one changed, because this is on a timer and the settings file
        should not be rewritten every few seconds for no reason.
        """
        corrections = [s for s in statuses if s.found_serial]
        if not corrections:
            return
        entries = configured_phones(self.store)
        for status in corrections:
            entries = merge_phone(
                entries, replace(status.entry, serial=status.found_serial)
            )
        save_phones(self.store, entries)
        for status in corrections:
            self.log.emit(f"{status.name} answers to a new address; saved.")

    def _tidy_thread(self) -> None:
        self._busy = False
        thread = self._thread
        self._thread = None
        self._worker = None
        if thread is not None:
            thread.quit()
            thread.wait(2000)
