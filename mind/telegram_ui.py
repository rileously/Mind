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
    MenuAction("devices", "📶  Devices", "network_scan_enabled"),
    # Everything below was reachable only by typing it, while /watch, /sleep,
    # /shutdown and /restart were already published in Telegram's own command
    # list - so the phone offered them in one place and not in the other.
    MenuAction("watch", "🔔  Alerts", "watchers_enabled"),
    MenuAction("print", "🖨  Print", "telegram_print_enabled"),
    MenuAction("power", "⏻  Power", "telegram_power_enabled"),
    MenuAction("help", "❓  Help"),
    MenuAction("hotspot", "📡  Hotspot", "telegram_hotspot_enabled"),
    MenuAction("ferry", "🚤  Ferry"),
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


def power_prompt(seconds: int, can_sleep: bool = True) -> str:
    """What the power panel says, describing only the buttons it is showing.

    Sleep follows the PC-controls switch, so with that off a sentence about it
    would be describing a button that is not there.
    """
    lines = [
        "⏻  What should this PC do?",
        "",
        f"Shutting down and restarting wait {seconds} seconds first, with a "
        "button to call it off.",
    ]
    if can_sleep:
        lines.append("Sleep happens at once.")
    return "\n".join(lines)


def build_power_menu_keyboard(power_callback, can_sleep: bool = True) -> dict:
    """The three ways to put a PC down, from one tap on the menu.

    ``power_callback`` builds the bridge's own callback data, because the delay
    and the abort belong to the bridge rather than to the shape of the buttons.
    Sleep follows the PC-controls switch and the other two follow the shutdown
    one, so a menu never offers what Preferences has turned off.
    """
    rows: list[list[dict]] = [
        [
            {"text": "⏻  Shut down", "callback_data": power_callback(1)},
            {"text": "↻  Restart", "callback_data": power_callback(2)},
        ]
    ]
    if can_sleep:
        rows.append([{"text": "😴  Sleep", "callback_data": power_callback(3)}])
    rows.append([{"text": "☰  Menu", "callback_data": callback(CB_MENU, None)}])
    return {"inline_keyboard": rows}


PRINT_PROMPT = (
    "🖨  Send me a file and Mind will offer to print it.\n\n"
    "PDFs, images, text and Office documents. After sending one, buttons ask "
    "which printer, what paper, and colour or black and white."
)


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
# Devices on the network. "y" refreshes the list.
CB_DEVICES = "y"
# Blocking carries the address rather than a position in the list. A row index
# would mean the wrong phone gets blocked whenever the list reorders between
# the panel being drawn and the button being tapped, which it does every scan.
# A ringing phone: two answers and no third. These carry nothing, because
# there is only ever one call to act on and naming it would let a tap from an
# old message reach a call that has since been replaced by another.
CB_CALL_ANSWER = "e"
CB_CALL_REJECT = "i"
CB_CALL_MUTE = "t"
CB_DEVICE_ASK = "b"
CB_DEVICE_BLOCK = "c"
# The hotspot panel. "w:1" turns it on, "w:2" off, "w:3" gives it the name of
# the Wi-Fi this PC is on, and "w:0" draws the panel again.
CB_HOTSPOT = "w"
HOTSPOT_REFRESH = 0
HOTSPOT_START = 1
HOTSPOT_STOP = 2
HOTSPOT_MATCH = 3


# Enough that the phone in question is almost certainly on the panel, few
# enough that the keyboard does not fill the screen.
BLOCKABLE_DEVICES = 8


def mac_field(mac: str) -> str:
    """An address as callback data: hex only, because 64 bytes is the budget."""
    return "".join(character for character in (mac or "").lower() if character in "0123456789abcdef")[:12]


def mac_from_field(field: str) -> str:
    """The address back in the form the rest of Mind writes it."""
    cleaned = mac_field(field)
    if len(cleaned) != 12:
        return ""
    return "-".join(cleaned[index : index + 2] for index in range(0, 12, 2))


def build_devices_keyboard(devices=(), blocked=(), can_block: bool = False) -> dict:
    """The devices panel: what is here, and a way to put one of them off.

    Blocking is offered only when the router is set up, because it is the only
    thing that can do it - and a button that always fails is worse than no
    button. Each row says which way the tap goes, so nothing has to be
    remembered between looking and tapping.
    """
    rows: list[list[dict]] = []
    if can_block:
        held = {mac for mac in blocked}
        for device in list(devices)[:BLOCKABLE_DEVICES]:
            is_blocked = device.mac in held
            mark = "✅" if is_blocked else "🚫"
            label = f"{mark}  {device.display_name}"
            rows.append(
                [
                    {
                        "text": label[:34],
                        "callback_data": f"{CB_DEVICE_ASK}:{mac_field(device.mac)}",
                    }
                ]
            )
    rows.append(
        [
            {"text": "⟳  Refresh", "callback_data": CB_DEVICES},
            {"text": "☰  Menu", "callback_data": callback(CB_MENU, None)},
        ]
    )
    return {"inline_keyboard": rows}


def build_block_confirm_keyboard(mac: str, blocking: bool) -> dict:
    """Ask before a tap takes somebody off the Wi-Fi.

    The apps panel closes a program on one tap, because a program can be opened
    again from the same chair. This reaches a phone in someone else's hand.
    """
    return {
        "inline_keyboard": [
            [
                {
                    "text": "🚫  Block it" if blocking else "✅  Let it back on",
                    "callback_data": f"{CB_DEVICE_BLOCK}:{mac_field(mac)}",
                },
                {"text": "✕  Cancel", "callback_data": CB_DEVICES},
            ]
        ]
    }


def block_confirm_text(name: str, blocking: bool) -> str:
    if blocking:
        return (
            f"🚫  Block {name} from the Wi-Fi?\n\n"
            "The router will refuse it on every network it broadcasts until it is "
            "let back on."
        )
    return f"✅  Let {name} back onto the Wi-Fi?"


def devices_text(devices, now: float, enabled: bool, blocked=()) -> str:
    """The /devices panel: who is on the network, and who was.

    Online first, because the question is almost always about right now. A
    blocked device is marked wherever it appears: it may well still be online
    and still trying, and a list that only said "online" would read as though
    the block had not worked.
    """
    if not enabled:
        return (
            "📶  Wi-Fi device scanning is off.\n\n"
            "Turn it on from Mind's Wi-Fi devices page to see what is on this "
            "network."
        )
    if not devices:
        return "📶  Nothing found on the network yet."
    held = {mac for mac in blocked}
    online = [device for device in devices if device.online]
    offline = [device for device in devices if not device.online]
    lines = [f"📶  {len(online)} online of {len(devices)} known", ""]
    for device in online[:14]:
        mark = " — 🚫 blocked" if device.mac in held else ""
        lines.append(f"• {device.display_name} — {device.ip or device.mac}{mark}")
    if offline:
        lines += ["", "Seen before:"]
        for device in offline[:6]:
            mark = " 🚫" if device.mac in held else ""
            lines.append(f"◦ {device.display_name} — {device.seen_label(now)}{mark}")
    return "\n".join(lines)


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


def build_device_alert_keyboard(mac: str, name: str) -> dict:
    """Offer to block the device the alert just announced.

    Being told that something joined the network is half of what somebody away
    from the house wants; the other half is being able to do something about it
    from the same message. The tap asks before it acts, as it does everywhere
    else, so this button is a way in rather than a trigger.
    """
    return {
        "inline_keyboard": [
            [
                {
                    "text": f"🚫  Block {name}"[:40],
                    "callback_data": f"{CB_DEVICE_ASK}:{mac_field(mac)}",
                }
            ]
        ]
    }


def build_call_keyboard(phone_id: str = "") -> dict:
    """Answer or refuse the call that is ringing.

    One tap each, and no confirmation: a ringing phone is a few seconds long,
    and a question in the middle of it is the same as a missed call.
    """
    return {
        "inline_keyboard": [
            [
                {
                    "text": "📞  Answer",
                    "callback_data": f"{CB_CALL_ANSWER}:{phone_id}" if phone_id else CB_CALL_ANSWER,
                },
                {
                    "text": "✕  Reject",
                    "callback_data": f"{CB_CALL_REJECT}:{phone_id}" if phone_id else CB_CALL_REJECT,
                },
            ]
        ]
    }


def build_in_call_keyboard(muted: bool = False, phone_id: str = "") -> dict:
    """What is left to do once the call is connected.

    Mute is the button somebody reaches for in the middle of a call rather
    than at the start of one, so it only appears once there is a call.
    """
    return {
        "inline_keyboard": [
            [
                {
                    "text": "🔊  Unmute" if muted else "🔇  Mute",
                    "callback_data": f"{CB_CALL_MUTE}:{phone_id}" if phone_id else CB_CALL_MUTE,
                },
                {
                    "text": "✕  Hang up",
                    "callback_data": f"{CB_CALL_REJECT}:{phone_id}" if phone_id else CB_CALL_REJECT,
                },
            ]
        ]
    }


def call_alert_text(who: str, model: str = "") -> str:
    """Who is calling, and on which phone.

    ``who`` is a name where the phone knows one and a number where it does not,
    because "Dhipoz is calling" is the thing somebody wants to read and
    "9322011 is calling" is a puzzle to solve first.
    """
    where = f"\n{model}" if model else ""
    return f"☎  The phone is ringing — {who or 'an unknown number'}{where}"


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
    ("devices", "Who is on this Wi-Fi", "network_scan_enabled"),
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
    ("hotspot", "Share this PC's Wi-Fi", "telegram_hotspot_enabled"),
    ("ferry", "Which RTL boats go from one island to another", None),
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


def build_hotspot_keyboard(
    state: str, matched: bool = True, clients: int = 0, match_home: bool = True
) -> dict:
    """On or off, and a way to make the hotspot look like the home network.

    The off button says how many devices are on it, because the phone reading
    this panel is quite likely one of them and the tap would be cutting its own
    connection. That is survivable - a hotspot named after the home network is
    one the phone simply falls back off - so this warns rather than refuses.
    """
    rows: list[list[dict]] = []
    if state == "on":
        label = "⏹  Turn off"
        if clients:
            label = f"⏹  Turn off ({clients} connected)"
        rows.append([{"text": label, "callback_data": f"{CB_HOTSPOT}:{HOTSPOT_STOP}"}])
    elif state == "intransition":
        rows.append([{"text": "…  Working", "callback_data": CB_NOOP}])
    else:
        rows.append([{"text": "📡  Turn on", "callback_data": f"{CB_HOTSPOT}:{HOTSPOT_START}"}])
    if not matched:
        label = "🏠  Use the home Wi-Fi name" if match_home else "✏️  Use the name I set"
        rows.append([{"text": label, "callback_data": f"{CB_HOTSPOT}:{HOTSPOT_MATCH}"}])
    rows.append(
        [
            {"text": "⟳  Refresh", "callback_data": f"{CB_HOTSPOT}:{HOTSPOT_REFRESH}"},
            {"text": "‹  Menu", "callback_data": CB_MENU},
        ]
    )
    return {"inline_keyboard": rows}


def hotspot_text(
    state: str,
    clients: int,
    ssid: str,
    wanted: str = "",
    idle_minutes: int = 0,
    enabled: bool = True,
    match_home: bool = True,
    band: str = "",
    fault: str = "",
) -> str:
    """What the hotspot panel says.

    It reports the name rather than the password. A hotspot that carries the
    home network's name also carries its password, which is already on the
    phone reading this, and a chat message is not where the other one belongs.
    """
    if not enabled:
        return (
            "The hotspot is switched off. Turn on 'Share this PC's Wi-Fi from "
            "Telegram' in Mind's Preferences to use it."
        )
    name = ssid or "the hotspot"
    if state == "on":
        lines = [f"📡 <b>{name}</b> is on."]
        if clients:
            lines.append(f"{clients} device{'s' if clients != 1 else ''} connected.")
        else:
            lines.append("Nothing has connected yet.")
        if band:
            lines.append(f"Band: {band}.")
        if idle_minutes:
            lines.append(
                f"It turns itself off after {idle_minutes} minutes with nothing connected."
            )
        if wanted and ssid == wanted and match_home:
            lines += [
                "",
                "It carries the same name and password as the home Wi-Fi, so a "
                "phone moves onto it by itself once this one is the stronger of "
                "the two.",
            ]
        elif not match_home:
            lines += [
                "",
                "It is a network of its own, so a phone joins it the first time "
                "from its Wi-Fi list and remembers it after that.",
            ]
        if fault:
            # Last, because it is the part worth leaving on screen. The radio
            # being up is not the same as the hotspot working, and a device
            # that cannot join says only that its IP configuration failed -
            # which points at the phone rather than at this.
            lines += ["", f"⚠️ {fault}"]
        return "\n".join(lines)
    if state == "intransition":
        return f"📡 {name} is still coming up. Refresh in a moment."
    lines = [f"📡 <b>{name}</b> is off."]
    if wanted and ssid and ssid != wanted:
        because = (
            "so a phone will not move onto it without being told to"
            if match_home
            else "and will be renamed when it starts"
        )
        lines += [
            "",
            f"It is named <b>{ssid}</b> rather than <b>{wanted}</b>, {because}.",
        ]
    lines += ["", "This PC is on Wi-Fi, so sharing it halves the speed. It is the "
              "reach that is worth having, not the speed."]
    return "\n".join(lines)


def ferry_text(origin: str, destination: str, routes: list, stops_named=None) -> str:
    """What sails between two islands.

    Routes rather than departures: the description RTL publishes without an
    account says which boats go this way and where they call, not when. That
    is still the answer to "can I get there from here", which is the question
    somebody at the other end of the country is actually asking.
    """
    if not routes:
        return (
            f"No RTL route goes from <b>{origin}</b> to <b>{destination}</b>.\n\n"
            "They may still be connected by changing boats, which this does not "
            "work out."
        )
    lines = [f"🚤 <b>{origin}</b> → <b>{destination}</b>", ""]
    for route in routes[:6]:
        between = stops_named(route) if stops_named else ()
        if between:
            calling = "calls at " + ", ".join(between)
        else:
            calling = "direct"
        lines.append(f"<b>{route.name}</b> — {calling}")
    if len(routes) > 6:
        lines.append(f"…and {len(routes) - 6} more.")
    lines += ["", "Times and seats need the RTL app or rtl.mv."]
    return "\n".join(lines)


def ferry_choices_text(typed: str, matches: list) -> str:
    """When what was typed names more than one island, or none."""
    if not matches:
        return (
            f"No island matches <b>{typed}</b>.\n\n"
            "Try part of the name on its own, like <code>naiva</code>."
        )
    names = ", ".join(stop.name for stop in matches[:12])
    more = "" if len(matches) <= 12 else f" …and {len(matches) - 12} more."
    return f"<b>{typed}</b> matches several islands:\n\n{names}{more}"


# Picking a ferry journey by tapping. "s:h/Hdh" chooses an atoll, "s:i/105" an
# island, "s:x" starts over. Codes rather than names, because a callback has
# sixty-four bytes and some island names spend a third of that on their own.
CB_FERRY = "s"
FERRY_ATOLL = "h"
FERRY_ISLAND = "i"
FERRY_RESTART = "x"


def ferry_callback(kind: str, value: str = "") -> str:
    return f"{CB_FERRY}:{kind}/{value}" if value else f"{CB_FERRY}:{kind}"


def parse_ferry_callback(data: str) -> tuple[str, str]:
    """"s:i/105" as ("i", "105")."""
    body = (data or "").partition(":")[2]
    kind, _, value = body.partition("/")
    return kind, value


def build_atoll_keyboard(atolls: list) -> dict:
    """Every atoll, four to a row: the names are two or three letters."""
    rows: list[list[dict]] = []
    for index, atoll in enumerate(atolls):
        button = {"text": atoll, "callback_data": ferry_callback(FERRY_ATOLL, atoll)}
        if index % 4 == 0:
            rows.append([button])
        else:
            rows[-1].append(button)
    rows.append([{"text": "‹  Menu", "callback_data": CB_MENU}])
    return {"inline_keyboard": rows}


def build_island_keyboard(stops: list) -> dict:
    """The islands on one atoll, two to a row, and a way back to the atolls."""
    rows: list[list[dict]] = []
    for index, stop in enumerate(stops):
        button = {
            "text": stop.island[:22],
            "callback_data": ferry_callback(FERRY_ISLAND, stop.code),
        }
        if index % 2 == 0:
            rows.append([button])
        else:
            rows[-1].append(button)
    rows.append(
        [
            {"text": "‹  Atolls", "callback_data": ferry_callback(FERRY_RESTART)},
            {"text": "‹  Menu", "callback_data": CB_MENU},
        ]
    )
    return {"inline_keyboard": rows}


def ferry_pick_text(stage: str, origin: str = "") -> str:
    """What the picker is asking for at this point."""
    if stage == "from-atoll":
        return "🚤 <b>Ferry</b>\n\nWhich atoll are you leaving from?"
    if stage == "from-island":
        return "🚤 <b>Ferry</b>\n\nWhich island are you leaving from?"
    if stage == "to-atoll":
        return f"🚤 Leaving <b>{origin}</b>.\n\nWhich atoll are you going to?"
    return f"🚤 Leaving <b>{origin}</b>.\n\nWhich island are you going to?"


def build_ferry_again_keyboard() -> dict:
    """Offered under a result: another journey, or back to the menu."""
    return {
        "inline_keyboard": [
            [
                {"text": "🚤  Another journey", "callback_data": ferry_callback(FERRY_RESTART)},
                {"text": "‹  Menu", "callback_data": CB_MENU},
            ]
        ]
    }


def sailings_text(origin: str, destination: str, when: str, sailings: list, routes: list = ()) -> str:
    """What sails today, with how many seats are left on each.

    Seats are the part that changes while somebody is deciding, so they lead.
    """
    head = f"🚤 <b>{origin}</b> → <b>{destination}</b>\n{when}"
    if not sailings:
        if routes:
            names = ", ".join(r.name for r in routes[:4])
            return (
                f"{head}\n\nNothing sails that day.\n\n"
                f"The route exists ({names}) - it may not run on that day of the week."
            )
        return f"{head}\n\nNo RTL route goes that way."
    lines = [head, ""]
    for sail in sailings[:8]:
        if sail.full:
            seats = "full"
        else:
            seats = f"{sail.seats_free} of {sail.seats_total} seats"
        stops = "direct" if not sail.stops else f"{sail.stops} stop{'s' if sail.stops > 1 else ''}"
        lines.append(
            f"<b>{sail.departs_at} → {sail.arrives_at}</b>  ·  {seats}\n"
            f"    {sail.route} · {stops} · MVR {sail.fare:.0f}"
        )
    if len(sailings) > 8:
        lines.append(f"…and {len(sailings) - 8} more.")
    lines += ["", "Book in the RTL app or at rtl.mv."]
    return "\n".join(lines)


FERRY_TRIP = "t"
FERRY_SEAT = "e"


def build_sailings_keyboard(sailings: list) -> dict:
    """One button per sailing that has room, so a seat can be picked on it."""
    rows: list[list[dict]] = []
    for index, sail in enumerate(sailings[:8]):
        if sail.full:
            continue
        rows.append(
            [
                {
                    "text": f"{sail.departs_at} · {sail.seats_free} seats",
                    "callback_data": ferry_callback(FERRY_TRIP, str(index)),
                }
            ]
        )
    rows.append(
        [
            {"text": "🚤  Another journey", "callback_data": ferry_callback(FERRY_RESTART)},
            {"text": "‹  Menu", "callback_data": CB_MENU},
        ]
    )
    return {"inline_keyboard": rows}


def build_seats_keyboard(sail, trip_index: int) -> dict:
    """The free seats, five to a row. Numbers are short and there are many."""
    rows: list[list[dict]] = []
    for position, seat in enumerate(sail.free_seats[:40]):
        button = {
            "text": str(seat),
            "callback_data": ferry_callback(FERRY_SEAT, f"{trip_index}.{seat}"),
        }
        if position % 5 == 0:
            rows.append([button])
        else:
            rows[-1].append(button)
    rows.append(
        [
            {"text": "‹  Sailings", "callback_data": ferry_callback(FERRY_TRIP, "back")},
            {"text": "‹  Menu", "callback_data": CB_MENU},
        ]
    )
    return {"inline_keyboard": rows}


def seat_pick_text(origin: str, destination: str, sail) -> str:
    return (
        f"🚤 <b>{origin}</b> → <b>{destination}</b>\n"
        f"{sail.departs_at} → {sail.arrives_at} · {sail.route} · MVR {sail.fare:.0f}\n\n"
        f"{sail.seats_free} seats free. Pick one.\n"
        "Three each side of the aisle, as they are on the boat. ✕ is taken."
    )


def seat_chosen_text(origin: str, destination: str, sail, seat: int, when: str) -> str:
    """The summary to carry to RTL, and the plain reason Mind stops here."""
    return (
        f"🚤 <b>{origin}</b> → <b>{destination}</b>\n"
        f"{when} · <b>{sail.departs_at} → {sail.arrives_at}</b>\n\n"
        f"Seat <b>{seat}</b> · {sail.route} · boat {sail.boat}\n"
        f"Fare <b>MVR {sail.fare:.0f}</b>\n\n"
        "Book it in the RTL app or at rtl.mv. Mind does not hold the seat: "
        "reserving needs your account, and paying needs your card.\n\n"
        "<a href=\"https://rtl.mv\">rtl.mv</a>"
    )


# The boat's own arrangement: six across, three on each side of the aisle.
SEATS_PER_ROW = 6
SEATS_PER_SIDE = 3


def seat_rows(seat_codes: list, per_row: int = SEATS_PER_ROW) -> list:
    """The seats laid out the way they are on the boat.

    Six to a row, numbered along it, which is what the booking page draws as
    Left Side and Right Side. A seat keeps the column its number gives it, so
    a row that is missing seats keeps its shape rather than closing the gap and
    sliding everything one place along.
    """
    codes = sorted(int(c) for c in seat_codes)
    if not codes:
        return []
    rows: list[list] = []
    highest = max(codes)
    present = set(codes)
    for start in range(1, highest + 1, per_row):
        row = [n if n in present else None for n in range(start, start + per_row)]
        if any(n is not None for n in row):
            rows.append(row)
    return rows


def build_seat_map_keyboard(sail, trip_index: int) -> dict:
    """Every seat in its place: the free ones tappable, the taken ones shown.

    A list of free numbers says how many are left. A map says where they are,
    which is the part somebody choosing actually cares about - by a window, at
    the front, away from the engine.
    """
    free = set(sail.free_seats)
    every = set(free) | set(getattr(sail, "taken_seats", ()) or ())
    if not every:
        every = free
    rows: list[list[dict]] = []
    for row in seat_rows(sorted(every)):
        buttons = []
        for seat in row:
            if seat is None:
                buttons.append({"text": " ", "callback_data": CB_NOOP})
            elif seat in free:
                buttons.append(
                    {
                        "text": str(seat),
                        "callback_data": ferry_callback(FERRY_SEAT, f"{trip_index}.{seat}"),
                    }
                )
            else:
                # Taken. Shown rather than hidden, so the map keeps its shape.
                buttons.append({"text": "✕", "callback_data": CB_NOOP})
        rows.append(buttons)
    rows.append(
        [
            {"text": "‹  Sailings", "callback_data": ferry_callback(FERRY_TRIP, "back")},
            {"text": "‹  Menu", "callback_data": CB_MENU},
        ]
    )
    return {"inline_keyboard": rows}


FERRY_HOLD = "d"


def build_seat_confirm_keyboard(trip_index: int, seat: int) -> dict:
    """Between picking a seat and holding it.

    A tap that takes a seat out of a ferry should not be the same tap that
    chooses which seat to look at. This is the step in between.
    """
    return {
        "inline_keyboard": [
            [
                {
                    "text": f"✅  Hold seat {seat}",
                    "callback_data": ferry_callback(FERRY_HOLD, f"{trip_index}.{seat}"),
                }
            ],
            [
                {"text": "‹  Seats", "callback_data": ferry_callback(FERRY_TRIP, str(trip_index))},
                {"text": "‹  Menu", "callback_data": CB_MENU},
            ],
        ]
    }


def seat_confirm_text(origin: str, destination: str, sail, seat: int, when: str) -> str:
    """What holding it will do, said before it is done."""
    return (
        f"🚤 <b>{origin}</b> → <b>{destination}</b>\n"
        f"{when} · <b>{sail.departs_at} → {sail.arrives_at}</b>\n\n"
        f"Seat <b>{seat}</b> · {sail.route} · boat {sail.boat}\n"
        f"Fare <b>MVR {sail.fare:.0f}</b>\n\n"
        "Holding it takes the seat off the boat for everybody else until it is "
        "paid for or the hold runs out. Mind cannot pay: that needs your card."
    )


def seat_held_text(
    origin: str, destination: str, sail, seat: int, booking: str, who: str = ""
) -> str:
    """The booking to carry to RTL, and the plain fact that it is not paid."""
    return (
        f"✅ Seat <b>{seat}</b> held.\n\n"
        f"🚤 <b>{origin}</b> → <b>{destination}</b>\n"
        f"<b>{sail.departs_at} → {sail.arrives_at}</b> · {sail.route} · MVR {sail.fare:.0f}\n\n"
        f"Booking <code>{booking}</code>\n\n"
        "<b>Not paid yet.</b> "
        + (
            f"Paying puts <b>{who}</b> on the ticket."
            if who
            else "Mind has nobody to put on the ticket: fill in "
            "<b>Who travels on a ferry ticket</b> in Preferences, on the Telegram "
            "tab, and payment can be offered here. Until then, pay for this "
            "booking in the RTL app or at rtl.mv."
        )
        + " The seat goes back on sale when the hold runs out."
    )


FERRY_PAY = "y"


def build_held_keyboard(trip_index: int, seat: int, can_pay: bool = True) -> dict:
    """Offered under a held seat: go and pay for it, or leave it.

    Payment is only offered when there is a passenger to put on the ticket.
    A button that exists to explain why it cannot work is worse than no
    button: it asks somebody to find that out by pressing it.
    """
    rows: list[list[dict]] = []
    if can_pay:
        rows.append(
            [
                {
                    "text": "💳  Continue to payment",
                    "callback_data": ferry_callback(FERRY_PAY, f"{trip_index}.{seat}"),
                }
            ]
        )
    rows.append(
        [
            {"text": "🚤  Another journey", "callback_data": ferry_callback(FERRY_RESTART)},
            {"text": "‹  Menu", "callback_data": CB_MENU},
        ]
    )
    return {"inline_keyboard": rows}


def ferry_payment_text(who: str, sail, seat: int, booking: str, link: str) -> str:
    """The link to pay on, and what it is for."""
    return (
        f"💳 <b>{who}</b> · seat <b>{seat}</b>\n"
        f"{sail.departs_at} → {sail.arrives_at} · {sail.route} · "
        f"<b>MVR {sail.fare:.0f}</b>\n"
        f"Booking <code>{booking}</code>\n\n"
        f'<a href="{link}">Pay on RTL\'s bank page</a>\n\n'
        "The card goes in on that page, which is the bank's, not Mind's. "
        "The ticket is emailed once it goes through."
    )


def ferry_payment_failed_text(booking: str, reason: str) -> str:
    """Why the payment link did not come, in a place it can be read.

    A refused payment is not a refused seat: the hold is still there, and
    saying so stops somebody holding another one on top of it.
    """
    denied = "403" in reason or "401" in reason
    lines = [f"💳 RTL would not start the payment.\n", f"<i>{reason}</i>", ""]
    if denied:
        lines += [
            "That is a refusal to serve the request rather than a problem with "
            "the booking. Paying needs a signed-in RTL session, and signing in "
            "needs the captcha on their login page - which Mind cannot do.",
            "",
        ]
    lines += [
        f"Seat is still held under <code>{booking}</code>. "
        "Pay for it in the RTL app or at rtl.mv, where you are already signed in."
    ]
    return "\n".join(lines)
