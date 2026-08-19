"""Printing a file that arrived over Telegram.

Printing is genuinely detailed - which printer, what paper, colour or not - and
the interface for it is a phone. So the detail lives here as three short lists,
and the chat asks three questions with buttons rather than presenting a dialog.

What can be printed is decided by what Windows can actually do with the file,
not by a list of extensions someone hoped would work. PDFs, Word and Excel
documents are printed by their own handlers through the registered "printto"
verb; plain text goes straight to the printer; images are drawn onto the page by
Mind itself, because Windows 11 registers no print verb for them. Anything else
is refused up front, which is friendlier than a print job that silently never
appears.

The decisions are here and the mechanics are in windows_print.ps1, so this
module stays testable without a printer.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path


PRINT_SCRIPT = Path(__file__).with_name("windows_print.ps1")
CREATE_NO_WINDOW = 0x08000000

# How a file gets to the printer.
STRATEGY_VERB = "verb"
STRATEGY_TEXT = "text"
STRATEGY_IMAGE = "image"

# Handled by the application that owns the format. Nothing here is opened or
# parsed by Mind.
VERB_SUFFIXES = frozenset(
    {
        ".pdf",
        ".doc",
        ".docx",
        ".rtf",
        ".odt",
        ".xls",
        ".xlsx",
        ".ods",
        ".ppt",
        ".pptx",
        ".odp",
        ".xps",
        ".oxps",
    }
)
TEXT_SUFFIXES = frozenset(
    {".txt", ".log", ".md", ".csv", ".json", ".xml", ".ini", ".cfg", ".yml", ".yaml"}
)
IMAGE_SUFFIXES = frozenset(
    {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff", ".webp"}
)

# Printing is physical: paper and ink are spent, and the person holding the phone
# may not be in the room. A large file is refused rather than turned into a
# hundred pages nobody asked for.
MAX_PRINT_BYTES = 25 * 1024 * 1024


class PrintError(RuntimeError):
    pass


@dataclass(frozen=True)
class Paper:
    key: str
    label: str
    # The name Windows knows it by, for both Set-PrintConfiguration and the
    # PaperSize kinds a PrintDocument offers.
    windows_name: str


# Six is what fits two rows of buttons without the labels being cut off, and
# covers everything a home printer is loaded with.
PAPERS: tuple[Paper, ...] = (
    Paper("a4", "A4", "A4"),
    Paper("letter", "Letter", "Letter"),
    Paper("legal", "Legal", "Legal"),
    Paper("a5", "A5", "A5"),
    Paper("a3", "A3", "A3"),
    Paper("photo", "Photo 10×15", "A6"),
)


@dataclass(frozen=True)
class ColourMode:
    key: str
    label: str
    color: bool
    # Shown back to the user, so what was chosen is not a mystery afterwards.
    summary: str


# Asked directly rather than inferred from a "document type", which is what this
# step used to be. Type was never anything but colour underneath, and a question
# that means one thing should say that thing.
COLOUR_MODES: tuple[ColourMode, ...] = (
    ColourMode("mono", "🖤  Black and white", False, "black and white"),
    ColourMode("colour", "🎨  Colour", True, "colour"),
)


@dataclass(frozen=True)
class Printer:
    name: str
    default: bool = False
    # Whether the driver reports it can print both sides. Offering the choice on
    # a printer that cannot would be a button that quietly does nothing.
    duplex: bool = False


SIDES_ONE = "one"
SIDES_BOTH = "both"
# A physical cap. The person tapping "+" may be nowhere near the paper tray.
MAX_COPIES = 10


@dataclass(frozen=True)
class PrintJob:
    """A file being set up to print, and the choices made so far."""

    path: Path
    printers: tuple[Printer, ...] = ()
    printer: str = ""
    paper: str = ""
    colour: str = ""
    sides: str = SIDES_ONE
    copies: int = 1
    # Capability of the chosen printer, carried so the panel knows what to offer.
    duplex_capable: bool = False

    @property
    def strategy(self) -> str:
        return strategy_for(self.path) or STRATEGY_VERB

    def with_printers(self, names) -> "PrintJob":
        """Remember the printers as they were offered.

        A button means the printer whose name was on it. Re-reading the list when
        the tap arrives would silently repoint every button if one were added or
        removed in between.
        """
        return replace(self, printers=tuple(names))

    def with_printer(self, index: int) -> "PrintJob":
        if not 0 <= index < len(self.printers):
            return self
        chosen = self.printers[index]
        # Both sides cannot stay selected on a printer that cannot do it.
        sides = self.sides if chosen.duplex else SIDES_ONE
        return replace(
            self, printer=chosen.name, duplex_capable=chosen.duplex, sides=sides
        )

    def with_sides(self, sides: str) -> "PrintJob":
        if sides not in (SIDES_ONE, SIDES_BOTH):
            return self
        if sides == SIDES_BOTH and not self.duplex_capable:
            return self
        return replace(self, sides=sides)

    def with_copies(self, count: int | None) -> "PrintJob":
        """Set the number of copies, clamped rather than refused.

        The buttons only ever ask for one more or one less, so a value outside
        the range means a stale message; holding at the edge is friendlier than
        an error about a number the user never typed.
        """
        if count is None:
            return self
        return replace(self, copies=max(1, min(int(count), MAX_COPIES)))

    def with_paper(self, index: int) -> "PrintJob":
        if not 0 <= index < len(PAPERS):
            return self
        return replace(self, paper=PAPERS[index].key)

    def with_colour(self, index: int) -> "PrintJob":
        if not 0 <= index < len(COLOUR_MODES):
            return self
        return replace(self, colour=COLOUR_MODES[index].key)

    @property
    def is_complete(self) -> bool:
        return bool(self.printer and self.paper and self.colour)


def paper_by_key(key: str) -> Paper | None:
    return next((paper for paper in PAPERS if paper.key == key), None)


def colour_by_key(key: str) -> ColourMode | None:
    return next((mode for mode in COLOUR_MODES if mode.key == key), None)


def colour_is_advisory(path: Path | str) -> bool:
    """Whether the colour and paper choices can only be a request for this file.

    True for the formats printed by their own application: it takes a printer
    name and nothing else, so the settings have to be written to the printer,
    which Windows allows administrators alone. The choice is still offered - it
    works for anyone running as administrator - but the chat says up front that
    it may not take.
    """
    return strategy_for(path) == STRATEGY_VERB


def strategy_for(path: Path | str) -> str | None:
    """How this file would be printed, or None when it cannot be.

    Extension rather than content: the handler Windows hands the file to is
    chosen by extension too, so agreeing with it is the honest test.
    """
    suffix = Path(path).suffix.lower()
    if suffix in VERB_SUFFIXES:
        return STRATEGY_VERB
    if suffix in IMAGE_SUFFIXES:
        return STRATEGY_IMAGE
    if suffix in TEXT_SUFFIXES:
        return STRATEGY_TEXT
    return None


def is_printable(path: Path | str) -> bool:
    return strategy_for(path) is not None


def refusal_for(path: Path | str, size: int | None = None) -> str:
    """Why this file cannot be printed, or "" when it can.

    Written as a sentence for the chat, because "unsupported" tells the person
    holding the phone nothing about what to do instead.
    """
    target = Path(path)
    if strategy_for(target) is None:
        suffix = target.suffix.lower().lstrip(".") or "that kind of file"
        return (
            f"Windows has no way to print {suffix} without the application that "
            "made it. Send it as a PDF, an image, or plain text."
        )
    if size is None:
        try:
            size = target.stat().st_size
        except OSError:
            return "That file could not be read."
    if size > MAX_PRINT_BYTES:
        return "That file is too large to print from here."
    return ""


def _run_script(arguments: list[str], timeout: float) -> str:
    if os.name != "nt":
        raise PrintError("Printing requires Windows.")
    if not PRINT_SCRIPT.exists():
        raise PrintError("Mind's printing helper is missing from this build.")
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(PRINT_SCRIPT),
            ]
            + arguments,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise PrintError("The printer did not answer in time.") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise PrintError(f"Printing could not start: {exc}") from exc
    if completed.returncode != 0:
        raise PrintError(_first_line(completed.stderr) or "Windows refused the print job.")
    return completed.stdout.strip()


def _first_line(text: str) -> str:
    """The first meaningful line of a PowerShell error, which is the useful one."""
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return ""


def printers(timeout: float = 25.0) -> list[Printer]:
    """The printers, default first so it is the obvious button to press."""
    output = _run_script(["-List"], timeout)
    try:
        payload = json.loads(output or "{}")
    except ValueError as exc:
        raise PrintError("The list of printers could not be read.") from exc
    entries = payload.get("printers") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return []
    found: list[Printer] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        found.append(
            Printer(
                name=name,
                default=bool(entry.get("default")),
                duplex=bool(entry.get("duplex")),
            )
        )
    # Windows' default first; the rest keep the order Windows gave them.
    found.sort(key=lambda printer: not printer.default)
    return found


def print_job(job: PrintJob, timeout: float = 180.0) -> list[str]:
    """Send the file to the printer, returning anything the user should be told.

    A warning is not a failure. Paper size for a PDF is written to the printer
    itself, which Windows only allows an administrator to do, so the usual
    outcome there is a printed file on the printer's own paper and a sentence
    explaining it. Silently ignoring the chosen size would be worse.
    """
    if not job.is_complete:
        raise PrintError("Choose a printer, a paper size and colour first.")
    if not job.path.is_file():
        raise PrintError("That file is no longer there.")
    paper = paper_by_key(job.paper)
    colour = colour_by_key(job.colour)
    if paper is None or colour is None:
        raise PrintError("Those print settings are no longer available.")
    output = _run_script(
        [
            "-Printer",
            job.printer,
            "-File",
            str(job.path),
            "-Paper",
            paper.windows_name,
            "-Strategy",
            job.strategy,
            # A word, not "$true": arguments reach a -File script as text, and
            # PowerShell refuses to bind that text to a boolean parameter.
            "-Ink",
            "colour" if colour.color else "mono",
            "-Sides",
            job.sides,
            "-Copies",
            str(job.copies),
        ],
        timeout,
    )
    return warnings_from(output)


def warnings_from(output: str) -> list[str]:
    """The "warning:" lines the script wrote, as sentences for the chat."""
    notes: list[str] = []
    for line in (output or "").splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("warning:"):
            note = stripped[len("warning:") :].strip()
            if note:
                notes.append(note[0].upper() + note[1:])
    return notes


def describe(job: PrintJob) -> str:
    """The choices in one line, for the message that says it was printed."""
    paper = paper_by_key(job.paper)
    colour = colour_by_key(job.colour)
    parts = [
        job.printer or "printer",
        paper.label if paper else "",
        colour.summary if colour else "",
        "both sides" if job.sides == SIDES_BOTH else "one side",
        "1 copy" if job.copies == 1 else f"{job.copies} copies",
    ]
    return " · ".join(part for part in parts if part)
