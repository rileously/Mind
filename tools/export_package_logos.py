"""Render the logos the sparse context menu package declares.

Windows validates that every logo a manifest names exists and is a PNG of the
right shape, so these are generated from Mind's own icon rather than kept as
checked-in copies that can drift away from it.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from PySide6.QtWidgets import QApplication

from mind.theme import app_icon

# The two sizes AppxManifest.xml refers to.
SIZES = (150, 44)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: export_package_logos.py OUTPUT_DIRECTORY")
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    _ = app
    for size in SIZES:
        target = output / f"logo_{size}x{size}.png"
        pixmap = app_icon(size).pixmap(size, size)
        if not pixmap.save(str(target), "PNG"):
            raise RuntimeError(f"Could not create {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
