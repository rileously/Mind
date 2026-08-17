"""PC controls exposed over Telegram, and the switches that keep them shut."""

import unittest
from unittest import mock

from mind.config_store import DEFAULT_CONFIG
from mind.telegram_system import (
    MEDIA_KEYS,
    Status,
    SystemActionError,
    format_status,
    press_media_key,
)


class DefaultsTests(unittest.TestCase):
    def test_every_remote_capability_ships_switched_off(self):
        # None of these should ever be something a user discovers they had.
        for key in (
            "telegram_enabled",
            "telegram_files_enabled",
            "telegram_control_enabled",
            "telegram_power_enabled",
        ):
            self.assertFalse(DEFAULT_CONFIG[key], key)

    def test_shutdown_has_its_own_switch(self):
        # Shutdown is the only control that can lose unsaved work, so enabling
        # the general controls must not enable it too.
        self.assertIn("telegram_power_enabled", DEFAULT_CONFIG)
        self.assertNotEqual("telegram_control_enabled", "telegram_power_enabled")


class StatusFormattingTests(unittest.TestCase):
    def test_battery_and_disks_are_reported(self):
        text = format_status(
            Status(
                battery_percent=62,
                on_mains=False,
                memory_used_percent=41,
                memory_total_gb=32.0,
                uptime_hours=11.9,
                disks=[("C:\\", 93.0, 953.0)],
            ),
            host="WORKSTATION",
        )
        self.assertIn("WORKSTATION", text)
        self.assertIn("62%", text)
        self.assertIn("on battery", text)
        self.assertIn("41%", text)
        self.assertIn("C:\\", text)
        self.assertIn("93 GB free", text)

    def test_a_desktop_without_a_battery_is_not_reported_as_empty(self):
        # A machine with no battery must not be shown as one sitting at 0%.
        text = format_status(
            Status(None, True, 30, 16.0, 2.0, [("C:\\", 100.0, 500.0)])
        )
        self.assertNotIn("🔋", text)
        self.assertIn("Mains power", text)

    def test_missing_readings_are_left_out_rather_than_guessed(self):
        text = format_status(Status(None, None, None, None, None, []))
        self.assertNotIn("None", text)


class MediaKeyTests(unittest.TestCase):
    def test_known_controls_are_sent_as_key_presses(self):
        with mock.patch("ctypes.windll.user32.keybd_event") as pressed:
            press_media_key("next")
        # Down then up, or the key stays held.
        self.assertEqual(pressed.call_count, 2)

    def test_an_unknown_control_is_refused_with_the_options(self):
        with self.assertRaises(SystemActionError) as caught:
            press_media_key("selfdestruct")
        self.assertIn("play", str(caught.exception))

    def test_control_names_are_case_insensitive(self):
        with mock.patch("ctypes.windll.user32.keybd_event"):
            press_media_key("  VolUp  ")

    def test_the_table_holds_only_media_and_volume_keys(self):
        # Nothing here should be a general keystroke injector; that would be a
        # way to type into whatever app has focus.
        self.assertTrue(all(0xA0 <= code <= 0xB3 for code in MEDIA_KEYS.values()))


if __name__ == "__main__":
    unittest.main()
