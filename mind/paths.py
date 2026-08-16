from __future__ import annotations

import os
from pathlib import Path


APP_NAME = "Mind"
SOURCE_DIR = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    override = os.environ.get("MIND_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    roaming = os.environ.get("APPDATA")
    base = Path(roaming) if roaming else Path.home() / "AppData" / "Roaming"
    return base / APP_NAME


def legacy_data_dir() -> Path:
    return Path.home() / ".swiftslate"


def local_app_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / APP_NAME


def runtime_dir() -> Path:
    """Where the packaged build unpacks its one-file runtime.

    Must stay in step with ``runtime_tmpdir`` in Mind.spec.
    """
    return local_app_dir() / "Runtime"


def install_dir() -> Path:
    """The permanent location a packaged Mind installs itself into."""
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / "Programs" / APP_NAME


def start_menu_shortcut() -> Path:
    roaming = os.environ.get("APPDATA")
    base = Path(roaming) if roaming else Path.home() / "AppData" / "Roaming"
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{APP_NAME}.lnk"


def engine_path() -> Path:
    return SOURCE_DIR / "SwiftSlate.pyw"


def launcher_path() -> Path:
    return SOURCE_DIR / "mind_app.pyw"

