"""The buttons and menus the Telegram bridge presents.

Kept apart from telegram_bridge, which polls and dispatches, and from
telegram_files, which knows about directories. What lives here is only the shape
of the interface: what is on a button, which rows it sits in, and what a tap
means. That makes it all testable without a network or a chat.

Two ideas run through it. Nothing should have to be typed from memory, so every
feature is reachable by tapping, and Telegram's own command menu is published so
the list is in front of the user rather than in the help text. And an
acknowledgement that carries no information should not become a message, so taps
are answered in place and plain confirmations are reactions.
"""

from __future__ import annotations

from dataclasses import dataclass

from .telegram_client import MAX_COPY_TEXT_CHARS, escape_html
from .telegram_routing import remote_safe_commands


# Actions a button can carry. Single letters because Telegram allows 64 bytes of
# callback_data and the file browser spends most of them on an index. These must
# not collide with the browsing actions in telegram_files.
CB_MENU = "n"
CB_MEDIA = "k"
CB_REFRESH = "r"
CB_ABORT = "a"
# A button that deliberately leads nowhere: a cancel, or a label given a button's
# shape. Defined here and re-exported by telegram_files, which uses it for the
# page indicator, so there is only ever one code for "do nothing".
CB_NOOP = "x"

# Reactions, used where a sentence would say nothing the user did not just ask
# for. Only emoji from Telegram's permitted reaction set work here.
REACTION_WORKING = "👀"
REACTION_SAVED = "✍"


@dataclass(frozen=True)
class MenuAction:
    """One button on the home menu.

    ``needs`` names the setting that has to be on for it to appear, so a menu
    never offers something the user has switched off.
    """

    key: str
    label: str
    needs: str | None = None


# Order matters: a tap carries this list's index, so inserting in the middle
# would repoint buttons in messages already sent. Append rather than insert.
MENU_ACTIONS: tuple[MenuAction, ...] = (
    MenuAction("files", "📁  Files", "telegram_files_enabled"),
    MenuAction("find", "🔎  Search", "telegram_files_enabled"),
    MenuAction("clip", "📋  Clipboard"),
    MenuAction("screen", "🖼  Screenshot", "telegram_control_enabled"),
    MenuAction("status", "📊  Status", "telegram_control_enabled"),
    MenuAction("media", "🎵  Media", "telegram_control_enabled"),
    MenuAction("lock", "🔒  Lock", "telegram_control_enabled"),
    MenuAction("commands", "✨  Commands"),
)

# Label, and the argument press_media_key already understands.
MEDIA_KEYS: tuple[tuple[str, str], ...] = (
    ("⏮", "prev"),
    ("⏯", "play"),
    ("⏭", "next"),
    ("🔇", "mute"),
    ("🔉", "voldown"),
    ("🔊", "volup"),
)


def callback(action: str, value: int | None = None) -> str:
    return action if value is None else f"{action}:{value}"


def available_menu_actions(config: dict) -> list[tuple[int, MenuAction]]:
    """The menu buttons this configuration allows, with their stable indexes."""
    allowed: list[tuple[int, MenuAction]] = []
    for index, action in enumerate(MENU_ACTIONS):
        if action.needs and not bool(config.get(action.needs, False)):
            continue
        allowed.append((index, action))
    return allowed


def menu_action_at(index: int | None) -> MenuAction | None:
    if index is None or not 0 <= index < len(MENU_ACTIONS):
        return None
    return MENU_ACTIONS[index]


def build_main_menu(config: dict) -> dict:
    """The home menu: two columns, because a phone shows those comfortably."""
    rows: list[list[dict]] = []
    for position, (index, action) in enumerate(available_menu_actions(config)):
        button = {"text": action.label, "callback_data": callback(CB_MENU, index)}
        if position % 2 == 0:
            rows.append([button])
        else:
            rows[-1].append(button)
    return {"inline_keyboard": rows}


def build_media_keyboard() -> dict:
    """Transport keys as one row, then a way back to the menu.

    Typing "/media voldown" to turn the volume down twice is the kind of thing
    buttons exist for.
    """
    return {
        "inline_keyboard": [
            [
                {"text": label, "callback_data": callback(CB_MEDIA, index)}
                for index, (label, _key) in enumerate(MEDIA_KEYS)
            ],
            [{"text": "☰  Menu", "callback_data": callback(CB_MENU, None)}],
        ]
    }


def media_key_at(index: int | None) -> str:
    if index is None or not 0 <= index < len(MEDIA_KEYS):
        return ""
    return MEDIA_KEYS[index][1]


def build_copy_keyboard(text: str) -> dict | None:
    """A button that copies text on the phone, when it is short enough to carry.

    copy_text keeps the payload in the button, so Telegram caps it. Above the cap
    there is nothing to fall back to: the text is already in the message, and
    selecting it by hand is what the button was saving.
    """
    body = text or ""
    if not body.strip() or len(body) > MAX_COPY_TEXT_CHARS:
        return None
    return {"inline_keyboard": [[{"text": "📋  Copy", "copy_text": {"text": body}}]]}


def build_power_keyboard(action: str, power_callback: str) -> dict:
    """Confirm a shutdown or restart.

    ``power_callback`` is passed in rather than defined here: the bridge owns
    that action because it owns the delay and the abort.
    """
    verb = "Shut down" if action == "shutdown" else "Restart"
    return {
        "inline_keyboard": [
            [
                {"text": f"⏻  Yes, {verb.lower()}", "callback_data": power_callback},
                {"text": "✕  Cancel", "callback_data": CB_NOOP},
            ]
        ]
    }


def build_abort_keyboard() -> dict:
    """Offered while a shutdown is counting down, so calling it off is one tap."""
    return {"inline_keyboard": [[{"text": "⏹  Stop it", "callback_data": CB_ABORT}]]}


def menu_text(config: dict, host: str = "") -> str:
    """The home screen. HTML, so the caller must not pass unescaped text in."""
    where = f" on <b>{escape_html(host)}</b>" if host else ""
    lines = [f"<b>Mind</b>{where}", "", "Tap what you need."]
    if not bool(config.get("telegram_files_enabled", False)):
        lines += ["", "<i>File access is off in Preferences.</i>"]
    if not bool(config.get("telegram_control_enabled", False)):
        lines += ["<i>PC controls are off in Preferences.</i>"]
    return "\n".join(lines)


# Descriptions Telegram shows beside each command in its own menu. Kept short:
# the menu gives them one line each.
BUILT_IN_COMMANDS: tuple[tuple[str, str, str | None], ...] = (
    ("menu", "Show the button menu", None),
    ("clip", "Send this PC's clipboard here", None),
    ("save", "Store text in the clipboard history", None),
    ("commands", "List Mind's text commands", None),
    ("files", "Browse this PC's files", "telegram_files_enabled"),
    ("find", "Search for a file by name", "telegram_files_enabled"),
    ("status", "Battery, memory, uptime, disk", "telegram_control_enabled"),
    ("screen", "Send a screenshot", "telegram_control_enabled"),
    ("media", "Play, pause, volume", "telegram_control_enabled"),
    ("lock", "Lock the session", "telegram_control_enabled"),
    ("sleep", "Put this PC to sleep", "telegram_control_enabled"),
    ("shutdown", "Shut this PC down", "telegram_power_enabled"),
    ("restart", "Restart this PC", "telegram_power_enabled"),
    ("abort", "Call off a shutdown", "telegram_power_enabled"),
)

# Telegram's own limit, and it rejects the whole list if it is exceeded.
MAX_BOT_COMMANDS = 100
MAX_DESCRIPTION_CHARS = 100


def bot_commands(config: dict, commands: list[dict] | None = None) -> list[dict[str, str]]:
    """The list published with setMyCommands.

    Built from the settings, so switching PC controls off in Preferences also
    takes /lock out of the menu on the phone rather than leaving an entry that
    answers with a refusal. The user's own text commands follow the built-in
    ones, since those are the ones being reached for most.
    """
    published: list[dict[str, str]] = []
    for name, description, needs in BUILT_IN_COMMANDS:
        if needs and not bool(config.get(needs, False)):
            continue
        published.append({"command": name, "description": description})

    for command in remote_safe_commands(commands or []):
        trigger = str(command.get("trigger", "")).strip().lower()
        # Telegram only accepts lowercase letters, digits and underscores.
        if not trigger or not trigger.replace("_", "").isalnum() or not trigger.isascii():
            continue
        if any(entry["command"] == trigger for entry in published):
            continue
        detail = str(command.get("prompt", command.get("value", ""))).replace("\n", " ").strip()
        published.append(
            {
                "command": trigger,
                "description": (detail or "A Mind command")[:MAX_DESCRIPTION_CHARS],
            }
        )
        if len(published) >= MAX_BOT_COMMANDS:
            break
    return published


def commands_signature(config: dict, commands: list[dict] | None = None) -> str:
    """A cheap value that changes when the published list would.

    The bridge compares this on each poll so setMyCommands is called when the
    settings change and not once every twenty-five seconds.
    """
    return "|".join(f"{entry['command']}:{entry['description']}" for entry in bot_commands(config, commands))
