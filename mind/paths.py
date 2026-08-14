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


def engine_path() -> Path:
    return SOURCE_DIR / "SwiftSlate.pyw"


def launcher_path() -> Path:
    return SOURCE_DIR / "mind_app.pyw"

