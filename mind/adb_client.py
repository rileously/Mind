"""Talking to an Android phone over ADB.

Bluetooth gave nothing here. Windows will let an ordinary program watch a phone
call go past and will not let it touch one: answering, rejecting and dialling
sit behind a restricted capability that only a system app gets, and asking for
it was refused outright rather than put to the user. So this takes the other
road, which the phone's owner opens deliberately by switching on debugging, and
which then gives everything rather than nothing - the keys a call needs, the
state of the line, and anything else the phone will answer.

Nothing here spawns a shell. Every call is a list of arguments handed to adb,
so a device name or a number with a space or a quote in it is an argument and
never a command. The runner is injectable for the same reason the router's
session is: what matters is the parsing and the refusing, and neither needs a
phone in the room to be tested.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path


DEFAULT_TIMEOUT = 12.0
# Windows shows a console window for every subprocess unless told not to, and
# a phone command must not flash a black box over what the user is doing.
CREATE_NO_WINDOW = 0x08000000

# Where adb tends to be when it was not put on PATH. Ordered by how likely the
# copy is to be current: the SDK's own, then the tools people install for one
# job and forget.
ADB_LOCATIONS = (
    r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe",
    r"%USERPROFILE%\AppData\Local\Android\Sdk\platform-tools\adb.exe",
    r"C:\android-sdk\platform-tools\adb.exe",
    r"%PROGRAMFILES(X86)%\Minimal ADB and Fastboot\adb.exe",
    r"%PROGRAMFILES%\Minimal ADB and Fastboot\adb.exe",
)

# What a key press is called on the phone.
#
# Answering is not one of these on a modern handset. KEYCODE_CALL was tried
# against a ringing Pixel on Android 17 and the call ended rather than
# connected, so it is not used for answering at all - the headset hook is tried
# first and the screen is used when that does nothing. KEYCODE_CALL is kept
# only because it is what opens the dialer.
KEY_CALL = "5"
KEY_ENDCALL = "6"
KEY_HEADSETHOOK = "79"
# The microphone, not the volume. KEYCODE_VOLUME_MUTE silences what comes out
# of the phone; this is the one that stops the other person hearing the room.
KEY_MUTE = "91"
MEDIA_KEYS = {
    "play": "85",
    "next": "87",
    "previous": "88",
    "volup": "24",
    "voldown": "25",
    "mute": "164",
}

# What the phone says it is doing, in the words dumpsys uses.
CALL_STATES = {"0": "idle", "1": "ringing", "2": "in a call"}

# What the button that answers a call calls itself. Read off the screen rather
# than guessed at a position, because the dialer moves it and the lock screen
# puts it somewhere else again.
ANSWER_WORDS = ("answer", "accept", "pick up", "swipe up to answer")
MUTE_WORDS = ("mute", "unmute")
BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


class AdbError(RuntimeError):
    """Something the person reading it can act on."""


@dataclass(frozen=True)
class AndroidDevice:
    """One phone as adb lists it."""

    serial: str
    state: str = "device"
    model: str = ""

    @property
    def ready(self) -> bool:
        return self.state == "device"

    @property
    def display_name(self) -> str:
        return self.model.replace("_", " ") if self.model else self.serial

    @property
    def over_wifi(self) -> bool:
        """Whether this is a wireless connection rather than a cable."""
        return ":" in self.serial


@dataclass(frozen=True)
class CallState:
    """What the phone's line is doing right now."""

    state: str = "idle"
    number: str = ""
    name: str = ""

    @property
    def ringing(self) -> bool:
        return self.state == "ringing"

    @property
    def busy(self) -> bool:
        return self.state in {"ringing", "in a call"}

    @property
    def caller(self) -> str:
        """Who is calling: a name, or the number, or nothing at all.

        Nothing, rather than a phrase standing in for it. A call placed on the
        handset carries no number for the shell to read, and "an unknown
        number - 00:18" on a notification says less than "00:18" does while
        taking a line to say it.
        """
        return self.name or self.number


def find_adb() -> str:
    """Where adb is on this machine, or "" if it is not installed.

    On PATH first, because someone who put it there meant that copy.
    """
    found = shutil.which("adb")
    if found:
        return found
    import os

    for candidate in ADB_LOCATIONS:
        expanded = Path(os.path.expandvars(candidate))
        if "%" in str(expanded):
            continue
        if expanded.is_file():
            return str(expanded)
    return ""


def parse_devices(payload: str) -> list[AndroidDevice]:
    """Read "adb devices -l" into something with names in it.

    A phone that has not been trusted yet is listed as "unauthorized" and is
    the single most common thing to go wrong, so it is kept in the list rather
    than filtered out - the state is what tells the user to look at the phone.
    """
    devices: list[AndroidDevice] = []
    for line in (payload or "").splitlines():
        line = line.strip()
        if not line or line.startswith(("List of devices", "*", "adb ")):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        model = ""
        for part in parts[2:]:
            if part.startswith("model:"):
                model = part.split(":", 1)[1]
        devices.append(AndroidDevice(serial, state, model))
    return devices


def parse_call_state(payload: str) -> CallState:
    """Read the call state out of what dumpsys prints.

    Two things are looked for and neither is guaranteed. The state is a number
    the platform has reported for years; the caller's number is redacted on
    newer Android unless the shell holds the phone permission, so it is offered
    when it is there and never depended on.
    """
    text = payload or ""
    state = "idle"
    found = re.search(r"mCallState\s*=\s*(\d)", text)
    if found:
        state = CALL_STATES.get(found.group(1), "idle")
    number = ""
    for pattern in (
        r"mCallIncomingNumber\s*=\s*([+\d][\d\s-]{2,20})",
        r"incomingNumber\s*=\s*([+\d][\d\s-]{2,20})",
    ):
        match = re.search(pattern, text)
        if match:
            number = match.group(1).strip()
            break
    return CallState(state, number)


def parse_contact_name(payload: str) -> str:
    """The name a phone_lookup answered with, if it answered with one.

    The provider returns a row per matching contact method, so the same person
    can come back several times over; the first name is the answer and the rest
    are the same answer again.
    """
    for line in (payload or "").splitlines():
        found = re.search(r"display_name=([^,]+)", line)
        if found:
            name = found.group(1).strip()
            if name and name.lower() != "null":
                return name
    return ""


def parse_mic_mute(payload: str) -> bool:
    """Whether the microphone is muted, as the audio service describes it.

    One line carries the whole answer in several flags - switched off by hand,
    by policy, or by whatever is holding the call - and any of them being true
    means the other person cannot hear anything.
    """
    for line in (payload or "").splitlines():
        if "mic mute" not in line.lower():
            continue
        return "=true" in line.lower().replace(" ", "")
    return False


def _default_runner(arguments: list[str], timeout: float) -> tuple[int, str, str]:
    """Run adb once, without a shell and without a console window."""
    try:
        done = subprocess.run(
            arguments,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except FileNotFoundError as exc:
        raise AdbError("adb could not be run. Install the Android platform tools.") from exc
    except subprocess.TimeoutExpired as exc:
        raise AdbError("The phone did not answer in time.") from exc
    except OSError as exc:
        raise AdbError(f"adb could not be run: {exc}") from exc
    return done.returncode, done.stdout or "", done.stderr or ""


class Phone:
    """One phone, reachable over adb.

    ``serial`` names which phone when more than one is attached. Left empty, adb
    picks the only one and complains if there are several - which is exactly the
    behaviour wanted, and the complaint is passed on rather than swallowed.
    """

    def __init__(
        self,
        serial: str = "",
        adb: str = "",
        run=_default_runner,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.serial = serial
        self.adb = adb or find_adb()
        self.run = run
        self.timeout = timeout

    # -- the wire --------------------------------------------------------

    def _adb(self, *arguments: str, timeout: float | None = None) -> str:
        if not self.adb:
            raise AdbError(
                "adb is not installed. Mind needs the Android platform tools to talk "
                "to a phone."
            )
        command = [self.adb]
        if self.serial:
            command += ["-s", self.serial]
        command += list(arguments)
        code, out, err = self.run(command, timeout or self.timeout)
        if code != 0:
            message = (err or out).strip().splitlines()
            detail = message[0] if message else f"adb failed ({code})"
            raise AdbError(self._explain(detail))
        return out

    @staticmethod
    def _explain(detail: str) -> str:
        """Turn adb's own words into something worth reading.

        These three are what nearly every failure is, and each one has a
        different thing for the user to do about it.
        """
        lowered = detail.lower()
        if "unauthorized" in lowered:
            return (
                "The phone has not trusted this PC yet. Unlock it and accept the "
                "debugging prompt."
            )
        if "no devices" in lowered or "device not found" in lowered:
            return (
                "No phone is connected. Plug it in with USB debugging on, or pair it "
                "over Wi-Fi."
            )
        if "more than one" in lowered:
            return "More than one phone is attached. Choose which one to use."
        if "offline" in lowered:
            return "The phone is attached but not responding. Unplug it and try again."
        return detail

    def shell(self, *arguments: str, timeout: float | None = None) -> str:
        """Run one command on the phone. Arguments are never a shell string."""
        return self._adb("shell", *arguments, timeout=timeout)

    # -- what it can be asked ---------------------------------------------

    def devices(self) -> list[AndroidDevice]:
        """Every phone adb can see, whatever state it is in."""
        return parse_devices(self._adb("devices", "-l"))

    def call_state(self, with_name: bool = False) -> CallState:
        """What the line is doing, and optionally who is on it.

        The name costs another visit to the phone, so it is asked for only when
        somebody is about to be told - not on every poll of a phone sitting
        idle in a pocket.
        """
        state = parse_call_state(self.shell("dumpsys", "telephony.registry"))
        if with_name and state.number and not state.name:
            return replace(state, name=self.contact_name(state.number))
        return state

    def contact_name(self, number: str) -> str:
        """What this number is called in the phone's contacts, if anything.

        phone_lookup rather than a search through the contact list: it is the
        provider's own matcher, and it knows that 932-2011 and 9322011 and
        +9609322011 are one person.
        """
        digits = re.sub(r"[^\d+]", "", number or "")
        if not digits:
            return ""
        try:
            answer = self.shell(
                "content",
                "query",
                "--uri",
                f"content://com.android.contacts/phone_lookup/{digits}",
                "--projection",
                "display_name",
            )
        except AdbError:
            return ""
        return parse_contact_name(answer)

    def answer(self) -> bool:
        """Take the call that is ringing. True if the phone actually took it.

        Two ways, in the order that does least harm. The headset hook is what a
        wired earbud's button sends and what a handset is most likely to accept
        from the shell. If the phone is still ringing after that, the answer
        control is found on the screen and tapped where it actually is.

        The old call key is not among them: pressed against a ringing Pixel it
        ended the call instead of connecting it, which is the opposite of the
        thing being asked for.
        """
        self.shell("input", "keyevent", KEY_HEADSETHOOK)
        if not self.call_state().ringing:
            return True
        if self.tap_answer():
            return not self.call_state().ringing
        return False

    def is_muted(self) -> bool:
        """Whether the microphone is off right now."""
        return parse_mic_mute(self.shell("dumpsys", "audio"))

    def set_muted(self, muted: bool) -> bool:
        """Mute or unmute the microphone. True when it ended up that way.

        The key is a toggle, so it is only pressed when the phone is not
        already where it is being asked to go - otherwise asking twice for mute
        would unmute somebody mid-sentence. If the key does nothing, the
        control is found on screen the same way the answer button is.
        """
        if self.is_muted() == muted:
            return True
        self.shell("input", "keyevent", KEY_MUTE)
        if self.is_muted() == muted:
            return True
        if self._tap_words(MUTE_WORDS):
            return self.is_muted() == muted
        return False

    def toggle_mute(self) -> bool:
        """Flip the microphone, and say whether it is muted afterwards."""
        wanted = not self.is_muted()
        self.set_muted(wanted)
        return self.is_muted()

    def screen_text(self) -> str:
        """What is on the screen now, as the accessibility tree describes it."""
        self.shell("uiautomator", "dump", "/sdcard/mind-window.xml", timeout=20.0)
        return self.shell("cat", "/sdcard/mind-window.xml", timeout=20.0)

    def tap_answer(self) -> bool:
        """Find the answer control on screen and press it. True if one was found."""
        return self._tap_words(ANSWER_WORDS)

    def _tap_words(self, words: tuple[str, ...]) -> bool:
        """Press the control that says one of these things.

        Matched on what it says rather than where it is: every dialer puts its
        buttons somewhere different, and the lock screen puts them somewhere
        different again.
        """
        try:
            screen = self.screen_text()
        except AdbError:
            return False
        for node in re.findall(r"<node[^>]*>", screen):
            lowered = node.lower()
            if not any(word in lowered for word in words):
                continue
            if 'clickable="true"' not in lowered:
                continue
            found = BOUNDS.search(node)
            if not found:
                continue
            left, top, right, bottom = (int(value) for value in found.groups())
            self.shell(
                "input", "tap", str((left + right) // 2), str((top + bottom) // 2)
            )
            return True
        return False

    def hang_up(self) -> None:
        """End the call in progress, or refuse the one that is ringing."""
        self.shell("input", "keyevent", KEY_ENDCALL)

    def dial(self, number: str) -> None:
        """Call a number.

        The number is checked here rather than trusted: it becomes an argument
        to an intent, and a "number" carrying a space and a second argument is
        how that would stop being a phone call.
        """
        cleaned = (number or "").strip()
        if not cleaned or not re.fullmatch(r"[+#*\d][\d+#*\-\s()]{1,24}", cleaned):
            raise AdbError(f"{number!r} is not a phone number.")
        compact = re.sub(r"[\s()\-]", "", cleaned)
        self.shell("am", "start", "-a", "android.intent.action.CALL", "-d", f"tel:{compact}")

    def press_media(self, key: str) -> None:
        code = MEDIA_KEYS.get((key or "").lower())
        if not code:
            raise AdbError(f"{key!r} is not one of: {', '.join(sorted(MEDIA_KEYS))}.")
        self.shell("input", "keyevent", code)

    def battery(self) -> int:
        """How much charge is left, or -1 when the phone will not say."""
        found = re.search(r"level:\s*(\d+)", self.shell("dumpsys", "battery"))
        return int(found.group(1)) if found else -1

    def hardware_serial(self) -> str:
        """What the handset itself calls itself.

        The same phone answers on an address and on the name it advertises, and
        on a different address after every restart. This is the one thing about
        it that does not change.
        """
        return self.shell("getprop", "ro.serialno").strip()

    def model(self) -> str:
        return self.shell("getprop", "ro.product.model").strip()

    # -- getting connected -------------------------------------------------

    def pair(self, address: str, code: str) -> str:
        """Pair with a phone over Wi-Fi, using the code it is showing.

        Android shows a pairing address and a six digit code under Wireless
        debugging. Both are typed by the user and both are checked before they
        are sent anywhere.
        """
        if not re.fullmatch(r"[\w.\-]+:\d{1,5}", (address or "").strip()):
            raise AdbError(f"{address!r} is not an address and port, like 192.168.18.5:37000.")
        if not re.fullmatch(r"\d{6}", (code or "").strip()):
            raise AdbError("The pairing code is the six digits the phone is showing.")
        return self._adb("pair", address.strip(), code.strip(), timeout=25.0)

    def connect(self, address: str) -> str:
        if not re.fullmatch(r"[\w.\-]+:\d{1,5}", (address or "").strip()):
            raise AdbError(f"{address!r} is not an address and port, like 192.168.18.5:5555.")
        answer = self._adb("connect", address.strip(), timeout=20.0)
        if "unable to connect" in answer.lower() or "failed" in answer.lower():
            raise AdbError(answer.strip().splitlines()[0])
        return answer

    def disconnect(self, address: str) -> str:
        """Drop one connection. Being already gone is not a failure.

        It does not stay dropped on its own: a paired phone that is still
        advertising is picked up again within seconds. This is for a phone that
        has been taken off the list, where nothing will ask for it afterwards.
        """
        if not re.fullmatch(r"[\w.\-]+:\d{1,5}", (address or "").strip()):
            raise AdbError(f"{address!r} is not an address and port, like 192.168.18.5:5555.")
        return self._adb("disconnect", address.strip(), timeout=20.0)


SCRCPY_LOCATIONS = (
    r"%LOCALAPPDATA%\Programs\scrcpy\scrcpy.exe",
    r"%USERPROFILE%\Downloads\scrcpy-win64\scrcpy.exe",
)


CONNECT_SERVICE = "_adb-tls-connect"
PAIRING_SERVICE = "_adb-tls-pairing"


def parse_mdns(payload: str, service: str = CONNECT_SERVICE) -> list[tuple[str, str]]:
    """The phones adb can see advertising themselves, and where they are.

    Worth having separately from "adb devices": a phone announces itself over
    mDNS whether or not adb has managed to connect to it, so this still knows
    where a phone is when the device list has gone empty. The port changes
    every time wireless debugging comes back, which is exactly the situation
    the list cannot help with.
    """
    found: list[tuple[str, str]] = []
    for line in (payload or "").splitlines():
        parts = line.split()
        # name, service type, address. The heading line has no address on it.
        # One service at a time, because a phone advertises two: a port to
        # connect on and a port to pair on. They are not interchangeable, and
        # offering one where the other is wanted gets nowhere.
        if len(parts) < 3 or not parts[1].startswith(service):
            continue
        address = parts[2]
        if ":" not in address:
            continue
        found.append((parts[0], address))
    return found


def mdns_services(
    adb: str = "", run=_default_runner, service: str = CONNECT_SERVICE
) -> list[tuple[str, str]]:
    """Ask adb what is advertising itself. Empty if it cannot say."""
    binary = adb or find_adb()
    if not binary:
        return []
    try:
        code, out, _err = run([binary, "mdns", "services"], 15.0)
    except OSError:
        return []
    if code != 0:
        return []
    return parse_mdns(out, service)


def restart_server(adb: str = "", run=_default_runner) -> bool:
    """Stop and start adb's own server.

    Blunt, and the only thing that reliably works. A phone reached over
    wireless debugging is connected through a TLS handshake that only adb's
    mDNS auto-connect performs; "adb connect" to the advertised port is refused
    because it is not that. When adb has lost a phone that is plainly still
    advertising itself, restarting the server is what makes it look again.

    Connecting takes a few seconds afterwards, so the caller should expect the
    phone on a later visit rather than this one.
    """
    binary = adb or find_adb()
    if not binary:
        return False
    try:
        run([binary, "kill-server"], 15.0)
        code, _out, _err = run([binary, "start-server"], 25.0)
    except OSError:
        return False
    return code == 0


def find_scrcpy() -> str:
    """Where scrcpy is, or "" if it is not on this machine.

    Mind does not ship it and does not need it: mirroring is a window scrcpy
    opens over the same connection Mind already has, and a machine without it
    simply does not offer the button.
    """
    import os

    found = shutil.which("scrcpy")
    if found:
        return found
    for candidate in SCRCPY_LOCATIONS:
        expanded = Path(os.path.expandvars(candidate))
        if "%" not in str(expanded) and expanded.is_file():
            return str(expanded)
    return ""


# What a phone's screen is worth sending over the air. The full panel is
# 1080x2424 at whatever the phone refreshes at, which is a lot of pixels to
# push through a radio and shows up as a picture that lags rather than one that
# looks worse. Under a thousand-odd pixels and thirty frames it stays readable
# in a desktop window and stops being the thing the link cannot keep up with.
#
# Both flags are old enough that every scrcpy worth having takes them, which
# matters because Mind bundles no copy and uses whichever one is installed.
WIRELESS_MIRROR_OPTIONS = ("--max-size=1200", "--max-fps=30")


def is_wireless(serial: str) -> bool:
    """Whether this phone is reached over the network rather than a cable.

    A USB serial is the number stamped on the phone. Anything with a port in it
    or an mDNS name arrived over the air, and it is the air that is slow.
    """
    return ":" in (serial or "") or "._tcp" in (serial or "")


def mirror(serial: str = "", scrcpy: str = "", spawn=None) -> bool:
    """Open the phone's screen on this PC. True if it was started.

    Left running on its own rather than waited for - it is a window the user
    closes when they are done with it, not a command with an answer.

    Over the air it is asked for less: see the note on the options above. On a
    cable it is asked for everything, because there is no reason not to.
    """
    program = scrcpy or find_scrcpy()
    if not program:
        return False
    start = spawn or subprocess.Popen
    command = [program]
    if serial:
        command += ["-s", serial]
    if is_wireless(serial):
        command += list(WIRELESS_MIRROR_OPTIONS)
    try:
        start(
            command,
            creationflags=CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, ValueError):
        return False
    return True


def attached(adb: str = "", run=_default_runner) -> list[AndroidDevice]:
    """Every phone adb can see, without needing a phone chosen first."""
    return Phone(adb=adb, run=run).devices()
