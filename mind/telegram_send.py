"""Send files or text to Telegram from outside the running app.

Invoked by the Explorer context menu, which starts Mind with --telegram-send and
a path. That runs as its own short-lived process and must not wait on, or
interfere with, whatever instance is already running, so it reads the saved
configuration directly rather than talking to the app.
"""

from __future__ import annotations

from pathlib import Path

from .config_store import ConfigStore
from .telegram_client import TelegramClient, TelegramError
from .telegram_files import MAX_SEND_BYTES, human_size
from .telegram_routing import parse_allowed_chat_ids


class SendError(RuntimeError):
    pass


def target_chat(config: dict) -> int:
    """The chat the context menu sends to.

    Sends go to one chat rather than every allowed one: the allowlist says who
    may drive Mind, which is not the same as who should receive a file the user
    right-clicked.
    """
    configured = config.get("telegram_send_chat_id")
    allowed = parse_allowed_chat_ids(config.get("telegram_allowed_chat_ids"))
    if configured:
        chosen = parse_allowed_chat_ids(configured)
        # Never send outside the allowlist, even if this setting says otherwise.
        picked = next((c for c in chosen if c in allowed), None)
        if picked is not None:
            return picked
    if not allowed:
        raise SendError("No allowed chat is configured for Telegram.")
    return sorted(allowed)[0]


def send_paths(paths: list[str], store: ConfigStore | None = None) -> tuple[int, list[str]]:
    """Send each path as a document. Returns (sent, failure messages)."""
    store = store or ConfigStore()
    config = store.load()
    if not bool(config.get("telegram_enabled", False)):
        raise SendError("The Telegram bridge is switched off in Mind's Preferences.")
    token = store.get_telegram_token(config)
    if not token:
        raise SendError("No Telegram bot token is saved.")

    chat_id = target_chat(config)
    client = TelegramClient(token)
    sent = 0
    failures: list[str] = []

    for raw in paths:
        candidate = Path(raw)
        if not candidate.is_file():
            failures.append(f"{candidate.name}: not a file")
            continue
        try:
            size = candidate.stat().st_size
        except OSError as exc:
            failures.append(f"{candidate.name}: {exc}")
            continue
        if size > MAX_SEND_BYTES:
            failures.append(
                f"{candidate.name}: {human_size(size)} is over Telegram's "
                f"{human_size(MAX_SEND_BYTES)} limit"
            )
            continue
        try:
            client.send_document(chat_id, str(candidate), caption=candidate.name)
            sent += 1
        except TelegramError as exc:
            failures.append(f"{candidate.name}: {exc}")
    return sent, failures


def send_text(text: str, store: ConfigStore | None = None) -> None:
    store = store or ConfigStore()
    config = store.load()
    if not bool(config.get("telegram_enabled", False)):
        raise SendError("The Telegram bridge is switched off in Mind's Preferences.")
    token = store.get_telegram_token(config)
    if not token:
        raise SendError("No Telegram bot token is saved.")
    body = (text or "").strip()
    if not body:
        raise SendError("There was no text to send.")
    try:
        TelegramClient(token).send_message(target_chat(config), body)
    except TelegramError as exc:
        raise SendError(str(exc)) from exc
