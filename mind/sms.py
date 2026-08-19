"""Reading the phone's messages from the desk.

Android keeps them in a content provider, and the shell user is allowed to
query it, so this needs no app on the phone and no permission dialog beyond the
wireless debugging the rest of Mind already uses.

Two things about that output shape the whole module. The body is printed last
and unescaped, so a message containing a comma would tear a row apart if the
fields were simply split - putting body at the end of the projection means
everything after "body=" is the message and nothing else has to be guessed. And
a message containing a newline is printed across several lines, with only the
first carrying the "Row:" marker: about half of them do. So a line without that
marker is not a malformed row, it is the rest of the message above it.

Nothing here writes to the phone. Reading messages is already the whole of what
was asked for, and sending one is a different decision with a different blast
radius.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass


# Body last, deliberately: see the note above. The separator is a colon because
# that is what "content query" takes for a projection.
PROJECTION = "_id:address:date:read:type:body"
SMS_URI = "content://sms"
# Android's own numbering: everything that is not sent was received, as far as
# a list on a desk is concerned.
TYPE_SENT = "2"
# Enough to scroll through an evening without waiting for a thousand rows.
DEFAULT_LIMIT = 300

_ROW = re.compile(r"^Row: (\d+) (.*)$", re.DOTALL)


@dataclass(frozen=True)
class Message:
    """One message, as the phone keeps it."""

    id: str = ""
    address: str = ""
    body: str = ""
    when: float = 0.0
    read: bool = True
    outgoing: bool = False

    @property
    def preview(self) -> str:
        """The message on one line, for a list."""
        flat = " ".join(self.body.split())
        return flat if len(flat) <= 90 else flat[:89] + "…"

    def when_label(self, now: float | None = None) -> str:
        """When it arrived, written the way somebody skimming would want it."""
        if not self.when:
            return ""
        moment = time.localtime(self.when)
        current = time.localtime(now if now is not None else time.time())
        if moment[:3] == current[:3]:
            return time.strftime("%H:%M", moment)
        if moment.tm_year == current.tm_year:
            return time.strftime("%d %b, %H:%M", moment)
        return time.strftime("%d %b %Y", moment)


def parse_messages(payload: str) -> list[Message]:
    """The rows "content query" printed, as messages.

    A line that does not begin a row belongs to the message before it. Anything
    arriving before the first row marker is dropped: it is a warning from the
    shell rather than part of a message.
    """
    messages: list[Message] = []
    fields: dict[str, str] = {}
    body: list[str] = []
    started = False

    def finish() -> None:
        if not started:
            return
        messages.append(_message(fields, "\n".join(body)))

    for line in (payload or "").splitlines():
        found = _ROW.match(line)
        if found is None:
            if started:
                body.append(line)
            continue
        finish()
        started = True
        head, separator, rest = found.group(2).partition("body=")
        fields = _fields(head if separator else found.group(2))
        body = [rest] if separator else []
    finish()
    return messages


def _fields(head: str) -> dict[str, str]:
    """The name=value pairs before the body."""
    found: dict[str, str] = {}
    for part in head.split(", "):
        name, separator, value = part.partition("=")
        if separator:
            found[name.strip()] = value.strip()
    return found


def _message(fields: dict[str, str], body: str) -> Message:
    try:
        # Milliseconds on the phone, seconds everywhere in Python.
        when = int(fields.get("date", "0")) / 1000
    except ValueError:
        when = 0.0
    return Message(
        id=fields.get("_id", ""),
        address=fields.get("address", ""),
        body=body,
        when=when,
        # An absent "read" is treated as read: marking real messages unread
        # because a column was missing would cry wolf on every one of them.
        read=fields.get("read", "1") != "0",
        outgoing=fields.get("type", "") == TYPE_SENT,
    )


def read_messages(phone, limit: int = DEFAULT_LIMIT, timeout: float = 30.0) -> list[Message]:
    """The newest messages on the phone, newest first.

    The limit rides on the sort argument because "content query" has nowhere
    else to put one, and a phone with several thousand messages should not have
    to hand over all of them to show the last screenful.
    """
    count = max(1, int(limit))
    # Quoted for the shell on the phone, not for this one. adb hands the
    # arguments to a shell at the far end, which splits them again, so a sort
    # of "date DESC LIMIT 300" arrives as four arguments and content prints its
    # usage. The quotes make it one argument there. Nothing user-supplied goes
    # in - the count is an integer and the rest is a constant - so this stays a
    # list of arguments rather than becoming a command line.
    payload = phone.shell(
        "content",
        "query",
        "--uri",
        SMS_URI,
        "--projection",
        PROJECTION,
        "--sort",
        f"'date DESC LIMIT {count}'",
        timeout=timeout,
    )
    return parse_messages(payload)


def matching(messages: list[Message], text: str) -> list[Message]:
    """Messages worth showing for what was typed into the search box."""
    wanted = (text or "").strip().lower()
    if not wanted:
        return list(messages)
    return [
        message
        for message in messages
        if wanted in message.body.lower() or wanted in message.address.lower()
    ]


def unread(messages: list[Message]) -> int:
    return sum(1 for message in messages if not message.read and not message.outgoing)
