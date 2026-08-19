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
