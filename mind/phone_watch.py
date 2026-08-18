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
from dataclasses import dataclass, field, replace

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from .adb_client import AdbError, CallState, Phone, find_adb
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

    @property
    def name(self) -> str:
        return self.model or self.entry.name

    @property
    def away(self) -> bool:
        return bool(self.trouble)


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


def next_id(entries: list[PhoneEntry]) -> str:
    taken = {entry.id for entry in entries}
    index = 1
    while f"p{index}" in taken:
        index += 1
    return f"p{index}"


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
        same = entry.serial == found.serial or (
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
    if serial:
        return Phone(serial=serial)
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
        self.finished.emit([self._visit(entry) for entry in configured_phones(self.store)])

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
        self._tidy_thread()
        self.state_changed.emit(list(self.statuses))
        for status in changed:
            self.call_changed.emit(status)

    def _tidy_thread(self) -> None:
        self._busy = False
        thread = self._thread
        self._thread = None
        self._worker = None
        if thread is not None:
            thread.quit()
            thread.wait(2000)
