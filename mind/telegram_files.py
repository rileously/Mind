"""Browsing and transferring files over the Telegram bridge.

Everything here stays inside a configured root directory. A chat bot is
reachable by anyone holding the token, so an unbounded path would turn a leaked
token into read access to the whole machine. Paths are resolved before they are
checked, which also defeats symlinks pointing out of the root and the usual
"..\\..\\" traversal.
"""

from __future__ import annotations

import os
import stat as stat_module
from dataclasses import dataclass
from pathlib import Path


# Telegram refuses documents larger than 50 MB from a bot.
MAX_SEND_BYTES = 45 * 1024 * 1024
PAGE_SIZE = 25

# The folders people actually want from a phone, offered first so the top level
# is not a wall of application data.
QUICK_PLACES = ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music")


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


def is_hidden(entry_path: Path, name: str | None = None) -> bool:
    """Whether an entry is application data rather than something a person filed.

    Covers both conventions: a leading dot, which is how tools like .ssh, .aws
    and .gnupg name themselves, and the Windows hidden and system attributes.
    These are excluded by default, which keeps credential folders out of a
    listing that travels over a chat bot as much as it reduces the clutter.
    """
    label = name if name is not None else entry_path.name
    if label.startswith("."):
        return True
    try:
        attributes = entry_path.stat().st_file_attributes  # Windows only
    except (OSError, AttributeError):
        return False
    hidden = getattr(stat_module, "FILE_ATTRIBUTE_HIDDEN", 0x2)
    system = getattr(stat_module, "FILE_ATTRIBUTE_SYSTEM", 0x4)
    return bool(attributes & (hidden | system))


def list_directory(path: Path, include_hidden: bool = False) -> list[Entry]:
    """Directories first, then files, both alphabetical."""
    entries: list[Entry] = []
    try:
        for item in os.scandir(path):
            try:
                is_dir = item.is_dir()
                if not include_hidden and is_hidden(Path(item.path), item.name):
                    continue
                size = 0 if is_dir else item.stat().st_size
            except OSError:
                continue
            entries.append(Entry(name=item.name, is_dir=is_dir, size=size))
    except (OSError, PermissionError) as exc:
        raise PathRefused(f"Could not read that folder: {exc}") from exc
    entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
    return entries


def quick_places(root: Path) -> list[Entry]:
    """The common folders that actually exist directly under the root."""
    found: list[Entry] = []
    for name in QUICK_PLACES:
        candidate = root / name
        if candidate.is_dir():
            found.append(Entry(name=name, is_dir=True, size=0))
    return found


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


def breadcrumb(root: Path, path: Path) -> str:
    label = relative_label(root, path)
    if label == "(top)":
        return "Home"
    return "Home / " + label.replace(os.sep, " / ")


def page_count(total: int) -> int:
    return max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)


def format_listing(
    root: Path,
    path: Path,
    entries: list[Entry],
    page: int = 1,
    places: list[Entry] | None = None,
) -> str:
    """A numbered listing. Numbering runs over the whole folder, not the page, so
    a number keeps meaning the same entry after paging."""
    lines = [f"📂  {breadcrumb(root, path)}"]

    if places:
        lines += ["", "Jump to:", "   " + "   ".join(f"/cd {p.name}" for p in places)]

    if not entries:
        lines += ["", "This folder is empty."]
        if path != root:
            lines += ["", "/cd ..  go up"]
        return "\n".join(lines)

    folders = [e for e in entries if e.is_dir]
    files = [e for e in entries if not e.is_dir]
    total_pages = page_count(len(entries))
    page = max(1, min(page, total_pages))
    start = (page - 1) * PAGE_SIZE
    window = entries[start : start + PAGE_SIZE]

    summary = []
    if folders:
        summary.append(f"{len(folders)} folder{'s' if len(folders) != 1 else ''}")
    if files:
        summary.append(f"{len(files)} file{'s' if len(files) != 1 else ''}")
    lines.append("     " + " · ".join(summary))

    width = len(str(start + len(window)))
    section = None
    for offset, entry in enumerate(window):
        number = str(start + offset + 1).rjust(width)
        label = "FOLDERS" if entry.is_dir else "FILES"
        if label != section:
            lines += ["", label]
            section = label
        if entry.is_dir:
            lines.append(f"  {number}.  📁  {entry.name}")
        else:
            lines.append(f"  {number}.  📄  {entry.name}   {human_size(entry.size)}")

    lines.append("")
    if total_pages > 1:
        lines.append(f"Page {page} of {total_pages} — /files {page + 1} for the next")
    footer = ["/cd <n> open", "/get <n> download"]
    if path != root:
        footer.append("/cd .. up")
    lines.append("  ·  ".join(footer))
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
