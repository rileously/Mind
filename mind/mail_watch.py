"""Watching a mailbox for the ticket, on a thread that is not drawing the window.

An IMAP visit is a TLS handshake, a login and a search across the network, which
is a second on a good day and thirty when the mailbox is slow. None of that may
happen on the interface thread, which is why this exists rather than the page
asking the mailbox directly.

Edge-triggered on the mailbox's own UID rather than on read state: UIDs only go
up, they survive a restart because the last one seen is written down, and
nothing here has to mark a message as read to remember it. That keeps the
mailbox untouched, which is the point - Mind is a guest in it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from .config_store import ConfigStore
from .mailbox import (
    Attachment,
    Credentials,
    MailboxError,
    fetch_new,
)
from .paths import data_dir


DEFAULT_POLL_SECONDS = 120
# A mailbox is not a phone. Polling it every few seconds gains nothing and is
# how an account gets rate-limited or locked.
MIN_POLL_SECONDS = 30

# Where the PDFs land. Kept rather than deleted after sending: a ticket is
# worth having on the PC too, and they are a few hundred kilobytes each.
FOLDER_NAME = "tickets"


def tickets_dir() -> Path:
    folder = data_dir() / FOLDER_NAME
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def parse_senders(raw: object) -> tuple[str, ...]:
    """The sender list as typed into settings: commas, spaces, or both."""
    text = str(raw or "")
    parts = [piece.strip() for piece in text.replace(";", ",").split(",")]
    return tuple(piece for piece in parts if piece)


def save_attachment(attachment: Attachment, folder: Path | None = None) -> Path:
    """Write one attachment down, without overwriting a ticket already there.

    The UID goes in the name because two ferry tickets are both called
    "ticket.pdf" and the second one is not a duplicate of the first.
    """
    where = folder if folder is not None else tickets_dir()
    where.mkdir(parents=True, exist_ok=True)
    stem = Path(attachment.filename).stem or "ticket"
    suffix = Path(attachment.filename).suffix or ".pdf"
    target = where / f"{stem}-{attachment.uid}{suffix}"
    count = 2
    while target.exists():
        target = where / f"{stem}-{attachment.uid}-{count}{suffix}"
        count += 1
    target.write_bytes(attachment.data)
    return target


def caption_for(attachment: Attachment) -> str:
    """What to say above the PDF in the chat."""
    subject = (attachment.subject or "").strip()
    if not subject:
        return "🎫  A ticket has arrived by email."
    return f"🎫  {subject[:180]}"


@dataclass
class Visit:
    """What one look at the mailbox came back with."""

    saved: list = field(default_factory=list)  # (Path, caption)
    high_uid: int = 0
    error: str = ""
    checked: bool = False


class MailPoll(QObject):
    """One visit to the mailbox, run on its own thread."""

    finished = Signal(object)

    def __init__(self, store: ConfigStore, parent: QObject | None = None):
        super().__init__(parent)
        self.store = store

    def run(self) -> None:
        visit = Visit()
        try:
            config = self.store.load()
            credentials = Credentials(
                user=str(config.get("mail_user", "")).strip(),
                password=self.store.get_mail_password(config),
                host=str(config.get("mail_host", "")).strip() or "imap.gmail.com",
                port=int(config.get("mail_port", 993) or 993),
            )
            if not credentials.usable:
                visit.error = "Mind has no mailbox address and app password saved."
                self.finished.emit(visit)
                return
            senders = parse_senders(config.get("mail_senders"))
            if not senders:
                visit.error = "No sender is listed, so no mail is being opened."
                self.finished.emit(visit)
                return
            try:
                since = int(config.get("mail_last_uid", 0) or 0)
            except (TypeError, ValueError):
                since = 0

            found, high = fetch_new(credentials, since_uid=since, senders=senders)
            visit.high_uid = int(high)
            visit.checked = True
            for attachment in found:
                try:
                    path = save_attachment(attachment)
                except OSError as exc:
                    visit.error = f"A ticket arrived but could not be saved: {exc}"
                    continue
                visit.saved.append((path, caption_for(attachment)))
        except MailboxError as exc:
            visit.error = str(exc)
        except Exception as exc:  # noqa: BLE001 - a poll must not kill the thread
            visit.error = f"The mailbox could not be checked: {exc}"
        self.finished.emit(visit)


class MailWatcher(QObject):
    """Polls the mailbox and says when a ticket has landed in it.

    Owned by the application rather than by a page, like the phone watcher, so
    a ticket that arrives while the window is closed still reaches the chat -
    which is the only time any of this matters.
    """

    tickets_arrived = Signal(list)  # [(Path, caption)]
    log = Signal(str)

    def __init__(self, store: ConfigStore, parent: QObject | None = None):
        super().__init__(parent)
        self.store = store
        self._busy = False
        self._thread: QThread | None = None
        self._worker: MailPoll | None = None
        self._complained = ""
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.poll_now)

    @property
    def interval(self) -> int:
        try:
            saved = int(self.store.load().get("mail_poll_seconds", DEFAULT_POLL_SECONDS))
        except (TypeError, ValueError):
            saved = DEFAULT_POLL_SECONDS
        return max(MIN_POLL_SECONDS, saved)

    @property
    def is_running(self) -> bool:
        return self._timer.isActive()

    def start(self) -> None:
        if self._timer.isActive():
            return
        self._complained = ""
        self._timer.start(self.interval * 1000)
        QTimer.singleShot(0, self.poll_now)

    def stop(self) -> None:
        self._timer.stop()

    def poll_now(self) -> None:
        """Look, unless a look is already under way.

        Overlapping visits would each hold their own connection open, and some
        servers count that as a reason to start refusing them.
        """
        if self._busy:
            return
        self._busy = True
        self._thread = QThread()
        self._worker = MailPoll(self.store)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._visited)
        self._thread.start()

    def _visited(self, visit: Visit) -> None:
        self._tidy_thread()
        if visit.error:
            # A mailbox that is refusing the password will refuse it every two
            # minutes, so the same complaint is only made once.
            if visit.error != self._complained:
                self._complained = visit.error
                self.log.emit(f"Mail: {visit.error}")
        else:
            self._complained = ""

        if visit.checked and visit.high_uid:
            config = self.store.load()
            try:
                previous = int(config.get("mail_last_uid", 0) or 0)
            except (TypeError, ValueError):
                previous = 0
            if visit.high_uid != previous:
                # Written down before anything is sent, so a crash between the
                # two costs a ticket rather than repeating one every poll.
                config["mail_last_uid"] = int(visit.high_uid)
                self.store.save(config)

        if visit.saved:
            for path, _ in visit.saved:
                self.log.emit(f"Mail: a ticket arrived - {Path(path).name}")
            self.tickets_arrived.emit(list(visit.saved))

    def _tidy_thread(self) -> None:
        self._busy = False
        thread = self._thread
        self._thread = None
        self._worker = None
        if thread is not None:
            thread.quit()
            thread.wait(2000)
