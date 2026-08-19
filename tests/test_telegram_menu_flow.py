"""One panel at a time.

A panel is a message that shows the current state of something - the menu, a
listing, the clipboard, the status of the PC - rather than carrying content. Two
copies of one are never extra information: the older is either identical or out
of date, and either way it is something to scroll past. Asking three times for
the clipboard left three copies in the chat.

These tests hold the rules that fix it: sending a panel takes away the previous
one of its kind, a tap that opens something reuses the message it was tapped on,
and a message that changes role stops being tracked as its old one. Content -
a file, a transform, text read from a photo - is never touched by any of this.
"""

import json
import tempfile
import unittest
from pathlib import Path

from mind.config_store import ConfigStore
from mind.telegram_bridge import (
    PANEL_CLIPBOARD,
    PANEL_FILES,
    PANEL_MENU,
    PANEL_SCREEN,
    TelegramBridge,
)
from mind.telegram_ui import CB_MENU, MENU_ACTIONS, callback


class FakeClient:
    """Records calls instead of making them, and hands out message ids."""

    def __init__(self):
        self.sent: list[dict] = []
        self.edited: list[dict] = []
        self.deleted: list[int] = []
        self.answered: list[str] = []
        self.photos: list[dict] = []
        self.documents: list[dict] = []
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

    def send_photo(self, chat_id, path, caption="", reply_markup=None):
        self._next_id += 1
        self.photos.append({"path": str(path), "caption": caption})
        self.sent.append({"id": self._next_id, "text": caption, "markup": reply_markup})
        return self._next_id

    def send_document(self, chat_id, path, caption="", reply_markup=None):
        self._next_id += 1
        self.documents.append({"path": str(path), "caption": caption})
        self.sent.append({"id": self._next_id, "text": caption, "markup": reply_markup})
        return self._next_id


def action_index(key: str) -> int:
    return next(i for i, action in enumerate(MENU_ACTIONS) if action.key == key)


class BridgeHarness:
    """A bridge wired to a fake client and a throwaway config.

    Not a TestCase itself, so the cases below share the setup without also
    re-running each other's tests.
    """

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


class HotspotPanelTests(BridgeHarness, unittest.TestCase):
    """The hotspot panel, without a radio to bring up.

    Only the paths that never reach Windows are exercised here: the refusal
    when the setting is off, and the idle check declining to run at all. What
    the script itself says is covered in test_hotspot.
    """

    def test_the_button_is_hidden_until_the_setting_is_on(self):
        from mind.telegram_ui import available_menu_actions

        keys = {action.key for _index, action in available_menu_actions(self.config)}
        self.assertNotIn("hotspot", keys)
        self.config["telegram_hotspot_enabled"] = True
        keys = {action.key for _index, action in available_menu_actions(self.config)}
        self.assertIn("hotspot", keys)

    def test_a_tap_with_it_switched_off_says_so_rather_than_starting_a_radio(self):
        self.bridge._send_hotspot_panel(self.client, 7, self.config)
        self.assertIn("switched off", str(self.client.sent[0]["text"]))

    def test_a_button_press_with_it_switched_off_is_refused(self):
        self.bridge._handle_hotspot_tap(self.client, 7, "cb", None, 1, self.config)
        self.assertIn("switched off", " ".join(self.client.answered))

    def test_nothing_is_watched_until_a_hotspot_has_been_started(self):
        # The check spawns PowerShell, so a PC that never shares its connection
        # should never reach it.
        self.assertIsNone(self.bridge._hotspot_chat)
        self.bridge._check_hotspot(self.client, self.config)
        self.assertEqual(self.client.sent, [])

    def test_switching_the_setting_off_stops_the_watching(self):
        self.bridge._hotspot_chat = 7
        self.bridge._check_hotspot(self.client, self.config)
        self.assertIsNone(self.bridge._hotspot_chat)


class HotspotNamingTests(BridgeHarness, unittest.TestCase):
    """Which name the hotspot is given, and by whom.

    A stand-in radio records what it was configured with, so no access point
    comes up and nothing reads the Wi-Fi this machine is actually on.
    """

    class FakeRadio:
        def __init__(self, current="", band="auto"):
            self.current = current
            self.band = band
            self.configured: list[tuple[str, str, str]] = []

        def state(self):
            from mind.hotspot import HotspotState

            return HotspotState(
                state="off", clients=0, ssid=self.current, band=self.band
            )

        def configure(self, ssid, passphrase, band=""):
            self.configured.append((ssid, passphrase, band))
            self.current = ssid
            self.band = band or self.band
            return self.state()

    def setUp(self):
        super().setUp()
        self.config["telegram_hotspot_enabled"] = True
        self.config["hotspot_match_home_wifi"] = False

    def test_a_name_of_its_own_is_used_when_matching_is_off(self):
        self.config["hotspot_ssid"] = "Toilet Wi-Fi"
        config = self.store.set_hotspot_password(self.config, "openthedoor")
        radio = self.FakeRadio(current="DESKTOP-1234")
        self.assertTrue(self.bridge._apply_hotspot_name(radio, config))
        self.assertEqual(radio.configured, [("Toilet Wi-Fi", "openthedoor", "auto")])

    def test_a_hotspot_already_carrying_that_name_is_left_alone(self):
        self.config["hotspot_ssid"] = "Toilet Wi-Fi"
        config = self.store.set_hotspot_password(self.config, "openthedoor")
        radio = self.FakeRadio(current="Toilet Wi-Fi")
        self.assertTrue(self.bridge._apply_hotspot_name(radio, config))
        self.assertEqual(radio.configured, [])

    def test_no_name_set_leaves_windows_own_alone_rather_than_failing(self):
        # Nothing configured is not an error: Windows has a name and a key
        # already, and there is no instruction here to replace them.
        radio = self.FakeRadio(current="DESKTOP-1234")
        self.assertTrue(self.bridge._apply_hotspot_name(radio, self.config))
        self.assertEqual(radio.configured, [])

    def test_a_name_with_no_password_is_not_sent_to_windows(self):
        # Windows has no open hotspot, so a name on its own cannot be applied.
        self.config["hotspot_ssid"] = "Toilet Wi-Fi"
        radio = self.FakeRadio(current="DESKTOP-1234")
        self.assertTrue(self.bridge._apply_hotspot_name(radio, self.config))
        self.assertEqual(radio.configured, [])

    def test_the_band_is_carried_into_the_configure_call(self):
        self.config["hotspot_ssid"] = "Toilet Wi-Fi"
        self.config["hotspot_band"] = "2.4"
        config = self.store.set_hotspot_password(self.config, "openthedoor")
        radio = self.FakeRadio(current="DESKTOP-1234")
        self.bridge._apply_hotspot_name(radio, config)
        self.assertEqual(radio.configured, [("Toilet Wi-Fi", "openthedoor", "2.4")])

    def test_a_band_change_alone_is_enough_to_reconfigure(self):
        # The name already matches, so only the band differs - and that is
        # still a reason to write the configuration rather than skip it.
        self.config["hotspot_ssid"] = "Toilet Wi-Fi"
        self.config["hotspot_band"] = "2.4"
        config = self.store.set_hotspot_password(self.config, "openthedoor")
        radio = self.FakeRadio(current="Toilet Wi-Fi", band="auto")
        self.bridge._apply_hotspot_name(radio, config)
        self.assertEqual(radio.configured, [("Toilet Wi-Fi", "openthedoor", "2.4")])

    def test_the_password_is_not_stored_as_itself(self):
        config = self.store.set_hotspot_password(self.config, "openthedoor")
        self.assertNotIn("openthedoor", json.dumps(config))
        self.assertEqual(self.store.get_hotspot_password(config), "openthedoor")


class MenuFlowTests(BridgeHarness, unittest.TestCase):
    """The menu itself: replaced when sent again, reused when tapped."""

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


class PanelReplacementTests(BridgeHarness, unittest.TestCase):
    """Every panel kind, not only the menu."""

    def test_asking_for_the_clipboard_three_times_leaves_one_answer(self):
        # Exactly what was reported: three taps, three identical messages.
        self.bridge._client = self.client
        for _ in range(3):
            self.bridge.send_clipboard(7, "Preserve")
        self.assertEqual(len(self.client.sent), 3)
        # The first two were taken away as each replacement arrived.
        self.assertEqual(len(self.client.deleted), 2)
        self.assertEqual(
            self.client.deleted, [self.client.sent[0]["id"], self.client.sent[1]["id"]]
        )

    def test_an_empty_clipboard_is_still_only_shown_once(self):
        self.bridge._client = self.client
        self.bridge.send_clipboard(7, "")
        self.bridge.send_clipboard(7, "")
        self.assertEqual(len(self.client.deleted), 1)
        self.assertIn("empty", self.client.sent[-1]["text"])

    def test_a_second_screenshot_takes_the_first_away(self):
        # The old one is a picture of a screen that has since changed.
        self.bridge._client = self.client
        shot = self.root / "shot.png"
        shot.write_bytes(b"x")
        self.bridge.send_image(7, shot, caption="Screen", panel=PANEL_SCREEN)
        self.bridge.send_image(7, shot, caption="Screen", panel=PANEL_SCREEN)
        self.assertEqual(self.client.deleted, [self.client.sent[0]["id"]])

    def test_a_file_the_user_asked_for_is_never_taken_away(self):
        # Content, not a panel: send_image without a panel, as send_file does.
        self.bridge._client = self.client
        shot = self.root / "shot.png"
        shot.write_bytes(b"x")
        self.bridge.send_image(7, shot, caption="Screen")
        self.bridge.send_image(7, shot, caption="Screen")
        self.assertEqual(self.client.deleted, [])

    def test_one_kind_does_not_delete_another(self):
        self.bridge._client = self.client
        self.bridge._send_menu(self.client, 7, self.config)
        self.bridge.send_clipboard(7, "text")
        self.assertEqual(self.client.deleted, [])

    def test_typing_files_twice_leaves_one_listing(self):
        self.bridge._handle_files(self.client, 7, "files", "", self.config)
        first = self.client.sent[0]["id"]
        self.bridge._handle_files(self.client, 7, "files", "", self.config)
        self.assertEqual(self.client.deleted, [first])

    def test_browsing_keeps_the_listing_it_edits(self):
        # Editing a message is not sending one, so nothing is deleted and the
        # message stays tracked as the listing.
        self.bridge._handle_files(self.client, 7, "files", "", self.config)
        listing = self.client.sent[0]["id"]
        self.bridge._replace_panel(
            self.client, 7, listing, PANEL_FILES, "deeper", None
        )
        self.assertEqual(self.client.deleted, [])
        self.assertEqual(self.bridge._panels[(7, PANEL_FILES)], listing)

    def test_a_message_is_only_ever_one_kind_of_panel(self):
        # The trap this guards: a listing that used to be the menu being deleted
        # when the next menu opens, taking away what the user is looking at.
        self.bridge._send_menu(self.client, 7, self.config)
        message = self.client.sent[0]["id"]
        self.tap("files", message_id=message)
        self.assertEqual(self.bridge._panels.get((7, PANEL_MENU)), None)
        self.assertEqual(self.bridge._panels.get((7, PANEL_FILES)), message)
        held = [key for key, value in self.bridge._panels.items() if value == message]
        self.assertEqual(len(held), 1)

    def test_searching_again_replaces_the_previous_results(self):
        # An older result list indexes into hits that have been replaced, so its
        # buttons would open the wrong thing.
        self.bridge._handle_search(self.client, 7, "notes", self.config)
        first = self.client.sent[0]["id"]
        self.bridge._handle_search(self.client, 7, "notes", self.config)
        self.assertEqual(self.client.deleted, [first])

    def test_the_status_of_the_pc_is_replaced_rather_than_repeated(self):
        self.bridge._handle_system(self.client, 7, "status", "", self.config)
        first = self.client.sent[0]["id"]
        self.bridge._handle_system(self.client, 7, "status", "", self.config)
        self.assertEqual(self.client.deleted, [first])

    def test_the_command_list_is_replaced_rather_than_repeated(self):
        self.bridge._handle_text(self.client, 7, "/commands", 1, self.config)
        first = self.client.sent[0]["id"]
        self.bridge._handle_text(self.client, 7, "/commands", 2, self.config)
        self.assertEqual(self.client.deleted, [first])

    def test_turning_a_message_into_the_menu_removes_the_menu_already_up(self):
        # The reported duplicate. A menu is sent, then Menu is tapped on some
        # other panel: that message becomes the menu, and the first one has to go
        # or the chat keeps two identical menus.
        self.bridge._send_menu(self.client, 7, self.config)
        first = self.client.sent[0]["id"]
        self.bridge._handle_system(self.client, 7, "status", "", self.config)
        status = self.client.sent[-1]["id"]
        self.tap(None, message_id=status)
        self.assertEqual(self.client.deleted, [first])
        self.assertEqual(self.bridge._panels[(7, PANEL_MENU)], status)

    def test_the_menu_is_not_deleted_when_it_is_the_message_being_reused(self):
        # Tapping Menu on the menu itself must not delete what it is redrawing.
        self.bridge._send_menu(self.client, 7, self.config)
        menu = self.client.sent[0]["id"]
        self.tap(None, message_id=menu)
        self.assertEqual(self.client.deleted, [])
        self.assertEqual(self.bridge._panels[(7, PANEL_MENU)], menu)

    def test_only_one_listing_survives_when_a_second_one_takes_over(self):
        # Same rule for every kind, not just the menu.
        self.bridge._handle_files(self.client, 7, "files", "", self.config)
        first = self.client.sent[0]["id"]
        self.bridge._send_menu(self.client, 7, self.config)
        menu = self.client.sent[-1]["id"]
        self.tap("files", message_id=menu)
        self.assertIn(first, self.client.deleted)
        self.assertEqual(self.bridge._panels[(7, PANEL_FILES)], menu)

    def test_a_send_that_fails_does_not_take_the_previous_one_away(self):
        # Leaving the chat with neither would be worse than leaving a stale one.
        self.bridge._client = self.client
        shot = self.root / "shot.png"
        shot.write_bytes(b"x")
        self.bridge.send_image(7, shot, caption="Screen", panel=PANEL_SCREEN)
        first = self.client.sent[0]["id"]

        def refuse(*args, **kwargs):
            from mind.telegram_client import TelegramError

            raise TelegramError("no")

        self.client.send_photo = refuse
        self.bridge.send_image(7, shot, caption="Screen", panel=PANEL_SCREEN)
        self.assertEqual(self.client.deleted, [])
        self.assertEqual(self.bridge._panels[(7, PANEL_SCREEN)], first)

    def test_a_text_panel_that_fails_to_send_keeps_the_old_one(self):
        from mind.telegram_client import TelegramError

        self.bridge._send_menu(self.client, 7, self.config)
        first = self.client.sent[0]["id"]

        def refuse(*args, **kwargs):
            raise TelegramError("no")

        self.client.send_message = refuse
        with self.assertRaises(TelegramError):
            self.bridge._send_menu(self.client, 7, self.config)
        self.assertEqual(self.client.deleted, [])
        self.assertEqual(self.bridge._panels[(7, PANEL_MENU)], first)

    def test_the_replacement_arrives_before_the_old_one_goes(self):
        # Deleting first would blank the panel for as long as the send takes.
        order: list[str] = []
        real_send = self.client.send_message
        real_delete = self.client.delete_message

        def send(*args, **kwargs):
            order.append("send")
            return real_send(*args, **kwargs)

        def delete(*args, **kwargs):
            order.append("delete")
            return real_delete(*args, **kwargs)

        self.bridge._send_menu(self.client, 7, self.config)
        self.client.send_message = send
        self.client.delete_message = delete
        self.bridge._send_menu(self.client, 7, self.config)
        self.assertEqual(order, ["send", "delete"])


class MenuReachTests(BridgeHarness, unittest.TestCase):
    """Everything the phone can be told to do, it can also be tapped into.

    Telegram's own command list published /watch, /sleep, /shutdown and
    /restart long before any of them had a button, so the same bot offered them
    in one place and not the other.
    """

    def setUp(self):
        super().setUp()
        self.config.update(
            {
                "watchers_enabled": True,
                "telegram_print_enabled": True,
                "telegram_power_enabled": True,
                "network_scan_enabled": True,
            }
        )

    def shown(self) -> str:
        return str(self.client.edited or self.client.sent)

    def test_every_published_command_that_opens_something_has_a_button(self):
        from mind.telegram_ui import BUILT_IN_COMMANDS, MENU_ACTIONS

        # /save and /find are typed with an argument, and /abort only exists
        # while a shutdown is counting down. The rest should be reachable.
        typed_only = {"save", "abort", "menu", "find"}
        keys = {action.key for action in MENU_ACTIONS}
        keys |= {"status", "screen", "sleep", "shutdown", "restart"}  # under Power
        missing = {
            name for name, _description, _needs in BUILT_IN_COMMANDS
            if name not in typed_only and name not in keys
        }
        self.assertEqual(missing, set())

    def test_alerts_opens_the_watcher_panel(self):
        self.tap("watch")
        self.assertIn("watch", self.shown().lower())

    def test_power_offers_all_three_without_doing_any_of_them(self):
        self.tap("power")
        shown = self.shown()
        self.assertIn("Shut down", shown)
        self.assertIn("Restart", shown)
        self.assertIn("Sleep", shown)

    def test_sleep_is_not_offered_when_pc_controls_are_off(self):
        # It follows the same switch as locking the screen; the other two
        # follow the shutdown one.
        self.config["telegram_control_enabled"] = False
        self.tap("power")
        self.assertNotIn("Sleep", self.shown())

    def test_power_is_not_on_the_menu_at_all_when_shutdown_is_off(self):
        from mind.telegram_ui import build_main_menu

        allowed = {**self.config, "telegram_power_enabled": False}
        labels = [
            button["text"]
            for row in build_main_menu(allowed)["inline_keyboard"]
            for button in row
        ]
        self.assertFalse(any("Power" in label for label in labels))

    def test_help_answers_with_the_help(self):
        self.tap("help")
        self.assertIn("Mind is connected to this chat", self.shown())

    def test_print_says_how_printing_starts(self):
        self.tap("print")
        self.assertIn("Send me a file", self.shown())

    def test_the_new_buttons_took_indexes_after_the_old_ones(self):
        # A tap carries a position in the list, so inserting rather than
        # appending would repoint buttons in messages already sent.
        from mind.telegram_ui import MENU_ACTIONS

        first_ten = [action.key for action in MENU_ACTIONS[:10]]
        self.assertEqual(
            first_ten,
            [
                "files", "find", "clip", "screen", "status",
                "media", "lock", "commands", "apps", "devices",
            ],
        )


if __name__ == "__main__":
    unittest.main()
