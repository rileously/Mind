"""Safely remove the verified legacy SwiftSlate installation via the Recycle Bin."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path


FO_DELETE = 0x0003
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400
FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
EXPECTED_FILES = {".lock", "commands.json", "config.json", "SwiftSlate.pyw"}


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", wintypes.WORD),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    return bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)


def _recycle(paths: list[Path]) -> None:
    source_list = "\0".join(str(path) for path in paths) + "\0\0"
    source_buffer = ctypes.create_unicode_buffer(source_list)
    operation = SHFILEOPSTRUCTW()
    operation.wFunc = FO_DELETE
    operation.pFrom = ctypes.cast(source_buffer, wintypes.LPCWSTR)
    operation.fFlags = FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI
    shell32 = ctypes.windll.shell32
    shell32.SHFileOperationW.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
    shell32.SHFileOperationW.restype = ctypes.c_int
    result = shell32.SHFileOperationW(ctypes.byref(operation))
    if result != 0 or operation.fAnyOperationsAborted:
        raise OSError(f"Windows could not move SwiftSlate to the Recycle Bin (code {result}).")


def main() -> None:
    legacy = Path(r"C:\Users\User\.swiftslate")
    shortcut = Path(
        r"C:\Users\User\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\SwiftSlate Desktop.lnk"
    )

    if not legacy.exists():
        print("SwiftSlate installation is already absent.")
        return
    if legacy.resolve(strict=True) != legacy or not legacy.is_dir() or _is_reparse_point(legacy):
        raise RuntimeError("SwiftSlate installation path validation failed.")

    children = list(legacy.iterdir())
    if {child.name for child in children} != EXPECTED_FILES:
        raise RuntimeError("Unexpected content found in the SwiftSlate directory; refusing removal.")
    if any(not child.is_file() or _is_reparse_point(child) for child in children):
        raise RuntimeError("Unexpected directory or reparse point found; refusing removal.")

    targets = [legacy]
    if shortcut.exists():
        if not shortcut.is_file() or _is_reparse_point(shortcut):
            raise RuntimeError("SwiftSlate startup shortcut validation failed.")
        targets.append(shortcut)
    _recycle(targets)

    if legacy.exists() or shortcut.exists():
        raise RuntimeError("SwiftSlate removal did not complete.")
    print("SwiftSlate installation and startup shortcut moved to the Recycle Bin.")


if __name__ == "__main__":
    main()

