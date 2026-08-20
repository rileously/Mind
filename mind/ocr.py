from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QTransform


MAX_OCR_DIMENSION = 2400
OCR_SCRIPT = Path(__file__).with_name("windows_ocr.ps1")


class OcrError(RuntimeError):
    pass


def extract_text_at_turns(image: QImage, timeout: float = 30.0):
    """Read the same image at each quarter turn, upright first.

    Windows OCR reads sideways text as nothing at all, and a card photographed
    on a table is sideways about as often as not - so rather than telling
    somebody to rotate their photograph, it is turned here. Yielded rather
    than returned as a list, so a caller that finds what it wants at the first
    turn never pays for the other three.
    """
    for turn in (0, 90, 270, 180):
        if turn:
            turned = image.transformed(QTransform().rotate(turn))
        else:
            turned = image
        try:
            found = extract_text_from_image(turned, timeout=timeout)
        except OcrError:
            continue
        if found.strip():
            yield found


def extract_text_from_image(image: QImage, timeout: float = 30.0) -> str:
    if image.isNull():
        raise OcrError("The clipboard image could not be read.")
    if os.name != "nt":
        raise OcrError("Image text extraction currently requires Windows.")
    if not OCR_SCRIPT.exists():
        raise OcrError("Mind's local OCR component is missing.")

    prepared = image.convertToFormat(QImage.Format_ARGB32)
    if prepared.width() > MAX_OCR_DIMENSION or prepared.height() > MAX_OCR_DIMENSION:
        prepared = prepared.scaled(
            MAX_OCR_DIMENSION,
            MAX_OCR_DIMENSION,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )

    with tempfile.TemporaryDirectory(prefix="mind-ocr-") as temporary:
        image_path = Path(temporary) / "clipboard.png"
        if not prepared.save(str(image_path), "PNG"):
            raise OcrError("Mind could not prepare the clipboard image for OCR.")
        command = [
            str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(OCR_SCRIPT),
            "-ImagePath",
            str(image_path),
        ]
        try:
            process = subprocess.run(
                command,
                capture_output=True,
                check=False,
                timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            raise OcrError("Image text extraction timed out.") from exc
        except OSError as exc:
            raise OcrError("Windows OCR could not be started.") from exc

    stdout = process.stdout.decode("utf-8-sig", "replace").strip()
    stderr = process.stderr.decode("utf-8-sig", "replace").strip()
    if process.returncode != 0:
        detail = stderr.splitlines()[-1].strip() if stderr else "Windows OCR failed."
        if "No Windows OCR language" in detail:
            raise OcrError("No Windows OCR language is installed. Add one in Windows Language settings.")
        raise OcrError("Windows OCR could not read this image.")
    if not stdout:
        raise OcrError("No readable text was found in the image.")
    return stdout
