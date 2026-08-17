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
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


DEFAULT_TIMEOUT = 8.0
# Where the device list lives on the models that publish one. Tried in turn.
DEVICE_PAGES = (
    "/html/bbsp/common/GetLanUserDevInfo.asp",
    "/html/amp/lanuserinfo/lanuserinfo.asp",
    "/html/bbsp/dhcp/dhcp.asp",
    "/api/system/HostInfo",
)
LOGIN_PAGES = ("/asp/GetRandCount.asp", "/index.asp", "/")


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

    # The JavaScript rows: quoted fields, in which a MAC is recognisable on
    # sight and an address likewise.
    for row in re.findall(r"new\s+st\w*DevInfo\s*\((.*?)\)", payload or "", re.S) or re.findall(
        r"\[([^\[\]]*\"[0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[^\[\]]*)\]", payload or ""
    ):
        fields = [field.strip().strip("\"'") for field in row.split(",")]
        mac = next((normalise_mac(field) for field in fields if normalise_mac(field)), "")
        if not mac:
            continue
        ip = next(
            (field for field in fields if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", field)), ""
        )
        hostname = next((field for field in fields if _looks_like_a_name(field)), "")
        devices[mac] = RouterDevice(mac=mac, ip=ip, hostname=hostname)
    return sorted(devices.values(), key=lambda device: device.ip)


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
        self.base = self._normalise_address(address)
        self.timeout = timeout
        self._jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar)
        )
        self.notes: list[str] = []

    @staticmethod
    def _normalise_address(address: str) -> str:
        cleaned = (address or "").strip().rstrip("/")
        if not cleaned:
            raise RouterError("Enter the router's address, for example 192.168.18.1.")
        if not cleaned.startswith(("http://", "https://")):
            cleaned = f"http://{cleaned}"
        return cleaned

    def _open(self, path: str, data: bytes | None = None) -> tuple[int, str]:
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers={
                "User-Agent": "Mind",
                "Referer": self.base + "/",
                **({"Content-Type": "application/x-www-form-urlencoded"} if data else {}),
            },
            method="POST" if data else "GET",
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
        """The one-shot value the login form carries, when the model uses one."""
        for page in LOGIN_PAGES:
            _status, body = self._open(page)
            match = re.search(
                r'name=["\'](?:x\.X_HW_Token|csrf_token|token)["\'][^>]*value=["\']([^"\']+)',
                body,
            )
            if match:
                return match.group(1)
            match = re.search(r'getElementById\("hwonttoken"\)\.value\s*=\s*"([^"]+)"', body)
            if match:
                return match.group(1)
        return ""

    def sign_in(self, username: str, password: str) -> None:
        """Sign in, saying which step failed rather than only that one did."""
        if not username or not password:
            raise RouterError("Enter the router's username and password.")
        token = self._token()
        if not token:
            self.notes.append("no login token was offered; sent the form without one")
        # Huawei's web UI sends the password base64 encoded rather than plain.
        encoded = base64.b64encode(password.encode("utf-8")).decode("ascii")
        form = {"UserName": username, "PassWord": encoded, "Language": "english"}
        if token:
            form["x.X_HW_Token"] = token
        status, body = self._open("/login.cgi", urllib.parse.urlencode(form).encode())
        if status >= 400:
            raise RouterError(
                f"The router refused the sign-in request ({status}). This model may use a "
                "different login page."
            )
        lowered = body.lower()
        if "errorcode" in lowered or "password" in lowered and "incorrect" in lowered:
            raise RouterError("The router rejected that username or password.")
        if not self._jar and "location" not in lowered:
            self.notes.append("the router returned no session cookie")

    def devices(self) -> list[RouterDevice]:
        """Fetch the connected list, trying the pages these models publish."""
        tried: list[str] = []
        for page in DEVICE_PAGES:
            status, body = self._open(page)
            if status != 200 or len(body) < 200:
                tried.append(f"{page} ({status})")
                continue
            found = parse_devices(body)
            if found:
                return found
            tried.append(f"{page} (nothing to read)")
        raise RouterError(
            "Signed in, but no device list could be read from: " + ", ".join(tried)
        )


def fetch_devices(
    address: str, username: str, password: str, timeout: float = DEFAULT_TIMEOUT
) -> tuple[list[RouterDevice], list[str]]:
    """Sign in, take the list, and report anything odd along the way."""
    session = RouterSession(address, timeout)
    session.sign_in(username, password)
    return session.devices(), list(session.notes)
