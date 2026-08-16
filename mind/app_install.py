"""Move a packaged Mind out of wherever it was downloaded to.

The README asks people to copy Mind.exe somewhere permanent before running it,
and in practice almost nobody does. Running from the Downloads folder has real
consequences: Windows Search lists every downloaded copy as a separate app, the
browser appends "(1)", "(2)" and so on instead of replacing, in-place updates
rewrite files inside Downloads, and clearing out Downloads deletes the app.

This module offers to install once, on first run, and never asks again.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .paths import install_dir, launcher_path, start_menu_shortcut


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Mind"
CREATE_NO_WINDOW = 0x08000000


class InstallError(RuntimeError):
    pass


def current_executable() -> Path:
    return Path(sys.executable).resolve()


def installed_executable() -> Path:
    return install_dir() / "Mind.exe"


def is_running_from_install_dir(executable: Path | None = None) -> bool:
    target = executable or current_executable()
    try:
        return target == installed_executable().resolve()
    except OSError:
        return False


def should_offer_install(
    config: dict,
    executable: Path | None = None,
    minimized: bool = False,
) -> bool:
    """True when a packaged Mind is running from somewhere it should not live.

    A launch that starts minimized is Mind coming up at login, where a modal
    question is both intrusive and easy to miss: Windows opens dialogs minimized
    when the session is not interactive yet. Stay quiet and ask the next time
    somebody opens Mind themselves.
    """
    if not getattr(sys, "frozen", False):
        return False
    if minimized:
        return False
    if config.get("install_prompt_dismissed", False):
        return False
    return not is_running_from_install_dir(executable)


def _clean_environment() -> dict[str, str]:
    """Drop the PyInstaller variables that break a freshly launched build."""
    env = os.environ.copy()
    for name in ("_MEIPASS2", "_MEIPASS", "PYTHONHOME", "PYTHONPATH"):
        env.pop(name, None)
    if "PATH" in env:
        env["PATH"] = os.pathsep.join(
            part for part in env["PATH"].split(os.pathsep) if "_MEI" not in part
        )
    return env


def _create_start_menu_shortcut(target: Path) -> None:
    shortcut = start_menu_shortcut()
    shortcut.parent.mkdir(parents=True, exist_ok=True)
    script = (
        "$sh = New-Object -ComObject WScript.Shell; "
        f"$sc = $sh.CreateShortcut('{shortcut}'); "
        f"$sc.TargetPath = '{target}'; "
        f"$sc.WorkingDirectory = '{target.parent}'; "
        "$sc.Description = 'Mind - AI Writing Workspace'; "
        f"$sc.IconLocation = '{target},0'; "
        "$sc.Save()"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=CREATE_NO_WINDOW,
    )
    if completed.returncode != 0:
        raise InstallError("Windows could not create the Start Menu shortcut.")


def _repoint_startup_entry(target: Path) -> None:
    """Keep "start with Windows" working after the executable moves."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            existing, _kind = winreg.QueryValueEx(key, RUN_VALUE)
    except FileNotFoundError:
        return
    if not isinstance(existing, str):
        return
    arguments = " --minimized" if "--minimized" in existing else ""
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, f'"{target}"{arguments}')


def install_to_programs(source: Path | None = None) -> Path:
    """Copy the running build into its permanent home and wire up shortcuts."""
    if not getattr(sys, "frozen", False):
        raise InstallError("Installing is only available in the packaged Mind app.")
    origin = (source or current_executable()).resolve()
    target = installed_executable()
    if origin == target:
        return target
    if not origin.is_file():
        raise InstallError("The running Mind executable could not be found.")

    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(target.name + ".incoming")
    try:
        # Stage beside the target so a half-written copy is never left behind
        # under the name the shortcut and startup entry point at.
        with origin.open("rb") as reader, staged.open("wb") as writer:
            while chunk := reader.read(1024 * 1024):
                writer.write(chunk)
        if staged.stat().st_size != origin.stat().st_size:
            raise InstallError("The copied Mind executable was incomplete.")
        os.replace(staged, target)
    except OSError as exc:
        try:
            staged.unlink()
        except OSError:
            pass
        raise InstallError(f"Mind could not be copied into place: {exc}") from exc

    _create_start_menu_shortcut(target)
    _repoint_startup_entry(target)
    return target


def relaunch_after_exit(target: Path, minimized: bool = False) -> None:
    """Start the installed build once this process has released its singleton.

    The application holds a named mutex, so a replacement launched immediately
    would find the old instance still alive, surface that window instead, and
    exit. Waiting on this process id avoids that.
    """
    arguments = " '--minimized'" if minimized else ""
    script = (
        f"$deadline = (Get-Date).AddSeconds(30); "
        f"while ((Get-Process -Id {os.getpid()} -ErrorAction SilentlyContinue) "
        f"-and (Get-Date) -lt $deadline) {{ Start-Sleep -Milliseconds 200 }}; "
        f"Start-Process -FilePath '{target}' -WorkingDirectory '{target.parent}'{arguments}"
    )
    try:
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            env=_clean_environment(),
            close_fds=True,
            creationflags=CREATE_NO_WINDOW,
        )
    except OSError as exc:
        raise InstallError("Windows could not restart Mind after installing.") from exc


MAX_DESCRIPTION_PARTS = 3


def source_description(executable: Path | None = None) -> str:
    """A short, human-readable name for where Mind is running from.

    Kept deliberately short: this goes in a dialog, and a deeply nested path
    wraps over several lines and buries the question being asked.
    """
    target = executable or current_executable()
    parent = target.parent
    home = Path.home()
    try:
        described = parent.relative_to(home)
        if str(described) == ".":
            described = parent
    except ValueError:
        described = parent

    parts = described.parts
    if len(parts) > MAX_DESCRIPTION_PARTS:
        tail = os.sep.join(parts[-MAX_DESCRIPTION_PARTS:])
        return f"...{os.sep}{tail}"
    return str(described)


__all__ = [
    "InstallError",
    "install_to_programs",
    "installed_executable",
    "is_running_from_install_dir",
    "launcher_path",
    "relaunch_after_exit",
    "should_offer_install",
    "source_description",
]
