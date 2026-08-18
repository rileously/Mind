"""Putting a ringing phone on screen, with something to press.

A notification with buttons needs two things an ordinary program does not have.
It needs an identity, because Windows will not show a notification from nobody -
that is registered here, in the user's own hive, no elevation and nothing
installed. And it needs somewhere for a button press to go, which is the "mind:"
protocol: Windows launches Mind with the URI, and Mind hands it to the copy
already running, through the same channel a second launch already uses to ask
for the window.

The alternative is a COM activator - a class to register, a factory to host, a
GUID to stamp on a shortcut - for exactly the same outcome.

Nothing here is fatal. A machine where notifications are switched off, or where
the registry will not take the keys, still has a phone page and a Telegram
message; the notification is the convenience, not the feature.
"""

from __future__ import annotations

import subprocess
import winreg
from pathlib import Path

from .paths import data_dir


# Ours, and unlikely to be anybody else's. Windows shows notifications under
# whatever name this key carries.
AUMID = "Mind.Desktop.Assistant"
DISPLAY_NAME = "Mind"
PROTOCOL = "mind"
TOAST_SCRIPT = Path(__file__).with_name("windows_toast.ps1")
CREATE_NO_WINDOW = 0x08000000
CALL_TAG = "mind-call"
TOAST_GROUP = "mind"

ANSWER_URI = f"{PROTOCOL}://call/answer"
REJECT_URI = f"{PROTOCOL}://call/reject"
MUTE_URI = f"{PROTOCOL}://call/mute"
KNOWN_ACTIONS = frozenset({"call/answer", "call/reject", "call/mute", "call/show"})
# Where a button press waits until the running copy of Mind reads it. A file
# because the press arrives as a whole new process, which has nothing else in
# common with the one that will act on it.
ACTION_FILE = "pending-action.txt"


def action_path() -> Path:
    return data_dir() / ACTION_FILE


def register(exe_path: str = "") -> bool:
    """Claim an identity and the protocol. True when both are in place.

    Written under HKEY_CURRENT_USER, so this needs no administrator and changes
    nothing for anybody else who uses the machine.
    """
    import sys

    target = exe_path or sys.executable
    if not target:
        return False
    try:
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{AUMID}"
        ) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, DISPLAY_NAME)

        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROTOCOL}"
        ) as key:
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ, f"URL:{DISPLAY_NAME}")
            # What tells Windows this key is a protocol rather than a file type.
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER,
            rf"Software\Classes\{PROTOCOL}\shell\open\command",
        ) as key:
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ, f'"{target}" "%1"')
    except OSError:
        return False
    return True


def is_registered() -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            rf"Software\Classes\{PROTOCOL}\shell\open\command",
        ):
            return True
    except OSError:
        return False


def _run(arguments: list[str], timeout: float = 20.0) -> str:
    try:
        done = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(TOAST_SCRIPT),
                *arguments,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (done.stdout or "").strip()


def show_call(who: str, model: str = "") -> bool:
    """Show the ringing phone. False if Windows would not show it."""
    body = who or "Unknown number"
    return _run(
        [
            "-Aumid", AUMID,
            "-Title", "Incoming call",
            "-Body", body,
            "-Attribution", model or "Phone",
            "-AnswerUri", ANSWER_URI,
            "-MuteUri", MUTE_URI,
            "-RejectUri", REJECT_URI,
            "-Tag", CALL_TAG,
            "-Group", TOAST_GROUP,
            "-Ringing",
        ]
    ) == "shown"


def show_in_call(who: str, model: str = "", muted: bool = False) -> bool:
    """Show the call that is under way, with the two things left to do.

    A notification is spent the moment a button on it is pressed, so answering
    from one takes it off the screen. Muting would be unreachable a second
    later if nothing replaced it - and mute is the button somebody reaches for
    in the middle of a call, not at the start of it.
    """
    body = who or "Unknown number"
    return _run(
        [
            "-Aumid", AUMID,
            "-Title", "Muted" if muted else "In a call",
            "-Body", body,
            "-Attribution", model or "Phone",
            "-MuteUri", MUTE_URI,
            "-MuteLabel", "Unmute" if muted else "Mute",
            "-RejectUri", REJECT_URI,
            "-RejectLabel", "Hang up",
            "-Tag", CALL_TAG,
            "-Group", TOAST_GROUP,
        ]
    ) == "shown"


# The one process counting out the call on screen. One at a time, because
# there is one notification and one call.
_live: subprocess.Popen | None = None


def start_in_call(who: str, model: str = "", muted: bool = False) -> bool:
    """Put the call on screen and leave it there, counting.

    A separate process rather than a timer here: the counting is a second of
    sleeping and a call into Windows, and doing it in Mind would mean a new
    PowerShell every second for as long as the call lasts.
    """
    stop_in_call(dismiss=False)
    global _live
    arguments = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(TOAST_SCRIPT),
        "-Aumid", AUMID,
        "-Title", "Muted" if muted else "In a call",
        # Empty rather than "Unknown number": the line is left out entirely
        # when nobody is known, which reads better than being told twice that
        # the phone does not know who this is.
        "-Body", who,
        "-Attribution", model or "Phone",
        "-MuteUri", MUTE_URI,
        "-MuteLabel", "Unmute" if muted else "Mute",
        "-RejectUri", REJECT_URI,
        "-Tag", CALL_TAG,
        "-Group", TOAST_GROUP,
        "-Live",
    ]
    try:
        _live = subprocess.Popen(
            arguments,
            creationflags=CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, ValueError):
        _live = None
        return False
    return True


def stop_in_call(dismiss: bool = True) -> None:
    """Stop counting, and take the notification away with it."""
    global _live
    process = _live
    _live = None
    if process is not None and process.poll() is None:
        try:
            process.terminate()
        except OSError:
            pass
    if dismiss:
        dismiss_call()


def dismiss_call() -> None:
    """Take the notification away, because the call it was about is over."""
    _run(["-Aumid", AUMID, "-Tag", CALL_TAG, "-Group", TOAST_GROUP, "-Dismiss"], timeout=10.0)


def parse_action(argument: str) -> str:
    """What a "mind://" launch is asking for, or "" if it is asking for nothing.

    Anything unrecognised is nothing. This arrives from a command line, which is
    the one place on this machine where a stranger's text could turn up.
    """
    text = (argument or "").strip().strip('"').lower()
    if not text.startswith(f"{PROTOCOL}://"):
        return ""
    what = text[len(PROTOCOL) + 3 :].strip("/")
    return what if what in KNOWN_ACTIONS else ""


def remember_action(action: str) -> bool:
    """Leave the action where the running copy of Mind will find it."""
    if not action:
        return False
    try:
        path = action_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(action, encoding="utf-8")
    except OSError:
        return False
    return True


def take_action() -> str:
    """Read the waiting action and clear it, so it is acted on once only."""
    path = action_path()
    try:
        action = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    try:
        path.unlink()
    except OSError:
        pass
    return action if action in KNOWN_ACTIONS else ""
