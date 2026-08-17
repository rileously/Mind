"""Runs the Telegram bridge on a worker thread inside the desktop app.

Deliberately a thread rather than another process. Mind's background engine is
already a second copy of the executable, and that alone caused an image lock
that blocked in-place updates and stranded runtime folders in temp. A third
process would repeat both problems for no benefit.
"""

from __future__ import annotations

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
)
from .telegram_system import (
    SystemActionError,
    abort_shutdown,
    format_status,
    lock_workstation,
    press_media_key,
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
MEDIA_PROMPT = "🎵  Media keys for this PC."
# Long enough to call off from a phone after a mis-tap.
POWER_DELAY_SECONDS = 60
CB_POWER = "w"
ERROR_BACKOFF_SECONDS = 15
MAX_INPUT_CHARS = 8000


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

    def send_text(self, chat_id: int, text: str) -> None:
        client = self._client
        if client is None:
            return
        try:
            client.send_message(int(chat_id), text)
        except TelegramError as exc:
            self.log.emit(f"Telegram: could not send a reply ({exc})")

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
