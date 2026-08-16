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


class TelegramError(RuntimeError):
    pass


def _redact(message: str, token: str) -> str:
    """Never let a token reach a log or an error dialog."""
    return message.replace(token, "***") if token else message


class TelegramClient:
    def __init__(self, token: str, timeout: float = DEFAULT_TIMEOUT):
        token = (token or "").strip()
        if not token:
            raise TelegramError("A Telegram bot token is required.")
        self._token = token
        self._timeout = timeout

    def _call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
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
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = self._call("getUpdates", payload)
        return result if isinstance(result, list) else []

    def send_message(self, chat_id: int, text: str, reply_to: int | None = None) -> None:
        body = text if text.strip() else "(empty result)"
        # Split rather than truncate: a transformed document is exactly the case
        # where losing the tail without saying so would be worst.
        for index in range(0, len(body), MAX_MESSAGE_CHARS):
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "text": body[index : index + MAX_MESSAGE_CHARS],
                "disable_web_page_preview": True,
            }
            if reply_to is not None and index == 0:
                payload["reply_to_message_id"] = reply_to
            self._call("sendMessage", payload)

    def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        try:
            self._call("sendChatAction", {"chat_id": chat_id, "action": action})
        except TelegramError:
            # Purely cosmetic feedback; never fail a request over it.
            pass

    def send_document(self, chat_id: int, path: str, caption: str = "") -> None:
        """Upload a file with a hand-rolled multipart body.

        sendDocument cannot take JSON, and the standard library has no multipart
        encoder, so the body is assembled here rather than adding a dependency.
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
        filename = source.name.replace('"', "")
        parts += [
            line,
            f'Content-Disposition: form-data; name="document"; filename="{filename}"'.encode(
                "utf-8"
            ),
            b"Content-Type: application/octet-stream",
            b"",
        ]
        body = b"\r\n".join(parts) + b"\r\n" + payload + f"\r\n--{boundary}--\r\n".encode()

        url = f"{API_ROOT}/bot{self._token}/sendDocument"
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
