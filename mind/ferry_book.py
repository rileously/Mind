"""Who travels, where they go, and what was booked before.

Typing a name and a national ID into a phone, in a toilet, before a hold runs
out, is the worst part of booking a ferry - and it is the same name and the
same number every time. So they are kept: a book of people to pick from, the
routes actually travelled, and the bookings already made.

Two of these three hold personal data, so they are kept the way the bot token
is - encrypted with DPAPI for this Windows account - rather than sitting in a
settings file in plain text. A national ID identifies a person to a transport
operator and to anybody who opens config.json. Routes are island names and are
not personal, so they stay readable.

Nothing here reaches the network or the clock on its own: every function takes
what it is working on and returns a new list, so all of it can be tested
without a mailbox, a booking, or waiting a day for "recent" to change meaning.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, replace


# How much is worth keeping. A book of people is a household, not a passenger
# manifest, and a history nobody scrolls is a liability rather than a feature.
MAX_PASSENGERS = 20
MAX_ROUTES = 12
MAX_HISTORY = 25

# How many to put on a panel. More than this and the keyboard is a wall.
ROUTES_OFFERED = 4
HISTORY_OFFERED = 5


@dataclass(frozen=True)
class Traveller:
    """Somebody who has travelled before, and their ID as RTL wants it."""

    name: str
    number: str
    id_type: str = "2"
    used: int = 0
    last: float = 0.0

    @property
    def label(self) -> str:
        """Name and the tail of the ID, which is how two Mohameds are told apart."""
        tail = self.number[-4:] if len(self.number) > 4 else self.number
        return f"{self.name} · {tail}" if tail else self.name


@dataclass(frozen=True)
class Route:
    """A journey somebody actually makes, rather than one they could."""

    from_code: str
    from_name: str
    to_code: str
    to_name: str
    used: int = 0
    last: float = 0.0

    @property
    def label(self) -> str:
        return f"{self.from_name} → {self.to_name}"

    @property
    def key(self) -> str:
        return f"{self.from_code}>{self.to_code}"

    def reversed(self) -> "Route":
        """The way home, which is the other journey anybody making this one wants."""
        return Route(
            from_code=self.to_code,
            from_name=self.to_name,
            to_code=self.from_code,
            to_name=self.from_name,
        )


@dataclass(frozen=True)
class Booking:
    """One booking that was paid for, or at least held and asked about."""

    reference: str
    from_name: str
    to_name: str
    departs: str = ""
    seats: str = ""
    fare: float = 0.0
    who: str = ""
    returning: bool = False
    made: float = 0.0
    from_code: str = ""
    to_code: str = ""
    # The ID numbers that travelled, so a booking can be repeated for the same
    # people rather than for whoever happens to share their name.
    numbers: str = ""

    @property
    def label(self) -> str:
        way = " ⇄" if self.returning else ""
        return f"{self.from_name} → {self.to_name}{way}"


# -- people ---------------------------------------------------------------


def traveller_from(raw: object) -> Traveller | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name", "")).strip()
    number = str(raw.get("number", "")).strip()
    if not name or not number:
        return None
    try:
        used = int(raw.get("used", 0) or 0)
    except (TypeError, ValueError):
        used = 0
    try:
        last = float(raw.get("last", 0) or 0)
    except (TypeError, ValueError):
        last = 0.0
    return Traveller(
        name=name,
        number=number,
        id_type=str(raw.get("id_type", "2") or "2"),
        used=used,
        last=last,
    )


def traveller_to(who: Traveller) -> dict:
    return {
        "name": who.name,
        "number": who.number,
        "id_type": who.id_type,
        "used": who.used,
        "last": who.last,
    }


def remember_traveller(people, name: str, number: str, id_type: str = "2", now=None):
    """Add somebody, or count another journey for somebody already there.

    Matched on the ID rather than the name, because the ID is what identifies
    a person to RTL and two people can share a name. A name typed differently
    the second time replaces the first, since the newer spelling is the one
    just used on a ticket.
    """
    clean_name = (name or "").strip()
    clean_number = (number or "").strip().upper()
    if not clean_name or not clean_number:
        return list(people or [])
    stamp = time.time() if now is None else float(now)
    out = []
    found = False
    for who in people or []:
        if who.number.upper() == clean_number:
            found = True
            out.append(
                replace(who, name=clean_name, id_type=id_type or who.id_type,
                        used=who.used + 1, last=stamp)
            )
        else:
            out.append(who)
    if not found:
        out.append(
            Traveller(name=clean_name, number=clean_number, id_type=id_type or "2",
                      used=1, last=stamp)
        )
    return order_travellers(out)[:MAX_PASSENGERS]


def order_travellers(people):
    """Most travelled first, and the most recent of those.

    Not alphabetical: the person booking is nearly always the same one or two,
    and they should be the first buttons rather than whoever is called Aishath.
    """
    return sorted(people or [], key=lambda who: (-who.used, -who.last, who.name.lower()))


def forget_traveller(people, number: str):
    wanted = (number or "").strip().upper()
    return [who for who in people or [] if who.number.upper() != wanted]


def find_travellers(people, query: str):
    """Everybody matching what was typed, by name or by ID.

    Substring rather than prefix: people search for "maaz" as readily as for
    "Mohamed", and a book this size cannot be got wrong by being generous.
    """
    text = (query or "").strip().lower()
    if not text:
        return order_travellers(people)
    return [
        who
        for who in order_travellers(people)
        if text in who.name.lower() or text in who.number.lower()
    ]


# -- routes ---------------------------------------------------------------


def route_from(raw: object) -> Route | None:
    if not isinstance(raw, dict):
        return None
    names = (
        str(raw.get("from_code", "")).strip(),
        str(raw.get("from_name", "")).strip(),
        str(raw.get("to_code", "")).strip(),
        str(raw.get("to_name", "")).strip(),
    )
    if not all(names):
        return None
    try:
        used = int(raw.get("used", 0) or 0)
    except (TypeError, ValueError):
        used = 0
    try:
        last = float(raw.get("last", 0) or 0)
    except (TypeError, ValueError):
        last = 0.0
    return Route(*names, used=used, last=last)


def route_to(route: Route) -> dict:
    return {
        "from_code": route.from_code,
        "from_name": route.from_name,
        "to_code": route.to_code,
        "to_name": route.to_name,
        "used": route.used,
        "last": route.last,
    }


def remember_route(routes, origin, destination, now=None):
    """Count one more journey between two islands.

    ``origin`` and ``destination`` are whatever the picker deals in - anything
    with a code and a name.
    """
    from_code = str(getattr(origin, "code", "") or "").strip()
    to_code = str(getattr(destination, "code", "") or "").strip()
    if not from_code or not to_code or from_code == to_code:
        return list(routes or [])
    stamp = time.time() if now is None else float(now)
    key = f"{from_code}>{to_code}"
    out = []
    found = False
    for route in routes or []:
        if route.key == key:
            found = True
            out.append(replace(route, used=route.used + 1, last=stamp))
        else:
            out.append(route)
    if not found:
        out.append(
            Route(
                from_code=from_code,
                from_name=str(getattr(origin, "name", "") or from_code),
                to_code=to_code,
                to_name=str(getattr(destination, "name", "") or to_code),
                used=1,
                last=stamp,
            )
        )
    return order_routes(out)[:MAX_ROUTES]


def order_routes(routes):
    return sorted(routes or [], key=lambda r: (-r.used, -r.last, r.label.lower()))


def top_routes(routes, limit: int = ROUTES_OFFERED):
    """The journeys worth a button, with the way home beside the way there.

    Somebody who has gone Naivaadhoo to Kulhudhuffushi wants the reverse at
    least as often, and will not have booked it yet the first time - so it is
    offered without having been earned.
    """
    ordered = order_routes(routes)[: max(1, int(limit))]
    if len(ordered) == 1 and limit > 1:
        back = ordered[0].reversed()
        if not any(r.key == back.key for r in routes or []):
            return [ordered[0], back]
    return ordered


# -- bookings -------------------------------------------------------------


def booking_from(raw: object) -> Booking | None:
    if not isinstance(raw, dict):
        return None
    reference = str(raw.get("reference", "")).strip()
    if not reference:
        return None
    try:
        fare = float(raw.get("fare", 0) or 0)
    except (TypeError, ValueError):
        fare = 0.0
    try:
        made = float(raw.get("made", 0) or 0)
    except (TypeError, ValueError):
        made = 0.0
    return Booking(
        reference=reference,
        from_name=str(raw.get("from_name", "")),
        to_name=str(raw.get("to_name", "")),
        departs=str(raw.get("departs", "")),
        seats=str(raw.get("seats", "")),
        fare=fare,
        who=str(raw.get("who", "")),
        returning=bool(raw.get("returning", False)),
        made=made,
        from_code=str(raw.get("from_code", "")),
        to_code=str(raw.get("to_code", "")),
        numbers=str(raw.get("numbers", "")),
    )


def booking_to(booking: Booking) -> dict:
    return {
        "reference": booking.reference,
        "from_name": booking.from_name,
        "to_name": booking.to_name,
        "departs": booking.departs,
        "seats": booking.seats,
        "fare": booking.fare,
        "who": booking.who,
        "returning": booking.returning,
        "made": booking.made,
        "from_code": booking.from_code,
        "to_code": booking.to_code,
        "numbers": booking.numbers,
    }


def remember_booking(history, booking: Booking):
    """Newest first, and one entry per booking reference.

    The same reference arriving twice is the panel being rebuilt rather than a
    second journey, so it replaces rather than piles up.
    """
    kept = [item for item in history or [] if item.reference != booking.reference]
    return ([booking] + kept)[:MAX_HISTORY]


def recent_bookings(history, limit: int = HISTORY_OFFERED):
    return sorted(history or [], key=lambda b: -b.made)[: max(1, int(limit))]


def booking_by_reference(history, reference: str):
    wanted = (reference or "").strip().upper()
    for booking in history or []:
        if booking.reference.upper() == wanted:
            return booking
    return None


def ticket_file(folder, reference: str):
    """The saved PDF for a booking, if the mail watcher has brought it in.

    MTCC names the attachment after the booking reference, which is what makes
    this possible at all: the ticket that arrived by email can be handed back
    later without asking RTL for anything.
    """
    wanted = (reference or "").strip().upper()
    if not wanted or folder is None:
        return None
    try:
        for path in sorted(folder.glob("*.pdf")):
            if wanted in path.name.upper():
                return path
    except OSError:
        return None
    return None


# -- storage --------------------------------------------------------------


def encode(items, to_dict) -> str:
    return json.dumps([to_dict(item) for item in items or []])


def decode(raw: str, from_dict):
    try:
        loaded = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(loaded, list):
        return []
    out = []
    for item in loaded:
        made = from_dict(item)
        if made is not None:
            out.append(made)
    return out


def ids_in(booking) -> list:
    """The ID numbers on a booking, if it was made since they were kept."""
    return [part.strip().upper() for part in (booking.numbers or "").split(",") if part.strip()]


def bookings_for(history, traveller, limit: int = HISTORY_OFFERED):
    """Everywhere one person has been.

    Matched on their ID number where the booking recorded one, and on their
    name where it did not - bookings made before the number was kept are still
    the ones somebody is looking for, and refusing to show them would make the
    feature look broken on exactly the history that prompted it.
    """
    if traveller is None:
        return []
    number = traveller.number.upper()
    name = traveller.name.strip().lower()
    found = []
    for booking in history or []:
        numbers = ids_in(booking)
        if numbers:
            if number in numbers:
                found.append(booking)
            continue
        if name and name in (booking.who or "").lower():
            found.append(booking)
    return sorted(found, key=lambda b: -b.made)[: max(1, int(limit))]


def travellers_in(booking, book):
    """The people on a booking, as entries from the book.

    By number when the booking kept them, by name otherwise. Anybody who
    cannot be resolved is left out rather than guessed at: a booking repeated
    for the wrong person is the one failure worth being careful about here.
    """
    numbers = ids_in(booking)
    if numbers:
        by_number = {who.number.upper(): who for who in book or []}
        return [by_number[n] for n in numbers if n in by_number]
    wanted = [part.strip().lower() for part in (booking.who or "").split(",") if part.strip()]
    by_name = {who.name.strip().lower(): who for who in book or []}
    return [by_name[n] for n in wanted if n in by_name]
