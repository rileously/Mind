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
from collections.abc import Callable
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
    # What someone typed into the router's own page for this device. A real
    # name, chosen by a person, and better than anything the device says.
    alias: str = ""
    # What sort of thing it is, read from the DHCP client's boilerplate.
    kind: str = ""
    # Which way in it took: "SSID2" for a Wi-Fi network, "" for a cable. The
    # row has always carried this; nothing read it until a network could be
    # switched off, at which point which network a device is on is the first
    # thing anyone would want to know.
    network: str = ""

    @property
    def name(self) -> str:
        """The best name the router holds: the one a person chose, first."""
        return self.alias or self.hostname


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
                    hostname=_first_real_name(
                        entry.get(key) for key in ("HostName", "hostname", "Name")
                    ),
                )
            if devices:
                return sorted(devices.values(), key=lambda device: device.ip)

    # The JavaScript rows. Every value in them is hex escaped - "192\x2e168"
    # rather than "192.168" - so nothing matches until that is undone.
    unescaped = _unescape_hex(payload or "")
    signatures = row_signatures(unescaped)
    # Any constructor, because the name differs by firmware: this model uses
    # USERDeviceNew where others use stLanUserDevInfo.
    rows = re.findall(
        r"new\s+(\w*(?:Device|DevInfo)\w*)\s*\(([^)]*)\)", unescaped, re.S
    )
    for constructor, row in rows:
        fields = [field.strip().strip("\"'") for field in row.split(",")]
        device = _device_from_signature(fields, signatures.get(constructor, ()))
        if device is None:
            device = _device_from_guesswork(fields)
        if device is not None:
            devices[device.mac] = device
    return sorted(devices.values(), key=lambda device: device.ip)


def row_signatures(payload: str) -> dict[str, tuple[str, ...]]:
    """The field names each row constructor declares, in the order it takes them.

    The page defines its own rows - "function USERDeviceNew(Domain, IpAddr,
    MacAddr, Port, ...)" - and then calls that function once per device. Reading
    the declaration is the difference between knowing which field is which and
    guessing: the tenth value is the host name because the page says the tenth
    parameter is HostName, not because it looked more like a name than the rest.

    Field order differs between firmwares, which is why it was guessed at
    before. It does not have to be: every firmware that writes these rows also
    writes the function that receives them.
    """
    found: dict[str, tuple[str, ...]] = {}
    for name, params in re.findall(
        r"function\s+(\w*(?:Device|DevInfo)\w*)\s*\(([^)]*)\)", payload
    ):
        fields = tuple(param.strip() for param in params.split(",") if param.strip())
        if fields:
            found[name] = fields
    return found


def _device_from_signature(
    fields: list[str], signature: tuple[str, ...]
) -> RouterDevice | None:
    """One device, read by the names the page gave its own columns."""
    if not signature:
        return None
    row = dict(zip(signature, fields))
    mac = normalise_mac(row.get("MacAddr", "") or row.get("MACAddress", ""))
    if not mac:
        return None
    return RouterDevice(
        mac=mac,
        ip=_plain(row.get("IpAddr", "") or row.get("IPAddress", "")),
        hostname=_real_name(row.get("HostName", "")),
        alias=_real_name(row.get("UserDevAlias", "")),
        kind=device_kind(row.get("DevType", "")),
        network=_wifi_network(row.get("Port", "")),
    )


def _wifi_network(value: str) -> str:
    """Which Wi-Fi network a device came in on, where it was one.

    The same field names the wired ports too - "LAN1" - and a cable is not a
    network you can switch off, so only the SSIDs are answered for.
    """
    text = _plain(value)
    if not re.fullmatch(r"(?i)ssid[-_]?\d+", text):
        return ""
    # Spelled the one way, so it can be matched against Ssid.port whichever
    # way this firmware happened to write it.
    return text.upper().replace("-", "").replace("_", "")


def _device_from_guesswork(fields: list[str]) -> RouterDevice | None:
    """One device from a row whose constructor was never declared."""
    mac = next((normalise_mac(field) for field in fields if normalise_mac(field)), "")
    if not mac:
        return None
    ip = next(
        (field for field in fields if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", field)), ""
    )
    return RouterDevice(mac=mac, ip=ip, hostname=_best_name(fields))


def _plain(value: str) -> str:
    """A value with the router's own placeholder for nothing taken out."""
    text = (value or "").strip()
    return "" if text == "--" else text


def _real_name(value: str) -> str:
    """A name from a named field, or nothing where it is not one.

    Knowing which column a value sits in is not the same as trusting it: a
    truncated page can put half a row into a name, so it still has to look like
    one, and boilerplate is still boilerplate wherever it was found.
    """
    text = _plain(value)
    if not text or not _looks_like_a_name(text) or BORING_NAMES.match(text):
        return ""
    return text


# The DHCP client's boilerplate says nothing about who owns a device and
# everything about what it is: every Android phone sends the same string, with
# the version on the end.
ANDROID_CLIENT = re.compile(r"(?i)^android-dhcp-(\d{1,2})$")


def device_kind(value: str) -> str:
    """What sort of thing this is, out of the name it refused to give.

    Useless as a name - eight phones would all read "Android 15", and a list of
    eight identical labels is the thing being avoided - but a straight answer to
    what the device is, worth showing beside the name rather than as it.
    "android-dhcp-15" is a phone running Android 15; "MSFT 5.0" is Windows.
    """
    text = _plain(value)
    version = ANDROID_CLIENT.match(text)
    if version:
        return f"Android {version.group(1)}"
    if text.lower().startswith("msft"):
        return "Windows"
    return ""


def _first_real_name(values) -> str:
    """The first of these keys holding a name rather than boilerplate.

    A firmware that answers in JSON puts the same nothing in HostName that the
    JavaScript rows carry - the SSID for a phone that sent no name of its own,
    "MSFT 5.0" for a Windows PC - and it may hold something real under Name
    while HostName is boilerplate. The keys are read in order of preference and
    the first useful one wins, rather than the first one merely present.
    """
    names = [str(value or "").strip() for value in values]
    return next((name for name in names if name and not BORING_NAMES.match(name)), "")


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
    "Redmi-Note-11". Anything recognisable beats the boilerplate.

    Where only boilerplate is left, the row is treated as having no name at all.
    A phone that sent no host name leaves the SSID it joined as the only
    name-shaped field in its row, and four phones on SSID2 all came through
    called SSID2 - one label, four devices, four block buttons that could not be
    told apart. Saying nothing lets the local scan's name stand, and failing
    that lets the address speak: Device 26 is at least one you can point at.
    """
    candidates = [
        field
        for field in fields
        if _looks_like_a_name(field) and field.strip().lower() not in ROW_VOCABULARY
    ]
    useful = [name for name in candidates if not BORING_NAMES.match(name)]
    return useful[0] if useful else ""


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
        # A single dot is a valid string and not a valid host. Left to urllib it
        # raises a UnicodeError out of the IDNA encoder rather than anything a
        # person could act on, and that error travelled far enough to stop the
        # network scan that does not need a router at all.
        host = cleaned.split("//")[-1].split("/")[0].split(":")[0]
        if not any(character.isalnum() for character in host):
            raise RouterError(
                f"{cleaned!r} is not a router address. It should look like "
                "192.168.18.1."
            )
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
        self,
        path: str,
        data: bytes | None = None,
        cookie: str = "",
        referer: str = "",
    ) -> tuple[int, str]:
        headers = {
            # The login page's script is checked by some firmwares against a
            # browser-shaped agent, and there is nothing to gain by being coy
            # with the box in the hallway.
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Mind",
            "Referer": referer or self.base + "/",
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

    def read(self, path: str) -> tuple[int, str]:
        """Fetch one page. A GET, so nothing on the router is changed by it."""
        return self._open(path)

    def post(self, path: str, body: bytes, referer: str = "") -> tuple[int, str]:
        """Send one form. This writes, so every caller of it is a change."""
        return self._open(path, data=body, referer=referer)

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


# -- finding the page that blocks a device ---------------------------------
#
# Blocking a device means writing to the router, and the page that does it
# differs by firmware exactly as the device list does - only worse, because a
# wrong guess at a form that writes changes a setting rather than returning
# nothing. So the endpoint is found before it is used: this reads and never
# writes, and reports what it saw.
#
# Guessing paths alone found the device list only after several attempts. The
# router also publishes its own menu, which names every page it has, so what is
# read there is followed too - a directory as well as a guess.

# The paths these firmwares are known to use, tried first.
FILTER_HINT_PAGES = (
    "/html/bbsp/wlanfilter/wlanfilter.asp",
    "/html/bbsp/wlanmacfilter/wlanmacfilter.asp",
    "/html/bbsp/macfilter/macfilter.asp",
    "/html/bbsp/wlanaccess/wlanaccess.asp",
    "/html/bbsp/wlanadvance/wlanadvance.asp",
    "/html/bbsp/parentctrl/parentctrl.asp",
    "/html/bbsp/common/GetWlanFilterInfo.asp",
    "/html/ssmp/accesscontrol/accesscontrol.asp",
    "/html/amp/parentcontrol/parentcontrol.asp",
    "/api/ntwk/wlanfilter",
    "/api/ntwk/macfilter",
)
# Where the router lists its own pages. The frameset at "/" leads to the rest.
MENU_PAGES = ("/", "/index.asp", "/html/index.asp", "/html/ssmp/common/menu.asp")
# What a page about blocking says that no other page does.
FILTER_MARKERS = (
    "MacFilter",
    "WlanFilter",
    "FilterMode",
    "AclMode",
    "X_HW_WlanFilter",
    "blacklist",
    "Blacklist",
    "DenyList",
    "AccessControl",
    "ParentCtrl",
    "TimeRule",
)
# A path worth following out of a menu, rather than every page the router has.
FILTER_WORDS = re.compile(
    r"(?i)(macfilter|wlanfilter|filter|acl|access|parent|block|deny|forbid|control)"
)
# Enough to walk a menu, few enough that this stays a look rather than a flood.
MAX_SURVEY_REQUESTS = 40


@dataclass(frozen=True)
class FilterPage:
    """One page the survey fetched, and what it appeared to be."""

    path: str
    status: int
    size: int
    markers: tuple[str, ...] = ()
    endpoints: tuple[str, ...] = ()

    @property
    def promising(self) -> bool:
        """Whether this reads as the page that keeps a block list.

        One marker is a coincidence - "control" appears in half the pages a
        router serves. Two mean the page is about filtering by address.
        """
        return len(self.markers) >= 2


def find_markers(body: str) -> tuple[str, ...]:
    """The filter vocabulary present in a page, in the order listed above."""
    text = _unescape_hex(body or "")
    return tuple(marker for marker in FILTER_MARKERS if marker in text)


def find_endpoints(body: str) -> tuple[str, ...]:
    """The places a page submits to.

    This is what blocking will need: the change is a POST to one of these, and
    reading them off the page beats guessing at them the way the device list was
    guessed at.
    """
    text = _unescape_hex(body or "")
    found: list[str] = []
    patterns = (
        r"""(?i)(?:action\s*=\s*|url\s*[:=]\s*)["']([^"']{2,120}?\.(?:cgi|asp))["']""",
        r"""(?i)["']([^"']{0,80}?(?:set|add|del|delete|apply)\.cgi[^"']{0,60})["']""",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            value = match.group(1).strip()
            if value and value not in found:
                found.append(value)
    return tuple(found[:12])


def harvest_paths(body: str, from_path: str) -> list[str]:
    """Every page this one links to, as paths from the root.

    A menu writes its links relative to itself - "../wlanfilter/wlanfilter.asp"
    - so they mean nothing until they are resolved against the page they were
    found on.
    """
    text = _unescape_hex(body or "")
    base = from_path if from_path.endswith("/") else from_path.rsplit("/", 1)[0] + "/"
    paths: list[str] = []
    for match in re.finditer(
        r"""["'(]([^"'()\s]{2,160}?\.(?:asp|cgi|html))["')]""", text
    ):
        raw = match.group(1)
        if raw.startswith(("http://", "https://", "//", "javascript:")):
            continue
        # Resolved against a stand-in origin, because urljoin on a bare path
        # leaves "../wlanfilter/wlanfilter.asp" relative - and a path without a
        # leading slash is not a page the router can be asked for.
        resolved = urllib.parse.urlsplit(
            urllib.parse.urljoin("http://router" + base, raw)
        ).path
        if resolved and resolved not in paths:
            paths.append(resolved)
    return paths


def survey_summary(pages: list[FilterPage]) -> list[str]:
    """What the survey found, in one line a person can read at a glance.

    The name of the page and nothing else. Every marker and every form it
    submits to is worth keeping, but on a status line four of them run to five
    wrapped lines and say less than one - so the detail goes to the file, which
    is where anyone acting on it will be looking anyway.
    """
    promising = [page.path for page in pages if page.promising]
    if not promising:
        answered = sum(1 for page in pages if page.status == 200)
        return [
            f"No page looked like a block list. {answered} of {len(pages)} paths answered."
        ]
    first, rest = promising[0], promising[1:]
    line = f"Found the block list: {first}"
    if rest:
        line += f" (and {len(rest)} more)"
    return [line + "."]


def survey_report(pages: list[FilterPage]) -> str:
    """The whole of what was found, for the file rather than the window."""
    lines = []
    for page in pages:
        if not page.promising:
            continue
        lines.append(f"{page.path}")
        lines.append(f"    markers: {', '.join(page.markers)}")
        for endpoint in page.endpoints:
            lines.append(f"    submits: {endpoint}")
    answered = [page for page in pages if page.status == 200]
    lines.append(f"{len(answered)} of {len(pages)} paths answered.")
    return "\n".join(lines)


class FilterSurvey:
    """A read-only look for the page that blocks a device, on a live session."""

    def __init__(self, session: RouterSession, limit: int = MAX_SURVEY_REQUESTS):
        self.session = session
        self.limit = limit
        self.pages: list[FilterPage] = []
        self.bodies: list[str] = []
        self.seen: set[str] = set()

    def run(self) -> list[FilterPage]:
        queue = list(MENU_PAGES) + list(FILTER_HINT_PAGES)
        while queue and len(self.seen) < self.limit:
            path = queue.pop(0)
            if path in self.seen:
                continue
            self.seen.add(path)
            body = self._fetch(path)
            if body is None:
                continue
            for found in harvest_paths(body, path):
                # Only pages whose name suggests they are about blocking. The
                # router publishes hundreds, and this is a look, not a crawl.
                if found not in self.seen and FILTER_WORDS.search(found):
                    queue.append(found)
        # The Wi-Fi list first among the pages that qualify. This router keeps
        # two - one for the wired side, one for the wireless - and the one
        # asked about is the one that puts a phone off the Wi-Fi.
        self.pages.sort(
            key=lambda page: (
                not page.promising,
                "wlan" not in page.path.lower(),
                page.path,
            )
        )
        return self.pages

    def _fetch(self, path: str) -> str | None:
        """Read one page and record what it was. GET only: nothing is changed."""
        try:
            status, body = self.session.read(path)
        except RouterError:
            return None
        markers = find_markers(body) if status == 200 else ()
        endpoints = find_endpoints(body) if status == 200 else ()
        self.pages.append(FilterPage(path, status, len(body), markers, endpoints))
        if status != 200 or self.session.looks_like_login(body):
            return None
        self.bodies.append(
            f"===== {path} -> {status}, {len(body)} bytes, "
            f"markers: {', '.join(markers) or 'none'} =====\n{body[:6000]}\n"
        )
        return body

    def write_probe(self, probe_into: Path) -> bool:
        """Keep what the pages returned, so this can be read without the router.

        What was found goes at the top, because the pages themselves are tens of
        thousands of characters and the answer should not have to be dug for.
        """
        if not self.bodies:
            return False
        report = survey_report(self.pages)
        try:
            probe_into.parent.mkdir(parents=True, exist_ok=True)
            probe_into.write_text(
                "----- what this survey found -----\n"
                + report
                + "\n\n"
                + "\n".join(self.bodies),
                encoding="utf-8",
            )
        except OSError:
            return False
        return True


def survey_filters(
    address: str,
    username: str,
    password: str,
    timeout: float = DEFAULT_TIMEOUT,
    probe_into: Path | None = None,
) -> tuple[list[FilterPage], list[str]]:
    """Sign in and look for the page that blocks a device. Reads, never writes."""
    session = RouterSession(address, timeout)
    session.sign_in(username, password)
    survey = FilterSurvey(session)
    pages = survey.run()
    notes = list(session.notes)
    if probe_into is not None and survey.write_probe(probe_into):
        notes.append(f"What those pages returned was written to {probe_into}")
    return pages, notes


# -- keeping a device off the Wi-Fi ----------------------------------------
#
# The survey found where this firmware keeps its block list, and this is the
# writing that follows from it. The router calls it the WLAN MAC filter: a list
# of addresses and a mode. In blacklist mode the listed devices are refused,
# which is the only mode Mind will use - whitelist means "refuse everything
# else", and one wrong click there takes the whole house off the Wi-Fi.
#
# The router will not change the mode quietly either: its own page warns that
# switching modes deletes every rule. So Mind adds to the list the router is
# already keeping, and refuses to touch a router set the other way.
#
# A rule is per SSID, so blocking a phone on the 2.4 GHz network alone would
# leave it free to join the 5 GHz one. Blocking means every SSID the router has.
#
# Every SSID it has, including the ones that are switched off. A rule against a
# network nobody can join looks like a wasted request, and was skipped for that
# reason - but a switched-off network is one click from being on again, and
# Mind now has that click. Blocking a phone while the guest network is down and
# finding it back on the Wi-Fi the moment somebody raises that network is not a
# block. The extra request is the cheaper half of that trade.

FILTER_PAGE = "/html/bbsp/wlanmacfilter/wlanmacfilter.asp"
FILTER_ADD = (
    "/html/bbsp/wlanmacfilter/add.cgi?x=InternetGatewayDevice.X_HW_Security.WLANMacFilter"
    "&RequestFile=html/bbsp/wlanmacfilter/wlanmacfilter.asp"
)
FILTER_DELETE = (
    "/html/bbsp/wlanmacfilter/del.cgi?x=InternetGatewayDevice.X_HW_Security.WLANMacFilter"
    "&RequestFile=html/bbsp/wlanmacfilter/wlanmacfilter.asp"
)
FILTER_SWITCH = (
    "/html/bbsp/wlanmacfilter/set.cgi?x=InternetGatewayDevice.X_HW_Security"
    "&RequestFile=html/bbsp/wlanmacfilter/wlanmacfilter.asp"
)
WLAN_LIST_PAGE = "/html/amp/common/wlan_list.asp"

# The wired list. The same page written twice by the same people: the same
# token, the same three forms, the same two settings - under different names,
# with no SSID because a cable is not a network you choose, and with the fields
# of a row in a different order.
WIRED_PAGE = "/html/bbsp/macfilter/macfilter.asp"
WIRED_ADD = (
    "/html/bbsp/macfilter/add.cgi?x=InternetGatewayDevice.X_HW_Security.MacFilter"
    "&RequestFile=html/bbsp/macfilter/macfilter.asp"
)
WIRED_DELETE = (
    "/html/bbsp/macfilter/del.cgi?x=InternetGatewayDevice.X_HW_Security.MacFilter"
    "&RequestFile=html/bbsp/macfilter/macfilter.asp"
)
WIRED_SWITCH = (
    "/html/bbsp/macfilter/set.cgi?x=InternetGatewayDevice.X_HW_Security"
    "&RequestFile=html/bbsp/macfilter/macfilter.asp"
)


@dataclass(frozen=True)
class FilterKind:
    """One block list on the router, and the names it happens to use.

    Two of these exist and a device can arrive by either road, so blocking that
    writes to one of them is a block that works until somebody plugs in a cable.
    Everything that differs between them is a value here rather than a branch
    somewhere in the writing.
    """

    key: str
    label: str
    page: str
    add: str
    delete: str
    switch: str
    right_field: str
    name_field: str
    per_ssid: bool = False


WIFI_FILTER = FilterKind(
    "wifi",
    "Wi-Fi",
    FILTER_PAGE,
    FILTER_ADD,
    FILTER_DELETE,
    FILTER_SWITCH,
    "x.WlanMacFilterRight",
    "x.DeviceName",
    per_ssid=True,
)
WIRED_FILTER = FilterKind(
    "wired",
    "wired",
    WIRED_PAGE,
    WIRED_ADD,
    WIRED_DELETE,
    WIRED_SWITCH,
    "x.MacFilterRight",
    "x.DeviceAlias",
)
FILTER_KINDS = (WIFI_FILTER, WIRED_FILTER)


@dataclass(frozen=True)
class Ssid:
    """One of the networks the router broadcasts."""

    index: int
    name: str = ""
    band: str = ""
    # The object the router keeps this network's settings under. Read off the
    # page rather than built from the index, because it is what a write has to
    # be addressed to and the router is the one that knows it.
    domain: str = ""
    # Whether it is being broadcast at the moment. A network that is switched
    # off still exists, still has a name, and can be switched back on.
    enabled: bool = True

    @property
    def field(self) -> str:
        """What the filter form calls it: SSID-1, SSID-5, and so on."""
        return f"SSID-{self.index}"

    @property
    def port(self) -> str:
        """What the device list calls it: SSID1, SSID5, and so on.

        The same network under a third spelling. The filter form hyphenates it,
        the device rows do not, and neither matches the object path - so the
        translation lives here rather than at each of the three call sites.
        """
        return f"SSID{self.index}"

    @property
    def label(self) -> str:
        """This network as a person would say it: the name, then the band."""
        name = self.name or self.field
        return f"{name} ({self.band})" if self.band else name


@dataclass(frozen=True)
class BlockEntry:
    """One rule in the router's list: this address, on this network."""

    domain: str
    mac: str
    ssid: str = ""
    name: str = ""


@dataclass(frozen=True)
class BlockState:
    """The block list as the router currently has it."""

    on: bool = False
    blacklist: bool = True
    entries: tuple[BlockEntry, ...] = ()
    token: str = ""
    # Whether the page actually said. The two lists are the same page written
    # twice and they do not use the same names for these settings, so a value
    # that was not found has to be told apart from one that was found to be
    # off. Read as "off", an unfound switch made every rule on that page
    # invisible: a phone genuinely refused by the router read as online, which
    # is a block nobody in Mind could see and so nobody could undo.
    switch_read: bool = True
    mode_read: bool = True

    def rules_for(self, mac: str) -> tuple[BlockEntry, ...]:
        wanted = normalise_mac(mac)
        return tuple(entry for entry in self.entries if entry.mac == wanted)

    @property
    def enforcing(self) -> bool:
        """Whether rules on this page are being acted on.

        A rule in a list nobody is enforcing is not a block. A rule in a list
        whose switch Mind could not find is treated as one anyway: the router
        holds it either way, and a rule that can be seen and removed is a far
        smaller mistake than one that is refusing a device Mind insists is fine.
        """
        return self.on or not self.switch_read

    def blocks(self, mac: str) -> bool:
        """Whether this address is actually being kept off right now."""
        return self.enforcing and self.blacklist and bool(self.rules_for(mac))

    @property
    def blocked_macs(self) -> tuple[str, ...]:
        if not (self.enforcing and self.blacklist):
            return ()
        return tuple(dict.fromkeys(entry.mac for entry in self.entries))


def _ssid_key(value: str) -> str:
    """One network under any of its spellings: SSID-2, SSID2, ssid_2."""
    return re.sub(r"[^0-9a-z]", "", (value or "").lower())


def router_mac(mac: str) -> str:
    """An address as this form wants it typed: AA:BB:CC:DD:EE:FF."""
    normalised = normalise_mac(mac)
    if not normalised:
        raise RouterError(f"{mac!r} is not a MAC address.")
    return normalised.replace("-", ":").upper()


def parse_ssids(payload: str) -> tuple[Ssid, ...]:
    """Every network the router defines, from its own WLAN list.

    Every one, not only the ones on the air. A network that is switched off is
    still a network: it has a name, it keeps its settings, and it is the one
    thing a person might want to switch back on - which cannot be offered from
    a list it was filtered out of.

    Whether each is currently broadcasting comes back as a field, so a caller
    that only wants the live ones can still say so.
    """
    found: dict[int, Ssid] = {}
    for row in re.findall(r"new stWlanInfo\(([^)]*)\)", _unescape_hex(payload or "")):
        fields = [field.strip().strip("\"'") for field in row.split(",")]
        if len(fields) < 4:
            continue
        domain, _interface, name, enable = fields[0], fields[1], fields[2], fields[3]
        instance = domain.rsplit(".", 1)[-1]
        if not instance.isdigit():
            continue
        band = fields[5] if len(fields) > 5 else ""
        found[int(instance)] = Ssid(
            int(instance), name, band, domain=domain, enabled=enable == "1"
        )
    return tuple(found[index] for index in sorted(found))


def read_networks(session: RouterSession) -> tuple[Ssid, ...]:
    """The router's own list of the networks it defines.

    One reading serves both the blocking, which writes a rule per network, and
    the switch that puts a whole network on or off the air.
    """
    status, body = session.read(WLAN_LIST_PAGE)
    if status != 200 or session.looks_like_login(body):
        raise RouterError("The router did not say which networks it broadcasts.")
    found = parse_ssids(body)
    if not found:
        raise RouterError("The router listed no Wi-Fi networks.")
    return found


# -- putting a whole network on or off the air -----------------------------
#
# Blocking names one device. This is the other end of the same wish: a guest
# network or a children's network that should simply not be there for a while,
# whoever owns the phone trying to join it.
#
# The router keeps each network's settings under its own object -
# "...WLANConfiguration.2" - and that path is read off the WLAN list rather
# than built from the index, because the router is the one that knows it. The
# write is the same shape as the filter's on/off switch: a set.cgi naming that
# object, one field, and the page's token last.
#
# Which page carries that form differs by firmware, and a write has to quote a
# token from the page it claims to come from. So the page is found by reading
# the candidates in turn and keeping the first that answers with a token - a
# read, before anything is changed.
WLAN_PAGES = (
    "/html/bbsp/wlanbasic/wlanbasic.asp",
    "/html/amp/wlanbasic/wlanbasic.asp",
    "/html/bbsp/wlan/wlanbasic.asp",
    "/html/amp/common/wlan_list.asp",
)


@dataclass(frozen=True)
class WlanForm:
    """The page a network's on/off switch is written through.

    The path only. Not the token that was on it when it was found: a token is
    spent by the write that quotes it, so one kept here would be stale by the
    time anything used it. Each write reads the page again for a fresh one.
    """

    path: str

    def set_url(self, domain: str) -> str:
        """Where a change to this network's settings is posted."""
        return (
            f"{self.path.rsplit('/', 1)[0]}/set.cgi"
            f"?x={urllib.parse.quote(domain, safe='.')}"
            f"&RequestFile={self.path.lstrip('/')}"
        )


class WlanNetworks:
    """The router's Wi-Fi networks, on a session that is already signed in.

    Reading them needs no form at all. Changing one does, so the page that
    carries it is looked for only when something is actually being changed -
    listing the networks on a router whose firmware keeps that form somewhere
    Mind cannot find must still work.
    """

    def __init__(self, session: RouterSession):
        self.session = session
        self._form: WlanForm | None = None

    # -- reading ---------------------------------------------------------

    def all(self) -> tuple[Ssid, ...]:
        """Every network the router defines, on the air or not."""
        return read_networks(self.session)

    def form(self) -> WlanForm:
        """The page whose token a change has to quote, found by reading.

        A page qualifies by carrying a token, not by existing - a firmware that
        serves the path but not the form would otherwise be written to and
        refused. Which page it was is remembered for the session; the token on
        it is not.
        """
        if self._form is not None:
            return self._form
        tried: list[str] = []
        for path in WLAN_PAGES:
            try:
                status, body = self.session.read(path)
            except RouterError:
                continue
            tried.append(path)
            if status != 200 or self.session.looks_like_login(body):
                continue
            if _page_token(body):
                self._form = WlanForm(path)
                return self._form
        raise RouterError(
            "Mind could not find the page this router keeps its Wi-Fi settings on, "
            "so it will not guess at one - a wrong guess here writes to a form that "
            f"changes something else. Pages tried: {', '.join(tried) or 'none answered'}."
        )

    def _fresh_token(self, form: WlanForm) -> str:
        """The token as the settings page carries it right now."""
        status, body = self.session.read(form.path)
        if status != 200 or self.session.looks_like_login(body):
            raise RouterError("The router asked for a sign-in again part way through.")
        token = _page_token(body)
        if not token:
            raise RouterError(
                "The router's Wi-Fi page carried no token, so nothing can be changed "
                "safely. It refuses any write without one."
            )
        return token

    # -- writing ---------------------------------------------------------

    def set_enabled(self, index: int, on: bool) -> Ssid:
        """Put one network on or off the air, and read back what happened.

        The answer comes from asking the router again rather than from the fact
        that it accepted the form. A firmware that takes the write and ignores
        it is the failure worth catching here: the network is still there, the
        button says it is not, and nobody finds out until a phone joins.
        """
        network = next((item for item in self.all() if item.index == index), None)
        if network is None:
            raise RouterError(f"This router has no network {index}.")
        if not network.domain:
            raise RouterError(
                f"The router did not say where it keeps the settings for "
                f"{network.label}, so Mind cannot change it."
            )
        if network.enabled == on:
            return network
        form = self.form()
        self._send(
            form,
            network.domain,
            {"x.Enable": "1" if on else "0", "x.X_HW_Token": self._fresh_token(form)},
        )
        after = next((item for item in self.all() if item.index == index), None)
        if after is None or after.enabled != on:
            raise RouterError(
                f"The router accepted the change but {network.label} is still "
                f"{'off' if on else 'on'} the air. This firmware may keep that "
                "switch somewhere else."
            )
        return after

    def _send(self, form: WlanForm, domain: str, fields: dict[str, str]) -> None:
        """One write, token last, with the settings page named as the referer.

        The same two rules the filter forms turned out to have, for the same
        reason: these pages are only ever reached from themselves, and anything
        after the token in the body is answered with a 403 whatever it holds.
        """
        ordered = [
            (key, value) for key, value in fields.items() if key != "x.X_HW_Token"
        ]
        ordered.append(("x.X_HW_Token", fields.get("x.X_HW_Token", "")))
        status, answer = self.session.post(
            form.set_url(domain),
            urllib.parse.urlencode(ordered).encode(),
            referer=self.session.base + form.path,
        )
        if status >= 400:
            raise RouterError(f"The router refused the change ({status}).")
        if self.session.looks_like_login(answer):
            raise RouterError("The router asked for a sign-in again part way through.")


def _page_token(payload: str) -> str:
    """The hidden one-shot value a settings page carries for its own forms."""
    found = re.search(
        r"""name\s*=\s*["']onttoken["'][^>]*?value\s*=\s*["']([^"']+)["']""",
        _unescape_hex(payload or ""),
    )
    return found.group(1) if found else ""


def page_settings(text: str) -> dict[str, str]:
    """Every "var name = 'value'" the page declares, keyed by a plain name."""
    # Either quote: the filter pages use single ones, and a firmware that
    # writes the same setting with double quotes is still stating it.
    return {
        name.lower(): value
        for name, value in re.findall(r"""var\s+(\w+)\s*=\s*['"]([^'"]*)['"]""", text)
    }


def _setting_named(
    settings: dict[str, str], *meanings: Callable[[str], bool]
) -> str | None:
    """The setting whose name means this, or None where none does.

    The readings are tried in the order given, so a page carrying two names for
    the same thing is read by the clearer of them rather than by whichever the
    page happened to declare first.
    """
    for means in meanings:
        found = next((value for name, value in settings.items() if means(name)), None)
        if found is not None:
            return found
    return None


# Whether a filter list is on. The wireless page calls it enableFilter; the
# wired page is the same page written twice by the same people and is under no
# obligation to agree, and the field each one is written back through -
# WlanMacFilterRight, MacFilterRight - is the second name these settings go by.
# Read by one spelling, an unfound switch made every rule on that page look
# inert, which is a real block that nothing in Mind could see.
SWITCH_NAMES = (
    lambda name: "filter" in name and "enable" in name,
    lambda name: "filter" in name and name.endswith("right"),
)
# Whether the list says who is refused or who is allowed.
MODE_NAMES = (
    lambda name: name in {"mode", "policy"},
    lambda name: "filter" in name and ("mode" in name or "policy" in name),
)


def parse_block_state(payload: str) -> BlockState:
    """Read the filter page: the switch, the mode, the rules, and the token.

    The token is a hidden field the page carries and every write must quote
    back, so it is read here rather than fetched separately - it belongs to the
    page that was just read, and a stale one is refused.

    The switch and the mode are looked for by what their names mean, and the
    state says whether they were found at all. Both pages keep the same two
    settings under names that only one of them was ever read by.
    """
    text = _unescape_hex(payload or "")
    token = _page_token(payload)
    settings = page_settings(text)
    switch = _setting_named(settings, *SWITCH_NAMES)
    mode = _setting_named(settings, *MODE_NAMES)

    entries: list[BlockEntry] = []
    for row in re.findall(r"new stMacFilter\(([^)]*)\)", text):
        fields = [field.strip().strip("\"'") for field in row.split(",")]
        # Read by shape, not by position. The wireless page writes a row as
        # (domain, SSID, name, address) and the wired one as (domain, address,
        # name), so counting along the row finds the address on one page and a
        # name on the other.
        mac = next((normalise_mac(field) for field in fields if normalise_mac(field)), "")
        if not mac:
            continue
        domain = next((field for field in fields if "InternetGatewayDevice" in field), "")
        ssid = next((field for field in fields if re.fullmatch(r"(?i)ssid[-_]?\d+", field)), "")
        name = next(
            (
                field
                for field in fields
                if field and field not in {domain, ssid} and not normalise_mac(field)
            ),
            "",
        )
        entries.append(BlockEntry(domain, mac, ssid, name))
    return BlockState(
        on=switch == "1",
        blacklist=mode != "1",
        entries=tuple(entries),
        token=token,
        switch_read=switch is not None,
        mode_read=mode is not None,
    )


@dataclass
class _Written:
    """What one list took during a block, and so what to take back.

    The rules that were there before this attempt, which are somebody else's
    block and not this change's to remove, and whether this attempt was the one
    that turned the list on.
    """

    kind: FilterKind
    before: set[str]
    woke: bool = False


class BlockList:
    """The router's block lists, on a session that is already signed in.

    Two of them: the wireless one and the wired one. A device reaches the
    network by one road or the other, and a phone can change roads by being
    plugged in, so blocking writes to both wherever both exist. A firmware that
    publishes only one is not an error - the other is simply skipped.

    Every write quotes back the token from the page it was read from, so each
    one begins by reading that page again. That is one extra request per change
    and it is what makes a change either take or say why it did not.
    """

    def __init__(self, session: RouterSession):
        self.session = session

    # -- reading ---------------------------------------------------------

    def state(self, kind: FilterKind = WIFI_FILTER) -> BlockState:
        """One list as the router currently has it. Raises if it is not there."""
        found = self.read_state(kind)
        if found is None:
            raise RouterError(
                f"The router did not show its {kind.label} filter page. The sign-in may "
                "have expired, or this model keeps its block list somewhere else."
            )
        return found

    def read_state(self, kind: FilterKind) -> BlockState | None:
        """The same, but None where this firmware has no such page at all."""
        status, body = self.session.read(kind.page)
        if status != 200 or self.session.looks_like_login(body):
            return None
        state = parse_block_state(body)
        if not state.token:
            raise RouterError(
                "The router's filter page carried no token, so nothing can be changed "
                "safely. It refuses any write without one."
            )
        return state

    def kinds(self) -> tuple[FilterKind, ...]:
        """The lists this router actually publishes."""
        return tuple(
            kind for kind in FILTER_KINDS if self.read_state(kind) is not None
        )

    def blocked_macs(self) -> tuple[str, ...]:
        """Every address any list on this router is keeping off.

        Any list, not every list. This used to be the addresses both lists
        named, on the reasoning that a device refused on one road and not the
        other is still using the other - a television on a cable, refused on
        three Wi-Fi networks it was never on, reads as blocked while it streams.

        True, and the wrong trade. The half-blocked device that actually turns
        up is the opposite one: a phone whose Wi-Fi rules were written and whose
        wired rules were not, because the second half of the change failed.
        Under "both lists" that phone is off the Wi-Fi and reads as online, so
        the button offers to block it - the one thing it does not need - and
        nothing in Mind offers to let it back on. It is refused, the owner
        complains, and the list says there is nothing to undo.

        So: refused anywhere reads as blocked, and unblocking clears every list,
        which is the answer that can always be given. Whoever wants that
        television off the Wi-Fi properly can block it again afterwards.
        """
        refused: set[str] = set()
        for kind in FILTER_KINDS:
            state = self.read_state(kind)
            if state is not None:
                refused |= set(state.blocked_macs)
        return tuple(sorted(refused))

    def networks(self) -> tuple[Ssid, ...]:
        """Every network to write a rule against, switched on or not."""
        return read_networks(self.session)

    # -- writing ---------------------------------------------------------

    def _send(self, kind: FilterKind, path: str, fields: dict[str, str]) -> None:
        """One write, with the page it came from named as the referer.

        These pages are only ever reached from themselves, and the firmware
        checks that, so a write that does not say where it came from is thrown
        away without an error worth reading.

        The token goes last, always. Anything after it in the body and the
        router answers 403 - the same add is accepted with the fields in one
        order and refused in another, whatever they contain. The page's own
        script puts the token at the end and this is the rule behind that. It is
        done here so that no caller has to remember it.
        """
        ordered = [
            (key, value) for key, value in fields.items() if key != "x.X_HW_Token"
        ]
        ordered.append(("x.X_HW_Token", fields.get("x.X_HW_Token", "")))
        body = urllib.parse.urlencode(ordered).encode()
        status, answer = self.session.post(
            path, body, referer=self.session.base + kind.page
        )
        if status >= 400:
            raise RouterError(f"The router refused the change ({status}).")
        if self.session.looks_like_login(answer):
            raise RouterError("The router asked for a sign-in again part way through.")

    def block(self, mac: str, name: str = "") -> BlockState:
        """Keep this device off, by wireless and by cable both.

        Either it takes on every list this router keeps, or the router is left
        as it was found. The wireless half is written first and there are half a
        dozen writes in it, so a wired half that fails - a list that is full, a
        session that expired, a page that answers 403 - used to leave a device
        refused on the Wi-Fi under a message saying the block had failed. That
        is the worst of both: the phone is off, and the person reading the
        message has been told it is not.

        Returns the wireless list, which is the one the rest of Mind asks about;
        what was written to each is the router's business.
        """
        first: BlockState | None = None
        written: list[_Written] = []
        for kind in FILTER_KINDS:
            state = self.read_state(kind)
            if state is None:
                continue
            # Written down before the writing starts, and kept up to date as it
            # goes. A list that fails in the middle of itself - three of the
            # four networks written, the fourth refused - has to be taken back
            # as surely as one that never started.
            record = _Written(kind, {entry.domain for entry in state.rules_for(mac)})
            written.append(record)
            try:
                after = self._block_one(kind, state, mac, name, record)
            except RouterError as exc:
                raise RouterError(f"{exc} {self._take_back(written, mac)}") from exc
            if first is None:
                first = after
        if first is None:
            raise RouterError(
                "This router published no block list that Mind recognises."
            )
        return first

    def _take_back(self, written: list[_Written], mac: str) -> str:
        """Undo the lists that took the change, and say whether that worked.

        Only what this attempt added: rules that were already there are somebody
        else's block, and a switch is put back only where Mind turned it on -
        one that was already on is holding other rules up.
        """
        trouble: list[str] = []
        for record in reversed(written):
            kind = record.kind
            try:
                state = self.state(kind)
                for rule in state.rules_for(mac):
                    if rule.domain in record.before:
                        continue
                    self._send(
                        kind, kind.delete, {rule.domain: "", "x.X_HW_Token": state.token}
                    )
                    state = self.state(kind)
                if record.woke:
                    self._send(
                        kind,
                        kind.switch,
                        {kind.right_field: "0", "x.X_HW_Token": state.token},
                    )
            except RouterError as exc:
                trouble.append(f"{kind.label}: {exc}")
        if trouble:
            return (
                "What had already been written could not all be taken back, so that "
                f"device may still be refused on part of the network ({'; '.join(trouble)}). "
                "Unblock it here to clear the rest."
            )
        return "Nothing was changed."

    def _block_one(
        self,
        kind: FilterKind,
        state: BlockState,
        mac: str,
        name: str,
        record: _Written,
    ) -> BlockState:
        address = router_mac(mac)
        if not state.blacklist:
            raise RouterError(
                f"This router's {kind.label} filter is set to whitelist, where the list "
                "is what is allowed rather than what is refused. Changing that mode "
                "deletes every rule the router has, so Mind will not do it for you."
            )
        # A wireless rule is per SSID; a wired one is not a network you choose.
        targets = (
            [network.field for network in self.networks()] if kind.per_ssid else [""]
        )
        # Matched on the network, not on its spelling. The form is given
        # "SSID-2" and the row may come back saying "SSID2" - the same network
        # written the way the other half of this firmware writes it - and taking
        # those for different networks adds four more rules on every block until
        # the list is full and no further block takes at all.
        already = {_ssid_key(entry.ssid) for entry in state.rules_for(mac)}
        for target in targets:
            if _ssid_key(target) in already:
                continue
            fields = {
                "x.SourceMACAddress": address,
                kind.name_field: (name or "")[:32],
                "x.Enable": "1",
            }
            if kind.per_ssid:
                fields["x.SSIDName"] = target
            # The token is added by _send, which puts it last where the router
            # insists on having it.
            fields["x.X_HW_Token"] = state.token
            self._send(kind, kind.add, fields)
            state = self.state(kind)  # each write spends the token it was given

        if not state.on:
            # Sent when the switch reads as off and when it could not be read
            # at all: this list has to be enforcing when this returns, and a
            # switch already on is not disturbed by being set on again.
            self._send(
                kind,
                kind.switch,
                {kind.right_field: "1", "x.X_HW_Token": state.token},
            )
            # Taken back only where the page said, in so many words, that it
            # was off. A switch Mind could not read may have been on and
            # holding somebody else's rules up, and turning that off to tidy
            # away a failed block would let every one of them back on.
            record.woke = state.switch_read
            state = self.state(kind)

        if not state.blocks(mac):
            raise RouterError(
                f"The router accepted the request but is not blocking that device on "
                f"{kind.label}. Its filter list may be full."
            )
        return state

    def unblock(self, mac: str) -> BlockState:
        """Let this device back on, by deleting the rules that named it."""
        first: BlockState | None = None
        for kind in FILTER_KINDS:
            state = self.read_state(kind)
            if state is None:
                continue
            cleared = self._unblock_one(kind, state, mac)
            if first is None:
                first = cleared
        if first is None:
            raise RouterError(
                "This router published no block list that Mind recognises."
            )
        return first

    def _unblock_one(self, kind: FilterKind, state: BlockState, mac: str) -> BlockState:
        rules = state.rules_for(mac)
        if not rules:
            return state
        for rule in rules:
            # The delete form names the rule by its own path, with nothing on
            # the other side of the equals sign - the value is not read.
            self._send(
                kind, kind.delete, {rule.domain: "", "x.X_HW_Token": state.token}
            )
            state = self.state(kind)
        if state.rules_for(mac):
            raise RouterError(
                f"The router kept the rule: that device is still blocked on {kind.label}."
            )
        return state


def blocked_macs(
    address: str,
    username: str,
    password: str,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[str, ...]:
    """Which devices the router is currently keeping off the Wi-Fi."""
    session = RouterSession(address, timeout)
    session.sign_in(username, password)
    return BlockList(session).blocked_macs()


def wifi_networks(
    address: str,
    username: str,
    password: str,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[Ssid, ...]:
    """Every Wi-Fi network the router defines, and whether each is on the air."""
    session = RouterSession(address, timeout)
    session.sign_in(username, password)
    return WlanNetworks(session).all()


def set_network_enabled(
    address: str,
    username: str,
    password: str,
    index: int,
    enabled: bool,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[Ssid, ...]:
    """Put one network on or off the air, and report every network afterwards.

    What comes back is read from the router rather than assumed from what was
    asked for, which is the same rule blocking follows and for the same reason.
    """
    session = RouterSession(address, timeout)
    session.sign_in(username, password)
    networks = WlanNetworks(session)
    networks.set_enabled(index, enabled)
    return networks.all()


def set_blocked(
    address: str,
    username: str,
    password: str,
    mac: str,
    blocked: bool,
    name: str = "",
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[str, ...]:
    """Block or unblock one device, and report who is blocked afterwards.

    What comes back is read from the router rather than assumed from what was
    asked for, and it covers both lists - which is the only answer worth
    writing down.
    """
    session = RouterSession(address, timeout)
    session.sign_in(username, password)
    blocking = BlockList(session)
    if blocked:
        blocking.block(mac, name)
    else:
        blocking.unblock(mac)
    return blocking.blocked_macs()
