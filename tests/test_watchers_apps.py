"""Apps, devices and networks: watching them, and closing an app from the chat.

Same rule as every other watcher - the change is the event, not the state - and
one addition: an alert about an app that has just opened carries a button to
close it, because being told the game is running is only half of what someone
away from the machine wants.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mind.config_store import ConfigStore
from mind.telegram_bridge import TelegramBridge
from mind.telegram_system import SystemActionError
from mind.watchers import (
    APP_CLOSED,
    APP_OPENED,
    DEVICE_NEW,
    IDLE,
    WIFI_NEW,
    Reading,
    Watcher,
    app_name,
    evaluate,
    watches_apps,
    watches_devices,
    watches_wifi,
)

from tests.test_telegram_menu_flow import FakeClient


class AppWatcherTests(unittest.TestCase):
    def test_it_fires_on_the_change_not_the_state(self):
        watcher = Watcher(id="a", kind=APP_OPENED, target="game")
        firings, state = evaluate([watcher], Reading(running_apps=frozenset()), {}, 0)
        self.assertEqual(firings, [], "the first look only records what is running")
        firings, state = evaluate(
            [watcher], Reading(running_apps=frozenset({"game.exe"})), state, 60
        )
        self.assertEqual(len(firings), 1)
        self.assertIn("opened", firings[0].message)
        firings, state = evaluate(
            [watcher], Reading(running_apps=frozenset({"game.exe"})), state, 120
        )
        self.assertEqual(firings, [], "still running is not news")

    def test_an_app_already_open_is_not_announced_when_the_watcher_is_made(self):
        watcher = Watcher(id="a", kind=APP_OPENED, target="game")
        firings, _ = evaluate(
            [watcher], Reading(running_apps=frozenset({"game.exe"})), {}, 0
        )
        self.assertEqual(firings, [])

    def test_closing_is_reported_by_its_own_kind_only(self):
        opened = Watcher(id="a", kind=APP_OPENED, target="game")
        closed = Watcher(id="b", kind=APP_CLOSED, target="game")
        _, state = evaluate([opened, closed], Reading(running_apps=frozenset()), {}, 0)
        firings, state = evaluate(
            [opened, closed], Reading(running_apps=frozenset({"game.exe"})), state, 60
        )
        self.assertEqual([f.watcher_id for f in firings], ["a"])
        firings, _ = evaluate(
            [opened, closed], Reading(running_apps=frozenset()), state, 120
        )
        self.assertEqual([f.watcher_id for f in firings], ["b"])

    def test_the_name_is_matched_however_it_was_typed(self):
        for typed in ("game", "Game.EXE", "C:/Games/game.exe", '"game.exe"'):
            watcher = Watcher(id="a", kind=APP_OPENED, target=typed)
            self.assertEqual(app_name(watcher), "game.exe", typed)

    def test_only_an_app_that_is_up_can_be_offered_for_closing(self):
        opened = Watcher(id="a", kind=APP_OPENED, target="game")
        closed = Watcher(id="b", kind=APP_CLOSED, target="game")
        _, state = evaluate([opened, closed], Reading(running_apps=frozenset()), {}, 0)
        firings, state = evaluate(
            [opened, closed], Reading(running_apps=frozenset({"game.exe"})), state, 60
        )
        self.assertEqual(firings[0].app, "game.exe")
        firings, _ = evaluate(
            [opened, closed], Reading(running_apps=frozenset()), state, 120
        )
        self.assertEqual(firings[0].app, "", "a closed app cannot be closed again")


class DeviceAndWifiTests(unittest.TestCase):
    def test_a_drive_appearing_is_reported_once(self):
        watcher = Watcher(id="d", kind=DEVICE_NEW)
        _, state = evaluate([watcher], Reading(removable_drives=frozenset()), {}, 0)
        firings, state = evaluate(
            [watcher], Reading(removable_drives=frozenset({"E:\\"})), state, 60
        )
        self.assertEqual(len(firings), 1)
        self.assertIn("E:", firings[0].message)
        firings, _ = evaluate(
            [watcher], Reading(removable_drives=frozenset({"E:\\"})), state, 120
        )
        self.assertEqual(firings, [])

    def test_the_same_stick_plugged_in_again_is_news_again(self):
        watcher = Watcher(id="d", kind=DEVICE_NEW)
        _, state = evaluate([watcher], Reading(removable_drives=frozenset()), {}, 0)
        _, state = evaluate(
            [watcher], Reading(removable_drives=frozenset({"E:\\"})), state, 60
        )
        _, state = evaluate([watcher], Reading(removable_drives=frozenset()), state, 120)
        firings, _ = evaluate(
            [watcher], Reading(removable_drives=frozenset({"E:\\"})), state, 180
        )
        self.assertEqual(len(firings), 1)

    def test_the_networks_already_in_range_are_not_news(self):
        watcher = Watcher(id="w", kind=WIFI_NEW)
        known = frozenset({"HomeWiFi", "Neighbour", "CoffeeShop"})
        firings, state = evaluate([watcher], Reading(wifi_networks=known), {}, 0)
        self.assertEqual(firings, [], "the whole street would arrive at once")
        firings, _ = evaluate(
            [watcher], Reading(wifi_networks=known | {"Guest"}), state, 60
        )
        self.assertEqual(len(firings), 1)
        self.assertIn("Guest", firings[0].message)

    def test_a_reading_nobody_asked_for_says_nothing(self):
        # None means it was not read, which is not the same as nothing found.
        for kind in (DEVICE_NEW, WIFI_NEW):
            firings, _ = evaluate([Watcher(id="x", kind=kind)], Reading(), {}, 0)
            self.assertEqual(firings, [], kind)

    def test_nothing_is_read_for_a_watcher_nobody_created(self):
        # A network scan costs a process every twenty-five seconds, so it must
        # only run when something actually watches for networks.
        none = [Watcher(id="x", kind=IDLE, threshold=5)]
        self.assertFalse(watches_apps(none))
        self.assertFalse(watches_devices(none))
        self.assertFalse(watches_wifi(none))
        some = [
            Watcher(id="a", kind=APP_OPENED, target="game"),
            Watcher(id="d", kind=DEVICE_NEW),
            Watcher(id="w", kind=WIFI_NEW),
        ]
        self.assertTrue(watches_apps(some))
        self.assertTrue(watches_devices(some))
        self.assertTrue(watches_wifi(some))
        self.assertFalse(watches_wifi([Watcher(id="w", kind=WIFI_NEW, enabled=False)]))


class RunningAppsPanelTests(unittest.TestCase):
    """Seeing what is open, and closing one from the chat."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ConfigStore(root=Path(self.temp.name) / "config")
        self.bridge = TelegramBridge(self.store)
        self.client = FakeClient()
        self.config = {"telegram_control_enabled": True}
        self.listed = [("game.exe", "Doom"), ("chrome.exe", "News")]

    def show(self) -> int:
        with mock.patch("mind.telegram_bridge.read_visible_apps", return_value=self.listed):
            self.bridge._send_apps_panel(self.client, 7, self.config)
        return self.client.sent[-1]["id"]

    def test_it_lists_what_is_open_with_a_button_each(self):
        self.show()
        text = self.client.sent[-1]["text"]
        self.assertIn("game.exe", text)
        self.assertIn("Doom", text)
        buttons = str(self.client.sent[-1]["markup"])
        self.assertIn("game.exe", buttons)
        self.assertIn("chrome.exe", buttons)

    def test_tapping_closes_the_app_whose_name_was_on_the_button(self):
        message = self.show()
        with mock.patch(
            "mind.telegram_bridge.close_app", return_value="game.exe closed."
        ) as killer, mock.patch(
            "mind.telegram_bridge.read_visible_apps", return_value=[("chrome.exe", "News")]
        ):
            self.bridge._handle_apps_tap(self.client, 7, "cb", message, 0, self.config)
        killer.assert_called_once_with("game.exe")

    def test_the_list_is_redrawn_so_it_never_shows_what_has_gone(self):
        message = self.show()
        with mock.patch("mind.telegram_bridge.close_app", return_value="closed."), mock.patch(
            "mind.telegram_bridge.read_visible_apps", return_value=[("chrome.exe", "News")]
        ):
            self.bridge._handle_apps_tap(self.client, 7, "cb", message, 0, self.config)
        self.assertNotIn("game.exe", self.client.edited[-1]["text"])

    def test_a_refusal_is_shown_rather_than_swallowed(self):
        message = self.show()
        with mock.patch(
            "mind.telegram_bridge.close_app",
            side_effect=SystemActionError("mind.exe keeps Windows running and cannot be closed."),
        ), mock.patch("mind.telegram_bridge.read_visible_apps", return_value=self.listed):
            self.bridge._handle_apps_tap(self.client, 7, "cb", message, 0, self.config)
        self.assertTrue(any("cannot be closed" in text for text in self.client.answered))

    def test_refresh_redraws_without_closing_anything(self):
        message = self.show()
        with mock.patch("mind.telegram_bridge.close_app") as killer, mock.patch(
            "mind.telegram_bridge.read_visible_apps", return_value=self.listed
        ):
            self.bridge._handle_apps_tap(self.client, 7, "cb", message, -1, self.config)
        killer.assert_not_called()
        self.assertIn("game.exe", self.client.edited[-1]["text"])

    def test_nothing_can_be_closed_while_pc_controls_are_off(self):
        self.bridge._handle_apps_tap(self.client, 7, "cb", 500, 0, {})
        self.assertTrue(any("switched off" in text for text in self.client.answered))

    def test_the_panel_says_so_when_controls_are_off(self):
        self.bridge._send_apps_panel(self.client, 7, {})
        self.assertIn("switched off", self.client.sent[-1]["text"])

    def test_a_tap_from_an_older_list_asks_for_a_fresh_one(self):
        self.show()
        self.bridge._handle_apps_tap(self.client, 7, "cb", 500, 9, self.config)
        self.assertTrue(any("again" in text for text in self.client.answered))

    def test_an_alert_about_an_app_offers_to_close_it(self):
        from mind.watchers import to_dict

        self.store.save_watchers(
            [to_dict(Watcher(id="a", kind=APP_OPENED, target="game"))]
        )
        config = {**self.config, "watchers_enabled": True, "telegram_allowed_chat_ids": [7]}
        with mock.patch("mind.telegram_bridge.read_running_apps", return_value=frozenset()):
            self.bridge._check_watchers(self.client, config)
        with mock.patch(
            "mind.telegram_bridge.read_running_apps", return_value=frozenset({"game.exe"})
        ):
            self.bridge._check_watchers(self.client, config)
        self.assertIn("opened", self.client.sent[-1]["text"])
        self.assertIn("Close game.exe", str(self.client.sent[-1]["markup"]))

    def test_that_button_closes_the_app_it_named(self):
        message = 4321
        self.bridge._alert_apps[(7, message)] = "game.exe"
        with mock.patch(
            "mind.telegram_bridge.close_app", return_value="game.exe closed."
        ) as killer:
            self.bridge._handle_app_close_tap(self.client, 7, "cb", message, self.config)
        killer.assert_called_once_with("game.exe")
        self.assertIn("closed", self.client.edited[-1]["text"])


if __name__ == "__main__":
    unittest.main()
