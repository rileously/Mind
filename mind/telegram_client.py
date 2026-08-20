"""A small Telegram Bot API client built on the standard library.

Long polling rather than webhooks: Mind is a desktop application with no public
address, and getUpdates only needs an outbound connection, so there is nothing
to expose, forward, or tunnel.

No new dependency is introduced; this uses urllib the same way transform_client
already does.
"""

from __future__ import annotations

import json
import mimetypes
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any


API_ROOT = "https://api.telegram.org"
DEFAULT_TIMEOUT = 35.0
# Telegram rejects text messages longer than this.
MAX_MESSAGE_CHARS = 4096
# copy_text buttons carry their payload in the button itself, and Telegram caps
# it. Longer text has to be offered another way.
MAX_COPY_TEXT_CHARS = 256


def _uncoloured(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """The same payload with every button colour removed, or None if there were none.

    None rather than an unchanged copy, so the caller can tell "nothing to
    strip" from "stripped", and does not retry a request that would fail the
    same way twice.
    """
    if not isinstance(payload, dict):
        return None
    markup = payload.get("reply_markup")
    if not isinstance(markup, dict):
        return None
    rows = markup.get("inline_keyboard")
    if not isinstance(rows, list):
        return None
    found = False
    clean: list[Any] = []
    for row in rows:
        if not isinstance(row, list):
            clean.append(row)
            continue
        buttons = []
        for item in row:
            if isinstance(item, dict) and "style" in item:
                found = True
                buttons.append({k: v for k, v in item.items() if k != "style"})
            else:
                buttons.append(item)
        clean.append(buttons)
    if not found:
        return None
    return {**payload, "reply_markup": {**markup, "inline_keyboard": clean}}


class TelegramError(RuntimeError):
    pass


def _redact(message: str, token: str) -> str:
    """Never let a token reach a log or an error dialog."""
    return message.replace(token, "***") if token else message


def escape_html(text: str) -> str:
    """Make text safe to send with parse_mode HTML.

    Formatting is opt-in per message precisely because most of what Mind sends is
    someone's own text, a path, or a model's output. An unescaped "<" in any of
    those would have Telegram reject the whole message.
    """
    return (
        str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def split_for_telegram(body: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """Cut a long message into sendable pieces, preferring line breaks.

    Splitting mid-tag would leave Telegram with unbalanced HTML and mid-word is
    simply unpleasant to read, so a newline near the end of the window is used
    when there is one.
    """
    text = body if body.strip() else "(empty result)"
    chunks: list[str] = []
    while len(text) > limit:
        window = text[:limit]
        cut = window.rfind("\n")
        # Only honour a break that is not pathologically early, or a single long
        # paragraph would be sliced into slivers.
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    chunks.append(text)
    return chunks


class TelegramClient:
    def __init__(self, token: str, timeout: float = DEFAULT_TIMEOUT):
        token = (token or "").strip()
        if not token:
            raise TelegramError("A Telegram bot token is required.")
        self._token = token
        self._timeout = timeout

    def _call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        """Make one API call, retrying once without button colours if needed.

        Button styles arrived in Bot API 9.4. An older Telegram refuses the
        whole message rather than ignoring the field it does not know, which
        would turn every coloured panel into no panel at all. Colour is worth
        having and not worth losing a panel over, so a refusal on that one
        ground is answered by sending the same keyboard uncoloured.
        """
        try:
            return self._request(method, payload)
        except TelegramError as exc:
            if "button style" not in str(exc).lower():
                raise
            plain = _uncoloured(payload)
            if plain is None:
                raise
            return self._request(method, plain)

    def _request(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{API_ROOT}/bot{self._token}/{method}"
        data = json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "Mind-Telegram-Bridge"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("description", "")
            except Exception:
                detail = exc.reason or ""
            raise TelegramError(_redact(f"Telegram rejected {method}: {detail}", self._token)) from exc
        except urllib.error.URLError as exc:
            raise TelegramError(_redact(f"Could not reach Telegram: {exc.reason}", self._token)) from exc
        except (ValueError, OSError) as exc:
            raise TelegramError(_redact(f"Telegram sent an unreadable reply: {exc}", self._token)) from exc

        if not isinstance(body, dict) or not body.get("ok"):
            description = ""
            if isinstance(body, dict):
                description = str(body.get("description", ""))
            raise TelegramError(_redact(f"Telegram reported an error: {description}", self._token))
        return body.get("result")

    def get_me(self) -> dict[str, Any]:
        result = self._call("getMe")
        return result if isinstance(result, dict) else {}

    def get_updates(self, offset: int | None = None, timeout: int = 25) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": timeout,
            # Only ask for what the bridge acts on, so a group's unrelated
            # traffic is not pulled down and discarded.
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = self._call("getUpdates", payload)
        return result if isinstance(result, list) else []

    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to: int | None = None,
        reply_markup: dict[str, Any] | None = None,
        html: bool = False,
    ) -> int | None:
        """Send a message, returning the id of the last part sent.

        The id is what lets a caller replace this message later instead of
        stacking another one on top of it.
        """
        # Split rather than truncate: a transformed document is exactly the case
        # where losing the tail without saying so would be worst.
        chunks = split_for_telegram(text)
        sent: int | None = None
        for position, chunk in enumerate(chunks):
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "text": chunk,
                # link_preview_options rather than disable_web_page_preview, which
                # Telegram has replaced.
                "link_preview_options": {"is_disabled": True},
            }
            if html:
                payload["parse_mode"] = "HTML"
            if reply_to is not None and position == 0:
                # reply_parameters is the current form; reply_to_message_id is
                # the retired one.
                payload["reply_parameters"] = {
                    "message_id": reply_to,
                    # The message may be gone by the time a slow transform ends.
                    "allow_sending_without_reply": True,
                }
            # Buttons belong on the last chunk, where they end up next to the
            # bottom of the message rather than buried mid-conversation.
            if reply_markup is not None and position == len(chunks) - 1:
                payload["reply_markup"] = reply_markup
            result = self._call("sendMessage", payload)
            if isinstance(result, dict) and isinstance(result.get("message_id"), int):
                sent = int(result["message_id"])
        return sent

    def delete_message(self, chat_id: int, message_id: int) -> None:
        """Remove a message the bot sent.

        Used to take away a menu that has been superseded, rather than leaving a
        column of identical menus behind in the chat. Failure is ignored: the
        message may already be gone, or older than the 48 hours Telegram allows
        a bot to delete within, and neither is worth reporting.
        """
        try:
            self._call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
        except TelegramError:
            pass

    def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
        html: bool = False,
    ) -> None:
        """Update a message in place, so browsing does not fill the chat."""
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text[:MAX_MESSAGE_CHARS],
            "link_preview_options": {"is_disabled": True},
        }
        if html:
            payload["parse_mode"] = "HTML"
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        try:
            self._call("editMessageText", payload)
        except TelegramError as exc:
            # Telegram rejects an edit that would not change anything; that is a
            # no-op for us, not a failure worth surfacing.
            if "not modified" not in str(exc).lower():
                raise

    def answer_callback_query(
        self, callback_id: str, text: str = "", alert: bool = False
    ) -> None:
        """Acknowledge a tap. Without this the button spins on the client."""
        payload: dict[str, Any] = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text[:200]
        if alert:
            # A refusal needs to be read, not glimpsed as a toast.
            payload["show_alert"] = True
        try:
            self._call("answerCallbackQuery", payload)
        except TelegramError:
            pass

    def set_my_commands(self, commands: list[dict[str, str]]) -> None:
        """Publish the command list Telegram shows in its own menu.

        This is what puts Mind's commands behind the "/" button and the menu
        beside the message box, so they can be picked from a list instead of
        remembered and typed.
        """
        try:
            self._call("setMyCommands", {"commands": commands[:100]})
        except TelegramError:
            # Cosmetic: the commands still work when typed.
            pass

    def set_chat_menu_button(self) -> None:
        """Make the button beside the message box open the command list."""
        try:
            self._call("setChatMenuButton", {"menu_button": {"type": "commands"}})
        except TelegramError:
            pass

    def set_message_reaction(self, chat_id: int, message_id: int, emoji: str) -> None:
        """React to a message instead of sending one.

        An acknowledgement that carries no information does not deserve its own
        message: a reaction says "got it" without pushing the conversation up.
        """
        try:
            self._call(
                "setMessageReaction",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "reaction": [{"type": "emoji", "emoji": emoji}],
                },
            )
        except TelegramError:
            pass

    def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        try:
            self._call("sendChatAction", {"chat_id": chat_id, "action": action})
        except TelegramError:
            # Purely cosmetic feedback; never fail a request over it.
            pass

    def send_document(
        self,
        chat_id: int,
        path: str,
        caption: str = "",
        reply_markup: dict[str, Any] | None = None,
    ) -> int | None:
        return self._upload(
            "sendDocument", "document", chat_id, path, caption, reply_markup
        )

    def send_photo(
        self,
        chat_id: int,
        path: str,
        caption: str = "",
        reply_markup: dict[str, Any] | None = None,
    ) -> int | None:
        """Send an image as a photo, which shows in the chat rather than as a file.

        Worth the separate call for screenshots: the whole point of asking for one
        from a phone is to look at it, not to download it first.
        """
        return self._upload("sendPhoto", "photo", chat_id, path, caption, reply_markup)

    def _upload(
        self,
        method: str,
        field: str,
        chat_id: int,
        path: str,
        caption: str,
        reply_markup: dict[str, Any] | None,
    ) -> int | None:
        """Upload a file with a hand-rolled multipart body.

        The upload methods cannot take JSON, and the standard library has no
        multipart encoder, so the body is assembled here rather than adding a
        dependency.
        """
        from pathlib import Path as _Path

        source = _Path(path)
        payload = source.read_bytes()
        boundary = f"----MindBoundary{uuid.uuid4().hex}"
        line = f"--{boundary}".encode()
        parts: list[bytes] = [
            line,
            b'Content-Disposition: form-data; name="chat_id"',
            b"",
            str(chat_id).encode(),
        ]
        if caption:
            parts += [
                line,
                b'Content-Disposition: form-data; name="caption"',
                b"",
                caption.encode("utf-8"),
            ]
        if reply_markup is not None:
            parts += [
                line,
                b'Content-Disposition: form-data; name="reply_markup"',
                b"",
                json.dumps(reply_markup).encode("utf-8"),
            ]
        filename = source.name.replace('"', "")
        parts += [
            line,
            f'Content-Disposition: form-data; name="{field}"; filename="{filename}"'.encode(
                "utf-8"
            ),
            b"Content-Type: application/octet-stream",
            b"",
        ]
        body = b"\r\n".join(parts) + b"\r\n" + payload + f"\r\n--{boundary}--\r\n".encode()

        url = f"{API_ROOT}/bot{self._token}/{method}"
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "Mind-Telegram-Bridge",
            },
            method="POST",
        )
        try:
            # Uploads are slower than API calls, so they get their own budget.
            with urllib.request.urlopen(request, timeout=max(self._timeout, 120.0)) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise TelegramError(_redact(f"Could not send the file: {exc}", self._token)) from exc
        if not isinstance(result, dict) or not result.get("ok"):
            raise TelegramError(_redact("Telegram rejected the file upload.", self._token))
        message = result.get("result")
        if isinstance(message, dict) and isinstance(message.get("message_id"), int):
            return int(message["message_id"])
        return None

    def get_file_path(self, file_id: str) -> str:
        result = self._call("getFile", {"file_id": file_id})
        if not isinstance(result, dict) or not result.get("file_path"):
            raise TelegramError("Telegram did not return a download path for that file.")
        return str(result["file_path"])

    def download_file(self, file_path: str, max_bytes: int = 20 * 1024 * 1024) -> bytes:
        url = f"{API_ROOT}/file/bot{self._token}/{urllib.parse.quote(file_path)}"
        request = urllib.request.Request(url, headers={"User-Agent": "Mind-Telegram-Bridge"})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                # Bounded read so a huge or misreported file cannot exhaust memory.
                data = response.read(max_bytes + 1)
        except (urllib.error.URLError, OSError) as exc:
            raise TelegramError(_redact(f"Could not download the file: {exc}", self._token)) from exc
        if len(data) > max_bytes:
            raise TelegramError("That file is too large for Mind to process.")
        return data


def guess_extension(file_path: str) -> str:
    suffix = "." + file_path.rsplit(".", 1)[-1] if "." in file_path else ""
    if suffix and mimetypes.guess_type(f"x{suffix}")[0]:
        return suffix
    return ".jpg"


def scratch_name(extension: str) -> str:
    return f"mind-telegram-{uuid.uuid4().hex[:12]}{extension}"
