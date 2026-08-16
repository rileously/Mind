"""Decide who may talk to the Telegram bridge, and what they may run.

Kept separate from the network code so the rules that matter for safety can be
tested directly, without a bot token or a live connection.

Two rules carry the weight:

Telegram bots are reachable by anyone who knows the bot's username, so an
unrecognised sender must never reach the command dispatcher. Without that,
a stranger can spend the owner's API quota and read whatever the bridge
exposes.

Shell replacers run with the signed-in user's permissions. Exposing them over
a chat bot would turn a leaked token, or a single mistake in the allowlist,
into remote code execution on the desktop, so they are refused here rather
than being left to the caller to remember.
"""

from __future__ import annotations

from dataclasses import dataclass


REMOTE_SAFE_TYPES = frozenset({"ai", "replacer-text"})
BLOCKED_TYPES = frozenset({"replacer-shell"})


class CommandRefused(RuntimeError):
    """Raised when a known command may not be run from a remote chat."""


@dataclass(frozen=True)
class Request:
    """A parsed inbound message."""

    trigger: str | None
    text: str
    is_builtin: bool = False


def parse_allowed_chat_ids(raw: object) -> frozenset[int]:
    """Read the allowlist from config, ignoring anything that is not an id.

    Accepts a list or a comma/whitespace separated string so the setting can be
    typed by hand without tripping over formatting.
    """
    if raw is None:
        return frozenset()
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return frozenset({int(raw)})
    if isinstance(raw, str):
        parts: list[str] = raw.replace(",", " ").split()
    elif isinstance(raw, (list, tuple, set, frozenset)):
        parts = [str(item) for item in raw]
    else:
        return frozenset()

    found: set[int] = set()
    for part in parts:
        candidate = str(part).strip()
        if not candidate:
            continue
        try:
            found.add(int(candidate))
        except ValueError:
            continue
    return frozenset(found)


def is_authorized(chat_id: object, allowed: frozenset[int]) -> bool:
    """Whether this chat may use the bridge at all.

    An empty allowlist authorises nobody. Treating "unconfigured" as "open to
    everyone" would silently expose the bridge the moment a token is set but the
    allowlist has not been filled in yet.
    """
    if not allowed:
        return False
    try:
        return int(chat_id) in allowed
    except (TypeError, ValueError):
        return False


def is_remote_safe(command: dict) -> bool:
    kind = str(command.get("type", "ai"))
    return kind in REMOTE_SAFE_TYPES and kind not in BLOCKED_TYPES


def parse_message(text: str, prefix: str = "?") -> Request:
    """Split an inbound message into a trigger and the text it applies to.

    Understands both the app's own "?fix some text" form and Telegram's native
    "/fix some text" slash commands, including the "/fix@BotName" that Telegram
    appends in group chats.
    """
    stripped = (text or "").strip()
    if not stripped:
        return Request(trigger=None, text="")

    marker = stripped[0]
    if marker not in (prefix, "/"):
        return Request(trigger=None, text=stripped)

    head, _, tail = stripped[1:].partition(" ")
    trigger = head.strip()
    if "@" in trigger:  # /fix@MindBot in a group chat
        trigger = trigger.split("@", 1)[0]
    if not trigger:
        return Request(trigger=None, text=stripped)
    return Request(
        trigger=trigger.lower(),
        text=tail.strip(),
        is_builtin=marker == "/",
    )


def resolve_command(trigger: str, commands: list[dict]) -> dict | None:
    for command in commands:
        if str(command.get("trigger", "")).lower() != trigger:
            continue
        if not command.get("enabled", True):
            return None
        return command
    return None


def select_command(
    request: Request,
    commands: list[dict],
    default_trigger: str = "",
) -> dict | None:
    """Pick the command to run, falling back to the configured default.

    Returns None when there is nothing to run. Raises CommandRefused when the
    command exists but must not be reachable from a chat.
    """
    trigger = request.trigger or str(default_trigger or "").strip().lower()
    if not trigger:
        return None
    command = resolve_command(trigger, commands)
    if command is None:
        return None
    if not is_remote_safe(command):
        raise CommandRefused(
            f"'{trigger}' runs a shell command, which Mind does not allow from Telegram."
        )
    return command


def remote_safe_commands(commands: list[dict]) -> list[dict]:
    """The commands worth advertising in a help message."""
    return [
        command
        for command in commands
        if command.get("enabled", True) and is_remote_safe(command)
    ]
