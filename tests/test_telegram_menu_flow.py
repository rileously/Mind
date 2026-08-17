"""One menu at a time.

A menu is worth nothing once something has been picked from it, and a second
copy carries the same buttons as the first. Both were being left in the chat, so
using the interface for a minute filled the conversation with identical menus.
These tests hold the two rules that fix it: a new menu replaces the one already
there, and a tap that opens something reuses the message it was tapped on.
"""

import tempfile
import unittest
from pathlib import Path

from mind.config_store import ConfigStore
from mind.telegram_bridge import TelegramBridge
from mind.telegram_ui import CB_MENU, MENU_ACTIONS, callback


class FakeClient:
    """Records calls instead of making them, and hands out message ids."""

    def __init__(self):
        self.sent: list[dict] = []
        self.edited: list[dict] = []
        self.deleted: list[int] = []
        self.answered: list[str] = []
        self._next_id = 100

    def send_message(self, chat_id, text, reply_to=None, reply_markup=None, html=False):
        self._next_id += 1
        self.sent.append({"id": self._next_id, "text": text, "markup": reply_markup})
        return self._next_id

    def edit_message_text(self, chat_id, message_id, text, reply_markup=None, html=False):
        self.edited.append({"id": message_id, "text": text, "markup": reply_markup})

    def delete_message(self, chat_id, message_id):
        self.deleted.append(message_id)

    def answer_callback_query(self, callback_id, text="", alert=False):
        self.answered.append(text)

    def send_chat_action(self, chat_id, action="typing"):
        pass


def action_index(key: str) -> int:
    return next(i for i, action in enumerate(MENU_ACTIONS) if action.key == key)


class MenuFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = ConfigStore(root=self.root / "config")
        self.bridge = TelegramBridge(self.store)
        self.client = FakeClient()
        self.files = self.root / "files"
        (self.files / "Documents").mkdir(parents=True)
        (self.files / "notes.txt").write_text("x", encoding="utf-8")
        self.config = {
            "telegram_files_enabled": True,
            "telegram_control_enabled": True,
            "telegram_files_root": str(self.files),
        }

    def tap(self, key: str | None, message_id: object = 500) -> None:
        index = None if key is None else action_index(key)
        self.bridge._show_menu(self.client, 7, "cb", message_id, index, self.config)

    def test_a_second_menu_removes_the_first(self):
        self.bridge._send_menu(self.client, 7, self.config)
        first = self.client.sent[0]["id"]
        self.bridge._send_menu(self.client, 7, self.config)
        self.assertEqual(self.client.deleted, [first])
        self.assertEqual(len(self.client.sent), 2)

    def test_the_first_menu_deletes_nothing(self):
        self.bridge._send_menu(self.client, 7, self.config)
        self.assertEqual(self.client.deleted, [])

    def test_each_chat_keeps_its_own_menu(self):
        # Deleting another chat's menu because this one moved on would be worse
        # than leaving both.
        self.bridge._send_menu(self.client, 7, self.config)
        self.bridge._send_menu(self.client, 8, self.config)
        self.assertEqual(self.client.deleted, [])

    def test_opening_files_reuses_the_menu_message(self):
        self.tap("files")
        self.assertEqual(len(self.client.sent), 0)
        self.assertEqual([edit["id"] for edit in self.client.edited], [500])
        self.assertIn("Documents", str(self.client.edited[0]["markup"]))

    def test_the_reused_menu_is_no_longer_deleted_as_one(self):
        # It is a file listing now; deleting it when the next menu opens would
        # take away what the user is looking at.
        self.bridge._send_menu(self.client, 7, self.config)
        listing = self.client.sent[0]["id"]
        self.tap("files", message_id=listing)
        self.bridge._send_menu(self.client, 7, self.config)
        self.assertEqual(self.client.deleted, [])

    def test_going_back_to_the_menu_reuses_the_listing(self):
        self.tap("files")
        self.client.edited.clear()
        self.tap(None)
        self.assertEqual(len(self.client.sent), 0)
        self.assertEqual(self.client.edited[0]["id"], 500)

    def test_a_menu_returned_to_is_replaced_next_time(self):
        # After going back, that message is the menu again, so a later /menu
        # must remove it rather than leave two.
        self.tap(None)
        self.bridge._send_menu(self.client, 7, self.config)
        self.assertEqual(self.client.deleted, [500])

    def test_media_and_commands_take_the_menus_place(self):
        for key in ("media", "commands", "find"):
            client = FakeClient()
            self.client = client
            self.tap(key)
            self.assertEqual(len(client.sent), 0, key)
            self.assertEqual(client.edited[0]["id"], 500, key)

    def test_a_message_too_old_to_edit_still_answers(self):
        # Telegram gives no message to edit when the tap is on something it can
        # no longer reach; the reply has to be sent instead of dropped.
        self.tap("media", message_id=None)
        self.assertEqual(len(self.client.edited), 0)
        self.assertEqual(len(self.client.sent), 1)

    def test_the_clipboard_leaves_the_menu_alone(self):
        # It answers with content of its own, so consuming the menu would cost a
        # tap to get back to.
        self.bridge._send_menu(self.client, 7, self.config)
        self.client.edited.clear()
        self.tap("clip", message_id=self.client.sent[0]["id"])
        self.assertEqual(self.client.edited, [])

    def test_a_switched_off_action_changes_nothing(self):
        self.tap("files", message_id=500)
        self.client.edited.clear()
        self.config["telegram_files_enabled"] = False
        self.tap("files", message_id=500)
        self.assertEqual(self.client.edited, [])
        self.assertEqual(len(self.client.sent), 0)
        self.assertTrue(any("Preferences" in text for text in self.client.answered))

    def test_typed_browsing_still_sends_its_own_message(self):
        # /files typed is not a tap on anything, so there is nothing to reuse.
        self.bridge._handle_files(self.client, 7, "files", "", self.config)
        self.assertEqual(len(self.client.sent), 1)
        self.assertEqual(len(self.client.edited), 0)

    def test_the_menu_button_carries_no_action(self):
        # What tells _show_menu to draw the menu rather than run something.
        self.assertEqual(callback(CB_MENU, None), CB_MENU)


if __name__ == "__main__":
    unittest.main()
