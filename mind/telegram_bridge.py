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
ERROR_BACKOFF_SECONDS = 15
MAX_INPUT_CHARS = 8000


class TelegramBridge(QObject):
    """Polls Telegram and answers messages from allowed chats."""

    log = Signal(str)
    status_changed = Signal(str)
    clipboard_requested = Signal(object)
    clipboard_received = Signal(str)
    image_received = Signal(object, object)

    def __init__(self, store: ConfigStore, parent: QObject | None = None):
        super().__init__(parent)
        self.store = store
        self._thread: QThread | None = None
        self._stopping = False
        self._offset: int | None = None
        self._client: TelegramClient | None = None
        self._clipboard_text = ""
        self._ocr_results: dict[str, str] = {}

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

    # -- dispatch --------------------------------------------------------

    def _handle_update(self, update: dict) -> None:
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
            self._handle_image(client, int(chat_id), message, photos, document)
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

        if trigger in {"start", "help"}:
            client.send_message(chat_id, self._help_text(config))
            return
        if trigger == "clip":
            self.clipboard_requested.emit(chat_id)
            return
        if trigger in {"commands", "list"}:
            client.send_message(chat_id, self._command_list(config))
            return
        if trigger == "save":
            payload = request.text or ""
            if not payload.strip():
                client.send_message(chat_id, "Send some text after /save to store it.")
                return
            self.clipboard_received.emit(payload)
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

    def _handle_image(
        self,
        client: TelegramClient,
        chat_id: int,
        message: dict,
        photos: object,
        document: object,
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

        caption = str(message.get("caption") or "")
        # OCR needs a QImage, which must be built on the GUI thread; hand it over
        # and let the main window call back with the extracted text.
        self.image_received.emit(str(target), {"chat_id": chat_id, "caption": caption})

    # -- replies ---------------------------------------------------------

    def send_text(self, chat_id: int, text: str) -> None:
        client = self._client
        if client is None:
            return
        try:
            client.send_message(int(chat_id), text)
        except TelegramError as exc:
            self.log.emit(f"Telegram: could not send a reply ({exc})")

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
            "/clip      send your PC's clipboard here",
            "/save ...  store text in your PC's clipboard history",
            "/commands  list the commands you can use",
            "",
            "Shell commands are not available from Telegram.",
        ]
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
