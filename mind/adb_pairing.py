"""Pairing a phone by showing it a code to look at.

Wireless debugging normally asks somebody to read a six-digit code and an
address off the phone and type both into the computer, which is two numbers
too many and both of them change. Android's other way round is better: the
computer shows a QR code, the phone's camera reads it, and nothing is typed.

The exchange is short. Mind invents a name and a password and puts them in the
code. The phone reads it, and starts advertising a pairing service under that
name - which is how Mind knows which phone on the network is the one that just
looked, and where to reach it. Then "adb pair" completes it with the password
that was in the code all along.

The password is worth nothing afterwards. It authorises one pairing, is never
stored, and a new one is made for every attempt.
"""

from __future__ import annotations

import secrets
import string
import time

from .adb_client import (
    PAIRING_SERVICE,
    AdbError,
    _default_runner,
    find_adb,
    mdns_services,
)


# The phone advertises under whatever name it is given, so this only has to be
# unique. It is spelled the way Android Studio spells it because that is the
# spelling this exchange has been seen to work with, and there is nothing to
# gain from finding out whether anything checks.
NAME_PREFIX = "studio-"
PASSWORD_LENGTH = 12
# Long enough to unlock the phone, open the camera and hold it still.
DEFAULT_TIMEOUT = 120.0
LOOK_EVERY = 1.5


class PairingError(RuntimeError):
    """Something the person holding the phone can act on."""


def new_name() -> str:
    return NAME_PREFIX + secrets.token_hex(4)


def new_password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(PASSWORD_LENGTH))


def qr_payload(name: str, password: str) -> str:
    """What the QR code carries.

    Android reads this as a Wi-Fi credential whose type is ADB, which is why it
    is shaped like one. The two semicolons at the end are part of that format,
    not a typo.
    """
    if not name or not password:
        raise PairingError("A pairing code needs a name and a password.")
    if ";" in name or ";" in password:
        # They would end the field early and the phone would read something
        # else entirely.
        raise PairingError("A pairing name and password cannot contain a semicolon.")
    return f"WIFI:T:ADB;S:{name};P:{password};;"


def address_for(name: str, services: list) -> str:
    """Where the phone that read our code is waiting, if it has read it yet."""
    for advertised, address in services:
        if advertised == name:
            return address
    return ""


def pair(address: str, password: str, adb: str = "", run=_default_runner) -> str:
    """Complete the pairing. The phone's own words on failure."""
    binary = adb or find_adb()
    if not binary:
        raise PairingError("adb is not installed, so a phone cannot be paired.")
    try:
        code, out, err = run([binary, "pair", address, password], 30.0)
    except OSError as exc:
        raise PairingError(f"Could not run adb: {exc}") from exc
    spoken = (out or err or "").strip()
    if code != 0 or "successfully" not in spoken.lower():
        raise PairingError(spoken.splitlines()[0] if spoken else "The phone refused the pairing.")
    return spoken


def wait_for_phone(
    name: str,
    timeout: float = DEFAULT_TIMEOUT,
    look=None,
    sleep=time.sleep,
    now=time.monotonic,
    keep_going=None,
) -> str:
    """Watch for the phone that read the code, and say where it is.

    Returns "" if nobody looked before the time ran out, which is not a failure
    worth a stack trace - it usually means the camera never got pointed at the
    screen.
    """
    finder = look or (lambda: mdns_services(service=PAIRING_SERVICE))
    wanted = keep_going or (lambda: True)
    deadline = now() + max(0.0, timeout)
    while True:
        if not wanted():
            # The window was closed. Nobody is waiting for this any more.
            return ""
        address = address_for(name, finder())
        if address:
            return address
        if now() >= deadline or not wanted():
            return ""
        sleep(LOOK_EVERY)
