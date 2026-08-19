"""Runs the Telegram bridge on a worker thread inside the desktop app.

Deliberately a thread rather than another process. Mind's background engine is
already a second copy of the executable, and that alone caused an image lock
that blocked in-place updates and stranded runtime folders in temp. A third
process would repeat both problems for no benefit.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from .config_store import ConfigStore
from .telegram_client import TelegramClient, TelegramError, guess_extension, scratch_name
from .telegram_files import (
    CB_FIND_OPEN,
    CB_GET,
    CB_HOME,
    CB_NOOP,
    CB_OPEN,
    CB_PAGE,
    CB_UP,
    MAX_SEND_BYTES,
    PathRefused,
    build_keyboard,
    build_search_keyboard,
    entry_at,
    format_header,
    format_search_header,
    human_size,
    is_hidden,
    list_directory,
    parse_callback,
    quick_places,
    relative_label,
    resolve_root,
    resolve_within_root,
    search_files,
    unique_destination,
)
from .telegram_print import (
    IMAGE_SUFFIXES,
    MAX_COPIES,
    PAPERS,
    SIDES_BOTH,
    SIDES_ONE,
    COLOUR_MODES,
    PrintError,
    PrintJob,
    describe as describe_print,
    print_job,
    printers,
    colour_is_advisory,
    refusal_for,
)
from .telegram_ui import (
    CB_ABORT,
    CB_MEDIA,
    CB_MENU,
    CB_PRINT,
    CB_REFRESH,
    CB_WATCH,
    CB_WATCH_FILE,
    CB_APP_CLOSE,
    CB_APP_KILL,
    CB_DEVICES,
    PRINT_CANCEL,
    PRINT_PAPER,
    PRINT_PRINTER,
    PRINT_START,
    PRINT_COLOUR,
    PRINT_COPIES,
    PRINT_GO,
    PRINT_SIDES,
    build_paper_keyboard,
    build_print_offer,
    build_printer_keyboard,
    build_colour_keyboard,
    build_print_summary,
    build_watcher_keyboard,
    build_watcher_files_keyboard,
    build_app_alert_keyboard,
    build_apps_keyboard,
    CB_CALL_ANSWER,
    CB_CALL_MUTE,
    CB_CALL_REJECT,
    CB_DEVICE_ASK,
    CB_DEVICE_BLOCK,
    block_confirm_text,
    build_block_confirm_keyboard,
    power_prompt,
    PRINT_PROMPT,
    build_call_keyboard,
    build_in_call_keyboard,
    build_device_alert_keyboard,
    build_devices_keyboard,
    build_power_menu_keyboard,
    devices_text,
    mac_from_field,
    apps_text,
    watcher_list_text,
    parse_print_callback,
    REACTION_SAVED,
    REACTION_WORKING,
    bot_commands,
    build_abort_keyboard,
    build_copy_keyboard,
    build_main_menu,
    build_media_keyboard,
    build_menu_keyboard,
    build_power_keyboard,
    commands_signature,
    media_key_at,
    menu_action_at,
    menu_text,
    ferry_text,
    ferry_choices_text,
    ferry_pick_text,
    ferry_callback,
    parse_ferry_callback,
    build_atoll_keyboard,
    build_island_keyboard,
    build_ferry_again_keyboard,
    CB_FERRY,
    FERRY_ATOLL,
    FERRY_ISLAND,
    FERRY_RESTART,
    build_hotspot_keyboard,
    hotspot_text,
    CB_HOTSPOT,
    HOTSPOT_REFRESH,
    HOTSPOT_START,
    HOTSPOT_STOP,
    HOTSPOT_MATCH,
)
from dataclasses import replace as replace_device

from .ferry_client import (
    FerryError,
    match_stops as match_ferry_stops,
    network as ferry_network,
    parse_routes as parse_ferry_routes,
    parse_stops as parse_ferry_stops,
    routes_between as ferry_routes_between,
    atolls as ferry_atolls,
    stops_in as ferry_stops_in,
    stop_by_code as ferry_stop_by_code,
)
from .hotspot import Hotspot, HotspotError, band_label, current_wifi, dhcp_fault
from .network_devices import from_dict as device_from_dict, local_ipv4
from .network_scanner import router_credentials
from .adb_client import AdbError
from .phone_watch import phone_for
from .router_client import RouterError, set_blocked
from .watchers import (
    Reading as WatcherReading,
    watches_apps,
    watches_devices,
    watches_wifi,
    to_dict as watcher_to_dict,
    toggled as watcher_toggled,
    evaluate as evaluate_watchers,
    from_dict as watcher_from_dict,
    watched_drives,
    watched_folders,
)
from .telegram_system import (
    SystemActionError,
    abort_shutdown,
    format_status,
    lock_workstation,
    close_app,
    press_media_key,
    read_idle_minutes,
    read_removable_drives,
    read_running_apps,
    read_visible_apps,
    read_wifi_networks,
    read_status,
    restart,
    shutdown,
    sleep_pc,
)
from .telegram_routing import (
    CommandRefused,
    is_authorized,
    parse_allowed_chat_ids,
    parse_message,
    remote_safe_commands,
    select_command,
)
from .transform_client import TransformError, transform_text


POLL_TIMEOUT_SECONDS = 25
# How long a hotspot stays up with nothing connected before Mind takes it
# down. Long enough to walk to the other end of the house and for a phone
# to decide to move, short enough that a forgotten tap costs nothing.
HOTSPOT_IDLE_SECONDS = 300
# Panel kinds. A message is a panel when only its newest copy is useful; two of
# them in the chat is a scrolling problem, never extra information.
PANEL_MENU = "menu"
PANEL_FILES = "files"
PANEL_SEARCH = "search"
PANEL_CLIPBOARD = "clipboard"
PANEL_STATUS = "status"
PANEL_SCREEN = "screen"
PANEL_MEDIA = "media"
PANEL_COMMANDS = "commands"
PANEL_HINT = "hint"
PANEL_WATCH = "watch"
PANEL_POWER = "power"
PANEL_APPS = "apps"
PANEL_DEVICES = "devices"
PANEL_HOTSPOT = "hotspot"
PANEL_FERRY = "ferry"
MEDIA_PROMPT = "🎵  Media keys for this PC."
# Long enough to call off from a phone after a mis-tap.
POWER_DELAY_SECONDS = 60
CB_POWER = "w"
ERROR_BACKOFF_SECONDS = 15
MAX_INPUT_CHARS = 8000
# How recently a device must have been seen to count as still here.
DEVICE_ONLINE_SECONDS = 210.0


def _host_name() -> str:
    import socket

    try:
        return socket.gethostname()
    except OSError:
        return ""


class TelegramBridge(QObject):
    """Polls Telegram and answers messages from allowed chats."""

    log = Signal(str)
    status_changed = Signal(str)
    clipboard_requested = Signal(object)
    clipboard_received = Signal(str)
    image_received = Signal(object, object)
    screenshot_requested = Signal(object)

    def __init__(self, store: ConfigStore, parent: QObject | None = None):
        super().__init__(parent)
        self.store = store
        self._thread: QThread | None = None
        self._stopping = False
        self._offset: int | None = None
        self._client: TelegramClient | None = None
        self._clipboard_text = ""
        self._ocr_results: dict[str, str] = {}
        # Where each chat is currently browsing, and the listing it last saw, so
        # "/get 3" refers to the same thing the user is looking at.
        self._browse_dir: dict[int, Path] = {}
        self._browse_entries: dict[int, list] = {}
        self._search_hits: dict[int, list] = {}
        # What was last published to Telegram's command menu, so it is only
        # re-sent when the settings behind it actually change.
        self._published_commands = ""
        # Panels: messages that show the current state of something rather than
        # carrying content, keyed by chat and kind. Only the newest of each is
        # worth keeping, so sending one takes the previous away. A file the user
        # asked for, a transform, or text read from a photo is content and is
        # never tracked here.
        self._panels: dict[tuple[int, str], int] = {}
        # Files being set up to print, keyed by the message showing the choices,
        # so two files can be arranged independently and a stale message cannot
        # print the wrong one.
        self._print_jobs: dict[tuple[int, int], PrintJob] = {}
        # What each watcher knew last time it was checked: whether it is armed,
        # when it last spoke, and for a folder the names already seen.
        self._watcher_state: dict[str, dict] = {}
        # The files each alert named, so its buttons can hand them over later.
        self._watched_files: dict[tuple[int, int], tuple[str, tuple[str, ...]]] = {}
        # The app each alert is about, so its button closes the right one.
        self._alert_apps: dict[tuple[int, int], str] = {}
        # The apps /apps last listed per chat, so a button means the one whose
        # name was written on it.
        self._listed_apps: dict[int, tuple[str, ...]] = {}
        # The hotspot is only watched once Mind has been asked to turn it on,
        # so a PC that never shares its connection never pays for the check.
        # The chat is kept so the message saying it turned itself off goes
        # back to whoever asked for it.
        self._hotspot_chat: int | None = None
        self._hotspot_idle_since: float | None = None
        # Why a hotspot that is plainly up still cannot be joined. Checked
        # when it starts, because the answer lives on this machine and the
        # error appears on the phone.
        self._hotspot_fault = ""
        # Where each chat has got to in picking a ferry journey.
        self._ferry_pick: dict[int, dict] = {}

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        config = self.store.load()
        token = self.store.get_telegram_token(config)
        if not token:
            self.log.emit("Telegram bridge not started: no bot token is saved.")
            return
        allowed = parse_allowed_chat_ids(config.get("telegram_allowed_chat_ids"))
        if not allowed:
            # Starting without an allowlist would expose the bridge to anyone who
            # finds the bot, so refuse rather than default to open.
            self.log.emit(
                "Telegram bridge not started: add at least one allowed chat ID first."
            )
            self.status_changed.emit("error")
            return

        self._stopping = False
        self._thread = QThread()
        self._thread.run = self._run  # type: ignore[method-assign]
        self._thread.start()
        self.status_changed.emit("starting")

    def stop(self) -> None:
        self._stopping = True
        thread = self._thread
        if thread is not None:
            thread.requestInterruption()
            if not thread.wait(3000):
                thread.terminate()
                thread.wait(1000)
        self._thread = None
        self.status_changed.emit("stopped")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def set_clipboard_text(self, text: str) -> None:
        """Cache the desktop clipboard so /clip can answer without the UI thread."""
        self._clipboard_text = text or ""

    def publish_ocr_result(self, token: str, text: str) -> None:
        self._ocr_results[token] = text

    # -- polling ---------------------------------------------------------

    def _run(self) -> None:
        thread = self._thread
        while thread is not None and not thread.isInterruptionRequested() and not self._stopping:
            try:
                config = self.store.load()
                token = self.store.get_telegram_token(config)
                if not token:
                    break
                if self._client is None:
                    self._client = TelegramClient(token)
                    identity = self._client.get_me()
                    name = identity.get("username") or identity.get("first_name") or "bot"
                    self.log.emit(f"Telegram bridge connected as @{name}")
                    self.status_changed.emit("running")
                    # A reconnect may follow a settings change, so the menu is
                    # republished rather than assumed to still be right.
                    self._published_commands = ""
                self._publish_commands(self._client, config)
                # Checked on the poll's own tick: no timer, no second thread, and
                # the loop is already awake every twenty-five seconds.
                self._check_watchers(self._client, config)
                self._check_hotspot(self._client, config)
                updates = self._client.get_updates(self._offset, timeout=POLL_TIMEOUT_SECONDS)
            except TelegramError as exc:
                self.log.emit(f"Telegram: {exc}")
                self.status_changed.emit("error")
                self._client = None
                self._sleep(ERROR_BACKOFF_SECONDS)
                continue
            except Exception as exc:  # keep the worker alive on anything unexpected
                self.log.emit(f"Telegram bridge error: {exc}")
                self._sleep(ERROR_BACKOFF_SECONDS)
                continue

            for update in updates:
                if self._stopping:
                    break
                try:
                    self._offset = int(update.get("update_id", 0)) + 1
                    self._handle_update(update)
                except Exception as exc:
                    self.log.emit(f"Telegram: could not handle a message ({exc})")

        self.status_changed.emit("stopped")

    def _sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not self._stopping:
            thread = self._thread
            if thread is not None and thread.isInterruptionRequested():
                return
            time.sleep(0.25)

    def _publish_commands(self, client: TelegramClient, config: dict) -> None:
        """Keep Telegram's command menu matching what is switched on.

        Sent through the same poll loop rather than from the settings page: the
        page has no client, and this thread already reloads the configuration on
        every pass.
        """
        commands = self.store.load_commands()
        signature = commands_signature(config, commands)
        if signature == self._published_commands:
            return
        client.set_my_commands(bot_commands(config, commands))
        client.set_chat_menu_button()
        self._published_commands = signature
        self.log.emit("Telegram: updated the command menu.")

    def _handle_app_close_tap(
        self,
        client: TelegramClient,
        chat_id: int,
        callback_id: str,
        message_id: object,
        config: dict,
    ) -> None:
        """Close the app an alert is about.

        Behind the PC controls switch, because closing a program can lose
        unsaved work in the same way the power controls can.
        """
        if not bool(config.get("telegram_control_enabled", False)):
            client.answer_callback_query(
                callback_id,
                "PC controls are switched off. Turn them on in Mind's Preferences.",
                alert=True,
            )
            return
        app = self._alert_apps.get((chat_id, message_id if isinstance(message_id, int) else -1))
        if not app:
            client.answer_callback_query(
                callback_id, "That alert is too old to close from.", alert=True
            )
            return
        client.answer_callback_query(callback_id, f"Closing {app}…")
        try:
            outcome = close_app(app)
        except SystemActionError as exc:
            client.edit_message_text(chat_id, int(message_id), f"{app}: {exc}")
            return
        self.log.emit(f"Telegram: {outcome}")
        client.edit_message_text(chat_id, int(message_id), f"✅  {outcome}")

    def _forget_stale_watched_files(self, chat_id: int, keep: int = 20) -> None:
        keys = [key for key in self._watched_files if key[0] == chat_id]
        for key in keys[:-keep]:
            self._watched_files.pop(key, None)

    def _handle_watched_file_tap(
        self,
        client: TelegramClient,
        chat_id: int,
        callback_id: str,
        message_id: object,
        index: int | None,
        config: dict,
    ) -> None:
        """Send the file an alert named, when its button is tapped."""
        remembered = self._watched_files.get((chat_id, message_id if isinstance(message_id, int) else -1))
        if remembered is None or index is None:
            client.answer_callback_query(
                callback_id, "That alert is too old to fetch from.", alert=True
            )
            return
        folder, names = remembered
        if not 0 <= index < len(names):
            client.answer_callback_query(callback_id)
            return
        try:
            # Resolved inside the watched folder, so a name can only ever mean a
            # file in the folder that was being watched.
            target = resolve_within_root(Path(folder), Path(folder), names[index])
        except PathRefused as exc:
            client.answer_callback_query(callback_id, str(exc)[:190], alert=True)
            return
        if not target.is_file():
            client.answer_callback_query(callback_id, "That file has gone.", alert=True)
            return
        size = target.stat().st_size
        if size > MAX_SEND_BYTES:
            client.answer_callback_query(
                callback_id,
                f"{human_size(size)} is over Telegram's {human_size(MAX_SEND_BYTES)} limit.",
                alert=True,
            )
            return
        client.answer_callback_query(callback_id, f"Sending {target.name}…")
        try:
            if target.suffix.lower() in IMAGE_SUFFIXES:
                # A picture is meant to be looked at, which is what "view" asked
                # for; anything else arrives as a file.
                client.send_chat_action(chat_id, "upload_photo")
                client.send_photo(chat_id, str(target), caption=target.name)
            else:
                client.send_chat_action(chat_id, "upload_document")
                client.send_document(chat_id, str(target), caption=target.name)
            self.log.emit(f"Telegram: sent '{target.name}' from a watcher alert")
        except TelegramError as exc:
            client.send_message(chat_id, f"Could not send that file: {exc}")

    def _send_devices_panel(
        self,
        client: TelegramClient,
        chat_id: int,
        config: dict,
        message_id: object = None,
    ) -> None:
        """Show what is on the network, from what the scanner last found.

        Read rather than scanned: the app owns the scanning, and a sweep from
        the poll thread would be a second scan racing the first.
        """
        enabled = bool(config.get("network_scan_enabled", False))
        devices = [
            device
            for device in (device_from_dict(item) for item in self.store.load_devices())
            if device
        ]
        # The file records history; whether something is online now is decided by
        # how recently it was seen.
        now = time.time()
        fresh = [
            replace_device(device, online=(now - device.last_seen) <= DEVICE_ONLINE_SECONDS)
            for device in devices
        ]
        fresh.sort(key=lambda device: (not device.online, device.display_name.lower()))
        blocked = self.store.load_blocked()
        text = devices_text(fresh, now, enabled, blocked)
        keyboard = (
            build_devices_keyboard(fresh, blocked, self._can_block(config))
            if enabled
            else build_menu_keyboard()
        )
        if self._replace_panel(client, chat_id, message_id, PANEL_DEVICES, text, keyboard):
            return
        self._send_panel(client, chat_id, PANEL_DEVICES, text, keyboard)

    def _can_block(self, config: dict) -> bool:
        """Whether blocking can be offered at all.

        It needs the router's sign-in, which only the Wi-Fi devices page asks
        for, and it is a control of the house rather than of this PC - so it
        follows the same switch as locking the screen and closing apps.
        """
        if not bool(config.get("telegram_control_enabled", False)):
            return False
        address, username, password = router_credentials(self.store)
        return bool(address and username and password)

    def _device_by_mac(self, mac: str):
        """The remembered device with this address, if it is still known."""
        for item in self.store.load_devices():
            device = device_from_dict(item)
            if device and device.mac == mac:
                return device
        return None

    def _is_panel(self, chat_id: int, message_id: object, kind: str) -> bool:
        """Whether this message is the panel of that kind, rather than an alert.

        The two want opposite things. A panel is spent the moment something is
        picked from it, so the question takes its place. An alert is content -
        it says a device joined, and that is worth keeping - so the question is
        written into it without the message becoming a panel that the next
        listing would delete.
        """
        return isinstance(message_id, int) and self._panels.get((chat_id, kind)) == message_id

    def call_keyboard(self, phone_id: str = "") -> dict | None:
        """The buttons for a ringing phone, when there is a phone to act on.

        The phone is named on the buttons: a tap arriving from a message about
        one handset must not reach another that happens to be ringing now.
        """
        if not bool(self.store.load().get("phone_enabled", False)):
            return None
        return build_call_keyboard(phone_id)

    def _handle_call_tap(
        self,
        client: TelegramClient,
        chat_id: int,
        callback_id: str,
        message_id: object,
        doing: str,
        config: dict,
        phone_id: str = "",
    ) -> None:
        """Answer or refuse the call, and say in the message what happened.

        The alert is the thing that said the phone was ringing, so the outcome
        is written into it rather than sent underneath: what matters afterwards
        is what became of that call, not that it was once ringing.
        """
        client.answer_callback_query(callback_id, doing.title() + "…")
        if not bool(config.get("phone_enabled", False)):
            client.send_message(chat_id, "The phone is not switched on in Mind.")
            return
        phone = phone_for(self.store, phone_id)
        keyboard = None
        try:
            if doing == "answer":
                took = phone.answer()
                said = "Answered." if took else "The phone would not take it."
                # Still a call to act on, so the message keeps buttons: the
                # ones that make sense once somebody is talking.
                keyboard = build_in_call_keyboard(False, phone_id) if took else None
            elif doing == "mute":
                muted = phone.toggle_mute()
                said = "Muted." if muted else "Unmuted."
                keyboard = build_in_call_keyboard(muted, phone_id)
            else:
                phone.hang_up()
                said = "Hung up."
        except AdbError as exc:
            said = str(exc)
        except Exception as exc:  # a tap must never take the bridge down
            said = f"The phone could not be reached: {exc}"
        self.log.emit(f"Telegram: {said}")
        if isinstance(message_id, int):
            client.edit_message_text(
                chat_id, message_id, f"☎  {said}", reply_markup=keyboard
            )
        else:
            client.send_message(chat_id, f"☎  {said}", reply_markup=keyboard)

    def _handle_device_ask_tap(
        self,
        client: TelegramClient,
        chat_id: int,
        callback_id: str,
        message_id: object,
        field: str,
        config: dict,
    ) -> None:
        """Ask before blocking, on the message that was tapped."""
        client.answer_callback_query(callback_id)
        mac = mac_from_field(field)
        device = self._device_by_mac(mac)
        if device is None or not self._can_block(config):
            self._send_devices_panel(client, chat_id, config, message_id)
            return
        here = local_ipv4()
        if here and device.ip and device.ip == here:
            # Blocking this PC over Wi-Fi cuts the connection that undoes it,
            # and the bridge runs on it.
            client.answer_callback_query(
                callback_id, "That is this PC. Mind will not block it.", alert=True
            )
            self._send_devices_panel(client, chat_id, config, message_id)
            return
        blocking = mac not in set(self.store.load_blocked())
        text = block_confirm_text(device.display_name, blocking)
        keyboard = build_block_confirm_keyboard(mac, blocking)
        if not self._is_panel(chat_id, message_id, PANEL_DEVICES):
            # Tapped on an arrival alert. The question is added to what the
            # alert already says rather than replacing it, so the message still
            # reads as the thing that happened.
            client.edit_message_text(chat_id, int(message_id), text, reply_markup=keyboard)
            return
        if self._replace_panel(client, chat_id, message_id, PANEL_DEVICES, text, keyboard):
            return
        self._send_panel(client, chat_id, PANEL_DEVICES, text, keyboard)

    def _handle_device_block_tap(
        self,
        client: TelegramClient,
        chat_id: int,
        callback_id: str,
        message_id: object,
        field: str,
        config: dict,
    ) -> None:
        """Do it, then show the panel again with what the router now says.

        This talks to the router, which takes a few seconds, so the tap is
        answered first - an unanswered button spins on the phone.
        """
        client.answer_callback_query(callback_id, "Asking the router…")
        mac = mac_from_field(field)
        device = self._device_by_mac(mac)
        if device is None or not self._can_block(config):
            self._send_devices_panel(client, chat_id, config, message_id)
            return
        blocking = mac not in set(self.store.load_blocked())
        address, username, password = router_credentials(self.store)
        try:
            blocked_now = set_blocked(
                address, username, password, mac, blocking, device.display_name
            )
        except RouterError as exc:
            client.send_message(chat_id, f"The router refused: {exc}")
            self._send_devices_panel(client, chat_id, config, message_id)
            return
        except Exception as exc:  # a tap must never take the bridge down
            client.send_message(chat_id, f"The router could not be changed: {exc}")
            self._send_devices_panel(client, chat_id, config, message_id)
            return
        # The router is the authority on who is blocked, so what it said is
        # what gets written down rather than what was asked for.
        self.store.save_blocked(list(blocked_now))
        said = "is off the Wi-Fi" if blocking else "can use the Wi-Fi again"
        self.log.emit(f"Telegram: {device.display_name} {said}")
        if not self._is_panel(chat_id, message_id, PANEL_DEVICES):
            mark = "🚫" if blocking else "✅"
            client.edit_message_text(
                chat_id, int(message_id), f"{mark}  {device.display_name} {said}."
            )
            return
        self._send_devices_panel(client, chat_id, config, message_id)

    def _send_apps_panel(
        self,
        client: TelegramClient,
        chat_id: int,
        config: dict,
        message_id: object = None,
    ) -> None:
        """Show what is open, with a button to close each one."""
        enabled = bool(config.get("telegram_control_enabled", False))
        apps = read_visible_apps() if enabled else []
        self._listed_apps[chat_id] = tuple(name for name, _title in apps)
        text = apps_text(apps, enabled)
        keyboard = build_apps_keyboard(apps) if apps else build_menu_keyboard()
        if self._replace_panel(client, chat_id, message_id, PANEL_APPS, text, keyboard):
            return
        self._send_panel(client, chat_id, PANEL_APPS, text, keyboard)

    def _handle_apps_tap(
        self,
        client: TelegramClient,
        chat_id: int,
        callback_id: str,
        message_id: object,
        index: int | None,
        config: dict,
    ) -> None:
        """Close the app that was tapped, then show the list as it now stands."""
        if not bool(config.get("telegram_control_enabled", False)):
            client.answer_callback_query(
                callback_id, "PC controls are switched off.", alert=True
            )
            return
        if index is not None and index < 0:
            # The refresh button, which is the same list drawn again.
            client.answer_callback_query(callback_id)
            self._send_apps_panel(client, chat_id, config, message_id)
            return
        listed = self._listed_apps.get(chat_id, ())
        if index is None or not 0 <= index < len(listed):
            client.answer_callback_query(callback_id, "Send /apps again.", alert=True)
            return
        app = listed[index]
        client.answer_callback_query(callback_id, f"Closing {app}…")
        try:
            outcome = close_app(app)
            self.log.emit(f"Telegram: {outcome}")
        except SystemActionError as exc:
            client.answer_callback_query(callback_id, str(exc)[:190], alert=True)
        # Redrawn either way, so the list shows what is actually still running.
        self._send_apps_panel(client, chat_id, config, message_id)

    # -- hotspot ---------------------------------------------------------

    def _hotspot_panel_parts(self, config: dict) -> tuple[str, dict]:
        """The panel's words and buttons, for whatever state the radio is in."""
        if not bool(config.get("telegram_hotspot_enabled", False)):
            return hotspot_text("off", 0, "", enabled=False), build_menu_keyboard()
        match_home = bool(config.get("hotspot_match_home_wifi", True))
        if match_home:
            wanted, _key = current_wifi()
        else:
            wanted = str(config.get("hotspot_ssid", "")).strip()
        try:
            state = Hotspot().state()
        except HotspotError as exc:
            return f"📡 {exc}", build_menu_keyboard()
        matched = not wanted or state.ssid == wanted
        text = hotspot_text(
            state.state,
            state.clients,
            state.ssid,
            wanted,
            idle_minutes=HOTSPOT_IDLE_SECONDS // 60,
            match_home=match_home,
            band=band_label(state.band),
            fault=self._hotspot_fault if state.is_on else "",
        )
        return text, build_hotspot_keyboard(
            state.state, matched, state.clients, match_home
        )

    def _send_hotspot_panel(
        self,
        client: TelegramClient,
        chat_id: int,
        config: dict,
        message_id: object = None,
    ) -> None:
        text, keyboard = self._hotspot_panel_parts(config)
        if self._replace_panel(
            client, chat_id, message_id, PANEL_HOTSPOT, text, keyboard, html=True
        ):
            return
        self._send_panel(client, chat_id, PANEL_HOTSPOT, text, keyboard, html=True)

    def _handle_hotspot_tap(
        self,
        client: TelegramClient,
        chat_id: int,
        callback_id: str,
        message_id: object,
        index: int | None,
        config: dict,
    ) -> None:
        """Turn the hotspot on or off, then show what actually happened."""
        if not bool(config.get("telegram_hotspot_enabled", False)):
            client.answer_callback_query(
                callback_id, "Sharing this PC's Wi-Fi is switched off.", alert=True
            )
            return

        radio = Hotspot()
        try:
            if index == HOTSPOT_START:
                client.answer_callback_query(callback_id, "Starting the hotspot…")
                # Named after the home network before it comes up, so a phone
                # that is already looking for that name finds this one too.
                self._apply_hotspot_name(radio, config)
                state = radio.start()
                if state.is_on:
                    self._hotspot_chat = chat_id
                    self._hotspot_idle_since = time.monotonic()
                    self.log.emit(f"Telegram: hotspot on as {state.ssid or 'the saved name'}")
                    # The radio being up is not the same as the hotspot working.
                    self._hotspot_fault = dhcp_fault()
                    if self._hotspot_fault:
                        self.log.emit(f"Telegram: hotspot cannot hand out addresses - {self._hotspot_fault}")
            elif index == HOTSPOT_STOP:
                client.answer_callback_query(callback_id, "Stopping the hotspot…")
                radio.stop()
                self._hotspot_chat = None
                self._hotspot_idle_since = None
                self._hotspot_fault = ""
                self.log.emit("Telegram: hotspot off")
            elif index == HOTSPOT_MATCH:
                client.answer_callback_query(callback_id, "Renaming…")
                if not self._apply_hotspot_name(radio, config):
                    client.answer_callback_query(
                        callback_id,
                        "Windows would not say what this PC's Wi-Fi password is. "
                        "Set the hotspot name yourself in Windows Settings.",
                        alert=True,
                    )
            else:
                client.answer_callback_query(callback_id)
        except HotspotError as exc:
            client.answer_callback_query(callback_id, str(exc)[:190], alert=True)

        # Redrawn whatever happened, so the panel shows the radio's real state
        # rather than the one the tap asked for.
        self._send_hotspot_panel(client, chat_id, config, message_id)

    def _apply_hotspot_name(self, radio: Hotspot, config: dict) -> bool:
        """Give the hotspot the name it is meant to have before it comes up.

        Two shapes, and the setting picks between them. Matching the home
        network means one name and one password across both access points,
        which is what a phone already knows how to handle: it moves to
        whichever is stronger without being asked. Windows hands out its own
        addresses behind the hotspot rather than the router's, so this is not a
        true extender - a connection held open across the move will drop - but
        for picking up a page in a room the router does not reach, it is the
        difference between working and not.

        A name of its own is the other shape, for when a separate network is
        the point rather than an accident. That one is joined by hand the first
        time and never again. There is no third shape without a password:
        Windows accepts WPA2 and WPA3 for a hotspot and nothing else.
        """
        band = str(config.get("hotspot_band", "auto")).strip() or "auto"
        if bool(config.get("hotspot_match_home_wifi", True)):
            ssid, key = current_wifi()
            if not ssid or len(key) < 8:
                return False
        else:
            ssid = str(config.get("hotspot_ssid", "")).strip()
            key = self.store.get_hotspot_password(config)
            if not ssid or len(key) < 8:
                # Nothing configured is not a failure. Windows already has a
                # name and a key of its own, and leaving them alone is the
                # sensible thing to do with no instruction to the contrary.
                return True
        try:
            state = radio.state()
            if state.ssid == ssid and state.band == band:
                return True
            radio.configure(ssid, key, band)
        except HotspotError:
            return False
        return True

    def _check_hotspot(self, client: TelegramClient, config: dict) -> None:
        """Take the hotspot down once nothing has been using it for a while.

        There is no "off" button while it is carrying somebody, because the
        phone tapping it is usually the one being carried. This is the ending
        instead: the radio goes quiet on its own, some minutes after the last
        device leaves.
        """
        if self._hotspot_chat is None:
            return
        if not bool(config.get("telegram_hotspot_enabled", False)):
            self._hotspot_chat = None
            self._hotspot_idle_since = None
            return
        try:
            state = Hotspot().state()
        except HotspotError:
            return
        if not state.is_on:
            self._hotspot_chat = None
            self._hotspot_idle_since = None
            return
        if state.clients > 0:
            self._hotspot_idle_since = None
            return
        now = time.monotonic()
        if self._hotspot_idle_since is None:
            self._hotspot_idle_since = now
            return
        if now - self._hotspot_idle_since < HOTSPOT_IDLE_SECONDS:
            return
        chat_id = self._hotspot_chat
        self._hotspot_chat = None
        self._hotspot_idle_since = None
        try:
            Hotspot().stop()
        except HotspotError as exc:
            self.log.emit(f"Telegram: could not stop the hotspot ({exc})")
            return
        self.log.emit("Telegram: hotspot off after nothing used it")
        client.send_message(
            chat_id,
            f"📡 Hotspot off. Nothing had connected for "
            f"{HOTSPOT_IDLE_SECONDS // 60} minutes.",
        )

    def _load_watchers(self) -> list:
        return [w for w in (watcher_from_dict(i) for i in self.store.load_watchers()) if w]

    def _send_watcher_panel(
        self,
        client: TelegramClient,
        chat_id: int,
        config: dict,
        message_id: object = None,
    ) -> None:
        watchers = self._load_watchers()
        enabled = bool(config.get("watchers_enabled", False))
        text = watcher_list_text(watchers, enabled)
        keyboard = build_watcher_keyboard(watchers) if watchers and enabled else build_menu_keyboard()
        if self._replace_panel(client, chat_id, message_id, PANEL_WATCH, text, keyboard):
            return
        self._send_panel(client, chat_id, PANEL_WATCH, text, keyboard)

    def _handle_watcher_tap(
        self,
        client: TelegramClient,
        chat_id: int,
        callback_id: str,
        message_id: object,
        index: int | None,
        config: dict,
    ) -> None:
        """Pause or resume the watcher that was tapped, and redraw the list."""
        watchers = self._load_watchers()
        if index is None or not 0 <= index < len(watchers):
            # The list has changed since the message was drawn.
            client.answer_callback_query(callback_id, "Send /watch again.", alert=True)
            return
        watcher = watchers[index]
        watchers[index] = watcher_toggled(watcher, not watcher.enabled)
        self.store.save_watchers([watcher_to_dict(w) for w in watchers])
        client.answer_callback_query(
            callback_id, "Paused." if watcher.enabled else "Watching again."
        )
        self._send_watcher_panel(client, chat_id, config, message_id)

    def _watcher_reading(self, watchers: list) -> WatcherReading:
        """Read only what the enabled watchers actually ask about.

        A folder is scanned because something watches it, never speculatively:
        this runs every twenty-five seconds, and walking somewhere large for a
        watcher nobody created would be a waste all day long.
        """
        drives = watched_drives(watchers)
        status = read_status(drives or [])
        free = {drive: free_gb for drive, free_gb, _total in status.disks}
        folders: dict[str, tuple[str, ...]] = {}
        for folder in watched_folders(watchers):
            try:
                with os.scandir(folder) as entries:
                    folders[folder] = tuple(
                        sorted(entry.name for entry in entries if entry.is_file())
                    )
            except OSError:
                # A folder that has gone, or one that cannot be read. Left out of
                # the reading, which the evaluation treats as nothing to say.
                continue
        return WatcherReading(
            running_apps=read_running_apps() if watches_apps(watchers) else None,
            removable_drives=read_removable_drives() if watches_devices(watchers) else None,
            # A network scan costs a process, so it only runs when asked for.
            wifi_networks=read_wifi_networks() if watches_wifi(watchers) else None,
            battery_percent=status.battery_percent,
            on_mains=status.on_mains,
            memory_used_percent=status.memory_used_percent,
            idle_minutes=read_idle_minutes(),
            free_gb=free,
            folder_files=folders,
        )

    def _check_watchers(self, client: TelegramClient, config: dict) -> None:
        """Send whatever the watchers have to say this time round."""
        if not bool(config.get("watchers_enabled", False)):
            return
        watchers = [w for w in (watcher_from_dict(i) for i in self.store.load_watchers()) if w]
        if not watchers:
            return
        try:
            reading = self._watcher_reading(watchers)
        except Exception as exc:  # a reading must never stop the bridge polling
            self.log.emit(f"Telegram: could not read the PC for watchers ({exc})")
            return
        firings, self._watcher_state = evaluate_watchers(
            watchers, reading, self._watcher_state, time.time()
        )
        if not firings:
            return
        allowed = parse_allowed_chat_ids(config.get("telegram_allowed_chat_ids"))
        for firing in firings:
            self.log.emit(f"Telegram: watcher fired - {firing.message}")
            keyboard = None
            if firing.names:
                keyboard = build_watcher_files_keyboard(list(firing.names))
            elif firing.app:
                keyboard = build_app_alert_keyboard(firing.app)
            for chat_id in allowed:
                try:
                    # Content, not a panel: an alert is the reason the chat
                    # exists, and must not be taken away by the next one.
                    sent = client.send_message(
                        int(chat_id), firing.message, reply_markup=keyboard
                    )
                except TelegramError as exc:
                    self.log.emit(f"Telegram: could not send an alert ({exc})")
                    continue
                if firing.app and sent is not None:
                    self._alert_apps[(int(chat_id), sent)] = firing.app
                if firing.names and sent is not None:
                    # Remembered against the message, so a button always means
                    # the file whose name is written on it.
                    self._watched_files[(int(chat_id), sent)] = (
                        firing.folder,
                        tuple(firing.names),
                    )
                    self._forget_stale_watched_files(int(chat_id))

    # -- dispatch --------------------------------------------------------

    def _handle_update(self, update: dict) -> None:
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            self._handle_callback(callback)
            return
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        config = self.store.load()
        allowed = parse_allowed_chat_ids(config.get("telegram_allowed_chat_ids"))
        if not is_authorized(chat_id, allowed):
            # Stay silent. Replying would confirm the bot is live and tell an
            # unknown sender that a real allowlist exists to get onto.
            self.log.emit(f"Telegram: ignored a message from unlisted chat {chat_id}")
            return

        client = self._client
        if client is None:
            return
        message_id = message.get("message_id")

        photos = message.get("photo")
        document = message.get("document")
        if photos or self._is_image_document(document):
            self._handle_image(client, int(chat_id), message, photos, document, config)
            return
        if isinstance(document, dict):
            # Any other attachment is a file transfer to the PC.
            self._save_incoming_document(client, int(chat_id), document, config)
            return

        text = str(message.get("text") or message.get("caption") or "")
        if not text.strip():
            return
        self._handle_text(client, int(chat_id), text, message_id, config)

    @staticmethod
    def _is_image_document(document: object) -> bool:
        return (
            isinstance(document, dict)
            and str(document.get("mime_type", "")).startswith("image/")
        )

    def _handle_text(
        self,
        client: TelegramClient,
        chat_id: int,
        text: str,
        message_id: object,
        config: dict,
    ) -> None:
        prefix = str(config.get("prefix", "?"))
        request = parse_message(text, prefix)
        trigger = (request.trigger or "").lower()

        if trigger in {"start", "menu"}:
            self._send_menu(client, chat_id, config)
            return
        if trigger == "help":
            self._send_panel(
                client,
                chat_id,
                PANEL_MENU,
                self._help_text(config),
                build_main_menu(config),
            )
            return
        if trigger == "clip":
            self.clipboard_requested.emit(chat_id)
            return
        if trigger in {"devices", "network", "wifi"}:
            self._send_devices_panel(client, chat_id, config)
            return
        if trigger in {"apps", "running", "tasks"}:
            self._send_apps_panel(client, chat_id, config)
            return
        # Not "wifi": that already means the devices on it.
        if trigger in {"hotspot", "share", "tether"}:
            self._send_hotspot_panel(client, chat_id, config)
            return
        if trigger in {"ferry", "ferries", "boat"}:
            # Two islands typed still work; nothing typed opens the picker.
            if (request.text or "").strip():
                self._handle_ferry(client, chat_id, request.text)
            else:
                self._send_ferry_panel(client, chat_id, config)
            return
        if trigger in {"watch", "watchers", "alerts"}:
            self._send_watcher_panel(client, chat_id, config)
            return
        if trigger in {"commands", "list"}:
            self._send_panel(
                client,
                chat_id,
                PANEL_COMMANDS,
                self._command_list(config),
                build_menu_keyboard(),
            )
            return
        if trigger in {
            "status",
            "screen",
            "lock",
            "sleep",
            "media",
            "shutdown",
            "restart",
            "abort",
        }:
            self._handle_system(client, chat_id, trigger, request.text, config)
            return
        if trigger in {"find", "search"}:
            self._handle_search(client, chat_id, request.text, config)
            return
        if trigger in {"files", "ls", "cd", "get", "pwd"}:
            self._handle_files(client, chat_id, trigger, request.text, config)
            return
        if trigger == "save":
            payload = request.text or ""
            if not payload.strip():
                client.send_message(chat_id, "Send some text after /save to store it.")
                return
            self.clipboard_received.emit(payload)
            # The reaction is the confirmation: the text is already on screen,
            # directly above, and a reply would only push it further away.
            if isinstance(message_id, int):
                client.set_message_reaction(chat_id, message_id, REACTION_SAVED)
            else:
                client.send_message(chat_id, "Saved to your PC's clipboard history.")
            return

        commands = self.store.load_commands()
        try:
            command = select_command(
                request, commands, str(config.get("telegram_default_command", ""))
            )
        except CommandRefused as exc:
            client.send_message(chat_id, str(exc))
            self.log.emit(f"Telegram: refused a shell command from chat {chat_id}")
            return

        if command is None:
            client.send_message(
                chat_id,
                f"I do not know '{trigger or text[:20]}'. Send /commands to see what is available.",
            )
            return

        payload = request.text if request.trigger else text
        if len(payload) > MAX_INPUT_CHARS:
            client.send_message(chat_id, "That message is too long for Mind to transform.")
            return
        if not payload.strip():
            client.send_message(chat_id, "Send the text to transform after the command.")
            return

        if str(command.get("type")) == "replacer-text":
            client.send_message(chat_id, str(command.get("value", "")), reply_to=message_id)
            return

        client.send_chat_action(chat_id)
        # A transform can take a few seconds. The typing indicator says something
        # is happening; the reaction says which message it is happening to.
        if isinstance(message_id, int):
            client.set_message_reaction(chat_id, message_id, REACTION_WORKING)
        try:
            result = transform_text(
                config,
                self.store.get_keys(config),
                payload,
                str(command.get("prompt", "")),
            )
        except TransformError as exc:
            client.send_message(chat_id, f"Mind could not transform that: {exc}")
            return
        client.send_message(chat_id, result, reply_to=message_id)

    def _handle_callback(self, callback: dict) -> None:
        """Act on a button tap and update the message in place."""
        client = self._client
        if client is None:
            return
        callback_id = str(callback.get("id", ""))
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        message_id = message.get("message_id")

        config = self.store.load()
        allowed = parse_allowed_chat_ids(config.get("telegram_allowed_chat_ids"))
        if not is_authorized(chat_id, allowed):
            # A tap is a request like any other; the allowlist applies equally.
            self.log.emit(f"Telegram: ignored a button tap from unlisted chat {chat_id}")
            client.answer_callback_query(callback_id)
            return
        chat_id = int(chat_id)
        action, index = parse_callback(str(callback.get("data", "")))

        if action == CB_NOOP:
            client.answer_callback_query(callback_id)
            return

        if action == CB_MENU:
            self._show_menu(client, chat_id, callback_id, message_id, index, config)
            return

        if action == CB_MEDIA:
            self._handle_media_tap(client, chat_id, callback_id, index, config)
            return

        if action == CB_DEVICES:
            client.answer_callback_query(callback_id)
            self._send_devices_panel(client, chat_id, config, message_id)
            return

        if action in {CB_CALL_ANSWER, CB_CALL_REJECT, CB_CALL_MUTE}:
            doing = {
                CB_CALL_ANSWER: "answer",
                CB_CALL_MUTE: "mute",
                CB_CALL_REJECT: "hang up",
            }[action]
            # The phone rides on the button: "d:p2" means that handset.
            _code, _, phone_id = str(callback.get("data", "")).partition(":")
            self._handle_call_tap(
                client, chat_id, callback_id, message_id, doing, config, phone_id
            )
            return

        if action in {CB_DEVICE_ASK, CB_DEVICE_BLOCK}:
            field = str(callback.get("data", "")).partition(":")[2]
            handler = (
                self._handle_device_ask_tap
                if action == CB_DEVICE_ASK
                else self._handle_device_block_tap
            )
            handler(client, chat_id, callback_id, message_id, field, config)
            return

        if action == CB_APP_KILL:
            self._handle_apps_tap(client, chat_id, callback_id, message_id, index, config)
            return

        if action == CB_HOTSPOT:
            self._handle_hotspot_tap(
                client, chat_id, callback_id, message_id, index, config
            )
            return

        if action == CB_FERRY:
            self._handle_ferry_tap(
                client, chat_id, callback_id, message_id, str(callback.get("data", "")), config
            )
            return

        if action == CB_APP_CLOSE:
            self._handle_app_close_tap(client, chat_id, callback_id, message_id, config)
            return

        if action == CB_WATCH_FILE:
            self._handle_watched_file_tap(
                client, chat_id, callback_id, message_id, index, config
            )
            return

        if action == CB_WATCH:
            self._handle_watcher_tap(client, chat_id, callback_id, message_id, index, config)
            return

        if action == CB_PRINT:
            self._handle_print_tap(
                client, chat_id, callback_id, message_id, str(callback.get("data", "")), config
            )
            return

        if action == CB_ABORT:
            if not bool(config.get("telegram_power_enabled", False)):
                client.answer_callback_query(callback_id, "Shutdown is switched off.")
                return
            try:
                abort_shutdown()
            except SystemActionError as exc:
                client.answer_callback_query(callback_id, str(exc)[:190], alert=True)
                return
            client.answer_callback_query(callback_id, "Stopped.")
            client.edit_message_text(
                chat_id, int(message_id), "Cancelled. The PC will stay on."
            )
            return

        # Everything from here on browses files, so this is where that setting
        # applies. Checking it earlier would refuse menu and power taps with a
        # message about file access.
        if not bool(config.get("telegram_files_enabled", False)) and action != CB_POWER:
            client.answer_callback_query(callback_id, "File access is switched off.")
            return

        root = self._files_root(config)
        current = self._browse_dir.get(chat_id, root)
        entries = self._browse_entries.get(chat_id, [])

        if action == CB_POWER:
            if not bool(config.get("telegram_power_enabled", False)):
                client.answer_callback_query(callback_id, "Shutdown is switched off.")
                return
            try:
                if index == 1:
                    shutdown(POWER_DELAY_SECONDS)
                    label = "Shutting down"
                elif index == 2:
                    restart(POWER_DELAY_SECONDS)
                    label = "Restarting"
                elif index == 3:
                    # No delay to abort, so this one is answered and done: the
                    # PC stops replying the moment it goes.
                    if not bool(config.get("telegram_control_enabled", False)):
                        client.answer_callback_query(
                            callback_id, "PC controls are switched off."
                        )
                        return
                    client.answer_callback_query(callback_id, "Going to sleep")
                    client.edit_message_text(
                        chat_id,
                        int(message_id),
                        "😴 Going to sleep. I will stop replying until it wakes.",
                    )
                    self.log.emit(f"Telegram: sleep requested by chat {chat_id}")
                    sleep_pc()
                    return
                else:
                    client.answer_callback_query(callback_id)
                    return
            except SystemActionError as exc:
                client.answer_callback_query(callback_id, str(exc)[:190])
                return
            self.log.emit(f"Telegram: {label.lower()} requested by chat {chat_id}")
            client.answer_callback_query(callback_id, label)
            client.edit_message_text(
                chat_id,
                int(message_id),
                f"{label} in {POWER_DELAY_SECONDS} seconds.",
                reply_markup=build_abort_keyboard(),
            )
            return

        try:
            if action == CB_HOME:
                current = root
                page = 1
            elif action == CB_UP:
                current = resolve_within_root(root, current, "..")
                page = 1
            elif action == CB_PAGE:
                page = index or 1
            elif action == CB_REFRESH:
                # Same folder, read again: a file may have finished downloading
                # or been added since the listing was drawn.
                page = 1
            elif action == CB_FIND_OPEN:
                hits = self._search_hits.get(chat_id, [])
                if index is None or not 0 <= index < len(hits):
                    client.answer_callback_query(
                        callback_id, "Those results are out of date. Search again."
                    )
                    return
                hit = hits[index]
                # Re-check containment: the result was produced earlier, and the
                # allowed root may have been narrowed in settings since.
                target = resolve_within_root(root, root, str(hit.path))
                if hit.is_dir:
                    current = target
                    page = 1
                else:
                    self._send_file(client, chat_id, callback_id, target, config)
                    return
            elif action in {CB_OPEN, CB_GET}:
                if index is None or not 0 <= index < len(entries):
                    # The app restarted, or the message is from an older listing.
                    client.answer_callback_query(
                        callback_id, "That listing is out of date. Send /files again."
                    )
                    return
                entry = entries[index]
                target = resolve_within_root(root, current, entry.name)
                if action == CB_GET:
                    self._send_file(client, chat_id, callback_id, target, config)
                    return
                current = target
                page = 1
            else:
                client.answer_callback_query(callback_id)
                return

            entries = list_directory(
                current, include_hidden=bool(config.get("telegram_show_hidden", False))
            )
        except PathRefused as exc:
            client.answer_callback_query(callback_id, str(exc)[:190])
            return
        except OSError as exc:
            client.answer_callback_query(callback_id, f"Could not open that: {exc}"[:190])
            return

        self._browse_dir[chat_id] = current
        self._browse_entries[chat_id] = entries
        places = quick_places(root) if current == root else None
        client.answer_callback_query(callback_id)
        # Through _replace_panel so a message that was the menu, or a search, is
        # tracked as the listing it has become.
        self._replace_panel(
            client,
            chat_id,
            int(message_id),
            PANEL_FILES,
            format_header(root, current, entries, page),
            build_keyboard(entries, page, current == root, places),
        )

    def _forget_panel(self, chat_id: int, message_id: int) -> None:
        """Stop treating a message as a panel of whatever kind it used to be.

        Called before a message is given a new role. Without it, a listing that
        was once the menu would still be deleted the next time a menu opens,
        taking away what the user is looking at.
        """
        for key, tracked in list(self._panels.items()):
            if key[0] == chat_id and tracked == message_id:
                self._panels.pop(key, None)

    def _send_panel(
        self,
        client: TelegramClient,
        chat_id: int,
        kind: str,
        text: str,
        reply_markup: dict | None = None,
        html: bool = False,
    ) -> None:
        """Send a panel, taking away the previous one of the same kind.

        Asking twice for the clipboard, or the menu, or the status of the PC
        should leave one answer in the chat rather than a column of them: the
        older copies are already out of date, or identical, and either way they
        are only something to scroll past.
        """
        key = (chat_id, kind)
        previous = self._panels.get(key)
        # Sent before the old one is removed, so a send that fails leaves the
        # stale panel rather than leaving the chat with nothing at all.
        sent = client.send_message(chat_id, text, reply_markup=reply_markup, html=html)
        if previous is not None:
            client.delete_message(chat_id, previous)
        if sent is None:
            self._panels.pop(key, None)
        else:
            self._panels[key] = sent

    def _replace_panel(
        self,
        client: TelegramClient,
        chat_id: int,
        message_id: object,
        kind: str,
        text: str,
        reply_markup: dict | None,
        html: bool = False,
    ) -> bool:
        """Turn a panel into a different one, in place.

        A menu is spent the moment something is picked from it, so the reply
        takes its place instead of being added below it. Returns whether the
        message could be reused; a tap on a message too old to edit falls back
        to sending.
        """
        if not isinstance(message_id, int):
            return False
        self._forget_panel(chat_id, message_id)
        # A panel of this kind may already exist somewhere else in the chat -
        # tapping Menu on a listing while a menu is still up is the everyday way
        # in. Without this the older one is only forgotten, not removed, and the
        # chat keeps both.
        displaced = self._panels.get((chat_id, kind))
        client.edit_message_text(
            chat_id, message_id, text, reply_markup=reply_markup, html=html
        )
        if displaced is not None and displaced != message_id:
            client.delete_message(chat_id, displaced)
        self._panels[(chat_id, kind)] = message_id
        return True

    def _send_menu(self, client: TelegramClient, chat_id: int, config: dict) -> None:
        self._send_panel(
            client,
            chat_id,
            PANEL_MENU,
            menu_text(config, _host_name()),
            reply_markup=build_main_menu(config),
            html=True,
        )

    def _show_menu(
        self,
        client: TelegramClient,
        chat_id: int,
        callback_id: str,
        message_id: object,
        index: int | None,
        config: dict,
    ) -> None:
        """Run a home menu button, or draw the menu when the tap carried no action.

        The actions are the same code paths the typed commands use, so a button
        and a command can never drift apart in what they do.
        """
        action = menu_action_at(index)
        if action is None:
            client.answer_callback_query(callback_id)
            # A listing or a keyboard becoming the menu again: reuse it.
            if not self._replace_panel(
                client,
                chat_id,
                message_id,
                PANEL_MENU,
                menu_text(config, _host_name()),
                build_main_menu(config),
                html=True,
            ):
                self._send_menu(client, chat_id, config)
            return

        # A setting can have been switched off since the message was sent.
        if action.needs and not bool(config.get(action.needs, False)):
            client.answer_callback_query(
                callback_id, "That is switched off in Mind's Preferences.", alert=True
            )
            return

        client.answer_callback_query(callback_id)
        if action.key == "commands":
            listing = self._command_list(config)
            if not self._replace_panel(
                client, chat_id, message_id, PANEL_COMMANDS, listing, build_menu_keyboard()
            ):
                self._send_panel(
                    client, chat_id, PANEL_COMMANDS, listing, build_menu_keyboard()
                )
        elif action.key == "find":
            hint = "Send /find followed by part of a name, for example:\n/find invoice"
            if not self._replace_panel(
                client, chat_id, message_id, PANEL_HINT, hint, build_menu_keyboard()
            ):
                self._send_panel(client, chat_id, PANEL_HINT, hint, build_menu_keyboard())
        elif action.key == "media":
            if not self._replace_panel(
                client,
                chat_id,
                message_id,
                PANEL_MEDIA,
                MEDIA_PROMPT,
                build_media_keyboard(),
            ):
                self._send_panel(
                    client, chat_id, PANEL_MEDIA, MEDIA_PROMPT, build_media_keyboard()
                )
        elif action.key == "devices":
            self._send_devices_panel(client, chat_id, config, message_id)
        elif action.key == "watch":
            self._send_watcher_panel(client, chat_id, config, message_id)
        elif action.key == "power":
            can_sleep = bool(config.get("telegram_control_enabled", False))
            prompt = power_prompt(POWER_DELAY_SECONDS, can_sleep)
            keyboard = build_power_menu_keyboard(
                lambda choice: f"{CB_POWER}:{choice}", can_sleep=can_sleep
            )
            if not self._replace_panel(
                client, chat_id, message_id, PANEL_POWER, prompt, keyboard
            ):
                self._send_panel(client, chat_id, PANEL_POWER, prompt, keyboard)
        elif action.key == "print":
            if not self._replace_panel(
                client, chat_id, message_id, PANEL_HINT, PRINT_PROMPT, build_menu_keyboard()
            ):
                self._send_panel(
                    client, chat_id, PANEL_HINT, PRINT_PROMPT, build_menu_keyboard()
                )
        elif action.key == "help":
            helping = self._help_text(config)
            if not self._replace_panel(
                client, chat_id, message_id, PANEL_HINT, helping, build_menu_keyboard()
            ):
                self._send_panel(
                    client, chat_id, PANEL_HINT, helping, build_menu_keyboard()
                )
        elif action.key == "apps":
            self._send_apps_panel(client, chat_id, config, message_id)
        elif action.key == "hotspot":
            self._send_hotspot_panel(client, chat_id, config, message_id)
        elif action.key == "ferry":
            self._send_ferry_panel(client, chat_id, config, message_id)
        elif action.key == "files":
            # The listing takes the menu's place, which is also how the browsing
            # buttons already behave once you are inside a folder.
            self._handle_files(
                client, chat_id, "files", "", config, replace_message=message_id
            )
        elif action.key == "clip":
            # These answer with content of their own - text, a photo - so the
            # menu stays where it is rather than being consumed.
            self.clipboard_requested.emit(chat_id)
        else:
            self._handle_system(client, chat_id, action.key, "", config)

    def _handle_print_tap(
        self,
        client: TelegramClient,
        chat_id: int,
        callback_id: str,
        message_id: object,
        data: str,
        config: dict,
    ) -> None:
        """Walk the three questions, then print.

        Each answer edits the same message into the next question, so the whole
        thing occupies one message however many taps it takes, and the file it
        refers to is remembered against that message rather than the chat.
        """
        if not bool(config.get("telegram_print_enabled", False)):
            client.answer_callback_query(
                callback_id,
                "Printing is switched off. Turn on 'Telegram printing' in Mind's "
                "Preferences to use it.",
                alert=True,
            )
            return
        if not isinstance(message_id, int):
            client.answer_callback_query(callback_id, "Send the file again.", alert=True)
            return

        step, index = parse_print_callback(data)
        key = (chat_id, message_id)
        job = self._print_jobs.get(key)
        if job is None:
            # The app restarted, or this is a message from before it did.
            client.answer_callback_query(
                callback_id, "Send the file again to print it.", alert=True
            )
            return

        if step == PRINT_CANCEL:
            self._print_jobs.pop(key, None)
            client.answer_callback_query(callback_id, "Cancelled.")
            client.edit_message_text(
                chat_id, message_id, f"{job.path.name} was not printed."
            )
            return

        if step == PRINT_START:
            try:
                available = printers()
            except PrintError as exc:
                client.answer_callback_query(callback_id, str(exc)[:190], alert=True)
                return
            if not available:
                client.answer_callback_query(
                    callback_id, "No printers are set up on this PC.", alert=True
                )
                return
            job = job.with_printers(available)
            self._print_jobs[key] = job
            client.answer_callback_query(callback_id)
            client.edit_message_text(
                chat_id,
                message_id,
                f"🖨  Print {job.path.name}\n\nWhich printer?",
                reply_markup=build_printer_keyboard(list(available)),
            )
            return

        if step == PRINT_PRINTER:
            job = job.with_printer(index if index is not None else -1)
            if not job.printer:
                client.answer_callback_query(callback_id, "Choose a printer.")
                return
            self._print_jobs[key] = job
            client.answer_callback_query(callback_id)
            client.edit_message_text(
                chat_id,
                message_id,
                f"🖨  {job.path.name}\nPrinter: {job.printer}\n\nWhich paper?",
                reply_markup=build_paper_keyboard(PAPERS),
            )
            return

        if step == PRINT_PAPER:
            job = job.with_paper(index if index is not None else -1)
            if not job.paper:
                client.answer_callback_query(callback_id, "Choose a paper size.")
                return
            self._print_jobs[key] = job
            client.answer_callback_query(callback_id)
            question = "Colour or black and white?"
            if colour_is_advisory(job.path):
                # Said before the choice rather than after the job: for these
                # formats only an administrator can make it stick, and finding
                # that out at the printer is worse than reading it here.
                question += (
                    "\n\nWindows may keep the printer's own setting for this kind "
                    "of file unless Mind runs as administrator."
                )
            client.edit_message_text(
                chat_id,
                message_id,
                f"🖨  {job.path.name}\n{describe_print(job)}\n\n{question}",
                reply_markup=build_colour_keyboard(COLOUR_MODES),
            )
            return

        if step in {PRINT_COLOUR, PRINT_SIDES, PRINT_COPIES}:
            if step == PRINT_COLOUR:
                job = job.with_colour(index if index is not None else -1)
                if not job.is_complete:
                    client.answer_callback_query(
                        callback_id, "Choose colour or black and white."
                    )
                    return
            elif step == PRINT_SIDES:
                job = job.with_sides(SIDES_BOTH if index else SIDES_ONE)
            else:
                job = job.with_copies(index)
            self._print_jobs[key] = job
            client.answer_callback_query(callback_id)
            # The last panel rather than a fourth question: sides and copies are
            # adjustable here, and nothing is printed until Print is tapped.
            lines = [f"🖨  {job.path.name}", describe_print(job)]
            if not job.duplex_capable:
                lines.append("\nThis printer prints one side only.")
            if colour_is_advisory(job.path):
                lines.append(
                    "\nWindows may keep the printer's own paper, colour and sides "
                    "for this kind of file unless Mind runs as administrator."
                )
            client.edit_message_text(
                chat_id,
                message_id,
                "\n".join(lines),
                reply_markup=build_print_summary(job, MAX_COPIES),
            )
            return

        if step == PRINT_GO:
            if not job.is_complete:
                client.answer_callback_query(callback_id, "Choose the settings first.")
                return
            self._print_jobs.pop(key, None)
            # Answered before printing starts: a print can take a moment, and an
            # unanswered tap spins on the phone until it times out.
            client.answer_callback_query(callback_id, "Printing…")
            client.edit_message_text(
                chat_id, message_id, f"🖨  Printing {job.path.name}\n{describe_print(job)}"
            )
            try:
                notes = print_job(job)
            except PrintError as exc:
                self.log.emit(f"Telegram: printing '{job.path.name}' failed ({exc})")
                client.edit_message_text(
                    chat_id,
                    message_id,
                    f"{job.path.name} was not printed.\n{exc}",
                    reply_markup=build_menu_keyboard(),
                )
                return
            self.log.emit(f"Telegram: printed '{job.path.name}' for chat {chat_id}")
            lines = [
                f"✅  Sent {job.path.name} to the printer.",
                describe_print(job),
            ]
            # Said plainly rather than buried: a chosen paper size that could not
            # be applied is exactly what the user would otherwise discover at the
            # printer.
            lines += notes
            client.edit_message_text(
                chat_id,
                message_id,
                "\n".join(lines),
                reply_markup=build_menu_keyboard(),
            )
            return

        client.answer_callback_query(callback_id)

    def _offer_printing(
        self,
        client: TelegramClient,
        chat_id: int,
        path: Path,
        text: str,
        config: dict,
    ) -> None:
        """Say a file was saved, and offer to print it when that is possible.

        The offer is only made when printing is switched on and Windows has a way
        to print that format, so a button never appears that would only explain
        why it cannot work.
        """
        if not bool(config.get("telegram_print_enabled", False)) or refusal_for(path):
            client.send_message(chat_id, text)
            return
        sent = client.send_message(
            chat_id, text, reply_markup=build_print_offer(path.suffix.lstrip(".").upper())
        )
        if sent is not None:
            self._print_jobs[(chat_id, sent)] = PrintJob(path=path)
            self._forget_stale_print_jobs(chat_id)

    def _keep_for_printing(
        self,
        client: TelegramClient,
        chat_id: int,
        document: dict,
        data: bytes,
        config: dict,
    ) -> None:
        """Save an image that arrived as a file, and offer to print it."""
        if not bool(config.get("telegram_files_enabled", False)):
            return
        try:
            inbox = self._inbox(config)
            destination = unique_destination(inbox, str(document.get("file_name") or "image"))
            destination.write_bytes(data)
        except OSError as exc:
            self.log.emit(f"Telegram: could not keep an image for printing ({exc})")
            return
        self._offer_printing(
            client,
            chat_id,
            destination,
            f"Saved {destination.name} ({human_size(len(data))}) to {destination.parent}",
            config,
        )

    def _forget_stale_print_jobs(self, chat_id: int, keep: int = 12) -> None:
        """Keep the most recent offers only, so this cannot grow without end."""
        keys = [key for key in self._print_jobs if key[0] == chat_id]
        for key in keys[:-keep]:
            self._print_jobs.pop(key, None)

    def _handle_media_tap(
        self,
        client: TelegramClient,
        chat_id: int,
        callback_id: str,
        index: int | None,
        config: dict,
    ) -> None:
        """Press a media key without adding a message to the chat for each tap."""
        if not bool(config.get("telegram_control_enabled", False)):
            client.answer_callback_query(
                callback_id, "PC controls are switched off.", alert=True
            )
            return
        key = media_key_at(index)
        if not key:
            client.answer_callback_query(callback_id)
            return
        try:
            press_media_key(key)
        except SystemActionError as exc:
            client.answer_callback_query(callback_id, str(exc)[:190], alert=True)
            return
        # The toast is the whole reply: volume goes up several taps in a row, and
        # each one becoming a message would bury everything else.
        client.answer_callback_query(callback_id, key)

    def _handle_system(
        self,
        client: TelegramClient,
        chat_id: int,
        trigger: str,
        argument: str,
        config: dict,
    ) -> None:
        if not bool(config.get("telegram_control_enabled", False)):
            client.send_message(
                chat_id,
                "PC controls are switched off. Turn on 'Telegram PC controls' in "
                "Mind's Preferences to use them.",
            )
            return

        try:
            if trigger == "status":
                self._send_panel(
                    client,
                    chat_id,
                    PANEL_STATUS,
                    format_status(read_status(), _host_name()),
                    build_menu_keyboard(),
                )
                return

            if trigger == "screen":
                # Grabbing the screen needs the GUI thread, so the main window
                # takes it and calls back.
                self.screenshot_requested.emit(chat_id)
                return

            if trigger == "media":
                if not (argument or "").strip():
                    # No argument is not a mistake to correct but a request for
                    # the controls themselves.
                    self._send_panel(
                        client, chat_id, PANEL_MEDIA, MEDIA_PROMPT, build_media_keyboard()
                    )
                    return
                press_media_key(argument)
                self._send_panel(
                    client,
                    chat_id,
                    PANEL_MEDIA,
                    f"Sent {argument.strip().lower()}.",
                    build_media_keyboard(),
                )
                return

            if trigger == "lock":
                lock_workstation()
                self.log.emit(f"Telegram: locked the session for chat {chat_id}")
                client.send_message(chat_id, "🔒 Locked.")
                return

            if trigger == "sleep":
                client.send_message(chat_id, "😴 Going to sleep. I will stop replying until it wakes.")
                sleep_pc()
                return

            if trigger == "abort":
                abort_shutdown()
                client.send_message(chat_id, "Cancelled. The PC will stay on.")
                return

            if trigger in {"shutdown", "restart"}:
                if not bool(config.get("telegram_power_enabled", False)):
                    client.send_message(
                        chat_id,
                        "Shutdown and restart are switched off. Turn on 'Allow shutdown "
                        "from Telegram' in Mind's Preferences if you want them.",
                    )
                    return
                # Unsaved work is the reason this asks rather than acting: the
                # request arrives from a phone, where a mis-tap is easy.
                verb = "Shut down" if trigger == "shutdown" else "Restart"
                client.send_message(
                    chat_id,
                    f"{verb} this PC?\n\nIt will happen after {POWER_DELAY_SECONDS} seconds, "
                    "and there is a button to stop it until then.",
                    reply_markup=build_power_keyboard(
                        trigger, f"{CB_POWER}:{1 if trigger == 'shutdown' else 2}"
                    ),
                )
                return
        except SystemActionError as exc:
            client.send_message(chat_id, str(exc))

    def _handle_ferry(self, client: TelegramClient, chat_id: int, argument: str) -> None:
        """Which boats go from one island to another.

        Two islands, in the order they are travelled. RTL publishes the network
        without asking anybody to sign in, so this answers from a desk, a
        pocket, or an island with one bar of signal.
        """
        words = (argument or "").split()
        if len(words) < 2:
            client.send_message(
                chat_id,
                "Send two islands, from and to. For example:\n"
                "/ferry naivaadhoo kulhudhuffushi",
            )
            return
        # Everything before the last word is the origin, so two-word island
        # names on the left still work.
        typed_from, typed_to = " ".join(words[:-1]), words[-1]
        try:
            described = ferry_network(cache=self.store.root / "ferry.json")
        except FerryError as exc:
            client.send_message(chat_id, f"🚤 {exc}")
            return
        stops = parse_ferry_stops(described)
        origins = match_ferry_stops(stops, typed_from)
        targets = match_ferry_stops(stops, typed_to)
        if len(origins) != 1:
            client.send_message(chat_id, ferry_choices_text(typed_from, origins), html=True)
            return
        if len(targets) != 1:
            client.send_message(chat_id, ferry_choices_text(typed_to, targets), html=True)
            return
        origin, destination = origins[0], targets[0]
        routes = ferry_routes_between(
            parse_ferry_routes(described), origin.name, destination.name
        )
        text = ferry_text(
            origin.name,
            destination.name,
            routes,
            stops_named=lambda r: r.between(origin.name, destination.name),
        )
        self._send_panel(client, chat_id, PANEL_HINT, text, build_menu_keyboard(), html=True)

    # -- ferry ------------------------------------------------------------

    def _ferry_stops(self):
        """Every island RTL calls at. Raises FerryError if it cannot be asked."""
        described = ferry_network(cache=self.store.root / "ferry.json")
        return parse_ferry_stops(described), described

    def _send_ferry_panel(
        self,
        client: TelegramClient,
        chat_id: int,
        config: dict,
        message_id: object = None,
    ) -> None:
        """Start the picker: the atolls, to choose where the journey begins."""
        try:
            stops, _ = self._ferry_stops()
        except FerryError as exc:
            client.send_message(chat_id, f"🚤 {exc}")
            return
        self._ferry_pick[chat_id] = {"stage": "from-atoll"}
        text = ferry_pick_text("from-atoll")
        keyboard = build_atoll_keyboard(ferry_atolls(stops))
        if self._replace_panel(client, chat_id, message_id, PANEL_FERRY, text, keyboard, html=True):
            return
        self._send_panel(client, chat_id, PANEL_FERRY, text, keyboard, html=True)

    def _handle_ferry_tap(
        self,
        client: TelegramClient,
        chat_id: int,
        callback_id: str,
        message_id: object,
        data: str,
        config: dict,
    ) -> None:
        """One tap in the picker: an atoll, an island, or start again."""
        kind, value = parse_ferry_callback(data)
        client.answer_callback_query(callback_id)
        try:
            stops, described = self._ferry_stops()
        except FerryError as exc:
            client.send_message(chat_id, f"🚤 {exc}")
            return

        state = self._ferry_pick.get(chat_id) or {"stage": "from-atoll"}
        if kind == FERRY_RESTART:
            self._send_ferry_panel(client, chat_id, config, message_id)
            return

        if kind == FERRY_ATOLL:
            going_out = state.get("stage", "from-atoll").startswith("from")
            state["stage"] = "from-island" if going_out else "to-island"
            self._ferry_pick[chat_id] = state
            origin = ferry_stop_by_code(stops, state.get("origin", ""))
            self._replace_panel(
                client, chat_id, message_id, PANEL_FERRY,
                ferry_pick_text(state["stage"], origin.island if origin else ""),
                build_island_keyboard(ferry_stops_in(stops, value)),
                html=True,
            )
            return

        if kind != FERRY_ISLAND:
            return
        chosen = ferry_stop_by_code(stops, value)
        if chosen is None:
            client.answer_callback_query(callback_id, "That island is not on the list.", alert=True)
            return

        if state.get("stage") == "from-island":
            # Halfway: remember where they are leaving from, ask where to.
            state.update({"stage": "to-atoll", "origin": chosen.code})
            self._ferry_pick[chat_id] = state
            self._replace_panel(
                client, chat_id, message_id, PANEL_FERRY,
                ferry_pick_text("to-atoll", chosen.island),
                build_atoll_keyboard(ferry_atolls(stops)),
                html=True,
            )
            return

        origin = ferry_stop_by_code(stops, state.get("origin", ""))
        if origin is None:
            self._send_ferry_panel(client, chat_id, config, message_id)
            return
        if origin.code == chosen.code:
            client.answer_callback_query(
                callback_id, "That is where you are leaving from.", alert=True
            )
            return
        routes = ferry_routes_between(parse_ferry_routes(described), origin.name, chosen.name)
        text = ferry_text(
            origin.name, chosen.name, routes,
            stops_named=lambda r: r.between(origin.name, chosen.name),
        )
        self._ferry_pick.pop(chat_id, None)
        self._replace_panel(
            client, chat_id, message_id, PANEL_FERRY, text, build_ferry_again_keyboard(), html=True
        )

    def _handle_search(
        self,
        client: TelegramClient,
        chat_id: int,
        query: str,
        config: dict,
    ) -> None:
        if not bool(config.get("telegram_files_enabled", False)):
            client.send_message(
                chat_id,
                "File access is switched off. Turn on 'Telegram file access' in "
                "Mind's Preferences to search this PC.",
            )
            return
        needle = (query or "").strip()
        if len(needle) < 2:
            client.send_message(
                chat_id, "Send /find followed by at least two characters of a name."
            )
            return

        root = self._files_root(config)
        client.send_chat_action(chat_id, "typing")
        hits, truncated = search_files(
            root, needle, include_hidden=bool(config.get("telegram_show_hidden", False))
        )
        self._search_hits[chat_id] = hits
        self.log.emit(f"Telegram: searched '{needle}', {len(hits)} match(es)")
        # Only the newest search matters: the buttons on an older one index into
        # results that have already been replaced.
        self._send_panel(
            client,
            chat_id,
            PANEL_SEARCH,
            format_search_header(needle, hits, truncated),
            build_search_keyboard(hits) if hits else build_menu_keyboard(),
        )

    def _send_file(
        self,
        client: TelegramClient,
        chat_id: int,
        callback_id: str,
        target: Path,
        config: dict,
    ) -> None:
        if not target.is_file():
            client.answer_callback_query(callback_id, "That is not a file.")
            return
        size = target.stat().st_size
        if size > MAX_SEND_BYTES:
            client.answer_callback_query(
                callback_id,
                f"{human_size(size)} is over Telegram's {human_size(MAX_SEND_BYTES)} limit.",
            )
            return
        client.answer_callback_query(callback_id, f"Sending {target.name}…")
        client.send_chat_action(chat_id, "upload_document")
        try:
            client.send_document(chat_id, str(target), caption=target.name)
            self.log.emit(f"Telegram: sent '{target.name}' to chat {chat_id}")
        except TelegramError as exc:
            client.send_message(chat_id, f"Could not send that file: {exc}")

    def _files_root(self, config: dict) -> Path:
        return resolve_root(str(config.get("telegram_files_root", "")))

    def _inbox(self, config: dict) -> Path:
        """Where files sent to the bot are saved."""
        configured = str(config.get("telegram_inbox", "")).strip()
        folder = Path(configured).expanduser() if configured else Path.home() / "Mind Inbox"
        folder.mkdir(parents=True, exist_ok=True)
        return folder.resolve()

    def _handle_files(
        self,
        client: TelegramClient,
        chat_id: int,
        trigger: str,
        argument: str,
        config: dict,
        replace_message: object = None,
    ) -> None:
        """Browse files. ``replace_message`` reuses that message for the listing.

        Passed when a button opened the listing, so the menu it was tapped on
        becomes the listing rather than staying above it.
        """
        if not bool(config.get("telegram_files_enabled", False)):
            client.send_message(
                chat_id,
                "File browsing is switched off. Turn on 'Telegram file access' in "
                "Mind's Preferences to use it.",
            )
            return

        root = self._files_root(config)
        show_hidden = bool(config.get("telegram_show_hidden", False))
        current = self._browse_dir.get(chat_id, root)
        try:
            # A root that changed in settings must not leave a stale location behind.
            current = resolve_within_root(root, root, str(current))
        except PathRefused:
            current = root

        def reachable(target: Path) -> bool:
            """Hidden entries are off limits unless the user opted in.

            Checked on the resolved path rather than the typed name, so naming a
            hidden folder outright does not get around the listing filter. This
            is what keeps .ssh, .aws and .gnupg out of reach of the bot.
            """
            if show_hidden:
                return True
            probe = target
            while True:
                if probe == root:
                    return True
                if is_hidden(probe):
                    return False
                if probe.parent == probe:
                    return True
                probe = probe.parent

        page = 1
        if trigger in {"files", "ls"} and (argument or "").strip().isdigit():
            page = max(1, int(argument.strip()))

        try:
            if trigger == "pwd":
                self._send_panel(
                    client,
                    chat_id,
                    PANEL_HINT,
                    f"📂 {relative_label(root, current)}\n{current}",
                    build_menu_keyboard(),
                )
                return

            if trigger == "cd":
                choice = (argument or "").strip()
                if not choice:
                    client.send_message(chat_id, "Send /cd followed by a number, a name, or ..")
                    return
                if choice in {"..", "../"}:
                    target = resolve_within_root(root, current, "..")
                else:
                    entry = entry_at(self._browse_entries.get(chat_id, []), choice)
                    name = entry.name if entry else choice
                    if entry is not None and not entry.is_dir:
                        client.send_message(chat_id, f"'{name}' is a file. Use /get to fetch it.")
                        return
                    target = resolve_within_root(root, current, name)
                if not target.is_dir():
                    client.send_message(chat_id, "That is not a folder.")
                    return
                if not reachable(target):
                    client.send_message(
                        chat_id,
                        "That is a hidden system folder. Mind keeps those out of "
                        "Telegram because they often hold credentials.",
                    )
                    return
                current = target
                page = 1

            if trigger == "get":
                entry = entry_at(self._browse_entries.get(chat_id, []), (argument or "").strip())
                name = entry.name if entry else (argument or "").strip()
                if not name:
                    client.send_message(chat_id, "Send /get followed by a number from the list.")
                    return
                target = resolve_within_root(root, current, name)
                if not target.is_file():
                    client.send_message(chat_id, "That is not a file I can send.")
                    return
                if not reachable(target):
                    client.send_message(
                        chat_id,
                        "That is a hidden system file. Mind keeps those out of "
                        "Telegram because they often hold credentials.",
                    )
                    return
                size = target.stat().st_size
                if size > MAX_SEND_BYTES:
                    client.send_message(
                        chat_id,
                        f"'{target.name}' is {human_size(size)}. Telegram will not accept "
                        f"anything over {human_size(MAX_SEND_BYTES)} from a bot.",
                    )
                    return
                client.send_chat_action(chat_id, "upload_document")
                client.send_document(chat_id, str(target), caption=target.name)
                self.log.emit(f"Telegram: sent '{target.name}' to chat {chat_id}")
                return

            entries = list_directory(current, include_hidden=show_hidden)
        except PathRefused as exc:
            client.send_message(chat_id, str(exc))
            return
        except OSError as exc:
            client.send_message(chat_id, f"Could not read that: {exc}")
            return

        self._browse_dir[chat_id] = current
        self._browse_entries[chat_id] = entries
        # Offer the everyday folders only at the top level, where the alternative
        # is a long list of application data.
        places = quick_places(root) if current == root else None
        header = format_header(root, current, entries, page)
        keyboard = build_keyboard(entries, page, current == root, places)
        if self._replace_panel(
            client, chat_id, replace_message, PANEL_FILES, header, keyboard
        ):
            return
        self._send_panel(client, chat_id, PANEL_FILES, header, keyboard)

    def _save_incoming_document(
        self,
        client: TelegramClient,
        chat_id: int,
        document: dict,
        config: dict,
    ) -> None:
        if not bool(config.get("telegram_files_enabled", False)):
            client.send_message(
                chat_id,
                "Saving files is switched off. Turn on 'Telegram file access' in "
                "Mind's Preferences to use it.",
            )
            return
        file_id = str(document.get("file_id", ""))
        if not file_id:
            return
        try:
            remote_path = client.get_file_path(file_id)
            data = client.download_file(remote_path)
        except TelegramError as exc:
            client.send_message(chat_id, f"Could not download that file: {exc}")
            return

        inbox = self._inbox(config)
        # The name comes from Telegram, so it is untrusted; unique_destination
        # strips any path in it and never overwrites.
        destination = unique_destination(inbox, str(document.get("file_name") or "file"))
        try:
            destination.write_bytes(data)
        except OSError as exc:
            client.send_message(chat_id, f"Could not save that file: {exc}")
            return
        self.log.emit(f"Telegram: saved '{destination.name}' from chat {chat_id}")
        self._offer_printing(
            client,
            chat_id,
            destination,
            f"Saved {destination.name} ({human_size(len(data))}) to {destination.parent}",
            config,
        )

    def _handle_image(
        self,
        client: TelegramClient,
        chat_id: int,
        message: dict,
        photos: object,
        document: object,
        config: dict | None = None,
    ) -> None:
        file_id = ""
        if isinstance(photos, list) and photos:
            # Telegram sends several sizes; the last is the largest, which is
            # what OCR needs.
            largest = photos[-1]
            file_id = str(largest.get("file_id", "")) if isinstance(largest, dict) else ""
        elif isinstance(document, dict):
            file_id = str(document.get("file_id", ""))
        if not file_id:
            return

        client.send_chat_action(chat_id, "typing")
        try:
            remote_path = client.get_file_path(file_id)
            data = client.download_file(remote_path)
        except TelegramError as exc:
            client.send_message(chat_id, f"Could not fetch that image: {exc}")
            return

        target = Path(tempfile.gettempdir()) / scratch_name(guess_extension(remote_path))
        try:
            target.write_bytes(data)
        except OSError as exc:
            client.send_message(chat_id, f"Could not save the image for reading: {exc}")
            return

        settings = config if config is not None else self.store.load()
        if isinstance(document, dict) and bool(settings.get("telegram_print_enabled", False)):
            # An image sent as a file is a file: it is kept where the other saved
            # files go, and can be printed. A compressed photo is not - that is
            # something to read, and is only ever read. Saved from the bytes
            # already in hand rather than downloaded a second time.
            self._keep_for_printing(client, chat_id, document, data, settings)

        caption = str(message.get("caption") or "")
        # OCR needs a QImage, which must be built on the GUI thread; hand it over
        # and let the main window call back with the extracted text.
        self.image_received.emit(str(target), {"chat_id": chat_id, "caption": caption})

    # -- replies ---------------------------------------------------------

    def send_file(self, chat_id: int, path, caption: str = "") -> None:
        client = self._client
        if client is None:
            return
        try:
            client.send_document(int(chat_id), str(path), caption=caption)
        except TelegramError as exc:
            self.log.emit(f"Telegram: could not send a file ({exc})")

    def send_image(self, chat_id: int, path, caption: str = "", panel: str = "") -> None:
        """Send an image so it shows in the chat instead of arriving as a file.

        ``panel`` marks it as a view of something rather than content, which for
        a screenshot it is: the previous one is a picture of a screen that has
        since changed, so it goes when a fresh one arrives.
        """
        client = self._client
        if client is None:
            return
        chat_id = int(chat_id)
        key = (chat_id, panel) if panel else None
        previous = self._panels.get(key) if key else None
        try:
            sent = client.send_photo(chat_id, str(path), caption=caption)
        except TelegramError as exc:
            # Nothing has been touched yet, so the previous screenshot is still
            # there and still tracked.
            self.log.emit(f"Telegram: could not send an image ({exc})")
            return
        if previous is not None:
            client.delete_message(chat_id, previous)
        if key is not None:
            if sent is None:
                self._panels.pop(key, None)
            else:
                self._panels[key] = sent

    def send_text(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
        client = self._client
        if client is None:
            return
        try:
            client.send_message(int(chat_id), text, reply_markup=reply_markup)
        except TelegramError as exc:
            self.log.emit(f"Telegram: could not send a reply ({exc})")

    def device_alert_keyboard(self, mac: str, name: str) -> dict | None:
        """The Block button for an arrival alert, when blocking is possible.

        Nothing is offered without the router's sign-in and the PC-controls
        switch, the same as on the devices panel: a button that can only fail is
        worse than a message with no button on it.
        """
        if not self._can_block(self.store.load()):
            return None
        return build_device_alert_keyboard(mac, name)

    def send_clipboard(self, chat_id: int, text: str) -> None:
        """Send clipboard text with a button that copies it on the phone.

        The point of /clip is usually to paste somewhere else, and selecting text
        out of a chat message by hand is the fiddliest part of doing that.
        """
        client = self._client
        if client is None:
            return
        body = text if text.strip() else "Your PC clipboard is empty."
        try:
            # A panel: asking for the clipboard again replaces the answer rather
            # than adding another copy of it below.
            self._send_panel(
                client,
                int(chat_id),
                PANEL_CLIPBOARD,
                body,
                build_copy_keyboard(text),
            )
        except TelegramError as exc:
            self.log.emit(f"Telegram: could not send the clipboard ({exc})")

    def _help_text(self, config: dict) -> str:
        prefix = str(config.get("prefix", "?"))
        default = str(config.get("telegram_default_command", "")).strip()
        lines = [
            "Mind is connected to this chat.",
            "",
            "Send text with a command, for example:",
            f"  /fix your text     or     {prefix}fix your text",
        ]
        if default:
            lines.append(f"Plain text with no command runs /{default}.")
        lines += [
            "",
            "Send a photo and Mind replies with the text it can read from it.",
            "",
            "/menu      buttons for everything below",
            "/clip      send your PC's clipboard here",
            "/save ...  store text in your PC's clipboard history",
            "/commands  list the commands you can use",
        ]
        if config.get("telegram_files_enabled", False):
            root = self._files_root(config)
            lines += [
                "",
                f"Files, limited to {root}:",
                "/files        browse, with buttons to tap",
                "/find <text>  search for a name anywhere below that folder",
                "/cd <n>       open a folder, /cd .. to go up",
                "/get <n>      send me that file",
                "/pwd          where am I",
                "",
                "Send any file and Mind saves it to your PC.",
            ]
            if config.get("telegram_print_enabled", False):
                lines += ["Saved files offer a Print button, if Windows can print them."]
        if config.get("telegram_control_enabled", False):
            lines += [
                "",
                "This PC:",
                "/status       battery, memory, uptime, disk space",
                "/screen       send a screenshot",
                "/lock         lock the session",
                "/sleep        put it to sleep",
                "/media        play, pause, volume, as buttons",
            ]
            if config.get("telegram_power_enabled", False):
                lines += ["/shutdown, /restart, /abort"]
        if config.get("telegram_hotspot_enabled", False):
            lines += [
                "",
                "/hotspot      share this PC's Wi-Fi with the far end of the house",
            ]
        lines += ["", "Shell commands are not available from Telegram."]
        return "\n".join(lines)

    def _command_list(self, config: dict) -> str:
        commands = remote_safe_commands(self.store.load_commands())
        if not commands:
            return "No commands are available from Telegram yet."
        lines = ["Available commands:", ""]
        for command in commands:
            trigger = str(command.get("trigger", ""))
            description = str(command.get("prompt", command.get("value", ""))).replace("\n", " ")
            if len(description) > 70:
                description = description[:67] + "..."
            lines.append(f"/{trigger} — {description}")
        return "\n".join(lines)
