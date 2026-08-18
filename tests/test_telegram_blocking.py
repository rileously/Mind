"""Putting a device off the Wi-Fi from the chat.

The same act as the button on the Wi-Fi devices page, reached from a phone that
is not at the PC - so the questions are the same and one is sharper. A tap
carries the address rather than a position in the list, because the list
reorders every scan and a stale index would block whoever moved into that row.

Nothing here reaches a router. What is tested is what the panel offers, what a
tap carries, and that a tap alone never blocks anything - it asks first.
"""

import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

from mind.config_store import ConfigStore
from mind.network_devices import Device
from mind.telegram_bridge import TelegramBridge
from mind.telegram_ui import (
    CB_DEVICE_ASK,
    CB_DEVICE_BLOCK,
    build_devices_keyboard,
    devices_text,
    mac_field,
    mac_from_field,
)

from test_telegram_menu_flow import FakeClient


PHONE = "a2-27-ec-61-6a-a6"
TV = "18-ac-c2-0e-a3-c0"


def buttons(keyboard) -> list[dict]:
    return [button for row in keyboard["inline_keyboard"] for button in row]


class AddressFieldTests(unittest.TestCase):
    def test_an_address_survives_the_trip_to_a_button_and_back(self):
        self.assertEqual(mac_from_field(mac_field(PHONE)), PHONE)

    def test_it_fits_what_telegram_allows_a_button_to_carry(self):
        # 64 bytes for the whole of callback_data, and the action needs some.
        self.assertLessEqual(len(f"{CB_DEVICE_ASK}:{mac_field(PHONE)}"), 20)

    def test_rubbish_gives_nothing_rather_than_half_an_address(self):
        for written in ("", "not-an-address", "a2-27-ec"):
            self.assertEqual(mac_from_field(written), "")


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.devices = [
            Device(mac=PHONE, ip="192.168.18.12", hostname="Redmi-Note-11", online=True),
            Device(mac=TV, ip="192.168.18.6", hostname="Smart-TV", online=False),
        ]

    def test_a_blocked_device_is_marked_wherever_it_appears(self):
        # It may well still be online and still trying, and a list that only
        # said "online" would read as though the block had not worked.
        text = devices_text(self.devices, time.time(), True, blocked=[PHONE])
        self.assertIn("🚫", text)

    def test_nothing_is_marked_when_nothing_is_blocked(self):
        text = devices_text(self.devices, time.time(), True, blocked=[])
        self.assertNotIn("🚫", text)

    def test_every_device_gets_a_button_when_the_router_is_set_up(self):
        keyboard = build_devices_keyboard(self.devices, [], can_block=True)
        labels = [button["text"] for button in buttons(keyboard)]
        self.assertTrue(any("Redmi-Note-11" in label for label in labels))
        self.assertTrue(any("Smart-TV" in label for label in labels))

    def test_no_buttons_are_offered_without_a_router_to_ask(self):
        # A button that always fails is worse than no button.
        keyboard = build_devices_keyboard(self.devices, [], can_block=False)
        labels = [button["text"] for button in buttons(keyboard)]
        self.assertEqual(len(labels), 2)
        self.assertFalse(any("Redmi-Note-11" in label for label in labels))

    def test_the_button_says_which_way_the_tap_goes(self):
        blocked = build_devices_keyboard(self.devices, [PHONE], can_block=True)
        free = build_devices_keyboard(self.devices, [], can_block=True)
        blocked_label = next(b["text"] for b in buttons(blocked) if "Redmi" in b["text"])
        free_label = next(b["text"] for b in buttons(free) if "Redmi" in b["text"])
        self.assertTrue(blocked_label.startswith("✅"))
        self.assertTrue(free_label.startswith("🚫"))

    def test_a_tap_carries_the_address_and_not_a_row_number(self):
        keyboard = build_devices_keyboard(self.devices, [], can_block=True)
        data = next(b["callback_data"] for b in buttons(keyboard) if "Redmi" in b["text"])
        self.assertEqual(data, f"{CB_DEVICE_ASK}:{mac_field(PHONE)}")


class TapTests(unittest.TestCase):
    """What a tap does, with a store but no router."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ConfigStore(root=Path(self.temp.name) / "config")
        self.bridge = TelegramBridge(self.store)
        self.client = FakeClient()
        self.store.save_devices(
            [
                {
                    "mac": PHONE,
                    "ip": "192.168.18.12",
                    "hostname": "Redmi-Note-11",
                    "last_seen": time.time(),
                }
            ]
        )
        config = self.store.load()
        config["router_address"] = "192.168.18.1"
        config["router_username"] = "Epuser"
        # Saved rather than only passed: an alert is sent from the app rather
        # than in reply to a tap, so it reads the settings from the store.
        config["telegram_control_enabled"] = True
        config = self.store.set_router_password(config, "not-a-real-password")
        self.store.save(config)
        self.config = {
            "network_scan_enabled": True,
            "telegram_control_enabled": True,
        }

    def ask(self, mac=PHONE, message_id=500):
        self.bridge._handle_device_ask_tap(
            self.client, 7, "cb", message_id, mac_field(mac), self.config
        )

    def test_a_tap_asks_rather_than_blocking(self):
        # The apps panel closes a program on one tap, because a program can be
        # opened again from the same chair. This reaches someone else's phone.
        self.ask()
        shown = str(self.client.edited or self.client.sent)
        self.assertIn("Block Redmi-Note-11", shown)
        self.assertIn(CB_DEVICE_BLOCK, shown)

    def test_the_question_is_asked_on_the_message_that_was_tapped(self):
        self.ask()
        self.assertEqual([edit["id"] for edit in self.client.edited], [500])
        self.assertEqual(self.client.sent, [])

    def test_blocking_is_not_offered_when_control_is_switched_off(self):
        # It is a control of the house, not of this PC, and follows the same
        # switch as locking the screen.
        self.config["telegram_control_enabled"] = False
        self.ask()
        self.assertNotIn("Block Redmi-Note-11", str(self.client.edited))

    def test_blocking_is_not_offered_without_the_router_sign_in(self):
        config = self.store.load()
        config["router_address"] = ""
        self.store.save(config)
        self.ask()
        self.assertNotIn("Block Redmi-Note-11", str(self.client.edited))

    def test_a_device_that_is_no_longer_known_falls_back_to_the_list(self):
        self.ask(mac="ff-ff-ff-ff-ff-ff")
        self.assertNotIn("Block", str(self.client.edited))

    def test_the_offer_reverses_once_the_device_is_blocked(self):
        self.store.save_blocked([PHONE])
        self.ask()
        self.assertIn("back onto the Wi-Fi", str(self.client.edited))


class RememberedBlocksTests(unittest.TestCase):
    """The block list is written down so the chat need not sign in for it."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ConfigStore(root=Path(self.temp.name) / "config")

    def test_what_the_router_said_is_kept_and_read_back(self):
        self.store.save_blocked([PHONE, TV])
        self.assertEqual(self.store.load_blocked(), sorted([PHONE, TV]))

    def test_nothing_saved_reads_as_nothing_blocked(self):
        self.assertEqual(self.store.load_blocked(), [])

    def test_a_damaged_file_reads_as_nothing_rather_than_raising(self):
        self.store.blocked_path.parent.mkdir(parents=True, exist_ok=True)
        self.store.blocked_path.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.store.load_blocked(), [])


class ArrivalAlertTests(TapTests):
    """Blocking from the message that said something joined.

    Mind already sends one alert per device that appears on the network, on the
    grounds that a stranger is worth its own message. That message is content
    rather than a panel, so a tap on it has to behave differently: the alert
    says what happened, and replacing it with a listing would take that away.
    """

    def test_the_alert_carries_a_way_to_act_on_it(self):
        keyboard = self.bridge.device_alert_keyboard(PHONE, "Redmi-Note-11")
        self.assertIsNotNone(keyboard)
        button = keyboard["inline_keyboard"][0][0]
        self.assertIn("Redmi-Note-11", button["text"])
        self.assertEqual(button["callback_data"], f"{CB_DEVICE_ASK}:{mac_field(PHONE)}")

    def test_no_button_is_offered_when_it_could_only_fail(self):
        config = self.store.load()
        config["router_address"] = ""
        self.store.save(config)
        self.assertIsNone(self.bridge.device_alert_keyboard(PHONE, "Redmi-Note-11"))

    def test_a_tap_on_an_alert_asks_in_the_alert_itself(self):
        # 909 is not the devices panel, so it is an alert.
        self.ask(message_id=909)
        self.assertEqual([edit["id"] for edit in self.client.edited], [909])
        self.assertIn("Block Redmi-Note-11", str(self.client.edited))

    def test_an_alert_never_becomes_the_panel_that_the_next_listing_deletes(self):
        # Otherwise asking for /devices later would remove the alert, which is
        # the one message here worth keeping.
        self.ask(message_id=909)
        self.assertNotIn((7, "devices"), self.bridge._panels)

    def test_the_outcome_is_written_into_the_alert_rather_than_a_new_listing(self):
        blocked = []
        self.bridge._device_block = blocked  # marker for readability only

        def fake_set_blocked(*args, **kwargs):
            blocked.append(args)
            # What the router says afterwards, across both its lists.
            return (PHONE,)

        with unittest.mock.patch(
            "mind.telegram_bridge.set_blocked", side_effect=fake_set_blocked
        ):
            self.bridge._handle_device_block_tap(
                self.client, 7, "cb", 909, mac_field(PHONE), self.config
            )
        self.assertTrue(blocked)
        self.assertIn("off the Wi-Fi", str(self.client.edited))
        self.assertEqual(self.client.sent, [])

    def test_what_the_router_says_afterwards_is_what_gets_remembered(self):
        with unittest.mock.patch(
            "mind.telegram_bridge.set_blocked", return_value=(PHONE,)
        ):
            self.bridge._handle_device_block_tap(
                self.client, 7, "cb", 909, mac_field(PHONE), self.config
            )
        self.assertEqual(self.store.load_blocked(), [PHONE])


if __name__ == "__main__":
    unittest.main()
