"""Read the machine's state and drive a few safe controls from Telegram.

Deliberately a fixed set of actions rather than a way to run commands. The
bridge already refuses shell replacers because a chat bot reachable with a
bearer token is a poor place to put code execution, and a general "run this"
verb would hand back exactly what that refusal removes.

So: report status, lock, sleep, media keys, and power actions that ask before
they act. Everything here is either read-only or reversible, except shutdown
and restart, which are gated behind their own setting and a confirmation.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


CREATE_NO_WINDOW = 0x08000000

# Virtual key codes for the media and volume keys. Sending these is the same as
# pressing the buttons on a keyboard, so whichever player has focus responds.
MEDIA_KEYS = {
    "play": 0xB3,
    "pause": 0xB3,
    "next": 0xB0,
    "prev": 0xB1,
    "stop": 0xB2,
    "mute": 0xAD,
    "voldown": 0xAE,
    "volup": 0xAF,
}
KEYEVENTF_KEYUP = 0x0002


class SystemActionError(RuntimeError):
    pass


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", wt.DWORD),
        ("dwMemoryLoad", wt.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class _PowerStatus(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", wt.BYTE),
        ("BatteryFlag", wt.BYTE),
        ("BatteryLifePercent", wt.BYTE),
        ("SystemStatusFlag", wt.BYTE),
        ("BatteryLifeTime", wt.DWORD),
        ("BatteryFullLifeTime", wt.DWORD),
    ]


@dataclass(frozen=True)
class Status:
    battery_percent: int | None
    on_mains: bool | None
    memory_used_percent: int | None
    memory_total_gb: float | None
    uptime_hours: float | None
    disks: list[tuple[str, float, float]]  # drive, free GB, total GB


def _format_uptime(hours: float) -> str:
    if hours < 1:
        return f"{int(hours * 60)} minutes"
    days, remainder = divmod(hours, 24)
    if days >= 1:
        return f"{int(days)}d {int(remainder)}h"
    return f"{hours:.1f} hours"


def read_status(drives: list[str] | None = None) -> Status:
    """Battery, memory, uptime and disk space, all via the Windows API."""
    kernel32 = ctypes.windll.kernel32

    battery: int | None = None
    mains: bool | None = None
    power = _PowerStatus()
    if kernel32.GetSystemPowerStatus(ctypes.byref(power)):
        if power.BatteryLifePercent != 255:
            battery = int(power.BatteryLifePercent)
        if power.ACLineStatus in (0, 1):
            mains = power.ACLineStatus == 1

    used_percent: int | None = None
    total_gb: float | None = None
    memory = _MemoryStatus()
    memory.dwLength = ctypes.sizeof(_MemoryStatus)
    if kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
        used_percent = int(memory.dwMemoryLoad)
        total_gb = memory.ullTotalPhys / (1024 ** 3)

    uptime_hours: float | None = None
    try:
        kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        uptime_hours = kernel32.GetTickCount64() / (1000 * 60 * 60)
    except (AttributeError, OSError):
        pass

    found: list[tuple[str, float, float]] = []
    candidates = drives if drives is not None else [f"{chr(letter)}:\\" for letter in range(65, 91)]
    for drive in candidates:
        try:
            if not Path(drive).exists():
                continue
            usage = shutil.disk_usage(drive)
        except OSError:
            continue
        found.append((drive, usage.free / (1024 ** 3), usage.total / (1024 ** 3)))

    return Status(
        battery_percent=battery,
        on_mains=mains,
        memory_used_percent=used_percent,
        memory_total_gb=total_gb,
        uptime_hours=uptime_hours,
        disks=found,
    )


def format_status(status: Status, host: str = "") -> str:
    lines = [f"🖥  {host}" if host else "🖥  This PC", ""]
    if status.battery_percent is not None:
        plug = "charging" if status.on_mains else "on battery"
        lines.append(f"🔋  {status.battery_percent}%  ({plug})")
    elif status.on_mains:
        lines.append("🔌  Mains power, no battery")
    if status.memory_used_percent is not None and status.memory_total_gb:
        lines.append(
            f"🧠  Memory {status.memory_used_percent}% of {status.memory_total_gb:.0f} GB"
        )
    if status.uptime_hours is not None:
        lines.append(f"⏱  Up {_format_uptime(status.uptime_hours)}")
    if status.disks:
        lines.append("")
        for drive, free, total in status.disks:
            share = (1 - free / total) * 100 if total else 0
            lines.append(f"💾  {drive}  {free:.0f} GB free of {total:.0f} GB  ({share:.0f}% used)")
    return "\n".join(lines)


def lock_workstation() -> None:
    """Lock the session. Reversible with the user's own password."""
    if not ctypes.windll.user32.LockWorkStation():
        raise SystemActionError("Windows would not lock the session.")


def sleep_pc() -> None:
    """Suspend to sleep. The machine wakes on a key press, so this is reversible.

    Note the bridge stops answering until the machine wakes, which is inherent
    rather than a fault.
    """
    try:
        ctypes.windll.powrprof.SetSuspendState(0, 1, 0)
    except (AttributeError, OSError) as exc:
        raise SystemActionError(f"Windows would not sleep: {exc}") from exc


def _run_shutdown(arguments: list[str]) -> None:
    try:
        completed = subprocess.run(
            ["shutdown.exe", *arguments],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SystemActionError(f"Could not reach the shutdown command: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise SystemActionError(detail or "Windows refused the request.")


def shutdown(delay_seconds: int = 60) -> None:
    """Schedule a shutdown with a delay, so it can still be called off."""
    _run_shutdown(["/s", "/t", str(max(0, delay_seconds))])


def restart(delay_seconds: int = 60) -> None:
    _run_shutdown(["/r", "/t", str(max(0, delay_seconds))])


def abort_shutdown() -> None:
    _run_shutdown(["/a"])


def press_media_key(name: str) -> None:
    key = MEDIA_KEYS.get((name or "").strip().lower())
    if key is None:
        raise SystemActionError(
            "Unknown control. Try play, pause, next, prev, stop, mute, volup or voldown."
        )
    user32 = ctypes.windll.user32
    user32.keybd_event(key, 0, 0, 0)
    user32.keybd_event(key, 0, KEYEVENTF_KEYUP, 0)


class _LastInput(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_ulong)]


def read_idle_minutes() -> float:
    """How long since the last key or mouse input, in minutes.

    Windows measures this for the whole session rather than per process, which
    is what "the PC is idle" should mean. Both this and the tick counter wrap
    after about 49 days, so the difference is masked to 32 bits rather than
    being allowed to go negative.
    """
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        info = _LastInput()
        info.cbSize = ctypes.sizeof(_LastInput)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        elapsed = (kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF
    except (AttributeError, OSError):
        return 0.0
    return elapsed / (1000 * 60)


TH32CS_SNAPPROCESS = 0x00000002
CREATE_NO_WINDOW = 0x08000000
# Killing any of these takes the session or the machine down with it, so they are
# refused however they are asked for. Mind is on the list because closing the app
# that runs the bridge would also close whatever was asking.
PROTECTED_PROCESSES = frozenset(
    {
        "mind.exe",
        "system",
        "registry",
        "smss.exe",
        "csrss.exe",
        "wininit.exe",
        "winlogon.exe",
        "services.exe",
        "lsass.exe",
        "svchost.exe",
        "dwm.exe",
    }
)


class _ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


def _process_entries() -> list[tuple[str, int]]:
    """Every running process as (lowercase name, pid).

    A snapshot through ToolHelp rather than running tasklist: this is checked on
    every poll, and spawning a console process every twenty-five seconds to read
    a list the API already has would be waste with a flicker attached.
    """
    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == -1:
        return []
    found: list[tuple[str, int]] = []
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(_ProcessEntry)
        if not kernel32.Process32First(snapshot, ctypes.byref(entry)):
            return []
        while True:
            name = entry.szExeFile.decode("latin-1", "replace").lower()
            found.append((name, int(entry.th32ProcessID)))
            if not kernel32.Process32Next(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    return found


def read_running_apps() -> frozenset[str]:
    return frozenset(name for name, _pid in _process_entries())


def normalise_app(name: str) -> str:
    """The name as the process list has it, so "Game" and "game.exe" both match."""
    cleaned = (name or "").strip().strip('"').lower()
    cleaned = cleaned.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    return cleaned if cleaned.endswith(".exe") else f"{cleaned}.exe"


def close_app(name: str, force_after_seconds: float = 4.0) -> str:
    """Close an application, asking politely before insisting.

    A window is asked to close first, which is what pressing X does and gives
    the program its chance to save. Anything still running after that is ended,
    because a game that ignores the request is exactly the case this is for.
    Returns a sentence describing what happened.
    """
    import subprocess
    import time as _time

    target = normalise_app(name)
    if target in PROTECTED_PROCESSES:
        raise SystemActionError(f"{target} keeps Windows running and cannot be closed.")
    pids = [pid for process, pid in _process_entries() if process == target]
    if not pids:
        raise SystemActionError(f"{target} is not running.")

    def kill(arguments: list[str]) -> None:
        for pid in pids:
            try:
                subprocess.run(
                    ["taskkill.exe", "/PID", str(pid), *arguments],
                    capture_output=True,
                    creationflags=CREATE_NO_WINDOW,
                    timeout=15,
                )
            except (OSError, subprocess.SubprocessError):
                continue

    kill([])
    deadline = _time.monotonic() + max(0.0, force_after_seconds)
    while _time.monotonic() < deadline:
        if target not in read_running_apps():
            return f"{target} closed."
        _time.sleep(0.4)

    kill(["/T", "/F"])
    _time.sleep(0.6)
    if target in read_running_apps():
        raise SystemActionError(f"{target} would not close.")
    return f"{target} was forced to close."


# Windows plumbing that owns a titled window without being an application
# anyone means. Explorer is here because closing it takes the taskbar and the
# desktop with it, which is not what "close that app" means from a phone.
SHELL_PROCESSES = frozenset(
    {
        "explorer.exe",
        "textinputhost.exe",
        "applicationframehost.exe",
        "shellexperiencehost.exe",
        "searchhost.exe",
        "startmenuexperiencehost.exe",
        "lockapp.exe",
    }
)


def read_visible_apps(limit: int = 14) -> list[tuple[str, str]]:
    """The applications a person would say are open, as (process, window title).

    Filtered by having a visible top-level window with a title, because "what is
    running" to someone holding a phone means Chrome and the game - not the
    hundred service processes that also happen to be running. One entry per
    program: a browser with nine windows is still one thing to close.
    """
    user32 = ctypes.windll.user32
    processes = {pid: name for name, pid in _process_entries()}
    found: dict[str, str] = {}

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def visit(handle, _param):
        if not user32.IsWindowVisible(handle):
            return True
        length = user32.GetWindowTextLengthW(handle)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, buffer, length + 1)
        title = buffer.value.strip()
        if not title:
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        name = processes.get(int(pid.value), "")
        if not name or name in PROTECTED_PROCESSES or name in SHELL_PROCESSES:
            # Nothing that cannot be closed, or that is the desktop itself, is
            # worth offering.
            return True
        # The first window seen for a program gives it its title, which is
        # usually the one in front.
        found.setdefault(name, title)
        return True

    user32.EnumWindows(visit, 0)
    return sorted(found.items())[:limit]


DRIVE_REMOVABLE = 2
DRIVE_CDROM = 5


def read_removable_drives() -> frozenset[str]:
    """Drive letters Windows calls removable, which is what a USB stick becomes.

    Read from the drive table rather than the device tree: it is instant, needs
    no elevation, and covers the case a person means by "my USB drive appeared".
    A phone connected over MTP has no drive letter and will not show here.
    """
    kernel32 = ctypes.windll.kernel32
    mask = kernel32.GetLogicalDrives()
    found = set()
    for index in range(26):
        if not mask & (1 << index):
            continue
        letter = f"{chr(65 + index)}:\\"
        if kernel32.GetDriveTypeW(letter) in (DRIVE_REMOVABLE, DRIVE_CDROM):
            found.add(letter)
    return frozenset(found)


def drive_label(letter: str) -> str:
    """The name Explorer shows for a drive, when it has one."""
    name = ctypes.create_unicode_buffer(261)
    try:
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(letter), name, 261, None, None, None, None, 0
        )
    except (AttributeError, OSError):
        return ""
    return name.value.strip() if ok else ""


def read_wifi_networks(timeout: float = 15.0) -> frozenset[str]:
    """The Wi-Fi networks in range, by name.

    netsh is the only way to this without the WLAN API, so it costs a process
    each time and is read only when something is actually watching for networks.
    """
    import re
    import subprocess

    try:
        completed = subprocess.run(
            ["netsh", "wlan", "show", "networks"],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    if completed.returncode != 0:
        # No wireless adapter, or the service is off. Nothing to report rather
        # than an error every twenty-five seconds.
        return frozenset()
    names = re.findall(r"^\s*SSID \d+\s*:\s*(.+?)\s*$", completed.stdout, re.MULTILINE)
    return frozenset(name for name in names if name)
