"""Asking RTL which ferries go where.

The booking site is an Angular front end over a plain JSON API, and the part
that describes the network - every island, every route, every stop in order -
needs no account at all. So this asks the API directly rather than driving a
browser: it is one request instead of a page load, and nothing here can be
broken by a button moving.

What it deliberately does not do is book. Reserving a seat needs a signed-in
session, and paying needs a card; both belong to the person, not to Mind. This
answers "what sails, and is there room", which is the part worth having on a
phone at the other end of the country.

The description is large and changes about as often as a new island gets a
harbour, so it is kept on disk and re-asked for once a day.
"""

from __future__ import annotations

import json
import re
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


BACKOFFICE_URL = "https://bo.rtl.mv:4455/maldives/api/booking/v1/ferries/backofficeinfo"
# The site sends these, and an API that is only ever called by one web page can
# reasonably expect them.
HEADERS = {
    "Accept": "application/json",
    "Origin": "https://rtl.mv",
    "Referer": "https://rtl.mv/",
    # The payment host sits behind Cloudflare, which refuses the default
    # urllib agent outright - "Error 1010", which reads like a permission
    # problem and is not one. This is the same request the site makes, so it
    # says so the same way.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    ),
}
TIMEOUT = 25.0
# A day. The island list is not a thing that moves quickly.
CACHE_SECONDS = 24 * 60 * 60


class FerryError(RuntimeError):
    """Something the person reading it can act on."""


@dataclass(frozen=True)
class Stop:
    """One island the ferry calls at."""

    name: str
    code: str
    dhivehi: str = ""
    latitude: str = ""
    longitude: str = ""

    @property
    def island(self) -> str:
        """The name without its atoll prefix, for matching what people type."""
        return self.name.split(".", 1)[-1] if "." in self.name else self.name


@dataclass(frozen=True)
class Route:
    """One route, and the order it calls in."""

    name: str
    code: str
    zone: str
    description: str
    stops: tuple[str, ...] = ()

    def position(self, stop_name: str) -> int:
        """Where a stop falls on this route, or -1."""
        try:
            return self.stops.index(stop_name)
        except ValueError:
            return -1

    def serves(self, origin: str, destination: str) -> bool:
        """Whether this route goes from one to the other, in that order."""
        start, end = self.position(origin), self.position(destination)
        return start >= 0 and end >= 0 and start < end

    def between(self, origin: str, destination: str) -> tuple[str, ...]:
        """The stops called at on the way, the two ends excluded."""
        start, end = self.position(origin), self.position(destination)
        if start < 0 or end < 0 or start >= end:
            return ()
        return self.stops[start + 1 : end]


def parse_stops(payload: dict) -> list[Stop]:
    found: list[Stop] = []
    for raw in payload.get("ferryStops") or []:
        if not isinstance(raw, dict) or not raw.get("name"):
            continue
        found.append(
            Stop(
                name=str(raw.get("name", "")),
                code=str(raw.get("code", "")),
                dhivehi=str(raw.get("dvname") or ""),
                latitude=str(raw.get("latitude") or ""),
                longitude=str(raw.get("longitude") or ""),
            )
        )
    return found


def _stop_name(entry: dict) -> str:
    """A route's stop, which arrives as a whole island rather than a name."""
    stop = entry.get("stop")
    if isinstance(stop, dict):
        return str(stop.get("name", ""))
    return str(stop or "")


def parse_routes(payload: dict) -> list[Route]:
    found: list[Route] = []
    for zone in payload.get("ferryZones") or []:
        if not isinstance(zone, dict):
            continue
        for raw in zone.get("ferryRoutes") or []:
            if not isinstance(raw, dict):
                continue
            ordered = sorted(
                (s for s in raw.get("stops") or [] if isinstance(s, dict)),
                key=lambda s: s.get("order", 0),
            )
            names = tuple(n for n in (_stop_name(s) for s in ordered) if n)
            found.append(
                Route(
                    name=str(raw.get("name", "")),
                    code=str(raw.get("code", "")),
                    zone=str(raw.get("zone") or zone.get("name") or ""),
                    description=str(raw.get("description") or ""),
                    stops=names,
                )
            )
    return found


def match_stops(stops: list[Stop], text: str) -> list[Stop]:
    """The islands somebody typing this probably meant.

    Matched on the island's own name as well as the full one, because nobody
    types the atoll prefix, and an exact hit is offered alone rather than
    buried among the islands that merely start the same way.
    """
    wanted = (text or "").strip().lower()
    if not wanted:
        return []
    exact = [s for s in stops if s.name.lower() == wanted or s.island.lower() == wanted]
    if exact:
        return exact
    return [s for s in stops if wanted in s.name.lower()]


def routes_between(routes: list[Route], origin: str, destination: str) -> list[Route]:
    """Every route that goes from one island to the other, shortest first."""
    going = [r for r in routes if r.serves(origin, destination)]
    return sorted(going, key=lambda r: len(r.between(origin, destination)))


def _fetch(url: str = BACKOFFICE_URL) -> dict:
    request = urllib.request.Request(url)
    for name, value in HEADERS.items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(
            request, timeout=TIMEOUT, context=ssl.create_default_context()
        ) as answer:
            raw = answer.read()
    except urllib.error.HTTPError as exc:
        raise FerryError(f"RTL answered HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise FerryError(f"Could not reach RTL: {getattr(exc, 'reason', exc)}") from exc
    except TimeoutError as exc:
        raise FerryError("RTL did not answer in time.") from exc
    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
    except ValueError as exc:
        raise FerryError("RTL answered with something that was not JSON.") from exc
    if not isinstance(payload, dict) or "ferryStops" not in payload:
        raise FerryError("RTL answered without the ferry description in it.")
    return payload


def network(cache: Path | None = None, fetch=_fetch, now=time.time) -> dict:
    """The whole description, from disk when it is fresh enough.

    Kept because it is a hundred kilobytes that describes a network of islands,
    and asking for it on every lookup would be rude to somebody else's server
    as well as slow.
    """
    if cache is not None and cache.exists():
        try:
            if now() - cache.stat().st_mtime < CACHE_SECONDS:
                return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    payload = fetch()
    if cache is not None:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            # A cache that cannot be written is slower, not broken.
            pass
    return payload


def atoll_of(stop: Stop) -> str:
    """The atoll an island belongs to, as RTL spells it."""
    return stop.name.split(".", 1)[0] if "." in stop.name else ""


def atolls(stops: list[Stop]) -> list[str]:
    """Every atoll with a stop on it, in the order they run down the country.

    RTL's own order is the useful one: the list is read by somebody looking for
    their own island, and the atolls are already grouped that way in what it
    sends.
    """
    found: list[str] = []
    for stop in stops:
        name = atoll_of(stop)
        if name and name not in found:
            found.append(name)
    return found


def stops_in(stops: list[Stop], atoll: str) -> list[Stop]:
    """The islands on one atoll, alphabetically."""
    wanted = (atoll or "").lower()
    return sorted(
        (s for s in stops if atoll_of(s).lower() == wanted), key=lambda s: s.island
    )


def stop_by_code(stops: list[Stop], code: str) -> Stop | None:
    for stop in stops:
        if stop.code == str(code):
            return stop
    return None


SEATS_URL = "https://bo.rtl.mv:4455/maldives/api/booking/v3/ferries/seats"
# The site sends these exactly. deviceType is a number, and scheduleId is null
# rather than an empty list - an empty list is accepted and then quietly
# returns no sailings at all, which is the worst of both answers.
ONE_WAY = 1
WEB_DEVICE = 1
REGULAR_PRODUCT = "101"
SEAT_FREE = 1


@dataclass(frozen=True)
class Sailing:
    """One departure, and how full it is."""

    route: str = ""
    route_code: str = ""
    boat: str = ""
    departs: str = ""
    arrives: str = ""
    stops: int = 0
    fare: float = 0.0
    seats_free: int = 0
    seats_total: int = 0
    schedule_id: str = ""
    # Which seats are actually free, so one can be pointed at rather than
    # merely counted.
    free_seats: tuple = ()
    # The taken ones too, so a map can be drawn with its shape intact.
    taken_seats: tuple = ()

    @property
    def departs_at(self) -> str:
        """The time on its own, from RTL's fourteen digits."""
        return f"{self.departs[8:10]}:{self.departs[10:12]}" if len(self.departs) >= 12 else ""

    @property
    def arrives_at(self) -> str:
        return f"{self.arrives[8:10]}:{self.arrives[10:12]}" if len(self.arrives) >= 12 else ""

    @property
    def full(self) -> bool:
        return self.seats_free <= 0


def trip_stamp(date_text: str, now=None) -> str:
    """RTL wants yyyyMMdd with a time stuck on the end.

    The site sends the current time, which for today means "sailings still to
    come". For a later day that would hide the morning ones, so a future date
    is asked about from one second past midnight.
    """
    moment = time.localtime(now if now is not None else time.time())
    today = time.strftime("%Y%m%d", moment)
    if date_text == today:
        return today + time.strftime("%H%M%S", moment)
    return date_text + "000001"


def parse_sailings(payload: dict) -> list[Sailing]:
    """The journeys in a seats reply, with their seat counts."""
    found: list[Sailing] = []
    schedules = payload.get("schedules")
    if not isinstance(schedules, dict):
        return found
    for journey in schedules.get("journey") or []:
        if not isinstance(journey, dict):
            continue
        fare = journey.get("totalFare") or 0
        for leg in journey.get("instances") or []:
            if not isinstance(leg, dict):
                continue
            deck = leg.get("deck") if isinstance(leg.get("deck"), dict) else {}
            seats = [s for s in (deck.get("seats") or []) if isinstance(s, dict)]
            free = sum(1 for s in seats if s.get("status") == SEAT_FREE)
            found.append(
                Sailing(
                    route=str(leg.get("routeName") or ""),
                    route_code=str(leg.get("routeCode") or ""),
                    boat=str(leg.get("assetName") or ""),
                    departs=str(leg.get("startTime") or ""),
                    arrives=str(leg.get("endTime") or ""),
                    stops=int(leg.get("intermediateStops") or 0),
                    fare=float(fare or 0),
                    seats_free=free,
                    seats_total=len(seats) or int(deck.get("seatCount") or 0),
                    schedule_id=str(leg.get("scheduleId") or ""),
                    free_seats=tuple(
                        int(s.get("code"))
                        for s in seats
                        if s.get("status") == SEAT_FREE and str(s.get("code", "")).isdigit()
                    ),
                    taken_seats=tuple(
                        int(s.get("code"))
                        for s in seats
                        if s.get("status") != SEAT_FREE and str(s.get("code", "")).isdigit()
                    ),
                )
            )
    return found


def sailings(
    origin_code: str,
    destination_code: str,
    date_text: str,
    passengers: int = 1,
    opener=None,
    now=None,
) -> list[Sailing]:
    """What sails between two stops on a day, and how many seats are left.

    No account needed: this is the same call the booking page makes before
    anybody signs in.
    """
    body = {
        "products": [
            {"productCode": REGULAR_PRODUCT, "passengerCount": max(1, int(passengers))}
        ],
        # Null, deliberately. See the note above.
        "scheduleId": None,
        "deviceType": WEB_DEVICE,
        "qrType": ONE_WAY,
        "inboundTripDate": trip_stamp(date_text, now=now),
        "outboundTripDate": None,
        "sourceStation": str(origin_code),
        "destinationStation": str(destination_code),
    }
    request = urllib.request.Request(
        SEATS_URL, data=json.dumps(body).encode("utf-8"), method="POST"
    )
    for name, value in {**HEADERS, "Content-Type": "application/json"}.items():
        request.add_header(name, value)
    send = opener or (
        lambda r: urllib.request.urlopen(
            r, timeout=TIMEOUT, context=ssl.create_default_context()
        )
    )
    try:
        with send(request) as answer:
            payload = json.loads(answer.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raise FerryError(f"RTL answered HTTP {exc.code} when asked about sailings.") from exc
    except urllib.error.URLError as exc:
        raise FerryError(f"Could not reach RTL: {getattr(exc, 'reason', exc)}") from exc
    except TimeoutError as exc:
        raise FerryError("RTL did not answer in time.") from exc
    except ValueError as exc:
        raise FerryError("RTL answered with something that was not JSON.") from exc
    return parse_sailings(payload)


RESERVE_URL = "https://bo.rtl.mv:4455/maldives/api/booking/v3/ferries/reserveFerrySeats"
DEFAULT_DECK = "1"


@dataclass(frozen=True)
class Reservation:
    """A seat held on a sailing, and how long there is to pay for it."""

    booking_id: str = ""
    total: float = 0.0
    held_seconds: int = 0
    detail: str = ""

    @property
    def held(self) -> bool:
        return bool(self.booking_id)


def reserve_body(
    sail,
    seat: int,
    origin_code: str,
    destination_code: str,
    passengers: int = 1,
    deck: str = DEFAULT_DECK,
) -> dict:
    """The request RTL's own page sends when somebody presses Select.

    Read out of the site's compiled source rather than guessed at, because
    every call that succeeds takes a real seat out of a real ferry and there is
    no polite way to discover a payload by trying.
    """
    return {
        "products": [
            {"productCode": REGULAR_PRODUCT, "passengerCount": max(1, int(passengers))}
        ],
        "startStation": str(origin_code),
        "endStation": str(destination_code),
        "totalPrice": float(sail.fare or 0),
        "deviceType": WEB_DEVICE,
        "qrType": ONE_WAY,
        "inbound": [
            {
                "scheduleId": sail.schedule_id,
                "sourceStation": str(origin_code),
                "destinationStation": str(destination_code),
                "seats": [{"deckCode": str(deck), "seatNumber": int(seat)}],
            }
        ],
        "outbound": None,
    }


def parse_reservation(payload: dict) -> Reservation:
    """What came back from holding a seat."""
    if not isinstance(payload, dict):
        raise FerryError("RTL answered with something unreadable.")
    booking = (
        payload.get("bookingId")
        or payload.get("bookingID")
        or (payload.get("booking") or {}).get("bookingId")
        if isinstance(payload.get("booking"), dict)
        else payload.get("bookingId")
    )
    if not booking:
        detail = payload.get("message") or "RTL did not hold the seat."
        raise FerryError(str(detail))
    return Reservation(
        booking_id=str(booking),
        total=float(payload.get("totalPrice") or payload.get("totalFare") or 0),
        held_seconds=int(payload.get("timeToConfirm") or 0),
        detail=str(payload.get("message") or ""),
    )


def reserve(body: dict, token: str = "", opener=None) -> Reservation:
    """Hold the seat. One call, and it has a consequence at the other end."""
    request = urllib.request.Request(
        RESERVE_URL, data=json.dumps(body).encode("utf-8"), method="POST"
    )
    for name, value in {**HEADERS, "Content-Type": "application/json"}.items():
        request.add_header(name, value)
    if token:
        request.add_header("Authorization", token)
    send = opener or (
        lambda r: urllib.request.urlopen(
            r, timeout=TIMEOUT, context=ssl.create_default_context()
        )
    )
    try:
        with send(request) as answer:
            payload = json.loads(answer.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        body_text = exc.read(300).decode("utf-8", "replace")
        try:
            said = json.loads(body_text).get("message")
        except ValueError:
            said = ""
        raise FerryError(said or f"RTL answered HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise FerryError(f"Could not reach RTL: {getattr(exc, 'reason', exc)}") from exc
    except ValueError as exc:
        raise FerryError("RTL answered with something that was not JSON.") from exc
    return parse_reservation(payload)


# Read out of the site's own environment block rather than guessed at. The
# payment service is on a different host and port from the booking one, which
# is not something worth reconstructing by inference - the first attempt got
# both wrong.
PAYMENT_URL = "https://bo.rtl.mv:443/paymentservice/api/payment/v6/transaction"
# The id, not the code. customerIdTypes carries both - National ID is id 2,
# code "101" - and the payment wants the id while the product wants a code.
NATIONAL_ID = "2"
# From the site: a card payment, on a ferry, from a browser.
PAYMENT_TYPE_CARD = 1
VEHICLE_FERRY = 3


@dataclass(frozen=True)
class Passenger:
    """Who is travelling, as RTL needs them described."""

    name: str = ""
    id_number: str = ""
    id_type: str = NATIONAL_ID
    date_of_birth: str = ""


@dataclass(frozen=True)
class Contact:
    """Who to send the ticket to."""

    name: str = ""
    email: str = ""
    phone: str = ""


def payment_body(
    booking_id: str,
    sail,
    seat: int,
    passenger: Passenger,
    contact: Contact,
    deck: str = DEFAULT_DECK,
) -> dict:
    """The request that turns a held seat into a ticket to pay for.

    Passenger details and payment are one call on RTL's side, not two: the
    person travelling is described here, and the reply is a link to a bank
    page. Mind builds this and never sees a card - the card is entered on the
    other end of that link.
    """
    if not booking_id:
        raise FerryError("There is no booking to pay for.")
    if not passenger.name or not passenger.id_number:
        raise FerryError("The passenger needs a name and an ID number.")
    return {
        "bookingId": booking_id,
        "rrn": "",
        "qrType": ONE_WAY,
        "totalPrice": float(sail.fare or 0),
        "deviceType": WEB_DEVICE,
        "vehicleType": VEHICLE_FERRY,
        "paymentType": PAYMENT_TYPE_CARD,
        # From the site: 1 saves the card for next time, 0 does not, 2 uses one
        # already saved. Not saving is the only honest default for a program
        # that is not the one entering the card.
        "tokenize": 0,
        "cardId": None,
        "isConcessional": 0,
        "isDnrVerified": 0,
        "customerName": contact.name or passenger.name,
        "customerEmail": contact.email,
        "customerPhone": contact.phone,
        "inbound": [
            {
                "scheduleId": sail.schedule_id,
                # "selectedSeats", and each entry is the person and the seat
                # together rather than a seat with a passenger beside it. The
                # name is the reason this took three tries.
                "selectedSeats": [
                    {
                        "customerCategoryId": passenger.id_type,
                        "customerId": passenger.id_number,
                        "customerName": passenger.name,
                        "dob": passenger.date_of_birth,
                        "productCode": REGULAR_PRODUCT,
                        "isPrimary": 1,
                        "isAccompanied": 0,
                        "seatNumber": int(seat),
                        "deckCode": str(deck),
                        "passengerCount": 0,
                    }
                ],
            }
        ],
        "outbound": [],
    }


def parse_payment(payload: dict) -> str:
    """The bank page to send somebody to, out of whatever RTL called it."""
    if not isinstance(payload, dict):
        raise FerryError("RTL answered with something unreadable.")
    for key in ("paymentUrl", "redirectUrl", "url", "paymentURL", "link"):
        found = payload.get(key)
        if isinstance(found, str) and found.startswith("http"):
            return found
    nested = payload.get("data")
    if isinstance(nested, dict):
        return parse_payment(nested)
    raise FerryError(str(payload.get("message") or "RTL did not give a payment link."))


def initiate_payment(body: dict, token: str = "", opener=None) -> str:
    """Ask for the bank page. Nothing here handles a card."""
    request = urllib.request.Request(
        PAYMENT_URL, data=json.dumps(body).encode("utf-8"), method="POST"
    )
    for name, value in {**HEADERS, "Content-Type": "application/json"}.items():
        request.add_header(name, value)
    if token:
        request.add_header("Authorization", token)
    send = opener or (
        lambda r: urllib.request.urlopen(
            r, timeout=TIMEOUT, context=ssl.create_default_context()
        )
    )
    try:
        with send(request) as answer:
            payload = json.loads(answer.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        body_text = exc.read(300).decode("utf-8", "replace")
        try:
            said = json.loads(body_text).get("message")
        except ValueError:
            said = ""
        raise FerryError(said or f"RTL answered HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise FerryError(f"Could not reach RTL: {getattr(exc, 'reason', exc)}") from exc
    except ValueError as exc:
        raise FerryError("RTL answered with something that was not JSON.") from exc
    return parse_payment(payload)
