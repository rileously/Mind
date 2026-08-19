"""Register "Send to Telegram" in the Windows Explorer context menu.

Windows offers two entirely different ways in, and Mind uses both because
neither one covers every machine:

* A sparse MSIX package with an IExplorerCommand handler. This is the only thing
  Windows 11 will show in the compact menu, the one that opens on a right-click.
  It needs package identity, so it needs a signed package, and Windows refuses
  the package outright when it does not trust the signing certificate.
* A plain registry verb under HKEY_CURRENT_USER. This works everywhere and needs
  no privileges or signature, but Windows 11 files it under "Show more options".

The package is tried first and the verb is the fallback, so an unsigned build
still gets an entry rather than nothing. Only ever one of the two is registered:
both at once puts the same command in the menu twice.

Selecting several files starts one process per file. That is fine here: each
file is sent on its own, so there is nothing to combine and nothing to
coordinate between them.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import winreg
from pathlib import Path

from . import __version__


# "*" applies to every file type, which is what "send any file" needs. The verb
# name is prefixed so it can never collide with another application's entry.
ALL_FILES_KEY = r"Software\Classes\*\shell\MindSendToTelegram"
COMMAND_SUBKEY = "command"
MENU_LABEL = "Send to Telegram"

# Must match Identity Name in shell\AppxManifest.xml.
PACKAGE_NAME = "Mind.ShellMenu"
PACKAGE_FILE = "MindShellMenu.msix"
HANDLER_DLL = "MindShellMenu.dll"
# Where Mind.spec puts the handler inside the packaged executable.
BUNDLED_SUBDIR = "shell_menu"
# Packages that register a COM server are listed here, which is a far cheaper
# check on every launch than starting PowerShell to ask.
PACKAGED_COM_KEY = r"Software\Classes\PackagedCom\Package"
# Appx versions are four-part; the manifest pads Mind's three.
PACKAGE_VERSION = f"{__version__}.0"
CREATE_NO_WINDOW = 0x08000000


def executable_path() -> Path:
    """The command the menu entry should run.

    Only meaningful for the packaged build; running from source would point the
    entry at a Python interpreter that will not be there tomorrow.
    """
    return Path(sys.executable).resolve()


def is_supported() -> bool:
    return bool(getattr(sys, "frozen", False))


def is_registered() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ALL_FILES_KEY):
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def registered_command() -> str:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, f"{ALL_FILES_KEY}\\{COMMAND_SUBKEY}"
        ) as key:
            value, _kind = winreg.QueryValueEx(key, "")
            return str(value)
    except (FileNotFoundError, OSError):
        return ""


def register(executable: Path | None = None) -> None:
    target = Path(executable) if executable else executable_path()
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, ALL_FILES_KEY) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, MENU_LABEL)
        winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, f"{target},0")
    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER, f"{ALL_FILES_KEY}\\{COMMAND_SUBKEY}"
    ) as key:
        winreg.SetValueEx(
            key, "", 0, winreg.REG_SZ, f'"{target}" --telegram-send "%1"'
        )


def unregister() -> None:
    for path in (f"{ALL_FILES_KEY}\\{COMMAND_SUBKEY}", ALL_FILES_KEY):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except FileNotFoundError:
            continue
        except OSError:
            continue


def bundled_handler_dir() -> Path | None:
    """Where the compiled handler and its package are, or None when absent.

    Absent is normal: the handler needs MSVC and the Windows SDK, which a build
    machine may not have, and Mind has to keep working without it.
    """
    root = getattr(sys, "_MEIPASS", None)
    candidates = [Path(root) / BUNDLED_SUBDIR] if root else []
    # From a source checkout, use whatever the build script last produced.
    candidates.append(Path(__file__).resolve().parents[1] / "artifacts" / "shell")
    for candidate in candidates:
        if (candidate / PACKAGE_FILE).is_file() and (candidate / HANDLER_DLL).is_file():
            return candidate
    return None


def registered_package_full_name() -> str:
    """The full name of Mind's registered package, or "" when it is not there."""
    prefix = f"{PACKAGE_NAME}_"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PACKAGED_COM_KEY) as key:
            index = 0
            while True:
                try:
                    name = winreg.EnumKey(key, index)
                except OSError:
                    return ""
                if name.startswith(prefix):
                    return name
                index += 1
    except (FileNotFoundError, OSError):
        return ""


def package_is_current() -> bool:
    """Whether the registered package is this version of Mind's.

    An update leaves the old package registered against the old manifest, and
    its handler would keep answering with stale behaviour.
    """
    full_name = registered_package_full_name()
    return bool(full_name) and full_name.startswith(f"{PACKAGE_NAME}_{PACKAGE_VERSION}_")


def _run_powershell(script: str, timeout: float = 180.0) -> tuple[int, str]:
    """Run PowerShell without flashing a console window over whatever is on top."""
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return completed.returncode, f"{completed.stdout}\n{completed.stderr}".strip()


def _stage_handler(source: Path, install_dir: Path) -> bool:
    """Put the handler DLL beside Mind.exe, which is the external location.

    A copy that fails because the shell still has the old DLL loaded is not
    fatal: the file already there is the one the package will use.
    """
    destination = install_dir / HANDLER_DLL
    try:
        if destination.is_file() and destination.stat().st_size == source.stat().st_size:
            return True
    except OSError:
        pass
    try:
        shutil.copy2(source, destination)
        return True
    except OSError:
        return destination.is_file()


def register_package(executable: Path | None = None) -> bool:
    """Register the sparse package, returning whether it is now in place.

    False is an ordinary outcome, not an error: an unsigned build, or a machine
    that does not trust the certificate, cannot have this and falls back to the
    registry verb.
    """
    assets = bundled_handler_dir()
    if assets is None:
        return False
    target = Path(executable) if executable else executable_path()
    install_dir = target.parent
    if not _stage_handler(assets / HANDLER_DLL, install_dir):
        return False
    if package_is_current():
        return True
    # A stale package holds the name, so it has to go before this one can land.
    if registered_package_full_name():
        unregister_package()
    package = assets / PACKAGE_FILE
    code, _output = _run_powershell(
        "Add-AppxPackage -Path {} -ExternalLocation {}".format(
            _quote(package), _quote(install_dir)
        )
    )
    if code != 0:
        return False
    return package_is_current()


def unregister_package() -> None:
    full_name = registered_package_full_name()
    if not full_name:
        return
    _run_powershell(f"Remove-AppxPackage -Package {_quote(full_name)}")


def _quote(value: object) -> str:
    """Single-quote for PowerShell, where a doubled quote is the escape."""
    return "'" + str(value).replace("'", "''") + "'"


def apply(enabled: bool, executable: Path | None = None) -> bool:
    """Make Windows match the setting, including after Mind moves.

    Re-applying when already present is deliberate: the first-run installer
    relocates the executable, and an entry still pointing at the old copy would
    fail silently from the user's point of view.

    Returns whether an entry is now registered as asked, so a caller can say why
    the entry is missing instead of leaving the user hunting for it.
    """
    if not is_supported():
        return not enabled
    if not enabled:
        unregister()
        unregister_package()
        return True
    if register_package(executable):
        # The verb would repeat the same command under "Show more options".
        unregister()
        return True
    unregister_package()
    register(executable)
    return is_registered()


def in_compact_menu() -> bool:
    """Whether the entry is in the menu a plain right-click opens.

    Windows 11 hides registry verbs behind "Show more options"; the packaged
    handler is what puts a command in the first-level menu. Windows 10 shows
    everything at the first level, and so does Windows 11 with the compact menu
    switched off.
    """
    if package_is_current():
        return True
    return not _compact_menu_in_use()


def _compact_menu_in_use() -> bool:
    if sys.getwindowsversion().build < 22000:  # type: ignore[attr-defined]
        return False
    # The documented way to opt out: an empty in-process server for the compact
    # menu's CLSID, which leaves Explorer with the classic menu.
    opt_out = (
        r"Software\Classes\CLSID"
        r"\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}\InprocServer32"
    )
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, opt_out) as key:
            value, _kind = winreg.QueryValueEx(key, "")
            return bool(str(value).strip())
    except (FileNotFoundError, OSError):
        return True


def describe() -> str:
    """A plain sentence about where the entry ended up, for the settings page."""
    if not is_supported():
        return "Only the installed Mind adds a right-click entry."
    if package_is_current():
        return "Registered as a packaged command, in the menu a right-click opens."
    if is_registered():
        if _compact_menu_in_use():
            return "Registered under \"Show more options\"."
        return "Registered, and Windows is set to show the classic menu."
    return "Not registered."
