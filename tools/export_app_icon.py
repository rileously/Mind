from __future__ import annotations

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from PySide6.QtWidgets import QApplication

from mind.theme import app_icon


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: export_app_icon.py OUTPUT.ico")
    output = Path(sys.argv[1]).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    _ = app
    pixmap = app_icon(256).pixmap(256, 256)
    if not pixmap.save(str(output), "ICO"):
        raise RuntimeError(f"Could not create {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
