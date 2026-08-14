from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes


CRYPTPROTECT_UI_FORBIDDEN = 0x1


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _input_blob(payload: bytes) -> tuple[DATA_BLOB, ctypes.Array]:
    buffer = ctypes.create_string_buffer(payload)
    blob = DATA_BLOB(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    return blob, buffer


def protect_text(value: str) -> str:
    """Encrypt text for the current Windows user using DPAPI."""
    if not value:
        return ""
    if not hasattr(ctypes, "windll"):
        raise RuntimeError("Mind secure storage requires Windows DPAPI.")

    source, source_buffer = _input_blob(value.encode("utf-8"))
    output = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    ok = crypt32.CryptProtectData(
        ctypes.byref(source),
        "Mind API credentials",
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output),
    )
    _ = source_buffer
    if not ok:
        raise ctypes.WinError()
    try:
        encrypted = ctypes.string_at(output.pbData, output.cbData)
        return base64.b64encode(encrypted).decode("ascii")
    finally:
        kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))


def unprotect_text(value: str) -> str:
    """Decrypt DPAPI text for the current Windows user."""
    if not value:
        return ""
    if not hasattr(ctypes, "windll"):
        raise RuntimeError("Mind secure storage requires Windows DPAPI.")

    try:
        encrypted = base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("The protected credential data is invalid.") from exc

    source, source_buffer = _input_blob(encrypted)
    output = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output),
    )
    _ = source_buffer
    if not ok:
        raise ctypes.WinError()
    try:
        plaintext = ctypes.string_at(output.pbData, output.cbData)
        return plaintext.decode("utf-8")
    finally:
        kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))
