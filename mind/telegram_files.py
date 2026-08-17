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


# Folders that hold generated or vendored content rather than anything a person
# filed. Searching a home folder without skipping these buries real documents
# under build intermediates, and walking them costs most of the time budget.
NOISY_DIRS = frozenset(
    {
        "node_modules",
        "__pycache__",
        "site-packages",
        "venv",
        "env",
        "vendor",
        "build",
        "dist",
        "out",
        "target",
        "obj",
        "bin",
        "intermediates",
        "generated",
        "deriveddata",
        "pods",
        "gradle",
        "appdata",
        "cmake-build-debug",
        "cmake-build-release",
    }
)


@dataclass(frozen=True)
class Hit:
    """A search result, carrying enough to show and fetch it."""

    path: Path
    relative: str
    size: int
    is_dir: bool


def search_files(
    root: Path,
    query: str,
    include_hidden: bool = False,
    limit: int = 40,
    time_budget: float = 8.0,
    max_depth: int = 12,
    skip_noisy: bool = True,
) -> tuple[list[Hit], bool]:
    """Find entries under ``root`` whose name contains ``query``.

    Bounded on three axes, because this runs on the thread that also polls
    Telegram: a result cap, a wall-clock budget, and a depth limit. Returns the
    hits and whether the walk stopped early, so the caller can say so rather
    than implying the list is complete.

    Hidden directories are pruned rather than merely filtered, which keeps the
    walk out of .git and node_modules and, more importantly, means a credential
    folder is never even descended into.
    """
    import time

    needle = (query or "").strip().lower()
    if not needle:
        return [], False

    root = Path(root).resolve()
    deadline = time.monotonic() + time_budget
    hits: list[Hit] = []
    truncated = False

    for current, dirnames, filenames in os.walk(root, topdown=True):
        current_path = Path(current)
        depth = len(current_path.relative_to(root).parts) if current_path != root else 0
        if depth >= max_depth:
            dirnames[:] = []
        if not include_hidden:
            dirnames[:] = [
                name for name in dirnames if not is_hidden(current_path / name, name)
            ]
        if skip_noisy:
            dirnames[:] = [name for name in dirnames if name.lower() not in NOISY_DIRS]

        for name in list(dirnames) + filenames:
            if time.monotonic() > deadline:
                return hits, True
            if needle not in name.lower():
                continue
            candidate = current_path / name
            if not include_hidden and is_hidden(candidate, name):
                continue
            try:
                is_dir = candidate.is_dir()
                size = 0 if is_dir else candidate.stat().st_size
            except OSError:
                continue
            hits.append(
                Hit(
                    path=candidate,
                    relative=str(candidate.relative_to(root)),
                    size=size,
                    is_dir=is_dir,
                )
            )
            if len(hits) >= limit:
                return hits, True

        if time.monotonic() > deadline:
            truncated = True
            break

    return rank_hits(hits, needle), truncated


def rank_hits(hits: list[Hit], needle: str) -> list[Hit]:
    """Most likely match first.

    A walk returns whatever it reached earliest, which is an arbitrary order.
    Prefer a name that starts with the query over one that merely contains it,
    then shallower paths, which are far more often the thing being looked for
    than something buried deep in a project tree.
    """

    def key(hit: Hit) -> tuple:
        name = hit.path.name.lower()
        stem = hit.path.stem.lower()
        exact = 0 if stem == needle else 1
        prefix = 0 if name.startswith(needle) else 1
        depth = hit.relative.count(os.sep)
        return (exact, prefix, depth, len(name), name)

    return sorted(hits, key=key)


def build_search_keyboard(hits: list[Hit]) -> dict:
    """Results are buttons indexed into the search, not the folder listing."""
    rows: list[list[dict]] = []
    for index, hit in enumerate(hits[:BUTTON_PAGE_SIZE * 2]):
        icon = "📁" if hit.is_dir else "📄"
        detail = "" if hit.is_dir else f"   {human_size(hit.size)}"
        rows.append(
            [
                {
                    "text": f"{icon}  {_truncate(hit.relative)}{detail}",
                    "callback_data": callback_data(CB_FIND_OPEN, index),
                }
            ]
        )
    rows.append([{"text": "🏠 Home", "callback_data": CB_HOME}])
    return {"inline_keyboard": rows}


def format_search_header(query: str, hits: list[Hit], truncated: bool) -> str:
    if not hits:
        return (
            f'Nothing matching "{query}".\n\n'
            "Search looks at names inside the folder Mind is allowed to browse, "
            "and skips hidden system folders."
        )
    lines = [f'🔎  "{query}" — {len(hits)} match{"es" if len(hits) != 1 else ""}']
    if truncated:
        lines.append("Stopped early, so there may be more. Try a narrower search.")
    lines.append("\nTap a file to download it, or a folder to open it.")
    return "\n".join(lines)


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


# Telegram caps callback_data at 64 bytes, so buttons carry a short action and
# an index into the listing the chat is currently looking at, never a path.
CB_OPEN = "o"
CB_GET = "g"
CB_UP = "u"
CB_HOME = "h"
CB_PAGE = "p"
CB_NOOP = "x"
CB_FIND_OPEN = "f"

# Rows of buttons, kept short so the message stays readable on a phone.
BUTTON_PAGE_SIZE = 10
BUTTON_LABEL_CHARS = 30


def callback_data(action: str, value: int | None = None) -> str:
    return action if value is None else f"{action}:{value}"


def parse_callback(data: str) -> tuple[str, int | None]:
    action, _, raw = (data or "").partition(":")
    if not raw:
        return action, None
    try:
        return action, int(raw)
    except ValueError:
        return action, None


def _truncate(label: str) -> str:
    if len(label) <= BUTTON_LABEL_CHARS:
        return label
    return label[: BUTTON_LABEL_CHARS - 1] + "…"


def build_keyboard(
    entries: list[Entry],
    page: int,
    at_root: bool,
    places: list[Entry] | None = None,
) -> dict:
    """One button per entry, plus a navigation row.

    Indexes are absolute within the folder rather than within the page, so a
    button keeps pointing at the same entry no matter how the user got there.
    """
    total_pages = page_count_for_buttons(len(entries))
    page = max(1, min(page, total_pages))
    start = (page - 1) * BUTTON_PAGE_SIZE
    window = entries[start : start + BUTTON_PAGE_SIZE]

    rows: list[list[dict]] = []
    if at_root and places:
        shortcut: list[dict] = []
        for place in places[:3]:
            index = next(
                (i for i, e in enumerate(entries) if e.name == place.name and e.is_dir),
                None,
            )
            if index is not None:
                shortcut.append(
                    {"text": place.name, "callback_data": callback_data(CB_OPEN, index)}
                )
        if shortcut:
            rows.append(shortcut)

    for offset, entry in enumerate(window):
        index = start + offset
        if entry.is_dir:
            rows.append(
                [
                    {
                        "text": f"📁  {_truncate(entry.name)}",
                        "callback_data": callback_data(CB_OPEN, index),
                    }
                ]
            )
        else:
            rows.append(
                [
                    {
                        "text": f"📄  {_truncate(entry.name)}   {human_size(entry.size)}",
                        "callback_data": callback_data(CB_GET, index),
                    }
                ]
            )

    navigation: list[dict] = []
    if page > 1:
        navigation.append(
            {"text": "◀ Back", "callback_data": callback_data(CB_PAGE, page - 1)}
        )
    if page < total_pages:
        navigation.append(
            {"text": "Next ▶", "callback_data": callback_data(CB_PAGE, page + 1)}
        )
    if navigation:
        rows.append(navigation)

    place_row: list[dict] = []
    if not at_root:
        place_row.append({"text": "⬆ Up", "callback_data": CB_UP})
        place_row.append({"text": "🏠 Home", "callback_data": CB_HOME})
    if place_row:
        rows.append(place_row)

    return {"inline_keyboard": rows}


def page_count_for_buttons(total: int) -> int:
    return max(1, (total + BUTTON_PAGE_SIZE - 1) // BUTTON_PAGE_SIZE)


def format_header(root: Path, path: Path, entries: list[Entry], page: int) -> str:
    """The text beside the buttons: where you are and how much is here."""
    lines = [f"📂  {breadcrumb(root, path)}"]
    if not entries:
        lines.append("This folder is empty.")
        return "\n".join(lines)
    folders = sum(1 for e in entries if e.is_dir)
    files = len(entries) - folders
    summary = []
    if folders:
        summary.append(f"{folders} folder{'s' if folders != 1 else ''}")
    if files:
        summary.append(f"{files} file{'s' if files != 1 else ''}")
    total_pages = page_count_for_buttons(len(entries))
    line = " · ".join(summary)
    if total_pages > 1:
        line += f"   —   page {min(page, total_pages)} of {total_pages}"
    lines.append(line)
    lines.append("\nTap a folder to open it, or a file to download it.")
    return "\n".join(lines)


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
