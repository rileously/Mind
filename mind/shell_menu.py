"""Register a "Send to Telegram" entry in the Windows Explorer context menu.

Written under HKEY_CURRENT_USER, so it needs no administrator rights and is
removed cleanly by unregistering. This is the legacy context menu, which on
Windows 11 means the entry appears under "Show more options" rather than in the
compact menu; the compact one requires a signed MSIX package with an
IExplorerCommand handler, and Mind's executable is unsigned.

Selecting several files starts one process per file. That is fine here: each
file is sent on its own, so there is nothing to combine and nothing to
coordinate between them.
"""

from __future__ import annotations

import sys
import winreg
from pathlib import Path


# "*" applies to every file type, which is what "send any file" needs. The verb
# name is prefixed so it can never collide with another application's entry.
ALL_FILES_KEY = r"Software\Classes\*\shell\MindSendToTelegram"
COMMAND_SUBKEY = "command"
MENU_LABEL = "Send to Telegram"


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


def apply(enabled: bool, executable: Path | None = None) -> None:
    """Make the registry match the setting, including after Mind moves.

    Re-registering when already present is deliberate: the first-run installer
    relocates the executable, and an entry still pointing at the old copy would
    fail silently from the user's point of view.
    """
    if not is_supported():
        return
    if enabled:
        register(executable)
    else:
        unregister()
