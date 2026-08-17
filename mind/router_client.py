"""Asking the router who is connected.

The router is the only thing on the network that knows every device by the name
it gave when it asked for an address, which is where "Adam's iPhone" actually
lives. Nothing else can see that: a phone with a randomised MAC that answers no
scan still had to tell the router its name to get on the Wi-Fi at all.

That means signing in, so the credentials are the user's to enter and are kept
the way the Telegram token is - encrypted with DPAPI, never shown back, never
logged. Nothing is sent anywhere except to the router's own address on the local
network.

Huawei's ONT firmware varies between models, so the sign-in is attempted the
documented way and each step reports what happened rather than a bare failure.
"Wrong password" and "this model does not use that endpoint" need different
answers from whoever is reading the message.
"""

from __future__ import annotations

import base64
import http.cookiejar
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


DEFAULT_TIMEOUT = 8.0
# Where the device list lives on the models that publish one, tried in turn.
# Huawei's ONT firmwares disagree about this more than about anything else.
DEVICE_PAGES = (
    "/html/bbsp/common/GetLanUserDevInfo.asp",
    "/html/amp/lanuserinfo/lanuserinfo.asp",
    "/html/bbsp/dhcp/dhcp.asp",
    "/html/ntwk/lancfg.asp",
    "/html/bbsp/lanuserinfo/lanuserinfo.asp",
    "/html/network/lanuserinfo.asp",
    "/html/status/lanstatus.asp",
    "/api/system/HostInfo",
    "/api/ntwk/lan_user_dev",
    "/api/ntwk/wlanuser",
)
LOGIN_PAGES = ("/asp/GetRandCount.asp", "/index.asp", "/")
# A response of about this size that still looks like the login page means the
# session did not take, whatever the status code said.
LOGIN_MARKERS = ("SSLHostIp", "IsMaintWan", "loginpage", "UserName")


class RouterError(RuntimeError):
    """Something the person reading it can act on."""


@dataclass(frozen=True)
class RouterDevice:
    """One lease as the router describes it."""

    mac: str
    ip: str = ""
    hostname: str = ""


def normalise_mac(value: str) -> str:
    """The router's format, whatever it is, as the ARP table writes it."""
    hexed = re.sub(r"[^0-9a-fA-F]", "", value or "").lower()
    if len(hexed) != 12:
        return ""
    return "-".join(hexed[index : index + 2] for index in range(0, 12, 2))


def parse_devices(payload: str) -> list[RouterDevice]:
    """Read a device list out of whatever the router returned.

    Two shapes cover the Huawei firmwares: a JSON array, and the JavaScript
    array their older pages embed as "new stDevInfo(...)" rows. Both are pulled
    apart by looking for the things that are unmistakable - a MAC address, an
    address, a name - rather than by trusting a field order that differs between
    models.
    """
    devices: dict[str, RouterDevice] = {}

    stripped = (payload or "").strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            loaded = json.loads(stripped)
        except ValueError:
            loaded = None
        if loaded is not None:
            for entry in _iter_dicts(loaded):
                mac = normalise_mac(
                    str(entry.get("MACAddress") or entry.get("mac") or entry.get("MAC") or "")
                )
                if not mac:
                    continue
                devices[mac] = RouterDevice(
                    mac=mac,
                    ip=str(entry.get("IPAddress") or entry.get("ip") or ""),
                    hostname=str(
                        entry.get("HostName") or entry.get("hostname") or entry.get("Name") or ""
                    ).strip(),
                )
            if devices:
                return sorted(devices.values(), key=lambda device: device.ip)

    # The JavaScript rows. Every value in them is hex escaped - "192\x2e168"
    # rather than "192.168" - so nothing matches until that is undone.
    unescaped = _unescape_hex(payload or "")
    # Any constructor, because the name differs by firmware: this model uses
    # USERDeviceNew where others use stLanUserDevInfo.
    rows = re.findall(r"new\s+\w*(?:Device|DevInfo)\w*\s*\(([^)]*)\)", unescaped, re.S)
    for row in rows:
        fields = [field.strip().strip("\"'") for field in row.split(",")]
        mac = next((normalise_mac(field) for field in fields if normalise_mac(field)), "")
        if not mac:
            continue
        ip = next(
            (field for field in fields if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", field)), ""
        )
        devices[mac] = RouterDevice(mac=mac, ip=ip, hostname=_best_name(fields))
    return sorted(devices.values(), key=lambda device: device.ip)


def _unescape_hex(text: str) -> str:
    r"""Turn the router's "\x2e" escapes back into the characters they stand for."""
    return re.sub(
        r"\\x([0-9a-fA-F]{2})", lambda match: chr(int(match.group(1), 16)), text
    )


# The row also carries the state of things - which radio, which protocol,
# whether it is online - and every one of those reads like a word. None of them
# is anybody's phone.
ROW_VOCABULARY = frozenset(
    {
        "dhcp",
        "static",
        "wifi",
        "wired",
        "lan",
        "ethernet",
        "online",
        "offline",
        "true",
        "false",
        "unknown",
        "localhost",
        "none",
    }
)
# What a device calls itself when nobody has given it a better name: the DHCP
# client's own boilerplate.
BORING_NAMES = re.compile(r"(?i)^(android-dhcp|msft\b|dhcp\b|ssid\d*$|eth\d*$|lan\d*$)")


def _best_name(fields: list[str]) -> str:
    """The most useful name in a row, of the several it may carry.

    These rows hold both the name the DHCP client sent - "android-dhcp-13",
    which says nothing - and the name the device actually goes by, like
    "Redmi-Note-11". Anything recognisable beats the boilerplate, and the
    boilerplate is still better than nothing.
    """
    candidates = [
        field
        for field in fields
        if _looks_like_a_name(field) and field.strip().lower() not in ROW_VOCABULARY
    ]
    if not candidates:
        return ""
    useful = [name for name in candidates if not BORING_NAMES.match(name)]
    return (useful or candidates)[0]


def _looks_like_a_name(field: str) -> bool:
    """Whether a field is plausibly what someone calls their device.

    These rows also carry the router's own object paths, and
    "InternetGatewayDevice.X_Hosts.Host.1" is not a phone. A name has letters,
    is not an address, and does not read as a dotted path.
    """
    if not field or len(field) > 63:
        return False
    if normalise_mac(field) or re.fullmatch(r"[\d.]+", field):
        return False
    if field.count(".") > 1 or "InternetGatewayDevice" in field:
        return False
    return any(character.isalpha() for character in field)


def _iter_dicts(value):
    """Every dictionary anywhere in a decoded JSON body."""
    if isinstance(value, dict):
        if any(key.lower() in {"macaddress", "mac"} for key in value):
            yield value
        for nested in value.values():
            yield from _iter_dicts(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_dicts(nested)


class RouterSession:
    """A signed-in conversation with the router, and nothing more."""

    def __init__(self, address: str, timeout: float = DEFAULT_TIMEOUT):
        self.candidates = self._candidates(address)
        self.base = self.candidates[0]
        self.timeout = timeout
        self._jar = http.cookiejar.CookieJar()
        # The certificate is the router's own, on a numbered address on the local
        # network. No authority can vouch for that, and there is nothing to be
        # gained by refusing to talk to the box in the hallway: this only ever
        # speaks to the address the user typed.
        self._context = ssl._create_unverified_context()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar),
            urllib.request.HTTPSHandler(context=self._context),
        )
        self.notes: list[str] = []

    @staticmethod
    def _candidates(address: str) -> list[str]:
        """The addresses to try, in the order that works.

        These models serve a page over plain HTTP whose only content is a script
        redirecting to HTTPS - on port 80, which reads like a mistake and is not.
        Talking HTTP to them therefore returns that same shell for every path,
        which looks exactly like being signed out. HTTPS is tried first for that
        reason.
        """
        cleaned = (address or "").strip().rstrip("/")
        if not cleaned:
            raise RouterError("Enter the router's address, for example 192.168.18.1.")
        if cleaned.startswith(("http://", "https://")):
            return [cleaned]
        return [f"https://{cleaned}:80", f"https://{cleaned}", f"http://{cleaned}"]

    def choose_base(self) -> None:
        """Settle on the address that answers with something other than the shell."""
        for candidate in self.candidates:
            self.base = candidate
            try:
                status, body = self._open("/")
            except RouterError:
                continue
            if status == 200 and not self.looks_like_login(body):
                return
            if status == 200 and len(body) > 20_000:
                return
        self.base = self.candidates[0]

    def _open(
        self, path: str, data: bytes | None = None, cookie: str = ""
    ) -> tuple[int, str]:
        headers = {
            # The login page's script is checked by some firmwares against a
            # browser-shaped agent, and there is nothing to gain by being coy
            # with the box in the hallway.
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Mind",
            "Referer": self.base + "/",
        }
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if cookie:
            headers["Cookie"] = cookie
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers=headers,
            method="POST" if data is not None else "GET",
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return response.status, response.read(400_000).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(20_000).decode("utf-8", "replace")
        except urllib.error.URLError as exc:
            raise RouterError(f"Could not reach the router: {exc.reason}") from exc
        except OSError as exc:
            raise RouterError(f"Could not reach the router: {exc}") from exc

    def _token(self) -> str:
        """The one-shot value the login form posts with the password.

        Fetched the way the page's own script does: a POST to GetRandCount.asp,
        whose entire body is the token. Scraping it out of the HTML finds
        nothing, because it is not in the HTML.
        """
        status, body = self._open("/asp/GetRandCount.asp", data=b"")
        token = body.strip().lstrip("﻿").strip()
        if status == 200 and token and len(token) < 128 and "<" not in token:
            return token
        return ""

    def sign_in(self, username: str, password: str) -> None:
        """Sign in the way the login page does, step by step.

        Read out of the page's own Submit(): clear the old cookie, ask for a
        random count, set the cookie the form expects, then post the username
        with the password base64 encoded and the token alongside.
        """
        if not username or not password:
            raise RouterError("Enter the router's username and password.")
        self.choose_base()
        token = self._token()
        if not token:
            self.notes.append("the router offered no login token")
        encoded = base64.b64encode(password.encode("utf-8")).decode("ascii")
        form = {"UserName": username, "PassWord": encoded, "Language": "english"}
        if token:
            form["x.X_HW_Token"] = token
        status, body = self._open(
            "/login.cgi",
            urllib.parse.urlencode(form).encode(),
            # The page sets this itself before submitting, and the form is
            # rejected without it.
            cookie="Cookie=body:Language:english:id=-1",
        )
        if status >= 400:
            raise RouterError(
                f"The router refused the sign-in request ({status}). This model may use a "
                "different login page."
            )
        lowered = body.lower()
        if "errorcode" in lowered or ("password" in lowered and "incorrect" in lowered):
            raise RouterError("The router rejected that username or password.")

    @staticmethod
    def looks_like_login(body: str) -> bool:
        """Whether this is the sign-in shell rather than a page of content.

        These models answer 200 with their login page for every path until a
        session exists, so a status code proves nothing. Recognising the shell is
        the difference between "the list is somewhere else" and "the sign-in did
        not take", which need opposite responses.
        """
        if len(body) > 20_000:
            return False
        return sum(1 for marker in LOGIN_MARKERS if marker in body) >= 2

    def devices(self, probe_into: Path | None = None) -> list[RouterDevice]:
        """Fetch the connected list, trying the pages these models publish.

        ``probe_into`` writes what each page actually returned to a file. The
        endpoints cannot be discovered from outside a session, so when this
        fails, what the router said is the only thing that moves it forward -
        and it can be shared without sharing a password.
        """
        tried: list[str] = []
        collected: list[str] = []
        blocked = 0
        for page in DEVICE_PAGES:
            status, body = self._open(page)
            if probe_into is not None:
                collected.append(
                    f"===== {page} -> {status}, {len(body)} bytes =====\n{body[:4000]}\n"
                )
            if status != 200 or len(body) < 200:
                tried.append(f"{page} ({status})")
                continue
            if self.looks_like_login(body):
                blocked += 1
                tried.append(f"{page} (still the sign-in page)")
                continue
            found = parse_devices(body)
            if found:
                return found
            tried.append(f"{page} (no addresses in it)")

        if probe_into is not None and collected:
            try:
                probe_into.parent.mkdir(parents=True, exist_ok=True)
                probe_into.write_text("\n".join(collected), encoding="utf-8")
                self.notes.append(f"What the router returned was written to {probe_into}")
            except OSError:
                pass
        if blocked and blocked >= len(tried) - 1:
            raise RouterError(
                "The sign-in did not take: every page still came back as the router's "
                "own login screen. This firmware wants a different login request."
            )
        raise RouterError(
            "Signed in, but no device list could be read from: " + ", ".join(tried)
        )


def fetch_devices(
    address: str,
    username: str,
    password: str,
    timeout: float = DEFAULT_TIMEOUT,
    probe_into: Path | None = None,
) -> tuple[list[RouterDevice], list[str]]:
    """Sign in, take the list, and report anything odd along the way."""
    session = RouterSession(address, timeout)
    session.sign_in(username, password)
    return session.devices(probe_into), list(session.notes)
