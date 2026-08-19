"""Sharing this PC's connection over its own Wi-Fi, from the phone.

The point is coverage, not speed. A PC that is closer to the far end of the
house than the router is makes a better access point for that end of the house,
and a phone can be told to switch to it without anybody walking to the PC.

On a PC that is itself on Wi-Fi the adapter hosts the hotspot on the channel it
is already using, so the throughput roughly halves. That is the trade being
made deliberately: half of a signal that reaches is worth more than all of one
that does not.

Windows does the work through NetworkOperatorTetheringManager, which is the
switch the Mobile Hotspot page in Settings drives. That lives in
windows_hotspot.ps1 for the same reason the notification XML does. Everything
here parses what it said, and the parsing is kept apart from the running so it
can be tested on any machine.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


CREATE_NO_WINDOW = 0x08000000
HOTSPOT_SCRIPT = Path(__file__).with_name("windows_hotspot.ps1")
# Starting an access point is slower than reading one: the radio has to come up.
STATUS_TIMEOUT = 20.0
CHANGE_TIMEOUT = 45.0
# Windows will not take a shorter one, and saying so beats a refusal from WinRT.
MIN_PASSPHRASE = 8


class HotspotError(RuntimeError):
    """Something the person reading it in the chat can act on."""


# What the setting stores, and what the script takes. "auto" is Windows
# choosing; on a PC already sitting on 2.4 GHz it usually chooses that, but the
# room at the far end of the house is not a good place to find out.
BANDS: tuple[tuple[str, str], ...] = (
    ("auto", "Automatic"),
    ("2.4", "2.4 GHz - goes through walls"),
    ("5", "5 GHz - faster, shorter range"),
)


@dataclass(frozen=True)
class HotspotState:
    """What the hotspot is doing, as Windows describes it."""

    state: str = "unknown"
    clients: int = 0
    ssid: str = ""
    band: str = "auto"

    @property
    def is_on(self) -> bool:
        return self.state == "on"

    @property
    def is_changing(self) -> bool:
        return self.state == "intransition"


def parse_report(payload: str) -> HotspotState:
    """The script's key=value lines as a state.

    Raises rather than returning a state when the script reported a failure,
    because "ok=0" always arrives with a sentence explaining it and that
    sentence is the useful part.
    """
    fields: dict[str, str] = {}
    for line in (payload or "").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            fields[key.strip().lower()] = value.strip()
    if fields.get("ok") == "0":
        raise HotspotError(fields.get("detail") or "Windows would not answer about the hotspot.")
    if "state" not in fields:
        raise HotspotError("Windows did not say whether the hotspot is on.")
    try:
        clients = int(fields.get("clients") or 0)
    except ValueError:
        clients = 0
    return HotspotState(
        state=fields.get("state", "unknown").lower(),
        clients=max(0, clients),
        ssid=fields.get("ssid", ""),
        band=band_from_windows(fields.get("band", "")),
    )


def band_from_windows(value: str) -> str:
    """Windows' enum name as the short form the settings use."""
    return {
        "twopointfourgigahertz": "2.4",
        "fivegigahertz": "5",
        "sixgigahertz": "6",
    }.get((value or "").strip().lower(), "auto")


def band_label(band: str) -> str:
    """How a band is written where somebody reads it."""
    for value, label in BANDS:
        if value == band:
            return label
    return "Automatic"


def parse_current_ssid(payload: str) -> str:
    """The network this PC is on, from "netsh wlan show interfaces".

    BSSID sits directly under SSID in that output and would match a looser
    pattern, so the anchor matters more than it looks.
    """
    for line in (payload or "").splitlines():
        found = re.match(r"^\s*SSID\s*:\s*(.+?)\s*$", line)
        if found:
            return found.group(1)
    return ""


def parse_profile_key(payload: str) -> str:
    """The saved password, from "netsh wlan show profile ... key=clear"."""
    for line in (payload or "").splitlines():
        found = re.match(r"^\s*Key Content\s*:\s*(.+?)\s*$", line)
        if found:
            return found.group(1)
    return ""


def _default_runner(arguments: list[str], timeout: float) -> tuple[int, str, str]:
    try:
        done = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        raise HotspotError("Windows did not answer about the hotspot in time.") from None
    except OSError as exc:
        raise HotspotError(f"Could not ask Windows about the hotspot: {exc}") from exc
    return done.returncode, done.stdout or "", done.stderr or ""


class Hotspot:
    """The Mobile Hotspot, driven the way its own Settings page drives it."""

    def __init__(self, run=_default_runner, script: Path | None = None):
        self._run = run
        self._script = script or HOTSPOT_SCRIPT

    def _call(self, action: str, timeout: float, *extra: str) -> HotspotState:
        code, out, err = self._run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self._script),
                "-Action",
                action,
                *extra,
            ],
            timeout,
        )
        if code != 0 and not out.strip():
            detail = (err or "").strip().splitlines()
            raise HotspotError(detail[0] if detail else "Windows refused the hotspot request.")
        return parse_report(out)

    def state(self) -> HotspotState:
        return self._call("status", STATUS_TIMEOUT)

    def start(self) -> HotspotState:
        return self._call("start", CHANGE_TIMEOUT)

    def stop(self) -> HotspotState:
        return self._call("stop", CHANGE_TIMEOUT)

    def configure(self, ssid: str, passphrase: str, band: str = "") -> HotspotState:
        ssid = (ssid or "").strip()
        if not ssid:
            raise HotspotError("A hotspot needs a name.")
        if len(passphrase or "") < MIN_PASSPHRASE:
            raise HotspotError(
                f"A hotspot password must be at least {MIN_PASSPHRASE} characters."
            )
        extra = ["-Ssid", ssid, "-Passphrase", passphrase]
        if band and band in {value for value, _label in BANDS}:
            extra += ["-Band", band]
        return self._call("configure", CHANGE_TIMEOUT, *extra)


def current_wifi(run=_default_runner) -> tuple[str, str]:
    """The name and password of the Wi-Fi this PC is on.

    Both are needed to give the hotspot the same pair, which is what lets a
    phone treat it as another access point of the same network and move to it
    on its own. Returns empty strings rather than raising: not being able to
    read them means the user names the hotspot themselves, not that anything
    is broken.

    The password never leaves this process. It goes to ConfigureAccessPointAsync
    as an argument and is not logged, shown, or stored by Mind.
    """
    try:
        _code, out, _err = run(["netsh", "wlan", "show", "interfaces"], STATUS_TIMEOUT)
    except HotspotError:
        return "", ""
    ssid = parse_current_ssid(out)
    if not ssid:
        return "", ""
    try:
        _code, out, _err = run(
            ["netsh", "wlan", "show", "profile", f"name={ssid}", "key=clear"], STATUS_TIMEOUT
        )
    except HotspotError:
        return ssid, ""
    return ssid, parse_profile_key(out)
