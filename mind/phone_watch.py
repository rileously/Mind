"""Watching the phone, on a thread that is not the one drawing the window.

Every adb command is a process launched, a Wi-Fi round trip and an answer
parsed - a fifth of a second on a good day and several seconds when the phone
has wandered off. None of that may happen on the interface thread, which is why
this exists at all rather than the page asking the phone directly.

What it watches is the call state, because that is the one thing worth knowing
the instant it changes. Everything else the phone can be asked - battery, model
- is read on the same visit, since the visit is the expensive part.

The connection is not permanent and is not treated as though it were. Wireless
debugging changes its port whenever it restarts, so a phone that stops
answering is reconnected by the name it advertises rather than by the address
it happened to have last time.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from .adb_client import AdbError, CallState, Phone, find_adb
from .config_store import ConfigStore


DEFAULT_POLL_SECONDS = 4
MIN_POLL_SECONDS = 2


def phone_settings(store: ConfigStore) -> tuple[str, str]:
    """Which phone to talk to, and where it was last seen."""
    config = store.load()
    return (
        str(config.get("phone_serial", "")).strip(),
        str(config.get("phone_address", "")).strip(),
    )


def phone_for(store: ConfigStore) -> Phone:
    serial, _address = phone_settings(store)
    return Phone(serial=serial)


class PhonePoll(QObject):
    """One visit to the phone: what it is doing, and how it is.

    A phone that cannot be reached is not an error worth a dialog - it is in
    somebody's pocket in another building. It reports itself as away and the
    next visit tries again.
    """

    finished = Signal(object, str, int, str)  # call state, model, battery, trouble

    def __init__(self, store: ConfigStore, parent: QObject | None = None):
        super().__init__(parent)
        self.store = store

    def run(self) -> None:
        serial, address = phone_settings(self.store)
        phone = Phone(serial=serial)
        try:
            # The name costs a second visit, so it is asked for only when there
            # is a call to put it against.
            state = phone.call_state()
            if state.busy:
                state = phone.call_state(with_name=True)
        except AdbError as exc:
            # One reconnection attempt, because the usual reason a phone stops
            # answering is that wireless debugging came back on another port.
            if address:
                try:
                    phone.connect(address)
                    state = phone.call_state()
                except AdbError:
                    self.finished.emit(CallState(), "", -1, str(exc))
                    return
            else:
                self.finished.emit(CallState(), "", -1, str(exc))
                return
        except Exception as exc:  # a poll must never take the thread down
            self.finished.emit(CallState(), "", -1, str(exc))
            return

        model, battery = "", -1
        try:
            model = phone.model()
            battery = phone.battery()
        except AdbError:
            pass
        self.finished.emit(state, model, battery, "")


class PhoneWatcher(QObject):
    """Keeps an eye on the phone, and says when the call state changes.

    Owned by the application rather than by the page, like the network scanner,
    so a call arriving is noticed while the window is closed - which is the only
    time it matters.
    """

    call_changed = Signal(object)
    state_changed = Signal(str, int, str)  # model, battery, trouble
    log = Signal(str)

    def __init__(self, store: ConfigStore, parent: QObject | None = None):
        super().__init__(parent)
        self.store = store
        self.call = CallState()
        self.model = ""
        self.battery = -1
        self.trouble = ""
        self._busy = False
        self._thread: QThread | None = None
        self._worker: PhonePoll | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.poll_now)

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

    def start(self) -> None:
        self._timer.start(self.interval * 1000)
        QTimer.singleShot(0, self.poll_now)

    def stop(self) -> None:
        self._timer.stop()

    def poll_now(self) -> None:
        """Visit the phone, unless a visit is already under way.

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

    def _polled(self, call, model: str, battery: int, trouble: str) -> None:
        was_ringing = self.call.ringing
        changed = (call.state, call.number) != (self.call.state, self.call.number)
        self.call = call
        self.model = model or self.model
        self.battery = battery
        self.trouble = trouble
        self._tidy_thread()
        self.state_changed.emit(self.model, self.battery, self.trouble)
        if changed:
            self.call_changed.emit(call)
            if call.ringing and not was_ringing:
                who = call.caller
                self.log.emit(f"The phone is ringing: {who}")

    def _tidy_thread(self) -> None:
        self._busy = False
        thread = self._thread
        self._thread = None
        self._worker = None
        if thread is not None:
            thread.quit()
            thread.wait(2000)
