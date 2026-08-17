"""Watchers: conditions about this PC that send a message when they come true.

The whole difficulty is not noticing that a battery is low - it is not saying so
every twenty-five seconds for the next hour. So a watcher is edge triggered: it
fires when the condition becomes true and then disarms, and it only re-arms when
the condition has comfortably passed (a battery back above the threshold and a
margin), or when its cooldown has run out and the situation still deserves
repeating. That margin matters on real hardware, where a battery reading sits on
19, 20, 19, 20 and a bare comparison would fire on every flicker.

Everything here is pure: it takes a reading and the state from last time, and
returns what to say and the new state. Nothing reads the machine, sends a
message, or looks at a clock, so all of it can be tested without a low battery,
a full disk, or waiting an hour.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from typing import Any


# What a watcher watches. Kept as plain strings because they are stored in JSON
# and shown in a chat.
BATTERY_LOW = "battery_low"
BATTERY_FULL = "battery_full"
DISK_LOW = "disk_low"
MEMORY_HIGH = "memory_high"
IDLE = "idle"
FOLDER_NEW = "folder_new"


@dataclass(frozen=True)
class Kind:
    key: str
    label: str
    # What the number means, for the editor and for the message.
    unit: str
    # How far the reading must move back before the watcher can fire again.
    rearm_margin: float
    default_threshold: float
    # Some kinds watch a place rather than a number.
    needs_target: bool = False


KINDS: tuple[Kind, ...] = (
    Kind(BATTERY_LOW, "Battery falls below", "%", 5, 20),
    Kind(BATTERY_FULL, "Battery charged above", "%", 5, 95),
    Kind(DISK_LOW, "Free space falls below", "GB", 5, 20, needs_target=True),
    Kind(MEMORY_HIGH, "Memory used rises above", "%", 5, 90),
    Kind(IDLE, "The PC sits idle for", "minutes", 1, 30),
    Kind(FOLDER_NEW, "A file appears in", "", 0, 0, needs_target=True),
)

MAX_NAMES_IN_MESSAGE = 5
DEFAULT_COOLDOWN_MINUTES = 60


def kind_by_key(key: str) -> Kind | None:
    return next((kind for kind in KINDS if kind.key == key), None)


@dataclass(frozen=True)
class Watcher:
    """One condition the user asked to be told about."""

    id: str
    kind: str
    threshold: float = 0.0
    # A drive for disk space, a folder for new files; unused by the rest.
    target: str = ""
    enabled: bool = True
    # How long before the same watcher may speak again while the condition
    # holds. Without it a disk that stays full would repeat for ever.
    cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES

    @property
    def label(self) -> str:
        kind = kind_by_key(self.kind)
        if kind is None:
            return "Unknown watcher"
        if self.kind == FOLDER_NEW:
            return f"{kind.label} {self.target or 'a folder'}"
        where = f" on {self.target}" if kind.needs_target and self.target else ""
        return f"{kind.label} {_number(self.threshold)}{kind.unit}{where}"


@dataclass(frozen=True)
class Reading:
    """What the machine looks like right now, as the watchers need it."""

    battery_percent: int | None = None
    on_mains: bool | None = None
    memory_used_percent: int | None = None
    idle_minutes: float = 0.0
    # Drive letter or path -> free gigabytes.
    free_gb: dict[str, float] = field(default_factory=dict)
    # Folder path -> the file names currently in it.
    folder_files: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class Firing:
    watcher_id: str
    message: str


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def _current_value(watcher: Watcher, reading: Reading) -> float | None:
    """The number this watcher compares against, or None when it cannot be read."""
    if watcher.kind in (BATTERY_LOW, BATTERY_FULL):
        return None if reading.battery_percent is None else float(reading.battery_percent)
    if watcher.kind == MEMORY_HIGH:
        return (
            None
            if reading.memory_used_percent is None
            else float(reading.memory_used_percent)
        )
    if watcher.kind == IDLE:
        return float(reading.idle_minutes)
    if watcher.kind == DISK_LOW:
        return reading.free_gb.get(watcher.target)
    return None


def _is_triggered(watcher: Watcher, value: float) -> bool:
    if watcher.kind in (DISK_LOW, BATTERY_LOW):
        return value <= watcher.threshold
    return value >= watcher.threshold


def _has_recovered(watcher: Watcher, value: float) -> bool:
    """Whether the reading has moved past the threshold by the kind's margin.

    The margin is what stops a battery hovering on the threshold from firing
    again the moment it flickers one percent the other way.
    """
    kind = kind_by_key(watcher.kind)
    margin = kind.rearm_margin if kind else 0
    if watcher.kind in (DISK_LOW, BATTERY_LOW):
        return value > watcher.threshold + margin
    return value < watcher.threshold - margin


def evaluate(
    watchers: list[Watcher],
    reading: Reading,
    state: dict[str, dict[str, Any]] | None,
    now: float,
) -> tuple[list[Firing], dict[str, dict[str, Any]]]:
    """Decide what to say, and what to remember for next time.

    ``state`` carries, per watcher: whether it is armed, when it last fired, and
    for a folder the names already seen. ``now`` is passed in rather than read so
    a cooldown can be tested without waiting an hour.
    """
    previous = state or {}
    updated: dict[str, dict[str, Any]] = {}
    firings: list[Firing] = []

    for watcher in watchers:
        memory = dict(previous.get(watcher.id) or {})
        if not watcher.enabled:
            # Left as it was: a paused watcher should not forget where it stood,
            # or every resume would fire immediately.
            updated[watcher.id] = memory
            continue

        if watcher.kind == FOLDER_NEW:
            firing, memory = _evaluate_folder(watcher, reading, memory, now)
        else:
            firing, memory = _evaluate_threshold(watcher, reading, memory, now)
        if firing is not None:
            firings.append(firing)
        updated[watcher.id] = memory

    return firings, updated


def _evaluate_threshold(
    watcher: Watcher,
    reading: Reading,
    memory: dict[str, Any],
    now: float,
) -> tuple[Firing | None, dict[str, Any]]:
    value = _current_value(watcher, reading)
    if value is None:
        # Nothing to compare: a machine with no battery, or a drive that has
        # gone. Silence is right, and the watcher keeps its state.
        return None, memory

    armed = bool(memory.get("armed", True))
    last_fired = float(memory.get("last_fired", 0.0) or 0.0)

    if not _is_triggered(watcher, value):
        if _has_recovered(watcher, value):
            memory["armed"] = True
        return None, memory

    if not armed:
        # Still true. Repeat only when the cooldown has passed, so a disk that
        # stays full says so hourly rather than every tick.
        elapsed_minutes = (now - last_fired) / 60.0
        if watcher.cooldown_minutes <= 0 or elapsed_minutes < watcher.cooldown_minutes:
            return None, memory

    if watcher.kind == BATTERY_LOW and reading.on_mains:
        # A low battery that is charging is not a problem to report.
        return None, memory
    if watcher.kind == BATTERY_FULL and reading.on_mains is False:
        # "Charged" only means something while it is plugged in.
        return None, memory

    memory["armed"] = False
    memory["last_fired"] = now
    return Firing(watcher.id, describe(watcher, value, reading)), memory


def _evaluate_folder(
    watcher: Watcher,
    reading: Reading,
    memory: dict[str, Any],
    now: float,
) -> tuple[Firing | None, dict[str, Any]]:
    names = reading.folder_files.get(watcher.target)
    if names is None:
        return None, memory
    current = set(names)
    if "seen" not in memory:
        # First sight of the folder is not news: everything in it would be
        # announced the moment the watcher is created.
        memory["seen"] = sorted(current)
        return None, memory

    seen = set(memory.get("seen") or [])
    added = sorted(current - seen)
    memory["seen"] = sorted(current)
    if not added:
        return None, memory
    memory["last_fired"] = now
    return Firing(watcher.id, describe_folder(watcher, added)), memory


def describe(watcher: Watcher, value: float, reading: Reading) -> str:
    """The sentence sent to the chat when a threshold watcher fires."""
    if watcher.kind == BATTERY_LOW:
        return f"🔋  Battery is at {_number(value)}% and not charging."
    if watcher.kind == BATTERY_FULL:
        return f"🔌  Battery has reached {_number(value)}%. You can unplug it."
    if watcher.kind == MEMORY_HIGH:
        return f"🧠  Memory is {_number(value)}% used."
    if watcher.kind == IDLE:
        return f"😴  This PC has been idle for {_number(value)} minutes."
    if watcher.kind == DISK_LOW:
        return f"💾  {watcher.target} has {_number(value)} GB free."
    return watcher.label


def describe_folder(watcher: Watcher, names: list[str]) -> str:
    shown = ", ".join(names[:MAX_NAMES_IN_MESSAGE])
    if len(names) > MAX_NAMES_IN_MESSAGE:
        shown += f", and {len(names) - MAX_NAMES_IN_MESSAGE} more"
    count = len(names)
    noun = "file" if count == 1 else "files"
    return f"📂  {count} new {noun} in {watcher.target}: {shown}"


def new_watcher(kind: str, threshold: float | None = None, target: str = "") -> Watcher:
    detail = kind_by_key(kind)
    return Watcher(
        id=uuid.uuid4().hex[:12],
        kind=kind,
        threshold=detail.default_threshold if threshold is None and detail else (threshold or 0.0),
        target=target,
    )


def to_dict(watcher: Watcher) -> dict[str, Any]:
    return {
        "id": watcher.id,
        "kind": watcher.kind,
        "threshold": watcher.threshold,
        "target": watcher.target,
        "enabled": watcher.enabled,
        "cooldown_minutes": watcher.cooldown_minutes,
    }


def from_dict(payload: Any) -> Watcher | None:
    """Read one watcher from stored JSON, or None when it is not usable.

    Anything unreadable is dropped rather than repaired: a watcher with a kind
    this build does not know would otherwise sit in the list doing nothing while
    looking as though it works.
    """
    if not isinstance(payload, dict):
        return None
    kind = str(payload.get("kind", ""))
    if kind_by_key(kind) is None:
        return None
    try:
        threshold = float(payload.get("threshold", 0) or 0)
        cooldown = int(payload.get("cooldown_minutes", DEFAULT_COOLDOWN_MINUTES))
    except (TypeError, ValueError):
        return None
    identifier = str(payload.get("id", "")).strip() or uuid.uuid4().hex[:12]
    return Watcher(
        id=identifier,
        kind=kind,
        threshold=threshold,
        target=str(payload.get("target", "")),
        enabled=bool(payload.get("enabled", True)),
        cooldown_minutes=max(0, cooldown),
    )


def toggled(watcher: Watcher, enabled: bool) -> Watcher:
    return replace(watcher, enabled=enabled)


def watched_folders(watchers: list[Watcher]) -> list[str]:
    """The folders a reading needs to scan, so nothing else is walked."""
    return sorted(
        {w.target for w in watchers if w.enabled and w.kind == FOLDER_NEW and w.target}
    )


def watched_drives(watchers: list[Watcher]) -> list[str]:
    return sorted(
        {w.target for w in watchers if w.enabled and w.kind == DISK_LOW and w.target}
    )
