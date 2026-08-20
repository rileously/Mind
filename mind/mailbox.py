"""Taking an attachment out of a mailbox and nothing else out of it.

RTL mints the ticket QR on its own servers and mails the result as a PDF, so
the only way Mind can put a scannable ticket in a chat is to go and fetch that
mail. Which means holding a key to somebody's whole inbox for the sake of one
attachment, and the shape of everything here follows from taking that
seriously.

Only headers are read first, and only a message whose sender matches is ever
downloaded in full. Mail from anyone else is passed over having cost two
header lines, so an inbox full of private correspondence stays on the server
rather than being pulled through this machine to be thrown away. The mailbox
is opened read-only, so nothing here marks a message as read, moves it, or
deletes it: somebody looking at their inbox afterwards cannot tell Mind was
ever in it.

Parsing is kept apart from fetching, so the awkward half - MIME trees, encoded
filenames, parts that claim to be one thing and are another - is testable
against a message built in a test rather than against a live mailbox.
"""

from __future__ import annotations

import email
import imaplib
from dataclasses import dataclass
from email.header import decode_header
from email.message import Message


GMAIL_HOST = "imap.gmail.com"
IMAP_PORT = 993
CONNECT_TIMEOUT = 30

# Telegram will not take a larger document from a bot, so a bigger attachment
# is not worth the bandwidth of downloading it.
MAX_ATTACHMENT_BYTES = 45 * 1024 * 1024

# Who is worth opening. The ticket comes from the operator's own domain, and
# MTCC runs the service behind it.
DEFAULT_SENDERS = ("rtl.mv", "mtcc.com.mv")
DEFAULT_KINDS = (".pdf",)

# How many messages one visit will look at. A backlog is caught up over several
# polls rather than in one long stall.
MAX_PER_VISIT = 8

# Anything outside this becomes an underscore, which takes the path separators,
# the colon of a drive letter and the control characters with it. The filename
# arrives from outside and is about to be written to disk.
NAME_KEEP = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789 ._-()[]"
)


class MailboxError(RuntimeError):
    """Raised when the mailbox cannot be reached, or refuses the sign-in."""


@dataclass(frozen=True)
class Credentials:
    """What it takes to open the mailbox.

    Never written to a log or a settings file in this form: the password comes
    out of DPAPI for the length of one visit and goes no further.
    """

    user: str
    password: str
    host: str = GMAIL_HOST
    port: int = IMAP_PORT
    folder: str = "INBOX"

    @property
    def usable(self) -> bool:
        return bool(self.user.strip() and self.password and self.host.strip())


@dataclass(frozen=True)
class Attachment:
    """One file found on one message."""

    uid: int
    filename: str
    data: bytes
    subject: str = ""
    sender: str = ""

    @property
    def size(self) -> int:
        return len(self.data)


def decoded(raw: object) -> str:
    """Read a header that may be RFC 2047 encoded, without ever raising.

    Subject lines arrive base64'd in whatever charset the sender felt like, and
    a ticket is not worth an exception, so anything unreadable degrades to the
    parts that are readable.
    """
    if raw is None:
        return ""
    text = str(raw)
    try:
        pieces = decode_header(text)
    except (ValueError, UnicodeDecodeError):
        return text
    out: list[str] = []
    for value, charset in pieces:
        if isinstance(value, bytes):
            try:
                out.append(value.decode(charset or "utf-8", "replace"))
            except (LookupError, UnicodeDecodeError):
                out.append(value.decode("utf-8", "replace"))
        else:
            out.append(value)
    return " ".join(part for part in (p.strip() for p in out) if part)


def wanted_sender(sender: str, senders) -> bool:
    """Whether a message is from somebody worth opening.

    Matched as a substring of the address rather than parsed into a domain, so
    "rtl.mv" catches noreply@rtl.mv and tickets@bo.rtl.mv alike. An empty list
    matches nothing: opening every message in the inbox is not a sane thing to
    fall back to.
    """
    address = (sender or "").strip().lower()
    if not address:
        return False
    for candidate in senders or ():
        needle = str(candidate).strip().lower()
        if needle and needle in address:
            return True
    return False


def wanted_file(filename: str, kinds) -> bool:
    name = (filename or "").strip().lower()
    if not name:
        return False
    wanted = [str(k).strip().lower() for k in (kinds or ()) if str(k).strip()]
    return any(name.endswith(kind) for kind in wanted)


def safe_name(raw: str, fallback: str = "ticket.pdf") -> str:
    """A filename that is only a filename.

    The name comes from the mail, which is to say from a stranger, and it is
    about to be joined onto a directory Mind owns.
    """
    cleaned = "".join(ch if ch in NAME_KEEP else "_" for ch in (raw or "").strip())
    cleaned = cleaned.strip("._ ")
    return cleaned[:120] or fallback


def attachments_in(raw: bytes, uid: int = 0, kinds=DEFAULT_KINDS) -> list[Attachment]:
    """Every wanted attachment on one raw message."""
    try:
        message = email.message_from_bytes(raw)
    except (ValueError, TypeError) as exc:
        raise MailboxError(f"That message could not be read: {exc}") from exc
    return attachments_of(message, uid=uid, kinds=kinds)


def attachments_of(message: Message, uid: int = 0, kinds=DEFAULT_KINDS) -> list[Attachment]:
    """The same, from an already parsed message.

    Walks the whole tree rather than the top level, because a forwarded ticket
    arrives as a message inside a message and the PDF is a leaf of that.
    """
    subject = decoded(message.get("Subject"))
    sender = decoded(message.get("From"))
    found: list[Attachment] = []
    for part in message.walk():
        if part.is_multipart():
            continue
        name = decoded(part.get_filename())
        if not wanted_file(name, kinds):
            continue
        try:
            payload = part.get_payload(decode=True)
        except (ValueError, TypeError):
            continue
        if not payload or len(payload) > MAX_ATTACHMENT_BYTES:
            continue
        found.append(
            Attachment(
                uid=int(uid),
                filename=safe_name(name),
                data=bytes(payload),
                subject=subject,
                sender=sender,
            )
        )
    return found


# -- talking to the server ------------------------------------------------


def _checked(result, message: str):
    """imaplib answers with a status string rather than raising."""
    status, data = result
    if status != "OK":
        raise MailboxError(message)
    return data


def _piece(data) -> bytes:
    """The payload out of one FETCH answer, whose shape imaplib leaves to you."""
    for item in data or ():
        if isinstance(item, tuple) and len(item) > 1 and item[1]:
            return bytes(item[1])
    return b""


def _uids(data) -> list[int]:
    numbers: list[int] = []
    for chunk in data or ():
        if not chunk:
            continue
        text = chunk.decode("ascii", "replace") if isinstance(chunk, bytes) else str(chunk)
        for token in text.split():
            try:
                numbers.append(int(token))
            except ValueError:
                continue
    return sorted(set(numbers))


def connect(credentials: Credentials):
    if not credentials.usable:
        raise MailboxError("Mind has no mailbox address and password to sign in with.")
    try:
        conn = imaplib.IMAP4_SSL(
            credentials.host, int(credentials.port), timeout=CONNECT_TIMEOUT
        )
    except (OSError, imaplib.IMAP4.error) as exc:
        raise MailboxError(f"Could not reach {credentials.host}: {exc}") from exc
    try:
        conn.login(credentials.user, credentials.password)
    except imaplib.IMAP4.error as exc:
        try:
            conn.logout()
        except (OSError, imaplib.IMAP4.error):
            pass
        # Almost always the app password rather than anything subtler, and the
        # server's own wording for it helps nobody.
        raise MailboxError(
            "The mailbox refused that address and password. Gmail needs an app "
            "password with IMAP turned on, not the password you log in with."
        ) from exc
    return conn


def newest_uid(credentials: Credentials) -> int:
    """The highest UID in the folder right now, opening no message at all."""
    conn = connect(credentials)
    try:
        _checked(
            conn.select(credentials.folder, readonly=True), "That folder is not there."
        )
        data = _checked(conn.uid("SEARCH", None, "ALL"), "The mailbox would not be searched.")
        found = _uids(data)
        return found[-1] if found else 0
    finally:
        _logout(conn)


def fetch_new(
    credentials: Credentials,
    since_uid: int = 0,
    senders=DEFAULT_SENDERS,
    kinds=DEFAULT_KINDS,
    limit: int = MAX_PER_VISIT,
) -> tuple[list[Attachment], int]:
    """Attachments arrived since ``since_uid``, and the new high mark.

    The first visit deliberately finds nothing. A mailbox that has been running
    for years would otherwise answer the first poll with every ticket in it, so
    an unarmed watcher notes where the mailbox has got to and starts from there.

    The high mark comes back even when nothing matched, so a poll that walked
    past fifty unrelated messages does not walk past them all again.
    """
    conn = connect(credentials)
    try:
        _checked(
            conn.select(credentials.folder, readonly=True),
            f"There is no folder called {credentials.folder}.",
        )
        since = max(0, int(since_uid))
        if since <= 0:
            data = _checked(
                conn.uid("SEARCH", None, "ALL"), "The mailbox would not be searched."
            )
            found = _uids(data)
            return [], (found[-1] if found else 0)

        data = _checked(
            conn.uid("SEARCH", None, f"UID {since + 1}:*"),
            "The mailbox would not be searched.",
        )
        # A "n:*" range answers with the last message when nothing is above n,
        # so the range is not to be trusted and is applied again here.
        fresh = [uid for uid in _uids(data) if uid > since]
        if not fresh:
            return [], since

        high = since
        out: list[Attachment] = []
        for uid in fresh[: max(1, int(limit))]:
            head = _piece(
                _checked(
                    conn.uid("FETCH", str(uid), "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])"),
                    "A message header could not be read.",
                )
            )
            high = max(high, uid)
            if not head:
                continue
            header = email.message_from_bytes(head)
            if not wanted_sender(decoded(header.get("From")), senders):
                # Somebody else's mail. Not downloaded, not looked at.
                continue
            body = _piece(
                _checked(
                    conn.uid("FETCH", str(uid), "(BODY.PEEK[])"),
                    "A message could not be read.",
                )
            )
            if body:
                out.extend(attachments_in(body, uid=uid, kinds=kinds))
        return out, high
    finally:
        _logout(conn)


def _logout(conn) -> None:
    try:
        conn.close()
    except (OSError, imaplib.IMAP4.error):
        pass
    try:
        conn.logout()
    except (OSError, imaplib.IMAP4.error):
        pass
