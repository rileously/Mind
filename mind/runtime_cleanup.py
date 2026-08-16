"""Remove one-file runtime folders left behind by earlier Mind processes.

The packaged build unpacks its Python runtime into ``%LOCALAPPDATA%\\Mind\\Runtime``
on every launch and deletes that folder on a clean exit. An ungraceful exit - a
crash, a forced End Task, or a machine losing power - skips the cleanup and
strands roughly 115 MB per launch, which accumulates into gigabytes over a few
days of use.

A stranded folder is only safe to delete when nothing is still running out of it.
Rather than inspecting loaded modules, which is slow and racy, this module tries
to rename each candidate first. Windows refuses to rename a directory holding an
open file, so a rename that succeeds proves the folder is idle and hands us
exclusive ownership of it in the same step.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from pathlib import Path

from .paths import runtime_dir


# Skip folders younger than this. A sibling Mind process - notably the engine,
# which is a second one-file launch - may still be unpacking into a folder whose
# files are not open yet, and would be broken by deleting it mid-extraction.
MIN_AGE_SECONDS = 600
CLAIM_SUFFIX = ".pruning"


def current_runtime_dir() -> Path | None:
    """The folder this process is running from, which must never be pruned."""
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return None
    try:
        return Path(meipass).resolve()
    except OSError:
        return None


def stale_runtime_dirs(
    root: Path | None = None,
    now: float | None = None,
    min_age_seconds: int = MIN_AGE_SECONDS,
) -> list[Path]:
    """Return runtime folders that look abandoned, newest excluded."""
    base = Path(root) if root is not None else runtime_dir()
    if not base.is_dir():
        return []
    active = current_runtime_dir()
    moment = time.time() if now is None else now

    candidates: list[Path] = []
    for entry in base.iterdir():
        if not entry.is_dir() or not entry.name.startswith("_MEI"):
            continue
        try:
            resolved = entry.resolve()
        except OSError:
            continue
        if active is not None and resolved == active:
            continue
        try:
            age = moment - entry.stat().st_mtime
        except OSError:
            continue
        if age < min_age_seconds:
            continue
        candidates.append(entry)
    return candidates


def _directory_size(path: Path) -> int:
    total = 0
    for current, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(current) / name).stat().st_size
            except OSError:
                continue
    return total


def prune_runtime_dirs(
    root: Path | None = None,
    min_age_seconds: int = MIN_AGE_SECONDS,
) -> tuple[int, int]:
    """Delete abandoned runtime folders. Returns (folders removed, bytes freed)."""
    removed = 0
    freed = 0
    for candidate in stale_runtime_dirs(root=root, min_age_seconds=min_age_seconds):
        claimed = candidate.with_name(candidate.name + CLAIM_SUFFIX)
        try:
            # Renaming fails while any file inside is open, so a success both
            # proves the folder is idle and stops another Mind process from
            # adopting it while the delete is in flight.
            candidate.rename(claimed)
        except OSError:
            continue
        size = _directory_size(claimed)
        try:
            shutil.rmtree(claimed)
        except OSError:
            # Put it back so a later run can retry rather than leaking a folder
            # under a name nothing recognises.
            try:
                claimed.rename(candidate)
            except OSError:
                pass
            continue
        removed += 1
        freed += size
    return removed, freed


def prune_in_background(
    root: Path | None = None,
    on_finished=None,
) -> threading.Thread | None:
    """Prune off the UI thread; deleting gigabytes must not delay startup."""
    if not getattr(sys, "frozen", False) and root is None:
        return None

    def _worker() -> None:
        try:
            removed, freed = prune_runtime_dirs(root=root)
        except Exception:
            return
        if on_finished is not None and removed:
            on_finished(removed, freed)

    thread = threading.Thread(target=_worker, name="mind-runtime-prune", daemon=True)
    thread.start()
    return thread
