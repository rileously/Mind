"""A bot token is a bearer credential and must not sit in config.json as text."""

import shutil
import tempfile
import unittest
from pathlib import Path

from mind.config_store import ConfigStore


TOKEN = "8123456789:AAH-this-would-be-a-real-bot-token"


class TelegramTokenStorageTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.store = ConfigStore(root=self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _save_token(self, token: str) -> None:
        config = self.store.set_telegram_token(self.store.load(), token)
        self.store.save(config)

    def test_token_is_not_written_in_plain_text(self):
        self._save_token(TOKEN)
        raw = (self.root / "config.json").read_text(encoding="utf-8")
        self.assertNotIn(TOKEN, raw)
        self.assertNotIn("AAH-this-would-be-a-real-bot-token", raw)

    def test_token_round_trips(self):
        self._save_token(TOKEN)
        self.assertEqual(self.store.get_telegram_token(), TOKEN)

    def test_clearing_the_token_removes_it(self):
        self._save_token(TOKEN)
        self._save_token("")
        self.assertEqual(self.store.get_telegram_token(), "")
        raw = (self.root / "config.json").read_text(encoding="utf-8")
        self.assertNotIn(TOKEN, raw)

    def test_missing_token_reads_as_empty(self):
        self.assertEqual(self.store.get_telegram_token(), "")

    def test_corrupt_token_does_not_raise(self):
        # A config copied between machines cannot be decrypted by DPAPI; the app
        # must report "no token" rather than crash at startup.
        config = self.store.load()
        config["telegram_token_protected"] = "not-a-valid-blob"
        self.store.save(config)
        self.assertEqual(self.store.get_telegram_token(), "")

    def test_bridge_defaults_to_off_with_no_allowlist(self):
        config = self.store.load()
        self.assertFalse(config["telegram_enabled"])
        self.assertEqual(config["telegram_allowed_chat_ids"], [])


if __name__ == "__main__":
    unittest.main()
