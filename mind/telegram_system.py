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
