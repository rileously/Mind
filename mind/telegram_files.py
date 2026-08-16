"""Browsing and transferring files over the Telegram bridge.

Everything here stays inside a configured root directory. A chat bot is
reachable by anyone holding the token, so an unbounded path would turn a leaked
token into read access to the whole machine. Paths are resolved before they are
checked, which also defeats symlinks pointing out of the root and the usual
"..\\..\\" traversal.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


# Telegram refuses documents larger than 50 MB from a bot.
MAX_SEND_BYTES = 45 * 1024 * 1024
MAX_ENTRIES = 60


class PathRefused(RuntimeError):
    """Raised when a requested path is outside the permitted root."""


@dataclass(frozen=True)
class Entry:
    name: str
    is_dir: bool
    size: int


def resolve_root(configured: str | None) -> Path:
    """The directory browsing is confined to, defaulting to the user's profile."""
    candidate = (configured or "").strip()
    if candidate:
        try:
            return Path(candidate).expanduser().resolve()
        except OSError:
            pass
    return Path.home().resolve()


def resolve_within_root(root: Path, current: Path, target: str) -> Path:
    """Resolve a user-supplied path and refuse anything outside the root.

    ``target`` may be a child name, "..", an absolute path, or empty for the
    current directory. Resolution happens before the containment check, so a
    symlink or a "../.." sequence that escapes the root is rejected rather than
    followed.
    """
    root = Path(root).resolve()
    current = Path(current).resolve()
    cleaned = (target or "").strip().strip('"')

    if not cleaned:
        candidate = current
    else:
        raw = Path(cleaned).expanduser()
        candidate = raw if raw.is_absolute() else current / raw

    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise PathRefused("That path could not be read.") from exc

    if resolved != root and not resolved.is_relative_to(root):
        raise PathRefused(
            "That location is outside the folder Mind is allowed to browse."
        )
    return resolved


def list_directory(path: Path) -> list[Entry]:
    """Directories first, then files, both alphabetical."""
    entries: list[Entry] = []
    try:
        for item in os.scandir(path):
            try:
                is_dir = item.is_dir()
                size = 0 if is_dir else item.stat().st_size
            except OSError:
                continue
            entries.append(Entry(name=item.name, is_dir=is_dir, size=size))
    except (OSError, PermissionError) as exc:
        raise PathRefused(f"Could not read that folder: {exc}") from exc
    entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
    return entries


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def relative_label(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return str(path)
    return str(relative) if str(relative) != "." else "(top)"


def format_listing(root: Path, path: Path, entries: list[Entry]) -> str:
    """A numbered listing, so navigation needs only a number rather than typing names."""
    header = f"📁 {relative_label(root, path)}"
    if not entries:
        return f"{header}\n\n(empty)"

    lines = [header, ""]
    shown = entries[:MAX_ENTRIES]
    for index, entry in enumerate(shown, start=1):
        if entry.is_dir:
            lines.append(f"{index}. 📁 {entry.name}")
        else:
            lines.append(f"{index}. 📄 {entry.name}  ({human_size(entry.size)})")
    if len(entries) > len(shown):
        lines.append(f"\n… and {len(entries) - len(shown)} more")
    lines += [
        "",
        "/cd <number>  open a folder      /cd ..  go up",
        "/get <number> send me that file  /files  refresh",
    ]
    return "\n".join(lines)


def entry_at(entries: list[Entry], selector: str) -> Entry | None:
    """Pick an entry by its listed number, or by name if a name was typed."""
    choice = (selector or "").strip()
    if not choice:
        return None
    if choice.isdigit():
        index = int(choice) - 1
        if 0 <= index < len(entries):
            return entries[index]
        return None
    lowered = choice.lower()
    for entry in entries:
        if entry.name.lower() == lowered:
            return entry
    return None


def unique_destination(folder: Path, name: str) -> Path:
    """A free filename in ``folder``, never overwriting what is already there."""
    safe = Path(name).name or "file"
    candidate = folder / safe
    if not candidate.exists():
        return candidate
    stem = Path(safe).stem
    suffix = Path(safe).suffix
    counter = 2
    while True:
        candidate = folder / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1
