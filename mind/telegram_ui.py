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
    MenuAction("apps", "🧩  Apps", "telegram_control_enabled"),
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


# The printing flow. One letter per step, because the payload also carries which
# option was picked: "p:r2" is the third printer, "p:s0" the first paper size.
CB_PRINT = "p"
PRINT_START = "g"
PRINT_PRINTER = "r"
PRINT_PAPER = "s"
PRINT_COLOUR = "t"
PRINT_SIDES = "d"
PRINT_COPIES = "n"
PRINT_GO = "o"
PRINT_CANCEL = "c"


def print_callback(step: str, value: int | None = None) -> str:
    return f"{CB_PRINT}:{step}" if value is None else f"{CB_PRINT}:{step}{value}"


def parse_print_callback(data: str) -> tuple[str, int | None]:
    """Split "p:r2" into its step and the option picked, if any."""
    _, _, rest = (data or "").partition(":")
    if not rest:
        return "", None
    step, digits = rest[0], rest[1:]
    if not digits:
        return step, None
    try:
        return step, int(digits)
    except ValueError:
        return step, None


def build_print_offer(label: str) -> dict:
    """Offered beside a file that has just arrived, when printing is switched on."""
    return {
        "inline_keyboard": [
            [{"text": f"🖨  Print {label}", "callback_data": print_callback(PRINT_START)}]
        ]
    }


def build_printer_keyboard(available) -> dict:
    """One printer per row: names are long, and this is the widest choice.

    The Windows default is marked and sorted first, so the top button is almost
    always the right one - which is what makes a few questions bearable on a
    phone. Takes the printer records rather than names, so the star follows what
    Windows actually reports rather than a position.
    """
    rows = [
        [
            {
                "text": ("★  " if printer.default else "") + printer.name[:32],
                "callback_data": print_callback(PRINT_PRINTER, index),
            }
        ]
        for index, printer in enumerate(available[:8])
    ]
    rows.append([{"text": "✕  Cancel", "callback_data": print_callback(PRINT_CANCEL)}])
    return {"inline_keyboard": rows}


def build_paper_keyboard(papers) -> dict:
    """Paper sizes, three to a row: the labels are short enough to share one."""
    rows: list[list[dict]] = []
    for index, paper in enumerate(papers):
        button = {
            "text": paper.label,
            "callback_data": print_callback(PRINT_PAPER, index),
        }
        if index % 3 == 0:
            rows.append([button])
        else:
            rows[-1].append(button)
    rows.append([{"text": "✕  Cancel", "callback_data": print_callback(PRINT_CANCEL)}])
    return {"inline_keyboard": rows}


def build_colour_keyboard(modes) -> dict:
    """Colour or black and white, one per row so each label reads in full."""
    rows = [
        [{"text": mode.label, "callback_data": print_callback(PRINT_COLOUR, index)}]
        for index, mode in enumerate(modes)
    ]
    rows.append([{"text": "✕  Cancel", "callback_data": print_callback(PRINT_CANCEL)}])
    return {"inline_keyboard": rows}


def build_print_summary(job, max_copies: int) -> dict:
    """The last panel: everything chosen, with the rest adjustable.

    Sides and copies live here rather than as two more questions. Most prints are
    one copy on one side, so asking would add taps to every job to serve a few;
    on the panel they cost nothing until wanted, and the whole job is visible in
    one place before any paper is spent.

    Both sides only appears where the printer says it can, and the current choice
    carries a dot so the panel shows a state rather than two identical buttons.
    """
    rows: list[list[dict]] = []
    if job.duplex_capable:
        rows.append(
            [
                {
                    "text": ("● " if job.sides == "one" else "") + "📄  One side",
                    "callback_data": f"{CB_PRINT}:{PRINT_SIDES}0",
                },
                {
                    "text": ("● " if job.sides == "both" else "") + "🔁  Both sides",
                    "callback_data": f"{CB_PRINT}:{PRINT_SIDES}1",
                },
            ]
        )
    # The count is a label between the two controls, so the number being changed
    # is where the eye already is.
    copies_row = [
        {"text": "−", "callback_data": print_callback(PRINT_COPIES, max(1, job.copies - 1))},
        {
            "text": f"{job.copies} copy" if job.copies == 1 else f"{job.copies} copies",
            "callback_data": print_callback(PRINT_COPIES, job.copies),
        },
        {
            "text": "+",
            "callback_data": print_callback(PRINT_COPIES, min(max_copies, job.copies + 1)),
        },
    ]
    rows.append(copies_row)
    rows.append(
        [
            {"text": "🖨  Print", "callback_data": print_callback(PRINT_GO)},
            {"text": "✕  Cancel", "callback_data": print_callback(PRINT_CANCEL)},
        ]
    )
    return {"inline_keyboard": rows}


# Watchers. "v:3" pauses or resumes the fourth one in the list shown, and "z:1"
# sends the second file named in an alert.
CB_WATCH = "v"
CB_WATCH_FILE = "z"
# "q" closes the app an alert is about.
CB_APP_CLOSE = "q"
# "j:2" closes the third app in the list /apps last showed.
CB_APP_KILL = "j"


def build_apps_keyboard(apps) -> dict:
    """One button per running app, plus a way to look again.

    The name is on the button and closing is one tap: this is the list you reach
    for when something is stuck and you are not at the machine.
    """
    rows = [
        [
            {
                "text": f"✕  {name}"[:38],
                "callback_data": f"{CB_APP_KILL}:{index}",
            }
        ]
        for index, (name, _title) in enumerate(apps[:12])
    ]
    rows.append(
        [
            {"text": "⟳  Refresh", "callback_data": f"{CB_APP_KILL}:-1"},
            {"text": "☰  Menu", "callback_data": callback(CB_MENU, None)},
        ]
    )
    return {"inline_keyboard": rows}


def apps_text(apps, enabled: bool) -> str:
    """What the /apps panel says above its buttons."""
    if not enabled:
        return (
            "🧩  PC controls are switched off.\n\n"
            "Turn on 'Telegram PC controls' in Mind's Preferences to see and close "
            "what is running."
        )
    if not apps:
        return "🧩  Nothing with a window is open."
    lines = ["🧩  Open on this PC", ""]
    for name, title in apps[:12]:
        lines.append(f"• {name} — {title[:40]}" if title else f"• {name}")
    lines += ["", "Tap one to close it."]
    return "\n".join(lines)


def build_app_alert_keyboard(app: str) -> dict:
    """Offer to close the app the alert just announced.

    One tap, with the name on the button: being told the game is running is
    only half of what someone away from the machine wants.
    """
    return {
        "inline_keyboard": [
            [{"text": f"✕  Close {app}"[:40], "callback_data": CB_APP_CLOSE}]
        ]
    }


def build_watcher_files_keyboard(names) -> dict:
    """Offer the files an alert just named, so seeing one is a tap.

    Being told a file arrived and then having to go and find it is most of the
    work left undone. The name is on the button rather than a bare "Send",
    because an alert can carry several.
    """
    single = len(names) == 1
    return {
        "inline_keyboard": [
            [
                {
                    "text": ("👁  View" if single else f"📥  {name}")[:40],
                    "callback_data": f"{CB_WATCH_FILE}:{index}",
                }
            ]
            for index, name in enumerate(names[:5])
        ]
    }


def build_watcher_keyboard(watchers) -> dict:
    """One row per watcher, saying what tapping it will do.

    The label carries the action rather than the state, because a button that
    reads "On" leaves you guessing whether tapping it turns it off or confirms
    it is on.
    """
    rows = [
        [
            {
                "text": ("⏸  Pause  " if watcher.enabled else "▶  Resume  ") + watcher.label[:34],
                "callback_data": f"{CB_WATCH}:{index}",
            }
        ]
        for index, watcher in enumerate(watchers[:10])
    ]
    rows.append([{"text": "☰  Menu", "callback_data": callback(CB_MENU, None)}])
    return {"inline_keyboard": rows}


def watcher_list_text(watchers, enabled: bool) -> str:
    """What the /watch panel says above its buttons."""
    if not enabled:
        return (
            "👁  PC watchers are switched off.\n\n"
            "Turn on 'PC watchers' in Mind's Preferences to be told when the "
            "battery runs low, a disk fills up, or a file lands in a folder."
        )
    if not watchers:
        return (
            "👁  No watchers yet.\n\n"
            "Add one on Mind's Notifications page - low battery, low disk space, "
            "high memory, an idle PC, or a new file in a folder."
        )
    lines = ["👁  Watching this PC", ""]
    for watcher in watchers[:10]:
        lines.append(("• " if watcher.enabled else "◦ ") + watcher.label)
    paused = sum(1 for watcher in watchers if not watcher.enabled)
    if paused:
        lines += ["", f"{paused} paused."]
    return "\n".join(lines)


def build_menu_keyboard() -> dict:
    """A single way back, for a message that replaced the menu it came from."""
    return {"inline_keyboard": [[{"text": "☰  Menu", "callback_data": callback(CB_MENU, None)}]]}


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
    ("apps", "See and close what is running", "telegram_control_enabled"),
    ("watch", "Alerts about this PC", "watchers_enabled"),
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
